"""纯逻辑读的数值常量 —— 从 ../shared/config-constants.json 载入(单一真相源,D-0042)。

**不手抄**:所有数值来自 TS 生成的 shared/config-constants.json;TS 一改、shared 守门测试即红。
对齐:src/config.ts:109-143 + CARRIER_RANK(deriveFormedBy.ts:62)+ MIN_ID_PREFIX(echoedId.ts:17)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ._shared import load_shared
from .types import ContentType, FormedBy


@dataclass(frozen=True, slots=True)
class CredThresholds:
    stable: int
    limited: int
    low: int


@dataclass(frozen=True, slots=True)
class Consolidation:
    base_by_formed_by: Mapping[FormedBy, int]
    support_step: int
    support_cap: int
    contradict_penalty: int
    min_confidence: int
    confidence_hard_max: int
    cred_thresholds: CredThresholds
    transient_types: tuple[ContentType, ...]
    transient_cap: int


@dataclass(frozen=True, slots=True)
class Config:
    consolidation: Consolidation
    half_life_days: Mapping[ContentType, float]
    min_effective_confidence: int
    carrier_rank: Mapping[str, int]
    min_id_prefix: int
    day_ms: int


def _load() -> Config:
    c = load_shared("config-constants.json")
    cons = c["consolidation"]
    return Config(
        consolidation=Consolidation(
            base_by_formed_by=dict(cons["baseByFormedBy"]),
            support_step=cons["supportStep"],
            support_cap=cons["supportCap"],
            contradict_penalty=cons["contradictPenalty"],
            min_confidence=cons["minConfidence"],
            confidence_hard_max=cons["confidenceHardMax"],
            cred_thresholds=CredThresholds(**{k: cons["credThresholds"][k] for k in ("stable", "limited", "low")}),
            transient_types=tuple(cons["transientTypes"]),
            transient_cap=cons["transientCap"],
        ),
        half_life_days=dict(c["background"]["halfLifeDays"]),
        min_effective_confidence=c["retrieval"]["minEffectiveConfidence"],
        carrier_rank=dict(c["carrierRank"]),
        min_id_prefix=c["minIdPrefix"],
        day_ms=c["dayMs"],
    )


#: 全局默认常量(= TS 的 config 单例的数值子集);纯逻辑函数缺省用它,可注入覆盖。
CONFIG: Config = _load()
