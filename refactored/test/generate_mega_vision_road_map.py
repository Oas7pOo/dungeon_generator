#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
1200x1200 分区大地图（demo）：只做**配置与调用**，机制在 src 内部实现
（RoadStyleGenerator：建筑区排布 / 建筑放置 / 门 / 寻路 / 曲线 / 道路保存 / 连接策略）。

- 6 个 300x400 建筑区（函数随机排布）
- 每区 10 建筑 = 8 矩形 + 1 圆形 + 1 斜矩形
- 路网风格：2 区**直角稠密** / 2 区**直角稀疏** / 1 区**弯曲稠密** / 1 区**弯曲稀疏**（区内小路宽 5）
- 区际：5 区用**中路**（宽 10）弯曲路网连接，1 区孤立
输出：refactored/test/output/mega_vision_road_map/
"""
import os
import sys
import json
import random

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import (
    DatabaseManager, MapGenerator, MapSpec, InteriorSpec,
    ConnectionSpec, DecorationSpec, RoadStyleGenerator,
)

SEED = 20260824
OUT = os.path.join(os.path.dirname(__file__), "output", "mega_vision_road_map")
MAP_W, MAP_H = 1200, 1200
AREA_W, AREA_H = 300, 400
N_BUILD = 10
BUILD_MIX = ["rect"] * 8 + ["circle", "rotated"]
AREA_TYPES = ["直角稠密", "直角稠密", "直角稀疏", "直角稀疏", "弯曲稠密", "弯曲稀疏"]
CONNECTED = [0, 1, 2, 3, 4]      # 5 个建筑区用中路连接
ISOLATED = 5                     # 第 6 区孤立


def main():
    os.makedirs(OUT, exist_ok=True)
    db_path = os.path.join(OUT, "mega_vision_road.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DatabaseManager(db_path)
    rng = random.Random(SEED)
    random.seed(SEED)

    # 1) 地图
    spec = MapSpec(name="1200x1200 分区路网地图", width=MAP_W, height=MAP_H, layers=1, seed=SEED,
                   areas=[], interior=InteriorSpec(mode="basic"),
                   connection=ConnectionSpec(mode="none"), decoration=DecorationSpec(kind="none"))
    mid = int(MapGenerator(db).generate(spec)["map_id"])
    gen = RoadStyleGenerator(db, mid, seed=SEED)
    gen.map_w, gen.map_h = MAP_W, MAP_H
    print("地图 1200x1200")

    # 2) 建筑区随机排布（内部函数）
    areas = gen.place_areas(6, AREA_W, AREA_H, rng)
    for i, a in enumerate(areas):
        a["type"] = AREA_TYPES[i]
        # 3) 建筑放置：8 矩形 + 1 圆形 + 1 斜矩形（内部函数）
        a["buildings"] = gen.place_buildings(a["area_id"], a["bbox"], n=N_BUILD, kinds=BUILD_MIX, rng=rng)
        kinds = {}
        for b in a["buildings"]:
            kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
        print(f"  区{i + 1}[{a['type']}] bbox={tuple(round(v) for v in a['bbox'])} 建筑 {len(a['buildings'])} {kinds}")

    # 4) 障碍（内部函数：建筑格 + 2 格外扩）——先建障碍再开门（门要避开障碍方向）
    all_buildings = [b for a in areas for b in a["buildings"]]
    gen.build_obstacles(all_buildings)

    # 5) 任意形状开门（内部函数，门外一段无建筑）
    for a in areas:
        a["doors"] = [gen.make_door(b, rng, blocked=gen.building_cells) for b in a["buildings"]]
        a["door_by_bid"] = {b["id"]: d for b, d in zip(a["buildings"], a["doors"]) if d is not None}
        print(f"  区{a['index'] + 1} 门 {sum(1 for d in a['doors'] if d)}/{len(a['buildings'])}")

    # 6) 区内路网（内部连接策略：直角/弯曲 × 稠密/稀疏，小路宽 5）
    for a in areas:
        style, density = a["type"][:2], a["type"][2:]
        n, _ = gen.connect_area(a, a["doors"], style, density, RoadStyleGenerator.ROAD_SMALL_W, rng=rng)
        print(f"  区{a['index'] + 1}[{a['type']}] 内部道路 {n} 条（小路 宽{RoadStyleGenerator.ROAD_SMALL_W}）")

    # 7) 区际中路（宽 10，5 区连接，1 区孤立）
    n_inter, n_conn = gen.connect_inter_area(areas, CONNECTED, ISOLATED, RoadStyleGenerator.ROAD_MED_W, rng=rng)
    print(f"区际中路: {n_inter} 条（宽{RoadStyleGenerator.ROAD_MED_W}，连接 {n_conn} 个区，区{ISOLATED + 1} 孤立）")

    # 7b) 道路墙格数据修正（内部函数：交叉打通后重算边界墙）
    gen.finalize_road_walls()
    print("道路外墙修正完成")

    # 8) 统计（内部函数）
    comps = gen.compute_components(all_buildings)
    n_road = db.fetch_one("SELECT COUNT(*) c FROM room WHERE map_id = ? AND room_type='road'", (mid,))["c"]
    print(f"总道路 {n_road} | 建筑 {len(all_buildings)} | 连通分量 {len(comps)}（应 2：5 区网络 + 孤立区）")
    for root, members in comps.items():
        print(f"  分量 {root}: {len(members)} 建筑")

    # 9) 渲染（内部函数；旧 PDF 若被查看器占用则用 v2 文件名）
    try:
        p = gen.render_pdf(f"mega_vision_road_seed{SEED}", OUT, fig_size=(16, 16))
    except PermissionError:
        p = gen.render_pdf(f"mega_vision_road_seed{SEED}_v2", OUT, fig_size=(16, 16))
    print("PDF:", os.path.abspath(p))
    db.close()


if __name__ == "__main__":
    main()
