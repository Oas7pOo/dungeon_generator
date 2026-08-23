# 地牢地图生成器

一个功能强大的地牢地图生成系统（V2，id-first 重构版），支持多层地图、多种建筑区类型、多套房间生成管线（基础房间 / Watabou 方块房间 / Dwellings 住宅），以及可复现的种子化生成与 PDF/PNG 可视化。

> 本文档包含两大部分：
> 1. **现状说明**（项目结构、核心组件、使用方式、数据库结构）
> 2. **开发路线图（规划）**：为后续的 **道路（Road）生成**、**房间分割/生成扩展**、**洞穴（Cave）生成**、**物体系统（水/石）**、**水系统（河流/瀑布/桥/河上建筑）** 做详细设计，并给出对当前类的**代码审核与预备改造清单**。

---

## 项目含义与地图生成架构

> **一句话**：本项目的核心不是"一个生成算法"，而是**配置驱动的分层生成流水线**——
> 用户用"地图配方（MapSpec）"逐层规定**生成什么、用什么方式生成、生成多少**，
> 生成器按配方执行；将来把合理配方打包成带随机性的**主题地图**，再由主题**组合**成更复杂的地图。

### 分层结构（自底向上）

| 层 | 内容 | 当前状态 |
|---|---|---|
| **L1 生成器（Generator）** | 建筑区生成器 / 房间生成器 / Watabou / Dwellings / 物品生成器（+ 规划中：道路、洞穴、物体、水系统） | ✅ 已有 + 规划 |
| **L2 地图配方（MapSpec）** | 声明式配方：`areas`（哪种方式生成多少哪种建筑区）→ `interior`（区内用哪种方式生成建筑/房屋）→ `connection`（用哪种方式连接）→ `decoration`（用哪种方式修饰），附 `seed` | ✅ **阶段 0 已落地**（`map_spec.py` + `map_generator.py`） |
| **L3 主题预设（Preset）** | 把合理配方打包为带随机性的地图类型：独栋别墅 / 乡村 / 地下城 / 外星飞碟……（`PRESETS`，seed 驱动随机性） | 🚧 已有 4 个示例预设，后续扩展 |
| **L4 组合（Composition）** | 多个主题/区域组合成一张复杂地图（分区布局 + 主题拼合 + 跨区连接） | 🔮 规划 |

### 当前阶段定位

- **现在**：正处于 **L1+L2（详细设定地图信息进行生成）** 阶段——
  "用户规定：哪种方式生成多少哪种建筑区 → 建筑区内用哪种方式生成建筑 → 这些建筑用哪种方式生成房屋 → 用哪种方式连接 → 用哪种方式修饰"。
- **下一步**：把合理的 L2 配方进一步打包为 L3 主题地图（带随机性）。
- **再之后**：通过 L4 组合形成更复杂的地图。

### MapSpec 配方结构（L2 已落地）

```python
MapSpec(
    name="乡村", width=160, height=160, layers=1, seed=42,
    areas=[                       # ① 哪种方式生成多少哪种建筑区
        BuildingAreaSpec(generator="rectangle", count=6, kwargs=dict(rect_size=[(8,8),(16,16)])),
        BuildingAreaSpec(generator="circle",    count=2, kwargs=dict(radius_range=(4,7))),
    ],
    interior=InteriorSpec(mode="basic"),        # ② 区内用哪种方式生成建筑/房屋
    connection=ConnectionSpec(mode="door_to_door"),  # ③ 用哪种方式连接（道路，阶段 2 已实现，见 §2.8）
    decoration=DecorationSpec(kind="stone"),    # ④ 用哪种方式修饰（物体，规划中）
)
```

执行：`MapGenerator(db).generate(spec)` —— 种子化、可复现、按 §0.6 流水线跑完整张地图（详见"使用方法 §3"）。

---

## 项目结构

```
dungenMap/
├── refactored/                # 重构后的代码（V2，id-first）
│   ├── src/
│   │   ├── __init__.py        # 公共导出
│   │   ├── db/
│   │   │   ├── database.py            # DatabaseManager（V2：dict 行、FK、WAL）
│   │   │   ├── migrations.py          # 迁移注册表（v001 建表，阶段 0 落地）
│   │   │   └── building_area_dao.py   # building_area 表 DAO
│   │   ├── generators/
│   │   │   ├── building_area_generator.py  # 建筑区生成器（矩形/圆形/正多边形/六边形）
│   │   │   ├── room_generator.py           # 基础房间生成器（圆/多边形/迷宫）
│   │   │   ├── block_room_generator.py     # Watabou 方块房间（粗格 10x10）
│   │   │   ├── item_generator.py           # 门/窗/楼梯 物品生成
│   │   │   ├── passability.py              # 可通行性查询（阶段 0 落地）
│   │   │   ├── road_generator.py           # 道路生成器 RoadGenerator v2（优先更直折线 + 多候选重连，见 §2.8）
│   │   │   ├── road_style_generator.py     # 路网风格生成器 RoadStyleGenerator（视觉直走/贝塞尔/大中小路宽，见 §2.10/2.11）
│   │   │   ├── room_subdivider.py          # 房间分割器 RoomSubdivider（BSP 二分 + 共享单墙内墙 + 内部门）
│   │   │   ├── map_spec.py                 # 地图配方 MapSpec + 主题预设（阶段 0 落地）
│   │   │   ├── map_generator.py            # 配方执行器 MapGenerator（阶段 0 落地）
│   │   │   ├── dwellings_house_generator.py # Dwellings 住宅写入器
│   │   │   └── dwellings_core/             # Dwellings.js 迁移核心（纯数据）
│   │   │       ├── house.py    # 住宅流水线、门连接、退台
│   │   │       ├── plan.py     # 房间划分（divideArea）、走廊合并、窗户
│   │   │       ├── shape.py    # 几何基元（Edge/Dir/outline_edges/contour2area）
│   │   │       ├── footprint.py# 不规则外形（notch）
│   │   │       ├── specs.py    # 参数 Specs
│   │   │       ├── tags.py     # 标签解析/互斥消解
│   │   │       ├── rng.py      # 可复现 RNG（Park-Miller LCG）
│   │   │       └── roomtypes/  # 楼梯等房间类型
│   │   └── visualization/
│   │       ├── map_visualizer.py     # 高性能渲染器（5 层 imshow 掩码）
│   │       └── map_visualizer_old.py # 旧版（保留参考）
│   ├── test/                  # pytest 测试与脚本
│   │   ├── test_combined.py / test_dwellings.py / test_block_room.py ...
│   │   └── output/            # 生成结果（PDF/PNG/JSON）
│   ├── test_parity.py / test_doors_count.py ...   # 复现性/一致性测试
│   └── dungeon.db             # SQLite 数据库
├── venv/                      # Python 虚拟环境
└── readme.md                  # 本文档
```

> **机制内部化原则（重要）**：**大部分生成机制应内部化到 `src/`**（生成器类、算法、落库、
> 门/墙/连通性处理都在 `src/generators/*.py` 内），**生成脚本（`test/*.py`）只做配置与调用**
> ——构造参数、调用生成器、做统计核验。新增机制时优先写进 `src/` 再让脚本薄薄地调用，
> 不要把算法逻辑堆在脚本里（如 `RoadGenerator` §2.8 / `RoadStyleGenerator` §2.10-2.11 均为此模式）。

---

## 功能特性

### 地图与建筑区
- 多层地牢地图（`map` 表 + `layer_start/layer_end`）
- 建筑区类型：矩形（轴对齐/旋转）、圆塔、正多边形、六边形
- 智能碰撞检测（STRtree 空间索引，支持层间/跨层避让）
- 最大空闲区引导放置 + 逐格缩小回退（`largest_first` / 随机）
- 跨层建筑区：同一几何在所有指定层生效

### 房间生成（现状，三套管线并存）
1. **RoomGenerator（基础）**：每个建筑区 → 一个房间（圆/多边形栅格化为 wall/space），支持迷宫内墙
2. **BlockRoomGenerator（Watabou）**：粗格 10x10 上生成走廊+房间拓扑，含门查找、door gap 挖洞、封闭孔合并
3. **DwellingsHouseDBWriter（住宅）**：Dwellings.js 流水线（不规则外形、退台、房间划分、门/窗/楼梯），逐层写入

### 物品生成
- 门：默认每房间一扇（随机外墙格）；Watabou/住宅管线则按 edge_key 精确定位
- 窗：仅外墙（全局 space 索引判定），避开内墙与门
- 楼梯：spiral / stairwell / ladder，跨层 item 分组去重

### 可视化
- 5 个渲染层可开关/排序：建筑区 / 房间格 / 房间矢量 / 物品格 / 物品矢量
- imshow 掩码批量渲染（性能友好），单层 PNG / 多层 PDF / 组合 PDF

### 地图配方与生成流水线（阶段 0 已落地）
- **MapSpec**：声明式地图配方（建筑区规格 / 内部生成方式 / 连接方式 / 修饰方式 + seed）
- **MapGenerator**：配方执行器，种子化、可复现，一键生成完整地图（建筑区 → 房间 → 门/窗/楼梯）
- **PRESETS**：主题预设示例（独栋别墅 / 乡村 / 地下城 / 外星飞碟）
- **PassabilityIndex**：可通行性统一查询（space 可走 / water、stone 不可走）

### 规划中的特性（详见"开发路线图"）
- 道路生成：方形直道 / 扭曲道路；门-门 / 类真菌 / 主干-分干 三种路网；**全联通 / 大部分联通 / 岛屿型 / 孤立单体建筑** 四种连通形态
- **房间分割与生成扩展**：BSP/网格分割、**膨胀接触分割**、**排屋/连续排列**、**对称神殿+不对称延伸**、**破碎侵蚀破墙**
- 洞穴房间：道路侵蚀 / 矩形溶蚀 / 不规则生成
- **物体系统**：水、石（岩石/碎石）
- **水系统**：河流/湖泊/瀑布、水穿过建筑、跨河桥（道路）、多层廊坊（河上建筑）

---

## 核心组件

### 建筑区生成器（`building_area_generator.py`）
- `BuildingAreaGenerator`（ABC）：层缓存、重叠检测、最大空闲区、尺寸分布（均匀/指数）、多层参数处理
- `RectangleBuildingAreaGenerator`：普通矩形 / 旋转矩形 / 批量放置 `create_building_areas_global`
- `CircleBuildingAreaGenerator` / `RegularPolygonBuildingAreaGenerator` / `HexagonBuildingAreaGenerator`

### 房间生成器（`room_generator.py`）
- `generate_and_save_rooms(map_id)`：为地图下所有建筑区生成房间（V2 id-first）
- `maze_room(room_id, ...)`：房间内 DFS 完美迷宫 + 复杂度开孔 + 走廊膨胀

### 方块房间生成器（`block_room_generator.py`）
- `generate_and_save(...)`：corridor / room_chain 两种模式，粗格拓扑 → 细格 tiles → door item
- 门以 edge_key（`("V",x,y)` / `("H",x,y)`）记录，落库为 item

### 住宅生成器（`dwellings_house_generator.py` + `dwellings_core/`）
- `DwellingsHouseDBWriter.generate_and_save_dwelling(building_area_id, seed, tags, n_floors)`
- core 为纯数据层（可复现），writer 负责写入 room + item

### 地图可视化器（`map_visualizer.py`）
- `draw_map(map_id, layer_index, ...)` / `save_map` / `save_map_by_layer` / `save_multi_layer_pdf` / `save_combined_pdf`

### 地图配方与执行器（阶段 0 已落地）
- `map_spec.py`：`MapSpec` / `BuildingAreaSpec` / `InteriorSpec` / `ConnectionSpec` / `DecorationSpec` / `PRESETS`（villa / village / dungeon / ufo）
- `map_generator.py`：`MapGenerator(db).generate(spec)` —— 种子化流水线执行器
- `passability.py`：`PassabilityIndex(db).is_walkable(map_id, layer, x, y)`

### 规划中的新生成器（见路线图）
- `RowHouseGenerator` / `TempleGenerator` / `FractureEroder` / `CaveGenerator`
- `ObjectGenerator`（水/石 item）
- `WaterSystem`（河流/瀑布/廊坊整合）

### 已落地的增强（阶段 1/2 进展）
- `RoomSubdivider`（`room_subdivider.py`）：矩形房间 BSP 递归二分 → 子房间 + 共享单墙内墙 + 内部门。
  **经验**：BSP 嵌套切分后相邻 region 起点不同，内部门检测必须按"共享边距离=1 且投影范围重叠"判断
  （`a.x1+1==b.x0` 且 y 范围重叠），不能用"同起点"判断。
- `RoadGenerator` 稠密/稀疏分层 + 兜底外墙 + 门口复用（见 §2.8）。

---

## 开发路线图（规划）

> 本节是后续开发的设计蓝本。目标特性：
> **① 房间分割/生成扩展**：BSP/网格、膨胀接触分割、排屋、对称神殿、破碎侵蚀。
> **② 道路（Road）**：为门之间做连接，方形直道/扭曲道路，多种路网算法，多种**连通性形态**。
> **③ 洞穴房间（Cave）**：道路侵蚀、矩形溶蚀、不规则生成。
> **④ 物体系统**：水、石。
> **⑤ 水系统**：河流/瀑布、水穿建筑、桥、河上廊坊。

### 0. 总体原则与概念模型（先读）

1. **三要素分层**：
   - **房间（Room）**：一律在建筑区（BuildingArea）内部；`building_area_id` 必填。
   - **道路（Road）**：不是建筑区的一部分，但**也是一种"形式的房间"**（`room_type='road'` 存在 `room` 表）。两种归属：
     - 跨建筑区道路：`building_area_id = NULL`，行驶在地图"空地"上；
     - 建筑区内道路：`building_area_id = 所在建筑区`，连接区内不同建筑/房间，**必须完全落在该建筑区多边形内**。
   - **物体（Object）**：水、石、装饰等，写入 `item` 表（`item_type` 扩展），可位于地图任意位置（含建筑区内外）。
2. **可通行性（passability）**：为后续寻路/避障建立统一约定——
   - 可通行：房间 `space`、道路带 `space`；
   - 不可通行：`wall` / `inner_wall`、水体（water）、岩石（stone）。
   - RoadGenerator 的"可走区域" = 地图 − 建筑区（跨区时）− 水体 − 岩石（详见 §2.6）。
3. **分层原则**：道路/河流只在**同一层**内连接建筑区；跨层连接由楼梯（stairs）与瀑布（跨层水）负责。RoadGenerator / WaterSystem 均按 `(map_id, layer)` 工作。
4. **坐标约定**：房间/道路/洞穴/物体的 tiles 统一使用**细网格世界坐标**（整数格）；粗格坐标只存在于 dwellings 内部（局部坐标 + origin 偏移），跨界必须换算（已有 `_coarse_cells_to_world_corners` 等工具）。
5. **可复现性**：所有新生成器使用种子化 RNG（复用 `dwellings_core.rng.RNG`，Park-Miller LCG），不再依赖全局 `random` / `np.random`。
6. **宏观生成顺序（建议流水线）**：
   建筑区（含特殊结构：排屋/神殿/孤立建筑）→ 水体（河流/湖泊，可切过建筑区预留河道）→ 房间生成与分割 → 洞穴侵蚀 → 道路生成（含桥）→ 门/窗/石等物体 → 可视化。
   **阶段 0 已按此流水线落地 L2 配方执行器**（`MapGenerator`：建筑区 → 房间 → 门/窗/楼梯，连接/修饰预留）。

---

### 1. 房间分割与生成扩展（Room Division & Structure Generation）

> 在现有"矩形房间分割"（BSP/网格）基础上扩展出 5 种"把一个区域变成多个房间"的方式，以及 2 种"一栋建筑如何成形"的方式。

#### 1.1 BSP / 网格分割（规整分割，现有规划）
- **BSP 递归二分**：沿随机位置的水平/垂直切割线把矩形一分为二，递归直到面积/边长阈值；
- **网格切分**：切成 n×m 个均匀子房间；
- 子房间之间生成 `inner_wall`（复用 `BlockRoomGenerator._rasterize_boundaries_to_tiles` 的边界栅格化思路，或 dwellings 的 Edge 模型）；
- 相邻子房间之间生成 door item（复用 `_find_doors` 的门判定与 gap 挖洞逻辑）；
- 命名：`Parent_A/B/...` 或 `Sub_1/2/...`。

#### 1.2 功能房间分配
- 切分后按规则/比例分配 `room_type`：living / dining / bedroom / special / storage 等；
- 复用 dwellings 的标签语义（`tags.py`）或新增房间类型表；
- 支持"功能房间 + 走廊"组合：先横向切一条 corridor 带，再细分两侧（对应 dwellings 的 divideArea + `merge_corridors_like_js`，plan.py 已有）。

#### 1.3 膨胀接触分割（Competitive Room Growth）
> "设定 n 个房屋，生成 n 个矩形/圆形/不规则形状，互相膨胀，两个房间边缘一接触就不再继续膨胀，从而形成分割房间。"

- **算法**（类 Voronoi 竞争生长）：
  1. 在建筑区内放置 n 个**种子**（随机点，或指定位置/形状：小矩形/圆/不规则团块）；
  2. 所有房间**同时**向外生长（每轮按随机顺序，各自 BFS 前沿扩 1 格）；
  3. **接触即停**：两个房间的前沿相邻时，该方向双方都不再生长，并把接触边记录为"候选墙"；
  4. 停止条件：所有前沿无法再生长（触到建筑区边界、其他房间或预留走廊带）或达到迭代上限；
  5. 后处理：候选墙 → `inner_wall`（+ 门）；未覆盖的空白 → 走廊/填充。
- **特点**：天然填满整个建筑区、边界不规整（有机/蜂窝感）；先长大的房间占位大；每颗种子形状与生长顺序决定最终形状。
- **复用**：`BlockRoomGenerator._grow_room`（单房间 blob/snake 生长）改造为"多种子竞争版"；接触边界检测可复用 `_find_doors` 的相邻判定。
- 与 BSP 的关系：BSP = 规整切割；竞争生长 = 有机分割；按标签/参数选择（`mechanical` → 规整，`organic` → 竞争生长）。

#### 1.4 排屋 / 并列连续排列（Row Houses）
> "排屋 / 并列的一排房子 / 马厩——规则相同矩形连续排列。"

- **参数**：轴线方向（水平/垂直/沿道路）、单元数 n、单元宽 w、进深 d、墙厚 t、单元间距 gap。
- **两种归属模式**（按用途选）：
  - **一栋建筑内部**（排屋/马厩）：1 个 `building_area`，每个单元 1 个 room；相邻单元**共享墙**（共用 `inner_wall`，不重复画两格厚）；马厩变体：每个单元再切成若干 stall（小隔间）。
  - **多栋独立建筑**（并列的一排房子）：n 个 `building_area` 等距并排，彼此独立墙，由道路连接（与 §2 路网对接）。
- **生成**：沿轴依次排布 `[i*(w+gap), i*(w+gap)+w] × [0, d]`；共享墙生成复用边界栅格化；每个单元生成门朝向道路侧。
- 适用：城镇街区、马厩、仓库群、兵营。

#### 1.5 对称神殿 + 不对称延伸（Symmetric Temple）
> "矩形、圆形等组成的对称神殿，再延伸出不对称部分。"

- **对称主体**：由基本形（矩形/圆/正多边形）按对称规则组合成一个 `building_area`：
  - 左右镜像（对称轴 x = cx）、前后对称、十字双轴对称、中心旋转对称（180°）；
  - 流程：设计"半侧/四分之一"部件表（类型/尺寸/相对位置）→ 镜像展开 → 合并为建筑区几何（`geom_json` 多边形或组合房间）。
- **不对称延伸**：以概率从主体边缘长出附加 room（偏殿、侧翼、耳房）——用 1.3 生长或 BSP 扩展放置，不要求对称。
- **校验（可选）**：主体部分做镜像一致性断言（生成后对照镜像几何）。
- 适用：神殿、祭坛、宫殿中轴、圆形竞技场+附属建筑。

#### 1.6 破碎侵蚀 → 破墙（Fractured Erosion）
> "生成随机破碎图形，侵蚀房屋，形成不连续的破墙。"

- **与洞穴侵蚀（§3）的区别**：洞穴 = 掏空内部（space 变无）；破碎侵蚀 = **破坏墙体**（wall 出现断口/缺口），内部仍可走，形成残垣断壁。
- **算法**：
  1. 生成**破碎图形**：随机线段/折线（裂缝）、Voronoi 碎片（房间切成若干碎片后随机丢弃部分碎片）、噪声阈值带；
  2. 应用：与破碎图形相交的墙格被移除（wall 更新）；
  3. 约束：保留最小墙体比例（如 ≥40%）；承重点（柱位）保留；**门/窗附近优先保留**（否则门悬空）；可保留"完整墙体段"而非零散单格（连通段保留，避免碎成渣）。
  4. 可选输出：破碎处生成碎石/瓦砾 item（`stone`，见 §4）。
- `other_json.eroded='fracture'`，记录破碎参数（强度、保留比例、裂缝数）。

#### 1.7 与现有 dwellings 的关系
- `PlanDivider.divide(area, specs)` 已实现"把任意 area 切成 rooms + inner_walls"，可作为**有机切割**版本；
- 1.1（规整）、1.3（竞争生长）、1.4/1.5（结构化排布）与 dwellings 有机划分共存，由标签/参数驱动：`mechanical` → 规整/排布，`organic` → 竞争生长/dwellings。

#### 1.8 真实感房间分割：大小混合 / 走廊+房间 / 大厅+小房间 / 环形走廊（待办）
> 分割应遵循**比例**，一般房间应**有大有小**（不要清一色均分小块）。以下使建筑分割更符合真实情况的方法，生成时可从其中**随机选取一种或多种组合**。
- **走廊 + 房间**：一条走廊连通多个房间，且**房间后面还有房间**（纵深布局）；
- **大厅 + 小房间**：一个大房间（hub）联通多个小房间；
- **环形走廊**：大建筑内走廊可能形成**环形**，环的两侧分布很多房间；
- **混合**：建筑很大时，可能**既有走廊又有大房间**；
- **规模约束**：若只有大房间或只有小房间（单一类型），一般**不超过 5 个房间、最大 7 个**；
  - **"大房间"指 20x20 格起步**，尺寸无上限；
- **适用范围**：以上方法**可能只用于非斜体（轴对齐）房间**；斜体房间的斜向分割由
  `RoomSubdivider.subdivide_rotated_room`（局部坐标系 BSP，子房间/共享墙/门沿旋转方向）独立处理。

#### 1.9 地上建筑：入口门 → 门厅 / 大厅 / 餐厅 / 卧室 / 瞭望塔（树形规划，待办）
> 大型**地上建筑**（城堡 / 庄园 / 要塞 / 府邸）的内部规划：**从入口门开始**，规划
> **门厅 → 大厅 → 餐厅 → 私人卧室 → 瞭望塔**等功能房间，**通过树形发生器（tree generator）
> 规划整栋建筑**，**没有被规划的部分直接丢弃**。

- **树形规划模型**：
  - **根 = 入口门 + 门厅**（foyer / entrance hall）：全楼枢纽，至少 2 条出边（大厅 / 走廊 / 塔楼）；
  - **节点 = 一个功能房间**，**边 = 门 / 通道**（父子房间之间开门互通，落库 `role=interior`）；
  - 从门厅逐层向外生长分支：**大厅**（grand hall，集会/宴会大空间）→ **餐厅**（dining hall）→
    **私人卧室**（private bedrooms，小房间群）→ **瞭望塔**（watchtower，高层小房间，可叠层）……；
  - **丢弃未规划部分**：树只覆盖被规划到的房间；建筑区内**未被树覆盖的区域不生成房间/墙**
    （保留为空地 / 外围，而不是被默认填满）。
- **功能房间规格**（节点可带属性，尺寸约束复用 §1.8）：
  - 门厅 / 大厅 / 餐厅：**大空间**（"大房间"指 **20x20 格起步**、尺寸无上限）；
  - 私人卧室：**小房间群**（单一类型一般**不超过 5 个房间、最大 7 个**）；
  - 瞭望塔：小体量**高层**房间，可向上叠层（对接多层地图）。
- **与现有系统的关系**：树形规划作为"把一个 `building_area` 变成多个房间"的又一种方式，
  与 BSP/网格（1.1）、功能房间分配（1.2）、竞争生长（1.3）、dwellings 有机划分（1.7）并列，
  由标签/参数驱动选择（如 `castle` / `manor` / `tower` 标签）；
- **复用**：树节点 → 子房间落库沿用 `RoomSubdivider` 契约（共享单墙内墙 + 内部门）；
  房间几何可基于 1.1 的局部 BSP 递归或 1.2 的功能带切分生成；落地顺序见 §6 阶段表。

---

### 2. 道路生成（Road Generation）

#### 2.1 道路形态（Road Shape）

统一的内部表示：一条**路径**（细网格格点序列，`List[Cell]`）+ **宽度** w（1~3 格）→ 膨胀为道路带（space = 带内格，wall = 带边界格）。

| 形态 | 生成方式 | 典型用途 |
|---|---|---|
| **方形直道（straight）** | 轴对齐直线段；或 L 形/之字形曼哈顿路径（先水平后垂直，可带 1~2 个转折），宽度固定 | 门-门相连、主干道 |
| **扭曲道路（twisted）** | 带目标偏置的随机游走（向终点方向弯曲的概率更高）+ 可选的蛇形/曲率扰动；宽度固定 | 类真菌路网、分干道 |
| **桥（bridge，跨水）** | 道路带跨越水域：桥面 tiles + 桥墩（stone item）+ 两岸桥头（landing）；见 §2.4 | 岛屿/跨河连接 |
| **渡口/浅滩（ford，可选）** | 道路带穿越浅水，可通行但慢 | 岛屿/低水位连接 |

建议接口（将来在 `road_generator.py` 内实现）：
```python
def road_path(start: Cell, goal: Cell, mode: str, rng: RNG) -> List[Cell]: ...
def expand_to_band(path: List[Cell], width: int) -> Tuple[Set[Cell], Set[Cell]]: ...
```

#### 2.2 路网计算方式（Network Modes）

节点（Node）= 门（door item，已有 `position_x/y` 与 `connects_room_ids`）或建筑区**入口点**（需新增概念，见 §7.2）。三种算法：

**(A) 门/门相连（door_to_door）** —— 直接以门为节点建图
- 连接策略（可配置）：**最近邻贪心** / **MST**（全连通、总长最短）/ **Delaunay + 部分保留**（更随机）。
- 道路形态默认**方形直道**。

**(B) 类真菌寻路（fungus / mycelium）** —— 多前沿生长 + 合并
- 从多个"菌落种子"（建筑区入口）**同时生长**，带生命值（life）与方向，随机游走但**向最近未覆盖目标偏置**；分支概率；**相遇即合并**（避免重复建路）；
- 天然产生**蜿蜒扭曲**的道路网；
- 已有原型：`BlockRoomGenerator._generate_branching_corridors`（walker + life + branch），需补"目标吸引 + 相遇合并"，输出改为独立 road room。

**(C) 主干道 + 分干道（trunk_branch）** —— 分级（层次）路网
- **主干道（trunk，level 0）**：连接"主要建筑区"（面积大/层数多/标记 major），宽度大（w=2~3），尽量直；拓扑 = MST / Delaunay over 入口点；
- **分干道（branch，level 1）**：从主干节点出发连接到次要门/建筑区，宽度小（w=1~2），可扭曲；
- 形成**路网图** `road_graph = {节点: 路口/门, 边: 道路段, 边属性: level/width}`，便于后续扩展。

#### 2.3 连通性模式（Connectivity Modes）
> "道路实现全部联通、大部分联通、岛屿型地图；地图也可以是孤立建筑/单体建筑。"

`RoadGenerator` 增加参数 `connectivity_mode`：

| 模式 | 语义 | 实现要点 | 典型场景 |
|---|---|---|---|
| **`full`（全部联通）** | 任意两个 door 通过路网可达 | 全连通图（MST/完整连接）生成后做连通性断言；现有 §2.7 验收标准即此模式 | 城镇、地下城主层 |
| **`mostly`（大部分联通）** | 允许少量断点/孤门 | full 生成后按概率剪断**次要边**（优先分干道、低流量边），保留主干连通；约 5%~20% door 可能不可达 | 废墟、野外聚落 |
| **`island`（岛屿型）** | 路网被地形（水/悬崖）分割成 ≥2 个连通分量 | 每个岛屿内部 full；岛屿之间默认不连通，由**桥/渡口**（§2.4）或渡船按需连接；`road_graph` 记录分量列表 | 岛屿地图、被河流分割的城区 |
| **孤立建筑（isolated）** | 地图可以是孤立建筑/单体建筑 | `skip_isolated=True`：该建筑不参与路网（无跨区道路）；**单体建筑地图**（整张图就一个建筑区）时路网为空或只生成区内道路 | 单体住宅、独立塔楼 |

- 相关参数：`prune_ratio`（mostly 剪边比例）、`island_min_areas`、`crossing_types: ['bridge','ford','none']`。
- `road_graph` 需记录：节点集合、边集合、**连通分量**（供岛屿判定与后续桥接）。

#### 2.4 桥与渡口（Bridge / Ford）
- **桥**：`room_type='road'`，`other_json.road_kind='bridge'`；
  - 桥面 = 道路带（跨水部分）+ 两岸桥头（landing，接回普通道路）；
  - 桥墩 = 间隔支撑点（`stone` item，见 §4）；
  - `other_json.crosses_water_ids` 记录跨越的水体 item；
- **渡口/浅滩**：`road_kind='ford'`，直接穿越浅水区，宽度同普通道路；
- 生成时机：路网候选边若穿越水体 → 升级为 bridge/ford（依赖 §4/§5 水体生成，落地在阶段 5）；
- 与岛屿模式配合：`island` 路网的分量之间，只在**显式要求**时生成桥（`crossing_types` 控制）。

#### 2.5 数据落库设计

- 每条道路 = 一行 `room`：
  - `room_type = 'road'`
  - `tiles_json = {wall, space, inner_wall:[]}`（space=路带，wall=边界）
  - `geom_json = {type:'road', road_kind:'straight'|'twisted'|'bridge'|'ford', width:w, center, corners/bbox}`
  - `other_json = {generator:'road_generator_v1', network_mode:'door_to_door'|'fungus'|'trunk_branch', connectivity_mode:'full'|'mostly'|'island', road_level:0|1, width:w, connects_door_ids:[id1,id2], connects_building_area_ids:[...], crosses_water_ids:[...], component:0|1|...}`
  - `building_area_id = NULL`（跨建筑区）或 `所在建筑区 id`（区内连接）
- 路网图 `road_graph`（含连通分量）序列化存入 `map` 级 `other_json`（或独立表），供可视化与后续功能使用。
- 门仍留在 `item` 表；道路通过 `connects_door_ids` 反向引用。

#### 2.6 与现有系统的接口

- 输入：`(map_id, layer, connectivity_mode)`、该层所有 door item、所有 building_area 几何、**可走区域**
- 可走区域：`BuildingAreaGenerator._largest_free_polygon_for_layer`（地图 − 建筑区）**再减去水体/岩石占格**（可通行性，见 §0.2 与 §4.4）

#### 2.8 已落地的道路实现经验（v2，重要）

> **三套路网的选择（主程序统一入口 = `ConnectionSpec(mode='door_to_door', kwargs={...})`）**：
> `MapGenerator` 按 kwargs 里**有没有 `style`** 自动分发到两套生成器（机制都在 `src/generators/`，
> 配方只做配置）：
>
> | 想生成 | kwargs 写法 | 走哪个生成器 |
> |---|---|---|
> | **折角折线**（直线→L→Z/C，A* 兜底折角 ≤max_turns） | **不带** `style`：`kwargs=dict(width=5, dense_groups=..., max_turns=6)` | `RoadGenerator` v2（本节的 §2.8） |
> | **直角路网**（4 向 A* 右角折线） | `kwargs=dict(style="直角", density="稀疏", width=5)` | `RoadStyleGenerator`（§2.10/2.11） |
> | **弯曲路网**（8 向 A* + 圆角曲线） | `kwargs=dict(style="弯曲", density="稠密", width=5)` | `RoadStyleGenerator`（§2.10/2.11） |
>
> 生成脚本（demo）也可绕过配方直接实例化生成器调用；但**主程序按配方生成地图时，三种都
> 通过 `ConnectionSpec.kwargs` 指定**（见 §3 使用方法示例）。

> **折角入口与选项**：`RoadGenerator.generate_and_save_roads(map_id, layer=1, width=5, seed=None,
> dense_room_ids=None, dense_groups=None, dense_degree=2, max_turns=6)`。
> 主程序通过 `ConnectionSpec(mode='door_to_door', kwargs={...})`（不带 style）原样透传
> （见 `map_spec.py`），生成脚本与 `MapGenerator` 都只做**配置与调用**，机制全部在 `src/generators/road_generator.py`。

| 选项 | 默认 | 说明 |
|---|---|---|
| `width` | 5 | 路宽（道路带 = 中心线膨胀 ±width/2） |
| `layer` | 1 | 生成所在层 |
| `seed` | None | 路网种子；None 时受 MapGenerator 全局 seed 约束（同配方同 seed 可复现） |
| `dense_room_ids` | None | 稠密阶段房间 id 列表（区内多连） |
| `dense_groups` | None | 按大建筑区分组的稠密房间；**区内稠密、区际留给稀疏**（v2.2） |
| `dense_degree` | 2 | 稠密阶段每房间连最近 n 个（含冗余稠密阶段 0b） |
| `max_turns` | 6 | **A\* 兜底路径允许的最大折角数**（直线/L/Z/C 折线之外的避让兜底；密集塞房场景可调大） |

- **道路贴墙走（共用外墙）**：路径规划障碍 = **仅房间 space**（墙不是障碍）。
  道路带可以贴着房间外墙走、与房屋共用外墙，只禁止覆盖房间内部 space；
  `road_space = 带 − 所有房间格`（墙格保留在房间，不重复画）。这是密集布局下路网能否连通的**关键**。
- **稠密按大建筑区分组（v2.2，用户修正）**：稠密路网**只连接同一大建筑区内的房间/区内道路**
  （`dense_groups` 参数：按大建筑区分组的房间列表）；
  跨建筑区连接**只留给稀疏阶段**——否则"区内不稠密、区际反而稠密"（用户观察到的颠倒）。
  mega 验证：区内 10 直连 + 16 接入（稠密），跨区仅 9 条（稀疏 MST）。
- **路径障碍 = 纯内部格（space − wall）**：basic 模型 wall ⊂ space（墙格同时也是 space 格），
  若把整个 space 当障碍，带贴墙走时覆盖墙线会被误判冲突（房间 25 孤立的根因）。
  墙格允许被道路带覆盖（道路可与房屋共用外墙），只禁止覆盖房间**内部**格。
- **稠密路网算法（用户规则）**：不是"每房间连固定度"——
  - 每个房间尝试连接所有**不同分量**的目标（房间或道路），最近优先；
  - **避免冗余**：已连通的房间/道路不再重复连接（b2 已连 b1，则 r1/r2（都连 b1）也不再连）；
  - **门口复用**：一个房间的多条道路从**同一门洞分叉**（不重复开门），`_insert_door` 幂等（每房间一个 road_entrance 门）；
- **门洞幂等复用（v2.3）**：`_ensure_exterior_door` 检查该房间是否已有 road_entrance 门，
  有则**复用同一门洞**（不挖新洞）——否则每次连接都挖洞会造成"墙上多洞/空洞/一墙多门"
  （门洞挖了但 `_insert_door` 幂等跳过 -> 空洞）。实测每房间恰好 1 门。
- **门 = 半径覆盖（v2.3，用户规则）**：门洞 = 以"交界处原点"（洞中心）为圆心、
  半径 = 路宽/2 覆盖到的**外墙格**——覆盖到的外墙都是门（渲染黄色区块与洞一致）。
- **每房间最多 3 个外联门（v2.4，设定 1）**：一个建筑区建筑只有 ≤3 个往外联通的门
  （走廊/内室不算）；`_ensure_exterior_door` 已有 ≥3 门则复用，`_insert_door` 按门洞匹配幂等。
  实测：每房间门数 max 3。
- **道路折角 ≤6（v2.4，设定 2）**：直线 / L(1折) / Z/S(2折) / C(2折) 之外，
  0/1/2 折角全失败时用 **A* 避让路径**兜底（墙可走、房间内部格为障碍），压缩后折角 ≤6。
- **优先更直（v2.5，用户反馈）**：候选路径按生成顺序（直线 → L 形1折 → Z 形2折）依次尝试，
  **不随机打乱**——此前 `rng.shuffle` 会选出"折两次"的路径即使直线/L 形也能到达。
  实测：96% 道路为 1 折 L 形，仅少量需 2 折避让。
- **多候选重连（v2.6，用户反馈）**：稀疏阶段 `pick_targets` 返回**最多 4 个最近的不同分量
  候选**（房间或道路），最近的连不通时**依次尝试更远的**——保证"尝试所有建筑和道路的重新连接"，
  不再因单一最近目标失败而跳过（稀疏/密集塞房下也全连通）。
- **兜底外墙模型感知（v2.3）**：`_ensure_outer_walls` 直接判定 space 边界格是否被墙隔开
  （自身是墙 / 外侧邻格是墙 / 门洞入口），缺失才补内边缘墙——
  不依赖易误判的"wall 是否在 space 内"检测，避免双圈墙与漏补。
- 一轮尝试完所有目标，迭代直到稠密集全连通或无法进展（"不一定全需要连接成功"）。
- **交叉截断（v2.1，用户规则）**：路径穿过已有道路时**截断到第一个真交叉**——
  新道路只到第一条被穿过的道路为止（connects = (起点, 该道路)），不再穿过多条道路形成混杂交叉
  （b1→r1→r2→b2 只留 b1→r1）；稠密时若截断接入的道路已与起点连通则放弃（b1 不能自己连自己）。
- **门口共享不算交叉**：两条路从同一门出发在门口外（门洞向外 ROAD_WIDTH 格）重叠一段是正常的，
  该区域内与已有道路的重叠不记录为交叉（`_door_zone`）。
- **端点对去重**：同一 (起点, 终点) 连接只生成一次；被重复截断成同一条路时计数
  （如 b1 连 b2/b3/b4 都被 r1 截断成 b1→r1），计数达阈值（4）**停止该房间的所有尝试**。
- **子群互联**：稀疏后仍有多个分量时，用**最近的一对不同分量的道路**连接子群
  （道路之间空地多、阻挡少），连接同样走交叉截断。
- **外墙判定**：只有**有外墙**的房间才直接连路网（`_has_exterior_wall`）；
  最大建筑分割的内部子房间无外墙，通过**内部门**互通、由边缘子房间接入路网。
- **`_door_exterior_point` 关键修复**：外侧方向必须按"洞格邻格不在 space 且其反方向邻格在 space"
  （垂直于墙朝外）判断——basic 模型 wall ⊂ space，洞格沿墙邻格也在 space，
  若按"邻格在 space 即内侧"会把沿墙方向误判为内侧，导致道路带从错误点出发覆盖房间自己、
  路径全失败（孤立房间的根因）。
- **稀疏路网**：只需连通（每轮连最近不同分量，MST 式 + 道路互联），不需要稠密；
  独立子网出现时子网道路再与主网道路互联。
- **兜底外墙机制**：路网生成后对每个房间，space 的 8 邻不在 space 的格必须是墙
  （缺失的墙格除 door 洞外补入 wall）——保证"房间内区域与外界之间永远有外墙隔开"
  （`_ensure_outer_walls`，mega 地图实测补墙 1300+ 格）。
- **道路矢量渲染**：road 的 `geom_json` 含 `path`（中心线），可视化器沿 path 画**折线**（线宽≈路宽），
  而非 bbox 矩形——矢量跟着路的形状。
- **区内建筑 = 房间而非嵌套 building_area**：大建筑区内的 5-10 个建筑是 `room`（building_area_id=父建筑区），
  **不插入 building_area**——保证建筑区之间零重叠（嵌套建筑区会导致 building_area 互相"重叠"）。
- **尺度**：道路宽 5 + 门半径 2 + 房间 30x40 时，地图需 160x300 级别；间距不足会导致
  带 5 宽路径全冲突（表现为大量不连通分量）。
- 避障规则：
  - 跨区道路带不与 building_area / water / stone 相交；
  - 区内道路带被所属 building_area 多边形 `covers`；
  - 桥/渡口例外：允许穿越 water（在 §2.4 处理）。
- 与 BlockRoomGenerator 的关系：其 corridor 已是"道路即房间"的雏形；road_generator 复用其门查找与边界栅格化，但输出独立 road room、尺度任意。

#### 2.7 验收标准（实现时对照）

- 同 seed 结果完全可复现；
- 连通性断言按模式：
  - `full`：任意两个 door 可达（图遍历）；
  - `mostly`：主干连通 + 孤立门比例在 `prune_ratio` 内；
  - `island`：连通分量数 == 预期，分量间仅通过 bridge/ford 相连；
- 道路不与建筑区/水体/岩石相交（区内道路除外，且必须被包含；桥/渡口除外）；
- 道路带宽一致、边界无破洞（wall/space 完整）。

#### 2.9 路网风格分类：现代 / 中世纪城镇 / 中世纪乡村 / 远程城镇间（待办）
> 路网规划应按**风格**分类，不同风格采用**不同的实现方法**（形态、分级、密度、弯曲程度、
> 层数都不同），而不是同一套算法套所有场景。

- **现代路网**（分级体系 + 高架）：
  - **大**：高速公路 / 城市快速路——宽、直、少交叉（互通立交）；
  - **中**：城市主干道——双向多车道，连接大/小区块；
  - **小**：支路 / 街区道路——填充街区内部，接驳中/大；
  - **高架**：**多层道路**（不同 layer 的 road room）——地面道路与高架**立体交叉**，节点用
    互通立交 / 匝道连接（对接多层地图，§5.4 廊坊同类的叠层语义）；
  - 实现方法：**逐级规划（先大后小）**——先布干线骨架（大/中），再在区块内细化支路（小）；
    高架作为独立层级，与地面层在投影上交叉但不占用同一层 space。
- **中世纪城镇路网**（密集街巷）：
  - 形态：中心**广场/集市放射** + **环状**街 + 密集**巷弄**，道路窄、曲折自然、街块小；
  - 实现方法：放射+环状骨架 + 有机弯曲（类真菌生长 / 随机游走，§2.2 fungus）+ 巷弄细化，
    沿建筑区轮廓生长，窄路（1~2 格宽）。
- **中世纪乡村路网**（稀疏土路）：
  - 形态：**稀疏**——一条主干连接村庄/农舍/田地，支路稀少、天然土路弯曲；
  - 实现方法：**MST 式最少连接**（只保证可达）+ 随机弯曲土路 + 沿田地/河岸走向，
    宽度 1 格为主。
- **远程城镇间连接路网**（跨城镇长途）：
  - 形态：**区域间主干**（商路/驿道）连接各**城镇中心**，沿途过桥/渡口（§2.4），长直线段 + 大弯；
  - 实现方法：**分层规划**——先做区域级主干（连接各城镇入口），再在每个城镇内部做各自的
    城镇/乡村路网，主干与城内路网在**城镇入口点**对接（§7.2 建筑区入口点概念）。
- **落地**：`ConnectionSpec` / `MapSpec` 增加**风格参数**
  （`style='modern' | 'medieval_town' | 'medieval_village' | 'intercity'`），
  风格决定 §2.1 形态组合、分级层数与密度；§2.2 算法模式（door_to_door / trunk_branch / fungus）
  与 §2.3 连通性模式在风格内部可组合选用；验收对照 §2.7。

#### 2.10 视觉直走路网（demo 已落地：`test/generate_vision_road_map.py`）
> 与 RoadGenerator v2 并列的独立路网 demo：**400x600 地图 + 唯一 400x600 建筑区 + 10 个建筑**，
> 每个建筑外墙开一个门，用"**出门直走朝建筑群中心 + 视野扩大**"接入路网；
> 道路为**真贝塞尔矢量曲线**（PS 钢笔工具式），像素路从曲线精确栅格化。
> 输出：`test/output/vision_road_map/`（`vision_road.db`、`*_多层视图.pdf`、`preview_*.png`）。

- **接入流程**：
  1. 每个建筑在外墙生成一个门（door item，宽 3，门洞从墙移除）；
  2. 随机取两个门：**A\*（8 向、octile、小扰动弯曲）**直连，作为首条路；
  3. 其余门（随机顺序）：**出门直走**——前 2 格沿门口朝外；之后**尽量朝当前建筑群的中心直走**
     （8 向贪心曼哈顿下降，受阻绕行）；**不走回头路**（visited 集合）；
     **绕开建筑周围 2 格**（障碍 = 建筑格 + 8 邻外扩 2 圈，道路带距建筑 ≥2）；
  4. **视野 = 已走步数**（走 2 格即有 2 格视野，越走视野越广）；视野内看到路 → 接入；
     步数上限仍看不到 → 走兜底层级（见下）。
- **兜底层级（用户规则）**：主 A\*（扰动 0.15，弯曲）→ 超时/失败 → **更不随机的确定性 A\***
  （扰动 0、上限 150 万）→ 仍失败 → **BFS 直接计算**（有界、必然找到）。
  不会因为"找不到"跳过建筑——每栋建筑必然接入主路网（核验：10/10 连通分量 1）。
- **道路 = 真矢量贝塞尔曲线**：
  - waypoints → **LOS 简化锚点**（间距 ≤50 格）→ **Catmull-Rom 转分段三次贝塞尔**
    （锚点+控制柄，柄长钳制 ≤8 格，曲线紧贴路径、局部圆角）；
  - `geom.curve = {"type":"bezier","segments":[[P,C1,C2,Q],...]}`，`geom.path` 保留锚点折线；
  - **像素路 = 从矢量曲线精确计算**：贝塞尔按**平直度自适应细分**（de Casteljau，LIFO 先压后半段，
    否则点序反转栅格化出锯齿宽带）→ 逐段栅格化 + 带宽（曲线矢量化）；
  - 可视化用 matplotlib `Path + CURVE4` 画**真正的曲线原语**（PDF 内即贝塞尔，非折线）；
    旧 `curve` 点列表与无 `curve` 数据仍按折线回退绘制。
- **门口接通（用户修正）**：路矢量端点接到**门洞中心格**；像素带**并入建筑门洞格**、
  **裁剪建筑内溢出**（门洞格保留）、**墙格剔除建筑内格**——门口正前方不再被外墙挡住，
  核验 10/10 建筑门洞到路带距离 = 0。
- **无几何断连子网（用户修正）**：每条路声称的 connects 必须**几何相交**——
  视野接入的 A\* 汇入失败时**不再静默保留"走不到"的假连接**，走兜底层级到最近可达路格；
  **目标路格跳过障碍内的格**（并入带的门洞格被建筑包围、A\* 永远到不了）。
  核验：声称连接但几何相交为 0 的连接数 = 0。
- **交叉连接处打通**：新路墙格被已有路 space 覆盖 → 移除；已有路墙格被新路带覆盖 → 并入其 space
  （更新已有道路行）；核验任意两条路互相遮挡墙格 = 0。
- **8 向 A\* 长路径**：稀疏布局下两门/门到路可能相距 500+ 格，A\* 需展开大半个地图
  （240k 格）；主 A\* 设访问上限（超时即降级到下一级兜底），确定性 A\* 上限 150 万，BFS 无上限。

#### 2.11 分区大地图与路网选项（demo 已落地：`test/generate_mega_vision_road_map.py`）
> **1200x1200 大地图 + 6 个 300x400 建筑区**（函数随机排布）+ 每区 10 个建筑
> （**8 矩形 + 1 圆形 + 1 斜矩形**），区内/区际分层路网。
> 输出：`test/output/mega_vision_road_map/`。
>
> **机制内部化**：全部实现位于核心 `src/generators/road_style_generator.py`
> （`RoadStyleGenerator`：建筑区排布 / 建筑放置 / 任意形状开门 / 寻路（4·8 向 A* + 兜底 + BFS）/
> 视觉直走 / 圆角曲线 / 道路保存（门口接通·裁剪·交叉打通·大中小路宽）/ 区内·区际连接策略）；
> demo 脚本只做**配置与调用**。

**用户可选的路网选项（大中小 × 直角/弯曲 × 稠密/稀疏）：**

| 维度 | 选项 | 说明 | 本 demo 用法 |
|---|---|---|---|
| **路网大小** | **大路 / 中路 / 小路** | 分级路网概念：小路=区内支路（宽 5），中路=区际干路（宽 10），大路=跨城镇主干（规划，更宽） | 区内=小路（宽 5），区际=**中路**（宽 10） |
| **路网风格** | **直角 / 弯曲** | 直角=4 向 A\* 右角折线路（横平竖直）；弯曲=8 向 A\* + 视觉直走 + **圆角曲线**（转角圆弧、**无过冲突起**） | 2 区直角、2 区弯曲 |
| **路网密度** | **稠密 / 稀疏** | 稠密=每建筑连最近 3 个（网）+ 连通保证；稀疏=树（每建筑连最近已连通） | 2 区稠密、2 区稀疏 |
| **建筑区排布** | **函数随机排布** | `place_areas`：地图分 2x3 网格单元 + 单元内随机抖动 + 区号随机分配（保证区距、必然放得下） | 6 区 |
| **建筑类型** | **矩形 / 圆形 / 斜矩形** | 圆形=半径栅格化；斜矩形=旋转角栅格化（斜边门用**8 邻外向**检测，门外一点不会算进建筑里） | 每区 8+1+1 |
| **区际连接** | **折角中路 / 孤立** | 每区先找**区内主路**（度最大的路 = 树中最根节点），区际**从主路中心接出**（`find_main_roads` + `generate_inter_area_roads`），折角路径（直线→L→Z/C→A* 兜底，折角≤max_turns）；可留若干区孤立 | 6 区全部连接（折角中路，宽 10） |

> **路级别不硬编码（v2.7，用户要求）**：大/中/小路只是**宽度参数**（默认
> `ROAD_SMALL_W=5 / ROAD_MED_W=10 / ROAD_LARGE_W=15`，可换任意值），生成方式（折角/直角/弯曲 ×
> 稀疏/稠密）同样每次指定——`connect_area` / `connect_inter_area` 的 `style`/`density`/`width`
> 全是参数，`connect_inter_area` 不再写死"弯曲"（默认仍弯曲，可传 `style="直角"` 等）。

> **区内主路识别（v2.7，用户要求）**：`RoadGenerator.find_main_roads(map_id, layer)` 把每个建筑区的
> 区内路网按树理解（叶子 = 房屋，父节点 = 路），主路 = **被最多其它路作为端点引用的路**
> （度最大的路 ≈ 树的最根节点）；区际连接 `generate_inter_area_roads(main_roads=...)`
> 从主路 space 中心接出（上级路用下级路的主路作为起点），而非"选离区中心最近的建筑门"。

> **门外点必须避开障碍（v2.7 修复）**：`make_door` 的 `blocked` 此前**只存签名没用**，斜矩形/圆形
> 斜边墙的门外点（`door_exterior` 取门外 `width//2+1` 格）可能落进**别的建筑或外扩圈** →
> A* 起点在障碍里、该建筑所有连接失败（mega 六风格地图实测 B4_10 斜矩形孤立）。修复：
> 选门洞时用 `dmid` + `out_dir` 计算门外点并检查不在 `blocked`（传 `gen.obstacles` = 建筑格+外扩圈，
> 而非 building_cells）。核验：60/60 建筑全连通。

> **区内多分量补连不放弃（v2.7 修复）**：`_ensure_area_connected` 此前最近跨分量对路径失败即
> `break`，该分量永远连不上（斜矩形孤立根因之一）——改为**跳过失败对、依次尝试次近跨分量对**。

> **直角/弯曲经主程序指定（`ConnectionSpec.kwargs` 带 `style` 即可，见 §2.8 顶部选择表）**：
>
> ```python
> connection=ConnectionSpec(mode="door_to_door", kwargs=dict(
>     style="直角",            # "直角"（4 向 A* 右角折线）| "弯曲"（8 向 A* + 圆角曲线）
>     density="稀疏",          # "稀疏"（树形，每建筑连最近已连通 + 连通保证）
>                              # "稠密"（每建筑连最近 dense_k 个 + 补连）
>     width=5, seed=7, dense_k=3,   # 稠密强度：dense_k 默认 3
> ))
> ```
> 不写 `style` 就是**折角折线**（`RoadGenerator` v2，选项见 §2.8 表格：`dense_groups`/`dense_degree`/`max_turns`）。
> 三种方式共享同一 `ConnectionSpec` 入口，`MapGenerator` 自动分发；稠密/稀疏在**直角/弯曲**里是
> `density` 参数（区内二选一），在**折角**里是分层阶段（稠密= `dense_groups` 分组多连，
> 稀疏= 全区 MST 恒跑，见 §2.8）。

- **实现要点（用户修正记录）**：
  - **门外一点必须避开建筑**：旋转矩形/圆形等斜边墙的外向是**对角**方向，用 4 邻检测会把门外一点算进建筑内
    （A\* 终点在障碍里 → 连接失败）——改为 **8 邻外向 + 径向匹配**；
  - **转角无突起**：弯曲路用**圆角**（直线段 + 转角圆弧，控制点=角点，无 Catmull-Rom 过冲/钩子），
    直角路保持右角折线（退化贝塞尔段）——消除"先左转再左转"和"汇入侧路突起"；
  - 门洞格并入路带（门口接通）、墙格剔除建筑内格、交叉连接处打通——同 §2.10。

---

### 3. 洞穴房间生成（Cave Generation）

洞穴仍是房间（`room_type='cave'`），必须位于建筑区内。三条路线：

#### 3.1 道路侵蚀算法（Road Erosion）
- 对**道路带**（或矩形房间）施加"侵蚀"，让边缘风化、破碎化：
  - **细胞自动机（CA）侵蚀**：区域内按密度随机撒填充，迭代若干轮（如 `B5678/S45678` 类规则）→ 边缘锯齿化；
  - **距离场侵蚀**：到墙的距离场 + 噪声 → 阈值化得到不规则边界。
- 输出可仍是 `room_type='road'`（带 `eroded:true`）或升级为 `'cave'`。

#### 3.2 溶蚀矩形房间 → 洞穴（Rectangle → Cave）
- 输入：矩形房间（wall/space tiles）
- 步骤：
  1. 生成噪声场（Perlin/单纯形，或随机点插值）覆盖矩形；
  2. 阈值化 + CA 平滑 → 洞穴形状；
  3. **外墙保护**：用 wall mask 约束，溶蚀不得穿透建筑区外墙（保留最小间距）；
  4. 重算 wall/space/inner_wall，`room_type='cave'`，`other_json.cave_mode='dissolve'` 记录参数。
- 变体：部分溶蚀（保留部分矩形骨架）+ 溶蚀出的洞穴通道。

#### 3.3 不规则生成洞穴（Irregular Cave）
- 直接生成而非先有矩形：
  - **随机游走填充**（random walk fill）；
  - **Voronoi 合并**：撒点 → 保留部分 cell → 合并/平滑；
  - **CA 随机密度场迭代**（经典洞穴生成）；
  - **中点位移 / 分形边界**。
- `other_json.cave_mode='irregular'`。

#### 3.4 洞穴与道路、破墙的衔接
- 洞穴洞口 = 特殊的门口（door item），road 可连接洞穴；
- 洞穴的 `geom_json` 记录洞口位置，供 RoadGenerator 作为端点；
- 与 1.6 破碎侵蚀的区别与组合：洞穴掏空内部、破碎破坏墙体；两者可叠加（先溶蚀成洞穴，再对残留墙体做破碎侵蚀）。

---

### 4. 物体系统：水与石（Object System）

> 在现有 `item` 表（door/window/stairs）基础上扩展新 `item_type`，物体可位于地图任意位置，作为地形/障碍/装饰。

#### 4.1 物体数据模型（复用 item 表，零迁移起步）

| item_type | 语义 | vector_json | tiles_json | properties_json 关键字段 |
|---|---|---|---|---|
| `water` | 水体（河流/湖泊/瀑布/水井） | 河流：polyline；湖泊：polygon；瀑布：线段 | 占格（水体格） | `{kind, flow_direction, depth, passable:false}` |
| `stone` | 岩石/碎石/桥墩/承重柱 | circle / polygon（巨石） | 占格 | `{kind:'rock'|'rubble'|'pillar'|'bridge_support', passable:false}` |
| `deco`（可选） | 装饰（路灯/火把/雕像等） | 点 | 占格（可空） | `{kind, passable:true}` |

- 复用 `ItemGenerator.save_item` 与可视化 item 层；`item_type` 是 TEXT 列，无需迁移。
- 后续若地形复杂度上升（需要批量查询/空间索引），再评估抽独立 `terrain` 表（阶段 5 时决策）。

#### 4.2 水（Water）
- **河流**：沿"路径"（复用道路路径算法，但结果不可通行）生成水体带；起点/终点在地图边界或高层入口；
- **湖泊**：圆形/不规则多边形水体（复用洞穴不规则生成做水域轮廓）；
- **瀑布**：跨层水体——水从 layer L 流到 L-1（在楼梯/洞口处）；item `layer_start/layer_end` 表达落差；
- **水井/水池**：建筑区内的小水体（房间生成时避让）。

#### 4.3 石（Stone）
- **随机散布**：装饰性岩石（草地/废墟）；
- **沿墙堆砌**：废墟/城墙根碎石；
- **结构化**：桥墩（与桥配套）、承重柱（破碎侵蚀时保留的柱位）、瀑布底岩石。

#### 4.4 可通行性与避障集成
- 统一可通行性查询（建议抽公共工具 `passability.py`）：`is_walkable(map_id, layer, x, y)` = 非 wall/inner_wall/water/stone；
- RoadGenerator 避障叠加 water/stone（§2.6）；
- RoomGenerator / 分割器在建筑区内避让水体（河道预留，见 §5.3）；
- 可视化：water 蓝色、stone 灰色（新增配色，见 §7.1 可视化缺口）。

---

### 5. 水系统整合（Water Systems：河流 / 瀑布 / 桥 / 河上建筑）

> "之后水穿过建筑形成河流和瀑布，以及跨越河流的桥（道路）和多层廊坊（河上建筑）。"

#### 5.1 河流与湖泊（River / Lake）
- 生成顺序（宏观流水线）：建筑区 → **水体** → 房间/道路（见 §0.6）；
- 河流可**切过建筑区**：水体生成时把建筑区内部的河道区域标记出来 → 建筑区内的房间生成**避让河道**，形成"滨水房间/河道房"；
- 数据：河道区域 = 一组 water item 占格；建筑区 `other_json.water_channels` 记录穿过的河道（供房间/道路查询）。

#### 5.2 瀑布（Waterfall）
- 垂直落差：水从高层（layer L）沿洞口/楼梯落到低层（L-1）；
- 生成：瀑布位置 = 河流路径与楼梯/洞口交点；跨层 water item + 可选落水声/特效（渲染）；
- 数据：`properties_json.kind='waterfall'`，`layer_start/layer_end` = 落差范围，`tiles_json` 记录上下层落水格。

#### 5.3 水穿过建筑（Water Through Buildings）
- 两种形态：
  - **河道房**：河流直接穿过建筑内部，房间沿河布置（建筑区预留河道 + 房间避让 + 沿河开门）；
  - **架空/底层通道**：建筑底层为河道通道（如 5.4 廊坊），上层为正常房间。
- 与楼梯的配合：瀑布/河道在多层建筑中贯通各层。

#### 5.4 多层廊坊（河上建筑，Arcade / Over-River Building）
- 建筑区**跨河而建**：building_area 与河道相交；
- 底层：架空（桥/通道/码头），与两岸道路或桥连通；
- 上层：正常房间（住宅/商铺/廊坊），多层；
- 生成：BuildingAreaGenerator 扩展"跨水建筑"模式——生成时检测水域，底层挖空河道通道（水仍可见），上层按普通多层生成；`other_json.kind='arcade'`。

#### 5.5 与道路/桥的衔接
- **桥 = 道路的一种**（§2.4）：跨越河流连接两岸路网；桥头与廊坊底层/岸边道路对接；
- 岛屿型地图（§2.3 island）的岛屿之间由桥/渡口连接；
- 廊坊底层通道可视为"建筑区内道路"（`road_kind='arcade_passage'`）。

---

### 6. 实施顺序建议（阶段）

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **阶段 0（前置重构）** | ✅ **已完成**：RNG 种子化（MapGenerator 层全局 seed，零侵入）；迁移机制（migrations.py + v001）；MapSpec 配方架构 + MapGenerator 执行器；PassabilityIndex 可通行性基础；建筑区入口点（待补）；坐标约定文档化；room_type/other_json 契约（文档） | 无 |
| **阶段 1** | 房间分割与生成扩展：BSP/网格 → 膨胀接触分割（1.3）→ 排屋（1.4）→ 对称神殿（1.5）→ 破碎侵蚀（1.6）→ 真实感分割 1.8（走廊+房间 / 大厅+小房间 / 环形走廊）→ 地上建筑树形规划 1.9（门厅/大厅/餐厅/卧室/瞭望塔） | 阶段 0 |
| **阶段 2** | 道路生成：door_to_door ✅（RoadGenerator v2：优先更直折线 + 多候选重连 + 稠密/稀疏分层，§2.8）→ trunk_branch → fungus（规划中）；连通性模式（full/mostly/island/孤立）；road room 落库 + 避障 → 路网风格分类 2.9（现代大中小高架 / 中世纪城镇 / 中世纪乡村 / 远程城镇间）→ 视觉直走路网 demo 2.10（已落地：出门直走朝中心 + 视野接入 + 贝塞尔曲线矢量 + 兜底层级）→ 分区大地图 demo 2.11（已落地：大中小×直角/弯曲×稠密/稀疏） | 阶段 0/1（门端点） |
| **阶段 3** | 洞穴生成：道路侵蚀 → 矩形溶蚀 → 不规则 | 阶段 0 |
| **阶段 4** | 物体系统：water/stone item、可通行性避障集成、可视化配色 | 阶段 0 |
| **阶段 5** | 水系统整合：河流/湖泊/瀑布、水穿建筑、桥/渡口（依赖 §4 水体）、多层廊坊 | 阶段 2/4 |
| **阶段 6** | 整合与测试：复现性、连通性（按模式）、避障断言、road_graph 分量校验、组合场景（岛屿+桥+廊坊） | 1~5 |

---

## 当前代码审核（为上述规划做预备）

### 7.1 模块现状总览

| 模块 | 现状要点 | 对道路/洞穴/分割/物体的可复用点 | 需要改进（缺口） |
|---|---|---|---|
| `db/database.py` | V2 id-first、dict 行、FK、WAL、`execute/fetch_*` | 基础设施良好，直接复用 | `migrate()` 引用 `db.migrations` 包，**该包不存在**（调用即 ImportError）；需补迁移机制或移除引用 |
| `db/building_area_dao.py` | 显式列名查询/插入 | area 读写封装完整 | 无 |
| `generators/building_area_generator.py` | STRtree 避障、最大空闲区、多层放置、旋转矩形 | `_largest_free_polygon_for_layer` 可充当道路"可走区域"；`_area_row_to_geom` 重建 area 几何 | **无"建筑区入口/门口点"概念**；无"组合建筑区"（神殿）与"跨水建筑"（廊坊）模式 |
| `generators/room_generator.py` | 每 area 一个房间（圆/多边形），迷宫内墙 | tiles 结构即道路/洞穴/破墙载体；`_tiles_for_polygon` 栅格化可复用 | 使用**全局 random**（不可复现）；room_type 无 road/cave；分割/侵蚀/避水逻辑未接入 |
| `generators/block_room_generator.py` | Watabou 粗格（scale=10）走廊+房间+门；door gap 挖洞；孔合并；轮廓提取 | **corridor 分支 walker 是"类真菌寻路"原型**；`_grow_room` 是"膨胀接触分割"的单房间原型；`_find_doors` + gap carve 可复用于道路/分割的门；`_cells_to_outer_loop` 轮廓提取 | 粗格尺度固定 10；corridor 未作为独立 road room 落库；门为粗格 key，无跨建筑区语义 |
| `generators/road_generator.py` | RoadGenerator v2（§2.8）：优先更直折线（直线/L/Z/C）+ A* 兜底（折角≤max_turns）+ 稠密/冗余稠密/稀疏/子群互联四阶段 + 多候选重连 + 门口复用 + 兜底外墙 + 道路矢量折线落库；`find_main_roads` 区内主路识别 + `generate_inter_area_roads` 区际折角连接（主路接出） | 节点图式路网（房间+道路统一并查集）；`ConnectionSpec.kwargs` 透传选项；`MapGenerator._generate_roads` 只做配置与调用 | 连通性模式 mostly/island/孤立未实现；避障只对房间内部格（water/stone 待接 §4.4） |
| `generators/road_style_generator.py` | RoadStyleGenerator（§2.10/2.11）：建筑区排布/建筑放置/任意形状开门（门外点避障）/4·8 向 A* + 兜底 + BFS/视觉直走/圆角曲线/道路保存/区内·区际连接策略（style 参数化不硬编码） | 直角/弯曲路网的机制本体：demo 脚本直调，或主程序经 `ConnectionSpec.kwargs` 带 `style` 分发（§2.8 选择表） | 区际连接策略仅 demo 直调使用；`style`/`density`/`width` 每次传入（路级别不硬编码） |
| `generators/dwellings_core/` | Dwellings.js 迁移：RNG / plan.py（divideArea、mergeCorridors）/ house.py（connectRooms、wallDoors）/ shape.py（Edge、Dir、outline_edges、contour2area）/ specs / tags | **`PlanDivider.divide` = 有机房间分割**；`connect_rooms_js` = 门连接；shape 几何基元库 = 道路/洞穴/破碎的底层工具；`RNG` = 可复现随机 | 局部粗格坐标 + origin 偏移，与细格世界坐标并存，道路/水体生成需统一换算（工具已部分存在） |
| `generators/dwellings_house_generator.py` | dwellings 输出写 room + door/window/stairs item；edge_key → 世界坐标 | **door item 的 position/edge_key 是道路端点的自然来源**；`_edge_center_world` 坐标换算 | 每个 building_area 独立生成，无跨建筑区概念 |
| `generators/item_generator.py` | 每房间随机门 + 外墙窗 + 楼梯分组；全局 space 索引 | `save_item` 可直接复用于 water/stone（item_type 扩展）；外墙判定思路可复用于道路边缘检测 | `generate_door_for_room` 是**随机门**（无 edge_key），不能作为道路的确定性端点输入；无 water/stone 生成逻辑 |
| `visualization/map_visualizer.py` | 5 层 imshow 掩码渲染；门 scatter | room_grid 层可直接渲染 road/cave（按 room_type 配色）；item 层可渲染 water/stone | 需新增 road/cave/水/石配色（按 room_type / item_type 或 other_json）；road_graph 可视化（可选） |
| `src/__init__.py` | 导出 6 个类 | — | 新生成器（RoadGenerator / CaveGenerator / RoomSubdivider / RoomGrower / RowHouseGenerator / TempleGenerator / FractureEroder / ObjectGenerator / WaterSystem）需加入导出 |

### 7.2 关键缺口清单（阶段 0 待办）

1. **统一 RNG**：阶段 0 采用**零侵入策略**——`MapGenerator.generate()` 入口对全局 `random` / `np.random` 做 `seed(spec.seed)`，同配方同 seed 完全可复现（已由 `test_map_spec.py` 验证）；dwellings 内部使用自身 RNG(seed)。后续阶段再把各生成器逐步改为注入式 RNG。
2. **room_type 扩展与元数据约定**：明确 `'road'` / `'cave'` 及 `other_json` 字段规范（road_kind / network_mode / connectivity_mode / road_level / cave_mode / eroded / subdivide_parent 等），写进本文档作为契约。
3. **item_type 扩展与物体契约**：`water` / `stone` / `deco` 的 vector/tiles/properties 字段规范（§4.1 表格），作为 ObjectGenerator 的落库契约。
4. **建筑区入口/门口点（entrance）**：新增概念——每个建筑区至少一个入口，供道路连接；实现方式：building_area 增加 entrance 字段，或从已有 door item 推导。
5. **可通行性（passability）**：抽公共模块 `passability.py`：`is_walkable(map_id, layer, x, y)`，统一 wall/inner_wall/water/stone 的不可通行判定；道路避障、房间避水、未来寻路共用。
6. **坐标统一**：确立"细网格世界坐标为 room/road/cave/object 默认坐标系"；粗格仅限 dwellings 内部，提供双向换算工具（复用 `fine_cells_to_coarse_area` / `coarse_cells_to_fine_set` / `_coarse_cells_to_world_corners`）。
7. **道路避障几何工具**：整理 shapely 工具函数（多边形差集、缓冲、`covers` 包含校验）到公共位置（部分逻辑散落在 building_area_generator 中）。
8. **迁移机制**：✅ **阶段 0 已完成**——`db/migrations.py`（`_registry` + `v001_init` 建四张核心表），`migrate()` 幂等可运行；后续新列/新表一律追加 `v00x` 迁移。
9. **测试基线**：✅ 阶段 0 已建立 `test/test_map_spec.py`（迁移幂等、同 seed 复现、preset 跑通、passability）；后续为每个新生成器扩展复现性/连通性/避障断言（参考现有 `test_parity*.py` / `test_merge_corridors_like_js.py`）。

### 7.3 阶段 0 重构进度（已落地）

| 项 | 状态 | 落地内容 |
|---|---|---|
| 迁移机制 | ✅ | `src/db/migrations.py`：`_registry.MIGRATIONS` + `v001_init`（map/building_area/room/item 四表，幂等）；`migrate()` 不再 ImportError |
| MapSpec 配置架构 | ✅ | `src/generators/map_spec.py`：四个配方单元（BuildingArea/Interior/Connection/Decoration Spec）+ `MapSpec`（含 seed）+ `PRESETS`（villa/village/dungeon/ufo 四主题示例） |
| 配方执行器 | ✅ | `src/generators/map_generator.py`：`MapGenerator.generate(spec)` 种子化流水线（建筑区 → 房间 basic/maze/watabou/dwellings → 门/窗/楼梯去重；连接/修饰规划中警告跳过） |
| 可通行性基础 | ✅ | `src/generators/passability.py`：`PassabilityIndex.is_walkable(map_id, layer, x, y)`（room space 可走；water/stone 占格不可走；按层缓存） |
| RNG 种子化 | ✅（零侵入） | `MapGenerator` 入口 `random.seed` + `np.random.seed`；dwellings 用自身 RNG 派生种子；同配方同 seed 复现已测试 |
| 兼容性修复 | ✅ | `item_generator.py`：`building_area_id=None` 的房间（Watabou 全图模式）生成门/窗不再崩溃 |
| 房间分割器 | ✅ | `RoomSubdivider`：BSP 二分 + 共享单墙内墙 + 内部门（mega 地图：最大建筑分割 5 房、7 内部门） |
| 道路稠密/稀疏分层 | ✅ | `RoadGenerator`：稠密（避免冗余 + 门口复用）+ 冗余稠密（dense_degree）+ 稀疏（多候选重连）+ 子群互联 + 兜底外墙 + 道路矢量折线（§2.8）；选项经 `ConnectionSpec.kwargs` 透传 |
| 测试 | ✅ | `test/test_map_spec.py` 6 项 + `test/test_road_generator.py` 3 项全过；既有测试无回归 |

**阶段 0 剩余待办**：建筑区入口点（entrance）概念、坐标统一工具抽取、道路避障几何工具整理（见 §7.2-4/6/7）。

---

## 使用方法

### 1. 基本地图生成（V2，id-first）

```python
from src import (
    DatabaseManager,
    RectangleBuildingAreaGenerator,
    CircleBuildingAreaGenerator,
    RoomGenerator,
    ItemGenerator,
    MapVisualizer,
)

# 初始化数据库
db = DatabaseManager()

# 插入地图，拿到 map_id（V2 全部使用 id 关联）
map_id = db.insert_map("测试地图", 100, 100)

# 生成圆塔（跨 1-3 层）
circle_gen = CircleBuildingAreaGenerator("三层塔", map_id, 1, db)
circle_gen.create_building_area(
    name="三层圆塔",
    layer=(1, 3),          # 跨 1-3 层
    radius_range=(5, 5),   # 固定半径 5
    max_attempts=200,
)

# 生成旋转矩形
rect_gen = RectangleBuildingAreaGenerator("旋转矩形", map_id, 1, db)
rect_gen.create_building_area(
    name="旋转矩形房间",
    layer=(1, 2),
    rect_size=[(10, 15), (10, 15)],  # 固定 10x15
    angle=[45],                      # 固定 45 度
    max_attempts=200,
)

# 为所有建筑区生成房间（V2 按 map_id）
RoomGenerator(db).generate_and_save_rooms(map_id)

# 生成门/窗/楼梯
ItemGenerator(db).generate_and_save_all(map_id)

# 绘制并保存地图
vis = MapVisualizer(db)
fig = vis.draw_map(map_id, layer_index=1, show_grid=True,
                   show_building_areas=True, show_area_names=True)
vis.save_map(fig, "测试地图_层1", formats=["png", "pdf"])
```

### 2. 运行测试脚本

```bash
cd refactored
python -m pytest test -v          # 运行 test/ 下全部测试
python test/test_combined.py      # 组合测试（房间与门 + 建筑区压力）
python test/test_road_generator.py      # RoadGenerator v2 单元测试（连通/门/孤立门清理，见 §2.8）
python test/test_road_generation.py     # 道路生成端到端脚本（6 房间 + 道路连通核验）
python test/generate_rect_fill_map.py      # 矩形房间填充地图（旋转矩形先放 + 稀疏/稠密路网，见 §1.8/2.9）
python test/generate_vision_road_map.py    # 视觉直走路网 demo（贝塞尔曲线路，见 §2.10）
python test/generate_mega_vision_road_map.py  # 1200x1200 分区大地图（大中小×直角/弯曲×稠密/稀疏，见 §2.11）
python test/generate_mega_six_style_map.py    # 1200x1200 六风格测验：6 区×（折角/直角/弯曲 × 稀疏/稠密）+ 区际折角主路连接
```

测试输出目录：`refactored/test/output/`（含 `room_and_door`、`building_area_stress`、`block_room_test`、
`rect_fill_map`、`vision_road_map` 等子目录）。

### 3. 按配方生成地图（MapSpec，阶段 0 新增）

```python
from src import DatabaseManager, MapGenerator, MapSpec, PRESETS

db = DatabaseManager()
gen = MapGenerator(db)

# 方式一：直接构造配方
spec = MapSpec(
    name="我的地图", width=120, height=120, layers=1, seed=42,
    areas=[
        BuildingAreaSpec(generator="rectangle", count=4, kwargs=dict(rect_size=[(6, 6), (14, 14)])),
        BuildingAreaSpec(generator="circle", count=2, kwargs=dict(radius_range=(4, 7))),
    ],
    interior=InteriorSpec(mode="maze"),
    # ---- 三种道路任选其一（MapGenerator 按 kwargs 有无 style 分发）----
    # ① 折角折线（RoadGenerator v2，§2.8）：不带 style
    connection=ConnectionSpec(mode="door_to_door", kwargs=dict(
        width=5, layer=1, seed=7,
        dense_groups=[[...]],   # 稠密分组（可选）：按大建筑区分组的稠密房间
        dense_degree=2,         # 稠密每房间连最近 n 个（可选）
        max_turns=6,            # A* 兜底最大折角（可选）
    )),
    # ② 直角路网（RoadStyleGenerator，§2.10/2.11）：style="直角"
    # connection=ConnectionSpec(mode="door_to_door", kwargs=dict(
    #     style="直角", density="稀疏", width=5, seed=7,
    # ))
    # ③ 弯曲路网（RoadStyleGenerator，§2.10/2.11）：style="弯曲"
    # connection=ConnectionSpec(mode="door_to_door", kwargs=dict(
    #     style="弯曲", density="稠密", width=5, seed=7, dense_k=3,
    # )),
)
result = gen.generate(spec)
print(result)  # {map_id, name, seed, areas, rooms, items, roads, warnings}

# 方式二：直接用主题预设（带随机性，seed 固定则结果固定）
res = gen.generate(PRESETS["village"](seed=7))
res = gen.generate(PRESETS["dungeon"](seed=7))
res = gen.generate(PRESETS["villa"](seed=7))    # dwellings 室内，多层
res = gen.generate(PRESETS["ufo"](seed=7))
```

---

## 核心 API

### BuildingAreaGenerator（基类）
- `create_building_area(name, map_id, layer, ...)`：创建单个建筑区
- `check_shape_overlap(shape, shape_type, layer, distance)` / `check_multi_layer_overlap(...)`：重叠检测
- `calculate_free_areas(layer_index, map_width, map_height, distance)`：最大空闲区

### RoomGenerator
- `generate_and_save_rooms(map_id) -> int`：为 map 下所有建筑区生成房间
- `generate_room_in_building(building_area_id, ...) -> dict|None`：内存中生成单个房间数据
- `save_room(room_data) -> room_id|None`：落库
- `maze_room(room_id, maze_width=1, complexity=0.5) -> bool`：迷宫化

### BlockRoomGenerator
- `generate_and_save(map_id=..., layer=..., building_area_id=None, target_rooms=40, mode='corridor', merge_holes=False, door_gap=None)`：粗格房间+门一次性落库

### ItemGenerator
- `generate_and_save_all(map_id, doors=True, windows=True, stairs=True)`：批量生成门/窗/楼梯（后续扩展 water/stone）

### DwellingsHouseDBWriter
- `generate_and_save_dwelling(building_area_id, seed, tags_raw, n_floors=None)`：住宅生成并落库

### MapGenerator（阶段 0 新增）
- `generate(spec, migrate=True) -> dict`：按 MapSpec 配方生成整张地图（种子化、可复现），返回统计与警告
  （`{map_id, name, seed, areas, rooms, items, roads, warnings}`；`roads` 含 roads/doors/connected/components）

### RoadGenerator（阶段 2 已落地，见 §2.8）
- `generate_and_save_roads(map_id, layer=1, width=5, seed=None, dense_room_ids=None, dense_groups=None, dense_degree=2, max_turns=6, only_room_ids=None, astar_max_steps=80000)`：节点图式路网（稠密 + 冗余稠密 + 稀疏 + 子群互联），返回统计
- 形态：优先更直（直线 → L 形1折 → Z/C 形2折），A* 兜底折角 ≤ `max_turns`；主程序经 `ConnectionSpec.kwargs`（不带 style）透传
- `find_main_roads(map_id, layer=1)`：每建筑区找**区内主路**（被最多其它路引用的路 = 树的最根节点），返回 `{area_id: road_id}`
- `generate_inter_area_roads(map_id, layer=1, main_roads=None, width=10, seed=None, max_turns=40, astar_max_steps=1500000)`：**区际折角连接**——从各主路 space 中心接出，路径中心线避障、保存剪建筑格（上级路用下级路的主路）

### RoadStyleGenerator（直角/弯曲，见 §2.10/2.11）
- 主程序接入：`ConnectionSpec.kwargs` 带 `style="直角"|"弯曲"` 即由 `MapGenerator` 调
  `connect_area`（在现有房间上开门连路），选项 `density="稠密"|"稀疏"`、`dense_k`（稠密每建筑连最近 n 个，默认 3）
- 直接调用（demo 方式）：`connect_area(area, doors, style, density, width, rng, dense_k=3)` /
  `connect_inter_area(areas_info, connected, isolated, width, rng, style="弯曲")` /
  `finalize_road_walls()` / `render_pdf(...)`；`style`/`density`/`width` 每次传入，路级别不硬编码

### MapSpec / PRESETS（阶段 0 新增）
- `MapSpec(name, width, height, layers, seed, areas, interior, connection, decoration)`：地图配方
- `PRESETS['villa'|'village'|'dungeon'|'ufo'](seed=...)`：主题预设工厂

### PassabilityIndex（阶段 0 新增）
- `is_walkable(map_id, layer, x, y) -> bool` / `walkable_cells` / `blocked_cells` / `invalidate`

### MapVisualizer
- `draw_map(map_id, layer_index, ...)`：绘制单层（5 层可控开关与叠放顺序）
- `save_map(fig, filename, formats, output_dir)` / `save_map_by_layer` / `save_multi_layer_pdf` / `save_combined_pdf`

---

## 数据库结构（V2）

1. **map**
   - id, name, width, height

2. **building_area**
   - id, map_id, name, layer_start, layer_end, geom_type（rectangle/polygon/circle）, center_x, center_y, radius, geom_json（多边形 corners）, size_json（width/height/area/angle）

3. **room**
   - id, map_id, building_area_id, name, layer_start, layer_end, room_type, geom_json, tiles_json（{wall, space, inner_wall}）, area, other_json

4. **item**
   - id, map_id, room_id, building_area_id, name, item_type（door/window/stairs，规划扩展：water/stone/deco）, layer_start, layer_end, timestep, position_x, position_y, vector_json, tiles_json, properties_json

> 约定：业务代码不建表、不 DROP/CREATE；表结构由迁移脚本负责（`DatabaseManager.migrate()`，当前迁移包待补齐，见 §7.2）。

---

## 地图输出

- `refactored/test/output/` 下按测试场景分子目录保存
- 命名示例：`map1_层1.png`、`map1_层1.pdf`、`test_combined_room_and_door.pdf`

---

## 依赖库

- numpy：数值计算 / 掩码渲染
- shapely：几何操作（碰撞检测、空闲区、道路避障、水域多边形）
- sqlite3：数据库（Python 标准库）
- matplotlib：可视化（PDF/PNG）
- json / random / math：数据处理与随机

---

## 扩展开发

### 添加新的建筑区类型
1. 继承 `BuildingAreaGenerator` 基类
2. 实现 `create_building_area` 方法
3. 添加特定形状生成逻辑 + 碰撞检测（`check_multi_layer_overlap`）

### 添加新的房间生成算法（含道路/洞穴/分割/排布）
1. 新建生成器类（如 `RoadGenerator` / `CaveGenerator` / `RoomSubdivider` / `RowHouseGenerator` / `TempleGenerator` / `FractureEroder`），统一接受 `(map_id, layer, seed)` 与可配置参数
2. 输出 `room` 行（room_type + geom_json + tiles_json + other_json）与可选 `item` 行
3. 在 `src/__init__.py` 导出
4. 在可视化器增加对应 room_type 配色

### 添加新的物体类型（水/石）
1. 在 `ObjectGenerator` 或扩展 `ItemGenerator` 中实现
2. 写 `item` 表（item_type + vector_json + tiles_json + properties_json），遵守 §4.1 契约
3. 更新可通行性（`passability.py`）与可视化配色

### 添加新的主题预设（L3 打包）
1. 在 `map_spec.py` 中编写一个工厂函数（如 `def castle_spec(**kw) -> MapSpec`），组合现有生成方式
2. 注册进 `PRESETS` 字典
3. 用 `MapGenerator(db).generate(PRESETS["castle"](seed=...))` 验证；同 seed 应可复现
4. 未来 L4 组合：把多个主题的区域配方（子 MapSpec）按分区布局拼合成大图

---

## 注意事项

1. 运行前确保已安装依赖（`pip install numpy shapely matplotlib`）
2. 首次运行前可显式调用 `DatabaseManager().migrate()`（当前迁移包待补齐）
3. 生成大量建筑区时，可能需要提高 `max_attempts`
4. 跨层建筑区会在所有指定层生成相同几何
5. V2 代码全部使用 id 关联（map_id / building_area_id / room_id），不要再用 name 当 key
6. 道路/洞穴/水生成遵循"房间在建筑区内、道路连接建筑区、物体任意位置"的约束（见路线图 §0）
7. 规划中的连通性模式（full/mostly/island/孤立）与桥/廊坊的落地顺序见 §6 阶段表

---

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request，共同改进这个地牢地图生成器。
