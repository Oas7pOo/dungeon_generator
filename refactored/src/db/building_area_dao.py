# src/db/building_area_dao.py
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


class BuildingAreaDAO:
    """
    building_area 表的数据访问层（DAO）

    ✅ 所有查询均使用 id / *_id，不使用 name 当 key
    ✅ 所有 SQL 显式列名，不使用 SELECT *
    """

    def __init__(self, db_manager):
        self.db = db_manager

    # -------------------------
    # helpers
    # -------------------------
    @staticmethod
    def _json_dumps_or_none(obj: Any) -> Optional[str]:
        if obj is None:
            return None
        return json.dumps(obj, ensure_ascii=False)

    @staticmethod
    def _json_loads_or_none(s: Any) -> Any:
        if s is None:
            return None
        if isinstance(s, (dict, list, int, float)):
            return s
        if isinstance(s, (bytes, bytearray)):
            s = s.decode("utf-8", errors="ignore")
        if isinstance(s, str) and s.strip() == "":
            return None
        if isinstance(s, str):
            try:
                return json.loads(s)
            except Exception:
                return None
        return None

    @staticmethod
    def _row_get(row: Any, key: str, idx: int):
        # 支持 sqlite3.Row / dict / tuple
        if row is None:
            return None
        if isinstance(row, dict):
            return row.get(key)
        try:
            # sqlite3.Row 支持下标与 key
            return row[key]
        except Exception:
            try:
                return row[idx]
            except Exception:
                return None

    def _fetch_one(self, sql: str, params: Tuple[Any, ...]) -> Any:
        # 兼容：有的 DatabaseManager 叫 fetch_one，有的只有 fetch_all
        if hasattr(self.db, "fetch_one"):
            return self.db.fetch_one(sql, params)
        rows = self.db.fetch_all(sql, params)
        return rows[0] if rows else None

    def _fetch_all(self, sql: str, params: Tuple[Any, ...]) -> List[Any]:
        return self.db.fetch_all(sql, params)

    # -------------------------
    # map
    # -------------------------
    def get_map_size(self, map_id: int) -> Optional[Tuple[int, int]]:
        row = self._fetch_one(
            "SELECT width, height FROM map WHERE id = ?",
            (int(map_id),),
        )
        if not row:
            return None
        width = self._row_get(row, "width", 0)
        height = self._row_get(row, "height", 1)
        if width is None or height is None:
            return None
        return int(width), int(height)

    # -------------------------
    # building_area
    # -------------------------
    def list_building_areas_covering_layer(self, map_id: int, layer: int) -> List[Dict[str, Any]]:
        rows = self._fetch_all(
            "SELECT "
            "id, map_id, name, layer_start, layer_end, geom_type, "
            "center_x, center_y, radius, geom_json, size_json "
            "FROM building_area "
            "WHERE map_id = ? AND layer_start <= ? AND layer_end >= ?",
            (int(map_id), int(layer), int(layer)),
        )

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": self._row_get(r, "id", 0),
                    "map_id": self._row_get(r, "map_id", 1),
                    "name": self._row_get(r, "name", 2),
                    "layer_start": self._row_get(r, "layer_start", 3),
                    "layer_end": self._row_get(r, "layer_end", 4),
                    "geom_type": self._row_get(r, "geom_type", 5),
                    "center_x": self._row_get(r, "center_x", 6),
                    "center_y": self._row_get(r, "center_y", 7),
                    "radius": self._row_get(r, "radius", 8),
                    "geom_json": self._json_loads_or_none(self._row_get(r, "geom_json", 9)),
                    "size_json": self._json_loads_or_none(self._row_get(r, "size_json", 10)),
                }
            )
        return out

    def insert_building_area(
        self,
        *,
        map_id: int,
        name: str,
        layer_start: int,
        layer_end: int,
        geom_type: str,
        center_x: Optional[float],
        center_y: Optional[float],
        radius: Optional[float],
        geom_json: Optional[Any],
        size_json: Optional[Any],
    ) -> int:
        cur = self.db.execute(
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
                None if center_x is None else float(center_x),
                None if center_y is None else float(center_y),
                None if radius is None else float(radius),
                self._json_dumps_or_none(geom_json),
                self._json_dumps_or_none(size_json),
            ),
        )

        # 兼容 execute 返回 cursor / None
        if cur is not None and hasattr(cur, "lastrowid"):
            return int(cur.lastrowid)

        row = self._fetch_one("SELECT last_insert_rowid()", tuple())
        if row is None:
            raise RuntimeError("insert_building_area: cannot get last_insert_rowid()")
        return int(self._row_get(row, "last_insert_rowid()", 0))
