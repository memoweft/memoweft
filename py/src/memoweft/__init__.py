"""MemoWeft Python 2.0 Core candidate.

The production Core integrations coexist with a parity-insurance layer, while the
top-level public facade remains intentionally limited to the rule kernel. This is
not a feature-complete general-purpose SDK: the Memory Experience product form has
not yet formed or received a version, and this identity does not claim Beta, GA,
release, or publication.
"""
from __future__ import annotations

from .config import CONFIG, Config
from .confidence import compute_confidence, derive_cred_status, is_hedged_stated, is_transient
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
    HedgeInput,
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
    "is_hedged_stated",
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
    "HedgeInput",
    "PropositionOrigin",
    "Resolution",
    "ResponseAct",
    "SourceKind",
]
