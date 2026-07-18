"""parity 测试共用:载入 ../../shared/parity/*.json(TS 生成的 {input,expected} 夹具)。"""
from __future__ import annotations

from typing import Any

from memoweft._shared import load_shared


def parity(relpath: str) -> Any:
    """读 shared/parity/<relpath>(如 'confidence.json')。"""
    return load_shared(f"parity/{relpath}")
