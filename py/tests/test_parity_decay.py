"""逐位对拍 decayFactor / effectiveConfidence(shared/parity/decay.json)。

effectiveConfidence 是整数、逐位精确(parity 杀手①:Math.round 半值向上,已用 floor(x+0.5))。
decayFactor 是 double:2^x 的 IEEE754 在 JS/Python 间**理论**逐位一致,实测容极小 ULP 差(用极紧 isclose)。
"""
from __future__ import annotations

import math
from datetime import datetime

from conftest import parity

from memoweft import decay_factor, effective_confidence


def _iso_ms(s: str) -> int:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return round(dt.timestamp() * 1000)


def test_decay_factor_bit_exact() -> None:
    data = parity("decay.json")["decayFactor"]
    for case in data["cases"]:
        i = case["input"]
        got = decay_factor(i["halfLifeDays"], i["ageMs"])
        want = case["expected"]
        # 先试逐位相等;容 2^x 的 libm 实现差(极紧 tol)。
        assert got == want or math.isclose(got, want, rel_tol=1e-15, abs_tol=1e-18), f"decayFactor 分叉 @ {i}: got {got!r}, want {want!r}"


def test_effective_confidence_bit_exact() -> None:
    data = parity("decay.json")["effectiveConfidence"]
    for case in data["cases"]:
        i = case["input"]
        cog = i["cog"]
        got = effective_confidence(
            confidence=cog["confidence"],
            content_type=cog["contentType"],
            updated_at_ms=_iso_ms(cog["updatedAt"]),
            now_ms=_iso_ms(i["now"]),
        )
        assert got == case["expected"], f"effectiveConfidence 分叉 @ {i}: got {got}, want {case['expected']}"
