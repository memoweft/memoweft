"""MemoWeft — 可移植 AI 长期记忆(Python 移植 · 1.3 · D-0042)。

Phase 1 = **parity 内核**:纯逻辑不变量层(置信度/可信状态/载体维/衰减/id 回显/哈希嵌入),
与 TS 源逐位对拍(读 ../shared/parity/*.json 验证)。存储 / 便携包 / LLM 写路径为后续阶段。
"""
from __future__ import annotations

from .config import CONFIG, Config
from .confidence import compute_confidence, derive_cred_status, is_transient
from .decay import decay_factor, effective_confidence, half_life_of
from .echoed_id import MIN_ID_PREFIX, resolve_echoed_id
from .formed_by import derive_formed_by
from .hash_embedder import DEFAULT_DIM, HashEmbedder, fnv1a32, tokenize
from .types import (
    CarrierFormedBy,
    CarrierInput,
    ConfidenceInputs,
    ContentType,
    CredStatus,
    FormedBy,
    PropositionOrigin,
    Resolution,
    ResponseAct,
    SourceKind,
)

__all__ = [
    "CONFIG",
    "Config",
    "compute_confidence",
    "derive_cred_status",
    "is_transient",
    "decay_factor",
    "effective_confidence",
    "half_life_of",
    "MIN_ID_PREFIX",
    "resolve_echoed_id",
    "derive_formed_by",
    "DEFAULT_DIM",
    "HashEmbedder",
    "fnv1a32",
    "tokenize",
    "CarrierFormedBy",
    "CarrierInput",
    "ConfidenceInputs",
    "ContentType",
    "CredStatus",
    "FormedBy",
    "PropositionOrigin",
    "Resolution",
    "ResponseAct",
    "SourceKind",
]
