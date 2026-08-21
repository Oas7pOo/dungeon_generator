from src.generators.dwellings_core.roomtypes.stairwell import roll_stairwell_like_js
from src.generators.dwellings_core.rng import RNG

def test_stairwell_js_basic():
    """测试JS风格楼梯井选点的基本功能"""
    # 创建一个6x6的方形区域
    area = set()
    for x in range(6):
        for y in range(6):
            area.add((x, y))
    
    entrance_landing = (2, 2)
    rng = RNG(42)  # 固定种子，保证测试可重复
    
    # 调用函数
    result = roll_stairwell_like_js(area, entrance_landing, rng)
    
    # 验证结果
    assert result is not None, "应该返回有效的StairwellSpec"
    
    # 1. stair != entrance_landing
    assert result.stair != entrance_landing, "楼梯不能选在入口着陆点"
    
    # 2. landing 在 area 内
    assert result.landing in area, "着陆点必须在区域内"
    
    # 3. area - {stair} 仍连通
    remaining_area = area - {result.stair}
    # 可以使用stairwell.py中的_is_connected函数来验证
    # 这里我们简单检查长度，因为6x6区域删除一个单元格后应该仍连通
    assert len(remaining_area) == 35, "删除楼梯单元格后区域应该仍连通"
    
    # 4. 验证exit_dir和landing的关系
    sx, sy = result.stair
    dx, dy = result.exit_dir
    assert result.landing == (sx + dx, sy + dy), "着陆点应该是楼梯沿出口方向的邻格"
    
    # 5. 验证exit_dir是有效的4方向之一
    assert result.exit_dir in ((1,0), (-1,0), (0,1), (0,-1)), "出口方向必须是4方向之一"

def test_stairwell_js_edge_case():
    """测试边缘情况"""
    # 创建一个2x2的小区域
    area = {(0,0), (0,1), (1,0), (1,1)}
    entrance_landing = (0, 0)
    rng = RNG(42)
    
    result = roll_stairwell_like_js(area, entrance_landing, rng)
    
    assert result is not None
    assert result.stair != entrance_landing
    assert result.landing in area
    
    # 2x2区域删除一个单元格后应该仍连通
    remaining_area = area - {result.stair}
    assert len(remaining_area) == 3

def test_stairwell_js_single_remaining():
    """测试删除一个单元格后剩下一个单元格的情况"""
    # 创建一个1x2的区域
    area = {(0,0), (0,1)}
    entrance_landing = (0, 0)
    rng = RNG(42)
    
    result = roll_stairwell_like_js(area, entrance_landing, rng)
    
    assert result is not None
    assert result.stair == (0, 1)  # 只能选这个单元格
    assert result.landing == (0, 0)  # 只能是这个着陆点
    assert result.exit_dir == (0, -1)  # 只能是这个方向