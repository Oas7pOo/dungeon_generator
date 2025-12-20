# refactored/src/generators/dwellings_core/house.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
from collections import deque
from math import inf

from .rng import RNG
from .specs import Specs
from .plan import PlanDivider, spawn_windows_excluding
from .shape import Edge, outline_edges, contour2area  # Edge 是 plan.py 里 inner_walls 的元素类型
from .footprint import irregularize_area_by_notches, get_notch_cut, apply_notch_if_valid

Cell = Tuple[int, int]
EdgeKey = Tuple[str, int, int]  # ("V", x, y) or ("H", x, y)


# -------------------------
# edge/cell helpers
# -------------------------
def _cells_for_edge_key(edge_key: Any) -> Optional[Tuple[Cell, Cell]]:
    """
    edge_key = ("V", x, y) 表示竖边：在 (x-1,y) 与 (x,y) 之间
    edge_key = ("H", x, y) 表示横边：在 (x,y-1) 与 (x,y) 之间
    """
    if isinstance(edge_key, tuple):
        edge_key = list(edge_key)
    if not isinstance(edge_key, list) or len(edge_key) != 3:
        return None
    typ, x, y = edge_key
    if typ == "V":
        x = int(x); y = int(y)
        return (x - 1, y), (x, y)
    if typ == "H":
        x = int(x); y = int(y)
        return (x, y - 1), (x, y)
    return None


def _edge_to_edge_key(e: Edge) -> EdgeKey:
    ax, ay = e.a
    bx, by = e.b
    # vertical
    if ax == bx:
        x = int(ax)
        y = int(min(ay, by))
        return ("V", x, y)
    # horizontal
    if ay == by:
        x = int(min(ax, bx))
        y = int(ay)
        return ("H", x, y)
    raise ValueError(f"non axis-aligned edge: {e}")


def _neighbors4(x: int, y: int) -> List[Cell]:
    return [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]


def _grid_size_from_cells(cells: List[Cell]) -> Tuple[int, int]:
    if not cells:
        return 0, 0
    max_x = max(x for x, _ in cells)
    max_y = max(y for _, y in cells)
    return max_x + 1, max_y + 1


def _edge_key_between(a: Cell, b: Cell) -> Optional[EdgeKey]:
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    if abs(dx) + abs(dy) != 1:
        return None
    if dx == 1:
        return ("V", ax + 1, ay)
    if dx == -1:
        return ("V", ax, ay)
    if dy == 1:
        return ("H", ax, ay + 1)
    if dy == -1:
        return ("H", ax, ay)
    return None


def _boundary_edges(area_set: Set[Cell]) -> List[Tuple[EdgeKey, Cell]]:
    """
    返回 (edge_key, inner_cell)，只包含外轮廓的边。
    inner_cell 一定在 area_set 里。
    """
    out: List[Tuple[EdgeKey, Cell]] = []
    for (x, y) in area_set:
        if (x - 1, y) not in area_set:
            out.append((("V", x, y), (x, y)))
        if (x + 1, y) not in area_set:
            out.append((("V", x + 1, y), (x, y)))
        if (x, y - 1) not in area_set:
            out.append((("H", x, y), (x, y)))
        if (x, y + 1) not in area_set:
            out.append((("H", x, y + 1), (x, y)))
    return out


# -------------------------
# DSU + door selection
# -------------------------
class _DSU:
    def __init__(self, n: int):
        self.p = list(range(n + 1))
        self.r = [0] * (n + 1)

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.r[ra] < self.r[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        if self.r[ra] == self.r[rb]:
            self.r[ra] += 1
        return True


def _select_doors_min_connectivity(
    rng: RNG,
    room_cells: Dict[int, List[Cell]],
    candidates: Dict[Tuple[int, int], List[EdgeKey]],
    connectivity: float = 0.2,
) -> List[Dict[str, Any]]:
    room_ids = sorted(room_cells.keys())
    if len(room_ids) <= 1:
        return []

    edges = []
    for (r1, r2), ek_list in candidates.items():
        if not ek_list:
            continue
        edges.append((len(ek_list), r1, r2))
    edges.sort(reverse=True)

    dsu = _DSU(max(room_ids))
    chosen_pairs: List[Tuple[int, int]] = []
    remaining_pairs: List[Tuple[int, int]] = []

    for _, r1, r2 in edges:
        if dsu.union(r1, r2):
            chosen_pairs.append((r1, r2))
        else:
            remaining_pairs.append((r1, r2))

    connectivity = max(0.0, min(1.0, float(connectivity)))

    eligible = []
    for (r1, r2) in remaining_pairs:
        ek_list = candidates.get((min(r1, r2), max(r1, r2)), [])
        if len(ek_list) >= 2:
            eligible.append((r1, r2))

    extra_n = int((len(room_ids) - 1) * connectivity)
    extra_n = max(0, min(extra_n, len(eligible)))

    rng.shuffle(eligible)
    chosen_pairs += eligible[:extra_n]

    doors: List[Dict[str, Any]] = []
    for (r1, r2) in chosen_pairs:
        ek_list = candidates.get((min(r1, r2), max(r1, r2)), [])
        if not ek_list:
            continue
        ek = rng.choice(ek_list)
        doors.append({
            "edge_key": list(ek),
            "r1": int(r1),
            "r2": int(r2),
            "door_type": "REGULAR",
            "price": float(len(ek_list)),
        })
    return doors


# -------------------------
# room merge to target
# -------------------------
def _reduce_rooms_to_target(rng: RNG, rooms: List[Set[Cell]], target_k: int) -> List[Set[Cell]]:
    rooms = [set(r) for r in rooms if r]
    target_k = max(1, int(target_k))
    if len(rooms) <= target_k:
        return rooms

    def rebuild_owner() -> Dict[Cell, int]:
        owner: Dict[Cell, int] = {}
        for i, r in enumerate(rooms):
            for c in r:
                owner[c] = i
        return owner

    while len(rooms) > target_k:
        owner = rebuild_owner()

        sizes = [(len(r), i) for i, r in enumerate(rooms)]
        sizes.sort()
        _, i_small = sizes[0]

        shared: Dict[int, int] = {}
        for (x, y) in rooms[i_small]:
            for nx, ny in _neighbors4(x, y):
                j = owner.get((nx, ny))
                if j is None or j == i_small:
                    continue
                shared[j] = shared.get(j, 0) + 1

        if shared:
            best = max(shared.values())
            cand = [j for j, v in shared.items() if v == best]
            j_merge = rng.choice(cand)
        else:
            cand = [j for j in range(len(rooms)) if j != i_small]
            j_merge = rng.choice(cand)

        rooms[j_merge].update(rooms[i_small])
        rooms.pop(i_small)

    return rooms


# -------------------------
# inner walls + door candidates (CORRECT VERSION)
# -------------------------
def _build_cell_owner_from_rooms(rooms: List[Set[Cell]]) -> Dict[Cell, int]:
    owner: Dict[Cell, int] = {}
    for rid, s in enumerate(rooms, start=1):
        for c in s:
            owner[c] = rid
    return owner


def _inner_walls_by_room_and_candidates(
    area_set: Set[Cell],
    rooms: List[Set[Cell]],
    inner_walls: List[List[Edge]],
) -> Tuple[Dict[int, List[EdgeKey]], Dict[Tuple[int, int], List[EdgeKey]]]:
    """
    返回：
      inner_by_room[rid] = [edge_key,...]  # 该房间边界上的内墙段
      candidates[(r1,r2)] = [edge_key,...] # r1<r2，两房间之间可开门的内墙段
    """
    owner = _build_cell_owner_from_rooms(rooms)

    inner_by_room: Dict[int, Set[EdgeKey]] = {rid: set() for rid in range(1, len(rooms) + 1)}
    cand: Dict[Tuple[int, int], Set[EdgeKey]] = {}

    for chain in (inner_walls or []):
        for e in chain:
            ek = _edge_to_edge_key(e)

            pair = _cells_for_edge_key(ek)
            if not pair:
                continue
            a, b = pair

            # 只要两侧 cell 都在 area_set，才算“真正的内墙段”
            if a not in area_set or b not in area_set:
                continue

            r1 = owner.get(a)
            r2 = owner.get(b)
            if not r1 or not r2 or r1 == r2:
                continue

            inner_by_room[r1].add(ek)
            inner_by_room[r2].add(ek)

            x, y = (r1, r2) if r1 < r2 else (r2, r1)
            cand.setdefault((x, y), set()).add(ek)

    inner_out = {rid: sorted(list(s)) for rid, s in inner_by_room.items()}
    cand_out = {k: sorted(list(s)) for k, s in cand.items()}
    return inner_out, cand_out


# -------------------------
# validation
# -------------------------
def validate_plan_export(plan_export: Dict[str, Any]) -> None:
    area = plan_export.get("area_cells") or []
    area_set: Set[Cell] = set((int(x), int(y)) for x, y in area)

    terrace = plan_export.get("terrace_cells") or []
    terrace_set: Set[Cell] = set((int(x), int(y)) for x, y in terrace)

    rooms = plan_export.get("rooms") or []
    room_map: Dict[int, Set[Cell]] = {}
    for r in rooms:
        rid = int(r["id"])
        rtype = (r.get("type") or "generic").lower()
        cells = r.get("cells") or []
        s = set((int(x), int(y)) for x, y in cells)
        if not s:
            raise ValueError(f"room {rid} has no cells")

        if rtype == "terrace":
            if not s.issubset(terrace_set):
                bad = next(iter(s - terrace_set))
                raise ValueError(f"terrace room {rid} cell not in terrace_cells: {bad}")
        else:
            if not s.issubset(area_set):
                bad = next(iter(s - area_set))
                raise ValueError(f"room {rid} cell not in area_cells: {bad}")

        if rid in room_map:
            raise ValueError(f"duplicate room id: {rid}")
        room_map[rid] = s

    boundary_edges = set(tuple(ek) for (ek, _inner) in _boundary_edges(area_set))

    ent = plan_export.get("entrance") or {}
    ek_ent = ent.get("edge_key")
    if isinstance(ek_ent, list):
        if tuple(ek_ent) not in boundary_edges:
            raise ValueError(f"entrance edge_key not on boundary: {ek_ent}")

    for d in (plan_export.get("doors") or []):
        ek = d.get("edge_key")
        r1 = int(d.get("r1"))
        r2 = int(d.get("r2"))
        if r1 == r2:
            raise ValueError(f"door connects same room: {r1}")
        if r1 not in room_map or r2 not in room_map:
            raise ValueError(f"door references missing room: {r1},{r2}")

        pair = _cells_for_edge_key(ek)
        if pair is None:
            raise ValueError(f"invalid door edge_key: {ek}")
        a, b = pair
        ok = (a in room_map[r1] and b in room_map[r2]) or (a in room_map[r2] and b in room_map[r1])
        if not ok:
            raise ValueError(f"door edge not between r1/r2 cells: door={ek}, r1={r1}, r2={r2}")

    for w in (plan_export.get("windows") or []):
        ek = w.get("edge_key")
        if not isinstance(ek, list):
            raise ValueError(f"invalid window edge_key: {ek}")
        if tuple(ek) not in boundary_edges:
            raise ValueError(f"window edge_key not on boundary: {ek}")

def _area_centroid(area: Set[Cell]) -> Tuple[float, float]:
    if not area:
        return (0.0, 0.0)
    sx = sum(x + 0.5 for x, _ in area)
    sy = sum(y + 0.5 for _, y in area)
    n = len(area)
    return (sx / n, sy / n)


def _find_stair_core_cells(area: Set[Cell], rng: RNG) -> Set[Cell]:
    """
    找一个“楼梯 core”，优先 2x2；找不到就降级到 1x2；再不行就单格。
    返回的是要永远保留的 cells 集合。
    """
    if not area:
        return set()

    cx, cy = _area_centroid(area)

    # 1) 2x2
    cand_2x2: List[Tuple[float, Set[Cell]]] = []
    for (x, y) in area:
        core = {(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)}
        if core.issubset(area):
            # 距离中心越近越好
            dx = (x + 1.0) - cx
            dy = (y + 1.0) - cy
            cand_2x2.append((dx * dx + dy * dy, core))
    if cand_2x2:
        cand_2x2.sort(key=lambda t: t[0])
        # 同分随机扰动一下，避免总是同一个
        best_d = cand_2x2[0][0]
        best = [c for d, c in cand_2x2 if abs(d - best_d) < 1e-9]
        return set(rng.choice(best))

    # 2) 1x2
    cand_1x2: List[Tuple[float, Set[Cell]]] = []
    for (x, y) in area:
        core_h = {(x, y), (x + 1, y)}
        core_v = {(x, y), (x, y + 1)}
        for core in (core_h, core_v):
            if core.issubset(area):
                dx = (x + 0.5) - cx
                dy = (y + 0.5) - cy
                cand_1x2.append((dx * dx + dy * dy, core))
    if cand_1x2:
        cand_1x2.sort(key=lambda t: t[0])
        best_d = cand_1x2[0][0]
        best = [c for d, c in cand_1x2 if abs(d - best_d) < 1e-9]
        return set(rng.choice(best))

    # 3) 单格
    # 选离中心最近的那一格
    best_cell = None
    best_dist = inf
    for (x, y) in area:
        dx = (x + 0.5) - cx
        dy = (y + 0.5) - cy
        d = dx * dx + dy * dy
        if d < best_dist:
            best_dist = d
            best_cell = (x, y)
    return {best_cell} if best_cell else set()

def _manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def terrace_outer_boundary_cells(terrace: Set[Cell], lower: Set[Cell]) -> Set[Cell]:
    """
    露台的“外边界”cell：它有至少一个邻居不在 lower footprint 中，说明贴着建筑外缘。
    """
    out: Set[Cell] = set()
    for (x, y) in terrace:
        for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
            if (x + dx, y + dy) not in lower:
                out.add((x, y))
                break
    return out


def bfs_path_on_cells(allowed: Set[Cell], start: Cell, goal: Cell) -> List[Cell]:
    if start == goal:
        return [start]
    q = deque([start])
    prev: Dict[Cell, Optional[Cell]] = {start: None}

    while q:
        x, y = q.popleft()
        for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
            nb = (x + dx, y + dy)
            if nb in allowed and nb not in prev:
                prev[nb] = (x, y)
                if nb == goal:
                    q.clear()
                    break
                q.append(nb)

    if goal not in prev:
        return []

    path: List[Cell] = []
    cur: Optional[Cell] = goal
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    return path


def _pick_boundary_start_goal(boundary: Set[Cell], core_cells: Set[Cell], rng: RNG) -> Tuple[Cell, Cell]:
    """
    start: 距离楼梯 core 最近的 boundary cell
    goal: 远离 start 的 boundary cell（用曼哈顿距离近似）
    """
    b_list = list(boundary)
    core_list = list(core_cells) if core_cells else [(b_list[0])]

    # start
    best_s = b_list[0]
    best_d = 10**9
    for b in b_list:
        d = min(_manhattan(b, c) for c in core_list)
        if d < best_d:
            best_d = d
            best_s = b

    # goal
    best_g = best_s
    best_gd = -1
    for b in b_list:
        d = _manhattan(b, best_s)
        if d > best_gd:
            best_gd = d
            best_g = b

    # 小随机扰动：有同距离的就随机一下
    far = [b for b in b_list if _manhattan(b, best_s) == best_gd]
    if far:
        best_g = rng.choice(far)

    return best_s, best_g


def _try_apply_one_notch(
    rng: RNG,
    area: Set[Cell],
    lower_n: int,
    core: Set[Cell],
    *,
    min_ratio: float,
    min_cells: int,
    notch_width_range: Tuple[int, int],
    notch_depth_range: Tuple[int, int],
) -> Optional[Set[Cell]]:
    cut = get_notch_cut(
        area,
        rng,
        notch_width_range=notch_width_range,
        notch_depth_range=notch_depth_range,
    )
    if not cut:
        return None
    # 绝不切到 core
    if core and (cut & core):
        return None

    new_area = apply_notch_if_valid(area, cut, min_cells=min_cells)
    if new_area is None:
        return None

    if core and (not core.issubset(new_area)):
        return None

    if len(new_area) < max(min_cells, int(lower_n * min_ratio)):
        return None

    # 必须真的“退台”：至少少掉 1 格
    if len(new_area) >= len(area):
        return None

    return new_area


def _generate_setback_by_notches(
    rng: RNG,
    lower_area_cells: List[Cell],
    core_cells: Set[Cell],
    *,
    min_ratio: float = 0.65,
    min_cells: int = 10,
    notch_attempts: int = 40,
    max_notches: int = 3,
    notch_width_range: Tuple[int, int] = (2, 5),
    notch_depth_range: Tuple[int, int] = (2, 6),
) -> List[Cell]:
    """
    在 lower footprint 上做 1..N 次 notch 裁切，得到 upper footprint。
    约束：
    - upper >= min_ratio * lower
    - 保留 core_cells
    - 形状保持连通（apply_notch_if_valid 已保证）
    - 尽量保证出现缩进（否则走 fallback）
    """
    lower_set: Set[Cell] = set(lower_area_cells)
    area: Set[Cell] = set(lower_area_cells)
    lower_n = len(lower_set)

    if lower_n < min_cells:
        return list(area)

    target_notches = rng.randint(1, max(1, int(max_notches)))
    made = 0

    for _ in range(notch_attempts):
        if made >= target_notches:
            break
        new_area = _try_apply_one_notch(
            rng,
            area,
            lower_n,
            core_cells,
            min_ratio=min_ratio,
            min_cells=min_cells,
            notch_width_range=notch_width_range,
            notch_depth_range=notch_depth_range,
        )
        if new_area is None:
            continue
        area = new_area
        made += 1

    # fallback：如果一次都没咬到，强制用更小 notch 再试一轮，尽量保证“稳定出现退台”
    if area == lower_set:
        for _ in range(notch_attempts):
            new_area = _try_apply_one_notch(
                rng,
                area,
                lower_n,
                core_cells,
                min_ratio=min_ratio,
                min_cells=min_cells,
                notch_width_range=(2, 2),
                notch_depth_range=(2, 2),
            )
            if new_area is not None:
                area = new_area
                break

    return sorted(list(area))

def _connected_components(cells: Set[Cell]) -> List[Set[Cell]]:
    comps: List[Set[Cell]] = []
    seen: Set[Cell] = set()
    for c in cells:
        if c in seen:
            continue
        q = deque([c])
        seen.add(c)
        comp = {c}
        while q:
            x, y = q.popleft()
            for nx, ny in _neighbors4(x, y):
                nb = (nx, ny)
                if nb in cells and nb not in seen:
                    seen.add(nb)
                    comp.add(nb)
                    q.append(nb)
        comps.append(comp)
    return comps


def _pick_farthest_seeds(rng: RNG, cells: List[Cell], k: int) -> List[Cell]:
    # 简单 farthest-point sampling
    seeds = [rng.choice(cells)]
    while len(seeds) < k:
        best = None
        best_d = -1
        for c in cells:
            d = min(abs(c[0]-s[0]) + abs(c[1]-s[1]) for s in seeds)
            if d > best_d:
                best_d = d
                best = c
        seeds.append(best if best is not None else rng.choice(cells))
    return seeds


def _split_connected_area_into_k(rng: RNG, comp: Set[Cell], k: int) -> List[Set[Cell]]:
    # 关键：只用“从已有格子扩张”的方式分配，保证每个子房间连通
    k = max(1, int(k))
    cells = list(comp)
    if k <= 1 or len(cells) <= k:
        return [set(comp)]

    seeds = _pick_farthest_seeds(rng, cells, k)
    regions = [set([s]) for s in seeds]
    owner: Dict[Cell, int] = {s: i for i, s in enumerate(seeds)}
    fronts = [deque([s]) for s in seeds]
    unassigned = set(comp) - set(seeds)

    while unassigned:
        progressed = False
        order = list(range(k))
        rng.shuffle(order)
        for i in order:
            if not fronts[i]:
                continue
            x, y = fronts[i].popleft()
            for nx, ny in _neighbors4(x, y):
                nb = (nx, ny)
                if nb in unassigned:
                    unassigned.remove(nb)
                    owner[nb] = i
                    regions[i].add(nb)
                    fronts[i].append(nb)
                    progressed = True
        if progressed:
            continue

        # 卡住时：随便挑一个未分配格，把它塞给它任意一个已分配邻居所属的 region
        u = next(iter(unassigned))
        assigned_neighbor = None
        for nx, ny in _neighbors4(u[0], u[1]):
            nb = (nx, ny)
            if nb in owner:
                assigned_neighbor = nb
                break
        if assigned_neighbor is None:
            # 理论上不会发生（comp 连通），兜底
            regions[0].update(unassigned)
            break
        i = owner[assigned_neighbor]
        unassigned.remove(u)
        owner[u] = i
        regions[i].add(u)
        fronts[i].append(u)

    # 清理空集
    regions = [r for r in regions if r]
    return regions


def _split_terrace_into_rooms(rng: RNG, terrace_set: Set[Cell]) -> List[Set[Cell]]:
    rooms: List[Set[Cell]] = []
    for comp in _connected_components(terrace_set):
        n = len(comp)
        # 你可以按喜好调阈值：越小越容易分裂出“相邻露台房间”
        if n < 20:
            k = 1
        elif n < 60:
            k = 2
        else:
            k = 3
        rooms.extend(_split_connected_area_into_k(rng, comp, k))
    return rooms

def _inner_walls_and_candidates_from_room_sets(
    rooms: List[Set[Cell]],
) -> Tuple[Dict[int, List[EdgeKey]], Dict[Tuple[int, int], List[EdgeKey]]]:
    owner: Dict[Cell, int] = {}
    for rid, s in enumerate(rooms, start=1):
        for c in s:
            owner[c] = rid

    inner_by_room: Dict[int, Set[EdgeKey]] = {rid: set() for rid in range(1, len(rooms) + 1)}
    cand: Dict[Tuple[int, int], Set[EdgeKey]] = {}

    # 扫描右/下邻居避免重复
    for (x, y), r1 in owner.items():
        for nb in [(x + 1, y), (x, y + 1)]:
            r2 = owner.get(nb)
            if not r2 or r2 == r1:
                continue
            ek = _edge_key_between((x, y), nb)
            if ek is None:
                continue
            inner_by_room[r1].add(ek)
            inner_by_room[r2].add(ek)
            a, b = (r1, r2) if r1 < r2 else (r2, r1)
            cand.setdefault((a, b), set()).add(ek)

    inner_out = {rid: sorted(list(s)) for rid, s in inner_by_room.items()}
    cand_out = {k: sorted(list(s)) for k, s in cand.items()}
    return inner_out, cand_out

def _should_generate_setback(tags: List[str], n_floors: int) -> bool:
    if "slab" in tags:
        return False
    return n_floors > 1

# -------------------------
# public API
# -------------------------
def generate_house_export(    *,
    seed: int,
    tags: List[str],
    area_cells: List[Tuple[int, int]],
    n_floors: int,
) -> Dict[str, Any]:
    base_rng = RNG(int(seed))

    area_cells_norm: List[Cell] = [(int(x), int(y)) for x, y in area_cells]
    
    # ✅ Step2：先改 footprint（不规则 + 凹口）
    area_cells_norm = irregularize_area_by_notches(
        area_cells_norm,
        base_rng,
        max_notches=3,
    )
    
    # 轮廓回填归一化：确保形状稳定，便于后续处理
    cont = outline_edges(set(area_cells_norm))
    area_cells_norm = contour2area(cont)

    # ✅ 选一个“楼梯 core”，后续楼层退台必须保留它
    base_area_set = set(area_cells_norm)
    core_cells = _find_stair_core_cells(base_area_set, base_rng)

    # grid size（用底层算就行）
    grid_w, grid_h = _grid_size_from_cells(area_cells_norm)

    specs_obj = Specs.from_tags(tags)
    specs = specs_obj.to_dict()

    floors: List[Dict[str, Any]] = []
    n_floors = max(1, int(n_floors))

    generate_setback = _should_generate_setback(tags, n_floors)

    # ✅ 默认有露台；传 no_terrace 才禁用
    allow_terrace = ("no_terrace" not in tags)

    # ✅ 每层 footprint 预先算好
    floor_areas: List[List[Cell]] = [area_cells_norm.copy()]
    for floor_i in range(1, n_floors):
        if generate_setback:
            rng = RNG(base_rng._next_u32() ^ (floor_i * 0x9E3779B9))
            upper_area = _generate_setback_by_notches(
                rng,
                floor_areas[-1],
                core_cells,
                min_ratio=0.65,
                max_notches=3,
            )
            # ✅ 再做一次轮廓回填，保持边界稳定
            cont_u = outline_edges(set(upper_area))
            upper_area = contour2area(cont_u)
            floor_areas.append(upper_area)
        else:
            floor_areas.append(floor_areas[-1].copy())

    for floor_i in range(n_floors):
        rng = RNG(base_rng._next_u32() ^ (floor_i * 0x9E3779B9))
        
        # Use the area for this floor (may have setback)
        current_area = floor_areas[floor_i]
        current_area_set = set(current_area)
        
        divider = PlanDivider(rng, specs_obj)
        res = divider.divide(current_area_set)

        rooms_list = list(res.rooms)  # 原版风格：divide() 出来多少就多少

        room_cells: Dict[int, List[Cell]] = {
            rid: sorted(list(cset))
            for rid, cset in enumerate(rooms_list, start=1)
        }

        # ✅ terrace：上层露台 = 下层 footprint - 本层 footprint
        terrace_area: List[Cell] = []
        terrace_rooms: List[Set[Cell]] = []
        if floor_i > 0 and generate_setback and allow_terrace:
            lower_set = set(floor_areas[floor_i - 1])
            cur_set = set(floor_areas[floor_i])
            # ✅ terrace：上层露台 = 下层 footprint - 本层 footprint（但不要内凹被包裹的块）
            terrace_area: List[Cell] = []
            terrace_rooms: List[Set[Cell]] = []

            if floor_i > 0 and generate_setback and allow_terrace:
                lower_set = set(floor_areas[floor_i - 1])
                cur_set = set(floor_areas[floor_i])
                terrace_raw = lower_set - cur_set

                if terrace_raw:
                    # 露台“触外”的边界cell（内凹被完全包裹的区域不会触外）
                    outer_cells = terrace_outer_boundary_cells(terrace_raw, lower_set)

                    keep: Set[Cell] = set()
                    for comp in _connected_components(terrace_raw):
                        # 只保留包含“触外边界cell”的连通块
                        if comp & outer_cells:
                            keep |= comp

                    terrace_area = sorted(list(keep))

                    # ✅ 不分割：每个连通块就是一个 terrace room
                    terrace_rooms = _connected_components(keep) if keep else []


        # rooms_all：室内 + 露台房间（露台拆成多个房间）
        rooms_all: List[Set[Cell]] = list(rooms_list) + list(terrace_rooms)

        room_cells: Dict[int, List[Cell]] = {
            rid: sorted(list(cset))
            for rid, cset in enumerate(rooms_all, start=1)
        }

        # ✅ 室内门（保持原逻辑）
        inner_by_room_indoor, door_cand = _inner_walls_and_candidates_from_room_sets(rooms_list)
        doors_export = _select_doors_min_connectivity(
            rng,
            {rid: sorted(list(cset)) for rid, cset in enumerate(rooms_list, start=1)},
            door_cand,
            connectivity=float(specs_obj.connectivity),
        )

        # ✅ 露台门：每个露台房间至少 1 扇，连接到相邻的室内房间
        if terrace_rooms:
            owner_indoor = _build_cell_owner_from_rooms(rooms_list)  # (x,y)->indoor rid
            indoor_n = len(rooms_list)

            for ti, tset in enumerate(terrace_rooms):
                terrace_rid = indoor_n + ti + 1

                # candidates: indoor_rid -> [edge_key...]
                cand_to_indoor: Dict[int, List[EdgeKey]] = {}

                for (x, y) in tset:
                    for nb in _neighbors4(x, y):
                        if nb not in current_area_set:  # 只允许贴着本层室内 footprint 的地方开门
                            continue
                        indoor_rid = owner_indoor.get(nb)
                        if not indoor_rid:
                            continue
                        ek = _edge_key_between((x, y), nb)
                        if ek is None:
                            continue
                        cand_to_indoor.setdefault(int(indoor_rid), []).append(ek)

                # 如果这个露台块完全没有贴着室内(只角接触或孤岛)，就不给它生成门
                if not cand_to_indoor:
                    continue

                # 选“共享边最多”的室内房间
                best_indoor = max(cand_to_indoor.items(), key=lambda kv: len(kv[1]))[0]
                ek = rng.choice(cand_to_indoor[best_indoor])

                doors_export.append({
                    "edge_key": list(ek),
                    "r1": int(best_indoor),
                    "r2": int(terrace_rid),
                    "door_type": "TERRACE",
                    "price": float(len(cand_to_indoor[best_indoor])),
                })

        # ✅ 把 inner_walls_by_room 补全到 rooms_all，露台房间给空列表
        inner_by_room = {rid: inner_by_room_indoor.get(rid, []) for rid in range(1, len(rooms_all) + 1)}

        # Default values for required export fields
        entrance = {}
        windows_export = []
        stairs_export = []

        # rooms_export：按 rid 判定是否为 terrace（室内数量 = len(rooms_list)）
        rooms_export = []
        indoor_n = len(rooms_list)
        for rid in sorted(room_cells.keys()):
            cells = room_cells[rid]
            if not cells:
                continue
            is_terrace = (rid > indoor_n)
            rtype = "terrace" if is_terrace else "generic"
            rname = f"Terrace_{rid - indoor_n:02d}" if is_terrace else f"Room_{rid}"
            rooms_export.append({
                "id": int(rid),
                "type": rtype,
                "name": rname,
                "cells": [[int(x), int(y)] for (x, y) in cells],
                "narrow": [],
                "contour": None,
            })


        plan_export: Dict[str, Any] = {
            "floor_index": int(floor_i),
            "grid_w": int(grid_w),
            "grid_h": int(grid_h),
            "area_cells": [[int(x), int(y)] for (x, y) in current_area],
            "terrace_cells": [[int(x), int(y)] for (x, y) in terrace_area],
            "stair_core_cells": [[int(x), int(y)] for (x, y) in sorted(core_cells)],
            "entrance": entrance,
            "rooms": rooms_export,
            "doors": doors_export,
            "windows": windows_export,
            "stairs": stairs_export,
            "inner_walls_by_room": {int(rid): [list(ek) for ek in eks] for rid, eks in inner_by_room.items()},
            "inner_walls": [
                [[int(e.a[0]), int(e.a[1]), int(e.b[0]), int(e.b[1])] for e in chain]
                for chain in (res.inner_walls or [])
            ],
        }

        validate_plan_export(plan_export)
        floors.append(plan_export)

    return {
        "seed": int(seed),
        "tags": list(tags),
        "specs": specs,
        "floors": floors,
        "has_setback": bool(generate_setback),
        "n_floors": int(n_floors),
        "stair_core_cells": [[int(x), int(y)] for (x, y) in sorted(core_cells)],
    }
