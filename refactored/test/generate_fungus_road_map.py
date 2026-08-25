#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真菌道路风格的固定验证地图（300 x 400）。

地图含：一个圆形建筑、一个斜矩形建筑、一个 50 x 60 且带内部分隔墙的建筑，
以及七个不同尺寸的矩形建筑。道路调用 ``connect_fungus``，默认由建筑占地面积
自动推导营养权重，并输出带冗余环路、5--10 宽的加权运输网。
"""
import json
import math
import os
import sys

from shapely.geometry import Point, Polygon

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import (DatabaseManager, MapGenerator, MapSpec, BuildingAreaSpec, InteriorSpec,
                 ConnectionSpec, DecorationSpec, RoadStyleGenerator, RoomSubdivider)


SEED = 20260823
MAP_W, MAP_H = 300, 400
OUT = os.path.join(os.path.dirname(__file__), "output", "fungus_road_map")


def _insert_building(db, map_id, area_id, gen, name, space, geom, inner_wall=None):
    tiles = gen._tiles_of([[x, y] for x, y in space])
    tiles["inner_wall"] = [list(p) for p in (inner_wall or [])]
    cur = db.execute(
        "INSERT INTO room (map_id, building_area_id, name, layer_start, layer_end, room_type, "
        "geom_json, tiles_json, area, other_json) VALUES (?, ?, ?, 1, 1, 'building', ?, ?, ?, ?)",
        (map_id, area_id, name, json.dumps(geom, ensure_ascii=False),
         json.dumps(tiles, ensure_ascii=False), len(space),
         json.dumps({"generator": "fungus_demo"}, ensure_ascii=False)),
    )
    return {"id": int(cur.lastrowid), "name": name, "center": geom["center"], "geom": geom}


def _rectangle(x0, y0, width, height):
    space = {(x, y) for x in range(x0, x0 + width) for y in range(y0, y0 + height)}
    return space, {"type": "rectangle", "bbox": [x0, y0, x0 + width, y0 + height],
                   "center": [x0 + width / 2.0, y0 + height / 2.0],
                   "width": width, "height": height}


def _circle(cx, cy, radius):
    space = {(x, y) for x in range(cx - radius - 1, cx + radius + 2)
             for y in range(cy - radius - 1, cy + radius + 2)
             if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= radius ** 2}
    return space, {"type": "circle", "center": [cx, cy], "radius": radius}


def _rotated(cx, cy, width, height, angle):
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    hw, hh = width / 2.0, height / 2.0
    corners = [(cx + lx * ca - ly * sa, cy + lx * sa + ly * ca)
               for lx, ly in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
    poly = Polygon(corners)
    space = {(x, y) for x in range(int(min(p[0] for p in corners)) - 1, int(max(p[0] for p in corners)) + 2)
             for y in range(int(min(p[1] for p in corners)) - 1, int(max(p[1] for p in corners)) + 2)
             if poly.contains(Point(x + 0.5, y + 0.5))}
    return space, {"type": "rotated_rectangle", "center": [cx, cy], "width": width,
                   "height": height, "angle": angle, "corners": [list(p) for p in corners]}


def generate(output_dir=OUT, render=True):
    os.makedirs(output_dir, exist_ok=True)
    db_path = os.path.join(output_dir, "fungus_road.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DatabaseManager(db_path)
    try:
        # 先用正常地图 API 创建地图和唯一的全图建筑区；随后放入本例的固定建筑。
        spec = MapSpec(
            name="真菌加权路网示例", width=MAP_W, height=MAP_H, layers=1, seed=SEED,
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
        gen.map_w, gen.map_h = MAP_W, MAP_H
        buildings = []
        space, geom = _circle(58, 70, 22)
        buildings.append(_insert_building(db, map_id, area_id, gen, "圆形建筑", space, geom))
        space, geom = _rotated(222, 72, 54, 34, 32)
        buildings.append(_insert_building(db, map_id, area_id, gen, "斜矩形建筑", space, geom))
        space, geom = _rectangle(115, 155, 50, 60)
        # 复用项目的 BSP 分割器：父建筑会被 4 个真实子房间和内部门替换，而非
        # 仅画几条装饰性内墙。路网只选其中一个朝外子房间作整栋建筑的入口。
        parent = _insert_building(db, map_id, area_id, gen, "50x60分割建筑", space, geom)
        split_ids = RoomSubdivider(db).subdivide_room(parent["id"], n_rooms=4, seed=SEED)
        split_rows = db.fetch_all(
            "SELECT id, name, geom_json, tiles_json FROM room WHERE id IN ({})".format(
                ",".join("?" for _ in split_ids)), tuple(split_ids))
        split_rooms = [{"id": int(row["id"]), "name": str(row["name"]),
                        "center": MapGenerator._room_center_fast(row)} for row in split_rows]
        split_access = max(split_rooms, key=lambda room: room["center"][0] + room["center"][1])
        buildings.append(split_access)
        for name, x, y, w, h in [
            ("建筑4", 22, 155, 34, 40), ("建筑5", 222, 155, 40, 42),
            ("建筑6", 35, 282, 44, 32), ("建筑7", 118, 294, 30, 46),
            ("建筑8", 204, 284, 42, 38), ("建筑9", 260, 225, 26, 36),
            ("建筑10", 150, 240, 30, 28),
        ]:
            space, geom = _rectangle(x, y, w, h)
            buildings.append(_insert_building(db, map_id, area_id, gen, name, space, geom))

        # 其余子房间同样是硬障碍，但只有 split_access 是这栋分割建筑的对外入口。
        obstacle_buildings = buildings + [room for room in split_rooms if room["id"] != split_access["id"]]
        # 宽 10 的道路使用半径 5 的带；非门口路段保持在建筑外扩 5 格之外。
        gen.build_obstacles(obstacle_buildings, clear_zone=5)
        doors = gen.make_fungus_doors(buildings, max_width=10, clear_zone=5)
        roads, _ = gen.connect_fungus({"area_id": area_id, "buildings": buildings}, doors,
                                      min_width=5, max_width=10,
                                      weights={split_access["id"]: (50 * 60) ** 0.5})
        gen.finalize_road_walls()

        components = gen.compute_components(buildings)
        road_rows = db.fetch_all("SELECT tiles_json, other_json FROM room WHERE map_id = ? AND room_type = 'road'", (map_id,))
        road_meta = [json.loads(r["other_json"]) for r in road_rows]
        widths = [meta["width"] for meta in road_meta]
        internal_doors = db.scalar(
            "SELECT COUNT(*) FROM item WHERE map_id = ? AND item_type = 'door' "
            "AND properties_json LIKE '%interior%'", (map_id,))
        assert len(buildings) == 10 and len(split_ids) == 4 and internal_doors >= 3
        assert len([d for d in doors if d]) == 10, [b["name"] for b, d in zip(buildings, doors) if d is None]
        cycle_count = sum(meta.get("edge_kind") == "redundant_cycle" for meta in road_meta)
        assert roads >= 9 and cycle_count >= 1 and len(components) == 1, f"真菌网必须连通且至少有一个环: {components}"
        assert widths and all(5 <= w <= 10 for w in widths), widths
        assert all(meta.get("algorithm") == "weighted_transport_mesh_contracted" for meta in road_meta)
        assert any(meta.get("shared_transport") for meta in road_meta), "支路应接入共享主干"
        assert all(meta.get("width_profile", {}).get("endpoint_width") == 5 for meta in road_meta)
        assert any(meta.get("width_profile", {}).get("peak_width", 5) > 5 for meta in road_meta)

        pdf_path = None
        if render:
            pdf_path = gen.render_pdf("fungus_road_seed20260823", output_dir, fig_size=(12, 16))
        return {"db": db_path, "pdf": pdf_path, "roads": roads, "cycles": cycle_count,
                "widths": widths, "components": len(components), "map_id": map_id}
    finally:
        db.close()


if __name__ == "__main__":
    result = generate()
    print("真菌路网:", result)
