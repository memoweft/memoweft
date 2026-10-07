"""Independent S1 P0 acceptance across formation, Apply, Recall and DSH.

The assertions use the production worker, batch adapter, current Recall and
DSH runtime against temporary SQLite databases.  The only private calls are
the two checkpoint methods used to place a crash boundary after a real route
return and before Apply; there is no public crash-injection surface there.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Mapping, NoReturn

import pytest

from support.json_assertions import as_objects

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor, OneShotRoute
from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.hermes.world_worker import WorldJobStore, WorldJobWorker
from memoweft.store import open_db

from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)


_NOW = "2026-08-24T12:00:00.000Z"


def _one_cognition(raw: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "result": "one_cognition",
        "cognition": {
            "target": "owner_self",
            "statement_kind": "preference",
            "proposition": raw,
            "supports": [{"evidence_id": "evidence-1", "start": 0, "end": len(raw)}],
        },
    }


def _world_counts(path: Path) -> tuple[int, int, int, int, int]:
    db = sqlite3.connect(path)
    try:
        return (
            db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM relationship").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM world_event").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0],
            db.execute("SELECT COALESCE(MAX(revision), 0) FROM memory_state").fetchone()[0],
        )
    finally:
        db.close()


def _unexpected_route(calls: list[object]) -> OneShotRoute:
    def route(*args: object, **kwargs: object) -> NoReturn:
        calls.append((args, kwargs))
        raise AssertionError("forbidden formation must not dispatch a route")

    return route


def _checkpoint_real_route(
    path: Path,
    clock: MutableClock,
    response: Mapping[str, object],
) -> tuple[list[object], object]:
    calls: list[object] = []

    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        calls.append((messages, session_id))
        return dict(response)

    store = WorldJobStore(path, policy=_policy(), clock=clock)
    claim = store.claim_one("crashed-after-checkpoint")
    assert claim is not None
    assert store.mark_dispatch_started(claim) is True
    processor = HermesBatchAdapterProcessor(str(path), route, clock=clock)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        payload = processor._dispatch_once(claim, db)
        processor._persist_checkpoint(db, claim, payload)
    finally:
        db.close()
    return calls, claim


@pytest.mark.parametrize("permission_column", ("allow_inference", "allow_cloud_read"))
def test_forbidden_cloud_formation_has_zero_route_payload_and_world_writes(
    tmp_path: Path, permission_column: str
) -> None:
    path = tmp_path / f"forbidden-{permission_column}.sqlite3"
    clock = MutableClock()
    _initialize_database(path)
    _insert_job(path, clock)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute(f"UPDATE evidence SET {permission_column} = 0 WHERE id = 'evidence-1'")
    finally:
        db.close()
    calls: list[object] = []
    processor = HermesBatchAdapterProcessor(
        str(path), _unexpected_route(calls), clock=clock
    )
    worker = WorldJobWorker(path, processor=processor, policy=_policy(), clock=clock)

    assert worker.run_until_quiescent() == 1
    assert calls == []
    assert _world_counts(path) == (0, 0, 0, 0, 0)
    assert _job(path)["model_dispatch_started_at"] is None


def test_enqueue_then_revoke_before_dispatch_has_zero_route_and_world_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revoke-before-dispatch.sqlite3"
    clock = MutableClock()
    _initialize_database(path)
    _insert_job(path, clock)
    calls: list[object] = []
    processor = HermesBatchAdapterProcessor(
        str(path), _unexpected_route(calls), clock=clock
    )
    worker = WorldJobWorker(path, processor=processor, policy=_policy(), clock=clock)
    original_heartbeat = worker.store.heartbeat

    def revoke_after_claim(claim: object) -> bool:
        alive = original_heartbeat(claim)  # type: ignore[arg-type]
        db = sqlite3.connect(path, isolation_level=None)
        try:
            db.execute("UPDATE evidence SET allow_inference = 0 WHERE id = 'evidence-1'")
        finally:
            db.close()
        return alive

    worker.store.heartbeat = revoke_after_claim  # type: ignore[method-assign]
    assert worker.run_until_quiescent() == 1
    assert calls == []
    assert _world_counts(path) == (0, 0, 0, 0, 0)


def test_checkpoint_replay_revalidates_revoked_job_evidence_without_second_route(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint-job-evidence.sqlite3"
    clock = MutableClock()
    raw = "user Evidence evidence-1"
    _initialize_database(path)
    _insert_job(path, clock)
    calls, _claim = _checkpoint_real_route(
        path, clock, {"content": json.dumps(_one_cognition(raw)), "model": "captured"}
    )
    assert _job(path)["model_result_json"] is not None
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("UPDATE evidence SET allow_inference = 0 WHERE id = 'evidence-1'")
    finally:
        db.close()
    assert _job(path)["model_result_json"] is not None
    clock.advance(11.0)

    def forbidden_second_route(*_args: object, **_kwargs: object) -> NoReturn:
        calls.append("second-route")
        raise AssertionError("a durable model result must suppress a second route")

    worker = WorldJobWorker(
        path,
        processor=HermesBatchAdapterProcessor(str(path), forbidden_second_route, clock=clock),
        policy=_policy(),
        clock=clock,
        worker_id="replay-worker",
    )
    assert worker.run_until_quiescent() == 1
    assert len(calls) == 1
    assert _world_counts(path) == (0, 0, 0, 0, 0)
    row = _job(path)
    # The worker's pre-processor currentness gate terminally dead-letters the
    # now-unauthorized job; the signed contract here is fail-closed replay,
    # one total route, and zero World/revision/ledger mutation.
    assert row["state"] == "dead"
    assert row["terminal_state"] == "failed"
    assert row["completed_at"] and row["result_hash"]


def test_checkpoint_replay_revalidates_retracted_historical_target_without_second_route(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint-history-target.sqlite3"
    clock = MutableClock()
    raw = "user Evidence evidence-1"
    _initialize_database(path)
    _insert_job(path, clock)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute(
            "INSERT INTO evidence (id, subject_id, source_kind, host_id, origin_id, "
            "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
            "allow_cloud_read, allow_inference, deleted_at) VALUES "
            "('history-target', 'owner', 'spoken', 'test', 'history-target', ?, ?, "
            "'old preference', 'old preference', 1, 1, 1, NULL)",
            (_NOW, _NOW),
        )
        db.execute(
            "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
            "confidence, cred_status, invalid_at, created_at, updated_at) VALUES "
            "('prior-cognition', 'owner', 'old preference', 'preference', 'stated', "
            "600, 'limited', NULL, ?, ?)",
            (_NOW, _NOW),
        )
        db.execute(
            "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
            "VALUES ('prior-cognition', 'history-target', 'support')"
        )
    finally:
        db.close()
    response = {
        "content": json.dumps(
            {
                "schema_version": 2,
                "result": "cognitions",
                "cognitions": [
                    {
                        "action": "correct",
                        "target": "owner_self",
                        "statement_kind": "preference",
                        "formed_by": "stated",
                        "proposition": raw,
                        "corrects_cognition_id": "prior-cognition",
                        "supports": [
                            {"evidence_id": "evidence-1", "start": 0, "end": len(raw)}
                        ],
                    }
                ],
            }
        ),
        "model": "captured",
    }
    calls, _claim = _checkpoint_real_route(path, clock, response)
    baseline = _world_counts(path)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("UPDATE cognition SET invalid_at = ? WHERE id = 'prior-cognition'", (_NOW,))
    finally:
        db.close()
    clock.advance(11.0)
    worker = WorldJobWorker(
        path,
        processor=HermesBatchAdapterProcessor(
            str(path), lambda *_a, **_k: pytest.fail("must not route twice"), clock=clock
        ),
        policy=_policy(),
        clock=clock,
        worker_id="history-replay-worker",
    )
    assert worker.run_until_quiescent() == 1
    assert len(calls) == 1
    assert _world_counts(path) == baseline
    row = _job(path)
    assert row["state"] == "no_change"
    assert "correction_target_not_current" in str(row["world_result_json"])


def test_apply_receipt_and_world_mutations_share_one_rollback_boundary(tmp_path: Path) -> None:
    path = tmp_path / "atomic-apply.sqlite3"
    clock = MutableClock()
    raw = "user Evidence evidence-1"
    _initialize_database(path)
    _insert_job(path, clock)
    calls: list[object] = []

    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        calls.append((messages, session_id))
        return {"content": json.dumps(_one_cognition(raw)), "model": "captured"}

    store = WorldJobStore(path, policy=_policy(), clock=clock)
    claim = store.claim_one("atomic-worker")
    assert claim is not None and store.mark_dispatch_started(claim)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute(
            "CREATE TRIGGER reject_applied_receipt BEFORE UPDATE OF terminal_state "
            "ON memory_world_job WHEN NEW.terminal_state = 'applied' "
            "BEGIN SELECT RAISE(ABORT, 'receipt injection'); END"
        )
    finally:
        db.close()
    processor = HermesBatchAdapterProcessor(str(path), route, clock=clock)
    with pytest.raises(sqlite3.IntegrityError, match="receipt injection"):
        processor.process(claim)
    assert calls and len(calls) == 1
    assert _world_counts(path) == (0, 0, 0, 0, 0)
    interrupted = _job(path)
    assert interrupted["terminal_state"] is None
    assert interrupted["model_result_json"] is not None

    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("DROP TRIGGER reject_applied_receipt")
    finally:
        db.close()
    result = processor.process(claim)
    assert result.state == "applied"
    assert len(calls) == 1
    row = _job(path)
    assert row["state"] == row["terminal_state"] == "applied"
    assert row["completed_at"] and row["world_result_json"] and row["result_hash"]
    assert row["result_hash"] == sha256(str(row["world_result_json"]).encode()).hexdigest()
    assert _world_counts(path) == (1, 0, 0, 1, 1)


def test_restricted_history_and_naming_never_enter_cloud_formation_payload(
    tmp_path: Path,
) -> None:
    path = tmp_path / "formation-payload.sqlite3"
    clock = MutableClock()
    _initialize_database(path)
    _insert_job(path, clock)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        for evidence_id, allowed in (("history-good", 1), ("history-restricted", 0)):
            db.execute(
                "INSERT INTO evidence (id, subject_id, source_kind, host_id, origin_id, "
                "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
                "allow_cloud_read, allow_inference, deleted_at) VALUES "
                "(?, 'owner', 'spoken', 'test', ?, ?, ?, ?, ?, 1, ?, 1, NULL)",
                (evidence_id, evidence_id, _NOW, _NOW, evidence_id, evidence_id, allowed),
            )
        for entity_id, name, support in (
            ("entity-good", "小王", "history-good"),
            ("entity-restricted", "秘密名字", "history-restricted"),
        ):
            db.execute(
                "INSERT INTO entity (id, world_id, kind, canonical_name, aliases_json, "
                "invalid_at, created_at, updated_at) VALUES (?, 'owner', 'person', ?, '[]', NULL, ?, ?)",
                (entity_id, name, _NOW, _NOW),
            )
            db.execute(
                "INSERT INTO evidence_ledger (id, content, payload_json) VALUES (?, ?, ?)",
                (
                    f"ledger-{entity_id}",
                    json.dumps({"relation": "support", "entity_id": entity_id, "evidence_id": support}),
                    json.dumps({"schema_version": 1}),
                ),
            )
        db.execute(
            "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, confidence, "
            "cred_status, invalid_at, created_at, updated_at) VALUES "
            "('restricted-cognition', 'owner', 'secret history', 'attribute', 'stated', "
            "600, 'limited', NULL, ?, ?)",
            (_NOW, _NOW),
        )
        db.execute(
            "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
            "VALUES ('restricted-cognition', 'history-restricted', 'support')"
        )
    finally:
        db.close()
    payloads: list[dict[str, object]] = []

    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        del session_id
        payloads.append(json.loads(messages[1]["content"]))
        return {"content": json.dumps({"schema_version": 1, "result": "no_change"})}

    worker = WorldJobWorker(
        path,
        processor=HermesBatchAdapterProcessor(str(path), route, clock=clock),
        policy=_policy(),
        clock=clock,
    )
    assert worker.run_until_quiescent() == 1
    payload = payloads[0]
    assert as_objects(payload["current_cognitions"]) == []
    assert {item["id"] for item in as_objects(payload["current_entities"])} == {"entity-good"}
    assert "history-restricted" not in json.dumps(payload)
    assert "秘密名字" not in json.dumps(payload, ensure_ascii=False)


def _insert_recall_fixture(db: sqlite3.Connection, subject: str) -> None:
    db.execute(
        "INSERT INTO evidence (id, subject_id, source_kind, host_id, origin_id, occurred_at, "
        "recorded_at, raw_content, summary, allow_local_read, allow_cloud_read, allow_inference, "
        "deleted_at) VALUES ('recall-evidence', ?, 'spoken', 'test', 'origin', ?, ?, "
        "'用户喜欢喝咖啡', '用户喜欢喝咖啡', 1, 1, 1, NULL)",
        (subject, _NOW, _NOW),
    )
    db.execute(
        "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, confidence, "
        "cred_status, invalid_at, archived_at, muted_at, created_at, updated_at) VALUES "
        "('recall-cognition', ?, '用户喜欢喝咖啡', 'preference', 'stated', 600, "
        "'limited', NULL, NULL, NULL, ?, ?)",
        (subject, _NOW, _NOW),
    )
    db.execute(
        "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
        "VALUES ('recall-cognition', 'recall-evidence', 'support')"
    )


def test_recall_snapshot_and_dsh_export_fail_closed_across_lifecycle_changes(
    tmp_path: Path,
) -> None:
    runtime = DshMemoWeftRuntime()
    initialized = runtime.initialize(
        "s1", dsh_home=str(tmp_path), platform="acceptance", user_id="owner", auto_route=False
    )
    subject = str(initialized["subject_id"])
    path = Path(str(initialized["db_path"]))
    try:
        db = open_db(str(path))
        try:
            _insert_recall_fixture(db, subject)
            db.commit()
            stable_a = recall_world_snapshot(db, subject, "喜欢喝咖啡")
            stable_b = recall_world_snapshot(db, subject, "喜欢喝咖啡")
            assert stable_a is not None and stable_a == stable_b and stable_a.count == 1
            assert runtime.prefetch("喜欢喝咖啡")["count"] == 1
            assert [row["id"] for row in as_objects(runtime.list_world()["cognitions"])] == ["recall-cognition"]
            assert [row["id"] for row in as_objects(runtime.export_world()["evidence"])] == ["recall-evidence"]

            tokens = [stable_a.recall_snapshot_token]
            db.execute("UPDATE evidence SET deleted_at = ? WHERE id = 'recall-evidence'", (_NOW,))
            db.commit()
            deleted = recall_world_snapshot(db, subject, "喜欢喝咖啡")
            assert deleted is not None and deleted.count == 0
            tokens.append(deleted.recall_snapshot_token)
            assert runtime.prefetch("喜欢喝咖啡")["count"] == 0
            assert as_objects(runtime.list_world()["cognitions"]) == []
            assert runtime.export_world() == {"cognitions": [], "evidence": []}

            db.execute(
                "UPDATE evidence SET deleted_at = NULL, allow_local_read = 0 "
                "WHERE id = 'recall-evidence'"
            )
            db.commit()
            revoked = recall_world_snapshot(db, subject, "喜欢喝咖啡")
            assert revoked is not None and revoked.count == 0
            tokens.append(revoked.recall_snapshot_token)

            db.execute("UPDATE evidence SET allow_local_read = 1 WHERE id = 'recall-evidence'")
            db.execute("UPDATE cognition SET invalid_at = ? WHERE id = 'recall-cognition'", (_NOW,))
            db.execute(
                "INSERT OR REPLACE INTO memory_state "
                "(singleton, revision, snapshot_json, snapshot_hash) "
                "VALUES (1, 1, '{}', 'acceptance')"
            )
            db.commit()
            retracted = recall_world_snapshot(db, subject, "喜欢喝咖啡")
            assert retracted is not None and retracted.count == 0
            tokens.append(retracted.recall_snapshot_token)

            db.execute(
                "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, confidence, "
                "cred_status, invalid_at, archived_at, muted_at, created_at, updated_at) VALUES "
                "('recall-successor', ?, '用户喜欢喝咖啡', 'preference', 'stated', 600, "
                "'limited', NULL, NULL, NULL, ?, ?)",
                (subject, _NOW, _NOW),
            )
            db.execute(
                "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
                "VALUES ('recall-successor', 'recall-evidence', 'support')"
            )
            db.execute("UPDATE memory_state SET revision = 2 WHERE singleton = 1")
            db.commit()
            superseded = recall_world_snapshot(db, subject, "喜欢喝咖啡")
            assert superseded is not None
            assert superseded.selected_item_ids == (("cognition", "recall-successor"),)
            tokens.append(superseded.recall_snapshot_token)
            assert len(tokens) == len(set(tokens))
        finally:
            db.close()
    finally:
        runtime.shutdown()


@pytest.mark.parametrize(
    ("lang", "raw"), (("zh", "昨天我去了南京"), ("en", "Yesterday I went to Nanjing"))
)
def test_chinese_and_english_confirmed_event_contract_reaches_apply(
    tmp_path: Path, lang: str, raw: str
) -> None:
    path = tmp_path / f"event-{lang}.sqlite3"
    clock = MutableClock()
    _initialize_database(path)
    _insert_job(path, clock)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (raw,))
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-1'",
            (sha256(raw.encode()).hexdigest(),),
        )
    finally:
        db.close()
    prompts: list[str] = []

    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        del session_id
        prompts.append(messages[0]["content"])
        return {
            "content": json.dumps(
                {
                    "schema_version": 7,
                    "result": "cognitions",
                    "cognitions": [
                        {
                            "action": "form",
                            "target": "owner_self",
                            "statement_kind": "event",
                            "formed_by": "stated",
                            "proposition": raw,
                            "supports": [
                                {"evidence_id": "evidence-1", "start": 0, "end": len(raw)}
                            ],
                        }
                    ],
                }
            )
        }

    worker = WorldJobWorker(
        path,
        processor=HermesBatchAdapterProcessor(str(path), route, clock=clock, lang=lang),
        policy=_policy(),
        clock=clock,
    )
    assert worker.run_until_quiescent() == 1
    assert len(prompts) == 1
    assert _job(path)["terminal_state"] == "applied"
    assert _world_counts(path)[2] == 1
