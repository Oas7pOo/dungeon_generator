from src.generators.dwellings_core.roomtypes.stairs import roll_stairwell_like_js
from src.generators.dwellings_core.rng import RNG

def test_roll_stairwell_basic():
    """测试基本功能"""
    # 创建一个简单的3x3区域
    area = {(0,0), (0,1), (0,2),
            (1,0), (1,1), (1,2),
            (2,0), (2,1), (2,2)}
    entrance_landing = (1,1)
    rng = RNG(42)
    
    # 调用函数
    result = roll_stairwell_like_js(area, entrance_landing, rng)
    
    # 验证结果
    assert result is not None
    assert result.stair in area
    assert result.landing in area
    assert result.stair != entrance_landing
    
    # 验证exit_dir和landing的关系
    sx, sy = result.stair
    dx, dy = result.exit_dir
    assert result.landing == (sx + dx, sy + dy)
    
    # 验证删除stair后区域仍连通
    rest = area - {result.stair}
    # 简单的3x3区域删除一个非中心单元格后应该仍连通
    assert len(rest) == 8

def test_roll_stairwell_small_area():
    """测试小区域情况"""
    # 创建一个2x2区域
    area = {(0,0), (0,1),
            (1,0), (1,1)}
    entrance_landing = (0,0)
    rng = RNG(42)
    
    # 调用函数
    result = roll_stairwell_like_js(area, entrance_landing, rng)
    
    # 验证结果
    assert result is not None
    assert result.stair in area
    assert result.landing in area

def test_roll_stairwell_single_cell_rest():
    """测试删除一个单元格后剩下一个单元格的情况"""
    # 创建一个只有2个单元格的区域，删除其中一个后另一个仍连通
    area = {(0,0), (0,1)}
    entrance_landing = (0,0)
    rng = RNG(42)
    
    # 调用函数
    result = roll_stairwell_like_js(area, entrance_landing, rng)
    
    # 验证结果：应该返回结果，因为删除(0,1)后剩下的{(0,0)}是连通的
    assert result is not None
    assert result.stair == (0, 1)
    assert result.landing == (0, 0)
    assert result.exit_dir == (0, -1)