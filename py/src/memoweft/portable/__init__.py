"""便携记忆包(Portable Memory Bundle)—— 移植自 src/portable(1.3 · D-0042 Phase 1c + D-0043 P2-旁)。

parity:validate_bundle 逐字对拍 shared/parity/bundle-validate.json;
      import_bundle 完整 ImportPlan 对拍 shared/parity/import.json;往返保真对拍 shared/parity/bundle.json。
"""
from __future__ import annotations

from .importer import import_bundle
from .model import (
    BUNDLE_FORMAT,
    BUNDLE_SCHEMA_VERSION,
    ImportCounts,
    ImportDuplicates,
    ImportMode,
    ImportPlan,
)
from .validate import ValidateResult, validate_bundle

__all__ = [
    "BUNDLE_FORMAT",
    "BUNDLE_SCHEMA_VERSION",
    "ValidateResult",
    "validate_bundle",
    "ImportCounts",
    "ImportDuplicates",
    "ImportMode",
    "ImportPlan",
    "import_bundle",
]
