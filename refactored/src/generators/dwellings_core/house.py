# refactored/src/generators/dwellings_core/house.py
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import deque
from math import inf

from .rng import RNG
from .specs import Specs
from .shape import Edge, outline_edges, contour2area  # Edge 是 plan.py 里 inner_walls 的元素类型
from .footprint import irregularize_area_by_notches, get_notch_cut, apply_notch_if_valid
from .plan import PlanDivider, spawn_windows_excluding, is_narrow, merge_corridors_like_js

Cell = Tuple[int, int]
EdgeKey = Tuple[str, int, int]  # ("V", x, y) or ("H", x, y)


@dataclass(eq=False)
class Door:
    room1: int
    room2: int
    edge_key: EdgeKey
    price: float

    def other(self, rid: int) -> int:
        return self.room2 if rid == self.room1 else self.room1


@dataclass
class RoomNode:
    rid: int
    area: set[Cell]
    doors: dict[int, Door] = field(default_factory=dict)


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

def _pick_entrance(rng: RNG, area_set: Set[Cell]) -> Dict[str, Any]:
    boundary = _boundary_edges(area_set)  # (edge_key, inner_cell)
    if not boundary:
        return {}
    # 避开“角落”边：inner_cell 的 4 邻居在 area_set 里的数量至少 2
    cand = []
    for ek, c in boundary:
        deg = sum((nb in area_set) for nb in _neighbors4(c[0], c[1]))
        if deg >= 2:
            cand.append((ek, c))
    ek, c = rng.choice(cand if cand else boundary)
    return {"edge_key": list(ek), "inner_cell": [c[0], c[1]]}

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

# -------------------------
# JS-like doors: connectRooms + wallDoors
# -------------------------

def _cell_degree_in_room(room: Set[Cell], c: Cell) -> int:
    """
    JS: deg(room, cell) = 1 + (# of cardinal neighbors inside the same room)
    """
    x, y = c
    deg = 1
    if (x + 1, y) in room: deg += 1
    if (x - 1, y) in room: deg += 1
    if (x, y + 1) in room: deg += 1
    if (x, y - 1) in room: deg += 1
    return deg


def _room_cost_js(room: Set[Cell]) -> int:
    """
    JS: roomCost = 10 if size==1 else (size - narrowLen)
    narrowLen uses is_narrow(area, cell) from plan.py (already JS-like).
    """
    n = len(room)
    if n <= 1:
        return 10
    narrow_len = sum(1 for c in room if is_narrow(room, c))
    return int(n - narrow_len)


def _door_price_js(room_a: Set[Cell], room_b: Set[Cell]) -> int:
    return _room_cost_js(room_a) + _room_cost_js(room_b)


# JS-like door connection constants and helpers
CARDINAL = [(1,0), (-1,0), (0,1), (0,-1)]

def _deg(cell: Cell, room_area: set[Cell], used_cells: set[Cell]) -> int:
    i, j = cell
    d = 1
    for di, dj in CARDINAL:
        if (i+di, j+dj) in room_area:
            d += 1
    if cell in used_cells:
        d *= 2
    return d


def connect_rooms_js(rng, room_nodes: list[RoomNode], candidates: dict[tuple[int,int], list[EdgeKey]], room_cost_fn):
    # adjacency map: rid -> other_rid -> [EdgeKey...]
    adj: dict[int, dict[int, list[EdgeKey]]] = {r.rid: {} for r in room_nodes}
    for (a, b), edges in candidates.items():
        if not edges:
            continue
        adj[a][b] = edges
        adj[b][a] = edges

    used_cells: set[Cell] = set()  # JS 的 p
    visited: list[int] = []
    rid2node = {r.rid: r for r in room_nodes}

    for r in room_nodes:  # JS: for rooms in order
        rid = r.rid
        visited.append(rid)

        neigh = []
        for other, edges in adj[rid].items():
            if other in visited:
                continue
            if edges:
                neigh.append((other, edges))
        neigh.sort(key=lambda x: len(x[1]))  # edges.length 升序

        for other, edges in neigh:
            best_edges = []
            best_score = 10**9

            roomA = rid2node[rid].area
            roomB = rid2node[other].area

            for ek in edges:
                c1, c2 = _cells_for_edge_key(ek)  # 你已有的函数：返回门两侧两个 cell
                if c1 in roomA and c2 in roomB:
                    a_cell, b_cell = c1, c2
                else:
                    a_cell, b_cell = c2, c1

                score = _deg(a_cell, roomA, used_cells) + _deg(b_cell, roomB, used_cells)
                if score < best_score:
                    best_score = score
                    best_edges = [ek]
                elif score == best_score:
                    best_edges.append(ek)

            if not best_edges:
                continue

            chosen = rng.choice(best_edges)

            price = room_cost_fn(rid2node[rid], chosen) + room_cost_fn(rid2node[other], chosen)
            door = Door(room1=rid, room2=other, edge_key=chosen, price=price)

            rid2node[rid].doors[other] = door
            rid2node[other].doors[rid] = door

            # JS: ca.add(p, edge2cell(Fa)) + edge2cell(reverse)
            c1, c2 = _cells_for_edge_key(chosen)
            used_cells.add(c1)
            used_cells.add(c2)


# 兼容原有调用的包装函数
def _connect_rooms_like_js(
    rng: RNG,
    rooms_list: List[Set[Cell]],
    door_cand: Dict[Tuple[int, int], List[EdgeKey]],
) -> List[Dict[str, Any]]:
    """
    包装新的 connect_rooms_js 函数，保持原有调用接口
    """
    n_rooms = len(rooms_list)
    if n_rooms <= 1:
        return []

    # 创建 RoomNode 对象
    room_nodes = []
    for i in range(n_rooms):
        rid = i + 1
        area = set(rooms_list[i])
        room_nodes.append(RoomNode(rid=rid, area=area))

    # 定义 room_cost_fn
    def room_cost_fn(room_node: RoomNode, edge_key: EdgeKey) -> int:
        return _room_cost_js(room_node.area)

    # 调用新的连接函数
    connect_rooms_js(rng, room_nodes, door_cand, room_cost_fn)

    # 收集生成的门
    doors_all: List[Dict[str, Any]] = []
    seen_pairs = set()
    
    for node in room_nodes:
        for other_rid, door in node.doors.items():
            # 避免重复添加
            pair = (min(node.rid, other_rid), max(node.rid, other_rid))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            
            doors_all.append({
                "edge_key": list(door.edge_key),
                "r1": int(door.room1),
                "r2": int(door.room2),
                "door_type": "REGULAR",
                "price": door.price,
            })

    return doors_all


def _js_round(x: float) -> int:
    # JS Math.round：0.5 永远向上
    if x <= 0:
        return 0
    import math
    return int(math.floor(x + 0.5))


def wall_doors_js(rng, room_nodes: list[RoomNode], connectivity: float) -> set[Door]:
    to_wall: list[Door] = []
    if connectivity < 0:
        return set()

    rooms = room_nodes
    rid2node = {r.rid: r for r in rooms}

    # 1) dominated doors
    for r in rooms:
        for door in list(r.doors.values()):
            l = door.other(r.rid)
            h = door.price
            keep = True
            for mid in rooms:
                d1 = r.doors.get(mid.rid)
                d2 = rid2node[mid.rid].doors.get(l)
                if d1 and d2 and d1.price < h and d2.price < h:
                    keep = False
                    break
            if not keep:
                to_wall.append(door)

    # 2) collect all doors unique (JS ca.add)
    all_doors: list[Door] = []
    seen = set()
    for r in rooms:
        for d in r.doors.values():
            if id(d) not in seen:
                seen.add(id(d))
                all_doors.append(d)

    # remove dominated from candidate list d
    dominated_ids = {id(d) for d in to_wall}
    d = [x for x in all_doors if id(x) not in dominated_ids]

    # 3) Prim-like keep MST doors: remove chosen from d, remaining will be candidates to wall
    q = rooms[:]                 # unvisited
    visited = [q.pop(rng.choice(range(len(q))))]      # JS ca.pick removes from q
    while q:
        k: list[Door] = []
        # collect crossing edges between visited and q
        for v in visited:
            for u in q:
                dd = v.doors.get(u.rid)
                if dd and id(dd) not in dominated_ids:
                    # add unique
                    if id(dd) not in {id(x) for x in k}:
                        k.append(dd)

        # JS 假设一定有 k（房间图连通）
        g = min(k, key=lambda x: x.price)

        # choose endpoint that is still in q
        q_ids = {r.rid for r in q}
        b_rid = g.room1 if g.room1 in q_ids else g.room2

        # remove g from d (kept open)
        d = [x for x in d if id(x) != id(g)]

        # remove b from q, add to visited
        for i, rr in enumerate(q):
            if rr.rid == b_rid:
                visited.append(q.pop(i))
                break

    # 4) randomly wall subset of remaining d
    k = int(round(len(d) * (1 - connectivity)))
    k = max(0, min(k, len(d)))
    if k > 0:
        rng.shuffle(d)
        extra = d[:k]
    else:
        extra = []
    to_wall.extend(extra)

    return set(to_wall)


def _wall_doors_like_js(
    rng: RNG,
    doors_all: List[Dict[str, Any]],
    n_rooms: int,
    connectivity: float,
) -> List[Dict[str, Any]]:
    """
    包装新的 wall_doors_js 函数，保持原有调用接口
    """
    if n_rooms <= 1 or not doors_all:
        return doors_all or []

    connectivity = float(connectivity)
    if connectivity < 0:
        return doors_all

    # 创建 RoomNode 和 Door 对象
    # 1. 先创建空 RoomNode
    room_nodes = [RoomNode(rid=i+1, area=set()) for i in range(n_rooms)]
    rid2node = {r.rid: r for r in room_nodes}
    
    # 2. 创建 Door 对象并添加到对应 RoomNode
    door_objects: Dict[Tuple[int, int], Door] = {}
    for d in doors_all:
        r1 = int(d["r1"])
        r2 = int(d["r2"])
        edge_key = tuple(d["edge_key"])
        price = float(d["price"])
        
        # 创建 Door 对象
        door = Door(room1=r1, room2=r2, edge_key=edge_key, price=price)
        
        # 双向关联
        rid2node[r1].doors[r2] = door
        rid2node[r2].doors[r1] = door
        door_objects[(r1, r2)] = door
        door_objects[(r2, r1)] = door

    # 调用新的 wall_doors_js 函数
    to_wall = wall_doors_js(rng, room_nodes, connectivity)
    walled_ids = {id(d) for d in to_wall}
    
    # 过滤掉要砌墙的门
    result = []
    for d in doors_all:
        r1 = int(d["r1"])
        r2 = int(d["r2"])
        door_obj = door_objects[(r1, r2)]
        if id(door_obj) not in walled_ids:
            result.append(d)
    
    return result


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


def _force_stairwell_only_connect_landing(
    door_cand: Dict[Tuple[int, int], List[EdgeKey]],
    rooms_list: List[Set[Cell]],
    stair_cell: Cell,
    landing_cell: Cell,
    stairwell_rid: int = 1,
) -> None:
    """
    JS 复刻：stairwell 房间只允许与 landing 所在房间通过“指定边”相连。
    - 清空 door_cand 中所有涉及 stairwell_rid 的候选
    - 写回唯一候选：edge(stair_cell, landing_cell)
    """
    if stair_cell is None or landing_cell is None:
        return

    owner = _build_cell_owner_from_rooms(rooms_list)
    landing_rid = owner.get(landing_cell)
    if landing_rid is None:
        # landing 不在任何 room 里，说明前面分割/退台把它弄丢了，直接不强制（但你应该去修 core_cells 保留）
        return
    if landing_rid == stairwell_rid:
        return

    ek = _edge_key_between(stair_cell, landing_cell)
    if ek is None:
        return

    # 1) 删除所有 stairwell 相关候选
    for k in list(door_cand.keys()):
        if stairwell_rid in k:
            del door_cand[k]

    # 2) 只保留 stairwell <-> landing_rid 的唯一边
    a, b = sorted((stairwell_rid, landing_rid))
    door_cand[(a, b)] = [ek]


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
# validation helpers
# -------------------------
def _sha1_json(obj: Any) -> str:
    """
    把 obj 做成稳定的 sha1（排序键、固定分隔符，避免 dict 顺序影响）
    返回前 12 位，足够对比。
    """
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def _sig_doors(doors_export: List[Dict[str, Any]]) -> List[Tuple]:
    """
    生成 door 的稳定签名列表：排序后用于 hash
    """
    sig = []
    for d in doors_export or []:
        ek = d.get("edge_key") or d.get("edge") or []
        door_type = d.get("door_type") or ""
        
        # Properly handle edge_key: first element is string ('H'/'V'), rest are integers
        ek_tuple = []
        for i, x in enumerate(ek):
            if i == 0:  # First element is type ('H' or 'V')
                ek_tuple.append(str(x))
            else:       # Rest are coordinates, convert to integers
                ek_tuple.append(int(x))
        
        sig.append((
            int(d.get("r1", 0)),
            int(d.get("r2", 0)),
            door_type,
            tuple(ek_tuple),
        ))
    sig.sort()
    return sig


def _sig_edge_keys(edge_keys: List[Any]) -> List[Tuple]:
    """
    windows/stairs 若用 edge_key 表示，就用这个做稳定签名
    """
    sig = []
    for ek in edge_keys or []:
        # ek 可能是 list，也可能是 tuple
        ek = tuple(ek)
        sig.append(tuple(int(x) if i else x for i, x in enumerate(ek)))
    sig.sort()
    return sig


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


# def _find_stair_core_cells(area: Set[Cell], rng: RNG) -> Set[Cell]:
#     """
#     找一个“楼梯 core”，优先 2x2；找不到就降级到 1x2；再不行就单格。
#     返回的是要永远保留的 cells 集合。
#     """
#     if not area:
#         return set()
# 
#     cx, cy = _area_centroid(area)
# 
#     # 1) 2x2
#     cand_2x2: List[Tuple[float, Set[Cell]]] = []
#     for (x, y) in area:
#         core = {(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)}
#         if core.issubset(area):
#             # 距离中心越近越好
#             dx = (x + 1.0) - cx
#             dy = (y + 1.0) - cy
#             cand_2x2.append((dx * dx + dy * dy, core))
#     if cand_2x2:
#         cand_2x2.sort(key=lambda t: t[0])
#         # 同分随机扰动一下，避免总是同一个
#         best_d = cand_2x2[0][0]
#         best = [c for d, c in cand_2x2 if abs(d - best_d) < 1e-9]
#         return set(rng.choice(best))
# 
#     # 2) 1x2
#     cand_1x2: List[Tuple[float, Set[Cell]]] = []
#     for (x, y) in area:
#         core_h = {(x, y), (x + 1, y)}
#         core_v = {(x, y), (x, y + 1)}
#         for core in (core_h, core_v):
#             if core.issubset(area):
#                 dx = (x + 0.5) - cx
#                 dy = (y + 0.5) - cy
#                 cand_1x2.append((dx * dx + dy * dy, core))
#     if cand_1x2:
#         cand_1x2.sort(key=lambda t: t[0])
#         best_d = cand_1x2[0][0]
#         best = [c for d, c in cand_1x2 if abs(d - best_d) < 1e-9]
#         return set(rng.choice(best))
# 
#     # 3) 单格
#     # 选离中心最近的那一格
#     best_cell = None
#     best_dist = inf
#     for (x, y) in area:
#         dx = (x + 0.5) - cx
#         dy = (y + 0.5) - cy
#         d = dx * dx + dy * dy
#         if d < best_dist:
#             best_dist = d
#             best_cell = (x, y)
#     return {best_cell} if best_cell else set()

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


def _carve_cells_into_new_room(
    rooms_list: List[Set[Cell]],
    carve: Set[Cell],
) -> Tuple[List[Set[Cell]], Set[Cell]]:
    """
    把 carve 这一坨格子从现有 rooms_list 中挖走，剩下的每个房间若被挖断则拆成连通分量。
    返回 (new_rooms_list, carved_room_cells)
    """
    carve = set(carve)
    if not carve:
        return list(rooms_list), set()

    new_rooms: List[Set[Cell]] = []
    for r in rooms_list:
        rem = set(r) - carve
        if not rem:
            continue
        # 保证每个房间连通：rem 若断开，拆分
        new_rooms.extend(_connected_components(rem))

    carved_room = set(carve)
    return new_rooms, carved_room



def _pick_landing_cell(stair_cells: Set[Cell]) -> Optional[Cell]:
    if not stair_cells:
        return None
    # 用排序保证稳定
    return sorted(stair_cells)[0]


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




def _target_ratio_range(size_class: str) -> Tuple[float, float]:
    # 这是“房屋占地占地块(envelope)的比例”
    if size_class == "small":  return (0.35, 0.55)
    if size_class == "large":  return (0.75, 0.92)
    return (0.55, 0.75)


def _make_footprint_from_envelope(
    envelope_cells: List[Cell],
    *,
    rng: RNG,
    specs_obj: Specs,
    footprint_mode: str,
) -> List[Cell]:
    """
    把 envelope（地块）变成底层 footprint。
    - full: 直接用 envelope
    - fit/orig: 在 envelope bbox 里随机矩形，再与 envelope 相交，逼近目标面积比例
    最后做 notches + 轮廓回填，保证形状稳定连通。
    """
    env: Set[Cell] = set((int(x), int(y)) for x, y in envelope_cells)
    if not env:
        return []

    if footprint_mode == "full":
        fp: Set[Cell] = set(env)
    else:
        # fit / orig 走同一套“随机矩形裁剪”策略
        xs = [x for x, _ in env]
        ys = [y for _, y in env]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)

        size_class = specs_obj.infer_size_class(len(env))
        r0, r1 = _target_ratio_range(size_class)

        target = int(round(len(env) * (r0 + (r1 - r0) * rng.random())))
        target = max(4, target)

        best: Optional[Set[Cell]] = None

        for _ in range(30):
            # 根据 target 粗估 w/h，再加扰动
            w = max(3, int(round((target ** 0.5) * (0.8 + 0.4 * rng.random()))))
            h = max(3, int(round(target / max(1, w))))

            # bbox 里随机放置
            x0_max = maxx - w + 1
            y0_max = maxy - h + 1
            x0 = rng.randint(minx, x0_max) if x0_max >= minx else minx
            y0 = rng.randint(miny, y0_max) if y0_max >= miny else miny

            rect = {(x, y) for x in range(x0, x0 + w) for y in range(y0, y0 + h)}
            cand = rect & env
            if not cand:
                continue

            if best is None or len(cand) > len(best):
                best = cand

            # 够接近就提前停
            if len(cand) >= int(target * 0.9):
                break

        fp = best if best else set(env)

    # ✅ 额外做一次 Dwellings 风格的 notches
    fp_list = sorted(list(fp))
    fp_list = irregularize_area_by_notches(fp_list, rng, max_notches=3)

    # ✅ 轮廓回填归一化：确保边界稳定
    cont = outline_edges(set(fp_list))
    fp_list = contour2area(cont)

    return fp_list


def pick_entrance_edge_js(rng: RNG, contour_edges: List[Edge]) -> Edge:
    """
    按边是否位于直线段/拐角类型给权重，加权选择入口边
    权重规则：
    - 连续三条同方向：权重 5
    - 连续两次顺时针转：权重 3
    - 两侧都“不满足 ccw 关系”：权重 1
    - 否则 0
    """
    n = len(contour_edges)
    weights = []
    
    for i, e in enumerate(contour_edges):
        prev_edge = contour_edges[(i-1) % n]
        cur_edge = e
        next_edge = contour_edges[(i+1) % n]
        
        prev_dir = prev_edge.dir
        cur_dir = cur_edge.dir
        next_dir = next_edge.dir

        if prev_dir == cur_dir and cur_dir == next_dir:
            w = 5
        elif prev_dir.cw == cur_dir and cur_dir.cw == next_dir:
            w = 3
        elif prev_dir.ccw != cur_dir and cur_dir.ccw != next_dir:
            w = 1
        else:
            w = 0
        weights.append(w)

    return rng.weighted(contour_edges, weights)

# -------------------------
# public API
# -------------------------
def generate_house_export(    *,
    seed: int,
    tags: List[str],
    area_cells: List[Tuple[int, int]],
    footprint_mode: str = "fit",   # "full" | "fit" | "orig"
    n_floors: int,
) -> Dict[str, Any]:
    base_rng = RNG(int(seed))
    
    # 提前创建 specs_obj，用于 size_class 推断
    specs_obj = Specs.from_tags(tags)

    area_cells_norm: List[Cell] = [(int(x), int(y)) for x, y in area_cells]
    '''
    # ✅ Step2：先改 footprint（不规则 + 凹口）
    area_cells_norm = irregularize_area_by_notches(
        area_cells_norm,
        base_rng,
        max_notches=3,
    )
    
    # 轮廓回填归一化：确保形状稳定，便于后续处理
    cont = outline_edges(set(area_cells_norm))
    area_cells_norm = contour2area(cont)
    '''
    area_cells_norm: List[Cell] = [(int(x), int(y)) for x, y in area_cells]
    envelope_cells = area_cells_norm[:]  # building_area 栅格化后的地块

    # ✅ 使用新函数生成 footprint
    area_cells_norm = _make_footprint_from_envelope(
        envelope_cells,
        rng=base_rng,
        specs_obj=specs_obj,
        footprint_mode=footprint_mode,
    )

    # grid size（用底层算就行）
    grid_w, grid_h = _grid_size_from_cells(area_cells_norm)

    specs = specs_obj.to_dict()

    floors: List[Dict[str, Any]] = []
    n_floors = max(1, int(n_floors))
    
    # ✅ 检查是否需要楼梯 - 放在n_floors定义后立即执行
    has_stairs = (n_floors > 1)

    parity: Dict[str, Any] = {
        "seed": int(seed),
        "tags": list(tags),
        "floors": [],
    }

    generate_setback = _should_generate_setback(tags, n_floors)

    # ✅ 默认有露台；传 no_terrace 才禁用
    allow_terrace = specs_obj.allow_terrace
    
    # ✅ 选一个“楼梯 core”，后续楼层退台必须保留它
    base_area_set = set(area_cells_norm)
    core_cells = set()
    
    # ✅ 提前选entrance，用于排除stair
    entrance = _pick_entrance(base_rng, base_area_set)  # 提前选entrance
    entrance_landing = tuple(entrance.get("inner_cell", (0, 0)))  # 默认值避免None
    
    # ✅ 如果有楼梯，使用JS风格选点并保存spec
    stairwell_spec = None
    if has_stairs:
        from .roomtypes.stairwell import roll_stairwell_like_js
        stairwell_spec = roll_stairwell_like_js(base_area_set, entrance_landing, base_rng)
        if stairwell_spec:
            # core_cells需要包含stair和landing，确保上层退台时不会被切掉
            core_cells = {stairwell_spec.stair, stairwell_spec.landing}

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
        
        # ============ NEW: JS-style stairwell first ============
        # 使用固定的 stairwell_spec，不再从 core_cells 随机取
        stair_cell = None
        landing_cell = None
        stair_room = set()
        stairwell_rid = None
        
        # 计算分割区域：如果有楼梯，移除 stair 单格，只分割剩余区域
        divide_area = current_area_set.copy()
        if has_stairs and stairwell_spec:
            # 使用固定的 stair 和 landing 单元格
            stair_cell = stairwell_spec.stair
            landing_cell = stairwell_spec.landing
            
            # 确保 stair_cell 在当前楼层中
            if stair_cell in current_area_set:
                # 创建单格 stair_room
                stair_room = {stair_cell}
                # 从分割区域中移除 stair_cell（仅移除楼梯，保留 landing）
                divide_area.remove(stair_cell)
            
        # 分割剩余区域
        divider = PlanDivider(rng, specs_obj)
        res = divider.divide(divide_area)

        # JS 顺序：stairwell 先入 rooms，再 divide 剩余 area
        if stair_room:
            # stair_room 作为单格房间先加入，rid=1
            rooms_list = [stair_room] + list(res.rooms)
            stairwell_rid = 1
        else:
            # 没有楼梯时，使用正常分割结果
            rooms_list = list(res.rooms)
        # ============ END NEW ============
        
        # JS-style corridor merging: merge only when shared edges == 1
        rooms_list = merge_corridors_like_js(
            rooms_list,
            stair_room=stair_room if stair_room else None
        )

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

        # ---------- Force Stairwell Connection ----------
        if has_stairs and stair_room:
            # 获取 stair_cell（stair_room 是单格）
            stair_cell = next(iter(stair_room))
            # 强制 stairwell 只连接 landing
            _force_stairwell_only_connect_landing(
                door_cand,
                rooms_list,
                stair_cell,
                landing_cell,
                stairwell_rid=stairwell_rid
            )

        # ---------- Entrance Generation (floor0 only) ----------
        blocked_edge_keys: Set[Tuple[str, int, int]] = set()

        if floor_i == 0:
            # 使用提前选好的entrance
            ek = entrance.get("edge_key")
            if ek:
                blocked_edge_keys.add(tuple(ek))  # 给 windows 排除

        # JS-like: connectRooms then wallDoors
        doors_all = _connect_rooms_like_js(
            rng,
            rooms_list,
            door_cand,
        )

        doors_export = _wall_doors_like_js(
            rng,
            doors_all,
            n_rooms=len(rooms_list),
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
        windows_export = []
        stairs_export = []
        
        # ---- stairs_export：跨层连接信息 ----
        if has_stairs and stairwell_rid is not None:
            # 使用 stair_room 中的 cells（仅包含 stair 单元格）
            stair_cells = list(stair_room)
            # 每层记录“这层 stairwell 房间”以及是否连接下一层
            stairs_export.append({
                "type": specs_obj.stairs_mode,
                "room_id": int(stairwell_rid),
                "cells": [[int(x), int(y)] for (x, y) in sorted(stair_cells)],
                "connects_to": int(floor_i + 1) if floor_i < n_floors - 1 else None,
            })
        
        # ---- windows (JS spawnWindowsExcluding) ----
        # room_by_cell: Cell -> rid（只对室内 rooms_list）
        owner_indoor = _build_cell_owner_from_rooms(rooms_list)

        # excluded_rooms：blank/transparent 在 spawn_windows_excluding 内部处理；
        # 这里主要排除 stairwell
        excluded_rooms = []
        if stairwell_rid is not None:
            excluded_rooms.append(int(stairwell_rid))

        # density：JS-like 楼层密度（上层更密）
        # 使用 specs 中配置的公式：0.9 - 0.1*(nFloors - floorIndex)
        density = specs_obj.window_density_for_floor(n_floors, floor_i)

        # Generate windows using JS-style segment-based approach
        cont = outline_edges(current_area_set)  # ordered contour edges
        window_edge_keys = spawn_windows_excluding(
            rng=rng,
            contour_edges=cont,
            room_by_cell=owner_indoor,
            excluded_rooms=excluded_rooms,
            blocked_edge_keys=blocked_edge_keys,
            density=density,
            window_mode=specs_obj.window_mode,
        )
        
        # Convert to export format - maintain [[typ,x,y], ...] for parity compatibility
        windows_export = [{"edge_key": [ek[0], int(ek[1]), int(ek[2])]} for ek in window_edge_keys]

        # rooms_export：按 rid 判定类型
        rooms_export = []
        indoor_n = len(rooms_list)  # 这里 rooms_list 已含 stairwell（若有）
        for rid in sorted(room_cells.keys()):
            cells = room_cells[rid]
            if not cells:
                continue

            is_terrace = (rid > indoor_n)
            if is_terrace:
                rtype = "terrace"
                rname = f"Terrace_{rid - indoor_n:02d}"
            else:
                if stairwell_rid is not None and rid == stairwell_rid:
                    rtype = "stairwell"
                    rname = "Stairwell"
                else:
                    rtype = "generic"
                    rname = f"Room_{rid}"

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
        
        # Only add entrance to ground floor
        if floor_i == 0:
            plan_export["entrance"] = entrance

        # ---------- Parity Debug (no RNG consumed) ----------
        # door_cand 是 dict[(r1,r2)] -> List[EdgeKey]
        door_pair_count = len(door_cand or {})
        door_edge_total = sum(len(v) for v in (door_cand or {}).values())

        room_sizes_indoor = sorted(len(cset) for cset in (rooms_list or []))
        room_sizes_all = sorted(len(cset) for cset in (rooms_all or []))

        # inner walls：res.inner_walls 是 List[List[Edge]]
        inner_wall_chains = res.inner_walls or []
        inner_wall_edges = sum(len(chain) for chain in inner_wall_chains)

        floor_sig = {
            "floor_i": int(floor_i),
            "area_cells": int(len(plan_export.get("area_cells") or [])),
            "terrace_cells": int(len(plan_export.get("terrace_cells") or [])),
            "rooms_indoor": int(len(rooms_list)),
            "rooms_total": int(len(rooms_all)),
            "room_sizes_indoor": room_sizes_indoor[:20],  # 太长就截断，避免刷屏
            "room_sizes_all": room_sizes_all[:20],
            "door_pairs": int(door_pair_count),
            "door_edges_total": int(door_edge_total),
            "doors_open": int(len(doors_export or [])),
            "windows": int(len(windows_export or [])),
            "stairs": int(len(stairs_export or [])),
            "inner_wall_chains": int(len(inner_wall_chains)),
            "inner_wall_edges": int(inner_wall_edges),
        }

        # 稳定 hash：只用“签名”，避免巨大 JSON
        door_hash = _sha1_json(_sig_doors(doors_export))
        
        # Extract edge_keys from windows_export (which is list[dict])
        windows_edge_keys = [w["edge_key"] for w in (windows_export or [])]
        win_hash = _sha1_json(_sig_edge_keys(windows_edge_keys))
        
        stair_hash = _sha1_json(stairs_export)                 # 你现在 stairs_export 还是 []

        floor_sig["hash"] = {
            "doors": door_hash,
            "windows": win_hash,
            "stairs": stair_hash,
            "floor": _sha1_json({
                "doors": _sig_doors(doors_export),
                "windows": _sig_edge_keys(windows_edge_keys),
                "stairs": stairs_export,
            }),
        }

        parity["floors"].append(floor_sig)
        # ---------- End Parity Debug ----------

        validate_plan_export(plan_export)
        floors.append(plan_export)

    # Calculate overall hash
    parity["overall_hash"] = _sha1_json([f["hash"]["floor"] for f in parity["floors"]])

    return {
        "seed": int(seed),
        "tags": list(tags),
        "specs": specs,
        "floors": floors,
        "has_setback": bool(generate_setback),
        "n_floors": int(n_floors),
        "stair_core_cells": [[int(x), int(y)] for (x, y) in sorted(core_cells)],
        "_parity": parity,
    }


