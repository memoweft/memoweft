"""S1 currentness regressions for deterministic Recall.

These fixtures deliberately mutate the accepted World database after creation.
They pin the contract that every support of a recalled row must be current for
the *requested subject* and local-read surface; a single invalid support hides
the entire cognition, relationship, or event.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.hermes.recall import recall_world_snapshot, recall_world_text
from memoweft.store import open_db


_T = "2026-08-24T10:00:00.000Z"
_KINDS = ("cognition", "relationship", "event")
_INVALID_SUPPORTS = (
    "missing",
    "soft_deleted",
    "cross_subject",
    "local_read_revoked",
    "multi_support_with_deleted_link",
)


def _runtime(tmp_path: Path) -> tuple[DshMemoWeftRuntime, str, Path]:
    runtime = DshMemoWeftRuntime()
    initialized = runtime.initialize(
        "s1-currentness", dsh_home=str(tmp_path), platform="test", user_id="owner"
    )
    return runtime, str(initialized["subject_id"]), Path(str(initialized["db_path"]))


def _insert_evidence(
    db: sqlite3.Connection,
    evidence_id: str,
    subject_id: str,
    *,
    deleted_at: str | None = None,
    allow_local_read: int = 1,
) -> None:
    db.execute(
        "INSERT INTO evidence (id, subject_id, source_kind, host_id, occurred_at, "
        "recorded_at, raw_content, summary, allow_local_read, allow_cloud_read, "
        "allow_inference, deleted_at) VALUES (?, ?, 'spoken', 's1-test', ?, ?, "
        "'用户喜欢喝咖啡', '用户喜欢喝咖啡', ?, 1, 1, ?)",
        (evidence_id, subject_id, _T, _T, allow_local_read, deleted_at),
    )


def _insert_world_row(
    db: sqlite3.Connection, kind: str, subject_id: str, row_id: str, evidence_id: str
) -> None:
    if kind == "cognition":
        db.execute(
            "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
            "confidence, cred_status, scope, valid_at, invalid_at, asked_at, archived_at, "
            "muted_at, created_at, updated_at) VALUES (?, ?, '用户喜欢喝咖啡', "
            "'preference', 'stated', 600, 'limited', NULL, NULL, NULL, NULL, NULL, "
            "NULL, ?, ?)",
            (row_id, subject_id, _T, _T),
        )
        db.execute(
            "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
            "VALUES (?, ?, 'support')",
            (row_id, evidence_id),
        )
        return
    if kind == "relationship":
        db.execute(
            "INSERT INTO relationship (id, world_id, source_entity_id, target_entity_id, "
            "relation_type, content, formed_by, confidence, cred_status, invalid_at, "
            "created_at, updated_at) VALUES (?, ?, 'entity-a', 'entity-b', 'likes', "
            "'用户喜欢喝咖啡', 'stated', 600, 'limited', NULL, ?, ?)",
            (row_id, subject_id, _T, _T),
        )
        db.execute(
            "INSERT INTO relationship_evidence (relationship_id, evidence_id, relation) "
            "VALUES (?, ?, 'support')",
            (row_id, evidence_id),
        )
        return
    if kind == "event":
        db.execute(
            "INSERT INTO world_event (id, world_id, content, occurred_at, time_expression, "
            "participants_json, objects_json, formed_by, confidence, cred_status, invalid_at, "
            "created_at, updated_at) VALUES (?, ?, '用户喜欢喝咖啡', NULL, NULL, '[]', "
            "'[]', 'stated', 600, 'limited', NULL, ?, ?)",
            (row_id, subject_id, _T, _T),
        )
        db.execute(
            "INSERT INTO world_event_evidence (world_event_id, evidence_id, relation) "
            "VALUES (?, ?, 'support')",
            (row_id, evidence_id),
        )
        return
    raise AssertionError(f"unknown World row kind: {kind}")


def _link_additional_support(
    db: sqlite3.Connection, kind: str, row_id: str, evidence_id: str
) -> None:
    table, column = {
        "cognition": ("cognition_evidence", "cognition_id"),
        "relationship": ("relationship_evidence", "relationship_id"),
        "event": ("world_event_evidence", "world_event_id"),
    }[kind]
    db.execute(
        f"INSERT INTO {table} ({column}, evidence_id, relation) VALUES (?, ?, 'support')",
        (row_id, evidence_id),
    )


def _insert_entity_naming_ledger(
    db: sqlite3.Connection, subject_id: str, evidence_id: str
) -> None:
    db.execute(
        "INSERT INTO entity (id, world_id, kind, canonical_name, invalid_at, created_at, "
        "updated_at, aliases_json) VALUES ('entity-wang', ?, 'person', '小王', NULL, ?, ?, ?)",
        (subject_id, _T, _T, json.dumps(["杨杨"], ensure_ascii=False)),
    )
    db.execute(
        "INSERT INTO evidence_ledger (id, content, payload_json) VALUES (?, ?, ?) ",
        (
            f"entity-support-{evidence_id}",
            json.dumps(
                {
                    "relation": "support",
                    "entity_id": "entity-wang",
                    "evidence_id": evidence_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            '{"schema_version":1}',
        ),
    )


def _insert_alias_ledger(
    db: sqlite3.Connection, evidence_ids: tuple[str, ...], *, ledger_suffix: str
) -> None:
    db.execute(
        "INSERT INTO evidence_ledger (id, content, payload_json) VALUES (?, ?, ?)",
        (
            f"entity-alias-{ledger_suffix}",
            json.dumps(
                {
                    "relation": "alias",
                    "canonical_entity_id": "entity-wang",
                    "alias_name": "杨杨",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            json.dumps(
                {"schema_version": 1, "evidence_ids": list(evidence_ids)},
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
    )
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("invalid_support", _INVALID_SUPPORTS)
def test_recall_hides_world_row_when_any_support_is_not_current(
    tmp_path: Path, kind: str, invalid_support: str
) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    row_id = f"{kind}-row"
    evidence_id = f"{kind}-support"
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, evidence_id, subject_id)
            _insert_world_row(db, kind, subject_id, row_id, evidence_id)
            db.commit()
            assert recall_world_text(db, subject_id, "喜欢喝咖啡")[1] == 1

            if invalid_support == "missing":
                db.execute("DELETE FROM evidence WHERE id = ?", (evidence_id,))
            elif invalid_support == "soft_deleted":
                db.execute(
                    "UPDATE evidence SET deleted_at = ? WHERE id = ?", (_T, evidence_id)
                )
            elif invalid_support == "cross_subject":
                db.execute(
                    "UPDATE evidence SET subject_id = 'other-subject' WHERE id = ?",
                    (evidence_id,),
                )
            elif invalid_support == "local_read_revoked":
                db.execute(
                    "UPDATE evidence SET allow_local_read = 0 WHERE id = ?", (evidence_id,)
                )
            elif invalid_support == "multi_support_with_deleted_link":
                invalid_id = f"{kind}-deleted-support"
                _insert_evidence(db, invalid_id, subject_id, deleted_at=_T)
                _link_additional_support(db, kind, row_id, invalid_id)
            else:
                raise AssertionError(f"unknown invalid support: {invalid_support}")
            db.commit()
            assert recall_world_text(db, subject_id, "喜欢喝咖啡") == ("", 0)
        finally:
            db.close()
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("kind", _KINDS)
def test_recall_keeps_world_row_when_all_supports_are_current(
    tmp_path: Path, kind: str
) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    row_id = f"{kind}-all-current"
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, f"{kind}-support-a", subject_id)
            _insert_evidence(db, f"{kind}-support-b", subject_id)
            _insert_world_row(db, kind, subject_id, row_id, f"{kind}-support-a")
            _link_additional_support(db, kind, row_id, f"{kind}-support-b")
            db.commit()
            text, count = recall_world_text(db, subject_id, "喜欢喝咖啡")
            assert count == 1
            assert "用户喜欢喝咖啡" in text
        finally:
            db.close()
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("invalid_naming_support", ("soft_deleted", "cross_subject", "local_read_revoked"))
def test_recall_does_not_expand_graph_from_noncurrent_entity_naming_evidence(
    tmp_path: Path, invalid_naming_support: str
) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, "relationship-support", subject_id)
            _insert_evidence(db, "entity-naming-support", subject_id)
            _insert_entity_naming_ledger(db, subject_id, "entity-naming-support")
            _insert_alias_ledger(db, ("entity-naming-support",), ledger_suffix="naming")
            db.execute(
                "INSERT INTO relationship (id, world_id, source_entity_id, target_entity_id, "
                "relation_type, content, formed_by, confidence, cred_status, invalid_at, "
                "created_at, updated_at) VALUES ('relationship-alias', ?, 'entity-wang', "
                "'entity-other', 'knows', '熟人', 'stated', 600, 'limited', NULL, ?, ?)",
                (subject_id, _T, _T),
            )
            db.execute(
                "INSERT INTO relationship_evidence (relationship_id, evidence_id, relation) "
                "VALUES ('relationship-alias', 'relationship-support', 'support')"
            )
            db.commit()
            assert recall_world_text(db, subject_id, "杨杨")[1] == 1

            if invalid_naming_support == "soft_deleted":
                db.execute(
                    "UPDATE evidence SET deleted_at = ? WHERE id = 'entity-naming-support'", (_T,)
                )
            elif invalid_naming_support == "cross_subject":
                db.execute(
                    "UPDATE evidence SET subject_id = 'other-subject' "
                    "WHERE id = 'entity-naming-support'"
                )
            else:
                db.execute(
                    "UPDATE evidence SET allow_local_read = 0 "
                    "WHERE id = 'entity-naming-support'"
                )
            db.commit()
            assert recall_world_text(db, subject_id, "杨杨") == ("", 0)
        finally:
            db.close()
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("alias_support_mode", ("single_revoked", "one_of_two_ledgers_revoked"))
def test_recall_does_not_expand_an_alias_when_any_alias_ledger_is_not_current(
    tmp_path: Path, alias_support_mode: str
) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, "relationship-support", subject_id)
            _insert_evidence(db, "entity-naming-support", subject_id)
            _insert_evidence(db, "alias-support-a", subject_id)
            _insert_entity_naming_ledger(db, subject_id, "entity-naming-support")
            _insert_alias_ledger(db, ("alias-support-a",), ledger_suffix="a")
            if alias_support_mode == "one_of_two_ledgers_revoked":
                _insert_evidence(db, "alias-support-b", subject_id)
                _insert_alias_ledger(db, ("alias-support-b",), ledger_suffix="b")
            db.execute(
                "INSERT INTO relationship (id, world_id, source_entity_id, target_entity_id, "
                "relation_type, content, formed_by, confidence, cred_status, invalid_at, "
                "created_at, updated_at) VALUES ('relationship-alias-ledger', ?, 'entity-wang', "
                "'entity-other', 'knows', '熟人', 'stated', 600, 'limited', NULL, ?, ?)",
                (subject_id, _T, _T),
            )
            db.execute(
                "INSERT INTO relationship_evidence (relationship_id, evidence_id, relation) "
                "VALUES ('relationship-alias-ledger', 'relationship-support', 'support')"
            )
            db.commit()
            assert recall_world_text(db, subject_id, "杨杨")[1] == 1
            db.execute("UPDATE evidence SET deleted_at = ? WHERE id = 'alias-support-a'", (_T,))
            db.commit()
            assert recall_world_text(db, subject_id, "杨杨") == ("", 0)
        finally:
            db.close()
    finally:
        runtime.shutdown()


def test_alias_ledger_change_invalidates_the_recall_snapshot_token(tmp_path: Path) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, "relationship-support", subject_id)
            _insert_evidence(db, "entity-naming-support", subject_id)
            _insert_evidence(db, "alias-support", subject_id)
            _insert_entity_naming_ledger(db, subject_id, "entity-naming-support")
            _insert_alias_ledger(db, ("alias-support",), ledger_suffix="snapshot")
            db.execute(
                "INSERT INTO relationship (id, world_id, source_entity_id, target_entity_id, "
                "relation_type, content, formed_by, confidence, cred_status, invalid_at, "
                "created_at, updated_at) VALUES ('relationship-alias-snapshot', ?, 'entity-wang', "
                "'entity-other', 'knows', '熟人', 'stated', 600, 'limited', NULL, ?, ?)",
                (subject_id, _T, _T),
            )
            db.execute(
                "INSERT INTO relationship_evidence (relationship_id, evidence_id, relation) "
                "VALUES ('relationship-alias-snapshot', 'relationship-support', 'support')"
            )
            db.commit()
            before = recall_world_snapshot(db, subject_id, "杨杨")
            assert before is not None and before.count == 1
            db.execute("UPDATE evidence SET deleted_at = ? WHERE id = 'alias-support'", (_T,))
            db.commit()
            after = recall_world_snapshot(db, subject_id, "杨杨")
            assert after is not None and after.count == 0
            assert after.currentness_digest != before.currentness_digest
            assert after.recall_snapshot_token != before.recall_snapshot_token
        finally:
            db.close()
    finally:
        runtime.shutdown()


def test_recall_renders_same_current_snapshot_as_byte_stable_text(tmp_path: Path) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            for row_id, content in (
                ("cognition-b", "用户喜欢喝咖啡B"),
                ("cognition-a", "用户喜欢喝咖啡A"),
            ):
                evidence_id = f"support-{row_id}"
                _insert_evidence(db, evidence_id, subject_id)
                _insert_world_row(db, "cognition", subject_id, row_id, evidence_id)
                db.execute("UPDATE cognition SET content = ? WHERE id = ?", (content, row_id))
            db.commit()
            first = recall_world_text(db, subject_id, "喜欢喝咖啡")
            second = recall_world_text(db, subject_id, "喜欢喝咖啡")
            assert first == second
            assert first == ("记忆：用户喜欢喝咖啡A\n记忆：用户喜欢喝咖啡B", 2)
        finally:
            db.close()
    finally:
        runtime.shutdown()


def test_recall_uses_a_current_target_entity_as_a_natural_question_anchor(
    tmp_path: Path,
) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, "support-mother", subject_id)
            _insert_world_row(
                db, "cognition", subject_id, "cognition-mother", "support-mother"
            )
            db.execute(
                "UPDATE cognition SET content = '妈妈是个善良的人' "
                "WHERE id = 'cognition-mother'"
            )
            db.execute(
                "INSERT INTO entity (id, world_id, kind, canonical_name, invalid_at, "
                "created_at, updated_at, aliases_json) VALUES "
                "('entity-mother', ?, 'person', '妈妈', NULL, ?, ?, '[]')",
                (subject_id, _T, _T),
            )
            db.execute(
                "INSERT INTO evidence_ledger (id, content, payload_json) VALUES "
                "('entity-support-mother', ?, '{\"schema_version\":1}')",
                (
                    json.dumps(
                        {
                            "relation": "support",
                            "entity_id": "entity-mother",
                            "evidence_id": "support-mother",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
            db.execute(
                "INSERT INTO cognition_target "
                "(cognition_id, target_entity_id, perspective_entity_id) VALUES "
                "('cognition-mother', 'entity-mother', NULL)"
            )
            db.commit()

            snapshot = recall_world_snapshot(db, subject_id, "你觉得我妈妈人怎么样")

            assert snapshot is not None
            assert snapshot.selected_item_ids == (("cognition", "cognition-mother"),)
            assert snapshot.rendered_recall == "记忆：妈妈是个善良的人"
        finally:
            db.close()
    finally:
        runtime.shutdown()


def test_recall_snapshot_returns_a_stable_structured_empty_world(tmp_path: Path) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            first = recall_world_snapshot(db, subject_id, "任意查询")
            second = recall_world_snapshot(db, subject_id, "任意查询")
            assert first is not None
            assert first == second
            assert first.subject_id == subject_id
            assert first.world_revision == 0
            assert first.selected_item_ids == ()
            assert first.rendered_recall == ""
            assert first.count == 0
            assert len(first.currentness_digest) == 64
            assert len(first.recall_snapshot_token) == 64
        finally:
            db.close()
    finally:
        runtime.shutdown()


def test_recall_snapshot_matches_owner_weixin_unpunctuated_paraphrase(
    tmp_path: Path,
) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, "owner-weixin-support", subject_id)
            _insert_world_row(
                db,
                "cognition",
                subject_id,
                "owner-weixin-cognition",
                "owner-weixin-support",
            )
            db.execute(
                "UPDATE cognition SET content = '用户是向往自由本身的' "
                "WHERE id = 'owner-weixin-cognition'"
            )
            db.execute(
                "UPDATE evidence SET raw_content = '我向往的是自由本身', "
                "summary = '用户是向往自由本身的' "
                "WHERE id = 'owner-weixin-support'"
            )
            db.execute("UPDATE memory_state SET revision = 1 WHERE singleton = 1")
            db.commit()
            changes_before = db.total_changes

            first = recall_world_snapshot(
                db, subject_id, "关于云和自由你还记得什么？"
            )
            second = recall_world_snapshot(
                db, subject_id, "关于云和自由你还记得什么？"
            )

            assert first is not None
            assert first.selected_item_ids == (
                ("cognition", "owner-weixin-cognition"),
            )
            assert first.rendered_recall == "记忆：用户是向往自由本身的"
            assert second == first
            assert db.total_changes == changes_before
        finally:
            db.close()
    finally:
        runtime.shutdown()


def test_recall_snapshot_fails_closed_for_unreadable_schema() -> None:
    db = sqlite3.connect(":memory:")
    try:
        assert recall_world_snapshot(db, "subject", "查询") is None
    finally:
        db.close()


def test_recall_snapshot_reuses_a_callers_active_transaction_without_rollback(
    tmp_path: Path,
) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            db.execute("BEGIN")
            _insert_evidence(db, "caller-support", subject_id)
            _insert_world_row(db, "cognition", subject_id, "caller-cognition", "caller-support")
            snapshot = recall_world_snapshot(db, subject_id, "喜欢喝咖啡")
            assert snapshot is not None and snapshot.count == 1
            assert db.in_transaction
            assert db.execute("SELECT COUNT(*) FROM cognition WHERE id = 'caller-cognition'").fetchone()[0] == 1
        finally:
            db.rollback()
            db.close()
    finally:
        runtime.shutdown()


def test_recall_snapshot_failure_does_not_rollback_a_callers_transaction() -> None:
    db = sqlite3.connect(":memory:")
    try:
        db.execute("BEGIN")
        db.execute("CREATE TABLE caller_pending (value TEXT NOT NULL)")
        db.execute("INSERT INTO caller_pending (value) VALUES ('retained')")
        assert recall_world_snapshot(db, "subject", "查询") is None
        assert db.in_transaction
        assert db.execute("SELECT value FROM caller_pending").fetchone() == ("retained",)
    finally:
        db.rollback()
        db.close()


def test_recall_snapshot_token_tracks_currentness_and_lifecycle_changes(tmp_path: Path) -> None:
    runtime, subject_id, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, "support-original", subject_id)
            _insert_world_row(db, "cognition", subject_id, "cognition-original", "support-original")
            db.execute(
                "INSERT INTO memory_state (singleton, revision, snapshot_json, snapshot_hash) "
                "VALUES (1, 1, '{}', 'fixture')"
            )
            db.commit()
            original = recall_world_snapshot(db, subject_id, "喜欢喝咖啡")
            assert original is not None and original.count == 1

            db.execute("UPDATE evidence SET deleted_at = ? WHERE id = 'support-original'", (_T,))
            db.commit()
            deleted = recall_world_snapshot(db, subject_id, "喜欢喝咖啡")
            assert deleted is not None and deleted.count == 0
            assert deleted.recall_snapshot_token != original.recall_snapshot_token

            db.execute("UPDATE evidence SET deleted_at = NULL, allow_local_read = 0 WHERE id = 'support-original'")
            db.commit()
            permission_revoked = recall_world_snapshot(db, subject_id, "喜欢喝咖啡")
            assert permission_revoked is not None and permission_revoked.count == 0
            assert permission_revoked.recall_snapshot_token != deleted.recall_snapshot_token

            db.execute("UPDATE evidence SET allow_local_read = 1 WHERE id = 'support-original'")
            db.execute("UPDATE cognition SET invalid_at = ? WHERE id = 'cognition-original'", (_T,))
            _insert_evidence(db, "support-correction", subject_id)
            _insert_world_row(db, "cognition", subject_id, "cognition-correction", "support-correction")
            db.execute("UPDATE memory_state SET revision = 2 WHERE singleton = 1")
            db.commit()
            corrected = recall_world_snapshot(db, subject_id, "喜欢喝咖啡")
            assert corrected is not None and corrected.selected_item_ids == (("cognition", "cognition-correction"),)
            assert corrected.recall_snapshot_token != permission_revoked.recall_snapshot_token

            db.execute("UPDATE cognition SET invalid_at = ? WHERE id = 'cognition-correction'", (_T,))
            db.execute("UPDATE memory_state SET revision = 3 WHERE singleton = 1")
            db.commit()
            retracted = recall_world_snapshot(db, subject_id, "喜欢喝咖啡")
            assert retracted is not None and retracted.count == 0
            assert retracted.recall_snapshot_token != corrected.recall_snapshot_token

            _insert_evidence(db, "support-superseding", subject_id)
            _insert_world_row(db, "cognition", subject_id, "cognition-superseding", "support-superseding")
            db.execute("UPDATE memory_state SET revision = 4 WHERE singleton = 1")
            db.commit()
            superseding = recall_world_snapshot(db, subject_id, "喜欢喝咖啡")
            assert superseding is not None and superseding.count == 1
            assert superseding.recall_snapshot_token != retracted.recall_snapshot_token
        finally:
            db.close()
    finally:
        runtime.shutdown()
