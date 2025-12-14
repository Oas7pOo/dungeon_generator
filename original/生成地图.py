import sqlite3
import uuid
import os
import time
import json
import math
import random
import numpy as np
from shapely.geometry import Point, LineString, Polygon

class 建筑区生成器:
    def __init__(self, 建筑区名, map_name = "地牢", layer = 1, 建筑区位置 = None, 建筑区类型 = None, 建筑区角点 = None, 建筑区大小 = None):
        self.建筑区名 = 建筑区名
        self.map_name = map_name
        self.layer = layer
        self.建筑区位置 = 建筑区位置
        self.建筑区类型 = 建筑区类型
        self.建筑区角点 = 建筑区角点
        self.建筑区大小 = 建筑区大小

        # 连接数据库
        self.conn = sqlite3.connect('dungeon.db')
        self.cursor = self.conn.cursor()
        
        # 确保building_areas表存在
        self.create_tables()

    def create_tables(self):
        """创建必要的数据库表"""
        # 创建building_areas表
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS building_areas (
            name TEXT PRIMARY KEY,
            map_name TEXT,
            layer INTEGER,
            position TEXT,
            type TEXT,
            size TEXT,
            corner TEXT,
            area REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        self.conn.commit()
        
    def create_building_area(self, name=None):      
        """创建建筑区"""
        if name is None:
            name = self.建筑区名
        
        # 确保name不重复 
        self.cursor.execute("SELECT name FROM building_areas WHERE name = ?", (name,))
        if self.cursor.fetchone():
            print(f"建筑区名称 '{name}' 已存在，请使用其他名称")
            return
    
    def 获取地图大小(self):
        """获取当前地图的尺寸"""
        try:
            self.cursor.execute("SELECT width, height FROM map WHERE name = ?", (self.map_name,))
            result = self.cursor.fetchone()
            if not result:
                print(f"错误：找不到名为 '{self.map_name}' 的地图")
                return None
            return result  # 返回 (width, height)
        except Exception as e:
            print(f"获取地图尺寸时出错: {e}")
            return None
    
    def 获取同层建筑区(self):
        """获取同一层的所有建筑区"""
        try:
            self.cursor.execute('''
            SELECT name, position, type, corner, size FROM building_areas 
            WHERE map_name = ? AND layer = ?
            ''', (self.map_name, self.layer))
            
            结果 = []
            for row in self.cursor.fetchall():
                name, position_str, type, corner_str, size_str = row
                
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
                    结果.append({
                        "name": name,
                        "type": type,
                        "position": position,
                        "radius": radius
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
                        
                    结果.append({
                        "name": name,
                        "type": type,
                        "position": position,
                        "corner": corner,
                        "size": size
                    })
            
            return 结果
        except Exception as e:
            print(f"获取同层建筑区时出错: {e}")
            return []
    
    def 检查形状重叠(self, 形状, 类型="polygon"):
        """
        检查任意形状是否与现有建筑区重叠
        
        参数:
            形状: 对于圆形是(圆心,半径)元组，对于多边形是顶点列表
            类型: "circle"或"polygon"
        """
        # 将输入转换为Shapely对象
        if 类型 == "circle":
            圆心, 半径 = 形状
            检测形状 = Point(圆心).buffer(半径)
        else:
            检测形状 = Polygon(形状)
        
        # 获取同层建筑区
        同层建筑区 = self.获取同层建筑区()
        
        # 检查与所有建筑区的重叠
        for 建筑区 in 同层建筑区:
            if 建筑区["name"] == self.建筑区名:
                continue
                
            if 建筑区["type"] == "circle":
                圆心 = 建筑区["position"]
                半径 = 建筑区["radius"]
                其他形状 = Point(圆心).buffer(半径)
            else:
                多边形顶点 = 建筑区["corner"]
                if not 多边形顶点 or not isinstance(多边形顶点, list) or len(多边形顶点) < 3:
                    continue
                其他形状 = Polygon(多边形顶点)
                
            if 检测形状.intersects(其他形状):
                return True
        
        return False

    def 保存建筑区到数据库(self, 名称, 地图名称, 层索引, 位置, 类型, 角点, 大小数据=None):
        """
        将建筑区数据保存到数据库
        
        参数:
            名称: 建筑区名称
            地图名称: 所属地图名称
            层索引: 所在层索引
            位置: 建筑区位置坐标
            类型: 建筑区类型
            角点: 建筑区角点数据
            大小数据: 建筑区大小数据
            
        返回:
            成功返回True，失败返回False
        """
        try:
            # 确保名称不重复
            self.cursor.execute("SELECT name FROM building_areas WHERE name = ?", (名称,))
            if self.cursor.fetchone():
                print(f"建筑区名称 '{名称}' 已存在")
                return False
            
            # 准备位置和角点数据
            位置_str = json.dumps(位置)
            角点_str = json.dumps(角点) if not isinstance(角点, (int, float, str)) else str(角点)
            大小_str = json.dumps(大小数据) if 大小数据 else "{}"
            
            # 从大小数据中获取面积
            面积 = 0
            if 大小数据 and "area" in 大小数据:
                面积 = float(大小数据["area"])
            
            # 插入数据
            self.cursor.execute('''
            INSERT INTO building_areas
            (name, map_name, layer, position, type, corner, size, area, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (名称, 地图名称, 层索引, 位置_str, 类型, 角点_str, 大小_str, 面积))
            
            self.conn.commit()
            print(f"建筑区 '{名称}' 保存成功")
            return True
            
        except Exception as e:
            print(f"保存建筑区时出错: {e}")
            return False

    def 处理建筑参数(self, 建筑区名=None, map_name=None, 层=None):
        """
        处理建筑区的基本参数，用于各种建筑区生成器的通用处理
        
        参数:
            建筑区名: 建筑区名称，None时使用原有值
            map_name: 地图名称，None时使用原有值
            层: 可以是单个层或层范围的元组(min_layer, max_layer)
            
        返回:
            (是否跨层, min_layer, max_layer, 层列表, 地图宽度, 地图高度)
            若出错则返回None
        """
        # 更新建筑区名和地图名
        if 建筑区名 is not None:
            self.建筑区名 = 建筑区名
            
        if map_name is not None:
            self.map_name = map_name
        
        if self.map_name is None:
            print("错误: 未指定地图名称")
            return None
            
        # 处理层参数
        是否跨层 = False
        min_layer = max_layer = 0
        
        if 层 is None:
            层列表 = [self.layer]  # 使用默认层
            min_layer = max_layer = self.layer
        elif isinstance(层, tuple) and len(层) == 2:
            # 处理层范围 - 表示一个跨层建筑
            min_layer, max_layer = 层
            是否跨层 = min_layer != max_layer
            self.layer = min_layer  # 使用最小层作为主层
            层列表 = [min_layer] if 是否跨层 else list(range(min_layer, max_layer + 1))
        elif isinstance(层, int):
            # 单个层
            层列表 = [层]
            min_layer = max_layer = 层
            self.layer = 层
        else:
            print(f"错误: 无效的层参数 {层}")
            return None
            
        # 获取地图尺寸
        地图大小 = self.获取地图大小()
        if not 地图大小:
            print(f"错误: 无法获取地图 '{self.map_name}' 的尺寸")
            return None
            
        地图宽度, 地图高度 = 地图大小
        
        return 是否跨层, min_layer, max_layer, 层列表, 地图宽度, 地图高度
    
    def 生成建筑区名称(self, 建筑区名, 是否跨层, min_layer, max_layer):
        """
        根据建筑区名和层级信息生成完整的建筑区名称
        
        参数:
            建筑区名: 基础建筑区名称
            是否跨层: 是否是跨层建筑
            min_layer: 最小层
            max_layer: 最大层
            
        返回:
            完整的建筑区名称
        """
        if 是否跨层:
            return f"{建筑区名}_层{min_layer}至{max_layer}"
        else:
            return f"{建筑区名}_层{min_layer}"
    
    def 添加跨层信息(self, 数据字典, 是否跨层, min_layer, max_layer):
        """
        为数据字典添加跨层建筑的层级信息
        
        参数:
            数据字典: 要添加信息的字典
            是否跨层: 是否是跨层建筑
            min_layer: 最小层
            max_layer: 最大层
            
        返回:
            更新后的字典
        """
        if 是否跨层:
            数据字典["min_layer"] = min_layer
            数据字典["max_layer"] = max_layer
            数据字典["is_multi_layer"] = True
        
        return 数据字典

    def 处理大小参数(self, 宽度范围, 高度范围=None, dist="指数分布"):
        """处理大小范围参数，根据分布生成实际大小"""
        # 兼容旧的调用方式(传入元组)和新的调用方式(分别传入宽度和高度范围)
        if 高度范围 is None and isinstance(宽度范围, tuple) and len(宽度范围) == 2:
            # 旧的调用方式，宽度范围是((min_width, min_height), (max_width, max_height))
            min_size, max_size = 宽度范围
            min_width, min_height = min_size
            max_width, max_height = max_size
        else:
            # 新的调用方式，分别传入宽度和高度范围
            min_width, max_width = 宽度范围
            min_height, max_height = 高度范围
        
        if dist == '均匀分布':
            return np.random.randint(min_width, max_width + 1), np.random.randint(min_height, max_height + 1)
        elif dist == '指数分布':
            width = int(np.random.exponential(scale=(max_width - min_width) / 3) + min_width)
            height = int(np.random.exponential(scale=(max_height - min_height) / 3) + min_height)
            return max(min_width, min(width, max_width)), max(min_height, min(height, max_height))
        else:
            raise ValueError(f"不支持的分布类型: {dist}")

    def 检查多层重叠(self, 形状, 类型, min_layer, max_layer):
        """在多个层上检查形状是否重叠"""
        原始层 = self.layer
        重叠 = False
        
        for 层索引 in range(min_layer, max_layer + 1):
            self.layer = 层索引
            重叠 = self.检查形状重叠(形状, 类型)
            if 重叠:
                break
        
        # 恢复原始层
        self.layer = 原始层
        return 重叠

    def 创建建筑区基础逻辑(self, 建筑区名, map_name, 层, 尝试次数, 生成形状函数, 保存函数):
        """
        建筑区创建的通用逻辑
        
        参数:
            生成形状函数: 生成建筑区形状的函数，返回顶点列表和形状信息
            保存函数: 保存建筑区的函数
        """
        # 处理建筑参数
        参数结果 = self.处理建筑参数(建筑区名, map_name, 层)
        if 参数结果 is None:
            return []
        
        是否跨层, min_layer, max_layer, 层列表, 地图宽度, 地图高度 = 参数结果
        
        # 生成建筑区名
        建筑区全名 = self.生成建筑区名称(建筑区名, 是否跨层, min_layer, max_layer)
        
        # 尝试随机放置
        for _ in range(尝试次数):
            # 生成形状
            顶点, 形状信息 = 生成形状函数(地图宽度, 地图高度)
            
            # 检查是否与其他建筑区重叠
            是否重叠 = self.检查多层重叠(顶点, "polygon", min_layer, max_layer)
            
            if 是否重叠:
                continue
            
            # 保存到数据库
            return 保存函数(建筑区全名, map_name, 是否跨层, min_layer, max_layer, 形状信息, 顶点)
        
        return []

class 圆塔建筑区生成器(建筑区生成器):
    def __init__(self, 建筑区名=None, map_name=None, layer=None, 圆心位置=None, 半径=None, 建筑区大小=None):
        """
        初始化圆塔建筑区生成器
        
        参数:
            建筑区名: 建筑区名称（可选）
            map_name: 所属地图名称（可选）
            layer: 所在层（可选）
            圆心位置: 圆的中心坐标，格式为(x, y)（可选）
            半径: 圆的半径，可以是整数或浮点数（可选）
            建筑区大小: 可选参数，对于圆形建筑不需要指定
        """
        # 设置圆形建筑区的属性
        建筑区类型 = "circle"
        
        # 如果没有提供必要参数，使用默认值初始化，具体值将在create_building_area中设置
        if 建筑区名 is None:
            建筑区名 = "圆塔"
        if layer is None:
            layer = 0
        if 圆心位置 is None:
            圆心位置 = (0, 0)  # 默认位置，将在创建时覆盖
        if 半径 is None:
            半径 = 10  # 默认半径，将在创建时覆盖
        
        # 调用父类初始化方法
        super().__init__(建筑区名, map_name, layer, 圆心位置, 建筑区类型, 半径, 建筑区大小)
        
    def create_building_area(self, 建筑区名=None, map_name=None, 层=None, 半径范围=None, 尝试次数=5):
        """
        创建圆塔建筑区
        
        参数:
            建筑区名: 建筑区名称，默认使用初始化时的名称
            map_name: 地图名称，默认使用初始化时的地图名称
            层: 单个层索引或层索引范围元组 (最小层, 最大层)，如果是范围则创建跨层建筑
            半径范围: (最小半径, 最大半径) 的元组，若最小值等于最大值则使用固定半径
            尝试次数: 寻找合适位置的最大尝试次数，默认为5次
            
        返回:
            成功创建的建筑区列表
        """
        # 处理建筑参数
        参数结果 = self.处理建筑参数(建筑区名, map_name, 层)
        if 参数结果 is None:
            return []
            
        是否跨层, min_layer, max_layer, 层列表, 地图宽度, 地图高度 = 参数结果
        
        # 处理半径范围
        if 半径范围 is None:
            min_radius = max_radius = self.建筑区角点 or 10
        elif isinstance(半径范围, tuple) and len(半径范围) == 2:
            min_radius, max_radius = 半径范围
        else:
            print(f"错误: 无效的半径范围参数 {半径范围}")
            return []
            
        # 计算地图短边的1/4作为最大可能半径
        短边 = min(地图宽度, 地图高度)
        最大可能半径 = 短边 // 4
        
        # 限制半径在合理范围内
        min_radius = max(3, min(min_radius, 最大可能半径))
        max_radius = max(min_radius, min(max_radius, 最大可能半径))
        
        # 创建建筑区
        成功创建的建筑区 = []
        
        # 若是跨层建筑，只处理一次；否则为每层创建独立建筑区
        处理的层 = [min_layer] if 是否跨层 else 层列表
        
        for 当前层 in 处理的层:
            self.layer = 当前层
            
            半径合格 = False

            # 确定半径
            if min_radius == max_radius:
                实际半径 = min_radius
            else:
                # 使用NumPy的随机函数生成半径
                if max_radius <= 5:
                    实际半径 = np.random.randint(min_radius, max_radius + 1)
                elif min_radius <= 5:
                    # 使用NumPy的三角分布
                    实际半径 = int(np.random.triangular(min_radius, 5, max_radius))
                else:
                    # 使用NumPy的指数分布
                    if 半径合格 == False:
                        实际半径 = int(np.random.exponential(scale=(max_radius - min_radius) / 4) + min_radius)
                        if 实际半径 >= min_radius and 实际半径 <= max_radius:
                            半径合格 = True
                        else:
                            半径合格 = False
            
            # 生成建筑区名
            层建筑区名 = self.生成建筑区名称(self.建筑区名, 是否跨层, min_layer, max_layer)
            
            # 查找可用位置，最多尝试指定次数
            有效位置 = None
            for _ in range(尝试次数):  # 默认为5次尝试
                # 根据半径调整可能的圆心位置范围(确保不超出边界)
                min_x = 实际半径
                max_x = 地图宽度 - 实际半径
                min_y = 实际半径
                max_y = 地图高度 - 实际半径
                
                # 如果范围无效，放弃
                if min_x >= max_x or min_y >= max_y:
                    print(f"警告: 半径 {实际半径} 太大，无法在地图内放置圆塔")
                    break
                
                # 使用NumPy生成随机圆心位置
                圆心x = np.random.randint(min_x, max_x + 1)
                圆心y = np.random.randint(min_y, max_y + 1)
                圆心 = (int(圆心x), int(圆心y))
                
                # 检查是否与圆形建筑区重叠
                圆形重叠 = self.检查形状重叠((圆心, 实际半径), "circle")
                
                # 检查是否与多边形建筑区重叠
                多边形重叠 = self.检查形状重叠((圆心, 实际半径), "circle")
                
                # 如果没有任何重叠，则位置有效
                if not 圆形重叠 and not 多边形重叠:
                    有效位置 = 圆心
                    break
            
            if 有效位置 is None:
                print(f"警告: 在{尝试次数}次尝试后，无法在层 {当前层} 找到合适的位置放置圆塔")
                continue
                
            # 更新圆心位置和半径
            self.建筑区位置 = 有效位置
            self.建筑区角点 = 实际半径
            
            # 计算圆的面积
            圆面积 = np.pi * 实际半径 * 实际半径
            
            # 使用父类的保存方法保存到数据库
            大小数据 = {"radius": 实际半径, "area": 圆面积}
            
            # 添加跨层信息
            大小数据 = self.添加跨层信息(大小数据, 是否跨层, min_layer, max_layer)
            
            保存成功 = self.保存建筑区到数据库(
                名称=层建筑区名,
                地图名称=self.map_name,
                层索引=当前层,
                位置=有效位置,
                类型=self.建筑区类型,
                角点=实际半径,
                大小数据=大小数据
            )
            
            if 保存成功:
                print(f"圆塔建筑区 '{层建筑区名}' 创建成功，圆心位置: {有效位置}，半径: {实际半径}")
                
                建筑区信息 = {
                    "name": 层建筑区名,
                    "position": 有效位置,
                    "radius": 实际半径
                }
                
                # 添加层级信息
                建筑区信息 = self.添加跨层信息(建筑区信息, 是否跨层, min_layer, max_layer)
                if not 是否跨层:
                    建筑区信息["layer"] = 当前层
                
                成功创建的建筑区.append(建筑区信息)
                return 成功创建的建筑区
        
        return 成功创建的建筑区
    
    def create_angle_building_area(self, 建筑区名, map_name, 层, 矩形大小, angle, dist, 尝试次数):
        """
        创建带角度的矩形建筑区
        在角度列表中随机选择一个角度，然后旋转矩形
        随机放置，使用检查多边形与多边形重叠，检查多边形与圆形重叠
        
        参数:
            建筑区名: 建筑区名称
            map_name: 地图名称
            层: 层级参数，可以是单个整数或层级范围元组
            矩形大小: 格式为[(minwidth, minheight),(maxwidth, maxheight)]
            angle: 角度列表或范围，如[(-30,30),45,90]或True(使用默认角度[0,30,45,60])
            尝试次数: 尝试放置的最大次数
            
        返回:
            成功创建的建筑区列表
        """
        # 初始化成功创建的建筑区列表
        成功创建的建筑区 = []
        
        # 处理建筑参数
        参数结果 = self.处理建筑参数(建筑区名, map_name, 层)
        if 参数结果 is None:
            return []
            
        是否跨层, min_layer, max_layer, 层列表, 地图宽度, 地图高度 = 参数结果
        
        # 生成建筑区名
        建筑区全名 = self.生成建筑区名称(建筑区名, 是否跨层, min_layer, max_layer)
        
        # 尝试放置建筑区
        for _ in range(尝试次数):
            # 处理角度参数
            if not isinstance(angle, list) and angle is True:  
                所选角度 = random.choice([0, 30, 45, 60])  # 默认角度
            else:
                角度项 = random.choice(angle)  # 默认角度
                if isinstance(角度项, tuple) and len(角度项) == 2:
                    # 角度范围，例如 (-30, 30)
                    min_angle, max_angle = 角度项
                    所选角度 = random.randint(min_angle, max_angle)  # 直接随机选取一个整数
                else:
                    所选角度 = 角度项
            # 处理矩形大小
            min_width, min_height = 矩形大小[0]
            max_width, max_height = 矩形大小[1]

            # 生成矩形尺寸（随机浮点数）
            width, height = self.处理大小参数((min_width, max_width), (min_height, max_height), dist)
            
            # 生成矩形中心位置
            # 考虑旋转后的矩形可能超出边界，所以缩小可选范围
            安全边距 = max(width, height) / 1.5  # 考虑旋转后的对角线
            
            center_x = np.random.randint(安全边距, 地图宽度 - 安全边距)
            center_y = np.random.randint(安全边距, 地图高度 - 安全边距)
            
            # 生成未旋转时的矩形顶点（以中心为基准）
            半宽 = width / 2
            半高 = height / 2
            
            # 矩形四个顶点，顺时针方向
            顶点 = [
                (center_x - 半宽, center_y - 半高),  # 左上
                (center_x + 半宽, center_y - 半高),  # 右上
                (center_x + 半宽, center_y + 半高),  # 右下
                (center_x - 半宽, center_y + 半高),  # 左下
            ]
            
            # 对矩形进行旋转（围绕中心点）
            旋转顶点 = []
            角度弧度 = np.radians(所选角度)
            
            for x, y in 顶点:
                # 平移到原点
                x_shifted = x - center_x
                y_shifted = y - center_y
                
                # 旋转
                x_rotated = x_shifted * np.cos(角度弧度) - y_shifted * np.sin(角度弧度)
                y_rotated = x_shifted * np.sin(角度弧度) + y_shifted * np.cos(角度弧度)
                
                # 平移回去
                x_final = x_rotated + center_x
                y_final = y_rotated + center_y
                
                旋转顶点.append((x_final, y_final))
            
            # 检查是否在地图范围内
            超出边界 = False
            for x, y in 旋转顶点:
                if x < 0 or x > 地图宽度 or y < 0 or y > 地图高度:
                    超出边界 = True
                    break
            
            if 超出边界:
                continue  # 超出边界，重新尝试
            
            # 检查是否与其他建筑区重叠
            是否重叠 = False
            
            # 检查与圆形和多边形的重叠
            for 层索引 in range(min_layer, max_layer + 1):
                self.layer = 层索引  # 临时设置当前层
                # 检查与圆形的重叠
                是否重叠 = self.检查形状重叠(旋转顶点)
                if 是否重叠:
                    break
                
                # 检查与多边形的重叠
                是否重叠 = self.检查形状重叠(旋转顶点)
                if 是否重叠:
                    break
            
            if 是否重叠:
                continue  # 有重叠，重新尝试
            
            # 重置当前层为主层
            self.layer = min_layer
            
            # 没有重叠，找到了合适的位置
            # 计算面积
            矩形面积 = width * height
            
            # 准备保存数据
            大小数据 = {
                "width": width,
                "height": height,
                "angle": 所选角度,
                "area": 矩形面积,
                "center": (center_x, center_y)
            }
            
            # 添加跨层信息
            大小数据 = self.添加跨层信息(大小数据, 是否跨层, min_layer, max_layer)
            
            # 保存到数据库
            保存成功 = self.保存建筑区到数据库(
                名称=建筑区全名,
                地图名称=map_name,
                层索引=min_layer,  # 使用最小层作为主层
                位置=(center_x, center_y),  # 使用中心点作为位置
                类型="polygon",  # 类型为多边形
                角点=旋转顶点,  # 保存旋转后的顶点
                大小数据=大小数据
            )
            
            if 保存成功:
                if 是否跨层:
                    print(f"旋转矩形建筑区 '{建筑区全名}' 创建成功，跨越{min_layer}至{max_layer}层，角度: {所选角度}°，大小: {width}x{height}")
                else:
                    print(f"旋转矩形建筑区 '{建筑区全名}' 创建成功，角度: {所选角度}°，大小: {width}x{height}")
                
                建筑区信息 = {
                    "name": 建筑区全名,
                    "position": (center_x, center_y),
                    "width": width,
                    "height": height,
                    "angle": 所选角度,
                    "corners": 旋转顶点
                }
                
                # 添加层级信息
                建筑区信息 = self.添加跨层信息(建筑区信息, 是否跨层, min_layer, max_layer)
                if not 是否跨层:
                    建筑区信息["layer"] = min_layer
                
                成功创建的建筑区.append(建筑区信息)
                return 成功创建的建筑区
        
        # 如果所有尝试都失败
        print(f"警告: 在{尝试次数}次尝试后，无法找到合适的位置放置旋转矩形建筑区")
        return []
    
class 矩形建筑区生成器(建筑区生成器):
    def __init__(self, 建筑区名=None, map_name=None, layer=None, 矩形位置=None, 矩形大小=None):
        """
        初始化矩形建筑区生成器
        
        参数:
           建筑区名: 建筑区名称，默认使用初始化时的名称
            map_name: 地图名称，默认使用初始化时的地图名称
            layer: 所在层，默认使用初始化时的层
           矩形位置: 矩形左上角坐标，格式为(x, y)
           矩形大小: 矩形宽度，格式为(width, height)
        """
        # 设置矩形建筑区的属性 
        建筑区类型 = "rectangle"

        # 调用父类初始化方法
        super().__init__(建筑区名, map_name, layer, 矩形位置, 建筑区类型, 矩形大小, 矩形大小)

    def create_building_area(self, 建筑区名=None, map_name=None, 层=None, 矩形大小=None, angle=False, dist = "指数分布", 尝试次数=5):
        """
        创建矩形建筑区
        
        参数:
           建筑区名: 建筑区名称，默认使用初始化时的名称
            map_name: 地图名称，默认使用初始化时的地图名称
           层: 所在层，默认使用初始化时的层
           矩形大小: 矩形宽度，格式为[(minwidth, minheight),(maxwidth, maxheight)]
           angle: 是否旋转矩形，默认False, 否则为角度的列表如[(-30,30),45,90]则为从-30到30度，以及45度和90度可以生成，如果设置为True,则角度列表为[0,30,45,60]
           尝试次数: 寻找合适位置的最大尝试次数，默认为5次
        
        返回:
            成功创建的建筑区列表    
        """
        # 处理建筑参数
        参数结果 = self.处理建筑参数(建筑区名, map_name, 层)
        if 参数结果 is None:
            return []
            
        是否跨层, min_layer, max_layer, 层列表, 地图宽度, 地图高度 = 参数结果

        if 矩形大小[1][0] > 地图宽度 or 矩形大小[1][1] > 地图高度:
            矩形大小[1][0] = 地图宽度
            矩形大小[1][1] = 地图高度
        
        if angle is False:
            return self.create_normal_building_area(建筑区名, map_name, 层, 矩形大小, dist, 尝试次数)
        else:
            return self.create_angle_building_area(建筑区名, map_name, 层, 矩形大小, angle, dist, 尝试次数)
    
    def create_normal_building_area(self, 建筑区名, map_name, 层, 矩形大小, dist, 尝试次数):
        """
        创建普通矩形建筑区
        随机放置，使用检查多边形与多边形重叠，检查多边形与圆形重叠
        如果到达尝试次数仍然没有合适的位置，则使用数学包计算空缺最大的区域，
        并在最大的区域中将maxwidth和maxheight设置为那个空缺最大的区域所能塞下的最大的矩形的位置，
        并且在该最大的矩形区域内生成符合条件的矩形建筑区
        """
        # 处理建筑参数
        参数结果 = self.处理建筑参数(建筑区名, map_name, 层)
        if 参数结果 is None:
            return []
            
        是否跨层, min_layer, max_layer, 层列表, 地图宽度, 地图高度 = 参数结果
        
        # 生成建筑区名
        建筑区全名 = self.生成建筑区名称(建筑区名, 是否跨层, min_layer, max_layer)

        # 阶段1: 尝试随机放置
        for _ in range(尝试次数):
            # 使用generate_room_size生成矩形尺寸
            width, height = self.generate_room_size(min_layer, 矩形大小, dist)
            
            # 随机选择左上角位置
            left = np.random.randint(0, 地图宽度 - width + 1)
            top = np.random.randint(0, 地图高度 - height + 1)
            
            # 计算四个顶点
            顶点 = [
                (left, top),  # 左上
                (left + width, top),  # 右上
                (left + width, top + height),  # 右下
                (left, top + height),  # 左下
            ]
            
            # 中心点
            center_x = left + width / 2
            center_y = top + height / 2
            
            # 检查是否与其他建筑区重叠
            是否重叠 = False
            for 层索引 in range(min_layer, max_layer + 1):
                self.layer = 层索引  # 临时设置当前层
                # 检查与圆形的重叠
                是否重叠 = self.检查形状重叠(顶点)
                if 是否重叠:
                    break
                
                # 检查与多边形的重叠
                是否重叠 = self.检查形状重叠(顶点)
                if 是否重叠:
                    break
            
            if 是否重叠:
                continue  # 有重叠，重新尝试
            
            # 没有重叠，找到了合适的位置
            成功建筑区 = self.保存普通矩形建筑区(建筑区全名, map_name, 是否跨层, min_layer, max_layer, center_x, center_y, width, height, 顶点)
            if 成功建筑区:
                return 成功建筑区
        
        # 获取地图短边长度
        地图短边 = min(地图宽度, 地图高度)
        
        # 阶段1.5: 如果地图短边小于200，尝试遍历整个地图寻找可行位置
        if 地图短边 < 200:
            print(f"随机放置{尝试次数}次失败，地图短边小于200，开始遍历搜索...")
            
            # 生成一个固定的房间尺寸用于搜索
            width, height = self.generate_room_size(min_layer, 矩形大小, dist)
            
            # 设置遍历步长，在小地图上不需要逐像素检查
            步长 = max(5, min(10, int(地图短边 / 20)))
            
            # 存储所有可行的位置
            可行位置列表 = []
            
            # 遍历整个地图的格子
            for left in range(0, 地图宽度 - width + 1, 步长):
                for top in range(0, 地图高度 - height + 1, 步长):
                    # 计算四个顶点
                    顶点 = [
                        (left, top),  # 左上
                        (left + width, top),  # 右上
                        (left + width, top + height),  # 右下
                        (left, top + height),  # 左下
                    ]
                    
                    # 中心点
                    center_x = left + width / 2
                    center_y = top + height / 2
                    
                    # 检查是否与其他建筑区重叠
                    是否重叠 = False
                    for 层索引 in range(min_layer, max_layer + 1):
                        self.layer = 层索引  # 临时设置当前层
                        是否重叠 = self.检查形状重叠(顶点)
                        if 是否重叠:
                            break
                    
                    if not 是否重叠:
                        # 没有重叠，这是一个可行位置
                        可行位置列表.append((left, top, width, height, 顶点, center_x, center_y))
            
            # 如果找到了可行位置，随机选择一个
            if 可行位置列表:
                print(f"通过遍历搜索找到 {len(可行位置列表)} 个可行位置，随机选择一个...")
                # 随机选择一个位置
                left, top, width, height, 顶点, center_x, center_y = random.choice(可行位置列表)
                
                # 保存建筑区
                成功建筑区 = self.保存普通矩形建筑区(建筑区全名, map_name, 是否跨层, min_layer, max_layer, center_x, center_y, width, height, 顶点)
                if 成功建筑区:
                    return 成功建筑区
            else:
                print("遍历搜索未找到可行位置，继续寻找最大空闲区域...")
        
        # 阶段2: 计算最大空闲矩形区域
        print(f"开始计算最大空闲区域...")
        
        # 创建表示整个地图的多边形
        地图多边形 = Polygon([
            (0, 0),
            (地图宽度, 0),
            (地图宽度, 地图高度),
            (0, 地图高度)
        ])
        
        # 收集所有层的所有建筑区
        所有建筑区形状 = []
        for 层索引 in range(min_layer, max_layer + 1):
            self.layer = 层索引
            同层建筑区 = self.获取同层建筑区()
            
            for 建筑区 in 同层建筑区:
                if 建筑区["type"] == "circle":
                    # 圆形建筑区
                    圆心 = 建筑区["position"]
                    半径 = 建筑区["radius"]
                    圆形 = Point(圆心).buffer(半径)
                    所有建筑区形状.append(圆形)
                elif 建筑区["type"] in ["rectangle", "polygon"]:
                    # 矩形或多边形建筑区
                    if "corner" in 建筑区 and 建筑区["corner"]:
                        多边形顶点 = 建筑区["corner"]
                        if isinstance(多边形顶点, list) and len(多边形顶点) >= 3:
                            多边形 = Polygon(多边形顶点)
                            所有建筑区形状.append(多边形)
        
        # 计算可用空间
        可用空间 = 地图多边形
        for 形状 in 所有建筑区形状:
            可用空间 = 可用空间.difference(形状)
        
        # 如果可用空间是多多边形，需要找到最大的一块
        if 可用空间.geom_type == 'MultiPolygon':
            最大多边形 = max(可用空间.geoms, key=lambda p: p.area)
        else:
            最大多边形 = 可用空间
        
        # 获取最大多边形的边界矩形
        minx, miny, maxx, maxy = 最大多边形.bounds
        
        # 边界矩形的宽度和高度
        边界宽度 = maxx - minx
        边界高度 = maxy - miny
        
        # 限制边界矩形的大小不超过给定的最大尺寸
        边界宽度 = min(边界宽度, 矩形大小[1][0])
        边界高度 = min(边界高度, 矩形大小[1][1])
        
        # 确保边界矩形不小于最小尺寸
        if 边界宽度 < 矩形大小[0][0] or 边界高度 < 矩形大小[0][1]:
            print(f"警告: 可用空间过小，无法放置至少 {矩形大小[0][0]}x{矩形大小[0][1]} 的矩形")
            return []
        
        # 构建新的矩形大小范围，考虑空闲区域的约束
        新矩形大小 = [
            (矩形大小[0][0], 矩形大小[0][1]),  # 原始最小尺寸
            (min(矩形大小[1][0], int(边界宽度)), min(矩形大小[1][1], int(边界高度)))  # 受限的最大尺寸
        ]
        
        # 在边界矩形内随机生成一个符合大小要求的矩形
        for _ in range(尝试次数):
            # 使用generate_room_size生成房间尺寸
            width, height = self.generate_room_size(min_layer, 新矩形大小, dist)
            
            # 随机选择左上角位置（确保在边界矩形内）
            left = np.random.randint(int(minx), int(maxx - width + 1))
            top = np.random.randint(int(miny), int(maxy - height + 1))
            
            # 计算四个顶点
            顶点 = [
                (left, top),  # 左上
                (left + width, top),  # 右上
                (left + width, top + height),  # 右下
                (left, top + height),  # 左下
            ]
            
            # 中心点
            center_x = left + width / 2
            center_y = top + height / 2
            
            # 创建矩形多边形
            矩形 = Polygon(顶点)
            
            # 检查是否完全在可用空间内
            if 矩形.within(最大多边形):
                # 没有重叠，找到了合适的位置
                成功建筑区 = self.保存普通矩形建筑区(建筑区全名, map_name, 是否跨层, min_layer, max_layer, center_x, center_y, width, height, 顶点)
                if 成功建筑区:
                    return 成功建筑区
        
        # 如果所有尝试都失败
        print(f"警告: 在计算最大空闲区域后，依然无法找到合适的位置放置矩形建筑区")
        return []
    
    def 保存普通矩形建筑区(self, 建筑区名, map_name, 是否跨层, min_layer, max_layer, center_x, center_y, width, height, 顶点):
        """保存普通矩形建筑区到数据库并返回建筑区信息"""
        # 重置当前层为主层
        self.layer = min_layer
        
        # 计算面积
        矩形面积 = width * height
        
        # 准备保存数据
        大小数据 = {
            "width": width,
            "height": height,
            "area": 矩形面积,
            "center": (center_x, center_y)
        }
        
        # 添加跨层信息
        大小数据 = self.添加跨层信息(大小数据, 是否跨层, min_layer, max_layer)
        
        # 保存到数据库
        保存成功 = self.保存建筑区到数据库(
            名称=建筑区名,
            地图名称=map_name,
            层索引=min_layer,  # 使用最小层作为主层
            位置=(center_x, center_y),  # 使用中心点作为位置
            类型="rectangle",  # 类型为矩形
            角点=顶点,  # 保存顶点
            大小数据=大小数据
        )
        
        if 保存成功:
            if 是否跨层:
                print(f"矩形建筑区 '{建筑区名}' 创建成功，跨越{min_layer}至{max_layer}层，大小: {width}x{height}")
            else:
                print(f"矩形建筑区 '{建筑区名}' 创建成功，大小: {width}x{height}")
            
            建筑区信息 = {
                "name": 建筑区名,
                "position": (center_x, center_y),
                "width": width,
                "height": height,
                "corners": 顶点
            }
            
            # 添加层级信息
            建筑区信息 = self.添加跨层信息(建筑区信息, 是否跨层, min_layer, max_layer)
            if not 是否跨层:
                建筑区信息["layer"] = min_layer
            
            return [建筑区信息]  # 返回列表以保持与其他方法一致
        
        return []  # 保存失败返回空列表
    
    def create_angle_building_area(self, 建筑区名, map_name, 层, 矩形大小, angle, dist, 尝试次数):
        """
        创建带角度的矩形建筑区
        在角度列表中随机选择一个角度，然后旋转矩形
        随机放置，使用检查多边形与多边形重叠，检查多边形与圆形重叠
        
        参数:
            建筑区名: 建筑区名称
            map_name: 地图名称
            层: 层级参数，可以是单个整数或层级范围元组
            矩形大小: 格式为[(minwidth, minheight),(maxwidth, maxheight)]
            angle: 角度列表或范围，如[(-30,30),45,90]或True(使用默认角度[0,30,45,60])
            dist: 分布类型，"均匀分布"或"指数分布"
            尝试次数: 尝试放置的最大次数
            
        返回:
            成功创建的建筑区列表
        """
        # 初始化成功创建的建筑区列表
        成功创建的建筑区 = []
        
        # 处理建筑参数
        参数结果 = self.处理建筑参数(建筑区名, map_name, 层)
        if 参数结果 is None:
            return []
            
        是否跨层, min_layer, max_layer, 层列表, 地图宽度, 地图高度 = 参数结果
        
        # 生成建筑区名
        建筑区全名 = self.生成建筑区名称(建筑区名, 是否跨层, min_layer, max_layer)
        
        # 处理角度参数
        if not isinstance(angle, list) and angle is True:  
            角度列表 = [0, 30, 45, 60]  # 默认角度
        elif isinstance(angle, list):
            角度列表 = []
            for 角度项 in angle:
                if isinstance(角度项, tuple) and len(角度项) == 2:
                    # 角度范围，例如 (-30, 30)
                    min_angle, max_angle = 角度项
                    角度列表.extend(list(range(min_angle, max_angle + 1, 5)))  # 每5度一个角度
                else:
                    角度列表.append(角度项)
        else:
            角度列表 = [0]  # 默认不旋转
        
        # 尝试放置建筑区
        for 尝试 in range(尝试次数):
            # 随机选择一个角度
            所选角度 = random.choice(角度列表)
            
            # 使用generate_room_size多次生成房间尺寸，选择最合适的一组
            候选尺寸列表 = []
            
            # 生成5组候选尺寸
            for _ in range(5):
                width, height = self.generate_room_size(min_layer, 矩形大小, dist)
                比例 = width / height if height > 0 else float('inf')
                比例差 = abs(比例 - 1.0)  # 越接近1越好
                面积 = width * height
                候选尺寸列表.append((width, height, 比例差, 面积))
            
            # 根据策略选择最终尺寸
            # 策略1: 优先选择比例合理的房间(40%概率)
            if random.random() < 0.4:
                候选尺寸列表.sort(key=lambda x: x[2])  # 按比例差排序
                width, height = 候选尺寸列表[0][0], 候选尺寸列表[0][1]
            # 策略2: 优先选择小房间(30%概率)
            elif random.random() < 0.5:
                候选尺寸列表.sort(key=lambda x: x[3])  # 按面积排序
                width, height = 候选尺寸列表[0][0], 候选尺寸列表[0][1]
            # 策略3: 随机选择一组(30%概率)
            else:
                随机索引 = random.randint(0, len(候选尺寸列表) - 1)
                width, height = 候选尺寸列表[随机索引][0], 候选尺寸列表[随机索引][1]
            
            # 生成矩形中心位置
            # 考虑旋转后的矩形可能超出边界，所以缩小可选范围
            对角线长度 = math.sqrt(width**2 + height**2)
            安全边距 = 对角线长度 / 2  # 对角线的一半
            
            # 确保安全边距不会导致无法放置
            if 安全边距 * 2 >= 地图宽度 or 安全边距 * 2 >= 地图高度:
                安全边距 = min(地图宽度, 地图高度) / 4
            
            # 计算有效的放置范围
            有效左边界 = max(int(安全边距), 0)
            有效右边界 = max(int(地图宽度 - 安全边距), 有效左边界 + 1)
            有效上边界 = max(int(安全边距), 0)
            有效下边界 = max(int(地图高度 - 安全边距), 有效上边界 + 1)
            
            # 随机生成中心点
            center_x = np.random.randint(有效左边界, 有效右边界)
            center_y = np.random.randint(有效上边界, 有效下边界)
            
            # 生成未旋转时的矩形顶点（以中心为基准）
            半宽 = width / 2
            半高 = height / 2
            
            # 矩形四个顶点，顺时针方向
            顶点 = [
                (center_x - 半宽, center_y - 半高),  # 左上
                (center_x + 半宽, center_y - 半高),  # 右上
                (center_x + 半宽, center_y + 半高),  # 右下
                (center_x - 半宽, center_y + 半高),  # 左下
            ]
            
            # 对矩形进行旋转（围绕中心点）
            旋转顶点 = []
            角度弧度 = np.radians(所选角度)
            
            for x, y in 顶点:
                # 平移到原点
                x_shifted = x - center_x
                y_shifted = y - center_y
                
                # 旋转
                x_rotated = x_shifted * np.cos(角度弧度) - y_shifted * np.sin(角度弧度)
                y_rotated = x_shifted * np.sin(角度弧度) + y_shifted * np.cos(角度弧度)
                
                # 平移回去
                x_final = x_rotated + center_x
                y_final = y_rotated + center_y
                
                旋转顶点.append((x_final, y_final))
            
            # 检查是否在地图范围内
            超出边界 = False
            for x, y in 旋转顶点:
                if x < 0 or x > 地图宽度 or y < 0 or y > 地图高度:
                    超出边界 = True
                    break
            
            if 超出边界:
                continue  # 超出边界，重新尝试
            
            # 检查是否与其他建筑区重叠
            是否重叠 = False
            
            # 检查与圆形和多边形的重叠
            for 层索引 in range(min_layer, max_layer + 1):
                self.layer = 层索引  # 临时设置当前层
                是否重叠 = self.检查形状重叠(旋转顶点)
                if 是否重叠:
                    break
            
            if 是否重叠:
                continue  # 有重叠，重新尝试
            
            # 重置当前层为主层
            self.layer = min_layer
            
            # 没有重叠，找到了合适的位置
            # 计算面积
            矩形面积 = width * height
            
            # 准备保存数据
            大小数据 = {
                "width": width,
                "height": height,
                "angle": 所选角度,
                "area": 矩形面积,
                "center": (center_x, center_y)
            }
            
            # 添加跨层信息
            大小数据 = self.添加跨层信息(大小数据, 是否跨层, min_layer, max_layer)
            
            # 保存到数据库
            保存成功 = self.保存建筑区到数据库(
                名称=建筑区全名,
                地图名称=map_name,
                层索引=min_layer,  # 使用最小层作为主层
                位置=(center_x, center_y),  # 使用中心点作为位置
                类型="polygon",  # 类型为多边形
                角点=旋转顶点,  # 保存旋转后的顶点
                大小数据=大小数据
            )
            
            if 保存成功:
                if 是否跨层:
                    print(f"旋转矩形建筑区 '{建筑区全名}' 创建成功，跨越{min_layer}至{max_layer}层，角度: {所选角度}°，大小: {width}x{height}")
                else:
                    print(f"旋转矩形建筑区 '{建筑区全名}' 创建成功，角度: {所选角度}°，大小: {width}x{height}")
                
                建筑区信息 = {
                    "name": 建筑区全名,
                    "position": (center_x, center_y),
                    "width": width,
                    "height": height,
                    "angle": 所选角度,
                    "corners": 旋转顶点
                }
                
                # 添加层级信息
                建筑区信息 = self.添加跨层信息(建筑区信息, 是否跨层, min_layer, max_layer)
                if not 是否跨层:
                    建筑区信息["layer"] = min_layer
                
                成功创建的建筑区.append(建筑区信息)
                return 成功创建的建筑区
        
        # 如果所有尝试都失败
        print(f"警告: 在{尝试次数}次尝试后，无法找到合适的位置放置旋转矩形建筑区")
        return []
    
    def generate_room_size(self, layer, 房间大小范围, dist='指数分布'):
        """根据指定的分布生成房间大小"""
        if isinstance(房间大小范围, list) and len(房间大小范围) == 2:
            # 标准格式：((min_width, min_height), (max_width, max_height))
            min_size, max_size = 房间大小范围
            rm = [min_size[0], min_size[1]]
            rx = [max_size[0], max_size[1]]
        else:
            print("警告：房间大小范围格式不正确，使用默认值")
            rm = [5, 5]  # 默认最小尺寸
            rx = [30, 30]  # 默认最大尺寸
        
        for _ in range(100):
            if dist == '均匀分布':
                h = random.randint(rm[0], rx[0])
                w = random.randint(rm[1], rx[1])
            elif dist == '指数分布':
                h = int(np.random.exponential(scale=(rx[0] - rm[0]) / 4) + rm[0])
                w = int(np.random.exponential(scale=(rx[1] - rm[1]) / 4) + rm[1])
            else:
                raise ValueError(f"不支持的分布类型: {dist}")
            
            if rx[0] > h > 0 and rx[1] > w > 0:
                ratio = w / h
                if random.random() > 0.7 or (ratio < 2 and ratio > 0.5):
                    return h, w
        
        # 如果无法生成合适的房间大小，返回最小值
        return rm[0], rm[1]

    def 处理角度参数(self, angle):
        """处理角度参数，返回角度列表或随机选择的角度"""
        if not isinstance(angle, list) and angle is True:  
            return [0, 30, 45, 60]  # 默认角度
        
        角度列表 = []
        for 角度项 in angle:
            if isinstance(角度项, tuple) and len(角度项) == 2:
                min_angle, max_angle = 角度项
                角度列表.extend(range(min_angle, max_angle + 1, 15))
            else:
                角度列表.append(角度项)
        
        return 角度列表

class 地图生成器:
    def __init__(self, name="地牢", size=(100, 100), layer=1):
        """
        初始化地图生成器
        
        参数:
            name: 地图名称，如果提供则作为默认地图名
            size: 地图大小，格式为(width, height)
            layer: 地图层数
        """
        self.size = size  # 地图大小 (width, height)
        self.layer = layer  # 层数
        self.map_name = name  # 地图名称
        
        # 连接数据库
        self.conn = sqlite3.connect('dungeon.db')
        self.cursor = self.conn.cursor()
        
        # 确保map表存在
        self.create_tables()
        
        # 如果提供了名称，可以立即创建地图
        if name is not None:
            print(f"已设置地图名称: {name}")
    
    def create_tables(self):
        """创建必要的数据库表"""
        # 创建map表，name为主键
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS map (
            name TEXT PRIMARY KEY,
            key TEXT,
            width INTEGER,
            height INTEGER,
            layers INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        self.conn.commit()
    
    def create_map(self, name=None, size=None, layer=None):
        """
        创建地图并在数据库中注册
        
        参数:
            name: 地图名称，如果为None则使用初始化时提供的名称或自动生成
            size: 地图大小，格式为(width, height)，如果为None则使用初始化时的大小
            layer: 地图层数，如果为None则使用初始化时的层数
            
        返回:
            创建的地图名称
        """
        # 优先使用参数提供的值，其次使用初始化时设置的值
        if size is None:
            size = self.size
        else:
            self.size = size  # 更新实例属性
            
        if layer is None:
            layer = self.layer
        else:
            self.layer = layer  # 更新实例属性
            
        # 优先使用参数提供的名称，其次使用初始化时设置的名称
        if name is None:
            name = self.map_name
        
        # 获取地图大小和层数
        width, height = size
        
        # 如果还是没有名称，自动生成一个不重复的名称
        if name is None:
            # 查询现有地图数量
            self.cursor.execute("SELECT COUNT(*) FROM map")
            count = self.cursor.fetchone()[0]
            timestamp = int(time.time())
            name = f"地图_{count+1}_{timestamp}"
        
        # 确保name不重复
        self.cursor.execute("SELECT name FROM map WHERE name = ?", (name,))
        if self.cursor.fetchone():
            # 如果已存在，添加时间戳后缀
            timestamp = int(time.time())
            name = f"{name}_{timestamp}"
            # 再次检查
            self.cursor.execute("SELECT name FROM map WHERE name = ?", (name,))
            if self.cursor.fetchone():
                # 如果还是重复，添加随机字符串
                random_suffix = uuid.uuid4().hex[:8]
                name = f"{name}_{random_suffix}"
        
        # 生成辅助的key值(不是主键，但可用于其他引用)
        key = str(uuid.uuid4())
        
        # 将地图信息插入数据库
        try:
            self.cursor.execute(
                "INSERT INTO map (name, key, width, height, layers) VALUES (?, ?, ?, ?, ?)",
                (name, key, width, height, layer)
            )
            self.conn.commit()
            
            self.map_name = name
            print(f"地图创建成功: {width}x{height}, {layer}层")
            print(f"地图名称: {name}")
            print(f"地图辅助key: {key}")
            
            return name
        except sqlite3.IntegrityError as e:
            # 名称已存在，尝试使用不同的名称
            print(f"名称 '{name}' 已存在，尝试使用新名称")
            return self.create_map(f"{name}_{uuid.uuid4().hex[:8]}", size, layer)
        except Exception as e:
            print(f"创建地图时出错: {e}")
            return None
    
    def load_map(self, name=None):
        """
        从数据库加载地图基本信息
        
        参数:
            name: 地图名称，如果为None则使用当前设置的地图名称
            
        返回:
            地图信息字典或None
        """
        try:
            # 如果没有提供名称，使用当前地图名称
            if name is None:
                name = self.map_name
                
            if name is None:
                print("未提供地图名称，且当前没有设置地图名称")
                return None
                
            self.cursor.execute("SELECT * FROM map WHERE name = ?", (name,))
            result = self.cursor.fetchone()
            
            if not result:
                print(f"未找到名称为 '{name}' 的地图")
                return None
            
            name, key, width, height, layers, created_at = result
            
            # 更新当前实例的属性
            self.size = (width, height)
            self.layer = layers
            self.map_name = name
            
            return {
                "name": name,
                "key": key,
                "width": width,
                "height": height,
                "layers": layers,
                "created_at": created_at
            }
            
        except Exception as e:
            print(f"加载地图时出错: {e}")
            return None
    
    def list_maps(self):
        """列出所有可用的地图"""
        try:
            self.cursor.execute("SELECT name, key, width, height, layers, created_at FROM map ORDER BY created_at DESC")
            maps = self.cursor.fetchall()
            
            if not maps:
                print("没有找到任何地图")
                return []
            
            print(f"找到 {len(maps)} 个地图:")
            for i, (name, key, width, height, layers, created_at) in enumerate(maps, 1):
                print(f"{i}. {name} - {width}x{height}, {layers}层 ({created_at})")
            
            return maps
        except Exception as e:
            print(f"列出地图时出错: {e}")
            return []
    
    def delete_map(self, name=None):
        """删除指定的地图"""
        try:
            # 如果没有提供名称，使用当前地图名称
            if name is None:
                name = self.map_name
                
            if name is None:
                print("未提供地图名称，且当前没有设置地图名称")
                return False
                
            self.cursor.execute("SELECT name FROM map WHERE name = ?", (name,))
            result = self.cursor.fetchone()
            
            if not result:
                print(f"未找到名称为 '{name}' 的地图")
                return False
            
            self.cursor.execute("DELETE FROM map WHERE name = ?", (name,))
            self.conn.commit()
            
            print(f"已删除地图 '{name}'")
            
            # 如果删除的是当前地图，清除当前地图名称
            if name == self.map_name:
                self.map_name = None
                
            return True
        except Exception as e:
            print(f"删除地图时出错: {e}")
            return False
    
    def __del__(self):
        """关闭数据库连接"""
        if hasattr(self, 'conn'):
            self.conn.close()

# 测试代码
if __name__ == "__main__":
    测试地图生成器 = True  # 设置为True时运行地图生成器测试
    测试圆塔建筑区生成器 = True  # 设置为True时运行圆塔建筑区生成器测试
    测试矩形建筑区生成器 = True  # 设置为True时运行矩形建筑区生成器测试

    if 测试地图生成器:
        # 测试1: 不指定名称创建地图
        print("\n==== 测试1: 不指定名称 ====")
        generator1 = 地图生成器()
        map_name1 = generator1.create_map(size=(200, 200), layer=3)
        
        # 列出所有地图
        generator1.list_maps()
        
        # 加载地图
        map_info1 = generator1.load_map("地牢")
        if map_info1:
            print(f"加载的地图信息: {map_info1}")
        
        # 测试2: 在实例化时指定名称
        print("\n==== 测试2: 实例化时指定名称 ====")
        generator2 = 地图生成器()
        map_name2 = generator2.create_map("地牢", size=(300, 300), layer=5)  # 应该使用"地牢"作为名称
        
        # 加载刚创建的地图
        map_info2 = generator2.load_map()  # 不需要指定名称，会使用当前地图名
        if map_info2:
            print(f"加载的地牢地图信息: {map_info2}")
            
        # 测试3: 指定名称创建另一个地图
        print("\n==== 测试3: 创建时指定不同名称 ====")
        custom_name = "我的测试地图"
        map_name3 = generator2.create_map(name=custom_name)  # 使用新名称创建地图，而不是初始化时的名称
        
        # 加载刚创建的地图
        map_info3 = generator2.load_map(name=map_name3)
        if map_info3:
            print(f"加载的自定义地图信息: {map_info3}")
        
        # 再次列出所有地图
        print("\n所有创建的地图:")
        generator2.list_maps()

    if 测试圆塔建筑区生成器:
        print("\n==== 测试圆塔建筑区生成器 ====")
        # 先确保有一个地图可用
        map_gen = 地图生成器()
        map_name = map_gen.create_map("地牢测试", size=(100, 200), layer=5)
        
        # 创建圆塔建筑区生成器
        circle = 圆塔建筑区生成器()
        
        # 在层1和层2上创建半径为10的圆塔
        建筑区列表 = circle.create_building_area("圆塔", "地牢测试", 层=(1, 2), 半径范围=(20, 35))       
        print(f"成功创建 {len(建筑区列表)} 个圆塔建筑区")

    if 测试矩形建筑区生成器:
        print("\n==== 测试矩形建筑区生成器 ====")
        # 先确保有一个地图可用
        map_gen = 地图生成器()
        map_name = map_gen.create_map("地牢测试", size=(100, 200), layer=5)
        
        # 创建矩形建筑区生成器
        矩形 = 矩形建筑区生成器()
        
        # 测试1: 使用角度列表创建倾斜矩形
        print("\n-- 测试1: 使用角度列表创建倾斜矩形 --")
        建筑区1 = 矩形.create_building_area(
            "旋转房间1", 
            "地牢测试", 
            层=1, 
            矩形大小=[(20, 10), (40, 30)],
            angle=[30, 45, 60],  # 指定角度列表
            dist="均匀分布",
            尝试次数=10
        )
        
        # 测试2: 使用角度范围创建倾斜矩形
        print("\n-- 测试2: 使用角度范围创建跨层倾斜矩形 --")
        建筑区2 = 矩形.create_angle_building_area(
            "旋转房间2", 
            "地牢测试", 
            层=(1, 2),  # 跨越1-2层
            矩形大小=[(15, 25), (35, 40)],
            angle=[(-30, 30)],  # 角度范围
            dist="指数分布",
            尝试次数=10
        )
        
        # 测试3: 使用默认角度创建倾斜矩形
        print("\n-- 测试3: 使用默认角度创建倾斜矩形 --")
        建筑区3 = 矩形.create_angle_building_area(
            "旋转房间3", 
            "地牢测试", 
            层=3, 
            矩形大小=[(25, 15), (45, 25)],
            angle=True,  # 使用默认角度列表
            dist="指数分布",
            尝试次数=10
        )
        
        # 测试4: 创建一个跨层的普通矩形建筑区
        print("\n-- 测试5: 创建跨层普通矩形建筑区 --")
        建筑区 = 矩形.create_normal_building_area(
            "三层洋房",
            "地牢测试",
            层=(1, 3),  # 1-3层
            矩形大小=[(20, 30), (20, 30)],  # 20x20到30x30的大小范围
            dist="均匀分布",
            尝试次数=50
        )
        
        if 建筑区:
            print("成功创建跨层矩形建筑区 '三层洋房'")
            print(f"建筑区信息: {建筑区[0]}")
        else:
            print("创建跨层矩形建筑区失败")
        
        # 测试5: 创建40个普通矩形建筑区
        print("\n-- 测试4: 创建普通矩形建筑区 --")
        成功计数 = 0
        for i in range(40):  
            建筑区名 = f"矩形房间_{i+1}"
            建筑区 = 矩形.create_normal_building_area(
                建筑区名,
                "地牢测试",
                层=1,  # 随机分配到1层
                矩形大小=[(5, 5), (70, 70)],  # 5x5到70x70的大小范围
                dist="指数分布",
                尝试次数=50
            )
            
            if 建筑区:
                成功计数 += 1
                print(f"成功创建矩形建筑区 {建筑区名}")
        
        print(f"\n总共成功创建 {成功计数}/40 个普通矩形建筑区")

        


