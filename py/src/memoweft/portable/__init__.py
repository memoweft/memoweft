"""便携记忆包（Portable Memory Bundle），与 TypeScript 实现共享契约资产。

共享 parity 资产分别验证 validate_bundle、import_bundle 的完整 ImportPlan，以及便携包往返保真语义。
"""
from __future__ import annotations

from .builder import MEMOWEFT_VERSION, build_bundle
from .importer import import_bundle
from .model import (
    BUNDLE_FORMAT,
    BUNDLE_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    ImportCounts,
    ImportDuplicates,
    ImportMode,
    ImportPlan,
    canonical_json,
    canonical_sha256,
    derive_bundle_id,
    derive_plan_ids,
)
from .validate import ValidateResult, validate_bundle

__all__ = [
    "BUNDLE_FORMAT",
    "BUNDLE_SCHEMA_VERSION",
    "PLAN_SCHEMA_VERSION",
    "MEMOWEFT_VERSION",
    "ValidateResult",
    "validate_bundle",
    "ImportCounts",
    "ImportDuplicates",
    "ImportMode",
    "ImportPlan",
    "canonical_json",
    "canonical_sha256",
    "derive_bundle_id",
    "derive_plan_ids",
    "import_bundle",
    "build_bundle",
]
