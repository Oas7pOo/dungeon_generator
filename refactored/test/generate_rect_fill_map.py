#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
矩形房间填充地图（demo）：
- 地图 300x400，1 层，**完全是一个建筑区**（300x400 外壳）
- 区内塞**矩形房间**（room）：尺寸 5x5 ~ 120x180，用 **regular_rect 尺寸法**
  （BuildingAreaGenerator.generate_room_size：1/20 概率正方形 + 宽高比修正，
  避免细长条）；其中 **2 个旋转矩形**（斜着，尺寸偏大保证可见）；房间间距 ≥5（容纳道路）
- **塞法（源程序）**：**旋转矩形先放**（最难塞、最占空间）→ 其余 largest_first
  排序 + 随机放置 + 重叠/间距检查 + 失败逐格缩小；最小 5x5 给 3000 次尝试尽量
  塞满角落；尽可能生成 100 个，连续塞不下（最小 5x5 都放不下）即停
- 路网：所有房间 **稀疏**（MST 全连通）与 **稠密**（多连）**各一份**，
  路宽 3（5 格间距内的道路带，1 格余量；5 格带宽在密排走廊中无法转弯，无法全连通）
输出：refactored/test/output/rect_fill_map/
"""
import os
import sys
import json
import random
import time
import shutil

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shapely.geometry import box, Point, Polygon

from src import (
    DatabaseManager, MapGenerator, MapSpec, BuildingAreaSpec, InteriorSpec,
    ConnectionSpec, DecorationSpec, RoadGenerator, MapVisualizer,
    BuildingAreaGenerator,
)

SEED = 20260820
OUT = os.path.join(os.path.dirname(__file__), "output", "rect_fill_map")
MAP_W, MAP_H = 300, 400
GAP = 5                       # 房间间距（容纳道路）
ROAD_W = 3                    # 路宽（5 格间距中可转弯；5 宽不可）
DENSE_DEGREE = 4              # 稠密路网：每房间连 4 个最近同组房间
MAX_W, MAX_H = 120, 180       # 最大房间
MIN_W, MIN_H = 5, 5           # 最小房间
ROT_COUNT = 2                 # 旋转矩形数量
TARGET = 100                  # 目标房间数
MIN_SIZE_ATTEMPTS = 3000      # 最小尺寸(5x5)的放置尝试次数（尽量塞满角落）


def room_size(min_size, max_size):
    """用项目的 regular_rect 尺寸法（BuildingAreaGenerator.generate_room_size，
    无绑定调用：函数不使用 self）：1/20 概率生成正方形，否则指数随机后修正宽高比
    （长宽比 >2 或 <0.5 的细长条按正态缩放拉回）。"""
    return BuildingAreaGenerator.generate_room_size(
        None, min_size, max_size, dist="exponential", regular_rect=True)


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
    """塞房间：**旋转矩形先放** → 轴对齐 largest_first + 随机放置 + 间距≥5 +
    失败逐格缩小 + 连续塞不下即停（塞法同源程序）。返回 room_ids。"""
    parent_poly = box(2, 2, MAP_W - 2, MAP_H - 2)
    placed_polys = []
    placed_boxes = []          # (minx, miny, maxx, maxy) 粗筛
    room_ids = []

    def _collides(poly):
        pb_poly = poly.buffer(GAP)   # 一次 buffer 复用（间距≥5）
        pb = pb_poly.bounds
        for (x0, y0, x1, y1), p in zip(placed_boxes, placed_polys):
            if pb[0] < x1 and pb[2] > x0 and pb[1] < y1 and pb[3] > y0:
                if pb_poly.intersects(p):
                    return True
        return False

    # ---- 候选 1：旋转矩形（**先放**，尺寸偏大、角度带方向变化）----
    sizes = []                 # (area, w, h, angle, name)
    for i in range(ROT_COUNT):
        w, h = room_size((50, 60), (MAX_W, MAX_H))   # 旋转矩形给较大尺寸，保证"斜着"可见
        angle = rng.choice([-60, -45, -30, 30, 45, 60])
        sizes.append((w * h, w, h, angle, f"RotRoom_{i + 1}"))
        print(f"  旋转矩形候选 RotRoom_{i + 1}: {w}x{h} @ {angle}°")
    sizes.sort(reverse=True, key=lambda s: s[0])

    # ---- 候选 2：轴对齐房间（5x5 ~ 120x180，regular_rect 尺寸法，面积降序，后放）----
    aligned = []
    for i in range(ROT_COUNT, TARGET):
        w, h = room_size((MIN_W, MIN_H), (MAX_W, MAX_H))
        aligned.append((w * h, w, h, 0, f"FillRoom_{i - ROT_COUNT + 1}"))
    aligned.sort(reverse=True, key=lambda s: s[0])
    sizes += aligned

    consecutive_fail = 0
    for area, w, h, angle, name in sizes:
        placed = False
        while w >= MIN_W and h >= MIN_H:
            ok = False
            # 最小尺寸（5x5）给更多尝试：地图快满时只有角落能塞下小房间
            attempts = MIN_SIZE_ATTEMPTS if (w == MIN_W and h == MIN_H) else 30
            for _try in range(attempts):
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
            if w == MIN_W and h == MIN_H:
                break          # 已到最小尺寸仍失败 -> 放弃该候选（防止死循环）
            step = max(1, min(w, h) // 8)
            w = max(MIN_W, w - step)
            h = max(MIN_H, h - step)
        if not placed:
            consecutive_fail += 1
            # 塞不下即停：连续多个候选连最小 5x5 都放不下 => 地图已满
            if consecutive_fail >= 3:
                print(f"  -- 连续 {consecutive_fail} 个候选塞不下（最小 5x5 已无空位），停止填充 --")
                break
        else:
            consecutive_fail = 0
    return room_ids


def _build_rooms(db_path):
    """生成 300x400 唯一建筑区并塞满矩形房间（旋转矩形先放）。
    返回 (map_id, room_ids)。"""
    db = DatabaseManager(db_path)
    random.seed(SEED)
    np.random.seed(SEED)   # regular_rect 尺寸法用 np.random（可复现）
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

    # 塞矩形房间（旋转矩形先放，间距≥5，塞不下即停）
    room_ids = fill_rooms(db, mid, area_id, random.Random(SEED + 1))
    print(f"生成房间: {len(room_ids)} / {TARGET}")
    db.close()
    return mid, room_ids


def _road_variant(db_path, mid, room_ids, dense: bool):
    """在房间布局上生成路网：dense=False 稀疏（MST 全连通） / dense=True 稠密（多连）。"""
    db = DatabaseManager(db_path)
    gen = RoadGenerator(db)
    gen._max_turns = 30
    t0 = time.time()
    road_res = gen.generate_and_save_roads(
        mid, layer=1, width=ROAD_W, seed=SEED + 2,
        dense_groups=([room_ids] if dense else None),
        dense_degree=DENSE_DEGREE,
    )
    print(f"[{'稠密' if dense else '稀疏'}] 路网 {time.time()-t0:.1f}s:", road_res)

    # 连通性 + 度数统计
    from src.generators.road_generator import UnionFind
    rooms = db.fetch_all("SELECT id, name, geom_json FROM room WHERE map_id = ? AND room_type != 'road'", (mid,))
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
    degree = {int(r["id"]): 0 for r in rooms}
    for rd in db.fetch_all("SELECT other_json FROM room WHERE map_id = ? AND room_type='road'", (mid,)):
        for c in json.loads(rd["other_json"]).get("connects", []):
            if c.get("kind") == "room":
                degree[int(c["id"])] += 1
    dv = sorted(degree.values())
    n_road = db.fetch_one("SELECT COUNT(*) c FROM room WHERE map_id = ? AND room_type='road'", (mid,))["c"]
    print(f"[{'稠密' if dense else '稀疏'}] 房间 {len(rooms)} | 道路 {n_road} | "
          f"连通分量 {len(roots)} | 房间度数 avg={sum(dv)/len(dv):.2f} max={dv[-1]}")
    db.close()
    return road_res


def main():
    os.makedirs(OUT, exist_ok=True)
    base = os.path.join(OUT, "rect_fill_rooms.db")
    for p in (base, base + "-wal", base + "-shm"):
        if os.path.exists(p):
            os.remove(p)
    mid, room_ids = _build_rooms(base)

    # 旋转矩形 / 尺寸统计
    db = DatabaseManager(base)
    rooms = db.fetch_all("SELECT id, name, geom_json FROM room WHERE map_id = ? AND room_type != 'road'", (mid,))
    rot = [r for r in rooms if "rotated_rectangle" in (r["geom_json"] or "")]
    def _rot_desc(r):
        g = json.loads(r["geom_json"])
        return "%s %gx%g @ %g°" % (r["name"], g["width"], g["height"], g["angle"])
    print("旋转矩形 %d：%s" % (len(rot), "，".join(_rot_desc(r) for r in rot)))
    sizes = [json.loads(r["geom_json"]) for r in rooms]
    ws = sorted(g["width"] for g in sizes)
    hs = sorted(g["height"] for g in sizes)
    print("尺寸范围: W %g~%g | H %g~%g" % (ws[0], ws[-1], hs[0], hs[-1]))
    # 宽高比统计（regular_rect 效果验证）：正方形 + 非细长比例占比
    ratios = [max(g["width"], g["height"]) / min(g["width"], g["height"]) for g in sizes]
    squares = sum(1 for r in ratios if r <= 1.05)
    not_slender = sum(1 for r in ratios if r <= 2.0)
    print("宽高比: 正方形(≤1.05) %d/%d | 非细长(≤2.0) %d/%d | 最大比例 %.2f" % (
        squares, len(ratios), not_slender, len(ratios), max(ratios)))
    db.close()

    # 稀疏 + 稠密 各一份（独立 DB，同一房间布局）
    for label, dense in (("稀疏", False), ("稠密", True)):
        db_path = os.path.join(OUT, f"rect_fill_{label}.db")
        shutil.copy(base, db_path)
        _road_variant(db_path, mid, room_ids, dense=dense)

        db = DatabaseManager(db_path)
        vis = MapVisualizer(db)
        p = vis.save_multi_view_pdf(mid, layers=[1], output_dir=OUT,
                                    filename=f"rect_fill_{label}_seed{SEED}",
                                    fig_size=(12, 16), show_grid=True,
                                    show_area_names=True, show_room_names=False)
        print(f"[{label}] PDF:", os.path.abspath(p))
        vis.close()
        db.close()


if __name__ == "__main__":
    main()
