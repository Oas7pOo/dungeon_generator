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


def _edge_to_edge_key(e: Edge) -> EdgeKey:
    ax, ay = e.a
    bx, by = e.b
    if ax == bx:  # vertical
        x = int(ax)
        y = int(min(ay, by))
        return ("V", x, y)
    if ay == by:  # horizontal
        x = int(min(ax, bx))
        y = int(ay)
        return ("H", x, y)
    raise ValueError(f"non axis-aligned edge: {e}")


def _weighted_index(rng: RNG, weights: List[int]) -> int:
    total = sum(weights)
    if total <= 0:
        # 兜底：全 0 就均匀选
        return rng.randint(0, len(weights) - 1)
    r = rng.random() * total
    s = 0.0
    for i, w in enumerate(weights):
        s += w
        if r < s:
            return i
    return len(weights) - 1


def spawn_windows_excluding(
    rng: RNG,
    contour_edges: List[Edge],
    *,
    room_by_cell: Dict[Cell, int],
    excluded_rooms: Optional[List[int]] = None,
    blocked_edge_keys: Optional[Set[EdgeKey]] = None,
    density: float = 0.5,
    window_mode: str = "normal",                 # 新增
    tags: Optional[List[str]] = None,            # 兼容旧调用（后面会删）
) -> List[EdgeKey]:
    """
    JS-like spawnWindowsExcluding:
    - 按“窗段”抽样，不是按单边
    - 段权重 = len(segment)^2
    - 抽到段 => 整段全放窗
    - blank => 不放窗
    - transparent => 密度强制 1
    """
    # 兼容：如果还传了 tags，就用 tags 覆盖 window_mode（旧代码不炸）
    if tags:
        if "blank" in tags:
            window_mode = "blank"
        elif "transparent" in tags:
            window_mode = "transparent"

    if window_mode == "blank":
        return []
    if window_mode == "transparent":
        density = 1.0

    density = float(density)
    if density <= 0:
        return []

    excluded: Set[int] = set(int(x) for x in (excluded_rooms or []))
    blocked: Set[EdgeKey] = set(blocked_edge_keys or set())

    # 1) 先算 eligible edge 集合（用于“沿 contour 顺序分段”）
    eligible_set: Set[EdgeKey] = set()
    eligible_count = 0

    for e in contour_edges:
        ek = _edge_to_edge_key(e)

        if ek in blocked:
            continue

        # contour 是 CW，正常内侧在右；为了鲁棒，右侧没有就回退左侧
        rc = edge_right_cell(e)
        rid = room_by_cell.get(rc)
        if rid is None:
            lc = edge_left_cell(e)
            rid = room_by_cell.get(lc)

        if rid is None:
            continue
        if rid in excluded:
            continue

        eligible_set.add(ek)
        eligible_count += 1

    if eligible_count == 0:
        return []

    target = eligible_count * density  # JS 是 float，不要先 int

    # 2) 沿 contour 顺序“分段”：同 room + 同 dir + 连续
    segments: List[List[EdgeKey]] = []
    cur_seg: List[EdgeKey] = []
    cur_room: Optional[int] = None
    cur_dir = None

    for e in contour_edges:
        ek = _edge_to_edge_key(e)
        if ek not in eligible_set:
            if cur_seg:
                segments.append(cur_seg)
                cur_seg = []
            cur_room = None
            cur_dir = None
            continue

        rc = edge_right_cell(e)
        rid = room_by_cell.get(rc)
        if rid is None:
            lc = edge_left_cell(e)
            rid = room_by_cell.get(lc)

        if rid is None:
            # 理论不该发生，兜底切段
            if cur_seg:
                segments.append(cur_seg)
                cur_seg = []
            cur_room = None
            cur_dir = None
            continue

        if cur_seg and (rid != cur_room or e.dir != cur_dir):
            segments.append(cur_seg)
            cur_seg = []

        cur_seg.append(ek)
        cur_room = rid
        cur_dir = e.dir

    if cur_seg:
        segments.append(cur_seg)

    if not segments:
        return []

    # 3) 按段抽样：权重 len(seg)^2，抽到整段加入
    picked: List[EdgeKey] = []
    while segments and (len(picked) < target):
        weights = [len(seg) * len(seg) for seg in segments]
        idx = _weighted_index(rng, weights)
        seg = segments.pop(idx)
        picked.extend(seg)

    return picked


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

def _is_corridor(room: Set[Cell]) -> bool:
    # JS: corridor if every cell is narrow in this room
    # ca.every(room.area, cell in room.narrow)
    return bool(room) and all(is_narrow(room, c) for c in room)

def _shared_edges_count_upto2(a: Set[Cell], b: Set[Cell]) -> int:
    """
    统计两房间共享的“网格边”条数，最多数到 2 就提前退出（因为 JS 只关心 0/1/>1）。
    """
    if not a or not b:
        return 0

    seen: Set[Tuple[Cell, Cell]] = set()
    cnt = 0
    for (x, y) in a:
        # 只要 4 邻域即可
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (x + dx, y + dy)
            if nb in b:
                e = ((x, y), nb) if (x, y) < nb else (nb, (x, y))
                if e in seen:
                    continue
                seen.add(e)
                cnt += 1
                if cnt > 1:
                    return cnt
    return cnt

def merge_corridors_like_js(
    rooms: List[Set[Cell]],
    *,
    stair_room: Optional[Set[Cell]] = None,
) -> List[Set[Cell]]:
    """
    JS 复刻版 mergeCorridors:
    - corridor = room 内每个 cell 都 is_narrow(room, cell)
    - 排除 stairwell.room
    - 只在两 corridor 共享边条数 == 1 时合并
    - 按 JS 的扫描顺序 do-while 合并，合并出的新房间 append 到末尾
    """
    rooms = [set(r) for r in rooms if r]

    corridors: List[Set[Cell]] = []
    for r in rooms:
        if stair_room is not None and r == stair_room:
            continue
        if _is_corridor(r):
            corridors.append(r)

    changed = True
    while changed:
        changed = False
        # JS: for a=0..len-2, c=a+1..len-1
        for i in range(len(corridors) - 1):
            g = corridors[i]
            for j in range(i + 1, len(corridors)):
                q = corridors[j]
                l = _shared_edges_count_upto2(g, q)
                if l == 1:
                    merged = set(g) | set(q)

                    # rooms / corridors 中删除旧房间
                    rooms.remove(g)
                    rooms.remove(q)
                    corridors.pop(j)
                    corridors.pop(i)

                    # JS: addRoom(...) 后 push 到列表尾部
                    rooms.append(merged)
                    corridors.append(merged)

                    changed = True
                    break
            if changed:
                break

    return rooms

def _spawn_windows(rng: RNG, area_set: Set[Cell], entrance_ek: Optional[EdgeKey], density: float) -> List[Dict[str, Any]]:
    boundary = _boundary_edges(area_set)
    if not boundary:
        return []
    density = max(0.0, min(1.0, float(density)))

    # 以“外墙边数”近似 perimeter
    n = int(round(len(boundary) * density))
    if n <= 0:
        return []

    # 过滤 entrance 边
    boundary2 = [(ek,c) for ek,c in boundary if (entrance_ek is None or tuple(ek) != tuple(entrance_ek))]

    rng.shuffle(boundary2)
    chosen = []
    used_inner = []

    # 做一点“间距”，避免窗挤在一起
    for ek, c in boundary2:
        if len(chosen) >= n:
            break
        ok = True
        for uc in used_inner:
            if abs(c[0]-uc[0]) + abs(c[1]-uc[1]) < 3:
                ok = False
                break
        if not ok:
            continue
        chosen.append({"edge_key": list(ek)})
        used_inner.append(c)

    return chosen

def _stair_kind_from_core(core: Set[Cell]) -> str:
    if len(core) >= 4:
        return "spiral"
    return "stairwell"
