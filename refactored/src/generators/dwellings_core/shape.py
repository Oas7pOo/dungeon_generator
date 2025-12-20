# dwellings_core/shape.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

Cell = Tuple[int, int]
Node = Tuple[int, int]


class Dir(Enum):
    EAST = (1, 0)
    SOUTH = (0, 1)
    WEST = (-1, 0)
    NORTH = (0, -1)

    @property
    def dx(self) -> int:
        return self.value[0]

    @property
    def dy(self) -> int:
        return self.value[1]

    @property
    def cw(self) -> "Dir":
        return {
            Dir.NORTH: Dir.EAST,
            Dir.EAST: Dir.SOUTH,
            Dir.SOUTH: Dir.WEST,
            Dir.WEST: Dir.NORTH,
        }[self]

    @property
    def ccw(self) -> "Dir":
        return {
            Dir.NORTH: Dir.WEST,
            Dir.WEST: Dir.SOUTH,
            Dir.SOUTH: Dir.EAST,
            Dir.EAST: Dir.NORTH,
        }[self]

    @property
    def op(self) -> "Dir":
        return {
            Dir.NORTH: Dir.SOUTH,
            Dir.SOUTH: Dir.NORTH,
            Dir.EAST: Dir.WEST,
            Dir.WEST: Dir.EAST,
        }[self]


@dataclass(frozen=True)
class Edge:
    a: Node
    b: Node
    dir: Dir


def node_ndir2edge(a: Node, d: Dir) -> Edge:
    return Edge(a=a, b=(a[0] + d.dx, a[1] + d.dy), dir=d)


def edge_right_cell(e: Edge) -> Cell:
    """
    在 CW 轮廓里，内侧在“右边”，这里返回 edge 右侧的相邻 cell（以 cell 为单位格）。
    cell 坐标约定：cell(x,y) 覆盖 [x,x+1]x[y,y+1] 的方格。
    """
    ax, ay = e.a
    bx, by = e.b

    if e.dir in (Dir.EAST, Dir.WEST):
        x = min(ax, bx)
        y = ay
        # EAST: right side is SOUTH => cell below the segment
        # WEST: right side is NORTH => cell above the segment
        return (x, y) if e.dir == Dir.EAST else (x, y - 1)
    else:
        x = ax
        y = min(ay, by)
        # SOUTH: right side is WEST => cell left of the segment
        # NORTH: right side is EAST => cell right of the segment
        return (x - 1, y) if e.dir == Dir.SOUTH else (x, y)


def edge_left_cell(e: Edge) -> Cell:
    ax, ay = e.a
    bx, by = e.b

    if e.dir in (Dir.EAST, Dir.WEST):
        x = min(ax, bx)
        y = ay
        return (x, y - 1) if e.dir == Dir.EAST else (x, y)
    else:
        x = ax
        y = min(ay, by)
        return (x, y) if e.dir == Dir.SOUTH else (x - 1, y)


def _boundary_segments(area: Set[Cell]) -> Set[Tuple[Node, Node]]:
    """
    返回无向边界段集合（Node-Node），用于追踪轮廓。
    """
    segs: Set[Tuple[Node, Node]] = set()
    for (x, y) in area:
        # top
        if (x, y - 1) not in area:
            segs.add(((x, y), (x + 1, y)))
        # right
        if (x + 1, y) not in area:
            segs.add(((x + 1, y), (x + 1, y + 1)))
        # bottom
        if (x, y + 1) not in area:
            segs.add(((x + 1, y + 1), (x, y + 1)))
        # left
        if (x - 1, y) not in area:
            segs.add(((x, y + 1), (x, y)))

    # normalize undirected
    norm: Set[Tuple[Node, Node]] = set()
    for a, b in segs:
        norm.add((a, b) if a <= b else (b, a))
    return norm


def _trace_loops(segs: Set[Tuple[Node, Node]]) -> List[List[Node]]:
    """
    将无向边界段组成若干个闭环（holes/outer）。
    """
    adj: Dict[Node, List[Node]] = {}
    for a, b in segs:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    unused = set(segs)
    loops: List[List[Node]] = []

    while unused:
        a, b = next(iter(unused))
        start = min(a, b)

        nxt: Optional[Node] = None
        for n in adj[start]:
            s = (start, n) if start <= n else (n, start)
            if s in unused:
                nxt = n
                break
        if nxt is None:
            unused.remove((a, b))
            continue

        loop = [start, nxt]
        prev, cur = start, nxt
        unused.remove((start, cur) if start <= cur else (cur, start))

        while cur != start:
            cand = [n for n in adj[cur] if n != prev]
            chosen: Optional[Node] = None
            for n in cand:
                s = (cur, n) if cur <= n else (n, cur)
                if s in unused:
                    chosen = n
                    break
            if chosen is None:
                break
            prev, cur = cur, chosen
            loop.append(cur)
            unused.remove((prev, cur) if prev <= cur else (cur, prev))

        if loop and loop[-1] == start:
            loops.append(loop[:-1])
        else:
            loops.append(loop)

    return loops


def _orient_loop_interior_right(nodes: List[Node], area: Set[Cell]) -> List[Node]:
    """
    确保生成的轮廓方向为 CW：大多数边的 right_cell 在 area 里。
    """
    def score(ns: List[Node]) -> int:
        ok = 0
        n = len(ns)
        for i in range(n):
            a = ns[i]
            b = ns[(i + 1) % n]
            dx = b[0] - a[0]
            dy = b[1] - a[1]
            if abs(dx) + abs(dy) != 1:
                continue
            d = Dir.EAST if dx == 1 else Dir.WEST if dx == -1 else Dir.SOUTH if dy == 1 else Dir.NORTH
            e = Edge(a=a, b=b, dir=d)
            rc = edge_right_cell(e)
            lc = edge_left_cell(e)
            if rc in area:
                ok += 1
            elif lc in area:
                ok -= 1
        return ok

    return nodes if score(nodes) >= 0 else list(reversed(nodes))


def outline_edges(area: Set[Cell]) -> List[Edge]:
    """
    返回外轮廓（outer loop）的 CW Edge 列表。
    先不处理 holes：取绝对面积最大的 loop 当 outer。
    """
    loops = _trace_loops(_boundary_segments(area))
    if not loops:
        return []

    def poly_area(ns: List[Node]) -> float:
        s = 0
        n = len(ns)
        for i in range(n):
            x1, y1 = ns[i]
            x2, y2 = ns[(i + 1) % n]
            s += x1 * y2 - x2 * y1
        return s / 2.0

    outer = max(loops, key=lambda ns: abs(poly_area(ns)))
    outer = _orient_loop_interior_right(outer, area)

    edges: List[Edge] = []
    n = len(outer)
    for i in range(n):
        a = outer[i]
        b = outer[(i + 1) % n]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        d = Dir.EAST if dx == 1 else Dir.WEST if dx == -1 else Dir.SOUTH if dy == 1 else Dir.NORTH
        edges.append(Edge(a=a, b=b, dir=d))
    return edges


def find_edge_by_start(contour: List[Edge], node: Node) -> Optional[Edge]:
    for e in contour:
        if e.a == node:
            return e
    return None


# ---------- Step2: contour -> area (robust, outside flood fill) ----------
from collections import deque
from typing import Deque

# undirected edge key on integer grid nodes: (ax,ay,bx,by) with (a<=b)
EdgeSegKey = Tuple[int, int, int, int]


def _edgekey_from_nodes(a: Node, b: Node) -> EdgeSegKey:
    (ax, ay), (bx, by) = a, b
    if (ax, ay) <= (bx, by):
        return (ax, ay, bx, by)
    return (bx, by, ax, ay)


def edge_undirected_key(e: Edge) -> EdgeSegKey:
    return _edgekey_from_nodes(e.a, e.b)


def cell_ndir2edge_key(cell: Cell, d: Dir) -> EdgeSegKey:
    """
    返回 cell 朝 d 方向的“边界边”(无向 key)，用于判断该边是否在 contour 上
    cell=(x,y) 覆盖 [x,x+1]x[y,y+1]
    """
    x, y = cell
    if d == Dir.NORTH:
        return _edgekey_from_nodes((x, y), (x + 1, y))
    if d == Dir.SOUTH:
        return _edgekey_from_nodes((x, y + 1), (x + 1, y + 1))
    if d == Dir.WEST:
        return _edgekey_from_nodes((x, y), (x, y + 1))
    if d == Dir.EAST:
        return _edgekey_from_nodes((x + 1, y), (x + 1, y + 1))
    raise ValueError(f"Unknown dir: {d}")


def contour2area(contour: List[Edge]) -> List[Cell]:
    """
    更稳的 contour2area：从外部做 flood fill，contour 边当“墙”，
    bbox 内没被 flood 到的 cell 就是 inside area。

    这更贴近 Dwellings.js 里“边界阻挡 + 填充”的思想，
    不依赖 clockwise 判断，也不需要猜 inside 起点。
    """
    if not contour:
        return []

    wall_keys: Set[EdgeSegKey] = {edge_undirected_key(e) for e in contour}

    xs = [e.a[0] for e in contour] + [e.b[0] for e in contour]
    ys = [e.a[1] for e in contour] + [e.b[1] for e in contour]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    # inside candidate cells are x in [minx..maxx-1], y in [miny..maxy-1]
    in_minx, in_maxx = minx, maxx - 1
    in_miny, in_maxy = miny, maxy - 1

    # expanded bounds for outside flood
    ex_minx, ex_maxx = minx - 1, maxx
    ex_miny, ex_maxy = miny - 1, maxy

    def in_expanded(c: Cell) -> bool:
        x, y = c
        return ex_minx <= x <= ex_maxx and ex_miny <= y <= ex_maxy

    start: Cell = (ex_minx, ex_miny)
    q: Deque[Cell] = deque([start])
    outside: Set[Cell] = {start}

    while q:
        x, y = q.popleft()
        for d in (Dir.NORTH, Dir.EAST, Dir.SOUTH, Dir.WEST):
            nx, ny = x + d.dx, y + d.dy
            nb = (nx, ny)
            if not in_expanded(nb) or nb in outside:
                continue

            # 被 contour 边挡住就不能过
            ek = cell_ndir2edge_key((x, y), d)
            if ek in wall_keys:
                continue

            outside.add(nb)
            q.append(nb)

    inside: List[Cell] = []
    for x in range(in_minx, in_maxx + 1):
        for y in range(in_miny, in_maxy + 1):
            c = (x, y)
            if c not in outside:
                inside.append(c)

    # 稳定排序，便于复现
    inside.sort()
    return inside
