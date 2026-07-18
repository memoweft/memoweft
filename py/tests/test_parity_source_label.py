"""sourceLabel / aiContextSuffix parity:Python 与 TS(shared/parity/source-label.json)逐例一致。

钉 js-trim(去 BOM 等)+ UTF-16 slice(240)+ 全角括号字节()。
"""
from __future__ import annotations

from typing import Any

from conftest import parity

from memoweft.source_label import ai_context_suffix, source_label


def test_source_label_matches_ts() -> None:
    data: Any = parity("source-label.json")
    for case in data["sourceLabel"]["cases"]:
        inp = case["input"]
        assert source_label(inp["sourceKind"], inp["lang"]) == case["expected"], f"sourceLabel {inp}"
    for case in data["aiContextSuffix"]["cases"]:
        inp = case["input"]
        assert ai_context_suffix(inp["text"], inp["lang"]) == case["expected"], f"aiContextSuffix {inp}"
