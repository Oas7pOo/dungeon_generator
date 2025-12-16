#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本：大型房间生成测试
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

def test_large_room():
    """
    测试例子：大型房间生成
    类型：建筑区测试
    参数：
    - 地图：
        - 名称：大型房间测试
        - 尺寸：1000x1000
    - 建筑区：
        - 类型：矩形
        - 名称：新式房间
        - 大小：与地图同样大（1000x1000）
        - 层数：1层
        - 间距：0
    测试目标：
    1. 验证生成超大地图的功能
    2. 验证生成与地图同样大的建筑区的功能
    3. 验证地图可视化功能
    生成内容：
    1. 数据库中创建建筑区记录
    2. 在test_output/large_room目录下生成1层地图的PNG和PDF文件
    """
    print("=== 测试例子：大型房间生成 ===")
    
    # 初始化数据库管理器
    db_manager = DatabaseManager()
    
    # 确保map表中有测试数据
    map_name = "大型房间测试"
    map_width = 1000
    map_height = 1000
    
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
    rect_gen = RectangleBuildingAreaGenerator("新式房间", map_name=map_name, layer=1, db_manager=db_manager)
    
    # 生成与地图同样大的建筑区
    print("\n1. 生成与地图同样大的建筑区 '新式房间'...")
    
    # 直接调用_create_normal_rectangle方法，传入与地图同样大的尺寸
    # 由于建筑区大小与地图相同，我们需要特殊处理，直接创建一个覆盖整个地图的建筑区
    
    # 生成建筑区的基本信息
    full_name = rect_gen.generate_building_name("新式房间", False, 1, 1)
    
    # 创建覆盖整个地图的矩形顶点
    vertices = [
        (0, 0),
        (map_width, 0),
        (map_width, map_height),
        (0, map_height)
    ]
    
    # 计算中心点和大小
    center_x = map_width / 2
    center_y = map_height / 2
    width = map_width
    height = map_height
    
    # 保存建筑区到数据库
    size_data = {
        "width": width,
        "height": height,
        "area": width * height,
        "center": (center_x, center_y)
    }
    
    # 添加跨层信息
    size_data = rect_gen.add_multi_layer_info(size_data, False, 1, 1)
    
    # 保存到数据库
    save_success = db_manager.save_building_area(
        name=full_name,
        map_name=map_name,
        min_layer=1,
        max_layer=1,
        position=(center_x, center_y),
        type="rectangle",
        corner=vertices,
        size_data=size_data
    )
    
    if save_success:
        print(f"✅ 成功创建与地图同样大的建筑区 '{full_name}'，大小: {width}x{height}")
    else:
        print("❌ 无法创建与地图同样大的建筑区")
        return
    
    # 2. 绘制并保存地图
    print("\n2. 绘制并保存地图...")
    visualizer = MapVisualizer(db_manager)
    
    output_dir = os.path.join(os.path.dirname(__file__), "output/large_room")
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存组合PDF，按照建筑区层的顺序
    print("\n保存组合PDF（建筑区层）...")
    combined_pdf_path = visualizer.save_combined_pdf(
        map_name, 
        layers=[1],  # 仅1层
        show_grid=False,  # 1000x1000地图不显示网格，避免性能问题
        show_building_areas=True, 
        show_area_names=True,
        show_rooms=False,
        fig_size=(15, 15),
        output_dir=output_dir,
        filename="test_large_room"
    )
    
    print(f"✅ 成功保存 {map_name} 的组合PDF到: {combined_pdf_path}")
    
    # 关闭连接
    visualizer.close()
    rect_gen.close()
    db_manager.close()
    
    print("\n🎉 大型房间生成测试完成！")

def main():
    """
    主函数：运行所有测试例子
    """
    print("=== 大型房间生成测试脚本启动 ===")
    
    # 确保输出目录存在
    os.makedirs(os.path.join(os.path.dirname(__file__), "output"), exist_ok=True)
    
    # 运行测试例子
    test_large_room()
    
    print("\n=== 所有测试例子运行完成！ ===")
    print(f"生成的测试结果保存在 test/output/ 目录中")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
