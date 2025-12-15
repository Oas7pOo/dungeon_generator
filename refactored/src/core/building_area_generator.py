from abc import ABC, abstractmethod
import math
import random
import numpy as np
import json

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

try:
    from shapely.strtree import STRtree
except Exception:
    STRtree = None

from ..db.database import DatabaseManager


class BuildingAreaGenerator(ABC):
    """
    建筑区生成器基类
    """

    def __init__(self, name, map_name="地牢", layer=1, db_manager=None):
        """
        初始化建筑区生成器

        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 层索引
            db_manager: 数据库管理器实例
        """
        self.name = name
        self.map_name = map_name
        self.layer = layer
        self.db_manager = db_manager or DatabaseManager()

        # 运行期缓存（仅用于加速，不影响对外接口）
        self._layer_geom_cache = {}   # layer -> {"geoms":[...], "tree":STRtree|None, "id2name":{id(geom):name}}
        self._free_area_cache = {}    # (layer, w, h, distance) -> shapely geom (largest free polygon)

    def _clear_runtime_caches(self):
        self._layer_geom_cache.clear()
        self._free_area_cache.clear()

    def _invalidate_layer_runtime_caches(self, min_layer, max_layer):
        """内部：使指定层范围内的运行期缓存失效（几何索引 + 最大空闲区）。"""
        try:
            a = int(min_layer)
            b = int(max_layer)
        except Exception:
            self._clear_runtime_caches()
            return

        if a > b:
            a, b = b, a

        # 几何索引缓存（STRtree）
        for L in range(a, b + 1):
            self._layer_geom_cache.pop(L, None)

        # 最大空闲区缓存
        if self._free_area_cache:
            for k in list(self._free_area_cache.keys()):
                try:
                    L = int(k[0])
                except Exception:
                    continue
                if a <= L <= b:
                    self._free_area_cache.pop(k, None)

    def _update_layer_geometry_cache_after_insert(self, geom, name, min_layer, max_layer):
        """
        内部：在成功写库后，把新几何体追加到已构建的层索引里，避免批量放置时仍使用旧 STRtree。
        同时失效对应层的最大空闲区缓存（障碍物已变化）。
        """
        try:
            a = int(min_layer)
            b = int(max_layer)
        except Exception:
            self._clear_runtime_caches()
            return

        if a > b:
            a, b = b, a

        # 仅更新“已构建”的层缓存；未构建的层，后续会按需从 DB 构建
        for L in range(a, b + 1):
            idx = self._layer_geom_cache.get(L)
            if idx is None:
                continue

            idx["geoms"].append(geom)
            idx["id2name"][id(geom)] = name

            if STRtree is not None:
                try:
                    idx["tree"] = STRtree(idx["geoms"])
                except Exception:
                    idx["tree"] = None

        # 空闲区缓存必须失效（障碍物变了）
        if self._free_area_cache:
            for k in list(self._free_area_cache.keys()):
                try:
                    L = int(k[0])
                except Exception:
                    continue
                if a <= L <= b:
                    self._free_area_cache.pop(k, None)


    def get_map_size(self):
        """
        获取当前地图的尺寸

        Returns:
            (width, height) 或 None
        """
        return self.db_manager.get_map_size(self.map_name)

    def get_building_areas_by_layer(self, layer=None):
        """
        获取同一层的所有建筑区

        Args:
            layer: 层索引，默认为当前层

        Returns:
            建筑区列表
        """
        # 兼容“只存 min_layer/max_layer 一条记录”的数据结构
        return self._get_same_layer_buildings(layer if layer is not None else self.layer)

    def _safe_polygon(self, vertices):
        """内部：把顶点列表安全转换为Polygon，必要时修复无效多边形。"""
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

    def _randint_inclusive(self, a, b):
        """内部：安全的 randint(a, b)（含端点）。若区间非法，返回 None。"""
        a = int(math.ceil(a))
        b = int(math.floor(b))
        if a > b:
            return None
        return int(np.random.randint(a, b + 1))

    def _build_layer_geometry_index(self, layer_index):
        """内部：为指定层构建几何索引缓存，提升重叠检测性能。"""
        if layer_index in self._layer_geom_cache:
            return self._layer_geom_cache[layer_index]

        areas = self._get_same_layer_buildings(layer_index)
        geoms = []
        id2name = {}

        for area in areas:
            t = area.get("type")
            name = area.get("name")

            if t == "circle":
                center = area.get("position")
                radius = area.get("radius", None)
                if center is None or radius is None:
                    continue
                try:
                    geom = Point(center).buffer(float(radius))
                except Exception:
                    continue
            else:
                geom = self._safe_polygon(area.get("corner"))
                if geom is None:
                    continue

            geoms.append(geom)
            id2name[id(geom)] = name

        tree = None
        if STRtree is not None and geoms:
            try:
                tree = STRtree(geoms)
            except Exception:
                tree = None

        self._layer_geom_cache[layer_index] = {"geoms": geoms, "tree": tree, "id2name": id2name}
        return self._layer_geom_cache[layer_index]

    def check_shape_overlap(self, shape, shape_type="polygon", layer=None, distance=0):
        """
        检查任意形状是否与现有建筑区重叠

        Args:
            shape: 对于圆形是(圆心,半径)元组，对于多边形是顶点列表
            shape_type: "circle"或"polygon"
            layer: 检查的层索引
            distance: 建筑区之间的最小距离，形状会膨胀此距离后再检查重叠

        Returns:
            重叠返回True，否则返回False
        """
        layer_index = layer if layer is not None else self.layer

        # 将输入转换为Shapely对象
        if shape_type == "circle":
            center, radius = shape
            try:
                base_shape = Point(center).buffer(float(radius))
            except Exception:
                return False
        else:
            base_shape = self._safe_polygon(shape)
            if base_shape is None:
                return False

        # 修复：distance 只在“待检测形状”侧生效，避免双方都 buffer 导致 2 倍距离
        check_shape = base_shape.buffer(distance) if distance and distance > 0 else base_shape

        idx = self._build_layer_geometry_index(layer_index)
        geoms = idx["geoms"]
        tree = idx["tree"]
        id2name = idx["id2name"]

        if not geoms:
            return False

        candidates = geoms
        if tree is not None:
            try:
                candidates = tree.query(check_shape)
            except Exception:
                candidates = geoms

        # 0距离允许贴边：只在交集面积 > 0 时判定为重叠
        eps = 1e-9
        for other in candidates:
            # 尽量跳过“自己同名”的记录（若数据库里已有同名，避免误判）
            other_name = id2name.get(id(other))
            if other_name == self.name :
                continue

            try:
                if not check_shape.intersects(other):
                    continue
                inter = check_shape.intersection(other)
                if getattr(inter, "area", 0.0) > eps:
                    return True
            except Exception:
                return True

        return False

    def check_multi_layer_overlap(self, shape, shape_type, min_layer, max_layer, distance=0):
        """
        在多个层上检查形状是否重叠

        Args:
            shape: 形状数据
            shape_type: 形状类型
            min_layer: 最小层索引
            max_layer: 最大层索引
            distance: 建筑区之间的最小距离

        Returns:
            重叠返回True，否则返回False
        """
        for layer in range(min_layer, max_layer + 1):
            if self.check_shape_overlap(shape, shape_type, layer, distance):
                return True
        return False

    def process_building_params(self, name=None, map_name=None, layer=None):
        """
        处理建筑区的基本参数

        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 单个层或层范围的元组(min_layer, max_layer)

        Returns:
            (is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height)
            若出错则返回None
        """
        # 更新建筑区名和地图名
        if name is not None:
            self.name = name

        if map_name is not None:
            self.map_name = map_name

        if self.map_name is None:
            print("错误: 未指定地图名称")
            return None

        # 处理层参数
        is_multi_layer = False
        min_layer = max_layer = 0

        # 确定要处理的层参数
        layer_to_process = layer if layer is not None else self.layer

        if isinstance(layer_to_process, tuple) and len(layer_to_process) == 2:
            # 处理层范围 - 表示一个跨层建筑
            min_layer, max_layer = layer_to_process
            is_multi_layer = min_layer != max_layer
            self.layer = min_layer  # 使用最小层作为主层
            layer_list = list(range(min_layer, max_layer + 1))
        elif isinstance(layer_to_process, int):
            # 单个层
            layer_list = [layer_to_process]
            min_layer = max_layer = layer_to_process
            self.layer = layer_to_process
        else:
            print(f"错误: 无效的层参数 {layer_to_process}")
            return None

        # 获取地图尺寸
        map_size = self.get_map_size()
        if not map_size:
            print(f"错误: 无法获取地图 '{self.map_name}' 的尺寸")
            return None

        map_width, map_height = map_size

        return is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height

    def generate_building_name(self, name, is_multi_layer, min_layer, max_layer):
        """
        根据建筑区名和层级信息生成完整的建筑区名称

        Args:
            name: 基础建筑区名称
            is_multi_layer: 是否是跨层建筑
            min_layer: 最小层
            max_layer: 最大层

        Returns:
            完整的建筑区名称
        """
        if is_multi_layer:
            return f"{name}_层{min_layer}至{max_layer}"
        else:
            return f"{name}_层{min_layer}"

    def add_multi_layer_info(self, data_dict, is_multi_layer, min_layer, max_layer):
        """
        为数据字典添加跨层建筑的层级信息

        Args:
            data_dict: 要添加信息的字典
            is_multi_layer: 是否是跨层建筑
            min_layer: 最小层
            max_layer: 最大层

        Returns:
            更新后的字典
        """
        if is_multi_layer:
            data_dict["min_layer"] = min_layer
            data_dict["max_layer"] = max_layer
            data_dict["is_multi_layer"] = True

        return data_dict

    def generate_room_size(self, min_size, max_size, dist="exponential", regular_rect=True):
        """
        生成矩形尺寸，根据指定分布类型和规整程度

        参数说明：
        - min_size: 最小尺寸，格式为(width, height)
        - max_size: 最大尺寸，格式为(width, height)
        - dist: 分布类型，
               - "uniform": 均匀分布，宽高在范围内等概率生成
               - "exponential": 指数分布，更倾向于生成较小尺寸（默认）
        - regular_rect: 是否生成规整矩形，
                      - True: 1/20概率生成正方形，其余情况生成接近1:1的矩形
                      - False: 生成普通矩形，长宽比随机

        生成流程：
        1. 验证和调整尺寸范围，确保最小宽度和高度至少为5
        2. 1/20概率生成正方形（仅当regular_rect为True时）
        3. 生成基础尺寸，考虑分布类型
        4. 调整长宽比，使其更接近1:1（仅当regular_rect为True时）
        5. 返回最终的宽高

        返回值：
        - (width, height) 元组，表示生成的矩形尺寸
        """
        min_width, min_height = min_size
        max_width, max_height = max_size

        # 确保最小宽度和高度至少为5
        min_width = max(5, min_width)
        min_height = max(5, min_height)
        max_width = max(min_width, max_width)
        max_height = max(min_height, max_height)

        # 1/20概率生成正方形房间
        if regular_rect and random.random() < 1 / 20:
            min_side = max(min_width, min_height)
            max_side = min(max_width, max_height)

            if min_side <= max_side:
                if dist == 'uniform':
                    side = np.random.randint(min_side, max_side + 1)
                else:  # exponential
                    side = int(np.random.exponential(scale=(max_side - min_side) / 3) + min_side)
                    side = max(min_side, min(side, max_side))
                return side, side

        # 生成基础尺寸
        if dist == 'uniform':
            width = np.random.randint(min_width, max_width + 1)
            height = np.random.randint(min_height, max_height + 1)
        elif dist == 'exponential':
            width = int(np.random.exponential(scale=(max_width - min_width) / 3) + min_width)
            height = int(np.random.exponential(scale=(max_height - min_height) / 3) + min_height)
            # 确保生成的尺寸在范围内
            width = max(min_width, min(width, max_width))
            height = max(min_height, min(height, max_height))
        else:
            raise ValueError(f"不支持的分布类型: {dist}")

        # 如果需要生成规整矩形，调整长宽比使其更接近1:1
        if regular_rect:
            aspect_ratio = width / height if height != 0 else 1.0

            # 如果长宽比偏离1:1较大，进行调整
            if aspect_ratio > 2.0 or aspect_ratio < 0.5:
                target_ratio = 1.0

                if dist == 'uniform':
                    if random.random() < 0.5:
                        target_height = int(width / target_ratio)
                        target_height = max(min_height, min(target_height, max_height))
                        height = target_height
                    else:
                        target_width = int(height * target_ratio)
                        target_width = max(min_width, min(target_width, max_width))
                        width = target_width
                else:  # exponential
                    scale_factor = np.random.normal(1.0, 0.3)
                    scale_factor = max(0.5, min(scale_factor, 2.0))

                    if width > height:
                        new_width = int(width / scale_factor)
                        new_height = int(height * scale_factor)
                        width = max(min_width, min(new_width, max_width))
                        height = max(min_height, min(new_height, max_height))
                    else:
                        new_width = int(width * scale_factor)
                        new_height = int(height / scale_factor)
                        width = max(min_width, min(new_width, max_width))
                        height = max(min_height, min(new_height, max_height))

        return width, height

    def _get_same_layer_buildings(self, layer_index):
        """
        获取同层的所有建筑区

        Args:
            layer_index: 层索引

        Returns:
            同层建筑区列表
        """
        # 只存 min_layer/max_layer：用区间覆盖判断同层
        building_areas = self.db_manager.fetch_all(
            "SELECT name, map_name, min_layer, max_layer, position, type, corner, size "
            "FROM building_areas WHERE map_name = ? AND min_layer <= ? AND max_layer >= ?",
            (self.map_name, layer_index, layer_index)
        )

        result = []
        for area in building_areas:
            try:
                name, map_name, min_layer, max_layer, position_str, area_type, corner_str, size_str = area

                # 解析位置
                if isinstance(position_str, str):
                    position = json.loads(position_str)
                else:
                    position = position_str

                if area_type == "circle":
                    # 圆形建筑区，corner字段存储半径
                    if isinstance(corner_str, str):
                        try:
                            radius = float(corner_str)
                        except ValueError:
                            corner_data = json.loads(corner_str)
                            radius = float(corner_data) if isinstance(corner_data, (int, float)) else 5.0
                    else:
                        radius = float(corner_str)

                    result.append({
                        "name": name,
                        "map_name": map_name,
                        "layer": layer_index,
                        "position": position,
                        "type": area_type,
                        "radius": radius,
                        "corner": radius,
                        "size": size_str,
                        "min_layer": min_layer,
                        "max_layer": max_layer
                    })
                else:
                    # 矩形或多边形建筑区，corner字段存储顶点
                    if isinstance(corner_str, str):
                        corners = json.loads(corner_str)
                    else:
                        corners = corner_str

                    result.append({
                        "name": name,
                        "map_name": map_name,
                        "layer": layer_index,
                        "position": position,
                        "type": area_type,
                        "corner": corners,
                        "size": size_str,
                        "min_layer": min_layer,
                        "max_layer": max_layer
                    })
            except Exception as e:
                print(f"解析建筑区数据时出错: {e}")
                import traceback
                traceback.print_exc()
                continue

        return result

    def _largest_free_polygon_for_layer(self, layer_index, map_width, map_height, distance=0):
        """内部：返回指定层的最大空闲区域 polygon（用于放置引导）。"""
        key = (layer_index, map_width, map_height, float(distance))
        if key in self._free_area_cache:
            return self._free_area_cache[key]

        map_polygon = Polygon([(0, 0), (map_width, 0), (map_width, map_height), (0, map_height)])

        building_areas = self.get_building_areas_by_layer(layer_index)
        obstacles = []

        for area in building_areas:
            if area.get("type") == "circle":
                center = area.get("position")
                radius = area.get("radius", None)
                if center is None or radius is None:
                    continue
                try:
                    geom = Point(center).buffer(float(radius))
                except Exception:
                    continue
            else:
                geom = self._safe_polygon(area.get("corner"))
                if geom is None:
                    continue

            if distance and distance > 0:
                try:
                    geom = geom.buffer(distance)
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

        if hasattr(available, 'geoms'):
            for geom in available.geoms:
                if getattr(geom, 'area', 0.0) > max_area:
                    max_area = geom.area
                    max_polygon = geom
        else:
            max_polygon = available

        if max_polygon is None or getattr(max_polygon, 'area', 0.0) <= 0:
            max_polygon = None

        self._free_area_cache[key] = max_polygon
        return max_polygon

    def calculate_free_areas(self, layer_index, map_width, map_height, distance=0):
        """
        计算指定层的空闲区域，用于建筑区放置决策

        算法详细流程：
        1. 创建表示整个地图边界的矩形多边形
        2. 收集指定层上所有已存在的建筑区数据
        3. 将每个建筑区转换为Shapely几何对象，并根据distance参数进行膨胀处理
           - 对于圆形建筑区：创建中心点并按半径+distance生成膨胀圆
           - 对于多边形建筑区：直接按distance参数进行膨胀
        4. 从地图多边形中依次减去所有建筑区的膨胀几何对象，得到剩余可用空间
        5. 处理可用空间结果：
           - 如果结果包含多个离散多边形（如被建筑区分割成多个区域）
           - 找出其中面积最大的多边形作为最优放置区域
           - 提取该多边形的边界坐标作为返回值

        参数说明：
            layer_index: 要计算空闲区域的层索引，必须为整数
            map_width: 地图的总宽度，单位为网格单元
            map_height: 地图的总高度，单位为网格单元
            distance: 建筑区之间的最小间距，默认为0
                      - 正数：建筑区之间保持指定距离
                      - 0：建筑区可以相邻但不重叠

        返回值：
            tuple: 最大空闲区域的边界坐标 (minx, miny, maxx, maxy)
            - minx: 空闲区域左边界x坐标
            - miny: 空闲区域下边界y坐标
            - maxx: 空闲区域右边界x坐标
            - maxy: 空闲区域上边界y坐标
            None: 如果地图上没有可用空闲区域

        应用场景：
            - 在建筑区生成时评估可用空间
            - 用于"largest_first"放置模式的空间评估
            - 帮助决定建筑区的最优放置位置
        """
        max_polygon = self._largest_free_polygon_for_layer(layer_index, map_width, map_height, distance)
        if not max_polygon or max_polygon.area == 0:
            return None
        return max_polygon.bounds

    @abstractmethod
    def create_building_area(self, **kwargs):
        """
        创建建筑区的抽象方法，子类必须实现

        Args:
            **kwargs: 建筑区参数

        Returns:
            成功创建的建筑区列表
        """
        pass

    def close(self):
        """
        关闭数据库连接
        """
        self.db_manager.close()


class RectangleBuildingAreaGenerator(BuildingAreaGenerator):
    """
    矩形建筑区生成器
    """

    def __init__(self, name, map_name="地牢", layer=1, db_manager=None):
        """
        初始化矩形建筑区生成器

        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 层索引
            db_manager: 数据库管理器实例
        """
        super().__init__(name, map_name, layer, db_manager)
        self.type = "rectangle"

    def create_building_area(self, name=None, map_name=None, layer=None, rect_size=[(5, 5), (30, 30)], angle=False, dist="exponential", placement_mode="largest_first", max_attempts=10, distance=0, enable_resize=False, regular_rect=True):
        """
        创建矩形建筑区

        参数说明：
        - name: 建筑区名称，用于标识生成的建筑区
        - map_name: 地图名称，指定建筑区所属的地图
        - layer: 单个层索引或层索引范围元组，如1表示第1层，(1,3)表示1-3层
        - rect_size: 矩形大小范围，格式为[(minwidth, minheight), (maxwidth, maxheight)]，
                    例如[(5, 5), (30, 30)]表示宽高在5-30之间
        - angle: 是否旋转矩形，
                - False: 不旋转，生成普通非旋转矩形（默认）
                - True: 使用默认角度列表[0, 30, 45, 60]
                - 列表: 指定角度范围或具体角度
        - dist: 分布类型，
               - "uniform": 均匀随机分布，宽高在指定范围内等概率生成
               - "exponential": 指数随机分布，更倾向于生成较小尺寸（默认）
        - placement_mode: 放置模式，
                        - "random": 随机选择位置放置
                        - "largest_first": 从大到小生成尺寸，寻找合适位置（默认）
        - max_attempts: 最大尝试次数，超过次数仍未成功则返回空列表
        - distance: 建筑区之间的最小距离，防止建筑区过于靠近
        - enable_resize: 是否允许同比缩小房间尺寸以适应空闲区域，默认False
        - regular_rect: 是否生成规整矩形，
                      - True: 更倾向于生成正方形或接近1:1的矩形（默认）
                      - False: 生成普通矩形，长宽比随机

        生成过程：
        1. 处理建筑区基本参数，确定层级信息和地图尺寸
        2. 根据angle参数决定调用普通矩形还是旋转矩形生成方法
        3. 生成矩形尺寸，考虑分布类型和规整程度
        4. 尝试放置矩形，根据placement_mode选择放置策略
        5. 检查与现有建筑区的重叠情况
        6. 保存成功生成的建筑区到数据库

        返回值：
        - 成功创建的建筑区列表，每个元素包含建筑区的完整信息
        - 失败时返回空列表
        """
        self._clear_runtime_caches()

        params_result = self.process_building_params(name, map_name, layer)
        if params_result is None:
            return []

        is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height = params_result

        for L in range(min_layer, max_layer + 1):
            self._build_layer_geometry_index(L)

        if not angle:
            return self._create_normal_rectangle(rect_size, dist, placement_mode, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height, distance, enable_resize, regular_rect)
        else:
            return self._create_angled_rectangle(rect_size, angle, dist, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height, distance, regular_rect)

    def create_building_areas_global(self,
                                 name=None,
                                 map_name=None,
                                 layer=None,
                                 rect_size=[(5, 5), (30, 30)],
                                 N=30,
                                 dist="exponential",
                                 placement_mode="largest_first",
                                 max_attempts=10,
                                 distance=0,
                                 enable_resize=True,
                                 regular_rect=True,
                                 fill_budget=None):
        """
        全局策略版：一次生成 N 个房间尺寸，按面积从大到小依次放置；
        放不下的进入“缩小队列”，再按大到小逐格缩小后尝试；
        最后再用小房间进行填缝。

        说明：
        - 不改动原 _create_normal_rectangle()，这是一个新增的批量生成入口
        - 返回值依旧是“成功创建的建筑区列表”
        """

        # 复用原有参数处理（不改注释/输出结构）
        params_result = self.process_building_params(name, map_name, layer)
        if params_result is None:
            return []

        is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height = params_result

        self._clear_runtime_caches()

        for L in range(min_layer, max_layer + 1):
            self._build_layer_geometry_index(L)

        # 统一名称前缀（每个房间用 base_name_序号）
        base_name = self.generate_building_name(self.name, is_multi_layer, min_layer, max_layer)

        # 尺寸边界（与 generate_room_size 保持一致：最小至少 5）
        min_size, max_size = rect_size
        min_w = max(5, int(min_size[0]))
        min_h = max(5, int(min_size[1]))
        max_w = max(min_w, int(max_size[0]))
        max_h = max(min_h, int(max_size[1]))

        # 内部：计算跨层共同最大空闲区域的“bounds 交集”（只用 bounds 做引导，轻量）
        def _common_free_bounds():
            eps = 1e-6
            common = None
            for L in range(min_layer, max_layer + 1):
                b = self.calculate_free_areas(L, map_width, map_height, distance)
                if not b:
                    return None
                minx, miny, maxx, maxy = b

                # 关键：避免浮点误差导致 ceil/floor 多缩 1 格
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
            return tuple(common)  # (bx0, by0, bx1, by1)

        # 内部：尝试在给定 bounds 内随机放置一个矩形
        def _try_place(width, height, unique_name, bounds=None):
            if width <= 0 or height <= 0:
                return None
            if width > map_width or height > map_height:
                return None

            # 放置范围
            if bounds is None:
                x0, y0, x1, y1 = 0, 0, map_width, map_height
            else:
                x0, y0, x1, y1 = bounds

            # 注意：x1/y1 是上边界坐标，不是格子数
            # left ∈ [x0, x1 - width]，top ∈ [y0, y1 - height]
            max_left = x1 - width
            max_top = y1 - height
            if max_left < x0 or max_top < y0:
                return None

            # 尝试次数：全局策略下给足一些
            trials = max(10, int(max_attempts) * 3)

            for _ in range(trials):
                left = np.random.randint(x0, max_left + 1)
                top = np.random.randint(y0, max_top + 1)

                vertices = [
                    (left, top),
                    (left + width, top),
                    (left + width, top + height),
                    (left, top + height),
                ]

                if self.check_multi_layer_overlap(vertices, "polygon", min_layer, max_layer, distance):
                    continue

                center_x = left + width / 2
                center_y = top + height / 2
                return self._save_rectangle(unique_name, is_multi_layer, min_layer, max_layer,
                                            center_x, center_y, width, height, vertices)
            return None

        # 内部：放置失败则进入逐格缩小（宽高各 -1，再尝试），直到下一步会触底停止
        def _try_place_with_shrink(width, height, unique_name, bounds=None):
            w, h = int(width), int(height)
            while True:
                placed = _try_place(w, h, unique_name, bounds=bounds)
                if placed:
                    return placed

                if not enable_resize:
                    return None

                # 下一步会低于最小尺寸就停止
                if (w - 1) < min_w or (h - 1) < min_h:
                    return None

                # 按你要求：逐格缩小（不是按比例）
                w -= 1
                h -= 1

        # 生成 N 个尺寸并排序（大到小）
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

        # 先计算一次“最大空闲区域”的共同 bounds（用于第二选择）
        common_bounds = _common_free_bounds()

        # 1) 按面积从大到小依次放置
        for _, w, h, seq in sizes:
            unique_name = f"{base_name}_{seq}"

            placed = _try_place(w, h, unique_name, bounds=None)
            if placed:
                success_areas.extend(placed)
            else:
                shrink_queue.append((w * h, w, h, unique_name))

        # 2) 放不下的进入缩小队列，再按大到小逐步缩小后尝试（优先用空闲区 bounds 引导）
        if shrink_queue:
            shrink_queue.sort(reverse=True, key=lambda x: x[0])
            for _, w, h, unique_name in shrink_queue:
                # 先用 common_bounds 引导（如果有），没有就全图
                bounds_to_use = common_bounds if common_bounds else None
                placed = _try_place_with_shrink(w, h, unique_name, bounds=bounds_to_use)
                if placed:
                    success_areas.extend(placed)

        # 3) 最后用小房间填缝
        # 默认填缝预算：N//2（你也可以传 fill_budget 指定）
        if fill_budget is None:
            fill_budget = max(0, int(N) // 2)

        if fill_budget > 0:
            # 小房间尺寸范围：靠近最小尺寸，略微放宽一点便于适配缝隙
            fill_max_w = min(max_w, min_w + 5)
            fill_max_h = min(max_h, min_h + 5)

            # 再计算一次（因为前面已经塞了很多建筑，空闲区变了）
            common_bounds = _common_free_bounds()

            for k in range(1, fill_budget + 1):
                # 从“小尺寸分布”生成（仍然复用你的 generate_room_size）
                w, h = self.generate_room_size((min_w, min_h), (fill_max_w, fill_max_h),
                                            dist="exponential", regular_rect=regular_rect)
                w, h = int(w), int(h)

                unique_name = f"{base_name}_fill_{k}"

                # 填缝优先在最大空闲区 bounds 中尝试，失败再全图尝试
                placed = _try_place_with_shrink(w, h, unique_name, bounds=common_bounds if common_bounds else None)
                if not placed:
                    placed = _try_place_with_shrink(w, h, unique_name, bounds=None)

                if placed:
                    success_areas.extend(placed)

        return success_areas


    def _create_normal_rectangle(self, rect_size, dist, placement_mode, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height, distance, enable_resize=False, regular_rect=True):
        """
        创建普通非旋转矩形建筑区

        参数说明：
        - rect_size: 矩形尺寸范围，格式为[(min_width, min_height), (max_width, max_height)]
        - dist: 分布类型，"uniform"表示均匀随机，"exponential"表示指数随机
        - placement_mode: 放置模式，"random"表示随机放置，"largest_first"表示从大到小填充
        - max_attempts: 最大尝试次数
        - is_multi_layer: 是否跨层建筑区
        - min_layer: 最小层索引
        - max_layer: 最大层索引
        - map_width: 地图宽度（网格数）
        - map_height: 地图高度（网格数）
        - distance: 建筑区之间的最小距离
        - enable_resize: 是否允许同比缩小房间尺寸以适应空闲区域，默认False
        - regular_rect: 是否生成规整矩形，True表示更倾向于生成正方形或接近1:1的矩形，默认True

        生成流程：
        1. 生成完整的建筑区名称，包含层级信息
        3. 阶段1：从大到小填充 - 如果随机放置失败，尝试生成更大尺寸优先放置
        4. 阶段2：同比缩小 - 如果启用resize，在某个建筑放不下时，等比缩小为当前可以放下的最大尺寸，如果该尺寸大于等于最小尺寸，则放置，否则无法放置。
        5. 保存成功生成的建筑区

        返回值：
        - 成功创建的建筑区列表，每个元素包含建筑区的完整信息
        - 失败时返回空列表
        """
        full_name = self.generate_building_name(self.name, is_multi_layer, min_layer, max_layer)
        min_size, max_size = rect_size

        # 与 generate_room_size 的约束保持一致：最小宽高至少为5
        min_w_eff = max(5, int(min_size[0]))
        min_h_eff = max(5, int(min_size[1]))
        max_w_eff = max(min_w_eff, int(max_size[0]))
        max_h_eff = max(min_h_eff, int(max_size[1]))

        # 阶段1：随机若干次（先挑面积更大的尺寸）
        print("阶段1: 尝试随机放置（优先大尺寸）...")

        sizes_to_try = []
        for _ in range(max_attempts):
            w, h = self.generate_room_size((min_w_eff, min_h_eff), (max_w_eff, max_h_eff), dist, regular_rect=regular_rect)
            sizes_to_try.append((w * h, w, h))

        if placement_mode == "largest_first":
            sizes_to_try.sort(reverse=True, key=lambda x: x[0])

        pos_trials = max(2, max_attempts)
        for _, width, height in sizes_to_try:
            if map_width - width < 0 or map_height - height < 0:
                continue

            for _t in range(pos_trials):
                left = np.random.randint(0, map_width - width + 1)
                top = np.random.randint(0, map_height - height + 1)

                vertices = [(left, top), (left + width, top), (left + width, top + height), (left, top + height)]
                center_x = left + width / 2
                center_y = top + height / 2

                if not self.check_multi_layer_overlap(vertices, "polygon", min_layer, max_layer, distance):
                    print(f"✅ 随机放置成功，尺寸: {width}x{height}")
                    return self._save_rectangle(full_name, is_multi_layer, min_layer, max_layer, center_x, center_y, width, height, vertices)

        # 阶段2：使用 calculate_free_areas 引导，enable_resize 时“逐格缩小”
        print("阶段2: 使用最大空闲区域引导放置...")

        free_polys = []
        for L in range(min_layer, max_layer + 1):
            _ = self.calculate_free_areas(L, map_width, map_height, distance)
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

            # 先生成若干“大尺寸优先”的候选
            sizes_to_try2 = []
            for _ in range(max_attempts):
                w, h = self.generate_room_size((min_w_eff, min_h_eff), (max_w_eff, max_h_eff), dist, regular_rect=regular_rect)
                sizes_to_try2.append((w * h, w, h))
            sizes_to_try2.sort(reverse=True, key=lambda x: x[0])

            for _, w0, h0 in sizes_to_try2:
                width = int(w0)
                height = int(h0)

                # 如果超出 bounds，按你要求“宽高各缩小1格”直到进入 bounds 或触底停止
                while (width > avail_w) or (height > avail_h):
                    width -= 1
                    height -= 1
                    if width < min_w_eff or height < min_h_eff:
                        width = None
                        break
                if width is None:
                    continue

                # 在此尺寸下多试几次，失败则继续逐格缩小再试（只在 enable_resize=True 才允许继续缩小）
                while width >= min_w_eff and height >= min_h_eff:
                    placed = False

                    for _try in range(max_attempts * 4):
                        left = self._randint_inclusive(bx0, bx1 - width)
                        top = self._randint_inclusive(by0, by1 - height)
                        if left is None or top is None:
                            break

                        vertices = [(left, top), (left + width, top), (left + width, top + height), (left, top + height)]
                        rect_poly = self._safe_polygon(vertices)
                        if rect_poly is None:
                            continue

                        try:
                            if not common_free.covers(rect_poly):
                                continue
                        except Exception:
                            pass

                        center_x = left + width / 2
                        center_y = top + height / 2

                        if not self.check_multi_layer_overlap(vertices, "polygon", min_layer, max_layer, distance):
                            if (width, height) != (w0, h0):
                                print(f"✅ 空闲区引导放置成功（逐格缩小），尺寸: {width}x{height}")
                            else:
                                print(f"✅ 空闲区引导放置成功，尺寸: {width}x{height}")
                            return self._save_rectangle(full_name, is_multi_layer, min_layer, max_layer, center_x, center_y, width, height, vertices)

                    # 本尺寸失败：若不允许缩小，就停止；允许缩小则下一步逐格缩小
                    if not enable_resize:
                        break

                    if (width - 1) < min_w_eff or (height - 1) < min_h_eff:
                        break

                    width -= 1
                    height -= 1

        # 阶段3：小地图(<=100x100) 才暴力扫描（enable_resize）
        if enable_resize and map_width <= 100 and map_height <= 100:
            print("阶段3: 小地图暴力扫描 + 逐格缩小（≤100x100）...")

            base_w, base_h = self.generate_room_size((min_w_eff, min_h_eff), (max_w_eff, max_h_eff), dist, regular_rect=regular_rect)
            width = int(base_w)
            height = int(base_h)

            # 若超出地图，按你要求“宽高各缩小1格”直到进入地图或触底停止
            while width > map_width or height > map_height:
                width -= 1
                height -= 1
                if width < min_w_eff or height < min_h_eff:
                    width = None
                    break

            if width is not None:
                while width >= min_w_eff and height >= min_h_eff:
                    # 暴力扫描
                    for left in range(0, map_width - width + 1):
                        for top in range(0, map_height - height + 1):
                            vertices = [(left, top), (left + width, top), (left + width, top + height), (left, top + height)]
                            center_x = left + width / 2
                            center_y = top + height / 2

                            if not self.check_multi_layer_overlap(vertices, "polygon", min_layer, max_layer, distance):
                                print(f"✅ 暴力扫描成功，逐格缩小后尺寸: {width}x{height}")
                                return self._save_rectangle(full_name, is_multi_layer, min_layer, max_layer, center_x, center_y, width, height, vertices)

                    # 若下一步会触底，停止
                    if (width - 1) < min_w_eff or (height - 1) < min_h_eff:
                        break

                    width -= 1
                    height -= 1

        print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置矩形建筑区")
        return []

    def _create_angled_rectangle(self, rect_size, angle, dist, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height, distance, regular_rect=True):
        """
        创建带角度的矩形建筑区
        """
        # 生成建筑区名
        full_name = self.generate_building_name(self.name, is_multi_layer, min_layer, max_layer)

        # 处理角度参数
        if not isinstance(angle, list) and angle is True:
            angle_list = [0, 30, 45, 60]  # 默认角度
        elif isinstance(angle, list):
            angle_list = []
            for angle_item in angle:
                if isinstance(angle_item, tuple) and len(angle_item) == 2:
                    # 角度范围，例如 (-30, 30)
                    min_angle, max_angle = angle_item
                    angle_list.extend(list(range(min_angle, max_angle + 1, 5)))  # 每5度一个角度
                else:
                    angle_list.append(angle_item)
        else:
            angle_list = [0]  # 默认不旋转

        # 尝试放置建筑区
        for _ in range(max_attempts):
            # 随机选择一个角度
            selected_angle = random.choice(angle_list)

            # 生成矩形尺寸
            min_size, max_size = rect_size
            width, height = self.generate_room_size(min_size, max_size, dist, regular_rect=regular_rect)

            # 生成矩形中心位置
            diagonal_length = math.sqrt(width**2 + height**2)
            safe_margin = diagonal_length / 2  # 对角线的一半

            # 确保安全边距不会导致无法放置
            if safe_margin * 2 >= map_width or safe_margin * 2 >= map_height:
                safe_margin = min(map_width, map_height) / 4

            # 计算有效的放置范围
            valid_left = max(int(safe_margin), 0)
            valid_right = max(int(map_width - safe_margin), valid_left + 1)
            valid_top = max(int(safe_margin), 0)
            valid_bottom = max(int(map_height - safe_margin), valid_top + 1)

            # 随机生成中心点
            center_x = np.random.randint(valid_left, valid_right)
            center_y = np.random.randint(valid_top, valid_bottom)

            # 生成未旋转时的矩形顶点（以中心为基准）
            half_width = width / 2
            half_height = height / 2

            # 矩形四个顶点，顺时针方向
            vertices = [
                (center_x - half_width, center_y - half_height),  # 左上
                (center_x + half_width, center_y - half_height),  # 右上
                (center_x + half_width, center_y + half_height),  # 右下
                (center_x - half_width, center_y + half_height),  # 左下
            ]

            # 对矩形进行旋转（围绕中心点）
            rotated_vertices = []
            angle_rad = np.radians(selected_angle)

            for x, y in vertices:
                # 平移到原点
                x_shifted = x - center_x
                y_shifted = y - center_y

                # 旋转
                x_rotated = x_shifted * np.cos(angle_rad) - y_shifted * np.sin(angle_rad)
                y_rotated = x_shifted * np.sin(angle_rad) + y_shifted * np.cos(angle_rad)

                # 平移回去
                x_final = x_rotated + center_x
                y_final = y_rotated + center_y

                rotated_vertices.append((x_final, y_final))

            # 检查是否在地图范围内
            is_out_of_bounds = any(x < 0 or x > map_width or y < 0 or y > map_height for x, y in rotated_vertices)
            if is_out_of_bounds:
                continue  # 超出边界，重新尝试

            # 检查是否与其他建筑区重叠
            is_overlap = self.check_multi_layer_overlap(rotated_vertices, "polygon", min_layer, max_layer, distance)
            if is_overlap:
                continue  # 有重叠，重新尝试

            # 保存建筑区
            return self._save_rectangle(full_name, is_multi_layer, min_layer, max_layer, center_x, center_y, width, height, rotated_vertices, selected_angle)

        print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置旋转矩形建筑区")
        return []

    def _save_rectangle(self, name, is_multi_layer, min_layer, max_layer, center_x, center_y, width, height, vertices, angle=None):
        """
        保存矩形建筑区到数据库并返回建筑区信息
        """
        # 计算面积
        area = width * height

        # 准备保存数据
        size_data = {
            "width": width,
            "height": height,
            "area": area,
            "center": (center_x, center_y)
        }

        if angle is not None:
            size_data["angle"] = angle

        # 添加跨层信息
        size_data = self.add_multi_layer_info(size_data, is_multi_layer, min_layer, max_layer)

        success_areas = []

        # 保存到数据库
        save_success = self.db_manager.save_building_area(
            name=name,
            map_name=self.map_name,
            min_layer=min_layer,  # 使用最小层
            max_layer=max_layer,  # 使用最大层
            position=(center_x, center_y),  # 位置
            type="rectangle" if angle is None else "polygon",
            corner=vertices,  # 顶点
            size_data=size_data  # 大小数据
        )

        if save_success:
            # ✅关键修复：批量放置时，写库成功后必须立刻更新/失效运行期缓存
            # 否则后续 check_shape_overlap 会继续用旧 STRtree，看不到刚放进去的房间，导致重叠
            geom = self._safe_polygon(vertices)
            if geom is not None:
                self._update_layer_geometry_cache_after_insert(geom, name, min_layer, max_layer)
            else:
                # 极端情况无法构造几何体：至少把缓存失效，强制下次从 DB 重建
                self._invalidate_layer_runtime_caches(min_layer, max_layer)

            if is_multi_layer:
                print(f"矩形建筑区 '{name}' 创建成功，层 {min_layer} 到 {max_layer}，大小: {width}x{height}")
            else:
                print(f"矩形建筑区 '{name}' 创建成功，层 {min_layer}，大小: {width}x{height}")

            area_info = {
                "name": name,
                "position": (center_x, center_y),
                "width": width,
                "height": height,
                "corners": vertices,
                "min_layer": min_layer,
                "max_layer": max_layer
            }

            # 添加层级信息
            area_info = self.add_multi_layer_info(area_info, is_multi_layer, min_layer, max_layer)
            success_areas.append(area_info)

        return success_areas  # 返回所有成功创建的建筑区



class RegularPolygonBuildingAreaGenerator(BuildingAreaGenerator):
    """
    正多边形塔建筑区生成器
    """

    def __init__(self, name, map_name="地牢", layer=1, db_manager=None, num_sides=6):
        """
        初始化正多边形建筑区生成器

        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 层索引
            db_manager: 数据库管理器实例
            num_sides: 正多边形的边数，默认为6（正六边形）
        """
        super().__init__(name, map_name, layer, db_manager)
        self.type = "regular_polygon"
        self.num_sides = num_sides

    def create_building_area(self, name=None, map_name=None, layer=None, radius_range=(5, 15), num_sides=None, max_attempts=10, distance=0):
        """
        创建正多边形建筑区

        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 单个层索引或层索引范围元组
            radius_range: (最小半径, 最大半径) 的元组，这里半径指从中心到顶点的距离
            num_sides: 正多边形的边数，默认使用初始化时设置的边数
            max_attempts: 最大尝试次数
            distance: 建筑区之间的最小距离

        Returns:
            成功创建的建筑区列表
        """
        self._clear_runtime_caches()

        params_result = self.process_building_params(name, map_name, layer)
        if params_result is None:
            return []

        is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height = params_result

        for L in range(min_layer, max_layer + 1):
            self._build_layer_geometry_index(L)

        # 处理半径范围
        if isinstance(radius_range, tuple) and len(radius_range) == 2:
            min_radius, max_radius = radius_range
        else:
            print(f"错误: 无效的半径范围参数 {radius_range}")
            return []

        # 计算地图短边的1/4作为最大可能半径
        short_side = min(map_width, map_height)
        max_possible_radius = short_side // 4

        # 限制半径在合理范围内
        min_radius = max(3, min(min_radius, max_possible_radius))
        max_radius = max(min_radius, min(max_radius, max_possible_radius))

        success_areas = []

        current_num_sides = num_sides if num_sides is not None else self.num_sides

        # 确定半径
        if min_radius == max_radius:
            actual_radius = min_radius
        else:
            actual_radius = int(np.random.exponential(scale=(max_radius - min_radius) / 4) + min_radius)
            actual_radius = max(min_radius, min(actual_radius, max_radius))

        # 生成建筑区名
        full_name = self.generate_building_name(self.name, is_multi_layer, min_layer, max_layer)

        # 查找可用位置，最多尝试指定次数
        valid_position = None
        vertices = None

        for _ in range(max_attempts):
            min_x = actual_radius
            max_x = map_width - actual_radius
            min_y = actual_radius
            max_y = map_height - actual_radius

            if min_x >= max_x or min_y >= max_y:
                print(f"警告: 半径 {actual_radius} 太大，无法在地图内放置正多边形")
                break

            center_x = np.random.randint(min_x, max_x + 1)
            center_y = np.random.randint(min_y, max_y + 1)
            center = (center_x, center_y)

            vtx = []
            for i in range(current_num_sides):
                ang = (2 * math.pi / current_num_sides) * i
                x = center_x + actual_radius * math.cos(ang)
                y = center_y + actual_radius * math.sin(ang)
                vtx.append((x, y))

            if not self.check_multi_layer_overlap(vtx, "polygon", min_layer, max_layer, distance):
                valid_position = center
                vertices = vtx
                break

        if not valid_position:
            print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置正多边形建筑区")
            return []

        # 计算面积（正多边形面积公式：(1/2)*周长*边心距）
        perimeter = current_num_sides * 2 * actual_radius * math.sin(math.pi / current_num_sides)
        apothem = actual_radius * math.cos(math.pi / current_num_sides)
        area = 0.5 * perimeter * apothem

        # 准备保存数据
        size_data = {
            "radius": actual_radius,
            "area": area,
            "center": valid_position,
            "num_sides": current_num_sides
        }

        # 添加跨层信息
        size_data = self.add_multi_layer_info(size_data, is_multi_layer, min_layer, max_layer)

        save_success = self.db_manager.save_building_area(
            name=full_name,
            map_name=self.map_name,
            min_layer=min_layer,
            max_layer=max_layer,
            position=valid_position,
            type="regular_polygon",
            corner=vertices,
            size_data=size_data
        )

        if save_success:
            print(f"正多边形建筑区 '{full_name}' 创建成功，中心位置: {valid_position}，边数: {self.num_sides}，半径: {actual_radius}，层: {min_layer} 到 {max_layer}")

            area_info = {
                "name": full_name,
                "position": valid_position,
                "radius": actual_radius,
                "num_sides": self.num_sides,
                "min_layer": min_layer,
                "max_layer": max_layer
            }

            area_info = self.add_multi_layer_info(area_info, is_multi_layer, min_layer, max_layer)
            success_areas.append(area_info)

        return success_areas


class HexagonBuildingAreaGenerator(RegularPolygonBuildingAreaGenerator):
    """
    正六边形建筑区生成器
    """

    def __init__(self, name, map_name="地牢", layer=1, db_manager=None):
        """
        初始化正六边形建筑区生成器

        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 层索引
            db_manager: 数据库管理器实例
        """
        super().__init__(name, map_name, layer, db_manager, num_sides=6)
        self.type = "hexagon"


class CircleBuildingAreaGenerator(BuildingAreaGenerator):
    """
    圆塔建筑区生成器
    """

    def __init__(self, name, map_name="地牢", layer=1, db_manager=None):
        """
        初始化圆塔建筑区生成器

        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 层索引
            db_manager: 数据库管理器实例
        """
        super().__init__(name, map_name, layer, db_manager)
        self.type = "circle"

    def create_building_area(self, name=None, map_name=None, layer=None, radius_range=(5, 15), max_attempts=10, distance=0):
        """
        创建圆塔建筑区

        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 单个层索引或层索引范围元组
            radius_range: (最小半径, 最大半径) 的元组
            max_attempts: 最大尝试次数
            distance: 建筑区之间的最小距离

        Returns:
            成功创建的建筑区列表
        """
        self._clear_runtime_caches()

        params_result = self.process_building_params(name, map_name, layer)
        if params_result is None:
            return []

        is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height = params_result

        for L in range(min_layer, max_layer + 1):
            self._build_layer_geometry_index(L)

        # 处理半径范围
        if isinstance(radius_range, tuple) and len(radius_range) == 2:
            min_radius, max_radius = radius_range
        else:
            print(f"错误: 无效的半径范围参数 {radius_range}")
            return []

        # 计算地图短边的1/4作为最大可能半径
        short_side = min(map_width, map_height)
        max_possible_radius = short_side // 4

        # 限制半径在合理范围内
        min_radius = max(3, min(min_radius, max_possible_radius))
        max_radius = max(min_radius, min(max_radius, max_possible_radius))

        success_areas = []

        # 确定半径
        if min_radius == max_radius:
            actual_radius = min_radius
        else:
            actual_radius = int(np.random.exponential(scale=(max_radius - min_radius) / 4) + min_radius)
            actual_radius = max(min_radius, min(actual_radius, max_radius))

        # 生成建筑区名
        full_name = self.generate_building_name(self.name, is_multi_layer, min_layer, max_layer)

        # 查找可用位置，最多尝试指定次数
        valid_position = None
        for _ in range(max_attempts):
            min_x = actual_radius
            max_x = map_width - actual_radius
            min_y = actual_radius
            max_y = map_height - actual_radius

            if min_x >= max_x or min_y >= max_y:
                print(f"警告: 半径 {actual_radius} 太大，无法在地图内放置圆塔")
                break

            center_x = np.random.randint(min_x, max_x + 1)
            center_y = np.random.randint(min_y, max_y + 1)
            center = (int(center_x), int(center_y))

            is_overlap = self.check_multi_layer_overlap((center, actual_radius), "circle", min_layer, max_layer, distance)
            if not is_overlap:
                valid_position = center
                break

        if valid_position is None:
            print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置圆塔")
            return []

        # 计算圆的面积
        circle_area = np.pi * actual_radius * actual_radius

        # 准备保存数据
        size_data = {
            "radius": actual_radius,
            "area": circle_area,
            "center": valid_position
        }

        # 添加跨层信息
        size_data = self.add_multi_layer_info(size_data, is_multi_layer, min_layer, max_layer)

        # 保存到数据库
        save_success = self.db_manager.save_building_area(
            name=full_name,
            map_name=self.map_name,
            min_layer=min_layer,
            max_layer=max_layer,
            position=valid_position,
            type="circle",
            corner=actual_radius,
            size_data=size_data
        )

        if save_success:
            print(f"圆塔建筑区 '{full_name}' 创建成功，圆心位置: {valid_position}，半径: {actual_radius}，层: {min_layer} 到 {max_layer}")

            area_info = {
                "name": full_name,
                "position": valid_position,
                "radius": actual_radius,
                "min_layer": min_layer,
                "max_layer": max_layer
            }

            area_info = self.add_multi_layer_info(area_info, is_multi_layer, min_layer, max_layer)
            success_areas.append(area_info)

        return success_areas
