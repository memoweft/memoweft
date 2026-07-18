"""从 ../shared/config-constants.json 加载纯逻辑常量与存储层授权默认值。

所有数值与授权默认值都来自 TS 生成的 shared/config-constants.json，避免维护重复配置。
共享资产契约涵盖 src/config.ts、CARRIER_RANK 与 MIN_ID_PREFIX。
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
class ConfidenceBand:
    min: int
    max: int


@dataclass(frozen=True, slots=True)
class Asking:
    """主动询问策略的完整共享字段。"""

    max_asks: int
    confidence_band: ConfidenceBand
    askable_statuses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Attribution:
    """归因规则的完整共享字段。"""

    window_hours: int
    hypothesis_cap: int
    max_phenomena_per_run: int
    max_causes_per_hypothesis: int
    min_phenomenon_support: int


@dataclass(frozen=True, slots=True)
class Identity:
    """单主体、单宿主的身份默认值；多用户场景由调用方覆盖。"""

    subject_id: str
    host_id: str


@dataclass(frozen=True, slots=True)
class EvidenceDefaults:
    """spoken/inferred 证据的通用授权默认；allow_cloud_read 由 cloud_read_default 决定。"""

    allow_local_read: bool
    allow_inference: bool


@dataclass(frozen=True, slots=True)
class SourceDefaults:
    """observed/tool 证据的保守授权默认：local✓、cloud✗、infer✓。"""

    allow_local_read: bool
    allow_cloud_read: bool
    allow_inference: bool


@dataclass(frozen=True, slots=True)
class Config:
    consolidation: Consolidation
    half_life_days: Mapping[ContentType, float]
    expire_after_days: Mapping[ContentType, int]
    #: 跨会话趋势的时间窗口与最低出现次数。
    trend_window_days: int
    trend_min_count: int
    attribution: Attribution
    asking: Asking
    min_effective_confidence: int
    carrier_rank: Mapping[str, int]
    min_id_prefix: int
    day_ms: int
    #: 身份默认(perceive/ingest 缺省 subject_id/host_id; 纳入 shared)。
    identity: Identity
    #: 存储层授权默认（evidence.put 补；跨语言授权约束，纳入 shared）。
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
        asking=Asking(
            max_asks=c["asking"]["maxAsks"],
            confidence_band=ConfidenceBand(min=c["asking"]["confidenceBand"]["min"], max=c["asking"]["confidenceBand"]["max"]),
            askable_statuses=tuple(c["asking"]["askableStatuses"]),
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
    """根据隐私模式返回 allow_cloud_read 默认值；隐私模式下默认禁止云读取。"""
    return not c.privacy_mode


def resolve_lang() -> Lang:
    """根据 MEMOWEFT_LANG 返回库语言；仅 zh 映射为中文，其余值使用英文。

    只影响提示词与后备文案，不参与置信度计算。
    """
    return "zh" if os.environ.get("MEMOWEFT_LANG") == "zh" else "en"
