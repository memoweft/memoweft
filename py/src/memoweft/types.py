"""领域枚举与数据形状；这些跨语言契约与 TypeScript 模型保持一致。

契约来源：ContentType、FormedBy 与 CredStatus 来自 cognition/model.ts；
SourceKind 来自 evidence/model.ts；ResponseAct 与 PropositionOrigin 来自 interaction/model.ts。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

ContentType = Literal["fact", "preference", "goal", "project", "state", "trait", "hypothesis", "trend"]
FormedBy = Literal["stated", "observed", "ruled", "confirmed", "inferred"]
CredStatus = Literal["candidate", "low", "limited", "stable", "conflicted", "contested"]
SourceKind = Literal["spoken", "inferred", "observed", "tool"]
ResponseAct = Literal["affirm", "negate", "select", "elaborate", "ask", "none", "other"]
PropositionOrigin = Literal["user_stated", "assistant_proposed"]

# 载体维的三个取值 —— FormedBy 的子集(deriveFormedBy 只算这三个,见 formed_by.py)。
CarrierFormedBy = Literal["stated", "confirmed", "observed"]


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    """computeConfidence 的输入。"""

    content_type: ContentType
    formed_by: FormedBy
    support_count: int
    contradict_count: int


@dataclass(frozen=True, slots=True)
class Resolution:
    """一条证据的语义解析(deriveFormedBy 只读这两维)。"""

    response_act: Optional[ResponseAct]
    proposition_origin: Optional[PropositionOrigin]


@dataclass(frozen=True, slots=True)
class CarrierInput:
    """deriveFormedBy 的单条支持证据输入。"""

    source_kind: SourceKind
    preceding_ai_context: Optional[str]
    resolution: Optional[Resolution]


# ── 证据 / 事件 / 认知的落库形状与写入入参 ──
#   形状与 evidence/model.ts、event/model.ts、cognition/model.ts 保持一致；dataclass(slots) 用于内部表示；
#   frozen 用于读结构(不可变),入参/patch 非 frozen(带默认 / 哨兵)。

EvidenceRelation = Literal["support", "contradict"]


@dataclass(frozen=True, slots=True)
class Evidence:
    """落库后的完整证据形状，与 evidence/model.ts 保持一致。
    注:preceding_ai_context 【故意不在读结构里】( 结构墙)——只经 put/insert 写、
       经 EvidenceStore.preceding_ai_context_of 专用只读取,永不进 Evidence。"""

    id: str
    subject_id: str
    source_kind: SourceKind
    host_id: str
    origin_id: Optional[str]
    occurred_at: str
    recorded_at: str
    raw_content: str
    summary: str
    allow_local_read: bool
    allow_cloud_read: bool
    allow_inference: bool
    corrects_evidence_id: Optional[str]


@dataclass(slots=True)
class EvidenceInput:
    """证据写入参数。id/recorded_at 由存储层生成；
    occurred_at/summary/授权位缺省时由 put 按 source_kind 规则补默认。"""

    subject_id: str
    source_kind: SourceKind
    host_id: str
    raw_content: str
    origin_id: Optional[str] = None
    occurred_at: Optional[str] = None
    summary: Optional[str] = None
    allow_local_read: Optional[bool] = None
    allow_cloud_read: Optional[bool] = None
    allow_inference: Optional[bool] = None
    corrects_evidence_id: Optional[str] = None
    #: 上一轮【AI 那句】作为非证据上下文：只写入，经 preceding_ai_context_of 专用读取，永不成为证据。
    preceding_ai_context: Optional[str] = None


@dataclass(frozen=True, slots=True)
class Event:
    """对话情境化事件摘要，并关联其覆盖的原话证据。"""

    id: str
    subject_id: str
    summary: str
    occurred_at: str
    created_at: str


@dataclass(slots=True)
class EventInput:
    """事件写入参数。"""

    subject_id: str
    summary: str
    occurred_at: str
    evidence_ids: list[str]


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    """溯源链中的证据与认知关系。"""

    evidence_id: str
    relation: EvidenceRelation


@dataclass(frozen=True, slots=True)
class Cognition:
    """落库后的完整认知形状。"""

    id: str
    subject_id: str
    content: str
    content_type: ContentType
    formed_by: FormedBy
    confidence: int
    cred_status: CredStatus
    scope: Optional[str]
    valid_at: Optional[str]
    invalid_at: Optional[str]
    asked_at: Optional[str]
    archived_at: Optional[str]
    muted_at: Optional[str]
    created_at: str
    updated_at: str


@dataclass(slots=True)
class CognitionInput:
    """认知写入参数；id 与时间由存储层生成，confidence/cred_status 由 consolidate 计算。"""

    subject_id: str
    content: str
    content_type: ContentType
    formed_by: FormedBy
    confidence: int
    cred_status: CredStatus
    scope: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    evidence: Optional[list[EvidenceLink]] = None


class _Unset:
    """区分未提供字段（保留原值）与显式 None（复位），对应 TS CognitionPatch 的 undefined 语义。"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


#: CognitionPatch 三态字段的「没传」标记(单例)。
UNSET = _Unset()


@dataclass(slots=True)
class CognitionPatch:
    """cognition.update 的 patch。content/confidence/cred_status/formed_by 使用 None 保留原值；
    scope/invalid_at/asked_at/archived_at/muted_at 使用 UNSET 保留、None 复位，对应 TS patch 语义。"""

    content: Optional[str] = None
    confidence: Optional[int] = None
    cred_status: Optional[CredStatus] = None
    formed_by: Optional[FormedBy] = None
    scope: "str | None | _Unset" = UNSET
    invalid_at: "str | None | _Unset" = UNSET
    asked_at: "str | None | _Unset" = UNSET
    archived_at: "str | None | _Unset" = UNSET
    muted_at: "str | None | _Unset" = UNSET


# ── 语言 / 模型 tier(跨模块共用)──
Lang = Literal["zh", "en"]
#: 模型 tier，用于隐私读取边界，并由 privacy 与 llm 模块共享。
ModelTier = Literal["cloud", "local"]


# ── 交互语义模型（v0.6，Python 实现） ──
#   交互形状与 interaction/model.ts 保持一致；ResponseAct/PropositionOrigin 定义在枚举区。

PromptAct = Literal["propose", "ask", "state", "none", "other"]
AssertionStrength = Literal["explicit", "weak", "none"]


@dataclass(frozen=True, slots=True)
class VisibleTurn:
    """仅包含用户可见内容的交互轮；role、content 字段顺序参与 hash_context 的 JSON 字节契约。"""

    role: Literal["user", "assistant", "tool"]
    content: str


@dataclass(frozen=True, slots=True)
class InteractionContext:
    """交互上下文快照；该结构不产生 Cognition，也不进入证据存储。"""

    id: str
    subject_id: str
    conversation_id: str
    episode_id: str
    context: list[VisibleTurn]
    context_hash: str
    created_at: str


@dataclass(slots=True)
class InteractionContextInput:
    subject_id: str
    conversation_id: str
    episode_id: str
    context: list[VisibleTurn]


@dataclass(frozen=True, slots=True)
class SemanticResolution:
    """语义解析结果；该结构用于解释，不作为证据。"""

    id: str
    evidence_id: str
    resolved_content: str
    response_act: Optional[ResponseAct]
    prompt_act: Optional[PromptAct]
    proposition_origin: Optional[PropositionOrigin]
    assertion_strength: Optional[AssertionStrength]
    required_context: Optional[str]
    resolver_version: str
    created_at: str


@dataclass(slots=True)
class SemanticResolutionInput:
    evidence_id: str
    resolved_content: str
    resolver_version: str
    response_act: Optional[ResponseAct] = None
    prompt_act: Optional[PromptAct] = None
    proposition_origin: Optional[PropositionOrigin] = None
    assertion_strength: Optional[AssertionStrength] = None
    required_context: Optional[str] = None
