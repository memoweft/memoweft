"""按内容类型计算读取时衰减，不持久化计算结果；与 TypeScript 实现保持一致。

跨语言数值契约要求 effectiveConfidence 使用 Math.round 的半值向上语义，并保留 2^x 的 IEEE 754 行为。
"""
from __future__ import annotations

from ._math import round_half_up
from .config import CONFIG, Config
from .types import ContentType


def decay_factor(half_life_days: float, age_ms: float) -> float:
    """计算 0~1 的衰减因子；半衰期 ≤0 或 NaN 时不衰减，语义与 decay.ts 一致。"""
    if not (half_life_days > 0):  # NaN 也走此支(NaN>0 为 False),与 JS `!(x>0)` 一致
        return 1.0
    age_days = max(0.0, age_ms) / CONFIG.day_ms
    return float(2.0 ** (-age_days / half_life_days))


def half_life_of(content_type: ContentType, cfg: Config = CONFIG) -> float:
    """按内容类型返回半衰期天数；未配置时返回 0，表示不衰减。"""
    return cfg.half_life_days.get(content_type, 0)


def effective_confidence(
    confidence: int,
    content_type: ContentType,
    updated_at_ms: float,
    now_ms: float,
    cfg: Config = CONFIG,
) -> int:
    """按距 updatedAt 的时间计算 confidence × 衰减因子，并返回整数。

    (TS 签名收 Cognition + Date;Python 侧收毫秒时间戳,由调用方/测试从 ISO 解析,保持纯函数无 I/O。)
    """
    age_ms = now_ms - updated_at_ms
    return round_half_up(confidence * decay_factor(half_life_of(content_type, cfg), age_ms))
