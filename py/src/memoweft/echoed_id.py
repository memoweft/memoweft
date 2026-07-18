"""将模型回显标识解析为白名单内的唯一标识；无法解析时返回 None。

解析顺序为标号映射、精确匹配、去除 ev-/cog- 后的唯一前缀匹配。
安全边界要求结果位于白名单且唯一；未知、歧义或过短前缀均返回 None。
"""
from __future__ import annotations

import re
from typing import Mapping, Optional, Set

from .config import CONFIG

#: 前缀匹配的最短长度；短于此值时拒绝推断。源自 shared/config-constants(minIdPrefix)。
MIN_ID_PREFIX: int = CONFIG.min_id_prefix

_PREFIX_RE = re.compile(r"^(ev-|cog-)", re.IGNORECASE)


def resolve_echoed_id(
    raw: Optional[str],
    whitelist: Set[str],
    tag_map: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """将模型回显标识解析为白名单中的唯一标识；语义与 echoedId.ts 一致。"""
    if not raw:
        return None
    key = raw.strip()
    if tag_map is not None:
        by_tag = tag_map.get(key)
        if by_tag and by_tag in whitelist:
            return by_tag  # ① 标号映射
    if raw in whitelist:
        return raw  # ② 精确
    bare = _PREFIX_RE.sub("", key)  # ③ 去除 ev-/cog- 后执行唯一前缀匹配。
    if len(bare) < MIN_ID_PREFIX:
        return None
    hit: Optional[str] = None
    for _id in whitelist:
        if not _id.startswith(bare):
            continue
        if hit is not None:
            return None  # 歧义 → 不猜
        hit = _id
    return hit
