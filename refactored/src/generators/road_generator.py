# src/generators/road_generator.py
"""
道路生成器（RoadGenerator v2，节点图式路网）

核心契约（见 readme 路线图 §2 与用户规则）：
1. 道路 = room_type='road' 的特殊房间；other_json.connects 记录两个端点
   （{"kind":"room"|"road","id":..}），传递连通用并查集追踪（房间+道路都是节点）。
2. **路网结构（非链式）**：房间先两两配对（1-2、3-4），配对道路之间互相连接
   （R1 接到 R2），剩余房间挂到道路上（5、6 接道路），形成树+分支的网状。
   起点优先级：无道路房间 -> 任意房间 -> 道路（道路互联，主干贯通）。
3. 道路形态：最多 2 个直角折角；**Z 形中间段 ≥ 3×宽度**（宽5 => 中间段≥15）。
4. 出发点 = 房间外墙门（墙洞）：洞宽=道路宽度，**不在外墙拐角处开门**
   （洞两端各留 DOOR_MARGIN=2 格墙）。
5. **道路不延伸进房屋**：路径从墙洞外侧 2 格出发（带边缘贴外墙），
   road space 不含任何房间 space；road 与房间**共用外墙**（贴墙处不重复画墙）。
6. 交叉：道路带相交 => 双方墙在交叉处打通，区域为联通空间，connects 记录道路。
7. **门清理**：只在外墙、没连接道路/房间的门删除（保留 road_entrance 与建筑内两房间门）。
"""
from __future__ import annotations

import heapq
import json
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from ..db.database import DatabaseManager

JsonDict = Dict[str, Any]
Cell = Tuple[int, int]

DIRS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIRS8 = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if not (dx == 0 and dy == 0)]


class UnionFind:
    """并查集：房间与道路统一为节点。"""

    def __init__(self):
        self.parent: Dict[int, int] = {}
        self.rank: Dict[int, int] = {}

    def make_set(self, x: int) -> None:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0

    def find(self, x: int) -> int:
        if self.parent.get(x) != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            self.parent[rx] = ry
        elif self.rank[rx] > self.rank[ry]:
            self.parent[ry] = rx
        else:
            self.parent[ry] = rx
            self.rank[rx] += 1

    def connected(self, x: int, y: int) -> bool:
        return self.find(x) == self.find(y)


class RoadGenerator:
    ROAD_WIDTH = 5
    MAX_TURNS = 2
    DOOR_MARGIN = 2          # 门洞两端各留的墙格数（不在拐角开门）

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

    # ==================================================================
    # helpers
    # ==================================================================
    @staticmethod
    def _loads(s: Optional[str], default: Any = None) -> Any:
        if not s:
            return default
        try:
            return json.loads(s)
        except Exception:
            return default

    @staticmethod
    def _dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False)

    def _fetch_rooms(self, map_id: int, layer: int, room_type: Optional[str] = None) -> List[JsonDict]:
        sql = (
            "SELECT id, building_area_id, name, room_type, geom_json, tiles_json, other_json "
            "FROM room WHERE map_id = ? AND layer_start <= ? AND layer_end >= ? "
        )
        params: List[Any] = [int(map_id), int(layer), int(layer)]
        if room_type is not None:
            sql += "AND room_type = ? "
            params.append(room_type)
        sql += "ORDER BY id ASC"
        return self.db.fetch_all(sql, tuple(params)) or []

    @staticmethod
    def _tile_cells(tiles: Dict[str, Any], keys: Tuple[str, ...]) -> Set[Cell]:
        out: Set[Cell] = set()
        for key in keys:
            for c in tiles.get(key) or []:
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    out.add((int(c[0]), int(c[1])))
        return out

    @staticmethod
    def _room_center(room: JsonDict) -> Tuple[float, float]:
        geom = RoadGenerator._loads(room.get("geom_json"), {})
        if isinstance(geom, dict) and isinstance(geom.get("center"), list) and len(geom["center"]) == 2:
            return float(geom["center"][0]), float(geom["center"][1])
        tiles = RoadGenerator._loads(room.get("tiles_json"), {})
        sp = tiles.get("space") or []
        if sp:
            xs = [s[0] for s in sp]
            ys = [s[1] for s in sp]
            return sum(xs) / len(xs), sum(ys) / len(ys)
        return 0.0, 0.0

    # ==================================================================
    # 外墙门：洞宽=路宽，洞两端留墙（不在拐角），洞格从 wall 移除
    # ==================================================================
    def _ensure_exterior_door(
        self,
        map_id: int,
        layer: int,
        room: JsonDict,
        obstacles: Set[Cell],
        rng: random.Random,
        toward: Optional[Tuple[float, float]] = None,
    ) -> Optional[JsonDict]:
        rid = int(room["id"])

        # 设定：一个建筑区建筑最多 3 个往外联通的门。
        # 已有 ≥3 个 road_entrance 门 -> 复用第一个门洞（不再挖新洞）；
        # 已有 <3 个 -> 可继续开新门（不同方向）。
        existing_doors = self.db.fetch_all(
            "SELECT tiles_json FROM item WHERE map_id = ? AND room_id = ? "
            "AND item_type = 'door' AND properties_json LIKE '%road_entrance%'",
            (int(map_id), rid),
        ) or []
        if len(existing_doors) >= 3:
            tj = self._loads(existing_doors[0].get("tiles_json"), {})
            wt = tj.get("wall_tiles") or []
            door_cells = sorted(set(
                (int(c[0]), int(c[1])) for c in wt
                if isinstance(c, (list, tuple)) and len(c) == 2
            ))
            if door_cells:
                return {
                    "room_id": rid,
                    "door_cells": door_cells,
                    "door_center": (
                        sum(x for x, _ in door_cells) / len(door_cells) + 0.5,
                        sum(y for _, y in door_cells) / len(door_cells) + 0.5,
                    ),
                }

        row = self.db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (rid,))
        tiles = self._loads(row["tiles_json"], {}) if row else self._loads(room.get("tiles_json"), {})
        wall = self._tile_cells(tiles, ("wall",))

        # 外墙墙格：4 邻存在空地
        exterior = [
            (x, y) for (x, y) in wall
            if any((x + dx, y + dy) not in obstacles for dx, dy in DIRS4)
        ]
        if not exterior:
            return None

        # 段长必须 ≥ 路宽 + 两端留墙，保证洞不在拐角
        min_len = self.ROAD_WIDTH + 2 * self.DOOR_MARGIN
        segments = [s for s in self._wall_segments(exterior) if len(s) >= min_len]

        if not segments:
            # 退化：圆屋/弧形外墙无足够长的直线段——取最长段并沿外墙延伸凑齐洞宽
            all_seg = self._wall_segments(exterior)
            if not all_seg:
                return None
            seg = max(all_seg, key=len)
            seg_sorted = sorted(seg, key=lambda c: (c[0], c[1]))
            door_set_cur = set(seg_sorted)
            while len(door_set_cur) < self.ROAD_WIDTH:
                grown = False
                for (x, y) in list(door_set_cur):
                    for dx, dy in DIRS4:
                        nb = (x + dx, y + dy)
                        if nb in exterior and nb not in door_set_cur:
                            door_set_cur.add(nb)
                            grown = True
                            break
                    if grown:
                        break
                if not grown:
                    break
            door_cells = sorted(door_set_cur)[:self.ROAD_WIDTH]
            door_set = set(door_cells)
            new_wall = sorted(w for w in tiles.get("wall") or [] if (int(w[0]), int(w[1])) not in door_set)
            tiles["wall"] = new_wall
            self.db.execute(
                "UPDATE room SET tiles_json = ? WHERE id = ?",
                (self._dumps(tiles), rid),
            )
            door_center = (
                sum(x for x, _ in door_cells) / len(door_cells) + 0.5,
                sum(y for _, y in door_cells) / len(door_cells) + 0.5,
            )
            return {
                "room_id": rid,
                "door_cells": sorted(door_cells),
                "door_center": door_center,
            }

        if toward is not None:
            def _seg_key(seg: List[Cell]) -> float:
                cx = sum(x for x, _ in seg) / len(seg)
                cy = sum(y for _, y in seg) / len(seg)
                return abs(cx - toward[0]) + abs(cy - toward[1])
            segments.sort(key=_seg_key)
            seg = segments[0]
        else:
            seg = rng.choice(segments)

        seg_sorted = sorted(seg, key=lambda c: (c[0], c[1]))
        start = (len(seg_sorted) - self.ROAD_WIDTH) // 2
        center_cell = seg_sorted[start + self.ROAD_WIDTH // 2]  # 洞中心（交界处原点）

        # 门 = 以交界处原点为圆心、半径 = 路宽/2 覆盖的外墙格
        # （用户规则：覆盖到的外墙都是门）
        r = max(1, self.ROAD_WIDTH // 2)
        door_set: Set[Cell] = set()
        for (x, y) in exterior:
            if abs(x - center_cell[0]) + abs(y - center_cell[1]) <= r:
                door_set.add((x, y))
        if len(door_set) < 3:
            # 兜底：至少取洞中心附近连续 width 格
            door_set = set(seg_sorted[start:start + self.ROAD_WIDTH])
        door_cells = sorted(door_set)

        # 洞格从 wall 移除（墙洞），room.space 不变（道路不延伸进房屋）
        new_wall = sorted(w for w in tiles.get("wall") or [] if (int(w[0]), int(w[1])) not in door_set)
        tiles["wall"] = new_wall
        self.db.execute(
            "UPDATE room SET tiles_json = ? WHERE id = ?",
            (self._dumps(tiles), rid),
        )

        door_center = (
            sum(x for x, _ in door_cells) / len(door_cells) + 0.5,
            sum(y for _, y in door_cells) / len(door_cells) + 0.5,
        )

        return {
            "room_id": rid,
            "door_cells": door_cells,
            "door_center": door_center,
        }

    @staticmethod
    def _wall_segments(exterior: List[Cell]) -> List[List[Cell]]:
        """外墙墙格分为纯直线段（水平或垂直，不跨拐角）。"""
        segs: List[List[Cell]] = []
        remaining = set(exterior)
        while remaining:
            seed = min(remaining)
            x, y = seed
            if (x + 1, y) in remaining or (x - 1, y) in remaining:
                seg = [seed]
                remaining.discard(seed)
                for dx in (-1, 1):
                    cx = x
                    while (cx + dx, y) in remaining:
                        cx += dx
                        seg.append((cx, y))
                        remaining.discard((cx, y))
                segs.append(sorted(seg))
            elif (x, y + 1) in remaining or (x, y - 1) in remaining:
                seg = [seed]
                remaining.discard(seed)
                for dy in (-1, 1):
                    cy = y
                    while (x, cy + dy) in remaining:
                        cy += dy
                        seg.append((x, cy))
                        remaining.discard((x, cy))
                segs.append(sorted(seg))
            else:
                segs.append([seed])
                remaining.discard(seed)
        return segs

    @staticmethod
    def _door_exterior_point(ep: JsonDict, room_space: Set[Cell]) -> Cell:
        """
        门洞中心向房间外侧移 2 格（带边缘贴外墙，不进入房屋）。

        外侧方向判定（修复：basic 模型 wall ⊂ space，洞格沿墙邻格也在 space，
        不能按"邻格在 space 即为内侧"判断——会把沿墙方向误判为内侧）：
        外侧 = 洞格 4 邻中**不在 space、且其反方向邻格在 space** 的方向
        （即垂直于墙、朝外）。
        """
        dcx, dcy = int(ep["door_center"][0]), int(ep["door_center"][1])
        for dx, dy in DIRS4:
            if (dcx + dx, dcy + dy) not in room_space and (dcx - dx, dcy - dy) in room_space:
                return (dcx + dx * (RoadGenerator.ROAD_WIDTH // 2),
                        dcy + dy * (RoadGenerator.ROAD_WIDTH // 2))
        # 兜底：任一不在 space 的方向（拐角洞等）
        for dx, dy in DIRS4:
            if (dcx + dx, dcy + dy) not in room_space:
                return (dcx + dx * (RoadGenerator.ROAD_WIDTH // 2),
                        dcy + dy * (RoadGenerator.ROAD_WIDTH // 2))
        return (dcx, dcy)

    # ==================================================================
    # 路径：最多 2 折角；Z 形中间段 ≥ 3×宽度
    # ==================================================================
    def _generate_paths(self, start: Cell, end: Cell) -> List[List[Cell]]:
        sx, sy = start
        ex, ey = end
        paths: List[List[Cell]] = []
        min_mid = 3 * self.ROAD_WIDTH   # Z 形中间段最短长度（3×宽度）

        if sx == ex or sy == ey:
            paths.append([start, end])
            return paths

        # 1 折角：L 形
        paths.append([start, (ex, sy), end])
        paths.append([start, (sx, ey), end])

        # 2 折角：Z 形（中间段长度是固定的：垂直型=|ey-sy|，水平型=|ex-sx|）
        if abs(ey - sy) >= min_mid:
            step_x = max(2, (max(sx, ex) - min(sx, ex)) // 10)
            for mx in range(min(sx, ex) + 1, max(sx, ex), step_x):
                paths.append([start, (mx, sy), (mx, ey), end])
        if abs(ex - sx) >= min_mid:
            step_y = max(2, (max(sy, ey) - min(sy, ey)) // 10)
            for my in range(min(sy, ey) + 1, max(sy, ey), step_y):
                paths.append([start, (sx, my), (ex, my), end])

        return paths

    @staticmethod
    def _rasterize_path(waypoints: List[Cell]) -> Set[Cell]:
        cells: Set[Cell] = set()
        for i in range(len(waypoints) - 1):
            x1, y1 = waypoints[i]
            x2, y2 = waypoints[i + 1]
            dx, dy = abs(x2 - x1), abs(y2 - y1)
            sx_step = 1 if x1 < x2 else -1
            sy_step = 1 if y1 < y2 else -1
            err = dx - dy
            cx, cy = x1, y1
            while True:
                cells.add((cx, cy))
                if cx == x2 and cy == y2:
                    break
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    cx += sx_step
                if e2 < dx:
                    err += dx
                    cy += sy_step
        return cells

    def _band_for_path(self, waypoints: List[Cell]) -> Set[Cell]:
        half = self.ROAD_WIDTH // 2
        band: Set[Cell] = set()
        for (cx, cy) in self._rasterize_path(waypoints):
            for dx in range(-half, half + 1):
                for dy in range(-half, half + 1):
                    band.add((cx + dx, cy + dy))
        return band

    # ==================================================================
    # 路径合法性与交叉
    # ==================================================================
    def _find_valid_path(
        self,
        start_cell: Cell,
        end_cell: Cell,
        openings: Set[Cell],
        room_spaces: Dict[int, Set[Cell]],
        road_cells: Dict[int, Set[Cell]],
        rng: random.Random,
    ) -> Optional[Tuple[List[Cell], Set[Cell]]]:
        """
        候选路径中找第一条**不进入任何房间 space** 的（墙不是障碍：
        道路可与房屋共用外墙、贴墙绕行，但不得覆盖房间内部 space）。

        **优先更直**：候选按 _generate_paths 的生成顺序（直线 → L 形1折 → Z 形2折）
        依次尝试，不随机打乱——否则会选出"折两次"的路径即使直线/L 形也能到达。
        0/1/2 折角全失败后，用 A* 避让路径兜底（折角 ≤ 6）。
        """
        candidates = self._generate_paths(start_cell, end_cell)
        for path in candidates:
            band = self._band_for_path(path)
            if self._band_free_of_rooms(band, openings, room_spaces):
                return path, band

        # 兜底：A* 避让（折角 ≤ 6）
        astar = self._astar_avoidance(start_cell, end_cell, room_spaces)
        if astar:
            pts, turns = self._simplify_and_turns(astar)
            if turns <= 6:
                band = self._band_for_path(pts)
                if self._band_free_of_rooms(band, openings, room_spaces):
                    return pts, band
        return None

    @staticmethod
    def _band_free_of_rooms(band: Set[Cell], openings: Set[Cell],
                            room_spaces: Dict[int, Set[Cell]]) -> bool:
        for (x, y) in band:
            if (x, y) in openings:
                continue
            if any((x, y) in cells for cells in room_spaces.values()):
                return False
        return True

    def _astar_avoidance(
        self,
        start: Cell,
        end: Cell,
        room_spaces: Dict[int, Set[Cell]],
        max_steps: int = 80000,
    ) -> Optional[List[Cell]]:
        """A*：在非房间内部格上找最短路径（墙可走，贴合"道路贴墙"语义）。"""
        obstacles: Set[Cell] = set()
        for cells in room_spaces.values():
            obstacles |= cells
        if start in obstacles or end in obstacles:
            return None
        open_heap: List[Tuple[int, int, Cell]] = []
        heapq.heappush(open_heap, (0, 0, start))
        came: Dict[Cell, Optional[Cell]] = {start: None}
        g: Dict[Cell, int] = {start: 0}
        while open_heap:
            f, _, cur = heapq.heappop(open_heap)
            if cur == end:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = came[cur]
                return path[::-1]
            if len(g) > max_steps:
                return None
            for dx, dy in DIRS4:
                nxt = (cur[0] + dx, cur[1] + dy)
                if nxt in obstacles:
                    continue
                ng = g[cur] + 1
                if ng < g.get(nxt, float("inf")):
                    g[nxt] = ng
                    came[nxt] = cur
                    h = abs(nxt[0] - end[0]) + abs(nxt[1] - end[1])
                    heapq.heappush(open_heap, (ng + h, ng, nxt))
        return None

    @staticmethod
    def _simplify_and_turns(path: List[Cell]) -> Tuple[List[Cell], int]:
        """压缩共线点，返回 (拐点序列, 折角数=线段数-1)。"""
        if len(path) <= 2:
            return path, 0
        pts = [path[0]]
        for i in range(1, len(path) - 1):
            p, c, n = path[i - 1], path[i], path[i + 1]
            d1 = (c[0] - p[0], c[1] - p[1])
            d2 = (n[0] - c[0], n[1] - c[1])
            if d1 != d2:
                pts.append(c)
        pts.append(path[-1])
        return pts, len(pts) - 1

    @staticmethod
    def _nearest_cell_in(target: Tuple[float, float], cells: Set[Cell]) -> Optional[Cell]:
        if not cells:
            return None
        tx, ty = target
        return min(cells, key=lambda c: abs(c[0] - tx) + abs(c[1] - ty))

    # ==================================================================
    # 落库
    # ==================================================================
    def _build_road_tiles(
        self,
        band: Set[Cell],
        room_spaces: Dict[int, Set[Cell]],
        all_room_cells: Set[Cell],
        other_road_spaces: Set[Cell],
    ) -> Tuple[Set[Cell], Set[Cell]]:
        """
        road space = 带 - 所有房间格（space+wall：道路贴墙但不占墙，与房屋共用外墙）。
        road wall = 带边界 - 房间格（贴墙处不重复画墙）。
        """
        road_space = band - all_room_cells

        wall: Set[Cell] = set()
        for (x, y) in band:
            if (x, y) in other_road_spaces:
                continue  # 交叉区域开放
            if any((x + dx, y + dy) not in band for dx, dy in DIRS8):
                wall.add((x, y))
        # 与房间共用外墙：road 边界与房间墙重合处不重复画墙
        wall = wall - all_room_cells
        return road_space, wall

    def _save_road(
        self,
        map_id: int,
        layer: int,
        name: str,
        band: Set[Cell],
        road_space: Set[Cell],
        road_wall: Set[Cell],
        connects: List[JsonDict],
        waypoints: List[Cell],
    ) -> Optional[int]:
        space_list = sorted([[int(x), int(y)] for (x, y) in road_space])
        if len(space_list) < 2:
            return None
        xs = [x for (x, y) in band]
        ys = [y for (x, y) in band]
        geom = {
            "type": "road",
            "road_kind": "straight",
            "width": self.ROAD_WIDTH,
            # 只存**拐点**（waypoints，2~4 个点），不存光栅化中心线格点——
            # 光栅化点会让矢量渲染变成数千端点的碎折线且卡顿
            "path": [[int(x), int(y)] for (x, y) in waypoints],
            "center": [(min(xs) + max(xs) + 1) / 2.0, (min(ys) + max(ys) + 1) / 2.0],
            "bbox": [min(xs), min(ys), max(xs) + 1, max(ys) + 1],
        }
        other = {
            "generator": "road_generator_v2",
            "network_mode": "door_to_door",
            "connectivity_mode": "full",
            "road_kind": "straight",
            "width": self.ROAD_WIDTH,
            "connects": connects,
        }
        tiles = {"wall": sorted(road_wall), "space": space_list, "inner_wall": []}
        try:
            cur = self.db.execute(
                "INSERT INTO room ("
                "map_id, building_area_id, name, layer_start, layer_end, "
                "room_type, geom_json, tiles_json, area, other_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(map_id),
                    None,
                    name,
                    int(layer),
                    int(layer),
                    "road",
                    self._dumps(geom),
                    self._dumps(tiles),
                    len(space_list),
                    self._dumps(other),
                ),
            )
            return int(cur.lastrowid)
        except Exception:
            import traceback
            traceback.print_exc()
            return None

    def _clear_crossing_walls(self, map_id: int, layer: int, new_band: Set[Cell]) -> None:
        """已有道路墙格落入新带 => 打通（移除墙并入 space）。"""
        for road in self._fetch_rooms(map_id, layer, room_type="road"):
            tiles = self._loads(road.get("tiles_json"), {})
            wall = {(int(x), int(y)) for (x, y) in tiles.get("wall") or []}
            hit = wall & new_band
            if not hit:
                continue
            space = {(int(x), int(y)) for (x, y) in tiles.get("space") or []}
            new_wall = sorted(w for w in tiles.get("wall") or [] if (int(w[0]), int(w[1])) not in hit)
            space |= hit
            tiles["wall"] = new_wall
            tiles["space"] = sorted(space)
            self.db.execute(
                "UPDATE room SET tiles_json = ?, area = ? WHERE id = ?",
                (self._dumps(tiles), len(space), int(road["id"])),
            )

    def _insert_door(self, map_id: int, layer: int, ep: JsonDict, road_id: int) -> Optional[int]:
        cells = ep["door_cells"]
        cell_set = set(tuple(c) for c in cells)
        cx = sum(x for x, _ in cells) / len(cells) + 0.5
        cy = sum(y for _, y in cells) / len(cells) + 0.5
        name = f"RoadDoor_r{ep['room_id']}_road{road_id}"
        # 幂等：该房间已有**相同门洞**的 road_entrance 门则不重复插（门口复用）；
        # 不同门洞（新开的第 2/3 门）允许插入。
        for d in self.db.fetch_all(
            "SELECT tiles_json FROM item WHERE map_id = ? AND room_id = ? "
            "AND item_type='door' AND properties_json LIKE '%road_entrance%'",
            (int(map_id), int(ep["room_id"])),
        ) or []:
            tj = self._loads(d.get("tiles_json"), {})
            wt = {(int(c[0]), int(c[1])) for c in tj.get("wall_tiles") or []
                  if isinstance(c, (list, tuple)) and len(c) == 2}
            if wt == cell_set:
                return None
        if self.db.fetch_one("SELECT id FROM item WHERE map_id = ? AND name = ?", (int(map_id), name)):
            return None
        cur = self.db.execute(
            "INSERT INTO item ("
            "map_id, room_id, building_area_id, name, item_type, layer_start, layer_end, timestep, "
            "position_x, position_y, vector_json, tiles_json, properties_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(map_id),
                int(ep["room_id"]),
                None,
                name,
                "door",
                int(layer),
                int(layer),
                0,
                float(cx),
                float(cy),
                self._dumps({"type": "circle", "center": [cx, cy], "radius": self.ROAD_WIDTH / 2}),
                self._dumps({"wall_tiles": [[x, y] for (x, y) in cells]}),
                self._dumps({"opening": "door", "role": "road_entrance", "road_id": int(road_id),
                             "connects_room": int(ep["room_id"])}),
            ),
        )
        return int(cur.lastrowid)

    # ==================================================================
    # 门清理：删除只在外墙、没连接道路/房间的门
    # ==================================================================
    def _cleanup_orphan_doors(self, map_id: int) -> int:
        removed = 0
        for d in self.db.fetch_all(
            "SELECT id, properties_json FROM item WHERE map_id = ? AND item_type = 'door'",
            (int(map_id),),
        ) or []:
            props = self._loads(d.get("properties_json"), {})
            if not isinstance(props, dict):
                continue
            # 保留：道路门（road_entrance）
            if props.get("role") == "road_entrance":
                continue
            # 保留：连接两个房间的门（建筑内门）
            room_conns = []
            for k in ("connects_room_ids", "connects_rooms", "db_rooms", "local_rooms"):
                v = props.get(k)
                if isinstance(v, list) and v:
                    room_conns = v
                    break
            if len(room_conns) >= 2:
                continue
            # 删除：孤立外墙门
            self.db.execute("DELETE FROM item WHERE id = ?", (int(d["id"]),))
            removed += 1
        return removed

    # ==================================================================
    # 外墙判定 / 兜底外墙机制
    # ==================================================================
    @staticmethod
    def _has_exterior_wall(room: JsonDict, obstacles: Set[Cell]) -> bool:
        """房间是否有外墙：任一墙格 4 邻存在空地（不属于任何房间）。"""
        tiles = RoadGenerator._loads(room.get("tiles_json"), {})
        wall = RoadGenerator._tile_cells(tiles, ("wall",))
        return any(
            (x + dx, y + dy) not in obstacles
            for (x, y) in wall for dx, dy in DIRS4
        )

    def _ensure_outer_walls(self, map_id: int, layer: int) -> int:
        """
        兜底外墙：确保每个房间的 space 与外界之间有外墙（除门洞）。
        **模型感知**：
        - basic 模型（wall ⊂ space，墙在 space 内边缘）：补 space 内边缘缺失的墙；
        - 外扩圈模型（子房间等，wall 在 space 外）：**不补内边缘**，
          否则会补出"双圈墙"（内边缘 + 外扩圈 = 2 格厚）。
        """
        repaired = 0
        door_cells_by_room: Dict[int, Set[Cell]] = {}
        for d in self.db.fetch_all(
            "SELECT room_id, tiles_json FROM item "
            "WHERE map_id = ? AND item_type = 'door'",
            (int(map_id),),
        ) or []:
            rid = d.get("room_id")
            if rid is None:
                continue
            tj = self._loads(d.get("tiles_json"), {})
            for c in tj.get("wall_tiles") or []:
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    door_cells_by_room.setdefault(int(rid), set()).add((int(c[0]), int(c[1])))

        rooms_all = self._fetch_rooms(map_id, layer)
        all_space: Set[Cell] = set()
        tiles_map: Dict[int, Dict[str, Any]] = {}
        for r in rooms_all:
            if (r.get("room_type") or "") == "road":
                continue
            t = self._loads(r.get("tiles_json"), {})
            tiles_map[int(r["id"])] = t
            all_space |= {(int(x), int(y)) for (x, y) in t.get("space") or []}

        for rid, tiles in tiles_map.items():
            space = {(int(x), int(y)) for (x, y) in tiles.get("space") or []}
            wall = {(int(x), int(y)) for (x, y) in tiles.get("wall") or []}
            if not space:
                continue
            door_cells = door_cells_by_room.get(rid, set())
            missing: Set[Cell] = set()
            for (x, y) in space:
                outer = [(x + dx, y + dy) for dx, dy in DIRS4 if (x + dx, y + dy) not in all_space]
                if not outer:
                    continue
                if (x, y) in wall:
                    continue                       # basic 内边缘墙
                if any(n in wall for n in outer):
                    continue                       # 外扩圈墙
                if (x, y) in door_cells or any(n in door_cells for n in outer):
                    continue                       # 门洞（入口）
                missing.add((x, y))                # 补内边缘墙
            if not missing:
                continue
            wall |= missing
            tiles["wall"] = sorted(wall)
            self.db.execute(
                "UPDATE room SET tiles_json = ? WHERE id = ?",
                (self._dumps(tiles), rid),
            )
            repaired += len(missing)
        return repaired

    # ==================================================================
    # 主入口
    # ==================================================================
    def generate_and_save_roads(
        self,
        map_id: int,
        layer: int = 1,
        width: int = 5,
        seed: Optional[int] = None,
        dense_room_ids: Optional[List[int]] = None,
        dense_groups: Optional[List[List[int]]] = None,
        dense_degree: int = 2,
    ) -> JsonDict:
        """
        节点图式路网生成（分层）：
        - 阶段 0（稠密）：dense_room_ids 内的房间互相多连（每房间连 dense_degree 个最近），
          用于大建筑区内部稠密路网（每个建筑不止与一个相连）。
        - 阶段 1（稀疏）：全区最近的不同分量节点（MST 式 + 道路互联），
          所有建筑区互相连接，形成"配对 + 道路互联 + 分支"的网状而非链式。
        - 生成后清理孤立外墙门。
        """
        self.ROAD_WIDTH = max(1, int(width))
        rng = random.Random(seed if seed is not None else random.randrange(0, 2 ** 31 - 1))

        rooms = self._fetch_rooms(map_id, layer)
        non_road = [r for r in rooms if (r.get("room_type") or "") != "road"]

        # 预计算所有房间格（用于"外墙"判定）
        _pre_cells: Dict[int, Set[Cell]] = {}
        _pre_obstacles: Set[Cell] = set()
        for r in non_road:
            c = self._tile_cells(self._loads(r.get("tiles_json"), {}), ("wall", "space", "inner_wall"))
            _pre_cells[int(r["id"])] = c
            _pre_obstacles |= c

        # 只有**有外墙**的房间才直接连路网（无外墙 = 内部/被包围房间，
        # 如最大建筑分割的内部子房间——它们通过内部门互通，由边缘子房间接入路网）
        connectable = [
            r for r in non_road
            if self._has_exterior_wall(r, _pre_obstacles)
        ]
        skipped_rooms = [int(r["id"]) for r in non_road if r not in connectable]
        if len(connectable) < 2:
            return {"roads": 0, "doors": 0, "connected": True, "skipped_rooms": skipped_rooms,
                    "components": [], "warnings": ["可连接房间数不足 2"]}
        non_road = connectable

        room_ids: List[int] = []
        room_cells: Dict[int, Set[Cell]] = {}
        room_spaces: Dict[int, Set[Cell]] = {}
        room_interiors: Dict[int, Set[Cell]] = {}   # space − wall（纯内部格，路径障碍用）
        all_room_cells: Set[Cell] = set()
        obstacles: Set[Cell] = set()
        for r in non_road:
            rid = int(r["id"])
            room_ids.append(rid)
            tiles = self._loads(r.get("tiles_json"), {})
            cells = self._tile_cells(tiles, ("wall", "space", "inner_wall"))
            spaces = self._tile_cells(tiles, ("space",))
            walls = self._tile_cells(tiles, ("wall",))
            room_cells[rid] = cells
            room_spaces[rid] = spaces
            # basic 模型 wall ⊂ space：路径障碍用"非墙的内部格"，
            # 墙格允许被道路带覆盖（道路可与房屋共用外墙、贴墙走）
            room_interiors[rid] = spaces - walls
            all_room_cells |= cells
            obstacles |= cells

        uf = UnionFind()
        for rid in room_ids:
            uf.make_set(rid)

        roaded_rooms: Set[int] = set()
        road_count = 0
        door_count = 0
        road_name_idx = 0
        roads_mem: List[Dict[str, Any]] = []   # 内存态道路

        def all_connected() -> bool:
            return len({uf.find(rid) for rid in room_ids}) <= 1

        def pick_target(
            origin: Tuple[float, float],
            start_root: int,
        ) -> Optional[Tuple[str, int, Tuple[float, float]]]:
            """最近的不同分量节点（房间或道路）。"""
            best = None
            best_d = float("inf")
            for rid in room_ids:
                if uf.find(rid) == start_root:
                    continue
                c = self._room_center(next(r for r in non_road if int(r["id"]) == rid))
                d = abs(c[0] - origin[0]) + abs(c[1] - origin[1])
                if d < best_d:
                    best_d = d
                    best = ("room", rid, c)
            for rm in roads_mem:
                if uf.find(rm["id"]) == start_root:
                    continue
                pt = self._nearest_cell_in(origin, rm["space"])
                if pt is None:
                    continue
                d = abs(pt[0] - origin[0]) + abs(pt[1] - origin[1])
                if d < best_d:
                    best_d = d
                    best = ("road", rm["id"], (pt[0] + 0.5, pt[1] + 0.5))
            return best

        def endpoint_for(
            kind: str,
            nid: int,
            toward: Tuple[float, float],
        ) -> Optional[Dict[str, Any]]:
            if kind == "room":
                room = next(r for r in non_road if int(r["id"]) == nid)
                ep = self._ensure_exterior_door(map_id, layer, room, obstacles, rng, toward=toward)
                if ep is None:
                    return None
                pt = self._door_exterior_point(ep, room_spaces[nid])
                return {"kind": "room", "id": nid, "cell": pt, "ep": ep,
                        "openings": set(ep["door_cells"])}
            else:
                rm = next(r for r in roads_mem if r["id"] == nid)
                pt = self._nearest_cell_in(toward, rm["space"])
                if pt is None:
                    return None
                return {"kind": "road", "id": nid, "cell": pt, "ep": None, "openings": set()}

        def _door_zone(ep: Dict[str, Any]) -> Set[Cell]:
            """门口共享区：门洞向外 ROAD_WIDTH 范围内的格。
            两条路从同一门出发会在门口外重叠一段（正常），该区域内重叠不算交叉。"""
            zone: Set[Cell] = set()
            if ep.get("ep") is not None:
                dc = ep["ep"]["door_center"]
                dcx, dcy = int(dc[0]), int(dc[1])
                r = self.ROAD_WIDTH
                for dx in range(-r, r + 1):
                    for dy in range(-r, r + 1):
                        zone.add((dcx + dx, dcy + dy))
            return zone

        def _truncate_to_first_crossing(
            waypoints: List[Cell],
            crossing_space: Set[Cell],
        ) -> Tuple[List[Cell], Optional[Cell]]:
            """沿路径找第一个进入 crossing_space 的格，截断 waypoints。"""
            for i in range(len(waypoints) - 1):
                x1, y1 = waypoints[i]
                x2, y2 = waypoints[i + 1]
                dx, dy = abs(x2 - x1), abs(y2 - y1)
                sx_step = 1 if x1 < x2 else -1
                sy_step = 1 if y1 < y2 else -1
                err = dx - dy
                cx, cy = x1, y1
                while True:
                    if (cx, cy) in crossing_space:
                        return waypoints[:i + 1] + [(cx, cy)], (cx, cy)
                    if cx == x2 and cy == y2:
                        break
                    e2 = 2 * err
                    if e2 > -dy:
                        err -= dy
                        cx += sx_step
                    if e2 < dx:
                        err += dx
                        cy += sy_step
            return waypoints, None

        # 端点对去重：同一 (起点, 终点) 连接只生成一次；重复计数达阈值则停止该起点
        generated_pairs: Dict[Tuple[Any, Any], int] = {}
        DUP_STOP_THRESHOLD = 4

        def connect_with_eps(
            s_ep: Dict[str, Any],
            t_ep: Dict[str, Any],
            check_redundant: bool = False,
        ) -> Optional[Tuple[Any, Any]]:
            """
            用已计算的端点连一条路（核心落库逻辑，供稠密/稀疏共用）。

            交叉处理（用户规则）：
            - **门口共享区**（门洞向外 ROAD_WIDTH 格）内与已有道路的重叠不算交叉
              （两条路从同一门出发在门口外重叠一段是正常的）；
            - 路径**穿过已有道路**时截断到**第一个真交叉**：
              新道路只到第一条被穿过的道路为止（connects = (起点, 该道路)），
              不再穿过更多道路形成复杂交叉（b1→r1→r2→b2 只留 b1→r1）；
            - check_redundant（稠密）：若截断接入的道路已与起点连通（r1 已连 b1）
              => 放弃（b1 不能自己连自己）。

            返回：
            - ("ok", 端点对) 生成成功
            - ("dup", 端点对) 该端点对已生成过（不落库，供调用方计数）
            - None 失败
            """
            nonlocal road_count, door_count, road_name_idx, progressed
            openings = s_ep["openings"] | t_ep["openings"]
            res_path = self._find_valid_path(s_ep["cell"], t_ep["cell"], openings,
                                             room_interiors, {}, rng)
            if res_path is None:
                return None
            path, band = res_path

            # 已有道路（用内存态 roads_mem，避免每次连接查 DB 全部道路）
            road_cells_map: Dict[int, Set[Cell]] = {}
            other_spaces: Set[Cell] = set()
            for rm in roads_mem:
                road_cells_map[int(rm["id"])] = rm["space"]
                other_spaces |= rm["space"]

            door_zone = _door_zone(s_ep) | _door_zone(t_ep)
            # 真交叉：带 ∩ 道路 space − 门口共享区
            crossings: List[Tuple[int, Set[Cell]]] = []
            for rid, sp in road_cells_map.items():
                inter = (band & sp) - door_zone
                if inter:
                    crossings.append((rid, inter))

            connect_to_road: Optional[int] = None
            if crossings:
                # 按路径顺序（离起点最近的真交叉优先）
                crossings.sort(key=lambda item: min(
                    abs(px - s_ep["cell"][0]) + abs(py - s_ep["cell"][1]) for (px, py) in item[1]
                ))
                if check_redundant:
                    # 稠密：接入的道路若已与起点连通则放弃
                    for rid, _ in crossings:
                        if not uf.connected(s_ep["id"], rid):
                            connect_to_road = rid
                            break
                    if connect_to_road is None:
                        return None
                else:
                    connect_to_road = crossings[0][0]

            if connect_to_road is not None:
                # 截断到第一个真交叉道路：新道路只接入该道路
                cross_sp = road_cells_map[connect_to_road]
                t_path, _ = _truncate_to_first_crossing(path, cross_sp)
                if t_path != path:
                    path = t_path
                    band = self._band_for_path(path)
                self._clear_crossing_walls(map_id, layer, band)
                road_space, road_wall = self._build_road_tiles(
                    band, room_spaces, all_room_cells, other_spaces,
                )
                connects: List[JsonDict] = [
                    {"kind": s_ep["kind"], "id": s_ep["id"]},
                    {"kind": "road", "id": int(connect_to_road)},
                ]
                t_ep = None  # 终点（目标）不再开门/连接
            else:
                if band & other_spaces:
                    self._clear_crossing_walls(map_id, layer, band)
                road_space, road_wall = self._build_road_tiles(
                    band, room_spaces, all_room_cells, other_spaces,
                )
                connects: List[JsonDict] = [
                    {"kind": s_ep["kind"], "id": s_ep["id"]},
                    {"kind": t_ep["kind"], "id": t_ep["id"]},
                ]

            # 端点对去重：同一连接只生成一次（用户：b1→r1 被重复截断时计数，达阈值停止该起点）
            a_key = (s_ep["kind"], s_ep["id"])
            b_key = (connects[1]["kind"], connects[1]["id"])
            pair_key = (a_key, b_key) if a_key <= b_key else (b_key, a_key)
            if pair_key in generated_pairs:
                generated_pairs[pair_key] += 1
                return ("dup", pair_key)

            road_name_idx += 1
            road_id = self._save_road(
                map_id, layer, f"Road_{road_name_idx}", band, road_space, road_wall,
                connects, path,
            )
            if road_id is None:
                return None
            generated_pairs[pair_key] = 1

            # 门：仅房间端点开外墙门（稠密阶段复用门口时 ep 可能已开过 -> 幂等）
            for epp in (s_ep, t_ep):
                if epp is not None and epp.get("ep") is not None:
                    door_count += (1 if self._insert_door(map_id, layer, epp["ep"], road_id) is not None else 0)
                    roaded_rooms.add(epp["id"])

            # 连通分量
            uf.make_set(road_id)
            uf.union(s_ep["id"], road_id)
            if t_ep is not None:
                uf.union(t_ep["id"], road_id)
            else:
                uf.union(connect_to_road, road_id)

            roads_mem.append({"id": road_id, "space": road_space, "connects": connects})
            road_count += 1
            progressed = True
            return ("ok", pair_key)

        def connect_once(
            s_kind: str,
            s_id: int,
            t_kind: str,
            t_id: int,
            t_pos: Tuple[float, float],
        ) -> bool:
            """从起点节点连到目标节点（稀疏阶段用，每次重新取端点）。"""
            s_ep = endpoint_for(s_kind, s_id, t_pos)
            if s_ep is None:
                return False
            t_ep = endpoint_for(t_kind, t_id, (s_ep["cell"][0] + 0.5, s_ep["cell"][1] + 0.5))
            if t_ep is None:
                return False
            res = connect_with_eps(s_ep, t_ep)
            return res is not None and res[0] == "ok"

        # ---- 阶段 0：稠密路网（用户规则：b1 尝试连其他房间形成 n 条路） ----
        # 规则：
        #   - **按大建筑区分组**：稠密只连接**同组**（同一大建筑区）的房间/区内道路；
        #     跨区连接只留给稀疏阶段（否则区内不稠密、区际反而稠密——用户观察到的颠倒）
        #   - 每个房间尝试连接所有**同组不同分量**的目标（房间或道路），最近优先
        #   - **避免冗余**：已连通的房间/道路不再重复连接
        #   - **门口复用**：一个房间的多条道路尽量从同一门洞分叉
        #   - **端点对去重**：同一连接只生成一次；被重复截断成同一条时计数，
        #     达阈值（4）停止该房间尝试
        room_id_set = set(room_ids)
        DENSE_FANOUT = 3   # 每房间每轮稠密尝试的最多目标数（大组稠密可控）
        if dense_groups is not None:
            dense_groups = [
                [int(x) for x in g if int(x) in room_id_set] for g in dense_groups
            ]
        else:
            dense_groups = [
                [int(x) for x in (dense_room_ids or []) if int(x) in room_id_set]
            ]
        dense_groups = [g for g in dense_groups if g]
        dense_ids: Set[int] = set()
        for g in dense_groups:
            dense_ids |= set(g)
        dense_eps: Dict[int, JsonDict] = {}   # 门口复用缓存
        dup_count: Dict[Any, int] = {}        # (起点,终点) -> 重复截断计数
        stopped_starts: Set[int] = set()      # 停止尝试的房间

        def dense_connect(rid: int, t_kind: str, t_id: int, t_pos: Tuple[float, float]):
            """稠密连接（复用门口）：起点房间用缓存门洞，目标房间也缓存。
            返回 connect_with_eps 的 (status, key) 或 None。"""
            nonlocal road_count, door_count, road_name_idx, progressed
            s_ep = dense_eps.get(rid)
            if s_ep is None:
                r = next(x for x in non_road if int(x["id"]) == rid)
                s_ep = endpoint_for("room", rid, t_pos)
                if s_ep is None:
                    return None
                dense_eps[rid] = s_ep
            if t_kind == "room":
                t_ep = dense_eps.get(t_id)
                if t_ep is None:
                    t_ep = endpoint_for("room", t_id, (s_ep["cell"][0] + 0.5, s_ep["cell"][1] + 0.5))
                    if t_ep is None:
                        return None
                    dense_eps[t_id] = t_ep
            else:
                t_ep = endpoint_for("road", t_id, (s_ep["cell"][0] + 0.5, s_ep["cell"][1] + 0.5))
                if t_ep is None:
                    return None
            # 稠密：交叉截断时接入的道路若已与起点连通则放弃（避免 b1 自己连自己）
            return connect_with_eps(s_ep, t_ep, check_redundant=True)

        for _round in range(len(dense_ids) + 2):
            if all_connected():
                break
            progressed = False
            for rid in sorted(dense_ids - stopped_starts):
                if all_connected():
                    break
                group = next((g for g in dense_groups if rid in g), [])
                r = next(x for x in non_road if int(x["id"]) == rid)
                origin = self._room_center(r)
                start_root = uf.find(rid)
                # 目标：**同组**不同分量的房间 + 同组区内道路（避免冗余/跨区）
                cand_rooms = sorted(
                    (d for d in group if d != rid and uf.find(d) != start_root),
                    key=lambda d: abs(self._room_center(
                        next(x for x in non_road if int(x["id"]) == d))[0] - origin[0])
                    + abs(self._room_center(next(x for x in non_road if int(x["id"]) == d))[1] - origin[1]),
                )[:DENSE_FANOUT]   # 每轮只试最近 FANOUT 个（大组稠密可控）
                group_set = set(group)
                cand_roads = [
                    rm for rm in roads_mem
                    if uf.find(rm["id"]) != start_root
                    and any(c.get("kind") == "room" and int(c.get("id", -1)) in group_set
                            for c in rm.get("connects", []))
                ]
                cand_roads.sort(key=lambda rm: min(
                    abs(pt[0] - origin[0]) + abs(pt[1] - origin[1]) for pt in rm["space"]
                ) if rm["space"] else 1e18)
                cand_roads = cand_roads[:DENSE_FANOUT]
                # 每房间每轮尝试**所有同组目标**（稠密多连；重复截断靠去重停止限制）
                for t_rid in cand_rooms:
                    tc = self._room_center(next(x for x in non_road if int(x["id"]) == t_rid))
                    res = dense_connect(rid, "room", t_rid, tc)
                    if res is None:
                        continue
                    status, key = res
                    if status == "ok":
                        progressed = True
                    else:
                        dup_count[key] = dup_count.get(key, 0) + 1
                        if dup_count[key] >= DUP_STOP_THRESHOLD:
                            stopped_starts.add(rid)
                            break
                if rid in stopped_starts:
                    continue
                for rm in cand_roads:
                    res = dense_connect(rid, "road", rm["id"], (origin[0], origin[1]))
                    if res is None:
                        continue
                    status, key = res
                    if status == "ok":
                        progressed = True
                    else:
                        dup_count[key] = dup_count.get(key, 0) + 1
                        if dup_count[key] >= DUP_STOP_THRESHOLD:
                            stopped_starts.add(rid)
                            break
            if not progressed:
                break

        # ---- 阶段 1：稀疏路网（全区：最近的不同分量节点，MST 式 + 道路互联） ----
        for _round in range(len(room_ids) * 3 + 2):
            if all_connected():
                break
            progressed = False

            # 起点优先级：无道路房间 -> 任意房间 -> 道路
            start_cands: List[Tuple[str, int]] = (
                [("room", rid) for rid in room_ids if rid not in roaded_rooms]
                + [("room", rid) for rid in room_ids if rid in roaded_rooms]
                + [("road", rm["id"]) for rm in roads_mem]
            )
            for kind, nid in start_cands:
                if all_connected():
                    break

                if kind == "room":
                    origin = self._room_center(next(r for r in non_road if int(r["id"]) == nid))
                    start_root = uf.find(nid)
                else:
                    rm = next(r for r in roads_mem if r["id"] == nid)
                    origin = (sum(x for x, _ in rm["space"]) / len(rm["space"]) + 0.5,
                              sum(y for _, y in rm["space"]) / len(rm["space"]) + 0.5)
                    start_root = uf.find(nid)

                target = pick_target(origin, start_root)
                if target is None:
                    continue
                t_kind, t_id, t_pos = target

                if connect_once(kind, nid, t_kind, t_id, t_pos):
                    break

            if not progressed:
                break

        # ---- 阶段 2：子群互联（稀疏后仍有多个分量时） ----
        # 用"最近的一对不同分量的道路"连接子群（道路之间空地多、路径直、阻挡少），
        # 连接同样走交叉截断逻辑（穿过其他道路时只接入第一个）。
        for _sg in range(len(room_ids) + 2):
            comps_now = defaultdict(list)
            for rid in room_ids:
                comps_now[uf.find(rid)].append(rid)
            if len(comps_now) <= 1:
                break
            progressed = False
            # 最近的不同分量道路对（道路间距离用 space 中心/bbox 粗算，避免 O(m²·格²)）
            best = None
            best_d = float("inf")
            centers = {}
            for rm in roads_mem:
                if rm["space"]:
                    centers[rm["id"]] = (
                        (min(x for x, _ in rm["space"]) + max(x for x, _ in rm["space"])) / 2.0,
                        (min(y for _, y in rm["space"]) + max(y for _, y in rm["space"])) / 2.0,
                    )
            for i in range(len(roads_mem)):
                ra = roads_mem[i]
                ca = centers.get(ra["id"])
                if ca is None:
                    continue
                for j in range(i + 1, len(roads_mem)):
                    rb = roads_mem[j]
                    if uf.find(ra["id"]) == uf.find(rb["id"]):
                        continue
                    cb = centers.get(rb["id"])
                    if cb is None:
                        continue
                    d = abs(ca[0] - cb[0]) + abs(ca[1] - cb[1])
                    if d < best_d:
                        best_d = d
                        best = ("road", ra["id"], "road", rb["id"],
                                (cb[0], cb[1]))
            if best is None:
                break
            s_kind, s_id, t_kind, t_id, t_pos = best
            if connect_once(s_kind, s_id, t_kind, t_id, t_pos):
                progressed = True
            if not progressed:
                break

        # 门清理：删除孤立外墙门
        removed_doors = self._cleanup_orphan_doors(map_id)
        # 兜底外墙：补全房间 space 边界墙（除门洞）
        repaired_walls = self._ensure_outer_walls(map_id, layer)

        comps = defaultdict(list)
        for rid in room_ids:
            comps[uf.find(rid)].append(rid)
        all_conn = len(comps) <= 1

        return {
            "roads": road_count,
            "doors": door_count,
            "orphan_doors_removed": removed_doors,
            "outer_walls_repaired": repaired_walls,
            "skipped_rooms": skipped_rooms,
            "connected": all_conn,
            "components": [{"root": root, "rooms": members} for root, members in comps.items()],
            "warnings": [] if all_conn else [f"警告：{len(comps)} 个不连通分量"],
        }
