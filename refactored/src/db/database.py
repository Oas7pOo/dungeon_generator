# src/db/database.py
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


Params = Union[Sequence[Any], Tuple[Any, ...]]


class DatabaseManager:
    """
    SQLite 数据库管理器（V2）

    关键约束落地：
    ✅ 业务代码不 CREATE/DROP 表（迁移脚本负责）
    ✅ 统一 snake_case
    ✅ 所有查询返回 dict，杜绝 r[7] 这种错位灾难
    ✅ 启用外键约束
    """

    def __init__(self, db_path: str = "dungeon.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # 先拿 Row，再转 dict
        self._apply_connection_pragmas()

    # -------------------------
    # connection
    # -------------------------
    def _apply_connection_pragmas(self) -> None:
        # 外键必须显式打开
        self.conn.execute("PRAGMA foreign_keys = ON;")
        # 可选：提升并发读写体验
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA synchronous = NORMAL;")

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # -------------------------
    # low-level helpers
    # -------------------------
    @staticmethod
    def _to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _to_dicts(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
        return [dict(r) for r in rows]

    @staticmethod
    def _normalize_params(params: Optional[Params]) -> Tuple[Any, ...]:
        if params is None:
            return tuple()
        return tuple(params)

    @staticmethod
    def _looks_like_write_sql(sql: str) -> bool:
        s = sql.lstrip().upper()
        return (
            s.startswith("INSERT")
            or s.startswith("UPDATE")
            or s.startswith("DELETE")
            or s.startswith("REPLACE")
            or s.startswith("CREATE")
            or s.startswith("ALTER")
            or s.startswith("DROP")
            or s.startswith("VACUUM")
        )

    # -------------------------
    # public SQL API
    # -------------------------
    def execute(self, query: str, params: Optional[Params] = None) -> sqlite3.Cursor:
        """
        执行 SQL（返回 cursor）
        - 写操作默认 commit
        - 读操作不强制 commit
        """
        p = self._normalize_params(params)
        cur = self.conn.execute(query, p)

        if self._looks_like_write_sql(query):
            self.conn.commit()

        return cur

    def executemany(self, query: str, seq_of_params: Sequence[Params]) -> sqlite3.Cursor:
        cur = self.conn.executemany(query, [self._normalize_params(p) for p in seq_of_params])
        if self._looks_like_write_sql(query):
            self.conn.commit()
        return cur

    def fetch_one(self, query: str, params: Optional[Params] = None) -> Optional[Dict[str, Any]]:
        """
        返回单行 dict 或 None
        """
        p = self._normalize_params(params)
        cur = self.conn.execute(query, p)
        row = cur.fetchone()
        return self._to_dict(row)

    def fetch_all(self, query: str, params: Optional[Params] = None) -> List[Dict[str, Any]]:
        """
        返回多行 dict 列表
        """
        p = self._normalize_params(params)
        cur = self.conn.execute(query, p)
        rows = cur.fetchall()
        return self._to_dicts(rows)

    def scalar(self, query: str, params: Optional[Params] = None) -> Any:
        """
        返回单值（第一行第一列）或 None
        """
        p = self._normalize_params(params)
        cur = self.conn.execute(query, p)
        row = cur.fetchone()
        if row is None:
            return None
        # sqlite3.Row 支持按 index 取
        return row[0]

    def begin(self) -> None:
        self.conn.execute("BEGIN;")

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # -------------------------
    # migrations (manual)
    # -------------------------
    def migrate(self) -> None:
        """
        手动执行迁移（业务流程不要在运行期隐式调用）。
        你可以在程序入口 / 初始化脚本里显式调用一次。
        """
        from .migrations import _registry  # 避免循环 import

        self.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "version TEXT NOT NULL UNIQUE,"
            "applied_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ");"
        )

        applied = set(
            r["version"]
            for r in self.fetch_all("SELECT version FROM schema_migrations", None)
        )

        for version, fn in _registry.MIGRATIONS:
            if version in applied:
                continue

            # 迁移期间临时关闭外键约束。
            # 某些迁移需要重建表（DROP TABLE + CREATE + RENAME），会触发 FK 检查。
            # PRAGMA foreign_keys 只能在事务外设置，因此必须在 self.begin() 之前关闭，
            # 在事务结束（commit/rollback）后于 finally 中重新开启。
            self.conn.execute("PRAGMA foreign_keys = OFF")

            self.begin()
            try:
                fn(self)
                self.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)",
                    (version,),
                )
                self.commit()
            except Exception:
                self.rollback()
                raise
            finally:
                self.conn.execute("PRAGMA foreign_keys = ON")

    # -------------------------
    # V2 convenience methods (id-first)
    # -------------------------
    def insert_map(self, name: str, width: int, height: int) -> int:
        """
        创建 map 并返回 map_id
        """
        cur = self.execute(
            "INSERT INTO map (name, width, height) VALUES (?, ?, ?)",
            (str(name), int(width), int(height)),
        )
        return int(cur.lastrowid)

    def get_map_size(self, map_id: int) -> Optional[Tuple[int, int]]:
        """
        ✅ id-first：按 map_id 获取 (width, height)
        """
        row = self.fetch_one("SELECT width, height FROM map WHERE id = ?", (int(map_id),))
        if not row:
            return None
        return int(row["width"]), int(row["height"])

    def get_map_id_by_name(self, name: str) -> Optional[int]:
        """
        仅用于初始化/调试，不建议业务逻辑依赖 name 当 key
        """
        row = self.fetch_one("SELECT id FROM map WHERE name = ?", (str(name),))
        return int(row["id"]) if row else None

    def insert_building_area(
        self,
        *,
        map_id: int,
        name: str,
        layer_start: int,
        layer_end: int,
        geom_type: str,
        center_x: Optional[float] = None,
        center_y: Optional[float] = None,
        radius: Optional[float] = None,
        geom_json: Optional[str] = None,
        size_json: Optional[str] = None,
    ) -> int:
        """
        直接插入 building_area（上层通常用 DAO 统一 json 序列化）
        """
        cur = self.execute(
            "INSERT INTO building_area ("
            "map_id, name, layer_start, layer_end, geom_type, "
            "center_x, center_y, radius, geom_json, size_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(map_id),
                str(name),
                int(layer_start),
                int(layer_end),
                str(geom_type),
                center_x,
                center_y,
                radius,
                geom_json,
                size_json,
            ),
        )
        return int(cur.lastrowid)

    def list_building_areas_covering_layer(self, map_id: int, layer: int) -> List[Dict[str, Any]]:
        """
        ✅ id-first：按 map_id 与 layer 查询覆盖该层的建筑区
        """
        return self.fetch_all(
            "SELECT "
            "id, map_id, name, layer_start, layer_end, geom_type, "
            "center_x, center_y, radius, geom_json, size_json, created_at, updated_at "
            "FROM building_area "
            "WHERE map_id = ? AND layer_start <= ? AND layer_end >= ?",
            (int(map_id), int(layer), int(layer)),
        )

    # -------------------------
    # Compatibility stubs (old V1 API)
    # -------------------------
    def save_building_area(self, *args, **kwargs):
        raise RuntimeError(
            "save_building_area 是 V1 旧接口（building_areas/map_name/min_layer/max_layer）。"
            "请改用 V2：building_area 表 + map_id + layer_start/layer_end + geom_type/geom_json/size_json。"
        )

    def get_building_areas_by_layer(self, *args, **kwargs):
        raise RuntimeError(
            "get_building_areas_by_layer(map_name, layer) 是 V1 旧接口。"
            "请改用 list_building_areas_covering_layer(map_id, layer)。"
        )
