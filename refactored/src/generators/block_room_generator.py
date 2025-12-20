# src/generators/block_room_generator.py
from __future__ import annotations

import json
import random
from typing import Dict, List, Tuple, Set, Optional, Any
from collections import deque

from ..db.database import DatabaseManager


JsonDict = Dict[str, Any]
Cell = Tuple[int, int]          # coarse cell (x,y)
FineCell = Tuple[int, int]      # fine cell (x,y)


class BlockRoomGenerator:
    """
    方块房间生成器（Watabou 高级房间生成模拟版）- V2 版

    V2 适配点：
    - 输入输出用 id：map_id / building_area_id / room_id
    - room.tiles_json: {"wall":[...], "space":[...], "inner_wall":[...]}  (fine grid)
    - room.geom_json: {"type": "...", "corners":[...], "center":[...], ...}
    - door 不写入 room：door -> item 表（tiles_json/vector_json/properties_json）
    - 业务代码不 CREATE/DROP 表
    """

    TYPES = {
        "EMPTY": 0,
        "CORRIDOR": 2,
        "LIVING": 3,
        "DINING": 4,
        "BEDROOM": 5,
        "SPECIAL": 6,
    }

    EDGE_MARGIN = 2

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

        # HTML 1格 -> 程序 10x10 细格
        self.grid_size = 10

        self.map_width = 0
        self.map_height = 0
        self.cols = 0
        self.rows = 0

        # 粗网格：grid 存类型；room_id_grid 存“内部房间编号”(非DB id)
        self.grid: List[List[int]] = []
        self.room_id_grid: List[List[int]] = []

        # rooms: internal_rid -> {"id":rid,"type":t,"cells":[(x,y),...]}
        self.rooms: Dict[int, Dict[str, Any]] = {}
        self.room_order: List[int] = []

        # doors: {"key":("V"/"H",...), "r1":rid, "r2":rid, "vertical":bool}
        self.doors: List[Dict[str, Any]] = []

        # params
        self.target_rooms = 40
        self.mode = "corridor"     # "corridor" / "room_chain"
        self.merge_holes = False
        self.door_gap: Optional[int] = None  # fine grid length

    # =========================================================
    # Public API
    # =========================================================

    def generate_and_save(
        self,
        *,
        map_id: int,
        layer: int = 1,
        building_area_id: Optional[int] = None,
        **kwargs,
    ) -> JsonDict:
        """
        生成并写入数据库：
        返回：
          {
            "room_ids": [...],          # DB room.id
            "door_item_ids": [...],     # DB item.id
          }

        kwargs:
          - target_rooms
          - mode: "corridor" | "room_chain"
          - merge_holes: bool（默认 False）
          - door_gap: int（fine grid 缺口长度）
        """
        w, h = self._get_map_size(map_id)
        if not w or not h:
            return {"room_ids": [], "door_item_ids": []}

        self.map_width, self.map_height = int(w), int(h)
        self.cols = self.map_width // self.grid_size
        self.rows = self.map_height // self.grid_size

        # params
        self.target_rooms = int(kwargs.get("target_rooms", self.target_rooms))
        self.mode = str(kwargs.get("mode", self.mode))
        self.merge_holes = bool(kwargs.get("merge_holes", kwargs.get("fill_gaps", False)))
        self.door_gap = kwargs.get("door_gap", None)

        # init
        self._init_grid()

        # generate coarse topology
        if self.mode == "corridor":
            self._generate_branching_corridors()
            self._fill_rooms_corridor_based(self.target_rooms)
        else:
            self._start_room_chain()
            self._fill_rooms_neighbor_based(self.target_rooms)

        # doors are decided BEFORE merge_holes (门不受合洞影响)
        self._find_doors()

        # merge holes changes area/walls, not doors
        if self.merge_holes:
            self._merge_enclosed_holes_into_rooms()

        # rasterize boundaries into fine tiles
        walls_by_room, door_tiles_by_room, door_tiles_by_key = self._rasterize_boundaries_to_tiles()

        # insert rooms -> get DB ids mapping
        internal_to_db: Dict[int, int] = {}
        room_ids: List[int] = []

        for rid in self.room_order:
            room = self.rooms.get(rid)
            if not room or not room["cells"]:
                continue

            geom_json = self._build_geom_json(room["cells"])
            tiles_json, area = self._build_tiles_json(
                rid=rid,
                room_cells=room["cells"],
                walls_by_room=walls_by_room,
                door_tiles_by_room=door_tiles_by_room,
            )

            room_type = self._type_to_room_type_name(room["type"])
            name = f"blk_room_L{int(layer)}_{rid}"

            other = {
                "generator": "block_room_generator_v2",
                "grid_size": self.grid_size,
                "watabou_mode": self.mode,
                "target_rooms": self.target_rooms,
                "merge_holes": self.merge_holes,
                "door_gap": int(self.door_gap) if isinstance(self.door_gap, int) else max(2, self.grid_size // 3),
                "internal_rid": rid,
                "internal_type": int(room["type"]),
            }

            db_room_id = self._insert_room_row(
                map_id=int(map_id),
                building_area_id=int(building_area_id) if building_area_id is not None else None,
                name=name,
                layer_start=int(layer),
                layer_end=int(layer),
                room_type=room_type,
                geom_json=geom_json,
                tiles_json=tiles_json,
                other_json=other,
                area=area,
            )
            if db_room_id is None:
                continue

            internal_to_db[rid] = db_room_id
            room_ids.append(db_room_id)

        # insert doors as item (one per door key)
        door_item_ids: List[int] = []
        for idx, d in enumerate(self.doors, start=1):
            key = d["key"]
            r1 = int(d["r1"])
            r2 = int(d["r2"])

            room_id_1 = internal_to_db.get(r1)
            room_id_2 = internal_to_db.get(r2)
            if not room_id_1 or not room_id_2:
                continue

            door_tiles = sorted(list(door_tiles_by_key.get(key, set())))
            if not door_tiles:
                continue

            px, py = self._centroid_of_fine_tiles(door_tiles)
            vec = {"type": "circle", "center": [px, py], "radius": 0.8}
            props = {
                "opening": "door",
                "door_key": list(key),
                "connects_room_ids": [room_id_1, room_id_2],
                "layer": int(layer),
            }
            tiles_obj = {"wall_tiles": [[x, y] for (x, y) in door_tiles]}

            item_name = f"Door_map{map_id}_L{layer}_r{room_id_1}_r{room_id_2}_{idx:04d}"

            item_id = self._insert_item_row(
                map_id=int(map_id),
                room_id=int(room_id_1),  # 主挂载房间（另一个房间写进 properties）
                building_area_id=int(building_area_id) if building_area_id is not None else None,
                name=item_name,
                item_type="door",
                layer_start=int(layer),
                layer_end=int(layer),
                position_x=float(px),
                position_y=float(py),
                vector_json=vec,
                tiles_json=tiles_obj,
                properties_json=props,
            )
            if item_id is not None:
                door_item_ids.append(item_id)

        return {"room_ids": room_ids, "door_item_ids": door_item_ids}

    # =========================================================
    # DB helpers (id-first)
    # =========================================================

    def _get_map_size(self, map_id: int) -> Tuple[Optional[int], Optional[int]]:
        row = self.db.fetch_one("SELECT width, height FROM map WHERE id = ?", (int(map_id),))
        if not row:
            return None, None
        # 支持 dict / tuple 两种返回
        if isinstance(row, dict):
            return row.get("width"), row.get("height")
        return row[0], row[1]

    def _insert_room_row(
        self,
        *,
        map_id: int,
        building_area_id: Optional[int],
        name: str,
        layer_start: int,
        layer_end: int,
        room_type: str,
        geom_json: JsonDict,
        tiles_json: JsonDict,
        other_json: JsonDict,
        area: int,
    ) -> Optional[int]:
        """
        插入 room，并返回 room.id
        假设 room 表至少有这些列：
          map_id, building_area_id, name, layer_start, layer_end,
          room_type, geom_json, tiles_json, other_json
        area 若存在就写入，不存在就忽略（兼容老库）
        """
        cols = self._get_table_columns("room")

        payload: Dict[str, Any] = {
            "map_id": int(map_id),
            "building_area_id": int(building_area_id) if building_area_id is not None else None,
            "name": str(name),
            "layer_start": int(layer_start),
            "layer_end": int(layer_end),
            "room_type": str(room_type),
            "geom_json": json.dumps(geom_json, ensure_ascii=False),
            "tiles_json": json.dumps(tiles_json, ensure_ascii=False),
            "other_json": json.dumps(other_json, ensure_ascii=False),
        }
        if "area" in cols:
            payload["area"] = int(area)

        # 只插入存在的列
        keys = [k for k in payload.keys() if k in cols]
        vals = [payload[k] for k in keys]
        if not keys:
            return None

        sql = f"INSERT INTO room ({', '.join(keys)}) VALUES ({', '.join(['?']*len(keys))})"
        try:
            cur = self.db.execute(sql, tuple(vals))
            return int(cur.lastrowid)
        except Exception:
            import traceback
            traceback.print_exc()
            return None

    def _insert_item_row(
        self,
        *,
        map_id: int,
        room_id: Optional[int],
        building_area_id: Optional[int],
        name: str,
        item_type: str,
        layer_start: int,
        layer_end: int,
        position_x: float,
        position_y: float,
        vector_json: JsonDict,
        tiles_json: JsonDict,
        properties_json: JsonDict,
    ) -> Optional[int]:
        cols = self._get_table_columns("item")

        payload: Dict[str, Any] = {
            "map_id": int(map_id),
            "room_id": int(room_id) if room_id is not None else None,
            "building_area_id": int(building_area_id) if building_area_id is not None else None,
            "name": str(name),
            "item_type": str(item_type),
            "layer_start": int(layer_start),
            "layer_end": int(layer_end),
            "timestep": 0,
            "position_x": float(position_x),
            "position_y": float(position_y),
            "vector_json": json.dumps(vector_json, ensure_ascii=False),
            "tiles_json": json.dumps(tiles_json, ensure_ascii=False),
            "properties_json": json.dumps(properties_json, ensure_ascii=False),
        }

        keys = [k for k in payload.keys() if k in cols]
        vals = [payload[k] for k in keys]
        if not keys:
            return None

        sql = f"INSERT INTO item ({', '.join(keys)}) VALUES ({', '.join(['?']*len(keys))})"
        try:
            cur = self.db.execute(sql, tuple(vals))
            return int(cur.lastrowid)
        except Exception:
            import traceback
            traceback.print_exc()
            return None

    _table_cols_cache: Dict[str, Set[str]] = {}

    def _get_table_columns(self, table: str) -> Set[str]:
        if table in self._table_cols_cache:
            return self._table_cols_cache[table]
        rows = self.db.fetch_all(f"PRAGMA table_info({table});")
        cols = set()
        for r in (rows or []):
            # PRAGMA 返回 tuple：(cid, name, type, notnull, dflt_value, pk)
            if isinstance(r, dict):
                cols.add(r.get("name"))
            else:
                cols.add(r[1])
        self._table_cols_cache[table] = cols
        return cols

    # =========================================================
    # Grid init / helpers
    # =========================================================

    def _init_grid(self):
        self.grid = [[self.TYPES["EMPTY"] for _ in range(self.cols)] for _ in range(self.rows)]
        self.room_id_grid = [[-1 for _ in range(self.cols)] for _ in range(self.rows)]
        self.rooms = {}
        self.room_order = []
        self.doors = []

    def _in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.cols and 0 <= y < self.rows

    def _set_cell(self, x: int, y: int, t: int, rid: int):
        self.grid[y][x] = t
        self.room_id_grid[y][x] = rid
        if rid not in self.rooms:
            self.rooms[rid] = {"id": rid, "type": t, "cells": []}
            self.room_order.append(rid)
        self.rooms[rid]["cells"].append((x, y))

    def _has_neighbor_type(self, x: int, y: int, types: List[int]) -> bool:
        for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nx, ny = x + dx, y + dy
            if self._in_bounds(nx, ny) and self.grid[ny][nx] in types:
                return True
        return False

    # =========================================================
    # Watabou corridor mode
    # =========================================================

    def _is_valid_corridor_move(self, nx: int, ny: int) -> bool:
        if nx <= 1 or nx >= self.cols - 1 or ny <= 1 or ny >= self.rows - 1:
            return False

        corridor_neighbors = 0
        for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            tx, ty = nx + dx, ny + dy
            if self._in_bounds(tx, ty) and self.grid[ty][tx] == self.TYPES["CORRIDOR"]:
                corridor_neighbors += 1

        if self.grid[ny][nx] == self.TYPES["EMPTY"]:
            if corridor_neighbors > 1:
                return random.random() < 0.1
            return True

        if self.grid[ny][nx] == self.TYPES["CORRIDOR"]:
            return True

        return False

    def _generate_branching_corridors(self):
        corridor_id = 0
        self.rooms[corridor_id] = {"id": corridor_id, "type": self.TYPES["CORRIDOR"], "cells": []}
        self.room_order.append(corridor_id)

        start_x = self.cols // 2
        start_y = self.rows // 2

        initial_life = int(self.cols * 1.5)
        walkers = [{"x": start_x, "y": start_y, "life": initial_life, "dir": (1, 0)}]

        while walkers:
            w = walkers.pop()
            while w["life"] > 0:
                x, y = w["x"], w["y"]
                if self.grid[y][x] == self.TYPES["EMPTY"]:
                    self._set_cell(x, y, self.TYPES["CORRIDOR"], corridor_id)

                if random.random() < 0.9:
                    dir_candidates = [w["dir"]]
                else:
                    dir_candidates = [(0, 1), (0, -1), (1, 0), (-1, 0)]

                dx, dy = random.choice(dir_candidates)
                nx, ny = x + dx, y + dy

                moved = False
                if not self._is_valid_corridor_move(nx, ny):
                    all_dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]
                    random.shuffle(all_dirs)
                    for ddx, ddy in all_dirs:
                        tx, ty = x + ddx, y + ddy
                        if self._is_valid_corridor_move(tx, ty):
                            dx, dy = ddx, ddy
                            nx, ny = tx, ty
                            moved = True
                            break
                else:
                    moved = True

                if moved:
                    w["x"], w["y"] = nx, ny
                    w["dir"] = (dx, dy)

                w["life"] -= 1

                if random.random() < 0.05 and len(walkers) < 5 and w["life"] > 10:
                    bdx, bdy = w["dir"]
                    branch = (0, 1) if bdx != 0 else (1, 0)
                    if random.random() > 0.5:
                        branch = (-branch[0], -branch[1])
                    walkers.append({
                        "x": w["x"],
                        "y": w["y"],
                        "life": int(w["life"] * 0.6),
                        "dir": branch
                    })

    def _grow_room(self, seed: Cell, t: int, rid: int, max_size: int) -> Tuple[bool, List[Cell]]:
        sx, sy = seed
        if not self._in_bounds(sx, sy) or self.grid[sy][sx] != self.TYPES["EMPTY"]:
            return False, []

        placed: List[Cell] = []
        open_list: List[Cell] = [(sx, sy)]
        touched_edge = False

        compactness = "snake" if random.random() > 0.7 else "blob"

        self._set_cell(sx, sy, t, rid)
        placed.append((sx, sy))
        size = 1

        while open_list and size < max_size:
            idx = random.randrange(len(open_list)) if compactness == "blob" else (len(open_list) - 1)
            cx, cy = open_list[idx]
            grew = False

            dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]
            random.shuffle(dirs)

            for dx, dy in dirs:
                nx, ny = cx + dx, cy + dy

                if nx <= self.EDGE_MARGIN or nx >= self.cols - self.EDGE_MARGIN or ny <= self.EDGE_MARGIN or ny >= self.rows - self.EDGE_MARGIN:
                    touched_edge = True

                if self._in_bounds(nx, ny) and self.grid[ny][nx] == self.TYPES["EMPTY"]:
                    self._set_cell(nx, ny, t, rid)
                    placed.append((nx, ny))
                    open_list.append((nx, ny))
                    size += 1
                    grew = True
                    if size >= max_size:
                        break

            if (not grew) or (compactness == "blob" and random.random() > 0.5):
                open_list.pop(idx)

        if t != self.TYPES["CORRIDOR"] and touched_edge:
            for x, y in placed:
                self.grid[y][x] = self.TYPES["EMPTY"]
                self.room_id_grid[y][x] = -1
            return False, []

        return True, placed

    def _fill_rooms_corridor_based(self, target_count: int):
        safety = 5000
        rid_counter = 1  # corridor uses 0

        allowed_neighbors = [self.TYPES["CORRIDOR"]]
        if random.random() > 0.8:
            allowed_neighbors.append(self.TYPES["LIVING"])

        while safety > 0 and len(self.room_order) < target_count:
            safety -= 1
            candidates = []
            for y in range(self.EDGE_MARGIN, self.rows - self.EDGE_MARGIN):
                for x in range(self.EDGE_MARGIN, self.cols - self.EDGE_MARGIN):
                    if self.grid[y][x] == self.TYPES["EMPTY"] and self._has_neighbor_type(x, y, allowed_neighbors):
                        candidates.append((x, y))
            if not candidates:
                break

            seed = random.choice(candidates)

            rcount = len(self.room_order)
            r = random.random()
            if rcount < 3:
                t = self.TYPES["LIVING"]
            elif rcount < 8:
                t = self.TYPES["DINING"]
            else:
                if r < 0.65:
                    t = self.TYPES["BEDROOM"]
                elif r < 0.85:
                    t = self.TYPES["SPECIAL"]
                else:
                    t = self.TYPES["DINING"]

            if t == self.TYPES["LIVING"]:
                max_size = int(60 + random.random() * 40)
            elif t == self.TYPES["DINING"]:
                max_size = int(30 + random.random() * 30)
            elif t == self.TYPES["BEDROOM"]:
                max_size = int(25 + random.random() * 15)
            elif t == self.TYPES["SPECIAL"]:
                max_size = int(10 + random.random() * 10)
            else:
                max_size = 20

            rid = rid_counter
            rid_counter += 1

            self.rooms[rid] = {"id": rid, "type": t, "cells": []}
            self.room_order.append(rid)

            ok, _ = self._grow_room(seed, t, rid, max_size)
            if not ok:
                self.rooms.pop(rid, None)
                self.room_order.pop()
                rid_counter -= 1

    # =========================================================
    # Watabou room_chain mode
    # =========================================================

    def _start_room_chain(self):
        seed = (self.cols // 2, self.rows // 2)
        rid = 1
        t = self.TYPES["LIVING"]
        self.rooms[rid] = {"id": rid, "type": t, "cells": []}
        self.room_order.append(rid)

        ok, _ = self._grow_room(seed, t, rid, 150)
        if not ok:
            self.rooms = {}
            self.room_order = []

    def _fill_rooms_neighbor_based(self, target_count: int):
        safety = 5000
        rid_counter = 2

        allowed_neighbors = [self.TYPES["LIVING"], self.TYPES["DINING"], self.TYPES["BEDROOM"], self.TYPES["SPECIAL"]]

        while safety > 0 and len(self.room_order) < target_count:
            safety -= 1
            candidates = []
            for y in range(self.EDGE_MARGIN, self.rows - self.EDGE_MARGIN):
                for x in range(self.EDGE_MARGIN, self.cols - self.EDGE_MARGIN):
                    if self.grid[y][x] == self.TYPES["EMPTY"] and self._has_neighbor_type(x, y, allowed_neighbors):
                        candidates.append((x, y))
            if not candidates:
                break

            seed = random.choice(candidates)

            rcount = len(self.room_order)
            r = random.random()
            if rcount < 3:
                t = self.TYPES["LIVING"]
            elif rcount < 8:
                t = self.TYPES["DINING"]
            else:
                if r < 0.65:
                    t = self.TYPES["BEDROOM"]
                elif r < 0.85:
                    t = self.TYPES["SPECIAL"]
                else:
                    t = self.TYPES["DINING"]

            if t == self.TYPES["LIVING"]:
                max_size = int(60 + random.random() * 40)
            elif t == self.TYPES["DINING"]:
                max_size = int(30 + random.random() * 30)
            elif t == self.TYPES["BEDROOM"]:
                max_size = int(25 + random.random() * 15)
            elif t == self.TYPES["SPECIAL"]:
                max_size = int(10 + random.random() * 10)
            else:
                max_size = 20

            rid = rid_counter
            rid_counter += 1

            self.rooms[rid] = {"id": rid, "type": t, "cells": []}
            self.room_order.append(rid)

            ok, _ = self._grow_room(seed, t, rid, max_size)
            if not ok:
                self.rooms.pop(rid, None)
                self.room_order.pop()
                rid_counter -= 1

    # =========================================================
    # Doors (Watabou)
    # =========================================================

    def _find_doors(self):
        self.doors = []
        for y in range(self.rows):
            for x in range(self.cols):
                curr_id = self.room_id_grid[y][x]
                curr_type = self.grid[y][x]
                if curr_id == -1 or curr_type == self.TYPES["EMPTY"]:
                    continue

                if x < self.cols - 1:
                    self._check_door(x, y, x + 1, y, curr_id, is_vertical=False)
                if y < self.rows - 1:
                    self._check_door(x, y, x, y + 1, curr_id, is_vertical=True)

    def _check_door(self, x: int, y: int, nx: int, ny: int, curr_id: int, is_vertical: bool):
        next_id = self.room_id_grid[ny][nx]
        next_type = self.grid[ny][nx]
        if next_id == -1 or next_id == curr_id:
            return
        if next_type == self.TYPES["EMPTY"]:
            return

        curr_type = self.grid[y][x]

        if self.mode == "corridor":
            if curr_type == self.TYPES["CORRIDOR"] or next_type == self.TYPES["CORRIDOR"]:
                prob = 0.5
            elif curr_type == self.TYPES["LIVING"] or next_type == self.TYPES["LIVING"]:
                prob = 0.2
            else:
                prob = 0.05
        else:
            prob = 0.4

        r1, r2 = sorted((curr_id, next_id))
        exists_pair = any((d["r1"], d["r2"]) == (r1, r2) for d in self.doors)

        if (not exists_pair) or (random.random() < 0.2):
            if random.random() < prob:
                key = ("H", x, y + 1) if is_vertical else ("V", x + 1, y)
                self.doors.append({"key": key, "r1": r1, "r2": r2, "vertical": is_vertical})

    # =========================================================
    # Enclosed holes + merge
    # =========================================================

    def _mark_outside_empty(self) -> List[List[bool]]:
        outside = [[False for _ in range(self.cols)] for _ in range(self.rows)]
        q = deque()
        dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]

        def push(x: int, y: int):
            outside[y][x] = True
            q.append((x, y))

        for x in range(self.cols):
            if self.grid[0][x] == self.TYPES["EMPTY"] and not outside[0][x]:
                push(x, 0)
            if self.grid[self.rows - 1][x] == self.TYPES["EMPTY"] and not outside[self.rows - 1][x]:
                push(x, self.rows - 1)

        for y in range(self.rows):
            if self.grid[y][0] == self.TYPES["EMPTY"] and not outside[y][0]:
                push(0, y)
            if self.grid[y][self.cols - 1] == self.TYPES["EMPTY"] and not outside[y][self.cols - 1]:
                push(self.cols - 1, y)

        while q:
            x, y = q.popleft()
            for dx, dy in dirs:
                nx, ny = x + dx, y + dy
                if self._in_bounds(nx, ny) and (not outside[ny][nx]) and self.grid[ny][nx] == self.TYPES["EMPTY"]:
                    outside[ny][nx] = True
                    q.append((nx, ny))

        return outside

    def _find_enclosed_hole_components(self) -> List[List[Cell]]:
        outside = self._mark_outside_empty()
        visited = [[False for _ in range(self.cols)] for _ in range(self.rows)]
        dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]

        comps: List[List[Cell]] = []
        for y in range(self.rows):
            for x in range(self.cols):
                if visited[y][x]:
                    continue
                if self.grid[y][x] != self.TYPES["EMPTY"]:
                    continue
                if outside[y][x]:
                    continue

                comp: List[Cell] = []
                q = deque([(x, y)])
                visited[y][x] = True
                while q:
                    cx, cy = q.popleft()
                    comp.append((cx, cy))
                    for dx, dy in dirs:
                        nx, ny = cx + dx, cy + dy
                        if self._in_bounds(nx, ny) and (not visited[ny][nx]) and self.grid[ny][nx] == self.TYPES["EMPTY"] and (not outside[ny][nx]):
                            visited[ny][nx] = True
                            q.append((nx, ny))

                comps.append(comp)
        return comps

    def _merge_enclosed_holes_into_rooms(self):
        hole_comps = self._find_enclosed_hole_components()
        if not hole_comps:
            return

        dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]

        room_cell_sets: Dict[int, Set[Cell]] = {}
        for rid in self.room_order:
            room = self.rooms.get(rid)
            if room:
                room_cell_sets[rid] = set(room["cells"])

        for comp in hole_comps:
            neighbor_ids: Set[int] = set()
            for x, y in comp:
                for dx, dy in dirs:
                    nx, ny = x + dx, y + dy
                    if not self._in_bounds(nx, ny):
                        continue
                    rid = self.room_id_grid[ny][nx]
                    if rid != -1:
                        neighbor_ids.add(rid)

            if len(neighbor_ids) == 1:
                rid = next(iter(neighbor_ids))
                room = self.rooms.get(rid)
                if not room:
                    continue
                t = room["type"]
                cell_set = room_cell_sets.setdefault(rid, set())

                for x, y in comp:
                    self.grid[y][x] = t
                    self.room_id_grid[y][x] = rid
                    if (x, y) not in cell_set:
                        room["cells"].append((x, y))
                        cell_set.add((x, y))

    # =========================================================
    # Rasterize boundaries to fine tiles
    # =========================================================

    def _rasterize_boundaries_to_tiles(
        self,
    ) -> Tuple[Dict[int, Set[FineCell]], Dict[int, Set[FineCell]], Dict[Tuple, Set[FineCell]]]:
        """
        返回：
          walls_by_room[rid] -> set((fx,fy))
          door_tiles_by_room[rid] -> set((fx,fy))   (用于从 space 中扣掉，避免 overlap)
          door_tiles_by_key[key] -> set((fx,fy))    (用于生成 door item，一门一条)
        """
        scale = self.grid_size
        gap = self.door_gap if isinstance(self.door_gap, int) else max(2, scale // 3)

        door_keys = set(d["key"] for d in self.doors)

        walls_by_room: Dict[int, Set[FineCell]] = {}
        door_by_room: Dict[int, Set[FineCell]] = {}
        door_by_key: Dict[Tuple, Set[FineCell]] = {}

        def add(dct: Dict[int, Set[FineCell]], rid: int, pts: Set[FineCell]):
            if rid not in dct:
                dct[rid] = set()
            dct[rid].update(pts)

        def add_key(key: Tuple, pts: Set[FineCell]):
            if key not in door_by_key:
                door_by_key[key] = set()
            door_by_key[key].update(pts)

        def carve(points: List[FineCell], is_door: bool) -> Tuple[Set[FineCell], Set[FineCell]]:
            if not is_door:
                return set(points), set()

            n = len(points)
            c = n // 2
            s = max(0, c - gap // 2)
            e = min(n, s + gap)

            wall_pts: Set[FineCell] = set()
            door_pts: Set[FineCell] = set()
            for i, p in enumerate(points):
                if s <= i < e:
                    door_pts.add(p)
                else:
                    wall_pts.add(p)
            return wall_pts, door_pts

        for y in range(self.rows):
            for x in range(self.cols):
                rid = self.room_id_grid[y][x]
                if rid == -1:
                    continue

                # RIGHT boundary
                bx = x + 1
                right_id = self.room_id_grid[y][x + 1] if (x + 1) < self.cols else None
                if (x + 1) >= self.cols or right_id != rid:
                    key = ("V", bx, y)
                    fx = bx * scale - 1
                    pts = [(fx, y * scale + k) for k in range(scale)]
                    wall_pts, door_pts = carve(pts, key in door_keys)

                    add(walls_by_room, rid, wall_pts)
                    if door_pts:
                        add(door_by_room, rid, door_pts)
                        add_key(key, door_pts)

                    if right_id is not None and right_id != -1 and right_id != rid:
                        add(walls_by_room, right_id, wall_pts)
                        if door_pts:
                            add(door_by_room, right_id, door_pts)
                            add_key(key, door_pts)

                # DOWN boundary
                by = y + 1
                down_id = self.room_id_grid[y + 1][x] if (y + 1) < self.rows else None
                if (y + 1) >= self.rows or down_id != rid:
                    key = ("H", x, by)
                    fy = by * scale - 1
                    pts = [(x * scale + k, fy) for k in range(scale)]
                    wall_pts, door_pts = carve(pts, key in door_keys)

                    add(walls_by_room, rid, wall_pts)
                    if door_pts:
                        add(door_by_room, rid, door_pts)
                        add_key(key, door_pts)

                    if down_id is not None and down_id != -1 and down_id != rid:
                        add(walls_by_room, down_id, wall_pts)
                        if door_pts:
                            add(door_by_room, down_id, door_pts)
                            add_key(key, door_pts)

                # OUTER LEFT edge
                if x == 0 or self.room_id_grid[y][x - 1] == -1:
                    key = ("V", x, y)
                    fx = x * scale
                    pts = [(fx, y * scale + k) for k in range(scale)]
                    wall_pts, door_pts = carve(pts, key in door_keys)
                    add(walls_by_room, rid, wall_pts)
                    if door_pts:
                        add(door_by_room, rid, door_pts)
                        add_key(key, door_pts)

                # OUTER UP edge
                if y == 0 or self.room_id_grid[y - 1][x] == -1:
                    key = ("H", x, y)
                    fy = y * scale
                    pts = [(x * scale + k, fy) for k in range(scale)]
                    wall_pts, door_pts = carve(pts, key in door_keys)
                    add(walls_by_room, rid, wall_pts)
                    if door_pts:
                        add(door_by_room, rid, door_pts)
                        add_key(key, door_pts)

        return walls_by_room, door_by_room, door_by_key

    # =========================================================
    # Room conversion (geom + tiles)
    # =========================================================

    def _type_to_room_type_name(self, t: int) -> str:
        if t == self.TYPES["CORRIDOR"]:
            return "corridor"
        if t == self.TYPES["LIVING"]:
            return "living"
        if t == self.TYPES["DINING"]:
            return "dining"
        if t == self.TYPES["BEDROOM"]:
            return "bedroom"
        if t == self.TYPES["SPECIAL"]:
            return "special"
        return "unknown"

    def _build_geom_json(self, cells: List[Cell]) -> JsonDict:
        """
        geom_json：尽量给 polygon；失败就 fallback rectangle bbox。
        corners 单位：fine grid 坐标
        """
        scale = self.grid_size
        loop = self._cells_to_outer_loop(cells)
        if loop:
            corners = [[vx * scale, vy * scale] for (vx, vy) in loop]
            cx = sum(p[0] for p in corners) / len(corners)
            cy = sum(p[1] for p in corners) / len(corners)
            return {"type": "polygon", "corners": corners, "center": [cx, cy]}

        # fallback bbox
        min_x = min(x for x, _ in cells)
        max_x = max(x for x, _ in cells)
        min_y = min(y for _, y in cells)
        max_y = max(y for _, y in cells)

        left = min_x * scale
        top = min_y * scale
        width = (max_x - min_x + 1) * scale
        height = (max_y - min_y + 1) * scale
        cx = left + width / 2
        cy = top + height / 2
        return {
            "type": "rectangle",
            "corners": [[left, top], [left + width, top], [left + width, top + height], [left, top + height]],
            "center": [cx, cy],
            "width": width,
            "height": height,
        }

    def _build_tiles_json(
        self,
        *,
        rid: int,
        room_cells: List[Cell],
        walls_by_room: Dict[int, Set[FineCell]],
        door_tiles_by_room: Dict[int, Set[FineCell]],
    ) -> Tuple[JsonDict, int]:
        """
        room.tiles_json：fine grid tiles
        - space: fill all fine tiles of coarse cells
        - wall: boundary line (1 tile thick)
        - door tiles 从 space 中扣掉（避免“两个房间共享同一批 door tile”的重叠）
        """
        scale = self.grid_size

        space_set: Set[FineCell] = set()
        for cx, cy in room_cells:
            base_x = cx * scale
            base_y = cy * scale
            for dx in range(scale):
                for dy in range(scale):
                    space_set.add((base_x + dx, base_y + dy))

        wall_set = walls_by_room.get(rid, set())
        door_set = door_tiles_by_room.get(rid, set())
        inner_wall_set: Set[FineCell] = set()

        # carve out walls / inner walls / doors from space
        if wall_set:
            space_set.difference_update(wall_set)
        if inner_wall_set:
            space_set.difference_update(inner_wall_set)
        if door_set:
            space_set.difference_update(door_set)

        wall_list = [[x, y] for (x, y) in sorted(wall_set)]
        space_list = [[x, y] for (x, y) in sorted(space_set)]
        inner_wall_list = [[x, y] for (x, y) in sorted(inner_wall_set)]
        area = len(space_list)

        return {"wall": wall_list, "space": space_list, "inner_wall": inner_wall_list}, area

    @staticmethod
    def _centroid_of_fine_tiles(tiles: List[FineCell]) -> Tuple[float, float]:
        # 用 tile 中心点平均
        xs = [x + 0.5 for (x, _) in tiles]
        ys = [y + 0.5 for (_, y) in tiles]
        return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))

    # =========================================================
    # Outer loop extraction (same idea as your original)
    # =========================================================

    def _cells_to_outer_loop(self, cells: List[Cell]) -> Optional[List[Cell]]:
        if not cells:
            return None
        cell_set = set(cells)

        edges: List[Tuple[Cell, Cell]] = []
        for x, y in cell_set:
            if (x, y - 1) not in cell_set:
                edges.append(((x, y), (x + 1, y)))
            if (x + 1, y) not in cell_set:
                edges.append(((x + 1, y), (x + 1, y + 1)))
            if (x, y + 1) not in cell_set:
                edges.append(((x + 1, y + 1), (x, y + 1)))
            if (x - 1, y) not in cell_set:
                edges.append(((x, y + 1), (x, y)))

        if not edges:
            return None

        outgoing: Dict[Cell, List[Cell]] = {}
        for a, b in edges:
            outgoing.setdefault(a, []).append(b)

        dir_order = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # E,S,W,N (y down)
        dir_index = {d: i for i, d in enumerate(dir_order)}

        def pick_next(prev: Cell, cur: Cell, cands: List[Cell], used: Set[Tuple[Cell, Cell]]) -> Cell:
            if len(cands) == 1:
                return cands[0]
            pd = (cur[0] - prev[0], cur[1] - prev[1])
            pi = dir_index.get(pd, 0)
            pref = [
                dir_order[(pi + 1) % 4],
                dir_order[pi],
                dir_order[(pi - 1) % 4],
                dir_order[(pi + 2) % 4],
            ]
            cand_map = {(nxt[0] - cur[0], nxt[1] - cur[1]): nxt for nxt in cands}
            for d in pref:
                nxt = cand_map.get(d)
                if nxt is not None and ((cur, nxt) not in used):
                    return nxt
            for nxt in cands:
                if (cur, nxt) not in used:
                    return nxt
            return cands[0]

        def poly_area(loop: List[Cell]) -> int:
            s = 0
            n = len(loop)
            for i in range(n):
                x1, y1 = loop[i]
                x2, y2 = loop[(i + 1) % n]
                s += x1 * y2 - y1 * x2
            return s

        def simplify(loop: List[Cell]) -> List[Cell]:
            if len(loop) <= 3:
                return loop
            out: List[Cell] = []
            n = len(loop)
            for i in range(n):
                px, py = loop[(i - 1) % n]
                cx, cy = loop[i]
                nx, ny = loop[(i + 1) % n]
                d1 = (cx - px, cy - py)
                d2 = (nx - cx, ny - cy)
                if d1 == d2:
                    continue
                out.append((cx, cy))
            return out

        used_edges: Set[Tuple[Cell, Cell]] = set()
        loops: List[List[Cell]] = []

        for a, b in edges:
            if (a, b) in used_edges:
                continue
            start = a
            prev = a
            cur = b
            loop = [start]
            used_edges.add((a, b))

            guard = 0
            while cur != start and guard < 5_000_000:
                guard += 1
                loop.append(cur)
                cands = outgoing.get(cur)
                if not cands:
                    loop = []
                    break
                nxt = pick_next(prev, cur, cands, used_edges)
                used_edges.add((cur, nxt))
                prev, cur = cur, nxt

            if loop:
                loop = simplify(loop)
                if len(loop) >= 3:
                    loops.append(loop)

        if not loops:
            return None
        loops.sort(key=lambda lp: abs(poly_area(lp)), reverse=True)
        return loops[0]
