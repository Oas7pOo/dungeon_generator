import sqlite3
import json

class DatabaseManager:
    """
    数据库管理器，负责数据库连接、表创建和数据操作
    """
    
    def __init__(self, db_path='dungeon.db'):
        """
        初始化数据库连接
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        """
        创建必要的数据库表
        """
        # 创建map表
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS map (
            name TEXT PRIMARY KEY,
            width INTEGER,
            height INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 创建building_areas表
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS building_areas (
            name TEXT PRIMARY KEY,
            map_name TEXT,
            min_layer INTEGER,
            max_layer INTEGER,
            position TEXT,
            type TEXT,
            size TEXT,
            corner TEXT,
            area REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        self.conn.commit()
    
    def close(self):
        """
        关闭数据库连接
        """
        self.conn.close()
    
    def execute(self, query, params=None):
        """
        执行SQL查询
        
        Args:
            query: SQL查询语句
            params: 查询参数
            
        Returns:
            查询结果
        """
        if params:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)
        self.conn.commit()
        return self.cursor
    
    def fetch_one(self, query, params=None):
        """
        获取单行查询结果
        
        Args:
            query: SQL查询语句
            params: 查询参数
            
        Returns:
            单行查询结果
        """
        self.execute(query, params)
        return self.cursor.fetchone()
    
    def fetch_all(self, query, params=None):
        """
        获取所有查询结果
        
        Args:
            query: SQL查询语句
            params: 查询参数
            
        Returns:
            所有查询结果
        """
        self.execute(query, params)
        return self.cursor.fetchall()
    
    def save_building_area(self, name, map_name, min_layer, max_layer, position, type, corner, size_data=None):
        """
        保存建筑区到数据库
        
        Args:
            name: 建筑区名称
            map_name: 地图名称
            min_layer: 最小层索引
            max_layer: 最大层索引
            position: 位置坐标
            type: 建筑区类型
            corner: 角点数据
            size_data: 大小数据
            
        Returns:
            成功返回True，失败返回False
        """
        try:
            # 确保名称不重复
            self.cursor.execute("SELECT name FROM building_areas WHERE name = ?", (name,))
            if self.cursor.fetchone():
                print(f"建筑区名称 '{name}' 已存在")
                return False
            
            # 准备位置和角点数据
            position_str = json.dumps(position)
            corner_str = json.dumps(corner) if not isinstance(corner, (int, float, str)) else str(corner)
            size_str = json.dumps(size_data) if size_data else "{}"
            
            # 从大小数据中获取面积
            area = 0
            if size_data and "area" in size_data:
                area = float(size_data["area"])
            
            # 插入数据
            self.cursor.execute('''
            INSERT INTO building_areas
            (name, map_name, min_layer, max_layer, position, type, corner, size, area, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (name, map_name, min_layer, max_layer, position_str, type, corner_str, size_str, area))
            
            self.conn.commit()
            print(f"建筑区 '{name}' 保存成功")
            return True
            
        except Exception as e:
            print(f"保存建筑区时出错: {e}")
            return False
    
    def get_map_size(self, map_name):
        """
        获取地图尺寸
        
        Args:
            map_name: 地图名称
            
        Returns:
            (width, height) 或 None
        """
        try:
            self.cursor.execute("SELECT width, height FROM map WHERE name = ?", (map_name,))
            result = self.cursor.fetchone()
            return result  # 返回 (width, height)
        except Exception as e:
            print(f"获取地图尺寸时出错: {e}")
            return None
    
    def get_building_areas_by_layer(self, map_name, layer):
        """
        获取与指定层有交集的所有建筑区
        
        Args:
            map_name: 地图名称
            layer: 层索引
            
        Returns:
            建筑区列表
        """
        try:
            self.cursor.execute('''
            SELECT name, position, type, corner, size, min_layer, max_layer FROM building_areas 
            WHERE map_name = ? AND (min_layer <= ? AND max_layer >= ?)
            ''', (map_name, layer, layer))
            
            result = []
            for row in self.cursor.fetchall():
                name, position_str, type, corner_str, size_str, min_layer, max_layer = row
                
                # 解析位置、角点和大小
                try:
                    position = json.loads(position_str)
                except:
                    position = eval(position_str) if position_str else None
                
                if type == "circle":
                    try:
                        radius = float(corner_str)
                    except:
                        radius = 0
                    result.append({
                        "name": name,
                        "type": type,
                        "position": position,
                        "radius": radius,
                        "min_layer": min_layer,
                        "max_layer": max_layer
                    })
                else:  # 矩形或其他形状
                    try:
                        corner = json.loads(corner_str)
                    except:
                        corner = eval(corner_str) if corner_str else None
                        
                    try:
                        size = json.loads(size_str)
                    except:
                        size = eval(size_str) if size_str else None
                        
                    result.append({
                        "name": name,
                        "type": type,
                        "position": position,
                        "corner": corner,
                        "size": size,
                        "min_layer": min_layer,
                        "max_layer": max_layer
                    })
            
            return result
        except Exception as e:
            print(f"获取建筑区时出错: {e}")
            return []
