from dataclasses import dataclass
from typing import List, Set, Tuple, Union

from ..rng import RNG

Cell = Tuple[int, int]  # (x,y)

@dataclass(frozen=True)
class StairwellSpec:
    stair: Cell
    landing: Cell
    exit_dir: Tuple[int, int]  # (dx, dy)

def _is_connected_4(area: Set[Cell]) -> bool:
    """
    检查区域是否4邻域连通
    使用BFS实现
    """
    if not area:
        return True
    
    start = next(iter(area))
    visited: Set[Cell] = set()
    queue: List[Cell] = [start]
    visited.add(start)
    
    while queue:
        x, y = queue.pop(0)
        for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
            neighbor = (x + dx, y + dy)
            if neighbor in area and neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    
    return len(visited) == len(area)

def weighted_choice(rng: RNG, candidates: List[Cell], weights: List[int]) -> Cell:
    """
    加权随机选择
    """
    total = sum(weights)
    r = rng.random() * total
    cumulative = 0
    for c, w in zip(candidates, weights):
        cumulative += w
        if r < cumulative:
            return c
    return candidates[-1]  # 兜底，理论上不会到这里

def roll_stairwell_like_js(area: Set[Cell], entrance_landing: Cell, rng: RNG) -> Union[StairwellSpec, None]:
    """
    复刻JS的rollStairwell函数
    1. 枚举area里的每个cell d，检查删掉d后area是否仍连通
    2. 把entrance.landing从候选里移除
    3. 权重=4 - (d的四邻里仍在area的数量)，加权随机选stair cell
    4. 从stair的四邻里随机挑一个仍在area的方向作为exit
    5. landing就是stair朝exit的那个邻格
    """
    # 1) candidates: remove one cell still connected
    candidates: List[Cell] = []
    area_list = list(area)
    
    for d in area_list:
        if d == entrance_landing:
            continue
        rest = area - {d}
        if not rest:
            continue
        if _is_connected_4(rest):
            candidates.append(d)
    
    if not candidates:
        return None
    
    # 2) weights: 4 - num_cardinal_neighbors_in_area
    weights = []
    for x, y in candidates:
        n = 0
        for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
            if (x + dx, y + dy) in area:
                n += 1
        weights.append(4 - n)
    
    stair = weighted_choice(rng, candidates, weights)
    
    # 3) choose exit_dir among neighbors that stay in area
    dirs = []
    sx, sy = stair
    for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
        nb = (sx + dx, sy + dy)
        if nb in area:
            dirs.append((dx, dy))
    
    if not dirs:
        return None
    
    exit_dir = rng.choice(dirs)
    landing = (sx + exit_dir[0], sy + exit_dir[1])
    
    return StairwellSpec(stair=stair, landing=landing, exit_dir=exit_dir)