"""验证 deriveFormedBy 全部分支、最低可信载体选择及空集语义的跨语言一致性。"""
from __future__ import annotations

from typing import Any

from conftest import parity

from memoweft import CarrierInput, Resolution, derive_formed_by


def _carrier(e: dict[str, Any]) -> CarrierInput:
    r = e["resolution"]
    return CarrierInput(
        source_kind=e["sourceKind"],
        preceding_ai_context=e["precedingAiContext"],
        resolution=None if r is None else Resolution(response_act=r["responseAct"], proposition_origin=r["propositionOrigin"]),
    )


def test_formed_by_bit_exact() -> None:
    data = parity("formed-by.json")
    for case in data["cases"]:
        inputs = [_carrier(e) for e in case["input"]]
        got = derive_formed_by(inputs)
        assert got == case["expected"], f"deriveFormedBy 分叉 @ {case['input']}: got {got}, want {case['expected']}"
