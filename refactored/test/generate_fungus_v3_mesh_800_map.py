#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""800 x 800、60 房间的真菌 v3 满铺侵蚀压力测试。"""
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
OUT = os.path.join(os.path.dirname(__file__), "output", "fungus_v3_mesh_800")


def generate(output_dir=OUT, render=False, maintenance_cost=None,
             min_road_width=5, optimization_cell_size=5,
             max_iterations=48, max_attempts=110,
             mutation_size=0,
             perimeter_weight=0.005, nucleation_interval=4, detour_factor=2.0,
             late_nucleation_rounds=12, hole_growth_steps=8):
    os.makedirs(output_dir, exist_ok=True)
    db_path = os.path.join(output_dir, "fungus_v3_mesh_800.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    random.seed(SEED)
    np.random.seed(SEED)
    db = DatabaseManager(db_path)
    try:
        spec = MapSpec(
            name="800x800真菌v3满铺侵蚀", width=MAP_W, height=MAP_H, layers=1, seed=SEED,
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

        gen = RoadStyleGenerator(db, map_id, seed=SEED)
        gen.map_w = gen.map_h = MAP_W
        buildings = gen.place_buildings(
            area_id, (0, 0, MAP_W, MAP_H), n=ROOM_COUNT, gap=10,
            kinds=["rect"] * ROOM_COUNT, rng=random.Random(SEED),
            min_size=(5, 5), max_size=(60, 80), size_dist="exponential")
        assert len(buildings) == ROOM_COUNT
        gen.build_obstacles(buildings, clear_zone=0)
        center = (MAP_W / 2.0, MAP_H / 2.0)
        doors = [gen.make_door(
            building, blocked=gen.obstacles, width=min_road_width,
            preferred_direction=(center[0] - building["center"][0],
                                 center[1] - building["center"][1]))
            for building in buildings]
        roads, area_map = gen.connect_fungus_v3(
            {"area_id": area_id, "buildings": buildings}, doors,
            maintenance_cost=maintenance_cost, min_road_width=min_road_width,
            optimization_cell_size=optimization_cell_size,
            max_iterations=max_iterations, max_attempts=max_attempts,
            erosion_batch_size=mutation_size,
            perimeter_weight=perimeter_weight,
            nucleation_interval=nucleation_interval, detour_factor=detour_factor,
            late_nucleation_rounds=late_nucleation_rounds,
            hole_growth_steps=hole_growth_steps)
        gen.finalize_road_walls()

        components = gen.compute_components(buildings)
        row = db.fetch_one(
            "SELECT geom_json, tiles_json, other_json FROM room WHERE map_id = ? AND room_type='road'", (map_id,))
        geom, tiles, meta = json.loads(row["geom_json"]), json.loads(row["tiles_json"]), json.loads(row["other_json"])
        assert roads == 1 and area_map and geom["style"] == "真菌"
        assert len([door for door in doors if door]) == ROOM_COUNT and len(components) == 1
        assert meta["initial_macro_cells"] >= meta["final_macro_cells"] > 0
        assert meta["min_road_width"] >= 5 and len(tiles["space"]) >= meta["road_area"]

        pdf_path = None
        if render:
            pdf_path = gen.render_pdf("fungus_v3_mesh_800_seed20260824", output_dir, fig_size=(16, 16))
        return {"db": db_path, "pdf": pdf_path, "buildings": len(buildings),
                "doors": len([door for door in doors if door]), "roads": roads,
                "components": len(components), "road_area": meta["road_area"],
                "initial_macro_cells": meta["initial_macro_cells"],
                "final_macro_cells": meta["final_macro_cells"],
                "travel_cost": meta["travel_cost"], "objective": meta["objective"],
                "maintenance_cost": meta["maintenance_cost"],
                "erosion_iterations": meta["erosion_iterations"],
                "erosion_attempts": meta["erosion_attempts"],
                "internal_void_seeds": meta["internal_void_seeds"],
                "colony_expansions": meta["colony_expansions"],
                "accepted_worse_mutations": meta["accepted_worse_mutations"]}
    finally:
        db.close()


if __name__ == "__main__":
    print("800x800 真菌v3满铺侵蚀:", generate())
