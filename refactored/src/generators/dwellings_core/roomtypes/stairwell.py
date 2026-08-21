from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple, Set, List

Cell = Tuple[int, int]
DIR4: Tuple[Cell, ...] = ((1,0), (-1,0), (0,1), (0,-1))

@dataclass(frozen=True)
class StairwellSpec:
    stair: Cell
    landing: Cell
    exit_dir: Cell   # (dx, dy)

def _nbr4(c: Cell) -> List[Cell]:
    x, y = c
    return [(x+dx, y+dy) for dx, dy in DIR4]

def _is_connected(cells: Set[Cell]) -> bool:
    if not cells:
        return True
    stack = [next(iter(cells))]
    seen = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for nb in _nbr4(cur):
            if nb in cells and nb not in seen:
                stack.append(nb)
    return len(seen) == len(cells)

def _weighted_choice(rng, items: List[Cell], weights: List[int]) -> Cell:
    # rng 兼容你当前 rng.py 的接口：只要有 random() 即可
    total = sum(weights)
    if total <= 0:
        return items[rng.randint(0, len(items)-1)]  # 兜底
    r = rng.random() * total
    acc = 0.0
    for it, w in zip(items, weights):
        acc += w
        if r <= acc:
            return it
    return items[-1]

def roll_stairwell_like_js(area: Set[Cell], entrance_landing: Cell, rng) -> Optional[StairwellSpec]:
    # 候选：移除后仍连通 + 不等于 entrance landing
    cand: List[Cell] = []
    wts: List[int] = []

    for c in area:
        if c == entrance_landing:
            continue
        rem = area - {c}
        if not _is_connected(rem):
            continue

        # JS：weight ~ 4 - neighbor_count（越靠边越大）
        ncnt = sum((nb in area) for nb in _nbr4(c))
        wt = max(1, 4 - ncnt)
        cand.append(c)
        wts.append(wt)

    if not cand:
        return None

    stair = _weighted_choice(rng, cand, wts)

    # exit_dir：从四个方向里选一个能落在 area 内的
    dirs = [(dx, dy) for (dx, dy) in DIR4 if (stair[0]+dx, stair[1]+dy) in area]
    if not dirs:
        return None

    exit_dir = dirs[rng.randint(0, len(dirs)-1)]
    landing = (stair[0] + exit_dir[0], stair[1] + exit_dir[1])
    return StairwellSpec(stair=stair, landing=landing, exit_dir=exit_dir)