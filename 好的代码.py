import numpy as np
import cmd
import random
import json
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import sqlite3

class Cell:
    def __init__(self, x, y, 类型="外部", 温度=0, 建筑区=None, 房间=None, 物品=None, 生态=None):
        self.x = x  # 行索引
        self.y = y  # 列索引
        self.类型 = 类型  # 外部, 墙壁, 室内等
        self.温度 = 温度  # 影响生态
        self.建筑区 = 建筑区  # 所属建筑区
        self.房间 = 房间  # 所属房间
        self.物品 = 物品  # 该单元上的物品
        self.生态 = 生态  # 该单元所属的生态
        self.可通行 = True  # 是否可通行
        self.高度 = 0  # 单元高度

class 物品:
    def __init__(self, 名称, x, y):
        self.名称 = 名称  # 物品名称，例如石笋、桌子
        self.x = x  # 物品的起始 x 坐标
        self.y = y  # 物品的起始 y 坐标
        self.占据的格子 = []  # 物品占据的 Cell 坐标列表
        self.高度 = 1  # 物品高度

class 房间:
    def __init__(self, 类型, 建筑区):
        self.类型 = 类型  # 房间类型，例如"卧室"、"大厅"
        self.建筑区 = 建筑区  # 该房间所属的建筑区
        self.建筑的格子 = []  # 该房间占据的格子
        self.门 = []  # 房间的门位置
        self.物品 = []  # 房间内的物品列表
        self.是否可通行 = True  # 影响 A* 算法
        self.楼梯 = False  # 是否有楼梯

class 建筑区:
    def __init__(self, 左上角x, 左上角y, 宽度, 高度, 层, 类型, 名字=None):
        self.左上角x = 左上角x  # 行索引
        self.左上角y = 左上角y  # 列索引
        self.宽度 = 宽度
        self.高度 = 高度
        self.层 = 层  # 该建筑区所属的层
        self.类型 = 类型  # 建筑区类型，决定房间生成规则
        self.房间列表 = []  # 该建筑区的房间
        self.门 = []  # 该建筑区的门
        self.名字 = 名字  # 建筑区名称，例如"建筑区1"
        self.占据的格子 = []  # 该建筑区覆盖的格子坐标

class 生态:
    def __init__(self, 类型, 层):
        self.类型 = 类型  # 生态类型，例如"森林"、"洞穴"
        self.层 = 层  # 生态所属的层
        self.温度 = 0  # 默认温度 0 度
        self.湿度 = 50  # 湿度影响生态
        self.地形高度 = 0  # 影响建筑分布
        self.占据的格子 = []  # 该生态区域覆盖的格子

class 层:
    def __init__(self, 行数: int, 列数: int, 高度: float):
        self.行数 = 行数  # 层的行数
        self.列数 = 列数  # 层的列数
        self.高度 = 高度  # 该层的高度
        self.网格 = [[Cell(x, y) for x in range(列数)] for y in range(行数)]  # 该层的格子
        self.建筑区 = []  # 该层的建筑区
        self.生态区 = []  # 该层的生态
        self.房间 = []  # 该层的所有房间

class 地图:
    def __init__(self, 宽度, 高度, 层数):
        self.地图宽度 = 宽度
        self.地图高度 = 高度
        self.层列表 = [层(高度, 宽度, i * 10) for i in range(层数)]
        self.建筑区 = []
        self.生态 = []
        self.温度场 = np.zeros((高度, 宽度))
        self.路径网格 = np.ones((高度, 宽度), dtype=bool)
        
        # 添加房间生成所需的属性
        self.rows = 高度
        self.cols = 宽度
        self.layers = 层数
        
        # 初始化网格
        self.grid = np.zeros((层数, 高度, 宽度), dtype=int)
        self.grid_status = np.zeros((层数, 高度, 宽度), dtype=int)
        self.placed_rooms = [[] for _ in range(层数)]
        self.room_params = {}  # 将在生成建筑区函数中设置
        self.建筑区计数 = 0  # 用于自动生成建筑区名称

        # Connect to the database
        self.conn = sqlite3.connect('dungeon.db')
        self.cursor = self.conn.cursor()

        # Insert map data into the database
        self.map_id = self.insert_map(宽度, 高度, 层数)

    def insert_map(self, width, height, layers):
        self.cursor.execute('INSERT INTO Maps (width, height, layers) VALUES (?, ?, ?)', (width, height, layers))
        self.conn.commit()
        return self.cursor.lastrowid

    def insert_layer(self, map_id, height):
        self.cursor.execute('INSERT INTO Layers (map_id, height) VALUES (?, ?)', (map_id, height))
        self.conn.commit()
        return self.cursor.lastrowid

    def insert_building_area(self, layer_id, name, x, y, width, height):
        self.cursor.execute('INSERT INTO BuildingAreas (layer_id, name, x, y, width, height) VALUES (?, ?, ?, ?, ?, ?)', 
                           (layer_id, name, x, y, width, height))
        self.conn.commit()
        return self.cursor.lastrowid

    def insert_room(self, building_area_id, type, x, y, width, height):
        self.cursor.execute('INSERT INTO Rooms (building_area_id, type, x, y, width, height) VALUES (?, ?, ?, ?, ?, ?)', 
                           (building_area_id, type, x, y, width, height))
        self.conn.commit()
        return self.cursor.lastrowid

    def 显示地图信息(self):
        print(f"地图大小：{self.地图宽度} x {self.地图高度}")
        print(f"层数：{len(self.层列表)}")
        print(f"建筑区数量：{len(self.建筑区)}")
        print(f"生态区数量：{len(self.生态)}")

    def 查询层(self, 层索引):
        if 0 <= 层索引 < len(self.层列表):
            print(f"层 {层索引} - {self.地图宽度}x{self.地图高度}, 高度 {self.层列表[层索引].高度}")
        else:
            print("错误：无效的层索引！")
    
    def 生成地图(self):
        #包括层数及地图大小，2，1000, 1000代表生成两层，每层大小为1000*1000
        self.层列表 = [层(1000, 1000, i * 10) for i in range(2)]
        print("地图生成完成！")

    def 生成建筑区(self, 参数列表=None):
        """
        生成建筑区函数，根据参数在指定的层创建建筑区
        
        参数列表格式示例：
        [
            ((1,2,3), ((10,20), (100,200), 10, "均匀分布")),
            ((5,), ((10,20), (100,200), 10, "指数分布"))
        ]
        第一个元组包含要创建建筑区的层索引
        第二个元组包含房间参数：(最小尺寸元组, 最大尺寸元组, 房间数量, 分布类型)
        """
        if 参数列表 is None:
            参数列表 = [
                ((0,), ((10,10), (30,30), 5, "uniform")),  # 默认参数
            ]
        
        for 层组合, 房间参数 in 参数列表:
            # 设置room_params
            最小尺寸, 最大尺寸, 房间数量, 分布类型 = 房间参数
            分布类型_英文 = "uniform" if 分布类型 == "均匀分布" else "指数分布"
            
            for 层索引 in 层组合:
                # 确保层索引有效
                if 层索引 >= self.layers:
                    # 如果该层不存在，输出警告而不创建新层
                    print(f"警告：第{层索引}层不存在，无法在该层创建建筑区。")
                    continue
                
                # 为该层设置room_params
                self.room_params[层索引] = (最小尺寸, 最大尺寸, 房间数量, 分布类型_英文)
                
                # 生成建筑区并检查重叠
                self.generate_building_areas(层索引, 房间数量, 最小尺寸, 最大尺寸)
        
        print("建筑区生成完成！")
    
    def generate_building_areas(self, layer, count, min_size, max_size):
        """在指定层生成不重叠的建筑区"""
        current_layer = self.层列表[layer]
        layer_id = self.insert_layer(self.map_id, current_layer.高度)
        
        # 尝试放置建筑区
        for _ in range(count):
            # 随机生成建筑区尺寸
            h = random.randint(min_size[0], max_size[0])
            w = random.randint(min_size[1], max_size[1])
            
            # 找出可能的放置位置
            valid_positions = self.get_valid_building_positions(layer, h, w)
            
            if not valid_positions:
                print(f"警告：无法在第{layer}层放置{h}x{w}大小的建筑区，可用空间不足。")
                continue
            
            # 随机选择位置
            row, col = random.choice(valid_positions)
            
            # 创建新建筑区
            self.建筑区计数 += 1
            new_building = self.place_building_area(layer, row, col, h, w, f"建筑区{self.建筑区计数}")
            
            # 插入建筑区数据到数据库
            building_area_id = self.insert_building_area(layer_id, new_building.名字, row, col, w, h)
            
            # 添加到地图和当前层
            self.建筑区.append(new_building)
            current_layer.建筑区.append(new_building)
    
    def get_valid_building_positions(self, layer, h, w):
        """获取有效的建筑区位置"""
        valid_positions = []
        
        for row in range(self.rows - h):
            for col in range(self.cols - w):
                # 检查是否与现有建筑区重叠
                overlap = False
                
                for building in self.建筑区:
                    if building.层 != layer:
                        continue
                    
                    # 检查矩形重叠
                    if (row < building.左上角x + building.高度 and 
                        row + h > building.左上角x and
                        col < building.左上角y + building.宽度 and
                        col + w > building.左上角y):
                        overlap = True
                        break
                
                if not overlap:
                    valid_positions.append((row, col))
        
        return valid_positions
    
    def place_building_area(self, layer, row, col, h, w, name):
        """放置建筑区并更新格子属性"""
        # 创建新建筑区
        new_building = 建筑区(row, col, w, h, layer, "普通", name)
        
        # 获取当前层
        current_layer = self.层列表[layer]
        
        # 更新格子属性
        for r in range(row, row + h):
            for c in range(col, col + w):
                if 0 <= r < self.rows and 0 <= c < self.cols:
                    # 更新Cell的建筑区属性
                    cell = current_layer.网格[r][c]
                    cell.建筑区 = new_building
                    
                    # 记录格子坐标
                    new_building.占据的格子.append((r, c))
        
        return new_building
    
    def place_room(self, layer, row, col, h, w):
        """在指定位置放置一个房间"""
        # 获取当前层
        current_layer = self.层列表[layer]
        
        # 检查该位置是否在建筑区内
        cell = current_layer.网格[row][col]
        building = cell.建筑区
        
        # 创建新房间
        new_room = 房间("普通", building)
        
        # 设置外墙
        for rr in range(row, row + h):
            for cc in range(col, col + w):
                if 0 <= rr < self.rows and 0 <= cc < self.cols:
                    # 更新Cell属性
                    grid_cell = current_layer.网格[rr][cc]
                    
                    # 设置为墙壁
                    if rr == row or rr == row+h-1 or cc == col or cc == col+w-1:
                        grid_cell.类型 = "墙壁"
                        self.grid[layer, rr, cc] = self.CELL_TYPE["墙壁"]
                    else:
                        # 内部为室内
                        grid_cell.类型 = "室内"
                        self.grid[layer, rr, cc] = self.CELL_TYPE["室内"]
                    
                    grid_cell.房间 = new_room
                    new_room.建筑的格子.append((rr, cc))
        
        # 添加到建筑区的房间列表
        if building:
            building.房间列表.append(new_room)
            
            # 插入房间数据到数据库
            self.insert_room(building_area_id=building.名字, type="普通", x=row, y=col, width=w, height=h)
        
        # 添加到层的房间列表
        current_layer.房间.append(new_room)
        
        # 记录放置的房间
        self.placed_rooms[layer].append((row, col, h, w))
        
        return new_room
    

    def generate_room_size(self, layer):
        """根据指定的分布生成房间大小"""
        rm, rx, _, dist = self.room_params[layer]
        for _ in range(100):
            if dist == '均匀分布':
                h = random.randint(rm[0], rx[0])
                w = random.randint(rm[1], rx[1])
            elif dist == '指数分布':
                h = int(np.random.exponential(scale=(rx[0] - rm[0]) / 3) + rm[0])
                w = int(np.random.exponential(scale=(rx[1] - rm[1]) / 3) + rm[1])
            else:
                raise ValueError(f"不支持的分布类型: {dist}")
            
            if h > 0 and w > 0:
                ratio = w / h
                if random.random() > 0.7 or (ratio < 2 and ratio > 0.5):
                    return h, w
        
    
    def get_all_valid_positions(self, layer, h, w):
        """获取所有可以放置房间的有效位置"""
        valid_positions = []
        for row in range(self.rows - h):
            for col in range(self.cols - w):
                # 检查周围的边界 (增加2格缓冲区)
                t = max(0, row - 2)
                l = max(0, col - 2)
                b = min(self.rows, row + h + 2)
                r = min(self.cols, col + w + 2)
                
                # 获取检查区域
                box = self.grid[layer, t:b, l:r]
                
                # 如果区域内有任何非空格子，则跳过
                if np.any(box != self.CELL_TYPE["外部"]):
                    continue
                
                valid_positions.append((row, col))
        
        return valid_positions

    def 为每一层生成建筑区(self):
        """为每一层生成房间"""
        for layer in range(self.layers):
            # 如果该层没有设置room_params，跳过
            if layer not in self.room_params:
                continue
            
            rm, rx, rc, _ = self.room_params[layer]
            tries = 100  # 尝试次数
            c = 0
            
            # 获取当前层的建筑区列表
            current_layer = self.层列表[layer]
            building_areas = current_layer.建筑区
            
            if not building_areas:
                print(f"警告：第{layer}层没有建筑区，无法生成房间。")
                continue
            
            # 清除旧房间
            for building in building_areas:
                building.房间列表.clear()
            current_layer.房间.clear()
            self.placed_rooms[layer].clear()
            
            # 为每个建筑区生成房间
            for building in building_areas:
                # 确定要在该建筑区生成的房间数量
                # 根据建筑区面积比例分配房间数量
                area_ratio = (building.宽度 * building.高度) / (self.rows * self.cols)
                building_rooms = max(1, int(rc * area_ratio))
                
                # 在建筑区内尝试放置房间
                ok = True
                for _ in range(building_rooms):
                    placed = False
                    for _a in range(4):  # 每个房间尝试4次不同的大小
                        # 生成房间大小
                        h, w = self.generate_room_size(layer)
                        
                        # 限制房间大小不超过建筑区
                        h = min(h, building.高度 - 2)  # 留出边界
                        w = min(w, building.宽度 - 2)
                        
                        if h < 3 or w < 3:  # 确保房间至少有3x3大小
                            continue
                        
                        # 在建筑区内寻找有效的房间位置
                        valid_positions = []
                        
                        for r in range(building.左上角x, building.左上角x + building.高度 - h):
                            for c in range(building.左上角y, building.左上角y + building.宽度 - w):
                                # 检查是否与现有房间重叠
                                overlap = False
                                for room_r, room_c, room_h, room_w in self.placed_rooms[layer]:
                                    if (r < room_r + room_h and 
                                        r + h > room_r and 
                                        c < room_c + room_w and 
                                        c + w > room_c):
                                        overlap = True
                                        break
                                
                                if not overlap:
                                    valid_positions.append((r, c))
                        
                        if not valid_positions:
                            continue
                        
                        # 随机选择一个位置放置房间
                        row, col = random.choice(valid_positions)
                        self.place_room(layer, row, col, h, w)
                        placed = True
                        break
                    
                    if not placed:
                        ok = False
                
                if not ok:
                    print(f"警告：无法在{building.名字}放置所有房间。")
            
            
            # 统计结果
            total_rooms = len(current_layer.房间)
            print(f"第{layer}层共生成{total_rooms}个房间。")


    def __del__(self):
        # Close the database connection
        self.conn.close()

class 渲染:
    def __init__(self):
        self.层数据 = []  # 需要渲染的层
        self.建筑区数据 = []  # 需要渲染的建筑区
        self.生态数据 = []  # 需要渲染的生态
        self.房间数据 = []  # 需要渲染的房间
        self.物品数据 = []  # 需要渲染的物品
        self.可视化模式 = "建筑"  # 默认渲染建筑
        self.颜色映射 = {}  # 颜色分类


class 交互界面(cmd.Cmd):
    intro = "欢迎使用地图交互界面！输入 'help' 查看可用命令。\n"
    prompt = "(地图) "

    def __init__(self):
        super().__init__()
        self.command_dict = {
            "初始化地图": self.初始化地图,
            "显示地图信息": self.显示地图信息,
            "查询层": self.查询层,
            "生成建筑区": self.生成建筑区,
            "生成房间": self.生成房间,
            "显示层房间": self.显示层房间,
            "帮助": self.帮助
        }

    def default(self, line):
        cmd_parts = line.split()
        if not cmd_parts:
            return
        
        cmd = cmd_parts[0]
        args = ' '.join(cmd_parts[1:])
        
        if cmd in self.command_dict:
            self.command_dict[cmd](args)
        else:
            print(f"未知命令: {cmd}，输入 '帮助' 查看可用命令")

    def 初始化地图(self, args):
        """ 初始化地图，格式：初始化地图 宽度 高度 层数 """
        try:
            参数 = args.split()
            if len(参数) != 3:
                print("格式错误！请输入：初始化地图 宽度 高度 层数")
                return
            宽度, 高度, 层数 = map(int, 参数)
            global world_map
            world_map = 地图(宽度, 高度, 层数)
            print("地图初始化完成！")
        except ValueError:
            print("请输入正确的数字！")

    def 显示地图信息(self, args):
        """ 显示当前地图的信息 """
        if world_map:
            world_map.显示地图信息()
        else:
            print("请先初始化地图！")

    def 查询层(self, args):
        """ 查询某一层的信息，格式：查询层 层索引 """
        try:
            层索引 = int(args)
            if world_map:
                world_map.查询层(层索引)
            else:
                print("请先初始化地图！")
        except ValueError:
            print("请输入有效的层索引！")
    
    def 生成建筑区(self, args):
        """ 生成建筑区，格式：生成建筑区 [参数] """
        if not world_map:
            print("请先初始化地图！")
            return
        
        try:
            # 如果没有参数，使用默认值
            if not args.strip():
                # 默认在第0层生成3个建筑区
                参数列表 = [((0,), ((10,10), (30,30), 3, "uniform"))]
                world_map.生成建筑区(参数列表)
                return
            
            # 解析复杂参数
            参数列表 = []
            
            # 尝试解析用户输入，这里简化为直接使用一些示例参数
            if args.strip().lower() == "默认":
                参数列表 = [((0,), ((10,10), (30,30), 3, "uniform"))]
            elif args.strip().lower() == "多层":
                # 在第0层和第1层分别生成3个建筑区
                参数列表 = [
                    ((0,), ((10,10), (30,30), 3, "uniform")),
                    ((1,), ((15,15), (40,40), 3, "uniform"))
                ]
            elif args.strip().lower().startswith("层"):
                try:
                    # 格式: "层 0 3" 表示在第0层生成3个建筑区
                    parts = args.strip().split()
                    if len(parts) >= 3:
                        层索引 = int(parts[1])
                        建筑区数量 = int(parts[2])
                        参数列表 = [((层索引,), ((10,10), (30,30), 建筑区数量, "uniform"))]
                    else:
                        print("参数格式错误，示例：层 0 3")
                        return
                except (ValueError, IndexError):
                    print("参数解析错误")
                    return
            else:
                print("参数格式不支持，使用默认参数")
                参数列表 = [((0,), ((10,10), (30,30), 3, "uniform"))]
            
            world_map.生成建筑区(参数列表)
        except Exception as e:
            print(f"生成建筑区时出错: {e}")
    
    def 生成房间(self, args):
        """ 在已有建筑区内生成房间，格式：生成房间 [层索引] """
        if not world_map:
            print("请先初始化地图！")
            return
        
        try:
            层索引 = 0  # 默认第0层
            if args.strip():
                层索引 = int(args)
            
            if 层索引 >= world_map.layers:
                print(f"错误：第{层索引}层不存在")
                return
            
            # 检查该层是否有建筑区
            current_layer = world_map.层列表[层索引]
            if not current_layer.建筑区:
                print(f"错误：第{层索引}层没有建筑区，请先生成建筑区")
                return
            
            # 设置房间生成参数
            if 层索引 not in world_map.room_params:
                world_map.room_params[层索引] = ((3,3), (10,10), 5, "uniform")
            
            # 生成房间
            world_map.generate_rooms()
            
        except ValueError:
            print("请输入有效的层索引！")
        except Exception as e:
            print(f"生成房间时出错: {e}")
    
    def 显示层房间(self, args):
        """ 显示某一层的房间布局，格式：显示层房间 [层索引] """
        if not world_map:
            print("请先初始化地图！")
            return
        
        try:
            层索引 = 0  # 默认显示第0层
            if args.strip():
                层索引 = int(args)
            
            world_map.显示层房间(层索引)
        except ValueError:
            print("请输入有效的层索引！")
        except Exception as e:
            print(f"显示层房间时出错: {e}")
    
    def 帮助(self, args=None):
        """ 显示帮助信息 """
        print("\n可用命令:")
        print("  初始化地图 宽度 高度 层数 - 创建新地图")
        print("  显示地图信息 - 显示当前地图的基本信息")
        print("  查询层 层索引 - 查看指定层的详细信息")
        print("  生成建筑区 [参数] - 在地图上生成建筑区")
        print("     - 默认: 在第0层生成3个建筑区")
        print("     - 多层: 在第0层和第1层各生成3个建筑区")
        print("     - 层 [层索引] [数量]: 在指定层生成指定数量的建筑区")
        print("  生成房间 [层索引] - 在指定层的建筑区内生成房间")
        print("  显示层房间 [层索引] - 可视化显示指定层的房间布局")
        print("  帮助 - 显示此帮助信息")
        print("  退出 - 退出程序\n")
        
    def do_exit(self, arg):
        """退出程序"""
        print("感谢使用，再见！")
        return True
    
    # 同义词命令
    def do_quit(self, arg):
        """退出程序 (同 exit)"""
        return self.do_exit(arg)
    
    def do_EOF(self, arg):
        """退出程序 (Ctrl+D)"""
        print()
        return self.do_exit(arg)

if __name__ == '__main__':
    world_map = None
    
    # 自动测试示例 (如果需要)
    def 运行测试():
        global world_map
        print("==== 开始自动测试 ====")
        
        # 1. 初始化地图
        print("\n1. 测试初始化地图")
        world_map = 地图(100, 100, 3)
        world_map.显示地图信息()
        
        # 2. 生成建筑区
        print("\n2. 测试生成建筑区")
        测试参数 = [
            ((0,), ((10,10), (30,30), 3, "uniform")),
            ((1,), ((15,15), (40,40), 2, "exponential"))
        ]
        world_map.生成建筑区(测试参数)
        print(f"第0层建筑区数量: {len(world_map.层列表[0].建筑区)}")
        print(f"第1层建筑区数量: {len(world_map.层列表[1].建筑区)}")
        
        # 3. 生成房间
        print("\n3. 测试生成房间")
        world_map.generate_rooms()
        
        # 4. 显示层房间
        print("\n4. 测试显示层房间 (第0层)")
        world_map.显示层房间(0)
        
        print("\n5. 测试显示层房间 (第1层)")
        world_map.显示层房间(1)
        
        print("\n==== 自动测试完成 ====")
    
    # 取消注释下面的行可以运行自动测试
    # 运行测试()
    
    # 启动交互界面
    交互界面().cmdloop()