# refactored/src/generators/dwellings_core/house.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from .rng import RNG
from .specs import Specs
from .plan import PlanDivider
from .shape import Edge  # Edge 是 plan.py 里 inner_walls 的元素类型

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

    rooms = plan_export.get("rooms") or []
    room_map: Dict[int, Set[Cell]] = {}
    for r in rooms:
        rid = int(r["id"])
        cells = r.get("cells") or []
        s = set((int(x), int(y)) for x, y in cells)
        if not s:
            raise ValueError(f"room {rid} has no cells")
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


# -------------------------
# public API
# -------------------------
def generate_house_export(
    *,
    seed: int,
    tags: List[str],
    area_cells: List[Tuple[int, int]],
    n_floors: int,
) -> Dict[str, Any]:
    base_rng = RNG(int(seed))

    area_cells_norm: List[Cell] = [(int(x), int(y)) for x, y in area_cells]
    area_set = set(area_cells_norm)
    grid_w, grid_h = _grid_size_from_cells(area_cells_norm)

    specs_obj = Specs.from_tags(tags)
    specs = specs_obj.to_dict()

    floors: List[Dict[str, Any]] = []
    n_floors = max(1, int(n_floors))

    for floor_i in range(n_floors):
        rng = RNG(base_rng._next_u32() ^ (floor_i * 0x9E3779B9))

        divider = PlanDivider(rng, specs_obj)
        res = divider.divide(set(area_cells_norm))

        target_k = specs_obj.target_rooms(len(area_cells_norm))
        rooms_list = _reduce_rooms_to_target(rng, list(res.rooms), target_k)

        room_cells: Dict[int, List[Cell]] = {
            rid: sorted(list(cset))
            for rid, cset in enumerate(rooms_list, start=1)
        }

        inner_by_room, door_cand = _inner_walls_by_room_and_candidates(
            area_set, rooms_list, res.inner_walls
        )

        boundary = _boundary_edges(area_set)
        if boundary:
            ek, inner = rng.choice(boundary)
            entrance = {"edge_key": list(ek), "cell": [int(inner[0]), int(inner[1])]}
        else:
            entrance = {"edge_key": None, "cell": None}

        doors_export = _select_doors_min_connectivity(
            rng, room_cells, door_cand, connectivity=float(specs_obj.connectivity)
        )

        windows_export: List[Dict[str, Any]] = []
        if boundary:
            boundary_edges = [ek for (ek, _inner) in boundary]
            if entrance.get("edge_key"):
                ent = tuple(entrance["edge_key"])
                boundary_edges = [ek for ek in boundary_edges if ek != ent]
            boundary_edges = list(dict.fromkeys(boundary_edges))
            rng.shuffle(boundary_edges)

            wd = specs_obj.window_density_for_floor(n_floors, floor_i)
            wd = max(0.0, min(0.5, float(wd)))
            n_win = int(len(boundary_edges) * wd)
            n_win = max(0, min(n_win, int(specs_obj.window_cap)))

            for ek in boundary_edges[:n_win]:
                windows_export.append({"edge_key": list(ek), "length": 1})

        rooms_export: List[Dict[str, Any]] = []
        for rid in sorted(room_cells.keys()):
            cells = room_cells[rid]
            if not cells:
                continue
            rooms_export.append({
                "id": int(rid),
                "type": "generic",
                "name": f"Room_{rid}",
                "cells": [[int(x), int(y)] for (x, y) in cells],
                "narrow": [],
                "contour": None,
            })

        plan_export: Dict[str, Any] = {
            "floor_index": int(floor_i),
            "grid_w": int(grid_w),
            "grid_h": int(grid_h),
            "area_cells": [[int(x), int(y)] for (x, y) in area_cells_norm],
            "entrance": entrance,
            "rooms": rooms_export,
            "doors": doors_export,
            "windows": windows_export,
            "stairs": [],
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
    }
