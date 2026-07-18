"""hashContext parity:Python json.dumps 字节 + sha256 与 TS(shared/parity/context-hash.json)逐例一致。

验证 JSON.stringify 字节等价（ensure_ascii=False + separators=(",",":"））+ role、content 字段序。
"""
from __future__ import annotations

from typing import Any

from conftest import parity

from memoweft.store.interaction_context import hash_context
from memoweft.types import VisibleTurn


def test_context_hash_matches_ts() -> None:
    data: Any = parity("context-hash.json")
    for case in data["cases"]:
        turns = [VisibleTurn(role=t["role"], content=t["content"]) for t in case["input"]]
        assert hash_context(turns) == case["expected"], f"hash 分叉 input={case['input']}"
