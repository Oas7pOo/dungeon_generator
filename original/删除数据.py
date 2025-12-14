#清空dungeon.db中的所有数据
import sqlite3

# 连接到dungeon.db数据库
conn = sqlite3.connect('dungeon.db')

# 获取所有表名
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()

# 清空每个表中的数据
print(tables)
#for table in tables:
#    cursor.execute(f"DELETE FROM {table[0]}")
cursor.execute(f"DELETE FROM {tables[2][0]}")

# 提交事务
conn.commit()

# 关闭连接
conn.close()
