#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本：双层建筑区生成测试
"""

import sys
import os

# 将项目根目录添加到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src import (
    RectangleBuildingAreaGenerator,
    DatabaseManager,
    MapVisualizer
)

def test_two_layer_buildings():
    """
    测试例子：双层建筑区生成
    类型：建筑区压力测试
    参数：
    - 地图：
        - 名称：双层建筑区测试
        - 尺寸：100x100
    - 建筑区：
        - 类型：矩形
        - 第一层：100个1层建筑区
        - 第二层：100个1层建筑区（仅在第二层）
        - 尺寸范围：(5, 5) 到 (20, 20)
        - 间距：0
        - 放置模式：largest_first
    测试目标：
    1. 验证在不同层生成大量建筑区的功能
    2. 验证跨层建筑区生成的性能和稳定性
    3. 验证地图可视化功能
    生成内容：
    1. 数据库中创建大量建筑区记录
    2. 在test_output/2layer_buildings目录下生成1-2层地图的PNG和PDF文件
    """
    print("=== 测试例子：双层建筑区生成 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 确保map表中有测试数据
    map_name = "双层建筑区测试"
    map_width = 100
    map_height = 100
    
    # 删除旧数据
    print("正在清理旧数据...")
    db_manager.execute("DELETE FROM item")
    db_manager.execute("DELETE FROM room")
    db_manager.execute("DELETE FROM building_area")
    db_manager.execute("DELETE FROM map")
    print("旧数据清理完成，开始生成新的地图和建筑区...")
    
    # 插入新的地图数据
    db_manager.execute(
        "INSERT INTO map (name, width, height) VALUES (?, ?, ?)",
        (map_name, map_width, map_height)
    )
    
    # 获取地图ID
    map_id = db_manager.fetch_one("SELECT id FROM map WHERE name = ?", (map_name,))["id"]
    
    total_success = 0
    
    # 生成建筑区的参数
    rect_size = [(5, 5), (20, 20)]
    N = 100
    distance = 0
    enable_resize = True
    placement_mode = "largest_first"
    max_attempts = 50
    
    # 1. 为第一层生成100个1层建筑区
    print("\n1. 为第一层生成100个1层建筑区...")
    layer1_gen = RectangleBuildingAreaGenerator("第一层建筑区", map_id=map_id, layer=1, db_manager=db_manager)
    result_layer1 = layer1_gen.create_building_areas_global(
        rect_size=rect_size,
        N=N,
        distance=distance,
        enable_resize=enable_resize,
        placement_mode=placement_mode,
        max_attempts=max_attempts
    )
    
    layer1_success = len(result_layer1)
    total_success += layer1_success
    print(f"✅ 成功生成 {layer1_success}/{N} 个第一层建筑区")
    
    # 2. 为第二层生成100个1层建筑区（仅在第二层）
    print("\n2. 为第二层生成100个1层建筑区...")
    layer2_gen = RectangleBuildingAreaGenerator("第二层建筑区", map_id=map_id, layer=2, db_manager=db_manager)
    result_layer2 = layer2_gen.create_building_areas_global(
        rect_size=rect_size,
        N=N,
        distance=distance,
        enable_resize=enable_resize,
        placement_mode=placement_mode,
        max_attempts=max_attempts
    )
    
    layer2_success = len(result_layer2)
    total_success += layer2_success
    print(f"✅ 成功生成 {layer2_success}/{N} 个第二层建筑区")
    
    print(f"\n📊 总成功生成 {total_success}/200 个建筑区")
    
    # 3. 绘制并保存地图
    print("\n3. 绘制并保存地图...")
    visualizer = MapVisualizer(db_manager)
    
    output_dir = os.path.join(os.path.dirname(__file__), "output/2layer_buildings")
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存组合PDF，按照建筑区层的顺序
    print("\n保存组合PDF（建筑区层）...")
    combined_pdf_path = visualizer.save_combined_pdf(
        map_id, 
        layers=range(1, 3),  # 1-2层
        show_grid=True, 
        show_building_areas=True, 
        show_area_names=False,
        fig_size=(15, 15),
        output_dir=output_dir,
        filename="test_2layer_buildings"
    )
    
    print(f"✅ 成功保存 map_id={map_id} 的组合PDF到: {combined_pdf_path}")
    
    # 关闭连接
    visualizer.close()
    layer1_gen.close()
    layer2_gen.close()
    db_manager.close()
    
    print("\n🎉 双层建筑区生成测试完成！")

def main():
    """
    主函数：运行所有测试例子
    """
    print("=== 双层建筑区生成测试脚本启动 ===")
    
    # 确保输出目录存在
    os.makedirs(os.path.join(os.path.dirname(__file__), "output"), exist_ok=True)
    
    # 运行测试例子
    test_two_layer_buildings()
    
    print("\n=== 所有测试例子运行完成！ ===")
    print(f"生成的测试结果保存在 test/output/ 目录中")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
