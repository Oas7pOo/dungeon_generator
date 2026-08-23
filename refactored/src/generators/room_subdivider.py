# src/generators/room_subdivider.py
"""
房间分割器（RoomSubdivider）：把一个大矩形房间递归二分（BSP）成若干子房间。

契约（readme 路线图 §1.1 + 用户需求）：
- 子房间之间用**内墙（单墙共享）**隔开：相邻子房间共用 1 列/行墙格
- 共享墙上**开洞生成门**（door item，连接两个子房间）
- 子房间继承父房间的 map_id / building_area_id / layer
- 父房间被分割替换（删除，子房间继承其归属）
"""
from __future__ import annotations

import json
import math
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from ..db.database import DatabaseManager

JsonDict = Dict[str, Any]
Cell = Tuple[int, int]
Rect = Tuple[int, int, int, int]  # (x0, y0, x1, y1) 半开区间 [x0,x1) x [y0,y1)

DIRS8 = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if not (dx == 0 and dy == 0)]


class RoomSubdivider:
    MIN_SIDE = 6

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

    # ------------------------------------------------------------------
    # json helpers
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # BSP：矩形区域递归二分
    # ------------------------------------------------------------------
    def _bsp_rect(self, rect: Rect, target: int, rng: random.Random) -> List[Rect]:
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        if target <= 1 or (w < 2 * self.MIN_SIDE and h < 2 * self.MIN_SIDE):
            return [rect]

        if w >= h:
            # 竖切：墙列 x=k，左 [x0,k) 右 [k+1,x1)
            lo, hi = x0 + self.MIN_SIDE, x1 - self.MIN_SIDE - 1
            if lo >= hi:
                return [rect]
            k = rng.randint(lo, hi)
            left = (x0, y0, k, y1)
            right = (k + 1, y0, x1, y1)
        else:
            # 横切：墙行 y=k，上 [y0,k) 下 [k+1,y1)
            lo, hi = y0 + self.MIN_SIDE, y1 - self.MIN_SIDE - 1
            if lo >= hi:
                return [rect]
            k = rng.randint(lo, hi)
            left = (x0, y0, x1, k)
            right = (x0, k + 1, x1, y1)

        l_area = (left[2] - left[0]) * (left[3] - left[1])
        r_area = (right[2] - right[0]) * (right[3] - right[1])
        n_left = max(1, int(round(target * l_area / (l_area + r_area))))
        n_right = max(1, target - n_left)
        return self._bsp_rect(left, n_left, rng) + self._bsp_rect(right, n_right, rng)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def subdivide_room(self, room_id: int, n_rooms: int, seed: Optional[int] = None) -> List[int]:
        """
        把 room_id（矩形房间）分割成 n_rooms 个子房间。
        返回子房间 id 列表；父房间被删除。
        """
        rng = random.Random(seed if seed is not None else random.randrange(0, 2 ** 31 - 1))
        row = self.db.fetch_one(
            "SELECT map_id, building_area_id, name, layer_start, layer_end, tiles_json "
            "FROM room WHERE id = ?",
            (int(room_id),),
        )
        if not row:
            return []
        tiles = self._loads(row["tiles_json"], {})
        space = {(int(x), int(y)) for (x, y) in tiles.get("space") or []}
        if len(space) < 4:
            return []
        xs = [x for (x, y) in space]
        ys = [y for (x, y) in space]
        bbox: Rect = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)

        n = max(2, int(n_rooms))
        regions = self._bsp_rect(bbox, n, rng)

        map_id = int(row["map_id"])
        ba_id = row.get("building_area_id")
        layer_start = int(row["layer_start"])
        layer_end = int(row["layer_end"])
        parent_name = str(row.get("name") or "Room")

        # 所有 region 的 space 集合（用于区分"共享墙"）
        region_spaces: List[Set[Cell]] = [
            {(x, y) for x in range(r[0], r[2]) for y in range(r[1], r[3])}
            for r in regions
        ]
        all_space: Set[Cell] = set()
        for rs in region_spaces:
            all_space |= rs

        child_ids: List[int] = []

        for i, r in enumerate(regions):
            x0, y0, x1, y1 = r
            region_space = region_spaces[i]

            # wall = region 外扩 1 圈（8 邻不在 region space）的格
            wall: Set[Cell] = set()
            for (x, y) in region_space:
                for dx, dy in DIRS8:
                    nx, ny = x + dx, y + dy
                    if (nx, ny) not in region_space:
                        wall.add((nx, ny))
            # 共享墙列只保留 1 格厚：外扩圈中属于"其他 region 空间外侧"的格剔除
            # （共享墙 = 两个 region 都把它当边界；此处 wall 已含共享列，直接使用）
            # 注：wall 格可能与其他 region 的 space 相邻（共享墙）或与外部空地相邻（外墙）

            name = f"{parent_name}_S{i + 1}"
            geom = {
                "type": "subdivided",
                "parent": parent_name,
                "bbox": [x0, y0, x1, y1],
                "center": [(x0 + x1) / 2.0, (y0 + y1) / 2.0],
            }
            tiles_json = {
                "wall": sorted([[x, y] for (x, y) in wall]),
                "space": sorted([[x, y] for (x, y) in region_space]),
                "inner_wall": [],
            }
            other = {"generator": "room_subdivider_v1", "parent_room_id": int(room_id),
                     "sub_index": i + 1}
            cur = self.db.execute(
                "INSERT INTO room ("
                "map_id, building_area_id, name, layer_start, layer_end, room_type, "
                "geom_json, tiles_json, area, other_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    map_id,
                    int(ba_id) if ba_id is not None else None,
                    name,
                    layer_start,
                    layer_end,
                    "subdivided",
                    self._dumps(geom),
                    self._dumps(tiles_json),
                    len(region_space),
                    self._dumps(other),
                ),
            )
            child_ids.append(int(cur.lastrowid))

        # 门：相邻 region（共享 1 列/行墙）在共享墙上开洞
        self._place_doors(map_id, layer_start, regions, region_spaces, child_ids)

        # 删除父房间（连带其 item）
        self.db.execute("DELETE FROM item WHERE room_id = ?", (int(room_id),))
        self.db.execute("DELETE FROM room WHERE id = ?", (int(room_id),))
        return child_ids

    # ------------------------------------------------------------------
    # 旋转矩形：按房间自身旋转轴斜向分割（BSP 在局部坐标系进行）
    # ------------------------------------------------------------------
    def subdivide_rotated_room(self, room_id: int, n_rooms: int, seed: Optional[int] = None) -> List[int]:
        """
        把旋转矩形（geom_json.type == 'rotated_rectangle'）**斜向**分割成 n_rooms 个子房间：
        - 先把世界格中心绕房间中心旋转 -angle 到**局部坐标系**（房间长/短轴），
          在局部坐标上做与轴对齐版相同的 BSP 递归二分；
        - 子房间的 space 由局部归属回填世界格，墙为世界 8 邻轮廓——因此子房间、
          共享墙、内部门都**沿旋转方向**（斜着），与房间朝向一致；
        - 返回子房间 id 列表；父房间被删除。
        """
        rng = random.Random(seed if seed is not None else random.randrange(0, 2 ** 31 - 1))
        row = self.db.fetch_one(
            "SELECT map_id, building_area_id, name, layer_start, layer_end, geom_json, tiles_json "
            "FROM room WHERE id = ?",
            (int(room_id),),
        )
        if not row:
            return []
        geom = self._loads(row["geom_json"], {})
        tiles = self._loads(row["tiles_json"], {})
        space = {(int(x), int(y)) for (x, y) in tiles.get("space") or []}
        if len(space) < 4:
            return []
        center = geom.get("center") or [0, 0]
        cx, cy = float(center[0]), float(center[1])
        angle = float(geom.get("angle") or 0)
        ang = math.radians(angle)
        cos_a, sin_a = math.cos(ang), math.sin(ang)

        def to_local(x: int, y: int) -> Tuple[float, float]:
            px, py = x + 0.5 - cx, y + 0.5 - cy
            return px * cos_a + py * sin_a, -px * sin_a + py * cos_a

        def to_world(lx: float, ly: float) -> Tuple[float, float]:
            return cx + lx * cos_a - ly * sin_a, cy + lx * sin_a + ly * cos_a

        local_cells: Dict[Cell, Tuple[float, float]] = {c: to_local(*c) for c in space}
        lxs = [lx for lx, _ in local_cells.values()]
        lys = [ly for _, ly in local_cells.values()]
        bbox: Rect = (math.floor(min(lxs)), math.floor(min(lys)),
                      math.ceil(max(lxs)), math.ceil(max(lys)))

        n = max(2, int(n_rooms))
        regions = self._bsp_rect(bbox, n, rng)

        # 每个世界格按局部中心归属到子区域
        region_cells: List[Set[Cell]] = [set() for _ in regions]
        for cell, (lx, ly) in local_cells.items():
            for i, (x0, y0, x1, y1) in enumerate(regions):
                if x0 <= lx < x1 and y0 <= ly < y1:
                    region_cells[i].add(cell)
                    break
        kept = [(r, c) for r, c in zip(regions, region_cells) if c]
        if not kept:
            return []
        regions = [r for r, _ in kept]
        region_cells = [c for _, c in kept]

        map_id = int(row["map_id"])
        ba_id = row.get("building_area_id")
        layer_start = int(row["layer_start"])
        layer_end = int(row["layer_end"])
        parent_name = str(row.get("name") or "Room")

        child_ids: List[int] = []
        walls: List[Set[Cell]] = []
        for i, (r, cells) in enumerate(zip(regions, region_cells)):
            x0, y0, x1, y1 = r
            wall: Set[Cell] = set()
            for (x, y) in cells:
                for dx, dy in DIRS8:
                    if (x + dx, y + dy) not in cells:
                        wall.add((x + dx, y + dy))
            walls.append(wall)

            corners = [to_world(lx, ly) for lx, ly in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            geom_new = {
                "type": "rotated_subdivided",
                "parent": parent_name,
                "center": [cx, cy],
                "angle": angle,
                "corners": [[float(a), float(b)] for a, b in corners],
                "bbox_local": [x0, y0, x1, y1],
            }
            tiles_json = {
                "wall": sorted([[x, y] for (x, y) in wall]),
                "space": sorted([[x, y] for (x, y) in cells]),
                "inner_wall": [],
            }
            name = f"{parent_name}_S{i + 1}"
            other = {"generator": "room_subdivider_v1_rotated", "parent_room_id": int(room_id),
                     "sub_index": i + 1}
            cur = self.db.execute(
                "INSERT INTO room ("
                "map_id, building_area_id, name, layer_start, layer_end, room_type, "
                "geom_json, tiles_json, area, other_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    map_id,
                    int(ba_id) if ba_id is not None else None,
                    name,
                    layer_start,
                    layer_end,
                    "subdivided",
                    self._dumps(geom_new),
                    self._dumps(tiles_json),
                    len(cells),
                    self._dumps(other),
                ),
            )
            child_ids.append(int(cur.lastrowid))

        self._place_rotated_doors(map_id, layer_start, regions, region_cells, walls, child_ids,
                                  (cx, cy, cos_a, sin_a))

        self.db.execute("DELETE FROM item WHERE room_id = ?", (int(room_id),))
        self.db.execute("DELETE FROM room WHERE id = ?", (int(room_id),))
        return child_ids

    # ------------------------------------------------------------------
    # 旋转分割：相邻子区域（局部共享边）在共享边界中间开内部门
    # ------------------------------------------------------------------
    def _place_rotated_doors(
        self,
        map_id: int,
        layer: int,
        regions: List[Rect],
        region_cells: List[Set[Cell]],
        walls: List[Set[Cell]],
        child_ids: List[int],
        frame: Tuple[float, float, float, float],
    ) -> int:
        cx, cy, cos_a, sin_a = frame
        doors = 0

        def to_local(x: int, y: int) -> Tuple[float, float]:
            px, py = x + 0.5 - cx, y + 0.5 - cy
            return px * cos_a + py * sin_a, -px * sin_a + py * cos_a

        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                a, b = regions[i], regions[j]
                shared: Optional[Tuple[str, int, int, int]] = None
                if a[2] + 1 == b[0] and min(a[3], b[3]) > max(a[1], b[1]):
                    shared = ("V", a[2], max(a[1], b[1]), min(a[3], b[3]))
                elif b[2] + 1 == a[0] and min(a[3], b[3]) > max(a[1], b[1]):
                    shared = ("V", b[2], max(a[1], b[1]), min(a[3], b[3]))
                if shared is None and a[3] + 1 == b[1] and min(a[2], b[2]) > max(a[0], b[0]):
                    shared = ("H", a[3], max(a[0], b[0]), min(a[2], b[2]))
                elif shared is None and b[3] + 1 == a[1] and min(a[2], b[2]) > max(a[0], b[0]):
                    shared = ("H", b[3], max(a[0], b[0]), min(a[2], b[2]))
                if shared is None:
                    continue
                axis, fixed, lo, hi = shared
                if hi - lo < 2:
                    continue
                hole_w = 2 if hi - lo < 6 else 3

                # 两侧"面向对方"的墙格：墙格 8 邻（含对角）落在对方 space
                # （斜向边界在世界上是对角相邻，不能用 4 邻）
                def facing_side(k: int, other_cells: Set[Cell]) -> List[Cell]:
                    out = []
                    for (x, y) in walls[k]:
                        if any((x + dx, y + dy) in other_cells for dx, dy in DIRS8):
                            out.append((x, y))
                    return out

                face_i = facing_side(i, region_cells[j])
                face_j = facing_side(j, region_cells[i])
                # 按切线局部坐标排序，取中间 hole_w 格
                def tangent_key(cell: Cell) -> float:
                    lx, ly = to_local(*cell)
                    return ly if axis == "V" else lx

                face_i.sort(key=tangent_key)
                face_j.sort(key=tangent_key)
                c = (len(face_i) - 1) // 2
                hole_i = set(face_i[max(0, c - hole_w // 2): c - hole_w // 2 + hole_w])
                c = (len(face_j) - 1) // 2
                hole_j = set(face_j[max(0, c - hole_w // 2): c - hole_w // 2 + hole_w])
                hole_set = hole_i | hole_j
                if not hole_set:
                    continue

                for k in (i, j):
                    self.db.execute(
                        "UPDATE room SET tiles_json = ? WHERE id = ?",
                        (self._remove_wall_cells(child_ids[k], hole_set), child_ids[k]),
                    )

                hole = sorted(hole_set)
                cx_d = sum(x for x, _ in hole) / len(hole) + 0.5
                cy_d = sum(y for _, y in hole) / len(hole) + 0.5
                name = f"SubDoor_r{child_ids[i]}_r{child_ids[j]}"
                if self.db.fetch_one("SELECT id FROM item WHERE map_id = ? AND name = ?", (map_id, name)):
                    continue
                self.db.execute(
                    "INSERT INTO item ("
                    "map_id, room_id, building_area_id, name, item_type, layer_start, layer_end, timestep, "
                    "position_x, position_y, vector_json, tiles_json, properties_json"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        map_id,
                        int(child_ids[i]),
                        None,
                        name,
                        "door",
                        int(layer),
                        int(layer),
                        0,
                        float(cx_d),
                        float(cy_d),
                        self._dumps({"type": "circle", "center": [cx_d, cy_d], "radius": 1.5}),
                        self._dumps({"wall_tiles": [[x, y] for (x, y) in hole]}),
                        self._dumps({"opening": "door", "role": "interior",
                                     "connects_room_ids": [int(child_ids[i]), int(child_ids[j])]}),
                    ),
                )
                doors += 1
        return doors

    # ------------------------------------------------------------------
    # 相邻 region 之间开内部门
    # ------------------------------------------------------------------
    def _place_doors(
        self,
        map_id: int,
        layer: int,
        regions: List[Rect],
        region_spaces: List[Set[Cell]],
        child_ids: List[int],
    ) -> int:
        doors = 0
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                a, b = regions[i], regions[j]
                shared: Optional[Tuple[str, int, int, int]] = None  # (axis, fixed, lo, hi)
                # 共享墙列/行：相邻 region（BSP 切分时墙列不属于任何一侧）
                # 竖共享：a.x1 + 1 == b.x0（墙列 x = a.x1），且 y 范围重叠
                if a[2] + 1 == b[0] and min(a[3], b[3]) > max(a[1], b[1]):
                    shared = ("V", a[2], max(a[1], b[1]), min(a[3], b[3]))
                elif b[2] + 1 == a[0] and min(a[3], b[3]) > max(a[1], b[1]):
                    shared = ("V", b[2], max(a[1], b[1]), min(a[3], b[3]))
                # 横共享：a.y1 + 1 == b.y0（墙行 y = a.y1），且 x 范围重叠
                if shared is None and a[3] + 1 == b[1] and min(a[2], b[2]) > max(a[0], b[0]):
                    shared = ("H", a[3], max(a[0], b[0]), min(a[2], b[2]))
                elif shared is None and b[3] + 1 == a[1] and min(a[2], b[2]) > max(a[0], b[0]):
                    shared = ("H", b[3], max(a[0], b[0]), min(a[2], b[2]))
                if shared is None:
                    continue
                axis, fixed, lo, hi = shared
                seg_len = hi - lo
                if seg_len < 2:
                    continue
                # 洞宽 2-3 格，在共享段中部
                hole_w = 2 if seg_len < 6 else 3
                c = (lo + hi) // 2
                h0, h1 = c - hole_w // 2, c - hole_w // 2 + hole_w
                hole: List[Cell] = []
                for t in range(h0, h1):
                    if axis == "V":
                        hole.append((fixed, t))
                    else:
                        hole.append((t, fixed))
                hole_set = set(hole)

                # 从两个子房间的 wall 移除洞格（墙洞）
                for k in (i, j):
                    self.db.execute(
                        "UPDATE room SET tiles_json = ? WHERE id = ?",
                        (self._remove_wall_cells(child_ids[k], hole_set), child_ids[k]),
                    )

                # door item
                cx = sum(x for x, _ in hole) / len(hole) + 0.5
                cy = sum(y for _, y in hole) / len(hole) + 0.5
                name = f"SubDoor_r{child_ids[i]}_r{child_ids[j]}"
                if self.db.fetch_one("SELECT id FROM item WHERE map_id = ? AND name = ?", (map_id, name)):
                    continue
                self.db.execute(
                    "INSERT INTO item ("
                    "map_id, room_id, building_area_id, name, item_type, layer_start, layer_end, timestep, "
                    "position_x, position_y, vector_json, tiles_json, properties_json"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        map_id,
                        int(child_ids[i]),
                        None,
                        name,
                        "door",
                        int(layer),
                        int(layer),
                        0,
                        float(cx),
                        float(cy),
                        self._dumps({"type": "circle", "center": [cx, cy], "radius": 1.5}),
                        self._dumps({"wall_tiles": [[x, y] for (x, y) in hole]}),
                        self._dumps({"opening": "door", "role": "interior",
                                     "connects_room_ids": [int(child_ids[i]), int(child_ids[j])]}),
                    ),
                )
                doors += 1
        return doors

    def _remove_wall_cells(self, room_id: int, cells: Set[Cell]) -> str:
        row = self.db.fetch_one("SELECT tiles_json FROM room WHERE id = ?", (int(room_id),))
        tiles = self._loads(row["tiles_json"], {}) if row else {}
        new_wall = [
            w for w in tiles.get("wall") or []
            if (int(w[0]), int(w[1])) not in cells
        ]
        tiles["wall"] = new_wall
        return self._dumps(tiles)
