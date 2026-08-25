#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同一批 800x800 / 60 房间建筑的三种道路风格横向对比。"""
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import (BuildingAreaSpec, ConnectionSpec, DatabaseManager, DecorationSpec,
                 InteriorSpec, MapGenerator, MapSpec, RoadStyleGenerator)


SEED = 20260824
MAP_W = MAP_H = 800
ROOM_COUNT = 60
OUT = os.path.join(os.path.dirname(__file__), "output", "road_style_compare_800")


def _create_base_map(db, title):
    spec = MapSpec(
        name=title, width=MAP_W, height=MAP_H, layers=1, seed=SEED,
        areas=[BuildingAreaSpec(generator="rectangle", count=1, name_prefix="地图",
                                kwargs={"rect_size": [(MAP_W, MAP_H), (MAP_W, MAP_H)],
                                        "max_attempts": 20})],
        interior=InteriorSpec(mode="basic"), connection=ConnectionSpec(mode="none"),
        decoration=DecorationSpec(kind="none"),
    )
    map_id = int(MapGenerator(db).generate(spec)["map_id"])
    area_id = int(db.fetch_one("SELECT id FROM building_area WHERE map_id = ?", (map_id,))["id"])
    db.execute("DELETE FROM item WHERE map_id = ?", (map_id,))
    db.execute("DELETE FROM room WHERE map_id = ?", (map_id,))
    return map_id, area_id


def _place_identical_buildings(db, map_id, area_id):
    # place_buildings 同时使用局部 RNG 与 numpy/random 的指数尺寸采样，三者均重置。
    random.seed(SEED)
    np.random.seed(SEED)
    gen = RoadStyleGenerator(db, map_id, seed=SEED)
    gen.map_w = gen.map_h = MAP_W
    buildings = gen.place_buildings(
        area_id, (0, 0, MAP_W, MAP_H), n=ROOM_COUNT, gap=10,
        kinds=["rect"] * ROOM_COUNT, rng=random.Random(SEED),
        min_size=(5, 5), max_size=(60, 80), size_dist="exponential",
    )
    assert len(buildings) == ROOM_COUNT, f"房间放置不足: {len(buildings)}/{ROOM_COUNT}"
    signature = tuple((b["name"], tuple(round(v, 4) for v in b["center"]),
                       json.dumps(b["geom"], sort_keys=True, ensure_ascii=False)) for b in buildings)
    return gen, buildings, signature


def _make_center_doors(gen, buildings, width=1):
    center = (MAP_W / 2.0, MAP_H / 2.0)
    return [gen.make_door(
        building, blocked=gen.obstacles, width=width,
        preferred_direction=(center[0] - building["center"][0], center[1] - building["center"][1]),
    ) for building in buildings]


def _generate_one(label, baseline_signature, output_dir):
    db_path = os.path.join(output_dir, f"{label}.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DatabaseManager(db_path)
    try:
        titles = {"curved_dense": "800x800道路风格对比 - 弯曲（稠密）",
                  "fungus_v1": "800x800道路风格对比 - 真菌",
                  "fungus_v2": "800x800道路风格对比 - 真菌v2"}
        map_id, area_id = _create_base_map(db, titles[label])
        gen, buildings, signature = _place_identical_buildings(db, map_id, area_id)
        if baseline_signature is not None:
            assert signature == baseline_signature, f"{label} 的建筑布局与基准不一致"

        if label == "curved_dense":
            gen.build_obstacles(buildings, clear_zone=gen.CLEAR_ZONE)
            doors = _make_center_doors(gen, buildings, width=5)
            area = {
                "area_id": area_id, "buildings": buildings, "doors": doors,
                "door_by_bid": {b["id"]: d for b, d in zip(buildings, doors) if d is not None},
                "bbox": (0, 0, MAP_W, MAP_H), "center": [MAP_W / 2.0, MAP_H / 2.0],
            }
            roads, _ = gen.connect_area(area, doors, "弯曲", "稠密", 5, rng=gen.rng, dense_k=3)
            style_name, pdf_name = "弯曲（稠密）", "curved_dense_800_seed20260824"
        elif label == "fungus_v1":
            gen.build_obstacles(buildings, clear_zone=5)
            doors = gen.make_fungus_doors(buildings, max_width=10, clear_zone=5)
            roads, _ = gen.connect_fungus(
                {"area_id": area_id, "buildings": buildings}, doors,
                min_width=5, max_width=10, maintenance_cost=20.0,
                loop_gain_threshold=1.25, max_cycles=12,
            )
            style_name, pdf_name = "真菌", "fungus_v1_800_seed20260824"
        elif label == "fungus_v2":
            gen.build_obstacles(buildings, clear_zone=0)
            doors = _make_center_doors(gen, buildings, width=1)
            roads, _ = gen.connect_fungus_v2(
                {"area_id": area_id, "buildings": buildings}, doors, candidate_degree=5)
            style_name, pdf_name = "真菌v2", "fungus_v2_800_seed20260824"
        else:
            raise ValueError(label)

        gen.finalize_road_walls()
        components = gen.compute_components(buildings)
        assert len([door for door in doors if door]) == ROOM_COUNT
        assert len(components) == 1
        road_rows = db.fetch_all(
            "SELECT tiles_json, other_json FROM room WHERE map_id = ? AND room_type='road'", (map_id,))
        total_road_cells = sum(len(json.loads(row["tiles_json"]).get("space") or []) for row in road_rows)
        metas = [json.loads(row["other_json"]) for row in road_rows]
        pdf_path = gen.render_pdf(pdf_name, output_dir, fig_size=(16, 16))
        return signature, {
            "style": style_name, "db": db_path, "pdf": pdf_path, "buildings": len(buildings),
            "doors": len([door for door in doors if door]), "roads": roads,
            "road_rooms": len(road_rows), "road_cells": total_road_cells,
            "components": len(components),
            "v2_objective": metas[0].get("objective") if label == "fungus_v2" else None,
        }
    finally:
        db.close()


def generate(output_dir=OUT):
    os.makedirs(output_dir, exist_ok=True)
    baseline = None
    results = []
    for label in ("curved_dense", "fungus_v1", "fungus_v2"):
        baseline, result = _generate_one(label, baseline, output_dir)
        results.append(result)
    return results


if __name__ == "__main__":
    print("800x800 道路风格横向对比:")
    for result in generate():
        print(result)
