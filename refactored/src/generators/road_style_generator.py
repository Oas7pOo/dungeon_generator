# -*- coding: utf-8 -*-
"""
路网风格生成器（RoadStyleGenerator）：把"路网怎么连"的完整机制内部化，
生成地图时只需调用，不用现写。

提供（生成脚本只做配置 + 调用）：
- 建筑区随机排布（place_areas：网格分区 + 抖动）
- 建筑放置（place_buildings：矩形 / 圆形 / 斜矩形）
- 任意形状外墙开门（make_door：8 邻外向检测，斜边门外一点不会算进建筑）
- 寻路：直角（4 向 A* 右角折线）/ 弯曲（8 向 A*）/ 确定性兜底 / BFS（a_star / a_star_fallback / bfs_connect）
- 视觉直走（center_walk：出门直走朝中心 + 视野扩大 + 视野内接入）
- 曲线：圆角曲线（rounded_curve：直线段 + 转角圆弧，无 Catmull-Rom 过冲）/ 直角折线
- 道路保存（save_road：门口接通 + 建筑内裁剪 + 墙剔建筑格 + 交叉打通 + 大中小路宽）
- 区内连接策略（connect_area：直角/弯曲 × 稠密/稀疏）
- 区际连接（connect_inter_area：中路连接 / 孤立区）
"""
from __future__ import annotations

import json
import math
import random
import heapq
from typing import Any, Dict, List, Optional, Set, Tuple

from shapely.geometry import box, Point, Polygon

from ..db.database import DatabaseManager
from .road_generator import UnionFind

JsonDict = Dict[str, Any]
Cell = Tuple[int, int]

DIRS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIRS8 = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
DIAG_COST = 1.4


class RoadStyleGenerator:
    """路网风格生成器：直角/弯曲 × 稠密/稀疏 × 大中小路宽。"""

    ROAD_SMALL_W = 5      # 小路（区内）
    ROAD_MED_W = 10       # 中路（区际）
    ROAD_LARGE_W = 15     # 大路（规划：跨城镇主干）
    CLEAR_ZONE = 2        # 建筑周围避让格数（路径距建筑 ≥ CLEAR_ZONE+1）
    DOOR_W = 3

    def __init__(self, db_manager: DatabaseManager, map_id: int, seed: Optional[int] = None):
        self.db = db_manager
        self.map_id = int(map_id)
        self.seed = seed if seed is not None else random.randrange(0, 2 ** 31 - 1)
        self.rng = random.Random(self.seed)
        self.map_w = 0
        self.map_h = 0
        self.obstacles: Set[Cell] = set()
        self.building_cells: Set[Cell] = set()
        self.existing_roads: List[Tuple[int, JsonDict]] = []
        self.road_counter = [0]

    # ==================================================================
    # 基础工具
    # ==================================================================
    @staticmethod
    def _loads(s: Optional[str], default: Any = None) -> Any:
        if not s:
            return default
        try:
            return json.loads(s)
        except Exception:
            return default

    @staticmethod
    def _rasterize_segment(p1: Cell, p2: Cell) -> List[Cell]:
        """画线段（Bresenham 风格，支持 8 向）。"""
        x1, y1 = p1
        x2, y2 = p2
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        sx, sy = (1 if x1 < x2 else -1), (1 if y1 < y2 else -1)
        err = dx - dy
        cells = []
        cx, cy = x1, y1
        while True:
            cells.append((cx, cy))
            if cx == x2 and cy == y2:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                cx += sx
            if e2 < dx:
                err += dx
                cy += sy
        return cells

    def band_for_points(self, dense_pts: List[Tuple[float, float]], w: int) -> Set[Cell]:
        """曲线稠密点 → 道路带（逐段栅格化 + ±half 方块并集），裁剪到地图内。"""
        half = w // 2
        cells: Set[Cell] = set()
        for i in range(len(dense_pts) - 1):
            p1 = (int(round(dense_pts[i][0])), int(round(dense_pts[i][1])))
            p2 = (int(round(dense_pts[i + 1][0])), int(round(dense_pts[i + 1][1])))
            cells.update(self._rasterize_segment(p1, p2))
        band: Set[Cell] = set()
        for (x, y) in cells:
            for dx in range(-half, half + 1):
                for dy in range(-half, half + 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.map_w and 0 <= ny < self.map_h:
                        band.add((nx, ny))
        return band

    @staticmethod
    def manhattan(a: Cell, b: Cell) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def in_map(self, p: Cell) -> bool:
        return 0 <= p[0] < self.map_w and 0 <= p[1] < self.map_h

    # ==================================================================
    # 建筑区随机排布（函数）
    # ==================================================================
    def place_areas(self, n: int, area_w: int, area_h: int, rng: Optional[random.Random] = None) -> List[JsonDict]:
        """随机排布 n 个 area_w x area_h 建筑区：地图分 2x3 网格单元 + 单元内随机抖动 +
        区号随机分配（保证区距、必然放得下）。返回 area dict 列表。"""
        rng = rng or self.rng
        grid_w = self.map_w // 3
        grid_h = self.map_h // 2
        cells = [(grid_w * c, grid_h * r, grid_w * (c + 1), grid_h * (r + 1))
                 for r in range(2) for c in range(3)]
        rng.shuffle(cells)
        areas = []
        for i, (cx0, cy0, cx1, cy1) in enumerate(cells[:n]):
            x0 = rng.randint(cx0 + 25, cx1 - area_w - 25)
            y0 = rng.randint(cy0 + 40, cy1 - area_h - 40)
            x1, y1 = x0 + area_w, y0 + area_h
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            cur = self.db.execute(
                "INSERT INTO building_area (map_id, name, layer_start, layer_end, geom_type, "
                "center_x, center_y, radius, geom_json, size_json) VALUES (?, ?, 1, 1, 'rectangle', ?, ?, NULL, NULL, ?)",
                (self.map_id, f"区{i + 1}", cx, cy,
                 json.dumps({"width": x1 - x0, "height": y1 - y0})),
            )
            areas.append({"index": i, "area_id": int(cur.lastrowid), "name": f"区{i + 1}",
                          "bbox": (x0, y0, x1, y1), "center": [(x0 + x1) / 2, (y0 + y1) / 2]})
        return areas

    # ==================================================================
    # 建筑放置：矩形 / 圆形 / 斜矩形
    # ==================================================================
    @staticmethod
    def _tiles_of(space: List[List[int]]) -> JsonDict:
        sp = {(int(x), int(y)) for (x, y) in space}
        wall = [[x, y] for (x, y) in sp
                if any((x + dx, y + dy) not in sp for dx, dy in
                       ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)))]
        return {"wall": wall, "space": space, "inner_wall": []}

    def place_buildings(self, area_id: int, bbox: Tuple[float, float, float, float],
                        n: int = 10, gap: int = 8,
                        kinds: Optional[List[str]] = None,
                        rng: Optional[random.Random] = None) -> List[JsonDict]:
        """区内放 n 个建筑（kinds: 'rect'/'circle'/'rotated'，默认全矩形）。"""
        rng = rng or self.rng
        x0a, y0a, x1a, y1a = bbox
        kinds = kinds or (["rect"] * n)
        placed: List[JsonDict] = []
        polys = []

        def _fits(poly) -> bool:
            return not any(poly.buffer(gap).intersects(p) for p in polys)

        for i, kind in enumerate(kinds):
            for _try in range(6000):
                if kind == "rect":
                    w = rng.randint(25, 60)
                    h = rng.randint(25, 60)
                    x0 = rng.randint(int(x0a) + 12, int(x1a) - 12 - w)
                    y0 = rng.randint(int(y0a) + 12, int(y1a) - 12 - h)
                    x1, y1 = x0 + w, y0 + h
                    poly = box(x0, y0, x1, y1)
                    if not _fits(poly):
                        continue
                    space = [[x, y] for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
                    geom = {"type": "rectangle", "bbox": [x0, y0, x1, y1],
                            "center": [(x0 + x1) / 2.0, (y0 + y1) / 2.0],
                            "width": float(w), "height": float(h)}
                    center = [(x0 + x1) / 2.0, (y0 + y1) / 2.0]
                elif kind == "circle":
                    r = rng.randint(15, 25)
                    cx = rng.randint(int(x0a) + 12 + r, int(x1a) - 12 - r)
                    cy = rng.randint(int(y0a) + 12 + r, int(y1a) - 12 - r)
                    poly = box(cx - r, cy - r, cx + r, cy + r)
                    if not _fits(poly):
                        continue
                    space = []
                    for x in range(int(cx) - r - 1, int(cx) + r + 2):
                        for y in range(int(cy) - r - 1, int(cy) + r + 2):
                            if ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) <= r * r:
                                space.append([x, y])
                    geom = {"type": "circle", "center": [cx, cy], "radius": float(r)}
                    center = [float(cx), float(cy)]
                else:  # rotated
                    w = rng.randint(30, 55)
                    h = rng.randint(30, 55)
                    cx = rng.randint(int(x0a) + 30, int(x1a) - 30)
                    cy = rng.randint(int(y0a) + 30, int(y1a) - 30)
                    angle = rng.choice([-60, -45, -30, 30, 45, 60])
                    a = math.radians(angle)
                    hw, hh = w / 2.0, h / 2.0
                    cos_a, sin_a = math.cos(a), math.sin(a)
                    corners = []
                    for lx, ly in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                        corners.append((cx + lx * cos_a - ly * sin_a, cy + lx * sin_a + ly * cos_a))
                    diag = ((w / 2) ** 2 + (h / 2) ** 2) ** 0.5
                    if cx - diag < x0a + 8 or cx + diag > x1a - 8 or cy - diag < y0a + 8 or cy + diag > y1a - 8:
                        continue
                    poly = Polygon(corners)
                    minx = min(p[0] for p in corners)
                    maxx = max(p[0] for p in corners)
                    miny = min(p[1] for p in corners)
                    maxy = max(p[1] for p in corners)
                    space = []
                    for x in range(int(minx) - 1, int(maxx) + 2):
                        for y in range(int(miny) - 1, int(maxy) + 2):
                            if poly.contains(Point(x + 0.5, y + 0.5)):
                                space.append([x, y])
                    if len(space) < 100:
                        continue
                    sp = {(int(x), int(y)) for (x, y) in space}
                    if not _fits(box(min(x for x, _ in sp), min(y for _, y in sp),
                                     max(x for x, _ in sp), max(y for _, y in sp))):
                        continue
                    geom = {"type": "rotated_rectangle", "center": [cx, cy], "angle": float(angle),
                            "width": float(w), "height": float(h),
                            "corners": [[float(a), float(b)] for a, b in corners]}
                    center = [float(cx), float(cy)]
                polys.append(poly)
                name = f"B{area_id}_{i + 1}_{kind}"
                cur = self.db.execute(
                    "INSERT INTO room (map_id, building_area_id, name, layer_start, layer_end, room_type, "
                    "geom_json, tiles_json, area, other_json) VALUES (?, ?, ?, 1, 1, 'building', ?, ?, ?, ?)",
                    (self.map_id, area_id, name,
                     json.dumps(geom, ensure_ascii=False),
                     json.dumps(self._tiles_of(space), ensure_ascii=False),
                     len(space), json.dumps({"generator": "road_style", "area_id": area_id, "kind": kind})),
                )
                placed.append({"id": int(cur.lastrowid), "name": name, "kind": kind,
                               "center": center, "geom": geom})
                break
        return placed

    # ==================================================================
    # 任意形状外墙开门（8 邻外向，斜边门外一点不会算进建筑）
    # ==================================================================
    def make_door(self, building: JsonDict, rng: Optional[random.Random] = None,
                  blocked: Optional[Set[Cell]] = None,
                  width: Optional[int] = None) -> Optional[JsonDict]:
        """任意形状外墙开门：取朝向随机方向、**门外一点无建筑**、**同一墙段（不包角）**
        的连续墙格（≥2）。8 邻外向检测；blocked = 障碍格（建筑格+外扩圈，传
        `gen.obstacles` 而非 building_cells——A* 的障碍含外扩圈，门外点必须避开）。

        门外点约束：`door_exterior` 会取门外 `width//2+1` 格（区内 5 宽 = 3 格）作为
        A* 起点，该点必须在 blocked 外（斜矩形/圆形斜边墙的门外点可能落进别的建筑或
        其外扩圈 → 起点在障碍里、该建筑所有连接失败）。只查这一个点：路径其余部分由
        A* 自动避障，带可贴建筑走（保存时只剪建筑格，外扩圈格保留为路）。
        返回 None 表示没找到合法门位。"""
        rng = rng or self.rng
        t = self._loads(self.db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (building["id"],))["tiles_json"], {})
        space = {(int(x), int(y)) for (x, y) in (t.get("space") or [])}
        wall = [(int(x), int(y)) for (x, y) in (t.get("wall") or [])]
        wall_set = set(wall)
        cx, cy = building["center"]

        def _outward(cell):
            """墙格外向：优先唯一主向自由格（轴对齐墙拿到正法向），否则 8 邻径向匹配。"""
            x, y = cell
            card = [d for d in DIRS4 if (x + d[0], y + d[1]) not in space]
            if len(card) == 1:
                return card[0]
            free = [d for d in DIRS8 if (x + d[0], y + d[1]) not in space]
            if not free:
                return None
            rx, ry = x + 0.5 - cx, y + 0.5 - cy
            rl = (rx * rx + ry * ry) ** 0.5 or 1.0
            best, bd = free[0], -1e9
            for d in free:
                dot = (d[0] * rx + d[1] * ry) / rl
                if dot > bd:
                    bd, best = dot, d
            return best

        def _wall_run(start, out_dir):
            """沿墙收集完整直线墙段：8 邻连续、外向与 out_dir ≤45°、尽量直走
            （对角墙的阶梯、圆的弧都能收长段，角处外向转 90° 即停）。"""
            run = [start]
            for _sign in (1, -1):
                prev = start
                last_dir = None
                while True:
                    cands = []
                    for d in DIRS8:
                        q = (prev[0] + d[0], prev[1] + d[1])
                        if q in wall_set and q not in run:
                            oq = _outward(q)
                            if oq is not None and oq[0] * out_dir[0] + oq[1] * out_dir[1] >= 1:
                                cands.append((d, q))
                    if not cands:
                        break
                    if last_dir:
                        cands.sort(key=lambda c: -(c[0][0] * last_dir[0] + c[0][1] * last_dir[1]))
                    d, q = cands[0]
                    run.append(q)
                    prev = q
                    last_dir = d
            return sorted(set(run))

        cands = []
        for (x, y) in wall:
            o = _outward((x, y))
            if o is not None:
                cands.append(((x, y), o))
        if not cands:
            return None
        ang = rng.uniform(0, 2 * math.pi)

        def _adir(d):
            return math.atan2(d[1], d[0])

        cands.sort(key=lambda c: abs((_adir(c[1]) - ang + math.pi) % (2 * math.pi) - math.pi))

        for (mid_cell, out_dir) in cands:
            run = _wall_run(mid_cell, out_dir)
            # 门必须取墙段中部、离拐角 ≥3 格（墙段长 ≥ DOOR_W + 6）
            if len(run) < self.DOOR_W + 6:
                continue          # 短墙段 / 靠近拐角 → 拒绝，换候选（可在其他位置重新开门）
            mid_i = len(run) // 2
            door_cells = run[mid_i - self.DOOR_W // 2: mid_i - self.DOOR_W // 2 + self.DOOR_W]
            if len(door_cells) < 2:
                continue
            dmid = door_cells[len(door_cells) // 2]
            # 门外点（door_exterior 会取门外 width//2+1 格）必须避开障碍：
            # 斜矩形/圆形斜边墙的门外点可能落进别的建筑或外扩圈 → A* 起点在障碍里、连接失败。
            if blocked is not None:
                w_half = (width if width is not None else self.ROAD_SMALL_W) // 2 + 1
                ex = (dmid[0] + out_dir[0] * w_half, dmid[1] + out_dir[1] * w_half)
                if ex in blocked:
                    continue          # 门外撞障碍 → 换候选
            door_center = (dmid[0] + 0.5, dmid[1] + 0.5)

            t = self._loads(self.db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (building["id"],))["tiles_json"], {})
            t["wall"] = [w for w in (t.get("wall") or []) if (int(w[0]), int(w[1])) not in set(door_cells)]
            self.db.execute("UPDATE room SET tiles_json = ? WHERE id = ?",
                            (json.dumps(t, ensure_ascii=False), building["id"]))

            self.db.execute(
                "INSERT INTO item (map_id, room_id, building_area_id, name, item_type, layer_start, layer_end, "
                "timestep, position_x, position_y, vector_json, tiles_json, properties_json) "
                "VALUES (?, ?, ?, ?, ?, 1, 1, 0, ?, ?, ?, ?, ?)",
                (self.map_id, building["id"], None, f"Door_{building['name']}", "door",
                 door_center[0], door_center[1],
                 json.dumps({"type": "circle", "center": [door_center[0], door_center[1]], "radius": self.ROAD_SMALL_W / 2}),
                 json.dumps({"wall_tiles": [list(c) for c in door_cells]}),
                 json.dumps({"opening": "door", "role": "road_entrance", "connects_room": building["id"]})),
            )
            return {"building_id": building["id"], "door_cells": door_cells,
                    "door_mid": dmid, "dir": out_dir}
        return None

    @staticmethod
    def door_exterior(d: JsonDict, width: int) -> Cell:
        m = d["door_mid"]
        return (m[0] + d["dir"][0] * (width // 2 + 1), m[1] + d["dir"][1] * (width // 2 + 1))

    # ==================================================================
    # 障碍
    # ==================================================================
    def build_obstacles(self, buildings: List[JsonDict], clear_zone: Optional[int] = None) -> Set[Cell]:
        """障碍 = 全部建筑格 + 外扩 clear_zone 格（8 邻逐层）。"""
        clear_zone = clear_zone if clear_zone is not None else self.CLEAR_ZONE
        cells: Set[Cell] = set()
        for b in buildings:
            t = self._loads(self.db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (b["id"],))["tiles_json"], {})
            cells |= {(int(x), int(y)) for (x, y) in (t.get("space") or [])}
        self.building_cells = cells
        obs = set(cells)
        frontier = set(cells)
        for _ in range(clear_zone):
            nf: Set[Cell] = set()
            for (x, y) in frontier:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < self.map_w and 0 <= ny < self.map_h and (nx, ny) not in obs:
                            nf.add((nx, ny))
            obs |= nf
            frontier = nf
        self.obstacles = obs
        return obs

    # ==================================================================
    # 寻路：直角（4 向）/ 弯曲（8 向）+ 确定性兜底 + BFS
    # ==================================================================
    def _a_star(self, start: Cell, end: Cell, style: str, rng: Optional[random.Random] = None,
                cap: int = 1500000, perturb: float = 0.0) -> Optional[List[Cell]]:
        """4 向（直角）或 8 向（弯曲）A*；perturb>0 时路径带随机扰动（弯曲主路径）。"""
        rng = rng or self.rng
        if not self.in_map(start) or not self.in_map(end):
            return None
        dirs = DIRS4 if style == "直角" else DIRS8
        heap = []
        heapq.heappush(heap, (0.0, 0, start))
        came = {start: None}
        g = {start: 0.0}
        n = 0
        while heap:
            f, _, cur = heapq.heappop(heap)
            if cur == end:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = came[cur]
                return path[::-1]
            n += 1
            if n > cap:
                return None
            for dd in dirs:
                diag = dd[0] != 0 and dd[1] != 0
                nxt = (cur[0] + dd[0], cur[1] + dd[1])
                if nxt == start:
                    continue
                if (nxt in self.obstacles and nxt != end) or not self.in_map(nxt):
                    continue
                ng = g[cur] + (DIAG_COST if diag else 1.0)
                if ng < g.get(nxt, 1 << 30):
                    g[nxt] = ng
                    came[nxt] = cur
                    dx = abs(nxt[0] - end[0])
                    dy = abs(nxt[1] - end[1])
                    h = max(dx, dy) + (DIAG_COST - 1.0) * min(dx, dy) if style != "直角" \
                        else dx + dy
                    heapq.heappush(heap, (ng + h + rng.random() * perturb, ng, nxt))
        return None

    def a_star(self, start: Cell, end: Cell, style: str, rng: Optional[random.Random] = None,
               perturb: float = 0.15) -> Optional[List[Cell]]:
        """主 A*：弯曲带小扰动（更自然），直角无扰动。"""
        return self._a_star(start, end, style, rng=rng,
                            perturb=(perturb if style != "直角" else 0.0))

    def a_star_fallback(self, start: Cell, end: Cell, style: str,
                        rng: Optional[random.Random] = None) -> Tuple[Optional[List[Cell]], str]:
        """兜底层级：主 A*（弯曲）→ 更不随机的确定性 A* → 仍失败由调用方走 BFS。"""
        path = self.a_star(start, end, style, rng=rng)
        if path:
            return path, "A*"
        path = self._a_star(start, end, style, rng=rng, cap=1500000, perturb=0.0)
        if path:
            return path, "确定性A*"
        return None, None

    def bfs_connect(self, start: Cell, targets: List[Cell]) -> Tuple[Optional[List[Cell]], Optional[Cell]]:
        """兜底计算：BFS（有界、必然找到）。返回 (path, target) 或 (None, None)。"""
        from collections import deque
        if not self.in_map(start):
            return None, None
        target_set = set(targets)
        if start in target_set:
            return [start], start
        q = deque([start])
        came = {start: None}
        while q:
            cur = q.popleft()
            for dd in DIRS8:
                nxt = (cur[0] + dd[0], cur[1] + dd[1])
                if not self.in_map(nxt) or nxt in self.obstacles or nxt in came:
                    continue
                came[nxt] = cur
                if nxt in target_set:
                    path = []
                    while nxt is not None:
                        path.append(nxt)
                        nxt = came[nxt]
                    path = path[::-1]
                    return path, path[-1]
                q.append(nxt)
        return None, None

    def path_between(self, ex1: Cell, ex2: Cell, style: str,
                     rng: Optional[random.Random] = None) -> Optional[List[Cell]]:
        """两门外一点的路径：A* 兜底层级 → BFS。"""
        path, _ = self.a_star_fallback(ex1, ex2, style, rng=rng)
        if not path:
            path, _ = self.bfs_connect(ex1, [ex2])
        return path

    # ==================================================================
    # 视觉直走：出门直走朝中心 + 视野扩大 + 视野内接入
    # ==================================================================
    def _road_in_vision(self, pos: Cell, radius: int, road_cell_map: Dict[Cell, int]) -> Optional[Tuple[Cell, int]]:
        best = None
        for (x, y), rid in road_cell_map.items():
            if (x, y) in self.obstacles:
                continue
            dist = abs(x - pos[0]) + abs(y - pos[1])
            if dist <= radius and (best is None or dist < best[2]):
                best = ((x, y), rid, dist)
        return None if best is None else (best[0], best[1])

    def center_walk(self, start: Cell, door_dir: Cell, center: Cell, road_cell_map: Dict[Cell, int],
                    vision_cap: int = 900) -> Optional[Tuple[List[Cell], Cell, int]]:
        """出门直走：前 2 格沿门口朝外；之后尽量朝中心直走（8 向贪心、不走回头路、
        绕开建筑周围 CLEAR_ZONE 格）；视野 = 已走步数，视野内看到路 → 返回
        (路径, 目标路格, 路id)；走不到 → None（调用方走兜底）。"""
        road_set = set(road_cell_map.keys())
        cur = start
        path = [start]
        visited = {start}
        d = door_dir
        for step in (1, 2):
            nxt = (cur[0] + d[0], cur[1] + d[1])
            if nxt in self.obstacles or not self.in_map(nxt) or nxt in visited:
                break
            cur = nxt
            path.append(cur)
            visited.add(cur)
            if cur in road_set:
                return path, cur, road_cell_map[cur]
            res = self._road_in_vision(cur, step, road_cell_map)
            if res is not None:
                return path, res[0], res[1]
        for step in range(3, vision_cap + 1):
            best_dirs = sorted(DIRS8, key=lambda dd: self.manhattan((cur[0] + dd[0], cur[1] + dd[1]), center))
            nxt = None
            for dd in best_dirs:
                cand = (cur[0] + dd[0], cur[1] + dd[1])
                if cand in visited or cand in self.obstacles or not self.in_map(cand):
                    continue
                nxt = cand
                break
            if nxt is None:
                break
            cur = nxt
            path.append(cur)
            visited.add(cur)
            if cur in road_set:
                return path, cur, road_cell_map[cur]
            res = self._road_in_vision(cur, step, road_cell_map)
            if res is not None:
                return path, res[0], res[1]
            if self.manhattan(cur, center) == 0:
                break
        return None

    # ==================================================================
    # 曲线：直角 = 折线；弯曲 = 圆角（无 Catmull-Rom 过冲）
    # ==================================================================
    @staticmethod
    def simplify_los(path: List[Cell], obstacles: Set[Cell], max_gap: int = 50) -> List[Cell]:
        """贪心视线简化：跳到最远且 ≤max_gap、直线段不穿障碍的点 → 适量锚点。"""
        if len(path) < 3:
            return [tuple(p) for p in path]
        pts = [tuple(path[0])]
        i = 0
        while i < len(path) - 1:
            j = min(len(path) - 1, i + max_gap)
            while j > i + 1:
                if all(c not in obstacles for c in RoadStyleGenerator._rasterize_segment(path[i], path[j])):
                    break
                j -= 1
            pts.append(tuple(path[j]))
            i = j
        return pts

    @staticmethod
    def rounded_curve(anchors: List[Cell], radius: float) -> List[List[Any]]:
        """折线 → 直线段 + 转角圆弧（二次贝塞尔控制点=角点，无过冲）。
        返回贝塞尔段列表（直线段 = 退化 [P,P,Q,Q]）。"""
        if len(anchors) < 2:
            return []
        segs = []
        prev = tuple(anchors[0])
        if len(anchors) == 2:
            return [[prev, prev, tuple(anchors[1]), tuple(anchors[1])]]
        for i in range(1, len(anchors) - 1):
            A, B, C = anchors[i - 1], anchors[i], anchors[i + 1]
            v1 = (B[0] - A[0], B[1] - A[1])
            v2 = (C[0] - B[0], C[1] - B[1])
            l1 = (v1[0] ** 2 + v1[1] ** 2) ** 0.5
            l2 = (v2[0] ** 2 + v2[1] ** 2) ** 0.5
            if l1 < 1e-6 or l2 < 1e-6:
                continue
            u = (v1[0] / l1, v1[1] / l1)
            w = (v2[0] / l2, v2[1] / l2)
            if u[0] * w[0] + u[1] * w[1] > 0.98:      # 接近直线
                segs.append([prev, prev, tuple(B), tuple(B)])
                prev = tuple(B)
                continue
            r = min(radius, l1 / 2, l2 / 2)
            T1 = (B[0] - u[0] * r, B[1] - u[1] * r)
            T2 = (B[0] + w[0] * r, B[1] + w[1] * r)
            segs.append([prev, prev, T1, T1])
            c1 = (T1[0] + (B[0] - T1[0]) * (2.0 / 3.0), T1[1] + (B[1] - T1[1]) * (2.0 / 3.0))
            c2 = (T2[0] + (B[0] - T2[0]) * (2.0 / 3.0), T2[1] + (B[1] - T2[1]) * (2.0 / 3.0))
            segs.append([T1, c1, c2, T2])
            prev = T2
        segs.append([prev, prev, tuple(anchors[-1]), tuple(anchors[-1])])
        return segs

    @staticmethod
    def flatten_bezier(segments: List[List[Any]], tol: float = 0.5) -> List[Tuple[float, float]]:
        """贝塞尔段按平直度自适应细分（de Casteljau），返回沿曲线稠密点。"""
        def _dps(p, a, b):
            ax, ay = a
            bx, by = b
            px, py = p
            dx, dy = bx - ax, by - ay
            if dx == 0 and dy == 0:
                return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5

        def _mid(a, b):
            return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)

        pts = []
        for (P, C1, C2, Q) in segments:
            stack = [(P, C1, C2, Q)]
            while stack:
                p0, p1, p2, p3 = stack.pop()
                dev = max(_dps(p1, p0, p3), _dps(p2, p0, p3))
                chord = ((p3[0] - p0[0]) ** 2 + (p3[1] - p0[1]) ** 2) ** 0.5
                if dev < tol and chord < 2.0:
                    if pts and (abs(pts[-1][0] - p3[0]) > 1e-9 or abs(pts[-1][1] - p3[1]) > 1e-9):
                        pts.append(p3)
                    elif not pts:
                        pts.append(p0)
                else:
                    p01 = _mid(p0, p1)
                    p12 = _mid(p1, p2)
                    p23 = _mid(p2, p3)
                    p012 = _mid(p01, p12)
                    p123 = _mid(p12, p23)
                    p0123 = _mid(p012, p123)
                    # LIFO：先压后半段，前半段先弹出 → 点序沿曲线方向
                    stack.append((p0123, p123, p23, p3))
                    stack.append((p0, p01, p012, p0123))
        if not pts:
            pts = [tuple(segments[0][0]), tuple(segments[-1][3])]
        return pts

    # ==================================================================
    # 道路保存：门口接通 + 裁剪 + 墙剔建筑格 + 交叉打通 + 大中小路宽
    # ==================================================================
    def save_road(self, area_id: Optional[int], name: str, path: List[Cell],
                  connects: List[Tuple[str, int]], style: str, width: int,
                  size: Optional[str] = None) -> Tuple[Optional[int], Set[Cell]]:
        """保存一条道路。style='直角'|'弯曲'（可换）；width=路宽（可换，大中小不硬编码）；
        size 可选：'大路'|'中路'|'小路'，不传时按 width 与 ROAD_*_W 常量推断（元数据，不影响生成）。"""
        door_cells: Set[Cell] = set()
        door_mids: List[Cell] = []
        for (kind, nid) in connects:
            if kind != "room":
                continue
            for r in self.db.fetch_all(
                    "SELECT tiles_json FROM item WHERE map_id = ? AND room_id = ? AND item_type='door' "
                    "AND properties_json LIKE '%road_entrance%'", (self.map_id, nid)):
                cells = {(int(x), int(y)) for (x, y) in (self._loads(r["tiles_json"], {}).get("wall_tiles") or [])}
                if cells:
                    door_cells |= cells
                    sc = sorted(cells)
                    door_mids.append(sc[len(sc) // 2])

        # 锚点：**曲线/折线起点接到门洞中心格**（矢量视图门→路直连，
        # 门外不再出现"内部格子"夹层）
        if style == "直角":
            base_anchors = [tuple(p) for p in path]
        else:
            base_anchors = self.simplify_los(path, self.obstacles)
        anchors = (([door_mids[0]] if door_mids else []) + list(base_anchors)
                   + ([door_mids[1]] if len(door_mids) >= 2 else []))

        if style == "直角":
            segments = [[a, a, b, b] for a, b in zip(anchors, anchors[1:])] if len(anchors) >= 2 else []
        else:
            radius = 8 if width <= 5 else 12
            segments = self.rounded_curve(anchors, radius)
        if not segments:
            return None, set()
        dense = self.flatten_bezier(segments)
        band = self.band_for_points(dense, width)
        door_mouth: Set[Cell] = set()
        if door_cells:
            band |= door_cells
            # 门洞外口：门洞每个格朝外的邻格并入带（门**全宽**接上路，
            # 不会出现门边格正上方是路带边界墙的阻挡）
            for (x, y) in door_cells:
                for dx, dy in DIRS4:
                    nxt = (x + dx, y + dy)
                    if nxt not in self.building_cells:
                        band.add(nxt)
                        door_mouth.add(nxt)
            band -= (self.building_cells - door_cells)
        else:
            band -= self.building_cells

        wall: Set[Cell] = set((x, y) for (x, y) in band
                              if any((x + dx, y + dy) not in band for dx, dy in
                                     ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))))
        wall -= self.building_cells
        wall -= door_mouth          # 门口外口不画路墙：门 → 路面直通

        # 交叉打通
        for er_id, er_tiles in self.existing_roads:
            er_space = {(int(x), int(y)) for (x, y) in (er_tiles.get("space") or [])}
            wall -= er_space
        for er_id, er_tiles in self.existing_roads:
            er_wall = {(int(x), int(y)) for (x, y) in (er_tiles.get("wall") or [])}
            er_space = {(int(x), int(y)) for (x, y) in (er_tiles.get("space") or [])}
            hit = er_wall & band
            if hit:
                er_wall -= hit
                er_space |= hit
                er_tiles["wall"] = sorted(er_wall)
                er_tiles["space"] = sorted(er_space)
                self.db.execute("UPDATE room SET tiles_json = ?, area = ? WHERE id = ?",
                                (json.dumps(er_tiles, ensure_ascii=False), len(er_space), er_id))

        mid_c = dense[len(dense) // 2]
        if size is None:
            size = ("大路" if width >= self.ROAD_LARGE_W
                    else "中路" if width >= self.ROAD_MED_W else "小路")
        geom = {"type": "road",
                "path": [[float(x), float(y)] for (x, y) in anchors],
                "curve": {"type": "bezier", "segments": segments},
                "center": [mid_c[0], mid_c[1]], "width": float(width),
                "style": style, "size": size}
        other = {"generator": "road_style", "width": width, "style": style,
                 "connects": [{"kind": c[0], "id": c[1]} for c in connects]}
        cur = self.db.execute(
            "INSERT INTO room (map_id, building_area_id, name, layer_start, layer_end, room_type, "
            "geom_json, tiles_json, area, other_json) VALUES (?, ?, ?, 1, 1, 'road', ?, ?, ?, ?)",
            (self.map_id, area_id, name,
             json.dumps(geom, ensure_ascii=False),
             json.dumps({"wall": [[x, y] for (x, y) in sorted(wall)],
                         "space": [[x, y] for (x, y) in sorted(band)],
                         "inner_wall": []}, ensure_ascii=False),
             len(band), json.dumps(other, ensure_ascii=False)),
        )
        rid = int(cur.lastrowid)
        self.existing_roads.append((rid, self._loads(
            self.db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (rid,))["tiles_json"], {})))
        return rid, band

    def new_road_name(self) -> str:
        self.road_counter[0] += 1
        return f"Road_{self.road_counter[0]}"

    # ==================================================================
    # 区内连接策略（直角/弯曲 × 稠密/稀疏）
    # ==================================================================
    def connect_area(self, area: JsonDict, doors: List[Optional[JsonDict]],
                     style: str, density: str, width: int,
                     rng: Optional[random.Random] = None,
                     dense_k: int = 3) -> Tuple[int, Dict[Cell, int]]:
        """区内路网。style='直角'|'弯曲'，density='稠密'|'稀疏'，width=路宽。
        返回 (道路数, 区路格 map)。"""
        rng = rng or self.rng
        buildings = area["buildings"]
        dmap = {b["id"]: d for b, d in zip(buildings, doors) if d is not None}
        area_map: Dict[Cell, int] = {}
        n = 0

        def _save(path, connects):
            nonlocal n
            rid, band = self.save_road(area["area_id"], self.new_road_name(), path, connects, style, width)
            for c in band:
                area_map[c] = rid
            n += 1

        if style == "弯曲":
            center = (sum(b["center"][0] for b in buildings) / len(buildings),
                      sum(b["center"][1] for b in buildings) / len(buildings))
            two = rng.sample(list(dmap.values()), 2)
            path = self.path_between(self.door_exterior(two[0], width),
                                     self.door_exterior(two[1], width), style, rng=rng)
            if path:
                _save(path, [("room", two[0]["building_id"]), ("room", two[1]["building_id"])])
            rest = [d for d in dmap.values() if d not in (two[0], two[1])]
            rng.shuffle(rest)
            for d in rest:
                full = connects = None
                res = self.center_walk(self.door_exterior(d, width), d["dir"], center, area_map)
                if res is not None:
                    walk_path, target_cell, target_rid = res
                    if target_cell != walk_path[-1]:
                        astar, akind = self.a_star_fallback(walk_path[-1], target_cell, style, rng=rng)
                        if astar:
                            full = walk_path + astar[1:]
                            connects = [("room", d["building_id"]), ("road", target_rid)]
                        else:
                            targets = [c for c, rid in area_map.items() if c not in self.obstacles]
                            full, target_cell = self.bfs_connect(self.door_exterior(d, width), targets)
                            if full:
                                connects = [("room", d["building_id"]), ("road", area_map[target_cell])]
                    else:
                        full = walk_path
                        connects = [("room", d["building_id"]), ("road", target_rid)]
                else:
                    targets = [c for c, rid in area_map.items() if c not in self.obstacles]
                    full, target_cell = self.bfs_connect(self.door_exterior(d, width), targets)
                    if full:
                        connects = [("room", d["building_id"]), ("road", area_map[target_cell])]
                if full:
                    _save(full, connects)
            # 弯曲稠密：每建筑再补连最近 1 个未直接相连同区建筑
            if density == "稠密":
                self._extra_dense_links(buildings, dmap, style, width, rng, area_map, _save)
        else:
            rng.shuffle(buildings)
            if density == "稀疏":
                connected = [buildings[0]]
                for b in buildings[1:]:
                    if b["id"] not in dmap:
                        continue
                    target = min(connected, key=lambda t: self.manhattan(b["center"], t["center"]))
                    path = self.path_between(self.door_exterior(dmap[b["id"]], width),
                                             self.door_exterior(dmap[target["id"]], width), style, rng=rng)
                    if not path:
                        continue
                    _save(path, [("room", b["id"]), ("room", target["id"])])
                    connected.append(b)
                # 稀疏也要连通保证：树中个别连接失败时补连，保证每栋建筑都接入
                self._ensure_area_connected(buildings, dmap, style, width, rng, _save)
            else:
                pairs = set()
                for b in buildings:
                    if b["id"] not in dmap:
                        continue
                    others = sorted([t for t in buildings if t["id"] != b["id"]],
                                    key=lambda t: self.manhattan(b["center"], t["center"]))[:dense_k]
                    for t in others:
                        if t["id"] not in dmap:
                            continue
                        key = tuple(sorted((b["id"], t["id"])))
                        if key in pairs:
                            continue
                        pairs.add(key)
                        path = self.path_between(self.door_exterior(dmap[b["id"]], width),
                                                 self.door_exterior(dmap[t["id"]], width), style, rng=rng)
                        if path:
                            _save(path, [("room", b["id"]), ("room", t["id"])])
                self._ensure_area_connected(buildings, dmap, style, width, rng, _save)
                self._extra_dense_links(buildings, dmap, style, width, rng, area_map, _save)
        return n, area_map

    def _ensure_area_connected(self, buildings, dmap, style, width, rng, _save):
        """区内多分量时用最近跨分量建筑对连接（只考虑有门的建筑）。
        最近对连不通时**跳过它、依次尝试次近的跨分量对**，直到该分量接入或无可尝试。"""
        connectable = [b for b in buildings if b["id"] in dmap]
        tried: Set[Tuple[int, int]] = set()
        for _ in range(len(connectable) * len(connectable)):
            uf = UnionFind()
            for b in connectable:
                uf.make_set(b["id"])
            for rd in self.db.fetch_all("SELECT other_json FROM room WHERE map_id = ? AND room_type='road'", (self.map_id,)):
                o = self._loads(rd["other_json"], {})
                ids = [int(c["id"]) for c in o.get("connects", []) if c.get("kind") == "room"]
                if len(ids) == 2 and ids[0] in dmap and ids[1] in dmap:
                    uf.union(ids[0], ids[1])
            comps = {}
            for b in connectable:
                comps.setdefault(uf.find(b["id"]), []).append(b)
            if len(comps) <= 1:
                break
            cl = list(comps.values())
            # 全部跨分量对按距离排序，跳过已失败的
            cands = []
            for i, x in enumerate(cl):
                for j in range(i + 1, len(cl)):
                    y = cl[j]
                    for a in x:
                        for b in y:
                            key = (a["id"], b["id"]) if a["id"] < b["id"] else (b["id"], a["id"])
                            if key in tried:
                                continue
                            d = self.manhattan(a["center"], b["center"])
                            cands.append((d, key, a, b))
            cands.sort(key=lambda t: t[0])
            best = None
            for _d, key, a, b in cands:
                path = self.path_between(self.door_exterior(dmap[a["id"]], width),
                                         self.door_exterior(dmap[b["id"]], width), style, rng=rng)
                if path:
                    best = (key, a, b, path)
                    break
                tried.add(key)
            if best is None:
                break
            _key, a, b, path = best
            _save(path, [("room", a["id"]), ("room", b["id"])])

    def _extra_dense_links(self, buildings, dmap, style, width, rng, area_map, _save):
        """稠密补连：每个建筑再连最近 1 个未直接相连的同区建筑。"""
        pairs = set()
        for rd in self.db.fetch_all("SELECT other_json FROM room WHERE map_id = ? AND room_type='road'", (self.map_id,)):
            o = self._loads(rd["other_json"], {})
            ids = [int(c["id"]) for c in o.get("connects", []) if c.get("kind") == "room"]
            if len(ids) == 2 and ids[0] in dmap and ids[1] in dmap:
                pairs.add(tuple(sorted(ids)))
        for b in buildings:
            if b["id"] not in dmap:
                continue
            for t in sorted([x for x in buildings if x["id"] != b["id"]],
                            key=lambda x: self.manhattan(b["center"], x["center"])):
                key = tuple(sorted((b["id"], t["id"])))
                if key in pairs or t["id"] not in dmap:
                    continue
                path = self.path_between(self.door_exterior(dmap[b["id"]], width),
                                         self.door_exterior(dmap[t["id"]], width), style, rng=rng)
                if path:
                    _save(path, [("room", b["id"]), ("room", t["id"])])
                    pairs.add(key)
                    break

    # ==================================================================
    # 区际连接（中路 / 孤立）
    # ==================================================================
    def connect_inter_area(self, areas_info: List[JsonDict], connected: List[int], isolated: int,
                           width: int, rng: Optional[random.Random] = None,
                           style: str = "弯曲") -> Tuple[int, int]:
        """区际：connected 里的区用 style 指定风格路网（默认弯曲）连接，isolated 区孤立。"""
        rng = rng or self.rng
        conn = [a for a in areas_info if a["index"] in connected]
        gates = []
        for a in conn:
            b = min(a["buildings"], key=lambda bb: self.manhattan(bb["center"], a["center"]))
            gates.append((a, b, a["door_by_bid"][b["id"]]))
        gcenter = (sum(g[2]["door_mid"][0] for g in gates) / len(gates),
                   sum(g[2]["door_mid"][1] for g in gates) / len(gates))
        inter_map: Dict[Cell, int] = {}
        n = 0

        def _save(path, connects):
            nonlocal n
            rid, band = self.save_road(None, self.new_road_name(), path, connects, style, width)
            for c in band:
                inter_map[c] = rid
            n += 1

        first = min(((g1, g2) for i, g1 in enumerate(gates) for g2 in gates[i + 1:]),
                    key=lambda gg: self.manhattan(gg[0][2]["door_mid"], gg[1][2]["door_mid"]))
        (a1, b1, d1), (a2, b2, d2) = first
        path = self.path_between(self.door_exterior(d1, width), self.door_exterior(d2, width), style, rng=rng)
        if path:
            _save(path, [("room", b1["id"]), ("room", b2["id"])])
        for a, b, d in gates:
            if (a, b, d) in first:
                continue
            full = connects = None
            res = self.center_walk(self.door_exterior(d, width), d["dir"], gcenter, inter_map)
            if res is not None:
                walk_path, target_cell, target_rid = res
                if target_cell != walk_path[-1]:
                    astar, akind = self.a_star_fallback(walk_path[-1], target_cell, style, rng=rng)
                    if astar:
                        full = walk_path + astar[1:]
                        connects = [("room", b["id"]), ("road", target_rid)]
                    else:
                        targets = [c for c, rid in inter_map.items() if c not in self.obstacles]
                        full, target_cell = self.bfs_connect(self.door_exterior(d, width), targets)
                        if full:
                            connects = [("room", b["id"]), ("road", inter_map[target_cell])]
                else:
                    full = walk_path
                    connects = [("room", b["id"]), ("road", target_rid)]
            else:
                targets = [c for c, rid in inter_map.items() if c not in self.obstacles]
                full, target_cell = self.bfs_connect(self.door_exterior(d, width), targets)
                if full:
                    connects = [("room", b["id"]), ("road", inter_map[target_cell])]
            if full:
                _save(full, connects)
        return n, len(conn)

    # ==================================================================
    # 统计 / 渲染
    # ==================================================================
    def finalize_road_walls(self):
        """道路墙格数据修正：按每条路**当前 space** 重算边界墙；**交叉口保持打通**
        （邻其它路 space 的格不画墙），剔除建筑格与门洞外口格。"""
        roads = self.db.fetch_all(
            "SELECT id, tiles_json, other_json FROM room WHERE map_id = ? AND room_type='road'",
            (self.map_id,))
        all_spaces: Set[Cell] = set()
        for rd in roads:
            t = self._loads(rd["tiles_json"], {})
            all_spaces |= {(int(x), int(y)) for (x, y) in (t.get("space") or [])}
        for rd in roads:
            rid = int(rd["id"])
            t = self._loads(rd["tiles_json"], {})
            space = {(int(x), int(y)) for (x, y) in (t.get("space") or [])}
            if not space:
                continue
            other_spaces = all_spaces - space
            # 该路连接房间的门洞 + 门洞外口（合法无墙格）
            o = self._loads(rd["other_json"], {})
            door_cells: Set[Cell] = set()
            for c in o.get("connects", []):
                if c.get("kind") != "room":
                    continue
                for r2 in self.db.fetch_all(
                        "SELECT tiles_json FROM item WHERE map_id = ? AND room_id = ? "
                        "AND item_type='door' AND properties_json LIKE '%road_entrance%'",
                        (self.map_id, int(c["id"]))):
                    door_cells |= {(int(x), int(y)) for (x, y)
                                   in (self._loads(r2["tiles_json"], {}).get("wall_tiles") or [])}
            door_mouth: Set[Cell] = set()
            for (x, y) in door_cells:
                for dx, dy in DIRS4:
                    nxt = (x + dx, y + dy)
                    if nxt not in self.building_cells:
                        door_mouth.add(nxt)
            wall: Set[Cell] = set()
            for (x, y) in space:
                if (x, y) in door_mouth:
                    continue
                # 有"开放"邻格（非本路 space、非其它路 space、非建筑）→ 外墙格
                if any((x + dx, y + dy) not in space
                       and (x + dx, y + dy) not in other_spaces
                       and (x + dx, y + dy) not in self.building_cells
                       for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                                      (1, 1), (1, -1), (-1, 1), (-1, -1))):
                    wall.add((x, y))
            t["wall"] = sorted(wall)
            self.db.execute("UPDATE room SET tiles_json = ? WHERE id = ?",
                            (json.dumps(t, ensure_ascii=False), rid))

    def compute_components(self, buildings: List[JsonDict]) -> Dict[int, List[int]]:
        """全图连通分量（按 road connects）。返回 root -> [building ids]。"""
        uf = UnionFind()
        for b in buildings:
            uf.make_set(b["id"])
        for rd in self.db.fetch_all("SELECT id, other_json FROM room WHERE map_id = ? AND room_type='road'", (self.map_id,)):
            o = self._loads(rd["other_json"], {})
            uf.make_set(int(rd["id"]))
            for c in o.get("connects", []):
                if isinstance(c, dict) and "id" in c:
                    uf.union(int(c["id"]), int(rd["id"]))
        comps = {}
        for b in buildings:
            comps.setdefault(uf.find(b["id"]), []).append(b["id"])
        return comps

    def render_pdf(self, filename: str, output_dir: str, fig_size=(16, 16),
                   show_area_names=True) -> str:
        from ..visualization.map_visualizer import MapVisualizer
        vis = MapVisualizer(self.db)
        p = vis.save_multi_view_pdf(self.map_id, layers=[1], output_dir=output_dir,
                                    filename=filename, fig_size=fig_size, show_grid=True,
                                    show_area_names=show_area_names, show_room_names=False)
        vis.close()
        return p
