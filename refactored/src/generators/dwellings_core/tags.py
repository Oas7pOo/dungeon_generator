from __future__ import annotations
from typing import List

_ALIAS = {
    # 中文 -> canonical
    "默认": "default",
    "机械": "mechanical",
    "有机": "organic",
    "走廊": "hallways",
    "走廊开": "hallways",
    "走廊关": "no_hallways",  # 你也可以不用这个，直接不传 hallways
    "地下室": "basement",
    "螺旋": "spiral",
    "楼梯井": "stairwell",
    "透明": "transparent",
    "空白": "blank",
    "无": "blank",
}

def parse_tags(tags_raw: List[str]) -> List[str]:
    out = []
    for t in (tags_raw or []):
        if t is None:
            continue
        s = str(t).strip().lower()
        if not s:
            continue
        s = _ALIAS.get(s, s)
        # 归一化一些写法
        if s in ("hallway", "corridor"):
            s = "hallways"
        if s == "no_hallways":
            # 简化：不加 hallways 即等价于 JS 的 noNooks=true
            continue
        if s not in out:
            out.append(s)
    if not out:
        out = ["default"]
    return out
