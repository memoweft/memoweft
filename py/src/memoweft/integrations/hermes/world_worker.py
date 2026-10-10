"""Durable, plugin-owned worker for Hermes compression-boundary World jobs.

The Hermes delivery callback owns only the short Evidence + job + receipt
transaction.  This module owns the later asynchronous job lifecycle.  It does
not import Hermes and the production processor deliberately performs no model
or World write until a formal one-call batch adapter exists.

The model-dispatch marker implements a strict at-most-once policy.  A process
that dies after persisting the marker but before persisting a result leaves an
unknowable remote-call outcome; recovery therefore dead-letters that job as
``dispatch_outcome_unknown`` instead of dispatching a second time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Callable, Literal, Mapping, Protocol, cast
from uuid import uuid4

from ...clock import to_iso_z
from ...store.driver import BUSY_TIMEOUT_MS
from ...types import ModelTier
from ..trust.currentness import evidence_state
from .worker_lifetime import PREFIX, acquire_lifetime, owner_is_gone
from .terminal_outcome import persist_terminal_outcome_in_transaction

logger = logging.getLogger(__name__)

WORLD_JOB_TABLE = "memory_world_job"
WORLD_JOB_LEASE_SECONDS = 300.0
WORLD_JOB_HEARTBEAT_SECONDS = 60.0
WORLD_JOB_MAX_ATTEMPTS = 4
WORLD_JOB_RETRY_BACKOFF_SECONDS = (5.0, 30.0, 300.0)
WORLD_JOB_LOOP_ERROR_RETRY_SECONDS = 1.0

JobState = Literal[
    "pending", "processing", "applied", "no_change", "retry", "dead"
]
ProcessorState = Literal[
    "applied",
    "no_change",
    "clarification_required",
    "out_of_scope",
    "retry",
    "dead",
]
Clock = Callable[[], datetime]

#: AUTHORITY §3 terminal (observable outcome) recorded for each terminal
#: transport state; non-terminal (retry/pending/processing) rows keep NULL.
TERMINAL_BY_STATE: Mapping[str, str | None] = {
    "applied": "applied",
    "no_change": "no_change",
    "clarification_required": "clarification_required",
    "out_of_scope": "out_of_scope",
    "dead": "failed",
    "retry": None,
}

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class WorldJobError(RuntimeError):
    """Base error for the durable World job pipeline."""


class RetryableWorldJobError(WorldJobError):
    """A failure known to have happened before any model dispatch."""


class PermanentWorldJobError(WorldJobError):
    """A content-free permanent processor or persisted-job error."""


@dataclass(frozen=True, slots=True)
class WorldJobPolicy:
    """Fixed retry, lease, and heartbeat policy.

    Four claims are allowed.  Safe pre-dispatch failures wait 5s, 30s, and
    300s after the first three attempts; a fourth failure is terminal.  There
    is deliberately no jitter so restart and test behavior are reproducible.
    """

    lease_seconds: float = WORLD_JOB_LEASE_SECONDS
    heartbeat_seconds: float = WORLD_JOB_HEARTBEAT_SECONDS
    max_attempts: int = WORLD_JOB_MAX_ATTEMPTS
    retry_backoff_seconds: tuple[float, ...] = WORLD_JOB_RETRY_BACKOFF_SECONDS
    busy_timeout_ms: int = BUSY_TIMEOUT_MS

    def __post_init__(self) -> None:
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if self.heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than the lease")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if len(self.retry_backoff_seconds) != self.max_attempts - 1:
            raise ValueError("retry_backoff_seconds must cover every non-final attempt")
        if any(delay < 0 for delay in self.retry_backoff_seconds):
            raise ValueError("retry backoff values cannot be negative")
        if self.busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms cannot be negative")

    def retry_delay(self, attempts: int) -> float:
        """Return the deterministic delay after a failed numbered attempt."""

        if attempts < 1 or attempts >= self.max_attempts:
            raise ValueError("retry delay is defined only before the final attempt")
        return self.retry_backoff_seconds[attempts - 1]


@dataclass(frozen=True, slots=True)
class ClaimedWorldJob:
    """Immutable snapshot of one token- and generation-fenced claim."""

    job_id: str
    boundary_event_id: str
    boundary_payload_hash: str
    boundary_schema_version: int
    provider_name: str
    parent_session_id: str
    result_session_id: str
    boundary_mode: str
    formal_target_json: str
    formal_target_hash: str
    subject_id: str
    host_id: str
    evidence_ids_json: str
    attempts: int
    claim_owner: str
    claim_token: str
    claimed_at: str
    lease_expires_at: str
    fencing_generation: int

    def validate_formal_target(self) -> None:
        """Recheck the hash-bound target before any processor or model work."""

        if (
            len(self.boundary_payload_hash) != 64
            or any(char not in "0123456789abcdef" for char in self.boundary_payload_hash)
        ):
            raise PermanentWorldJobError("invalid_boundary_payload_hash")
        if _hash_text(self.formal_target_json) != self.formal_target_hash:
            raise PermanentWorldJobError("formal_target_hash_mismatch")
        try:
            decoded = cast(object, json.loads(self.formal_target_json))
        except (TypeError, ValueError) as exc:
            raise PermanentWorldJobError("invalid_formal_target_json") from exc
        if not isinstance(decoded, dict):
            raise PermanentWorldJobError("invalid_formal_target_json")
        expected: dict[str, object] = {
            "boundary_schema_version": self.boundary_schema_version,
            "provider_name": self.provider_name,
            "parent_session_id": self.parent_session_id,
            "result_session_id": self.result_session_id,
            "mode": self.boundary_mode,
            "subject_id": self.subject_id,
            "host_id": self.host_id,
        }
        if decoded != expected or _canonical_json(decoded) != self.formal_target_json:
            raise PermanentWorldJobError("formal_target_mismatch")
        if (
            self.boundary_schema_version < 1
            or self.provider_name != "memoweft"
            or self.boundary_mode not in {"in_place", "rotation"}
            or (
                self.boundary_mode == "in_place"
                and self.parent_session_id != self.result_session_id
            )
            or (
                self.boundary_mode == "rotation"
                and self.parent_session_id == self.result_session_id
            )
        ):
            raise PermanentWorldJobError("formal_target_mismatch")

    def evidence_ids(self) -> tuple[str, ...]:
        """Decode the closed Evidence batch or raise a permanent job error."""

        try:
            decoded = cast(object, json.loads(self.evidence_ids_json))
        except (TypeError, ValueError) as exc:
            raise PermanentWorldJobError("invalid_evidence_ids_json") from exc
        if not isinstance(decoded, list):
            raise PermanentWorldJobError("invalid_evidence_ids_json")
        ids: list[str] = []
        for value in decoded:
            if not isinstance(value, str) or not value:
                raise PermanentWorldJobError("invalid_evidence_ids_json")
            ids.append(value)
        if len(ids) != len(set(ids)):
            raise PermanentWorldJobError("duplicate_evidence_ids")
        if _canonical_json(ids) != self.evidence_ids_json:
            raise PermanentWorldJobError("noncanonical_evidence_ids")
        return tuple(ids)


@dataclass(frozen=True, slots=True)
class WorldJobResult:
    """A compact processor result persisted by the fenced settlement."""

    state: ProcessorState
    reason: str
    world_result: Mapping[str, object] | None = None
    model_provider: str | None = None
    model_name: str | None = None
    model_usage: Mapping[str, object] | None = None
    model_result: Mapping[str, object] | None = None
    display: str | None = None

    def __post_init__(self) -> None:
        _validate_error_code(self.reason)
        if self.state in {"clarification_required", "out_of_scope"}:
            if not isinstance(self.display, str) or not self.display.strip():
                raise ValueError(
                    f"{self.state} requires a non-empty display"
                )
        if self.display is not None:
            if not isinstance(self.display, str) or not self.display.strip():
                raise ValueError("display must be a non-empty string when present")
            if len(self.display) > 500:
                raise ValueError("display exceeds 500 characters")

    @classmethod
    def applied(
        cls,
        *,
        reason: str = "applied",
        world_result: Mapping[str, object] | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        model_usage: Mapping[str, object] | None = None,
        model_result: Mapping[str, object] | None = None,
    ) -> WorldJobResult:
        return cls(
            state="applied",
            reason=reason,
            world_result=world_result,
            model_provider=model_provider,
            model_name=model_name,
            model_usage=model_usage,
            model_result=model_result,
        )

    @classmethod
    def no_change(
        cls,
        reason: str,
        *,
        world_result: Mapping[str, object] | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        model_usage: Mapping[str, object] | None = None,
        model_result: Mapping[str, object] | None = None,
    ) -> WorldJobResult:
        return cls(
            state="no_change",
            reason=reason,
            world_result=world_result,
            model_provider=model_provider,
            model_name=model_name,
            model_usage=model_usage,
            model_result=model_result,
        )

    @classmethod
    def clarification_required(
        cls,
        reason: str,
        *,
        display: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        model_usage: Mapping[str, object] | None = None,
        model_result: Mapping[str, object] | None = None,
    ) -> WorldJobResult:
        """AUTHORITY §3: identity or meaning could not be uniquely resolved.
        Zero World writes; ``display`` carries the clarifying question."""
        return cls(
            state="clarification_required",
            reason=reason,
            display=display,
            model_provider=model_provider,
            model_name=model_name,
            model_usage=model_usage,
            model_result=model_result,
        )

    @classmethod
    def out_of_scope(
        cls,
        reason: str,
        *,
        display: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        model_usage: Mapping[str, object] | None = None,
        model_result: Mapping[str, object] | None = None,
    ) -> WorldJobResult:
        """AUTHORITY §3: understood but outside the current formal contract.
        Zero World writes; ``display`` carries the one-line note."""
        return cls(
            state="out_of_scope",
            reason=reason,
            display=display,
            model_provider=model_provider,
            model_name=model_name,
            model_usage=model_usage,
            model_result=model_result,
        )

    @classmethod
    def retry(cls, reason: str) -> WorldJobResult:
        return cls(state="retry", reason=reason)

    @classmethod
    def dead(cls, reason: str) -> WorldJobResult:
        return cls(state="dead", reason=reason)


class WorldJobProcessor(Protocol):
    """Injectable formal batch processor.

    A processor that can issue the one permitted remote model request must set
    ``dispatches_model`` true.  The worker then durably marks dispatch before
    invoking ``process``.  A deterministic processor leaves it false.
    """

    dispatches_model: bool
    model_tier: ModelTier

    def process(self, job: ClaimedWorldJob) -> WorldJobResult:
        """Process all Evidence references in ``job`` as one batch."""


class FormalBatchAdapterUnavailableProcessor:
    """Production-safe default: terminal, deterministic, and zero World writes."""

    dispatches_model = False
    model_tier: ModelTier = "cloud"

    def process(self, job: ClaimedWorldJob) -> WorldJobResult:
        del job
        return WorldJobResult.no_change("formal_batch_adapter_unavailable")


@dataclass(frozen=True, slots=True)
class RecoverySummary:
    retried: int = 0
    dead: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("worker clock must return a timezone-aware datetime")
    return to_iso_z(value)


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PermanentWorldJobError("invalid_job_timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PermanentWorldJobError("invalid_job_timestamp")
    return parsed.astimezone(timezone.utc)


def _validate_error_code(value: str) -> str:
    if not _ERROR_CODE.fullmatch(value):
        raise ValueError("reason/error codes must be stable lowercase identifiers")
    return value


def _exception_code(exc: BaseException) -> str:
    name = type(exc).__name__
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    normalized = re.sub(r"[^a-z0-9_.-]+", "_", normalized).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = "processor_error"
    return normalized[:128]


def _declared_exception_code(exc: BaseException, fallback: str) -> str:
    """Use an explicit stable code when valid, otherwise a content-free fallback."""

    value = str(exc)
    return value if _ERROR_CODE.fullmatch(value) else fallback


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise PermanentWorldJobError("noncanonical_processor_result") from exc


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _outcome_json(result: WorldJobResult) -> str:
    payload: dict[str, object] = {
        "schema_version": 1,
        "state": result.state,
        "reason": result.reason,
    }
    if result.world_result is not None:
        payload["result"] = dict(result.world_result)
    if result.display is not None:
        payload["display"] = result.display
    return _canonical_json(payload)


class WorldJobStore:
    """SQLite state machine with short, fenced ``BEGIN IMMEDIATE`` writes."""

    def __init__(
        self,
        db_path: Path,
        *,
        policy: WorldJobPolicy | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self.db_path = Path(db_path)
        self.policy = policy or WorldJobPolicy()
        self._clock = clock

    def _now(self) -> datetime:
        value = self._clock()
        _timestamp(value)
        return value.astimezone(timezone.utc)

    def _connect(self, *, query_only: bool = False) -> sqlite3.Connection:
        db = sqlite3.connect(
            str(self.db_path),
            timeout=self.policy.busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout = {self.policy.busy_timeout_ms}")
        if query_only:
            db.execute("PRAGMA query_only = ON")
        return db

    @staticmethod
    def _rollback(db: sqlite3.Connection) -> None:
        if not db.in_transaction:
            return
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def recover_interrupted(self, *, owner: str | None = None,
                            retry_inference: bool = False,
                            busy_timeout_ms: int | None = None) -> tuple[str, ...]:
        """Revoke dead lifetimes (or this stopped worker), retaining checkpoints.

        Only DSH's pure interpretation route opts into repeating an interrupted
        inference. Generic processors keep their at-most-once dispatch contract.
        Revocation and Apply serialize on the same SQLite write transaction.
        """
        db = self._connect()
        recovered: list[str] = []
        if busy_timeout_ms is not None:
            db.execute(f"PRAGMA busy_timeout = {max(0, busy_timeout_ms)}")
        try:
            db.execute("BEGIN IMMEDIATE")
            now = _timestamp(self._now())
            rows = db.execute(
                f"SELECT * FROM {WORLD_JOB_TABLE} WHERE state = 'processing'"
            ).fetchall()
            for row in rows:
                if owner is not None:
                    if row["claim_owner"] != owner:
                        continue
                elif not owner_is_gone(self.db_path, row["claim_owner"]):
                    continue
                if (row["model_dispatch_started_at"] is not None
                        and row["model_result_json"] is None and not retry_inference):
                    self._dead_claim_in_transaction(
                        db, job_id=row["job_id"], claim_token=row["claim_token"],
                        fencing_generation=row["fencing_generation"],
                        reason="dispatch_outcome_unknown", completed_at=now)
                    continue
                reason = "shutdown_recovered" if owner is not None else "restart_recovered"
                db.execute(
                    f"""UPDATE {WORLD_JOB_TABLE}
                           SET state = 'retry', next_attempt_at = ?,
                               attempts = MAX(0, attempts - 1),
                               claim_owner = NULL, claim_token = NULL,
                               lease_expires_at = NULL, heartbeat_at = NULL,
                               fencing_generation = fencing_generation + 1,
                               model_dispatch_started_at = CASE
                                 WHEN model_result_json IS NULL THEN NULL
                                 ELSE model_dispatch_started_at END,
                               last_error_type = ?
                         WHERE job_id = ? AND state = 'processing'
                           AND claim_token = ? AND fencing_generation = ?""",
                    (now, reason, row["job_id"], row["claim_token"], row["fencing_generation"]))
                recovered.append(row["job_id"])
                logger.info("MemoWeft World job recovered: reason=%s job_id=%s", reason, row["job_id"])
            db.execute("COMMIT")
            return tuple(recovered)
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    def recover_expired(self) -> RecoverySummary:
        """Recover expired claims without dispatching or processing a job."""

        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            summary = self._recover_expired_in_transaction(db, self._now())
            db.execute("COMMIT")
            return summary
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    def _recover_expired_in_transaction(
        self, db: sqlite3.Connection, now: datetime
    ) -> RecoverySummary:
        now_text = _timestamp(now)
        rows = db.execute(
            f"""SELECT job_id, attempts, model_dispatch_started_at,
                       model_result_json, claim_token, fencing_generation
                  FROM {WORLD_JOB_TABLE}
                 WHERE state = 'processing'
                   AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                 ORDER BY created_at, job_id""",
            (now_text,),
        ).fetchall()
        retried = 0
        dead = 0
        for row in rows:
            job_id = cast(str, row["job_id"])
            attempts = cast(int, row["attempts"])
            marker = cast(str | None, row["model_dispatch_started_at"])
            model_result = cast(str | None, row["model_result_json"])
            claim_token = cast(str, row["claim_token"])
            generation = cast(int, row["fencing_generation"])
            if marker is not None:
                if model_result is None:
                    # Unknown remote outcome: never re-dispatch.
                    dead += int(
                        self._dead_claim_in_transaction(
                            db,
                            job_id=job_id,
                            claim_token=claim_token,
                            fencing_generation=generation,
                            reason="dispatch_outcome_unknown",
                            completed_at=now_text,
                        )
                    )
                else:
                    # The model result IS durably stored: replay the
                    # deterministic Apply only — never a second model call.
                    # A replay is not another remote attempt, including when
                    # the completed remote call was the final allowed one.
                    cursor = db.execute(
                        f"""UPDATE {WORLD_JOB_TABLE}
                               SET state = 'retry',
                                   next_attempt_at = ?,
                                   claim_owner = NULL,
                                   claim_token = NULL,
                                   lease_expires_at = NULL,
                                   completed_at = NULL,
                                   last_error_type = 'dispatch_result_unsettled'
                             WHERE job_id = ?
                               AND state = 'processing'
                               AND claim_token = ?
                               AND fencing_generation = ?""",
                        (now_text, job_id, claim_token, generation),
                    )
                    retried += int(cursor.rowcount == 1)
            elif attempts >= self.policy.max_attempts:
                dead += int(
                    self._dead_claim_in_transaction(
                        db,
                        job_id=job_id,
                        claim_token=claim_token,
                        fencing_generation=generation,
                        reason="max_attempts_exhausted",
                        completed_at=now_text,
                    )
                )
            else:
                cursor = db.execute(
                    f"""UPDATE {WORLD_JOB_TABLE}
                           SET state = 'retry',
                               next_attempt_at = ?,
                               claim_owner = NULL,
                               claim_token = NULL,
                               lease_expires_at = NULL,
                               completed_at = NULL,
                               last_error_type = 'lease_expired'
                         WHERE job_id = ?
                           AND state = 'processing'
                           AND claim_token = ?
                           AND fencing_generation = ?""",
                    (now_text, job_id, claim_token, generation),
                )
                retried += int(cursor.rowcount == 1)
        return RecoverySummary(retried=retried, dead=dead)

    def _dead_claim_in_transaction(
        self,
        db: sqlite3.Connection,
        *,
        job_id: str,
        claim_token: str,
        fencing_generation: int,
        reason: str,
        completed_at: str,
    ) -> bool:
        result = WorldJobResult.dead(reason)
        world_json = _outcome_json(result)
        cursor = db.execute(
            f"""UPDATE {WORLD_JOB_TABLE}
                   SET state = 'dead',
                       terminal_state = 'failed',
                       next_attempt_at = NULL,
                       claim_owner = NULL,
                       claim_token = NULL,
                       lease_expires_at = NULL,
                       world_result_json = ?,
                       result_hash = ?,
                       completed_at = ?,
                       last_error_type = ?
                 WHERE job_id = ?
                   AND state = 'processing'
                   AND claim_token = ?
                   AND fencing_generation = ?""",
            (
                world_json,
                _hash_text(world_json),
                completed_at,
                reason,
                job_id,
                claim_token,
                fencing_generation,
            ),
        )
        if cursor.rowcount != 1:
            return False
        persist_terminal_outcome_in_transaction(db, job_id)
        return True

    def _dead_unclaimed_in_transaction(
        self,
        db: sqlite3.Connection,
        *,
        job_id: str,
        reason: str,
        completed_at: str,
    ) -> bool:
        result = WorldJobResult.dead(reason)
        world_json = _outcome_json(result)
        cursor = db.execute(
            f"""UPDATE {WORLD_JOB_TABLE}
                   SET state = 'dead',
                       terminal_state = 'failed',
                       next_attempt_at = NULL,
                       claim_owner = NULL,
                       claim_token = NULL,
                       lease_expires_at = NULL,
                       world_result_json = ?,
                       result_hash = ?,
                       completed_at = ?,
                       last_error_type = ?
                 WHERE job_id = ?
                   AND state IN ('pending', 'retry')""",
            (world_json, _hash_text(world_json), completed_at, reason, job_id),
        )
        if cursor.rowcount != 1:
            return False
        persist_terminal_outcome_in_transaction(db, job_id)
        return True

    def claim_one(self, claim_owner: str) -> ClaimedWorldJob | None:
        """Recover stale work and atomically claim the oldest due job."""

        if not claim_owner:
            raise ValueError("claim_owner must be non-empty")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            now = self._now()
            now_text = _timestamp(now)
            self._recover_expired_in_transaction(db, now)

            unsafe_rows = db.execute(
                f"""SELECT job_id
                       FROM {WORLD_JOB_TABLE}
                      WHERE state IN ('pending', 'retry')
                        AND model_dispatch_started_at IS NOT NULL
                        AND model_result_json IS NULL"""
            ).fetchall()
            for row in unsafe_rows:
                self._dead_unclaimed_in_transaction(
                    db,
                    job_id=cast(str, row["job_id"]),
                    reason="dispatch_outcome_unknown",
                    completed_at=now_text,
                )

            exhausted = db.execute(
                f"""SELECT job_id
                      FROM {WORLD_JOB_TABLE}
                      WHERE state IN ('pending', 'retry')
                        AND attempts >= ?
                        AND NOT (
                            model_dispatch_started_at IS NOT NULL
                            AND model_result_json IS NOT NULL
                        )""",
                (self.policy.max_attempts,),
            ).fetchall()
            for row in exhausted:
                self._dead_unclaimed_in_transaction(
                    db,
                    job_id=cast(str, row["job_id"]),
                    reason="max_attempts_exhausted",
                    completed_at=now_text,
                )

            row = db.execute(
                f"""SELECT job_id
                      FROM {WORLD_JOB_TABLE}
                      WHERE state IN ('pending', 'retry')
                        AND (
                            attempts < ?
                            OR (
                                model_dispatch_started_at IS NOT NULL
                                AND model_result_json IS NOT NULL
                            )
                        )
                        AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                      ORDER BY COALESCE(next_attempt_at, created_at), created_at, job_id
                      LIMIT 1""",
                (self.policy.max_attempts, now_text),
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return None

            job_id = cast(str, row["job_id"])
            claim_token = uuid4().hex
            lease_text = _timestamp(now + timedelta(seconds=self.policy.lease_seconds))
            cursor = db.execute(
                f"""UPDATE {WORLD_JOB_TABLE}
                       SET state = 'processing',
                           attempts = CASE
                               WHEN model_dispatch_started_at IS NOT NULL
                                AND model_result_json IS NOT NULL
                                AND attempts >= ?
                               THEN attempts
                               ELSE attempts + 1
                           END,
                           next_attempt_at = NULL,
                           claim_owner = ?,
                           claim_token = ?,
                           claimed_at = ?,
                           lease_expires_at = ?,
                           heartbeat_at = ?,
                           fencing_generation = fencing_generation + 1,
                           completed_at = NULL,
                           last_error_type = CASE WHEN last_error_type IN
                             ('restart_recovered', 'shutdown_recovered')
                             THEN last_error_type ELSE NULL END
                     WHERE job_id = ?
                       AND state IN ('pending', 'retry')
                       AND (
                           attempts < ?
                           OR (
                               model_dispatch_started_at IS NOT NULL
                               AND model_result_json IS NOT NULL
                           )
                       )""",
                (
                    self.policy.max_attempts,
                    claim_owner,
                    claim_token,
                    now_text,
                    lease_text,
                    now_text,
                    job_id,
                    self.policy.max_attempts,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("World job claim CAS failed under write reservation")
            claimed_row = db.execute(
                f"SELECT * FROM {WORLD_JOB_TABLE} WHERE job_id = ?", (job_id,)
            ).fetchone()
            if claimed_row is None:
                raise RuntimeError("Claimed World job disappeared")
            claim = self._claim_from_row(claimed_row)
            db.execute("COMMIT")
            return claim
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    def has_durable_model_result(self, claim: ClaimedWorldJob) -> bool:
        """Whether this live claim can replay Apply without a model call."""

        now_text = _timestamp(self._now())
        db = self._connect()
        try:
            row = db.execute(
                f"""SELECT 1 FROM {WORLD_JOB_TABLE}
                       WHERE job_id = ?
                         AND state = 'processing'
                         AND claim_owner = ?
                         AND claim_token = ?
                         AND fencing_generation = ?
                         AND lease_expires_at > ?
                         AND model_dispatch_started_at IS NOT NULL
                         AND model_result_json IS NOT NULL""",
                (
                    claim.job_id,
                    claim.claim_owner,
                    claim.claim_token,
                    claim.fencing_generation,
                    now_text,
                ),
            ).fetchone()
            return row is not None
        finally:
            db.close()

    @staticmethod
    def _claim_from_row(row: sqlite3.Row) -> ClaimedWorldJob:
        return ClaimedWorldJob(
            job_id=cast(str, row["job_id"]),
            boundary_event_id=cast(str, row["boundary_event_id"]),
            boundary_payload_hash=cast(str, row["boundary_payload_hash"]),
            boundary_schema_version=cast(int, row["boundary_schema_version"]),
            provider_name=cast(str, row["provider_name"]),
            parent_session_id=cast(str, row["parent_session_id"]),
            result_session_id=cast(str, row["result_session_id"]),
            boundary_mode=cast(str, row["boundary_mode"]),
            formal_target_json=cast(str, row["formal_target_json"]),
            formal_target_hash=cast(str, row["formal_target_hash"]),
            subject_id=cast(str, row["subject_id"]),
            host_id=cast(str, row["host_id"]),
            evidence_ids_json=cast(str, row["evidence_ids_json"]),
            attempts=cast(int, row["attempts"]),
            claim_owner=cast(str, row["claim_owner"]),
            claim_token=cast(str, row["claim_token"]),
            claimed_at=cast(str, row["claimed_at"]),
            lease_expires_at=cast(str, row["lease_expires_at"]),
            fencing_generation=cast(int, row["fencing_generation"]),
        )

    def validate_current_evidence(
        self,
        claim: ClaimedWorldJob,
        evidence_ids: tuple[str, ...],
        *,
        model_tier: ModelTier = "cloud",
    ) -> None:
        """Fail closed unless every referenced Evidence is current and on-target.

        Identity/provenance columns plus the per-row raw-content hash binding
        (``boundary_evidence_content``, schema v8) are checked.  Raw user or
        assistant content is hashed in memory and never logged.
        """

        if not evidence_ids:
            return
        rows_by_id: dict[str, sqlite3.Row] = {}
        db = self._connect(query_only=True)
        try:
            # Stay below SQLite's common host-parameter limit while preserving
            # the authoritative order from evidence_ids_json in the caller.
            for offset in range(0, len(evidence_ids), 500):
                chunk = evidence_ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = db.execute(
                    "SELECT e.id, e.subject_id, e.host_id, e.source_kind, "
                    "e.deleted_at, e.raw_content, e.allow_local_read, "
                    "e.allow_cloud_read, e.allow_inference, b.raw_content_hash "
                    "FROM evidence e "
                    "LEFT JOIN boundary_evidence_content b "
                    "ON b.evidence_id = e.id "
                    f"WHERE e.id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    rows_by_id[cast(str, row["id"])] = row
        finally:
            db.close()

        for evidence_id in evidence_ids:
            row = rows_by_id.get(evidence_id)
            if row is None:
                raise PermanentWorldJobError("evidence_missing")
            state = evidence_state(
                {
                    "deleted_at": row["deleted_at"],
                    "allow_local_read": row["allow_local_read"],
                    "allow_cloud_read": row["allow_cloud_read"],
                    "allow_inference": row["allow_inference"],
                },
                surface="formation",
                model_tier=model_tier,
            )
            if state is not None:
                raise PermanentWorldJobError(state)
            if (
                row["subject_id"] != claim.subject_id
                or row["host_id"] != claim.host_id
            ):
                raise PermanentWorldJobError("evidence_target_mismatch")
            if row["source_kind"] != "spoken":
                raise PermanentWorldJobError("evidence_source_kind_mismatch")
            bound_hash = row["raw_content_hash"]
            if not isinstance(bound_hash, str) or not bound_hash:
                raise PermanentWorldJobError("evidence_content_hash_missing")
            actual_hash = sha256(str(row["raw_content"]).encode("utf-8")).hexdigest()
            if actual_hash != bound_hash:
                raise PermanentWorldJobError("evidence_content_hash_mismatch")

    def heartbeat(self, claim: ClaimedWorldJob) -> bool:
        """Extend a live lease only for the exact token and generation."""

        now = self._now()
        now_text = _timestamp(now)
        lease_text = _timestamp(now + timedelta(seconds=self.policy.lease_seconds))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                f"""UPDATE {WORLD_JOB_TABLE}
                       SET heartbeat_at = ?, lease_expires_at = ?
                     WHERE job_id = ?
                       AND state = 'processing'
                       AND claim_owner = ?
                       AND claim_token = ?
                       AND fencing_generation = ?
                       AND lease_expires_at > ?""",
                (
                    now_text,
                    lease_text,
                    claim.job_id,
                    claim.claim_owner,
                    claim.claim_token,
                    claim.fencing_generation,
                    now_text,
                ),
            )
            db.execute("COMMIT")
            return cursor.rowcount == 1
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    def _validate_current_evidence_on_connection(
        self,
        db: sqlite3.Connection,
        claim: ClaimedWorldJob,
        evidence_ids: tuple[str, ...],
        *,
        model_tier: ModelTier,
    ) -> None:
        """Repeat the currentness predicate under a caller-owned transaction."""

        if not evidence_ids:
            return
        rows_by_id: dict[str, sqlite3.Row] = {}
        for offset in range(0, len(evidence_ids), 500):
            chunk = evidence_ids[offset : offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = db.execute(
                "SELECT e.id, e.subject_id, e.host_id, e.source_kind, "
                "e.deleted_at, e.raw_content, e.allow_local_read, "
                "e.allow_cloud_read, e.allow_inference, b.raw_content_hash "
                "FROM evidence e LEFT JOIN boundary_evidence_content b "
                "ON b.evidence_id = e.id "
                f"WHERE e.id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                rows_by_id[cast(str, row["id"])] = row
        for evidence_id in evidence_ids:
            row = rows_by_id.get(evidence_id)
            if row is None:
                raise PermanentWorldJobError("evidence_missing")
            state = evidence_state(
                {
                    "deleted_at": row["deleted_at"],
                    "allow_local_read": row["allow_local_read"],
                    "allow_cloud_read": row["allow_cloud_read"],
                    "allow_inference": row["allow_inference"],
                },
                surface="formation",
                model_tier=model_tier,
            )
            if state is not None:
                raise PermanentWorldJobError(state)
            if row["subject_id"] != claim.subject_id or row["host_id"] != claim.host_id:
                raise PermanentWorldJobError("evidence_target_mismatch")
            if row["source_kind"] != "spoken":
                raise PermanentWorldJobError("evidence_source_kind_mismatch")
            bound_hash = row["raw_content_hash"]
            if not isinstance(bound_hash, str) or not bound_hash:
                raise PermanentWorldJobError("evidence_content_hash_missing")
            if sha256(str(row["raw_content"]).encode("utf-8")).hexdigest() != bound_hash:
                raise PermanentWorldJobError("evidence_content_hash_mismatch")

    def mark_dispatch_started(
        self, claim: ClaimedWorldJob, *, model_tier: ModelTier = "cloud"
    ) -> bool:
        """Persist the irreversible single-dispatch marker before model I/O."""

        now_text = _timestamp(self._now())
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._validate_current_evidence_on_connection(
                db, claim, claim.evidence_ids(), model_tier=model_tier
            )
            cursor = db.execute(
                f"""UPDATE {WORLD_JOB_TABLE}
                       SET model_dispatch_started_at = ?,
                           model_task = COALESCE(model_task, 'memory_world')
                     WHERE job_id = ?
                       AND state = 'processing'
                       AND claim_owner = ?
                       AND claim_token = ?
                       AND fencing_generation = ?
                       AND lease_expires_at > ?
                       AND model_dispatch_started_at IS NULL""",
                (
                    now_text,
                    claim.job_id,
                    claim.claim_owner,
                    claim.claim_token,
                    claim.fencing_generation,
                    now_text,
                ),
            )
            db.execute("COMMIT")
            return cursor.rowcount == 1
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    def settle(
        self,
        claim: ClaimedWorldJob,
        result: WorldJobResult,
        *,
        model_completed: bool = False,
    ) -> bool:
        """ACK/NACK a claim with token+generation fencing.

        A retry after a dispatch marker is forbidden.  If the caller cannot
        prove that the model returned, a marked claim is instead terminalized
        as ``dispatch_outcome_unknown``.
        """

        if not isinstance(result, WorldJobResult):
            raise TypeError("processor must return WorldJobResult")
        now_text = _timestamp(self._now())
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"""SELECT attempts, model_dispatch_started_at
                       FROM {WORLD_JOB_TABLE}
                      WHERE job_id = ?
                        AND state = 'processing'
                        AND claim_owner = ?
                        AND claim_token = ?
                        AND fencing_generation = ?
                        AND lease_expires_at > ?""",
                (
                    claim.job_id,
                    claim.claim_owner,
                    claim.claim_token,
                    claim.fencing_generation,
                    now_text,
                ),
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return False
            if result.state == "applied":
                # Only the batch adapter owns the transaction that can bind a
                # World mutation, revision, Job terminal, and applied outcome.
                # A generic settlement still holding this claim cannot forge it.
                raise PermanentWorldJobError(
                    "applied_requires_atomic_world_mutation"
                )

            attempts = cast(int, row["attempts"])
            dispatch_started = row["model_dispatch_started_at"] is not None
            effective = result
            error_type: str | None = None
            if dispatch_started and not model_completed:
                effective = WorldJobResult.dead("dispatch_outcome_unknown")
                error_type = effective.reason
            elif dispatch_started and result.state == "retry":
                effective = WorldJobResult.dead("post_dispatch_retry_forbidden")
                error_type = effective.reason
            elif result.state in {"retry", "dead"}:
                error_type = result.reason

            if model_completed and not dispatch_started:
                raise ValueError("model completion cannot precede the dispatch marker")

            terminal = effective.state != "retry"
            next_attempt_at: str | None = None
            completed_at: str | None = now_text if terminal else None
            if effective.state == "retry":
                if attempts >= self.policy.max_attempts:
                    effective = WorldJobResult.dead("max_attempts_exhausted")
                    error_type = effective.reason
                    terminal = True
                    completed_at = now_text
                else:
                    next_attempt_at = _timestamp(
                        self._now()
                        + timedelta(seconds=self.policy.retry_delay(attempts))
                    )
            # The transport ``state`` column stays the closed six-value machine
            # (CHECK constraint); clarification_required / out_of_scope settle as
            # transport no_change and carry their AUTHORITY §3 terminal in
            # ``terminal_state``.  Computed AFTER the max-attempts upgrade so a
            # retried job that exhausts its budget settles as dead, not retry.
            transport_state = (
                effective.state
                if effective.state in ("applied", "no_change", "retry", "dead")
                else "no_change"
            )

            world_json = _outcome_json(effective)
            model_result_json: str | None = None
            model_result_hash: str | None = None
            model_usage_json: str | None = None
            if model_completed:
                model_payload: object = (
                    dict(result.model_result)
                    if result.model_result is not None
                    else {
                        "schema_version": 1,
                        "state": result.state,
                        "reason": result.reason,
                    }
                )
                model_result_json = _canonical_json(model_payload)
                model_result_hash = _hash_text(model_result_json)
                if result.model_usage is not None:
                    model_usage_json = _canonical_json(dict(result.model_usage))

            persisted_world_json = world_json if terminal else None
            persisted_result_hash = _hash_text(world_json) if terminal else None

            cursor = db.execute(
                f"""UPDATE {WORLD_JOB_TABLE}
                       SET state = ?,
                           next_attempt_at = ?,
                           claim_owner = NULL,
                           claim_token = NULL,
                           lease_expires_at = NULL,
                           model_completed_at = CASE WHEN ? THEN ? ELSE model_completed_at END,
                           model_provider = CASE WHEN ? THEN ? ELSE model_provider END,
                           model_name = CASE WHEN ? THEN ? ELSE model_name END,
                           model_usage_json = CASE WHEN ? THEN ? ELSE model_usage_json END,
                           model_result_json = CASE WHEN ? THEN ? ELSE model_result_json END,
                           model_result_hash = CASE WHEN ? THEN ? ELSE model_result_hash END,
                           world_result_json = ?,
                           result_hash = ?,
                           completed_at = ?,
                           last_error_type = ?,
                           terminal_state = ?,
                           terminal_detail = ?
                     WHERE job_id = ?
                       AND state = 'processing'
                       AND claim_owner = ?
                       AND claim_token = ?
                       AND fencing_generation = ?
                       AND lease_expires_at > ?""",
                (
                    transport_state,
                    next_attempt_at,
                    int(model_completed),
                    now_text,
                    int(model_completed),
                    result.model_provider,
                    int(model_completed),
                    result.model_name,
                    int(model_completed),
                    model_usage_json,
                    int(model_completed),
                    model_result_json,
                    int(model_completed),
                    model_result_hash,
                    persisted_world_json,
                    persisted_result_hash,
                    completed_at,
                    error_type,
                    TERMINAL_BY_STATE.get(effective.state) if terminal else None,
                    result.display if terminal else None,
                    claim.job_id,
                    claim.claim_owner,
                    claim.claim_token,
                    claim.fencing_generation,
                    now_text,
                ),
            )
            if terminal and cursor.rowcount == 1:
                # BatchAdapter applies in its own atomic transaction, so its
                # outer worker settlement matches no processing row. Every
                # remaining generic terminal is zero-mutation and writes its
                # outcome only after the fenced Job terminal succeeds.
                persist_terminal_outcome_in_transaction(db, claim.job_id)
            db.execute("COMMIT")
            return cursor.rowcount == 1
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    def next_wakeup_delay(self) -> float | None:
        """Return seconds until pending/retry work or a lease can be recovered."""

        db = self._connect()
        try:
            row = db.execute(
                f"""SELECT MIN(due_at) AS due_at
                       FROM (
                         SELECT COALESCE(next_attempt_at, created_at) AS due_at
                           FROM {WORLD_JOB_TABLE}
                          WHERE state IN ('pending', 'retry')
                         UNION ALL
                         SELECT lease_expires_at AS due_at
                           FROM {WORLD_JOB_TABLE}
                          WHERE state = 'processing'
                       )
                      WHERE due_at IS NOT NULL"""
            ).fetchone()
            if row is None or row["due_at"] is None:
                return None
            due = _parse_timestamp(cast(str, row["due_at"]))
            return max(0.0, (due - self._now()).total_seconds())
        finally:
            db.close()


class WorldJobWorker:
    """Run-to-quiescence daemon worker owned by one MemoWeft provider runtime."""

    def __init__(
        self,
        db_path: Path,
        *,
        processor: WorldJobProcessor | None = None,
        policy: WorldJobPolicy | None = None,
        clock: Clock = _utc_now,
        worker_id: str | None = None,
        retry_interrupted_inference: bool = False,
    ) -> None:
        self.policy = policy or WorldJobPolicy()
        self.store = WorldJobStore(db_path, policy=self.policy, clock=clock)
        self.processor = processor or FormalBatchAdapterUnavailableProcessor()
        self.worker_id = worker_id or f"{PREFIX}{uuid4().hex}"
        self._lifetime = acquire_lifetime(Path(db_path), self.worker_id) if worker_id is None else None
        self._retry_interrupted_inference = retry_interrupted_inference
        self._recovered = False
        self._lifecycle_lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._timer: threading.Timer | None = None

    def start(self) -> bool:
        """Start recovery/drain work without blocking the caller."""

        return self.kick()

    def kick(self, delay: float = 0.0) -> bool:
        """Wake or lazily start the run-to-quiescence worker."""

        with self._lifecycle_lock:
            if self._stop.is_set():
                return False
            if delay > 0.0:
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None
                self._schedule_locked(delay)
                return True
            self._wake.set()
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._thread is not None and self._thread.is_alive():
                return True
            thread = threading.Thread(
                target=self._run_background,
                name="memoweft-world-worker",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return True

    def _run_background(self) -> None:
        failed = False
        try:
            while not self._stop.is_set():
                self._wake.clear()
                self.run_until_quiescent()
                with self._lifecycle_lock:
                    if self._wake.is_set() and not self._stop.is_set():
                        continue
                    break
        except Exception as exc:
            failed = True
            logger.warning(
                "MemoWeft World worker drain failed: error_type=%s",
                type(exc).__name__,
            )
        finally:
            with self._lifecycle_lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                if self._stop.is_set():
                    self._release_lifetime()
                if not self._stop.is_set() and self._wake.is_set():
                    self._schedule_locked(0.0)
                elif not self._stop.is_set():
                    try:
                        delay = (
                            WORLD_JOB_LOOP_ERROR_RETRY_SECONDS
                            if failed
                            else self.store.next_wakeup_delay()
                        )
                    except Exception as exc:
                        logger.warning(
                            "MemoWeft World wakeup lookup failed: error_type=%s",
                            type(exc).__name__,
                        )
                        delay = WORLD_JOB_LOOP_ERROR_RETRY_SECONDS
                    if delay is not None:
                        self._schedule_locked(delay)

    def _schedule_locked(self, delay: float) -> None:
        if self._stop.is_set() or self._timer is not None:
            return
        timer = threading.Timer(delay, self._timer_fired)
        timer.name = "memoweft-world-wakeup"
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _timer_fired(self) -> None:
        with self._lifecycle_lock:
            self._timer = None
        self.kick()

    def run_until_quiescent(self, *, max_jobs: int | None = None) -> int:
        """Synchronously process all currently due jobs; primarily testable API."""

        if max_jobs is not None and max_jobs < 1:
            raise ValueError("max_jobs must be positive when supplied")
        processed = 0
        while not self._stop.is_set() and (max_jobs is None or processed < max_jobs):
            with self._lifecycle_lock:
                if self._stop.is_set():
                    break
                if not self._recovered:
                    self.store.recover_interrupted(retry_inference=self._retry_interrupted_inference)
                    self._recovered = True
                claim = self.store.claim_one(self.worker_id)
            if claim is None:
                break
            self._process_claim(claim)
            processed += 1
        return processed

    def _process_claim(self, claim: ClaimedWorldJob) -> None:
        try:
            claim.validate_formal_target()
            evidence_ids = claim.evidence_ids()
            configured_model_tier = getattr(self.processor, "model_tier", "cloud")
            if configured_model_tier not in ("cloud", "local"):
                raise PermanentWorldJobError("invalid_model_tier")
            model_tier = cast(ModelTier, configured_model_tier)
            self.store.validate_current_evidence(
                claim, evidence_ids, model_tier=model_tier
            )
        except PermanentWorldJobError as exc:
            self.store.settle(
                claim,
                WorldJobResult.dead(
                    _declared_exception_code(exc, "invalid_evidence_batch")
                ),
            )
            return
        if not evidence_ids:
            self.store.settle(
                claim,
                WorldJobResult.no_change("no_eligible_user_evidence"),
            )
            return

        # Reassert the live fence after the read-only currentness check.  A
        # worker whose lease expired during that check must not enter either a
        # deterministic processor or model dispatch.
        if not self.store.heartbeat(claim):
            return

        try:
            dispatches_model = bool(self.processor.dispatches_model)
        except Exception as exc:
            self.store.settle(claim, WorldJobResult.retry(_exception_code(exc)))
            return

        model_completed = False
        replaying_durable_model_result = False
        if dispatches_model:
            if self.store.has_durable_model_result(claim):
                # A prior worker checkpointed the remote response but lost its
                # lease before Apply.  The processor must load that immutable
                # checkpoint and replay deterministic Apply, never dispatch.
                model_completed = True
                replaying_durable_model_result = True
            else:
                # The earlier validation happens before the processor capability
                # lookup. Recheck immediately before persisting the irreversible
                # dispatch marker so a just-revoked Evidence cannot start a route.
                try:
                    self.store.validate_current_evidence(
                        claim, evidence_ids, model_tier=model_tier
                    )
                except PermanentWorldJobError as exc:
                    self.store.settle(
                        claim,
                        WorldJobResult.dead(
                            _declared_exception_code(exc, "invalid_evidence_batch")
                        ),
                    )
                    return
                try:
                    dispatch_started = self.store.mark_dispatch_started(
                        claim, model_tier=model_tier
                    )
                except PermanentWorldJobError as exc:
                    self.store.settle(
                        claim,
                        WorldJobResult.dead(
                            _declared_exception_code(exc, "invalid_evidence_batch")
                        ),
                    )
                    return
                if not dispatch_started:
                    return
                model_completed = True

        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(claim, heartbeat_stop),
            name="memoweft-world-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = self.processor.process(claim)
            if not isinstance(result, WorldJobResult):
                raise PermanentWorldJobError("invalid_processor_result")
            self.store.settle(
                claim,
                result,
                model_completed=model_completed,
            )
        except PermanentWorldJobError as exc:
            result = WorldJobResult.dead(
                _declared_exception_code(exc, "permanent_processor_error")
            )
            logger.warning(
                "MemoWeft World processor failed permanently: error_type=%s",
                result.reason,
            )
            self.store.settle(
                claim,
                result,
                model_completed=model_completed,
            )
        except RetryableWorldJobError as exc:
            result = WorldJobResult.retry(
                _declared_exception_code(exc, "retryable_processor_error")
            )
            logger.warning(
                "MemoWeft World processor failed retryably: error_type=%s",
                result.reason,
            )
            self.store.settle(
                claim,
                result,
                model_completed=replaying_durable_model_result,
            )
        except Exception as exc:
            result = WorldJobResult.retry(_exception_code(exc))
            logger.warning(
                "MemoWeft World processor raised: error_type=%s class=%s",
                result.reason,
                type(exc).__name__,
                exc_info=True,
            )
            self.store.settle(
                claim,
                result,
                model_completed=replaying_durable_model_result,
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=min(1.0, self.policy.heartbeat_seconds))

    def _heartbeat_loop(
        self, claim: ClaimedWorldJob, stop: threading.Event
    ) -> None:
        while not stop.wait(self.policy.heartbeat_seconds):
            try:
                if not self.store.heartbeat(claim):
                    return
            except Exception as exc:
                logger.warning(
                    "MemoWeft World heartbeat failed: error_type=%s",
                    type(exc).__name__,
                )
                return

    def shutdown(self, *, timeout: float = 5.0) -> bool:
        """Stop timers and wait at most ``timeout`` seconds for the worker."""

        if timeout < 0:
            raise ValueError("shutdown timeout cannot be negative")
        self._stop.set()
        self._wake.set()
        with self._lifecycle_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            thread = self._thread
            # Revoke before waiting for a slow network call. A late checkpoint,
            # Apply or heartbeat from that thread cannot pass the old fence.
            try:
                self.store.recover_interrupted(owner=self.worker_id,
                                              retry_inference=self._retry_interrupted_inference,
                                              busy_timeout_ms=200)
            except sqlite3.OperationalError:
                # A contended writer must not stretch host shutdown. Keep the
                # lifetime held until this thread (or the process) really exits.
                logger.warning("MemoWeft World shutdown recovery deferred: database busy")
            else:
                self._release_lifetime()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return thread is None or not thread.is_alive()

    def _release_lifetime(self) -> None:
        if self._lifetime is not None:
            name = self._lifetime.name
            self._lifetime.close()
            self._lifetime = None
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass  # An unlocked leftover still unambiguously means gone.

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()


__all__ = [
    "ClaimedWorldJob",
    "FormalBatchAdapterUnavailableProcessor",
    "PermanentWorldJobError",
    "RecoverySummary",
    "RetryableWorldJobError",
    "WORLD_JOB_HEARTBEAT_SECONDS",
    "WORLD_JOB_LEASE_SECONDS",
    "WORLD_JOB_MAX_ATTEMPTS",
    "WORLD_JOB_RETRY_BACKOFF_SECONDS",
    "WorldJobPolicy",
    "WorldJobProcessor",
    "WorldJobResult",
    "WorldJobStore",
    "WorldJobWorker",
]
