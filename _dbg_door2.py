# -*- coding: utf-8 -*-
"""直接调用 make_door 追踪失败原因。"""
import os, sys, json, math, random

sys.path.insert(0, "refactored")
from src import DatabaseManager
from src.generators.road_style_generator import RoadStyleGenerator, DIRS8

db = DatabaseManager("refactored/test/output/mega_vision_road_map/mega_vision_road.db")
mid = db.fetch_one("SELECT id FROM map ORDER BY id DESC LIMIT 1")["id"]

def _loads(s, d=None):
    return json.loads(s) if s else d

buildings = db.fetch_all("SELECT id, name, geom_json, tiles_json FROM room WHERE map_id = ? AND room_type='building'", (mid,))
gen = RoadStyleGenerator(db, mid, seed=20260824)
gen.map_w = gen.map_h = 1200
gen.build_obstacles([{"id": int(b["id"])} for b in buildings])

for bid in (2, 10):
    b = next(bb for bb in buildings if int(bb["id"]) == bid)
    t = _loads(b["tiles_json"], {})
    space = {(int(x), int(y)) for (x, y) in (t.get("space") or [])}
    wall = [(int(x), int(y)) for (x, y) in (t.get("wall") or [])]
    g = _loads(b["geom_json"], {})
    cx, cy = g["center"]
    print(f"\n=== {b['name']} (id={bid}) {g.get('type')} ===")
    # 复刻 make_door 的候选排序，取前 5 个看失败原因
    def _outward(cell):
        x, y = cell
        free = [d for d in DIRS8 if (x+d[0], y+d[1]) not in space]
        if not free:
            return None
        rx, ry = x + 0.5 - cx, y + 0.5 - cy
        rl = (rx*rx + ry*ry) ** 0.5 or 1.0
        best, bd = free[0], -1e9
        for d in free:
            dot = (d[0]*rx + d[1]*ry) / rl
            if dot > bd:
                bd, best = dot, d
        return best
    cands = [((x, y), _outward((x, y))) for (x, y) in wall if _outward((x, y))]
    rng = random.Random(42)
    ang = rng.uniform(0, 2*math.pi)
    def _adir(d):
        return math.atan2(d[1], d[0])
    cands.sort(key=lambda c: abs((_adir(c[1]) - ang + math.pi) % (2*math.pi) - math.pi))
    for (mid_cell, out_dir) in cands[:5]:
        tangent = (-out_dir[1], out_dir[0])
        line_val = mid_cell[0]*out_dir[0] + mid_cell[1]*out_dir[1]
        run = [mid_cell]
        wall_set = set(wall)
        for sign in (1, -1):
            p = mid_cell
            while True:
                q = (p[0]+tangent[0]*sign, p[1]+tangent[1]*sign)
                if q in wall_set and q[0]*out_dir[0] + q[1]*out_dir[1] == line_val:
                    run.append(q); p = q
                else:
                    break
        run = sorted(set(run))
        probe = all((mid_cell[0]+out_dir[0]*k, mid_cell[1]+out_dir[1]*k) not in gen.building_cells
                    for k in range(2, 6))
        print(f"  候选 {mid_cell} out={out_dir} 线段长 {len(run)} 探测 {probe}")
    res = gen.make_door({"id": bid, "name": b["name"], "center": [cx, cy]}, rng, blocked=gen.building_cells)
    print("  make_door 结果:", "成功" if res else "失败")
db.close()
