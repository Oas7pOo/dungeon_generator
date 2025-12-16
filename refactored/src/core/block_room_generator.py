#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
方块房间生成器（Watabou 高级房间生成模拟版）

目标：尽量 1:1 复刻你提供的 HTML 逻辑，并输出兼容你现有 room 表结构的：
- wall_grid_list: 1格厚“边界墙线”（按 10x 放大后栅格化）
- space_grid_list: 房间内部地板（按 10x 放大后栅格化）
- door_grid_list: 门洞所在的“边界缺口”格子（保留门格子）
- inner_wall_grid_list: 默认不生成（与网页一致），如需可扩展

核心特性（按你的最新要求调整）：
1) 默认“空洞就是空洞”：封闭空洞不再变成 garden，也不会输出成 garden 房间
2) 可选 merge_holes=True：将封闭空洞合并到附近房间，但仅合并“只接触 1 个房间”的空洞连通块，
   以保证：只是给某个房间增加面积，不改变房间之间的联通关系
3) 合并空洞会改变墙：空洞周围原本的内墙线会被打通消失（因为两边变成同一房间）
4) 空洞不会改变门：门在合并空洞之前计算，合并后不重新计算门
5) 10x10 缩放：HTML 的 1 格在本程序里变成 10x10 的细格；墙线占 1 细格厚

调用参数（generate_rooms kwargs）：
- target_rooms: 目标房间数（HTML roomCountRange）
- mode: "corridor" 或 "room_chain"（HTML modeSelect）
- merge_holes: bool，默认 False
- door_gap: int（门缺口长度，细网格单位，默认自动 max(2, grid_size//3)）
"""

import random
import json
from typing import List, Dict, Tuple, Set, Optional
from collections import deque

from .room_generator import RoomGenerator
from ..db.database import DatabaseManager


class BlockRoomGenerator(RoomGenerator):
    # ===== Watabou TYPES（和你 HTML 保持一致，去掉 GARDEN 输出逻辑）=====
    TYPES = {
        "EMPTY": 0,
        "WALL": 1,        # HTML 的 grid 里其实不存 WALL（墙是边界线），这里保留常量但不直接用
        "CORRIDOR": 2,
        "LIVING": 3,
        "DINING": 4,
        "BEDROOM": 5,
        "SPECIAL": 6,
    }

    EDGE_MARGIN = 2  # HTML: 边缘安全距离

    def __init__(self, db_manager: DatabaseManager):
        super().__init__(db_manager)

        # 你的约定：10x10 代表网页 1 格
        self.grid_size = 10

        self.map_width = 0
        self.map_height = 0
        self.cols = 0
        self.rows = 0

        # 网页同款：grid 存类型；room_id_grid 存房间 id（EMPTY 初始为 -1）
        self.grid: List[List[int]] = []
        self.room_id_grid: List[List[int]] = []

        # rooms: id -> {"id":id,"type":type,"cells":[(x,y),...]}
        self.rooms: Dict[int, Dict] = {}
        self.room_order: List[int] = []  # 输出稳定顺序（id递增）

        # doors：保存边界 key（"V"/"H"）和房间对
        # key 形式：
        #   竖边（左右相邻）：("V", bx, y)  bx = x+1，表示列 x 与 x+1 之间
        #   横边（上下相邻）：("H", x, by)  by = y+1，表示行 y 与 y+1 之间
        self.doors: List[Dict] = []

        # 生成参数（可从 generate_rooms kwargs 覆盖）
        self.target_rooms = 40
        self.mode = "corridor"       # "corridor" / "room_chain"

        # 可选：合并封闭空洞（默认 False）
        self.merge_holes = False

        # 门洞缺口长度（在细网格里，以 1 格为单位）
        # HTML 门是 6px 小缺口；放大后取 scale//3 左右更像
        self.door_gap = None  # None -> 自动

    # ---------------- public API ----------------

    def generate_rooms(self, map_name: str, layer: int = 1, **kwargs) -> List[Dict]:
        """
        kwargs:
          - target_rooms: 目标房间数
          - mode: "corridor" 或 "room_chain"
          - merge_holes: bool（默认 False）
          - door_gap: int（门缺口长度，细网格单位）
        """
        map_size = self.db_manager.get_map_size(map_name)
        if not map_size:
            return []

        self.map_width, self.map_height = map_size
        self.cols = self.map_width // self.grid_size
        self.rows = self.map_height // self.grid_size

        # 读参数
        self.target_rooms = int(kwargs.get("target_rooms", self.target_rooms))
        self.mode = kwargs.get("mode", self.mode)
        # 兼容你旧调用：如果有人传 fill_gaps，就把它当 merge_holes（语义接近“填洞”）
        self.merge_holes = bool(kwargs.get("merge_holes", kwargs.get("fill_gaps", False)))
        self.door_gap = kwargs.get("door_gap", None)

        # 初始化
        self._init_grid()

        # 生成主体（先生成房间拓扑）
        if self.mode == "corridor":
            self._generate_branching_corridors()
            self._fill_rooms_corridor_based(self.target_rooms)
        else:
            self._start_room_chain()
            self._fill_rooms_neighbor_based(self.target_rooms)

        # doors：在“空洞还是 EMPTY”的阶段找门（保证之后 merge_holes 不改变门）
        self._find_doors()

        # merge holes：只影响墙和面积，不改变 door（不重算 door）
        if self.merge_holes:
            self._merge_enclosed_holes_into_rooms()

        # 边界墙线 & 门洞缺口：全局一次生成，然后按 roomId 分配
        walls_by_room, doors_by_room = self._rasterize_boundaries_to_tiles()

        # 转为 room dict 列表
        return self._convert_to_rooms(map_name, layer, walls_by_room, doors_by_room)

    # ---------------- core grid helpers ----------------

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
        dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]
        for dx, dy in dirs:
            nx, ny = x + dx, y + dy
            if self._in_bounds(nx, ny):
                if self.grid[ny][nx] in types:
                    return True
        return False

    # ---------------- Watabou: corridor mode ----------------

    def _is_valid_corridor_move(self, nx: int, ny: int) -> bool:
        # HTML: nx <= 1 || nx >= cols-1 || ...
        if nx <= 1 or nx >= self.cols - 1 or ny <= 1 or ny >= self.rows - 1:
            return False

        corridor_neighbors = 0
        for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            tx, ty = nx + dx, ny + dy
            if self._in_bounds(tx, ty) and self.grid[ty][tx] == self.TYPES["CORRIDOR"]:
                corridor_neighbors += 1

        # 目标是空地时，周围走廊太多则大概率禁止，小概率允许合并
        if self.grid[ny][nx] == self.TYPES["EMPTY"]:
            if corridor_neighbors > 1:
                return random.random() < 0.1
            return True

        # 允许走在现有走廊上
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

                # 90% 概率保持方向
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

                # 分支概率 0.05，walkers < 5，life > 10
                if random.random() < 0.05 and len(walkers) < 5 and w["life"] > 10:
                    bdx, bdy = w["dir"]
                    if bdx != 0:
                        branch = (0, 1)
                    else:
                        branch = (1, 0)
                    if random.random() > 0.5:
                        branch = (-branch[0], -branch[1])

                    walkers.append({
                        "x": w["x"],
                        "y": w["y"],
                        "life": int(w["life"] * 0.6),
                        "dir": branch
                    })

    def _grow_room(self, seed: Tuple[int, int], t: int, rid: int, max_size: int) -> Tuple[bool, List[Tuple[int, int]]]:
        """
        Watabou growRoom 复刻：
        - openList 生长（blob or snake）
        - 非走廊房间如果 touchedEdge 则失败并回滚
        返回：(success, placed_cells)
        """
        sx, sy = seed
        if not self._in_bounds(sx, sy) or self.grid[sy][sx] != self.TYPES["EMPTY"]:
            return False, []

        placed = []
        open_list = [(sx, sy)]
        touched_edge = False

        # compactness: 70% snake / 30% blob（HTML: Math.random() > 0.7 ? snake : blob）
        compactness = "snake" if random.random() > 0.7 else "blob"

        self._set_cell(sx, sy, t, rid)
        placed.append((sx, sy))
        size = 1

        while open_list and size < max_size:
            if compactness == "blob":
                idx = random.randrange(len(open_list))
            else:
                idx = len(open_list) - 1

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

        # touchedEdge：非 corridor 失败
        if t != self.TYPES["CORRIDOR"] and touched_edge:
            for x, y in placed:
                self.grid[y][x] = self.TYPES["EMPTY"]
                self.room_id_grid[y][x] = -1
            return False, []

        return True, placed

    def _fill_rooms_corridor_based(self, target_count: int):
        safety = 5000
        rid_counter = 1  # corridor 占用 0

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

    # ---------------- Watabou: room_chain mode ----------------

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
        rid_counter = 2  # startRoomChain 已占 1

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

    # ---------------- doors (Watabou) ----------------

    def _find_doors(self):
        """
        HTML: findDoors() + checkDoor()
        doors 只在两个不同 roomId 的“地板格子”之间产生（排除 EMPTY）
        """
        self.doors = []
        for y in range(self.rows):
            for x in range(self.cols):
                curr_id = self.room_id_grid[y][x]
                curr_type = self.grid[y][x]
                if curr_id == -1:
                    continue
                if curr_type == self.TYPES["EMPTY"]:
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
                if is_vertical:
                    key = ("H", x, y + 1)  # 横边：在 y 与 y+1 之间
                else:
                    key = ("V", x + 1, y)  # 竖边：在 x 与 x+1 之间

                self.doors.append({
                    "key": key,
                    "r1": r1,
                    "r2": r2,
                    "vertical": is_vertical
                })

    # ---------------- enclosed holes + merge ----------------

    def _mark_outside_empty(self) -> List[List[bool]]:
        """
        标记所有与边界连通的 EMPTY 区域（outside）。
        比 HTML 更稳：从所有边界 EMPTY 一起 flood fill，而不是只从 (0,0)。
        """
        outside = [[False for _ in range(self.cols)] for _ in range(self.rows)]
        q = deque()
        dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]

        def push(x: int, y: int):
            outside[y][x] = True
            q.append((x, y))

        # 上下边界
        for x in range(self.cols):
            if self.grid[0][x] == self.TYPES["EMPTY"] and not outside[0][x]:
                push(x, 0)
            if self.grid[self.rows - 1][x] == self.TYPES["EMPTY"] and not outside[self.rows - 1][x]:
                push(x, self.rows - 1)

        # 左右边界
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

    def _find_enclosed_hole_components(self) -> List[List[Tuple[int, int]]]:
        """
        找出所有“封闭空洞”（EMPTY 且不与边界 EMPTY 连通）的连通块列表。
        """
        outside = self._mark_outside_empty()
        visited = [[False for _ in range(self.cols)] for _ in range(self.rows)]
        dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]

        comps: List[List[Tuple[int, int]]] = []

        for y in range(self.rows):
            for x in range(self.cols):
                if visited[y][x]:
                    continue
                if self.grid[y][x] != self.TYPES["EMPTY"]:
                    continue
                if outside[y][x]:
                    continue  # 外部空地，不算洞

                # BFS 取一个洞连通块
                comp = []
                q = deque()
                q.append((x, y))
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
        """
        合并封闭空洞到附近房间：
        - 只合并“该洞的边界仅接触 1 个房间 id”的洞
        - 这样只是增加该房间面积，不引入新的房间-房间邻接关系
        - doors 不重算（门保持生成时的结果）
        """
        hole_comps = self._find_enclosed_hole_components()
        if not hole_comps:
            return

        dirs = [(0, 1), (0, -1), (1, 0), (-1, 0)]
        # 为了避免重复 cell，做一个 room->set 快速去重
        room_cell_sets: Dict[int, Set[Tuple[int, int]]] = {}
        for rid in self.room_order:
            room = self.rooms.get(rid)
            if not room:
                continue
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

            # 只接触 1 个房间：安全合并
            if len(neighbor_ids) == 1:
                rid = next(iter(neighbor_ids))
                room = self.rooms.get(rid)
                if not room:
                    continue
                t = room["type"]

                cell_set = room_cell_sets.setdefault(rid, set())

                for x, y in comp:
                    # 变成该房间的粗格
                    self.grid[y][x] = t
                    self.room_id_grid[y][x] = rid
                    if (x, y) not in cell_set:
                        room["cells"].append((x, y))
                        cell_set.add((x, y))

            # 触摸多个房间：不合并，避免改变联通关系
            else:
                continue

    # ---------------- walls/doors rasterization ----------------

    def _rasterize_boundaries_to_tiles(self) -> Tuple[Dict[int, Set[Tuple[int, int]]], Dict[int, Set[Tuple[int, int]]]]:
        """
        把“边界线”栅格化成 1000x1000 细格子上的 wall/door tile 坐标。

        规则：
        - 扫描 RIGHT 和 DOWN 边界，每条边界只生成一次
        - 房间-房间边界：墙线分配给两侧房间（共享墙）
        - 房间-EMPTY 边界：墙线分配给该房间（空洞存在时会产生“内墙线”）
        - door：在对应边界线上挖缺口，把缺口段写入 door_grid_list，并从 wall 中移除
        """
        scale = self.grid_size
        gap = self.door_gap if isinstance(self.door_gap, int) else max(2, scale // 3)

        door_keys = set(d["key"] for d in self.doors)

        walls_by_room: Dict[int, Set[Tuple[int, int]]] = {}
        doors_by_room: Dict[int, Set[Tuple[int, int]]] = {}

        def add_wall_for_room(rid: int, pts: Set[Tuple[int, int]]):
            if rid not in walls_by_room:
                walls_by_room[rid] = set()
            walls_by_room[rid].update(pts)

        def add_door_for_room(rid: int, pts: Set[Tuple[int, int]]):
            if rid not in doors_by_room:
                doors_by_room[rid] = set()
            doors_by_room[rid].update(pts)

        def carve_line(points: List[Tuple[int, int]], is_door: bool) -> Tuple[Set[Tuple[int, int]], Set[Tuple[int, int]]]:
            if not is_door:
                return set(points), set()

            n = len(points)
            c = n // 2
            s = max(0, c - gap // 2)
            e = min(n, s + gap)

            wall_pts = set()
            door_pts = set()
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

                # RIGHT 边界
                bx = x + 1
                nx = x + 1
                ny = y
                right_id = None
                if nx < self.cols:
                    right_id = self.room_id_grid[ny][nx]

                if nx >= self.cols or right_id != rid:
                    key = ("V", bx, y)
                    fx = bx * scale - 1
                    pts_list = [(fx, y * scale + k) for k in range(scale)]
                    wall_pts, door_pts = carve_line(pts_list, key in door_keys)

                    add_wall_for_room(rid, wall_pts)
                    if door_pts:
                        add_door_for_room(rid, door_pts)

                    if right_id is not None and right_id != -1:
                        add_wall_for_room(right_id, wall_pts)
                        if door_pts:
                            add_door_for_room(right_id, door_pts)

                # DOWN 边界
                by = y + 1
                nx = x
                ny = y + 1
                down_id = None
                if ny < self.rows:
                    down_id = self.room_id_grid[ny][nx]

                if ny >= self.rows or down_id != rid:
                    key = ("H", x, by)
                    fy = by * scale - 1
                    pts_list = [(x * scale + k, fy) for k in range(scale)]
                    wall_pts, door_pts = carve_line(pts_list, key in door_keys)

                    add_wall_for_room(rid, wall_pts)
                    if door_pts:
                        add_door_for_room(rid, door_pts)

                    if down_id is not None and down_id != -1:
                        add_wall_for_room(down_id, wall_pts)
                        if door_pts:
                            add_door_for_room(down_id, door_pts)
                
                # LEFT 边界（补齐：当左边是 EMPTY 或越界时）
                if x == 0:
                    left_id = None
                else:
                    left_id = self.room_id_grid[y][x - 1]

                if x == 0 or left_id == -1:
                    key = ("V", x, y)  # 边界在列 x 与 x-1 之间；与 door key 规则兼容
                    fx = x * scale     # 注意：左边界放在本格内部的最左列
                    pts_list = [(fx, y * scale + k) for k in range(scale)]
                    wall_pts, door_pts = carve_line(pts_list, key in door_keys)

                    add_wall_for_room(rid, wall_pts)
                    if door_pts:
                        add_door_for_room(rid, door_pts)

                # UP 边界（补齐：当上边是 EMPTY 或越界时）
                if y == 0:
                    up_id = None
                else:
                    up_id = self.room_id_grid[y - 1][x]

                if y == 0 or up_id == -1:
                    key = ("H", x, y)  # 边界在行 y 与 y-1 之间；与 door key 规则兼容
                    fy = y * scale     # 下边界放在本格内部的最下行（视觉上就是“下墙”）
                    pts_list = [(x * scale + k, fy) for k in range(scale)]
                    wall_pts, door_pts = carve_line(pts_list, key in door_keys)

                    add_wall_for_room(rid, wall_pts)
                    if door_pts:
                        add_door_for_room(rid, door_pts)

        return walls_by_room, doors_by_room

    # ---------------- conversion to DB room dicts ----------------

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

    def _cells_to_outer_loop(self, cells: List[Tuple[int, int]]) -> Optional[List[Tuple[int, int]]]:
        """
        将房间的粗格 cells(polyomino) 转成“外轮廓”顶点环（顶点坐标单位：粗格的角点坐标）。
        - 返回值不重复首尾点（即不闭合重复 start）
        - 若存在洞，会同时产生多个环；这里取面积最大的那个作为外环
        - 输出会做 collinear 简化（同一直线上的中间点去掉）
        """
        if not cells:
            return None

        cell_set = set(cells)

        # 以“屏幕坐标系 y 向下”为准，按顺时针方向生成边界有向边
        # 每条边是 (p -> q)，p/q 为角点坐标 (vx, vy)
        edges: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
        for x, y in cell_set:
            # 上边：若上方没有同房间格子
            if (x, y - 1) not in cell_set:
                edges.append(((x, y), (x + 1, y)))
            # 右边
            if (x + 1, y) not in cell_set:
                edges.append(((x + 1, y), (x + 1, y + 1)))
            # 下边
            if (x, y + 1) not in cell_set:
                edges.append(((x + 1, y + 1), (x, y + 1)))
            # 左边
            if (x - 1, y) not in cell_set:
                edges.append(((x, y + 1), (x, y)))

        if not edges:
            return None

        # start -> [end...]
        outgoing: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        for a, b in edges:
            outgoing.setdefault(a, []).append(b)

        # 用“右手法则”在可能的分叉处选边（理论上外轮廓不会分叉，但为鲁棒性留着）
        dir_order = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # E,S,W,N (y向下)
        dir_index = {d: i for i, d in enumerate(dir_order)}

        def pick_next(prev: Tuple[int, int], cur: Tuple[int, int], cands: List[Tuple[int, int]], used_edges: Set[Tuple[Tuple[int,int],Tuple[int,int]]]):
            if len(cands) == 1:
                return cands[0]
            pd = (cur[0] - prev[0], cur[1] - prev[1])
            pi = dir_index.get(pd, 0)

            # 顺时针走边界：优先“右转”，其次直行，其次左转，最后回头
            pref = [
                dir_order[(pi + 1) % 4],  # right turn
                dir_order[pi],            # straight
                dir_order[(pi - 1) % 4],  # left
                dir_order[(pi + 2) % 4],  # back
            ]
            cand_map = {(nxt[0] - cur[0], nxt[1] - cur[1]): nxt for nxt in cands}
            for d in pref:
                nxt = cand_map.get(d)
                if nxt is not None and ((cur, nxt) not in used_edges):
                    return nxt
            # 兜底：选一个没用过的，否则随便一个
            for nxt in cands:
                if (cur, nxt) not in used_edges:
                    return nxt
            return cands[0]

        def poly_area(loop: List[Tuple[int, int]]) -> int:
            # shoelace，返回 2*area（带符号）。在 y 向下坐标里，顺时针通常为正。
            s = 0
            n = len(loop)
            for i in range(n):
                x1, y1 = loop[i]
                x2, y2 = loop[(i + 1) % n]
                s += x1 * y2 - y1 * x2
            return s

        def simplify(loop: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
            if len(loop) <= 3:
                return loop
            out = []
            n = len(loop)
            for i in range(n):
                px, py = loop[(i - 1) % n]
                cx, cy = loop[i]
                nx, ny = loop[(i + 1) % n]
                d1 = (cx - px, cy - py)
                d2 = (nx - cx, ny - cy)
                if d1 == d2:
                    continue  # 共线冗余点
                out.append((cx, cy))
            return out

        used_edges: Set[Tuple[Tuple[int, int], Tuple[int, int]]] = set()
        loops: List[List[Tuple[int, int]]] = []

        # 遍历所有边，提取所有闭环（外环+洞环）
        for a, b in edges:
            if (a, b) in used_edges:
                continue

            start = a
            prev = a
            cur = b
            loop = [start]

            used_edges.add((a, b))

            # 防止异常死循环
            guard = 0
            while cur != start and guard < 10_000_000:
                guard += 1
                loop.append(cur)
                cands = outgoing.get(cur)
                if not cands:
                    loop = []  # 开口轮廓，丢弃
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

        # 取“面积绝对值最大”的那个环作为外轮廓
        loops.sort(key=lambda lp: abs(poly_area(lp)), reverse=True)
        return loops[0]


    def _convert_to_rooms(
        self,
        map_name: str,
        layer: int,
        walls_by_room: Dict[int, Set[Tuple[int, int]]],
        doors_by_room: Dict[int, Set[Tuple[int, int]]],
    ) -> List[Dict]:
        """
        输出与你现有 room 表兼容的 dict 列表（每个房间一条记录）
        - space_grid_list：房间 cell 的 10x10 全部填充，然后扣掉 wall/inner_wall
        - wall_grid_list：边界墙线（1格厚）
        - door_grid_list：门缺口格子
        """
        rooms_out: List[Dict] = []
        scale = self.grid_size

        for rid in self.room_order:
            room = self.rooms.get(rid)
            if not room:
                continue

            cells: List[Tuple[int, int]] = room["cells"]
            if not cells:
                continue

            min_x = min(x for x, _ in cells)
            max_x = max(x for x, _ in cells)
            min_y = min(y for _, y in cells)
            max_y = max(y for _, y in cells)

            left = min_x * scale
            top = min_y * scale
            width = (max_x - min_x + 1) * scale
            height = (max_y - min_y + 1) * scale
            center_x = left + width / 2
            center_y = top + height / 2

            # 真实轮廓（coarse vertex units -> fine units）
            loop = self._cells_to_outer_loop(cells)
            if loop:
                corners = [[vx * scale, vy * scale] for vx, vy in loop]
                # center 简单用 corners 平均即可（够用且稳定）
                center_x = sum(p[0] for p in corners) / len(corners)
                center_y = sum(p[1] for p in corners) / len(corners)

                vector_params = {
                    "type": "polygon",
                    "corners": corners,
                    "center": [center_x, center_y],
                }
            else:
                # 兜底：万一没提取到环（理论不该），退回包围盒
                min_x = min(x for x, _ in cells)
                max_x = max(x for x, _ in cells)
                min_y = min(y for _, y in cells)
                max_y = max(y for _, y in cells)

                left = min_x * scale
                top = min_y * scale
                width = (max_x - min_x + 1) * scale
                height = (max_y - min_y + 1) * scale
                center_x = left + width / 2
                center_y = top + height / 2
                vector_params = {
                    "type": "rectangle",
                    "corners": [
                        [left, top],
                        [left + width, top],
                        [left + width, top + height],
                        [left, top + height],
                    ],
                    "center": [center_x, center_y],
                    "width": width,
                    "height": height,
                }

            space_set: Set[Tuple[int, int]] = set()
            for cx, cy in cells:
                base_x = cx * scale
                base_y = cy * scale
                for dx in range(scale):
                    for dy in range(scale):
                        space_set.add((base_x + dx, base_y + dy))

            wall_set = walls_by_room.get(rid, set())
            door_set = doors_by_room.get(rid, set())

            inner_wall_set: Set[Tuple[int, int]] = set()

            if wall_set:
                space_set.difference_update(wall_set)
            if inner_wall_set:
                space_set.difference_update(inner_wall_set)

            wall_grid_list = [[x, y] for (x, y) in wall_set]
            door_grid_list = [[x, y] for (x, y) in door_set]
            inner_wall_grid_list = [[x, y] for (x, y) in inner_wall_set]
            space_grid_list = [[x, y] for (x, y) in space_set]

            area = len(space_grid_list)
            room_type_name = self._type_to_room_type_name(room["type"])

            room_dict = {
                "name": f"room_{rid}",
                "map_name": map_name,
                "min_layer": layer,
                "max_layer": layer,
                "building_area": "新式房间",
                "wall_grid_list": json.dumps(wall_grid_list),
                "space_grid_list": json.dumps(space_grid_list),
                "inner_wall_grid_list": json.dumps(inner_wall_grid_list),
                "door_grid_list": json.dumps(door_grid_list),
                "room_type": room_type_name,
                "vector_params": json.dumps(vector_params),
                "other_params": json.dumps({
                    "grid_size": scale,
                    "watabou_mode": self.mode,
                    "target_rooms": self.target_rooms,
                    "merge_holes": self.merge_holes,
                    "door_gap": self.door_gap if self.door_gap is not None else max(2, scale // 3),
                }),
                "area": area,
            }

            rooms_out.append(room_dict)

        return rooms_out
