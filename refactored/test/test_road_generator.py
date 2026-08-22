#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始道路测试（阶段 2）：
- 6 个房间（basic 矩形房间）场景
- RoadGenerator.generate_and_save_roads 使 6 房间全部联通
- 道路为 room_type='road' 特殊房间，connects 记录端点类型（room/road）
- 道路不与房间重叠（外墙门开洞 + 门口格移入道路）
- 交叉区域联通（双方墙在交叉处打通）
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import DatabaseManager, MapGenerator, MapSpec, BuildingAreaSpec, InteriorSpec, RoadGenerator


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _make_six_rooms(db):
    # 尺度参考（用户）：道路宽5、房间约30x40、门半径2 -> 地图需 160x300 级别
    # 这里用 300x240、房间 30x40，保证房间间距足够容纳带宽5与Z形中间段≥15
    spec = MapSpec(
        name="六房道路测试", width=300, height=240, layers=1, seed=7,
        areas=[
            BuildingAreaSpec(
                generator="rectangle", count=6, name_prefix="房",
                kwargs=dict(rect_size=[(30, 40), (40, 50)], max_attempts=80),
            ),
        ],
        interior=InteriorSpec(mode="basic"),
    )
    res = MapGenerator(db).generate(spec)
    return int(res["map_id"])


def _room_tiles(db, map_id):
    rows = db.fetch_all(
        "SELECT id, room_type, tiles_json, other_json FROM room WHERE map_id = ?",
        (map_id,),
    ) or []
    return rows


def test_six_rooms_all_connected():
    path = _tmp_db()
    db = DatabaseManager(path)
    try:
        mid = _make_six_rooms(db)

        # 先确认有 6 个非道路房间
        assert len([r for r in _room_tiles(db, mid) if r["room_type"] != "road"]) == 6

        gen = RoadGenerator(db)
        res = gen.generate_and_save_roads(mid, layer=1, width=5, seed=1)
        print("road result:", res)

        assert res["roads"] > 0, "应生成至少一条道路"
        assert res["connected"] is True, f"6 房间应全部联通: {res['warnings']}"

        # 道路记录端点类型（room/road）
        roads = [r for r in _room_tiles(db, mid) if r["room_type"] == "road"]
        assert len(roads) == res["roads"]
        road_conns = []
        for rd in roads:
            other = json.loads(rd["other_json"]) if rd["other_json"] else {}
            conns = other.get("connects", [])
            assert len(conns) == 2, f"道路应连接两个端点: {conns}"
            for c in conns:
                assert c.get("kind") in ("room", "road"), f"端点需带 kind: {c}"
                assert "id" in c
            assert other.get("width") == 5
            road_conns.append(conns)

        # 路网非链：至少存在一条"道路连道路"（配对道路互联/分支挂到道路）
        has_road_road = any(any(c["kind"] == "road" for c in conns) for conns in road_conns)
        assert has_road_road, f"路网应是网状（含道路互联），实际: {road_conns}"

        # 道路不与任何房间重叠（space ∩ space == ∅）
        # 注意：必须在道路生成**之后**重新查询房间（开门会把门口格从房间 space 移入道路）
        rooms_after = [r for r in _room_tiles(db, mid) if r["room_type"] != "road"]
        room_spaces = {}
        for r in rooms_after:
            tiles = json.loads(r["tiles_json"]) if r["tiles_json"] else {}
            room_spaces[int(r["id"])] = {
                (int(x), int(y)) for (x, y) in tiles.get("space", [])
            }
        for rd in roads:
            tiles = json.loads(rd["tiles_json"]) if rd["tiles_json"] else {}
            road_space = {(int(x), int(y)) for (x, y) in tiles.get("space", [])}
            for rid, rsp in room_spaces.items():
                assert road_space & rsp == set(), f"道路 {rd['id']} 与房间 {rid} 空间重叠"

        # 交叉联通：若两条道路 space 相交，则双方墙不落在对方 space 内
        road_walls = {}
        road_spaces = {}
        for rd in roads:
            tiles = json.loads(rd["tiles_json"]) if rd["tiles_json"] else {}
            road_spaces[int(rd["id"])] = {(int(x), int(y)) for (x, y) in tiles.get("space", [])}
            road_walls[int(rd["id"])] = {(int(x), int(y)) for (x, y) in tiles.get("wall", [])}
        for a in road_spaces:
            for b in road_spaces:
                if a >= b:
                    continue
                if road_spaces[a] & road_spaces[b]:
                    assert road_walls[a] & road_spaces[b] == set(), f"交叉处 {a} 的墙应打通"
                    assert road_walls[b] & road_spaces[a] == set(), f"交叉处 {b} 的墙应打通"
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_road_doors_on_wall():
    """外墙门：门洞格在房间外墙（洞宽=5，洞两端留墙），且生成 door item。"""
    path = _tmp_db()
    db = DatabaseManager(path)
    try:
        mid = _make_six_rooms(db)
        RoadGenerator(db).generate_and_save_roads(mid, layer=1, width=5, seed=2)

        doors = db.fetch_all(
            "SELECT tiles_json FROM item WHERE map_id = ? AND item_type = 'door' "
            "AND properties_json LIKE '%road_entrance%'",
            (mid,),
        ) or []
        assert len(doors) >= 2, "应有道路门（起点/终点外墙门）"
        for d in doors:
            t = json.loads(d["tiles_json"]) if d["tiles_json"] else {}
            wt = t.get("wall_tiles", [])
            assert len(wt) >= 3, "门洞应至少数格宽"
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_orphan_doors_removed():
    """孤立外墙门清理：没连道路/房间的门删除；road_entrance 保留。"""
    path = _tmp_db()
    db = DatabaseManager(path)
    try:
        mid = _make_six_rooms(db)
        # MapGenerator 的 basic 模式 + ItemGenerator 会给每个房间随机一个外墙门（孤立门）
        db.execute(
            "INSERT INTO item (map_id, room_id, name, item_type, layer_start, layer_end, "
            "tiles_json, properties_json) "
            "SELECT ?, id, 'Door_room' || id, 'door', 1, 1, '{}', '{\"opening\":\"door\"}' "
            "FROM room WHERE map_id = ? AND room_type != 'road'",
            (mid, mid),
        )
        before = db.scalar("SELECT COUNT(*) FROM item WHERE map_id=? AND item_type='door'", (mid,))
        assert before >= 6, "应预置 6 个孤立门"

        res = RoadGenerator(db).generate_and_save_roads(mid, layer=1, width=5, seed=3)

        after = db.fetch_all(
            "SELECT properties_json FROM item WHERE map_id = ? AND item_type = 'door'",
            (mid,),
        ) or []
        assert len(after) > 0
        for d in after:
            props = json.loads(d["properties_json"]) if d["properties_json"] else {}
            assert props.get("role") == "road_entrance", f"只应保留道路门: {props}"
        assert res["orphan_doors_removed"] >= 6, "6 个孤立门应被清理"
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
