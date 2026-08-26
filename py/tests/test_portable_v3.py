"""Portable World sections: v4 writer round-trip plus v2/v3 reader compatibility."""
from __future__ import annotations

import copy
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memoweft.portable import build_bundle, import_bundle, validate_bundle
from memoweft.store import make_transaction, open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.store.semantic_resolution import SqliteSemanticResolutionStore

T = "2026-08-14T12:00:00.000Z"
SUBJECT = "owner"


def _clock() -> datetime:
    return datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _stores(db: sqlite3.Connection) -> dict[str, Any]:
    return {
        "evidence_store": SqliteEvidenceStore(db, clock=_clock),
        "event_store": SqliteEventStore(db, clock=_clock),
        "cognition_store": SqliteCognitionStore(db, clock=_clock),
        "interaction_context_store": SqliteInteractionContextStore(db, clock=_clock),
        "semantic_resolution_store": SqliteSemanticResolutionStore(db, clock=_clock),
    }


def _seed_world(db: sqlite3.Connection, subject: str = SUBJECT) -> None:
    db.execute(
        "INSERT INTO evidence (id, subject_id, source_kind, host_id, occurred_at, "
        "recorded_at, raw_content, summary, allow_local_read, allow_cloud_read, "
        "allow_inference) VALUES ('ev-1', ?, 'spoken', 'host', ?, ?, '原话', '原话', 1, 1, 1)",
        (subject, T, T),
    )
    db.execute(
        "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
        "confidence, cred_status, scope, valid_at, invalid_at, asked_at, archived_at, "
        "muted_at, created_at, updated_at) VALUES ('cog-1', ?, '小王是男生', 'attribute', "
        "'stated', 600, 'limited', NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)",
        (subject, T, T),
    )
    db.execute(
        "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
        "VALUES ('cog-1', 'ev-1', 'support')"
    )
    for entity_id, name, aliases in (("ent-a", "小王", ["杨杨"]), ("ent-b", "小李", [])):
        db.execute(
            "INSERT INTO entity (id, world_id, kind, canonical_name, invalid_at, "
            "created_at, updated_at, aliases_json) VALUES (?, ?, 'person', ?, NULL, ?, ?, ?)",
            (entity_id, subject, name, T, T, str(aliases).replace("'", '"')),
        )
    db.execute(
        "INSERT INTO relationship (id, world_id, source_entity_id, target_entity_id, "
        "relation_type, content, formed_by, confidence, cred_status, invalid_at, "
        "created_at, updated_at) VALUES ('rel-1', ?, 'ent-a', 'ent-b', 'girlfriend', "
        "'小王是小李的女朋友', 'stated', 600, 'limited', NULL, ?, ?)",
        (subject, T, T),
    )
    db.execute(
        "INSERT INTO relationship_evidence (relationship_id, evidence_id, relation) "
        "VALUES ('rel-1', 'ev-1', 'support')"
    )
    db.execute(
        "INSERT INTO world_event (id, world_id, content, occurred_at, time_expression, "
        "participants_json, objects_json, formed_by, confidence, cred_status, invalid_at, "
        "created_at, updated_at) VALUES ('we-1', ?, '上周末和小王去了南京', '2026-08-09', "
        "'上周末', '[{\"canonical_name\": \"小王\", \"kind\": \"person\"}]', '[]', 'stated', "
        "600, 'limited', NULL, ?, ?)",
        (subject, T, T),
    )
    db.execute(
        "INSERT INTO world_event_evidence (world_event_id, evidence_id, relation) "
        "VALUES ('we-1', 'ev-1', 'support')"
    )
    db.execute(
        "INSERT INTO cognition_target (cognition_id, target_entity_id, "
        "perspective_entity_id) VALUES ('cog-1', 'ent-a', NULL)"
    )


def _as_v3(bundle: dict[str, Any]) -> dict[str, Any]:
    """Downgrade a current writer fixture to the legacy v3 reader contract."""
    legacy = copy.deepcopy(bundle)
    legacy["schemaVersion"] = 3
    for key in ("bundleId", "sourceSubjectId", "worldRevision", "worldSnapshotHash"):
        legacy.pop(key, None)
    for key in ("retractions", "cognitionTransitions", "worldItemLifecycle"):
        legacy["data"].pop(key, None)
    for evidence in legacy["data"]["evidence"]:
        evidence.pop("deletedAt", None)
        evidence.pop("precedingAiContext", None)
    for key in ("retractions", "cognitionTransitions", "worldItemLifecycle"):
        legacy["metadata"]["counts"].pop(key, None)
    return legacy


def test_bundle_v4_round_trip_build_validate_import(tmp_path: Path) -> None:
    source = open_db(str(tmp_path / "source.sqlite3"))
    try:
        _seed_world(source)
        bundle = build_bundle(source, SUBJECT, host_id="host", exported_at=T)
    finally:
        source.close()

    validation = validate_bundle(bundle)
    assert validation.valid, validation.errors
    assert bundle["schemaVersion"] == 4
    assert {e["id"] for e in bundle["data"]["entities"]} == {"ent-a", "ent-b"}
    assert bundle["data"]["entities"][0]["aliases"] in (["杨杨"], [])
    assert len(bundle["data"]["relationships"]) == 1
    assert len(bundle["data"]["worldEvents"]) == 1

    target = open_db(":memory:")
    try:
        stores = _stores(target)
        plan = import_bundle(
            bundle, **stores, transaction=make_transaction(target), mode="merge",
            world_db=target,
        )
        assert plan.valid, plan.errors
        assert plan.counts.entities == 2
        assert plan.counts.relationships == 1
        assert plan.counts.world_events == 1
        assert plan.counts.cognition_targets == 1
        assert target.execute(
            "SELECT COUNT(*) FROM entity WHERE world_id = ?", (SUBJECT,)
        ).fetchone()[0] == 2
        assert target.execute(
            "SELECT COUNT(*) FROM relationship WHERE world_id = ?", (SUBJECT,)
        ).fetchone()[0] == 1
        assert target.execute(
            "SELECT COUNT(*) FROM world_event WHERE world_id = ?", (SUBJECT,)
        ).fetchone()[0] == 1
        assert target.execute(
            "SELECT COUNT(*) FROM cognition_target WHERE cognition_id = 'cog-1'"
        ).fetchone()[0] == 1
        # 幂等：再导一遍 → duplicates 语义（World 对象同 id 完全相同 → 跳过）。
        again = import_bundle(
            bundle, **stores, transaction=make_transaction(target), mode="merge",
            world_db=target,
        )
        assert again.valid
        assert again.counts.entities == 0
    finally:
        target.close()


def test_bundle_v3_rejects_dangling_world_references() -> None:
    db = open_db(":memory:")
    try:
        _seed_world(db)
        bundle = build_bundle(db, SUBJECT, host_id="host", exported_at=T)
    finally:
        db.close()

    broken = copy.deepcopy(bundle)
    broken["data"]["relationships"][0]["targetEntityId"] = "ent-ghost"
    result = validate_bundle(broken)
    assert not result.valid
    assert any("non-existent entity" in e for e in result.errors)

    broken2 = copy.deepcopy(bundle)
    broken2["data"]["entities"][0]["worldId"] = "other-subject"
    result2 = validate_bundle(broken2)
    assert not result2.valid
    assert any("does not match the bundle" in e for e in result2.errors)

    broken3 = copy.deepcopy(bundle)
    broken3["data"]["relationshipEvidence"][0]["relation"] = "neither"
    result3 = validate_bundle(broken3)
    assert not result3.valid


def test_bundle_v2_is_unchanged_and_world_keys_warn() -> None:
    # v2 bundle（无 World 段）照旧有效；带 World 段但 schemaVersion 2 → 警告并忽略。
    bundle = {
        "format": "memoweft-bundle",
        "schemaVersion": 2,
        "exportedAt": T,
        "memoWeftVersion": "1.0.0-rc.1",
        "subjectId": "owner",
        "source": {"hostId": "test", "exportMode": "full"},
        "data": {
            "evidence": [],
            "events": [],
            "eventEvidence": [],
            "cognitions": [],
            "cognitionEvidence": [],
        },
        "metadata": {"counts": {"evidence": 0, "events": 0, "cognitions": 0}, "notes": []},
    }
    assert validate_bundle(bundle).valid

    stray = copy.deepcopy(bundle)
    stray["data"]["entities"] = []
    result = validate_bundle(stray)
    assert result.valid
    assert any("World sections" in w for w in result.warnings)


def test_import_without_world_db_skips_world_sections_with_warning() -> None:
    db = open_db(":memory:")
    try:
        _seed_world(db)
        bundle = _as_v3(build_bundle(db, SUBJECT, host_id="host", exported_at=T))
    finally:
        db.close()

    target = open_db(":memory:")
    try:
        stores = _stores(target)
        plan = import_bundle(bundle, **stores, transaction=make_transaction(target), mode="merge")
        assert plan.valid, plan.errors
        assert plan.counts.entities == 0
        assert any("world_db" in w for w in plan.warnings)
        assert target.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 0
    finally:
        target.close()


def test_world_collision_is_fail_closed_zero_write() -> None:
    db = open_db(":memory:")
    try:
        _seed_world(db)
        bundle = build_bundle(db, SUBJECT, host_id="host", exported_at=T)
    finally:
        db.close()

    target = open_db(":memory:")
    try:
        stores = _stores(target)
        # 预埋同 id 不同内容的 entity → 整包拒绝、零写入。
        target.execute(
            "INSERT INTO entity (id, world_id, kind, canonical_name, invalid_at, "
            "created_at, updated_at, aliases_json) VALUES "
            "('ent-a', ?, 'place', '北京', NULL, ?, ?, '[]')",
            (SUBJECT, T, T),
        )
        plan = import_bundle(
            bundle, **stores, transaction=make_transaction(target), mode="merge",
            world_db=target,
        )
        assert not plan.valid
        assert any("collides" in e for e in plan.errors)
        assert target.execute("SELECT COUNT(*) FROM relationship").fetchone()[0] == 0
        assert target.execute("SELECT COUNT(*) FROM world_event").fetchone()[0] == 0
    finally:
        target.close()
