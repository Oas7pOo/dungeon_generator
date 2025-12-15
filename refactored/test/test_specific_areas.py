#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本：生成特定建筑区
"""

import sys
import os
import sqlite3

# 将项目根目录添加到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src import RectangleBuildingAreaGenerator, CircleBuildingAreaGenerator, DatabaseManager, MapVisualizer, RoomGenerator, ItemGenerator

def test_specific_areas():
    """
    测试生成特定建筑区
    类型：特定建筑区+房间+门综合测试
    参数：
    - 三层圆塔：
        - 建筑区类型：圆形
        - 数量：1个
        - 半径：5（固定）
        - 层数：1-3层（跨层）
        - 最大尝试次数：200
    - 旋转矩形房间：
        - 建筑区类型：旋转矩形
        - 数量：1个
        - 尺寸：(10, 15)（固定）
        - 旋转角度：45度（固定）
        - 层数：1-2层（跨层）
        - 最大尝试次数：200
    - 随机建筑区：
        - 建筑区类型：矩形
        - 数量：15个
        - 尺寸范围：(5, 3) 到 (50, 30)
        - 层数：1层
        - 分布：指数分布
        - 最大尝试次数：100
    - 地图：
        - 名称：特定建筑区地图
        - 尺寸：100x100
    数量：
    - 建筑区：17个（1个三层圆塔，1个旋转矩形，15个随机矩形）
    - 房间：根据建筑区自动生成
    - 门：根据房间自动生成
    测试目标：
    1. 验证固定参数建筑区的生成功能
    2. 验证跨层建筑区的生成功能
    3. 验证特定旋转角度建筑区的生成功能
    4. 验证指数分布建筑区的生成功能
    5. 验证建筑区生成房间和门的功能
    生成内容：
    1. 数据库中创建建筑区、房间和门记录
    2. 在specific_areas_output目录下生成1-3层地图的PNG和PDF文件
    """
    print("=== 测试生成特定建筑区 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 确保map表中有测试数据
    map_name = "特定建筑区地图"
    map_width = 100
    map_height = 100
    
    # 删除旧数据，包括map、building_areas、room和item表中的数据
    print("正在清理旧数据...")
    try:
        # 尝试删除item表数据（如果存在）
        db_manager.execute("DELETE FROM item")
    except sqlite3.OperationalError:
        pass  # 表不存在，忽略
    
    try:
        # 尝试删除room表数据（如果存在）
        db_manager.execute("DELETE FROM room")
    except sqlite3.OperationalError:
        pass  # 表不存在，忽略
    
    try:
        # 删除旧建筑区数据
        db_manager.execute("DELETE FROM building_areas")
    except sqlite3.OperationalError:
        pass  # 表不存在，忽略
    
    try:
        # 删除旧地图数据
        db_manager.execute("DELETE FROM map")
    except sqlite3.OperationalError:
        pass  # 表不存在，忽略
    
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
            print(f"   - 层 {area['min_layer']}至{area['max_layer']}: {area['name']}，位置: {area['position']}")
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
    
    # 使用ItemGenerator生成门
    print("\n4. 为所有房间生成门...")
    item_gen = ItemGenerator(db_manager)
    door_count = item_gen.generate_and_save_doors(map_name)
    print(f"✅ 成功为 {door_count} 个房间生成了门")
    
    # 绘制并保存地图
    print("\n绘制地图...")
    visualizer = MapVisualizer(db_manager)
    
    # 设置输出目录为test/output
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)
    
    # 获取最大层数
    max_layer = 3  # 已知测试用例有3层
    
    # 保存组合PDF，按照物品层->房间层->建筑区层的顺序
    print("\n保存组合PDF（物品层->房间层->建筑区层）...")
    combined_pdf_path = visualizer.save_combined_pdf(
        map_name, 
        layers=range(1, max_layer + 1),
        show_grid=True, 
        show_building_areas=True, 
        show_area_names=True,
        show_rooms=True,  # 显示房间
        fig_size=(12, 12),
        output_dir=output_dir,
        filename="test_specific_areas"  # 指定文件名与程序名对应
    )
    
    print(f"\n✅ 生成的文件位于: {output_dir} 目录")
    print(f"   - 组合PDF: {os.path.basename(combined_pdf_path)}")
    
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
