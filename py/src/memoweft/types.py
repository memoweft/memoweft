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
