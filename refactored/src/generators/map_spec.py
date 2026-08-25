# src/generators/map_spec.py
"""
地图配方（MapSpec）：配置驱动的生成架构核心（阶段 0 落地）。

项目含义（见 readme "项目含义与地图生成架构"）：
- 当前阶段：用户用"配方"详细设定地图信息 ——
    1) 哪种方式生成多少哪种建筑区（BuildingAreaSpec 列表）
    2) 建筑区内用哪种方式生成建筑/房屋（InteriorSpec）
    3) 这些建筑用哪种方式连接（ConnectionSpec —— 道路，规划中）
    4) 用哪种方式修饰（DecorationSpec —— 水/石等物体，规划中）
- 未来阶段：把合理的配方打包为带随机性的"主题地图"（独栋别墅/乡村/地下城/飞碟），
  再由主题组合成更复杂的地图（见 PRESETS 与 readme）。

所有字段 frozen dataclass，保证配方可哈希、可序列化（asdict/to_json）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# 配方单元
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BuildingAreaSpec:
    """
    一种建筑区的生成规格："用哪种方式（generator）生成多少（count）个"。
    kwargs 原样透传给对应生成器的 create_building_area()。
    """
    generator: str = "rectangle"          # rectangle | circle | regular_polygon | hexagon
    count: int = 1
    name_prefix: str = ""                 # 空则用 generator 名当前缀
    kwargs: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InteriorSpec:
    """
    建筑区内部"用哪种方式生成建筑/房屋"。
    mode:
      - basic     : 每个建筑区一个基础房间（RoomGenerator）
      - maze      : 基础房间 + 迷宫内墙（maze_room）
      - watabou   : Watabou 粗格走廊+房间+门（BlockRoomGenerator，全图模式）
      - dwellings : Dwellings 住宅（不规则外形/退台/门/窗/楼梯）
      - subdivide / grow / rowhouse / temple / fracture : 规划中（阶段 1）
    kwargs 透传对应生成器的参数。
    """
    mode: str = "basic"
    kwargs: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectionSpec:
    """
    建筑之间"用哪种方式连接"（道路路网，阶段 2 已落地 door_to_door）。
    mode:
      - none          : 不生成道路（孤立/单体建筑地图默认）
      - door_to_door  : ✅ 已实现（见 readme §2.8/§2.10/§2.11）
      - fungus        : 生长树（旧真菌；建筑面积为营养权重，路宽按流量取 5--10）
      - fungus_v2     : 电路（旧真菌v2；道路面积 + 全部建筑对的最短步行总代价）
      - fungus_v3     : 真菌（早期维护成本加权侵蚀 + 后期深部开洞扩孔）
      - trunk_branch  : 主干道+分干道（规划中，暂按 door_to_door 生成）
    kwargs（透传给 src/ 内的生成器，机制内部化；MapGenerator 按是否有 style 分发）：

      无 style -> RoadGenerator v2（折角折线，§2.8）：
        - width          : 路宽（默认 5）
        - layer          : 生成层（默认 1）
        - seed           : 路网种子（None 时受 MapGenerator 全局 seed 约束，可复现）
        - dense_room_ids : 稠密阶段房间 id 列表（区内多连）
        - dense_groups   : 按大建筑区分组的稠密房间（区内稠密、区际留给稀疏）
        - dense_degree   : 稠密阶段每房间连最近 n 个（默认 2）
        - max_turns      : A* 兜底路径最大折角数（默认 6）

      有 style -> RoadStyleGenerator（直角/弯曲 × 稠密/稀疏）：
        - style          : "直角"（4 向 A* 右角折线）| "弯曲"（8 向 A* + 圆角曲线）
        - density        : "稠密"（每建筑连最近 dense_k 个）| "稀疏"（树形 + 连通保证）
        - width          : 路宽（默认 5）
        - seed           : 路网种子
        - dense_k        : 稠密时每建筑连最近 n 个（默认 3）

      生长树可用 ``mode="fungus"`` 或 ``style="生长树"``：
        - min_width / max_width : 最窄/最宽道路（默认并建议 5 / 10）
        - weights                : 可选 ``{room_id|room_name: 权重}``；不传则按建筑面积平方根
        - weight_bias            : 权重对骨架选边的影响（默认 0.7）
        - maintenance_cost       : 每格冗余路的维护成本（默认 20.0）
        - loop_gain_threshold    : 节省运输收益/维护成本的最小比值（默认 1.25）
        - max_cycles             : 最多保留多少条冗余环（默认 max(2, 建筑数//5)）；
                                   与主干近乎平行的候选会在收缩阶段自动舍弃

      电路可用 ``mode="fungus_v2"`` 或 ``style="电路"``：
        - 不使用 min_width / max_width；道路是一个由可走格组成的区域
        - 人流：建筑 i 有 A_i 格，则 i→j 为 A_i 人；无向需求为 A_i+A_j
        - area_cost : 维护一格道路相当于多少“人步”成本；缺省时按地图和总人流自动标定
        - candidate_degree : 每栋建筑参与优化的近邻候选数（默认 5，防止退化成完全图）
        - weights : 可选覆盖 A_i；默认使用建筑可用格面积

      真菌可用 ``mode="fungus_v3"`` 或 ``style="真菌"``：
        - 初态是建筑区内所有可通行区域；空洞按离散水平集/拓扑导数生长
        - maintenance_cost : 每个道路格的维护成本（单位为人步）；越高收缩越强
        - min_road_width : 最小道路宽度（默认 5，不能小于 5）
        - optimization_cell_size : 形状求解格宽，默认等于 min_road_width（即 5）
        - max_iterations / max_attempts : 侵蚀成功轮次与回溯评估次数上限（默认 48 / 110）
        - erosion_batch_size : 初始形状步长；0 表示按地图规模/迭代数自动设置
        - perimeter_weight : 周长正则权重（默认 0.005），越高边界越平滑紧凑
        - nucleation_interval : 每隔多少轮允许内部低流量处萌发空洞（默认 4）
        - detour_factor : 形状导数对人流绕路的敏感度（默认 2.0）
        - late_nucleation_rounds : 主侵蚀后寻找深部新空洞的轮数（默认 12）
        - hole_growth_steps / hole_growth_batch : 每个深部空洞的扩张轮数/批量（默认 8 / 自动）
        - late_min_solid_depth : 深部空洞距已有空洞的最小优化格距离（默认 3）
        - boundary_rounding / building_clearance : 输出圆角半径（默认 0）/非门位置建筑退距
        - weights : 可选覆盖建筑格数 A_i；默认 i→j 的人流量为 A_i
    """
    mode: str = "none"
    kwargs: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecorationSpec:
    """
    地图修饰方式（物体系统，规划中，阶段 4）。
    kind:
      - none  : 不修饰
      - water : 水体（河流/湖泊/瀑布）
      - stone : 岩石/碎石/桥墩
    当前执行器遇到非 none 会打印"规划中"警告并跳过。
    """
    kind: str = "none"
    kwargs: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 地图配方
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MapSpec:
    """
    一张地图的完整生成配方。

    - name / width / height / layers : 地图基础信息
    - seed : 可复现种子（None 时执行器随机取一个并返回）
    - areas / interior / connection / decoration : 四个生成维度
    """
    name: str = "未命名地图"
    width: int = 100
    height: int = 100
    layers: int = 1
    seed: Optional[int] = None
    areas: List[BuildingAreaSpec] = field(default_factory=list)
    interior: InteriorSpec = field(default_factory=InteriorSpec)
    connection: ConnectionSpec = field(default_factory=ConnectionSpec)
    decoration: DecorationSpec = field(default_factory=DecorationSpec)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 主题预设（未来"把合理配方打包为带随机性的地图"）
# 每个函数返回一个 MapSpec；调用方可用 seed=... 固定随机性。
# ---------------------------------------------------------------------------
def villa_spec(**kw: Any) -> MapSpec:
    """独栋别墅：单栋多层住宅 + dwellings 室内 + 少量石饰。"""
    return MapSpec(
        name="独栋别墅",
        width=90,
        height=90,
        layers=3,
        areas=[
            BuildingAreaSpec(
                generator="rectangle",
                count=1,
                name_prefix="别墅",
                kwargs=dict(
                    rect_size=[(36, 36), (56, 56)],
                    angle=False,
                    layer=(1, 3),
                    max_attempts=100,
                ),
            ),
        ],
        interior=InteriorSpec(
            mode="dwellings",
            kwargs=dict(tags=["默认", "机械", "走廊", "露台"]),
        ),
        connection=ConnectionSpec(mode="none"),
        decoration=DecorationSpec(kind="stone", kwargs=dict(count=6)),
        **kw,
    )


def village_spec(**kw: Any) -> MapSpec:
    """乡村：若干农舍 + 谷仓，基础房间，规划为道路连通、散布岩石。"""
    return MapSpec(
        name="乡村",
        width=160,
        height=160,
        layers=1,
        areas=[
            BuildingAreaSpec(
                generator="rectangle",
                count=6,
                name_prefix="农舍",
                kwargs=dict(rect_size=[(8, 8), (16, 16)], max_attempts=50),
            ),
            BuildingAreaSpec(
                generator="circle",
                count=2,
                name_prefix="谷仓",
                kwargs=dict(radius_range=(4, 7), max_attempts=50),
            ),
        ],
        interior=InteriorSpec(mode="basic"),
        connection=ConnectionSpec(mode="door_to_door"),  # 规划中
        decoration=DecorationSpec(kind="stone", kwargs=dict(count=10)),  # 规划中
        **kw,
    )


def dungeon_spec(**kw: Any) -> MapSpec:
    """地下城：1200x1200 大地图，唯一一个占满全图的建筑区（地牢本体），
    建筑区内用 Watabou 走廊布局生成地牢（房间/走廊全部归属该建筑区，
    由 MapGenerator 通用数据层硬裁剪保证不越界）。

    注意：Watabou 粗格为 10 格/粗格，大地图才有足够的粗格让走廊+房间生长。"""
    return MapSpec(
        name="地下城",
        width=1200,
        height=1200,
        layers=1,
        areas=[
            BuildingAreaSpec(
                generator="rectangle",
                count=1,
                name_prefix="地牢",
                kwargs=dict(rect_size=[(1190, 1190), (1200, 1200)], angle=False, max_attempts=200),
            ),
        ],
        interior=InteriorSpec(
            mode="watabou",
            kwargs=dict(target_rooms=100),
        ),
        connection=ConnectionSpec(mode="none"),
        decoration=DecorationSpec(kind="none"),
        **kw,
    )


def ufo_spec(**kw: Any) -> MapSpec:
    """外星飞碟：圆形/多边形舱室环绕 + 中心圆厅（规划占位示例）。"""
    return MapSpec(
        name="外星飞碟",
        width=120,
        height=120,
        layers=2,
        areas=[
            BuildingAreaSpec(
                generator="circle",
                count=1,
                name_prefix="主舱",
                kwargs=dict(radius_range=(12, 14), layer=(1, 2), max_attempts=80),
            ),
            BuildingAreaSpec(
                generator="regular_polygon",
                count=6,
                name_prefix="舱室",
                kwargs=dict(radius_range=(4, 6), num_sides=6, max_attempts=80),
            ),
        ],
        interior=InteriorSpec(mode="maze"),
        connection=ConnectionSpec(mode="none"),
        decoration=DecorationSpec(kind="none"),
        **kw,
    )


PRESETS: Dict[str, Callable[..., MapSpec]] = {
    "villa": villa_spec,
    "village": village_spec,
    "dungeon": dungeon_spec,
    "ufo": ufo_spec,
}
