from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import cast

import pytest

from memoweft.integrations.hermes.boundary_store import (
    HermesBoundaryEvidenceCandidate,
    HermesBoundaryFormalTarget,
    HermesBoundaryStore,
    ValidatedHermesBoundary,
)
from memoweft.integrations.hermes.world_worker import (
    ClaimedWorldJob,
    PermanentWorldJobError,
    RetryableWorldJobError,
    WorldJobPolicy,
    WorldJobResult,
    WorldJobStore,
    WorldJobWorker,
)
from memoweft.store import open_db


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class SequenceProcessor:
    def __init__(
        self,
        *outcomes: WorldJobResult | BaseException,
        dispatches_model: bool = False,
    ) -> None:
        self.dispatches_model = dispatches_model
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, ...]] = []

    def process(self, job: ClaimedWorldJob) -> WorldJobResult:
        self.calls.append(job.evidence_ids())
        if not self.outcomes:
            raise AssertionError("processor called more times than expected")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class BlockingProcessor:
    dispatches_model = False

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def process(self, job: ClaimedWorldJob) -> WorldJobResult:
        del job
        self.entered.set()
        if not self.release.wait(5.0):
            raise AssertionError("test did not release blocking processor")
        return WorldJobResult.no_change("test_released")


def _timestamp(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _initialize_database(path: Path) -> None:
    db = open_db(str(path))
    db.close()


def _insert_job(
    path: Path,
    clock: MutableClock,
    *,
    job_id: str = "job-1",
    evidence_ids: tuple[str, ...] = ("evidence-1",),
    attempts: int = 0,
) -> None:
    now = _timestamp(clock())
    formal_target_json = _canonical(
        {
            "boundary_schema_version": 1,
            "provider_name": "memoweft",
            "parent_session_id": "session-parent",
            "result_session_id": "session-parent",
            "mode": "in_place",
            "subject_id": "owner",
            "host_id": "hermes:test",
        }
    )
    receipt_json = _canonical(
        {
            "schema_version": 1,
            "event_id": f"boundary-{job_id}",
            "payload_hash": "a" * 64,
            "job_id": job_id,
        }
    )
    db = sqlite3.connect(path, isolation_level=None)
    try:
        for evidence_id in dict.fromkeys(evidence_ids):
            db.execute(
                """INSERT OR IGNORE INTO evidence (
                     id, subject_id, source_kind, host_id, origin_id,
                     occurred_at, recorded_at, raw_content, summary,
                     allow_local_read, allow_cloud_read, allow_inference,
                     corrects_evidence_id, deleted_at, preceding_ai_context
                   ) VALUES (?, 'owner', 'spoken', 'hermes:test', ?, ?, ?, ?, ?,
                             1, 1, 1, NULL, NULL, NULL)""",
                (
                    evidence_id,
                    f"hermes:test:{job_id}:{evidence_id}",
                    now,
                    now,
                    f"user Evidence {evidence_id}",
                    f"user Evidence {evidence_id}",
                ),
            )
        for evidence_id in dict.fromkeys(evidence_ids):
            db.execute(
                """INSERT OR IGNORE INTO boundary_evidence_content (
                     evidence_id, raw_content_hash
                   ) VALUES (?, ?)""",
                (
                    evidence_id,
                    sha256(
                        f"user Evidence {evidence_id}".encode("utf-8")
                    ).hexdigest(),
                ),
            )
        db.execute(
            """INSERT INTO memory_world_job (
                 job_id, job_schema_version, boundary_event_id,
                 boundary_payload_hash, boundary_schema_version, provider_name,
                 parent_session_id, result_session_id, boundary_mode,
                 formal_target_json, formal_target_hash, subject_id, host_id,
                 evidence_ids_json, state, attempts, next_attempt_at, model_task,
                 delivery_receipt_json, delivery_receipt_hash, created_at
               ) VALUES (?, 1, ?, ?, 1, 'memoweft', ?, ?, 'in_place',
                         ?, ?, 'owner', 'hermes:test', ?, 'pending', ?, ?,
                         'memory_world', ?, ?, ?)""",
            (
                job_id,
                f"boundary-{job_id}",
                "a" * 64,
                "session-parent",
                "session-parent",
                formal_target_json,
                sha256(formal_target_json.encode("utf-8")).hexdigest(),
                _canonical(list(evidence_ids)),
                attempts,
                now,
                receipt_json,
                sha256(receipt_json.encode("utf-8")).hexdigest(),
                now,
            ),
        )
    finally:
        db.close()


def _job(path: Path, job_id: str = "job-1") -> dict[str, object]:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            "SELECT * FROM memory_world_job WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert row is not None
        return {key: cast(object, row[key]) for key in row.keys()}
    finally:
        db.close()


def _policy() -> WorldJobPolicy:
    return WorldJobPolicy(
        lease_seconds=10.0,
        heartbeat_seconds=1.0,
        max_attempts=4,
        retry_backoff_seconds=(5.0, 30.0, 300.0),
    )


def test_claim_is_atomic_across_competing_workers(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    store_a = WorldJobStore(db_path, policy=_policy(), clock=clock)
    store_b = WorldJobStore(db_path, policy=_policy(), clock=clock)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(store_a.claim_one, "worker-a"),
            pool.submit(store_b.claim_one, "worker-b"),
        ]
        claims = [future.result() for future in futures]

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0].attempts == 1
    assert winners[0].fencing_generation == 1
    row = _job(db_path)
    assert row["state"] == "processing"
    assert row["claim_owner"] in {"worker-a", "worker-b"}


def test_heartbeat_extends_only_exact_token_and_generation(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    store = WorldJobStore(db_path, policy=_policy(), clock=clock)
    claim = store.claim_one("worker-a")
    assert claim is not None
    old_lease = claim.lease_expires_at

    clock.advance(3.0)
    assert store.heartbeat(claim) is True
    assert cast(str, _job(db_path)["lease_expires_at"]) > old_lease
    assert store.heartbeat(replace(claim, claim_token="stale-token")) is False
    assert store.heartbeat(replace(claim, fencing_generation=0)) is False


def test_expired_undispatched_claim_is_recovered_with_new_fence(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    store = WorldJobStore(db_path, policy=_policy(), clock=clock)
    first = store.claim_one("worker-a")
    assert first is not None

    clock.advance(11.0)
    assert store.heartbeat(first) is False
    assert store.mark_dispatch_started(first) is False
    assert store.settle(first, WorldJobResult.applied()) is False
    second = store.claim_one("worker-b")
    assert second is not None
    assert second.claim_token != first.claim_token
    assert second.fencing_generation == first.fencing_generation + 1
    assert second.attempts == 2
    assert store.heartbeat(first) is False
    assert store.settle(first, WorldJobResult.applied()) is False
    assert store.settle(second, WorldJobResult.no_change("recovered")) is True


def test_expired_dispatch_marker_is_dead_and_never_redispatched(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    store = WorldJobStore(db_path, policy=_policy(), clock=clock)
    claim = store.claim_one("crashed-worker")
    assert claim is not None
    assert store.mark_dispatch_started(claim) is True
    clock.advance(11.0)

    processor = SequenceProcessor(
        WorldJobResult.applied(),
        dispatches_model=True,
    )
    restarted = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
        worker_id="restarted-worker",
    )
    assert restarted.run_until_quiescent() == 0
    assert processor.calls == []
    row = _job(db_path)
    assert row["state"] == "dead"
    assert row["last_error_type"] == "dispatch_outcome_unknown"
    assert row["completed_at"] is not None


def test_default_processor_is_terminal_no_change_with_zero_dispatch(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    worker = WorldJobWorker(
        db_path,
        policy=_policy(),
        clock=clock,
        worker_id="default-worker",
    )

    assert worker.run_until_quiescent() == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert row["model_dispatch_started_at"] is None
    assert row["model_completed_at"] is None
    assert row["last_error_type"] is None
    world_result = json.loads(cast(str, row["world_result_json"]))
    assert world_result["reason"] == "formal_batch_adapter_unavailable"


def test_zero_eligible_evidence_never_calls_processor(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=())
    processor = SequenceProcessor(WorldJobResult.applied(), dispatches_model=True)
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.run_until_quiescent() == 1
    assert processor.calls == []
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert row["model_dispatch_started_at"] is None
    assert json.loads(cast(str, row["world_result_json"]))["reason"] == (
        "no_eligible_user_evidence"
    )


def test_multiple_evidence_are_one_batch_and_one_dispatch(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=("evidence-1", "evidence-2"))
    processor = SequenceProcessor(
        WorldJobResult.applied(
            world_result={"object_count": 1},
            model_provider="test-provider",
            model_name="test-model",
            model_usage={"input_tokens": 12, "output_tokens": 3},
            model_result={"kind": "test-result"},
        ),
        dispatches_model=True,
    )
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.run_until_quiescent() == 1
    assert processor.calls == [("evidence-1", "evidence-2")]
    row = _job(db_path)
    assert row["state"] == "applied"
    assert row["model_dispatch_started_at"] is not None
    assert row["model_completed_at"] is not None
    assert row["model_provider"] == "test-provider"
    assert row["model_name"] == "test-model"
    assert row["model_result_hash"] is not None
    assert row["result_hash"] is not None


def test_model_retry_result_is_dead_after_the_single_dispatch(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    processor = SequenceProcessor(
        WorldJobResult.retry("remote_transient"),
        dispatches_model=True,
    )
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.run_until_quiescent() == 1
    assert len(processor.calls) == 1
    row = _job(db_path)
    assert row["state"] == "dead"
    assert row["last_error_type"] == "post_dispatch_retry_forbidden"
    assert row["model_completed_at"] is not None
    clock.advance(1_000.0)
    assert worker.run_until_quiescent() == 0
    assert len(processor.calls) == 1


def test_model_exception_is_unknown_dead_and_never_called_twice(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    processor = SequenceProcessor(
        RetryableWorldJobError("remote_transient"),
        WorldJobResult.applied(),
        dispatches_model=True,
    )
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.run_until_quiescent() == 1
    row = _job(db_path)
    assert row["state"] == "dead"
    assert row["last_error_type"] == "dispatch_outcome_unknown"
    assert row["model_completed_at"] is None
    clock.advance(1_000.0)
    assert worker.run_until_quiescent() == 0
    assert len(processor.calls) == 1


def test_predispatch_retry_uses_fixed_backoff_then_succeeds(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    processor = SequenceProcessor(
        WorldJobResult.retry("adapter_not_ready"),
        WorldJobResult.applied(),
    )
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.run_until_quiescent(max_jobs=1) == 1
    row = _job(db_path)
    assert row["state"] == "retry"
    assert row["next_attempt_at"] == _timestamp(clock() + timedelta(seconds=5))
    assert row["world_result_json"] is None
    assert row["result_hash"] is None
    clock.advance(4.0)
    assert worker.run_until_quiescent() == 0
    clock.advance(1.0)
    assert worker.run_until_quiescent() == 1
    assert _job(db_path)["state"] == "applied"
    assert len(processor.calls) == 2


def test_fixed_attempt_budget_dead_letters_fourth_predispatch_failure(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    processor = SequenceProcessor(
        *[WorldJobResult.retry("adapter_not_ready") for _ in range(4)]
    )
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    for delay in (5.0, 30.0, 300.0):
        assert worker.run_until_quiescent(max_jobs=1) == 1
        assert _job(db_path)["state"] == "retry"
        clock.advance(delay)
    assert worker.run_until_quiescent(max_jobs=1) == 1
    row = _job(db_path)
    assert row["state"] == "dead"
    assert row["terminal_state"] == "failed"  # AUTHORITY §3：事务/权威失败
    assert row["attempts"] == 4
    assert row["last_error_type"] == "max_attempts_exhausted"
    assert len(processor.calls) == 4


def test_permanent_predispatch_error_is_dead_without_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    processor = SequenceProcessor(
        PermanentWorldJobError("invalid_formal_target"),
    )
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.run_until_quiescent() == 1
    row = _job(db_path)
    assert row["state"] == "dead"
    assert row["last_error_type"] == "invalid_formal_target"
    assert row["attempts"] == 1


def test_invalid_or_duplicate_evidence_refs_are_permanent_and_not_processed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=("same", "same"))
    processor = SequenceProcessor(WorldJobResult.applied())
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.run_until_quiescent() == 1
    assert processor.calls == []
    assert _job(db_path)["last_error_type"] == "duplicate_evidence_ids"


def test_formal_target_mismatch_is_dead_before_processor_or_model(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute(
            "UPDATE memory_world_job SET subject_id = 'different-owner' "
            "WHERE job_id = 'job-1'"
        )
    finally:
        db.close()
    processor = SequenceProcessor(WorldJobResult.applied(), dispatches_model=True)
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.run_until_quiescent() == 1
    assert processor.calls == []
    row = _job(db_path)
    assert row["state"] == "dead"
    assert row["last_error_type"] == "formal_target_mismatch"
    assert row["model_dispatch_started_at"] is None


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("DELETE FROM evidence WHERE id = 'evidence-1'", "evidence_missing"),
        (
            "UPDATE evidence SET deleted_at = '2026-08-14T12:00:01.000Z' "
            "WHERE id = 'evidence-1'",
            "evidence_deleted",
        ),
        (
            "UPDATE evidence SET subject_id = 'different-owner' "
            "WHERE id = 'evidence-1'",
            "evidence_target_mismatch",
        ),
        (
            "UPDATE evidence SET host_id = 'hermes:different' "
            "WHERE id = 'evidence-1'",
            "evidence_target_mismatch",
        ),
        (
            "UPDATE evidence SET source_kind = 'observed' "
            "WHERE id = 'evidence-1'",
            "evidence_source_kind_mismatch",
        ),
        (
            "UPDATE evidence SET raw_content = 'tampered' "
            "WHERE id = 'evidence-1'",
            "evidence_content_hash_mismatch",
        ),
        (
            "DELETE FROM boundary_evidence_content "
            "WHERE evidence_id = 'evidence-1'",
            "evidence_content_hash_missing",
        ),
    ],
)
def test_missing_deleted_or_off_target_evidence_is_dead_before_processor(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute(mutation)
    finally:
        db.close()
    processor = SequenceProcessor(WorldJobResult.applied(), dispatches_model=True)
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.run_until_quiescent() == 1
    assert processor.calls == []
    row = _job(db_path)
    assert row["state"] == "dead"
    assert row["last_error_type"] == expected_error
    assert row["model_dispatch_started_at"] is None


def test_worker_consumes_the_real_boundary_store_pending_job_contract(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    db = open_db(str(db_path))
    try:
        receipt = HermesBoundaryStore(db, clock=clock).accept(
            ValidatedHermesBoundary(
                event_id="boundary-store-worker-contract",
                payload_hash="b" * 64,
                formal_target=HermesBoundaryFormalTarget(
                    boundary_schema_version=1,
                    provider_name="memoweft",
                    parent_session_id="session-parent",
                    result_session_id="session-parent",
                    mode="in_place",
                    subject_id="owner",
                    host_id="hermes:test",
                ),
                evidence=(
                    HermesBoundaryEvidenceCandidate(
                        origin_id="hermes:boundary-store-worker-contract:0",
                        raw_content="one real boundary Evidence row",
                    ),
                ),
            )
        )
    finally:
        db.close()

    worker = WorldJobWorker(db_path, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    row = _job(db_path, receipt.job_id)
    assert row["state"] == "no_change"
    assert row["model_dispatch_started_at"] is None
    assert json.loads(cast(str, row["world_result_json"]))["reason"] == (
        "formal_batch_adapter_unavailable"
    )


def test_shutdown_is_bounded_while_processor_finishes_outside_chat_thread(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    processor = BlockingProcessor()
    worker = WorldJobWorker(
        db_path,
        processor=processor,
        policy=_policy(),
        clock=clock,
    )

    assert worker.start() is True
    assert processor.entered.wait(2.0)
    started = time.monotonic()
    assert worker.shutdown(timeout=0.01) is False
    assert time.monotonic() - started < 0.5
    processor.release.set()
    assert worker.shutdown(timeout=2.0) is True
    assert _job(db_path)["state"] == "no_change"
