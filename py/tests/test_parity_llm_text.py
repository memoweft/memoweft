"""strip_reasoning + read_reply_text parity:Python 与 TS(shared/parity/llm-text.json)逐例一致。

验证 <think> 剥离正则（大小写/跨行）+ js_trim + reasoning_content 兜底取值顺序。
"""
from __future__ import annotations

from typing import Any

from conftest import parity

from memoweft.llm.client import read_reply_text, strip_reasoning


def test_strip_reasoning_matches_ts() -> None:
    data: Any = parity("llm-text.json")
    for case in data["stripReasoning"]["cases"]:
        assert strip_reasoning(case["input"]) == case["expected"], f"strip {case['input']!r}"


def test_read_reply_text_matches_ts() -> None:
    data: Any = parity("llm-text.json")
    for case in data["readReplyText"]["cases"]:
        assert read_reply_text(case["input"]) == case["expected"], f"read {case['input']!r}"
