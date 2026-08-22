import sqlite3

conn = sqlite3.connect(r'C:\Users\HASEE\dungenMap_copy\refactored\dungeon.db')

print("=== map table schema ===")
for r in conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='map'").fetchall():
    print(r[0])

print()
print("=== room building_area_id column ===")
for r in conn.execute("PRAGMA table_info(room)").fetchall():
    if r[1] == "building_area_id":
        print(r)

print()
print("=== schema_migrations ===")
for r in conn.execute("SELECT * FROM schema_migrations").fetchall():
    print(r)

print()
print("=== existing map names ===")
for r in conn.execute("SELECT id, name FROM map").fetchall():
    print(r)

conn.close()
