from __future__ import annotations
from typing import List, Set, Tuple, Optional
from collections import deque

from .shape import Dir, outline_edges, contour2area

Cell = Tuple[int, int]


def _is_connected(area: Set[Cell]) -> bool:
    if not area:
        return False
    start = next(iter(area))
    q = deque([start])
    seen = {start}
    while q:
        x, y = q.popleft()
        for d in (Dir.NORTH, Dir.EAST, Dir.SOUTH, Dir.WEST):
            nb = (x + d.dx, y + d.dy)
            if nb in area and nb not in seen:
                seen.add(nb)
                q.append(nb)
    return len(seen) == len(area)


def _boundary_cells(area: Set[Cell]) -> List[Cell]:
    out = []
    for (x, y) in area:
        for d in (Dir.NORTH, Dir.EAST, Dir.SOUTH, Dir.WEST):
            if (x + d.dx, y + d.dy) not in area:
                out.append((x, y))
                break
    return out


def _missing_dirs(area: Set[Cell], c: Cell) -> List[Dir]:
    x, y = c
    miss = []
    for d in (Dir.NORTH, Dir.EAST, Dir.SOUTH, Dir.WEST):
        if (x + d.dx, y + d.dy) not in area:
            miss.append(d)
    return miss


def get_notch_cut(
    area: Set[Cell],
    rng,
    *,
    notch_width_range: Tuple[int, int] = (2, 5),
    notch_depth_range: Tuple[int, int] = (2, 6),
) -> Optional[Set[Cell]]:
    boundary = _boundary_cells(area)
    if not boundary:
        return None
    anchor = rng.choice(boundary)

    miss_dirs = _missing_dirs(area, anchor)
    if not miss_dirs:
        return None

    outward = rng.choice(miss_dirs)
    inward = outward.op

    w = rng.randint(notch_width_range[0], notch_width_range[1])
    d = rng.randint(notch_depth_range[0], notch_depth_range[1])
    side = rng.choice([inward.cw, inward.ccw])

    cut: Set[Cell] = set()
    ax, ay = anchor
    for dd in range(d):
        bx, by = ax + inward.dx * dd, ay + inward.dy * dd
        for ww in range(w):
            cx, cy = bx + side.dx * ww, by + side.dy * ww
            cut.add((cx, cy))
    return cut


def apply_notch_if_valid(
    area: Set[Cell],
    cut: Set[Cell],
    *,
    min_cells: int = 10,
) -> Optional[Set[Cell]]:
    if not cut:
        return None

    hit = sum(1 for c in cut if c in area)
    # 至少大部分切到房子内部
    if hit < max(2, len(cut) * 2 // 3):
        return None

    new_area = set(area)
    new_area.difference_update(cut)

    if len(new_area) < min_cells:
        return None
    if not _is_connected(new_area):
        return None
    return new_area


def irregularize_area_by_notches(
    area_cells: List[Cell],
    rng,
    *,
    notch_attempts: int = 12,
    notch_width_range: Tuple[int, int] = (2, 5),
    notch_depth_range: Tuple[int, int] = (2, 6),
    max_notches: int = 3,
) -> List[Cell]:
    """
    最小可用：在 footprint 上“咬掉几口”，制造凹口/不规则边
    - 不碰门、不挖洞；只改 footprint 的 area_cells
    - 每次 carving 后做连通性检查，避免把房子咬裂成两半
    """
    area: Set[Cell] = set(tuple(c) for c in area_cells)
    if len(area) < 20:
        return list(area)

    made = 0
    for _ in range(notch_attempts):
        if made >= max_notches:
            break

        cut = get_notch_cut(
            area,
            rng,
            notch_width_range=notch_width_range,
            notch_depth_range=notch_depth_range,
        )
        if not cut:
            continue

        new_area = apply_notch_if_valid(area, cut, min_cells=10)
        if new_area is None:
            continue

        area = new_area
        made += 1

    return list(area)