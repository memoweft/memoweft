"""deriveFormedBy —— 从支持证据集派生认知的【载体维】来源强度(取最弱)。

移植自 src/consolidation/deriveFormedBy.ts(D-0035)。**只算载体维**(stated/confirmed/observed),
不算 inferred(那由模型报)。多证据取最弱。spoken 但无解析走结构事实兜底(有 AI 上句→confirmed,否则→stated)。
"""
from __future__ import annotations

from typing import Optional, Sequence

from .config import CONFIG
from .types import CarrierFormedBy, CarrierInput

#: 载体维强弱序(confirmed < observed < stated);取最弱 = rank 最小。源自 shared/config-constants(carrierRank)。
_CARRIER_RANK = CONFIG.carrier_rank


def _derive_one(e: CarrierInput) -> CarrierFormedBy:
    """单条证据 → 载体维。逐条对应派生表,见 deriveFormedBy.ts:65-89。"""
    # 前两行:非 spoken(observed/tool/inferred 型证据)不是用户在说话 → observed。
    if e.source_kind != "spoken":
        return "observed"

    has_ai_context = bool((e.preceding_ai_context or "").strip())
    r = e.resolution

    # 兜底:没解析、或 propositionOrigin 收敛成 null —— 结构事实兜底。
    if r is None or r.proposition_origin is None:
        return "confirmed" if has_ai_context else "stated"

    # 第 3 行:用户自己说出来的内容 → stated。
    if r.proposition_origin == "user_stated":
        return "stated"

    # assistant_proposed:命题是 AI 提的。唯一例外 negate(用户否认 AI 猜测=自己的明确表达)→ stated。
    if r.response_act == "negate":
        return "stated"

    # 其余(affirm/select/elaborate/ask/none/other/null)在 assistant_proposed 下一律 confirmed。
    return "confirmed"


def derive_formed_by(evidences: Sequence[CarrierInput]) -> Optional[CarrierFormedBy]:
    """支持证据集 → 载体维(取最弱);空集返回 None(调用方按「算不出」处理)。deriveFormedBy.ts:96-103。"""
    weakest: Optional[CarrierFormedBy] = None
    for e in evidences:
        c = _derive_one(e)
        if weakest is None or _CARRIER_RANK[c] < _CARRIER_RANK[weakest]:
            weakest = c
    return weakest
