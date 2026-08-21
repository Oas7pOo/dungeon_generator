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
