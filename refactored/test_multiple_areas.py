#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本：生成100个不旋转的矩形建筑区
"""

import sys
import os

# 将src目录添加到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src import RectangleBuildingAreaGenerator, DatabaseManager, MapVisualizer

def test_multiple_rectangles():
    """
    测试生成100个不旋转的矩形建筑区
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
            
            # 绘制地图
            fig = visualizer.draw_map(map_name, layer_index=1, show_grid=True, 
                                     show_building_areas=True, show_area_names=False, 
                                     fig_size=(15, 15))
            
            if fig:
                # 保存地图
                output_dir = "multiple_areas_output"
                print(f"保存地图到 {output_dir} 目录...")
                visualizer.save_map(
                    fig, 
                    f"{map_name}_{success_count}个建筑区", 
                    formats=['png', 'pdf'],
                    output_dir=output_dir
                )
                print("✅ 成功保存地图")
                
                # 打开生成的PDF文件
                import os
                import subprocess
                pdf_path = os.path.join(output_dir, f"{map_name}_{success_count}个建筑区.pdf")
                if os.path.exists(pdf_path):
                    print(f"正在打开PDF文件: {pdf_path}")
                    subprocess.Popen([pdf_path], shell=True)
            
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
