import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import (BuildingAreaSpec, ConnectionSpec, DatabaseManager, DecorationSpec,
                 InteriorSpec, MapGenerator, MapSpec, RoadStyleGenerator)


def test_fungus_v2_optimizes_one_connected_road_region():
    """v2 保存的是一块道路区域，并记录面积--OD 步行目标，而非变宽的路边。"""
    with tempfile.TemporaryDirectory() as directory:
        db = DatabaseManager(os.path.join(directory, "fungus_v2.db"))
        try:
            spec = MapSpec(
                name="fungus-v2-test", width=180, height=180, layers=1, seed=20260824,
                areas=[BuildingAreaSpec(generator="rectangle", count=1, name_prefix="地图",
                                        kwargs={"rect_size": [(180, 180), (180, 180)], "max_attempts": 10})],
                interior=InteriorSpec(mode="basic"), connection=ConnectionSpec(mode="none"),
                decoration=DecorationSpec(kind="none"),
            )
            map_id = int(MapGenerator(db).generate(spec)["map_id"])
            area_id = int(db.fetch_one("SELECT id FROM building_area WHERE map_id = ?", (map_id,))["id"])
            db.execute("DELETE FROM item WHERE map_id = ?", (map_id,))
            db.execute("DELETE FROM room WHERE map_id = ?", (map_id,))

            gen = RoadStyleGenerator(db, map_id, seed=20260824)
            gen.map_w = gen.map_h = 180
            buildings = gen.place_buildings(
                area_id, (0, 0, 180, 180), n=9, gap=7,
                min_size=(5, 5), max_size=(36, 42), size_dist="exponential",
            )
            assert len(buildings) == 9
            gen.build_obstacles(buildings, clear_zone=0)
            center = (90.0, 90.0)
            doors = [gen.make_door(
                building, blocked=gen.obstacles, width=1,
                preferred_direction=(center[0] - building["center"][0], center[1] - building["center"][1]),
            ) for building in buildings]
            roads, area_map = gen.connect_fungus_v2(
                {"area_id": area_id, "buildings": buildings}, doors, candidate_degree=4)
            gen.finalize_road_walls()

            assert roads == 1 and area_map
            assert len(gen.compute_components(buildings)) == 1
            row = db.fetch_one("SELECT geom_json, tiles_json, other_json FROM room WHERE map_id = ? AND room_type='road'", (map_id,))
            geom, tiles, other = (json.loads(row["geom_json"]), json.loads(row["tiles_json"]),
                                  json.loads(row["other_json"]))
            assert geom["type"] == "road_region"
            assert geom["style"] == "电路" and other["style"] == "电路"
            assert other["algorithm"] == "fungus_v2_area_transport"
            assert other["road_area"] > 0 and other["travel_cost"] > 0 and other["objective"] > 0
            assert len(tiles["space"]) >= other["road_area"]
            assert len(other["connects"]) == 9
        finally:
            db.close()
