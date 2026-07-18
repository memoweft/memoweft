"""把握度自算 —— 由规则算,不采信 LLM 自报(铁律 3b)。移植自 src/consolidation/confidence.ts。

parity 硬点:computeConfidence 用 Math.round(半值向上)—— 见 _math.round_half_up。
"""
from __future__ import annotations

from ._math import round_half_up
from .config import CONFIG, Config
from .types import ConfidenceInputs, ContentType, CredStatus


def is_transient(content_type: ContentType, cfg: Config = CONFIG) -> bool:
    return content_type in cfg.consolidation.transient_types


def compute_confidence(i: ConfidenceInputs, cfg: Config = CONFIG) -> int:
    """算把握度 0~1000(恒 >0);临时类封顶。逐位对拍 confidence.ts:25-34。"""
    c = cfg.consolidation
    base = c.base_by_formed_by[i.formed_by]
    support = min(max(i.support_count - 1, 0), c.support_cap) * c.support_step
    penalty = i.contradict_count * c.contradict_penalty
    result = max(c.min_confidence, min(c.confidence_hard_max, round_half_up(base + support - penalty)))
    if is_transient(i.content_type, cfg):
        result = min(result, c.transient_cap)
    return result


def derive_cred_status(
    confidence: int,
    contradict_count: int,
    content_type: ContentType,
    cfg: Config = CONFIG,
) -> CredStatus:
    """由把握度 + 反对证据 + 内容类型定可信状态。逐位对拍 confidence.ts:37-53。"""
    if contradict_count > 0:
        return "conflicted"  # 有反对证据 → 先暴露,不消解
    t = cfg.consolidation.cred_thresholds
    if is_transient(content_type, cfg):
        return "low" if confidence >= t.low else "candidate"
    if confidence >= t.stable:
        return "stable"
    if confidence >= t.limited:
        return "limited"
    if confidence >= t.low:
        return "low"
    return "candidate"
