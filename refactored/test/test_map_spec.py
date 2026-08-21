#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 0 重构测试：
- migrate() 可运行且幂等（补齐 db/migrations）
- MapSpec + MapGenerator 可复现（同 seed 同结果）
- 各主题 preset（villa/village/dungeon/ufo）可完整跑通
- PassabilityIndex 可通行性判定（space 可走 / water 占格不可走）
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import DatabaseManager, MapGenerator, MapSpec, PRESETS, PassabilityIndex

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# 1. 迁移机制
# ---------------------------------------------------------------------------
def test_migrate_creates_tables_and_idempotent():
    path = _tmp_db()
    db = DatabaseManager(path)
    try:
        db.migrate()  # 之前这里会 ImportError（db.migrations 缺失）
        tables = {
            r["name"]
            for r in db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"map", "building_area", "room", "item", "schema_migrations"} <= tables

        # 再次迁移：幂等，不抛异常、不重复执行
        db.migrate()
        cnt = db.scalar("SELECT COUNT(*) FROM schema_migrations WHERE version='v001_init'")
        assert cnt == 1
    finally:
        db.close()
        os.remove(path)


# ---------------------------------------------------------------------------
# 2. MapSpec + MapGenerator 可复现性
# ---------------------------------------------------------------------------
def _map_fingerprint(path, spec):
    db = DatabaseManager(path)
    try:
        res = MapGenerator(db).generate(spec)
        map_id = int(res["map_id"])

        areas = db.fetch_all(
            "SELECT center_x, center_y, size_json, geom_json FROM building_area WHERE map_id = ?",
            (map_id,),
        )
        area_sig = sorted(
            (round(float(a["center_x"]), 4), round(float(a["center_y"]), 4),
             a["size_json"] or "", a["geom_json"] or "")
            for a in areas
        )

        rooms = db.fetch_all(
            "SELECT room_type, geom_json, tiles_json FROM room WHERE map_id = ?",
            (map_id,),
        )
        room_sig = sorted(
            (r["room_type"], r["geom_json"] or "", r["tiles_json"] or "")
            for r in rooms
        )

        items = db.scalar("SELECT COUNT(*) FROM item WHERE map_id = ?", (map_id,))
        return {
            "areas": len(areas),
            "rooms": len(rooms),
            "items": int(items or 0),
            "area_sig": area_sig,
            "room_sig": room_sig,
        }
    finally:
        db.close()


def test_same_seed_same_result():
    spec = PRESETS["village"](seed=42)
    p1, p2 = _tmp_db(), _tmp_db()
    try:
        f1 = _map_fingerprint(p1, spec)
        f2 = _map_fingerprint(p2, spec)
        assert f1 == f2, "同 seed 应产生完全一致的结果"
        assert f1["areas"] >= 2 and f1["rooms"] >= 1
    finally:
        for p in (p1, p2):
            if os.path.exists(p):
                os.remove(p)


def test_different_seed_differs():
    p1, p2 = _tmp_db(), _tmp_db()
    try:
        a = _map_fingerprint(p1, PRESETS["village"](seed=1))
        b = _map_fingerprint(p2, PRESETS["village"](seed=2))
        assert a["area_sig"] != b["area_sig"], "不同 seed 的布局应当不同"
    finally:
        for p in (p1, p2):
            if os.path.exists(p):
                os.remove(p)


# ---------------------------------------------------------------------------
# 3. 主题 preset 跑通
# ---------------------------------------------------------------------------
def test_presets_run():
    for key, factory in PRESETS.items():
        path = _tmp_db()
        db = DatabaseManager(path)
        try:
            res = MapGenerator(db).generate(factory(seed=7))
            assert res["areas"] > 0, f"{key} 应生成至少一个建筑区"
            assert res["rooms"] >= 0
            # 连接/修饰为规划中功能，应带警告而非报错
            assert isinstance(res["warnings"], list)
        finally:
            db.close()
            if os.path.exists(path):
                os.remove(path)


# ---------------------------------------------------------------------------
# 4. PassabilityIndex
# ---------------------------------------------------------------------------
def test_passability_space_walkable_water_blocked():
    path = _tmp_db()
    db = DatabaseManager(path)
    try:
        db.migrate()
        map_id = db.insert_map("通行性测试", 30, 30)

        # 一个 3x3 房间：wall 外圈 + space 内圈
        wall = [[x, y] for x in range(10, 13) for y in range(10, 13) if x in (10, 12) or y in (10, 12)]
        space = [[11, 11]]
        db.execute(
            "INSERT INTO room (map_id, name, layer_start, layer_end, room_type, geom_json, tiles_json, other_json) "
            "VALUES (?, ?, 1, 1, 'rectangle', '{}', ?, '{}')",
            (map_id, "测试房", json.dumps({"wall": wall, "space": space, "inner_wall": []})),
        )

        # 一个水体覆盖 (11,11)
        db.execute(
            "INSERT INTO item (map_id, name, item_type, layer_start, layer_end, tiles_json, properties_json) "
            "VALUES (?, '水', 'water', 1, 1, ?, '{}')",
            (map_id, json.dumps({"wall_tiles": [[11, 11]]})),
        )

        idx = PassabilityIndex(db)
        assert idx.is_walkable(map_id, 1, 11, 11) is False, "水体占格不可通行"
        assert idx.is_walkable(map_id, 1, 11, 12) is False, "墙格不可通行"

        # 房间外任意格：无 room space -> 不可走（默认不可通行）
        assert idx.is_walkable(map_id, 1, 5, 5) is False

        # 无 water 时 space 可走
        idx.invalidate_all()
        db.execute("DELETE FROM item WHERE map_id = ?", (map_id,))
        assert idx.is_walkable(map_id, 1, 11, 11) is True
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# 5. 通用数据层约束：建筑不得超出自己建筑区 / 覆盖其他建筑区（硬扣 tiles）
# ---------------------------------------------------------------------------
def test_clip_rooms_to_areas_hard_rule():
    path = _tmp_db()
    db = DatabaseManager(path)
    try:
        db.migrate()
        map_id = db.insert_map("硬扣测试", 100, 100)

        # 建筑区 A：矩形 (20,20)-(40,40)；建筑区 B：矩形 (50,20)-(70,40)
        db.execute(
            "INSERT INTO building_area (map_id, name, layer_start, layer_end, geom_type, center_x, center_y, size_json) "
            "VALUES (?, 'A', 1, 1, 'rectangle', 30, 30, ?)",
            (map_id, json.dumps({"width": 20, "height": 20})),
        )
        db.execute(
            "INSERT INTO building_area (map_id, name, layer_start, layer_end, geom_type, center_x, center_y, size_json) "
            "VALUES (?, 'B', 1, 1, 'rectangle', 60, 30, ?)",
            (map_id, json.dumps({"width": 20, "height": 20})),
        )
        aid_a = db.scalar("SELECT id FROM building_area WHERE name='A' AND map_id=?", (map_id,))
        aid_b = db.scalar("SELECT id FROM building_area WHERE name='B' AND map_id=?", (map_id,))

        def _insert_room(name, ba_id, cells, wall=None):
            db.execute(
                "INSERT INTO room (map_id, building_area_id, name, layer_start, layer_end, room_type, geom_json, tiles_json, other_json) "
                "VALUES (?, ?, ?, 1, 1, 'test', '{}', ?, '{}')",
                (map_id, ba_id, name, json.dumps({"wall": wall or [], "space": cells, "inner_wall": []})),
            )

        # 房间 R1（属 A）：格 (25,25) 在 A 内（保留）；(45,25) 出 A 未压别人（扣）；
        # (55,25) 在 B 内（扣）；(30,45) 出 A 未压别人（扣）
        # 原墙：单墙模型——(24,25) 在 A 内（保留）；(44,25) 出 A（裁剪掉）
        _insert_room("R1", aid_a, [[25, 25], [45, 25], [55, 25], [30, 45], [26, 26], [27, 26], [26, 27]],
                     wall=[[24, 25], [44, 25]])
        # 房间 R2（无所属建筑区）：(10,10) 空地（保留）；(55,25) 在 B 内（扣）
        _insert_room("R2", None, [[10, 10], [11, 10], [10, 11], [11, 11], [55, 25]])

        gen = MapGenerator(db)
        kept = gen._clip_rooms_to_areas(map_id)
        assert kept == 2, "两个房间都应保留（裁后仍有 >=4 格）"

        def _space_of(name):
            row = db.fetch_one("SELECT tiles_json FROM room WHERE name=? AND map_id=?", (name, map_id))
            t = json.loads(row["tiles_json"])
            return set((x, y) for (x, y) in t["space"])

        s1 = _space_of("R1")
        assert (25, 25) in s1 and (26, 26) in s1 and (27, 26) in s1 and (26, 27) in s1, "A 内格应保留"
        assert (45, 25) not in s1, "超出自己建筑区的格必须硬扣"
        assert (55, 25) not in s1, "覆盖其他建筑区（B）的格必须硬扣"
        assert (30, 45) not in s1, "超出自己建筑区的格必须硬扣"

        s2 = _space_of("R2")
        assert (10, 10) in s2 and (11, 11) in s2, "空地格应保留"
        assert (55, 25) not in s2, "无所属建筑区的房间也不得覆盖其他建筑区"

        # 原墙保留（单墙共享模型）：有效墙格保留、超出建筑区的墙格裁剪掉，不重算
        row = db.fetch_one("SELECT tiles_json FROM room WHERE name='R1' AND map_id=?", (map_id,))
        t = json.loads(row["tiles_json"])
        wall_set = set((x, y) for (x, y) in t["wall"])
        assert (24, 25) in wall_set, "建筑区内的原墙格应保留"
        assert (44, 25) not in wall_set, "超出建筑区的墙格应裁剪掉"
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
