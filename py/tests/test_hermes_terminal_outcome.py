"""Durable Core terminal outcomes: identity, delivery, and stale-claim fences."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Mapping

import pytest

from memoweft.integrations.hermes.terminal_outcome import (
    TerminalOutcomeStore,
    persist_terminal_outcome_in_transaction,
)
from memoweft.store import open_db


TERMINALS = (
    "applied",
    "no_change",
    "clarification_required",
    "out_of_scope",
    "failed",
)


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def _timestamp() -> str:
    return "2026-08-24T12:00:00.000Z"


def _result_payload(outcome: Mapping[str, object]) -> dict[str, object]:
    """The stored result hash intentionally excludes outcome/delivery identity."""

    return {
        name: outcome[name]
        for name in (
            "schema_version",
            "job_id",
            "boundary_event_id",
            "provider_name",
            "subject_id",
            "parent_session_id",
            "result_session_id",
            "terminal_state",
            "terminal_detail",
            "world_revision",
            "world_result",
            "occurred_at",
        )
    }


def _claim_token(outcome: Mapping[str, object]) -> str:
    token = outcome["claim_token"]
    assert isinstance(token, str) and token
    return token


def _seed_terminal_job(
    path: Path,
    *,
    job_id: str = "job-1",
    terminal_state: str = "applied",
    with_memory_state: bool = True,
) -> None:
    """Create an already-settled v15 job; N1 must derive its v16 outcome from it."""

    db = open_db(str(path))
    try:
        if with_memory_state:
            revision = 41 if terminal_state == "applied" else 7
            db.execute(
                "INSERT INTO memory_state (singleton, revision, snapshot_json, snapshot_hash) "
                "VALUES (1, ?, '{}', 'snapshot') "
                "ON CONFLICT(singleton) DO UPDATE SET revision = excluded.revision",
                (revision,),
            )
        formal_target = _canonical(
            {
                "boundary_schema_version": 1,
                "host_id": "hermes:test",
                "mode": "in_place",
                "parent_session_id": "parent-session",
                "provider_name": "memoweft",
                "result_session_id": "result-session",
                "subject_id": "subject-1",
            }
        )
        state = "dead" if terminal_state == "failed" else (
            "applied" if terminal_state == "applied" else "no_change"
        )
        world_result: dict[str, object] = {
            "reason": f"{terminal_state}_reason",
            "schema_version": 1,
            "state": terminal_state,
        }
        if terminal_state == "applied":
            world_result["world_revision"] = 41
        world_json = _canonical(world_result)
        receipt = _canonical({"schema_version": 1, "job_id": job_id})
        db.execute(
            """INSERT INTO memory_world_job (
                 job_id, job_schema_version, boundary_event_id,
                 boundary_payload_hash, boundary_schema_version, provider_name,
                 parent_session_id, result_session_id, boundary_mode,
                 formal_target_json, formal_target_hash, subject_id, host_id,
                 evidence_ids_json, state, attempts, delivery_receipt_json,
                 delivery_receipt_hash, created_at, completed_at, world_result_json,
                 result_hash, last_error_type, terminal_state, terminal_detail
               ) VALUES (?, 1, ?, ?, 1, 'memoweft', ?, ?, 'in_place', ?, ?,
                         'subject-1', 'hermes:test', '[]', ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                f"boundary-{job_id}",
                "b" * 64,
                "parent-session",
                "result-session",
                formal_target,
                sha256(formal_target.encode()).hexdigest(),
                state,
                receipt,
                sha256(receipt.encode()).hexdigest(),
                _timestamp(),
                _timestamp(),
                world_json,
                sha256(world_json.encode()).hexdigest(),
                "failed_reason" if terminal_state == "failed" else None,
                terminal_state,
                "Which person do you mean?"
                if terminal_state == "clarification_required"
                else ("Not a memory-world action." if terminal_state == "out_of_scope" else None),
            ),
        )
    finally:
        db.close()


@pytest.mark.parametrize("terminal_state", TERMINALS)
def test_persist_five_terminal_outcomes_have_stable_identity_and_hash(
    tmp_path: Path, terminal_state: str
) -> None:
    path = tmp_path / f"{terminal_state}.sqlite3"
    _seed_terminal_job(path, terminal_state=terminal_state)
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        first = persist_terminal_outcome_in_transaction(db, "job-1")
        second = persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("COMMIT")
    finally:
        db.close()

    assert first == second
    assert first["terminal_state"] == terminal_state
    assert first["job_id"] == "job-1"
    assert first["boundary_event_id"] == "boundary-job-1"
    assert first["subject_id"] == "subject-1"
    assert first["result_session_id"] == "result-session"
    assert first["result_hash"] == sha256(_canonical(_result_payload(first)).encode()).hexdigest()
    assert first["delivery_state"] == "pending"


@pytest.mark.parametrize("terminal_state", ("clarification_required", "out_of_scope"))
def test_user_facing_terminal_requires_nonempty_bounded_detail(
    tmp_path: Path, terminal_state: str
) -> None:
    path = tmp_path / f"missing-detail-{terminal_state}.sqlite3"
    _seed_terminal_job(path, terminal_state=terminal_state)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute(
            "UPDATE memory_world_job SET terminal_detail = NULL WHERE job_id = 'job-1'"
        )
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="requires terminal_detail"):
            persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("ROLLBACK")

        db.execute(
            "UPDATE memory_world_job SET terminal_detail = ? WHERE job_id = 'job-1'",
            ("x" * 501,),
        )
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="exceeds 500"):
            persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("ROLLBACK")
    finally:
        db.close()


def test_applied_uses_world_result_revision_and_other_terminal_uses_current_world_revision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision.sqlite3"
    _seed_terminal_job(path, job_id="applied", terminal_state="applied")
    _seed_terminal_job(path, job_id="no-change", terminal_state="no_change")
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        applied = persist_terminal_outcome_in_transaction(db, "applied")
        no_change = persist_terminal_outcome_in_transaction(db, "no-change")
        db.execute("COMMIT")
    finally:
        db.close()
    assert applied["world_revision"] == 41
    assert no_change["world_revision"] == 7


def test_non_applied_without_world_row_records_zero_revision(tmp_path: Path) -> None:
    path = tmp_path / "zero-revision.sqlite3"
    _seed_terminal_job(
        path, terminal_state="failed", with_memory_state=False
    )
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        outcome = persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("COMMIT")
    finally:
        db.close()
    assert outcome["world_revision"] == 0


def test_non_applied_replay_keeps_first_persisted_revision_after_world_advances(
    tmp_path: Path,
) -> None:
    path = tmp_path / "replay-frozen-revision.sqlite3"
    _seed_terminal_job(path, terminal_state="no_change")
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        first = persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("COMMIT")
        db.execute("UPDATE memory_state SET revision = 8 WHERE singleton = 1")
        db.execute("BEGIN IMMEDIATE")
        replay = persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("COMMIT")
    finally:
        db.close()
    assert first["world_revision"] == 7
    assert replay["world_revision"] == 7
    assert replay["outcome_id"] == first["outcome_id"]
    assert replay["result_hash"] == first["result_hash"]


def test_persist_rejects_missing_or_damaged_job_terminal_binding_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "damaged.sqlite3"
    _seed_terminal_job(path)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("UPDATE memory_world_job SET result_hash = 'wrong' WHERE job_id = 'job-1'")
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="result_hash"):
            persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("ROLLBACK")
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="terminal"):
            persist_terminal_outcome_in_transaction(db, "missing")
        db.execute("ROLLBACK")
    finally:
        db.close()


def test_read_revalidates_persisted_outcome_hash_and_identity(tmp_path: Path) -> None:
    path = tmp_path / "read-verify.sqlite3"
    _seed_terminal_job(path)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        persisted = persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("COMMIT")
        db.execute(
            "UPDATE terminal_outcome SET result_hash = 'tampered' WHERE outcome_id = ?",
            (persisted["outcome_id"],),
        )
    finally:
        db.close()
    with pytest.raises(ValueError, match="result_hash"):
        TerminalOutcomeStore(path).get_terminal_outcome("job-1")


def test_claim_heartbeat_ack_and_competing_claimers_are_fenced(tmp_path: Path) -> None:
    path = tmp_path / "claim.sqlite3"
    _seed_terminal_job(path)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        persisted = persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("COMMIT")
    finally:
        db.close()

    store = TerminalOutcomeStore(path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        candidates = list(
            executor.map(
                lambda owner: TerminalOutcomeStore(path).claim_terminal_outcomes(owner, limit=8),
                ("consumer-a", "consumer-b"),
            )
        )
    first = [claim for result in candidates for claim in result]
    assert len(first) == 1
    claim = first[0]
    assert claim["outcome_id"] == persisted["outcome_id"]
    assert store.claim_terminal_outcomes("consumer-b", limit=8) == []
    assert store.heartbeat_terminal_outcome(
        claim["outcome_id"], _claim_token(claim)
    ) is True
    assert store.ack_terminal_outcome(
        claim["outcome_id"], "wrong-token"
    ) is False
    assert store.ack_terminal_outcome(
        claim["outcome_id"], _claim_token(claim)
    ) is True
    assert store.get_terminal_outcome("job-1")["delivery_state"] == "delivered"


def test_nack_retry_dead_and_stale_recovery_preserve_business_terminal(tmp_path: Path) -> None:
    path = tmp_path / "retry.sqlite3"
    _seed_terminal_job(path, terminal_state="out_of_scope")
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        persisted = persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("COMMIT")
    finally:
        db.close()
    store = TerminalOutcomeStore(path)
    claim = store.claim_terminal_outcomes("consumer-a")[0]
    assert store.nack_terminal_outcome(
        claim["outcome_id"], _claim_token(claim), error_type="host_unavailable"
    ) is True
    retried = store.get_terminal_outcome("job-1")
    assert retried["delivery_state"] == "retry"
    assert retried["terminal_state"] == "out_of_scope"

    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute(
            "UPDATE terminal_outcome SET next_attempt_at = '2000-01-01T00:00:00.000Z'"
        )
    finally:
        db.close()
    retry_claim = store.claim_terminal_outcomes("consumer-a")[0]
    for _ in range(3):
        assert store.nack_terminal_outcome(
            retry_claim["outcome_id"], _claim_token(retry_claim), error_type="host_unavailable"
        ) is True
        if store.get_terminal_outcome("job-1")["delivery_state"] == "dead":
            break
        db = sqlite3.connect(path, isolation_level=None)
        try:
            db.execute(
                "UPDATE terminal_outcome SET next_attempt_at = '2000-01-01T00:00:00.000Z'"
            )
        finally:
            db.close()
        retry_claim = store.claim_terminal_outcomes("consumer-a")[0]
    dead = store.get_terminal_outcome("job-1")
    assert dead["delivery_state"] == "dead"
    assert dead["terminal_state"] == "out_of_scope"
    assert dead["outcome_id"] == persisted["outcome_id"]


def test_stale_recovery_reclaims_and_old_token_cannot_mutate(tmp_path: Path) -> None:
    path = tmp_path / "stale.sqlite3"
    _seed_terminal_job(path)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        persist_terminal_outcome_in_transaction(db, "job-1")
        db.execute("COMMIT")
    finally:
        db.close()
    store = TerminalOutcomeStore(path)
    old = store.claim_terminal_outcomes("consumer-a")[0]
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute(
            "UPDATE terminal_outcome SET lease_expires_at = '2000-01-01T00:00:00.000Z'"
        )
    finally:
        db.close()
    assert store.recover_stale_terminal_outcomes() == 1
    new = store.claim_terminal_outcomes("consumer-b")[0]
    assert new["claim_token"] != old["claim_token"]
    assert store.heartbeat_terminal_outcome(
        old["outcome_id"], _claim_token(old)
    ) is False
    assert store.ack_terminal_outcome(
        old["outcome_id"], _claim_token(old)
    ) is False
    assert store.ack_terminal_outcome(
        new["outcome_id"], _claim_token(new)
    ) is True
