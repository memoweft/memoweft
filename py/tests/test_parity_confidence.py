"""逐位对拍 computeConfidence / deriveCredStatus 全组合(shared/parity/confidence.json + cred-status.json)。"""
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
        got = derive_cred_status(i["confidence"], i["contradictCount"], i["contentType"])
        assert got == case["expected"], f"deriveCredStatus 分叉 @ {i}: got {got}, want {case['expected']}"
