"""N7 Portable v4 Trust service and cross-host migration contract."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from support.json_assertions import as_object, as_objects

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.trust import QueryService
from memoweft.integrations.trust.portable_service import PortableError, PortableService
from memoweft.portable import (
    BUNDLE_SCHEMA_VERSION,
    derive_bundle_id,
    validate_bundle,
)
from memoweft.store import open_db


SHARED = Path(__file__).parents[2] / "shared" / "portable-v4" / "fixtures"
T = "2026-08-25T00:00:00.000Z"


def _clock() -> datetime:
    return datetime(2026, 8, 25, tzinfo=timezone.utc)


def _fixture(name: str) -> dict[str, object]:
    decoded = json.loads((SHARED / name).read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)


def test_shared_v4_fixture_has_stable_identity_and_legacy_readers_remain_compatible() -> None:
    bundle = _fixture("full-v4.json")
    assert BUNDLE_SCHEMA_VERSION == 4
    assert bundle["bundleId"] == derive_bundle_id(bundle)
    assert validate_bundle(bundle).valid
    assert validate_bundle(_fixture("compat-v2.json")).valid
    assert validate_bundle(_fixture("compat-v3.json")).valid


def test_portable_service_symbol_is_a_real_subject_bound_service() -> None:
    assert PortableService.__name__ == "PortableService"


def _service(path: Path, subject: str, host: str) -> PortableService:
    db = open_db(str(path))
    db.close()
    return PortableService(path, subject_id=subject, host_id=host, clock=_clock)


def test_shared_fresh_target_plan_matches_the_cross_language_oracle(
    tmp_path: Path,
) -> None:
    expected = _fixture("fresh-target-plan.json")
    plan = _service(
        tmp_path / "fresh-plan.sqlite3", "host-b-owner", "host-b"
    ).plan_import(_fixture("full-v4.json"))
    assert plan["bundle_id"] == expected["bundleId"]
    assert plan["source_subject_id"] == expected["sourceSubjectId"]
    assert plan["target_subject_id"] == expected["targetSubjectId"]
    assert plan["target_world_revision"] == expected["targetWorldRevision"]
    assert plan["target_snapshot_hash"] == expected["targetSnapshotHash"]
    assert plan["plan_hash"] == expected["planHash"]
    assert plan["command_id"] == expected["commandId"]
    assert plan["receipt_id"] == expected["receiptId"]
    counts = plan["counts"]
    assert isinstance(counts, dict)
    assert {
        "evidence": counts["evidence"],
        "events": counts["events"],
        "cognitions": counts["cognitions"],
        "eventEvidence": counts["event_evidence"],
        "cognitionEvidence": counts["cognition_evidence"],
        "interactionContexts": counts["interaction_contexts"],
        "semanticResolutions": counts["semantic_resolutions"],
        "entities": counts["entities"],
        "entityEvidence": counts["entity_evidence"],
        "relationships": counts["relationships"],
        "worldEvents": counts["world_events"],
        "relationshipEvidence": counts["relationship_evidence"],
        "worldEventEvidence": counts["world_event_evidence"],
        "cognitionTargets": counts["cognition_targets"],
        "retractions": counts["retractions"],
        "cognitionTransitions": counts["cognition_transitions"],
        "worldItemLifecycle": counts["world_item_lifecycle"],
        "evidenceTombstones": counts["evidence_tombstones"],
    } == expected["counts"]


def _rehash(bundle: dict[str, Any]) -> dict[str, Any]:
    bundle["bundleId"] = derive_bundle_id(bundle)
    return bundle


def _replace_subject(value: Any, subject: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_subject(item, subject) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_subject(item, subject) for item in value]
    return "<subject>" if value == subject else value


def test_cross_host_plan_apply_restart_replay_world_and_recall_are_consistent(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "host-a.sqlite3"
    target_path = tmp_path / "host-b.sqlite3"
    source = _service(source_path, "host-a-owner", "host-a")
    fixture = _fixture("full-v4.json")

    source_plan = source.plan_import(fixture)
    assert source_plan["valid"] is True
    assert source_plan["target_subject_id"] == "host-a-owner"
    source_receipt = source.apply_import(
        fixture, plan_hash=str(source_plan["plan_hash"])
    )
    assert source_receipt["result_state"] == "applied"
    assert source_receipt["after_world_revision"] == 1

    source_db = sqlite3.connect(source_path)
    try:
        source_db.execute(
            "UPDATE evidence SET preceding_ai_context = ? WHERE id = 'ev-live'",
            ("assistant-only context must stay on host A",),
        )
        source_db.commit()
    finally:
        source_db.close()

    # Restart host A before export: all data and the first receipt come from
    # SQLite, not process memory.
    source = _service(source_path, "host-a-owner", "host-a")
    exported = source.export_bundle(exported_at=T)
    assert validate_bundle(exported).valid
    assert exported["bundleId"] == derive_bundle_id(exported)
    assert all(
        "precedingAiContext" not in item for item in exported["data"]["evidence"]
    )

    target = _service(target_path, "host-b-owner", "host-b")
    first_plan = target.plan_import(exported)
    second_plan = target.plan_import(exported)
    assert first_plan == second_plan
    assert first_plan["valid"] is True
    assert first_plan["source_subject_id"] == "host-a-owner"
    assert first_plan["target_subject_id"] == "host-b-owner"
    assert first_plan["target_world_revision"] == 0
    assert first_plan["would_advance_revision"] is True
    assert isinstance(first_plan["plan_hash"], str)

    receipt = target.apply_import(
        exported, plan_hash=str(first_plan["plan_hash"])
    )
    assert receipt["result_state"] == "applied"
    assert receipt["target_world_revision"] == 0
    assert receipt["after_world_revision"] == 1
    assert receipt["receipt_id"] == first_plan["receipt_id"]
    assert receipt["command_id"] == first_plan["command_id"]
    assert receipt["replayed"] is False

    db = sqlite3.connect(target_path)
    try:
        assert db.execute(
            "SELECT DISTINCT subject_id FROM evidence"
        ).fetchall() == [("host-b-owner",)]
        assert db.execute("SELECT DISTINCT world_id FROM entity").fetchall() == [
            ("host-b-owner",)
        ]
        assert db.execute(
            "SELECT deleted_at FROM evidence WHERE id = 'ev-deleted'"
        ).fetchone() == ("2026-08-24T00:00:00.000Z",)
        assert db.execute("SELECT COUNT(*) FROM retraction").fetchone() == (1,)
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_transitions"
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT subject_id, object_kind, item_id FROM world_item_lifecycle"
        ).fetchone() == ("host-b-owner", "entity", "ent-owner")
        entity_ledger = db.execute(
            "SELECT content, payload_json FROM evidence_ledger "
            "WHERE json_extract(content, '$.entity_id') = 'ent-owner' "
            "AND json_extract(content, '$.relation') = 'support'"
        ).fetchone()
        assert entity_ledger is not None
        assert json.loads(str(entity_ledger[0])) == {
            "entity_id": "ent-owner",
            "evidence_id": "ev-live",
            "relation": "support",
        }
        assert json.loads(str(entity_ledger[1])) == {
            "schema_version": 1,
            "start": 0,
            "end": 2,
        }
        before_replay_counts = tuple(
            db.execute(
                "SELECT (SELECT COUNT(*) FROM evidence), "
                "(SELECT COUNT(*) FROM cognition), "
                "(SELECT COUNT(*) FROM entity), "
                "(SELECT COUNT(*) FROM portable_import_receipt), "
                "(SELECT revision FROM memory_state WHERE singleton = 1)"
            ).fetchone()
        )
    finally:
        db.close()

    # A new service instance must return the original immutable result without
    # replanning against the now-advanced target.
    target = _service(target_path, "host-b-owner", "host-b")
    replay = target.apply_import(exported, plan_hash=str(first_plan["plan_hash"]))
    assert replay["receipt_id"] == receipt["receipt_id"]
    assert replay["result_hash"] == receipt["result_hash"]
    assert replay["replayed"] is True
    assert target.get_receipt(str(receipt["receipt_id"]))["result_hash"] == receipt[
        "result_hash"
    ]
    db = sqlite3.connect(target_path)
    try:
        assert tuple(
            db.execute(
                "SELECT (SELECT COUNT(*) FROM evidence), "
                "(SELECT COUNT(*) FROM cognition), "
                "(SELECT COUNT(*) FROM entity), "
                "(SELECT COUNT(*) FROM portable_import_receipt), "
                "(SELECT revision FROM memory_state WHERE singleton = 1)"
            ).fetchone()
        ) == before_replay_counts
    finally:
        db.close()

    target_export = target.export_bundle(exported_at=T)
    assert _replace_subject(exported["data"], "host-a-owner") == _replace_subject(
        target_export["data"], "host-b-owner"
    )

    source_world = QueryService(
        source_path, subject_id="host-a-owner"
    ).list_world_items(include_history=True)
    target_world = QueryService(
        target_path, subject_id="host-b-owner"
    ).list_world_items(include_history=True)
    assert any(
        item["object_kind"] == "entity" and item["item_id"] == "ent-owner"
        for item in as_objects(target_world["items"])
    )
    assert _replace_subject(as_objects(source_world["items"]), "host-a-owner") == _replace_subject(
        as_objects(target_world["items"]), "host-b-owner"
    )

    source_db = sqlite3.connect(source_path)
    target_db = sqlite3.connect(target_path)
    try:
        source_recall = recall_world_snapshot(source_db, "host-a-owner", "南京")
        target_recall = recall_world_snapshot(target_db, "host-b-owner", "南京")
    finally:
        source_db.close()
        target_db.close()
    assert source_recall is not None and target_recall is not None
    assert source_recall.selected_item_ids == target_recall.selected_item_ids
    assert source_recall.rendered_recall == target_recall.rendered_recall


def test_conflict_plan_and_receipt_failure_are_zero_write(tmp_path: Path) -> None:
    target_path = tmp_path / "conflict-target.sqlite3"
    target = _service(target_path, "host-b-owner", "host-b")
    fixture = _fixture("full-v4.json")
    initial = target.plan_import(fixture)
    target.apply_import(fixture, plan_hash=str(initial["plan_hash"]))
    duplicate_plan = target.plan_import(fixture)
    assert duplicate_plan["valid"] is True
    assert as_object(duplicate_plan["counts"])["entity_evidence"] == 0

    conflicting = json.loads(json.dumps(fixture, ensure_ascii=False))
    as_objects(conflicting["data"]["cognitions"])[0]["content"] = "同 id 的不同内容"
    _rehash(conflicting)
    conflict_plan = target.plan_import(conflicting)
    assert conflict_plan["valid"] is False
    assert conflict_plan["conflicts"] == [
        {
            "kind": "cognition",
            "id": "cog-old",
            "code": "same_id_different_content",
        }
    ]

    provenance_conflict = json.loads(json.dumps(fixture, ensure_ascii=False))
    provenance_conflict["data"]["relationshipEvidence"][0]["relation"] = "contradict"
    _rehash(provenance_conflict)
    provenance_plan = target.plan_import(provenance_conflict)
    assert provenance_plan["valid"] is False
    assert provenance_plan["conflicts"] == [
        {
            "kind": "relationship",
            "id": "rel-1",
            "code": "same_id_different_provenance",
        }
    ]

    entity_provenance_conflict = json.loads(json.dumps(fixture, ensure_ascii=False))
    entity_provenance_conflict["data"]["entityEvidence"][0]["end"] = 3
    _rehash(entity_provenance_conflict)
    entity_provenance_plan = target.plan_import(entity_provenance_conflict)
    assert entity_provenance_plan["valid"] is False
    assert entity_provenance_plan["conflicts"] == [
        {
            "kind": "entity",
            "id": "ent-owner",
            "code": "same_id_different_provenance",
        }
    ]
    db = sqlite3.connect(target_path)
    try:
        before = tuple(
            db.execute(
                "SELECT (SELECT COUNT(*) FROM cognition), "
                "(SELECT COUNT(*) FROM portable_import_receipt), "
                "(SELECT revision FROM memory_state WHERE singleton = 1)"
            ).fetchone()
        )
    finally:
        db.close()
    with pytest.raises(PortableError, match="portable_plan_invalid"):
        target.apply_import(conflicting, plan_hash=str(conflict_plan["plan_hash"]))
    db = sqlite3.connect(target_path)
    try:
        after = tuple(
            db.execute(
                "SELECT (SELECT COUNT(*) FROM cognition), "
                "(SELECT COUNT(*) FROM portable_import_receipt), "
                "(SELECT revision FROM memory_state WHERE singleton = 1)"
            ).fetchone()
        )
    finally:
        db.close()
    assert after == before

    # Force the last write (receipt) to fail. The imported rows and revision
    # must roll back with it because service owns one BEGIN IMMEDIATE.
    rollback_path = tmp_path / "receipt-failure.sqlite3"
    rollback_target = _service(rollback_path, "host-c-owner", "host-c")
    rollback_plan = rollback_target.plan_import(fixture)
    db = sqlite3.connect(rollback_path)
    try:
        db.execute(
            "CREATE TRIGGER fail_portable_receipt BEFORE INSERT ON "
            "portable_import_receipt BEGIN SELECT RAISE(ABORT, 'receipt fault'); END"
        )
        db.commit()
    finally:
        db.close()
    with pytest.raises(PortableError, match="portable_apply_failed"):
        rollback_target.apply_import(
            fixture, plan_hash=str(rollback_plan["plan_hash"])
        )
    db = sqlite3.connect(rollback_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM evidence").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM entity").fetchone() == (0,)
        assert db.execute(
            "SELECT COUNT(*) FROM portable_import_receipt"
        ).fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM memory_state").fetchone() == (0,)
    finally:
        db.close()


def test_entity_canonical_name_collision_is_invalid_during_preflight_and_zero_write(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "entity-canonical-conflict.sqlite3"
    target = _service(target_path, "host-b-owner", "host-b")
    db = sqlite3.connect(target_path)
    try:
        db.execute(
            "INSERT INTO entity (id, world_id, kind, canonical_name, aliases_json, "
            "invalid_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "target-xiaowang",
                "host-b-owner",
                "person",
                "小王",
                "[]",
                None,
                T,
                T,
            ),
        )
        db.commit()
        before = tuple(
            db.execute(
                "SELECT (SELECT COUNT(*) FROM entity), "
                "(SELECT COUNT(*) FROM portable_import_receipt), "
                "(SELECT revision FROM memory_state WHERE singleton = 1)"
            ).fetchone()
        )
    finally:
        db.close()

    bundle = _fixture("full-v4.json")
    first_plan = target.plan_import(bundle)
    second_plan = target.plan_import(bundle)

    assert first_plan == second_plan
    assert first_plan["valid"] is False
    assert first_plan["conflicts"] == [
        {
            "kind": "entity",
            "id": "ent-xiaowang",
            "code": "same_canonical_name_different_id",
        }
    ]
    with pytest.raises(PortableError, match="portable_plan_invalid"):
        target.apply_import(bundle, plan_hash=str(first_plan["plan_hash"]))

    db = sqlite3.connect(target_path)
    try:
        after = tuple(
            db.execute(
                "SELECT (SELECT COUNT(*) FROM entity), "
                "(SELECT COUNT(*) FROM portable_import_receipt), "
                "(SELECT revision FROM memory_state WHERE singleton = 1)"
            ).fetchone()
        )
    finally:
        db.close()
    assert after == before


def test_older_live_backup_cannot_revive_a_target_tombstone(tmp_path: Path) -> None:
    target_path = tmp_path / "tombstone-target.sqlite3"
    target = _service(target_path, "host-a-owner", "host-a")
    deleted = _fixture("full-v4.json")
    deleted_plan = target.plan_import(deleted)
    target.apply_import(deleted, plan_hash=str(deleted_plan["plan_hash"]))

    older = json.loads(json.dumps(deleted, ensure_ascii=False))
    deleted_evidence = next(
        item for item in older["data"]["evidence"] if item["id"] == "ev-deleted"
    )
    deleted_evidence["deletedAt"] = None
    _rehash(older)
    older_plan = target.plan_import(older)
    assert older_plan["valid"] is True
    warnings = older_plan["warnings"]
    assert isinstance(warnings, list)
    assert any("tombstoned" in str(warning) for warning in warnings)
    receipt = target.apply_import(older, plan_hash=str(older_plan["plan_hash"]))
    assert receipt["result_state"] == "no_change"
    db = sqlite3.connect(target_path)
    try:
        assert db.execute(
            "SELECT deleted_at FROM evidence WHERE id = 'ev-deleted'"
        ).fetchone() == ("2026-08-24T00:00:00.000Z",)
        assert db.execute(
            "SELECT revision FROM memory_state WHERE singleton = 1"
        ).fetchone() == (1,)
    finally:
        db.close()
