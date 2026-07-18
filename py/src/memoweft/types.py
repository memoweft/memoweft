"""领域枚举与形状 —— 与 TS 的 union 类型逐字对齐(跨语言资产,拼错即数据不兼容)。

对齐锚点:
  ContentType  ← src/cognition/model.ts:15-23
  FormedBy     ← src/cognition/model.ts:29
  CredStatus   ← src/cognition/model.ts:32
  SourceKind   ← src/evidence/model.ts:12
  ResponseAct / PropositionOrigin ← src/interaction/model.ts:16,20
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

ContentType = Literal["fact", "preference", "goal", "project", "state", "trait", "hypothesis", "trend"]
FormedBy = Literal["stated", "observed", "ruled", "confirmed", "inferred"]
CredStatus = Literal["candidate", "low", "limited", "stable", "conflicted"]
SourceKind = Literal["spoken", "inferred", "observed", "tool"]
ResponseAct = Literal["affirm", "negate", "select", "elaborate", "ask", "none", "other"]
PropositionOrigin = Literal["user_stated", "assistant_proposed"]

# 载体维的三个取值 —— FormedBy 的子集(deriveFormedBy 只算这三个,见 formed_by.py)。
CarrierFormedBy = Literal["stated", "confirmed", "observed"]


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    """computeConfidence 的输入(confidence.ts:13-18)。"""

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
    """deriveFormedBy 的逐条支持证据输入(deriveFormedBy.ts:50-56)。"""

    source_kind: SourceKind
    preceding_ai_context: Optional[str]
    resolution: Optional[Resolution]


# ── 证据 / 事件 / 认知的落库形状与写入入参(Phase 2 · P2-1a)──
#   对齐:evidence/model.ts、event/model.ts、cognition/model.ts。dataclass(slots) 内部形状;
#   frozen 用于读结构(不可变),入参/patch 非 frozen(带默认 / 哨兵)。

EvidenceRelation = Literal["support", "contradict"]


@dataclass(frozen=True, slots=True)
class Evidence:
    """一条证据(落库后的完整形状,evidence/model.ts:15-41)。
    注:preceding_ai_context 【故意不在读结构里】(D-0033 结构墙)——只经 put/insert 写、
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
    """写入证据的入参(evidence/model.ts:49-73)。id/recorded_at 由存储层生成;
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
    #: 上一轮【AI 那句】只读上下文(D-0033):只写入、经 preceding_ai_context_of 取,永不成证据(3a/3d)。
    preceding_ai_context: Optional[str] = None


@dataclass(frozen=True, slots=True)
class Event:
    """一个事件(event/model.ts:10-18):对话情境化摘要,挂回覆盖的原话证据。"""

    id: str
    subject_id: str
    summary: str
    occurred_at: str
    created_at: str


@dataclass(slots=True)
class EventInput:
    """写入事件的入参(event/model.ts:20-26)。"""

    subject_id: str
    summary: str
    occurred_at: str
    evidence_ids: list[str]


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    """溯源链上一条证据与认知的关系(cognition/model.ts:37-40)。"""

    evidence_id: str
    relation: EvidenceRelation


@dataclass(frozen=True, slots=True)
class Cognition:
    """一条认知(落库后的完整形状,cognition/model.ts:43-67)。"""

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
    """写入认知的入参(cognition/model.ts:70-82);id/时间由存储层生成,confidence/cred_status 由 consolidate 算好传入。"""

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
    """哨兵:区分「字段没传」(保留原值)与「传了 None」(复位)——复刻 TS CognitionPatch 的 `=== undefined`。"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


#: CognitionPatch 三态字段的「没传」标记(单例)。
UNSET = _Unset()


@dataclass(slots=True)
class CognitionPatch:
    """cognition.update 的 patch(cognition/model.ts:87-104)。两组语义:
    content/confidence/cred_status/formed_by:None = 保留原值(复刻 TS `?? cur`);
    scope/invalid_at/asked_at/archived_at/muted_at:UNSET = 保留、None = 复位(复刻 TS `=== undefined ? cur : patch`)。"""

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
#: 模型 tier(隐私读取门用;TS 在 llm/client.ts:21,py 归 types 供 privacy/llm 共用)。
ModelTier = Literal["cloud", "local"]


# ── 交互语义模型(v0.6 · D-0034;Phase 2 P2-1b 移植)──
#   对齐 interaction/model.ts。ResponseAct/PropositionOrigin 已在上(枚举区)。

PromptAct = Literal["propose", "ask", "state", "none", "other"]
AssertionStrength = Literal["explicit", "weak", "none"]


@dataclass(frozen=True, slots=True)
class VisibleTurn:
    """可见交互轮(interaction/model.ts:25-28):只含用户可见内容。字段序 role,content 用于 hash_context 的 JSON 字节。"""

    role: Literal["user", "assistant", "tool"]
    content: str


@dataclass(frozen=True, slots=True)
class InteractionContext:
    """一条交互上下文快照(interaction/model.ts:31-42)。不产 Cognition、永不成证据(3a)。"""

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
    """一条语义解析(interaction/model.ts:52-67)。解释结果、不是证据(3a)。"""

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
