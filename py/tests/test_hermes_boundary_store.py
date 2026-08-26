from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from typing import cast

import pytest

import memoweft.integrations.hermes.boundary_store as boundary_store_module
from memoweft.integrations.hermes.boundary_store import (
    BoundaryEvidenceConflictError,
    BoundaryReceiptIntegrityError,
    BoundaryReplayMismatchError,
    BoundaryStoreInputError,
    HermesBoundaryEvidenceCandidate,
    HermesBoundaryFormalTarget,
    HermesBoundaryStore,
    ValidatedHermesBoundary,
)
from memoweft.store import SqliteEvidenceStore, open_db
from memoweft.types import EvidenceInput


_NOW = datetime(2026, 8, 14, 12, 34, 56, 789000, tzinfo=timezone.utc)
_NOW_ISO = "2026-08-14T12:34:56.789Z"


def _clock() -> datetime:
    return _NOW


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _target(
    *,
    boundary_schema_version: int = 1,
    provider_name: str = "memoweft",
    parent_session_id: str = "session-parent",
    result_session_id: str = "session-parent",
    mode: str = "in_place",
    subject_id: str = "owner",
    host_id: str = "hermes:weixin",
) -> HermesBoundaryFormalTarget:
    return HermesBoundaryFormalTarget(
        boundary_schema_version=boundary_schema_version,
        provider_name=provider_name,
        parent_session_id=parent_session_id,
        result_session_id=result_session_id,
        mode=mode,
        subject_id=subject_id,
        host_id=host_id,
    )


def _changed_target(
    target: HermesBoundaryFormalTarget, field: str
) -> HermesBoundaryFormalTarget:
    if field == "boundary_schema_version":
        return replace(target, boundary_schema_version=2)
    if field == "parent_session_id":
        return replace(target, parent_session_id="different-parent")
    if field == "result_session_id":
        return replace(target, result_session_id="different-result")
    if field == "mode":
        return replace(target, mode="rotation")
    if field == "subject_id":
        return replace(target, subject_id="different-owner")
    if field == "host_id":
        return replace(target, host_id="hermes:cli")
    raise AssertionError(f"unsupported test target field: {field}")


def _candidate(
    suffix: str,
    *,
    raw_content: str | None = None,
    occurred_at: str | None = "2026-08-14T12:00:00Z",
    preceding_ai_context: str | None = "assistant context",
) -> HermesBoundaryEvidenceCandidate:
    return HermesBoundaryEvidenceCandidate(
        origin_id=f"hermes:origin-{suffix}",
        raw_content=raw_content or f"user evidence {suffix}",
        occurred_at=occurred_at,
        preceding_ai_context=preceding_ai_context,
    )


def _boundary(
    *candidates: HermesBoundaryEvidenceCandidate,
    event_id: str = "boundary-event-1",
    payload_label: str = "payload-1",
    target: HermesBoundaryFormalTarget | None = None,
) -> ValidatedHermesBoundary:
    return ValidatedHermesBoundary(
        event_id=event_id,
        payload_hash=_digest(payload_label),
        formal_target=target or _target(),
        evidence=tuple(candidates),
    )


def _counts(db: sqlite3.Connection) -> tuple[int, int]:
    evidence = int(db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0])
    jobs = int(db.execute("SELECT COUNT(*) FROM memory_world_job").fetchone()[0])
    return evidence, jobs


def _terminal_outcome_count(db: sqlite3.Connection, job_id: str | None = None) -> int:
    if job_id is None:
        return int(db.execute("SELECT COUNT(*) FROM terminal_outcome").fetchone()[0])
    return int(
        db.execute(
            "SELECT COUNT(*) FROM terminal_outcome WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
    )


def _job(db: sqlite3.Connection, event_id: str = "boundary-event-1") -> sqlite3.Row:
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    row = cursor.execute(
        "SELECT * FROM memory_world_job WHERE boundary_event_id = ?", (event_id,)
    ).fetchone()
    assert row is not None
    return cast(sqlite3.Row, row)


def test_boundary_accept_atomically_persists_multi_evidence_job_and_receipt(
    tmp_path: Path,
) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        boundary = _boundary(
            _candidate("one", raw_content="first private user sentence"),
            _candidate(
                "two",
                raw_content="second private user sentence",
                preceding_ai_context="second private assistant context",
            ),
        )
        receipt = HermesBoundaryStore(db, clock=_clock).accept(boundary)

        assert receipt.job_state == "pending"
        assert receipt.reason is None
        assert (receipt.eligible, receipt.stored, receipt.skipped) == (2, 2, 0)
        assert receipt.evidence_count == 2
        assert receipt.accepted_at == _NOW_ISO
        assert len(receipt.receipt_hash) == 64

        evidence_rows = db.execute(
            "SELECT id, raw_content, preceding_ai_context FROM evidence ORDER BY rowid"
        ).fetchall()
        assert [row[1] for row in evidence_rows] == [
            "first private user sentence",
            "second private user sentence",
        ]
        assert [row[2] for row in evidence_rows] == [
            "assistant context",
            "second private assistant context",
        ]

        job = _job(db)
        assert job["state"] == "pending"
        assert job["attempts"] == 0
        assert job["next_attempt_at"] == _NOW_ISO
        assert job["completed_at"] is None
        assert job["model_task"] == "memory_world"
        assert json.loads(job["evidence_ids_json"]) == [
            row[0] for row in evidence_rows
        ]
        assert job["delivery_receipt_hash"] == receipt.receipt_hash
        assert json.loads(job["delivery_receipt_json"]) == {
            key: value
            for key, value in receipt.as_dict().items()
            if key != "receipt_hash"
        }
        assert "first private user sentence" not in job["delivery_receipt_json"]
        assert "second private assistant context" not in job["delivery_receipt_json"]
        assert "evidence_ids" not in receipt.as_dict()
    finally:
        db.close()


def test_no_eligible_boundary_persists_terminal_no_change_receipt_and_reason(
    tmp_path: Path,
) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        receipt = HermesBoundaryStore(db, clock=_clock).accept(_boundary())

        assert receipt.job_state == "no_change"
        assert receipt.reason == "no_eligible_user_evidence"
        assert (receipt.eligible, receipt.stored, receipt.skipped) == (0, 0, 0)
        assert receipt.evidence_count == 0
        assert _counts(db) == (0, 1)

        job = _job(db)
        assert job["state"] == "no_change"
        assert job["next_attempt_at"] is None
        assert job["model_task"] is None
        assert job["completed_at"] == _NOW_ISO
        result = {"reason": "no_eligible_user_evidence", "state": "no_change"}
        result_json = json.dumps(
            result,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert job["world_result_json"] == result_json
        assert job["result_hash"] == sha256(result_json.encode("utf-8")).hexdigest()
        assert _terminal_outcome_count(db, str(job["job_id"])) == 1

        # Exact replay is receipt-only: it neither updates the first outcome nor
        # produces a second row for the same terminal Job.
        assert HermesBoundaryStore(db, clock=_clock).accept(_boundary()) == receipt
        assert _terminal_outcome_count(db, str(job["job_id"])) == 1
    finally:
        db.close()


def test_no_eligible_outcome_insert_failure_rolls_back_acceptance_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = open_db(str(tmp_path / "terminal-outcome-rollback.sqlite3"))
    try:
        def fail(_db: sqlite3.Connection, _job_id: str) -> dict[str, object]:
            raise RuntimeError("injected terminal outcome write failure")

        monkeypatch.setattr(
            boundary_store_module, "persist_terminal_outcome_in_transaction", fail
        )
        with pytest.raises(RuntimeError, match="terminal outcome write failure"):
            HermesBoundaryStore(db, clock=_clock).accept(_boundary())

        assert _counts(db) == (0, 0)
        assert _terminal_outcome_count(db) == 0
    finally:
        db.close()


def test_duplicate_origin_in_one_boundary_fails_closed_before_any_write(
    tmp_path: Path,
) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        first = _candidate("duplicate", raw_content="first form")
        conflicting = _candidate("duplicate", raw_content="second form")

        with pytest.raises(BoundaryStoreInputError, match="duplicate Evidence origin"):
            HermesBoundaryStore(db, clock=_clock).accept(
                _boundary(first, conflicting)
            )

        assert _counts(db) == (0, 0)
    finally:
        db.close()


def test_repeated_text_with_distinct_origins_remains_two_evidence_refs(
    tmp_path: Path,
) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        receipt = HermesBoundaryStore(db, clock=_clock).accept(
            _boundary(
                _candidate("repeat-one", raw_content="继续"),
                _candidate("repeat-two", raw_content="继续"),
            )
        )

        evidence_ids = json.loads(_job(db)["evidence_ids_json"])
        assert receipt.evidence_count == 2
        assert len(evidence_ids) == 2
        assert evidence_ids[0] != evidence_ids[1]
        assert db.execute(
            "SELECT COUNT(*) FROM evidence WHERE raw_content = '继续'"
        ).fetchone()[0] == 2
    finally:
        db.close()


def test_exact_replay_returns_first_receipt_after_worker_changes_mutable_state(
    tmp_path: Path,
) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        boundary = _boundary(_candidate("one"))
        store = HermesBoundaryStore(db, clock=_clock)
        first = store.accept(boundary)
        first_receipt_json = _job(db)["delivery_receipt_json"]

        db.execute(
            "UPDATE memory_world_job SET state = 'applied', next_attempt_at = NULL, "
            "completed_at = ? WHERE boundary_event_id = ?",
            ("2026-08-14T13:00:00.000Z", boundary.event_id),
        )
        replay = store.accept(boundary)

        assert replay == first
        assert replay.as_dict() == first.as_dict()
        assert replay.stored == 1
        assert replay.skipped == 0
        assert _counts(db) == (1, 1)
        assert _job(db)["state"] == "applied"
        assert _job(db)["delivery_receipt_json"] == first_receipt_json
    finally:
        db.close()


def test_exact_replay_after_evidence_deletion_does_not_resurrect_or_rewrite_it(
    tmp_path: Path,
) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        boundary = _boundary(_candidate("one"))
        store = HermesBoundaryStore(db, clock=_clock)
        first = store.accept(boundary)
        evidence_id = json.loads(_job(db)["evidence_ids_json"])[0]
        assert SqliteEvidenceStore(db, clock=_clock).remove(evidence_id) is True

        replay = store.accept(boundary)

        assert replay == first
        assert _counts(db) == (1, 1)
        row = db.execute(
            "SELECT origin_id, deleted_at FROM evidence WHERE id = ?",
            (evidence_id,),
        ).fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] is not None
        assert db.execute(
            "SELECT COUNT(*) FROM evidence WHERE deleted_at IS NULL"
        ).fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.parametrize(
    "changed",
    [
        "payload_hash",
        "boundary_schema_version",
        "parent_session_id",
        "result_session_id",
        "mode",
        "subject_id",
        "host_id",
    ],
)
def test_same_event_with_any_changed_payload_or_formal_target_is_zero_write(
    tmp_path: Path,
    changed: str,
) -> None:
    db = open_db(str(tmp_path / f"{changed}.sqlite3"))
    try:
        store = HermesBoundaryStore(db, clock=_clock)
        original = _boundary(_candidate("one"))
        store.accept(original)
        before = (
            _counts(db),
            tuple(db.execute("SELECT id, raw_content FROM evidence ORDER BY rowid")),
            tuple(db.execute("SELECT * FROM memory_world_job ORDER BY rowid")),
        )

        if changed == "payload_hash":
            replay = replace(
                original,
                payload_hash=_digest("different-payload"),
            )
        else:
            replay = replace(
                original,
                formal_target=_changed_target(original.formal_target, changed),
            )

        with pytest.raises(BoundaryReplayMismatchError):
            store.accept(replay)

        after = (
            _counts(db),
            tuple(db.execute("SELECT id, raw_content FROM evidence ORDER BY rowid")),
            tuple(db.execute("SELECT * FROM memory_world_job ORDER BY rowid")),
        )
        assert after == before
    finally:
        db.close()


def test_same_event_hash_and_target_with_changed_candidate_manifest_is_zero_write(
    tmp_path: Path,
) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        store = HermesBoundaryStore(db, clock=_clock)
        original = _boundary(_candidate("one"))
        store.accept(original)
        before = _counts(db)

        with pytest.raises(BoundaryReplayMismatchError):
            store.accept(replace(original, evidence=(_candidate("new"),)))

        assert _counts(db) == before
        assert db.execute(
            "SELECT COUNT(*) FROM evidence WHERE origin_id = ?",
            ("hermes:origin-new",),
        ).fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.parametrize("failure_point", ["receipt", "job"])
def test_failure_after_evidence_rolls_back_evidence_job_and_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    db = open_db(str(tmp_path / f"{failure_point}.sqlite3"))
    try:
        store = HermesBoundaryStore(db, clock=_clock)

        def fail(**_kwargs: object) -> object:
            raise RuntimeError(f"injected {failure_point} failure")

        if failure_point == "receipt":
            monkeypatch.setattr(store, "_build_delivery_receipt", fail)
        else:
            monkeypatch.setattr(store, "_insert_job", fail)

        with pytest.raises(RuntimeError, match=f"injected {failure_point} failure"):
            store.accept(_boundary(_candidate("one"), _candidate("two")))

        assert _counts(db) == (0, 0)
        assert db.in_transaction is False
    finally:
        db.close()


@pytest.mark.parametrize(
    "conflict",
    ["raw_content", "occurred_at", "preceding_ai_context", "host_id"],
)
def test_origin_content_or_critical_metadata_conflict_rolls_back_entire_batch(
    tmp_path: Path,
    conflict: str,
) -> None:
    db = open_db(str(tmp_path / f"{conflict}.sqlite3"))
    try:
        SqliteEvidenceStore(db, clock=_clock).put(
            EvidenceInput(
                subject_id="owner",
                source_kind="spoken",
                host_id="hermes:weixin",
                origin_id="hermes:origin-shared",
                occurred_at="2026-08-14T11:00:00Z",
                raw_content="original user content",
                preceding_ai_context="original assistant context",
            )
        )
        candidate = HermesBoundaryEvidenceCandidate(
            origin_id="hermes:origin-shared",
            occurred_at=(
                "2026-08-14T11:00:01Z"
                if conflict == "occurred_at"
                else "2026-08-14T11:00:00Z"
            ),
            raw_content=(
                "different user content"
                if conflict == "raw_content"
                else "original user content"
            ),
            preceding_ai_context=(
                "different assistant context"
                if conflict == "preceding_ai_context"
                else "original assistant context"
            ),
        )
        target = _target(
            host_id="hermes:cli" if conflict == "host_id" else "hermes:weixin"
        )

        with pytest.raises(BoundaryEvidenceConflictError):
            HermesBoundaryStore(db, clock=_clock).accept(
                _boundary(_candidate("new-first"), candidate, target=target)
            )

        assert _counts(db) == (1, 0)
        rows = db.execute(
            "SELECT origin_id, raw_content FROM evidence ORDER BY rowid"
        ).fetchall()
        assert rows == [("hermes:origin-shared", "original user content")]
    finally:
        db.close()


def test_concurrent_same_event_delivery_commits_once_and_returns_exact_receipt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    initial = open_db(str(db_path))
    initial.close()
    boundary = _boundary(_candidate("one"), _candidate("two"))
    barrier = Barrier(2)

    def deliver() -> dict[str, object]:
        db = open_db(str(db_path))
        try:
            barrier.wait(timeout=5)
            return HermesBoundaryStore(db, clock=_clock).accept(boundary).as_dict()
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(deliver) for _ in range(2)]
        receipts = [future.result(timeout=10) for future in futures]

    assert receipts[0] == receipts[1]
    assert receipts[0]["stored"] == 2
    assert receipts[0]["skipped"] == 0
    check = open_db(str(db_path))
    try:
        assert _counts(check) == (2, 1)
    finally:
        check.close()


def test_replay_revalidates_persisted_receipt_hash(tmp_path: Path) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        boundary = _boundary(_candidate("one"))
        store = HermesBoundaryStore(db, clock=_clock)
        store.accept(boundary)
        db.execute(
            "UPDATE memory_world_job SET delivery_receipt_hash = ? "
            "WHERE boundary_event_id = ?",
            ("0" * 64, boundary.event_id),
        )

        with pytest.raises(BoundaryReceiptIntegrityError, match="receipt hash"):
            store.accept(boundary)

        assert _counts(db) == (1, 1)
    finally:
        db.close()


def test_later_boundary_reuses_exact_existing_evidence_ids_without_duplication(
    tmp_path: Path,
) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        candidate = _candidate("shared")
        store = HermesBoundaryStore(db, clock=_clock)
        first = store.accept(_boundary(candidate))
        first_id = json.loads(_job(db)["evidence_ids_json"])[0]

        second = store.accept(
            _boundary(
                candidate,
                event_id="boundary-event-2",
                payload_label="payload-2",
            )
        )
        second_id = json.loads(_job(db, "boundary-event-2")["evidence_ids_json"])[0]

        assert first.stored == 1
        assert second.stored == 0
        assert second.skipped == 1
        assert second_id == first_id
        assert _counts(db) == (1, 2)
    finally:
        db.close()
