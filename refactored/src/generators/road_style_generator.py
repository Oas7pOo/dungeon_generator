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
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from shapely.geometry import box, Point, Polygon

from ..db.database import DatabaseManager
from .building_area_generator import BuildingAreaGenerator
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

    def band_for_width_profile(self, dense_pts: List[Tuple[float, float]], widths: List[int]) -> Set[Cell]:
        """按曲线采样点的可变宽度生成道路带，用于真菌的“细末端、粗主干”。"""
        if not dense_pts:
            return set()
        if len(widths) != len(dense_pts):
            raise ValueError("道路宽度轮廓必须与曲线采样点一一对应")
        band: Set[Cell] = set()
        # flatten_bezier 的相邻点间距 < 2；逐点方形带求并即可连续且不会留下孔洞。
        for (px, py), width in zip(dense_pts, widths):
            x, y = int(round(px)), int(round(py))
            half = max(1, int(width)) // 2
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
                        rng: Optional[random.Random] = None,
                        min_size: Tuple[int, int] = (5, 5),
                        max_size: Tuple[int, int] = (60, 80),
                        size_dist: str = "exponential") -> List[JsonDict]:
        """区内放 n 个建筑（kinds: 'rect'/'circle'/'rotated'，默认全矩形）。

        尺寸复用 ``BuildingAreaGenerator.generate_room_size`` 的指数分布：多数小
        建筑、少数大建筑，默认范围 5x5--60x80；而非旧版 25--60 的均匀抽样。
        """
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
                    w, h = BuildingAreaGenerator.generate_room_size(
                        None, min_size, max_size, dist=size_dist, regular_rect=True)
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
                    cw, ch = BuildingAreaGenerator.generate_room_size(
                        None, min_size, max_size, dist=size_dist, regular_rect=True)
                    r = max(3, min(cw, ch) // 2)
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
                    w, h = BuildingAreaGenerator.generate_room_size(
                        None, min_size, max_size, dist=size_dist, regular_rect=True)
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
                  width: Optional[int] = None,
                  preferred_direction: Optional[Tuple[float, float]] = None) -> Optional[JsonDict]:
        """任意形状外墙开门：取朝向随机方向、**门外一点无建筑**、**同一墙段（不包角）**
        的连续墙格（≥2）。门位在直墙段中点附近作截断高斯采样；角格只作无可用
        直墙时的最终保底。8 邻外向检测；blocked = 障碍格（建筑格+外扩圈，传
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
            # 不能用 (x, y) 的字典序当作墙段顺序：斜墙会被错排，随后所谓的
            # “中点”可能落到拐角。按墙的切线投影排序，才是几何上的墙段中点。
            tx, ty = -out_dir[1], out_dir[0]
            return sorted(set(run), key=lambda p: (p[0] + 0.5 - cx) * tx + (p[1] + 0.5 - cy) * ty)

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

        if preferred_direction is not None:
            # 真菌入口优先使用有唯一正交法向的真正直墙格。角点通常有两个正交
            # 自由邻格、法向会被判成对角线；若不排除它们，方向匹配会压过“靠近
            # 直边中心”的要求，导致门黏在角上。圆/斜矩形没有可用直墙时才回退。
            straight_cands = []
            for candidate in cands:
                x, y = candidate[0]
                cardinal_free = sum((x + dx, y + dy) not in space for dx, dy in DIRS4)
                if cardinal_free == 1:
                    straight_cands.append(candidate)
            straight_cells = {candidate[0] for candidate in straight_cands}
            # 真菌路网等需要门面向营养网络：先选与目标方向一致的外墙，门仍取该
            # 墙段的中点。对矩形/斜矩形这正是最靠近直边中心的位置；圆形没有直边，
            # 则自然退化为最朝向网络的一段圆弧。
            px, py = preferred_direction
            plen = math.hypot(px, py) or 1.0
            def _fungus_door_key(candidate):
                cell, out_dir = candidate
                alignment = (out_dir[0] * px + out_dir[1] * py) / plen
                # 同一朝向的一整面墙中，先尝试最接近切线中心的格，而非 set 的
                # 任意迭代顺序；这会让门稳定落在直边中部、远离角点。
                tx, ty = -out_dir[1], out_dir[0]
                off_center = abs((cell[0] + 0.5 - cx) * tx + (cell[1] + 0.5 - cy) * ty)
                straight_penalty = 0 if cell in straight_cells else 1
                return (straight_penalty, -alignment, off_center)
            cands.sort(key=_fungus_door_key)
        else:
            cands.sort(key=lambda c: abs((_adir(c[1]) - ang + math.pi) % (2 * math.pi) - math.pi))

        for (mid_cell, out_dir) in cands:
            run = _wall_run(mid_cell, out_dir)
            if len(run) < self.DOOR_W:
                continue
            # 正常情况下门不能跨过拐角。只有没有足够长的直墙段可用时，才允许
            # 退回原始墙段（圆形或极窄斜墙的最后保底）。
            strict_run = [p for p in run if sum((p[0] + dx, p[1] + dy) not in space for dx, dy in DIRS4) == 1]
            usable_run = strict_run if len(strict_run) >= self.DOOR_W else run
            if len(usable_run) < self.DOOR_W:
                continue
            # 直边中点是均值；高斯扰动只允许门在中心附近自然变动，并保留两端
            # 各 3 格墙，因此不会把门推到拐角。
            # 大墙段在两端保留 3 格；5x5 等极小建筑的无角直边只有 3 格，
            # 允许使用整个直边中段，仍不触碰拐角。
            margin = 3 if len(usable_run) >= self.DOOR_W + 6 else 0
            ideal = (len(usable_run) - 1) / 2.0 + rng.gauss(0.0, max(0.75, len(usable_run) * 0.12))
            start_i = int(round(ideal)) - self.DOOR_W // 2
            start_i = max(margin, min(len(usable_run) - self.DOOR_W - margin, start_i))
            door_cells = usable_run[start_i: start_i + self.DOOR_W]
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
                if style == "生长树":
                    # 真菌并不把建筑周围的空地切成不可走区域；而是把离建筑越近
                    # 的格变得越“昂贵”。这是一种连续势场：路会优先留白，却能在
                    # 狭窄处自然贴近建筑，不会被硬障碍环截成难看的直角缺口。
                    distance = getattr(self, "fungus_clearance_distance", {}).get(nxt)
                    preferred = int(getattr(self, "fungus_preferred_clearance", 0))
                    if distance is not None and distance < preferred:
                        ng += float(getattr(self, "fungus_clearance_penalty", 0.22)) * (preferred - distance) ** 2
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
                  size: Optional[str] = None,
                  extra_other: Optional[JsonDict] = None) -> Tuple[Optional[int], Set[Cell]]:
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
        if style == "生长树":
            # 真菌末端贴门时保持最小路宽，向路径中部（通常是汇合/高流量区）
            # 平滑变粗。sin 曲线保证两端严格回到最小宽度、无突兀台阶。
            min_end_width = min(5, int(width))
            profile = [
                int(round(min_end_width + (int(width) - min_end_width) * math.sin(math.pi * i / max(1, len(dense) - 1))))
                for i in range(len(dense))
            ]
            band = self.band_for_width_profile(dense, profile)
        else:
            profile = None
            band = self.band_for_points(dense, width)
        # 真菌道路不再对“建筑外扩圈”裁切带宽：外扩圈已在 A* 中作为连续
        # 势场处理。这里只在下方剔除真正的建筑格，保证道路仍然完整、圆滑。
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
        if profile is not None:
            other["width_profile"] = {"endpoint_width": min_end_width, "peak_width": int(width),
                                      "shape": "sin_taper"}
        if extra_other:
            other.update(extra_other)
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
    # 真菌路网：加权营养树 + 缩裹（weighted transport tree）
    # ==================================================================
    def make_fungus_doors(self, buildings: List[JsonDict], *, max_width: int = 10,
                          clear_zone: Optional[int] = None) -> List[Optional[JsonDict]]:
        """为真菌路网开门并建立仅在门口允许穿过的短门廊。

        门优先朝向按建筑面积加权的聚集中心；矩形与旋转矩形的候选门始终位于
        一条墙段的中点。道路只把建筑本体设为硬障碍，并用 clear_zone 对应的
        连续势场偏好留出空地，因此不会出现把道路带硬切掉的缺口。
        """
        if not buildings:
            return []
        clear_zone = self.CLEAR_ZONE if clear_zone is None else int(clear_zone)
        self.fungus_clear_zone = clear_zone
        total = sum(float(b.get("fungus_weight", 1.0)) for b in buildings) or 1.0
        cx = sum(float(b["center"][0]) * float(b.get("fungus_weight", 1.0)) for b in buildings) / total
        cy = sum(float(b["center"][1]) * float(b.get("fungus_weight", 1.0)) for b in buildings) / total
        doors: List[Optional[JsonDict]] = []
        # 门口终端置于墙外足够远的位置，使宽路带能从门平顺伸出。
        desired_stem_len = clear_zone + max_width // 2 + 2
        for b in buildings:
            direction = (cx - float(b["center"][0]), cy - float(b["center"][1]))
            d = self.make_door(b, blocked=self.obstacles, width=max_width,
                               preferred_direction=direction)
            if d is None:
                doors.append(None)
                continue
            stem_len = desired_stem_len
            ex = (d["door_mid"][0] + d["dir"][0] * stem_len,
                  d["door_mid"][1] + d["dir"][1] * stem_len)
            # 门太靠地图边缘时退到合法的最远格；A* 会以该格作为终端。
            while not self.in_map(ex) and stem_len > max_width // 2 + 1:
                stem_len -= 1
                ex = (d["door_mid"][0] + d["dir"][0] * stem_len,
                      d["door_mid"][1] + d["dir"][1] * stem_len)
            d["fungus_exterior"] = ex
            doors.append(d)

        # 多源 8 邻距离场：只记录离建筑较近的格。A* 对这些格增加平滑代价，
        # 但 self.obstacles 只保留建筑本体，故不会发生道路带被裁掉一半的现象。
        preferred = max(int(clear_zone), max_width // 2 + 1)
        distance: Dict[Cell, int] = {cell: 0 for cell in self.building_cells}
        frontier = set(self.building_cells)
        for step in range(1, preferred + 1):
            nxt_frontier: Set[Cell] = set()
            for x, y in frontier:
                for dx, dy in DIRS8:
                    nxt = (x + dx, y + dy)
                    if self.in_map(nxt) and nxt not in distance:
                        distance[nxt] = step
                        nxt_frontier.add(nxt)
            frontier = nxt_frontier
            if not frontier:
                break
        self.fungus_clearance_distance = distance
        self.fungus_preferred_clearance = preferred
        self.fungus_clearance_penalty = 0.22
        self.obstacles = set(self.building_cells)
        return doors

    def _fungus_weight(self, building: JsonDict, supplied: Optional[Dict[Any, float]]) -> float:
        """权重优先用调用方配置；否则以占地面积的平方根表示可运输营养。"""
        bid = int(building["id"])
        if supplied:
            raw = supplied.get(bid, supplied.get(str(bid), supplied.get(building.get("name"))))
            if raw is not None:
                return max(0.01, float(raw))
        t = self._loads(self.db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (bid,))["tiles_json"], {})
        return max(1.0, float(len(t.get("space") or [])) ** 0.5)

    def connect_fungus(self, area: JsonDict, doors: List[Optional[JsonDict]], *,
                       min_width: int = 5, max_width: int = 10,
                       weights: Optional[Dict[Any, float]] = None,
                       weight_bias: float = 0.7,
                       maintenance_cost: float = 20.0,
                       loop_gain_threshold: float = 1.25,
                       max_cycles: Optional[int] = None) -> Tuple[int, Dict[Cell, int]]:
        """生成近似 Physarum 的加权运输网，而非只有一棵树。

        第一步以加权最小树给出会收缩的营养骨架；第二步检验每条非树候选边：
        若它减少的加权运输距离大于其维护成本（``maintenance_cost * 路长``）
        的 ``loop_gain_threshold`` 倍，就保留为冗余环路。故路网会在高需求处
        长成网状而非树桩，同时不会无限填满空地。
        """
        min_width, max_width = int(min_width), int(max_width)
        if min_width < 1 or max_width < min_width:
            raise ValueError("真菌路宽需满足 1 <= min_width <= max_width")
        buildings = [b for b, d in zip(area["buildings"], doors) if d is not None]
        dmap = {int(d["building_id"]): d for d in doors if d is not None}
        if len(buildings) < 2:
            return 0, {}
        bmap = {int(b["id"]): b for b in buildings}
        bw = {bid: self._fungus_weight(b, weights) for bid, b in bmap.items()}
        for b in buildings:
            b["fungus_weight"] = bw[int(b["id"])]

        # 只在候选边真的需要跨越当前分量时做 A*，避免为 n=10 的完整图预先跑 45 次。
        candidates = []
        ids = sorted(bmap)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                pa, pb = dmap[a]["fungus_exterior"], dmap[b]["fungus_exterior"]
                distance = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
                cost = distance / max(0.01, (bw[a] * bw[b]) ** float(weight_bias))
                candidates.append((cost, distance, a, b))
        candidates.sort()
        uf = UnionFind()
        for bid in ids:
            uf.make_set(bid)
        edges: List[Tuple[int, int, List[Cell]]] = []
        for _cost, _distance, a, b in candidates:
            if uf.find(a) == uf.find(b):
                continue
            path = self.path_between(dmap[a]["fungus_exterior"], dmap[b]["fungus_exterior"], "生长树", rng=self.rng)
            if not path:
                continue
            uf.union(a, b)
            edges.append((a, b, path))
            if len(edges) == len(ids) - 1:
                break
        if len(edges) != len(ids) - 1:
            # 密集随机排布偶尔会令 5 格避让环首尾相接。逐级收缩避让环再尝试
            # 尚未连通的边：这是约束下的最后一次“缩裹”，优先保住全图可达性，
            # 而不会放宽建筑本体这个硬障碍。
            # 仅做一次硬障碍（建筑本体）回退，并限制候选数；否则在高度密集的
            # 随机图上，多层清空环会触发大量等价的全图 A* 搜索。
            for relaxed_clearance in (0,):
                self.build_obstacles(buildings, clear_zone=relaxed_clearance)
                attempts = 0
                for _cost, _distance, a, b in candidates:
                    if uf.find(a) == uf.find(b):
                        continue
                    if attempts >= max(12, len(ids) * 2):
                        break
                    attempts += 1
                    path = self.path_between(dmap[a]["fungus_exterior"], dmap[b]["fungus_exterior"], "生长树", rng=self.rng)
                    if not path:
                        continue
                    uf.union(a, b)
                    edges.append((a, b, path))
                    if len(edges) == len(ids) - 1:
                        break
                if len(edges) == len(ids) - 1:
                    break

        # 割边流量：所有建筑两两之间的潜在往来以 w_left * w_right 经过骨架边。
        adjacency: Dict[int, List[Tuple[int, int]]] = {bid: [] for bid in ids}
        for edge_i, (a, b, _path) in enumerate(edges):
            adjacency[a].append((b, edge_i))
            adjacency[b].append((a, edge_i))
        total_weight = sum(bw.values())
        flows: List[float] = []
        for edge_i, (a, b, _path) in enumerate(edges):
            stack, seen = [a], {b}
            side = 0.0
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                side += bw[cur]
                stack.extend(nxt for nxt, _ei in adjacency[cur] if nxt not in seen)
            flows.append(side * max(0.0, total_weight - side))
        def _path_length(path: List[Cell]) -> float:
            return sum(DIAG_COST if a[0] != b[0] and a[1] != b[1] else 1.0
                       for a, b in zip(path, path[1:]))

        # 树内任意两点的现有运输距离（唯一通路）。它是环路能否节约运输资源的基线。
        tree_lengths = [_path_length(path) for _a, _b, path in edges]

        def _tree_distance(start: int, goal: int) -> float:
            stack = [(start, None, 0.0)]
            while stack:
                cur, parent, dist = stack.pop()
                if cur == goal:
                    return dist
                for nxt, edge_i in adjacency.get(cur, []):
                    if nxt != parent:
                        stack.append((nxt, cur, dist + tree_lengths[edge_i]))
            return float("inf")

        # 在运输收益足以支付维护的前提下添加少量环。上限可防止一张地图退化为完全图；
        # 默认每 5 个建筑最多约 1 个环。环路是高价值冗余，不是把相近路径
        # 重复铺开的理由；后续还会淘汰贴着已有主干走的“伪环”。
        tree_pairs = {tuple(sorted((a, b))) for a, b, _path in edges}
        candidate_loops = []
        for _cost, direct_estimate, a, b in candidates:
            if (a, b) in tree_pairs:
                continue
            tree_distance = _tree_distance(a, b)
            saved_distance = tree_distance - direct_estimate
            if not math.isfinite(tree_distance) or saved_distance <= 1.0:
                continue
            demand = bw[a] * bw[b]
            estimated_benefit = demand * saved_distance
            estimated_maintenance = max(1.0, float(maintenance_cost) * direct_estimate)
            if estimated_benefit / estimated_maintenance < float(loop_gain_threshold):
                continue
            candidate_loops.append((-estimated_benefit / estimated_maintenance, a, b, tree_distance,
                                    estimated_benefit, estimated_maintenance))
        candidate_loops.sort()
        max_cycles = max(1, int(max_cycles)) if max_cycles is not None else max(2, len(ids) // 5)
        backbone_cells = {cell for _a, _b, path in edges for cell in path}
        loops: List[Tuple[int, int, List[Cell], float, float]] = []
        for _neg_ratio, a, b, tree_distance, _est_benefit, _est_maintenance in candidate_loops:
            if len(loops) >= max_cycles:
                break
            path = self.path_between(dmap[a]["fungus_exterior"], dmap[b]["fungus_exterior"], "生长树", rng=self.rng)
            if not path:
                continue
            direct_length = _path_length(path)
            saved_distance = tree_distance - direct_length
            benefit = bw[a] * bw[b] * saved_distance
            maintenance = max(1.0, float(maintenance_cost) * direct_length)
            overlap = len(set(path) & backbone_cells) / max(1, len(set(path)))
            if saved_distance <= 1.0 or benefit / maintenance < float(loop_gain_threshold) or overlap > 0.65:
                continue
            loops.append((a, b, path, bw[a] * bw[b], benefit / maintenance))
            backbone_cells.update(path)

        # 树边承载跨割的总需求，冗余环承载端点间的直接需求；两类容量统一映射为
        # 5--10 的峰值宽度，随后 save_road 再把每条路向门口渐缩为 5。
        network_edges: List[Tuple[int, int, List[Cell], float, str, Optional[float]]] = [
            (a, b, path, flows[i], "backbone", None) for i, (a, b, path) in enumerate(edges)
        ]
        network_edges.extend((a, b, path, flow, "redundant_cycle", ratio)
                             for a, b, path, flow, ratio in loops)
        capacities = [flow for _a, _b, _path, flow, _kind, _ratio in network_edges]
        lo, hi = (min(capacities), max(capacities)) if capacities else (0.0, 0.0)

        def _width_for(flow: float) -> int:
            if hi > lo:
                result = int(round(min_width + (flow - lo) * (max_width - min_width) / (hi - lo)))
            else:
                result = (min_width + max_width) // 2
            return max(min_width, min(max_width, result))

        # 物理网络并不把抽象树的每一条边各自画出来。先铺一条高流量骨架，
        # 其余终端只需接到现有中心线即可。这相当于对同方向、同端点的运输
        # 通道做 Steiner 式收缩：共享段只维护一次，而共享段上的总通量更高。
        area_map: Dict[Cell, int] = {}
        core_owner: Dict[Cell, int] = {}
        core_cells: Set[Cell] = set()
        road_load: Dict[int, float] = {}
        saved = 0

        def _record_core(path: List[Cell], rid: int, band: Set[Cell]) -> None:
            for cell in path:
                core_cells.add(cell)
                core_owner[cell] = rid
            for cell in band:
                area_map[cell] = rid

        def _path_to_core(start: Cell) -> Tuple[Optional[List[Cell]], Optional[Cell], Optional[int]]:
            """只抽样少量最近主干点；A* 决定实际可行、平滑的接入点。"""
            if not core_owner:
                return None, None, None
            points = list(core_owner)
            # 这里不是寻找全局最短路径，而是让支路并入已有菌丝；一两个最近
            # 样本足够。限制样本数和 A* 次数是 800x800、60 终端时可用的关键。
            stride = max(1, len(points) // 96)
            sample = points[::stride]
            sample.sort(key=lambda p: (p[0] - start[0]) ** 2 + (p[1] - start[1]) ** 2)
            for target in sample[:3]:
                path = self.path_between(start, target, "生长树", rng=self.rng)
                if path:
                    return path, target, core_owner[target]
            return None, None, None

        def _near_existing_fraction(path: List[Cell], radius: int = 6) -> float:
            if not path or not core_cells:
                return 0.0
            near = 0
            for x, y in set(path):
                found = False
                for dx in range(-radius, radius + 1):
                    for dy in range(-radius, radius + 1):
                        if (x + dx, y + dy) in core_cells:
                            found = True
                            break
                    if found:
                        break
                near += int(found)
            return near / max(1, len(set(path)))

        def _save(path: List[Cell], connects: List[Tuple[str, int]], flow: float,
                  kind: str, benefit_ratio: Optional[float], a: int, b: int) -> Optional[int]:
            nonlocal saved
            rid, band = self.save_road(
                area.get("area_id"), self.new_road_name(), path, connects,
                "生长树", _width_for(flow),
                extra_other={
                    "algorithm": "weighted_transport_mesh_contracted",
                    "edge_kind": kind,
                    "min_width": min_width, "max_width": max_width,
                    "flow": flow,
                    "maintenance_cost": float(maintenance_cost),
                    "loop_gain_threshold": float(loop_gain_threshold),
                    "benefit_maintenance_ratio": benefit_ratio,
                    "endpoint_weights": {str(a): bw[a], str(b): bw[b]},
                    "shared_transport": kind == "shared_branch",
                },
            )
            if rid is not None:
                saved += 1
                road_load[rid] = flow
                _record_core(path, rid, band)
            return rid

        backbone = [edge for edge in network_edges if edge[4] == "backbone"]
        pending = list(backbone)
        connected: Set[int] = set()
        while pending:
            if not connected:
                index = max(range(len(pending)), key=lambda i: pending[i][3])
                a, b, path, flow, _kind, ratio = pending.pop(index)
                rid = _save(path, [("room", a), ("room", b)], flow, "backbone", ratio, a, b)
                if rid is not None:
                    connected.update((a, b))
                continue
            crossing = [(i, edge) for i, edge in enumerate(pending)
                        if (edge[0] in connected) != (edge[1] in connected)]
            if not crossing:
                # 仅在前一次保存失败时可能出现；保住可达性的最后回退。
                index = max(range(len(pending)), key=lambda i: pending[i][3])
                a, b, path, flow, _kind, ratio = pending.pop(index)
                rid = _save(path, [("room", a), ("room", b)], flow, "backbone", ratio, a, b)
                if rid is not None:
                    connected.update((a, b))
                continue
            index, (a, b, _tree_path, flow, _kind, ratio) = max(crossing, key=lambda pair: pair[1][3])
            pending.pop(index)
            source = b if a in connected else a
            attach_path, _target, target_rid = _path_to_core(dmap[source]["fungus_exterior"])
            if attach_path and target_rid is not None:
                rid = _save(attach_path, [("room", source), ("road", target_rid)],
                            flow, "shared_branch", ratio, a, b)
                if rid is not None:
                    # 接入主干的需求会累积在被复用的旧道路上；其渲染宽度在
                    # 下方统一重标，因而主干比末端更粗。
                    road_load[target_rid] = road_load.get(target_rid, 0.0) + flow
                    connected.add(source)

        # 真正的环必须给现有网络提供明显不同的路线。若大部分路径落在主干
        # 6 格走廊内，它只是平行复制，收缩模型会直接舍弃它。
        for a, b, path, flow, _kind, ratio in (edge for edge in network_edges if edge[4] == "redundant_cycle"):
            if _near_existing_fraction(path) > 0.28:
                continue
            _save(path, [("room", a), ("room", b)], flow, "redundant_cycle", ratio, a, b)

        # 已共享的主干以累计通量重新标定宽度（5--10），使“合并后更粗”不仅
        # 是元数据，也真正反映在道路带上；不重跑寻路，不会再生成平行走廊。
        if road_load:
            load_lo, load_hi = min(road_load.values()), max(road_load.values())
            for rid, load in road_load.items():
                target_width = (min_width if load_hi <= load_lo else
                                int(round(min_width + (load - load_lo) * (max_width - min_width) /
                                          (load_hi - load_lo))))
                target_width = max(min_width, min(max_width, target_width))
                row = self.db.fetch_one("SELECT geom_json, tiles_json, other_json FROM room WHERE id = ?", (rid,))
                if row is None:
                    continue
                geom, tiles, other = self._loads(row["geom_json"], {}), self._loads(row["tiles_json"], {}), self._loads(row["other_json"], {})
                segments = ((geom.get("curve") or {}).get("segments") or [])
                if not segments:
                    continue
                dense = self.flatten_bezier(segments)
                profile = [int(round(min_width + (target_width - min_width) * math.sin(math.pi * i / max(1, len(dense) - 1))))
                           for i in range(len(dense))]
                band = self.band_for_width_profile(dense, profile)
                # 保住各个房间门洞；其他建筑格始终不可穿越。
                door_cells: Set[Cell] = set()
                for c in other.get("connects", []):
                    if c.get("kind") != "room":
                        continue
                    for door in self.db.fetch_all(
                            "SELECT tiles_json FROM item WHERE map_id = ? AND room_id = ? AND item_type='door' AND properties_json LIKE '%road_entrance%'",
                            (self.map_id, int(c["id"]))):
                        door_cells |= {(int(x), int(y)) for x, y in
                                       (self._loads(door["tiles_json"], {}).get("wall_tiles") or [])}
                band |= door_cells
                band -= (self.building_cells - door_cells)
                geom["width"] = float(target_width)
                other["width"] = target_width
                other["aggregated_flow"] = load
                other["width_profile"] = {"endpoint_width": min_width, "peak_width": target_width,
                                          "shape": "sin_taper"}
                self.db.execute("UPDATE room SET geom_json = ?, tiles_json = ?, area = ?, other_json = ? WHERE id = ?",
                                (json.dumps(geom, ensure_ascii=False),
                                 json.dumps({"wall": [], "space": [[x, y] for x, y in sorted(band)], "inner_wall": []}, ensure_ascii=False),
                                 len(band), json.dumps(other, ensure_ascii=False), rid))
        return saved, area_map

    # ==================================================================
    # 真菌 v2：以「道路区域」而不是「道路边」为变量的运输优化
    # ==================================================================
    def connect_fungus_v2(self, area: JsonDict, doors: List[Optional[JsonDict]], *,
                          area_cost: Optional[float] = None,
                          candidate_degree: int = 5,
                          weights: Optional[Dict[Any, float]] = None) -> Tuple[int, Dict[Cell, int]]:
        """生成面积--全 OD 人流最短路折衷的真菌道路区域。

        对每对建筑 ``i, j``，存在 ``A_i`` 人从 i 到 j、``A_j`` 人从 j 到 i，
        所以无向需求为 ``A_i + A_j``。道路不是预设宽度的边集合，而是一组
        可走格 ``R``；目标函数为：

        ``area_cost * |R| + sum(i<j) (A_i+A_j) * d_R(door_i, door_j)``。

        精确枚举所有格集合是 NP-hard，故实现采取区域级的增生--收缩近似：
        先构造保证连通的最短路径骨架，随后仅在新增道路面积能降低更多全 OD
        行走代价时添加捷径，最后逐条移除不值得维护的候选通道。最终保存为一
        个 ``road`` 房间（其 ``space`` 即道路区域），而非一组有固定宽度的线。
        """
        buildings = [b for b, door in zip(area.get("buildings", []), doors) if door is not None]
        if len(buildings) < 2:
            return 0, {}
        dmap = {int(d["building_id"]): d for d in doors if d is not None}
        ids = [int(b["id"]) for b in buildings]
        index = {bid: i for i, bid in enumerate(ids)}
        n = len(ids)
        # v2 严格采用用户定义的人口模型：一格向每个其它建筑派出一人，故
        # 默认质量就是建筑格数本身（不是 v1 为压缩动态范围使用的 sqrt(area)）。
        masses: Dict[int, float] = {}
        for bid, building in zip(ids, buildings):
            raw = None
            if weights:
                raw = weights.get(bid, weights.get(str(bid), weights.get(building.get("name"))))
            if raw is None:
                row = self.db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (bid,))
                tiles = self._loads(row["tiles_json"] if row else None, {})
                raw = len(tiles.get("space") or [])
            masses[bid] = max(1.0, float(raw))

        # 门外相邻格是路网的终端；门洞格本身会在最终区域中保留，确保只在门口
        # 与建筑接触。v2 没有建筑外扩禁区，建筑本体是唯一硬障碍。
        terminals: Dict[int, Cell] = {}
        door_cells: Set[Cell] = set()
        for bid, door in dmap.items():
            gate = (int(door["door_mid"][0]) + int(door["dir"][0]),
                    int(door["door_mid"][1]) + int(door["dir"][1]))
            if self.in_map(gate) and gate not in self.building_cells:
                terminals[bid] = gate
                door_cells |= {(int(x), int(y)) for x, y in door.get("door_cells", [])}
        ids = [bid for bid in ids if bid in terminals]
        index = {bid: i for i, bid in enumerate(ids)}
        n = len(ids)
        if n < 2:
            return 0, {}

        def path_length(path: List[Cell]) -> float:
            return sum(DIAG_COST if x1 != x2 and y1 != y2 else 1.0
                       for (x1, y1), (x2, y2) in zip(path, path[1:]))

        path_cache: Dict[Tuple[int, int], Optional[List[Cell]]] = {}
        def route(a: int, b: int) -> Optional[List[Cell]]:
            key = (a, b) if a < b else (b, a)
            if key not in path_cache:
                # 只避开建筑本体；这条路径稍后会成为可通行区域的一部分。
                path_cache[key] = self._a_star(terminals[a], terminals[b], "弯曲",
                                                 rng=self.rng, cap=600000, perturb=0.0)
            return path_cache[key]

        pair_flow = {(a, b): masses[a] + masses[b]
                     for pos, a in enumerate(ids) for b in ids[pos + 1:]}
        total_pair_flow = sum(pair_flow.values())
        # 自动标定把一格道路维护成本换算为“人步”：规模越大，人流越大，但不会
        # 因绝对地图尺寸改变而无端偏向铺满或缩成树。调用方可显式覆盖该系数。
        if area_cost is None:
            area_cost = max(1.0, total_pair_flow / max(1.0, math.sqrt(self.map_w * self.map_h)))
        area_cost = float(area_cost)
        if area_cost <= 0:
            raise ValueError("真菌v2 的 area_cost 必须为正数")
        candidate_degree = max(2, min(n - 1, int(candidate_degree)))

        # 用几何距离列出候选；真正路线仅在需要时做 A*，避免在 60 房间地图
        # 上预先计算完整图。Kruskal 得到一组必连通的初始道路区域。
        geometric_pairs = []
        for pos, a in enumerate(ids):
            for b in ids[pos + 1:]:
                pa, pb = terminals[a], terminals[b]
                geometric_pairs.append((math.hypot(pa[0] - pb[0], pa[1] - pb[1]), a, b))
        geometric_pairs.sort()
        uf = UnionFind()
        for bid in ids:
            uf.make_set(bid)
        selected: List[Dict[str, Any]] = []
        selected_pairs: Set[Tuple[int, int]] = set()
        for _geo, a, b in geometric_pairs:
            if uf.find(a) == uf.find(b):
                continue
            path = route(a, b)
            if not path:
                continue
            uf.union(a, b)
            selected.append({"a": a, "b": b, "path": path, "length": path_length(path), "kind": "seed"})
            selected_pairs.add((a, b))
            if len(selected) == n - 1:
                break
        if len(selected) != n - 1:
            return 0, {}

        # 终端图的 Floyd-Warshall 距离用于快速比较候选捷径。区域交叉会在最后
        # 的栅格最短路复核中自然产生额外好处，因此这里的增益估计是保守的。
        def terminal_dist(edges: List[Dict[str, Any]]) -> List[List[float]]:
            inf = float("inf")
            dist = [[0.0 if i == j else inf for j in range(n)] for i in range(n)]
            for edge in edges:
                i, j, length = index[edge["a"]], index[edge["b"]], float(edge["length"])
                if length < dist[i][j]:
                    dist[i][j] = dist[j][i] = length
            for k in range(n):
                for i in range(n):
                    dik = dist[i][k]
                    if not math.isfinite(dik):
                        continue
                    for j in range(n):
                        nd = dik + dist[k][j]
                        if nd < dist[i][j]:
                            dist[i][j] = nd
            return dist

        def travel_cost(dist: List[List[float]]) -> float:
            total = 0.0
            for pos, a in enumerate(ids):
                for b in ids[pos + 1:]:
                    total += pair_flow[(a, b)] * dist[pos][index[b]]
            return total

        # 每个终端仅保留若干近邻候选。它们足以形成局部绕行与合流，并防止高
        # 人口情况下退化为稠密完全图。
        candidate_pairs: Set[Tuple[int, int]] = set()
        for a in ids:
            ordered = sorted((math.hypot(terminals[a][0] - terminals[b][0], terminals[a][1] - terminals[b][1]), b)
                             for b in ids if b != a)
            for _distance, b in ordered[:candidate_degree]:
                candidate_pairs.add((a, b) if a < b else (b, a))

        region: Set[Cell] = set(door_cells) | set(terminals.values())
        for edge in selected:
            region.update(edge["path"])
        dist = terminal_dist(selected)

        # 增生：选择单位新增面积带来最大 OD 节省的捷径。路径与现有区域重叠
        # 时，重叠部分没有额外面积成本，因此自然会合并成共同通道。
        while True:
            baseline = travel_cost(dist)
            best: Optional[Tuple[float, Dict[str, Any], List[List[float]]]] = None
            for a, b in candidate_pairs:
                if (a, b) in selected_pairs:
                    continue
                ia, ib = index[a], index[b]
                euclidean = math.hypot(terminals[a][0] - terminals[b][0], terminals[a][1] - terminals[b][1])
                # 用直线长度的乐观估计先剪枝；只有可能收回道路面积成本才寻路。
                estimate = 0.0
                for i in range(n):
                    for j in range(i + 1, n):
                        via = min(dist[i][ia] + euclidean + dist[ib][j],
                                  dist[i][ib] + euclidean + dist[ia][j])
                        if via < dist[i][j]:
                            estimate += pair_flow[(ids[i], ids[j])] * (dist[i][j] - via)
                if estimate <= area_cost * max(1.0, euclidean):
                    continue
                path = route(a, b)
                if not path:
                    continue
                extra_area = len(set(path) - region)
                trial = selected + [{"a": a, "b": b, "path": path, "length": path_length(path), "kind": "shortcut"}]
                trial_dist = terminal_dist(trial)
                benefit = baseline - travel_cost(trial_dist)
                net = benefit - area_cost * extra_area
                if net > 1e-6 and (best is None or net > best[0]):
                    best = (net, trial[-1], trial_dist)
            if best is None:
                break
            _net, edge, dist = best
            selected.append(edge)
            selected_pairs.add((edge["a"], edge["b"]))
            region.update(edge["path"])

        # 收缩：若删除整条候选通道后节省的独占道路面积大于增加的人行总代价，
        # 就删除。这样环只有在真实缩短大量 OD 路径时才存活。
        changed = True
        while changed:
            changed = False
            for edge in list(selected):
                remaining = [other for other in selected if other is not edge]
                trial_dist = terminal_dist(remaining)
                if any(not math.isfinite(trial_dist[i][j]) for i in range(n) for j in range(n)):
                    continue
                other_cells = set(door_cells) | set(terminals.values())
                for other in remaining:
                    other_cells.update(other["path"])
                saved_area = len(region - other_cells - self.building_cells)
                if saved_area <= 0:
                    continue
                delta_walk = travel_cost(trial_dist) - travel_cost(dist)
                if area_cost * saved_area > delta_walk:
                    selected = remaining
                    selected_pairs.discard((edge["a"], edge["b"]))
                    region = other_cells
                    dist = trial_dist
                    changed = True
                    break

        # 以真实“可走格区域”复核总 OD 距离；这里允许道路在交叉和合流处直接
        # 互通，正是 v2 与仅在端点连线的图模型不同之处。
        walkable = set(region)
        def grid_dist(start: Cell) -> Dict[Cell, float]:
            heap = [(0.0, start)]
            result = {start: 0.0}
            while heap:
                cost, cur = heapq.heappop(heap)
                if cost != result.get(cur):
                    continue
                for dx, dy in DIRS8:
                    nxt = (cur[0] + dx, cur[1] + dy)
                    if nxt not in walkable:
                        continue
                    nxt_cost = cost + (DIAG_COST if dx and dy else 1.0)
                    if nxt_cost < result.get(nxt, float("inf")):
                        result[nxt] = nxt_cost
                        heapq.heappush(heap, (nxt_cost, nxt))
            return result

        exact_walk = 0.0
        for pos, a in enumerate(ids):
            dists = grid_dist(terminals[a])
            for b in ids[pos + 1:]:
                exact_walk += pair_flow[(a, b)] * dists.get(terminals[b], float("inf"))
        road_area = len(walkable - self.building_cells)
        objective = area_cost * road_area + exact_walk

        wall = {(x, y) for x, y in walkable
                if any((x + dx, y + dy) not in walkable for dx, dy in DIRS8)}
        wall -= self.building_cells
        center = (sum(x for x, _ in walkable) / max(1, len(walkable)),
                  sum(y for _, y in walkable) / max(1, len(walkable)))
        geom = {"type": "road_region", "center": [center[0], center[1]], "style": "电路"}
        other = {
            "generator": "road_style",
            "algorithm": "fungus_v2_area_transport",
            "style": "电路",
            "connects": [{"kind": "room", "id": bid} for bid in ids],
            "area_cost": area_cost,
            "road_area": road_area,
            "travel_cost": exact_walk,
            "objective": objective,
            "candidate_degree": candidate_degree,
            "selected_corridors": len(selected),
            "demand_model": "i_to_j=A_i; undirected=A_i+A_j",
            "terminal_masses": {str(bid): masses[bid] for bid in ids},
        }
        cur = self.db.execute(
            "INSERT INTO room (map_id, building_area_id, name, layer_start, layer_end, room_type, geom_json, tiles_json, area, other_json) "
            "VALUES (?, ?, ?, 1, 1, 'road', ?, ?, ?, ?)",
            (self.map_id, area.get("area_id"), self.new_road_name(), json.dumps(geom, ensure_ascii=False),
             json.dumps({"wall": [[x, y] for x, y in sorted(wall)],
                         "space": [[x, y] for x, y in sorted(walkable)], "inner_wall": []}, ensure_ascii=False),
             len(walkable), json.dumps(other, ensure_ascii=False)),
        )
        rid = int(cur.lastrowid)
        self.existing_roads.append((rid, {"wall": sorted(wall), "space": sorted(walkable), "inner_wall": []}))
        return 1, {cell: rid for cell in walkable}

    # ==================================================================
    # 真菌：早期维护成本加权侵蚀 + 后期深部开洞（唯一正式模型）
    # ==================================================================
    def connect_fungus_v3(self, area: JsonDict, doors: List[Optional[JsonDict]], *,
                          maintenance_cost: Optional[float] = None,
                          min_road_width: int = 5,
                          optimization_cell_size: Optional[int] = None,
                          max_iterations: int = 48,
                          max_attempts: int = 110,
                          erosion_batch_size: int = 0,
                          boundary_rounding: int = 0,
                          building_clearance: int = 1,
                          perimeter_weight: float = 0.005,
                          nucleation_interval: int = 4,
                          detour_factor: float = 2.0,
                          late_nucleation_rounds: int = 12,
                          hole_growth_steps: int = 8,
                          hole_growth_batch: int = 0,
                          late_min_solid_depth: int = 3,
                          weights: Optional[Dict[Any, float]] = None) -> Tuple[int, Dict[Cell, int]]:
        """从满铺可通行区域开始，用早期维护成本加权侵蚀得到真菌道路。

        设建筑 i 的可用格数为 A_i。i 的每格都会向每个其它建筑派出一人，
        因而 i 到 j 的定向需求为 A_i，双向需求为 A_i+A_j。对道路区域 R，
        v3 最小化 ``maintenance_cost*|R| + sum(i<j)(A_i+A_j)*d_R(i,j)``。

        搜索状态不是独立方块列表，而是从地图外缘、建筑边缘或内部种子长出的
        “空洞菌落”。每轮以 Brandes 依赖回传把人流严格分配给全部等长最短路，
        再用面积、流量和边界曲率组成离散拓扑导数，使一整段低价值边界同步移动。
        完整 OD 目标与周长正则负责回溯验收，步长过大时自动折半；主侵蚀后会
        在剩余大块区域深部重新萌发空洞并局部扩孔。这就是项目保留的早期侵蚀版。

        优化格默认就是 ``min_road_width``，而非旧版大图强制 10 格。道路宏格仅
        以四邻域相接，因此任意承担连通性的通道至少具有一个完整优化格的宽度；
        输出时只向外做圆角扩张，不会把这些通道削窄。
        """
        requested_min_width = max(5, int(min_road_width))
        if optimization_cell_size is None:
            optimization_cell_size = requested_min_width
        width = max(requested_min_width, int(optimization_cell_size))
        boundary_rounding = max(0, int(boundary_rounding))
        building_clearance = max(0, int(building_clearance))
        perimeter_weight = max(0.0, float(perimeter_weight))
        nucleation_interval = max(1, int(nucleation_interval))
        detour_factor = max(0.0, float(detour_factor))
        late_nucleation_rounds = max(0, int(late_nucleation_rounds))
        hole_growth_steps = max(0, int(hole_growth_steps))
        hole_growth_batch = max(0, int(hole_growth_batch))
        late_min_solid_depth = max(1, int(late_min_solid_depth))
        _style_name = "真菌"
        _algorithm_name = "fungus_early_weighted_erosion"
        buildings = [b for b, door in zip(area.get("buildings", []), doors) if door is not None]
        if len(buildings) < 2:
            return 0, {}
        dmap = {int(d["building_id"]): d for d in doors if d is not None}
        ids = [int(b["id"]) for b in buildings if int(b["id"]) in dmap]
        bmap = {int(b["id"]): b for b in buildings}
        if len(ids) < 2:
            return 0, {}

        # v3 使用面积本身作为人口；可选 weights 仅用于明确覆盖 A_i。
        masses: Dict[int, float] = {}
        for bid in ids:
            raw = None
            if weights:
                raw = weights.get(bid, weights.get(str(bid), weights.get(bmap[bid].get("name"))))
            if raw is None:
                row = self.db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (bid,))
                tiles = self._loads(row["tiles_json"] if row else None, {})
                raw = len(tiles.get("space") or [])
            masses[bid] = max(1.0, float(raw))
        pair_flow = {(a, b): masses[a] + masses[b]
                     for i, a in enumerate(ids) for b in ids[i + 1:]}
        total_pair_flow = sum(pair_flow.values())
        if maintenance_cost is None:
            # 自动值的单位是“人步/道路格”：地图越大，单位道路的长期维护价值越高。
            maintenance_cost = max(1.0, total_pair_flow / max(1.0, math.sqrt(self.map_w * self.map_h)))
        maintenance_cost = float(maintenance_cost)
        if maintenance_cost <= 0:
            raise ValueError("真菌v3 的 maintenance_cost 必须为正数")
        erosion_batch_size = int(erosion_batch_size)

        cols = int(math.ceil(self.map_w / width))
        rows = int(math.ceil(self.map_h / width))
        routing_forbidden = set(self.building_cells)
        if building_clearance:
            routing_forbidden |= {
                (x + dx, y + dy) for x, y in self.building_cells
                for dx in range(-building_clearance, building_clearance + 1)
                for dy in range(-building_clearance, building_clearance + 1)
                if 0 <= x + dx < self.map_w and 0 <= y + dy < self.map_h
            }
        tile_cells: Dict[Cell, Set[Cell]] = {}
        safe: Set[Cell] = set()
        for tx in range(cols):
            for ty in range(rows):
                cells = {(x, y) for x in range(tx * width, min(self.map_w, (tx + 1) * width))
                         for y in range(ty * width, min(self.map_h, (ty + 1) * width))}
                if not cells or cells & routing_forbidden:
                    continue
                node = (tx, ty)
                tile_cells[node] = cells
                safe.add(node)
        if not safe:
            return 0, {}

        def tile_center(node: Cell) -> Cell:
            cells = tile_cells[node]
            cx = min(max(int((node[0] + 0.5) * width), 0), self.map_w - 1)
            cy = min(max(int((node[1] + 0.5) * width), 0), self.map_h - 1)
            return (cx, cy) if (cx, cy) in cells else next(iter(cells))

        # 每个门通过一个不参与侵蚀的最短门廊接入最近的完整宽度宏格。
        anchors: Dict[int, Cell] = {}
        connectors: Dict[int, List[Cell]] = {}
        door_cells: Set[Cell] = set()
        for bid in ids:
            door = dmap[bid]
            gate = (int(door["door_mid"][0]) + int(door["dir"][0]),
                    int(door["door_mid"][1]) + int(door["dir"][1]))
            candidates = heapq.nsmallest(
                64, safe,
                key=lambda node: (tile_center(node)[0] - gate[0]) ** 2 + (tile_center(node)[1] - gate[1]) ** 2)
            connector = None
            anchor = None
            # 门外到最近完整道路面的直线通常无遮挡。先用 Bresenham 验证，
            # 避免 60 个门在 800x800 图上反复启动细网格 A*。
            for node in candidates:
                direct = self._rasterize_segment(gate, tile_center(node))
                if all(cell not in self.building_cells for cell in direct):
                    connector, anchor = direct, node
                    break
            # 少数门被其它建筑恰好挡住时，才以少量 A* 作为可靠回退。
            for node in candidates[:4] if connector is None else []:
                candidate = self._a_star(gate, tile_center(node), "弯曲", rng=self.rng,
                                         cap=250000, perturb=0.0)
                if candidate:
                    connector, anchor = candidate, node
                    break
            if connector is None or anchor is None:
                return 0, {}
            anchors[bid] = anchor
            connectors[bid] = connector
            door_cells |= {(int(x), int(y)) for x, y in door.get("door_cells", [])}

        # 宏格避障可能在建筑缝隙中留下不含任何门的封闭自由空腔。它们无法承担
        # 人流，不应被当作“参数 0 的道路面积”；只保留包含全部门锚点的主分量。
        anchor_nodes = set(anchors.values())
        reachable_safe = {next(iter(anchor_nodes))}
        reachable_queue = deque(reachable_safe)
        while reachable_queue:
            node = reachable_queue.popleft()
            for dx, dy in DIRS4:
                nxt = (node[0] + dx, node[1] + dy)
                if nxt in safe and nxt not in reachable_safe:
                    reachable_safe.add(nxt)
                    reachable_queue.append(nxt)
        if not anchor_nodes <= reachable_safe:
            return 0, {}
        safe = reachable_safe
        tile_cells = {node: cells for node, cells in tile_cells.items() if node in safe}

        n = len(ids)
        locked = set(anchors.values())

        grid_size = cols * rows

        def node_index(node: Cell) -> int:
            return node[1] * cols + node[0]

        def index_node(index_value: int) -> Cell:
            return index_value % cols, index_value // cols

        def distances(source: Cell, occupied_mask: bytearray) -> Tuple[List[int], List[float], List[int]]:
            """BFS 同时计算距离、最短路条数和拓扑序，供 Brandes 流量回传。"""
            source_index = node_index(source)
            queue = deque([source_index])
            dist = [-1] * grid_size
            sigma = [0.0] * grid_size
            order: List[int] = []
            dist[source_index] = 0
            sigma[source_index] = 1.0
            while queue:
                index_value = queue.popleft()
                order.append(index_value)
                x, y = index_value % cols, index_value // cols
                next_dist = dist[index_value] + 1
                adjacent = []
                if x + 1 < cols:
                    adjacent.append(index_value + 1)
                if x > 0:
                    adjacent.append(index_value - 1)
                if y + 1 < rows:
                    adjacent.append(index_value + cols)
                if y > 0:
                    adjacent.append(index_value - cols)
                for nxt in adjacent:
                    if not occupied_mask[nxt]:
                        continue
                    if dist[nxt] < 0:
                        dist[nxt] = next_dist
                        queue.append(nxt)
                    if dist[nxt] == next_dist:
                        sigma[nxt] += sigma[index_value]
            return dist, sigma, order

        connector_lengths = {
            bid: sum(DIAG_COST if x1 != x2 and y1 != y2 else 1.0
                     for (x1, y1), (x2, y2) in zip(path, path[1:]))
            for bid, path in connectors.items()
        }

        def evaluate(occupied: Set[Cell]) -> Tuple[Optional[float], Dict[Cell, float]]:
            """返回精确 OD 距离成本及在多条等长最短路间分摊的流量。"""
            flows = {node: 0.0 for node in occupied}
            occupied_mask = bytearray(grid_size)
            for node in occupied:
                occupied_mask[node_index(node)] = 1
            total = 0.0
            for i, bid in enumerate(ids[:-1]):
                dist, sigma, order = distances(anchors[bid], occupied_mask)
                if any(dist[node_index(anchors[other])] < 0 for other in ids):
                    return None, flows
                dependency = [0.0] * grid_size
                a = bid
                for j in range(i + 1, n):
                    b = ids[j]
                    demand = pair_flow[(a, b)]
                    target_index = node_index(anchors[b])
                    macro_distance = dist[target_index] * width
                    total += demand * (macro_distance + connector_lengths[a] + connector_lengths[b])
                    dependency[target_index] += demand
                # Brandes 依赖回传：每个目标的需求按最短路条数比例分到全部
                # 等长最短路，消除“任取一条父链”造成的方向偏置。
                for index_value in reversed(order):
                    amount = dependency[index_value]
                    if amount <= 0:
                        continue
                    flows[index_node(index_value)] += amount
                    d = dist[index_value]
                    if d <= 0 or sigma[index_value] <= 0:
                        continue
                    x, y = index_value % cols, index_value // cols
                    predecessor_indices = []
                    if x + 1 < cols:
                        predecessor_indices.append(index_value + 1)
                    if x > 0:
                        predecessor_indices.append(index_value - 1)
                    if y + 1 < rows:
                        predecessor_indices.append(index_value + cols)
                    if y > 0:
                        predecessor_indices.append(index_value - cols)
                    for predecessor in predecessor_indices:
                        if dist[predecessor] == d - 1:
                            dependency[predecessor] += amount * sigma[predecessor] / sigma[index_value]
            return total, flows

        def tile_area(occupied: Set[Cell]) -> int:
            return sum(len(tile_cells[node]) for node in occupied)

        # 宏格均为完整空地；门廊以 5 格带连接门和宏格，且始终保留。
        connector_band: Set[Cell] = set(door_cells)
        for path in connectors.values():
            connector_band |= self.band_for_points([(float(x), float(y)) for x, y in path], width)
        connector_band -= (self.building_cells - door_cells)

        occupied = set(safe)                       # 真菌初态：覆盖所有可通行区域
        initial_macro_cells = len(occupied)
        initial_area = tile_area(occupied) + len(connector_band - set().union(*tile_cells.values()))
        travel, flows = evaluate(occupied)
        if travel is None:
            return 0, {}
        initial_travel_cost = travel
        current_area = tile_area(occupied)
        iterations = 0
        attempts = 0
        internal_seeds = 0
        colony_expansions = 0
        rejected = 0
        max_iterations = max(1, int(max_iterations))
        max_attempts = max(1, int(max_attempts))
        # 周长正则只负责消除网格锯齿，不改变用户定义的原始面积/人步目标。
        perimeter_cost = maintenance_cost * width * perimeter_weight

        def macro_perimeter(state: Set[Cell]) -> int:
            return width * sum(
                (node[0] + dx, node[1] + dy) not in state
                for node in state for dx, dy in DIRS4)

        current_perimeter = macro_perimeter(occupied)
        current_search_objective = (maintenance_cost * current_area + travel +
                                    perimeter_cost * current_perimeter)
        initial_search_objective = current_search_objective
        # 0 表示按图规模自动步长；每次完整 OD 计算推动一整段水平集边界。
        step_size = erosion_batch_size
        if step_size <= 0:
            step_size = max(8, initial_macro_cells // max_iterations)
        initial_mutation_size = step_size
        accepted_batch_sizes: List[int] = []
        late_batch = hole_growth_batch or max(2, initial_macro_cells // 1000)

        def is_growth_frontier(node: Cell, state: Set[Cell]) -> bool:
            return any((node[0] + dx, node[1] + dy) not in state for dx, dy in DIRS4)

        while iterations < max_iterations and attempts < max_attempts:
            removable = [node for node in occupied if node not in locked]
            if not removable:
                break
            boundary = [node for node in removable if is_growth_frontier(node, occupied)]
            interior = [node for node in removable if node not in boundary]

            def topology_gain(node: Cell) -> float:
                # 删除一个格的离散形状导数。2*k-4 是其导致的四邻周长变化；
                # 邻域平均流量令梯度不依赖单个像素的数值噪声。
                occupied_neighbors = sum((node[0] + dx, node[1] + dy) in occupied
                                         for dx, dy in DIRS4)
                delta_perimeter = (2 * occupied_neighbors - 4) * width
                local_flow = flows.get(node, 0.0)
                local_flow += 0.25 * sum(flows.get((node[0] + dx, node[1] + dy), 0.0)
                                         for dx, dy in DIRS8)
                return (maintenance_cost * len(tile_cells[node]) -
                        detour_factor * width * local_flow -
                        perimeter_cost * delta_perimeter)

            candidate_rows: List[Tuple[float, Cell, bool]] = [
                (topology_gain(node), node, False) for node in boundary]
            # 周期性拓扑导数：在内部最低流量处萌发少量单格空洞，后续轮次
            # 它们会像外边界一样按曲率和流量继续生长。
            seed_nodes: Set[Cell] = set()
            if interior and iterations % nucleation_interval == 0:
                seed_limit = max(1, min(len(interior), step_size // 30))
                ranked_interior = sorted(interior, key=topology_gain, reverse=True)
                seed_nodes = set(ranked_interior[:seed_limit])
                candidate_rows.extend((topology_gain(node), node, True) for node in seed_nodes)
            # 形状导数是候选排序器而非硬门槛：最短路目标不可微，同一格即使
            # 分到了流量，删除后也可能仍存在完全等长的替代路。是否可删只由
            # 下方完整 OD 能量与回溯线搜索决定。
            candidates = candidate_rows
            if not candidates:
                break
            candidates.sort(key=lambda item: item[0], reverse=True)
            accepted = False
            batch_count = min(step_size, len(candidates))
            # Armijo 风格回溯：若形状导数的一阶预测过大，就把水平集步长折半。
            while batch_count >= 1:
                if attempts >= max_attempts:
                    break
                attempts += 1
                selected = candidates[:batch_count]
                patch = {node for _, node, _ in selected}
                trial = occupied - patch
                trial_travel, trial_flows = evaluate(trial)
                if trial_travel is None:
                    rejected += 1
                else:
                    saved_area = sum(len(tile_cells[node]) for node in patch)
                    trial_area = current_area - saved_area
                    trial_perimeter = macro_perimeter(trial)
                    trial_objective = (maintenance_cost * trial_area + trial_travel +
                                       perimeter_cost * trial_perimeter)
                improves_weighted_objective = (
                    trial_travel is not None and
                    trial_objective < current_search_objective - 1e-6)
                if trial_travel is not None and improves_weighted_objective:
                    occupied = trial
                    current_area = trial_area
                    travel, flows = trial_travel, trial_flows
                    current_perimeter = trial_perimeter
                    current_search_objective = trial_objective
                    iterations += 1
                    accepted = True
                    accepted_count = len(patch)
                    accepted_seeds = sum(
                        is_seed for _, node, is_seed in selected if node in patch)
                    internal_seeds += accepted_seeds
                    colony_expansions += accepted_count - accepted_seeds
                    accepted_batch_sizes.append(accepted_count)
                    # 连续接受整步时稍增步长；回溯过则保守地从两倍接受量继续。
                    if batch_count == min(step_size, len(candidates)):
                        step_size = min(max(1, len(occupied) // 3),
                                        max(step_size + 1, int(step_size * 1.15)))
                    else:
                        step_size = max(1, batch_count * 2)
                    break
                rejected += 1
                if batch_count == 1:
                    break
                batch_count = max(1, batch_count // 2)
            if not accepted:
                break

        # 主侵蚀停下后，寻找“离任何现有空洞都很远”的大块道路深部，
        # 在那里单格萌发新洞，并给该洞保留独立的局部扩张配额。
        late_holes_opened = 0
        late_growth_cells = 0
        late_attempts = 0

        def late_topology_gain(node: Cell) -> float:
            occupied_neighbors = sum((node[0] + dx, node[1] + dy) in occupied
                                     for dx, dy in DIRS4)
            delta_perimeter = (2 * occupied_neighbors - 4) * width
            local_flow = flows.get(node, 0.0)
            local_flow += 0.25 * sum(flows.get((node[0] + dx, node[1] + dy), 0.0)
                                     for dx, dy in DIRS8)
            return (maintenance_cost * len(tile_cells[node]) -
                    detour_factor * width * local_flow -
                    perimeter_cost * delta_perimeter)

        def accept_late_patch(patch: Set[Cell]) -> bool:
            nonlocal occupied, current_area, travel, flows, current_perimeter
            nonlocal current_search_objective, late_attempts, rejected
            if not patch or patch & locked:
                return False
            late_attempts += 1
            trial = occupied - patch
            trial_travel, trial_flows = evaluate(trial)
            if trial_travel is None:
                rejected += 1
                return False
            saved_area = sum(len(tile_cells[node]) for node in patch)
            trial_area = current_area - saved_area
            trial_perimeter = macro_perimeter(trial)
            trial_objective = (maintenance_cost * trial_area + trial_travel +
                               perimeter_cost * trial_perimeter)
            if trial_objective >= current_search_objective - 1e-6:
                rejected += 1
                return False
            occupied = trial
            current_area = trial_area
            travel, flows = trial_travel, trial_flows
            current_perimeter = trial_perimeter
            current_search_objective = trial_objective
            return True

        for _late_round in range(late_nucleation_rounds):
            if not occupied:
                break
            solid_boundary = [
                node for node in occupied
                if any((node[0] + dx, node[1] + dy) not in occupied for dx, dy in DIRS4)]
            if not solid_boundary:
                break
            depth = {node: 0 for node in solid_boundary}
            depth_queue = deque(solid_boundary)
            while depth_queue:
                node = depth_queue.popleft()
                next_depth = depth[node] + 1
                for dx, dy in DIRS4:
                    nxt = (node[0] + dx, node[1] + dy)
                    if nxt in occupied and nxt not in depth:
                        depth[nxt] = next_depth
                        depth_queue.append(nxt)
            deep_candidates = [
                node for node, node_depth in depth.items()
                if node_depth >= late_min_solid_depth and node not in locked and
                all(abs(node[0] - anchor[0]) + abs(node[1] - anchor[1]) > late_min_solid_depth
                    for anchor in locked)]
            if not deep_candidates:
                break
            # 深度奖励寻找大块区域中心；拓扑增益在同等深度下避开高流量位置。
            deep_candidates.sort(
                key=lambda node: (depth[node] * maintenance_cost * len(tile_cells[node]) * 0.25 +
                                  late_topology_gain(node)), reverse=True)
            seed = None
            for candidate in deep_candidates[:12]:
                if accept_late_patch({candidate}):
                    seed = candidate
                    break
            if seed is None:
                break
            late_holes_opened += 1
            internal_seeds += 1
            accepted_batch_sizes.append(1)
            active_hole: Set[Cell] = {seed}

            for _growth_step in range(hole_growth_steps):
                frontier = {
                    (node[0] + dx, node[1] + dy)
                    for node in active_hole for dx, dy in DIRS4
                    if (node[0] + dx, node[1] + dy) in occupied and
                    (node[0] + dx, node[1] + dy) not in locked}
                if not frontier:
                    break
                ranked = sorted(frontier, key=late_topology_gain, reverse=True)
                batch_count = min(late_batch, len(ranked))
                accepted_growth = False
                while batch_count >= 1:
                    patch = set(ranked[:batch_count])
                    if accept_late_patch(patch):
                        active_hole |= patch
                        late_growth_cells += len(patch)
                        colony_expansions += len(patch)
                        accepted_batch_sizes.append(len(patch))
                        accepted_growth = True
                        break
                    if batch_count == 1:
                        break
                    batch_count = max(1, batch_count // 2)
                if not accepted_growth:
                    break

        best_objective = current_search_objective
        road_cells: Set[Cell] = set(connector_band)
        for node in occupied:
            road_cells |= tile_cells[node]
        # 只向外添加圆角像素，不删减承担连通性的完整宏格，因此最窄通道仍 >=5。
        if boundary_rounding:
            offsets = [(dx, dy) for dx in range(-boundary_rounding, boundary_rounding + 1)
                       for dy in range(-boundary_rounding, boundary_rounding + 1)
                       if dx * dx + dy * dy <= boundary_rounding * boundary_rounding]
            outline = {(x, y) for x, y in road_cells
                       if any((x + dx, y + dy) not in road_cells for dx, dy in DIRS8)}
            road_cells |= {(x + dx, y + dy) for x, y in outline for dx, dy in offsets
                           if 0 <= x + dx < self.map_w and 0 <= y + dy < self.map_h}
        road_cells -= (self.building_cells - door_cells)
        if building_clearance:
            near_buildings = {(x + dx, y + dy) for x, y in self.building_cells
                              for dx in range(-building_clearance, building_clearance + 1)
                              for dy in range(-building_clearance, building_clearance + 1)
                              if 0 <= x + dx < self.map_w and 0 <= y + dy < self.map_h}
            road_cells -= (near_buildings - connector_band - door_cells)
        road_area = len(road_cells - self.building_cells)
        wall = {(x, y) for x, y in road_cells
                if any((x + dx, y + dy) not in road_cells for dx, dy in DIRS8)}
        wall -= self.building_cells
        center = (sum(x for x, _ in road_cells) / max(1, len(road_cells)),
                  sum(y for _, y in road_cells) / max(1, len(road_cells)))
        geom = {"type": "road_region", "center": [center[0], center[1]], "style": _style_name,
                "min_road_width": requested_min_width, "optimization_cell_size": width}
        other = {
            "generator": "road_style", "algorithm": _algorithm_name,
            "style": _style_name, "connects": [{"kind": "room", "id": bid} for bid in ids],
            "demand_model": "i_to_j=A_i; undirected=A_i+A_j",
            "terminal_masses": {str(bid): masses[bid] for bid in ids},
            "maintenance_cost": maintenance_cost, "min_road_width": requested_min_width,
            "optimization_cell_size": width,
            "erosion_batch_size": erosion_batch_size,
            "initial_mutation_size": initial_mutation_size,
            "flow_assignment": "all_equal_shortest_paths_brandes",
            "perimeter_weight": perimeter_weight, "perimeter_cost": perimeter_cost,
            "nucleation_interval": nucleation_interval, "detour_factor": detour_factor,
            "boundary_rounding": boundary_rounding, "building_clearance": building_clearance,
            "initial_macro_cells": initial_macro_cells, "final_macro_cells": len(occupied),
            "initial_area_estimate": initial_area, "road_area": road_area,
            "initial_travel_cost": initial_travel_cost,
            "initial_search_objective": initial_search_objective,
            "travel_cost": travel, "objective": maintenance_cost * road_area + travel,
            "erosion_iterations": iterations, "erosion_attempts": attempts,
            "internal_void_seeds": internal_seeds, "colony_expansions": colony_expansions,
            "accepted_worse_mutations": 0, "rejected_mutations": rejected,
            "late_nucleation_rounds": late_nucleation_rounds,
            "late_holes_opened": late_holes_opened,
            "late_growth_cells": late_growth_cells,
            "late_attempts": late_attempts,
            "hole_growth_steps": hole_growth_steps,
            "hole_growth_batch": late_batch,
            "late_min_solid_depth": late_min_solid_depth,
            "selection_model": "weighted_sum",
            "macro_perimeter": current_perimeter,
            "accepted_batch_sizes": accepted_batch_sizes,
            "best_search_objective": best_objective,
        }
        cur = self.db.execute(
            "INSERT INTO room (map_id, building_area_id, name, layer_start, layer_end, room_type, geom_json, tiles_json, area, other_json) "
            "VALUES (?, ?, ?, 1, 1, 'road', ?, ?, ?, ?)",
            (self.map_id, area.get("area_id"), self.new_road_name(), json.dumps(geom, ensure_ascii=False),
             json.dumps({"wall": [[x, y] for x, y in sorted(wall)],
                         "space": [[x, y] for x, y in sorted(road_cells)], "inner_wall": []}, ensure_ascii=False),
             len(road_cells), json.dumps(other, ensure_ascii=False)),
        )
        rid = int(cur.lastrowid)
        self.existing_roads.append((rid, {"wall": sorted(wall), "space": sorted(road_cells), "inner_wall": []}))
        return 1, {cell: rid for cell in road_cells}

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
