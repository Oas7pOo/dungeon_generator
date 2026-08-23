#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视觉直走路网（demo）：只做**配置与调用**，机制在 src 内部实现
（RoadStyleGenerator：建筑放置 / 门 / 寻路 / 视觉直走 / 圆角曲线 / 道路保存）。

- 地图 400x600，唯一 400x600 建筑区，10 个建筑
- **弯曲路网**（出门直走朝建筑群中心 + 视野扩大 + 视野内接入），路宽 3
输出：refactored/test/output/vision_road_map/
"""
import os
import sys
import random

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import (
    DatabaseManager, MapGenerator, MapSpec, BuildingAreaSpec, InteriorSpec,
    ConnectionSpec, DecorationSpec, RoadStyleGenerator,
)

SEED = 20260823
OUT = os.path.join(os.path.dirname(__file__), "output", "vision_road_map")
MAP_W, MAP_H = 400, 600
ROAD_W = 3


def main():
    os.makedirs(OUT, exist_ok=True)
    db_path = os.path.join(OUT, "vision_road.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DatabaseManager(db_path)
    rng = random.Random(SEED)
    random.seed(SEED)

    # 1) 地图 + 唯一 400x600 建筑区
    spec = MapSpec(name="视觉直走路网地图", width=MAP_W, height=MAP_H, layers=1, seed=SEED,
                   areas=[BuildingAreaSpec(generator="rectangle", count=1, name_prefix="城",
                                           kwargs=dict(rect_size=[(MAP_W, MAP_H), (MAP_W, MAP_H)], max_attempts=20))],
                   interior=InteriorSpec(mode="basic"),
                   connection=ConnectionSpec(mode="none"),
                   decoration=DecorationSpec(kind="none"))
    res = MapGenerator(db).generate(spec)
    mid = int(res["map_id"])
    area_id = int(db.fetch_one("SELECT id FROM building_area WHERE map_id = ?", (mid,))["id"])
    # 删除外壳房间（区内放 10 个独立建筑）
    db.execute("DELETE FROM item WHERE room_id IN (SELECT id FROM room WHERE map_id = ? AND room_type != 'road')", (mid,))
    db.execute("DELETE FROM room WHERE map_id = ? AND room_type != 'road'", (mid,))
    print(f"地图 {MAP_W}x{MAP_H}，建筑区 {area_id}")

    gen = RoadStyleGenerator(db, mid, seed=SEED)
    gen.map_w, gen.map_h = MAP_W, MAP_H

    # 2) 建筑（内部函数）
    area = {"index": 0, "area_id": area_id, "bbox": (0, 0, MAP_W, MAP_H),
            "center": [MAP_W / 2.0, MAP_H / 2.0]}
    area["buildings"] = gen.place_buildings(area_id, area["bbox"], n=10, rng=rng)
    print(f"建筑: {len(area['buildings'])} 个")

    # 3) 障碍（内部函数：建筑格 + 2 格外扩）——先建障碍再开门
    gen.build_obstacles(area["buildings"])

    # 4) 门（内部函数，门外一段无建筑）
    area["doors"] = [gen.make_door(b, rng, blocked=gen.building_cells) for b in area["buildings"]]
    print(f"门: {sum(1 for d in area['doors'] if d)} 个")

    # 5) 弯曲路网（内部连接策略：视觉直走朝中心 + 视野接入）
    n, _ = gen.connect_area(area, area["doors"], "弯曲", "稀疏", ROAD_W, rng=rng)
    print(f"弯曲路网: {n} 条（宽{ROAD_W}）")

    # 5b) 道路墙格数据修正（内部函数）
    gen.finalize_road_walls()

    # 6) 统计（内部函数）
    comps = gen.compute_components(area["buildings"])
    print(f"连通分量: {len(comps)}（应1）")

    # 7) 渲染（内部函数）
    p = gen.render_pdf(f"vision_road_seed{SEED}", OUT, fig_size=(12, 18))
    print("PDF:", os.path.abspath(p))
    db.close()


if __name__ == "__main__":
    main()
