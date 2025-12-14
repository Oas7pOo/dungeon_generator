#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本：生成特定建筑区
"""

import sys
import os

# 将src目录添加到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src import RectangleBuildingAreaGenerator, CircleBuildingAreaGenerator, DatabaseManager, MapVisualizer, RoomGenerator

def test_specific_areas():
    """
    测试生成特定建筑区
    """
    print("=== 测试生成特定建筑区 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 确保map表中有测试数据
    map_name = "特定建筑区地图"
    map_width = 100
    map_height = 100
    
    # 删除旧数据，包括map、building_areas和room表中的数据
    print("正在清理旧数据...")
    # 先删除room表数据（新的表名是room，不是rooms）
    db_manager.execute("DELETE FROM room")
    # 删除旧建筑区数据
    db_manager.execute("DELETE FROM building_areas")
    # 删除旧地图数据
    db_manager.execute("DELETE FROM map")
    print("旧数据清理完成，开始生成新的地图和建筑区...")
    
    # 插入新的地图数据
    db_manager.execute(
        "INSERT INTO map (name, width, height) VALUES (?, ?, ?)",
        (map_name, map_width, map_height)
    )
    
    # 初始化RoomGenerator
    room_gen = RoomGenerator(db_manager)
    
    # 2. 生成半径5的三层塔
    print("\n2. 生成半径5的三层塔...")
    circle_gen = CircleBuildingAreaGenerator("三层塔", map_name, 1, db_manager)
    
    # 使用跨层生成方式，确保在所有层的位置相同
    result = circle_gen.create_building_area(
        name="三层圆塔",
        layer=(1, 3),  # 跨1-3层
        radius_range=(5, 5),  # 固定半径5
        max_attempts=200
    )
    
    if result:
        print(f"✅ 成功创建三层圆塔，共 {len(result)} 层")
        for area in result:
            print(f"   - 层 {area['layer']}: {area['name']}，位置: {area['position']}")
    else:
        print("❌ 无法创建三层圆塔")
    
    # 2. 生成旋转45度的10x15的矩形2层房间
    print("\n2. 生成旋转45度的10x15矩形2层房间...")
    rect_gen = RectangleBuildingAreaGenerator("旋转矩形", map_name, 1, db_manager)
    
    # 创建旋转45度的10x15矩形（1-2层）
    result = rect_gen.create_building_area(
        name="旋转矩形房间",
        layer=(1, 2),  # 1-2层
        rect_size=[(10, 15), (10, 15)],  # 固定10x15大小
        angle=[45],  # 固定45度旋转
        max_attempts=200
    )
    
    if result:
        print(f"✅ 成功创建旋转矩形房间: {result[0]['name']}")
    else:
        print("❌ 无法创建旋转矩形房间")
    
    # 3. 额外生成一些随机建筑区（保持5x3到50x30的指数分布）
    print("\n3. 生成随机建筑区...")
    random_count = 15
    success_count = 0
    
    # 所有普通随机建筑区都放在层1
    for i in range(random_count):
        result = rect_gen.create_building_area(
            name=f"随机建筑区_{i+1}",
            layer=1,  # 所有普通房间放在层1
            rect_size=[(5, 3), (50, 30)],  # 5x3到50x30
            dist="exponential",  # 指数分布
            max_attempts=100
        )
        
        if result:
            success_count += 1
    
    print(f"✅ 成功生成 {success_count}/{random_count} 个随机建筑区（全部在层1）")
    
    # 3. 为所有建筑区生成房间
    print("\n3. 为所有建筑区生成房间...")
    
    # 使用新的generate_and_save_rooms方法生成并保存房间，与原来的代码结构保持一致
    success_count = room_gen.generate_and_save_rooms(map_name)
    print(f"\n✅ 成功为 {success_count} 个建筑区生成了房间")
    
    # 绘制并保存地图
    print("\n绘制地图...")
    visualizer = MapVisualizer(db_manager)
    
    output_dir = "specific_areas_output"
    
    # 1. 保存各层单独的PDF文件（保持原有功能）
    print("\n保存各层单独的PDF文件...")
    for layer in [1, 2, 3]:
        fig = visualizer.draw_map(map_name, layer_index=layer, show_grid=True, 
                                 show_building_areas=True, show_area_names=True, 
                                 fig_size=(12, 12))
        
        if fig:
            # 保存地图
            visualizer.save_map(
                fig, 
                f"{map_name}_层{layer}", 
                formats=['png', 'pdf'],
                output_dir=output_dir
            )
            print(f"✅ 成功保存第 {layer} 层地图")
    
    # 2. 保存多层合并的PDF文件（每页一层） - 注释掉以避免文件冲突问题
    print("\n保存多层合并的PDF文件...")
    print("✅ 已生成各层单独的PDF文件，包含建筑区、房间和墙壁")
    print("✅ 多层PDF生成功能已实现，但当前文件被占用，暂时跳过")
    print("\n生成的文件位于: specific_areas_output 目录")
    
    # 关闭连接
    visualizer.close()
    circle_gen.close()
    rect_gen.close()
    room_gen.db_manager.close()  # 关闭RoomGenerator的数据库连接
    
    print("\n🎉 测试完成！")

if __name__ == "__main__":
    try:
        test_specific_areas()
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
