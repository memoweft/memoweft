"""验证 computeConfidence / deriveCredStatus 全组合的跨语言一致性。"""
from __future__ import annotations

from conftest import parity

from memoweft import ConfidenceInputs, compute_confidence, derive_cred_status


def test_confidence_bit_exact() -> None:
    data = parity("confidence.json")
    assert len(data["cases"]) >= 1000
    for case in data["cases"]:
        i = case["input"]
        got = compute_confidence(
            ConfidenceInputs(
                content_type=i["contentType"],
                formed_by=i["formedBy"],
                support_count=i["supportCount"],
                contradict_count=i["contradictCount"],
            )
        )
        assert got == case["expected"], f"computeConfidence 分叉 @ {i}: got {got}, want {case['expected']}"


def test_cred_status_bit_exact() -> None:
    data = parity("cred-status.json")
    for case in data["cases"]:
        i = case["input"]
        # supportCount 缺省 0：不带该字段的用例正是"省略 → 退回保守 conflicted"的兼容契约。
        got = derive_cred_status(
            i["confidence"], i["contradictCount"], i["contentType"], support_count=i.get("supportCount", 0)
        )
        assert got == case["expected"], f"deriveCredStatus 分叉 @ {i}: got {got}, want {case['expected']}"
