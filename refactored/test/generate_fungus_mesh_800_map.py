#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""800 x 800、60 房间的真菌运输网压力验证。

其余房间通过 BuildingAreaGenerator 的指数尺寸函数生成，范围为 5x5--60x80。
网状冗余边必须通过运输收益/维护成本判定，小且相距很远的建筑不会仅为“好看”
而形成环路。
"""
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import (DatabaseManager, MapGenerator, MapSpec, BuildingAreaSpec, InteriorSpec,
                 ConnectionSpec, DecorationSpec, RoadStyleGenerator)


SEED = 20260824
MAP_W = MAP_H = 800
ROOM_COUNT = 60
OUT = os.path.join(os.path.dirname(__file__), "output", "fungus_mesh_800")


def generate(output_dir=OUT, render=True):
    os.makedirs(output_dir, exist_ok=True)
    db_path = os.path.join(output_dir, "fungus_mesh_800.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    random.seed(SEED)
    np.random.seed(SEED)
    db = DatabaseManager(db_path)
    try:
        spec = MapSpec(
            name="800x800真菌运输网", width=MAP_W, height=MAP_H, layers=1, seed=SEED,
            areas=[BuildingAreaSpec(generator="rectangle", count=1, name_prefix="地图",
                                    kwargs=dict(rect_size=[(MAP_W, MAP_H), (MAP_W, MAP_H)], max_attempts=20))],
            interior=InteriorSpec(mode="basic"), connection=ConnectionSpec(mode="none"),
            decoration=DecorationSpec(kind="none"),
        )
        map_id = int(MapGenerator(db).generate(spec)["map_id"])
        area_id = int(db.fetch_one("SELECT id FROM building_area WHERE map_id = ?", (map_id,))["id"])
        db.execute("DELETE FROM item WHERE map_id = ?", (map_id,))
        db.execute("DELETE FROM room WHERE map_id = ?", (map_id,))

        gen = RoadStyleGenerator(db, map_id, seed=SEED)
        gen.map_w = gen.map_h = MAP_W
        # RoadStyleGenerator.place_buildings 内部复用项目的指数尺寸生成逻辑。
        buildings = gen.place_buildings(
            area_id, (0, 0, MAP_W, MAP_H), n=ROOM_COUNT, gap=10,
            kinds=["rect"] * ROOM_COUNT, rng=random.Random(SEED),
            min_size=(5, 5), max_size=(60, 80), size_dist="exponential",
        )
        assert len(buildings) == ROOM_COUNT, f"房间放置不足: {len(buildings)}/{ROOM_COUNT}"

        gen.build_obstacles(buildings, clear_zone=5)
        doors = gen.make_fungus_doors(buildings, max_width=10, clear_zone=5)
        roads, _ = gen.connect_fungus(
            {"area_id": area_id, "buildings": buildings}, doors,
            min_width=5, max_width=10,
            maintenance_cost=20.0, loop_gain_threshold=1.25, max_cycles=12,
        )
        gen.finalize_road_walls()

        components = gen.compute_components(buildings)
        road_rows = db.fetch_all(
            "SELECT other_json FROM room WHERE map_id = ? AND room_type = 'road'", (map_id,))
        meta = [json.loads(row["other_json"]) for row in road_rows]
        cycles = [m for m in meta if m.get("edge_kind") == "redundant_cycle"]
        assert len([d for d in doors if d]) == ROOM_COUNT, "每个 5x5--60x80 房间都应可开门"
        assert len(components) == 1 and roads >= ROOM_COUNT - 1
        assert cycles, "高流量区域应至少保留一个收益为正的冗余环"
        assert all(5 <= int(m["width"]) <= 10 for m in meta)

        pdf_path = None
        if render:
            pdf_path = gen.render_pdf("fungus_mesh_800_seed20260824", output_dir, fig_size=(16, 16))
        return {"db": db_path, "pdf": pdf_path, "buildings": len(buildings), "doors": len([d for d in doors if d]),
                "roads": roads, "cycles": len(cycles), "components": len(components)}
    finally:
        db.close()


if __name__ == "__main__":
    print("800x800 真菌路网:", generate())
