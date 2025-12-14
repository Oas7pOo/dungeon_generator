import numpy as np
import random
import math
import json
import sqlite3
import time
from shapely.geometry import Point, LineString, Polygon

class RoomGenerator:
    """
    房间生成器，负责在建筑区内生成房间，与原来的Room类结构保持一致
    """
    
    def __init__(self, db_manager):
        """
        初始化房间生成器
        
        Args:
            db_manager: 数据库管理器实例
        """
        self.db_manager = db_manager
        self.room_id = 0
        # 确保使用与重构前兼容的room表结构
        self._create_compatible_tables()
    
    def _create_compatible_tables(self):
        """
        创建与重构前兼容的room表结构
        """
        # 先删除旧表，重新创建以修改主键
        self.db_manager.execute('''
        DROP TABLE IF EXISTS room
        ''')
        # 创建room表，使用复合主键(name, map_name, layer_name)
        self.db_manager.execute('''
        CREATE TABLE IF NOT EXISTS room (
            name TEXT,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (name, map_name, layer_name)
        )
        ''')
    
    def _generate_unique_name(self, prefix="Room"):
        """
        生成唯一的房间名称
        
        Args:
            prefix: 房间名称前缀
            
        Returns:
            唯一的房间名称
        """
        try:
            # 获取当前最大的房间编号
            result = self.db_manager.fetch_one(
                "SELECT name FROM room WHERE name LIKE ? ORDER BY CAST(SUBSTR(name, ?) AS INTEGER) DESC LIMIT 1",
                (f"{prefix}_%", len(prefix) + 2)
            )
            
            if result:
                current_max = int(result[0].split('_')[1])
                new_number = current_max + 1
            else:
                new_number = 1
            
            return f"{prefix}_{new_number}"
        except Exception as e:
            print(f"生成房间名称时出错: {e}")
            # 使用时间戳作为备选方案
            return f"{prefix}_{int(time.time())}"
    
    def generate_room_in_building(self, building_area, map_name, layer):
        """
        在建筑区内生成单个房间，与原来的代码结构保持一致
        
        Args:
            building_area: 建筑区数据
            map_name: 地图名称
            layer: 层级
            
        Returns:
            生成的房间数据，符合重构前格式
        """
        try:
            building_name = building_area["name"]
            area_type = building_area["type"]
            position_str = building_area["position"]
            corner_str = building_area["corner"]
            
            # 解析建筑区数据
            position = json.loads(position_str)
            
            if area_type == "circle":
                return self._generate_circle_room(building_name, map_name, layer, position, corner_str)
            else:
                return self._generate_polygon_room(building_name, map_name, layer, position, corner_str)
        except Exception as e:
            print(f"在建筑区内生成房间时出错: {e}")
            return None
    
    def _generate_circle_room(self, building_name, map_name, layer, position, corner_str):
        """
        在圆形建筑区内生成房间
        
        Args:
            building_name: 建筑区名称
            map_name: 地图名称
            layer: 层级
            position: 建筑区中心位置
            corner_str: 圆形建筑区的半径
            
        Returns:
            生成的房间数据
        """
        try:
            radius = float(corner_str)
            center_x, center_y = position
            
            # 生成房间名称
            if "三层圆塔" in building_name:  # 圆塔使用固定房间名
                room_name = "room1"
            else:
                room_name = self._generate_unique_name()
            
            # 生成房间的所有空间格子，确保与地图格子对齐
            all_space_grids = []
            # 计算整数边界，确保格子对齐
            min_x = int(center_x - radius)
            max_x = int(center_x + radius)
            min_y = int(center_y - radius)
            max_y = int(center_y + radius)
            
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    # 检查格子中心是否在圆形内，确保格子对齐
                    grid_center_x = x + 0.5
                    grid_center_y = y + 0.5
                    if (grid_center_x - center_x)**2 + (grid_center_y - center_y)**2 <= radius**2:
                        all_space_grids.append([x, y])
            
            if not all_space_grids:
                return None
            
            # 识别外层格子作为墙壁，修复圆形房间墙壁生成破碎的问题
            outer_grids = []
            for grid in all_space_grids:
                x, y = grid
                # 检查格子是否是真正的外层（至少有一个相邻格子不在圆形内）
                is_outer = False
                
                # 检查所有8个相邻格子
                for dx in [-1, 0, 1]:
                    for dy in [-1, 0, 1]:
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        # 检查相邻格子中心是否在圆形内，与生成空间格子时的逻辑保持一致
                        neighbor_center_x = nx + 0.5
                        neighbor_center_y = ny + 0.5
                        if (neighbor_center_x - center_x)**2 + (neighbor_center_y - center_y)**2 > radius**2:
                            is_outer = True
                            break
                    if is_outer:
                        break
                if is_outer:
                    outer_grids.append(grid)
            
            # 计算内部空间格子（排除外层格子）
            space_grid_list = [grid for grid in all_space_grids if grid not in outer_grids]
            
            # 设置房间基本属性
            wall_grid_list = outer_grids
            inner_wall_grid_list = []  # 初始化为空
            room_type = "circle"
            
            # 计算房间面积
            area = len(space_grid_list)
            
            # 生成矢量参数
            vector_params = {
                "type": "circle",
                "radius": radius,
                "center": position,
                "width": radius * 2,
                "height": radius * 2
            }
            
            # 生成房间数据
            room_data = {
                "name": room_name,
                "map_name": map_name,
                "layer_name": str(layer),
                "building_area": building_name,
                "wall_grid_list": json.dumps(wall_grid_list),
                "space_grid_list": json.dumps(space_grid_list),
                "inner_wall_grid_list": json.dumps(inner_wall_grid_list),
                "room_type": room_type,
                "vector_params": json.dumps(vector_params),
                "other_params": json.dumps({}),
                "area": area
            }
            
            return room_data
        except Exception as e:
            print(f"生成圆形房间时出错: {e}")
            return None
    
    def _generate_polygon_room(self, building_name, map_name, layer, position, corner_str):
        """
        在多边形建筑区内生成房间
        
        Args:
            building_name: 建筑区名称
            map_name: 地图名称
            layer: 层级
            position: 建筑区中心位置
            corner_str: 建筑区的角点数据
            
        Returns:
            生成的房间数据
        """
        try:
            # 解析角点数据
            corners = json.loads(corner_str)
            
            # 生成房间名称
            room_name = self._generate_unique_name()
            
            # 创建建筑区多边形
            building_polygon = Polygon(corners)
            
            # 计算建筑区的边界
            min_x = int(min(point[0] for point in corners))
            max_x = int(max(point[0] for point in corners))
            min_y = int(min(point[1] for point in corners))
            max_y = int(max(point[1] for point in corners))
            
            # 生成房间的所有空间格子
            all_space_grids = []
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    # 检查格子是否在多边形内
                    grid_point = Point(x + 0.5, y + 0.5)  # 检查格子中心是否在多边形内
                    if building_polygon.contains(grid_point):
                        all_space_grids.append([x, y])
            
            if not all_space_grids:
                return None
            
            # 识别外层格子作为墙壁
            outer_grids = []
            for grid in all_space_grids:
                x, y = grid
                # 检查格子是否在外层（至少有一个相邻格子不在多边形内）
                is_outer = False
                for dx in [-1, 0, 1]:
                    for dy in [-1, 0, 1]:
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        neighbor_point = Point(nx + 0.5, ny + 0.5)
                        if not building_polygon.contains(neighbor_point):
                            is_outer = True
                            break
                    if is_outer:
                        break
                if is_outer:
                    outer_grids.append(grid)
            
            # 计算内部空间格子（排除外层格子）
            space_grid_list = [grid for grid in all_space_grids if grid not in outer_grids]
            
            # 设置房间基本属性
            wall_grid_list = outer_grids
            inner_wall_grid_list = []  # 初始化为空
            
            # 确定房间类型
            if len(corners) == 4:
                # 检查是否为旋转矩形
                x_coords = [x for x, y in corners]
                y_coords = [y for x, y in corners]
                width = max(x_coords) - min(x_coords)
                height = max(y_coords) - min(y_coords)
                
                # 计算旋转角度
                angle = 0
                # 简单判断是否为旋转矩形
                is_aligned = abs(corners[0][0] - corners[3][0]) < 0.1 or abs(corners[0][1] - corners[1][1]) < 0.1
                
                if is_aligned:
                    room_type = "rectangle"
                else:
                    room_type = "rotated_rectangle"
            else:
                room_type = "polygon"
            
            # 计算房间面积
            area = len(space_grid_list)
            
            # 生成矢量参数
            vector_params = {
                "type": room_type,
                "corners": corners,
                "center": position,
                "width": max_x - min_x,
                "height": max_y - min_y
            }
            
            # 生成房间数据
            room_data = {
                "name": room_name,
                "map_name": map_name,
                "layer_name": str(layer),
                "building_area": building_name,
                "wall_grid_list": json.dumps(wall_grid_list),
                "space_grid_list": json.dumps(space_grid_list),
                "inner_wall_grid_list": json.dumps(inner_wall_grid_list),
                "room_type": room_type,
                "vector_params": json.dumps(vector_params),
                "other_params": json.dumps({}),
                "area": area
            }
            
            return room_data
        except Exception as e:
            print(f"生成多边形房间时出错: {e}")
            return None
    
    def save_room(self, room_data):
        """
        保存房间数据到数据库（使用与重构前兼容的room表）
        
        Args:
            room_data: 符合重构前格式的房间数据
            
        Returns:
            成功返回True，失败返回False
        """
        if not room_data:
            return False
        
        try:
            # 检查房间是否已存在，使用(name, map_name, layer_name)的组合作为唯一标识
            existing = self.db_manager.fetch_one(
                "SELECT name FROM room WHERE name = ? AND map_name = ? AND layer_name = ?",
                (room_data["name"], room_data["map_name"], room_data["layer_name"])
            )
            
            if existing:
                return False
            
            # 保存到数据库
            self.db_manager.execute('''
            INSERT INTO room (
                name, map_name, layer_name, building_area, wall_grid_list, 
                space_grid_list, inner_wall_grid_list, room_type, vector_params, 
                other_params, area
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                room_data["name"],
                room_data["map_name"],
                room_data["layer_name"],
                room_data["building_area"],
                room_data["wall_grid_list"],
                room_data["space_grid_list"],
                room_data["inner_wall_grid_list"],
                room_data["room_type"],
                room_data["vector_params"],
                room_data["other_params"],
                room_data["area"]
            ))
            
            return True
        except Exception as e:
            print(f"保存房间到数据库时出错: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def maze_room(self, room_name, maze_width=1, complexity=0.5):
        """
        使用完全重写的迷宫算法，根据房间的空间格子列表生成真正的迷宫，
        迷宫由通道和墙壁组成，内部墙壁存储为inner_wall_grid_list
        
        参数:
            room_name: 要处理的房间名称
            maze_width: 迷宫通道的宽度，默认为1
            complexity: 迷宫的复杂度，0.0-1.0之间，值越大迷宫越复杂
            
        返回:
            成功返回True，失败返回False
        """
        print(f"开始将房间 '{room_name}' 转换为迷宫...")
        try:
            # 读取房间数据
            result = self.db_manager.fetch_one(
                '''SELECT vector_params, room_type, space_grid_list, wall_grid_list, inner_wall_grid_list
                   FROM room WHERE name = ?''',
                (room_name,)
            )
            
            if not result:
                print(f"未找到房间 '{room_name}'")
                return False
            
            vector_params = json.loads(result[0])
            room_type = result[1]
            space_grid_list = json.loads(result[2])
            wall_grid_list = json.loads(result[3])
            
            print(f"房间空间格子数量: {len(space_grid_list)}")
            
            if len(space_grid_list) < 9:  # 至少需要3x3的空间才能生成有意义的迷宫
                print("空间格子数量太少，无法生成迷宫")
                return False
            
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
                    if [x, y] in space_grid_list:
                        grid[(x, y)] = 0  # 初始都是墙
                    else:
                        grid[(x, y)] = -1  # 不在房间内的格子
            
            # 找出所有可能的迷宫单元格（每隔一格）
            maze_cells = []
            for y in range(min_y, max_y + 1, 2):
                for x in range(min_x, max_x + 1, 2):
                    if (x, y) in grid and grid[(x, y)] == 0:
                        maze_cells.append((x, y))
            
            if not maze_cells:
                print("没有足够的格子来生成迷宫")
                return False
                
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
                    # 回溯
                    stack.pop()
            
            # 确保至少有一个入口/出口
            # 找到边缘格子
            edge_cells = []
            for (x, y) in grid:
                if grid[(x, y)] != -1:  # 在房间内
                    # 检查是否在边缘
                    is_edge = (x == min_x or x == max_x or y == min_y or y == max_y)
                    if is_edge:
                        edge_cells.append((x, y))
            
            # 在边缘创建2-4个入口/出口
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
            self.db_manager.execute('''
                UPDATE room 
                SET inner_wall_grid_list = ?
                WHERE name = ?
            ''', (inner_wall_grid_str, room_name))
            
            print(f"房间 '{room_name}' 迷宫生成成功，内墙数量: {len(inner_wall_grid_list)}")
            return True
            
        except Exception as e:
            print(f"迷宫生成出错: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def generate_and_save_rooms(self, map_name):
        """
        为所有建筑区生成并保存房间，与原来的generate_rooms方法保持一致
        
        Args:
            map_name: 地图名称
            
        Returns:
            成功生成的房间数量
        """
        # 获取所有建筑区
        building_areas = self.db_manager.fetch_all(
            "SELECT name, position, type, corner, layer FROM building_areas WHERE map_name = ?",
            (map_name,)
        )
        
        success_count = 0
        
        for area in building_areas:
            name, position_str, area_type, corner_str, layer = area
            
            # 构造建筑区数据
            building_area = {
                "name": name,
                "type": area_type,
                "position": position_str,
                "corner": corner_str
            }
            
            # 生成房间
            room_data = self.generate_room_in_building(building_area, map_name, layer)
            
            if room_data and self.save_room(room_data):
                success_count += 1
                print(f"✅ 为 {name}（层 {layer}）生成了房间")
                
                # 对于旋转矩形房间的层1，生成迷宫
                if "旋转矩形房间" in name and layer == 1:
                    self.maze_room(room_data["name"], maze_width=1, complexity=0.5)
        
        return success_count
