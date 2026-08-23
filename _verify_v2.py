# -*- coding: utf-8 -*-
"""核验：1) 门口矢量接到门 + 门外无夹层；2) 交叉口无外墙（保持打通）。"""
import os, sys, json

sys.path.insert(0, "refactored")
from src import DatabaseManager

db = DatabaseManager("refactored/test/output/mega_vision_road_map/mega_vision_road.db")
mid = db.fetch_one("SELECT id FROM map ORDER BY id DESC LIMIT 1")["id"]

def L(s, d=None):
    return json.loads(s) if s else d

b_space = {}
for b in db.fetch_all("SELECT id, tiles_json FROM room WHERE map_id=? AND room_type='building'", (mid,)):
    t = L(b["tiles_json"], {})
    b_space[int(b["id"])] = {(int(x), int(y)) for (x, y) in (t.get("space") or [])}

roads = db.fetch_all("SELECT id, name, tiles_json, geom_json FROM room WHERE map_id=? AND room_type='road'", (mid,))
road_spaces = []
for rd in roads:
    t = L(rd["tiles_json"], {})
    road_spaces.append({(int(x), int(y)) for (x, y) in (t.get("space") or [])})
road_walls = []
for rd in roads:
    t = L(rd["tiles_json"], {})
    road_walls.append({(int(x), int(y)) for (x, y) in (t.get("wall") or [])})

# 1) 每条路矢量 path/curve 是否接到门（首个锚点 = 门洞中心格）
print("== 1) 矢量接到门 ==")
ok_doors = 0
for rd in roads:
    g = L(rd["geom_json"], {})
    curve = g.get("curve", {})
    segs = curve.get("segments") or []
    first = segs[0][0] if segs else None
    o = L(rd["other_json"] if "other_json" in rd else None, {})
db2 = db
# 用门格与首锚点距离检查
doors = db.fetch_all("SELECT room_id, tiles_json FROM item WHERE map_id=? AND item_type='door' AND properties_json LIKE '%road_entrance%'", (mid,))
door_ok = 0
for rd in roads:
    g = L(rd["geom_json"], {})
    segs = (g.get("curve", {}) or {}).get("segments") or []
    if not segs:
        continue
    first = tuple(round(v, 1) for v in segs[0][0])
    for d in doors:
        cells = [(int(x), int(y)) for (x, y) in (L(d["tiles_json"], {}).get("wall_tiles") or [])]
        if any(abs(first[0] - c[0]) <= 1.5 and abs(first[1] - c[1]) <= 1.5 for c in cells):
            door_ok += 1
            break
print(f"  道路曲线首锚点落在门洞中心 ±1.5 格内的道路数: {door_ok}/{len(roads)}（应为全部连接到门的道路）")

# 2) 交叉口：任意路 space 格的 8 邻属于另一路 space 且该格有墙 → 交叉口被墙挡
cross_wall = 0
for i, rd in enumerate(roads):
    t = L(rd["tiles_json"], {})
    space = {(int(x), int(y)) for (x, y) in (t.get("space") or [])}
    wall = {(int(x), int(y)) for (x, y) in (t.get("wall") or [])}
    other = set().union(*[s for j, s in enumerate(road_spaces) if j != i]) if len(road_spaces) > 1 else set()
    for (x, y) in wall:
        if any((x+dx, y+dy) in other for dx, dy in ((1,0),(-1,0),(0,1),(0,-1))):
            cross_wall += 1
print("== 2) 交叉口被墙格挡住:", cross_wall, "（应0）")

# 3) 门洞邻格仍无路墙阻挡
blocked = 0
for d in doors:
    cells = {(int(x), int(y)) for (x, y) in (L(d["tiles_json"], {}).get("wall_tiles") or [])}
    for (x, y) in cells:
        for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
            nxt = (x+dx, y+dy)
            if nxt in b_space.get(int(d["room_id"]), set()):
                continue
            if any(nxt in rw for rw in road_walls):
                blocked += 1
print("门洞邻格被路墙挡住:", blocked, "（应0）")
db.close()
