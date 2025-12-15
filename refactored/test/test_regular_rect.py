#!/usr/bin/env python3
"""
测试规整矩形生成功能
"""

import sys
import os
import numpy as np

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.db.database import DatabaseManager
from src.core.building_area_generator import RectangleBuildingAreaGenerator
from src.visualization.map_visualizer import MapVisualizer

def test_regular_rectangle_generation():
    """测试规整矩形生成功能"""
    print("=== 测试规整矩形生成功能 ===")
    
    # 删除现有数据库文件，确保测试使用全新数据库
    db_path = "dungeon.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"✅ 删除现有数据库文件: {db_path}")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 创建地图
    db_manager.execute("INSERT OR IGNORE INTO map (name, width, height) VALUES (?, ?, ?)", ("测试地图", 200, 200))
    
    # 测试1：默认开启规整矩形生成
    print("\n1. 测试默认开启规整矩形生成:")
    rect_generator = RectangleBuildingAreaGenerator("测试规整矩形", "测试地图", 1, db_manager)
    
    # 生成多个建筑区，统计长宽比分布
    aspect_ratios = []
    square_count = 0
    total_count = 200  # 生成200个建筑区进行统计
    
    for i in range(total_count):
        # 为每个建筑区生成唯一名称
        unique_name = f"测试规整矩形_{i+1}"
        result = rect_generator.create_building_area(
            name=unique_name,
            rect_size=[(5, 5), (30, 30)],
            dist="exponential",
            placement_mode="random",
            max_attempts=5,
            regular_rect=True  # 默认开启
        )
        
        if result:
            # 获取生成的建筑区大小
            building = result[0]
            width = building["width"]
            height = building["height"]
            aspect_ratio = max(width, height) / min(width, height)
            aspect_ratios.append(aspect_ratio)
            
            if width == height:
                square_count += 1
                print(f"   生成正方形: {width}x{height}")
    
    # 统计结果
    avg_aspect_ratio = np.mean(aspect_ratios)
    square_percentage = (square_count / len(aspect_ratios)) * 100 if aspect_ratios else 0
    print(f"   生成建筑区数量: {len(aspect_ratios)}")
    print(f"   平均长宽比: {avg_aspect_ratio:.2f}")
    print(f"   正方形比例: {square_percentage:.1f}% (预期约5%)")
    print(f"   长宽比接近1:1的比例（<1.5）: {sum(1 for ar in aspect_ratios if ar < 1.5) / len(aspect_ratios) * 100:.1f}%")
    
    # 测试2：关闭规整矩形生成
    print("\n2. 测试关闭规整矩形生成:")
    
    # 重置统计数据
    aspect_ratios = []
    square_count = 0
    
    for i in range(total_count):
        # 为每个建筑区生成唯一名称
        unique_name = f"测试普通矩形_{i+1}"
        result = rect_generator.create_building_area(
            name=unique_name,
            rect_size=[(5, 5), (30, 30)],
            dist="exponential",
            placement_mode="random",
            max_attempts=5,
            regular_rect=False  # 关闭规整矩形生成
        )
        
        if result:
            # 获取生成的建筑区大小
            building = result[0]
            width = building["width"]
            height = building["height"]
            aspect_ratio = max(width, height) / min(width, height)
            aspect_ratios.append(aspect_ratio)
            
            if width == height:
                square_count += 1
                print(f"   生成正方形: {width}x{height}")
    
    # 统计结果
    avg_aspect_ratio = np.mean(aspect_ratios)
    square_percentage = (square_count / len(aspect_ratios)) * 100 if aspect_ratios else 0
    print(f"   生成建筑区数量: {len(aspect_ratios)}")
    print(f"   平均长宽比: {avg_aspect_ratio:.2f}")
    print(f"   正方形比例: {square_percentage:.1f}% (预期约0%)")
    print(f"   长宽比接近1:1的比例（<1.5）: {sum(1 for ar in aspect_ratios if ar < 1.5) / len(aspect_ratios) * 100:.1f}%")
    
    # 绘制并保存地图
    print("\n绘制并保存地图...")
    try:
        visualizer = MapVisualizer(db_manager)
        
        output_dir = os.path.join(os.path.dirname(__file__), "output/regular_rect")
        os.makedirs(output_dir, exist_ok=True)
        
        # 保存组合PDF，按照物品层->房间层->建筑区层的顺序
        print("\n保存组合PDF（物品层->房间层->建筑区层）...")
        combined_pdf_path = visualizer.save_combined_pdf(
            "测试地图", 
            layers=[1],  # 只有1层
            show_grid=True, 
            show_building_areas=True, 
            show_area_names=False,
            show_rooms=False,  # 没有生成房间，所以不显示
            fig_size=(15, 15),
            output_dir=output_dir,
            filename="test_regular_rect"  # 指定文件名与程序名对应
        )
        
        print(f"✅ 成功保存规整矩形测试地图到: {combined_pdf_path}")
        visualizer.close()
    except Exception as e:
        print(f"❌ 地图可视化失败: {e}")
    
    db_manager.close()
    return True

def main():
    """主测试函数"""
    print("开始测试规整矩形生成功能...")
    
    # 运行测试
    success = test_regular_rectangle_generation()
    
    print("\n" + "="*50)
    if success:
        print("🎉 规整矩形生成功能测试通过！")
        return 0
    else:
        print("❌ 规整矩形生成功能测试失败，请检查错误信息")
        return 1

if __name__ == "__main__":
    sys.exit(main())
