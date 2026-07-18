"""deriveFormedBy —— 从支持证据集派生认知的【载体维】形成方式（按最保守项取值）。

与 TypeScript deriveFormedBy 契约保持一致，只计算载体维（stated/confirmed/observed），
不计算由模型产生的 inferred。多证据取最低可信载体；spoken 缺少解析时使用结构事实后备规则。
"""
from __future__ import annotations

from typing import Optional, Sequence

from .config import CONFIG
from .types import CarrierFormedBy, CarrierInput

#: 载体维强弱序(confirmed < observed < stated);取最弱 = rank 最小。源自 shared/config-constants(carrierRank)。
_CARRIER_RANK = CONFIG.carrier_rank


def _derive_one(e: CarrierInput) -> CarrierFormedBy:
    """按共享派生规则将单条证据映射为载体维。"""
    # 前两行:非 spoken(observed/tool/inferred 型证据)不是用户在说话 → observed。
    if e.source_kind != "spoken":
        return "observed"

    has_ai_context = bool((e.preceding_ai_context or "").strip())
    r = e.resolution

    # 未解析或 propositionOrigin 为 null 时使用结构事实后备规则。
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
    """将支持证据集映射为最低可信载体维；空集返回 None。"""
    weakest: Optional[CarrierFormedBy] = None
    for e in evidences:
        c = _derive_one(e)
        if weakest is None or _CARRIER_RANK[c] < _CARRIER_RANK[weakest]:
            weakest = c
    return weakest
