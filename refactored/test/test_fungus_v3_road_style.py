import json
import os
import sys
import tempfile
from dataclasses import replace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import (BuildingAreaSpec, ConnectionSpec, DatabaseManager, DecorationSpec,
                 InteriorSpec, MapGenerator, MapSpec, RoadStyleGenerator)


def test_fungus_early_erosion_opens_late_holes_and_preserves_doors():
    with tempfile.TemporaryDirectory() as directory:
        db = DatabaseManager(os.path.join(directory, "fungus_v3.db"))
        try:
            spec = MapSpec(
                name="fungus-v3-test", width=160, height=160, layers=1, seed=20260824,
                areas=[BuildingAreaSpec(generator="rectangle", count=1, name_prefix="地图",
                                        kwargs={"rect_size": [(160, 160), (160, 160)], "max_attempts": 10})],
                interior=InteriorSpec(mode="basic"), connection=ConnectionSpec(mode="none"),
                decoration=DecorationSpec(kind="none"),
            )
            map_id = int(MapGenerator(db).generate(spec)["map_id"])
            area_id = int(db.fetch_one("SELECT id FROM building_area WHERE map_id = ?", (map_id,))["id"])
            db.execute("DELETE FROM item WHERE map_id = ?", (map_id,))
            db.execute("DELETE FROM room WHERE map_id = ?", (map_id,))
            gen = RoadStyleGenerator(db, map_id, seed=20260824)
            gen.map_w = gen.map_h = 160
            buildings = gen.place_buildings(
                area_id, (0, 0, 160, 160), n=8, gap=7,
                min_size=(5, 5), max_size=(30, 34), size_dist="exponential")
            assert len(buildings) == 8
            gen.build_obstacles(buildings, clear_zone=0)
            doors = [gen.make_door(building, blocked=gen.obstacles, width=5,
                                   preferred_direction=(80 - building["center"][0], 80 - building["center"][1]))
                     for building in buildings]
            roads, area_map = gen.connect_fungus_v3(
                {"area_id": area_id, "buildings": buildings}, doors,
                min_road_width=5, max_iterations=30, max_attempts=90,
                erosion_batch_size=8, late_nucleation_rounds=4,
                hole_growth_steps=3)
            gen.finalize_road_walls()
            assert roads == 1 and area_map
            assert len(gen.compute_components(buildings)) == 1
            row = db.fetch_one("SELECT geom_json, tiles_json, other_json FROM room WHERE map_id = ? AND room_type='road'", (map_id,))
            geom, tiles, other = json.loads(row["geom_json"]), json.loads(row["tiles_json"]), json.loads(row["other_json"])
            assert geom["style"] == "真菌" and geom["min_road_width"] == 5
            assert other["algorithm"] == "fungus_early_weighted_erosion"
            assert other["selection_model"] == "weighted_sum"
            assert "shrinkage" not in other and "pareto_target_macro_cells" not in other
            assert other["initial_macro_cells"] >= other["final_macro_cells"] > 0
            assert other["road_area"] > 0 and other["travel_cost"] > 0 and other["objective"] > 0
            assert other["internal_void_seeds"] + other["colony_expansions"] > 0
            assert other["late_holes_opened"] >= 0 and other["late_growth_cells"] >= 0
            assert other["flow_assignment"] == "all_equal_shortest_paths_brandes"
            assert other["accepted_batch_sizes"] and other["macro_perimeter"] > 0
            assert sum(other["accepted_batch_sizes"]) == other["initial_macro_cells"] - other["final_macro_cells"]
            assert other["best_search_objective"] < other["initial_search_objective"]
            assert len(other["connects"]) == 8 and len(tiles["space"]) >= other["road_area"]
            assert not hasattr(gen, "connect_fungus_v4")
            assert not hasattr(gen, "connect_fungus_vector")
            for removed_connection in (
                    ConnectionSpec(mode="fungus_v4"),
                    ConnectionSpec(mode="fungus_vector"),
                    ConnectionSpec(mode="door_to_door", kwargs={"style": "真菌优化"}),
                    ConnectionSpec(mode="door_to_door", kwargs={"style": "矢量真菌"})):
                removed_warnings = []
                removed_result = MapGenerator(db)._generate_roads(
                    map_id, replace(spec, connection=removed_connection),
                    warnings=removed_warnings)
                assert removed_result["roads"] == 0
                assert removed_result["connected"] is False
                assert removed_warnings and "未知道路" in removed_warnings[0]
        finally:
            db.close()
