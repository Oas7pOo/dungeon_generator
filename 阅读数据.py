#读取dungeon.db中的所有数据，打印每个表中前6条信息到terminal（如果有6条信息）
import sqlite3

# 连接到dungeon.db数据库
conn = sqlite3.connect('dungeon.db')
cursor = conn.cursor()

# 获取所有表名
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()

# 打印每个表中前6条信息
for table in tables:
    table_name = table[0]
    print(f"\n===== 表: {table_name} =====") 
    
    # 获取表的列名（表头）
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns_info = cursor.fetchall()
    
    # 提取列名并打印表头
    headers = [col[1] for col in columns_info]
    print("表头:", " | ".join(headers))
    print("-" * 60)  # 分隔线
    
    # 获取表中的前6条数据
    cursor.execute(f"SELECT * FROM {table_name} LIMIT 6")
    data = cursor.fetchall()
    
    if data:
        for row in data:
            # 将所有字段转换为字符串并打印
            row_str = [str(field) for field in row]
            print(" | ".join(row_str))
    else:
        print("表中没有数据")
    
    print("\n")

# 关闭连接
conn.close()
