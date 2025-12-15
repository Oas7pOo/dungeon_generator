import numpy as np
import random
import math
import json
from shapely.geometry import Point, LineString, Polygon, MultiPoint

class ItemGenerator:
    """
    物品生成器，负责生成各种物品，包括门
    """
    
    def __init__(self, db_manager):
        """
        初始化物品生成器
        
        Args:
            db_manager: 数据库管理器实例
        """
        self.db_manager = db_manager
        self._create_item_tables()
    
    def _create_item_tables(self):
        """
        创建物品相关的数据库表
        """
        # 创建物品表
        self.db_manager.execute('''
        CREATE TABLE IF NOT EXISTS item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            map_name TEXT,
            min_layer INTEGER,
            max_layer INTEGER,
            type TEXT,
            position TEXT,
            vector_params TEXT,
            properties TEXT,
            container_room TEXT,
            container_building TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(container_room) REFERENCES room(name)
        )
        ''')
    
    def generate_door(self, room_data, radius=0.8):
        """
        在房间边缘生成门，使用圆形算法
        
        Args:
            room_data: 房间数据
            radius: 门生成的半径，默认为0.8
            
        Returns:
            门物品数据
        """
        try:
            # 解析房间数据
            wall_grid_list = json.loads(room_data["wall_grid_list"])
            vector_params = json.loads(room_data["vector_params"])
            room_type = room_data["room_type"]
            min_layer = room_data["min_layer"]
            max_layer = room_data["max_layer"]
            
            # 生成门的位置和矢量参数
            door_position, door_vector, door_walls = self._calculate_door_params(
                wall_grid_list, vector_params, room_type, radius
            )
            
            if not door_position or not door_vector:
                return None
            
            # 确保至少有一个墙壁格子
            if not door_walls:
                # 对于圆形房间，直接使用门位置作为墙壁格子
                if room_type == "circle":
                    door_walls = [[round(door_position[0]), round(door_position[1])]]
                else:
                    return None
            
            # 生成门物品数据，包含min_layer和max_layer
            door_item = {
                "name": f"Door_{room_data['name']}",
                "map_name": room_data["map_name"],
                "min_layer": min_layer,
                "max_layer": max_layer,
                "type": "door",
                "position": json.dumps(door_position),
                "vector_params": json.dumps(door_vector),
                "properties": json.dumps({
                    "door_radius": radius,
                    "room_name": room_data["name"]
                }),
                "container_room": room_data["name"],
                "container_building": room_data["building_area"],
                "door_wall_list": json.dumps(door_walls)
            }
            
            return door_item
        except Exception as e:
            print(f"生成门时出错: {e}")
            return None
    
    def _calculate_door_params(self, wall_grid_list, vector_params, room_type, radius):
        """
        计算门的位置和矢量参数
        
        Args:
            wall_grid_list: 墙壁格子列表
            vector_params: 房间矢量参数
            room_type: 房间类型
            radius: 门生成的半径
            
        Returns:
            门位置, 门矢量参数, 门所在的墙壁格子
        """
        door_walls = []
        
        if room_type == "circle":
            # 圆形房间门生成，与其他房间一致的逻辑
            center = vector_params.get("center", [0, 0])
            radius_room = vector_params.get("radius", 0)
            
            # 1. 在圆周上随机选择一个点作为门的中心
            # 生成随机角度（0到2π）
            random_angle = random.uniform(0, 2 * math.pi)
            
            # 计算门的位置（圆周上的点）
            door_position = [
                center[0] + radius_room * math.cos(random_angle),
                center[1] + radius_room * math.sin(random_angle)
            ]
            
            # 2. 生成门的矢量参数（圆形）
            door_vector = {
                "type": "circle",
                "center": door_position,
                "radius": radius
            }
            
            # 3. 找出与门圆接触的所有墙壁格子
            door_circle = Point(door_position).buffer(radius)
            for wall in wall_grid_list:
                # 墙壁格子的中心点
                wall_center = Point(wall[0] + 0.5, wall[1] + 0.5)
                # 墙壁格子的边界
                wall_bounds = [
                    (wall[0], wall[1]),
                    (wall[0] + 1, wall[1]),
                    (wall[0] + 1, wall[1] + 1),
                    (wall[0], wall[1] + 1)
                ]
                wall_polygon = Polygon(wall_bounds)
                
                # 检查墙壁格子是否与门圆接触
                if door_circle.intersects(wall_polygon):
                    door_walls.append(wall)
                    
        elif room_type in ["rectangle", "rotated_rectangle", "polygon"]:
            # 多边形/矩形房间门生成
            corners = vector_params.get("corners", [])
            if not corners or len(corners) < 3:
                return None, None, []
            
            # 创建房间多边形
            room_polygon = Polygon(corners)
            
            # 获取房间的所有边
            edges = []
            for i in range(len(corners)):
                start = corners[i]
                end = corners[(i+1) % len(corners)]
                edges.append(LineString([start, end]))
            
            # 随机选择一条边
            selected_edge = random.choice(edges)
            
            # 在边上随机选择一个点，避开顶点
            edge_length = selected_edge.length
            if edge_length <= 2 * radius:
                return None, None, []
            
            # 生成边上的随机点，避开顶点
            distance = random.uniform(radius, edge_length - radius)
            door_position = list(selected_edge.interpolate(distance).coords)[0]
            
            # 生成门的矢量参数（圆形）
            door_vector = {
                "type": "circle",
                "center": door_position,
                "radius": radius
            }
            
            # 找出与门圆接触的墙壁格子
            door_circle = Point(door_position).buffer(radius)
            for wall in wall_grid_list:
                wall_center = Point(wall[0] + 0.5, wall[1] + 0.5)
                wall_square = wall_center.buffer(0.5)
                if door_circle.intersects(wall_square):
                    door_walls.append(wall)
        else:
            return None, None, []
        
        return door_position, door_vector, door_walls
    
    def save_item(self, item_data):
        """
        保存物品到数据库
        
        Args:
            item_data: 物品数据
            
        Returns:
            成功返回True，失败返回False
        """
        try:
            # 检查物品是否已存在
            existing = self.db_manager.fetch_one(
                "SELECT id FROM item WHERE name = ? AND map_name = ?",
                (item_data["name"], item_data["map_name"])
            )
            
            if existing:
                return False
            
            # 保存物品
            self.db_manager.execute('''
            INSERT INTO item (
                name, map_name, min_layer, max_layer, type, position, vector_params, 
                properties, container_room, container_building
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item_data["name"],
                item_data["map_name"],
                item_data["min_layer"],
                item_data["max_layer"],
                item_data["type"],
                item_data["position"],
                item_data["vector_params"],
                item_data["properties"],
                item_data["container_room"],
                item_data["container_building"]
            ))
            
            return True
        except Exception as e:
            print(f"保存物品时出错: {e}")
            return False
    
    def generate_and_save_doors(self, map_name):
        """
        为地图中的所有房间生成并保存门
        
        Args:
            map_name: 地图名称
            
        Returns:
            成功生成的门数量
        """
        # 获取所有房间
        rooms = self.db_manager.fetch_all(
            "SELECT * FROM room WHERE map_name = ?",
            (map_name,)
        )
        
        success_count = 0
        
        for room in rooms:
            # 将房间数据转换为字典，使用min_layer和max_layer代替layer_name
            room_data = {
                "name": room[0],
                "map_name": room[1],
                "min_layer": room[2],
                "max_layer": room[3],
                "building_area": room[4],
                "wall_grid_list": room[5],
                "space_grid_list": room[6],
                "inner_wall_grid_list": room[7],
                "door_grid_list": room[8],
                "room_type": room[9],
                "vector_params": room[10],
                "other_params": room[11],
                "area": room[12]
            }
            
            # 生成门
            door_item = self.generate_door(room_data)
            
            if door_item and self.save_item(door_item):
                success_count += 1
                
                # 更新房间的door_grid_list，使用name和map_name作为条件
                door_walls = json.loads(door_item["door_wall_list"])
                if door_walls:
                    self.db_manager.execute('''
                    UPDATE room 
                    SET door_grid_list = ?
                    WHERE name = ? AND map_name = ?
                    ''', (
                        door_item["door_wall_list"],
                        room_data["name"],
                        room_data["map_name"]
                    ))
        
        return success_count
