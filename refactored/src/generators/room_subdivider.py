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
