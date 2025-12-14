#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本，验证重构后的建筑区生成器功能
"""

import sys
import os

# 将src目录添加到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src import RectangleBuildingAreaGenerator, CircleBuildingAreaGenerator, DatabaseManager, MapVisualizer

def test_rectangle_generator():
    """
    测试矩形建筑区生成器
    """
    print("=== 测试矩形建筑区生成器 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 确保map表中有测试数据
    db_manager.execute(
        "INSERT OR REPLACE INTO map (name, width, height) VALUES (?, ?, ?)",
        ("测试地图", 200, 200)
    )
    
    # 初始化矩形建筑区生成器
    rect_gen = RectangleBuildingAreaGenerator("测试矩形", "测试地图", 1, db_manager)
    
    # 创建普通矩形建筑区
    result = rect_gen.create_building_area(
        rect_size=[(10, 10), (30, 30)],
        max_attempts=10
    )
    
    if result:
        print(f"✅ 成功创建普通矩形建筑区: {result[0]['name']}")
    else:
        print("❌ 无法创建普通矩形建筑区")
    
    # 创建旋转矩形建筑区
    result = rect_gen.create_building_area(
        name="测试旋转矩形",
        rect_size=[(15, 15), (25, 25)],
        angle=[(-30, 30), 45, 90],
        max_attempts=10
    )
    
    if result:
        print(f"✅ 成功创建旋转矩形建筑区: {result[0]['name']}")
    else:
        print("❌ 无法创建旋转矩形建筑区")
    
    # 关闭数据库连接
    rect_gen.close()

def test_circle_generator():
    """
    测试圆塔建筑区生成器
    """
    print("\n=== 测试圆塔建筑区生成器 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 初始化圆塔建筑区生成器
    circle_gen = CircleBuildingAreaGenerator("测试圆塔", "测试地图", 1, db_manager)
    
    # 创建圆塔建筑区
    result = circle_gen.create_building_area(
        radius_range=(5, 15),
        max_attempts=10
    )
    
    if result:
        print(f"✅ 成功创建圆塔建筑区: {result[0]['name']}")
    else:
        print("❌ 无法创建圆塔建筑区")
    
    # 关闭数据库连接
    circle_gen.close()

def test_multi_layer_building():
    """
    测试跨层建筑区生成
    """
    print("\n=== 测试跨层建筑区生成 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 初始化矩形建筑区生成器
    rect_gen = RectangleBuildingAreaGenerator("测试跨层建筑", "测试地图", 1, db_manager)
    
    # 创建跨层矩形建筑区
    result = rect_gen.create_building_area(
        name="测试跨层建筑",
        layer=(1, 3),  # 跨越1-3层
        rect_size=[(20, 20), (40, 40)],
        max_attempts=10
    )
    
    if result:
        print(f"✅ 成功创建跨层建筑区: {result[0]['name']}")
        print(f"   跨层信息: 层{result[0]['min_layer']}至{result[0]['max_layer']}")
    else:
        print("❌ 无法创建跨层建筑区")
    
    # 关闭数据库连接
    rect_gen.close()

def test_map_visualization():
    """
    测试地图可视化和PDF生成
    """
    print("\n=== 测试地图可视化和PDF生成 ===")
    
    try:
        # 初始化数据库管理器
        db_manager = DatabaseManager()
        
        # 初始化地图可视化管理器
        visualizer = MapVisualizer(db_manager)
        
        # 绘制地图
        print("绘制地图...")
        fig = visualizer.draw_map("测试地图", layer_index=1)
        
        if fig:
            print("✅ 成功绘制地图")
            
            # 保存为PNG和PDF格式
            output_dir = "test_output"
            print(f"保存地图到 {output_dir} 目录...")
            visualizer.save_map(
                fig, 
                "测试地图_层1", 
                formats=['png', 'pdf'],
                output_dir=output_dir
            )
            print("✅ 成功保存地图为PNG和PDF格式")
        else:
            print("❌ 无法绘制地图")
        
        # 关闭连接
        visualizer.close()
        
    except ImportError as e:
        if "matplotlib" in str(e) or "shapely" in str(e):
            print(f"⚠️  警告: 缺少可视化依赖库 ({e})，PDF生成功能未测试")
        else:
            raise
    except Exception as e:
        print(f"❌ 地图可视化测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    try:
        test_rectangle_generator()
        test_circle_generator()
        test_multi_layer_building()
        test_map_visualization()
        print("\n🎉 所有测试完成！")
    except Exception as e:
        print(f"\n❌ 测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
