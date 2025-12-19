from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from ..db.database import DatabaseManager

# 你自己的 core
from .dwellings_core.tags import parse_tags
from .dwellings_core.house import generate_house_export  # 你接下来要实现/先写个简化版也行

JsonDict = Dict[str, Any]
Grid = List[int]          # [x, y]
GridList = List[Grid]


class DwellingsHouseDBWriter:
    """
    把 dwellings_core 输出的 house_export 写入现有 DB（room + item）
    - room.tiles_json: {wall, space, inner_wall}
    - item: door/window/stairs
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None, cell_scale: int = 10):
        self.db = db_manager or DatabaseManager()
        self.cell_scale = cell_scale

    # -------------------------
    # json helpers
    # -------------------------
    @staticmethod
    def _dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False)

    @staticmethod
    def _loads(s: Optional[str], default: Any) -> Any:
        if not s:
            return default
        try:
            return json.loads(s)
        except Exception:
            return default

    # -------------------------    
    # fine -> coarse grid conversion
    # -------------------------    
    @staticmethod
    def fine_cells_to_coarse_area(fine_cells: set[tuple[int, int]], scale: int, threshold: float = 0.6) -> list[tuple[int, int]]:
        """
        将细网格单元格转换为粗网格区域
        
        Args:
            fine_cells: 细网格单元格集合
            scale: 缩放因子，1粗格 = scale细格
            threshold: 占比阈值，粗格内细格占比≥threshold才被计入
            
        Returns:
            粗网格单元格列表
        """
        from collections import defaultdict
        
        bucket = defaultdict(int)
        for x, y in fine_cells:
            bucket[(x // scale, y // scale)] += 1
        
        need = int(scale * scale * threshold)
        return [(cx, cy) for (cx, cy), cnt in bucket.items() if cnt >= need]
    
    @staticmethod
    def expand_room_cells_to_tiles(room_cells_coarse: list[tuple[int, int]], scale: int) -> dict:
        """
        将粗网格房间单元格展开为细网格tiles_json
        
        Args:
            room_cells_coarse: 粗网格房间单元格列表
            scale: 缩放因子，1粗格 = scale细格
            
        Returns:
            tiles_json: {wall: list, space: list, inner_wall: list}
        """
        space = set()
        for cx, cy in room_cells_coarse:
            x0, y0 = cx * scale, cy * scale
            for x in range(x0, x0 + scale):
                for y in range(y0, y0 + scale):
                    space.add((x, y))
        
        wall = []
        for x, y in space:
            has_empty_neighbor = False
            for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                if (x + dx, y + dy) not in space:
                    has_empty_neighbor = True
                    break
            if has_empty_neighbor:
                wall.append([x, y])
        
        wall_set = set((x, y) for x, y in wall)
        space_list = [[x, y] for (x, y) in space if (x, y) not in wall_set]
        
        return {
            "wall": wall,
            "space": space_list,
            "inner_wall": []
        }
    
    @staticmethod
    def coarse_cells_to_fine_set(cells_coarse: list[tuple[int, int]], scale: int) -> set[tuple[int, int]]:
        """
        把粗网格 cells 展开成细网格 tile 集合（全集，不区分 wall/space）
        """
        s: set[tuple[int, int]] = set()
        for cx, cy in cells_coarse:
            x0, y0 = cx * scale, cy * scale
            for x in range(x0, x0 + scale):
                for y in range(y0, y0 + scale):
                    s.add((x, y))
        return s
    
    # -------------------------    
    # building_area -> area_cells (tile grid)
    # -------------------------
    def _get_building_area(self, building_area_id: int) -> Optional[JsonDict]:
        return self.db.fetch_one(
            "SELECT id, map_id, name, layer_start, layer_end, geom_type, "
            "center_x, center_y, radius, geom_json, size_json "
            "FROM building_area WHERE id = ?",
            (int(building_area_id),),
        )

    @staticmethod
    def _safe_polygon(vertices: Any) -> Optional[Polygon]:
        if not isinstance(vertices, list) or len(vertices) < 3:
            return None
        try:
            poly = Polygon(vertices)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                return None
            return poly
        except Exception:
            return None

    def _area_cells_from_building_area(self, ba: JsonDict) -> Tuple[List[Tuple[int, int]], Tuple[int, int]]:
        """
        返回: (cells_local, origin_xy)
        - cells_local：以 origin 为 (0,0) 的局部格子坐标
        - origin_xy：局部坐标转世界坐标的偏移 (ox, oy)
        """
        geom_type = str(ba.get("geom_type") or "")
        if geom_type == "circle":
            cx = float(ba["center_x"])
            cy = float(ba["center_y"])
            r = float(ba["radius"])

            min_x = int(math.floor(cx - r))
            max_x = int(math.floor(cx + r))
            min_y = int(math.floor(cy - r))
            max_y = int(math.floor(cy + r))

            cells_world = []
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r ** 2:
                        cells_world.append((x, y))

        else:
            corners = self._loads(ba.get("geom_json"), None)
            if not corners:
                # fallback: axis-aligned rectangle from size_json
                size = self._loads(ba.get("size_json"), {})
                w = size.get("width")
                h = size.get("height")
                cx = ba.get("center_x")
                cy = ba.get("center_y")
                if w and h and cx is not None and cy is not None:
                    w = float(w); h = float(h)
                    cx = float(cx); cy = float(cy)
                    corners = [
                        [cx - w / 2, cy - h / 2],
                        [cx + w / 2, cy - h / 2],
                        [cx + w / 2, cy + h / 2],
                        [cx - w / 2, cy + h / 2],
                    ]

            poly = self._safe_polygon(corners)
            if poly is None:
                return [], (0, 0)

            minx, miny, maxx, maxy = poly.bounds
            min_x = int(math.floor(minx))
            max_x = int(math.floor(maxx))
            min_y = int(math.floor(miny))
            max_y = int(math.floor(maxy))

            cells_world = []
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    if poly.contains(Point(x + 0.5, y + 0.5)):
                        cells_world.append((x, y))

        if not cells_world:
            return [], (0, 0)

        # ✅ 细转粗：将细网格转换为粗网格
        cells_world_coarse = self.fine_cells_to_coarse_area(
            set(cells_world), 
            scale=self.cell_scale, 
            threshold=0.6
        )

        if not cells_world_coarse:
            return [], (0, 0)

        # 粗网格的原点和局部坐标
        ox_coarse = min(cx for cx, _ in cells_world_coarse)
        oy_coarse = min(cy for _, cy in cells_world_coarse)

        cells_local = [(cx - ox_coarse, cy - oy_coarse) for (cx, cy) in cells_world_coarse]
        
        # 粗网格原点转换为细网格世界坐标
        origin = (ox_coarse * self.cell_scale, oy_coarse * self.cell_scale)
        
        return cells_local, origin

    # -------------------------
    # cells -> tiles_json (wall/space)
    # -------------------------
    @staticmethod
    def _neighbors8(x: int, y: int) -> List[Tuple[int, int]]:
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                out.append((x + dx, y + dy))
        return out

    def _split_wall_space(self, cells: List[Tuple[int, int]]) -> Tuple[GridList, GridList]:
        """
        wall = 任意8邻居不在 cells 的那些格
        space = cells - wall
        """
        s = set((int(x), int(y)) for x, y in cells)
        wall = []
        for x, y in s:
            for nx, ny in self._neighbors8(x, y):
                if (nx, ny) not in s:
                    wall.append([x, y])
                    break
        wall_set = set((w[0], w[1]) for w in wall)
        space = [[x, y] for (x, y) in s if (x, y) not in wall_set]
        return wall, space

    # -------------------------
    # edge_key -> world center + adjacent cells
    # -------------------------
    def _edge_center_world(self, edge_key: Tuple[str, int, int], origin: Tuple[int, int]) -> Tuple[float, float]:
        """
        计算粗网格edge_key在细网格世界中的中心点坐标
        """
        kind, a, b = edge_key
        ox, oy = origin
        scale = self.cell_scale
        
        if kind == "V":
            bx, y = a, b
            # 粗网格竖边 (V, bx, y) 对应细网格 x = bx * scale
            # 中心点y坐标：y * scale + scale/2
            return (ox + bx * scale, oy + y * scale + scale / 2)
        else:
            x, by = a, b
            # 粗网格横边 (H, x, by) 对应细网格 y = by * scale
            # 中心点x坐标：x * scale + scale/2
            return (ox + x * scale + scale / 2, oy + by * scale)

    def _edge_adjacent_cells(self, edge_key: Tuple[str, int, int]) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """
        计算粗网格edge_key相邻的粗网格单元格
        """
        kind, a, b = edge_key
        if kind == "V":
            bx, y = a, b
            return (bx - 1, y), (bx, y)
        else:
            x, by = a, b
            return (x, by - 1), (x, by)

    # -------------------------
    # room naming (map_id scoped)
    # -------------------------
    def _unique_room_name(self, map_id: int, base: str) -> str:
        # base like "Kitchen" / "Corridor"
        cand = base
        row = self.db.fetch_one(
            "SELECT id FROM room WHERE map_id = ? AND name = ?",
            (int(map_id), str(cand)),
        )
        if not row:
            return cand

        # fallback: base_01, base_02 ...
        i = 1
        while True:
            cand = f"{base}_{i:02d}"
            row = self.db.fetch_one(
                "SELECT id FROM room WHERE map_id = ? AND name = ?",
                (int(map_id), str(cand)),
            )
            if not row:
                return cand
            i += 1

    # -------------------------
    # write: rooms + items
    # -------------------------
    def generate_and_save_dwelling(
        self,
        building_area_id: int,
        *,
        seed: int,
        tags_raw: List[str],
        n_floors: Optional[int] = None,
    ) -> JsonDict:
        """
        主入口：在一个 building_area 内生成 dwellings 风格的多房间布局并落库。

        返回：{"rooms": N, "doors": N, "windows": N, "stairs": N, "map_id":..., "tags":..., "seed":...}
        """
        ba = self._get_building_area(building_area_id)
        if not ba:
            return {"rooms": 0, "doors": 0, "windows": 0, "stairs": 0}

        map_id = int(ba["map_id"])
        layer_start = int(ba["layer_start"])
        layer_end = int(ba["layer_end"])

        tags = parse_tags(tags_raw)  # canonical tags

        area_cells, origin = self._area_cells_from_building_area(ba)
        if not area_cells:
            return {"rooms": 0, "doors": 0, "windows": 0, "stairs": 0, "map_id": map_id, "tags": tags, "seed": seed}

        max_floors = (layer_end - layer_start + 1)
        if n_floors is None:
            n_floors = max_floors
        n_floors = max(1, min(int(n_floors), int(max_floors)))

        # 1) core 生成（纯数据）
        house_export = generate_house_export(
            seed=seed,
            tags=tags,
            area_cells=area_cells,     # 局部坐标
            n_floors=n_floors,
        )

        # 2) 写 rooms
        room_db_id_by_local: Dict[Tuple[int, int], int] = {}  # (floor_index, local_room_id) -> db_room_id
        cell2room_local: Dict[Tuple[int, int, int], int] = {} # (floor, x, y) -> local_room_id

        created_rooms = 0

        for floor_i, plan in enumerate(house_export.get("floors", [])):
            # layer number
            L = layer_start + floor_i

            rooms_in_plan = plan.get("rooms", []) or []

            # ----- Pass 0: 先建立本层完整 cell -> local_room_id 映射（必须先做）-----
            for r in rooms_in_plan:
                local_rid = int(r["id"])
                cells_coarse = [(int(x), int(y)) for (x, y) in r.get("cells", [])]
                if not cells_coarse:
                    continue
                for (x, y) in cells_coarse:
                    cell2room_local[(floor_i, x, y)] = local_rid

            # inner_walls_by_room（粗网格 edge_key 列表）
            inner_map = plan.get("inner_walls_by_room", {}) or {}

            # ✅ 预计算：本层建筑外轮廓墙（只算一次）
            ox, oy = origin
            area_cells_coarse = [(int(x), int(y)) for (x, y) in (plan.get("area_cells", []) or [])]
            area_tiles_local = self.expand_room_cells_to_tiles(area_cells_coarse, self.cell_scale)
            area_wall_set_world = set((int(x) + ox, int(y) + oy) for x, y in area_tiles_local["wall"])

            # ----- Pass 1: 再真正插入 room（这里生成 inner_wall tiles）-----
            for r in rooms_in_plan:
                local_rid = int(r["id"])
                cells_coarse = [(int(x), int(y)) for (x, y) in r.get("cells", [])]
                if not cells_coarse:
                    continue

                ox, oy = origin

                room_type = str(r.get("type") or "generic")
                display = str(r.get("name") or room_type)

                # ✅ 粗转细：房间细网格全集（不按“房间边界”切 wall/space）
                room_full_local = self.coarse_cells_to_fine_set(cells_coarse, self.cell_scale)
                room_full_world = set((x + ox, y + oy) for (x, y) in room_full_local)

                # ✅ room.wall：只保留建筑外轮廓墙（与房间相交的那部分）
                wall_set_world = room_full_world.intersection(area_wall_set_world)
                wall = [[int(x), int(y)] for (x, y) in sorted(wall_set_world)]

                # space 先占位，后面会减 inner_wall
                space_set_world = room_full_world.difference(wall_set_world)
                space = [[int(x), int(y)] for (x, y) in sorted(space_set_world)]

                # ============================
                # ✅ 新增：inner wall tiles（细网格）
                # 规则：每条内墙段只归属两侧房间中 id 较小的一侧，避免画成 2 格厚
                # 门不挖洞：不做任何“跳过 door edge”的逻辑
                # ============================
                inner_wall_tiles_set = set()
                scale = self.cell_scale

                # plan 里 key 可能是 int 或 str，兼容两种
                edge_keys = inner_map.get(local_rid) or inner_map.get(str(local_rid)) or []
                for ek in edge_keys:
                    if not (isinstance(ek, (list, tuple)) and len(ek) == 3):
                        continue
                    kind = str(ek[0])
                    a = int(ek[1])
                    b = int(ek[2])

                    ek_t = (kind, a, b)
                    cA, cB = self._edge_adjacent_cells(ek_t)  # 相邻粗格

                    rA = cell2room_local.get((floor_i, cA[0], cA[1]))
                    rB = cell2room_local.get((floor_i, cB[0], cB[1]))
                    if not rA or not rB or rA == rB:
                        continue

                    # 只让 min(rA,rB) 的房间画这条内墙（避免双层墙）
                    if local_rid != min(int(rA), int(rB)):
                        continue

                    # 这条墙归属哪一侧粗格（属于 local_rid 的那侧）
                    owner_cell = cA if int(rA) == local_rid else cB

                    if kind == "V":
                        bx = a
                        cy = b
                        # owner_cell 是左侧粗格 (bx-1, cy) 就画右边界；否则画左边界
                        if owner_cell == (bx - 1, cy):
                            x_line = bx * scale - 1
                        else:
                            x_line = bx * scale
                        for y_f in range(cy * scale, (cy + 1) * scale):
                            inner_wall_tiles_set.add((ox + x_line, oy + y_f))

                    elif kind == "H":
                        cx = a
                        by = b
                        # owner_cell 是上侧粗格 (cx, by-1) 就画下边界；否则画上边界
                        if owner_cell == (cx, by - 1):
                            y_line = by * scale - 1
                        else:
                            y_line = by * scale
                        for x_f in range(cx * scale, (cx + 1) * scale):
                            inner_wall_tiles_set.add((ox + x_f, oy + y_line))

                inner_wall = [[int(x), int(y)] for (x, y) in sorted(inner_wall_tiles_set)]

                # 可选但推荐：把 inner_wall 从 space 里扣掉，避免“墙下还有地板”
                if inner_wall_tiles_set:
                    space_set = set((x, y) for x, y in space)
                    space_set.difference_update(inner_wall_tiles_set)
                    space = [[int(x), int(y)] for (x, y) in sorted(space_set)]

                # geom_json：先用 bbox+center，使用粗网格坐标计算
                xs = [x for x, _ in cells_coarse]
                ys = [y for _, y in cells_coarse]
                minx, maxx = min(xs), max(xs)
                miny, maxy = min(ys), max(ys)
                cx = (minx + maxx + 1) / 2.0
                cy = (miny + maxy + 1) / 2.0

                geom_json = {
                    "type": "dwelling_room",
                    "room_type": room_type,
                    "display_name": display,
                    "local_floor": floor_i,
                    "local_room_id": local_rid,
                    "origin": [ox, oy],
                    "bbox": [ox + minx * self.cell_scale, oy + miny * self.cell_scale, 
                             ox + (maxx + 1) * self.cell_scale, oy + (maxy + 1) * self.cell_scale],
                    "center": [ox + (cx * self.cell_scale), oy + (cy * self.cell_scale)],
                }

                tiles_json = {"wall": wall, "space": space, "inner_wall": inner_wall}
                other_json = {
                    "dwelling": {
                        "seed": int(seed),
                        "tags": tags,
                        "specs": house_export.get("specs", {}),
                        "floor_index": int(floor_i),
                        "local_room_id": int(local_rid),
                        "room_type": room_type,
                        "cell_scale": self.cell_scale,
                    }
                }

                # name：尽量像 JS（优先 display/type），但必须 map_id 唯一
                base_name = display[:1].upper() + display[1:] if display else "Room"
                name = self._unique_room_name(map_id, base_name)

                cur = self.db.execute(
                    "INSERT INTO room ("
                    "map_id, building_area_id, name, layer_start, layer_end, "
                    "room_type, geom_json, tiles_json, area, other_json"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(map_id),
                        int(building_area_id),
                        str(name),
                        int(L),
                        int(L),
                        str(room_type),
                        self._dumps(geom_json),
                        self._dumps(tiles_json),
                        int(len(space)),
                        self._dumps(other_json),
                    ),
                )
                db_room_id = int(cur.lastrowid)
                room_db_id_by_local[(floor_i, local_rid)] = db_room_id
                created_rooms += 1

        # 3) 写 items（doors/windows/stairs）
        created_doors = 0
        created_windows = 0
        created_stairs = 0

        for floor_i, plan in enumerate(house_export.get("floors", [])):
            L = layer_start + floor_i

            # door
            for idx, d in enumerate(plan.get("doors", []), start=1):
                edge_key = tuple(d["edge_key"])
                r1 = int(d["r1"]); r2 = int(d["r2"])
                db_r1 = room_db_id_by_local.get((floor_i, r1))
                db_r2 = room_db_id_by_local.get((floor_i, r2))
                if not db_r1 or not db_r2:
                    continue

                center_x, center_y = self._edge_center_world(edge_key, origin)

                cA_coarse, cB_coarse = self._edge_adjacent_cells(edge_key)

                # ✅ 门贴到“inner_wall 同侧”的墙线：选两侧房间中 local id 较小的一侧
                r_small = min(r1, r2)

                # 找到 r_small 对应的那侧粗格（cA 或 cB）
                rA = cell2room_local.get((floor_i, cA_coarse[0], cA_coarse[1]))
                rB = cell2room_local.get((floor_i, cB_coarse[0], cB_coarse[1]))
                if rA is None or rB is None:
                    continue

                owner_cell = cA_coarse if int(rA) == int(r_small) else cB_coarse

                # ✅ 生成 door 的 wall_tiles（只用一侧，保证与 inner_wall 同一条线）
                wall_tiles_set = set()
                scale = self.cell_scale
                ox, oy = origin

                kind = str(edge_key[0])
                a = int(edge_key[1])
                b = int(edge_key[2])

                if kind == "V":
                    bx = a
                    cy0 = b
                    # owner_cell == (bx-1, cy) => 线在 x=bx*scale-1；否则 x=bx*scale
                    x_line = bx * scale - 1 if owner_cell == (bx - 1, cy0) else bx * scale
                    for y_f in range(cy0 * scale, (cy0 + 1) * scale):
                        wall_tiles_set.add((ox + x_line, oy + y_f))

                else:  # "H"
                    cx0 = a
                    by = b
                    # owner_cell == (cx, by-1) => 线在 y=by*scale-1；否则 y=by*scale
                    y_line = by * scale - 1 if owner_cell == (cx0, by - 1) else by * scale
                    for x_f in range(cx0 * scale, (cx0 + 1) * scale):
                        wall_tiles_set.add((ox + x_f, oy + y_line))

                wall_tiles = [[int(x), int(y)] for (x, y) in sorted(wall_tiles_set)]

                # 计算门的半径：粗格1m，门宽0.9m（按 tile 尺寸缩放）
                door_radius = max(0.8, 0.9 * scale / 2)
                vector = {"type": "circle", "center": [center_x, center_y], "radius": door_radius}

                # ✅ 门挂到 owner 房间
                db_owner = room_db_id_by_local.get((floor_i, int(r_small)))
                if not db_owner:
                    continue

                props = {
                    "opening": "door",
                    "edge_key": list(edge_key),
                    "local_rooms": [r1, r2],
                    "db_rooms": [db_r1, db_r2],
                    "door_type": d.get("door_type", "REGULAR"),
                    "price": d.get("price", None),
                }
                item = {
                    "map_id": map_id,
                    "room_id": db_owner,  # 门挂到 owner 房间
                    "building_area_id": int(building_area_id),
                    "name": f"Door_L{L}_{db_r1}_{db_r2}_{idx:03d}",
                    "item_type": "door",
                    "layer_start": int(L),
                    "layer_end": int(L),
                    "timestep": 0,
                    "position_x": float(center_x),
                    "position_y": float(center_y),
                    "vector_json": self._dumps(vector),
                    "tiles_json": self._dumps({"wall_tiles": wall_tiles}),
                    "properties_json": self._dumps(props),
                }
                if self._insert_item(item):
                    created_doors += 1

            # window
            for idx, w in enumerate(plan.get("windows", []), start=1):
                edge_key = tuple(w["edge_key"])
                cx, cy = self._edge_center_world(edge_key, origin)

                cA_coarse, cB_coarse = self._edge_adjacent_cells(edge_key)
                
                # window 通常只有“内侧”那格属于 area_cells；用它来确定宿主 room
                # 使用粗网格坐标检查
                area_set_coarse = set((int(x), int(y)) for x, y in plan.get("area_cells", []))
                inner_coarse = cA_coarse if cA_coarse in area_set_coarse else (cB_coarse if cB_coarse in area_set_coarse else None)
                if inner_coarse is None:
                    continue

                local_rid = cell2room_local.get((floor_i, inner_coarse[0], inner_coarse[1]))
                if not local_rid:
                    continue
                db_r = room_db_id_by_local.get((floor_i, int(local_rid)))
                if not db_r:
                    continue

                # ✅ 粗转细：将内粗网格单元格转换为细网格墙格
                wall_tiles = []
                scale = self.cell_scale
                cx_coarse, cy_coarse = inner_coarse
                
                # 窗户只占内侧粗格中靠近边界的那一排细格
                if edge_key[0] == "V":
                    # 竖边：取内侧粗格靠近边界的竖线
                    x = cx_coarse * scale
                    if inner_coarse == cA_coarse:  # 内侧是左边的粗格，边界在右侧
                        x += scale - 1
                    # 整个y范围
                    for y_fine in range(cy_coarse * scale, (cy_coarse + 1) * scale):
                        wall_tiles.append([origin[0] + x, origin[1] + y_fine])
                else:
                    # 横边：取内侧粗格靠近边界的横线
                    y = cy_coarse * scale
                    if inner_coarse == cA_coarse:  # 内侧是上边的粗格，边界在下方
                        y += scale - 1
                    # 整个x范围
                    for x_fine in range(cx_coarse * scale, (cx_coarse + 1) * scale):
                        wall_tiles.append([origin[0] + x_fine, origin[1] + y])

                # 计算窗的半径：粗格1m，窗宽0.6m
                window_radius = max(0.5, 0.6 * scale / 2)
                vector = {"type": "circle", "center": [cx, cy], "radius": window_radius}
                props = {"opening": "window", "edge_key": list(edge_key)}

                item = {
                    "map_id": map_id,
                    "room_id": db_r,
                    "building_area_id": int(building_area_id),
                    "name": f"Window_L{L}_{db_r}_{idx:03d}",
                    "item_type": "window",
                    "layer_start": int(L),
                    "layer_end": int(L),
                    "timestep": 0,
                    "position_x": float(cx),
                    "position_y": float(cy),
                    "vector_json": self._dumps(vector),
                    "tiles_json": self._dumps({"wall_tiles": wall_tiles}),
                    "properties_json": self._dumps(props),
                }
                if self._insert_item(item):
                    created_windows += 1

            # stairs（先按 plan_export 输出直接写；后续你也可以走你现有的 group 逻辑）
            for idx, st in enumerate(plan.get("stairs", []), start=1):
                cells = st.get("cells", [])
                if not cells:
                    continue
                xs = [c[0] for c in cells]
                ys = [c[1] for c in cells]
                cx = origin[0] + (min(xs) + max(xs) + 1) / 2.0
                cy = origin[1] + (min(ys) + max(ys) + 1) / 2.0

                kind = st.get("kind", "stair")
                vector = {"type": "rectangle", "center": [cx, cy], "width": 4.0, "height": 8.0}
                props = {"stair_kind": kind, "edge": st.get("meta", {})}

                # 先挂到任意一个包含 stairs 的 room（用第一个 cell 找）
                area_set = set((int(x), int(y)) for x, y in plan.get("area_cells", []))
                inner = cells[0]
                local_rid = cell2room_local.get((floor_i, inner[0], inner[1]))
                db_r = room_db_id_by_local.get((floor_i, int(local_rid))) if local_rid else None

                item = {
                    "map_id": map_id,
                    "room_id": int(db_r) if db_r else None,
                    "building_area_id": int(building_area_id),
                    "name": f"Stairs_L{L}_{idx:03d}",
                    "item_type": "stairs",
                    "layer_start": int(L),
                    "layer_end": int(L),
                    "timestep": 0,
                    "position_x": float(cx),
                    "position_y": float(cy),
                    "vector_json": self._dumps(vector),
                    "tiles_json": self._dumps({}),
                    "properties_json": self._dumps(props),
                }
                if self._insert_item(item):
                    created_stairs += 1

        return {
            "map_id": map_id,
            "seed": int(seed),
            "tags": tags,
            "rooms": created_rooms,
            "doors": created_doors,
            "windows": created_windows,
            "stairs": created_stairs,
        }

    def _insert_item(self, item: JsonDict) -> bool:
        # 防重复：同 map_id + name
        exists = self.db.fetch_one(
            "SELECT id FROM item WHERE map_id = ? AND name = ?",
            (int(item["map_id"]), str(item["name"])),
        )
        if exists:
            return False

        self.db.execute(
            "INSERT INTO item ("
            "map_id, room_id, building_area_id, name, item_type, "
            "layer_start, layer_end, timestep, "
            "position_x, position_y, "
            "vector_json, tiles_json, properties_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(item["map_id"]),
                int(item["room_id"]) if item.get("room_id") is not None else None,
                int(item["building_area_id"]) if item.get("building_area_id") is not None else None,
                str(item["name"]),
                str(item["item_type"]),
                int(item["layer_start"]),
                int(item["layer_end"]),
                int(item.get("timestep", 0)),
                float(item["position_x"]) if item.get("position_x") is not None else None,
                float(item["position_y"]) if item.get("position_y") is not None else None,
                str(item.get("vector_json") or ""),
                str(item.get("tiles_json") or ""),
                str(item.get("properties_json") or ""),
            ),
        )
        return True
