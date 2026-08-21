# -*- coding: utf-8 -*-
import sys, os, json
sys.path.insert(0, 'refactored')
from src import DatabaseManager, MapGenerator, PRESETS
from shapely.geometry import Point, Polygon

out = 'refactored/test/output/map_spec_demo'
db_path = os.path.join(out, 'dungeon_only.db')
if os.path.exists(db_path):
    os.remove(db_path)
db = DatabaseManager(db_path)
res = MapGenerator(db).generate(PRESETS['dungeon'](seed=20251220))
print('STAT:', res)
mid = res['map_id']

print('--- item 统计 ---')
for r in db.fetch_all('SELECT item_type, COUNT(*) c FROM item WHERE map_id=? GROUP BY item_type', (mid,)):
    print(' ', r['item_type'], r['c'])

print('--- 房间名中心抽查（center 是否在 space 内）---')
rooms = db.fetch_all('SELECT id, name, geom_json, tiles_json FROM room WHERE map_id=?', (mid,))
bad = 0
for r in rooms[:200]:
    g = json.loads(r['geom_json'])
    t = json.loads(r['tiles_json'])
    sp = set((x, y) for (x, y) in t.get('space', []))
    cx, cy = g.get('center', [None, None])
    if cx is None:
        continue
    cell = (int(cx), int(cy))
    if cell not in sp:
        # 允许中心格取整误差：检查 4 邻
        if not any((cell[0]+dx, cell[1]+dy) in sp for dx, dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1))):
            bad += 1
            print('  BAD', r['id'], r['name'], 'center=', g['center'], 'bbox=', g.get('bbox'))
print('center 不在房间内的房间数:', bad, '/', len(rooms))

db.close()
