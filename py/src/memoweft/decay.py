"""分型衰减(读时算、不持久化)。移植自 src/background/decay.ts。

parity 硬点:effectiveConfidence 用 Math.round(半值向上);decayFactor 用 2^x(IEEE754,应与 JS 逐位一致)。
"""
from __future__ import annotations

from ._math import round_half_up
from .config import CONFIG, Config
from .types import ContentType


def decay_factor(half_life_days: float, age_ms: float) -> float:
    """衰减因子 0~1:半衰期 ≤0(或 NaN)= 不衰减返回 1;否则 2^(-age天/半衰期)。逐位对拍 decay.ts:16-20。"""
    if not (half_life_days > 0):  # NaN 也走此支(NaN>0 为 False),与 JS `!(x>0)` 一致
        return 1.0
    age_days = max(0.0, age_ms) / CONFIG.day_ms
    return float(2.0 ** (-age_days / half_life_days))


def half_life_of(content_type: ContentType, cfg: Config = CONFIG) -> float:
    """半衰期(天)按类型取;没配 = 0 = 不衰减。decay.ts:23-25。"""
    return cfg.half_life_days.get(content_type, 0)


def effective_confidence(
    confidence: int,
    content_type: ContentType,
    updated_at_ms: float,
    now_ms: float,
    cfg: Config = CONFIG,
) -> int:
    """有效置信 = confidence × 衰减因子(按距 updatedAt 的时间),读时算、恒整数。decay.ts:31-38。

    (TS 签名收 Cognition + Date;Python 侧收毫秒时间戳,由调用方/测试从 ISO 解析,保持纯函数无 I/O。)
    """
    age_ms = now_ms - updated_at_ms
    return round_half_up(confidence * decay_factor(half_life_of(content_type, cfg), age_ms))
