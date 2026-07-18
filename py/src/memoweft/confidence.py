"""由确定性规则计算把握度，不采信模型自报分数；与 TypeScript 实现保持一致。

跨语言数值契约要求使用 Math.round 的半值向上语义，见 _math.round_half_up。
"""
from __future__ import annotations

from ._math import round_half_up
from .config import CONFIG, Config
from .types import ConfidenceInputs, ContentType, CredStatus


def is_transient(content_type: ContentType, cfg: Config = CONFIG) -> bool:
    return content_type in cfg.consolidation.transient_types


def compute_confidence(i: ConfidenceInputs, cfg: Config = CONFIG) -> int:
    """计算 0~1000 的把握度，并对时效类内容应用上限；语义与 confidence.ts 一致。"""
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
    """根据把握度、反对证据和内容类型确定可信状态；语义与 confidence.ts 一致。"""
    if contradict_count > 0:
        return "conflicted"  # 保留冲突可见性，不自动消解反对证据。
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
