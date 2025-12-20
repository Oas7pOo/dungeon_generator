# src/generators/room_generator.py
from __future__ import annotations

import json
import math
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from shapely.geometry import Point, Polygon

from ..db.database import DatabaseManager


JsonDict = Dict[str, Any]
Grid = List[int]          # [x, y]
GridList = List[Grid]


class RoomGenerator:
    """
    房间生成器（V2）

    - 只写入 room 表（V2），不负责建表
    - 全部使用 id 做关联：map_id / building_area_id / room_id
    - tiles_json 固定结构：
        {
          "wall": [[x,y],...],
          "space": [[x,y],...],
          "inner_wall": [[x,y],...]
        }
    - geom_json 统一存矢量/参数（原 vector_params）
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
    # geometry helpers
    # -------------------------
    @staticmethod
    def _safe_polygon(vertices: Any) -> Optional[Polygon]:
        if not isinstance(vertices, list) or len(vertices) < 3:
            return None
        try:
            poly = Polygon(vertices)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                return None
            return poly
        except Exception:
            return None

    @staticmethod
    def _grid_neighbors8(x: int, y: int) -> List[Tuple[int, int]]:
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                out.append((x + dx, y + dy))
        return out

    # -------------------------
    # naming (map_id scoped)
    # -------------------------
    def _generate_unique_name(self, map_id: int, prefix: str = "Room") -> str:
        """
        在 map_id 范围内生成唯一房间名：Room_1 / Room_2 ...
        """
        try:
            start_pos = len(prefix) + 2  # SQLite substr 1-based; "Room_" 后第一个数字位置
            row = self.db.fetch_one(
                "SELECT name "
                "FROM room "
                "WHERE map_id = ? AND name LIKE ? "
                "ORDER BY CAST(SUBSTR(name, ?) AS INTEGER) DESC "
                "LIMIT 1",
                (int(map_id), f"{prefix}_%", int(start_pos)),
            )
            if row and row.get("name"):
                try:
                    current_max = int(str(row["name"]).split("_", 1)[1])
                    return f"{prefix}_{current_max + 1}"
                except Exception:
                    pass
            return f"{prefix}_1"
        except Exception:
            return f"{prefix}_{int(time.time())}"

    # -------------------------
    # load building area (id-first)
    # -------------------------
    def _get_building_area(self, building_area_id: int) -> Optional[JsonDict]:
        return self.db.fetch_one(
            "SELECT "
            "id, map_id, name, layer_start, layer_end, geom_type, "
            "center_x, center_y, radius, geom_json, size_json "
            "FROM building_area "
            "WHERE id = ?",
            (int(building_area_id),),
        )

    def _should_generate_per_layer(self, building_area_row: JsonDict) -> bool:
        """
        旧逻辑里“旋转矩形房间”每层单独生成。
        在 V2 里更稳的判断：size_json 里有 angle（或 name 里含旋转字样作为兼容）。
        """
        size = self._json_loads_maybe(building_area_row.get("size_json"), {})
        angle = size.get("angle", None)

        name = str(building_area_row.get("name") or "")
        if angle is not None:
            return True
        if "旋转" in name:
            return True
        return False

    def _building_polygon_corners(self, building_area_row: JsonDict) -> Optional[List[List[float]]]:
        """
        从 building_area.geom_json 取 corners。
        如果是 rectangle 且 geom_json 缺失，则尝试用 size_json + center 推出轴对齐 corners。
        """
        geom_type = str(building_area_row.get("geom_type") or "")
        geom_json = building_area_row.get("geom_json")

        if geom_json:
            corners = self._json_loads_maybe(geom_json, None)
            if isinstance(corners, list) and len(corners) >= 3:
                return corners

        # fallback: axis-aligned rectangle with size_json
        if geom_type == "rectangle":
            size = self._json_loads_maybe(building_area_row.get("size_json"), {})
            w = size.get("width", None)
            h = size.get("height", None)
            cx = building_area_row.get("center_x", None)
            cy = building_area_row.get("center_y", None)
            if w and h and cx is not None and cy is not None:
                w = float(w)
                h = float(h)
                cx = float(cx)
                cy = float(cy)
                return [
                    [cx - w / 2, cy - h / 2],
                    [cx + w / 2, cy - h / 2],
                    [cx + w / 2, cy + h / 2],
                    [cx - w / 2, cy + h / 2],
                ]

        return None

    # -------------------------
    # tile generation
    # -------------------------
    def _tiles_for_circle(self, center_x: float, center_y: float, radius: float) -> Tuple[GridList, GridList]:
        """
        返回 (wall_tiles, space_tiles)：
        - wall_tiles: 外圈
        - space_tiles: 内部（不含外圈）
        """
        cx = float(center_x)
        cy = float(center_y)
        r = float(radius)

        all_space: GridList = []
        min_x = int(math.floor(cx - r))
        max_x = int(math.floor(cx + r))
        min_y = int(math.floor(cy - r))
        max_y = int(math.floor(cy + r))

        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                gx = x + 0.5
                gy = y + 0.5
                if (gx - cx) ** 2 + (gy - cy) ** 2 <= r ** 2:
                    all_space.append([x, y])

        if not all_space:
            return [], []

        space_set = { (t[0], t[1]) for t in all_space }
        wall: GridList = []

        for x, y in space_set:
            is_outer = False
            for nx, ny in self._grid_neighbors8(x, y):
                gx = nx + 0.5
                gy = ny + 0.5
                if (gx - cx) ** 2 + (gy - cy) ** 2 > r ** 2:
                    is_outer = True
                    break
            if is_outer:
                wall.append([x, y])

        wall_set = { (t[0], t[1]) for t in wall }
        space = [[x, y] for (x, y) in space_set if (x, y) not in wall_set]

        return wall, space

    def _tiles_for_polygon(self, corners: List[List[float]]) -> Tuple[GridList, GridList]:
        """
        返回 (wall_tiles, space_tiles)：
        - wall_tiles: 外圈
        - space_tiles: 内部（不含外圈）
        """
        poly = self._safe_polygon(corners)
        if poly is None:
            return [], []

        minx, miny, maxx, maxy = poly.bounds
        min_x = int(math.floor(minx))
        max_x = int(math.floor(maxx))
        min_y = int(math.floor(miny))
        max_y = int(math.floor(maxy))

        all_space: GridList = []
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                p = Point(x + 0.5, y + 0.5)
                if poly.contains(p):
                    all_space.append([x, y])

        if not all_space:
            return [], []

        space_set = { (t[0], t[1]) for t in all_space }
        wall: GridList = []

        for x, y in space_set:
            is_outer = False
            for nx, ny in self._grid_neighbors8(x, y):
                p2 = Point(nx + 0.5, ny + 0.5)
                if not poly.contains(p2):
                    is_outer = True
                    break
            if is_outer:
                wall.append([x, y])

        wall_set = { (t[0], t[1]) for t in wall }
        space = [[x, y] for (x, y) in space_set if (x, y) not in wall_set]
        return wall, space

    # -------------------------
    # room type inference
    # -------------------------
    def _infer_room_type_from_polygon(self, corners: List[List[float]], building_area_row: JsonDict) -> str:
        size = self._json_loads_maybe(building_area_row.get("size_json"), {})
        if size.get("angle", None) is not None:
            return "rotated_rectangle"

        if len(corners) == 4:
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)

            # 粗略判断轴对齐
            aligned = (
                abs(corners[0][0] - corners[3][0]) < 1e-6
                or abs(corners[0][1] - corners[1][1]) < 1e-6
            )
            if aligned:
                return "rectangle"
            return "rotated_rectangle"

        return "polygon"

    # -------------------------
    # build room data (in-memory)
    # -------------------------
    def generate_room_in_building(
        self,
        building_area_id: int,
        *,
        layer_start: Optional[int] = None,
        layer_end: Optional[int] = None,
    ) -> Optional[JsonDict]:
        """
        在指定 building_area 内生成一个 room_data（不写库）

        - 默认使用 building_area.layer_start/layer_end
        - 允许外部覆盖层范围（用于 per-layer 生成）
        """
        ba = self._get_building_area(building_area_id)
        if not ba:
            return None

        map_id = int(ba["map_id"])
        building_name = str(ba.get("name") or "")
        geom_type = str(ba.get("geom_type") or "")

        ls = int(layer_start) if layer_start is not None else int(ba["layer_start"])
        le = int(layer_end) if layer_end is not None else int(ba["layer_end"])

        # room name
        room_name: str
        if geom_type == "circle" and ("三层圆塔" in building_name):
            # 兼容旧剧情写法：尽量用固定名，但仍保证唯一
            cand = "room1"
            exists = self.db.fetch_one(
                "SELECT id FROM room WHERE map_id = ? AND name = ?",
                (map_id, cand),
            )
            room_name = cand if not exists else self._generate_unique_name(map_id, "Room")
        else:
            room_name = self._generate_unique_name(map_id, "Room")

        # build tiles + geom_json
        if geom_type == "circle":
            cx = ba.get("center_x")
            cy = ba.get("center_y")
            r = ba.get("radius")
            if cx is None or cy is None or r is None:
                return None

            wall, space = self._tiles_for_circle(float(cx), float(cy), float(r))
            room_type = "circle"
            area = int(len(space))

            geom_json = {
                "type": "circle",
                "center": [float(cx), float(cy)],
                "radius": float(r),
                "width": float(r) * 2.0,
                "height": float(r) * 2.0,
            }

        else:
            corners = self._building_polygon_corners(ba)
            if not corners:
                return None

            wall, space = self._tiles_for_polygon(corners)
            if not wall and not space:
                return None

            room_type = self._infer_room_type_from_polygon(corners, ba)

            # center 用 polygon centroid，fallback 用 corners 均值
            poly = self._safe_polygon(corners)
            if poly is not None:
                cx = float(poly.centroid.x)
                cy = float(poly.centroid.y)
                bounds = poly.bounds
            else:
                cx = float(sum(c[0] for c in corners) / len(corners))
                cy = float(sum(c[1] for c in corners) / len(corners))
                xs = [c[0] for c in corners]
                ys = [c[1] for c in corners]
                bounds = (min(xs), min(ys), max(xs), max(ys))

            minx, miny, maxx, maxy = bounds
            area = int(len(space))

            geom_json = {
                "type": room_type,
                "corners": corners,
                "center": [cx, cy],
                "width": float(maxx - minx),
                "height": float(maxy - miny),
            }

            size = self._json_loads_maybe(ba.get("size_json"), {})
            if "angle" in size:
                geom_json["angle"] = size.get("angle")

        tiles_json = {
            "wall": wall,
            "space": space,
            "inner_wall": [],  # 默认空
        }

        room_data: JsonDict = {
            "map_id": map_id,
            "building_area_id": int(building_area_id),
            "name": room_name,
            "layer_start": ls,
            "layer_end": le,
            "room_type": room_type,
            "geom_json": self._json_dumps(geom_json),
            "tiles_json": self._json_dumps(tiles_json),
            "area": int(area),
            "other_json": self._json_dumps({}),
        }
        return room_data

    # -------------------------
    # persistence
    # -------------------------
    def save_room(self, room_data: Optional[JsonDict]) -> Optional[int]:
        """
        写入 room 表，返回 room_id；失败返回 None
        """
        if not room_data:
            return None

        try:
            exists = self.db.fetch_one(
                "SELECT id FROM room WHERE map_id = ? AND name = ?",
                (int(room_data["map_id"]), str(room_data["name"])),
            )
            if exists:
                return None

            cur = self.db.execute(
                "INSERT INTO room ("
                "map_id, building_area_id, name, layer_start, layer_end, "
                "room_type, geom_json, tiles_json, area, other_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(room_data["map_id"]),
                    int(room_data["building_area_id"]),
                    str(room_data["name"]),
                    int(room_data["layer_start"]),
                    int(room_data["layer_end"]),
                    str(room_data["room_type"]),
                    str(room_data["geom_json"]),
                    str(room_data["tiles_json"]),
                    int(room_data["area"]) if room_data.get("area") is not None else None,
                    str(room_data["other_json"]),
                ),
            )
            return int(cur.lastrowid)
        except Exception:
            import traceback
            traceback.print_exc()
            return None

    # -------------------------
    # maze (id-first)
    # -------------------------
    def maze_room(self, room_id: int, maze_width: int = 1, complexity: float = 0.5) -> bool:
        """
        把 room.tiles_json["space"] 内部生成迷宫，并写回 tiles_json["inner_wall"]。

        - room_id：只用 id，不用 name
        - maze_width：>1 会做“走廊膨胀”
        - complexity：0~1，越大越倾向于多开一些孔（更多连通/分支）
        """
        try:
            row = self.db.fetch_one(
                "SELECT id, tiles_json FROM room WHERE id = ?",
                (int(room_id),),
            )
            if not row:
                return False

            tiles = self._json_loads_maybe(row.get("tiles_json"), {})
            space_list: GridList = tiles.get("space", [])
            wall_list: GridList = tiles.get("wall", [])
            inner_wall_list: GridList = tiles.get("inner_wall", [])

            if not isinstance(space_list, list) or len(space_list) < 9:
                return False

            space_set = { (t[0], t[1]) for t in space_list }
            if not space_set:
                return False

            xs = [x for x, _ in space_set]
            ys = [y for _, y in space_set]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            # grid: -1 不在房间内；0 墙；1 通道
            grid: Dict[Tuple[int, int], int] = {}
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    if (x, y) in space_set:
                        grid[(x, y)] = 0
                    else:
                        grid[(x, y)] = -1

            # 候选迷宫单元格（隔一格）
            maze_cells: List[Tuple[int, int]] = []
            for y in range(min_y, max_y + 1, 2):
                for x in range(min_x, max_x + 1, 2):
                    if grid.get((x, y), -1) == 0:
                        maze_cells.append((x, y))

            if not maze_cells:
                return False

            start = random.choice(maze_cells)
            grid[start] = 1
            stack = [start]

            directions = [(0, -2), (2, 0), (0, 2), (-2, 0)]

            # DFS 生成完美迷宫
            while stack:
                x, y = stack[-1]
                neighbors = []
                for dx, dy in directions:
                    nx, ny = x + dx, y + dy
                    if grid.get((nx, ny), -1) == 0:
                        neighbors.append((nx, ny))

                if neighbors:
                    nx, ny = random.choice(neighbors)
                    mx, my = (x + nx) // 2, (y + ny) // 2
                    if (mx, my) in grid and grid[(mx, my)] != -1:
                        grid[(mx, my)] = 1
                    grid[(nx, ny)] = 1
                    stack.append((nx, ny))
                else:
                    stack.pop()

            # complexity: 随机再打通少量墙（增加连通/减少单一路径）
            complexity = float(max(0.0, min(1.0, complexity)))
            if complexity > 0:
                wall_candidates = [pos for pos, v in grid.items() if v == 0]
                carve_n = int(len(wall_candidates) * (0.05 + 0.20 * complexity))
                if carve_n > 0 and wall_candidates:
                    random.shuffle(wall_candidates)
                    for pos in wall_candidates[:carve_n]:
                        grid[pos] = 1

            # maze_width: 走廊膨胀
            corridor = {pos for pos, v in grid.items() if v == 1}
            if int(maze_width) > 1:
                r = max(1, int(maze_width) // 2)
                expanded = set(corridor)
                for (x, y) in list(corridor):
                    for dx in range(-r, r + 1):
                        for dy in range(-r, r + 1):
                            nx, ny = x + dx, y + dy
                            if (nx, ny) in space_set:
                                expanded.add((nx, ny))
                corridor = expanded

            # inner_wall = space - corridor
            new_inner_wall = [[x, y] for (x, y) in space_set if (x, y) not in corridor]

            # 写回 tiles_json
            tiles["wall"] = wall_list
            tiles["space"] = space_list
            tiles["inner_wall"] = new_inner_wall

            self.db.execute(
                "UPDATE room SET tiles_json = ? WHERE id = ?",
                (self._json_dumps(tiles), int(room_id)),
            )
            return True

        except Exception:
            import traceback
            traceback.print_exc()
            return False

    # -------------------------
    # batch (id-first)
    # -------------------------
    def generate_and_save_rooms(self, map_id: int) -> int:
        """
        为 map_id 下所有 building_area 生成并保存房间（id-first）

        - circle / 普通建筑区：默认生成一个跨 layer_start-layer_end 的 room
        - “旋转矩形”（size_json 有 angle）：每层生成独立 room（兼容旧逻辑）
        """
        areas = self.db.fetch_all(
            "SELECT id, name, layer_start, layer_end, geom_type, size_json "
            "FROM building_area "
            "WHERE map_id = ? "
            "ORDER BY id ASC",
            (int(map_id),),
        )

        success = 0

        for ba in areas:
            building_area_id = int(ba["id"])
            ls = int(ba["layer_start"])
            le = int(ba["layer_end"])

            per_layer = self._should_generate_per_layer(ba)

            if per_layer:
                for L in range(ls, le + 1):
                    room_data = self.generate_room_in_building(
                        building_area_id,
                        layer_start=L,
                        layer_end=L,
                    )
                    room_id = self.save_room(room_data)
                    if room_id:
                        success += 1
                        # 兼容旧逻辑：若层=1，则生成迷宫
                        if L == 1:
                            self.maze_room(room_id, maze_width=1, complexity=0.5)
            else:
                room_data = self.generate_room_in_building(building_area_id)
                room_id = self.save_room(room_data)
                if room_id:
                    success += 1

        return success
