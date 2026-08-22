#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
矩形房间填充地图（demo）：
- 地图 300x400，1 层，**完全是一个建筑区**（300x400 外壳）
- 区内塞**矩形房间**（room）：尺寸 5x5 ~ 120x180 指数随机，
  其中 **2 个旋转矩形**（斜着）；房间间距 ≥5（容纳道路）
- **塞法（源程序）**：largest_first 排序 + 随机放置 + 重叠/间距检查 +
  失败逐格缩小 + 空闲区重试；尽可能生成 100 个，塞不下即停
- 路网：所有房间 **稠密**（多连）+ **稀疏**（全连通）各一份
输出：refactored/test/output/rect_fill_map/
"""
import os
import sys
import json
import random

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shapely.geometry import box, Point, Polygon

from src import (
    DatabaseManager, MapGenerator, MapSpec, BuildingAreaSpec, InteriorSpec,
    ConnectionSpec, DecorationSpec, RoadGenerator, MapVisualizer,
)

SEED = 20260820
OUT = os.path.join(os.path.dirname(__file__), "output", "rect_fill_map")
MAP_W, MAP_H = 300, 400
GAP = 5                       # 房间间距（容纳道路）
MAX_W, MAX_H = 120, 180       # 最大房间
MIN_W, MIN_H = 5, 5           # 最小房间
ROT_COUNT = 2                 # 旋转矩形数量
TARGET = 100                  # 目标房间数


def exp_size(lo, hi):
    s = int(random.expovariate(1.0 / ((hi - lo) / 3.0))) + lo
    return max(lo, min(s, hi))


def rasterize_poly(poly):
    """矩形/旋转矩形栅格化：space = 多边形内格，wall = space 中 8 邻不在 poly 的格。"""
    minx, miny, maxx, maxy = poly.bounds
    space = []
    for x in range(int(minx) - 1, int(maxx) + 2):
        for y in range(int(miny) - 1, int(maxy) + 2):
            if poly.contains(Point(x + 0.5, y + 0.5)):
                space.append((x, y))
    sp_set = set(space)
    wall = []
    for (x, y) in sp_set:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            if (x + dx, y + dy) not in sp_set:
                wall.append([x, y])
                break
    return sorted(wall), sorted(space)


def rect_corners(cx, cy, w, h, angle_deg=0):
    hw, hh = w / 2.0, h / 2.0
    pts = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    if angle_deg:
        import math
        a = math.radians(angle_deg)
        cos_a, sin_a = math.cos(a), math.sin(a)
        pts = [(x * cos_a - y * sin_a, x * sin_a + y * cos_a) for x, y in pts]
    return [(cx + x, cy + y) for x, y in pts]


def make_rect_room(db, map_id, area_id, name, cx, cy, w, h, angle_deg=0):
    if angle_deg:
        corners = rect_corners(cx, cy, w, h, angle_deg)
        poly = Polygon(corners)
        wall, space = rasterize_poly(poly)
        geom = {"type": "rotated_rectangle", "corners": corners,
                "center": [cx, cy], "width": float(w), "height": float(h), "angle": float(angle_deg)}
    else:
        x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
        x1, y1 = int(round(cx + w / 2)), int(round(cy + h / 2))
        space = [[x, y] for x in range(x0, x1) for y in range(y0, y1)]
        sp_set = set((x, y) for (x, y) in space)
        wall = []
        for (x, y) in sp_set:
            if any((x + dx, y + dy) not in sp_set for dx, dy in
                   ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))):
                wall.append([x, y])
        geom = {"type": "rectangle", "center": [cx, cy], "width": float(w), "height": float(h),
                "bbox": [x0, y0, x1, y1]}

    cur = db.execute(
        "INSERT INTO room (map_id, building_area_id, name, layer_start, layer_end, room_type, "
        "geom_json, tiles_json, area, other_json) VALUES (?, ?, ?, 1, 1, 'fill_rect', ?, ?, ?, ?)",
        (map_id, area_id, name,
         json.dumps(geom, ensure_ascii=False),
         json.dumps({"wall": wall, "space": space, "inner_wall": []}, ensure_ascii=False),
         len(space), json.dumps({"generator": "rect_fill"})),
    )
    return int(cur.lastrowid)


def fill_rooms(db, map_id, area_id, rng):
    """塞房间：largest_first + 随机放置 + 间距≥5 + 失败逐格缩小。返回 room_ids。"""
    parent_poly = box(2, 2, MAP_W - 2, MAP_H - 2)
    placed_polys = []
    placed_boxes = []          # (minx, miny, maxx, maxy) 粗筛
    room_ids = []

    def _collides(poly):
        pb = poly.buffer(GAP).bounds
        for (x0, y0, x1, y1), p in zip(placed_boxes, placed_polys):
            if pb[0] < x1 and pb[2] > x0 and pb[1] < y1 and pb[3] > y0:
                if poly.buffer(GAP).intersects(p):
                    return True
        return False

    # 候选尺寸（面积降序）
    sizes = []
    for i in range(TARGET):
        w, h = exp_size(MIN_W, MAX_W), exp_size(MIN_H, MAX_H)
        rot = 1 if i < ROT_COUNT else 0
        angle = rng.choice([30, 45, 60]) if rot else 0
        sizes.append((w * h, w, h, angle, f"FillRoom_{i + 1}"))
    sizes.sort(reverse=True, key=lambda s: s[0])

    for area, w, h, angle, name in sizes:
        placed = False
        while w >= MIN_W and h >= MIN_H:
            ok = False
            for _try in range(30):
                if angle:
                    diag = (w ** 2 + h ** 2) ** 0.5 / 2.0
                    cx = rng.uniform(2 + diag, MAP_W - 2 - diag)
                    cy = rng.uniform(2 + diag, MAP_H - 2 - diag)
                    corners = rect_corners(cx, cy, w, h, angle)
                    poly = Polygon(corners)
                else:
                    cx = rng.uniform(2 + w / 2.0, MAP_W - 2 - w / 2.0)
                    cy = rng.uniform(2 + h / 2.0, MAP_H - 2 - h / 2.0)
                    poly = box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                if not parent_poly.contains(poly):
                    continue
                if _collides(poly):
                    continue
                rid = make_rect_room(db, map_id, area_id, name, cx, cy, w, h, angle)
                placed_polys.append(poly)
                placed_boxes.append(poly.buffer(GAP).bounds)
                room_ids.append(rid)
                ok = True
                break
            if ok:
                placed = True
                break
            # 逐格缩小（按比例步长，快速收敛：塞不下就明显缩小再试）
            step = max(1, min(w, h) // 8)
            w = max(MIN_W, w - step)
            h = max(MIN_H, h - step)
        if not placed:
            pass  # 塞不下 -> 停（继续下一个尺寸尝试更小的）
    return room_ids


def main():
    os.makedirs(OUT, exist_ok=True)
    db_path = os.path.join(OUT, "rect_fill.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DatabaseManager(db_path)
    random.seed(SEED)

    # 1) 建筑区：300x400 占满（唯一建筑区）
    spec = MapSpec(
        name="矩形房间填充地图",
        width=MAP_W, height=MAP_H, layers=1, seed=SEED,
        areas=[
            BuildingAreaSpec(generator="rectangle", count=1, name_prefix="大地",
                             kwargs=dict(rect_size=[(MAP_W, MAP_H), (MAP_W, MAP_H)], max_attempts=20)),
        ],
        interior=InteriorSpec(mode="basic"),
        connection=ConnectionSpec(mode="none"),
        decoration=DecorationSpec(kind="none"),
    )
    res = MapGenerator(db).generate(spec)
    mid = int(res["map_id"])
    area_row = db.fetch_one("SELECT id FROM building_area WHERE map_id = ?", (mid,))
    area_id = int(area_row["id"])
    # 删除外壳房间（建筑区内塞的是矩形房间，外壳本身不是房间）
    db.execute("DELETE FROM item WHERE room_id IN (SELECT id FROM room WHERE map_id = ? AND room_type != 'road')", (mid,))
    db.execute("DELETE FROM room WHERE map_id = ? AND room_type != 'road'", (mid,))
    print("建筑区:", res["areas"])

    # 2) 塞矩形房间
    room_ids = fill_rooms(db, mid, area_id, random.Random(SEED + 1))
    print(f"生成房间: {len(room_ids)} / {TARGET}")

    # 3) 路网：稠密（同组多连）+ 稀疏（全连通）
    road_res = RoadGenerator(db).generate_and_save_roads(
        mid, layer=1, width=5, seed=SEED + 2,
        dense_groups=[room_ids],
    )
    print("路网:", road_res)

    # 4) 统计
    from src.generators.road_generator import UnionFind
    rooms = db.fetch_all("SELECT id FROM room WHERE map_id = ? AND room_type != 'road'", (mid,))
    uf = UnionFind()
    for r in rooms:
        uf.make_set(int(r["id"]))
    for rd in db.fetch_all("SELECT id, other_json FROM room WHERE map_id = ? AND room_type='road'", (mid,)):
        o = json.loads(rd["other_json"])
        uf.make_set(int(rd["id"]))
        for c in o.get("connects", []):
            if isinstance(c, dict) and "id" in c:
                uf.union(int(c["id"]), int(rd["id"]))
    roots = {uf.find(int(r["id"])) for r in rooms}
    print(f"房间 {len(rooms)} | 连通分量 {len(roots)}")
    rot = db.fetch_all("SELECT id FROM room WHERE map_id = ? AND room_type = 'fill_rect' AND geom_json LIKE '%rotated_rectangle%'", (mid,))
    print(f"旋转矩形房间: {len(rot)}")

    # 5) 渲染
    vis = MapVisualizer(db)
    p = vis.save_multi_view_pdf(mid, layers=[1], output_dir=OUT, filename="rect_fill_seed20260820",
                                fig_size=(12, 16), show_grid=True,
                                show_area_names=True, show_room_names=False)
    print("PDF:", os.path.abspath(p))
    vis.close()
    db.close()


if __name__ == "__main__":
    main()
