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
    
    # 创建测试地图（更大尺寸以适配缩放）
    map_id = db_manager.execute(
        "INSERT INTO map (name, width, height) VALUES (?, ?, ?)",
        ("测试地图", 300, 300)
    ).lastrowid
    print(f"✅ 创建测试地图，map_id={map_id}")
    
    # 创建测试建筑区（更大尺寸，200×200细网格，对应20×20粗网格）
    corners = [[50, 50], [250, 50], [250, 250], [50, 250]]
    building_area_id = db_manager.execute(
        "INSERT INTO building_area (map_id, name, layer_start, layer_end, geom_type, center_x, center_y, radius, geom_json, size_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (map_id, "测试建筑区", 1, 1, "rectangle", 150.0, 150.0, None, 
         json.dumps(corners), 
         json.dumps({"width": 200, "height": 200}))
    ).lastrowid
    print(f"✅ 创建测试建筑区，building_area_id={building_area_id}")
    
    # 使用DwellingsHouseDBWriter生成住宅
    writer = DwellingsHouseDBWriter(db_manager)
    result = writer.generate_and_save_dwelling(
        building_area_id=building_area_id,
        seed=42,
        tags_raw=["默认", "机械", "走廊"],
        n_floors=1,
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
    
    # 使用MapVisualizer生成PDF
    print(f"\n📄 生成可视化PDF...")
    visualizer = MapVisualizer(db_manager)
    output_dir = os.path.join(os.path.dirname(__file__), "test")
    os.makedirs(output_dir, exist_ok=True)
    
    pdf_path = visualizer.save_combined_pdf(
        map_id,
        layers=range(1, 2),
        show_grid=True,
        show_building_areas=True,
        show_area_names=True,
        fig_size=(15, 15),
        output_dir=output_dir,
        filename="test_dwellings"
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
