"""Experimental Python parity implementation for MemoWeft.

The stable top-level exports are currently limited to the rule kernel. Additional
storage, portable-bundle, and write-path modules are verified inside the monorepo
but do not yet form a feature-complete public Python SDK.
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
