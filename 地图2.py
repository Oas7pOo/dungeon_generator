import cmd
import sqlite3
import random
import numpy as np

# ============ 基础类 ============
class Cell:
    def __init__(self, x, y, 类型="外部"):
        self.x = x
        self.y = y
        self.类型 = 类型
        self.温度 = 0
        self.建筑区 = None
        self.房间 = None
        self.物品 = None
        self.生态 = None
        self.可通行 = True
        self.高度 = 0

class 物品:
    def __init__(self, 名称, x, y):
        self.名称 = 名称
        self.x = x
        self.y = y
        self.占据的格子 = []
        self.高度 = 1

class 房间:
    def __init__(self, 类型, 建筑区):
        self.类型 = 类型
        self.建筑区 = 建筑区
        self.建筑的格子 = []
        self.门 = []
        self.物品 = []
        self.是否可通行 = True
        self.楼梯 = False

# ============ 层类 ============
class 层:
    def __init__(self, 行数, 列数, 高度, layer_id=None):
        """
        :param 行数: 地图行数
        :param 列数: 地图列数
        :param 高度: 该层在Z轴的高度
        :param layer_id: 该层在数据库的ID
        """
        self.行数 = 行数
        self.列数 = 列数
        self.高度 = 高度
        self.网格 = [[Cell(r, c) for c in range(列数)] for r in range(行数)]
        self.建筑区列表 = []
        self.layer_id = layer_id  # 数据库中的主键ID

# ============ 建筑区类 ============
class 建筑区:
    def __init__(self, 坐标=(0,0), 范围, 层对象=None, 类型="普通", 名字=None, building_area_id=None):
        """
        :param 左上角x: 建筑区左上角行
        :param 左上角y: 建筑区左上角列
        :param 宽度:
        :param 高度:
        :param 层对象: 该建筑区所属层的对象 (type: 层)
        :param 类型: 建筑区类型
        :param 名字: 建筑区名称
        :param building_area_id: 数据库中的主键ID
        """
        self.左上角x = 左上角x
        self.左上角y = 左上角y
        self.宽度 = 宽度
        self.高度 = 高度
        self.层对象 = 层对象
        self.类型 = 类型
        self.名字 = 名字
        self.房间列表 = []
        self.building_area_id = building_area_id
        self.占据的格子 = []

    @staticmethod
    def generate_building_areas(地图对象, 层对象, count, min_size, max_size, name_prefix="建筑区"):
        """
        在指定层(层对象)上生成 count 个建筑区。  
        - 在这里实现**随机生成**、**坐标检查**、**DB插入**等逻辑。
        - 需要地图对象的数据库连接来插入数据。
        """

        layer_id = 层对象.layer_id
        行数, 列数 = 层对象.行数, 层对象.列数

        for i in range(count):
            # 随机生成宽高
            h = random.randint(min_size[0], max_size[0])
            w = random.randint(min_size[1], max_size[1])

            # 找到可行位置
            valid_positions = []
            for row in range(行数 - h):
                for col in range(列数 - w):
                    # 检查与现有建筑区是否重叠
                    overlap = False
                    for b in 层对象.建筑区列表:
                        if (row < b.左上角x + b.高度 and
                            row + h > b.左上角x and
                            col < b.左上角y + b.宽度 and
                            col + w > b.左上角y):
                            overlap = True
                            break
                    if not overlap:
                        valid_positions.append((row, col))

            if not valid_positions:
                print(f"[警告] 在层ID={layer_id}上找不到放置 {h}x{w} 的空间。跳过。")
                continue

            row, col = random.choice(valid_positions)
            building_name = f"{name_prefix}{i+1}"
            新建筑区 = 建筑区(row, col, w, h, 层对象, 类型="普通", 名字=building_name)

            # 更新 Cell 的建筑区引用
            for rr in range(row, row+h):
                for cc in range(col, col+w):
                    cell = 层对象.网格[rr][cc]
                    cell.建筑区 = 新建筑区
                    新建筑区.占据的格子.append((rr, cc))

            # 插入数据库
            building_area_id = 地图对象.insert_building_area(
                layer_id=layer_id,
                name=building_name,
                x=row,
                y=col,
                width=w,
                height=h
            )
            新建筑区.building_area_id = building_area_id

            层对象.建筑区列表.append(新建筑区)

# ============ 地图类 ============
class 地图:
    def __init__(self, 宽度, 高度, 层数):
        self.地图宽度 = 宽度
        self.地图高度 = 高度
        self.层数 = 层数

        # 数据库初始化
        self.conn = sqlite3.connect("dungeon.db")
        self.cursor = self.conn.cursor()

        # 初始化表（如果已存在则可省略，或判断已创建就跳过）
        self.init_tables()

        # 插入地图数据
        self.map_id = self.insert_map(宽度, 高度, 层数)

        # 初始化层对象，并插入到数据库
        self.层列表 = []
        for i in range(层数):
            # 这里的 行数、列数 可以与地图同大，也可以根据需求缩放
            行数 = 高度
            列数 = 宽度
            高度值 = i * 10

            layer_id = self.insert_layer(self.map_id, 高度值)
            layer_obj = 层(行数, 列数, 高度值, layer_id=layer_id)
            self.层列表.append(layer_obj)

    def init_tables(self):
        """初始化数据库表，简单示例，确保表存在"""
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS Maps(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                width INTEGER,
                height INTEGER,
                layers INTEGER
            );
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS Layers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                map_id INTEGER,
                height REAL,
                FOREIGN KEY(map_id) REFERENCES Maps(id)
            );
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS BuildingAreas(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                layer_id INTEGER,
                name TEXT,
                x INTEGER,
                y INTEGER,
                width INTEGER,
                height INTEGER,
                FOREIGN KEY(layer_id) REFERENCES Layers(id)
            );
        """)
        # 也可以创建房间表、物品表等
        self.conn.commit()

    def insert_map(self, width, height, layers):
        self.cursor.execute(
            "INSERT INTO Maps (width, height, layers) VALUES (?, ?, ?)",
            (width, height, layers)
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def insert_layer(self, map_id, height_value):
        self.cursor.execute(
            "INSERT INTO Layers (map_id, height) VALUES (?, ?)",
            (map_id, height_value)
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def insert_building_area(self, layer_id, name, x, y, width, height):
        self.cursor.execute("""
            INSERT INTO BuildingAreas (layer_id, name, x, y, width, height)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (layer_id, name, x, y, width, height))
        self.conn.commit()
        return self.cursor.lastrowid

    def 显示地图信息(self):
        print(f"地图大小：{self.地图宽度} x {self.地图高度}")
        print(f"层数：{self.层数}")

        # 查询数据库中的建筑区数量
        self.cursor.execute("""
            SELECT COUNT(*) FROM BuildingAreas
            WHERE layer_id IN (
                SELECT id FROM Layers WHERE map_id=?
            )
        """, (self.map_id,))
        building_count = self.cursor.fetchone()[0]

        print(f"已创建的建筑区数量：{building_count}")

    def 查询层(self, 层索引):
        if 0 <= 层索引 < len(self.层列表):
            layer_obj = self.层列表[层索引]
            print(f"层 {层索引} - 地图大小: {self.地图宽度}x{self.地图高度}, 高度 = {layer_obj.高度}")
            # 可以再查询该层的建筑区数量
            self.cursor.execute("""
                SELECT COUNT(*) FROM BuildingAreas
                WHERE layer_id=?
            """, (layer_obj.layer_id,))
            count = self.cursor.fetchone()[0]
            print(f"该层建筑区数量：{count}")
        else:
            print("错误：无效的层索引！")

    def 生成建筑区(self, 层索引=0, count=3, min_size=(10,10), max_size=(30,30)):
        """
        在指定层索引上，生成一定数量的建筑区
        """
        if not (0 <= 层索引 < self.层数):
            print("错误：层索引超出范围")
            return

        layer_obj = self.层列表[层索引]

        # 调用 建筑区 类中的生成方法
        建筑区.generate_building_areas(
            地图对象=self,
            层对象=layer_obj,
            count=count,
            min_size=min_size,
            max_size=max_size
        )
        print(f"[完成] 在第 {层索引} 层生成 {count} 个建筑区")

    def 查询建筑区(self):
        """
        查询并打印数据库中的所有建筑区信息
        """
        self.cursor.execute("""
            SELECT b.id, b.name, b.x, b.y, b.width, b.height, l.id as layer_id, l.height as layer_height
            FROM BuildingAreas b
            JOIN Layers l ON b.layer_id = l.id
            WHERE l.map_id=?
        """, (self.map_id,))
        rows = self.cursor.fetchall()
        print("[建筑区列表]")
        for row in rows:
            b_id, name, x, y, w, h, lid, lheight = row
            print(f"  - 建筑区ID={b_id}, 名称={name}, 坐标=({x},{y}), 尺寸=({w}x{h}), 所属层ID={lid}, 层高度={lheight}")

    def __del__(self):
        self.conn.close()