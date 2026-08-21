# refactored/src/generators/dwellings_core/tags.py
from __future__ import annotations
from typing import Iterable, List, Dict, Set

# 1) 中文 / 旧写法 / 同义词 -> 中间态 tag（再进一步 canonical）
_ALIAS: Dict[str, str] = {
    # UI/默认
    "默认": "default",
    "default": "default",

    # 风格
    "机械": "mechanical",
    "mechanical": "mechanical",
    "有机": "organic",
    "organic": "organic",

    # 走廊
    "走廊": "hallways",
    "走廊开": "hallways",
    "hallway": "hallways",
    "corridor": "hallways",
    "hallways": "hallways",
    # “走廊关/无走廊”在 JS 语义上等价于“不传 hallways”
    "走廊关": "no_hallways",
    "no_hallways": "no_hallways",
    "no-hallways": "no_hallways",

    # 地下室/楼梯
    "地下室": "basement",
    "basement": "basement",
    "螺旋": "spiral",
    "spiral": "spiral",
    "楼梯井": "stairwell",
    "stairwell": "stairwell",

    # 窗户倾向
    "透明": "transparent",
    "transparent": "transparent",
    "空白": "blank",
    "无": "blank",
    "blank": "blank",

    # 露台
    "露台": "terrace",
    "阳台": "terrace",
    "terrace": "terrace",
    "无露台": "no_terrace",
    "不生成露台": "no_terrace",
    "no_terrace": "no_terrace",
    "no-terrace": "no_terrace",

    # 尺寸（兼容旧写法）
    "size_small": "small",
    "size_medium": "medium",
    "size_large": "large",
    "小": "small",
    "中": "medium",
    "大": "large",
    "small": "small",
    "medium": "medium",
    "large": "large",

    # 层高（兼容旧写法）
    "low": "low",
    "tall": "tall",
    "矮": "low",
    "高": "tall",
}

# 2) 互斥组（JS Tags.resolve 的最核心部分）
#   注意：no_hallways 这里不当作“保留标签”，而是当作“移除 hallways”的语义
_MUTEX_GROUPS: Dict[str, Set[str]] = {
    "size": {"small", "medium", "large"},
    "height": {"low", "tall"},
    "windows": {"blank", "transparent"},
    "stairs": {"spiral", "stairwell"},
    "terrace": {"terrace", "no_terrace"},
    # hallways 组只用来消解（最后不保留 no_hallways）
    "hallways": {"hallways", "no_hallways"},
}

_GROUP_UNION: Set[str] = set().union(*_MUTEX_GROUPS.values())


def _dedup_keep_order(xs: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_tags(tags_raw: List[str] | None) -> List[str]:
    """
    仅做：
    - 清洗输入（None/空串）
    - lower/strip
    - alias 映射
    - 去重（保序）
    不做互斥消解（互斥交给 resolve_tags）
    """
    cleaned: List[str] = []
    for t in (tags_raw or []):
        if t is None:
            continue
        s = str(t).strip().lower()
        if not s:
            continue
        s = _ALIAS.get(s, s)
        cleaned.append(s)

    cleaned = _dedup_keep_order(cleaned)

    # 如果除了 default 还有其他 tag，就把 default 去掉（default 只是“空”的占位符）
    if "default" in cleaned and len(cleaned) > 1:
        cleaned = [x for x in cleaned if x != "default"]

    # 如果真的空了，回退 default
    return cleaned or ["default"]


def resolve_tags(tags: List[str] | None) -> List[str]:
    """
    互斥标签消解：
    - 每个互斥组只保留一个（按输入顺序：保留“最后出现”的那个）
    - no_hallways 不作为最终标签保留（语义是“别传 hallways”）
    """
    if not tags:
        return ["default"]

    tags = list(tags)

    # default 规则（同 parse_tags）
    if "default" in tags and len(tags) > 1:
        tags = [x for x in tags if x != "default"]

    # 计算每个互斥组“最后出现”的 tag
    last_in_group: Dict[str, str] = {}
    for t in tags:
        for g, s in _MUTEX_GROUPS.items():
            if t in s:
                last_in_group[g] = t

    # 重建输出：保留非互斥组 tag + 各组最后 tag（保持原始顺序）
    keep: Set[str] = set()
    for g, t in last_in_group.items():
        # hallways 特殊：如果最后是 no_hallways，就最终啥也不留（表示不传 hallways）
        if g == "hallways":
            if t == "hallways":
                keep.add("hallways")
            continue
        keep.add(t)

    out: List[str] = []
    for t in tags:
        if t in _GROUP_UNION:
            if t in keep:
                out.append(t)
        else:
            out.append(t)

    out = _dedup_keep_order(out)
    return out or ["default"]
