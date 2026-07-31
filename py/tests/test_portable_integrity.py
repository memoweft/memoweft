"""Portable 导入的删除单调性、字段守门和 semantic resolution 完整性。"""
from __future__ import annotations

import copy
import sqlite3
from datetime import datetime, timezone
from typing import Any

from memoweft.portable import import_bundle, validate_bundle
from memoweft.store import make_transaction, open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.store.semantic_resolution import SqliteSemanticResolutionStore
from memoweft.types import SemanticResolutionInput

T = "2026-07-31T00:00:00.000Z"


def _clock() -> datetime:
    return datetime(2026, 7, 31, tzinfo=timezone.utc)


def _stores(db: sqlite3.Connection) -> dict[str, Any]:
    return {
        "evidence_store": SqliteEvidenceStore(db, clock=_clock),
        "event_store": SqliteEventStore(db, clock=_clock),
        "cognition_store": SqliteCognitionStore(db, clock=_clock),
        "interaction_context_store": SqliteInteractionContextStore(db, clock=_clock),
        "semantic_resolution_store": SqliteSemanticResolutionStore(db, clock=_clock),
    }


def _bundle() -> dict[str, Any]:
    return {
        "format": "memoweft-bundle", "schemaVersion": 2, "exportedAt": T,
        "memoWeftVersion": "1.0.0-rc.1", "subjectId": "owner",
        "source": {"hostId": "test", "exportMode": "full"},
        "data": {
            "evidence": [{
                "id": "ev-1", "subjectId": "owner", "sourceKind": "spoken", "hostId": "test",
                "originId": None, "occurredAt": T, "recordedAt": T, "rawContent": "用户原话",
                "summary": "用户原话", "allowLocalRead": True, "allowCloudRead": False,
                "allowInference": True, "correctsEvidenceId": None,
            }],
            "events": [], "eventEvidence": [], "cognitions": [], "cognitionEvidence": [],
            "unconsolidatedEventIds": [],
        },
        "metadata": {"counts": {"evidence": 1, "events": 0, "cognitions": 0}, "notes": []},
    }


def test_validate_rejects_bad_date_relation_orphan_and_duplicate_resolution() -> None:
    bad_date = _bundle()
    bad_date["data"]["evidence"][0]["occurredAt"] = "not-a-date"
    assert not validate_bundle(bad_date).valid

    bad_relation = _bundle()
    bad_relation["data"]["cognitions"] = [{
        "id": "cog-1", "subjectId": "owner", "content": "判断", "contentType": "fact",
        "formedBy": "stated", "confidence": 600, "credStatus": "limited", "scope": None,
        "validAt": None, "invalidAt": None, "askedAt": None, "createdAt": T, "updatedAt": T,
    }]
    bad_relation["data"]["cognitionEvidence"] = [
        {"cognitionId": "cog-1", "evidenceId": "ev-1", "relation": "neither"}
    ]
    assert not validate_bundle(bad_relation).valid

    orphan = _bundle()
    orphan["data"]["semanticResolutions"] = [_resolution("sem-orphan", "missing")]
    assert not validate_bundle(orphan).valid

    duplicate = _bundle()
    duplicate["data"]["semanticResolutions"] = [_resolution("sem-1", "ev-1"), _resolution("sem-2", "ev-1")]
    assert not validate_bundle(duplicate).valid


def test_import_rejects_malicious_mixed_subject_provenance_with_zero_writes() -> None:
    db = open_db(":memory:")
    try:
        stores = _stores(db)
        bundle = _bundle()
        bundle["data"]["evidence"][0]["subjectId"] = "other-user"
        bundle["data"]["cognitions"] = [{
            "id": "cog-a", "subjectId": "owner", "content": "试图泄露 other-user 摘要",
            "contentType": "fact", "formedBy": "stated", "confidence": 600,
            "credStatus": "limited", "scope": None, "validAt": None, "invalidAt": None,
            "askedAt": None, "createdAt": T, "updatedAt": T,
        }]
        bundle["data"]["cognitionEvidence"] = [
            {"cognitionId": "cog-a", "evidenceId": "ev-1", "relation": "support"}
        ]
        validation = validate_bundle(bundle)
        assert not validation.valid
        assert any("crosses subjects" in error for error in validation.errors)
        plan = import_bundle(bundle, **stores, transaction=make_transaction(db))
        assert not plan.valid
        assert stores["evidence_store"].all() == []
        assert stores["cognition_store"].all("owner") == []
    finally:
        db.close()


def test_import_rejects_duplicate_event_and_cognition_joins_with_zero_writes() -> None:
    db = open_db(":memory:")
    try:
        stores = _stores(db)
        bundle = _bundle()
        bundle["data"]["events"] = [{
            "id": "evt-1", "subjectId": "owner", "summary": "事件", "occurredAt": T, "createdAt": T,
        }]
        bundle["data"]["eventEvidence"] = [
            {"eventId": "evt-1", "evidenceId": "ev-1"},
            {"eventId": "evt-1", "evidenceId": "ev-1"},
        ]
        bundle["data"]["cognitions"] = [{
            "id": "cog-1", "subjectId": "owner", "content": "判断", "contentType": "fact",
            "formedBy": "stated", "confidence": 600, "credStatus": "limited", "scope": None,
            "validAt": None, "invalidAt": None, "askedAt": None, "createdAt": T, "updatedAt": T,
        }]
        bundle["data"]["cognitionEvidence"] = [
            {"cognitionId": "cog-1", "evidenceId": "ev-1", "relation": "support"},
            {"cognitionId": "cog-1", "evidenceId": "ev-1", "relation": "support"},
        ]
        validation = validate_bundle(bundle)
        assert not validation.valid
        assert any("duplicate link" in error for error in validation.errors)
        plan = import_bundle(bundle, **stores, transaction=make_transaction(db))
        assert not plan.valid
        assert stores["evidence_store"].all() == []
        assert stores["event_store"].all("owner") == []
        assert stores["cognition_store"].all("owner") == []
    finally:
        db.close()


def test_validate_and_dry_run_accept_empty_evidence_from_stable_write_shape() -> None:
    db = open_db(":memory:")
    try:
        stores = _stores(db)
        bundle = _bundle()
        bundle["data"]["evidence"][0]["rawContent"] = ""
        bundle["data"]["evidence"][0]["summary"] = ""
        assert validate_bundle(bundle).valid
        plan = import_bundle(bundle, **stores, transaction=make_transaction(db), mode="dryRun")
        assert plan.valid
        assert plan.counts.evidence == 1
    finally:
        db.close()


def test_validate_accepts_integral_float_confidence_like_json_number() -> None:
    bundle = _bundle()
    bundle["data"]["cognitions"] = [{
        "id": "cog-1", "subjectId": "owner", "content": "判断", "contentType": "fact",
        "formedBy": "stated", "confidence": 600.0, "credStatus": "limited", "scope": None,
        "validAt": None, "invalidAt": None, "askedAt": None, "createdAt": T, "updatedAt": T,
    }]
    bundle["data"]["cognitionEvidence"] = [
        {"cognitionId": "cog-1", "evidenceId": "ev-1", "relation": "support"}
    ]
    assert validate_bundle(bundle).valid


def _resolution(record_id: str, evidence_id: str) -> dict[str, Any]:
    return {
        "id": record_id, "evidenceId": evidence_id, "resolvedContent": "解析", "responseAct": None,
        "promptAct": None, "propositionOrigin": None, "assertionStrength": None,
        "requiredContext": None, "resolverVersion": "r1", "createdAt": T,
    }


def test_import_does_not_revive_tombstoned_evidence_event_summary_or_derived_cognition() -> None:
    db = open_db(":memory:")
    try:
        stores = _stores(db)
        bundle = _bundle()
        bundle["data"]["events"] = [{
            "id": "evt-1", "subjectId": "owner", "summary": "不应复活的事件摘要",
            "occurredAt": T, "createdAt": T,
        }]
        bundle["data"]["eventEvidence"] = [{"eventId": "evt-1", "evidenceId": "ev-1"}]
        bundle["data"]["cognitions"] = [{
            "id": "cog-1", "subjectId": "owner", "content": "不应复活的派生画像",
            "contentType": "preference", "formedBy": "stated", "confidence": 600,
            "credStatus": "limited", "scope": None, "validAt": None, "invalidAt": None,
            "askedAt": None, "createdAt": T, "updatedAt": T,
        }]
        bundle["data"]["cognitionEvidence"] = [
            {"cognitionId": "cog-1", "evidenceId": "ev-1", "relation": "support"}
        ]
        assert import_bundle(bundle, **stores, transaction=make_transaction(db)).valid
        assert stores["evidence_store"].remove("ev-1")
        assert stores["event_store"].remove("evt-1")
        assert stores["cognition_store"].remove("cog-1")

        plan = import_bundle(bundle, **stores, transaction=make_transaction(db))
        assert plan.valid
        assert plan.counts.evidence == 0
        assert plan.counts.events == 0
        assert plan.counts.cognitions == 0
        assert plan.counts.event_evidence == 0
        assert plan.counts.cognition_evidence == 0
        assert plan.duplicates.evidence == 1
        assert stores["evidence_store"].get("ev-1") is None
        assert stores["event_store"].get("evt-1") is None
        assert stores["cognition_store"].get("cog-1") is None
        assert all(event.summary != "不应复活的事件摘要" for event in stores["event_store"].all("owner"))
        assert all(cognition.content != "不应复活的派生画像" for cognition in stores["cognition_store"].all("owner"))
        assert db.execute(
            "SELECT 1 FROM evidence WHERE id = ? AND deleted_at IS NOT NULL", ("ev-1",)
        ).fetchone() is not None
        assert any("tombstoned" in warning for warning in plan.warnings)
        assert any("derived summary revival" in warning for warning in plan.warnings)
        assert any("derived content revival" in warning for warning in plan.warnings)
    finally:
        db.close()


def test_import_keeps_existing_semantic_resolution_instead_of_inserting_a_second_one() -> None:
    db = open_db(":memory:")
    try:
        stores = _stores(db)
        bundle = _bundle()
        assert import_bundle(bundle, **stores, transaction=make_transaction(db)).valid
        stores["semantic_resolution_store"].put(
            SemanticResolutionInput(evidence_id="ev-1", resolved_content="既有解析", resolver_version="existing")
        )
        bundle = copy.deepcopy(bundle)
        bundle["data"]["semanticResolutions"] = [_resolution("sem-import", "ev-1")]

        plan = import_bundle(bundle, **stores, transaction=make_transaction(db))
        assert plan.valid
        assert plan.counts.semantic_resolutions == 0
        assert len(stores["semantic_resolution_store"].for_evidence_ids(["ev-1"])) == 1
        assert any("one resolution per evidence" in warning for warning in plan.warnings)
    finally:
        db.close()
