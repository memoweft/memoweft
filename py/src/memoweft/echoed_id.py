"""把模型【回显的 id】解回白名单内的真 id;解不出返回 None。移植自 src/llm/echoedId.ts(D-0036)。

三级:① 标号映射 → ② 精确匹配 → ③ 唯一前缀兜底(剥示例前缀 ev-/cog-)。
护栏一寸不让(3a/3d):只解到白名单内、且唯一命中;捏造/歧义前缀/过短(< MIN_ID_PREFIX)一律 None。
"""
from __future__ import annotations

import re
from typing import Mapping, Optional, Set

from .config import CONFIG

#: 前缀容错的最短长度(echoedId.ts:17);短于此不猜。源自 shared/config-constants(minIdPrefix)。
MIN_ID_PREFIX: int = CONFIG.min_id_prefix

_PREFIX_RE = re.compile(r"^(ev-|cog-)", re.IGNORECASE)


def resolve_echoed_id(
    raw: Optional[str],
    whitelist: Set[str],
    tag_map: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """逐位对拍 echoedId.ts:19-38。"""
    if not raw:
        return None
    key = raw.strip()
    if tag_map is not None:
        by_tag = tag_map.get(key)
        if by_tag and by_tag in whitelist:
            return by_tag  # ① 标号(治本)
    if raw in whitelist:
        return raw  # ② 精确
    bare = _PREFIX_RE.sub("", key)  # ③ 剥 ev-/cog- 前缀后唯一前缀兜底
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
