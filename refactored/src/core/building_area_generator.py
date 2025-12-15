from abc import ABC, abstractmethod
import math
import random
import numpy as np
import json
from shapely.geometry import Point, Polygon
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
        return self.db_manager.get_building_areas_by_layer(self.map_name, layer or self.layer)
    
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
        # 将输入转换为Shapely对象
        if shape_type == "circle":
            center, radius = shape
            check_shape = Point(center).buffer(radius + distance)  # 圆形膨胀
        else:
            check_shape = Polygon(shape).buffer(distance)  # 多边形膨胀
        
        # 获取同层建筑区
        building_areas = self.get_building_areas_by_layer(layer)
        
        # 检查与所有建筑区的重叠
        for area in building_areas:
            if area["name"] == self.name:
                continue
                
            if area["type"] == "circle":
                center = area["position"]
                radius = area["radius"]
                other_shape = Point(center).buffer(radius + distance)  # 现有圆形也膨胀
            else:
                polygon_vertices = area["corner"]
                if not polygon_vertices or not isinstance(polygon_vertices, list) or len(polygon_vertices) < 3:
                    continue
                other_shape = Polygon(polygon_vertices).buffer(distance)  # 现有多边形也膨胀
                
            if check_shape.intersects(other_shape):
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
            layer_list = [min_layer] if is_multi_layer else list(range(min_layer, max_layer + 1))
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
        if regular_rect and random.random() < 1/20:
            # 生成正方形
            # 计算合适的正方形边长范围
            min_side = max(min_width, min_height)
            max_side = min(max_width, max_height)
            
            if min_side > max_side:
                # 如果无法生成正方形，回退到普通矩形生成
                pass
            else:
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
            # 计算当前长宽比
            aspect_ratio = width / height if height != 0 else 1.0
            
            # 如果长宽比偏离1:1较大，进行调整
            if aspect_ratio > 2.0 or aspect_ratio < 0.5:
                # 计算目标尺寸，使长宽比更接近1:1
                target_ratio = 1.0
                
                # 根据分布类型调整目标尺寸
                if dist == 'uniform':
                    # 均匀分布：随机选择以宽度或高度为基准
                    if random.random() < 0.5:
                        # 以宽度为基准，调整高度
                        target_height = int(width / target_ratio)
                        # 确保高度在范围内
                        target_height = max(min_height, min(target_height, max_height))
                        height = target_height
                    else:
                        # 以高度为基准，调整宽度
                        target_width = int(height * target_ratio)
                        # 确保宽度在范围内
                        target_width = max(min_width, min(target_width, max_width))
                        width = target_width
                else:  # exponential
                    # 指数分布：使用高斯分布调整长宽比
                    # 生成接近1的缩放因子
                    scale_factor = np.random.normal(1.0, 0.3)  # 均值1.0，标准差0.3
                    scale_factor = max(0.5, min(scale_factor, 2.0))  # 限制缩放因子范围
                    
                    if width > height:
                        # 宽度较大，缩小宽度或增大高度
                        new_width = int(width / scale_factor)
                        new_height = int(height * scale_factor)
                        # 确保在范围内
                        width = max(min_width, min(new_width, max_width))
                        height = max(min_height, min(new_height, max_height))
                    else:
                        # 高度较大，增大宽度或缩小高度
                        new_width = int(width * scale_factor)
                        new_height = int(height / scale_factor)
                        # 确保在范围内
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
        # 从数据库获取同层建筑区
        building_areas = self.db_manager.fetch_all(
            "SELECT name, map_name, layer, position, type, corner, size FROM building_areas WHERE layer = ? AND map_name = ?",
            (layer_index, self.map_name)
        )
        
        # 转换为便于处理的格式
        result = []
        for area in building_areas:
            name, map_name, layer, position_str, area_type, corner_str, size_str = area
            
            try:
                # 解析位置
                if isinstance(position_str, str):
                    position = json.loads(position_str)
                else:
                    position = position_str
                
                # 解析角点和尺寸
                if area_type == "circle":
                    # 圆形建筑区，corner字段存储半径
                    if isinstance(corner_str, str):
                        try:
                            radius = float(corner_str)
                        except ValueError:
                            # 如果是JSON字符串，尝试解析
                            corner_data = json.loads(corner_str)
                            radius = float(corner_data) if isinstance(corner_data, (int, float)) else 5.0
                    else:
                        radius = float(corner_str)
                    
                    result.append({
                        "name": name,
                        "map_name": map_name,
                        "layer": layer,
                        "position": position,
                        "type": area_type,
                        "radius": radius,
                        "corner": radius,
                        "size": size_str
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
                        "layer": layer,
                        "position": position,
                        "type": area_type,
                        "corner": corners,
                        "size": size_str
                    })
            except Exception as e:
                print(f"解析建筑区数据时出错: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        return result
    
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
        # 步骤1: 创建表示整个地图的多边形
        map_polygon = Polygon([
            (0, 0),
            (map_width, 0),
            (map_width, map_height),
            (0, map_height)
        ])
        
        # 步骤2: 收集指定层上所有现有建筑区
        building_areas = self.get_building_areas_by_layer(layer_index)
        
        # 步骤3: 将每个建筑区转换为Shapely多边形，并考虑建筑之间的最小距离
        all_building_shapes = []
        for area in building_areas:
            if area["name"] == self.name:
                continue  # 跳过当前正在创建的建筑区
                
            if area["type"] == "circle":
                # 圆形建筑区
                center = area["position"]
                radius = area["radius"]
                circle = Point(center).buffer(radius + distance)  # 膨胀距离
                all_building_shapes.append(circle)
            else:
                # 矩形、多边形或正多边形建筑区
                polygon_vertices = area["corner"]
                if isinstance(polygon_vertices, list) and len(polygon_vertices) >= 3:
                    polygon = Polygon(polygon_vertices).buffer(distance)  # 膨胀距离
                    all_building_shapes.append(polygon)
        
        # 步骤4: 从地图多边形中减去所有建筑区多边形，得到可用空间
        available_area = map_polygon
        for building_shape in all_building_shapes:
            available_area = available_area.difference(building_shape)
        
        # 步骤5: 如果可用空间是多个多边形，返回最大的那个
        if hasattr(available_area, 'geoms'):
            # 多个多边形
            max_polygon = None
            max_area = 0
            for geom in available_area.geoms:
                if geom.area > max_area:
                    max_area = geom.area
                    max_polygon = geom
        else:
            # 单个多边形
            max_polygon = available_area
        
        if not max_polygon or max_polygon.area == 0:
            return None
        
        # 返回最大空闲区域的边界坐标
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
        # 处理建筑参数
        params_result = self.process_building_params(name, map_name, layer)
        if params_result is None:
            return []
            
        is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height = params_result
        
        if not angle:
            return self._create_normal_rectangle(rect_size, dist, placement_mode, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height, distance, enable_resize, regular_rect)
        else:
            return self._create_angled_rectangle(rect_size, angle, dist, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height, distance, regular_rect)
    
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
        # 生成建筑区名
        full_name = self.generate_building_name(self.name, is_multi_layer, min_layer, max_layer)

        min_size, max_size = rect_size
        
        # 阶段1: 尝试随机放置
        print(f"阶段1: 尝试随机放置...")
        for attempt in range(max_attempts):
            # 生成矩形尺寸：根据分布类型和规整程度生成宽高
            width, height = self.generate_room_size(min_size, max_size, dist, regular_rect=regular_rect)
            
            # 检查尺寸是否超出地图范围
            if map_width - width < 0 or map_height - height < 0:
                continue
                
            # 随机选择左上角位置
            left = np.random.randint(0, map_width - width + 1)
            top = np.random.randint(0, map_height - height + 1)
            
            # 计算矩形四个顶点坐标（左上、右上、右下、左下，顺时针方向）
            vertices = [
                (left, top),  # 左上顶点
                (left + width, top),  # 右上顶点
                (left + width, top + height),  # 右下顶点
                (left, top + height),  # 左下顶点
            ]
            
            # 计算矩形中心点坐标
            center_x = left + width / 2
            center_y = top + height / 2
            
            # 检查矩形是否与现有建筑区重叠（考虑跨层情况和最小距离）
            is_overlap = self.check_multi_layer_overlap(vertices, "polygon", min_layer, max_layer, distance)
            if not is_overlap:
                # 没有重叠，找到了合适的位置，保存并返回结果
                print(f"✅ 随机放置成功，尺寸: {width}x{height}")
                return self._save_rectangle(full_name, is_multi_layer, min_layer, max_layer, center_x, center_y, width, height, vertices)
        
        # 阶段2: 尝试从大到小填充模式
        if placement_mode == "largest_first":
            print("切换到从大到小填充模式...")
            
            # 生成多个尺寸，按面积从大到小排序
            sizes_to_try = []
            for attempt in range(max_attempts):
                width, height = self.generate_room_size(min_size, max_size, dist, regular_rect=regular_rect)
                # 保存面积、宽度、高度的元组
                sizes_to_try.append((width * height, width, height))
            
            # 按面积从大到小排序，优先尝试较大的尺寸
            sizes_to_try.sort(reverse=True, key=lambda x: x[0])
            
            # 尝试每个尺寸
            for area, width, height in sizes_to_try:
                # 检查尺寸是否超出地图范围
                if map_width - width < 0 or map_height - height < 0:
                    continue
                    
                # 随机选择左上角位置
                left = np.random.randint(0, map_width - width + 1)
                top = np.random.randint(0, map_height - height + 1)
                
                # 计算矩形四个顶点坐标
                vertices = [
                    (left, top),  # 左上
                    (left + width, top),  # 右上
                    (left + width, top + height),  # 右下
                    (left, top + height),  # 左下
                ]
                
                # 计算矩形中心点坐标
                center_x = left + width / 2
                center_y = top + height / 2
                
                # 检查矩形是否与现有建筑区重叠
                is_overlap = self.check_multi_layer_overlap(vertices, "polygon", min_layer, max_layer, distance)
                if not is_overlap:
                    # 没有重叠，找到了合适的位置，保存并返回结果
                    print(f"✅ 从大到小放置成功，尺寸: {width}x{height}")
                    return self._save_rectangle(full_name, is_multi_layer, min_layer, max_layer, center_x, center_y, width, height, vertices)
        
        # 阶段3: 尝试同比缩小尺寸（如果启用resize选项）
        if enable_resize:
            print(f"阶段3: 尝试同比缩小尺寸...")
            
            # 生成基础尺寸
            base_width, base_height = self.generate_room_size(min_size, max_size, dist)
            # 计算基础长宽比，用于后续同比缩小
            aspect_ratio = base_width / base_height if base_height != 0 else 1.0
            
            # 生成20个缩放比例，从1.0到0.1，逐步缩小
            for scale in np.linspace(1.0, 0.1, 20):
                # 计算当前缩放后的宽度
                scaled_width = max(min_size[0], int(base_width * scale))
                # 根据长宽比计算对应的高度，保持比例不变
                scaled_height = max(min_size[1], int(scaled_width / aspect_ratio))
                
                # 确保缩放后的尺寸在指定范围内
                scaled_height = max(min_size[1], min(scaled_height, max_size[1]))
                scaled_width = max(min_size[0], min(int(scaled_height * aspect_ratio), max_size[0]))
                
                # 遍历地图上所有可能的左上角位置，寻找合适的放置点
                for left in range(0, map_width - scaled_width + 1):
                    for top in range(0, map_height - scaled_height + 1):
                        # 计算矩形四个顶点坐标
                        vertices = [
                            (left, top),  # 左上
                            (left + scaled_width, top),  # 右上
                            (left + scaled_width, top + scaled_height),  # 右下
                            (left, top + scaled_height),  # 左下
                        ]
                        
                        # 计算矩形中心点坐标
                        center_x = left + scaled_width / 2
                        center_y = top + scaled_height / 2
                        
                        # 检查矩形是否与现有建筑区重叠
                        is_overlap = self.check_multi_layer_overlap(vertices, "polygon", min_layer, max_layer, distance)
                        if not is_overlap:
                            # 没有重叠，找到了合适的位置，保存并返回结果
                            print(f"✅ 同比缩小成功，原始尺寸: {base_width}x{base_height}，缩放后尺寸: {scaled_width}x{scaled_height}")
                            return self._save_rectangle(full_name, is_multi_layer, min_layer, max_layer, center_x, center_y, scaled_width, scaled_height, vertices)
        
        # 如果所有尝试都失败
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
            # 考虑旋转后的矩形可能超出边界，所以缩小可选范围
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
        
        # 只创建一个建筑区记录，包含min_layer和max_layer
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
                "min_layer": min_layer,  # 添加最小层信息
                "max_layer": max_layer   # 添加最大层信息
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
        # 处理建筑参数
        params_result = self.process_building_params(name, map_name, layer)
        if params_result is None:
            return []
            
        is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height = params_result
        
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
        
        # 创建建筑区
        success_areas = []
        
        # 确定半径
        if min_radius == max_radius:
            actual_radius = min_radius
        else:
            # 使用指数分布生成半径
            actual_radius = int(np.random.exponential(scale=(max_radius - min_radius) / 4) + min_radius)
            actual_radius = max(min_radius, min(actual_radius, max_radius))
        
        # 生成建筑区名
        full_name = self.generate_building_name(self.name, is_multi_layer, min_layer, max_layer)
        
        # 查找可用位置，最多尝试指定次数
        valid_position = None
        for _ in range(max_attempts):  # 默认为10次尝试
            # 根据半径调整可能的中心位置范围(确保不超出边界)
            min_x = actual_radius
            max_x = map_width - actual_radius
            min_y = actual_radius
            max_y = map_height - actual_radius
            
            # 如果范围无效，放弃
            if min_x >= max_x or min_y >= max_y:
                print(f"警告: 半径 {actual_radius} 太大，无法在地图内放置正多边形")
                break
            
            # 随机生成中心位置
            center_x = np.random.randint(min_x, max_x + 1)
            center_y = np.random.randint(min_y, max_y + 1)
            center = (center_x, center_y)
            
            # 使用传入的边数或默认边数
            current_num_sides = num_sides if num_sides is not None else self.num_sides
            
            # 生成正多边形的顶点
            vertices = []
            for i in range(current_num_sides):
                angle = (2 * math.pi / current_num_sides) * i
                x = center_x + actual_radius * math.cos(angle)
                y = center_y + actual_radius * math.sin(angle)
                vertices.append((x, y))
            
            # 检查是否与其他建筑区重叠
            is_overlap = self.check_multi_layer_overlap(vertices, "polygon", min_layer, max_layer, distance)
            if not is_overlap:
                valid_position = center
                break
        
        if not valid_position:
            print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置正多边形建筑区")
            return []
        
        # 计算面积（正多边形面积公式：(1/2)*周长*边心距）
        current_num_sides = num_sides if num_sides is not None else self.num_sides
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
        
        # 只创建一个建筑区记录，包含min_layer和max_layer
        # 保存到数据库
        save_success = self.db_manager.save_building_area(
            name=full_name,  # 统一名称，不添加层后缀
            map_name=self.map_name,
            min_layer=min_layer,
            max_layer=max_layer,
            position=valid_position,  # 位置
            type="regular_polygon",
            corner=vertices,  # 正多边形的顶点
            size_data=size_data  # 大小数据
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
            
            # 添加层级信息
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
        # 处理建筑参数
        params_result = self.process_building_params(name, map_name, layer)
        if params_result is None:
            return []
            
        is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height = params_result
        
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
        
        # 创建建筑区
        success_areas = []
        
        # 确定半径
        if min_radius == max_radius:
            actual_radius = min_radius
        else:
            # 使用指数分布生成半径
            actual_radius = int(np.random.exponential(scale=(max_radius - min_radius) / 4) + min_radius)
            actual_radius = max(min_radius, min(actual_radius, max_radius))
        
        # 生成建筑区名
        full_name = self.generate_building_name(self.name, is_multi_layer, min_layer, max_layer)
        
        # 查找可用位置，最多尝试指定次数
        valid_position = None
        for _ in range(max_attempts):  # 默认为10次尝试
            # 根据半径调整可能的圆心位置范围(确保不超出边界)
            min_x = actual_radius
            max_x = map_width - actual_radius
            min_y = actual_radius
            max_y = map_height - actual_radius
            
            # 如果范围无效，放弃
            if min_x >= max_x or min_y >= max_y:
                print(f"警告: 半径 {actual_radius} 太大，无法在地图内放置圆塔")
                break
            
            # 生成随机圆心位置
            center_x = np.random.randint(min_x, max_x + 1)
            center_y = np.random.randint(min_y, max_y + 1)
            center = (int(center_x), int(center_y))
            
            # 检查是否与圆形建筑区重叠（跨所有层）
            is_overlap = self.check_multi_layer_overlap((center, actual_radius), "circle", min_layer, max_layer, distance)
            
            # 如果没有任何重叠，则位置有效
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
        
        # 只创建一个建筑区记录，包含min_layer和max_layer
        # 保存到数据库
        save_success = self.db_manager.save_building_area(
            name=full_name,  # 统一名称，不添加层后缀
            map_name=self.map_name,
            min_layer=min_layer,
            max_layer=max_layer,
            position=valid_position,  # 位置
            type="circle",
            corner=actual_radius,  # 半径
            size_data=size_data  # 大小数据
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
            
            # 添加层级信息
            area_info = self.add_multi_layer_info(area_info, is_multi_layer, min_layer, max_layer)
            
            success_areas.append(area_info)
        
        return success_areas
