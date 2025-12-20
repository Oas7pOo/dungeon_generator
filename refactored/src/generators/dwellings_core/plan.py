# dwellings_core/plan.py
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple, Dict, Any

from .shape import (
    Cell, Edge, Dir,
    edge_left_cell, edge_right_cell,
    find_edge_by_start, node_ndir2edge, outline_edges,
)
from .rng import RNG
from .specs import Specs


EdgeKey = Tuple[str, int, int]  # ("V", x, y) or ("H", x, y)

def _edge2cell(area: Set[Cell], e: Edge) -> Cell:
    # CW 轮廓：内侧在右；但为了鲁棒，右侧不在 area 就回退左侧
    rc = edge_right_cell(e)
    if rc in area:
        return rc
    return edge_left_cell(e)


def is_narrow(area: Set[Cell], c: Cell) -> bool:
    """
    原版 JS isNarrow 逻辑复刻（对 cell_set 直接做 membership）。
    """
    x, y = c
    n = (x, y - 1) in area
    s = (x, y + 1) in area
    if (not n) and (not s):
        return True

    e = (x + 1, y) in area
    w = (x - 1, y) in area
    if (not e) and (not w):
        return True

    if (x + 1, y - 1) in area and n and e:
        return False
    if (x - 1, y - 1) in area and n and w:
        return False
    if (x + 1, y + 1) in area and s and e:
        return False
    if (x - 1, y + 1) in area and s and w:
        return False

    return True


def spawn_windows_excluding(
    rng: RNG,
    boundary: List[Tuple[EdgeKey, Cell]],
    doors: List[Dict[str, Any]],
    stairs: List[Dict[str, Any]],
    window_density: float,
    window_cap: int
) -> List[Dict[str, Any]]:
    """
    复刻 Dwellings.js spawnWindowsExcluding 逻辑：
    - 生成窗户边，避开门、楼梯等
    - 将窗户边推入返回列表
    """
    windows = []
    
    # Extract door edges to avoid
    door_edges = set()
    for door in doors:
        door_edge = tuple(door.get("edge_key", []))
        if door_edge:
            door_edges.add(door_edge)
    
    # Extract stair-related edges to avoid (simplified for now)
    stair_edges = set()
    
    # Get all boundary edges, excluding those near doors/stairs
    available_edges = []
    for edge_key, _ in boundary:
        if edge_key not in door_edges and edge_key not in stair_edges:
            available_edges.append(edge_key)
    
    # Shuffle edges for randomness
    rng.shuffle(available_edges)
    
    # Calculate number of windows based on density
    n_win = int(len(available_edges) * window_density)
    n_win = max(0, min(n_win, window_cap))
    
    # Generate windows
    for ek in available_edges[:n_win]:
        windows.append({
            "edge_key": list(ek),
            "length": 1
        })
    
    return windows


def get_notch(
    rng: RNG,
    contour: List[Edge],
    area: Set[Cell],
    *,
    prefer_corners: bool,
    prefer_walls: bool,
) -> Optional[Edge]:
    """
    原版 JS getNotch 结构复刻：
    - candidates_wall: 直线段上的候选
    - candidates_corner: 左转角（CW 轮廓里的“凹角”）候选
    - 根据 preferCorners / preferWalls 决策
    - 返回 nodeNdir2edge(q.a, q.dir.cw) 作为 notch（向内切一刀的第一段）
    """
    candidates_wall: List[Edge] = []
    candidates_corner: List[Edge] = []

    n = len(contour)
    if n < 4:
        return None

    for i, q in enumerate(contour):
        k = contour[i - 1]  # prev edge

        if q.dir == k.dir:
            if not (is_narrow(area, _edge2cell(area, k)) and is_narrow(area, _edge2cell(area, q))):
                candidates_wall.append(q)

        elif q.dir == k.dir.ccw:
            l = _edge2cell(area, q)
            h = _edge2cell(area, node_ndir2edge(q.a, q.dir.cw))
            if not (is_narrow(area, l) and is_narrow(area, h)):
                candidates_corner.append(q)

            l2 = _edge2cell(area, k)
            if not (is_narrow(area, h) and is_narrow(area, l2)):
                candidates_corner.append(node_ndir2edge(q.a, k.dir))

    if prefer_corners:
        pool = candidates_corner if candidates_corner else candidates_wall
    elif prefer_walls:
        pool = candidates_wall if candidates_wall else candidates_corner
    else:
        pool = candidates_wall + candidates_corner

    if not pool:
        return None

    q = rng.choice(pool)
    return node_ndir2edge(q.a, q.dir.cw)


def _blocked_pairs_from_chain(area: Set[Cell], chain: List[Edge]) -> Set[Tuple[Cell, Cell]]:
    """
    chain 是一组 grid-line edge，表示“内墙”。
    它会阻断一对相邻 cell 的连通（上下或左右）。
    """
    blocked: Set[Tuple[Cell, Cell]] = set()

    for e in chain:
        ax, ay = e.a
        bx, by = e.b

        if e.dir in (Dir.EAST, Dir.WEST):
            x = min(ax, bx)
            y = ay
            above = (x, y - 1)
            below = (x, y)
            if above in area and below in area:
                blocked.add((above, below))
                blocked.add((below, above))
        else:
            x = ax
            y = min(ay, by)
            left = (x - 1, y)
            right = (x, y)
            if left in area and right in area:
                blocked.add((left, right))
                blocked.add((right, left))

    return blocked


def split_area_by_chain(area: Set[Cell], chain: List[Edge]) -> Tuple[Set[Cell], Set[Cell]]:
    if not area:
        return set(), set()

    blocked = _blocked_pairs_from_chain(area, chain)
    start = next(iter(area))

    q = deque([start])
    seen = {start}

    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            n = (nx, ny)
            if n in area and n not in seen and ((x, y), n) not in blocked:
                seen.add(n)
                q.append(n)

    a = set(seen)
    b = set(area) - a
    return a, b


def _is_rectangle(area: Set[Cell]) -> bool:
    xs = [x for x, _ in area]
    ys = [y for _, y in area]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    return len(area) == (maxx - minx + 1) * (maxy - miny + 1)


def _split_score(area: Set[Cell], specs: Specs) -> float:
    """
    对应 JS divideArea 里 g(c)*g(d) 的那套评价（noNooks 分支不同）。
    """
    L = len(area)
    if L <= 0:
        return 0.0

    if specs.no_nooks:
        good = sum(1 for c in area if not is_narrow(area, c))
        return (good + 1) / (L + 1)

    # allow nooks 时：大房间不惩罚；小房间按 narrow 占比做概率
    if L > specs.avg_room_size:
        return 1.0
    bad = sum(1 for c in area if is_narrow(area, c))
    return (bad + 1) / (L + 1)


@dataclass
class DivideResult:
    rooms: List[Set[Cell]]
    inner_walls: List[List[Edge]]


class PlanDivider:
    """
    用 Dwellings 原版 divideArea 的风格把一个 area(cell_set) 切成若干 rooms。
    """
    def __init__(self, rng: RNG, specs: Specs):
        self.rng = rng
        self.specs = specs

    def divide(self, area: Set[Cell], *, max_retry: int = 12) -> DivideResult:
        rooms: List[Set[Cell]] = []
        inner_walls: List[List[Edge]] = []

        def rec(a: Set[Cell]) -> None:
            if not a:
                return

            # ---- stop condition (按 JS) ----
            chaos = 2 ** float(self.specs.room_size_chaos)

            if self.specs.regular_rooms:
                if _is_rectangle(a) and len(a) < self.specs.avg_room_size * chaos:
                    rooms.append(a)
                    return
            else:
                # len <= avgRoomSize * chaos^(rand/3)
                limit = self.specs.avg_room_size * (chaos ** (self.rng.random() / 3.0))
                if len(a) <= limit:
                    rooms.append(a)
                    return

            # ---- try split with retry ----
            for _attempt in range(max_retry):
                contour = outline_edges(a)
                notch = get_notch(
                    self.rng, contour, a,
                    prefer_corners=bool(self.specs.prefer_corners),
                    prefer_walls=bool(self.specs.prefer_walls),
                )
                if notch is None:
                    rooms.append(a)
                    return

                # build straight chain until hit boundary
                chain: List[Edge] = [notch]
                d = notch.dir
                while find_edge_by_start(contour, chain[-1].b) is None:
                    chain.append(node_ndir2edge(chain[-1].b, d))
                    if len(chain) > 4096:
                        break

                # optional "turn cut" (JS: if rand < len/avgRoomSize)
                if len(chain) > 1 and self.rng.random() < (len(chain) / float(self.specs.avg_room_size)):
                    turn = self.rng.choice([d.cw, d.ccw])
                    half = len(chain) / 2.0
                    keep = int(half) + (1 if self.rng.random() < (half - int(half)) else 0)
                    keep = max(1, keep)
                    chain = chain[:keep]

                    while find_edge_by_start(contour, chain[-1].b) is None:
                        chain.append(node_ndir2edge(chain[-1].b, turn))
                        if len(chain) > 4096:
                            break

                a1, a2 = split_area_by_chain(a, chain)
                if not a2:
                    # 没切开：重试换 notch
                    continue

                g = (_split_score(a1, self.specs) * _split_score(a2, self.specs)) ** 2
                if self.rng.random() < g:
                    inner_walls.append(chain)
                    rec(a1)
                    rec(a2)
                    return
                # else: retry

            # fallback: split 没成功就当一个 room（JS 最终也会回退）
            rooms.append(a)

        rec(set(area))
        return DivideResult(rooms=rooms, inner_walls=inner_walls)
