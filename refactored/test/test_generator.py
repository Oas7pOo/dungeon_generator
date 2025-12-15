#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本，验证重构后的建筑区生成器功能
"""

import sys
import os

# 将项目根目录添加到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src import RectangleBuildingAreaGenerator, CircleBuildingAreaGenerator, DatabaseManager, MapVisualizer

def test_rectangle_generator():
    """
    测试矩形建筑区生成器
    类型：建筑区生成测试
    参数：
    - 普通矩形：
        - 建筑区类型：矩形
        - 尺寸范围：(10, 10) 到 (30, 30)
        - 地图：测试地图
        - 最大尝试次数：10
    - 旋转矩形：
        - 建筑区类型：旋转矩形
        - 尺寸范围：(15, 15) 到 (25, 25)
        - 旋转角度：(-30, 30)、45、90
        - 地图：测试地图
        - 最大尝试次数：10
    数量：2个矩形建筑区（1个普通，1个旋转）
    测试目标：验证普通矩形和旋转矩形建筑区的生成功能
    生成内容：在数据库中创建普通矩形和旋转矩形建筑区记录
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
    类型：建筑区生成测试
    参数：
    - 建筑区类型：圆塔
    - 半径范围：5到15
    - 地图：测试地图
    - 最大尝试次数：10
    数量：1个圆塔建筑区
    测试目标：验证圆塔建筑区的生成功能
    生成内容：在数据库中创建圆塔建筑区记录
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
    类型：建筑区生成测试
    参数：
    - 建筑区类型：矩形
    - 尺寸范围：(20, 20) 到 (40, 40)
    - 层数：1-3层（跨层）
    - 地图：测试地图
    - 最大尝试次数：10
    数量：1个跨层建筑区
    测试目标：验证跨越多个楼层的建筑区生成功能
    生成内容：在数据库中创建跨层建筑区记录（跨越1-3层）
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
    类型：地图可视化测试
    参数：
    - 地图：测试地图
    - 图层：1层
    - 输出格式：PNG、PDF
    - 输出目录：test_output
    - 地图尺寸：(12, 12)英寸
    数量：1张地图（包含建筑区信息）
    测试目标：验证地图可视化和多格式保存功能
    生成内容：在test_output目录下生成"测试地图_层1.png"和"测试地图_层1.pdf"文件
    """
    print("\n=== 测试地图可视化和PDF生成 ===")
    
    try:
        # 初始化数据库管理器
        db_manager = DatabaseManager()
        
        # 初始化地图可视化管理器
        visualizer = MapVisualizer(db_manager)
        
        # 保存组合PDF，按照物品层->房间层->建筑区层的顺序
        output_dir = os.path.join(os.path.dirname(__file__), "output")
        os.makedirs(output_dir, exist_ok=True)
        print(f"保存地图到 {output_dir} 目录...")
        combined_pdf_path = visualizer.save_combined_pdf(
            "测试地图", 
            layers=range(1, 4),  # 3层
            show_grid=True, 
            show_building_areas=True, 
            show_area_names=True,
            show_rooms=True,  # 显示房间
            fig_size=(12, 12),
            output_dir=output_dir,
            filename="test_generator"  # 指定文件名与程序名对应
        )
        print(f"✅ 成功保存组合PDF到: {combined_pdf_path}")
        
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
