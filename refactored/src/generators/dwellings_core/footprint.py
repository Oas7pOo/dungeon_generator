from __future__ import annotations
from typing import List, Set, Tuple, Optional
from collections import deque

from .shape import Dir, outline_edges, contour2area

Cell = Tuple[int, int]

def _grow_connected_subset(rng: RNG, envelope: Set[Cell], target_n: int) -> Set[Cell]:
    env_list = list(envelope)
    if not env_list:
        return set()

    # 从靠近中心的点开始，形状更“住宅”
    cx, cy = _area_centroid(envelope)
    env_list.sort(key=lambda c: (c[0]+0.5-cx)**2 + (c[1]+0.5-cy)**2)
    start = env_list[0]
    chosen = {start}

    frontier = [start]
    frontier_set = {start}

    while len(chosen) < target_n and frontier:
        cur = frontier[rng.randint(0, len(frontier)-1)]
        # 随机扩张方向
        nbs = _neighbors4(cur[0], cur[1])
        rng.shuffle(nbs)
        added = False
        for nb in nbs:
            if nb in envelope and nb not in chosen:
                chosen.add(nb)
                if nb not in frontier_set:
                    frontier.append(nb)
                    frontier_set.add(nb)
                added = True
                break
        if not added:
            # 这个 frontier 点扩不动了
            frontier_set.discard(cur)
            frontier.remove(cur)

    return chosen


def _is_too_narrow(area: Set[Cell], min_span: int = 3) -> bool:
    if not area:
        return True
    xs = [x for x,_ in area]; ys = [y for _,y in area]
    w = max(xs) - min(xs) + 1
    h = max(ys) - min(ys) + 1
    if w < min_span or h < min_span:
        return True
    # 防止出现 1 格“掐脖子”
    col_counts = {}
    row_counts = {}
    for x,y in area:
        col_counts[x] = col_counts.get(x,0) + 1
        row_counts[y] = row_counts.get(y,0) + 1
    if min(col_counts.values()) <= 1 or min(row_counts.values()) <= 1:
        return True
    return False


def _make_footprint_from_envelope(
    rng: RNG,
    envelope_cells: List[Cell],
    tags: List[str],
) -> List[Cell]:
    envelope = set(envelope_cells)
    size_class = _size_class_from_tags_or_area(tags, len(envelope))
    r0, r1 = _target_ratio_range(size_class)
    target = int(round(len(envelope) * (r0 + (r1 - r0) * rng.random())))

    # 多次尝试，带“太窄就重来”
    best = None
    best_score = -1
    for _ in range(20):
        fp = _grow_connected_subset(rng, envelope, target)
        if not fp:
            continue
        if _is_too_narrow(fp, min_span=3):
            score = len(fp) - 9999
        else:
            score = len(fp)
        if score > best_score:
            best_score = score
            best = fp
        if score >= target:
            break

    return sorted(list(best if best else envelope))

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