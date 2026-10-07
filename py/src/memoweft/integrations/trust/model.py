"""Stable JSON-native DTO contracts for read-only Trust Query v1."""
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


TRUST_SCHEMA_VERSION = 1
TRUST_CAPABILITIES_VERSION = 1

TrustWorldItemKind = Literal["entity", "relationship", "event", "cognition"]
TrustSurface = Literal["trust_local", "trust_cloud"]
TrustCommandOperation = Literal[
    "update_evidence_permissions",
    "correct_world_item",
    "retract_world_item",
    "forget_evidence",
    "delete_evidence",
    "delete_world_item",
    "archive_world_item",
    "mute_world_item",
]
TrustCommandTargetKind = Literal[
    "evidence", "entity", "relationship", "event", "cognition"
]
TrustCommandResultState = Literal[
    "applied", "no_change", "revision_conflict", "rejected"
]


class CommandEnvelopeV1(TypedDict):
    schema_version: int
    command_id: str
    subject_id: str
    actor: str
    expected_world_revision: int
    operation: TrustCommandOperation
    target_kind: TrustCommandTargetKind
    target_id: str
    payload: dict[str, object]
    submitted_at: str


class CommandReceiptV1(TypedDict):
    schema_version: int
    command_id: str
    accepted: bool
    result_state: TrustCommandResultState
    before_revision: int
    after_revision: int
    affected_ids: list[str]
    transition_ids: list[str]
    result_hash: str
    completed_at: str
    rejection_code: NotRequired[str]
    storage_cleanup: NotRequired[dict[str, str]]


class PermissionsV1(TypedDict):
    allow_local_read: bool
    allow_cloud_read: bool
    allow_inference: bool


class LifecycleV1(TypedDict):
    invalid_at: str | None
    archived_at: str | None
    muted_at: str | None
    deleted_at: str | None
    visible: bool
    currentness_state: str


class EvidenceV1(TypedDict):
    schema_version: int
    subject_id: str
    world_revision: int
    evidence_id: str
    source_kind: str
    host_id: str
    origin_id: str | None
    occurred_at: str
    recorded_at: str
    raw_content: str | None
    summary: str | None
    content_available: bool
    permissions: PermissionsV1
    corrects_evidence_id: str | None
    currentness_state: str
    lifecycle: LifecycleV1


class ProvenanceV1(TypedDict):
    evidence_id: str
    relation: str
    currentness_state: str
    permissions: PermissionsV1
    evidence: EvidenceV1 | None
    linked_world_items: NotRequired[list[dict[str, object]]]
    model_content_available: NotRequired[bool]
    model_denial_reason: NotRequired[str | None]


class TransitionV1(TypedDict):
    transition_id: str
    transition_kind: str
    object_kind: TrustWorldItemKind
    prior_item_id: str | None
    replacement_item_id: str | None
    revision: int | None
    occurred_at: str | None
    evidence_ids: list[str]


class WorldItemV1(TypedDict):
    schema_version: int
    subject_id: str
    world_revision: int
    object_kind: TrustWorldItemKind
    item_id: str
    current_state: str
    lifecycle: LifecycleV1
    permissions: list[PermissionsV1]
    provenance: list[ProvenanceV1]
    transition_history: list[TransitionV1]
    value: dict[str, object]
    created_at: str
    updated_at: str


class TrustQueryError(ValueError):
    """Stable fail-closed error for an invalid or unavailable Trust query."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
