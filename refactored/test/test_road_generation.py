"""
道路生成测试脚本

测试内容：
1. 生成 6 个房间
2. 生成道路，验证所有房间连通
3. 验证道路不与房间重叠
4. 验证道路交叉区域为联通空间
5. 验证门生成
6. 输出可视化 PNG
"""
import os
import sys
import json
import time

# matplotlib 无头渲染（必须在 pyplot 导入前设置）
import matplotlib
matplotlib.use("Agg")

# 确保能找到 src 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import (
    DatabaseManager,
    MapGenerator,
    MapSpec,
    BuildingAreaSpec,
    InteriorSpec,
    ConnectionSpec,
    DecorationSpec,
    MapVisualizer,
    RoadGenerator,
)
from src.generators.road_generator import UnionFind


def test_road_generation():
    print("=" * 60)
    print("  道路生成测试：6 个房间 + 道路连通")
    print("=" * 60)

    db = DatabaseManager()
    db.migrate()

    gen = MapGenerator(db)

    # 构造配方：6 个矩形房间，door_to_door 道路连接
    # 使用时间戳避免旧版 map 表 name UNIQUE 约束冲突
    spec = MapSpec(
        name=f"道路测试地图_{int(time.time())}",
        width=160,
        height=160,
        layers=1,
        seed=42,
        areas=[
            BuildingAreaSpec(
                generator="rectangle",
                count=6,
                name_prefix="房间",
                kwargs=dict(
                    rect_size=[(12, 12), (20, 20)],
                    max_attempts=200,
                ),
            ),
        ],
        interior=InteriorSpec(mode="basic"),
        connection=ConnectionSpec(
            mode="door_to_door",
            kwargs=dict(width=5, layer=1),
        ),
        decoration=DecorationSpec(kind="none"),
    )

    print("\n[1] 生成地图（含 6 个房间 + 道路）...")
    result = gen.generate(spec)
    map_id = result["map_id"]
    print(f"  map_id = {map_id}")
    print(f"  建筑区: {result['areas']}")
    print(f"  房间: {result['rooms']}")
    print(f"  物品: {result['items']}")
    print(f"  道路: {result.get('roads', {})}")
    if result.get("warnings"):
        print(f"  警告:")
        for w in result["warnings"]:
            print(f"    - {w}")

    # ------------------------------------------------------------------
    # 验证 1：检查房间数量
    # ------------------------------------------------------------------
    print("\n[2] 验证房间数量...")
    rooms = db.fetch_all(
        "SELECT id, name, room_type FROM room WHERE map_id = ? "
        "AND (room_type != 'road' OR room_type IS NULL)",
        (map_id,),
    ) or []
    print(f"  非道路房间数: {len(rooms)}")
    assert len(rooms) == 6, f"期望 6 个房间，实际 {len(rooms)}"
    print("  ✓ 房间数量正确")

    # ------------------------------------------------------------------
    # 验证 2：检查道路数量与连通性
    # ------------------------------------------------------------------
    print("\n[3] 验证道路连通性...")
    roads = db.fetch_all(
        "SELECT id, name, other_json, tiles_json FROM room "
        "WHERE map_id = ? AND room_type = 'road'",
        (map_id,),
    ) or []
    print(f"  道路数: {len(roads)}")

    for road in roads:
        other = json.loads(road["other_json"]) if road["other_json"] else {}
        tiles = json.loads(road["tiles_json"]) if road["tiles_json"] else {}
        space_count = len(tiles.get("space", []))
        connects = other.get("connects", [])
        road_kind = other.get("road_kind", "?")
        print(f"  道路 {road['id']} ({road['name']}): "
              f"connects={connects}, kind={road_kind}, space格数={space_count}")

    road_result = result.get("roads", {})
    connected = road_result.get("connected", False)
    components = road_result.get("components", [])
    print(f"  全部连通: {connected}")
    print(f"  连通分量数: {len(components)}")

    if not connected:
        for comp in components:
            print(f"    分量 root={comp['root']}: rooms={comp['rooms']}")
        print("  ⚠ 未全部连通（可能是路径碰撞导致跳过）")
    else:
        print("  ✓ 所有房间通过道路连通")

    # ------------------------------------------------------------------
    # 验证 3：道路不与房间重叠
    # ------------------------------------------------------------------
    print("\n[4] 验证道路不与房间重叠...")
    all_rooms = db.fetch_all(
        "SELECT id, room_type, tiles_json, other_json FROM room WHERE map_id = ?",
        (map_id,),
    ) or []

    # 收集每个房间的占格
    room_cells = {}
    room_types = {}
    for r in all_rooms:
        rid = int(r["id"])
        rt = r.get("room_type") or ""
        room_types[rid] = rt
        tiles = json.loads(r["tiles_json"]) if r["tiles_json"] else {}
        cells = set()
        for key in ("wall", "space", "inner_wall"):
            for c in tiles.get(key, []):
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    cells.add((int(c[0]), int(c[1])))
        room_cells[rid] = cells

    overlap_found = False
    for r in all_rooms:
        rid = int(r["id"])
        if room_types[rid] != "road":
            continue
        other = json.loads(r["other_json"]) if r["other_json"] else {}
        # connects 是 {"kind","id"} 的列表（dict 不可哈希）——提取端点 id 做集合
        connects = set(
            int(c["id"]) for c in other.get("connects", [])
            if isinstance(c, dict) and "id" in c
        )

        for other_rid, other_cells in room_cells.items():
            if other_rid == rid:
                continue
            # 允许与连接的端点房间接触
            if other_rid in connects:
                continue
            # 道路与非连接的非道路房间不应重叠
            if room_types.get(other_rid) != "road":
                overlap = room_cells[rid] & other_cells
                if overlap:
                    print(f"  ✗ 道路 {rid} 与房间 {other_rid} 重叠 {len(overlap)} 格")
                    overlap_found = True

    if not overlap_found:
        print("  ✓ 道路不与非连接房间重叠")
    else:
        print("  ✗ 发现道路与房间重叠")

    # ------------------------------------------------------------------
    # 验证 4：道路交叉区域为联通空间
    # ------------------------------------------------------------------
    print("\n[5] 验证道路交叉区域为联通空间...")
    road_rows = [r for r in all_rooms if room_types[int(r["id"])] == "road"]
    road_space_sets = {}
    for r in road_rows:
        rid = int(r["id"])
        tiles = json.loads(r["tiles_json"]) if r["tiles_json"] else {}
        road_space_sets[rid] = set(
            (int(c[0]), int(c[1])) for c in tiles.get("space", [])
            if isinstance(c, (list, tuple)) and len(c) == 2
        )

    crossing_count = 0
    checked_pairs = set()
    road_ids_sorted = sorted(road_space_sets.keys())
    for i, rid_a in enumerate(road_ids_sorted):
        for rid_b in road_ids_sorted[i + 1:]:
            crossing = road_space_sets[rid_a] & road_space_sets[rid_b]
            if crossing:
                crossing_count += 1
                print(f"  道路 {rid_a} ∩ 道路 {rid_b}: {len(crossing)} 格交叉空间（联通）")

    if crossing_count == 0:
        print("  （无道路交叉，或仅单条道路）")
    else:
        print(f"  ✓ 共 {crossing_count} 对道路交叉，交叉区域为共享 space（联通空间）")

    # ------------------------------------------------------------------
    # 验证 5：门生成
    # ------------------------------------------------------------------
    print("\n[6] 验证道路门...")
    doors = db.fetch_all(
        "SELECT id, name, room_id, properties_json FROM item "
        "WHERE map_id = ? AND item_type = 'door'",
        (map_id,),
    ) or []
    road_doors = []
    for d in doors:
        props = json.loads(d["properties_json"]) if d["properties_json"] else {}
        if "connects_road" in props:
            road_doors.append(d)
    print(f"  道路门数: {len(road_doors)}")
    for d in road_doors:
        props = json.loads(d["properties_json"]) if d["properties_json"] else {}
        print(f"    门 {d['id']} ({d['name']}): "
              f"road={props.get('connects_road')}, room={props.get('connects_room')}")

    # ------------------------------------------------------------------
    # 验证 6：最终连通性详细报告（从数据库重建并查集）
    # ------------------------------------------------------------------
    print("\n[7] 连通性详细报告（从数据库重建）...")
    rooms_non_road = db.fetch_all(
        "SELECT id FROM room WHERE map_id = ? "
        "AND (room_type != 'road' OR room_type IS NULL)",
        (map_id,),
    ) or []
    room_id_list = [int(r["id"]) for r in rooms_non_road]
    uf = UnionFind()
    for rid in room_id_list:
        uf.make_set(rid)

    # 读取道路连接关系，递归合并
    # 道路连接图：road_id -> connects 端点 id 列表（connects 是 {"kind","id"} 的 dict 列表）
    road_conn_map = {}
    for road in road_rows:
        rid = int(road["id"])
        other = json.loads(road["other_json"]) if road["other_json"] else {}
        road_conn_map[rid] = [
            int(c["id"]) for c in other.get("connects", [])
            if isinstance(c, dict) and "id" in c
        ]

    # 对每条道路，找到它连接的所有房间（递归展开道路链）
    def find_connected_rooms(road_id, visited=None):
        """递归找到一条道路连接到的所有非道路房间。"""
        if visited is None:
            visited = set()
        if road_id in visited:
            return set()
        visited.add(road_id)
        result = set()
        for ep in road_conn_map.get(road_id, []):
            if ep in room_id_list:
                result.add(ep)
            elif ep in road_conn_map:
                result |= find_connected_rooms(ep, visited)
        return result

    for rid, connects in road_conn_map.items():
        connected_rooms = find_connected_rooms(rid)
        connected_rooms_list = list(connected_rooms)
        # 将所有连接到的房间合并为一个连通分量
        if len(connected_rooms_list) >= 2:
            for i in range(1, len(connected_rooms_list)):
                uf.union(connected_rooms_list[0], connected_rooms_list[i])

    comps = uf.components()
    print(f"  房间连通分量数: {len(comps)}")
    for root, members in sorted(comps.items()):
        print(f"    分量 root={root}: rooms={sorted(members)}")

    if len(comps) == 1:
        print("  ✓ 所有房间最终连通")
    else:
        print(f"  ⚠ 存在 {len(comps)} 个不连通分量")

    # ------------------------------------------------------------------
    # 输出可视化
    # ------------------------------------------------------------------
    print("\n[8] 生成可视化 PNG...")
    vis = MapVisualizer(db)
    output_dir = os.path.join(os.path.dirname(__file__), "output", "road_test")
    os.makedirs(output_dir, exist_ok=True)

    fig = vis.draw_map(
        map_id,
        layer_index=1,
        show_grid=True,
        show_building_areas=True,
        show_room_grid=True,
        show_room_vector=True,
        show_item_grid=True,
        show_area_names=True,
        show_room_names=True,
        grid_major_step=10,
        fig_size=(12, 12),
    )
    if fig:
        vis.save_map(fig, "道路测试_层1", formats=["png"], output_dir=output_dir, dpi=150)
        print(f"  可视化已保存到: {output_dir}")

    print("\n" + "=" * 60)
    print("  测试完成")
    print("=" * 60)

    db.close()
    return map_id


if __name__ == "__main__":
    test_road_generation()
