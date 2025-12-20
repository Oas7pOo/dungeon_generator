# src/generators/item_generator.py
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import Point, Polygon

from ..db.database import DatabaseManager


JsonDict = Dict[str, Any]
Grid = List[int]       # [x, y]
GridList = List[Grid]


class ItemGenerator:
    """
    物品生成器（V2）
    - door/window/stairs 全都写入 item 表
    - 所有关联只用 *_id：map_id / room_id / building_area_id
    - item.vector_json / item.tiles_json 承载矢量与占格
    - 业务代码不建表、不DROP/CREATE
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

    # -------------------------
    # json helpers
    # -------------------------
    @staticmethod
    def _json_loads_maybe(s: Optional[str], default: Any) -> Any:
        if not s:
            return default
        try:
            return json.loads(s)
        except Exception:
            return default

    @staticmethod
    def _json_dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False)

    # -------------------------
    # db helpers (id-first)
    # -------------------------
    def _fetch_rooms(self, map_id: int) -> List[JsonDict]:
        """
        只取生成 item 必要字段，显式列名，避免错位灾难。
        """
        rows = self.db.fetch_all(
            "SELECT "
            "id, map_id, building_area_id, name, "
            "layer_start, layer_end, room_type, geom_json, tiles_json, other_json "
            "FROM room "
            "WHERE map_id = ? "
            "ORDER BY id ASC",
            (int(map_id),),
        )
        return rows or []

    def _fetch_room(self, room_id: int) -> Optional[JsonDict]:
        return self.db.fetch_one(
            "SELECT "
            "id, map_id, building_area_id, name, "
            "layer_start, layer_end, room_type, geom_json, tiles_json, other_json "
            "FROM room "
            "WHERE id = ?",
            (int(room_id),),
        )

    def _fetch_existing_items(self, room_id: int, item_type: str) -> List[JsonDict]:
        return self.db.fetch_all(
            "SELECT id, tiles_json, vector_json, position_x, position_y "
            "FROM item "
            "WHERE room_id = ? AND item_type = ? "
            "ORDER BY id ASC",
            (int(room_id), str(item_type)),
        ) or []

    def save_item(self, item_data: JsonDict) -> Optional[int]:
        """
        写入 item 表，返回 item_id；失败返回 None
        """
        try:
            # 防重复：同 map_id + name
            exists = self.db.fetch_one(
                "SELECT id FROM item WHERE map_id = ? AND name = ?",
                (int(item_data["map_id"]), str(item_data["name"])),
            )
            if exists:
                return None

            cur = self.db.execute(
                "INSERT INTO item ("
                "map_id, room_id, building_area_id, name, item_type, "
                "layer_start, layer_end, timestep, "
                "position_x, position_y, "
                "vector_json, tiles_json, properties_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(item_data["map_id"]),
                    int(item_data["room_id"]) if item_data.get("room_id") is not None else None,
                    int(item_data["building_area_id"]) if item_data.get("building_area_id") is not None else None,
                    str(item_data["name"]),
                    str(item_data["item_type"]),
                    int(item_data["layer_start"]),
                    int(item_data["layer_end"]),
                    int(item_data.get("timestep", 0)),
                    float(item_data["position_x"]) if item_data.get("position_x") is not None else None,
                    float(item_data["position_y"]) if item_data.get("position_y") is not None else None,
                    str(item_data.get("vector_json") or ""),
                    str(item_data.get("tiles_json") or ""),
                    str(item_data.get("properties_json") or ""),
                ),
            )
            return int(cur.lastrowid)
        except Exception:
            import traceback
            traceback.print_exc()
            return None

    # -------------------------
    # geometry helpers
    # -------------------------
    @staticmethod
    def _circle_intersects_wall_tiles(center_xy: List[float], radius: float, wall_grid_list: GridList) -> GridList:
        """
        返回 door/window 圆与哪些墙格子相交（用于 item.tiles_json["wall_tiles"]）
        """
        cx, cy = float(center_xy[0]), float(center_xy[1])
        circle = Point(cx, cy).buffer(float(radius))

        touched: GridList = []
        for wx, wy in wall_grid_list:
            wall_poly = Polygon([(wx, wy), (wx + 1, wy), (wx + 1, wy + 1), (wx, wy + 1)])
            if circle.intersects(wall_poly):
                touched.append([int(wx), int(wy)])
        return touched

    @staticmethod
    def _centroid_of_tiles(tiles: GridList) -> Optional[List[float]]:
        if not tiles:
            return None
        xs = [t[0] + 0.5 for t in tiles]
        ys = [t[1] + 0.5 for t in tiles]
        return [float(sum(xs) / len(xs)), float(sum(ys) / len(ys))]

    @staticmethod
    def _wall_candidates(wall_tiles: GridList, space_tiles: GridList) -> List[Tuple[int, int]]:
        """
        door/window 候选墙格：
        - 必须是 wall tile
        - 且至少有一个 4 邻居是 space（保证“门/窗朝向房间内部”）
        """
        wall_set = {(int(x), int(y)) for x, y in (wall_tiles or [])}
        space_set = {(int(x), int(y)) for x, y in (space_tiles or [])}
        if not wall_set or not space_set:
            return []

        dirs4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        cand = []
        for wx, wy in wall_set:
            ok = False
            for dx, dy in dirs4:
                if (wx + dx, wy + dy) in space_set:
                    ok = True
                    break
            if ok:
                cand.append((wx, wy))
        return cand

    # -------------------------
    # door
    # -------------------------
    def generate_door_for_room(self, room_row: JsonDict, radius: float = 0.8) -> Optional[JsonDict]:
        """
        为单个 room 生成一个门（默认每房间最多一个；已存在 door 则不再生成）
        - 占用墙格子写入 item.tiles_json
        - 矢量写入 item.vector_json
        """
        room_id = int(room_row["id"])
        map_id = int(room_row["map_id"])

        # 已有门则跳过
        if self._fetch_existing_items(room_id, "door"):
            return None

        tiles = self._json_loads_maybe(room_row.get("tiles_json"), {})
        wall_tiles: GridList = tiles.get("wall", [])
        space_tiles: GridList = tiles.get("space", [])
        if not wall_tiles or not space_tiles:
            return None

        candidates = self._wall_candidates(wall_tiles, space_tiles)
        if not candidates:
            return None

        wx, wy = random.choice(candidates)
        pos = [wx + 0.5, wy + 0.5]

        wall_touched = self._circle_intersects_wall_tiles(pos, radius, wall_tiles)
        if not wall_touched:
            wall_touched = [[wx, wy]]

        vector = {"type": "circle", "center": pos, "radius": float(radius)}
        tiles_json = {"wall_tiles": wall_touched}
        props = {"opening": "door", "radius": float(radius)}

        item_data: JsonDict = {
            "map_id": map_id,
            "room_id": room_id,
            "building_area_id": int(room_row["building_area_id"]),
            "name": f"Door_room{room_id}",
            "item_type": "door",
            "layer_start": int(room_row["layer_start"]),
            "layer_end": int(room_row["layer_end"]),
            "timestep": 0,
            "position_x": float(pos[0]),
            "position_y": float(pos[1]),
            "vector_json": self._json_dumps(vector),
            "tiles_json": self._json_dumps(tiles_json),
            "properties_json": self._json_dumps(props),
        }
        return item_data

    def generate_and_save_doors(self, map_id: int, radius: float = 0.8) -> int:
        rooms = self._fetch_rooms(map_id)
        created = 0
        for r in rooms:
            it = self.generate_door_for_room(r, radius=radius)
            if not it:
                continue
            if self.save_item(it):
                created += 1
        return created

    # -------------------------
    # windows
    # -------------------------
    def _build_global_space_index(self, rooms: List[JsonDict]) -> Dict[int, set]:
        """
        global_space_by_layer[layer] = set((x,y))
        用于识别“墙外侧是否贴着别的房间空间”，从而过滤内墙窗。
        注意：多层房间按 layer_start..layer_end 都加入（tiles 复用）。
        """
        global_space_by_layer: Dict[int, set] = defaultdict(set)
        for r in rooms:
            tiles = self._json_loads_maybe(r.get("tiles_json"), {})
            space: GridList = tiles.get("space", [])
            if not space:
                continue
            ls = int(r["layer_start"])
            le = int(r["layer_end"])
            pts = {(int(x), int(y)) for x, y in space}
            for L in range(ls, le + 1):
                global_space_by_layer[L].update(pts)
        return global_space_by_layer

    def _door_wall_tiles_for_room(self, room_id: int) -> set:
        """
        从 item 表读取 door 的占用墙格（避免窗开到门上）。
        """
        out = set()
        for row in self._fetch_existing_items(room_id, "door"):
            tiles = self._json_loads_maybe(row.get("tiles_json"), {})
            wts = tiles.get("wall_tiles", [])
            for x, y in (wts or []):
                out.add((int(x), int(y)))
        return out

    def _external_wall_candidates(
        self,
        room_wall_set: set,
        room_space_set: set,
        global_space_set: set,
        forbidden_wall_set: set,
    ) -> List[Tuple[int, int]]:
        """
        从 wall tiles 中筛选“外墙”候选：
        - 该 wall tile 必须挨着本房间 space（内侧）
        - 且在其外侧方向上，不应该挨着 global_space（否则说明墙外是别的房间 = 内墙）
        - 同时避开 door 占用墙格子
        """
        dirs4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        cand = []
        for wx, wy in room_wall_set:
            if (wx, wy) in forbidden_wall_set:
                continue

            adj_in = []
            adj_out = []
            for dx, dy in dirs4:
                nx, ny = wx + dx, wy + dy
                if (nx, ny) in room_space_set:
                    adj_in.append((dx, dy))
                else:
                    adj_out.append((dx, dy))

            if not adj_in:
                continue

            touches_other_room = False
            for dx, dy in adj_out:
                nx, ny = wx + dx, wy + dy
                if (nx, ny) in global_space_set and (nx, ny) not in room_space_set:
                    touches_other_room = True
                    break
            if touches_other_room:
                continue

            cand.append((wx, wy))
        return cand

    def generate_windows_for_room(
        self,
        room_row: JsonDict,
        global_space_by_layer: Dict[int, set],
        *,
        radius: float = 0.6,
        count_range: Tuple[int, int] = (1, 3),
    ) -> List[JsonDict]:
        """
        为单个 room 生成若干窗户（外墙上）。
        - 每个窗户：item_type='window'
        - tiles_json: {"wall_tiles": [...]}
        """
        room_id = int(room_row["id"])
        map_id = int(room_row["map_id"])
        ls = int(room_row["layer_start"])
        le = int(room_row["layer_end"])

        tiles = self._json_loads_maybe(room_row.get("tiles_json"), {})
        wall_tiles: GridList = tiles.get("wall", [])
        space_tiles: GridList = tiles.get("space", [])
        if not wall_tiles or not space_tiles:
            return []

        room_wall_set = {(int(x), int(y)) for x, y in wall_tiles}
        room_space_set = {(int(x), int(y)) for x, y in space_tiles}

        # 只用首层做“外墙”判定（多层房间 tiles 复用，通常一致）
        global_space_set = global_space_by_layer.get(ls, set())

        forbidden = self._door_wall_tiles_for_room(room_id)

        candidates = self._external_wall_candidates(room_wall_set, room_space_set, global_space_set, forbidden)
        if not candidates:
            return []

        k = random.randint(int(count_range[0]), int(count_range[1]))
        k = max(0, min(k, len(candidates)))

        random.shuffle(candidates)
        chosen = candidates[:k]

        items: List[JsonDict] = []
        for idx, (wx, wy) in enumerate(chosen, start=1):
            pos = [wx + 0.5, wy + 0.5]
            wall_touched = self._circle_intersects_wall_tiles(pos, radius, wall_tiles)
            if not wall_touched:
                wall_touched = [[wx, wy]]

            vector = {"type": "circle", "center": pos, "radius": float(radius)}
            tiles_json = {"wall_tiles": wall_touched}
            props = {"opening": "window", "radius": float(radius)}

            items.append({
                "map_id": map_id,
                "room_id": room_id,
                "building_area_id": int(room_row["building_area_id"]),
                "name": f"Window_room{room_id}_{idx:02d}",
                "item_type": "window",
                "layer_start": ls,
                "layer_end": le,
                "timestep": 0,
                "position_x": float(pos[0]),
                "position_y": float(pos[1]),
                "vector_json": self._json_dumps(vector),
                "tiles_json": self._json_dumps(tiles_json),
                "properties_json": self._json_dumps(props),
            })

        return items

    def generate_and_save_windows(
        self,
        map_id: int,
        *,
        radius: float = 0.6,
        count_range: Tuple[int, int] = (1, 3),
    ) -> int:
        rooms = self._fetch_rooms(map_id)
        if not rooms:
            return 0

        global_space = self._build_global_space_index(rooms)
        created = 0

        for r in rooms:
            rt = (r.get("room_type") or "").lower()
            if rt in ("corridor", "stair", "stairs", "spiral", "ladder"):
                local_range = (0, 1)
            else:
                local_range = count_range

            items = self.generate_windows_for_room(r, global_space, radius=radius, count_range=local_range)
            for it in items:
                if self.save_item(it):
                    created += 1

        return created

    # -------------------------
    # stairs (跨层 item)
    # -------------------------
    def _stairs_kind_and_anchor(self, room_row: JsonDict) -> Tuple[Optional[str], Optional[List[float]], Optional[List[int]]]:
        """
        约定（从 room.other_json 读取）：
        other_json["dwelling_stairs"] = {
          "kind": "spiral"|"stair"|"ladder",
          "anchor": [x,y],
          "span": [minL,maxL]
        }
        """
        other = self._json_loads_maybe(room_row.get("other_json"), {})
        ds = other.get("dwelling_stairs")
        if isinstance(ds, dict):
            kind = ds.get("kind")
            anchor = ds.get("anchor")
            span = ds.get("span")
            if isinstance(kind, str) and kind:
                return kind, anchor if isinstance(anchor, list) else None, span if isinstance(span, list) else None

        # fallback：room_type
        rt = (room_row.get("room_type") or "").lower()
        if rt in ("spiral", "spiral_staircase"):
            return "spiral", None, None
        if rt in ("stair", "stairs", "staircase"):
            return "stair", None, None
        if rt in ("ladder",):
            return "ladder", None, None
        return None, None, None

    def generate_and_save_stairs(self, map_id: int) -> int:
        """
        生成楼梯 item：
        - 对同一个“楼梯竖井/旋转楼梯”只生成 1 个 item
        - layer_start/layer_end 表示连接跨度
        """
        rooms = self._fetch_rooms(map_id)
        if not rooms:
            return 0

        groups: Dict[Any, List[Tuple[JsonDict, Optional[List[int]]]]] = defaultdict(list)

        for r in rooms:
            kind, anchor, span = self._stairs_kind_and_anchor(r)
            if not kind:
                continue

            # 分组 key：优先 anchor；否则用 geom_json center 做粗量化
            if anchor and len(anchor) == 2:
                key = (kind, int(anchor[0]), int(anchor[1]))
            else:
                geom = self._json_loads_maybe(r.get("geom_json"), {})
                c = geom.get("center")
                if isinstance(c, list) and len(c) == 2:
                    key = (kind, int(round(float(c[0]) / 10)), int(round(float(c[1]) / 10)))
                else:
                    key = (kind, int(r["id"]))

            groups[key].append((r, span))

        created = 0
        for key, lst in groups.items():
            kind = key[0]

            # 跨层范围：span 优先，否则取组内 min/max
            span_list = [sp for (_, sp) in lst if isinstance(sp, list) and len(sp) == 2]
            if span_list:
                minL = min(int(sp[0]) for sp in span_list)
                maxL = max(int(sp[1]) for sp in span_list)
            else:
                minL = min(int(r["layer_start"]) for (r, _) in lst)
                maxL = max(int(r["layer_end"]) for (r, _) in lst)

            # 位置：用组内 geom center 平均
            centers = []
            room_id_anchor = None
            building_area_id = None
            for r, _ in lst:
                room_id_anchor = room_id_anchor or int(r["id"])
                building_area_id = building_area_id or int(r["building_area_id"])
                geom = self._json_loads_maybe(r.get("geom_json"), {})
                c = geom.get("center")
                if isinstance(c, list) and len(c) == 2:
                    centers.append([float(c[0]), float(c[1])])

            if not centers:
                continue

            pos = [sum(c[0] for c in centers) / len(centers), sum(c[1] for c in centers) / len(centers)]

            # vector_json：spiral 用 circle，其它用小矩形
            if kind == "spiral":
                vec = {"type": "circle", "center": pos, "radius": 4.0}
            elif kind == "ladder":
                vec = {"type": "rectangle", "center": pos, "width": 2.0, "height": 6.0}
            else:
                vec = {"type": "rectangle", "center": pos, "width": 4.0, "height": 8.0}

            props = {
                "stair_kind": kind,
                "connects_layers": [int(minL), int(maxL)],
            }

            item_data: JsonDict = {
                "map_id": int(map_id),
                "room_id": int(room_id_anchor) if room_id_anchor is not None else None,
                "building_area_id": int(building_area_id) if building_area_id is not None else None,
                "name": f"Stairs_{kind}_map{map_id}_{minL}_{maxL}_{abs(hash(key)) % 99999}",
                "item_type": "stairs",
                "layer_start": int(minL),
                "layer_end": int(maxL),
                "timestep": 0,
                "position_x": float(pos[0]),
                "position_y": float(pos[1]),
                "vector_json": self._json_dumps(vec),
                "tiles_json": self._json_dumps({}),  # stairs 默认不占墙格
                "properties_json": self._json_dumps(props),
            }

            if self.save_item(item_data):
                created += 1

        return created

    # -------------------------
    # batch
    # -------------------------
    def generate_and_save_all(
        self,
        map_id: int,
        *,
        doors: bool = True,
        windows: bool = True,
        stairs: bool = True,
    ) -> JsonDict:
        res = {"doors": 0, "windows": 0, "stairs": 0}
        if doors:
            res["doors"] = self.generate_and_save_doors(map_id)
        if windows:
            res["windows"] = self.generate_and_save_windows(map_id)
        if stairs:
            res["stairs"] = self.generate_and_save_stairs(map_id)
        return res
