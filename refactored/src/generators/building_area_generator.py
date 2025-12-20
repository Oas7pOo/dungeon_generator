# src/generators/building_area_generator.py
from __future__ import annotations

from abc import ABC, abstractmethod
import math
import random
import json
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

try:
    from shapely.strtree import STRtree
except Exception:
    STRtree = None

from ..db.database import DatabaseManager
from ..db.building_area_dao import BuildingAreaDAO


# -----------------------------
# geometry helpers
# -----------------------------
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


def _rect_corners_from_center(center_x: float, center_y: float, width: float, height: float) -> List[Tuple[float, float]]:
    half_w = float(width) / 2.0
    half_h = float(height) / 2.0
    return [
        (center_x - half_w, center_y - half_h),
        (center_x + half_w, center_y - half_h),
        (center_x + half_w, center_y + half_h),
        (center_x - half_w, center_y + half_h),
    ]


def _normalize_tree_candidates(tree: Any, geoms: List[Any], query_geom: Any) -> List[Any]:
    """
    shapely STRtree 在不同版本可能返回:
    - geometry 列表
    - index (int) 数组
    这里统一转换为 geometry 列表
    """
    try:
        cand = tree.query(query_geom)
    except Exception:
        return geoms

    if cand is None:
        return geoms

    # 可能是 numpy array
    try:
        cand_list = list(cand)
    except Exception:
        return geoms

    if not cand_list:
        return []

    # 若返回的是索引
    if isinstance(cand_list[0], (int, np.integer)):
        out = []
        for i in cand_list:
            ii = int(i)
            if 0 <= ii < len(geoms):
                out.append(geoms[ii])
        return out

    return cand_list


# -----------------------------
# Base Generator
# -----------------------------
class BuildingAreaGenerator(ABC):
    """
    建筑区生成器基类（V2）

    ✅ 使用 map_id，不使用 map_name 当 key
    ✅ building_area: layer_start/layer_end, geom_type, center_x/center_y/radius/geom_json/size_json
    ✅ 查询与关联只用 *_id
    """

    def __init__(self, name: str, map_id: int, layer: int = 1, db_manager=None):
        self.name = str(name)
        self.map_id = int(map_id)
        self.layer = int(layer)
        self.db_manager = db_manager or DatabaseManager()
        self.dao = BuildingAreaDAO(self.db_manager)

        # 运行期缓存：layer -> {"geoms":[...], "tree":STRtree|None, "id2area_id":{id(geom):area_id}}
        self._layer_geom_cache: Dict[int, Dict[str, Any]] = {}
        # 最大空闲区缓存：(layer, w, h, distance) -> shapely geom
        self._free_area_cache: Dict[Tuple[int, int, int, float], Any] = {}

    # -------------------------
    # caches
    # -------------------------
    def _clear_runtime_caches(self) -> None:
        self._layer_geom_cache.clear()
        self._free_area_cache.clear()

    def _invalidate_layer_runtime_caches(self, layer_start: int, layer_end: int) -> None:
        a = int(layer_start)
        b = int(layer_end)
        if a > b:
            a, b = b, a

        for L in range(a, b + 1):
            self._layer_geom_cache.pop(L, None)

        if self._free_area_cache:
            for k in list(self._free_area_cache.keys()):
                if a <= int(k[0]) <= b:
                    self._free_area_cache.pop(k, None)

    def _update_layer_geometry_cache_after_insert(self, geom: Any, layer_start: int, layer_end: int, building_area_id: int) -> None:
        a = int(layer_start)
        b = int(layer_end)
        if a > b:
            a, b = b, a

        for L in range(a, b + 1):
            idx = self._layer_geom_cache.get(L)
            if idx is None:
                continue

            idx["geoms"].append(geom)
            idx["id2area_id"][id(geom)] = int(building_area_id)

            if STRtree is not None:
                try:
                    idx["tree"] = STRtree(idx["geoms"])
                except Exception:
                    idx["tree"] = None

        # 障碍变化 -> 空闲区缓存失效
        if self._free_area_cache:
            for k in list(self._free_area_cache.keys()):
                if a <= int(k[0]) <= b:
                    self._free_area_cache.pop(k, None)

    # -------------------------
    # map / areas fetch
    # -------------------------
    def get_map_size(self) -> Optional[Tuple[int, int]]:
        return self.dao.get_map_size(self.map_id)

    def get_building_areas_by_layer(self, layer: Optional[int] = None) -> List[Dict[str, Any]]:
        return self._get_same_layer_buildings(layer if layer is not None else self.layer)

    def _get_same_layer_buildings(self, layer_index: int) -> List[Dict[str, Any]]:
        return self.dao.list_building_areas_covering_layer(self.map_id, int(layer_index))

    # -------------------------
    # shape conversion
    # -------------------------
    def _area_row_to_geom(self, area: Dict[str, Any]) -> Optional[Any]:
        geom_type = (area.get("geom_type") or "").strip()
        cx = area.get("center_x")
        cy = area.get("center_y")
        radius = area.get("radius")
        geom_json = area.get("geom_json")
        size_json = area.get("size_json") or {}

        if geom_type == "circle":
            if cx is None or cy is None or radius is None:
                return None
            try:
                return Point((float(cx), float(cy))).buffer(float(radius))
            except Exception:
                return None

        # polygon: geom_json 必须是 corners
        if geom_type == "polygon":
            return _safe_polygon(geom_json)

        # rectangle: 尽量不存 corners，靠 center + size_json 重建
        if geom_type == "rectangle":
            try:
                width = float(size_json.get("width"))
                height = float(size_json.get("height"))
            except Exception:
                return _safe_polygon(geom_json)

            if cx is None or cy is None:
                return None

            # angle != 0 的旋转矩形，必须有 corners（geom_json）
            angle = size_json.get("angle", 0)
            try:
                angle_val = float(angle)
            except Exception:
                angle_val = 0.0

            if abs(angle_val) > 1e-9:
                return _safe_polygon(geom_json)

            corners = _rect_corners_from_center(float(cx), float(cy), width, height)
            return _safe_polygon(corners)

        # 其它类型：默认按 polygon 解析 geom_json
        return _safe_polygon(geom_json)

    # -------------------------
    # overlap index
    # -------------------------
    def _build_layer_geometry_index(self, layer_index: int) -> Dict[str, Any]:
        layer_index = int(layer_index)
        if layer_index in self._layer_geom_cache:
            return self._layer_geom_cache[layer_index]

        areas = self._get_same_layer_buildings(layer_index)
        geoms: List[Any] = []
        id2area_id: Dict[int, int] = {}

        for a in areas:
            geom = self._area_row_to_geom(a)
            if geom is None:
                continue
            geoms.append(geom)
            try:
                id2area_id[id(geom)] = int(a["id"])
            except Exception:
                pass

        tree = None
        if STRtree is not None and geoms:
            try:
                tree = STRtree(geoms)
            except Exception:
                tree = None

        self._layer_geom_cache[layer_index] = {"geoms": geoms, "tree": tree, "id2area_id": id2area_id}
        return self._layer_geom_cache[layer_index]

    # -------------------------
    # overlap checks
    # -------------------------
    def check_shape_overlap(
        self,
        shape: Any,
        shape_type: str = "polygon",
        layer: Optional[int] = None,
        distance: float = 0.0,
    ) -> bool:
        layer_index = int(layer if layer is not None else self.layer)

        if shape_type == "circle":
            center, radius = shape
            try:
                base_shape = Point(center).buffer(float(radius))
            except Exception:
                return False
        else:
            base_shape = _safe_polygon(shape)
            if base_shape is None:
                return False

        check_shape = base_shape.buffer(float(distance)) if distance and distance > 0 else base_shape

        idx = self._build_layer_geometry_index(layer_index)
        geoms = idx["geoms"]
        tree = idx["tree"]

        if not geoms:
            return False

        candidates = geoms
        if tree is not None:
            candidates = _normalize_tree_candidates(tree, geoms, check_shape)

        # 允许贴边：只在交集面积 > eps 才算重叠
        eps = 1e-9
        for other in candidates:
            try:
                if not check_shape.intersects(other):
                    continue
                inter = check_shape.intersection(other)
                if getattr(inter, "area", 0.0) > eps:
                    return True
            except Exception:
                return True

        return False

    def check_multi_layer_overlap(
        self,
        shape: Any,
        shape_type: str,
        layer_start: int,
        layer_end: int,
        distance: float = 0.0,
    ) -> bool:
        a = int(layer_start)
        b = int(layer_end)
        if a > b:
            a, b = b, a
        for L in range(a, b + 1):
            if self.check_shape_overlap(shape, shape_type=shape_type, layer=L, distance=distance):
                return True
        return False

    # -------------------------
    # params
    # -------------------------
    def process_building_params(
        self,
        name: Optional[str] = None,
        map_id: Optional[int] = None,
        layer: Optional[Any] = None,
    ):
        if name is not None:
            self.name = str(name)
        if map_id is not None:
            self.map_id = int(map_id)

        if self.map_id is None:
            print("错误: 未指定 map_id")
            return None

        layer_to_process = layer if layer is not None else self.layer

        is_multi_layer = False
        layer_start = layer_end = 0
        if isinstance(layer_to_process, tuple) and len(layer_to_process) == 2:
            layer_start, layer_end = int(layer_to_process[0]), int(layer_to_process[1])
            is_multi_layer = (layer_start != layer_end)
            self.layer = layer_start
            layer_list = list(range(min(layer_start, layer_end), max(layer_start, layer_end) + 1))
            layer_start, layer_end = min(layer_start, layer_end), max(layer_start, layer_end)
        elif isinstance(layer_to_process, int):
            layer_start = layer_end = int(layer_to_process)
            self.layer = int(layer_to_process)
            layer_list = [self.layer]
        else:
            print(f"错误: 无效的 layer 参数 {layer_to_process}")
            return None

        map_size = self.get_map_size()
        if not map_size:
            print(f"错误: 无法获取 map_id={self.map_id} 的尺寸")
            return None

        map_width, map_height = map_size
        return is_multi_layer, layer_start, layer_end, layer_list, map_width, map_height

    def generate_building_name(self, name: str, is_multi_layer: bool, layer_start: int, layer_end: int) -> str:
        if is_multi_layer:
            return f"{name}_层{layer_start}至{layer_end}"
        return f"{name}_层{layer_start}"

    def add_multi_layer_info(self, data_dict: Dict[str, Any], is_multi_layer: bool, layer_start: int, layer_end: int) -> Dict[str, Any]:
        if is_multi_layer:
            data_dict["layer_start"] = int(layer_start)
            data_dict["layer_end"] = int(layer_end)
            data_dict["is_multi_layer"] = True
        return data_dict

    # -------------------------
    # size generation
    # -------------------------
    def generate_room_size(self, min_size, max_size, dist="exponential", regular_rect=True):
        min_width, min_height = min_size
        max_width, max_height = max_size

        min_width = max(5, int(min_width))
        min_height = max(5, int(min_height))
        max_width = max(min_width, int(max_width))
        max_height = max(min_height, int(max_height))

        if regular_rect and random.random() < 1 / 20:
            min_side = max(min_width, min_height)
            max_side = min(max_width, max_height)
            if min_side <= max_side:
                if dist == "uniform":
                    side = np.random.randint(min_side, max_side + 1)
                else:
                    side = int(np.random.exponential(scale=(max_side - min_side) / 3) + min_side)
                    side = max(min_side, min(side, max_side))
                return int(side), int(side)

        if dist == "uniform":
            width = int(np.random.randint(min_width, max_width + 1))
            height = int(np.random.randint(min_height, max_height + 1))
        elif dist == "exponential":
            width = int(np.random.exponential(scale=(max_width - min_width) / 3) + min_width)
            height = int(np.random.exponential(scale=(max_height - min_height) / 3) + min_height)
            width = max(min_width, min(width, max_width))
            height = max(min_height, min(height, max_height))
        else:
            raise ValueError(f"不支持的分布类型: {dist}")

        if regular_rect:
            aspect_ratio = width / height if height != 0 else 1.0
            if aspect_ratio > 2.0 or aspect_ratio < 0.5:
                if dist == "uniform":
                    if random.random() < 0.5:
                        height = max(min_height, min(int(width), max_height))
                    else:
                        width = max(min_width, min(int(height), max_width))
                else:
                    scale_factor = float(np.random.normal(1.0, 0.3))
                    scale_factor = max(0.5, min(scale_factor, 2.0))
                    if width > height:
                        width = max(min_width, min(int(width / scale_factor), max_width))
                        height = max(min_height, min(int(height * scale_factor), max_height))
                    else:
                        width = max(min_width, min(int(width * scale_factor), max_width))
                        height = max(min_height, min(int(height / scale_factor), max_height))

        return int(width), int(height)

    # -------------------------
    # free areas
    # -------------------------
    def _largest_free_polygon_for_layer(self, layer_index: int, map_width: int, map_height: int, distance: float = 0.0):
        key = (int(layer_index), int(map_width), int(map_height), float(distance))
        if key in self._free_area_cache:
            return self._free_area_cache[key]

        map_polygon = Polygon([(0, 0), (map_width, 0), (map_width, map_height), (0, map_height)])

        areas = self.get_building_areas_by_layer(layer_index)
        obstacles = []
        for a in areas:
            geom = self._area_row_to_geom(a)
            if geom is None:
                continue
            if distance and distance > 0:
                try:
                    geom = geom.buffer(float(distance))
                except Exception:
                    pass
            obstacles.append(geom)

        if obstacles:
            try:
                blocked = unary_union(obstacles)
                available = map_polygon.difference(blocked)
            except Exception:
                available = map_polygon
                for ob in obstacles:
                    try:
                        available = available.difference(ob)
                    except Exception:
                        pass
        else:
            available = map_polygon

        max_polygon = None
        max_area = 0.0
        if hasattr(available, "geoms"):
            for g in available.geoms:
                if getattr(g, "area", 0.0) > max_area:
                    max_area = float(g.area)
                    max_polygon = g
        else:
            max_polygon = available

        if max_polygon is None or getattr(max_polygon, "area", 0.0) <= 0:
            max_polygon = None

        self._free_area_cache[key] = max_polygon
        return max_polygon

    def calculate_free_areas(self, layer_index: int, map_width: int, map_height: int, distance: float = 0.0):
        max_polygon = self._largest_free_polygon_for_layer(layer_index, map_width, map_height, distance)
        if not max_polygon or max_polygon.area == 0:
            return None
        return max_polygon.bounds

    # -------------------------
    @abstractmethod
    def create_building_area(self, **kwargs):
        pass

    def close(self):
        self.db_manager.close()


# -----------------------------
# Rectangle Generator
# -----------------------------
class RectangleBuildingAreaGenerator(BuildingAreaGenerator):
    def __init__(self, name: str, map_id: int, layer: int = 1, db_manager=None):
        super().__init__(name=name, map_id=map_id, layer=layer, db_manager=db_manager)
        self.type = "rectangle"

    def create_building_area(
        self,
        name: Optional[str] = None,
        map_id: Optional[int] = None,
        layer: Optional[Any] = None,
        rect_size=[(5, 5), (30, 30)],
        angle=False,
        dist="exponential",
        placement_mode="largest_first",
        max_attempts=10,
        distance=0,
        enable_resize=False,
        regular_rect=True,
    ):
        self._clear_runtime_caches()

        params_result = self.process_building_params(name=name, map_id=map_id, layer=layer)
        if params_result is None:
            return []

        is_multi_layer, layer_start, layer_end, _, map_width, map_height = params_result

        for L in range(layer_start, layer_end + 1):
            self._build_layer_geometry_index(L)

        if not angle:
            return self._create_normal_rectangle(
                rect_size, dist, placement_mode, max_attempts,
                is_multi_layer, layer_start, layer_end,
                map_width, map_height, distance,
                enable_resize=enable_resize, regular_rect=regular_rect
            )
        return self._create_angled_rectangle(
            rect_size, angle, dist, max_attempts,
            is_multi_layer, layer_start, layer_end,
            map_width, map_height, distance,
            regular_rect=regular_rect
        )

    def create_building_areas_global(
        self,
        name: Optional[str] = None,
        map_id: Optional[int] = None,
        layer: Optional[Any] = None,
        rect_size=[(5, 5), (30, 30)],
        N=30,
        dist="exponential",
        placement_mode="largest_first",
        max_attempts=10,
        distance=0,
        enable_resize=True,
        regular_rect=True,
        fill_budget=None,
    ):
        params_result = self.process_building_params(name=name, map_id=map_id, layer=layer)
        if params_result is None:
            return []

        is_multi_layer, layer_start, layer_end, _, map_width, map_height = params_result

        self._clear_runtime_caches()
        for L in range(layer_start, layer_end + 1):
            self._build_layer_geometry_index(L)

        base_name = self.generate_building_name(self.name, is_multi_layer, layer_start, layer_end)

        min_size, max_size = rect_size
        min_w = max(5, int(min_size[0]))
        min_h = max(5, int(min_size[1]))
        max_w = max(min_w, int(max_size[0]))
        max_h = max(min_h, int(max_size[1]))

        def _common_free_bounds():
            eps = 1e-6
            common = None
            for L in range(layer_start, layer_end + 1):
                b = self.calculate_free_areas(L, map_width, map_height, distance)
                if not b:
                    return None
                minx, miny, maxx, maxy = b
                bx0 = int(math.ceil(minx - eps))
                by0 = int(math.ceil(miny - eps))
                bx1 = int(math.floor(maxx + eps))
                by1 = int(math.floor(maxy + eps))
                if common is None:
                    common = [bx0, by0, bx1, by1]
                else:
                    common[0] = max(common[0], bx0)
                    common[1] = max(common[1], by0)
                    common[2] = min(common[2], bx1)
                    common[3] = min(common[3], by1)
                if common[0] >= common[2] or common[1] >= common[3]:
                    return None
            return tuple(common)

        def _try_place(width, height, unique_name, bounds=None):
            width = int(width)
            height = int(height)
            if width <= 0 or height <= 0:
                return None
            if width > map_width or height > map_height:
                return None

            if bounds is None:
                x0, y0, x1, y1 = 0, 0, map_width, map_height
            else:
                x0, y0, x1, y1 = bounds

            max_left = x1 - width
            max_top = y1 - height
            if max_left < x0 or max_top < y0:
                return None

            trials = max(10, int(max_attempts) * 3)
            for _ in range(trials):
                left = int(np.random.randint(x0, max_left + 1))
                top = int(np.random.randint(y0, max_top + 1))
                center_x = left + width / 2.0
                center_y = top + height / 2.0

                # axis-aligned rectangle -> overlap 用 polygon corners
                vertices = _rect_corners_from_center(center_x, center_y, width, height)

                if self.check_multi_layer_overlap(vertices, "polygon", layer_start, layer_end, distance):
                    continue

                return self._save_rectangle(
                    name=unique_name,
                    is_multi_layer=is_multi_layer,
                    layer_start=layer_start,
                    layer_end=layer_end,
                    center_x=center_x,
                    center_y=center_y,
                    width=width,
                    height=height,
                    vertices=None,   # ✅ 轴对齐矩形不存 geom_json
                    angle=None,
                )
            return None

        def _try_place_with_shrink(width, height, unique_name, bounds=None):
            w, h = int(width), int(height)
            while True:
                placed = _try_place(w, h, unique_name, bounds=bounds)
                if placed:
                    return placed
                if not enable_resize:
                    return None
                if (w - 1) < min_w or (h - 1) < min_h:
                    return None
                w -= 1
                h -= 1

        sizes = []
        for i in range(max(1, int(N))):
            w, h = self.generate_room_size((min_w, min_h), (max_w, max_h), dist, regular_rect=regular_rect)
            sizes.append((int(w) * int(h), int(w), int(h), i + 1))

        if placement_mode == "largest_first":
            sizes.sort(reverse=True, key=lambda x: x[0])
        else:
            random.shuffle(sizes)

        success_areas = []
        shrink_queue = []

        common_bounds = _common_free_bounds()

        for _, w, h, seq in sizes:
            unique_name = f"{base_name}_{seq}"
            placed = _try_place(w, h, unique_name, bounds=None)
            if placed:
                success_areas.extend(placed)
            else:
                shrink_queue.append((w * h, w, h, unique_name))

        if shrink_queue:
            shrink_queue.sort(reverse=True, key=lambda x: x[0])
            for _, w, h, unique_name in shrink_queue:
                bounds_to_use = common_bounds if common_bounds else None
                placed = _try_place_with_shrink(w, h, unique_name, bounds=bounds_to_use)
                if placed:
                    success_areas.extend(placed)

        if fill_budget is None:
            fill_budget = max(0, int(N) // 2)

        if fill_budget > 0:
            fill_max_w = min(max_w, min_w + 5)
            fill_max_h = min(max_h, min_h + 5)
            common_bounds = _common_free_bounds()

            for k in range(1, fill_budget + 1):
                w, h = self.generate_room_size((min_w, min_h), (fill_max_w, fill_max_h), dist="exponential", regular_rect=regular_rect)
                unique_name = f"{base_name}_fill_{k}"
                placed = _try_place_with_shrink(w, h, unique_name, bounds=common_bounds if common_bounds else None)
                if not placed:
                    placed = _try_place_with_shrink(w, h, unique_name, bounds=None)
                if placed:
                    success_areas.extend(placed)

        return success_areas

    def _create_normal_rectangle(
        self,
        rect_size,
        dist,
        placement_mode,
        max_attempts,
        is_multi_layer,
        layer_start,
        layer_end,
        map_width,
        map_height,
        distance,
        enable_resize=False,
        regular_rect=True,
    ):
        full_name = self.generate_building_name(self.name, is_multi_layer, layer_start, layer_end)
        min_size, max_size = rect_size

        min_w_eff = max(5, int(min_size[0]))
        min_h_eff = max(5, int(min_size[1]))
        max_w_eff = max(min_w_eff, int(max_size[0]))
        max_h_eff = max(min_h_eff, int(max_size[1]))

        print("阶段1: 尝试随机放置（优先大尺寸）...")

        sizes_to_try = []
        for _ in range(max_attempts):
            w, h = self.generate_room_size((min_w_eff, min_h_eff), (max_w_eff, max_h_eff), dist, regular_rect=regular_rect)
            sizes_to_try.append((w * h, w, h))

        if placement_mode == "largest_first":
            sizes_to_try.sort(reverse=True, key=lambda x: x[0])

        pos_trials = max(2, max_attempts)
        for _, width, height in sizes_to_try:
            width = int(width)
            height = int(height)
            if map_width - width < 0 or map_height - height < 0:
                continue

            for _t in range(pos_trials):
                left = int(np.random.randint(0, map_width - width + 1))
                top = int(np.random.randint(0, map_height - height + 1))
                center_x = left + width / 2.0
                center_y = top + height / 2.0

                vertices = _rect_corners_from_center(center_x, center_y, width, height)

                if not self.check_multi_layer_overlap(vertices, "polygon", layer_start, layer_end, distance):
                    print(f"✅ 随机放置成功，尺寸: {width}x{height}")
                    return self._save_rectangle(
                        name=full_name,
                        is_multi_layer=is_multi_layer,
                        layer_start=layer_start,
                        layer_end=layer_end,
                        center_x=center_x,
                        center_y=center_y,
                        width=width,
                        height=height,
                        vertices=None,  # ✅ 不存 corners
                        angle=None,
                    )

        print("阶段2: 使用最大空闲区域引导放置...")

        free_polys = []
        for L in range(layer_start, layer_end + 1):
            poly = self._largest_free_polygon_for_layer(L, map_width, map_height, distance)
            if poly is None:
                free_polys = []
                break
            free_polys.append(poly)

        common_free = None
        if free_polys:
            common_free = free_polys[0]
            for p in free_polys[1:]:
                try:
                    common_free = common_free.intersection(p)
                except Exception:
                    common_free = None
                    break

        if common_free is not None and hasattr(common_free, "geoms"):
            best = None
            best_area = 0.0
            for g in common_free.geoms:
                if getattr(g, "area", 0.0) > best_area:
                    best_area = g.area
                    best = g
            common_free = best

        if common_free is not None and getattr(common_free, "area", 0.0) > 0:
            minx, miny, maxx, maxy = common_free.bounds
            bx0 = int(math.ceil(minx))
            by0 = int(math.ceil(miny))
            bx1 = int(math.floor(maxx))
            by1 = int(math.floor(maxy))

            avail_w = max(0, bx1 - bx0)
            avail_h = max(0, by1 - by0)

            sizes_to_try2 = []
            for _ in range(max_attempts):
                w, h = self.generate_room_size((min_w_eff, min_h_eff), (max_w_eff, max_h_eff), dist, regular_rect=regular_rect)
                sizes_to_try2.append((w * h, w, h))
            sizes_to_try2.sort(reverse=True, key=lambda x: x[0])

            for _, w0, h0 in sizes_to_try2:
                width = int(w0)
                height = int(h0)

                while (width > avail_w) or (height > avail_h):
                    width -= 1
                    height -= 1
                    if width < min_w_eff or height < min_h_eff:
                        width = None
                        break
                if width is None:
                    continue

                while width >= min_w_eff and height >= min_h_eff:
                    for _try in range(max_attempts * 4):
                        left = random.randint(bx0, bx1 - width) if (bx1 - width) >= bx0 else None
                        top = random.randint(by0, by1 - height) if (by1 - height) >= by0 else None
                        if left is None or top is None:
                            break

                        center_x = left + width / 2.0
                        center_y = top + height / 2.0
                        corners = _rect_corners_from_center(center_x, center_y, width, height)
                        rect_poly = _safe_polygon(corners)
                        if rect_poly is None:
                            continue

                        try:
                            if not common_free.covers(rect_poly):
                                continue
                        except Exception:
                            pass

                        if not self.check_multi_layer_overlap(corners, "polygon", layer_start, layer_end, distance):
                            print(f"✅ 空闲区引导放置成功（逐格缩小），尺寸: {width}x{height}")
                            return self._save_rectangle(
                                name=full_name,
                                is_multi_layer=is_multi_layer,
                                layer_start=layer_start,
                                layer_end=layer_end,
                                center_x=center_x,
                                center_y=center_y,
                                width=width,
                                height=height,
                                vertices=None,  # ✅ 仍不存 corners
                                angle=None,
                            )

                    if not enable_resize:
                        break
                    if (width - 1) < min_w_eff or (height - 1) < min_h_eff:
                        break
                    width -= 1
                    height -= 1

        if enable_resize and map_width <= 100 and map_height <= 100:
            print("阶段3: 小地图暴力扫描 + 逐格缩小（≤100x100）...")

            base_w, base_h = self.generate_room_size((min_w_eff, min_h_eff), (max_w_eff, max_h_eff), dist, regular_rect=regular_rect)
            width = int(base_w)
            height = int(base_h)

            while width > map_width or height > map_height:
                width -= 1
                height -= 1
                if width < min_w_eff or height < min_h_eff:
                    width = None
                    break

            if width is not None:
                while width >= min_w_eff and height >= min_h_eff:
                    for left in range(0, map_width - width + 1):
                        for top in range(0, map_height - height + 1):
                            center_x = left + width / 2.0
                            center_y = top + height / 2.0
                            corners = _rect_corners_from_center(center_x, center_y, width, height)

                            if not self.check_multi_layer_overlap(corners, "polygon", layer_start, layer_end, distance):
                                print(f"✅ 暴力扫描成功，逐格缩小后尺寸: {width}x{height}")
                                return self._save_rectangle(
                                    name=full_name,
                                    is_multi_layer=is_multi_layer,
                                    layer_start=layer_start,
                                    layer_end=layer_end,
                                    center_x=center_x,
                                    center_y=center_y,
                                    width=width,
                                    height=height,
                                    vertices=None,
                                    angle=None,
                                )

                    if (width - 1) < min_w_eff or (height - 1) < min_h_eff:
                        break
                    width -= 1
                    height -= 1

        print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置矩形建筑区")
        return []

    def _create_angled_rectangle(
        self,
        rect_size,
        angle,
        dist,
        max_attempts,
        is_multi_layer,
        layer_start,
        layer_end,
        map_width,
        map_height,
        distance,
        regular_rect=True,
    ):
        full_name = self.generate_building_name(self.name, is_multi_layer, layer_start, layer_end)

        if not isinstance(angle, list) and angle is True:
            angle_list = [0, 30, 45, 60]
        elif isinstance(angle, list):
            angle_list = []
            for angle_item in angle:
                if isinstance(angle_item, tuple) and len(angle_item) == 2:
                    a0, a1 = angle_item
                    angle_list.extend(list(range(int(a0), int(a1) + 1, 5)))
                else:
                    angle_list.append(angle_item)
        else:
            angle_list = [0]

        for _ in range(max_attempts):
            selected_angle = float(random.choice(angle_list))

            min_size, max_size = rect_size
            width, height = self.generate_room_size(min_size, max_size, dist, regular_rect=regular_rect)

            diagonal_length = math.sqrt(width ** 2 + height ** 2)
            safe_margin = diagonal_length / 2.0
            if safe_margin * 2 >= map_width or safe_margin * 2 >= map_height:
                safe_margin = min(map_width, map_height) / 4.0

            valid_left = max(int(safe_margin), 0)
            valid_right = max(int(map_width - safe_margin), valid_left + 1)
            valid_top = max(int(safe_margin), 0)
            valid_bottom = max(int(map_height - safe_margin), valid_top + 1)

            center_x = float(np.random.randint(valid_left, valid_right))
            center_y = float(np.random.randint(valid_top, valid_bottom))

            half_width = float(width) / 2.0
            half_height = float(height) / 2.0

            vertices = [
                (center_x - half_width, center_y - half_height),
                (center_x + half_width, center_y - half_height),
                (center_x + half_width, center_y + half_height),
                (center_x - half_width, center_y + half_height),
            ]

            rotated_vertices = []
            angle_rad = np.radians(selected_angle)
            cos_a = float(np.cos(angle_rad))
            sin_a = float(np.sin(angle_rad))

            for x, y in vertices:
                x_shifted = x - center_x
                y_shifted = y - center_y
                x_rot = x_shifted * cos_a - y_shifted * sin_a
                y_rot = x_shifted * sin_a + y_shifted * cos_a
                rotated_vertices.append((x_rot + center_x, y_rot + center_y))

            is_out = any((x < 0 or x > map_width or y < 0 or y > map_height) for x, y in rotated_vertices)
            if is_out:
                continue

            if self.check_multi_layer_overlap(rotated_vertices, "polygon", layer_start, layer_end, distance):
                continue

            # ✅ 旋转矩形属于复杂几何：存 geom_json corners
            return self._save_rectangle(
                name=full_name,
                is_multi_layer=is_multi_layer,
                layer_start=layer_start,
                layer_end=layer_end,
                center_x=center_x,
                center_y=center_y,
                width=int(width),
                height=int(height),
                vertices=rotated_vertices,
                angle=selected_angle,
            )

        print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置旋转矩形建筑区")
        return []

    def _save_rectangle(
        self,
        *,
        name: str,
        is_multi_layer: bool,
        layer_start: int,
        layer_end: int,
        center_x: float,
        center_y: float,
        width: int,
        height: int,
        vertices: Optional[List[Tuple[float, float]]],
        angle: Optional[float],
    ):
        area_val = int(width) * int(height)

        size_json = {
            "width": int(width),
            "height": int(height),
            "area": int(area_val),
        }
        if angle is not None:
            size_json["angle"] = float(angle)

        # ✅ 轴对齐矩形：geom_type=rectangle，geom_json=None
        # ✅ 旋转矩形：geom_type=polygon，geom_json=corners
        if angle is None:
            geom_type = "rectangle"
            geom_json = None
        else:
            geom_type = "polygon"
            geom_json = vertices

        building_area_id = self.dao.insert_building_area(
            map_id=self.map_id,
            name=name,
            layer_start=int(layer_start),
            layer_end=int(layer_end),
            geom_type=geom_type,
            center_x=float(center_x),
            center_y=float(center_y),
            radius=None,
            geom_json=geom_json,
            size_json=size_json,
        )

        # 写库成功 -> 更新/失效运行期缓存
        if geom_type == "rectangle":
            corners = _rect_corners_from_center(float(center_x), float(center_y), float(width), float(height))
            geom = _safe_polygon(corners)
        else:
            geom = _safe_polygon(vertices)

        if geom is not None:
            self._update_layer_geometry_cache_after_insert(geom, layer_start, layer_end, building_area_id)
        else:
            self._invalidate_layer_runtime_caches(layer_start, layer_end)

        info = {
            "id": int(building_area_id),
            "map_id": int(self.map_id),
            "name": name,
            "layer_start": int(layer_start),
            "layer_end": int(layer_end),
            "geom_type": geom_type,
            "center_x": float(center_x),
            "center_y": float(center_y),
            "radius": None,
            "size_json": size_json,
        }
        if geom_json is not None:
            info["geom_json"] = geom_json

        if is_multi_layer:
            print(f"建筑区 '{name}' 创建成功，层 {layer_start} 到 {layer_end}，尺寸: {width}x{height}，id={building_area_id}")
        else:
            print(f"建筑区 '{name}' 创建成功，层 {layer_start}，尺寸: {width}x{height}，id={building_area_id}")

        return [info]


# -----------------------------
# Regular Polygon Generator
# -----------------------------
class RegularPolygonBuildingAreaGenerator(BuildingAreaGenerator):
    def __init__(self, name: str, map_id: int, layer: int = 1, db_manager=None, num_sides: int = 6):
        super().__init__(name=name, map_id=map_id, layer=layer, db_manager=db_manager)
        self.type = "regular_polygon"
        self.num_sides = int(num_sides)

    def create_building_area(
        self,
        name: Optional[str] = None,
        map_id: Optional[int] = None,
        layer: Optional[Any] = None,
        radius_range=(5, 15),
        num_sides=None,
        max_attempts=10,
        distance=0,
    ):
        self._clear_runtime_caches()
        params_result = self.process_building_params(name=name, map_id=map_id, layer=layer)
        if params_result is None:
            return []

        is_multi_layer, layer_start, layer_end, _, map_width, map_height = params_result
        for L in range(layer_start, layer_end + 1):
            self._build_layer_geometry_index(L)

        if not (isinstance(radius_range, tuple) and len(radius_range) == 2):
            print(f"错误: 无效的半径范围参数 {radius_range}")
            return []

        min_radius, max_radius = int(radius_range[0]), int(radius_range[1])
        short_side = min(map_width, map_height)
        max_possible_radius = max(3, short_side // 4)

        min_radius = max(3, min(min_radius, max_possible_radius))
        max_radius = max(min_radius, min(max_radius, max_possible_radius))

        current_num_sides = int(num_sides) if num_sides is not None else self.num_sides

        if min_radius == max_radius:
            actual_radius = min_radius
        else:
            actual_radius = int(np.random.exponential(scale=(max_radius - min_radius) / 4) + min_radius)
            actual_radius = max(min_radius, min(actual_radius, max_radius))

        full_name = self.generate_building_name(self.name, is_multi_layer, layer_start, layer_end)

        valid_center = None
        vertices = None
        for _ in range(max_attempts):
            min_x = actual_radius
            max_x = map_width - actual_radius
            min_y = actual_radius
            max_y = map_height - actual_radius
            if min_x >= max_x or min_y >= max_y:
                print(f"警告: 半径 {actual_radius} 太大，无法放置正多边形")
                break

            cx = float(np.random.randint(min_x, max_x + 1))
            cy = float(np.random.randint(min_y, max_y + 1))

            vtx = []
            for i in range(current_num_sides):
                ang = (2 * math.pi / current_num_sides) * i
                vtx.append((cx + actual_radius * math.cos(ang), cy + actual_radius * math.sin(ang)))

            if not self.check_multi_layer_overlap(vtx, "polygon", layer_start, layer_end, distance):
                valid_center = (cx, cy)
                vertices = vtx
                break

        if not valid_center:
            print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置正多边形建筑区")
            return []

        perimeter = current_num_sides * 2 * actual_radius * math.sin(math.pi / current_num_sides)
        apothem = actual_radius * math.cos(math.pi / current_num_sides)
        area_val = float(0.5 * perimeter * apothem)

        size_json = {
            "radius": int(actual_radius),
            "area": float(area_val),
            "num_sides": int(current_num_sides),
        }

        building_area_id = self.dao.insert_building_area(
            map_id=self.map_id,
            name=full_name,
            layer_start=int(layer_start),
            layer_end=int(layer_end),
            geom_type="polygon",
            center_x=float(valid_center[0]),
            center_y=float(valid_center[1]),
            radius=None,
            geom_json=vertices,   # ✅ 多边形 corners 属于复杂结构
            size_json=size_json,
        )

        geom = _safe_polygon(vertices)
        if geom is not None:
            self._update_layer_geometry_cache_after_insert(geom, layer_start, layer_end, building_area_id)
        else:
            self._invalidate_layer_runtime_caches(layer_start, layer_end)

        print(f"正多边形建筑区 '{full_name}' 创建成功，中心: {valid_center}，边数: {current_num_sides}，半径: {actual_radius}，id={building_area_id}")

        return [{
            "id": int(building_area_id),
            "map_id": int(self.map_id),
            "name": full_name,
            "layer_start": int(layer_start),
            "layer_end": int(layer_end),
            "geom_type": "polygon",
            "center_x": float(valid_center[0]),
            "center_y": float(valid_center[1]),
            "geom_json": vertices,
            "size_json": size_json,
        }]


class HexagonBuildingAreaGenerator(RegularPolygonBuildingAreaGenerator):
    def __init__(self, name: str, map_id: int, layer: int = 1, db_manager=None):
        super().__init__(name=name, map_id=map_id, layer=layer, db_manager=db_manager, num_sides=6)
        self.type = "hexagon"


# -----------------------------
# Circle Generator
# -----------------------------
class CircleBuildingAreaGenerator(BuildingAreaGenerator):
    def __init__(self, name: str, map_id: int, layer: int = 1, db_manager=None):
        super().__init__(name=name, map_id=map_id, layer=layer, db_manager=db_manager)
        self.type = "circle"

    def create_building_area(
        self,
        name: Optional[str] = None,
        map_id: Optional[int] = None,
        layer: Optional[Any] = None,
        radius_range=(5, 15),
        max_attempts=10,
        distance=0,
    ):
        self._clear_runtime_caches()
        params_result = self.process_building_params(name=name, map_id=map_id, layer=layer)
        if params_result is None:
            return []

        is_multi_layer, layer_start, layer_end, _, map_width, map_height = params_result
        for L in range(layer_start, layer_end + 1):
            self._build_layer_geometry_index(L)

        if not (isinstance(radius_range, tuple) and len(radius_range) == 2):
            print(f"错误: 无效的半径范围参数 {radius_range}")
            return []

        min_radius, max_radius = int(radius_range[0]), int(radius_range[1])
        short_side = min(map_width, map_height)
        max_possible_radius = max(3, short_side // 4)

        min_radius = max(3, min(min_radius, max_possible_radius))
        max_radius = max(min_radius, min(max_radius, max_possible_radius))

        if min_radius == max_radius:
            actual_radius = min_radius
        else:
            actual_radius = int(np.random.exponential(scale=(max_radius - min_radius) / 4) + min_radius)
            actual_radius = max(min_radius, min(actual_radius, max_radius))

        full_name = self.generate_building_name(self.name, is_multi_layer, layer_start, layer_end)

        valid_center = None
        for _ in range(max_attempts):
            min_x = actual_radius
            max_x = map_width - actual_radius
            min_y = actual_radius
            max_y = map_height - actual_radius
            if min_x >= max_x or min_y >= max_y:
                print(f"警告: 半径 {actual_radius} 太大，无法在地图内放置圆塔")
                break

            cx = int(np.random.randint(min_x, max_x + 1))
            cy = int(np.random.randint(min_y, max_y + 1))
            center = (cx, cy)

            if not self.check_multi_layer_overlap((center, actual_radius), "circle", layer_start, layer_end, distance):
                valid_center = center
                break

        if valid_center is None:
            print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置圆塔")
            return []

        circle_area = float(np.pi * actual_radius * actual_radius)
        size_json = {"area": circle_area}

        building_area_id = self.dao.insert_building_area(
            map_id=self.map_id,
            name=full_name,
            layer_start=int(layer_start),
            layer_end=int(layer_end),
            geom_type="circle",
            center_x=float(valid_center[0]),
            center_y=float(valid_center[1]),
            radius=float(actual_radius),
            geom_json=None,
            size_json=size_json,
        )

        geom = Point((float(valid_center[0]), float(valid_center[1]))).buffer(float(actual_radius))
        self._update_layer_geometry_cache_after_insert(geom, layer_start, layer_end, building_area_id)

        print(f"圆塔建筑区 '{full_name}' 创建成功，圆心: {valid_center}，半径: {actual_radius}，id={building_area_id}")

        return [{
            "id": int(building_area_id),
            "map_id": int(self.map_id),
            "name": full_name,
            "layer_start": int(layer_start),
            "layer_end": int(layer_end),
            "geom_type": "circle",
            "center_x": float(valid_center[0]),
            "center_y": float(valid_center[1]),
            "radius": float(actual_radius),
            "size_json": size_json,
        }]
