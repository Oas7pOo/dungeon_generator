import sqlite3
import json
from shapely.geometry import Polygon, Point, LineString       
import random
import math
import time
import traceback
import datetime

#房间必须有房间名，房间所属地图和层名（可以跨层），和房间墙格子列表，房间空间格子列表，可以有房间类型，房间矢量参数
class Room:
    def __init__(self, 房间名=None, 地图名=None, 层名=None, 建筑区=None, 坐标=None, 墙格子列表=None, 空间格子列表=None, 
                 房间类型=None, 矢量参数=None, 面积=None, 其他参数=None, 内部墙格子列表=None):
        # 连接数据库
        self.conn = sqlite3.connect('dungeon.db')
        self.cursor = self.conn.cursor()
        
        # 确保room表存在
        self.create_tables()
        
        # 如果没有提供房间名，生成一个唯一的名称
        房间名 = self._generate_unique_name()
        
        # 初始化基本属性
        self.name = 房间名
        self.wall_grid_list = 墙格子列表 or []
        self.space_grid_list = 空间格子列表 or []
        self.inner_wall_grid_list = 内部墙格子列表 or []  # 新增内部墙壁格子列表
        self.room_type = 房间类型
        self.vector_params = 矢量参数 or {}
        self.建筑区 = 建筑区
        self.面积 = 面积
        self.坐标 = 坐标
        # 地图名称和层级名称的默认值
        default_map_name = "地牢"
        default_layer_name = "1"
        
    def 阅读建筑区(self, 建筑区名称):
        # 从数据库获取建筑区信息
        self.cursor.execute('''
            SELECT name, map_name, layer, position, type, size, corner, area
            FROM building_areas 
            WHERE name LIKE ? 
            ORDER BY name ASC 
            LIMIT 1
        ''', (建筑区名称 + '%',))
        
        建筑区数据 = self.cursor.fetchone()

        # 解析 size 字段（JSON 格式）
        size_data = json.loads(建筑区数据[5])

        # 如果是多层建筑，更新建筑区数据的层数信息
        if size_data.get("is_multi_layer", False):  # 检查 is_multi_layer 是否为 True
            min_layer = size_data.get("min_layer", 建筑区数据[2])  # 默认用原层
            max_layer = size_data.get("max_layer", 建筑区数据[2])
            
            # 更新 `layer` 字段为 `[min_layer, max_layer]`
            建筑区数据 = list(建筑区数据)  # 元组不可变，转为列表
            建筑区数据[2] = [min_layer, max_layer]  # 更新层数
            建筑区数据 = tuple(建筑区数据)  # 变回元组

        return 建筑区数据
          
    def _generate_unique_name(self, 名字=None):
        """生成唯一的房间名称"""
        if 名字 is not None:
            #在数据库中查询是否存在这个名字
            self.cursor.execute("SELECT name FROM room WHERE name = ?", (名字,))
            if 名字 != self.cursor.fetchone(): #如果这个名字不存在，返回这个名字
                return 名字
            else:                              #如果这个名字存在，返回一个新名字
                pass 
        try:
            # 获取当前最大的房间编号
            self.cursor.execute("""
                SELECT name FROM room 
                WHERE name LIKE 'Room_%' 
                ORDER BY CAST(SUBSTR(name, 6) AS INTEGER) DESC 
                LIMIT 1
            """)
            result = self.cursor.fetchone()
            
            if result:
                # 从现有的最大编号增加1
                current_max = int(result[0].split('_')[1])
                new_number = current_max + 1
            else:
                # 如果没有现有的房间，从1开始
                new_number = 1
            
            return f"Room_{new_number}"
            
        except Exception as e:
            print(f"生成房间名称时出错: {e}")
            # 使用时间戳作为备选方案
            return f"Room_{int(time.time())}"
    
    def create_tables(self):
        """创建必要的数据库表"""
        # 检查表是否存在
        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='room'")
        table_exists = self.cursor.fetchone()
        
        if not table_exists:
            # 表不存在，创建新表
            self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS room (
                name TEXT PRIMARY KEY,
                map_name TEXT,
                layer_name TEXT,
                building_area TEXT,
                wall_grid_list TEXT,
                space_grid_list TEXT,
                inner_wall_grid_list TEXT,
                room_type TEXT,
                vector_params TEXT,
                other_params TEXT,
                area REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            self.conn.commit()
        else:
            # 表已存在，检查是否有内部墙壁列
            self.cursor.execute("PRAGMA table_info(room)")
            columns = self.cursor.fetchall()
            column_names = [column[1] for column in columns]
            
            # 如果没有内部墙壁列，添加它
            if "inner_wall_grid_list" not in column_names:
                try:
                    self.cursor.execute("ALTER TABLE room ADD COLUMN inner_wall_grid_list TEXT")
                    self.conn.commit()
                    print("添加了inner_wall_grid_list列到已存在的room表")
                except sqlite3.OperationalError as e:
                    print(f"添加列时出错: {e}")
    
    def save_to_db(self):
        """将房间信息保存到数据库"""
        try:
            # 检查是否已存在
            self.cursor.execute("SELECT name FROM room WHERE name = ?", (self.name,))
            if self.cursor.fetchone():
                print(f"房间 '{self.name}' 已存在")
                return False
            
            # 检查空间重叠 - 新增检查
            if hasattr(self, 'space_grid_list') and hasattr(self, 'map_name') and hasattr(self, 'layer_name'):
                overlap_info = self._check_space_overlap(self.space_grid_list, self.map_name, self.layer_name)
                if overlap_info:
                    print(f"错误: {overlap_info['message']}")
                    print(f"重叠的房间: {overlap_info['overlapping_room']}")
                    print(f"重叠格子数: {overlap_info['overlap_count']}")
                    print(f"前几个重叠格子: {overlap_info['overlap_grids']}")
                    return False
            
            # 准备数据
            wall_grid_str = json.dumps(self.wall_grid_list)
            space_grid_str = json.dumps(self.space_grid_list)
            inner_wall_grid_str = json.dumps(self.inner_wall_grid_list)  # 序列化内部墙壁列表
            vector_params_str = json.dumps(self.vector_params)
            
            # 插入数据
            self.cursor.execute('''
            INSERT INTO room 
            (name, map_name, layer_name, building_area, wall_grid_list, space_grid_list, inner_wall_grid_list, room_type, vector_params, other_params, area, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (self.name, self.map_name, self.layer_name, self.building_area, wall_grid_str, 
                 space_grid_str, inner_wall_grid_str, self.room_type, vector_params_str, self.other_params, self.area, self.created_at))
            
            self.conn.commit()
            print(f"房间 '{self.name}' 保存成功")
            return True
            
        except Exception as e:
            print(f"保存房间时出错: {e}")
            return False
    
    def load_from_db(self, name=None):
        """从数据库加载房间信息"""
        try:
            if name is None:
                name = self.name
            
            self.cursor.execute('''
            SELECT map_name, layer_name, wall_grid_list, space_grid_list, inner_wall_grid_list,
                   room_type, vector_params 
            FROM room WHERE name = ?
            ''', (name,))
            
            result = self.cursor.fetchone()
            if not result:
                print(f"未找到房间 '{name}'")
                return False
            
            # 解析数据
            map_name, layer_name, wall_grid_str, space_grid_str, inner_wall_grid_str, room_type, vector_params_str = result
            
            self.name = name
            self.map_name = map_name
            self.layer_name = layer_name
            self.wall_grid_list = json.loads(wall_grid_str)
            self.space_grid_list = json.loads(space_grid_str)
            
            # 处理可能为NULL的内部墙壁格子列表
            if inner_wall_grid_str:
                self.inner_wall_grid_list = json.loads(inner_wall_grid_str)
            else:
                self.inner_wall_grid_list = []
                
            self.room_type = room_type
            self.vector_params = json.loads(vector_params_str)
            
            return True
            
        except Exception as e:
            print(f"加载房间时出错: {e}")
            return False
    
    def 计算点到线段的最小距离(self, point, line):
        """ 计算点到线段的最短距离 """
        return line.distance(Point(point))

    def _is_point_inside_polygon(self, point, polygon):
        """判断点是否在多边形内部（射线法）"""
        x, y = point
        n = len(polygon)
        inside = False
        
        p1x, p1y = polygon[0]
        for i in range(1, n + 1):
            p2x, p2y = polygon[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        
        return inside
    
    def _is_valid_polygon(self, corners, min_distance):
        """检查多边形是否合法"""
        if len(corners) < 3:
            return False
        
        # 检查是否有自相交
        poly = Polygon(corners)
        if not poly.is_valid:
            return False
        print(f"多边形合法")
        
        # 检查是否有狭缝（任意非相邻点之间的距离小于最小值）
        # 遍历每个点
        for i, point in enumerate(corners):
            # 该点的相邻边（上一条边和下一条边）
            prev_idx = (i - 1) % len(corners)
            next_idx = (i + 1) % len(corners)

            # 计算点到所有**非相邻边**的最小距离
            for j in range(len(corners)):
                if j == prev_idx or j == i or (j + 1) % len(corners) == i:
                    continue  # 跳过相邻边

                # 当前边 (corner[j], corner[j+1])
                edge = LineString([corners[j], corners[(j + 1) % len(corners)]])
                #print(f"计算点到线段的最小距离: {point}, {edge}")
                # 计算点到这条边的最小距离
                distance = self.计算点到线段的最小距离(point, edge)
                #print(f"计算点到线段的最小距离: {distance}")    
                # 如果距离小于 `min_distance`，立即返回 False   
                if distance < min_distance:
                    return False

        return True  # 所有点都满足条件

    def 随机点在线上(self, 点1, 点2):
                
        # 生成 0 到 1 之间的随机数 t
        t = random.uniform(0, 1)

        # 计算线段上的随机点
        x = 点1[0] + t * (点2[0] - 点1[0])
        y = 点1[1] + t * (点2[1] - 点1[1])

        random_point = (x, y)

        return random_point
   
    def distance(self, p1, p2):
        # 计算房间对角线长度（用于确定默认最大移动值）
        return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5

    def __del__(self):
        """关闭数据库连接"""
        if hasattr(self, 'conn'):
            self.conn.close()

    def _check_space_overlap(self, space_grid_list, 地图名, 层名):
        """
        检查新房间的空间格子是否与已存在房间重叠
        
        参数:
            space_grid_list: 新房间的空间格子列表
            地图名: 地图名称
            层名: 层名或层级范围
            
        返回:
            overlap_info: 如果有重叠，返回重叠信息的字典；如果没有重叠，返回None
        """
        try:
            # 将空间格子列表转换为集合，以便快速查找
            new_space_set = set(tuple(grid) for grid in space_grid_list)
            
            # 处理层级范围
            层级条件 = ""
            if '-' in str(层名):
                # 处理层级范围如 "1-3"
                try:
                    min_layer, max_layer = map(int, str(层名).split('-'))
                    层级列表 = [str(i) for i in range(min_layer, max_layer + 1)]
                    层级条件 = f"layer_name IN ({','.join(['?'] * len(层级列表))})"
                    层级参数 = 层级列表
                except:
                    # 如果解析失败，使用原始层名
                    层级条件 = "layer_name = ?"
                    层级参数 = [层名]
            else:
                # 单一层级
                层级条件 = "layer_name = ?"
                层级参数 = [层名]
            
            # 查询具有相同地图名和层级名的所有房间
            查询 = f"""
                SELECT name, space_grid_list
                FROM room
                WHERE map_name = ? AND {层级条件}
            """
            
            查询参数 = [地图名] + 层级参数
            self.cursor.execute(查询, 查询参数)
            
            结果列表 = self.cursor.fetchall()
            
            # 检查每个房间是否与新房间重叠
            for 房间名, 格子列表字符串 in 结果列表:
                # 如果是检查自己，跳过
                if 房间名 == getattr(self, 'name', None):
                    continue
                    
                existing_space_list = json.loads(格子列表字符串)
                existing_space_set = set(tuple(grid) for grid in existing_space_list)
                
                # 寻找重叠的格子
                overlap_grids = new_space_set.intersection(existing_space_set)
                
                if overlap_grids:
                    # 转换回列表格式以便返回
                    overlap_list = [list(grid) for grid in overlap_grids]
                    return {
                        "overlapping_room": 房间名,
                        "overlap_count": len(overlap_list),
                        "overlap_grids": overlap_list[:10],  # 只返回前10个重叠格子避免数据过大
                        "message": f"新房间与现有房间 '{房间名}' 重叠，共 {len(overlap_list)} 个格子重叠"
                    }
            
            # 如果没有重叠，返回None
            return None
            
        except Exception as e:
            print(f"检查空间重叠时出错: {e}")
            traceback.print_exc()
            return {"error": str(e), "message": "检查空间重叠时出现错误"}

class 多边形房间(Room):
    def __init__(self, 房间名=None, 地图名=None, 层名=None, 建筑区=None, 墙格子列表=None, 空间格子列表=None, 
                 房间类型=None, 矢量参数=None, 面积=None, 其他参数=None, 内部墙格子列表=None):
        super().__init__(房间名, 地图名, 层名, 建筑区, 墙格子列表, 空间格子列表, 
                        房间类型, 矢量参数, 面积, 其他参数, 内部墙格子列表)
    
    def _generate_space_grids(self, corners):
        """
        生成多边形内部的空间格子
        使用简单的矢量方法判断：当格子中心点在多边形内部时，视为内部格子
        """
        # 获取边界
        x_coords = [x for x, y in corners]
        y_coords = [y for x, y in corners]
        min_x, max_x = int(min(x_coords)), int(max(x_coords))
        min_y, max_y = int(min(y_coords)), int(max(y_coords))
        
        # 创建多边形对象
        poly = Polygon(corners)
        
        # 收集内部格子
        space_grids = []
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                # 简单判断：格子中心点在多边形内部
                if poly.contains(Point(x, y)):
                    space_grids.append((x, y))
        
        return space_grids
    
    def _generate_circle_space_grids(self, center_x, center_y, radius):
        """
        生成圆形内部的空间格子
        使用简单的矢量方法判断：当格子中心点到圆心的距离小于等于半径时，视为内部格子
        """
        space_grids = []
        
        # 确定包围盒
        min_x = int(center_x - radius)
        max_x = int(center_x + radius)
        min_y = int(center_y - radius)
        max_y = int(center_y + radius)
        
        # 扫描每个可能的格子点
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                # 计算点到圆心的距离
                dx = x - center_x
                dy = y - center_y
                distance = (dx**2 + dy**2)**0.5
                
                # 简单判断：格子中心点到圆心的距离小于等于半径
                if distance <= radius:
                    space_grids.append((x, y))
        
        return space_grids
    
    def _is_rectangle(self, corners):
        """判断一组顶点是否形成矩形（不一定是轴对齐的）"""
        if len(corners) != 4:
            return False
        
        # 对于矩形，相邻两条边应该垂直
        for i in range(4):
            p1 = corners[i]
            p2 = corners[(i+1) % 4]
            p3 = corners[(i+2) % 4]
            
            # 计算两条边的向量
            v1 = (p2[0] - p1[0], p2[1] - p1[1])
            v2 = (p3[0] - p2[0], p3[1] - p2[1])
            
            # 计算点积，如果垂直，点积应该约等于0
            dot_product = v1[0] * v2[0] + v1[1] * v2[1]
            
            # 允许一定的误差
            if abs(dot_product) > 0.01:
                return False
        
        return True
    
    def _calculate_center(self, corners):
        """计算多边形的中心点"""
        x_coords = [x for x, y in corners]
        y_coords = [y for x, y in corners]
        center_x = sum(x_coords) / len(corners)
        center_y = sum(y_coords) / len(corners)
        return [center_x, center_y]
    
    def _calculate_rotation_angle(self, corners):
        """计算矩形的旋转角度（弧度）"""
        if len(corners) != 4:
            return 0
            
        # 取第一条边的方向作为旋转角度
        dx = corners[1][0] - corners[0][0]
        dy = corners[1][1] - corners[0][1]
        
        angle = math.atan2(dy, dx)
        return angle
    
    def _calculate_rectangle_dimensions(self, corners):
        """计算旋转矩形的宽度和高度"""
        if len(corners) != 4:
            return 0, 0

        # 计算两条边的长度
        edge1_len = math.sqrt((corners[1][0] - corners[0][0])**2 + (corners[1][1] - corners[0][1])**2)
        edge2_len = math.sqrt((corners[2][0] - corners[1][0])**2 + (corners[2][1] - corners[1][1])**2)
        
        return edge1_len, edge2_len
    
    def _identify_outer_grids(self, space_grids):
        """
        识别外部格子（边界格子）
        当内部格子周围4个方向中有任何一个方向不是内部格子，则该格子被视为外部格子
        
        参数:
            space_grids: 内部格子列表
            
        返回:
            外部格子列表
        """
        # 创建一个集合用于快速查找
        space_grid_set = set(space_grids)
        outer_grids = []
        
        # 定义四个方向：上、右、下、左
        directions = [(0, 1), (1, 0), (0, -1), (-1, 0)]
        
        # 遍历每个内部格子
        for grid in space_grids:
            x, y = grid
            
            # 检查四个相邻格子
            for dx, dy in directions:
                neighbor = (x + dx, y + dy)
                
                # 如果任何相邻格子不在内部格子集合中，则当前格子是外部格子
                if neighbor not in space_grid_set:
                    outer_grids.append(grid)
                    break  # 一旦确定是外部格子，就不需要继续检查
        
        return outer_grids
    
    def 从建筑区生成一个房间(self, 建筑区, 层级=None, 房间名=None, 允许重叠=False):
        """
        根据建筑区生成多边形房间，确保矢量参数与建筑区相同
        
        参数:
            建筑区: 建筑区名称（字符串）
            层级: 层级信息，可以是单个数字或层级范围元组(min_layer, max_layer)
            房间名: 自定义房间名称，如果不提供则自动生成
            允许重叠: 是否允许与已存在的房间空间重叠，默认为False
            
        返回:
            生成的房间实例，如果生成失败则返回None
        """
        try:
            # 从数据库读取建筑区数据
            建筑区数据 = self.阅读建筑区(建筑区)
            if not 建筑区数据:
                print(f"错误: 未找到建筑区 '{建筑区}'")
                return None
                
            # 解析建筑区数据
            建筑区名称 = 建筑区数据[0]
            地图名称 = 建筑区数据[1]
            层级信息 = 建筑区数据[2]
            建筑区位置 = json.loads(建筑区数据[3]) if 建筑区数据[3] else None
            建筑区类型 = 建筑区数据[4]
            建筑区矢量数据 = json.loads(建筑区数据[5]) if 建筑区数据[5] else {}
            建筑区顶点数据 = 建筑区数据[6]
            建筑区面积 = 建筑区数据[7]
            
            print(f"读取建筑区: {建筑区名称}, 类型: {建筑区类型}, 层级: {层级信息}")
            
            # 处理层级参数
            if isinstance(层级信息, list):
                min_layer, max_layer = 层级信息
            else:
                min_layer = max_layer = 层级信息
                
            # 如果有显式指定的层级参数
            if 层级 is not None:
                # 验证层级在建筑区范围内
                if isinstance(层级, tuple) and len(层级) == 2:
                    if not (min_layer <= 层级[0] <= 层级[1] <= max_layer):
                        print(f"警告: 指定的层级范围 {层级} 不在建筑区的层级范围 ({min_layer}, {max_layer}) 内")
                    min_layer, max_layer = 层级
                elif isinstance(层级, int):
                    if not (min_layer <= 层级 <= max_layer):
                        print(f"警告: 指定的层级 {层级} 不在建筑区的层级范围 ({min_layer}, {max_layer}) 内")
                    min_layer = max_layer = 层级
                else:
                    print(f"警告: 忽略无效的层级参数 {层级}")
            
            # 设置房间的层级名称
            layer_name = str(min_layer) if min_layer == max_layer else f"{min_layer}-{max_layer}"
            
            # 显式设置 layer_name 属性确保正确保存
            self.layer_name = layer_name
            
            # 处理房间名参数
            if 房间名 is None:
                房间名 = self._generate_unique_name()
            else:
                # 检查房间名是否存在
                self.cursor.execute("SELECT name FROM room WHERE name = ?", (房间名,))
                if self.cursor.fetchone():
                    print(f"警告: 房间名 '{房间名}' 已存在，将生成新的唯一名称")
                    房间名 = self._generate_unique_name()
            
            # 设置房间基本属性
            self.name = 房间名
            self.map_name = 地图名称
            self.building_area = 建筑区名称
            self.area = 建筑区面积
            
            # 生成当前时间戳
            self.created_at = datetime.datetime.now().isoformat()
            
            # 生成其他参数 - 确保层级信息明确保存
            self.other_params = json.dumps({
                "source_building_area": 建筑区名称,
                "min_layer": min_layer,
                "max_layer": max_layer,
                "specific_layers": [min_layer] if min_layer == max_layer else list(range(min_layer, max_layer + 1))
            })
            
            # 根据建筑区类型生成不同形状的房间
            if 建筑区类型 == "rectangle":
                # 处理矩形建筑区
                print("处理矩形建筑区")
                
                # 解析角点数据
                if isinstance(建筑区顶点数据, str):
                    try:
                        corners = json.loads(建筑区顶点数据)
                    except:
                        print(f"警告: 无法解析角点数据: {建筑区顶点数据}")
                        corners = []
                else:
                    corners = 建筑区顶点数据
                
                if not corners or len(corners) < 4:
                    print("错误: 矩形建筑区没有足够的角点")
                    return None
                
                # 生成空间格子
                all_space_grids = self._generate_space_grids(corners)
                
                # 识别外部格子
                outer_grids = self._identify_outer_grids(all_space_grids)
                
                # 更新内部格子列表（移除外部格子）
                self.space_grid_list = [grid for grid in all_space_grids if grid not in outer_grids]
                self.wall_grid_list = outer_grids  # 外部格子作为墙格子
                self.inner_wall_grid_list = []  # 初始化内部墙壁列表为空
                
                # 提取矩形参数
                width = 建筑区矢量数据.get("width", 0)
                height = 建筑区矢量数据.get("height", 0)
                
                # 如果没有明确的宽高，从角点计算
                if width == 0 or height == 0:
                    # 计算边界框
                    x_coords = [x for x, y in corners]
                    y_coords = [y for x, y in corners]
                    width = max(x_coords) - min(x_coords)
                    height = max(y_coords) - min(y_coords)
                
                # 设置房间类型和矢量参数
                self.room_type = "rectangle"
                self.vector_params = {
                    "type": "rectangle",
                    "width": width,
                    "height": height,
                    "center": 建筑区位置,
                    "corners": corners,
                    "angle": 0,  # 轴对齐矩形的角度为0
                    "min_layer": min_layer,
                    "max_layer": max_layer
                }
                
            elif 建筑区类型 == "circle":
                # 处理圆形建筑区
                print("处理圆形建筑区")
                
                # 获取半径（可能存储在不同位置）
                radius = None
                
                # 1. 从角点数据获取
                if 建筑区顶点数据 and isinstance(建筑区顶点数据, (int, float, str)):
                    try:
                        radius = float(建筑区顶点数据)
                    except:
                        print(f"警告: 无法从角点数据获取半径: {建筑区顶点数据}")
                
                # 2. 从矢量数据获取
                if (radius is None or radius <= 0) and "radius" in 建筑区矢量数据:
                    radius = float(建筑区矢量数据["radius"])
                
                if radius is None or radius <= 0:
                    print("错误: 无法获取圆形建筑区的半径")
                    return None
                
                # 确保有中心点
                if not 建筑区位置:
                    print("错误: 圆形建筑区没有中心点位置")
                    return None
                
                center_x, center_y = 建筑区位置
                
                # 生成圆形空间格子
                all_space_grids = self._generate_circle_space_grids(center_x, center_y, radius)
                
                # 识别外部格子
                outer_grids = self._identify_outer_grids(all_space_grids)
                
                # 更新内部格子列表（移除外部格子）
                self.space_grid_list = [grid for grid in all_space_grids if grid not in outer_grids]
                self.wall_grid_list = outer_grids  # 外部格子作为墙格子
                self.inner_wall_grid_list = []  # 初始化内部墙壁列表为空
                
                # 设置房间类型和矢量参数
                self.room_type = "circle"
                self.vector_params = {
                    "type": "circle",
                    "radius": radius,
                    "center": 建筑区位置,
                    "width": radius * 2,  # 直径作为宽度
                    "height": radius * 2,  # 直径作为高度
                    "shape": "circle",
                    "min_layer": min_layer,
                    "max_layer": max_layer,
                    "layer_name": layer_name,  # 添加明确的层级名称
                    "specific_layers": [min_layer] if min_layer == max_layer else list(range(min_layer, max_layer + 1))
                }
                
            elif 建筑区类型 == "polygon":
                # 处理多边形建筑区
                print("处理多边形建筑区")
                
                # 解析角点数据
                if isinstance(建筑区顶点数据, str):
                    try:
                        corners = json.loads(建筑区顶点数据)
                    except:
                        print(f"警告: 无法解析角点数据: {建筑区顶点数据}")
                        corners = []
                else:
                    corners = 建筑区顶点数据
                
                if not corners or len(corners) < 3:
                    print("错误: 多边形建筑区没有足够的角点")
                    return None
                
                # 生成空间格子
                all_space_grids = self._generate_space_grids(corners)
                
                # 识别外部格子
                outer_grids = self._identify_outer_grids(all_space_grids)
                
                # 更新内部格子列表（移除外部格子）
                self.space_grid_list = [grid for grid in all_space_grids if grid not in outer_grids]
                self.wall_grid_list = outer_grids  # 外部格子作为墙格子
                self.inner_wall_grid_list = []  # 初始化内部墙壁列表为空
                
                # 检查是否是矩形
                is_rectangle = self._is_rectangle(corners)
                
                if is_rectangle and len(corners) == 4:
                    # 计算旋转矩形的参数
                    width, height = self._calculate_rectangle_dimensions(corners)
                    angle = self._calculate_rotation_angle(corners)
                    
                    # 判断是否为轴对齐的矩形
                    is_aligned = abs(math.sin(angle)) < 0.01 or abs(math.cos(angle)) < 0.01
                    
                    if is_aligned:
                        self.room_type = "rectangle"
                    else:
                        self.room_type = "rotated_rectangle"
                    
                    # 计算或获取中心点
                    center = 建筑区位置 if 建筑区位置 else self._calculate_center(corners)
                    
                    # 设置矢量参数
                    self.vector_params = {
                        "type": self.room_type,
                        "corners": corners,
                        "center": center,
                        "width": width,
                        "height": height,
                        "angle": angle,
                        "min_layer": min_layer,
                        "max_layer": max_layer
                    }
                else:
                    # 一般多边形
                    self.room_type = "polygon"
                    
                    # 计算或获取中心点
                    center = 建筑区位置 if 建筑区位置 else self._calculate_center(corners)
                    
                    # 计算多边形的边界框
                    x_coords = [x for x, y in corners]
                    y_coords = [y for x, y in corners]
                    min_x, max_x = min(x_coords), max(x_coords)
                    min_y, max_y = min(y_coords), max(y_coords)
                    width = max_x - min_x
                    height = max_y - min_y
                    
                    # 设置矢量参数
                    self.vector_params = {
                        "type": "polygon",
                        "corners": corners,
                        "center": center,
                        "width": width,
                        "height": height,
                        "num_vertices": len(corners),
                        "min_layer": min_layer,
                        "max_layer": max_layer
                    }
            else:
                print(f"错误: 不支持的建筑区类型 '{建筑区类型}'")
                return None
            
            # 确保面积值被设置
            if "area" not in self.vector_params and self.area:
                self.vector_params["area"] = self.area
            
            # 添加其他有用的向量信息
            self.vector_params["source_building_area"] = 建筑区名称
            self.vector_params["layer_name"] = self.layer_name
            
            # 确保层级信息在矢量参数中始终一致
            if "min_layer" not in self.vector_params:
                self.vector_params["min_layer"] = min_layer
            if "max_layer" not in self.vector_params:
                self.vector_params["max_layer"] = max_layer
            if "specific_layers" not in self.vector_params:
                self.vector_params["specific_layers"] = [min_layer] if min_layer == max_layer else list(range(min_layer, max_layer + 1))
            
            # 检查新房间是否与已有房间重叠
            if not 允许重叠:
                overlap_info = self._check_space_overlap(self.space_grid_list, self.map_name, self.layer_name)
                if overlap_info:
                    print(f"错误: {overlap_info['message']}")
                    print(f"重叠的房间: {overlap_info['overlapping_room']}")
                    print(f"重叠格子数: {overlap_info['overlap_count']}")
                    print(f"前几个重叠格子: {overlap_info['overlap_grids']}")
                    return None
            
            print(f"生成的{self.room_type}房间: {len(self.space_grid_list)} 内部格子, {len(self.wall_grid_list)} 外部格子, 层级: {self.layer_name}")
            
            # 保存到数据库
            if self.save_to_db():
                print(f"成功将房间 '{self.name}' 保存到数据库, 层级: {self.layer_name}")
                return self
            else:
                print(f"无法将房间 '{self.name}' 保存到数据库")
                return None
                
        except Exception as e:
            print(f"从建筑区生成房间时出错: {e}")
            traceback.print_exc()
            return None

    def 收缩房间(self, 房间名, 狭缝最小值=2, 移动最大值=None, 收缩次数范围=(0, 3)):
        """
        读取已生成的房间，对其形状进行修改。
        
        参数:
            房间名: 要处理的房间名称
            狭缝最小值: 收缩后最小保持的距离，默认为2
            移动最大值: 最大收缩距离，默认为房间最大对角线的1/6
            收缩次数范围: 收缩的次数范围，默认为(0,3)，表示最可能0次，最不可能3次
            
        返回:
            成功返回修改后的房间实例，失败返回None
        """
        try:
            # 读取房间数据
            self.cursor.execute('''
                SELECT vector_params, room_type, space_grid_list, wall_grid_list
                FROM room WHERE name = ?
            ''', (房间名,))
            
            result = self.cursor.fetchone()
            if not result:
                print(f"未找到房间 '{房间名}'")
                return None
                
            vector_params = json.loads(result[0])
            room_type = result[1]
            space_grid_list = json.loads(result[2])
            wall_grid_list = json.loads(result[3])
            
            # 检查房间类型和矢量参数
            if room_type not in ["rectangle", "rotated_rectangle", "polygon"]:
                print(f"房间类型 '{room_type}' 不支持修改操作")
                return None
                
            if "corners" not in vector_params:
                print("矢量参数中没有角点信息")
                return None
                
            corners = vector_params["corners"]
            if len(corners) < 3:
                print("角点数量不足，无法进行修改")
                return None
                
            # 确定修改次数（三角概率分布）
            min_repeats, max_repeats = 收缩次数范围
            if min_repeats > max_repeats:
                min_repeats, max_repeats = max_repeats, min_repeats

            # 使用三角分布，最可能值是下限，最不可能值是上限
            操作次数 = int(random.triangular(min_repeats, max_repeats + 1, min_repeats))
            
            # 如果操作次数为0，直接返回原房间
            if 操作次数 == 0:
                print("无需进行修改操作")
                return self.load_from_db(房间名)
                
            # 计算所有顶点对之间的最大距离
            max_dist = 0
            for i in range(len(corners)):
                for j in range(i+1, len(corners)):
                    dist = self.distance(corners[i], corners[j])
                    max_dist = max(max_dist, dist)
            
            print(f"[DEBUG] 计算的最大顶点距离: {max_dist:.2f}")
            
            # 设置默认最大移动值为对角线的1/6
            if 移动最大值 is None:
                移动最大值 = max_dist / 6
                print(f"设置最大移动值为: {移动最大值:.2f}")
            
            # 记录原始角点
            original_corners = corners.copy()
            
            # 创建原始房间多边形
            try:
                original_polygon = Polygon(corners)
                if not original_polygon.is_valid:
                    print("原始房间多边形无效，无法进行修改")
                    return None
                print(f"[DEBUG] 原始多边形有效，面积: {original_polygon.area:.2f}")
            except Exception as e:
                print(f"创建原始多边形时出错: {e}")
                return None
            
            # 初始化修改状态
            modified = False
            
            # 进行多次修改尝试
            for attempt in range(操作次数):
                print(f"\n尝试第 {attempt+1}/{操作次数} 次顶点操作")
                success = False
                
                # 每次尝试最多50次
                for trial in range(50):
                    # 随机选择一个顶点
                    print(f"[DEBUG] 尝试 {trial+1}/50") if trial % 10 == 0 else None
                    
                    # 随机选择一个边
                    vertex_idx = random.randint(0, len(corners) - 1)
                    
                    # 获取顶点及其相邻顶点
                    next_idx = (vertex_idx + 1) % len(corners)
                    
                    current_vertex = corners[vertex_idx]
                    next_vertex = corners[next_idx]
                    
                    # 计算向内的方向
                    direction = [(current_vertex[1] - next_vertex[1]), (next_vertex[0] - current_vertex[0])]
                    magnitude = math.sqrt(direction[0] ** 2 + direction[1] ** 2)
                    direction = [direction[0] / magnitude, direction[1] / magnitude]
                    
                    # 随机移动距离
                    move_distance = random.uniform(0.5, 移动最大值)
                    print(f"[DEBUG] 选择边 {vertex_idx}-{next_idx}, 移动距离: {move_distance:.2f}")
                    
                    # 选择收缩模式
                    mode = random.choice([0, 1, 2])
                    new_corners = corners.copy()
                    
                    if mode == 0:
                        # 模式0: 移动左角点，添加中间点和移动后的中间点
                        new_vertex = self.随机点在线上(current_vertex, next_vertex)
                        current_vertex = [
                                            current_vertex[0] + direction[0] * move_distance,
                                            current_vertex[1] + direction[1] * move_distance
                                        ]
                        new_vertex_after = [
                            new_vertex[0] + direction[0] * move_distance,
                            new_vertex[1] + direction[1] * move_distance
                                        ]
                        # 尝试更新顶点
                        new_corners = corners.copy()
                        new_corners[vertex_idx] = current_vertex
                        new_corners.insert(vertex_idx + 1, new_vertex_after)
                        new_corners.insert(vertex_idx + 2, new_vertex)
                        print(f"模式0: 移动左角点，添加中间点和移动后的中间点")

                    elif mode == 1:
                        new_vertex = self.随机点在线上(current_vertex, next_vertex)
                        next_vertex = [
                                            next_vertex[0] + direction[0] * move_distance,
                                            next_vertex[1] + direction[1] * move_distance
                                        ]
                        new_vertex_after = [
                            new_vertex[0] + direction[0] * move_distance,
                            new_vertex[1] + direction[1] * move_distance
                                        ]
                        # 尝试更新顶点
                        new_corners = corners.copy()
                        new_corners[next_idx] = next_vertex
                        new_corners.insert(next_idx, new_vertex)
                        new_corners.insert(next_idx + 1, new_vertex_after)
                        print(f"模式1: 移动右角点，添加中间点和移动后的中间点")

                    else:
                        new_vertex1 = self.随机点在线上(current_vertex, next_vertex)
                        new_vertex2 = self.随机点在线上(current_vertex, next_vertex)
                        distance1 = self.distance(new_vertex1,current_vertex)
                        distance2 = self.distance(new_vertex2,current_vertex)
                        if distance1 > distance2:
                            new_vertex1, new_vertex2 = new_vertex2, new_vertex1

                        new_vertex_after1 = [
                            new_vertex1[0] + direction[0] * move_distance,
                            new_vertex1[1] + direction[1] * move_distance
                                        ]
                        new_vertex_after2 = [
                            new_vertex2[0] + direction[0] * move_distance,
                            new_vertex2[1] + direction[1] * move_distance
                                        ]
                        # 尝试更新顶点
                        new_corners = corners.copy()
                        new_corners.insert(vertex_idx + 1, new_vertex1)
                        new_corners.insert(vertex_idx + 2, new_vertex_after1)
                        new_corners.insert(vertex_idx + 3, new_vertex_after2)
                        new_corners.insert(vertex_idx + 4, new_vertex2)
                        print(f"模式2: 移动两个角点，添加中间点和移动后的中间点")
                    
                    # 检查新多边形是否有效
                    try:
                        success_po = self._is_valid_polygon(new_corners, 狭缝最小值)
                        if not success_po:
                            continue
                    except Exception as e:
                        # 如果发生异常，尝试下一种方案
                        print(f"发生异常，尝试下一种方案")
                        continue
                    
                    # 移动成功
                    corners = new_corners
                    success = True
                    break
                
                if success:
                    print(f"第 {attempt+1} 次操作成功")
                    modified = True
                else:
                    print(f"第 {attempt+1} 次操作失败，无法找到合适的方案")
            
            # 如果没有成功修改，直接返回
            if not modified:
                print("未能成功修改房间形状")
                return self.load_from_db(房间名)
                
            # 输出新顶点信息
            print(f"房间角点数量从 {len(original_corners)} 增加到 {len(corners)}")
            
            # 更新房间的矢量参数
            vector_params["corners"] = corners
            
            # 重新生成空间格子和墙格子
            print(f"[DEBUG] 重新生成空间格子")
            all_space_grids = self._generate_space_grids(corners)
            outer_grids = self._identify_outer_grids(all_space_grids)
            space_grid_list = [grid for grid in all_space_grids if grid not in outer_grids]
            wall_grid_list = outer_grids
            
            print(f"[DEBUG] 新的空间格子数: {len(space_grid_list)}, 墙格子数: {len(wall_grid_list)}")
            
            # 更新数据库
            vector_params_str = json.dumps(vector_params)
            space_grid_str = json.dumps(space_grid_list)
            wall_grid_str = json.dumps(wall_grid_list)
            inner_wall_grid_str = json.dumps([])  # 默认为空内部墙壁列表
            
            print(f"[DEBUG] 更新数据库")
            self.cursor.execute('''
                UPDATE room 
                SET vector_params = ?, space_grid_list = ?, wall_grid_list = ?, inner_wall_grid_list = ?
                WHERE name = ?
            ''', (vector_params_str, space_grid_str, wall_grid_str, inner_wall_grid_str, 房间名))
            
            self.conn.commit()
            print(f"[DEBUG] 数据库更新完成")
            
            print(f"房间 '{房间名}' 形状修改完成")
            
            # 返回更新后的房间
            return self.load_from_db(房间名)
            
        except Exception as e:
            print(f"修改房间时出错: {e}")
            traceback.print_exc()
            return None
    
    def 迷宫房间(self, 房间名, 迷宫宽度=1, 复杂度=0.5):
        """
        使用完全重写的迷宫算法，根据房间的空间格子列表生成真正的迷宫，
        迷宫由通道和墙壁组成，内部墙壁存储为inner_wall_grid_list
        
        参数:
            房间名: 要处理的房间名称
            迷宫宽度: 迷宫通道的宽度，默认为1
            复杂度: 迷宫的复杂度，0.0-1.0之间，值越大迷宫越复杂
            
        返回:
            成功返回修改后的房间实例，失败返回None
        """
        print(f"开始将房间 '{房间名}' 转换为迷宫...")
        try:
            # 读取房间数据
            self.cursor.execute('''
                SELECT vector_params, room_type, space_grid_list, wall_grid_list, inner_wall_grid_list
                FROM room WHERE name = ?
            ''', (房间名,))
            
            result = self.cursor.fetchone()
            if not result:
                print(f"未找到房间 '{房间名}'")
                return None
            
            vector_params = json.loads(result[0])
            room_type = result[1]
            space_grid_list = json.loads(result[2])
            wall_grid_list = json.loads(result[3])
            
            print(f"房间空间格子数量: {len(space_grid_list)}")
            
            if len(space_grid_list) < 9:  # 至少需要3x3的空间才能生成有意义的迷宫
                print("空间格子数量太少，无法生成迷宫")
                return None
            
            # 获取房间的边界
            x_coords = [x for x, y in space_grid_list]
            y_coords = [y for x, y in space_grid_list]
            min_x, max_x = min(x_coords), max(x_coords)
            min_y, max_y = min(y_coords), max(y_coords)
            width = max_x - min_x + 1
            height = max_y - min_y + 1
            
            print(f"房间边界: ({min_x},{min_y}) - ({max_x},{max_y}), 尺寸: {width}x{height}")
            
            # 创建网格表示
            # 0 = 墙壁, 1 = 通道, 2 = 已访问单元格
            grid = {}
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    if (x, y) in [(p[0], p[1]) for p in space_grid_list]:
                        grid[(x, y)] = 0  # 初始都是墙
                    else:
                        grid[(x, y)] = -1  # 不在房间内的格子
            
            # 迷宫代码使用坐标(行,列)而不是(x,y)
            # 创建一个稀疏网格，只包含房间内的单元格
            # 找出所有可能的迷宫单元格（每隔一格）
            maze_cells = []
            for y in range(min_y, max_y + 1, 2):
                for x in range(min_x, max_x + 1, 2):
                    if (x, y) in grid and grid[(x, y)] == 0:
                        maze_cells.append((x, y))
            
            if not maze_cells:
                print("没有足够的格子来生成迷宫")
                return None
                
            print(f"可用的迷宫单元格: {len(maze_cells)}")
            
            # 随机选择起点
            current = random.choice(maze_cells)
            grid[current] = 1  # 标记为通道
            
            # 将起点的邻居添加到栈中
            stack = [current]
            visited = {current}
            
            # 定义可能的移动方向（上、右、下、左）
            directions = [(0, -2), (2, 0), (0, 2), (-2, 0)]
            
            # 主循环: 随机深度优先搜索
            while stack:
                x, y = stack[-1]
                
                # 获取当前位置所有可能的移动（间隔2格）
                neighbors = []
                for dx, dy in directions:
                    nx, ny = x + dx, y + dy
                    if (nx, ny) in grid and grid[(nx, ny)] == 0:
                        neighbors.append((nx, ny))
                
                if neighbors:
                    # 随机选择下一个位置
                    nx, ny = random.choice(neighbors)
                    
                    # 计算中间位置
                    mx, my = (x + nx) // 2, (y + ny) // 2
                    
                    # 打通通道（中间格子和目标格子）
                    if (mx, my) in grid:
                        grid[(mx, my)] = 1
                    grid[(nx, ny)] = 1
                    
                    # 将新访问的位置添加到栈中
                    stack.append((nx, ny))
                    visited.add((nx, ny))
                else:
                    # 如果没有可用的邻居，回溯
                    stack.pop()
            
            # 根据复杂度添加额外通道（复杂度越低，额外通道越多）
            extra_paths = int(((1 - 复杂度) * len(maze_cells)) / 4)
            print(f"添加 {extra_paths} 条额外通道")
            
            wall_cells = [(x, y) for (x, y) in grid if grid[(x, y)] == 0 
                        and (x % 2 == 1 or y % 2 == 1)  # 只考虑中间的墙，而不是迷宫单元格
                        and any(grid.get((x+dx, y+dy)) == 1 for dx, dy in [(0,1), (1,0), (0,-1), (-1,0)] 
                             if (x+dx, y+dy) in grid)]  # 至少有一个邻居是通道
            
            # 随机选择一些墙并打通它们
            for _ in range(min(extra_paths, len(wall_cells))):
                if not wall_cells:
                    break
                    
                wall_idx = random.randint(0, len(wall_cells) - 1)
                wx, wy = wall_cells.pop(wall_idx)
                grid[(wx, wy)] = 1
            
            # 如果迷宫宽度大于1，扩展通道
            if 迷宫宽度 > 1:
                print(f"扩展通道宽度至 {迷宫宽度}")
                for width_expansion in range(迷宫宽度 - 1):
                    # 找到所有通道格子
                    path_cells = [(x, y) for (x, y) in grid if grid[(x, y)] == 1]
                    
                    # 扩展每个通道格子
                    for px, py in path_cells:
                        # 尝试扩展四个方向
                        for dx, dy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
                            nx, ny = px + dx, py + dy
                            if (nx, ny) in grid and grid[(nx, ny)] == 0:
                                # 只有当至少一个邻居已经是通道时才扩展
                                if any(grid.get((nx+dx2, ny+dy2)) == 1 
                                       for dx2, dy2 in [(0,1), (1,0), (0,-1), (-1,0)] 
                                       if (nx+dx2, ny+dy2) in grid and (nx+dx2, ny+dy2) != (px, py)):
                                    grid[(nx, ny)] = 1
            
            # 确保有出入口
            # 找出边缘格子
            edge_cells = []
            for x, y in [(p[0], p[1]) for p in space_grid_list]:
                if grid.get((x, y), -1) == 0:  # 是墙
                    # 检查是否在边缘
                    if any((x+dx, y+dy) not in grid or grid.get((x+dx, y+dy), -1) == -1
                           for dx, dy in [(0,1), (1,0), (0,-1), (-1,0)]):
                        # 只有当邻居中有通道时才考虑作为出入口
                        if any(grid.get((x+dx, y+dy), -1) == 1
                               for dx, dy in [(0,1), (1,0), (0,-1), (-1,0)]):
                            edge_cells.append((x, y))
            
            # 随机选择2-4个边缘格子作为出入口
            entrances_count = min(len(edge_cells), random.randint(2, 4))
            for _ in range(entrances_count):
                if not edge_cells:
                    break
                    
                idx = random.randint(0, len(edge_cells) - 1)
                ex, ey = edge_cells.pop(idx)
                grid[(ex, ey)] = 1  # 标记为通道
            
            # 收集所有仍然是墙的格子作为内部墙壁
            inner_wall_grid_list = [[x, y] for (x, y) in grid if grid[(x, y)] == 0]
            
            print(f"创建的内墙数量: {len(inner_wall_grid_list)}")
            print(f"通道数量: {sum(1 for v in grid.values() if v == 1)}")
            
            # 更新数据库
            inner_wall_grid_str = json.dumps(inner_wall_grid_list)
            self.cursor.execute('''
                UPDATE room 
                SET inner_wall_grid_list = ?
                WHERE name = ?
            ''', (inner_wall_grid_str, 房间名))
            
            self.conn.commit()
            print(f"房间 '{房间名}' 迷宫生成成功，内墙数量: {len(inner_wall_grid_list)}")
            
            # 返回更新后的房间
            return self.load_from_db(房间名)
            
        except Exception as e:
            print(f"迷宫生成出错: {e}")
            traceback.print_exc()
            self.conn.rollback()
            return None

    def 扇贝房间(self, 房间名, 房间最小值=4):
        """
        生成扇贝形状的房间，房间最小值为4
        
        参数:
            房间名: 要处理的房间名称
            房间最小值: 房间的最小边长，默认为4
        
        返回:
            成功返回修改后的房间实例，失败返回None
        """

        print(f"开始将房间 '{房间名}' 转换为扇贝...")
        try:
            # 读取房间数据
            self.cursor.execute('''
                SELECT vector_params, room_type, space_grid_list, wall_grid_list, inner_wall_grid_list
                FROM room WHERE name = ?
            ''', (房间名,))
            
            result = self.cursor.fetchone()
            if not result:
                print(f"未找到房间 '{房间名}'")
                return None
            
            vector_params = json.loads(result[0])
            room_type = result[1]
            space_grid_list = json.loads(result[2])
            wall_grid_list = json.loads(result[3])
            
            print(f"房间空间格子数量: {len(space_grid_list)}")
            
            if len(space_grid_list) < 9:  # 至少需要3x3的空间才能生成有意义的迷宫
                print("空间格子数量太少，无法生成迷宫")
                return None
            
            if room_type != "circle":
                print("房间类型不是圆形，无法生成扇贝")
                return None
            
            # 获取房间的边界
            
        

if __name__ == "__main__":
    # 测试为三层洋房生成房间 - 不指定房间名，指定在1-2层
    print("\n===== 测试生成三层洋房房间 =====")
    
    # 创建多边形房间实例
    room = 多边形房间(地图名="地牢测试")

    # 确保我们不会创建重叠的房间 - 先清除同名房间
    try:
        conn = sqlite3.connect('dungeon.db')
        cursor = conn.cursor()
        
        # 检查并删除可能存在的测试房间
        test_rooms = ["我的新房间", "新圆形房间", "多层测试房间"]
        for test_room in test_rooms:
            cursor.execute("SELECT name FROM room WHERE name = ?", (test_room,))
            if cursor.fetchone():
                print(f"删除已存在的房间: {test_room}")
                cursor.execute("DELETE FROM room WHERE name = ?", (test_room,))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"清除旧房间时出错: {e}")

    # 从建筑区名称生成房间 - 确保使用不同的名称避免重叠
    new_room = room.从建筑区生成一个房间("旋转房间2_层1至2", 层级=(1, 2), 房间名="我的新房间")

    # 也可以指定层级范围 - 使用不同名称
    multi_layer_room = room.从建筑区生成一个房间("三层洋房_层1至3", 层级=(1, 2), 房间名="多层测试房间")

    # 测试生成圆形房间 - 使用不同名称
    print("\n===== 测试生成圆形房间 =====")
    circle_room = room.从建筑区生成一个房间("圆塔", 层级=1, 房间名="新圆形房间")

    # 测试房间收缩功能
    print("\n===== 测试房间收缩功能 =====")
    if new_room:  # 确保房间创建成功
        room.收缩房间(
            "我的新房间", 
            狭缝最小值=2,  # 确保收缩后保持至少2格距离
            移动最大值=3,  # 最大收缩3格
            收缩次数范围=(1, 1),  # 进行1次操作
        )

        # 测试迷宫生成功能
        print("\n===== 测试迷宫生成功能 =====")
        # 生成适度复杂度的迷宫
        maze_room = room.迷宫房间("我的新房间", 迷宫宽度=1, 复杂度=0.5)
    else:
        print("房间创建失败，跳过收缩和迷宫测试")

