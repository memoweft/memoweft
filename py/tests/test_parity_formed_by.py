"""逐位对拍 deriveFormedBy(shared/parity/formed-by.json):deriveOne 全分支 + 取最弱 + 空集→None。"""
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
