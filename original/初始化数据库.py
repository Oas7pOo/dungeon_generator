import sqlite3
import datetime
import os
import traceback

def 初始化数据库():
    """初始化数据库和必要的表"""
    # 如果数据库文件已存在，先删除它（可选）
    if os.path.exists('dungeon.db'):
        print("发现现有数据库，将重新创建...")
    
    # 连接到数据库（如果不存在会自动创建）
    conn = sqlite3.connect('dungeon.db')
    cursor = conn.cursor()
    
    try:
        print("正在创建数据库表...")
        
        # 创建建筑区表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS 建筑区 (
            名称 TEXT PRIMARY KEY,
            地图名 TEXT,
            层级开始 INTEGER,
            层级结束 INTEGER,
            类型 TEXT,
            位置 TEXT,
            角点 TEXT,
            层级 TEXT,
            大小数据 TEXT
        )
        ''')
        
        # 创建房间表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS 房间 (
            名称 TEXT PRIMARY KEY,
            地图名 TEXT,
            层开始 INTEGER,
            层结束 INTEGER,
            层名称 TEXT,
            建筑区 TEXT,
            墙格子列表 TEXT,
            空间格子列表 TEXT,
            内部墙格子列表 TEXT,
            房间类型 TEXT,
            矢量参数 TEXT,
            面积 REAL,
            其他参数 TEXT,
            FOREIGN KEY(建筑区) REFERENCES 建筑区(名称)
        )
        ''')
        
        # 创建层信息表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS 层信息 (
            地图名 TEXT,
            层索引 INTEGER,
            宽度 INTEGER,
            高度 INTEGER,
            栅格大小 REAL,
            PRIMARY KEY (地图名, 层索引)
        )
        ''')
        
        # 创建地图表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS map (
            name TEXT PRIMARY KEY,
            description TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        ''')
        
        # 创建道具/物品表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            map_name TEXT,
            layer_name TEXT,
            position TEXT,
            type TEXT,
            vector_params TEXT,
            properties TEXT,
            parent_item TEXT,
            container_room TEXT,
            FOREIGN KEY(container_room) REFERENCES 房间(名称)
        )
        ''')
        
        # 创建默认地图
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
        INSERT OR REPLACE INTO map (name, description, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ''', ('地牢测试', '测试用地牢地图', current_time, current_time))
        
        # 创建默认层信息
        for i in range(1, 6):  # 创建5个层级
            cursor.execute('''
            INSERT OR REPLACE INTO 层信息 (地图名, 层索引, 宽度, 高度, 栅格大小)
            VALUES (?, ?, ?, ?, ?)
            ''', ('地牢测试', i, 50, 50, 1.0))
        
        # 创建默认建筑区
        # 1. 旋转房间建筑区
        cursor.execute('''
        INSERT OR REPLACE INTO 建筑区 (名称, 地图名, 层级开始, 层级结束, 类型, 位置, 角点, 层级, 大小数据)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', ('旋转房间2_层1至2', '地牢测试', 1, 2, 'polygon', 
             json.dumps([20, 20]), 
             json.dumps([[15, 15], [25, 15], [25, 25], [15, 25]]), 
             json.dumps([1, 2]), 
             json.dumps({"area": 100, "is_multi_layer": True, "min_layer": 1, "max_layer": 2})))
        
        # 2. 矩形建筑区
        cursor.execute('''
        INSERT OR REPLACE INTO 建筑区 (名称, 地图名, 层级开始, 层级结束, 类型, 位置, 角点, 层级, 大小数据)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', ('三层洋房_层1至3', '地牢测试', 1, 3, 'rectangle', 
             json.dumps([35, 15]), 
             json.dumps([[30, 10], [40, 10], [40, 20], [30, 20]]), 
             json.dumps([1, 2, 3]), 
             json.dumps({"area": 100, "is_multi_layer": True, "min_layer": 1, "max_layer": 3})))
        
        # 3. 圆形建筑区
        cursor.execute('''
        INSERT OR REPLACE INTO 建筑区 (名称, 地图名, 层级开始, 层级结束, 类型, 位置, 角点, 层级, 大小数据)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', ('圆塔_层1至2', '地牢测试', 1, 2, 'circle', 
             json.dumps([10, 10]), 
             json.dumps(5), 
             json.dumps([1, 2]), 
             json.dumps({"area": 78.5, "is_multi_layer": True, "min_layer": 1, "max_layer": 2})))
        
        # 提交更改
        conn.commit()
        print("数据库初始化成功！")
        print("已创建: 建筑区表, 房间表, 层信息表, 地图表, 物品表")
        print("已添加默认地图: 地牢测试 (5层)")
        print("已添加默认建筑区: 旋转房间2_层1至2, 三层洋房_层1至3, 圆塔_层1至2")
        
    except Exception as e:
        print(f"初始化数据库时出错: {e}")
        traceback.print_exc()
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    import json
    初始化数据库()
    print("\n现在您可以运行 生成房间.py 来创建房间，然后运行 绘图.py 来查看结果。") 