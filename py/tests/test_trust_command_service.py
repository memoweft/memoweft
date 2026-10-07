"""N5 Trust Command: durable mutation, receipt, replay, and currentness contract."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
from threading import Barrier

import pytest

from support.json_assertions import as_int, as_object, as_objects

from memoweft.integrations.trust import QueryService
from memoweft.integrations.trust.command_service import CommandService
from memoweft.integrations.trust.command_store import TrustCommandError
from memoweft.integrations.trust.portable_service import PortableService
from memoweft.integrations.trust.currentness import evidence_state
from memoweft.integrations.hermes.batch_adapter import owner_entity_id_for
from memoweft.portable.builder import build_bundle
from memoweft.store import open_db
from memoweft.store.driver import application_id, user_version
from memoweft.store.schema import PYTHON_APPLICATION_ID, SCHEMA_VERSION


_SUBJECT = "owner"
_HOST = "trust:test"
_T0 = "2026-08-25T00:00:00.000Z"


def _clock() -> datetime:
    return datetime(2026, 8, 25, tzinfo=timezone.utc)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _open(path: Path) -> sqlite3.Connection:
    db = open_db(str(path))
    db.row_factory = sqlite3.Row
    return db


def _seed_evidence(
    db: sqlite3.Connection,
    evidence_id: str,
    raw: str,
    *,
    allow_local_read: bool = True,
    allow_cloud_read: bool = True,
    allow_inference: bool = True,
) -> None:
    db.execute(
        "INSERT INTO evidence (id, subject_id, source_kind, host_id, origin_id, "
        "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
        "allow_cloud_read, allow_inference, corrects_evidence_id, deleted_at, "
        "preceding_ai_context) VALUES (?, ?, 'spoken', ?, NULL, ?, ?, ?, ?, ?, ?, ?, "
        "NULL, NULL, NULL)",
        (
            evidence_id,
            _SUBJECT,
            _HOST,
            _T0,
            _T0,
            raw,
            raw,
            int(allow_local_read),
            int(allow_cloud_read),
            int(allow_inference),
        ),
    )
    db.execute(
        "INSERT INTO boundary_evidence_content (evidence_id, raw_content_hash) "
        "VALUES (?, ?)",
        (evidence_id, sha256(raw.encode("utf-8")).hexdigest()),
    )


def _seed_cognition(
    db: sqlite3.Connection,
    cognition_id: str = "cog-coffee",
    evidence_id: str = "e-coffee",
    content: str = "用户喜欢喝咖啡",
) -> None:
    _seed_evidence(db, evidence_id, content)
    db.execute(
        "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
        "confidence, cred_status, scope, valid_at, invalid_at, asked_at, "
        "archived_at, muted_at, created_at, updated_at) VALUES (?, ?, ?, "
        "'preference', 'stated', 800, 'trusted', NULL, NULL, NULL, NULL, NULL, NULL, "
        "?, ?)",
        (cognition_id, _SUBJECT, content, _T0, _T0),
    )
    db.execute(
        "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
        "VALUES (?, ?, 'support')",
        (cognition_id, evidence_id),
    )


def _seed_four_kinds(db: sqlite3.Connection) -> dict[str, str]:
    _seed_cognition(db)
    _seed_evidence(db, "e-entity", "小王是用户的朋友")
    db.execute(
        "INSERT INTO entity (id, world_id, kind, canonical_name, invalid_at, "
        "created_at, updated_at, aliases_json) VALUES "
        "('entity-wang', ?, 'person', '小王', NULL, ?, ?, '[]')",
        (_SUBJECT, _T0, _T0),
    )
    db.execute(
        "INSERT INTO evidence_ledger (id, content, payload_json) VALUES "
        "('ledger-entity', ?, ?)",
        (
            _canonical(
                {
                    "entity_id": "entity-wang",
                    "evidence_id": "e-entity",
                    "relation": "support",
                }
            ),
            _canonical({"schema_version": 1, "start": 0, "end": 2}),
        ),
    )
    _seed_evidence(db, "e-relationship", "小王是用户的朋友")
    db.execute(
        "INSERT INTO relationship (id, world_id, source_entity_id, "
        "target_entity_id, relation_type, content, formed_by, confidence, "
        "cred_status, invalid_at, created_at, updated_at) VALUES "
        "('rel-wang', ?, 'owner-entity', 'entity-wang', 'friend', "
        "'小王是用户的朋友', 'stated', 800, 'trusted', NULL, ?, ?)",
        (_SUBJECT, _T0, _T0),
    )
    db.execute(
        "INSERT INTO relationship_evidence (relationship_id, evidence_id, relation) "
        "VALUES ('rel-wang', 'e-relationship', 'support')"
    )
    _seed_evidence(db, "e-event", "用户和小王去爬山")
    db.execute(
        "INSERT INTO world_event (id, world_id, content, occurred_at, "
        "time_expression, participants_json, objects_json, formed_by, confidence, "
        "cred_status, invalid_at, created_at, updated_at) VALUES "
        "('event-hike', ?, '用户和小王去爬山', NULL, NULL, '[]', '[]', 'stated', "
        "800, 'trusted', NULL, ?, ?)",
        (_SUBJECT, _T0, _T0),
    )
    db.execute(
        "INSERT INTO world_event_evidence (world_event_id, evidence_id, relation) "
        "VALUES ('event-hike', 'e-event', 'support')"
    )
    return {
        "entity": "entity-wang",
        "relationship": "rel-wang",
        "event": "event-hike",
        "cognition": "cog-coffee",
    }


def _seed_revision(db: sqlite3.Connection, revision: int = 1) -> None:
    snapshot = _canonical({"schema_version": 5, "revision": revision})
    db.execute(
        "INSERT INTO memory_state (singleton, revision, snapshot_json, snapshot_hash) "
        "VALUES (1, ?, ?, ?)",
        (revision, snapshot, sha256(snapshot.encode("utf-8")).hexdigest()),
    )


def _command(
    command_id: str,
    revision: int,
    operation: str,
    target_kind: str,
    target_id: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_id": command_id,
        "subject_id": _SUBJECT,
        "actor": "owner",
        "expected_world_revision": revision,
        "operation": operation,
        "target_kind": target_kind,
        "target_id": target_id,
        "payload": {} if payload is None else payload,
        "submitted_at": _T0,
    }


def _service(path: Path) -> CommandService:
    return CommandService(
        path,
        subject_id=_SUBJECT,
        host_id=_HOST,
        clock=_clock,
    )


def test_fresh_and_v16_migrated_databases_have_one_current_command_shape(
    tmp_path: Path,
) -> None:
    fresh_path = tmp_path / "fresh.sqlite3"
    with _open(fresh_path) as fresh:
        assert user_version(fresh) == SCHEMA_VERSION == 20
        assert application_id(fresh) == PYTHON_APPLICATION_ID
        fresh_shapes = {
            table: tuple(
                str(row[1])
                for row in fresh.execute(f"PRAGMA table_info({table})").fetchall()
            )
            for table in (
                "trust_command",
                "trust_command_receipt",
                "world_item_lifecycle",
            )
        }

    migrated_path = tmp_path / "migrated.sqlite3"
    with _open(migrated_path) as old:
        old.execute("DROP TABLE portable_import_receipt")
        old.execute("DROP TABLE clarification")
        old.execute("DROP TABLE trust_command_receipt")
        old.execute("DROP TABLE trust_command")
        old.execute("DROP TABLE world_item_lifecycle")
        old.execute("PRAGMA user_version = 16")
    with _open(migrated_path) as migrated:
        assert user_version(migrated) == SCHEMA_VERSION
        assert application_id(migrated) == PYTHON_APPLICATION_ID
        migrated_shapes = {
            table: tuple(
                str(row[1])
                for row in migrated.execute(f"PRAGMA table_info({table})").fetchall()
            )
            for table in fresh_shapes
        }
    assert migrated_shapes == fresh_shapes


def test_permission_command_is_atomic_query_visible_exported_and_replay_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "permissions.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        _seed_revision(db)

    service = _service(path)
    receipt = service.submit_command(
        _command(
            "cmd-permissions",
            1,
            "update_evidence_permissions",
            "evidence",
            "e-coffee",
            {
                "allow_local_read": False,
                "allow_cloud_read": False,
                "allow_inference": False,
            },
        )
    )
    assert receipt == service.get_command_receipt("cmd-permissions")
    assert receipt["accepted"] is True
    assert receipt["result_state"] == "applied"
    assert receipt["before_revision"] == 1
    assert as_int(receipt["after_revision"]) == 2
    assert receipt["affected_ids"] == ["e-coffee"]
    assert len(str(receipt["result_hash"])) == 64
    assert receipt["result_hash"] == sha256(
        _canonical(
            {
                key: value
                for key, value in receipt.items()
                if key != "result_hash"
            }
        ).encode("utf-8")
    ).hexdigest()

    query = QueryService(path, subject_id=_SUBJECT)
    evidence = as_object(query.get_evidence("e-coffee")["evidence"])
    assert evidence["permissions"] == {
        "allow_local_read": False,
        "allow_cloud_read": False,
        "allow_inference": False,
    }
    assert evidence["raw_content"] is None
    assert as_objects(query.list_world_items()["items"]) == []
    assert as_object(query.preview_recall("咖啡")["preview"])["count"] == 0

    with _open(path) as db:
        formation_row = db.execute(
            "SELECT deleted_at, allow_local_read, allow_cloud_read, allow_inference "
            "FROM evidence WHERE id = 'e-coffee'"
        ).fetchone()
        assert formation_row is not None
        assert evidence_state(
            formation_row,
            surface="formation",
            model_tier="cloud",
        ) == "evidence_inference_denied"
        bundle = build_bundle(
            db,
            _SUBJECT,
            host_id="export-test",
            exported_at=_T0,
        )
        exported = next(
            row for row in as_objects(bundle["data"]["evidence"]) if row["id"] == "e-coffee"
        )
        assert exported["allowLocalRead"] is False
        assert exported["allowCloudRead"] is False
        assert exported["allowInference"] is False
        counts_before = {
            table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("trust_command", "trust_command_receipt", "evidence")
        }

    replay = _service(path).submit_command(
        _command(
            "cmd-permissions",
            1,
            "update_evidence_permissions",
            "evidence",
            "e-coffee",
            {
                "allow_local_read": False,
                "allow_cloud_read": False,
                "allow_inference": False,
            },
        )
    )
    assert replay == receipt
    with _open(path) as db:
        assert int(db.execute("SELECT revision FROM memory_state").fetchone()[0]) == 2
        assert counts_before == {
            table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in counts_before
        }

    changed = _command(
        "cmd-permissions",
        1,
        "update_evidence_permissions",
        "evidence",
        "e-coffee",
        {"allow_local_read": True},
    )
    with pytest.raises(TrustCommandError, match="command_id_conflict"):
        _service(path).submit_command(changed)
    with _open(path) as db:
        db.execute(
            "UPDATE trust_command_receipt SET result_hash = ? "
            "WHERE command_id = 'cmd-permissions'",
            ("0" * 64,),
        )
    with pytest.raises(TrustCommandError, match="command_receipt_hash_mismatch"):
        _service(path).get_command_receipt("cmd-permissions")


def test_expected_revision_conflict_is_durable_and_zero_write(tmp_path: Path) -> None:
    path = tmp_path / "conflict.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        _seed_revision(db, 4)
        before = db.execute("SELECT * FROM evidence WHERE id = 'e-coffee'").fetchone()

    receipt = _service(path).submit_command(
        _command(
            "cmd-stale",
            3,
            "update_evidence_permissions",
            "evidence",
            "e-coffee",
            {"allow_cloud_read": False},
        )
    )
    assert receipt["accepted"] is False
    assert receipt["result_state"] == "revision_conflict"
    assert receipt["before_revision"] == as_int(receipt["after_revision"]) == 4
    assert receipt["affected_ids"] == []
    assert receipt == _service(path).get_command_receipt("cmd-stale")
    with _open(path) as db:
        after = db.execute("SELECT * FROM evidence WHERE id = 'e-coffee'").fetchone()
        assert tuple(after) == tuple(before)
        assert int(db.execute("SELECT revision FROM memory_state").fetchone()[0]) == 4


def test_no_change_and_concurrent_same_revision_commands_advance_at_most_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrent-revision.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        _seed_revision(db)

    no_change = _service(path).submit_command(
        _command(
            "cmd-no-change",
            1,
            "update_evidence_permissions",
            "evidence",
            "e-coffee",
            {"allow_local_read": True},
        )
    )
    assert no_change["accepted"] is True
    assert no_change["result_state"] == "no_change"
    assert no_change["before_revision"] == as_int(no_change["after_revision"]) == 1

    barrier = Barrier(2)

    def submit(command_id: str, field: str) -> dict[str, object]:
        barrier.wait()
        return dict(
            _service(path).submit_command(
                _command(
                    command_id,
                    1,
                    "update_evidence_permissions",
                    "evidence",
                    "e-coffee",
                    {field: False},
                )
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(
            future.result()
            for future in (
                executor.submit(
                    submit, "cmd-concurrent-local", "allow_local_read"
                ),
                executor.submit(
                    submit, "cmd-concurrent-cloud", "allow_cloud_read"
                ),
            )
        )
    assert sorted(str(row["result_state"]) for row in receipts) == [
        "applied",
        "revision_conflict",
    ]
    assert sorted(int(as_int(row["after_revision"])) for row in receipts) == [2, 2]
    with _open(path) as db:
        permissions = db.execute(
            "SELECT allow_local_read, allow_cloud_read FROM evidence "
            "WHERE id = 'e-coffee'"
        ).fetchone()
        assert permissions is not None
        assert tuple(permissions) in {(0, 1), (1, 0)}
        assert int(db.execute("SELECT revision FROM memory_state").fetchone()[0]) == 2
        assert int(db.execute("SELECT COUNT(*) FROM trust_command").fetchone()[0]) == 3
        assert int(
            db.execute("SELECT COUNT(*) FROM trust_command_receipt").fetchone()[0]
        ) == 3


def test_forget_preserves_evidence_audit_but_removes_current_world_and_recall(
    tmp_path: Path,
) -> None:
    path = tmp_path / "forget.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        _seed_revision(db)

    receipt = _service(path).submit_command(
        _command("cmd-forget", 1, "forget_evidence", "evidence", "e-coffee")
    )
    assert receipt["result_state"] == "applied"
    assert receipt["before_revision"] == 1
    assert as_int(receipt["after_revision"]) == 2
    query = QueryService(path, subject_id=_SUBJECT)
    evidence = as_object(query.get_evidence("e-coffee")["evidence"])
    assert evidence["currentness_state"] == "evidence_deleted"
    assert as_object(evidence["lifecycle"])["deleted_at"] == _T0
    assert evidence["raw_content"] is None
    historical = query.get_world_item("cognition", "cog-coffee", include_history=True)
    assert as_object(historical["item"])["current_state"] == "not_current"
    assert as_object(as_object(historical["item"])["value"]) == {"redacted": True}
    assert as_object(query.preview_recall("咖啡")["preview"])["count"] == 0
    with _open(path) as db:
        audit = db.execute(
            "SELECT raw_content, summary, deleted_at FROM evidence WHERE id = 'e-coffee'"
        ).fetchone()
        assert tuple(audit) == ("用户喜欢喝咖啡", "用户喜欢喝咖啡", _T0)


def test_delete_evidence_clears_source_world_and_portable_replay(tmp_path: Path) -> None:
    path = tmp_path / "delete.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        db.execute("UPDATE cognition SET cred_status = 'stable' WHERE id = 'cog-coffee'")
        db.execute("UPDATE evidence SET origin_id = 'source:coffee', "
                   "preceding_ai_context = 'private context' WHERE id = 'e-coffee'")
        db.execute("INSERT INTO semantic_resolution (id, evidence_id, resolved_content, "
                   "resolver_version, created_at) VALUES ('res-coffee', 'e-coffee', "
                   "'resolved private content', 'test', ?)", (_T0,))
        db.execute("INSERT INTO proposals (id, kind, base_revision, result_hash, "
                   "payload_json, review_payload_json, status) VALUES "
                   "('p-private', 'test', 1, 'hash', ?, NULL, 'pending')",
                   (json.dumps({"evidence_id": "e-coffee", "content": "derived private"}),))
        db.execute("INSERT INTO trust_command (command_id, schema_version, subject_id, "
                   "actor, expected_world_revision, operation, target_kind, target_id, "
                   "payload_json, request_hash, submitted_at) VALUES "
                   "('historical-private', 1, ?, 'owner', 0, 'correct_world_item', "
                   "'evidence', 'e-coffee', ?, 'hash', ?)",
                   (_SUBJECT, json.dumps({"content": "derived private"}), _T0))
        _seed_revision(db)
        old_bundle = build_bundle(db, _SUBJECT, host_id=_HOST, exported_at=_T0)
    service = _service(path)
    receipt = service.submit_command(
        _command("delete-evidence", 1, "delete_evidence", "evidence", "e-coffee")
    )
    assert receipt["result_state"] == "applied"
    assert "用户喜欢喝咖啡" not in json.dumps(receipt, ensure_ascii=False)
    with _open(path) as db:
        row = db.execute("SELECT raw_content, summary, preceding_ai_context, "
                         "origin_id, deleted_at FROM evidence WHERE id = 'e-coffee'").fetchone()
        assert tuple(row[:4]) == ("", "", None, None)
        assert row[4] is not None
        for table in ("cognition", "cognition_evidence", "semantic_resolution", "boundary_evidence_content"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
        assert db.execute("SELECT payload_json FROM trust_command WHERE command_id='historical-private'").fetchone()[0] == "{}"
        new_bundle = build_bundle(db, _SUBJECT, host_id=_HOST, exported_at=_T0)
        assert "用户喜欢喝咖啡" not in json.dumps(new_bundle, ensure_ascii=False)
    restarted = PortableService(path, subject_id=_SUBJECT, host_id=_HOST, clock=_clock)
    assert restarted.plan_import(old_bundle)["valid"] is False
    assert restarted.plan_import(old_bundle)["errors"] == ["hard_deleted_source"]
    assert _service(path).get_command_receipt("delete-evidence") == receipt
    assert _service(path).submit_command(
        _command("delete-evidence-again", 2, "delete_evidence", "evidence", "e-coffee")
    )["result_state"] == "no_change"


def test_delete_world_item_exclusive_source_and_rejection_reasons(tmp_path: Path) -> None:
    path = tmp_path / "delete-world.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        _seed_revision(db)
        old_bundle = build_bundle(db, _SUBJECT, host_id=_HOST, exported_at=_T0)
    receipt = _service(path).submit_command(
        _command("delete-world", 1, "delete_world_item", "cognition", "cog-coffee")
    )
    assert receipt["result_state"] == "applied"
    assert receipt["affected_ids"] == ["cog-coffee", "e-coffee"]
    with _open(path) as db:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
        assert db.execute("SELECT 1 FROM world_delete_marker WHERE item_id = 'cog-coffee'").fetchone()
    portable = PortableService(path, subject_id=_SUBJECT, host_id=_HOST, clock=_clock)
    assert portable.plan_import(old_bundle)["valid"] is False
    assert _service(path).get_command_receipt("delete-world") == receipt


def test_delete_world_item_shared_or_untracked_source_is_atomic_rejection(tmp_path: Path) -> None:
    path = tmp_path / "shared.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        db.execute("INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
                   "confidence, cred_status, created_at, updated_at) VALUES "
                   "('cog-shared', ?, 'other understanding', 'preference', 'stated', "
                   "800, 'trusted', ?, ?)", (_SUBJECT, _T0, _T0))
        db.execute("INSERT INTO cognition_evidence VALUES ('cog-shared', 'e-coffee', 'support')")
        db.execute("INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
                   "confidence, cred_status, created_at, updated_at) VALUES "
                   "('cog-untracked', ?, 'untracked', 'preference', 'stated', "
                   "800, 'trusted', ?, ?)", (_SUBJECT, _T0, _T0))
        _seed_revision(db)
    shared = _service(path).submit_command(
        _command("delete-shared", 1, "delete_world_item", "cognition", "cog-coffee")
    )
    assert shared["result_state"] == "rejected"
    assert shared["rejection_code"] == "source_evidence_shared"
    unknown = _service(path).submit_command(
        _command("delete-untracked", 1, "delete_world_item", "cognition", "cog-untracked")
    )
    assert unknown["result_state"] == "rejected"
    assert unknown["rejection_code"] == "source_provenance_missing"
    assert _service(path).get_command_receipt("delete-shared") == shared
    with _open(path) as db:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 3
        assert db.execute("SELECT raw_content FROM evidence WHERE id='e-coffee'").fetchone()[0] == "用户喜欢喝咖啡"


def test_delete_storage_status_tracks_busy_wal_and_retry(tmp_path: Path) -> None:
    path = tmp_path / "delete-wal.sqlite3"
    with _open(path) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        _seed_cognition(db)
        _seed_revision(db)
    reader = sqlite3.connect(path, isolation_level=None)
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT raw_content FROM evidence WHERE id='e-coffee'").fetchone()
        receipt = _service(path).submit_command(
            _command("delete-wal", 1, "delete_evidence", "evidence", "e-coffee")
        )
        assert receipt["storage_cleanup"]["state"] == "pending"
        assert _service(path).get_command_receipt("delete-wal")["storage_cleanup"]["state"] == "pending"
    finally:
        reader.execute("ROLLBACK")
        reader.close()
    retried = _service(path).retry_delete_storage_cleanup("delete-wal")
    assert retried["storage_cleanup"] == {
        "state": "complete", "detail_code": "current_wal_truncated"
    }


def test_soft_forget_then_hard_delete_retains_only_hashed_origin(tmp_path: Path) -> None:
    path = tmp_path / "forget-then-delete.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        db.execute("UPDATE evidence SET origin_id = 'source:coffee' WHERE id = 'e-coffee'")
        _seed_revision(db)
    assert _service(path).submit_command(
        _command("soft", 1, "forget_evidence", "evidence", "e-coffee")
    )["result_state"] == "applied"
    with _open(path) as db:
        assert db.execute("SELECT origin_id FROM evidence WHERE id='e-coffee'").fetchone()[0] is None
        assert db.execute("SELECT origin_id FROM evidence_origin_history WHERE evidence_id='e-coffee'").fetchone()[0] == "source:coffee"
    assert _service(path).submit_command(
        _command("hard", 2, "delete_evidence", "evidence", "e-coffee")
    )["result_state"] == "applied"
    with _open(path) as db:
        assert db.execute("SELECT origin_id FROM evidence WHERE id='e-coffee'").fetchone()[0] is None
        assert db.execute("SELECT COUNT(*) FROM evidence_origin_history").fetchone()[0] == 0
        assert db.execute("SELECT origin_hash FROM hard_deleted_origin").fetchone()[0] == sha256(b"source:coffee").hexdigest()


def test_historical_soft_delete_without_origin_rejects_hard_delete(tmp_path: Path) -> None:
    path = tmp_path / "legacy-soft.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        db.execute("UPDATE evidence SET deleted_at = ?, origin_id = NULL WHERE id = 'e-coffee'", (_T0,))
        _seed_revision(db)
    receipt = _service(path).submit_command(
        _command("legacy-hard", 1, "delete_evidence", "evidence", "e-coffee")
    )
    assert receipt["result_state"] == "rejected"
    assert receipt["rejection_code"] == "source_origin_unrecoverable"
    with _open(path) as db:
        assert db.execute("SELECT raw_content FROM evidence WHERE id = 'e-coffee'").fetchone()[0] == "用户喜欢喝咖啡"


def test_delete_world_item_second_source_rejection_rolls_back_first(tmp_path: Path) -> None:
    path = tmp_path / "world-second-source-reject.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        _seed_evidence(db, "z-legacy", "另一条来源")
        db.execute("INSERT INTO cognition_evidence VALUES ('cog-coffee', 'z-legacy', 'support')")
        db.execute("UPDATE evidence SET deleted_at = ?, origin_id = NULL WHERE id = 'z-legacy'", (_T0,))
        _seed_revision(db)
    receipt = _service(path).submit_command(
        _command("delete-world-second-reject", 1, "delete_world_item", "cognition", "cog-coffee")
    )
    assert receipt["result_state"] == "rejected"
    assert receipt["rejection_code"] == "source_origin_unrecoverable"
    assert receipt["affected_ids"] == []
    with _open(path) as db:
        assert db.execute("SELECT revision FROM memory_state").fetchone()[0] == 1
        assert db.execute("SELECT raw_content, deleted_at FROM evidence WHERE id='e-coffee'").fetchone()[:] == ("用户喜欢喝咖啡", None)
        assert db.execute("SELECT content FROM cognition WHERE id='cog-coffee'").fetchone()[0] == "用户喜欢喝咖啡"
        assert db.execute("SELECT COUNT(*) FROM cognition_evidence WHERE cognition_id='cog-coffee'").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM world_delete_marker").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("raw", "summary", "expected_code"),
    [("用户喜欢喝咖啡", "用户喜欢喝咖啡", "source_origin_unrecoverable"),
     ("", "", "source_already_deleted")],
)
def test_delete_world_item_single_source_rejection_never_reports_applied(
    tmp_path: Path, raw: str, summary: str, expected_code: str
) -> None:
    path = tmp_path / f"world-single-{expected_code}.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        db.execute(
            "UPDATE evidence SET deleted_at = ?, origin_id = NULL, raw_content = ?, summary = ? "
            "WHERE id = 'e-coffee'", (_T0, raw, summary),
        )
        _seed_revision(db)
    receipt = _service(path).submit_command(
        _command("delete-world-single-reject", 1, "delete_world_item", "cognition", "cog-coffee")
    )
    assert receipt["result_state"] == "rejected"
    assert receipt["rejection_code"] == expected_code
    with _open(path) as db:
        assert db.execute("SELECT revision FROM memory_state").fetchone()[0] == 1
        assert db.execute("SELECT raw_content, summary FROM evidence WHERE id='e-coffee'").fetchone()[:] == (raw, summary)
        assert db.execute("SELECT content FROM cognition WHERE id='cog-coffee'").fetchone()[0] == "用户喜欢喝咖啡"
        assert db.execute("SELECT COUNT(*) FROM world_delete_marker").fetchone()[0] == 0


@pytest.mark.parametrize("operation,column", [("archive_world_item", "archived_at"), ("mute_world_item", "muted_at")])
def test_archive_and_mute_share_cross_kind_lifecycle_authority(
    tmp_path: Path, operation: str, column: str
) -> None:
    path = tmp_path / f"{operation}.sqlite3"
    with _open(path) as db:
        ids = _seed_four_kinds(db)
        _seed_revision(db)

    service = _service(path)
    revision = 1
    for kind in ("entity", "relationship", "event", "cognition"):
        receipt = service.submit_command(
            _command(
                f"cmd-{operation}-{kind}",
                revision,
                operation,
                kind,
                ids[kind],
            )
        )
        assert receipt["result_state"] == "applied"
        revision += 1
        assert as_int(receipt["after_revision"]) == revision

    query = QueryService(path, subject_id=_SUBJECT)
    assert as_objects(query.list_world_items()["items"]) == []
    history = as_objects(query.list_world_items(include_history=True)["items"])
    assert len(history) == 4
    assert all(as_object(item["lifecycle"])[column] == _T0 for item in history)
    assert all(item["current_state"] == "not_current" for item in history)
    assert as_object(query.preview_recall("小王 咖啡 爬山")["preview"])["count"] == 0
    with _open(path) as db:
        cognition = db.execute(
            f"SELECT {column} FROM cognition WHERE id = 'cog-coffee'"
        ).fetchone()
        assert cognition[0] == _T0
        rows = db.execute(
            f"SELECT object_kind, {column} FROM world_item_lifecycle "
            f"WHERE subject_id = ? ORDER BY object_kind",
            (_SUBJECT,),
        ).fetchall()
        assert len(rows) == 4
    assert all(row[1] == _T0 for row in rows)


def test_local_only_correction_applies_without_cloud_permission_or_model_call(
    tmp_path: Path,
) -> None:
    path = tmp_path / "local-only-correction.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        _seed_revision(db)

    correction_text = "用户出门时习惯带一把蓝色雨伞。"
    receipt = _service(path).submit_command(
        _command(
            "cmd-local-only-correct",
            1,
            "correct_world_item",
            "cognition",
            "cog-coffee",
            {"correction_text": correction_text, "allow_cloud_read": False},
        )
    )

    assert receipt["result_state"] == "applied"
    assert receipt["before_revision"] == 1
    assert as_int(receipt["after_revision"]) == 2
    replacement_id = str(receipt["affected_ids"][1])
    correction_evidence_id = str(receipt["affected_ids"][2])
    query = QueryService(path, subject_id=_SUBJECT)
    assert as_object(as_object(query.get_world_item("cognition", replacement_id)["item"])["value"])[
        "content"
    ] == correction_text
    evidence = as_object(query.get_evidence(correction_evidence_id)["evidence"])
    assert evidence["permissions"] == {
        "allow_local_read": True,
        "allow_cloud_read": False,
        "allow_inference": True,
    }
    assert evidence["raw_content"] == correction_text


def test_correction_and_retract_use_exact_evidence_formal_history_and_zero_replay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "correction.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        _seed_revision(db)

    service = _service(path)
    correction = service.submit_command(
        _command(
            "cmd-correct",
            1,
            "correct_world_item",
            "cognition",
            "cog-coffee",
            {"correction_text": "用户喜欢喝茶"},
        )
    )
    assert correction["result_state"] == "applied"
    assert correction["before_revision"] == 1
    assert as_int(correction["after_revision"]) == 2
    assert correction["affected_ids"][0] == "cog-coffee"
    replacement_id = str(correction["affected_ids"][1])
    correction_evidence_id = str(correction["affected_ids"][2])
    assert correction["transition_ids"]

    query = QueryService(path, subject_id=_SUBJECT)
    prior = as_object(query.get_world_item("cognition", "cog-coffee", include_history=True)["item"])
    replacement = as_object(query.get_world_item("cognition", replacement_id)["item"])
    evidence = as_object(query.get_evidence(correction_evidence_id)["evidence"])
    assert prior["current_state"] == "not_current"
    assert as_object(replacement["value"])["content"] == "用户喜欢喝茶"
    assert evidence["raw_content"] == "用户喜欢喝茶"
    assert evidence["origin_id"] == (
        "trust-command:cmd-correct:cognition:cog-coffee:correction"
    )
    assert evidence["corrects_evidence_id"] == "e-coffee"
    assert [row["evidence_id"] for row in as_objects(replacement["provenance"])] == [
        correction_evidence_id
    ]
    history = query.get_world_item_history("cognition", replacement_id)
    assert as_objects(history["transition_history"])[0]["prior_item_id"] == "cog-coffee"

    with _open(path) as db:
        counts = {
            table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "evidence",
                "cognition",
                "cognition_transitions",
                "trust_command",
                "trust_command_receipt",
            )
        }
    assert service.submit_command(
        _command(
            "cmd-correct",
            1,
            "correct_world_item",
            "cognition",
            "cog-coffee",
            {"correction_text": "用户喜欢喝茶"},
        )
    ) == correction
    with _open(path) as db:
        assert counts == {
            table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in counts
        }
        assert int(db.execute("SELECT revision FROM memory_state").fetchone()[0]) == 2

    retract = service.submit_command(
        _command(
            "cmd-retract",
            2,
            "retract_world_item",
            "cognition",
            replacement_id,
        )
    )
    assert retract["result_state"] == "applied"
    assert retract["before_revision"] == 2
    assert as_int(retract["after_revision"]) == 3
    assert retract["transition_ids"]
    retracted = as_object(query.get_world_item(
        "cognition", replacement_id, include_history=True
    )["item"])
    assert retracted["current_state"] == "not_current"
    assert as_object(query.get_evidence(correction_evidence_id)["evidence"])["raw_content"] == "用户喜欢喝茶"
    assert as_object(query.preview_recall("茶")["preview"])["count"] == 0


def test_provider_command_dispatch_is_subject_bound_and_receipt_survives_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider.sqlite3"
    with _open(path) as db:
        _seed_cognition(db)
        _seed_revision(db)

    service = _service(path)
    args = {
        key: value
        for key, value in _command(
            "cmd-provider",
            1,
            "update_evidence_permissions",
            "evidence",
            "e-coffee",
            {"allow_cloud_read": False},
        ).items()
        if key not in {"schema_version", "subject_id"}
    }
    submitted = service.execute_provider_tool("memoweft_submit_trust_command", args)
    assert as_object(submitted["receipt"])["result_state"] == "applied"
    looked_up = _service(path).execute_provider_tool(
        "memoweft_get_trust_command_receipt", {"command_id": "cmd-provider"}
    )
    assert as_object(looked_up["receipt"]) == as_object(submitted["receipt"])

    with pytest.raises(TrustCommandError, match="unexpected_trust_command_argument"):
        service.execute_provider_tool(
            "memoweft_submit_trust_command",
            {**args, "subject_id": "someone-else"},
        )
    with pytest.raises(TrustCommandError, match="unknown_trust_command_tool"):
        service.execute_provider_tool("memoweft_unknown_write", {})


def test_relationship_and_event_correction_and_retract_share_formal_apply(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cross-kind-correction.sqlite3"
    owner_id = owner_entity_id_for(_SUBJECT)
    with _open(path) as db:
        ids = _seed_four_kinds(db)
        db.execute(
            "INSERT INTO entity (id, world_id, kind, canonical_name, invalid_at, "
            "created_at, updated_at, aliases_json) VALUES (?, ?, 'person', '用户', "
            "NULL, ?, ?, '[]')",
            (owner_id, _SUBJECT, _T0, _T0),
        )
        db.execute(
            "UPDATE relationship SET source_entity_id = ? WHERE id = 'rel-wang'",
            (owner_id,),
        )
        _seed_revision(db)

    service = _service(path)
    relationship = service.submit_command(
        _command(
            "cmd-correct-relationship",
            1,
            "correct_world_item",
            "relationship",
            ids["relationship"],
            {
                "correction_text": "小王是用户的同事",
                "relation_type": "colleague",
            },
        )
    )
    assert relationship["result_state"] == "applied"
    new_relationship_id = str(relationship["affected_ids"][1])
    assert as_object(as_object(QueryService(path, subject_id=_SUBJECT).get_world_item(
        "relationship", new_relationship_id
    )["item"])["value"])["relation_type"] == "colleague"

    event = service.submit_command(
        _command(
            "cmd-correct-event",
            2,
            "correct_world_item",
            "event",
            ids["event"],
            {"correction_text": "用户和小王去露营"},
        )
    )
    assert event["result_state"] == "applied"
    new_event_id = str(event["affected_ids"][1])
    query = QueryService(path, subject_id=_SUBJECT)
    assert as_object(as_object(query.get_world_item("event", new_event_id)["item"])["value"])["content"] == "用户和小王去露营"

    relation_retract = service.submit_command(
        _command(
            "cmd-retract-relationship",
            3,
            "retract_world_item",
            "relationship",
            new_relationship_id,
        )
    )
    event_retract = service.submit_command(
        _command(
            "cmd-retract-event",
            4,
            "retract_world_item",
            "event",
            new_event_id,
        )
    )
    assert relation_retract["result_state"] == "applied"
    assert event_retract["result_state"] == "applied"
    assert as_object(query.get_world_item(
        "relationship", new_relationship_id, include_history=True
    )["item"])["current_state"] == "not_current"
    assert as_object(query.get_world_item(
        "event", new_event_id, include_history=True
    )["item"])["current_state"] == "not_current"

    unsupported = service.submit_command(
        _command(
            "cmd-correct-entity",
            5,
            "correct_world_item",
            "entity",
            ids["entity"],
            {"correction_text": "小王改名为老王"},
        )
    )
    assert unsupported["accepted"] is False
    assert unsupported["result_state"] == "rejected"
    assert unsupported["before_revision"] == as_int(unsupported["after_revision"]) == 5
    assert _service(path).get_command_receipt("cmd-correct-entity") == unsupported
    assert _service(path).submit_command(
        _command(
            "cmd-correct-entity",
            5,
            "correct_world_item",
            "entity",
            ids["entity"],
            {"correction_text": "小王改名为老王"},
        )
    ) == unsupported
    assert unsupported["before_revision"] == as_int(unsupported["after_revision"]) == 5
