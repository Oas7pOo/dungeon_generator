#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本：组合测试（房间与门测试 + 建筑区压力测试）
"""

import sys
import os
import json

# 将项目根目录添加到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src import (
    RectangleBuildingAreaGenerator, 
    CircleBuildingAreaGenerator, 
    HexagonBuildingAreaGenerator,
    DatabaseManager, 
    MapVisualizer, 
    RoomGenerator, 
    ItemGenerator
)

def test_room_and_door():
    """
    测试例子1：房间与门测试
    类型：建筑区+房间+门综合测试
    参数：
    - 圆形高塔：
        - 建筑区类型：圆形
        - 数量：2个
        - 半径范围：3-10
        - 层数：3层
        - 最大尝试次数：200
    - 随机角度倾斜矩形：
        - 建筑区类型：旋转矩形
        - 数量：2个
        - 尺寸范围：(5, 5) 到 (15, 20)
        - 层数：1层
        - 随机角度：True
        - 最大尝试次数：200
    - 正六边形塔：
        - 建筑区类型：正六边形
        - 数量：1个
        - 半径：5
        - 层数：2层
        - 最大尝试次数：200
    - 地图：
        - 名称：房间与门测试
        - 尺寸：100x100
    数量：
    - 建筑区：5个（2个圆形高塔，2个倾斜矩形，1个正六边形塔）
    - 房间：根据建筑区自动生成
    - 门：根据房间自动生成
    测试目标：
    1. 验证多种建筑区类型的生成功能
    2. 验证房间生成功能
    3. 验证门生成功能
    4. 验证地图可视化功能
    生成内容：
    1. 数据库中创建建筑区、房间和门记录
    2. 在test_output/room_and_door目录下生成1-3层地图的PNG和PDF文件
    """
    print("=== 测试例子1：房间与门测试 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 确保map表中有测试数据
    map_name = "房间与门测试"
    map_width = 100
    map_height = 100
    
    # 删除旧数据
    print("正在清理旧数据...")
    db_manager.execute("DELETE FROM item")
    db_manager.execute("DELETE FROM room")
    db_manager.execute("DELETE FROM building_areas")
    db_manager.execute("DELETE FROM map")
    print("旧数据清理完成，开始生成新的地图和建筑区...")
    
    # 插入新的地图数据
    db_manager.execute(
        "INSERT INTO map (name, width, height) VALUES (?, ?, ?)",
        (map_name, map_width, map_height)
    )
    
    # 初始化生成器
    room_gen = RoomGenerator(db_manager)
    item_gen = ItemGenerator(db_manager)
    
    # 1. 生成2个最小半径为3，最大半径为10的圆形3层高塔建筑区
    print("\n1. 生成圆形3层高塔建筑区...")
    circle_gen = CircleBuildingAreaGenerator("圆塔", map_name, 1, db_manager)
    
    for i in range(2):
        result = circle_gen.create_building_area(
            name=f"圆塔_{i+1}",
            layer=(1, 3),  # 3层高塔
            radius_range=(3, 10),  # 最小半径3，最大半径10
            max_attempts=200
        )
        
        if result:
            print(f"✅ 成功创建圆塔_{i+1}，共 {len(result)} 层")
        else:
            print(f"❌ 无法创建圆塔_{i+1}")
    
    # 2. 生成2个最大为15x20的随机角度倾斜矩形建筑区
    print("\n2. 生成随机角度倾斜矩形建筑区...")
    rect_gen = RectangleBuildingAreaGenerator("倾斜矩形", map_name, 1, db_manager)
    
    for i in range(2):
        result = rect_gen.create_building_area(
            name=f"倾斜矩形_{i+1}",
            layer=1,  # 单层
            rect_size=[(5, 5), (15, 20)],  # 最大15x20
            angle=True,  # 随机角度
            max_attempts=200
        )
        
        if result:
            print(f"✅ 成功创建倾斜矩形_{i+1}")
        else:
            print(f"❌ 无法创建倾斜矩形_{i+1}")
    
    # 3. 生成1个宽度为5的正六边形2层高塔建筑区
    print("\n3. 生成正六边形2层高塔建筑区...")
    hex_gen = HexagonBuildingAreaGenerator("正六边形塔", map_name, 1, db_manager)
    
    result = hex_gen.create_building_area(
        name="正六边形塔_1",
        layer=(1, 2),  # 2层高塔
        radius_range=(5, 5),  # 固定半径5
        max_attempts=200
    )
    
    if result:
        print(f"✅ 成功创建正六边形塔_1，共 {len(result)} 层")
    else:
        print("❌ 无法创建正六边形塔_1")
    
    # 4. 生成房间
    print("\n4. 为所有建筑区生成房间...")
    success_count = room_gen.generate_and_save_rooms(map_name)
    print(f"✅ 成功为 {success_count} 个建筑区生成了房间")
    
    # 5. 生成门
    print("\n5. 为所有房间生成门...")
    door_count = item_gen.generate_and_save_doors(map_name)
    print(f"✅ 成功为 {door_count} 个房间生成了门")
    
    # 6. 绘制并保存地图
    print("\n6. 绘制并保存地图...")
    visualizer = MapVisualizer(db_manager)
    
    output_dir = os.path.join(os.path.dirname(__file__), "output/room_and_door")
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存组合PDF，按照物品层->房间层->建筑区层的顺序
    print("\n保存组合PDF（物品层->房间层->建筑区层）...")
    combined_pdf_path = visualizer.save_combined_pdf(
        map_name, 
        layers=range(1, 4),  # 3层
        show_grid=True, 
        show_building_areas=True, 
        show_area_names=True,
        show_rooms=True,  # 显示房间
        fig_size=(12, 12),
        output_dir=output_dir,
        filename="test_combined_room_and_door"  # 指定文件名与程序名对应
    )
    
    print(f"✅ 成功保存 {map_name} 的组合PDF到: {combined_pdf_path}")
    
    # 关闭连接
    visualizer.close()
    circle_gen.close()
    rect_gen.close()
    hex_gen.close()
    room_gen.db_manager.close()
    
    print("\n🎉 房间与门测试完成！")

def test_building_area_stress():
    """
    测试例子2：建筑区与多建筑区压力测试
    类型：建筑区压力测试
    参数：
    - 圆形高塔：
        - 建筑区类型：圆形
        - 数量：2个
        - 半径范围：3-10
        - 层数：3层
        - 最大尝试次数：200
    - 随机角度倾斜矩形：
        - 建筑区类型：旋转矩形
        - 数量：2个
        - 尺寸范围：(5, 5) 到 (15, 20)
        - 层数：1层
        - 随机角度：True
        - 最大尝试次数：200
    - 正六边形塔：
        - 建筑区类型：正六边形
        - 数量：1个
        - 半径：5
        - 层数：2层
        - 最大尝试次数：200
    - 大量矩形建筑区：
        - 建筑区类型：矩形
        - 数量：100个
        - 尺寸范围：(3, 4) 到 (40, 60)
        - 层数：1层
        - 最大尝试次数：50
    - 地图：
        - 名称：建筑区与多建筑区压力测试
        - 尺寸：100x100
    数量：
    - 建筑区：105个（5个特殊类型，100个普通矩形）
    测试目标：
    1. 验证大量建筑区生成的性能和稳定性
    2. 验证不同建筑区类型的生成功能
    3. 验证建筑区名称唯一性和冲突处理
    生成内容：
    1. 数据库中创建大量建筑区记录
    2. 在test_output/building_area_stress目录下生成1-3层地图的PNG和PDF文件
    """
    print("\n=== 测试例子2：建筑区与多建筑区压力测试 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 确保map表中有测试数据
    map_name = "建筑区与多建筑区压力测试"
    map_width = 100
    map_height = 100
    
    # 删除旧数据
    print("正在清理旧数据...")
    db_manager.execute("DELETE FROM room")
    db_manager.execute("DELETE FROM building_areas")
    db_manager.execute("DELETE FROM map")
    print("旧数据清理完成，开始生成新的地图和建筑区...")
    
    # 插入新的地图数据
    db_manager.execute(
        "INSERT INTO map (name, width, height) VALUES (?, ?, ?)",
        (map_name, map_width, map_height)
    )
    
    # 初始化生成器
    circle_gen = CircleBuildingAreaGenerator("压力测试圆塔", map_name, 1, db_manager)
    rect_gen = RectangleBuildingAreaGenerator("压力测试矩形", map_name, 1, db_manager)
    hex_gen = HexagonBuildingAreaGenerator("压力测试正六边形塔", map_name, 1, db_manager)
    
    total_success = 0
    
    # 1. 生成2个最小半径为3，最大半径为10的圆形3层高塔建筑区
    print("\n1. 生成圆形3层高塔建筑区...")
    for i in range(2):
        result = circle_gen.create_building_area(
            name=f"压力测试圆塔_{i+1}",
            layer=(1, 3),  # 3层高塔
            radius_range=(3, 10),  # 最小半径3，最大半径10
            max_attempts=200
        )
        
        if result:
            total_success += len(result)
            print(f"✅ 成功创建压力测试圆塔_{i+1}，共 {len(result)} 层")
        else:
            print(f"❌ 无法创建压力测试圆塔_{i+1}")
    
    # 2. 生成2个最大为15x20的随机角度倾斜矩形建筑区
    print("\n2. 生成随机角度倾斜矩形建筑区...")
    for i in range(2):
        result = rect_gen.create_building_area(
            name=f"压力测试倾斜矩形_{i+1}",
            layer=1,  # 单层
            rect_size=[(5, 5), (15, 20)],  # 最大15x20
            angle=True,  # 随机角度
            max_attempts=200
        )
        
        if result:
            total_success += len(result)
            print(f"✅ 成功创建压力测试倾斜矩形_{i+1}")
        else:
            print(f"❌ 无法创建压力测试倾斜矩形_{i+1}")
    
    # 3. 生成1个宽度为5的正六边形2层高塔建筑区
    print("\n3. 生成正六边形2层高塔建筑区...")
    result = hex_gen.create_building_area(
        name="压力测试正六边形塔_1",
        layer=(1, 2),  # 2层高塔
        radius_range=(5, 5),  # 固定半径5
        max_attempts=200
    )
    
    if result:
        total_success += len(result)
        print(f"✅ 成功创建压力测试正六边形塔_1，共 {len(result)} 层")
    else:
        print("❌ 无法创建压力测试正六边形塔_1")
    
    # 4. 生成100个最大为40x60，最小为3x4的矩形建筑区
    print("\n4. 生成100个矩形建筑区（最大40x60，最小3x4）...")
    rect_success = 0
    total_rect = 100
    
    for i in range(total_rect):
        result = rect_gen.create_building_area(
            name=f"压力测试矩形_{i+1}",
            layer=1,  # 单层
            rect_size=[(3, 4), (40, 60)],  # 最大40x60，最小3x4
            max_attempts=50
        )
        
        if result:
            rect_success += len(result)
            total_success += len(result)
        
        # 每10个建筑区显示一次进度
        if (i + 1) % 10 == 0:
            print(f"   已生成 {i+1}/{total_rect} 个矩形建筑区，成功 {rect_success} 个")
    
    print(f"✅ 成功生成 {rect_success}/{total_rect} 个矩形建筑区")
    print(f"\n📊 总成功生成 {total_success} 个建筑区")
    
    # 5. 绘制并保存地图
    print("\n5. 绘制并保存地图...")
    visualizer = MapVisualizer(db_manager)
    
    output_dir = os.path.join(os.path.dirname(__file__), "output/building_area_stress")
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存组合PDF，按照物品层->房间层->建筑区层的顺序
    print("\n保存组合PDF（物品层->房间层->建筑区层）...")
    combined_pdf_path = visualizer.save_combined_pdf(
        map_name, 
        layers=range(1, 4),  # 3层
        show_grid=True, 
        show_building_areas=True, 
        show_area_names=True,
        show_rooms=False,  # 没有生成房间，所以不显示
        fig_size=(15, 15),
        output_dir=output_dir,
        filename="test_combined_building_stress"  # 指定文件名与程序名对应
    )
    
    print(f"✅ 成功保存 {map_name} 的组合PDF到: {combined_pdf_path}")
    
    # 关闭连接
    visualizer.close()
    circle_gen.close()
    rect_gen.close()
    hex_gen.close()
    db_manager.close()
    
    print("\n🎉 建筑区与多建筑区压力测试完成！")

def main():
    """
    主函数：运行所有测试例子
    """
    print("=== 组合测试脚本启动 ===")
    
    # 确保输出目录存在
    os.makedirs(os.path.join(os.path.dirname(__file__), "output"), exist_ok=True)
    
    # 运行测试例子1
    test_room_and_door()
    
    # 运行测试例子2
    test_building_area_stress()
    
    print("\n=== 所有测试例子运行完成！ ===")
    print(f"生成的测试结果保存在 test/output/ 目录中")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
