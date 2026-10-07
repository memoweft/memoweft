"""交互上下文的跨语言规范化哈希。"""
from __future__ import annotations

import hashlib
import json

from .types import VisibleTurn


def hash_context(context: list[VisibleTurn]) -> str:
    """Legacy role/content hash unless causal metadata is present.

    Old rows retain their exact JSON.stringify-compatible hash.  New linked
    assistant turns add a canonical dependency summary so a conditional update
    cannot silently overwrite a concurrent, different causal binding.
    """
    payload = [{"role": turn.role, "content": turn.content} for turn in context]
    if any(turn.model_context_dependencies is not None for turn in context):
        payload = [
            {**row, **({"model_context_dependencies": turn.model_context_dependencies} if turn.model_context_dependencies is not None else {})}
            for row, turn in zip(payload, context)
        ]
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=any(turn.model_context_dependencies is not None for turn in context))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
