# src/generators/passability.py
"""
可通行性（passability）统一查询（阶段 0 落地）。

约定（见 readme 路线图 §0.2）：
- 可通行：room.tiles_json["space"]（不含 wall / inner_wall）
- 不可通行：wall / inner_wall、水体（water）、岩石（stone）等障碍 item 占格

所有查询按 (map_id, layer) 缓存；构建时读库，之后纯内存。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Set, Tuple

from ..db.database import DatabaseManager

Cell = Tuple[int, int]

# item_type 中作为"不可通行障碍物"的类型（阶段 4 会扩展 water/stone 生成器）
BLOCKING_ITEM_TYPES = ("water", "stone")


class PassabilityIndex:
    """
    按层构建"可走格集合"，提供 is_walkable / walkable_cells / blocked_cells。

    用法：
        idx = PassabilityIndex(db)
        idx.is_walkable(map_id, layer, x, y)
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()
        self._walkable_cache: Dict[Tuple[int, int], Set[Cell]] = {}
        self._blocked_cache: Dict[Tuple[int, int], Set[Cell]] = {}

    # ------------------------------------------------------------------
    # json helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _loads(s: Optional[str], default: Any = None) -> Any:
        if not s:
            return default
        try:
            return json.loads(s)
        except Exception:
            return default

    @staticmethod
    def _collect_cells(obj: Any) -> Set[Cell]:
        """从 tiles_json 中收集所有形如 [[x,y],...] 的格点列表（兼容 wall_tiles/space/cells 等键）。"""
        out: Set[Cell] = set()
        if isinstance(obj, dict):
            for v in obj.values():
                out |= PassabilityIndex._collect_cells(v)
        elif isinstance(obj, list):
            # 列表元素是 [x, y] 数对？
            if obj and all(
                isinstance(e, (list, tuple)) and len(e) == 2
                and all(isinstance(c, (int, float)) for c in e)
                for e in obj
            ):
                for e in obj:
                    out.add((int(e[0]), int(e[1])))
            else:
                for v in obj:
                    out |= PassabilityIndex._collect_cells(v)
        return out

    # ------------------------------------------------------------------
    # build / query
    # ------------------------------------------------------------------
    def _build(self, map_id: int, layer: int) -> None:
        key = (int(map_id), int(layer))
        if key in self._walkable_cache:
            return

        walkable: Set[Cell] = set()

        # 1) 所有房间的 space（已不含 wall / inner_wall）
        rows = self.db.fetch_all(
            "SELECT tiles_json FROM room "
            "WHERE map_id = ? AND layer_start <= ? AND layer_end >= ?",
            (int(map_id), int(layer), int(layer)),
        ) or []
        for r in rows:
            tiles = self._loads(r.get("tiles_json"), {})
            space = tiles.get("space") if isinstance(tiles, dict) else None
            if isinstance(space, list):
                for e in space:
                    if isinstance(e, (list, tuple)) and len(e) == 2:
                        walkable.add((int(e[0]), int(e[1])))

        # 2) 障碍 item（water / stone）占格 -> 从可走中扣除
        blocked: Set[Cell] = set()
        placeholders = ", ".join("?" * len(BLOCKING_ITEM_TYPES))
        if placeholders:
            rows = self.db.fetch_all(
                "SELECT tiles_json, position_x, position_y FROM item "
                "WHERE map_id = ? AND layer_start <= ? AND layer_end >= ? "
                "AND item_type IN (" + placeholders + ")",
                (int(map_id), int(layer), int(layer), *BLOCKING_ITEM_TYPES),
            ) or []
            for r in rows:
                tiles = self._loads(r.get("tiles_json"), {})
                cells = self._collect_cells(tiles)
                if cells:
                    blocked |= cells
                else:
                    # 无占格信息时，用 position 所在格兜底
                    px = r.get("position_x")
                    py = r.get("position_y")
                    if px is not None and py is not None:
                        blocked.add((int(px), int(py)))

        self._walkable_cache[key] = walkable - blocked
        self._blocked_cache[key] = blocked

    def invalidate(self, map_id: int, layer: int) -> None:
        key = (int(map_id), int(layer))
        self._walkable_cache.pop(key, None)
        self._blocked_cache.pop(key, None)

    def invalidate_all(self) -> None:
        self._walkable_cache.clear()
        self._blocked_cache.clear()

    def walkable_cells(self, map_id: int, layer: int) -> Set[Cell]:
        self._build(int(map_id), int(layer))
        return self._walkable_cache[(int(map_id), int(layer))]

    def blocked_cells(self, map_id: int, layer: int) -> Set[Cell]:
        self._build(int(map_id), int(layer))
        return self._blocked_cache[(int(map_id), int(layer))]

    def is_walkable(self, map_id: int, layer: int, x: int, y: int) -> bool:
        return (int(x), int(y)) in self.walkable_cells(int(map_id), int(layer))
