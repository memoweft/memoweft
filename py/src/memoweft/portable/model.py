"""Portable v4 identity and import-plan models shared with TypeScript.

The identity functions deliberately accept plain JSON values.  They are used
by the low-level importer, the Trust service and the cross-language fixtures,
so there is one hashing rule instead of host-specific approximations.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from typing import Any, Literal

#: 便携包格式标记。
BUNDLE_FORMAT = "memoweft-bundle"
#: v2 adds interaction data; v3 adds World objects; v4 adds durable history,
#: tombstone/currentness facts, subject remap planning and stable identities.
BUNDLE_SCHEMA_VERSION = 4
PLAN_SCHEMA_VERSION = 1

#: 导入模式:dryRun 只算不写 / merge 实际写入。
ImportMode = Literal["dryRun", "merge"]


@dataclass(slots=True)
class ImportCounts:
    """将写入(dryRun)/ 已写入(merge)的条数。"""

    evidence: int = 0
    events: int = 0
    cognitions: int = 0
    event_evidence: int = 0
    cognition_evidence: int = 0
    interaction_contexts: int = 0
    semantic_resolutions: int = 0
    entities: int = 0
    entity_evidence: int = 0
    relationships: int = 0
    world_events: int = 0
    relationship_evidence: int = 0
    world_event_evidence: int = 0
    cognition_targets: int = 0
    retractions: int = 0
    cognition_transitions: int = 0
    world_item_lifecycle: int = 0
    evidence_tombstones: int = 0


@dataclass(slots=True)
class ImportDuplicates:
    """按 id(或 originId)判重跳过的条数。"""

    evidence: int = 0
    events: int = 0
    cognitions: int = 0
    entities: int = 0
    relationships: int = 0
    world_events: int = 0
    retractions: int = 0
    cognition_transitions: int = 0
    world_item_lifecycle: int = 0


@dataclass(slots=True)
class ImportPlan:
    """导入计划/结果(对齐 model.ts 的 ImportPlan)。"""

    mode: ImportMode
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: ImportCounts = field(default_factory=ImportCounts)
    duplicates: ImportDuplicates = field(default_factory=ImportDuplicates)
    bundle_id: str | None = None
    source_subject_id: str | None = None
    target_subject_id: str | None = None
    target_world_revision: int | None = None
    target_snapshot_hash: str | None = None
    conflicts: list[dict[str, str]] = field(default_factory=list)
    would_advance_revision: bool = False
    plan_hash: str | None = None
    command_id: str | None = None
    receipt_id: str | None = None
    replayed: bool = False


def canonical_json(value: object) -> str:
    """Return the Portable canonical JSON representation.

    Object keys are recursively sorted by ``json.dumps``; arrays preserve the
    schema-defined order.  ``allow_nan=False`` makes non-JSON numbers fail
    closed.  ``ensure_ascii=False`` is part of the Python/TypeScript v4 hash
    contract and keeps the hashed bytes equal to the user's UTF-8 memory.
    """

    def wire_stable(item: object) -> object:
        if isinstance(item, float) and math.isfinite(item) and item.is_integer():
            return 0 if item == 0 else int(item)
        if isinstance(item, list):
            return [wire_stable(child) for child in item]
        if isinstance(item, dict):
            return {key: wire_stable(child) for key, child in item.items()}
        return item

    return json.dumps(
        wire_stable(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_sha256(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def derive_bundle_id(bundle: object) -> str:
    """Derive a v4 bundle id after excluding only the top-level ``bundleId``."""

    if not isinstance(bundle, dict):
        raise TypeError("bundle_must_be_object")
    payload: dict[str, Any] = copy.deepcopy(bundle)
    payload.pop("bundleId", None)
    return f"portable:v4:{canonical_sha256(payload)}"


def derive_plan_ids(payload: object) -> tuple[str, str, str]:
    """Return ``(plan_hash, command_id, receipt_id)`` for a plan payload."""

    digest = canonical_sha256(payload)
    return (
        digest,
        f"portable:command:v1:{digest}",
        f"portable:receipt:v1:{digest}",
    )
