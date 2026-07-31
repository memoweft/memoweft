"""交互上下文的跨语言规范化哈希。"""
from __future__ import annotations

import hashlib
import json

from .types import VisibleTurn


def hash_context(context: list[VisibleTurn]) -> str:
    """计算 sha256(JSON.stringify(context))，与 TypeScript 的字段序和 UTF-8 字节一致。"""
    payload = [{"role": turn.role, "content": turn.content} for turn in context]
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
