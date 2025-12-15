#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本：生成100个不旋转的矩形建筑区
"""

import sys
import os

# 将项目根目录添加到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src import RectangleBuildingAreaGenerator, DatabaseManager, MapVisualizer

def test_multiple_rectangles():
    """
    测试生成100个不旋转的矩形建筑区
    类型：大量矩形建筑区压力测试
    参数：
    - 建筑区类型：不旋转矩形
    - 数量：100个
    - 尺寸范围：(5, 3) 到 (50, 30)
    - 层数：1层
    - 旋转：False
    - 分布：指数分布
    - 最大尝试次数：150
    - 地图：
        - 名称：小面积地图
        - 尺寸：100x100
    数量：100个不旋转矩形建筑区
    测试目标：
    1. 验证在小面积地图上生成大量矩形建筑区的效率
    2. 验证指数分布在建筑区生成中的应用
    3. 验证大量建筑区生成的成功率
    4. 验证大量建筑区的可视化效果
    生成内容：
    1. 数据库中创建100个不旋转矩形建筑区记录
    2. 在multiple_areas_output目录下生成包含所有建筑区的PNG和PDF文件
    3. 自动打开生成的PDF文件
    """
    print("=== 测试生成100个不旋转的矩形建筑区 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 确保map表中有合适大小的测试数据
    map_name = "小面积地图"
    map_width = 100
    map_height = 100
    
    db_manager.execute(
        "INSERT OR REPLACE INTO map (name, width, height) VALUES (?, ?, ?)",
        (map_name, map_width, map_height)
    )
    
    # 初始化矩形建筑区生成器
    rect_gen = RectangleBuildingAreaGenerator("测试矩形", map_name, 1, db_manager)
    
    # 生成参数
    rect_size = [(5, 3), (50, 30)]  # 保持用户要求的5x3到50x30大小范围
    max_attempts = 150  # 合理的尝试次数，平衡成功率和运行时间
    total_areas = 100  # 生成100个建筑区
    success_count = 0  # 成功生成的数量
    
    # 清空所有建筑区，确保名称唯一性
    db_manager.execute("DELETE FROM building_areas")
    
    print(f"开始生成 {total_areas} 个不旋转的矩形建筑区...")
    print(f"地图尺寸: {map_width}x{map_height}")
    print(f"矩形尺寸范围: {rect_size}")
    print(f"每次尝试次数: {max_attempts}")
    print("=" * 60)
    
    # 开始生成
    for i in range(total_areas):
        # 生成唯一的建筑区名称
        area_name = f"矩形_{i+1}"
        
        # 创建不旋转的矩形建筑区，使用指数分布
        result = rect_gen.create_building_area(
            name=area_name,
            rect_size=rect_size,
            angle=False,  # 不旋转
            dist="exponential",  # 使用指数分布
            max_attempts=max_attempts
        )
        
        if result:
            success_count += 1
            print(f"✅ 成功生成第 {i+1}/{total_areas} 个建筑区: {result[0]['name']}")
        else:
            print(f"❌ 无法生成第 {i+1}/{total_areas} 个建筑区")
        
        # 显示进度
        if (i+1) % 10 == 0:
            print(f"\n进度: {i+1}/{total_areas} (成功: {success_count})")
    
    print("=" * 60)
    print(f"生成完成！")
    print(f"总尝试次数: {total_areas}")
    print(f"成功次数: {success_count}")
    print(f"成功率: {success_count/total_areas:.2%}")
    
    # 绘制并保存地图
    if success_count > 0:
        print("\n正在绘制地图...")
        try:
            visualizer = MapVisualizer(db_manager)
            
            # 保存组合PDF，按照物品层->房间层->建筑区层的顺序
            output_dir = os.path.join(os.path.dirname(__file__), "output")
            os.makedirs(output_dir, exist_ok=True)
            print(f"保存地图到 {output_dir} 目录...")
            combined_pdf_path = visualizer.save_combined_pdf(
                map_name, 
                layers=[1],  # 只有1层
                show_grid=True, 
                show_building_areas=True, 
                show_area_names=False, 
                show_rooms=False,  # 没有生成房间，所以不显示
                fig_size=(15, 15),
                output_dir=output_dir,
                filename="test_multiple_areas"  # 指定文件名与程序名对应
            )
            print(f"✅ 成功保存组合PDF到: {combined_pdf_path}")
            
            # 打开生成的PDF文件
            if os.path.exists(combined_pdf_path):
                print(f"正在打开PDF文件: {combined_pdf_path}")
                import subprocess
                subprocess.Popen([combined_pdf_path], shell=True)
            
            visualizer.close()
            
        except Exception as e:
            print(f"❌ 地图可视化失败: {e}")
            import traceback
            traceback.print_exc()
    
    # 关闭数据库连接
    rect_gen.close()
    
    return success_count

if __name__ == "__main__":
    try:
        success_count = test_multiple_rectangles()
        print(f"\n🎉 测试完成！成功生成 {success_count} 个不旋转的矩形建筑区")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
