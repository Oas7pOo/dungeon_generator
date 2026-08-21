from src.generators.dwellings_core.plan import merge_corridors_like_js

def test_merge_only_when_shared_edge_eq_1():
    # 两条走廊只在一条边相触 -> 合并
    c1 = {(0,0), (1,0), (2,0), (3,0)}          # 横向 1x4
    c2 = {(3,1), (3,2), (3,3), (3,4)}          # 纵向 4x1，与 c1 仅共享 (3,0)-(3,1) 一条边
    rooms = [c1, c2]
    out = merge_corridors_like_js(rooms)
    assert len(out) == 1
    assert out[0] == (c1 | c2)

def test_not_merge_when_shared_edge_gt_1():
    # 两条走廊共享两条边（例如并排接触两格） -> 不合并
    c1 = {(0,0), (1,0), (2,0), (3,0)}
    c2 = {(2,1), (3,1), (4,1), (5,1)}          # 这里让它与 c1 共享两条边： (2,0)-(2,1) 和 (3,0)-(3,1)
    rooms = [c1, c2]
    out = merge_corridors_like_js(rooms)
    assert len(out) == 2

def test_exclude_stair_room():
    c1 = {(0,0), (1,0), (2,0), (3,0)}
    stair = {(3,1)}                            # stair room 很可能是 narrow
    rooms = [c1, stair]
    out = merge_corridors_like_js(rooms, stair_room=stair)
    assert len(out) == 2