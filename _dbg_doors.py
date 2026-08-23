# -*- coding: utf-8 -*-
"""逐个建筑调试 make_door 失败原因。"""
import os, sys, json, math, random

sys.path.insert(0, "refactored")
from src import DatabaseManager
from src.generators.road_style_generator import RoadStyleGenerator, DIRS8

db = DatabaseManager("refactored/test/output/mega_vision_road_map/mega_vision_road.db")
mid = db.fetch_one("SELECT id FROM map ORDER BY id DESC LIMIT 1")["id"]

def _loads(s, d=None):
    return json.loads(s) if s else d

buildings = db.fetch_all("SELECT id, name, geom_json, tiles_json, other_json FROM room WHERE map_id = ? AND room_type='building'", (mid,))
door_rooms = {int(d["room_id"]) for d in db.fetch_all(
    "SELECT room_id FROM item WHERE map_id = ? AND item_type='door' AND properties_json LIKE '%road_entrance%'", (mid,))}

gen = RoadStyleGenerator(db, mid, seed=20260824)
gen.map_w = gen.map_h = 1200
gen.build_obstacles([{"id": int(b["id"])} for b in buildings])

for b in buildings:
    bid = int(b["id"])
    if bid in door_rooms:
        continue
    t = _loads(b["tiles_json"], {})
    space = {(int(x), int(y)) for (x, y) in (t.get("space") or [])}
    wall = [(int(x), int(y)) for (x, y) in (t.get("wall") or [])]
    g = _loads(b["geom_json"], {})
    cx, cy = g["center"]
    # 无候选检查
    cands = 0
    for (x, y) in wall:
        free = [d for d in DIRS8 if (x+d[0], y+d[1]) not in space]
        if free:
            cands += 1
    # 最长直墙段（同线）
    from collections import defaultdict
    lines = defaultdict(int)
    for (x, y) in wall:
        for o in DIRS8:
            if (x+o[0], y+o[1]) not in space:
                lines[(o, x*o[0]+y*o[1])] += 1
                break
    max_run = max(lines.values()) if lines else 0
    # 探测：是否有方向 2-5 格不在建筑内
    probe_ok = 0
    for (x, y) in wall:
        for o in DIRS8:
            if (x+o[0], y+o[1]) not in space:
                if all((x+o[0]*k, y+o[1]*k) not in gen.building_cells for k in range(2, 6)):
                    probe_ok += 1
                break
    print(f"{b['name']} (id={bid}) {g.get('type')} {g.get('width')}x{g.get('height')}: "
          f"墙格 {len(wall)} | 有外向候选 {cands} | 最长同线墙段 {max_run} | 探测通过方向数 {probe_ok}")
db.close()
