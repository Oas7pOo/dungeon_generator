from abc import ABC, abstractmethod
import math
import random
import numpy as np
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
    
    def check_shape_overlap(self, shape, shape_type="polygon", layer=None):
        """
        检查任意形状是否与现有建筑区重叠
        
        Args:
            shape: 对于圆形是(圆心,半径)元组，对于多边形是顶点列表
            shape_type: "circle"或"polygon"
            layer: 检查的层索引
            
        Returns:
            重叠返回True，否则返回False
        """
        # 将输入转换为Shapely对象
        if shape_type == "circle":
            center, radius = shape
            check_shape = Point(center).buffer(radius)
        else:
            check_shape = Polygon(shape)
        
        # 获取同层建筑区
        building_areas = self.get_building_areas_by_layer(layer)
        
        # 检查与所有建筑区的重叠
        for area in building_areas:
            if area["name"] == self.name:
                continue
                
            if area["type"] == "circle":
                center = area["position"]
                radius = area["radius"]
                other_shape = Point(center).buffer(radius)
            else:
                polygon_vertices = area["corner"]
                if not polygon_vertices or not isinstance(polygon_vertices, list) or len(polygon_vertices) < 3:
                    continue
                other_shape = Polygon(polygon_vertices)
                
            if check_shape.intersects(other_shape):
                return True
        
        return False
    
    def check_multi_layer_overlap(self, shape, shape_type, min_layer, max_layer):
        """
        在多个层上检查形状是否重叠
        
        Args:
            shape: 形状数据
            shape_type: 形状类型
            min_layer: 最小层索引
            max_layer: 最大层索引
            
        Returns:
            重叠返回True，否则返回False
        """
        for layer in range(min_layer, max_layer + 1):
            if self.check_shape_overlap(shape, shape_type, layer):
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
        
        if layer is None:
            layer_list = [self.layer]  # 使用默认层
            min_layer = max_layer = self.layer
        elif isinstance(layer, tuple) and len(layer) == 2:
            # 处理层范围 - 表示一个跨层建筑
            min_layer, max_layer = layer
            is_multi_layer = min_layer != max_layer
            self.layer = min_layer  # 使用最小层作为主层
            layer_list = [min_layer] if is_multi_layer else list(range(min_layer, max_layer + 1))
        elif isinstance(layer, int):
            # 单个层
            layer_list = [layer]
            min_layer = max_layer = layer
            self.layer = layer
        else:
            print(f"错误: 无效的层参数 {layer}")
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
    
    def generate_room_size(self, min_size, max_size, dist="exponential"):
        """
        处理大小范围参数，根据分布生成实际大小
        
        Args:
            min_size: 最小尺寸 (width, height)
            max_size: 最大尺寸 (width, height)
            dist: 分布类型，"uniform"或"exponential"
            
        Returns:
            (width, height)
        """
        min_width, min_height = min_size
        max_width, max_height = max_size
        
        # 确保最小宽度和高度至少为5
        min_width = max(5, min_width)
        min_height = max(5, min_height)
        max_width = max(min_width, max_width)
        max_height = max(min_height, max_height)
        
        if dist == 'uniform':
            return np.random.randint(min_width, max_width + 1), np.random.randint(min_height, max_height + 1)
        elif dist == 'exponential':
            width = int(np.random.exponential(scale=(max_width - min_width) / 3) + min_width)
            height = int(np.random.exponential(scale=(max_height - min_height) / 3) + min_height)
            # 确保生成的尺寸在范围内
            return max(min_width, min(width, max_width)), max(min_height, min(height, max_height))
        else:
            raise ValueError(f"不支持的分布类型: {dist}")
    
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
    
    def create_building_area(self, name=None, map_name=None, layer=None, rect_size=[(5, 5), (30, 30)], angle=False, dist="exponential", max_attempts=10):
        """
        创建矩形建筑区
        
        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 单个层索引或层索引范围元组
            rect_size: 矩形大小范围 [(minwidth, minheight),(maxwidth, maxheight)]
            angle: 是否旋转矩形，False表示不旋转，True表示使用默认角度，列表表示指定角度范围
            dist: 分布类型
            max_attempts: 最大尝试次数
            
        Returns:
            成功创建的建筑区列表
        """
        # 处理建筑参数
        params_result = self.process_building_params(name, map_name, layer)
        if params_result is None:
            return []
            
        is_multi_layer, min_layer, max_layer, layer_list, map_width, map_height = params_result
        
        if not angle:
            return self._create_normal_rectangle(rect_size, dist, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height)
        else:
            return self._create_angled_rectangle(rect_size, angle, dist, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height)
    
    def _create_normal_rectangle(self, rect_size, dist, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height):
        """
        创建普通矩形建筑区
        """
        # 生成建筑区名
        full_name = self.generate_building_name(self.name, is_multi_layer, min_layer, max_layer)

        # 阶段1: 尝试随机放置
        for _ in range(max_attempts):
            # 生成矩形尺寸
            min_size, max_size = rect_size
            width, height = self.generate_room_size(min_size, max_size, dist)
            
            # 随机选择左上角位置
            left = np.random.randint(0, map_width - width + 1)
            top = np.random.randint(0, map_height - height + 1)
            
            # 计算四个顶点
            vertices = [
                (left, top),  # 左上
                (left + width, top),  # 右上
                (left + width, top + height),  # 右下
                (left, top + height),  # 左下
            ]
            
            # 中心点
            center_x = left + width / 2
            center_y = top + height / 2
            
            # 检查是否与其他建筑区重叠
            is_overlap = self.check_multi_layer_overlap(vertices, "polygon", min_layer, max_layer)
            
            if not is_overlap:
                # 没有重叠，找到了合适的位置
                return self._save_rectangle(full_name, is_multi_layer, min_layer, max_layer, center_x, center_y, width, height, vertices)
        
        print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置矩形建筑区")
        return []
    
    def _create_angled_rectangle(self, rect_size, angle, dist, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height):
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
            width, height = self.generate_room_size(min_size, max_size, dist)
            
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
            is_overlap = self.check_multi_layer_overlap(rotated_vertices, "polygon", min_layer, max_layer)
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
        
        # 如果是跨层建筑，在所有层创建相同位置的建筑区
        layers_to_create = range(min_layer, max_layer + 1) if is_multi_layer else [min_layer]
        
        for current_layer in layers_to_create:
            # 为每层生成唯一名称
            layer_name = f"{name}_层{current_layer}" if is_multi_layer else name
            
            # 保存到数据库
            save_success = self.db_manager.save_building_area(
                name=layer_name,
                map_name=self.map_name,
                layer=current_layer,  # 使用当前层
                position=(center_x, center_y),  # 相同的位置
                type="rectangle" if angle is None else "polygon",
                corner=vertices,  # 相同的顶点
                size_data=size_data  # 相同的大小数据
            )
            
            if save_success:
                if is_multi_layer:
                    print(f"矩形建筑区 '{layer_name}' 创建成功，层 {current_layer}，大小: {width}x{height}")
                else:
                    print(f"矩形建筑区 '{layer_name}' 创建成功，大小: {width}x{height}")
                
                area_info = {
                    "name": layer_name,
                    "position": (center_x, center_y),
                    "width": width,
                    "height": height,
                    "corners": vertices,
                    "layer": current_layer  # 添加层信息
                }
                
                # 添加层级信息
                area_info = self.add_multi_layer_info(area_info, is_multi_layer, min_layer, max_layer)
                
                success_areas.append(area_info)
        
        return success_areas  # 返回所有成功创建的建筑区
    
    
    def _create_angled_rectangle(self, rect_size, angle, dist, max_attempts, is_multi_layer, min_layer, max_layer, map_width, map_height):
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
            width, height = self.generate_room_size(min_size, max_size, dist)
            
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
            is_overlap = self.check_multi_layer_overlap(rotated_vertices, "polygon", min_layer, max_layer)
            if is_overlap:
                continue  # 有重叠，重新尝试
            
            # 保存建筑区
            return self._save_rectangle(full_name, is_multi_layer, min_layer, max_layer, center_x, center_y, width, height, rotated_vertices, selected_angle)
        
        print(f"警告: 在{max_attempts}次尝试后，无法找到合适的位置放置旋转矩形建筑区")
        return []

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
    
    def create_building_area(self, name=None, map_name=None, layer=None, radius_range=(5, 15), max_attempts=10):
        """
        创建圆塔建筑区
        
        Args:
            name: 建筑区名称
            map_name: 地图名称
            layer: 单个层索引或层索引范围元组
            radius_range: (最小半径, 最大半径) 的元组
            max_attempts: 最大尝试次数
            
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
            is_overlap = self.check_multi_layer_overlap((center, actual_radius), "circle", min_layer, max_layer)
            
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
        
        # 如果是跨层建筑，在所有层创建相同位置的建筑区
        layers_to_create = range(min_layer, max_layer + 1) if is_multi_layer else [min_layer]
        
        for current_layer in layers_to_create:
            # 保存到数据库
            save_success = self.db_manager.save_building_area(
                name=f"{full_name}_层{current_layer}",  # 为每层生成唯一名称
                map_name=self.map_name,
                layer=current_layer,
                position=valid_position,  # 相同的位置
                type="circle",
                corner=actual_radius,  # 相同的半径
                size_data=size_data  # 相同的大小数据
            )
            
            if save_success:
                print(f"圆塔建筑区 '{full_name}_层{current_layer}' 创建成功，圆心位置: {valid_position}，半径: {actual_radius}")
                
                area_info = {
                    "name": f"{full_name}_层{current_layer}",
                    "position": valid_position,
                    "radius": actual_radius
                }
                
                # 添加层级信息
                area_info = self.add_multi_layer_info(area_info, is_multi_layer, min_layer, max_layer)
                area_info["layer"] = current_layer
                
                success_areas.append(area_info)
        
        return success_areas
