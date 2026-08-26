from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, cast

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.trust import (
    QueryService,
    TRUST_SCHEMA_VERSION,
    TrustQueryError,
)
from memoweft.store import open_db


SUBJECT = "trust-owner"


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def _seed(path: Path) -> None:
    db = open_db(str(path))
    try:
        db.execute(
            "INSERT INTO memory_state(singleton, revision, snapshot_json, snapshot_hash) "
            "VALUES (1, 7, '{}', 'snapshot-7')"
        )
        evidence_rows = (
            ("ev-main", 1, 1, 1, None, "用户说小王是同事"),
            ("ev-local", 1, 0, 1, None, "只允许本地读取"),
            ("ev-deleted", 1, 1, 1, "2026-08-24T00:00:00Z", "已忘记原文"),
        )
        for evidence_id, local, cloud, inference, deleted_at, raw in evidence_rows:
            db.execute(
                """INSERT INTO evidence (
                       id, subject_id, source_kind, host_id, origin_id,
                       occurred_at, recorded_at, raw_content, summary,
                       allow_local_read, allow_cloud_read, allow_inference,
                       corrects_evidence_id, deleted_at, preceding_ai_context
                   ) VALUES (?, ?, 'spoken', 'hermes:test', ?,
                             '2026-08-24T00:00:00Z', '2026-08-24T00:00:01Z', ?, ?,
                             ?, ?, ?, NULL, ?, NULL)""",
                (
                    evidence_id,
                    SUBJECT,
                    f"origin:{evidence_id}",
                    raw,
                    raw,
                    local,
                    cloud,
                    inference,
                    deleted_at,
                ),
            )

        db.execute(
            "INSERT INTO entity(id, world_id, kind, canonical_name, invalid_at, "
            "created_at, updated_at, aliases_json) VALUES "
            "('entity-1', ?, 'person', '小王', NULL, '2026-08-24T00:01:00Z', "
            "'2026-08-24T00:01:00Z', '[\"王同学\"]')",
            (SUBJECT,),
        )
        db.execute(
            "INSERT INTO evidence_ledger(id, content, payload_json) VALUES (?, ?, ?)",
            (
                "ledger-entity",
                _canonical(
                    {"relation": "support", "entity_id": "entity-1", "evidence_id": "ev-main"}
                ),
                _canonical({"schema_version": 1}),
            ),
        )
        db.execute(
            "INSERT INTO cognition(id, subject_id, content, content_type, formed_by, "
            "confidence, cred_status, scope, valid_at, invalid_at, asked_at, archived_at, "
            "muted_at, created_at, updated_at) VALUES "
            "('cognition-old', ?, '用户以前不喜欢咖啡', 'preference', 'stated', 70, "
            "'candidate', NULL, NULL, '2026-08-24T00:03:00Z', NULL, NULL, NULL, "
            "'2026-08-24T00:01:00Z', '2026-08-24T00:03:00Z')",
            (SUBJECT,),
        )
        db.execute(
            "INSERT INTO cognition(id, subject_id, content, content_type, formed_by, "
            "confidence, cred_status, scope, valid_at, invalid_at, asked_at, archived_at, "
            "muted_at, created_at, updated_at) VALUES "
            "('cognition-1', ?, '用户喜欢手冲咖啡', 'preference', 'stated', 80, "
            "'credible', NULL, NULL, NULL, NULL, NULL, NULL, "
            "'2026-08-24T00:03:00Z', '2026-08-24T00:03:00Z')",
            (SUBJECT,),
        )
        for cognition_id in ("cognition-old", "cognition-1"):
            db.execute(
                "INSERT INTO cognition_evidence(cognition_id, evidence_id, relation) "
                "VALUES (?, 'ev-main', 'support')",
                (cognition_id,),
            )
        db.execute(
            "INSERT INTO cognition_target(cognition_id, target_entity_id, perspective_entity_id) "
            "VALUES ('cognition-1', 'entity-1', NULL)"
        )
        db.execute(
            "INSERT INTO cognition_transitions(id, prior_cognition_id, replacement_cognition_id, "
            "reason, revision) VALUES ('transition-1', 'cognition-old', 'cognition-1', "
            "'corrects', 7)"
        )

        db.execute(
            "INSERT INTO relationship(id, world_id, source_entity_id, target_entity_id, "
            "relation_type, content, formed_by, confidence, cred_status, invalid_at, "
            "created_at, updated_at) VALUES ('relationship-1', ?, 'entity-1', 'entity-1', "
            "'colleague', '小王是用户的同事', 'stated', 75, 'candidate', NULL, "
            "'2026-08-24T00:02:00Z', '2026-08-24T00:02:00Z')",
            (SUBJECT,),
        )
        db.execute(
            "INSERT INTO relationship_evidence(relationship_id, evidence_id, relation) "
            "VALUES ('relationship-1', 'ev-main', 'support')"
        )

        for event_id, content, invalid_at in (
            ("event-old", "用户昨天去过旧书店", "2026-08-24T00:04:00Z"),
            ("event-1", "用户昨天和小王去了书店", None),
        ):
            db.execute(
                "INSERT INTO world_event(id, world_id, content, occurred_at, time_expression, "
                "participants_json, objects_json, formed_by, confidence, cred_status, invalid_at, "
                "created_at, updated_at) VALUES (?, ?, ?, '2026-08-23', '昨天', "
                "'[\"entity-1\"]', '[\"书店\"]', 'stated', 72, 'candidate', ?, "
                "'2026-08-24T00:02:00Z', '2026-08-24T00:04:00Z')",
                (event_id, SUBJECT, content, invalid_at),
            )
            db.execute(
                "INSERT INTO world_event_evidence(world_event_id, evidence_id, relation) "
                "VALUES (?, 'ev-main', 'support')",
                (event_id,),
            )
        db.execute(
            "INSERT INTO retraction(id, prior_cognition_id, prior_relationship_id, reason, "
            "revision, created_at, prior_event_id) VALUES "
            "('retraction-1', NULL, NULL, 'retracts', 7, '2026-08-24T00:04:00Z', 'event-old')"
        )

        receipt = _canonical({"schema_version": 1, "accepted": True})
        db.execute(
            """INSERT INTO memory_world_job (
                   job_id, job_schema_version, boundary_event_id, boundary_payload_hash,
                   boundary_schema_version, provider_name, parent_session_id, result_session_id,
                   boundary_mode, formal_target_json, formal_target_hash, subject_id, host_id,
                   evidence_ids_json, state, attempts, next_attempt_at, claim_owner, claim_token,
                   claimed_at, lease_expires_at, heartbeat_at, fencing_generation,
                   model_dispatch_started_at, model_completed_at, model_task, model_provider,
                   model_name, model_usage_json, model_result_json, model_result_hash,
                   world_result_json, result_hash, delivery_receipt_json, delivery_receipt_hash,
                   created_at, completed_at, last_error_type, terminal_state, terminal_detail
               ) VALUES (
                   'job-1', 1, 'boundary-1', 'boundary-hash', 1, 'memoweft', 'session-a',
                   'session-a', 'in_place', '{}', 'target-hash', ?, 'hermes:test',
                   '[\"ev-main\"]', 'applied', 1, NULL, NULL, NULL, NULL, NULL, NULL, 1,
                   '2026-08-24T00:05:00Z', '2026-08-24T00:05:01Z', 'memory_world', 'test',
                   'test-model', '{}', '{}', 'model-hash', '{"world_revision":7}',
                   'world-result-hash', ?, 'receipt-hash', '2026-08-24T00:05:00Z',
                   '2026-08-24T00:05:02Z', NULL, 'applied', NULL)""",
            (SUBJECT, receipt),
        )
        db.execute(
            """INSERT INTO terminal_outcome (
                   outcome_id, schema_version, job_id, boundary_event_id, provider_name,
                   subject_id, parent_session_id, result_session_id, terminal_state,
                   terminal_detail, world_revision, world_result_json, result_hash, occurred_at,
                   delivery_state, attempts, next_attempt_at, claim_owner, claim_token,
                   lease_expires_at, heartbeat_at, delivered_at, last_error
               ) VALUES ('outcome-1', 1, 'job-1', 'boundary-1', 'memoweft', ?, 'session-a',
                         'session-a', 'applied', NULL, 7, '{"world_revision":7}', 'outcome-hash',
                         '2026-08-24T00:05:02Z', 'delivered', 1, NULL, NULL, NULL, NULL, NULL,
                         '2026-08-24T00:05:03Z', NULL)""",
            (SUBJECT,),
        )
    finally:
        db.close()


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _shape(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return "scalar"


def _any_result(value: dict[str, object]) -> dict[str, Any]:
    return cast(dict[str, Any], value)


@pytest.fixture()
def trust_db(tmp_path: Path) -> Path:
    path = tmp_path / "trust.sqlite3"
    _seed(path)
    return path


def test_four_world_kinds_use_one_stable_contract(trust_db: Path) -> None:
    service = QueryService(trust_db, subject_id=SUBJECT)
    result = _any_result(service.list_world_items())

    assert result["schema_version"] == TRUST_SCHEMA_VERSION == 1
    assert result["subject_id"] == SUBJECT
    assert result["world_revision"] == 7
    assert [(item["object_kind"], item["item_id"]) for item in result["items"]] == [
        ("entity", "entity-1"),
        ("relationship", "relationship-1"),
        ("event", "event-1"),
        ("cognition", "cognition-1"),
    ]
    assert all(item["current_state"] == "current" for item in result["items"])
    assert _any_result(service.get_world_item("entity", "entity-1"))["item"]["value"]["canonical_name"] == "小王"


def test_history_provenance_and_tombstones_remain_explainable(trust_db: Path) -> None:
    service = QueryService(trust_db, subject_id=SUBJECT)

    history = _any_result(service.get_world_item_history("cognition", "cognition-1"))
    assert history["transition_history"] == [
        {
            "transition_id": "transition-1",
            "transition_kind": "corrects",
            "object_kind": "cognition",
            "prior_item_id": "cognition-old",
            "replacement_item_id": "cognition-1",
            "revision": 7,
            "occurred_at": None,
            "evidence_ids": ["ev-main"],
        }
    ]
    provenance = _any_result(service.get_world_item_provenance("cognition", "cognition-1"))
    assert provenance["provenance"][0]["evidence_id"] == "ev-main"
    assert provenance["provenance"][0]["currentness_state"] == "current"
    assert provenance["provenance"][0]["permissions"] == {
        "allow_local_read": True,
        "allow_cloud_read": True,
        "allow_inference": True,
    }

    evidence = _any_result(service.list_evidence())["evidence"]
    assert [item["evidence_id"] for item in evidence] == ["ev-main", "ev-local", "ev-deleted"]
    assert evidence[-1]["currentness_state"] == "evidence_deleted"
    assert evidence[-1]["lifecycle"]["deleted_at"] == "2026-08-24T00:00:00Z"

    old_event = _any_result(
        service.get_world_item("event", "event-old", include_history=True)
    )["item"]
    assert old_event["current_state"] == "not_current"
    assert old_event["lifecycle"]["invalid_at"] == "2026-08-24T00:04:00Z"


def test_jobs_separate_acceptance_core_terminal_delivery_and_host_scope(trust_db: Path) -> None:
    job = _any_result(QueryService(trust_db, subject_id=SUBJECT).get_job("job-1"))["job"]

    assert job["acceptance"]["boundary_event_id"] == "boundary-1"
    assert job["core_terminal"]["terminal_state"] == "applied"
    assert job["core_terminal"]["world_revision"] == 7
    assert job["delivery"]["delivery_state"] == "delivered"
    assert job["host_event"] == {
        "available": False,
        "scope": "hermes_state",
        "reason": "not_stored_in_core_database",
    }


def test_recall_preview_reuses_production_snapshot_and_is_zero_write(trust_db: Path) -> None:
    before = trust_db.read_bytes()
    service = QueryService(trust_db, subject_id=SUBJECT)
    preview = _any_result(service.preview_recall("小王"))
    db = sqlite3.connect(f"file:{trust_db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        expected = recall_world_snapshot(db, SUBJECT, "小王")
    finally:
        db.close()

    assert expected is not None
    assert preview["world_revision"] == expected.world_revision
    assert preview["preview"]["selected_item_ids"] == [list(pair) for pair in expected.selected_item_ids]
    assert preview["preview"]["rendered_recall"] == expected.rendered_recall
    assert preview["preview"]["recall_snapshot_token"] == expected.recall_snapshot_token
    assert preview["preview"]["model_call_count"] == 0
    assert preview["preview"]["world_write_count"] == 0
    assert trust_db.read_bytes() == before


def test_provider_dispatch_is_closed_subject_bound_and_cloud_permission_aware(
    trust_db: Path,
) -> None:
    service = QueryService(trust_db, subject_id=SUBJECT, surface="trust_cloud")

    world = _any_result(
        service.execute_provider_tool(
            "memoweft_query_world", {"operation": "list"}
        )
    )
    assert world["world_revision"] == 7
    assert len(world["items"]) == 4

    evidence = _any_result(
        service.execute_provider_tool(
            "memoweft_query_evidence", {"operation": "list"}
        )
    )["evidence"]
    local_only = next(item for item in evidence if item["evidence_id"] == "ev-local")
    assert local_only["currentness_state"] == "evidence_cloud_read_denied"
    assert local_only["content_available"] is False
    assert local_only["raw_content"] is None

    with pytest.raises(TrustQueryError, match="unexpected_trust_tool_argument"):
        service.execute_provider_tool(
            "memoweft_query_world",
            {"operation": "revision", "subject_id": "different-owner"},
        )
    with pytest.raises(TrustQueryError, match="unknown_trust_tool"):
        service.execute_provider_tool("memoweft_delete_world", {})


def test_coherent_revision_read_does_not_mix_a_concurrent_commit(
    trust_db: Path,
) -> None:
    setup = sqlite3.connect(str(trust_db), isolation_level=None)
    try:
        setup.execute("PRAGMA journal_mode = WAL")
    finally:
        setup.close()

    reader_started = threading.Event()
    writer_finished = threading.Event()

    class PausingQueryService(QueryService):
        def _item_ids(
            self, db: sqlite3.Connection, kind: str
        ) -> tuple[str, ...]:
            if kind == "entity" and not reader_started.is_set():
                reader_started.set()
                assert writer_finished.wait(timeout=5)
            return super()._item_ids(db, kind)  # type: ignore[arg-type]

    def writer() -> None:
        assert reader_started.wait(timeout=5)
        db = sqlite3.connect(str(trust_db), isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE memory_state SET revision = 8, snapshot_hash = 'snapshot-8' "
                "WHERE singleton = 1"
            )
            db.execute(
                "UPDATE entity SET canonical_name = '新名字', "
                "updated_at = '2026-08-24T00:06:00Z' WHERE id = 'entity-1'"
            )
            db.execute("COMMIT")
        finally:
            db.close()
            writer_finished.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    result = _any_result(PausingQueryService(trust_db, subject_id=SUBJECT).list_world_items())
    thread.join(timeout=5)
    assert not thread.is_alive()
    entity = next(item for item in result["items"] if item["object_kind"] == "entity")
    assert result["world_revision"] == 7
    assert entity["value"]["canonical_name"] == "小王"

    after = _any_result(QueryService(trust_db, subject_id=SUBJECT).get_world_item("entity", "entity-1"))
    assert after["world_revision"] == 8
    assert after["item"]["value"]["canonical_name"] == "新名字"


def test_fresh_and_migrated_sqlite_return_the_same_contract_shape(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh.sqlite3"
    migrated = tmp_path / "migrated.sqlite3"
    _seed(fresh)
    _seed(migrated)

    raw = sqlite3.connect(str(migrated), isolation_level=None)
    try:
        raw.execute("DROP TABLE portable_import_receipt")
        raw.execute("DROP TABLE clarification")
        raw.execute("DROP INDEX ix_terminal_outcome_expired")
        raw.execute("DROP INDEX ix_terminal_outcome_ready")
        raw.execute("DROP TABLE terminal_outcome")
        raw.execute("DROP TABLE trust_command_receipt")
        raw.execute("DROP TABLE trust_command")
        raw.execute("DROP TABLE world_item_lifecycle")
        raw.execute("PRAGMA user_version = 15")
    finally:
        raw.close()
    upgraded = open_db(str(migrated))
    upgraded.close()

    fresh_service = QueryService(fresh, subject_id=SUBJECT)
    migrated_service = QueryService(migrated, subject_id=SUBJECT)
    assert _shape(fresh_service.list_world_items()) == _shape(migrated_service.list_world_items())
    assert _shape(fresh_service.list_evidence()) == _shape(migrated_service.list_evidence())
    assert _shape(fresh_service.list_jobs()) == _shape(migrated_service.list_jobs())
    assert _shape(fresh_service.preview_recall("小王")) == _shape(
        migrated_service.preview_recall("小王")
    )
