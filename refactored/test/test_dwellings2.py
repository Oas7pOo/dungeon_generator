#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试DwellingsHouseDBWriter功能
"""

import os
import sys
import json

# 将项目根目录添加到Python搜索路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.db.database import DatabaseManager
from src.generators.dwellings_house_generator import DwellingsHouseDBWriter
from src.visualization.map_visualizer import MapVisualizer


def test_dwellings_house_writer():
    """
    测试DwellingsHouseDBWriter功能
    """
    print("=== 测试DwellingsHouseDBWriter ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 运行数据库迁移，创建所需的表
    print("🧹 运行数据库迁移...")
    try:
        db_manager.migrate()
    except Exception as e:
        print(f"   迁移失败，直接创建表: {e}")
        # 直接创建所需的表
        db_manager.execute("CREATE TABLE IF NOT EXISTS map (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, width INTEGER NOT NULL, height INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        db_manager.execute("CREATE TABLE IF NOT EXISTS building_area (id INTEGER PRIMARY KEY AUTOINCREMENT, map_id INTEGER NOT NULL REFERENCES map(id), name TEXT NOT NULL, layer_start INTEGER NOT NULL, layer_end INTEGER NOT NULL, geom_type TEXT NOT NULL, center_x REAL, center_y REAL, radius REAL, geom_json TEXT, size_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        db_manager.execute("CREATE TABLE IF NOT EXISTS room (id INTEGER PRIMARY KEY AUTOINCREMENT, map_id INTEGER NOT NULL REFERENCES map(id), building_area_id INTEGER NOT NULL REFERENCES building_area(id), name TEXT NOT NULL, layer_start INTEGER NOT NULL, layer_end INTEGER NOT NULL, room_type TEXT DEFAULT 'generic', geom_json TEXT, tiles_json TEXT, area INTEGER, other_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        db_manager.execute("CREATE TABLE IF NOT EXISTS item (id INTEGER PRIMARY KEY AUTOINCREMENT, map_id INTEGER NOT NULL REFERENCES map(id), room_id INTEGER REFERENCES room(id), building_area_id INTEGER REFERENCES building_area(id), name TEXT NOT NULL, item_type TEXT NOT NULL, layer_start INTEGER NOT NULL, layer_end INTEGER NOT NULL, timestep INTEGER DEFAULT 0, position_x REAL, position_y REAL, vector_json TEXT, tiles_json TEXT, properties_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    
    # 清理旧数据
    print("🧹 清理旧数据...")
    # 按依赖顺序删除数据
    try:
        db_manager.execute("DELETE FROM item WHERE map_id IN (SELECT id FROM map WHERE name = '测试地图')")
    except Exception as e:
        print(f"   删除 item 表数据失败: {e}")
    try:
        db_manager.execute("DELETE FROM room WHERE map_id IN (SELECT id FROM map WHERE name = '测试地图')")
    except Exception as e:
        print(f"   删除 room 表数据失败: {e}")
    try:
        db_manager.execute("DELETE FROM building_area WHERE map_id IN (SELECT id FROM map WHERE name = '测试地图')")
    except Exception as e:
        print(f"   删除 building_area 表数据失败: {e}")
    try:
        db_manager.execute("DELETE FROM map WHERE name = '测试地图'")
    except Exception as e:
        print(f"   删除 map 表数据失败: {e}")
    
    # 创建测试地图（更大尺寸以适配缩放）
    map_id = db_manager.execute(
        "INSERT INTO map (name, width, height) VALUES (?, ?, ?)",
        ("测试地图", 300, 300)
    ).lastrowid
    print(f"✅ 创建测试地图，map_id={map_id}")
    
    # 创建测试建筑区（150×150细网格，对应15×15粗网格）
    corners = [[75, 75], [225, 75], [225, 225], [75, 225]]
    building_area_id = db_manager.execute(
        "INSERT INTO building_area (map_id, name, layer_start, layer_end, geom_type, center_x, center_y, radius, geom_json, size_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (map_id, "测试建筑区", 1, 3, "rectangle", 150.0, 150.0, None, 
         json.dumps(corners), 
         json.dumps({"width": 150, "height": 150}))
    ).lastrowid
    print(f"✅ 创建测试建筑区，building_area_id={building_area_id}")
    
    # 使用DwellingsHouseDBWriter生成住宅（多层建筑，测试新功能）
    writer = DwellingsHouseDBWriter(db_manager)
    result = writer.generate_and_save_dwelling(
        building_area_id=building_area_id,
        seed=42,
        tags_raw=["默认", "有机", "走廊", "露台"],
        n_floors=3,  # 3层建筑，测试退台和露台生成
    )
    
    print(f"\n📊 生成结果：")
    print(f"   地图ID: {result['map_id']}")
    print(f"   生成房间数: {result['rooms']}")
    print(f"   生成门数: {result['doors']}")
    print(f"   生成窗数: {result['windows']}")
    print(f"   生成楼梯数: {result['stairs']}")
    print(f"   种子: {result['seed']}")
    print(f"   标签: {result['tags']}")
    
    # 检查生成的房间
    rooms = db_manager.fetch_all(
        "SELECT id, name, room_type FROM room WHERE map_id = ?",
        (map_id,)
    )
    print(f"\n🏠 生成的房间:")
    for room in rooms:
        print(f"   - ID: {room['id']}, 名称: {room['name']}, 类型: {room['room_type']}")
    
    # 检查生成的门
    doors = db_manager.fetch_all(
        "SELECT id, name, position_x, position_y, properties_json FROM item WHERE map_id = ? AND item_type = 'door' LIMIT 3",
        (map_id,)
    )
    print(f"\n🚪 随机检查3个门:")
    for door in doors:
        props = json.loads(door['properties_json'])
        print(f"   - ID: {door['id']}, 名称: {door['name']}")
        print(f"     位置: ({door['position_x']}, {door['position_y']})")
        print(f"     Edge Key: {props['edge_key']}")
        print(f"     关联房间: {props['db_rooms']}")
    
    # 使用MapVisualizer生成PDF，包括所有楼层
    print(f"\n📄 生成可视化PDF...")
    visualizer = MapVisualizer(db_manager)
    output_dir = os.path.join(os.path.dirname(__file__), "test")
    os.makedirs(output_dir, exist_ok=True)
    
    # 查找地图名称
    map_name = '测试地图'
    
    # 使用save_multi_layer_pdf生成多层PDF，每层一页
    pdf_path = visualizer.save_multi_layer_pdf(
        map_id,
        layers=range(1, 4),  # 3层建筑
        show_grid=True,
        show_building_areas=True,
        show_area_names=True,
        fig_size=(15, 15),
        output_dir=output_dir,
        filename="test_dwellings2"
    )
    print(f"✅ 可视化PDF已生成: {pdf_path}")
    
    # 清理测试数据
    print(f"\n🧹 清理测试数据...")
    db_manager.execute("DELETE FROM item WHERE map_id = ?", (map_id,))
    db_manager.execute("DELETE FROM room WHERE map_id = ?", (map_id,))
    db_manager.execute("DELETE FROM building_area WHERE map_id = ?", (map_id,))
    db_manager.execute("DELETE FROM map WHERE id = ?", (map_id,))
    print(f"✅ 测试数据已清理")
    
    print(f"\n🎉 测试完成！")
    return result


if __name__ == "__main__":
    test_dwellings_house_writer()
