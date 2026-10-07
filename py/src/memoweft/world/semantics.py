"""Pure Stage-1 domain semantics shared by extraction and Gate checks."""
from __future__ import annotations

import re
import unicodedata


_INTERPERSONAL_CONFLICT_TYPES = frozenset({
    "interpersonal conflict",
    "人际冲突",
    "争执",
    "吵架",
})


def is_interpersonal_conflict_type(value: object) -> bool:
    """Return whether a wire event type denotes an interpersonal conflict.

    Gate@14 treats separator variants and Unicode compatibility forms as the
    same type, but does not rewrite the caller/model-owned wire value.
    """
    if not isinstance(value, str):
        return False
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[_-]+", " ", normalized)
    normalized = " ".join(normalized.split())
    return normalized in _INTERPERSONAL_CONFLICT_TYPES


__all__ = ["is_interpersonal_conflict_type"]
