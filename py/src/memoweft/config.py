"""纯逻辑读的数值常量 + 存储层授权默认 —— 从 ../shared/config-constants.json 载入(单一真相源,D-0042)。

**不手抄**:所有数值 / 授权默认来自 TS 生成的 shared/config-constants.json;TS 一改、shared 守门测试即红。
对齐:src/config.ts:99-154 + CARRIER_RANK(deriveFormedBy.ts:62)+ MIN_ID_PREFIX(echoedId.ts:17)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from ._shared import load_shared
from .types import ContentType, FormedBy, Lang


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
class Attribution:
    """M4 归因规则门(config.ts:119-125);全字段 P2-7 纳入 shared(原只有 hypothesis_cap)。"""

    window_hours: int
    hypothesis_cap: int
    max_phenomena_per_run: int
    max_causes_per_hypothesis: int
    min_phenomenon_support: int


@dataclass(frozen=True, slots=True)
class Identity:
    """身份默认(config.ts:100):v1 单人单宿主,perceive/ingest 缺省用;多用户由调用方覆盖。"""

    subject_id: str
    host_id: str


@dataclass(frozen=True, slots=True)
class EvidenceDefaults:
    """spoken/inferred 证据的通用授权默认(config.ts:104);无 allow_cloud_read → 走 cloud_read_default。"""

    allow_local_read: bool
    allow_inference: bool


@dataclass(frozen=True, slots=True)
class SourceDefaults:
    """observed/tool 证据的保守授权默认(config.ts:105-106):local✓/cloud✗/infer✓。"""

    allow_local_read: bool
    allow_cloud_read: bool
    allow_inference: bool


@dataclass(frozen=True, slots=True)
class Config:
    consolidation: Consolidation
    half_life_days: Mapping[ContentType, float]
    expire_after_days: Mapping[ContentType, int]
    #: 跨会话趋势(config.ts:140-141):看近多少天 + 窗口内至少几次才算趋势。
    trend_window_days: int
    trend_min_count: int
    attribution: Attribution
    min_effective_confidence: int
    carrier_rank: Mapping[str, int]
    min_id_prefix: int
    day_ms: int
    #: 身份默认(perceive/ingest 缺省 subject_id/host_id;P2-1b 纳入 shared)。
    identity: Identity
    #: 存储层授权默认(evidence.put 补;跨语言授权红线,P2-1a 纳入 shared)。
    privacy_mode: bool
    evidence_defaults: EvidenceDefaults
    observed_defaults: SourceDefaults
    tool_defaults: SourceDefaults


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
        expire_after_days=dict(c["background"]["expireAfterDays"]),
        trend_window_days=c["background"]["trendWindowDays"],
        trend_min_count=c["background"]["trendMinCount"],
        attribution=Attribution(
            window_hours=c["attribution"]["windowHours"],
            hypothesis_cap=c["attribution"]["hypothesisCap"],
            max_phenomena_per_run=c["attribution"]["maxPhenomenaPerRun"],
            max_causes_per_hypothesis=c["attribution"]["maxCausesPerHypothesis"],
            min_phenomenon_support=c["attribution"]["minPhenomenonSupport"],
        ),
        min_effective_confidence=c["retrieval"]["minEffectiveConfidence"],
        carrier_rank=dict(c["carrierRank"]),
        min_id_prefix=c["minIdPrefix"],
        day_ms=c["dayMs"],
        identity=Identity(subject_id=c["identity"]["subjectId"], host_id=c["identity"]["hostId"]),
        privacy_mode=c["privacyMode"],
        evidence_defaults=EvidenceDefaults(
            allow_local_read=c["evidenceDefaults"]["allowLocalRead"],
            allow_inference=c["evidenceDefaults"]["allowInference"],
        ),
        observed_defaults=SourceDefaults(
            allow_local_read=c["observedDefaults"]["allowLocalRead"],
            allow_cloud_read=c["observedDefaults"]["allowCloudRead"],
            allow_inference=c["observedDefaults"]["allowInference"],
        ),
        tool_defaults=SourceDefaults(
            allow_local_read=c["toolDefaults"]["allowLocalRead"],
            allow_cloud_read=c["toolDefaults"]["allowCloudRead"],
            allow_inference=c["toolDefaults"]["allowInference"],
        ),
    )


#: 全局默认常量(= TS 的 config 单例的数值 + 授权默认子集);缺省用它,可注入覆盖。
CONFIG: Config = _load()


def cloud_read_default(c: Config = CONFIG) -> bool:
    """allow_cloud_read 的默认:跟随配置——隐私模式下默认不上云。对齐 config.ts:146。"""
    return not c.privacy_mode


def resolve_lang() -> Lang:
    """取库语言:env MEMOWEFT_LANG=zh → zh,否则 en(含未设/其它值)。对齐 config.ts:102,152。

    只影响文本产出(提示词/兜底文案),绝不流入置信度自算。
    """
    return "zh" if os.environ.get("MEMOWEFT_LANG") == "zh" else "en"

