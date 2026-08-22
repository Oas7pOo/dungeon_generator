#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版建筑地图（demo）：
- 1200x1200 大地图
- 2 个大建筑区（600x400）：内部放置 5-10 个独立小建筑（30x40 指数随机、最小 4x5），
  每个小建筑 1 个房间（有外墙）=> "每个建筑区中各有 5-10 个建筑"
- 10 个普通建筑区（30x40 指数随机、最小 4x5；其中 2 个圆形），各 1 个正常房间
- "最大的建筑"（面积最大的建筑）内部 BSP 分割成 4-6 个内部房间（内墙+门互通）
- 路网：大建筑区内 = 稠密路网（每房间连 2 个最近）；建筑区间 = 稀疏路网（MST+道路互联）
- 最终所有房间联通

输出：refactored/test/output/mega_map/
"""
import os
import sys
import json
import random

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shapely.geometry import box, Point, Polygon

from src import (
    DatabaseManager, MapGenerator, MapSpec, BuildingAreaSpec, InteriorSpec,
    ConnectionSpec, DecorationSpec, RoomGenerator, RoadGenerator,
    RoomSubdivider, MapVisualizer,
)
from src.db.building_area_dao import BuildingAreaDAO

SEED = 20260820
OUT = os.path.join(os.path.dirname(__file__), "output", "mega_map_v2")


def exp_size(lo, hi):
    s = int(random.expovariate(1.0 / ((hi - lo) / 3.0))) + lo
    return max(lo, min(s, hi))


def exp_size_regular(min_w, min_h, max_w, max_h):
    """
    regular_rect 版尺寸：宽高比收敛到 [0.5, 2]（趋近规矩矩形），
    且 1/20 概率直接生成正方形（复用 BuildingAreaGenerator.generate_room_size 的规则）。
    """
    if random.random() < 1 / 20:
        lo = max(min_w, min_h)
        hi = min(max_w, max_h)
        if lo <= hi:
            side = exp_size(lo, hi)
            return side, side
    w, h = exp_size(min_w, max_w), exp_size(min_h, max_h)
    if w / h > 2.0:
        h = max(min_h, min(int(w / random.uniform(1.0, 1.6)), max_h))
    elif h / w > 2.0:
        w = max(min_w, min(int(h / random.uniform(1.0, 1.6)), max_w))
    return w, h


def place_sub_buildings(db, map_id, parent_row, count, name_prefix):
    """
    在大建筑区内生成 count 个**房间**（building_area_id = 大建筑区 id）。
    区内建筑是房间而不是独立 building_area——保证建筑区之间不重叠。
    """
    size = json.loads(parent_row["size_json"])
    cx, cy = float(parent_row["center_x"]), float(parent_row["center_y"])
    w, h = float(size["width"]), float(size["height"])
    parent_poly = box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    parent_id = int(parent_row["id"])

    # 已有建筑区多边形（避让）
    areas = db.fetch_all(
        "SELECT id, geom_json, size_json, center_x, center_y, radius, geom_type "
        "FROM building_area WHERE map_id = ?",
        (map_id,),
    )
    polys = []
    for a in areas:
        if int(a["id"]) == parent_id:
            continue
        if a["geom_type"] == "circle":
            polys.append(Point(a["center_x"], a["center_y"]).buffer(a["radius"] or 0))
        elif a["geom_json"]:
            g = json.loads(a["geom_json"]) if isinstance(a["geom_json"], str) else a["geom_json"]
            polys.append(Polygon(g))
        else:
            sz = json.loads(a["size_json"])
            polys.append(box(a["center_x"] - sz["width"] / 2, a["center_y"] - sz["height"] / 2,
                             a["center_x"] + sz["width"] / 2, a["center_y"] + sz["height"] / 2))

    room_ids = []
    bnds = parent_poly.bounds
    for i in range(count):
        placed = False
        for _try in range(80):
            # 区内建筑（稠密路网节点）：最小 20x24，最大 120x80，规矩矩形/正方形。
            # （过小的房间如 6x6 会被大邻居包围，带 5 宽道路无法连通——孤立房间的根因）
            bw, bh = exp_size_regular(20, 24, 120, 80)
            minx, miny, maxx, maxy = bnds
            cx2 = random.uniform(minx + 5 + bw / 2, maxx - 5 - bw / 2)
            cy2 = random.uniform(miny + 5 + bh / 2, maxy - 5 - bh / 2)
            poly = box(cx2 - bw / 2, cy2 - bh / 2, cx2 + bw / 2, cy2 + bh / 2)
            if not parent_poly.contains(poly):
                continue
            if any(poly.buffer(5).intersects(p) for p in polys):
                continue
            # 生成房间行（矩形：space 内部格 + wall 边缘格，basic 模型）
            x0, y0 = int(round(cx2 - bw / 2)), int(round(cy2 - bh / 2))
            x1, y1 = int(round(cx2 + bw / 2)), int(round(cy2 + bh / 2))
            space = [[x, y] for x in range(x0, x1) for y in range(y0, y1)]
            sp_set = set((x, y) for (x, y) in space)
            wall = []
            for (x, y) in sp_set:
                if any((x + dx, y + dy) not in sp_set for dx, dy in
                       ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))):
                    wall.append([x, y])
            geom = {"type": "rectangle", "center": [cx2, cy2], "width": float(bw), "height": float(bh),
                    "bbox": [x0, y0, x1, y1]}
            cur = db.execute(
                "INSERT INTO room (map_id, building_area_id, name, layer_start, layer_end, room_type, "
                "geom_json, tiles_json, area, other_json) VALUES (?, ?, ?, 1, 1, 'building', ?, ?, ?, ?)",
                (map_id, parent_id, f"{name_prefix}_{i + 1}",
                 json.dumps(geom, ensure_ascii=False),
                 json.dumps({"wall": wall, "space": space, "inner_wall": []}, ensure_ascii=False),
                 len(space), json.dumps({"generator": "mega_zone_room", "parent_area": parent_id})),
            )
            polys.append(poly)
            room_ids.append(int(cur.lastrowid))
            placed = True
            break
        if not placed:
            print(f"  !! {name_prefix} 第 {i + 1} 个放置失败")
    return room_ids


def main():
    os.makedirs(OUT, exist_ok=True)
    db_path = os.path.join(OUT, "mega.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DatabaseManager(db_path)
    random.seed(SEED)

    # ---------- 1. 建筑区 ----------
    spec = MapSpec(
        name="增强版建筑地图",
        width=1200, height=1200, layers=1, seed=SEED,
        areas=[
            BuildingAreaSpec(generator="rectangle", count=2, name_prefix="大区",
                             kwargs=dict(rect_size=[(600, 400), (600, 400)], max_attempts=60)),
            # 普通建筑区：最大 120x80，regular_rect（趋近规矩矩形、正方形概率更高）
            BuildingAreaSpec(generator="rectangle", count=8, name_prefix="小屋",
                             kwargs=dict(rect_size=[(4, 5), (120, 80)], dist="exponential",
                                         regular_rect=True, max_attempts=100)),
            # 圆塔：半径 10~20
            BuildingAreaSpec(generator="circle", count=2, name_prefix="圆塔",
                             kwargs=dict(radius_range=(10, 20), max_attempts=100)),
        ],
        interior=InteriorSpec(mode="basic"),
        connection=ConnectionSpec(mode="none"),
        decoration=DecorationSpec(kind="none"),
    )
    res = MapGenerator(db).generate(spec)
    mid = int(res["map_id"])
    print("建筑区:", res["areas"], "| 初始房间:", res["rooms"])

    # ---------- 2. 大建筑区内放置独立小建筑（稠密路网节点） ----------
    big_rows = db.fetch_all(
        "SELECT id, center_x, center_y, size_json FROM building_area "
        "WHERE map_id = ? AND name LIKE '大区%'",
        (mid,),
    )
    # 删除大建筑区外壳房间（园区外壳不是房间；区内建筑才是）
    big_ids = [int(a["id"]) for a in big_rows]
    if big_ids:
        ph = ",".join("?" * len(big_ids))
        db.execute(
            "DELETE FROM item WHERE room_id IN "
            "(SELECT id FROM room WHERE map_id = ? AND building_area_id IN (" + ph + "))",
            (mid, *big_ids),
        )
        db.execute(
            "DELETE FROM room WHERE map_id = ? AND building_area_id IN (" + ph + ")",
            (mid, *big_ids),
        )
        print(f"  删除大建筑区外壳房间 x{len(big_ids)}")

    dense_groups = []
    for bi, big in enumerate(big_rows):
        n = random.randint(5, 10)
        ids = place_sub_buildings(db, mid, big, n, f"区内房{bi + 1}")
        dense_groups.append(ids)
        print(f"  大区 {big['id']} 放置 {len(ids)} 个区内建筑")

    # ---------- 3. 最大的建筑内部生成房间（内墙+门） ----------
    rooms_all = db.fetch_all(
        "SELECT id, tiles_json FROM room WHERE map_id = ? AND room_type NOT IN ('road','subdivided')",
        (mid,),
    )
    def area_of(r):
        t = json.loads(r["tiles_json"])
        return len(t.get("space", []))
    if rooms_all:
        biggest = max(rooms_all, key=area_of)
        n_sub = random.randint(4, 6)
        sub_ids = RoomSubdivider(db).subdivide_room(int(biggest["id"]), n_sub, seed=SEED + 3)
        print(f"最大建筑 room{biggest['id']} 分割为 {len(sub_ids)} 个内部房间")
        # 被分割的父房间已删除：从稠密集移除（子房间继承归属但不参与区内稠密）
        # 被分割的父房间已删除：从稠密集移除（子房间继承归属但不参与区内稠密）
        for g in dense_groups:
            if int(biggest["id"]) in g:
                g.remove(int(biggest["id"]))
                print(f"  （room{biggest['id']} 从稠密集移除）")
                break

    # ---------- 4. 路网：稠密（区内分组）+ 稀疏（全区） ----------
    road_res = RoadGenerator(db).generate_and_save_roads(
        mid, layer=1, width=5, seed=SEED + 1,
        dense_groups=dense_groups,
    )
    print("路网:", road_res)

    # ---------- 5. 统计与连通性（用并查集从 connects 重建，权威） ----------
    from src.generators.road_generator import UnionFind
    rooms = db.fetch_all("SELECT id FROM room WHERE map_id = ? AND room_type != 'road'", (mid,))
    uf = UnionFind()
    for r in rooms:
        uf.make_set(int(r["id"]))
    for rd in db.fetch_all("SELECT id, other_json FROM room WHERE map_id = ? AND room_type='road'", (mid,)):
        o = json.loads(rd["other_json"])
        conns = o.get("connects", [])
        uf.make_set(int(rd["id"]))
        for c in conns:
            if isinstance(c, dict) and "id" in c:
                uf.union(int(c["id"]), int(rd["id"]))
    roots = {uf.find(int(r["id"])) for r in rooms}
    covered = len(rooms) if len(roots) <= 1 else max(
        sum(1 for r in rooms if uf.find(int(r["id"])) == root) for root in roots
    )
    door_count = db.scalar("SELECT COUNT(*) FROM item WHERE map_id = ? AND item_type='door'", (mid,))
    print(f"房间: {len(rooms)} | 道路网络覆盖: {covered}/{len(rooms)} | 连通分量: {len(roots)} | 门: {door_count}")

    # 区内 vs 区际道路统计（验证：区内稠密、区际稀疏）
    big_area_ids = {int(a["id"]) for a in big_rows}
    room_area = {}
    for rw in db.fetch_all("SELECT id, building_area_id FROM room WHERE map_id = ? AND room_type != 'road'", (mid,)):
        room_area[int(rw["id"])] = int(rw["building_area_id"]) if rw["building_area_id"] else None
    intra, inter, road_conn = 0, 0, 0
    for rd in db.fetch_all("SELECT other_json FROM room WHERE map_id = ? AND room_type='road'", (mid,)):
        o = json.loads(rd["other_json"])
        cs = o.get("connects", [])
        room_ends = [c["id"] for c in cs if c.get("kind") == "room"]
        if len(room_ends) == 2:
            a1, a2 = room_area.get(int(room_ends[0])), room_area.get(int(room_ends[1]))
            if a1 is not None and a1 == a2 and a1 in big_area_ids:
                intra += 1   # 大建筑区内道路（稠密）
            elif a1 is not None and a2 is not None and a1 != a2:
                inter += 1   # 跨建筑区道路（稀疏）
        elif len(room_ends) == 1:
            road_conn += 1  # 房间-道路接入
    print(f"区内道路(大建筑区内): {intra} | 跨建筑区道路: {inter} | 房间-道路接入: {road_conn}")
    sub_doors = db.scalar(
        "SELECT COUNT(*) FROM item WHERE map_id=? AND properties_json LIKE '%interior%'", (mid,)
    )
    print(f"内部门: {sub_doors}")

    # ---------- 6. 渲染 ----------
    vis = MapVisualizer(db)
    p = vis.save_multi_view_pdf(mid, layers=[1], output_dir=OUT, filename="mega_seed20260820",
                                fig_size=(20, 20), show_grid=True,
                                show_area_names=True, show_room_names=True)
    print("PDF:", os.path.abspath(p))
    vis.close()
    db.close()


if __name__ == "__main__":
    main()
