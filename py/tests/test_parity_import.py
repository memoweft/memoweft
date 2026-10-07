"""importBundle 完整 ImportPlan parity:与 TS(shared/parity/import.json)一致。

钉 dryRun 只算不写 / merge 写入 / 幂等 duplicates / 非法包拒写 / originId 撞库丢悬空 join + 告警 /
悬空 correctsEvidenceId 置空并记录告警。
"""
from __future__ import annotations

import copy
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from conftest import parity

from memoweft.portable import ImportPlan, derive_bundle_id, import_bundle
from memoweft.store import make_transaction, open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.store.semantic_resolution import SqliteSemanticResolutionStore
from memoweft.types import EvidenceInput

T = "2026-01-01T00:00:00.000Z"


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _stores(db: sqlite3.Connection) -> dict[str, Any]:
    return {
        "evidence_store": SqliteEvidenceStore(db, clock=_clock),
        "event_store": SqliteEventStore(db, clock=_clock),
        "cognition_store": SqliteCognitionStore(db, clock=_clock),
        "interaction_context_store": SqliteInteractionContextStore(db, clock=_clock),
        "semantic_resolution_store": SqliteSemanticResolutionStore(db, clock=_clock),
    }


def _dump(plan: ImportPlan) -> dict[str, Any]:
    return {
        "mode": plan.mode, "valid": plan.valid, "errors": plan.errors, "warnings": plan.warnings,
        "counts": {
            "evidence": plan.counts.evidence, "events": plan.counts.events, "cognitions": plan.counts.cognitions,
            "eventEvidence": plan.counts.event_evidence, "cognitionEvidence": plan.counts.cognition_evidence,
            "interactionContexts": plan.counts.interaction_contexts, "semanticResolutions": plan.counts.semantic_resolutions,
            "entities": plan.counts.entities, "entityEvidence": plan.counts.entity_evidence,
            "relationships": plan.counts.relationships,
            "worldEvents": plan.counts.world_events, "relationshipEvidence": plan.counts.relationship_evidence,
            "worldEventEvidence": plan.counts.world_event_evidence, "cognitionTargets": plan.counts.cognition_targets,
            "retractions": plan.counts.retractions, "cognitionTransitions": plan.counts.cognition_transitions,
            "worldItemLifecycle": plan.counts.world_item_lifecycle,
            "evidenceTombstones": plan.counts.evidence_tombstones,
        },
        "duplicates": {
            "evidence": plan.duplicates.evidence, "events": plan.duplicates.events,
            "cognitions": plan.duplicates.cognitions, "entities": plan.duplicates.entities,
            "relationships": plan.duplicates.relationships, "worldEvents": plan.duplicates.world_events,
            "retractions": plan.duplicates.retractions,
            "cognitionTransitions": plan.duplicates.cognition_transitions,
            "worldItemLifecycle": plan.duplicates.world_item_lifecycle,
        },
        "bundleId": plan.bundle_id, "sourceSubjectId": plan.source_subject_id,
        "targetSubjectId": plan.target_subject_id,
        "targetWorldRevision": plan.target_world_revision,
        "targetSnapshotHash": plan.target_snapshot_hash,
        "conflicts": plan.conflicts, "wouldAdvanceRevision": plan.would_advance_revision,
        "planHash": plan.plan_hash, "commandId": plan.command_id,
        "receiptId": plan.receipt_id,
    }


def _db_state(st: dict[str, Any]) -> dict[str, int]:
    return {
        "evidence": len(st["evidence_store"].all()),
        "events": len(st["event_store"].all("owner")),
        "cognitions": len(st["cognition_store"].all("owner")),
    }


def _run(bundle: Any, mode: Any, pre_seed: Optional[Callable[[dict[str, Any]], None]] = None) -> tuple[ImportPlan, dict[str, int]]:
    db = open_db(":memory:")
    try:
        st = _stores(db)
        if pre_seed is not None:
            pre_seed(st)
        plan = import_bundle(bundle, **st, transaction=make_transaction(db), mode=mode)
        return plan, _db_state(st)
    finally:
        db.close()


def test_import_matches_ts() -> None:
    want: Any = parity("import.json")
    good: Any = parity("bundle.json")

    # ① dryRun:只算不写
    plan, state = _run(good, "dryRun")
    assert _dump(plan) == want["dryRun"]["plan"]
    assert state == want["dryRun"]["dbState"]

    # ② merge:实际写入
    plan, state = _run(good, "merge")
    assert _dump(plan) == want["merge"]["plan"]
    assert state == want["merge"]["dbState"]

    # ③ 幂等:同库连导两次
    db = open_db(":memory:")
    try:
        st = _stores(db)
        tx = make_transaction(db)
        first = import_bundle(good, **st, transaction=tx, mode="merge")
        second = import_bundle(good, **st, transaction=tx, mode="merge")
        assert _dump(first) == want["twice"]["first"]
        assert _dump(second) == want["twice"]["second"]
        assert _db_state(st) == want["twice"]["dbState"]
    finally:
        db.close()

    # ④ 非法包(悬空溯源)→ 拒写
    invalid = copy.deepcopy(good)
    invalid["data"]["eventEvidence"][0]["evidenceId"] = "ghost"
    invalid["bundleId"] = derive_bundle_id(invalid)
    plan, state = _run(invalid, "merge")
    assert _dump(plan) == want["invalid"]["plan"]
    assert state == want["invalid"]["dbState"]

    # ⑤ originId 撞库 → 跳过该条 + 丢弃指向它的 join + 告警
    with_origin = copy.deepcopy(good)
    with_origin["data"]["evidence"][0]["originId"] = "origin-x"
    with_origin["bundleId"] = derive_bundle_id(with_origin)

    def seed(st: dict[str, Any]) -> None:
        st["evidence_store"].put(
            EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", occurred_at=T, raw_content="库里已有", origin_id="origin-x")
        )

    plan, state = _run(with_origin, "merge", seed)
    assert _dump(plan) == want["originCollision"]["plan"]
    assert state == want["originCollision"]["dbState"]

    # ⑥ 悬空 correctsEvidenceId → 置空 + 告警
    dangling = copy.deepcopy(good)
    dangling["data"]["evidence"][1]["correctsEvidenceId"] = "ghost-corrects"
    dangling["bundleId"] = derive_bundle_id(dangling)
    db = open_db(":memory:")
    try:
        st = _stores(db)
        plan = import_bundle(dangling, **st, transaction=make_transaction(db), mode="merge")
        got = st["evidence_store"].get("ev-2")
        assert _dump(plan) == want["danglingCorrects"]["plan"]
        assert _db_state(st) == want["danglingCorrects"]["dbState"]
        assert got is not None and got.corrects_evidence_id == want["danglingCorrects"]["correctsAfter"]  # 置空
    finally:
        db.close()
