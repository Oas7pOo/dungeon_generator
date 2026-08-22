# src/generators/map_generator.py
"""
地图执行器（MapGenerator）：把 MapSpec 配方跑成真实地图（阶段 0 落地）。

流水线（对应 readme §0.6 宏观生成顺序）：
    seed 初始化 -> migrate（幂等）-> 建 map 行
    -> 建筑区（BuildingAreaSpec 列表）-> 房间（InteriorSpec）
    -> 门/窗/楼梯（按 interior 模式去重）
    -> 连接/修饰（ConnectionSpec / DecorationSpec：规划中，警告跳过）

可复现性（阶段 0 策略，零侵入）：
    在 generate() 入口对全局 random / np.random 做 seed（spec.seed），
    使"同 MapSpec + 同 seed"结果完全一致；dwellings 内部使用自身 RNG(seed)。
    后续阶段再把各生成器逐步改为注入式 RNG（见 readme §7.2-1）。
"""
from __future__ import annotations

import json
import random
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from shapely.geometry import Point, Polygon

from ..db.database import DatabaseManager
from .building_area_generator import (
    RectangleBuildingAreaGenerator,
    CircleBuildingAreaGenerator,
    RegularPolygonBuildingAreaGenerator,
    HexagonBuildingAreaGenerator,
)
from .room_generator import RoomGenerator
from .block_room_generator import BlockRoomGenerator
from .item_generator import ItemGenerator
from .road_generator import RoadGenerator
from .dwellings_house_generator import DwellingsHouseDBWriter
from .map_spec import MapSpec, BuildingAreaSpec

JsonDict = Dict[str, Any]


class MapGenerator:
    """
    根据 MapSpec 生成整张地图并落库。

    用法：
        gen = MapGenerator(db)
        result = gen.generate(MapSpec(...))   # 或 gen.generate(PRESETS["village"](seed=42))
    """

    # 生成器名 -> 工厂类（BuildingAreaSpec.generator 的取值）
    AREA_GENERATORS = {
        "rectangle": RectangleBuildingAreaGenerator,
        "circle": CircleBuildingAreaGenerator,
        "regular_polygon": RegularPolygonBuildingAreaGenerator,
        "hexagon": HexagonBuildingAreaGenerator,
    }

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

    # ------------------------------------------------------------------
    # main entry
    # ------------------------------------------------------------------
    def generate(self, spec: Optional[MapSpec] = None, *, migrate: bool = True) -> JsonDict:
        """
        生成 spec 描述的地图，返回统计：
            {"map_id", "name", "seed", "areas", "rooms", "items",
             "doors", "windows", "stairs", "warnings"}
        """
        if spec is None:
            raise ValueError("MapGenerator.generate 需要一个 MapSpec")

        # 1) 种子：同配方 + 同 seed 可复现（阶段 0 零侵入策略）
        seed = spec.seed
        if seed is None:
            seed = random.randrange(0, 2 ** 31 - 1)
        random.seed(int(seed))
        np.random.seed(int(seed))

        # 2) 迁移（幂等）
        if migrate:
            self.db.migrate()

        # 3) 建 map 行
        map_id = self.db.insert_map(spec.name, int(spec.width), int(spec.height))

        warnings: List[str] = []

        # 4) 建筑区
        area_ids: List[int] = []
        for i, area_spec in enumerate(spec.areas):
            ids = self._generate_building_areas(map_id, area_spec, index=i, warnings=warnings)
            area_ids.extend(ids)

        # 5) 房间（按 InteriorSpec）
        rooms = self._generate_rooms(map_id, spec, area_ids, seed, warnings=warnings)

        # 5b) 通用数据层约束（对所有模式生效）：
        #     任何建筑不得超出自己的建筑区、不得覆盖其他建筑区（渲染红色部分），
        #     超出/重叠格一律硬性扣除 tiles；被裁空的房间删除。
        rooms = self._clip_rooms_to_areas(map_id)
        # 悬空门/窗（墙格被硬扣后失去依附）一并清除
        self._prune_hanging_items(map_id)

        # 6) 门/窗/楼梯（按 interior 模式去重）
        item_stats = self._generate_items(map_id, spec, warnings=warnings)

        # 7) 道路连接（按 ConnectionSpec）
        road_stats = self._generate_roads(map_id, spec, warnings=warnings)

        # 8) 修饰（规划中）
        self._warn_planned(warnings, "decoration", spec.decoration.kind, spec.decoration.kind != "none")

        return {
            "map_id": int(map_id),
            "name": spec.name,
            "seed": int(seed),
            "areas": len(area_ids),
            "rooms": rooms,
            "items": item_stats,
            "roads": road_stats,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # building areas
    # ------------------------------------------------------------------
    def _generate_building_areas(
        self,
        map_id: int,
        area_spec: BuildingAreaSpec,
        *,
        index: int,
        warnings: List[str],
    ) -> List[int]:
        cls = self.AREA_GENERATORS.get(area_spec.generator)
        if cls is None:
            warnings.append(f"未知建筑区生成器: {area_spec.generator!r}（跳过）")
            return []

        prefix = area_spec.name_prefix or area_spec.generator
        count = max(1, int(area_spec.count))
        ids: List[int] = []

        for k in range(count):
            gen = cls(f"{prefix}{index}", map_id, 1, self.db)
            name = f"{prefix}_{index}_{k + 1}" if count > 1 else f"{prefix}_{index}"
            result = gen.create_building_area(name=name, **area_spec.kwargs)
            if result:
                ids.extend(int(item["id"]) for item in result)

        if not ids:
            warnings.append(f"建筑区 {prefix} 生成失败（可能空间不足）")
        return ids

    # ------------------------------------------------------------------
    # rooms
    # ------------------------------------------------------------------
    def _generate_rooms(
        self,
        map_id: int,
        spec: MapSpec,
        area_ids: List[int],
        seed: int,
        *,
        warnings: List[str],
    ) -> int:
        mode = spec.interior.mode
        kw = spec.interior.kwargs or {}
        room_gen = RoomGenerator(self.db)

        if mode == "maze":
            n = room_gen.generate_and_save_rooms(map_id)
            for row in self.db.fetch_all(
                "SELECT id FROM room WHERE map_id = ?", (int(map_id),)
            ) or []:
                room_gen.maze_room(int(row["id"]), **kw)
            return n

        if mode == "watabou":
            res = BlockRoomGenerator(self.db).generate_and_save(
                map_id=int(map_id),
                layer=1,
                **kw,
            )
            room_ids = res.get("room_ids", [])
            # 归属：把全图生成的房间挂到"最大建筑区（外壳）"名下，
            # 真正的"不越界/不压其它建筑区"硬裁剪由通用 _clip_rooms_to_areas 统一执行。
            return self._attach_watabou_rooms_to_shell(map_id, room_ids)

        if mode == "dwellings":
            total = 0
            for i, aid in enumerate(area_ids):
                writer = DwellingsHouseDBWriter(self.db)
                res = writer.generate_and_save_dwelling(
                    building_area_id=int(aid),
                    seed=int(seed) + i,  # 派生种子：同 spec 同 seed 可复现
                    tags_raw=list(kw.get("tags", ["默认"])),
                    n_floors=kw.get("n_floors"),
                )
                total += int(res.get("rooms", 0))
            return total

        if mode in ("subdivide", "grow", "rowhouse", "temple", "fracture"):
            warnings.append(f"InteriorSpec.mode={mode!r} 规划中（阶段 1），暂按 basic 生成")
            return room_gen.generate_and_save_rooms(map_id)

        # basic（默认）
        return room_gen.generate_and_save_rooms(map_id)

    # ------------------------------------------------------------------
    # 通用数据层约束：任何建筑不得超出自己建筑区 / 覆盖其他建筑区
    # ------------------------------------------------------------------
    def _attach_watabou_rooms_to_shell(self, map_id: int, room_ids: List[int]) -> int:
        """
        watabou 全图生成模式：把房间的 building_area_id 挂到"最大建筑区（外壳）"名下。
        只做归属，不做裁剪（硬裁剪由 _clip_rooms_to_areas 统一执行）。
        """
        if not room_ids:
            return 0
        areas = self.db.fetch_all(
            "SELECT id, geom_type, center_x, center_y, radius, geom_json, size_json "
            "FROM building_area WHERE map_id = ?",
            (int(map_id),),
        ) or []
        best_id = None
        best_area = -1.0
        for a in areas:
            p = self._area_to_poly(a)
            if p is not None and p.area > best_area:
                best_area = float(p.area)
                best_id = int(a["id"])
        if best_id is None:
            return len(room_ids)

        for rid in room_ids:
            self.db.execute(
                "UPDATE room SET building_area_id = ? WHERE id = ? AND building_area_id IS NULL",
                (int(best_id), int(rid)),
            )
        return len(room_ids)

    def _clip_rooms_to_areas(self, map_id: int) -> int:
        """
        通用硬裁剪（数据层面，对所有 interior 模式生效）：

        规则（对应"渲染中红色部分"的硬性扣除）：
        - 有所属建筑区的房间：space 格必须位于**自己的建筑区**内，
          且不得落在**任何其他建筑区**内；
        - 无所属建筑区的房间：space 格不得落在任何建筑区内（没有自己的地盘，也不能压别人的）；
        - 不满足的格从 tiles 中硬性扣除，随后重算 wall/inner_wall/area；
        - 被裁空/过小的房间整行删除（连带其 item）。

        返回：裁剪后剩余的房间数。
        """
        areas = self.db.fetch_all(
            "SELECT id, geom_type, center_x, center_y, radius, geom_json, size_json "
            "FROM building_area WHERE map_id = ?",
            (int(map_id),),
        ) or []
        poly_by_id: Dict[int, Any] = {}
        for a in areas:
            p = self._area_to_poly(a)
            if p is not None:
                poly_by_id[int(a["id"])] = p

        rooms = self.db.fetch_all(
            "SELECT id, building_area_id, tiles_json, geom_json FROM room WHERE map_id = ?",
            (int(map_id),),
        ) or []

        kept = 0
        for r in rooms:
            rid = int(r["id"])
            own_id = r.get("building_area_id")
            if own_id is not None:
                own_poly = poly_by_id.get(int(own_id))
                other_polys = [p for aid, p in poly_by_id.items() if aid != int(own_id)]
            else:
                own_poly = None
                other_polys = list(poly_by_id.values())

            try:
                tiles = json.loads(r["tiles_json"]) if r["tiles_json"] else {}
            except Exception:
                tiles = {}
            if not isinstance(tiles, dict):
                tiles = {}

            space = tiles.get("space") or []
            inner = tiles.get("inner_wall") or []

            new_space = [
                [int(x), int(y)] for (x, y) in space
                if self._cell_kept(int(x), int(y), own_poly, other_polys)
            ]

            if len(new_space) < 4:
                # 被硬扣成空/过小：整行删除（连带 item）
                self.db.execute("DELETE FROM item WHERE room_id = ?", (rid,))
                self.db.execute("DELETE FROM room WHERE id = ?", (rid,))
                continue

            # 保留原墙（单墙共享模型：两个相邻房间共用一列墙，门在该墙上开洞），
            # 只裁剪不重算。若用 space 8 邻重算，墙会内移到 space 边缘，
            # 原墙列与门洞格变成夹在两墙之间的孤立空隙（用户反馈的"双墙夹门"）。
            kept_wall = [
                [int(x), int(y)] for (x, y) in (tiles.get("wall") or [])
                if self._cell_kept(int(x), int(y), own_poly, other_polys)
            ]

            new_inner = [
                [int(x), int(y)] for (x, y) in inner
                if self._cell_kept(int(x), int(y), own_poly, other_polys)
            ]

            tiles["wall"] = sorted(kept_wall)
            tiles["space"] = sorted(new_space)
            tiles["inner_wall"] = sorted(new_inner)

            # 同步更新 geom_json：center 用裁剪后 space 质心，bbox 同步，
            # 保证房间名/矢量定位不落在被硬扣掉的区域外。
            new_geom = None
            try:
                new_geom = json.loads(r["geom_json"]) if r["geom_json"] else None
            except Exception:
                new_geom = None
            if isinstance(new_geom, dict):
                xs = [int(x) for (x, y) in new_space]
                ys = [int(y) for (x, y) in new_space]
                mean_x = sum(xs) / len(xs)
                mean_y = sum(ys) / len(ys)
                # 中心格 = space 中离质心最近的一格（凹形房间的质心可能落在房间外，
                # 必须用真实存在的一格保证房间名显示在房间内）
                best = min(new_space, key=lambda c: (c[0] - mean_x) ** 2 + (c[1] - mean_y) ** 2)
                new_geom["center"] = [best[0] + 0.5, best[1] + 0.5]
                new_geom["bbox"] = [min(xs), min(ys), max(xs) + 1, max(ys) + 1]

            if new_geom is not None:
                self.db.execute(
                    "UPDATE room SET tiles_json = ?, area = ?, geom_json = ? WHERE id = ?",
                    (json.dumps(tiles, ensure_ascii=False), len(new_space),
                     json.dumps(new_geom, ensure_ascii=False), rid),
                )
            else:
                self.db.execute(
                    "UPDATE room SET tiles_json = ?, area = ? WHERE id = ?",
                    (json.dumps(tiles, ensure_ascii=False), len(new_space), rid),
                )
            kept += 1

        return kept

    def _prune_hanging_items(self, map_id: int) -> None:
        """
        悬空门窗清理：通用裁剪会重算墙，若 door/window 的 wall_tiles 既不在任何
        剩余房间的 wall 上、也不与任何剩余房间的 space 相邻（4 邻），则该 item
        已失去依附，删除。

        注意：门（door）的 wall_tiles 是"墙上的开口格"（墙洞），裁剪后不属于
        wall 集合，但必然贴着两侧房间的 space——所以用"贴墙 OR 贴 space"判定。
        """
        wall_cells: set = set()
        space_cells: set = set()
        for r in self.db.fetch_all(
            "SELECT tiles_json FROM room WHERE map_id = ?", (int(map_id),)
        ) or []:
            try:
                tiles = json.loads(r["tiles_json"]) if r["tiles_json"] else {}
            except Exception:
                continue
            if not isinstance(tiles, dict):
                continue
            for w in tiles.get("wall") or []:
                if isinstance(w, (list, tuple)) and len(w) == 2:
                    wall_cells.add((int(w[0]), int(w[1])))
            for s in tiles.get("space") or []:
                if isinstance(s, (list, tuple)) and len(s) == 2:
                    space_cells.add((int(s[0]), int(s[1])))

        dirs4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

        for it in self.db.fetch_all(
            "SELECT id, tiles_json FROM item "
            "WHERE map_id = ? AND item_type IN ('door', 'window')",
            (int(map_id),),
        ) or []:
            try:
                tj = json.loads(it["tiles_json"]) if it["tiles_json"] else {}
            except Exception:
                tj = {}
            wt = tj.get("wall_tiles") if isinstance(tj, dict) else None
            if not isinstance(wt, list):
                continue

            def _attached(c: Any) -> bool:
                if not (isinstance(c, (list, tuple)) and len(c) == 2):
                    return False
                cx, cy = int(c[0]), int(c[1])
                if (cx, cy) in wall_cells:
                    return True
                return any((cx + dx, cy + dy) in space_cells for dx, dy in dirs4)

            if not any(_attached(c) for c in wt):
                self.db.execute("DELETE FROM item WHERE id = ?", (int(it["id"]),))

    @staticmethod
    def _cell_kept(x: int, y: int, own_poly: Optional[Any], other_polys: List[Any]) -> bool:
        """
        格 (x,y) 中心点是否保留：
        - own_poly 非空：必须被自己的建筑区包含；
        - 不得被任何其他建筑区包含（own_poly 为空时，不得被任何建筑区包含）。
        """
        p = Point(x + 0.5, y + 0.5)
        if own_poly is not None and not own_poly.contains(p):
            return False
        for op in other_polys:
            if op.contains(p):
                return False
        return True

    @staticmethod
    def _area_to_poly(a: Dict[str, Any]) -> Optional[Any]:
        """从 building_area 行重建多边形（circle/rectangle/polygon）。"""
        gt = str(a.get("geom_type") or "")
        try:
            if gt == "circle":
                cx, cy, r = a.get("center_x"), a.get("center_y"), a.get("radius")
                if cx is None or cy is None or not r:
                    return None
                return Point(float(cx), float(cy)).buffer(float(r))

            geom = a.get("geom_json")
            if isinstance(geom, str):
                try:
                    geom = json.loads(geom)
                except Exception:
                    geom = None

            if isinstance(geom, list) and len(geom) >= 3:
                p = Polygon(geom)
                if p.is_valid and not p.is_empty:
                    return p

            if gt == "rectangle":
                size = a.get("size_json")
                if isinstance(size, str):
                    try:
                        size = json.loads(size)
                    except Exception:
                        size = None
                if isinstance(size, dict) and size.get("width") and size.get("height") \
                        and a.get("center_x") is not None and a.get("center_y") is not None:
                    hw = float(size["width"]) / 2.0
                    hh = float(size["height"]) / 2.0
                    cx, cy = float(a["center_x"]), float(a["center_y"])
                    return Polygon([
                        (cx - hw, cy - hh), (cx + hw, cy - hh),
                        (cx + hw, cy + hh), (cx - hw, cy + hh),
                    ])
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------
    # items（门/窗/楼梯，按 interior 模式去重）
    # ------------------------------------------------------------------
    def _generate_items(self, map_id: int, spec: MapSpec, *, warnings: List[str]) -> JsonDict:
        mode = spec.interior.mode
        item_gen = ItemGenerator(self.db)

        if mode == "dwellings":
            # dwellings 已生成门/窗；只补楼梯
            stairs = item_gen.generate_and_save_stairs(map_id)
            return {"doors": 0, "windows": 0, "stairs": stairs}

        if mode == "watabou":
            # watabou 已生成门；补窗与楼梯
            windows = item_gen.generate_and_save_windows(map_id)
            stairs = item_gen.generate_and_save_stairs(map_id)
            return {"doors": 0, "windows": windows, "stairs": stairs}

        return item_gen.generate_and_save_all(map_id)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _warn_planned(warnings: List[str], dim: str, value: str, active: bool) -> None:
        if active:
            warnings.append(
                f"{dim}.{value} 规划中（未实现），当前跳过"
            )

    # ------------------------------------------------------------------
    # roads（道路生成，阶段 2）
    # ------------------------------------------------------------------
    def _generate_roads(
        self,
        map_id: int,
        spec: MapSpec,
        *,
        warnings: List[str],
    ) -> JsonDict:
        """
        按 ConnectionSpec 生成道路路网。

        mode:
          - none          : 不生成道路
          - door_to_door  : 门-门相连道路（已实现）
          - fungus / trunk_branch : 规划中，暂按 door_to_door 生成
        """
        mode = spec.connection.mode
        if mode == "none":
            return {"roads": 0, "connected": True, "components": [], "warnings": []}

        if mode in ("fungus", "trunk_branch"):
            warnings.append(f"connection.{mode} 规划中，暂按 door_to_door 生成")

        kw = spec.connection.kwargs or {}
        width = int(kw.get("width", 5))
        layer = int(kw.get("layer", 1))

        gen = RoadGenerator(self.db)
        try:
            result = gen.generate_and_save_roads(map_id, layer=layer, width=width)
            if result.get("warnings"):
                warnings.extend(result["warnings"])
            return result
        except Exception as e:
            import traceback
            traceback.print_exc()
            warnings.append(f"道路生成失败: {e}")
            return {"roads": 0, "connected": False, "components": [], "warnings": [str(e)]}
