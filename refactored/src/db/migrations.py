# src/db/migrations.py
"""
迁移注册表（供 DatabaseManager.migrate() 消费）。

用法：
    from .migrations import _registry
    for version, fn in _registry.MIGRATIONS: ...

业务代码不建表、不 DROP/CREATE —— 建表统一走这里（迁移脚本负责）。
新增迁移：
    @_registry.register("v002_something")
    def v002_something(db):
        db.execute("ALTER TABLE ... ADD COLUMN ...")
"""
from __future__ import annotations

from typing import Any, Callable, List, Tuple


class _MigrationRegistry:
    """按 version 顺序保存迁移函数。version 为唯一字符串（如 'v001_init'）。"""

    def __init__(self) -> None:
        self.MIGRATIONS: List[Tuple[str, Callable[[Any], None]]] = []

    def register(self, version: str) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
        def deco(fn: Callable[[Any], None]) -> Callable[[Any], None]:
            for v, _ in self.MIGRATIONS:
                if v == version:
                    raise ValueError(f"migration version already registered: {version}")
            self.MIGRATIONS.append((version, fn))
            return fn
        return deco


_registry = _MigrationRegistry()


# ---------------------------------------------------------------------------
# v001：初始四张核心表（与现有 V2 schema 一致，IF NOT EXISTS 幂等）
# 顺序：map -> building_area -> room -> item（外键依赖）
# ---------------------------------------------------------------------------
@_registry.register("v001_init")
def v001_init(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS map ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "name TEXT NOT NULL,"
        "width INTEGER NOT NULL,"
        "height INTEGER NOT NULL,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ");"
    )

    db.execute(
        "CREATE TABLE IF NOT EXISTS building_area ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "map_id INTEGER NOT NULL,"
        "name TEXT,"
        "layer_start INTEGER DEFAULT 1,"
        "layer_end INTEGER DEFAULT 1,"
        "geom_type TEXT,"
        "center_x REAL,"
        "center_y REAL,"
        "radius REAL,"
        "geom_json TEXT,"
        "size_json TEXT,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "FOREIGN KEY (map_id) REFERENCES map(id)"
        ");"
    )

    db.execute(
        "CREATE TABLE IF NOT EXISTS room ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "map_id INTEGER NOT NULL,"
        "building_area_id INTEGER,"
        "name TEXT,"
        "layer_start INTEGER DEFAULT 1,"
        "layer_end INTEGER DEFAULT 1,"
        "room_type TEXT,"
        "geom_json TEXT,"
        "tiles_json TEXT,"
        "area INTEGER,"
        "other_json TEXT,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "FOREIGN KEY (map_id) REFERENCES map(id),"
        "FOREIGN KEY (building_area_id) REFERENCES building_area(id)"
        ");"
    )

    db.execute(
        "CREATE TABLE IF NOT EXISTS item ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "map_id INTEGER NOT NULL,"
        "room_id INTEGER,"
        "building_area_id INTEGER,"
        "name TEXT,"
        "item_type TEXT,"
        "layer_start INTEGER DEFAULT 1,"
        "layer_end INTEGER DEFAULT 1,"
        "timestep INTEGER DEFAULT 0,"
        "position_x REAL,"
        "position_y REAL,"
        "vector_json TEXT,"
        "tiles_json TEXT,"
        "properties_json TEXT,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "FOREIGN KEY (map_id) REFERENCES map(id),"
        "FOREIGN KEY (room_id) REFERENCES room(id),"
        "FOREIGN KEY (building_area_id) REFERENCES building_area(id)"
        ");"
    )


# ---------------------------------------------------------------------------
# v002：道路支持——让 room.building_area_id 可为 NULL（跨建筑区道路）
# SQLite 不支持 ALTER COLUMN DROP NOT NULL，需重建表。
# 使用 PRAGMA defer_foreign_keys 延迟 FK 检查，避免 DROP TABLE 时的约束冲突。
# ---------------------------------------------------------------------------
@_registry.register("v002_road_nullable_building_area_id")
def v002_road_nullable_building_area_id(db) -> None:
    """
    重建 room 表：building_area_id 允许 NULL（道路 room_type='road' 跨建筑区时无所属建筑区）。
    migrate() 已在事务中包裹本函数，无需自行 begin/commit。
    """
    # 检查 building_area_id 是否已经是 nullable
    info = db.fetch_all("PRAGMA table_info(room)") or []
    ba_col = None
    for col in info:
        if col.get("name") == "building_area_id":
            ba_col = col
            break
    # notnull == 0 表示已经 nullable，无需迁移
    if ba_col and not ba_col.get("notnull", 0):
        return

    # 延迟外键约束检查，使 DROP TABLE 不会因 item 表的 FK 引用而失败。
    # 注意：必须使用 db.conn.execute() 绕过 DatabaseManager 的自动 commit，
    # 否则每次写操作 commit 都会重置 PRAGMA defer_foreign_keys，
    # 导致后续 DROP TABLE room 时外键约束仍然生效而报错。
    # migrate() 已用 BEGIN/COMMIT 包裹整个迁移，PRAGMA 在此事务内全程有效。
    conn = db.conn
    conn.execute("PRAGMA defer_foreign_keys = ON")

    # 清理可能残留的临时表（上次失败遗留）
    conn.execute("DROP TABLE IF EXISTS room_new")

    # 创建新表：building_area_id 和 name 均允许 NULL
    conn.execute(
        "CREATE TABLE room_new ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "map_id INTEGER NOT NULL,"
        "building_area_id INTEGER,"
        "name TEXT,"
        "layer_start INTEGER DEFAULT 1,"
        "layer_end INTEGER DEFAULT 1,"
        "room_type TEXT,"
        "geom_json TEXT,"
        "tiles_json TEXT,"
        "area INTEGER,"
        "other_json TEXT,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "FOREIGN KEY (map_id) REFERENCES map(id),"
        "FOREIGN KEY (building_area_id) REFERENCES building_area(id)"
        ");"
    )

    # 逐列复制数据（避免依赖列顺序）
    conn.execute(
        "INSERT INTO room_new ("
        "id, map_id, building_area_id, name, layer_start, layer_end, "
        "room_type, geom_json, tiles_json, area, other_json, created_at, updated_at"
        ") SELECT "
        "id, map_id, building_area_id, name, layer_start, layer_end, "
        "room_type, geom_json, tiles_json, area, other_json, created_at, updated_at "
        "FROM room"
    )

    # 替换旧表
    conn.execute("DROP TABLE room")
    conn.execute("ALTER TABLE room_new RENAME TO room")
