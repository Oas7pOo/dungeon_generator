#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
1200x1200 分区大地图（六风格路网测验，机制内部化在 src/，脚本只做配置与调用）：

- 6 个 300x400 建筑区（RoadStyleGenerator.place_areas 函数随机排布）
- 每区 10 个建筑 = 8 矩形 + 1 圆形 + 1 斜矩形（RoadStyleGenerator.place_buildings）
- 每区一种路网组合，覆盖 style × density 全部 6 种：
    区1 折角稀疏 / 区2 折角稠密   -> RoadGenerator（优先更直折线，区内 only_room_ids）
    区3 直角稀疏 / 区4 直角稠密   -> RoadStyleGenerator.connect_area(style="直角")
    区5 弯曲稀疏 / 区6 弯曲稠密   -> RoadStyleGenerator.connect_area(style="弯曲")
- 区际：RoadGenerator **折角中型**（宽 10，max_turns=6）连接全部 6 区
  （每区选离区中心最近的建筑作"大门"，only_room_ids 限定，避免把全区房间重连）

输出：refactored/test/output/mega_six_style_map/
"""
import os
import sys
import random

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import (
    DatabaseManager, MapGenerator, MapSpec, InteriorSpec,
    ConnectionSpec, DecorationSpec, RoadStyleGenerator, RoadGenerator,
)

SEED = 20260825
OUT = os.path.join(os.path.dirname(__file__), "output", "mega_six_style_map")
MAP_W, MAP_H = 1200, 1200
AREA_W, AREA_H = 300, 400
N_BUILD = 10
BUILD_MIX = ["rect"] * 8 + ["circle", "rotated"]
AREA_TYPES = ["折角稀疏", "折角稠密", "直角稀疏", "直角稠密", "弯曲稀疏", "弯曲稠密"]


def main():
    os.makedirs(OUT, exist_ok=True)
    db_path = os.path.join(OUT, "mega_six_style.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DatabaseManager(db_path)
    rng = random.Random(SEED)
    random.seed(SEED)

    # 1) 空地图（区内/区际道路由下方直接生成，不经过 ConnectionSpec）
    spec = MapSpec(name="1200x1200 六风格路网地图", width=MAP_W, height=MAP_H, layers=1, seed=SEED,
                   areas=[], interior=InteriorSpec(mode="basic"),
                   connection=ConnectionSpec(mode="none"), decoration=DecorationSpec(kind="none"))
    mid = int(MapGenerator(db).generate(spec)["map_id"])
    gen = RoadStyleGenerator(db, mid, seed=SEED)
    gen.map_w, gen.map_h = MAP_W, MAP_H
    print("地图 1200x1200")

    # 2) 建筑区随机排布 + 每区 10 建筑
    areas = gen.place_areas(6, AREA_W, AREA_H, rng)
    all_buildings = []
    for i, a in enumerate(areas):
        a["type"] = AREA_TYPES[i]
        a["buildings"] = gen.place_buildings(a["area_id"], a["bbox"], n=N_BUILD, kinds=BUILD_MIX, rng=rng)
        all_buildings.extend(a["buildings"])
        kinds = {}
        for b in a["buildings"]:
            kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
        print(f"  区{i + 1}[{a['type']}] bbox={tuple(round(v) for v in a['bbox'])} 建筑 {len(a['buildings'])} {kinds}")

    # 3) 障碍（建筑格 + 2 格外扩）——先建障碍再开门
    gen.build_obstacles(all_buildings)

    # 4) 任意形状开门（8 邻外向，斜边门外一点不算进建筑）
    #    blocked 传 obstacles（建筑格+2格外扩圈）：门外点必须不在 A* 障碍里，
    #    否则斜矩形/圆形门外点落进外扩圈 → 该建筑所有连接失败
    for a in areas:
        a["doors"] = [gen.make_door(b, rng, blocked=gen.obstacles) for b in a["buildings"]]
        a["door_by_bid"] = {b["id"]: d for b, d in zip(a["buildings"], a["doors"]) if d is not None}
        print(f"  区{a['index'] + 1} 门 {sum(1 for d in a['doors'] if d)}/{len(a['buildings'])}")

    # 5) 区内路网：6 区 × 6 种组合（区内小路宽 5）
    for a in areas:
        style, density = a["type"][:2], a["type"][2:]
        room_ids = [b["id"] for b in a["buildings"]]
        if style == "折角":
            # 折角：RoadGenerator 限定区内房间（only_room_ids）；稠密=组内多连，稀疏=组内 MST
            rg = RoadGenerator(db)
            res = rg.generate_and_save_roads(
                mid, layer=1, width=RoadStyleGenerator.ROAD_SMALL_W,
                seed=SEED + a["index"] + 1,
                only_room_ids=room_ids,
                dense_groups=([room_ids] if density == "稠密" else None),
                dense_degree=2,
                max_turns=6,
            )
            print(f"  区{a['index'] + 1}[{a['type']}] 折角道路 {res['roads']} 条"
                  f"（小路 宽{RoadStyleGenerator.ROAD_SMALL_W}）conn={res['connected']}")
        else:
            n, _ = gen.connect_area(a, a["doors"], style, density,
                                    RoadStyleGenerator.ROAD_SMALL_W, rng=rng)
            print(f"  区{a['index'] + 1}[{a['type']}] 内部道路 {n} 条"
                  f"（小路 宽{RoadStyleGenerator.ROAD_SMALL_W}）")

    # 6) 区际：折角中型（宽 10）连接全部 6 区——**上级路用下级路的主路作为起点**：
    #    每区先找"区内主路"（度最大的路 = 树中最根节点的路），区际路从主路中心接出
    rg = RoadGenerator(db)
    main_map = rg.find_main_roads(mid, layer=1)
    print("区内主路:", {a["index"] + 1: rid for a in areas for rid in [main_map.get(a["area_id"], -1)]})
    main_roads = [rid for a in areas for rid in [main_map.get(a["area_id"])] if rid is not None]
    inter = rg.generate_inter_area_roads(
        mid, layer=1, main_roads=main_roads, width=RoadStyleGenerator.ROAD_MED_W,
        seed=SEED + 100, max_turns=40, astar_max_steps=1500000,
    )
    print(f"区际折角中路（宽{RoadStyleGenerator.ROAD_MED_W}）: {inter['roads']} 条"
          f" | 连通: {inter['connected']} | warnings: {inter['warnings']}")

    # 7) 道路墙格修正（交叉打通后重算边界墙）+ 连通统计
    gen.finalize_road_walls()
    comps = gen.compute_components(all_buildings)
    n_road = db.fetch_one("SELECT COUNT(*) c FROM room WHERE map_id = ? AND room_type='road'", (mid,))["c"]
    n_intra = n_road - inter["roads"]
    print(f"总道路 {n_road}（区内 {n_intra} + 区际 {inter['roads']}）| 建筑 {len(all_buildings)} | "
          f"连通分量 {len(comps)}（应 1：6 区整体连接）")
    for root, members in comps.items():
        print(f"  分量 {root}: {len(members)} 建筑")

    # 8) 渲染（旧 PDF 被占用则用 v2 文件名）
    try:
        p = gen.render_pdf(f"mega_six_style_seed{SEED}", OUT, fig_size=(16, 16))
    except PermissionError:
        p = gen.render_pdf(f"mega_six_style_seed{SEED}_v2", OUT, fig_size=(16, 16))
    print("PDF:", os.path.abspath(p))
    db.close()


if __name__ == "__main__":
    main()
