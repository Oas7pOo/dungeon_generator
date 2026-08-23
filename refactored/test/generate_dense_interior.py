#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给稠密版地图的"大楼"生成内部小房间：
- 目标：rect_fill_稠密.db 中所有**宽高均 > 10** 的房间
  - 轴对齐矩形：RoomSubdivider.subdivide_room（BSP 轴对齐分割）
  - **旋转矩形：RoomSubdivider.subdivide_rotated_room（按房间自身旋转轴斜向分割）**
- 保留原 303 条道路：分割后把道路门（road_entrance door item）重新挂到对应的
  边缘子房间（含门洞格从子房间墙移除），并把道路 connects 中父房间 id 改指该子房间
输出：rect_fill_稠密_内室.{db,pdf}
"""
import os
import sys
import json
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import DatabaseManager, RoomSubdivider, MapVisualizer

SEED = 20260820
OUT = os.path.join(os.path.dirname(__file__), "output", "rect_fill_map")
SRC = os.path.join(OUT, "rect_fill_稠密.db")
DST = os.path.join(OUT, "rect_fill_稠密_内室.db")
MIN_SIDE = 10          # 宽高均 > 10 才分割
AREA_TARGET = 150      # 每个小房间约 150 格
MAX_N = 40             # 单个大楼最多分割数


def _loads(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def subdivide_big_rooms(db, map_id):
    """分割所有宽高>10的房间（轴对齐 BSP；旋转矩形斜向分割）。
    返回 (分割房间数, 新增子房间数, 重挂门数, 更新道路数)。"""
    rows = db.fetch_all(
        "SELECT id, name, geom_json, tiles_json FROM room WHERE map_id = ? AND room_type='fill_rect'",
        (map_id,),
    )
    candidates = []
    for r in rows:
        g = _loads(r["geom_json"], {})
        w, h = g.get("width", 0), g.get("height", 0)
        if w > MIN_SIDE and h > MIN_SIDE:
            t = _loads(r["tiles_json"], {})
            area = len(t.get("space", []))
            candidates.append((int(r["id"]), r["name"], area, w, h, g.get("type")))
    candidates.sort(key=lambda x: -x[2])
    print(f"待分割大楼: {len(candidates)} 个（旋转矩形用斜向分割）")

    sub = RoomSubdivider(db)
    n_split = n_sub = n_door = n_road = 0
    for rid, name, area, w, h, rtype in candidates:
        # 1) 保存父房间的道路门（subdivide_room 会删除父房间的 item）
        doors = db.fetch_all(
            "SELECT id, name, map_id, layer_start, layer_end, position_x, position_y, "
            "vector_json, tiles_json, properties_json FROM item "
            "WHERE map_id = ? AND room_id = ? AND item_type='door' "
            "AND properties_json LIKE '%road_entrance%'",
            (map_id, rid),
        ) or []

        # 2) 分割：轴对齐用 BSP；旋转矩形用斜向分割
        n = max(2, min(MAX_N, int(round(area / AREA_TARGET))))
        if rtype == "rotated_rectangle":
            sub_ids = sub.subdivide_rotated_room(rid, n, seed=SEED + rid)
        else:
            sub_ids = sub.subdivide_room(rid, n, seed=SEED + rid)
        if not sub_ids:
            print(f"  room{rid} {name}: 分割失败（跳过）")
            continue
        n_split += 1
        n_sub += len(sub_ids)
        print(f"  room{rid} {name}: {w:g}x{h:g} {rtype} area={area} -> {len(sub_ids)} 个子房间")

        # 3) 道路门重挂到包含门洞格的边缘子房间
        sub_walls = {}
        for sid in sub_ids:
            t = _loads(db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (sid,))["tiles_json"], {})
            sub_walls[sid] = {(int(x), int(y)) for (x, y) in (t.get("wall") or [])}
        road_to_sub = {}
        default_sub = sub_ids[0]
        best_hit = -1
        for d in doors:
            cells = {(int(x), int(y)) for (x, y) in (_loads(d["tiles_json"], {}).get("wall_tiles") or [])}
            if not cells:
                continue
            target = max(sub_ids, key=lambda s: len(sub_walls[s] & cells))
            hit = len(sub_walls[target] & cells)
            if hit > best_hit:
                best_hit = hit
                default_sub = target
            # 重挂门：insert 到目标子房间，并把洞格从子房间墙移除
            prop = _loads(d["properties_json"], {})
            road_id = prop.get("road_id")
            new_name = f"{d['name']}_S{target}"
            db.execute(
                "INSERT INTO item (map_id, room_id, building_area_id, name, item_type, "
                "layer_start, layer_end, timestep, position_x, position_y, vector_json, "
                "tiles_json, properties_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d["map_id"], target, None, new_name, "door", d["layer_start"], d["layer_end"],
                 0, d["position_x"], d["position_y"], d["vector_json"], d["tiles_json"],
                 d["properties_json"]),
            )
            n_door += 1
            # 洞格从子房间墙移除
            t = _loads(db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (target,))["tiles_json"], {})
            t["wall"] = [w for w in (t.get("wall") or []) if (int(w[0]), int(w[1])) not in cells]
            db.execute("UPDATE room SET tiles_json = ? WHERE id = ?", (json.dumps(t, ensure_ascii=False), target))
            sub_walls[target] = {(int(x), int(y)) for (x, y) in (t.get("wall") or [])}
            if road_id is not None:
                road_to_sub[int(road_id)] = target
        # 门对应的子房间；未挂到门的道路 -> 默认子房间（命中洞格最多的）
        road_to_sub.setdefault(None, default_sub)

        # 4) 道路 connects 改指目标子房间
        for rd in db.fetch_all("SELECT id, other_json FROM room WHERE map_id = ? AND room_type='road'", (map_id,)):
            o = _loads(rd["other_json"], {})
            conns = o.get("connects", [])
            changed = False
            for c in conns:
                if isinstance(c, dict) and c.get("kind") == "room" and int(c.get("id", -1)) == rid:
                    c["id"] = road_to_sub.get(int(rd["id"]), default_sub)
                    changed = True
            if changed:
                db.execute("UPDATE room SET other_json = ? WHERE id = ?",
                           (json.dumps(o, ensure_ascii=False), rd["id"]))
                n_road += 1
    return n_split, n_sub, n_door, n_road


def main():
    os.makedirs(OUT, exist_ok=True)
    if os.path.exists(DST):
        os.remove(DST)
    shutil.copy(SRC, DST)
    db = DatabaseManager(DST)
    mid = db.fetch_one("SELECT id FROM map ORDER BY id DESC LIMIT 1")["id"]

    n_split, n_sub, n_door, n_road = subdivide_big_rooms(db, mid)
    print(f"分割 {n_split} 个大楼 -> 新增 {n_sub} 个子房间 | 重挂道路门 {n_door} | 更新道路 {n_road}")

    # 连通性：道路 + 内部门（interior door 的 connects_room_ids）
    from src.generators.road_generator import UnionFind
    rooms = db.fetch_all("SELECT id FROM room WHERE map_id = ? AND room_type != 'road'", (mid,))
    uf = UnionFind()
    for r in rooms:
        uf.make_set(int(r["id"]))
    for rd in db.fetch_all("SELECT id, other_json FROM room WHERE map_id = ? AND room_type='road'", (mid,)):
        o = _loads(rd["other_json"], {})
        uf.make_set(int(rd["id"]))
        for c in o.get("connects", []):
            if isinstance(c, dict) and "id" in c:
                uf.union(int(c["id"]), int(rd["id"]))
    for d in db.fetch_all("SELECT properties_json FROM item WHERE map_id = ? AND item_type='door'", (mid,)):
        p = _loads(d["properties_json"], {})
        ids = p.get("connects_room_ids") or []
        if len(ids) >= 2:
            for i in range(1, len(ids)):
                uf.union(int(ids[0]), int(ids[i]))
    roots = {uf.find(int(r["id"])) for r in rooms}
    n_room = len(rooms)
    n_road_row = db.fetch_one("SELECT COUNT(*) c FROM room WHERE map_id = ? AND room_type='road'", (mid,))["c"]
    print(f"房间总数 {n_room} | 道路 {n_road_row} | 全图连通分量 {len(roots)}（应为1）")

    vis = MapVisualizer(db)
    p = vis.save_multi_view_pdf(mid, layers=[1], output_dir=OUT,
                                filename="rect_fill_稠密_内室_seed20260820",
                                fig_size=(12, 16), show_grid=True,
                                show_area_names=True, show_room_names=False)
    print("PDF:", os.path.abspath(p))
    vis.close()
    db.close()


if __name__ == "__main__":
    main()
