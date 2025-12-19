from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Sequence


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


@dataclass(frozen=True)
class Specs:
    """
    先对齐 Dwellings.js 的“参数语义”，但算法还不对齐。
    这些参数会被 house.generate_house_export 消费，决定：
      - target_rooms
      - connectivity (额外门环)
      - window_density (每层窗数量)
    """
    # ---- Dwellings.js 同名/同意图参数（先做影子实现）
    avg_room_size: float = 6.0
    room_size_chaos: float = 1.0
    regular_rooms: bool = False
    prefer_corners: bool = False
    prefer_walls: bool = False
    no_nooks: bool = False
    connectivity: float = 0.5
    window_density_base: float = 0.9
    window_density_step: float = 0.1

    # ---- 我们目前最简算法的“桥接系数”
    # avg_room_size 在 JS 里不等价于“每房间 cell 数”，为了不爆炸，先用一个缩放系数。
    room_count_scale: float = 4.0
    room_count_min: int = 2
    room_count_max: int = 20
    window_cap: int = 40

    @classmethod
    def from_tags(cls, tags: Sequence[str]) -> "Specs":
        ts = set(str(t).strip().lower() for t in (tags or []) if str(t).strip())

        # JS: preferCorners = tags includes "mechanical"
        #     preferWalls   = tags includes "organic"
        #     noNooks       = NOT includes "hallways"
        prefer_corners = "mechanical" in ts
        prefer_walls = "organic" in ts
        no_nooks = "hallways" not in ts

        # 先保持与 JS 默认值一致的“感觉”（后面你还原 divideArea 时会真正使用这些偏好）
        return cls(
            avg_room_size=6.0,
            room_size_chaos=1.0,
            regular_rooms=False,
            prefer_corners=prefer_corners,
            prefer_walls=prefer_walls,
            no_nooks=no_nooks,
            connectivity=0.5,
            window_density_base=0.9,
            window_density_step=0.1,
        )

    def window_density_for_floor(self, n_floors: int, floor_index: int) -> float:
        """
        参考 JS 里每层 windowDensity 有变化的写法：
          windowDensity ≈ 0.9 - 0.1*(nFloors - floorIndex)
        floor_index 从 0 开始
        """
        n = max(1, int(n_floors))
        i = max(0, min(int(floor_index), n - 1))
        wd = self.window_density_base - self.window_density_step * (n - i)
        return _clamp(float(wd), 0.0, 1.0)

    def target_rooms(self, area_cell_count: int) -> int:
        """
        最简桥接：用 avg_room_size 和 scale 推一个“合理数量”的 rooms
        （后面用 JS 的 divideArea 会彻底替换掉这里）
        """
        A = max(1, int(area_cell_count))
        base = A / max(1e-6, (self.avg_room_size * self.room_count_scale))
        k = int(round(base))
        k = max(self.room_count_min, min(self.room_count_max, k))
        return k

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
