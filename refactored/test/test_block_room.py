#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试方块房间生成器（适配新版 BlockRoomGenerator：默认空洞=空洞，可选 merge_holes）
并调用新版 MapVisualizer（imshow 掩码渲染 + 五层可控 + 叠放顺序可调）输出 PDF
"""

import os
import sys
import json

# 添加项目根目录到路径（保持与你原脚本一致）
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from src.core.block_room_generator import BlockRoomGenerator
from src.db.database import DatabaseManager


def _import_map_visualizer():
    # 兼容两种导入方式
    try:
        from src import MapVisualizer
        return MapVisualizer
    except Exception:
        from src.core.map_visualizer import MapVisualizer
        return MapVisualizer


def _ensure_clean_db(db_manager: DatabaseManager):
    """清理旧数据（避免旧房间/门影响观察）"""
    print("正在清理旧数据...")
    try:
        db_manager.execute("DELETE FROM item")
    except Exception:
        pass
    db_manager.execute("DELETE FROM room")
    db_manager.execute("DELETE FROM building_areas")
    db_manager.execute("DELETE FROM map")
    print("旧数据清理完成。")


def _create_full_map_building_area(db_manager: DatabaseManager, map_name: str, map_width: int, map_height: int):
    """创建一个与地图同样大的建筑区（用于给房间生成器一个容器）"""
    print("\n生成测试建筑区（覆盖整张地图）...")
    from src.core.building_area_generator import RectangleBuildingAreaGenerator

    rect_gen = RectangleBuildingAreaGenerator("测试建筑区", map_name=map_name, layer=1, db_manager=db_manager)

    result = rect_gen.create_building_area(
        name="新式房间",
        layer=1,
        rect_size=[(map_width, map_height), (map_width, map_height)],
        max_attempts=10
    )

    if result:
        print("✅ 成功创建测试建筑区")
        return True
    else:
        print("❌ 无法创建测试建筑区")
        return False


def _print_room_brief(rooms, max_show=5):
    for i, room in enumerate(rooms[:max_show]):
        print(f"\n房间 {i+1}:")
        print(f"  名称: {room.get('name')}")
        print(f"  类型: {room.get('room_type')}")
        print(f"  面积: {room.get('area')}")
        print(f"  层级: {room.get('min_layer')} - {room.get('max_layer')}")
        print(f"  墙壁数量: {len(json.loads(room.get('wall_grid_list', '[]')))}")
        print(f"  空间数量: {len(json.loads(room.get('space_grid_list', '[]')))}")
        print(f"  内墙壁数量: {len(json.loads(room.get('inner_wall_grid_list', '[]')))}")
        print(f"  门数量: {len(json.loads(room.get('door_grid_list', '[]')))}")


def _stat_flags(rooms):
    has_walls = any(len(json.loads(r.get("wall_grid_list", "[]"))) > 0 for r in rooms)
    has_spaces = any(len(json.loads(r.get("space_grid_list", "[]"))) > 0 for r in rooms)
    has_doors = any(len(json.loads(r.get("door_grid_list", "[]"))) > 0 for r in rooms)
    has_inner_walls = any(len(json.loads(r.get("inner_wall_grid_list", "[]"))) > 0 for r in rooms)

    print(f"\n📊 房间生成统计:")
    print(f"  墙壁生成: {'✅' if has_walls else '❌'}")
    print(f"  空间生成: {'✅' if has_spaces else '❌'}")
    print(f"  门生成: {'✅' if has_doors else '❌'}")
    print(f"  内墙壁生成: {'✅' if has_inner_walls else '❌'}")


def _save_rooms_json(rooms, filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(rooms, f, ensure_ascii=False, indent=2)
    print(f"📄 房间数据已保存到 {filepath}")


def _get_table_columns(db_manager: DatabaseManager, table_name: str):
    """
    读取表结构列名（SQLite）
    PRAGMA table_info(room) -> (cid, name, type, notnull, dflt_value, pk)
    """
    cols = []
    rows = db_manager.fetch_all(f"PRAGMA table_info({table_name})")
    for r in rows:
        if len(r) >= 2:
            cols.append(r[1])
    return cols


def _insert_rooms_to_db(db_manager: DatabaseManager, rooms: list[dict]):
    """
    将 BlockRoomGenerator 返回的 rooms dict 列表写入 room 表
    使用 room 表真实列名做交集插入，避免列名不一致导致报错
    """
    table_cols = set(_get_table_columns(db_manager, "room"))
    if not table_cols:
        raise RuntimeError("无法读取 room 表结构（PRAGMA table_info(room) 返回空）。")

    inserted = 0
    for room in rooms:
        data = {k: v for k, v in room.items() if k in table_cols}

        # 字段兜底：room_type -> type
        if "room_type" not in data and "type" in table_cols and "room_type" in room:
            data["type"] = room["room_type"]

        if not data:
            continue

        cols = list(data.keys())
        placeholders = ",".join(["?"] * len(cols))
        col_sql = ",".join([f'"{c}"' for c in cols])

        sql = f'INSERT INTO room ({col_sql}) VALUES ({placeholders})'
        db_manager.execute(sql, tuple(data[c] for c in cols))
        inserted += 1

    print(f"✅ 已写入 room 表记录数: {inserted}")
    return inserted


def _new_db_manager_like(db_manager: DatabaseManager) -> DatabaseManager:
    """
    创建一个“指向同一数据库文件”的新连接，避免 MapVisualizer.close() 把主连接关掉。
    - 如果你的 DatabaseManager 支持传 db_path，这里会尽量复用；
    - 否则退化为 DatabaseManager()（大部分项目默认会指向同一个 sqlite 文件）
    """
    for attr in ("db_path", "db_file", "db_name", "path"):
        if hasattr(db_manager, attr):
            try:
                p = getattr(db_manager, attr)
                if isinstance(p, str) and p:
                    return DatabaseManager(p)
            except Exception:
                pass
    return DatabaseManager()


def _render_pdf(
    db_manager: DatabaseManager,
    map_name: str,
    output_dir: str,
    filename: str,
    show_grid: bool,
    fig_size=(15, 15),
):
    """
    新版 MapVisualizer 渲染：
    - 关键点：这里用“新的 DatabaseManager 连接”专门给可视化，
      让 visualizer.close() 关它自己的 connection，不影响主 db_manager
    """
    MapVisualizer = _import_map_visualizer()

    vis_db = _new_db_manager_like(db_manager)
    visualizer = MapVisualizer(vis_db)

    os.makedirs(output_dir, exist_ok=True)

    page_specs = [
        {
            "title": "全层(建筑区+房间格子+房间矢量+门格子)",
            "draw_kwargs": {
                "show_building_areas": True,
                "show_room_grid": True,
                "show_room_vector": True,
                "show_item_grid": True,     # 门格子
                "show_item_vector": False,  # 门矢量可选
                "show_area_names": True,
                "show_room_names": False,
                "show_grid": show_grid,
                "layer_order": ["building_areas", "room_grid", "room_vector", "item_grid"],
                "grid_minor_step": 0,
            }
        },
        {
            "title": "仅房间(不画门)",
            "draw_kwargs": {
                "show_building_areas": False,
                "show_room_grid": True,
                "show_room_vector": True,
                "show_item_grid": False,
                "show_item_vector": False,
                "show_area_names": False,
                "show_room_names": False,
                "show_grid": show_grid,
                "layer_order": ["room_grid", "room_vector"],
                "grid_minor_step": 0,
            }
        },
        {
            "title": "仅建筑区",
            "draw_kwargs": {
                "show_building_areas": True,
                "show_room_grid": False,
                "show_room_vector": False,
                "show_item_grid": False,
                "show_item_vector": False,
                "show_area_names": True,
                "show_room_names": False,
                "show_grid": show_grid,
                "layer_order": ["building_areas"],
                "grid_minor_step": 0,
            }
        },
    ]

    try:
        pdf_path = visualizer.save_combined_pdf(
            map_name,
            layers=range(1, 2),
            output_dir=output_dir,
            fig_size=fig_size,
            filename=filename,
            page_specs=page_specs,
            dpi=150,
            tight_bbox=False
        )
        print(f"✅ PDF 已输出: {pdf_path}")
        return pdf_path
    finally:
        # 这里关掉可视化自己的连接，不影响主 db_manager
        try:
            visualizer.close()
        except Exception:
            pass
        try:
            vis_db.close()
        except Exception:
            pass


def test_block_room_generator():
    """
    测试方块房间生成器：
    - Case A: merge_holes=False（默认空洞=空洞）
    - Case B: merge_holes=True（合并封闭空洞到附近房间）
    每个 case 都写库 + 用新版 MapVisualizer 导出 PDF
    """
    print("=== 测试方块房间生成器（新版：merge_holes + 新版可视化 PDF） ===")

    db_manager = DatabaseManager()

    map_name = "方块房间测试地图"
    map_width = 1000
    map_height = 1000

    output_dir = os.path.join(os.path.dirname(__file__), "output", "block_room_test")
    os.makedirs(output_dir, exist_ok=True)

    SHOW_GRID = False
    RUN_CASE_B = True

    _ensure_clean_db(db_manager)

    print("插入新的地图数据...")
    db_manager.execute(
        "INSERT INTO map (name, width, height) VALUES (?, ?, ?)",
        (map_name, map_width, map_height)
    )

    if not _create_full_map_building_area(db_manager, map_name, map_width, map_height):
        return False

    block_gen = BlockRoomGenerator(db_manager)

    gen_kwargs = dict(
        map_name=map_name,
        layer=1,
        target_rooms=60,
        mode="corridor",  # corridor: 先生成过道房间; room_chain: 不生成过道
        door_gap=3,
    )

    cases = [(False, "FALSE")]
    if RUN_CASE_B:
        cases.append((True, "TRUE"))

    for merge_holes, tag in cases:
        print(f"\n=== Case merge_holes={merge_holes} ===")

        # 主连接继续使用，不会被 _render_pdf 关闭
        db_manager.execute("DELETE FROM room")
        try:
            db_manager.execute("DELETE FROM item")
        except Exception:
            pass

        rooms = block_gen.generate_rooms(**gen_kwargs, merge_holes=merge_holes)
        if not rooms:
            print(f"❌ Case {tag} 无法生成方块房间")
            return False

        print(f"✅ Case {tag} 成功生成 {len(rooms)} 个方块房间")
        _print_room_brief(rooms, max_show=5)
        _stat_flags(rooms)

        json_path = os.path.join(output_dir, f"block_rooms_merge_holes_{tag}.json")
        _save_rooms_json(rooms, json_path)

        _insert_rooms_to_db(db_manager, rooms)

        _render_pdf(
            db_manager=db_manager,
            map_name=map_name,
            output_dir=output_dir,
            filename=f"block_room_merge_holes_{tag}",
            show_grid=SHOW_GRID,
            fig_size=(15, 15),
        )

    try:
        db_manager.close()
    except Exception:
        pass

    print(f"\n🎉 测试完成！输出目录: {output_dir}")
    return True


if __name__ == "__main__":
    success = test_block_room_generator()
    if success:
        print("\n🎉 方块房间生成器测试成功！")
        sys.exit(0)
    else:
        print("\n❌ 方块房间生成器测试失败！")
        sys.exit(1)
