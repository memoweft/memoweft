"""Core-owned durable five-terminal outcome and fenced host-delivery ledger.

The module has two deliberately separate entry points.  Terminal formation
calls :func:`persist_terminal_outcome_in_transaction` on its already-open
``BEGIN IMMEDIATE`` connection, which keeps the Job terminal and outcome
atomic.  Hermes consumers use :class:`TerminalOutcomeStore`, whose short
transactions operate only on the independent delivery state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping, TypedDict, cast
from uuid import uuid4

from ...clock import to_iso_z
from ...store.driver import BUSY_TIMEOUT_MS


TERMINAL_OUTCOME_TABLE = "terminal_outcome"
TERMINAL_OUTCOME_SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset(
    {"applied", "no_change", "clarification_required", "out_of_scope", "failed"}
)
DELIVERY_LEASE_SECONDS = 300.0
DELIVERY_MAX_ATTEMPTS = 4
DELIVERY_RETRY_BACKOFF_SECONDS = (5.0, 30.0, 300.0)
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class TerminalOutcomeV1(TypedDict):
    """Canonical outcome envelope plus independently durable delivery fields."""

    schema_version: int
    outcome_id: str
    job_id: str
    boundary_event_id: str
    provider_name: str
    subject_id: str
    parent_session_id: str
    result_session_id: str
    terminal_state: str
    terminal_detail: str | None
    world_revision: int
    world_result: dict[str, object]
    result_hash: str
    occurred_at: str
    delivery_state: str
    attempts: int
    next_attempt_at: str | None
    claim_owner: str | None
    claim_token: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    delivered_at: str | None
    last_error: str | None


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
        raise ValueError("terminal outcome payload is not canonical JSON") from exc


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _now_text() -> str:
    return to_iso_z(datetime.now(timezone.utc))


def _require_nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"terminal job has invalid {field}")
    return value


def _decode_canonical_mapping(raw: object, field: str) -> dict[str, object]:
    text = _require_nonempty_text(raw, field)
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"terminal job has invalid {field}") from exc
    if not isinstance(decoded, dict) or _canonical_json(decoded) != text:
        raise ValueError(f"terminal job has noncanonical {field}")
    return cast(dict[str, object], decoded)


def _stable_failed_detail(row: Mapping[str, object], world_result: Mapping[str, object]) -> str:
    """Never place arbitrary exception/user text in the failed terminal detail."""

    for candidate in (row.get("last_error_type"), world_result.get("reason")):
        if isinstance(candidate, str) and _ERROR_CODE.fullmatch(candidate):
            return candidate
    return "terminal_failed"


def _validated_terminal_detail(terminal_state: str, value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError("terminal job has invalid terminal_detail")
    detail: str | None = value
    if terminal_state in {"clarification_required", "out_of_scope"}:
        if detail is None or not detail.strip():
            raise ValueError(f"{terminal_state} terminal job requires terminal_detail")
        if len(detail) > 500:
            raise ValueError("terminal job terminal_detail exceeds 500 characters")
    return detail


def _result_payload(outcome: Mapping[str, object]) -> dict[str, object]:
    """The immutable source for result_hash; delivery fields never participate."""

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


def _derive_outcome_id(job_id: str, result_hash: str) -> str:
    domain = _canonical_json(
        {
            "domain": "memoweft_terminal_outcome_v1",
            "job_id": job_id,
            "result_hash": result_hash,
            "schema_version": TERMINAL_OUTCOME_SCHEMA_VERSION,
        }
    )
    return f"terminal-outcome:v1:{_hash(domain)}"


def _job_outcome_in_transaction(
    db: sqlite3.Connection, job_id: str, *, frozen_world_revision: int | None = None
) -> TerminalOutcomeV1:
    row = db.execute(
        """SELECT job_id, boundary_event_id, provider_name, subject_id,
                  parent_session_id, result_session_id, terminal_state,
                  terminal_detail, world_result_json, result_hash, completed_at,
                  last_error_type
             FROM memory_world_job WHERE job_id = ?""",
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError("terminal job does not exist")
    names = (
        "job_id",
        "boundary_event_id",
        "provider_name",
        "subject_id",
        "parent_session_id",
        "result_session_id",
        "terminal_state",
        "terminal_detail",
        "world_result_json",
        "result_hash",
        "completed_at",
        "last_error_type",
    )
    source = dict(zip(names, row, strict=True))
    terminal_state = _require_nonempty_text(source["terminal_state"], "terminal_state")
    if terminal_state not in TERMINAL_STATES:
        raise ValueError("terminal job has no valid terminal_state")
    world_json = _require_nonempty_text(source["world_result_json"], "world_result_json")
    job_result_hash = _require_nonempty_text(source["result_hash"], "result_hash")
    if _hash(world_json) != job_result_hash:
        raise ValueError("terminal job result_hash does not bind world_result_json")
    world_result = _decode_canonical_mapping(world_json, "world_result_json")
    if terminal_state == "applied":
        revision = world_result.get("world_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("applied terminal job lacks world_result world_revision")
        world_revision = revision
    elif frozen_world_revision is not None:
        if frozen_world_revision < 0:
            raise ValueError("persisted terminal outcome has invalid world_revision")
        world_revision = frozen_world_revision
    else:
        revision_row = db.execute(
            "SELECT revision FROM memory_state WHERE singleton = 1"
        ).fetchone()
        world_revision = 0 if revision_row is None else int(revision_row[0])
        if world_revision < 0:
            raise ValueError("terminal job has invalid current world revision")
    if terminal_state == "failed":
        terminal_detail: str | None = _stable_failed_detail(source, world_result)
    else:
        terminal_detail = _validated_terminal_detail(
            terminal_state, source["terminal_detail"]
        )
    outcome: TerminalOutcomeV1 = {
        "schema_version": TERMINAL_OUTCOME_SCHEMA_VERSION,
        "outcome_id": "",
        "job_id": _require_nonempty_text(source["job_id"], "job_id"),
        "boundary_event_id": _require_nonempty_text(source["boundary_event_id"], "boundary_event_id"),
        "provider_name": _require_nonempty_text(source["provider_name"], "provider_name"),
        "subject_id": _require_nonempty_text(source["subject_id"], "subject_id"),
        "parent_session_id": _require_nonempty_text(source["parent_session_id"], "parent_session_id"),
        "result_session_id": _require_nonempty_text(source["result_session_id"], "result_session_id"),
        "terminal_state": terminal_state,
        "terminal_detail": terminal_detail,
        "world_revision": world_revision,
        "world_result": world_result,
        "result_hash": "",
        "occurred_at": _require_nonempty_text(source["completed_at"], "completed_at"),
        "delivery_state": "pending",
        "attempts": 0,
        "next_attempt_at": None,
        "claim_owner": None,
        "claim_token": None,
        "lease_expires_at": None,
        "heartbeat_at": None,
        "delivered_at": None,
        "last_error": None,
    }
    result_hash = _hash(_canonical_json(_result_payload(outcome)))
    outcome["result_hash"] = result_hash
    outcome["outcome_id"] = _derive_outcome_id(outcome["job_id"], result_hash)
    return outcome


def persist_terminal_outcome_in_transaction(
    db: sqlite3.Connection, job_id: str
) -> TerminalOutcomeV1:
    """Persist/replay one terminal outcome without opening a second connection.

    The caller owns the transaction so it can invoke this immediately beside a
    terminal ``memory_world_job`` update.  Existing rows are revalidated and
    must exactly match the immutable Job-derived outcome.
    """

    if not db.in_transaction:
        raise ValueError("terminal outcome persistence requires a caller-owned transaction")
    existing = db.execute(
        """SELECT schema_version, outcome_id, job_id, boundary_event_id,
                  provider_name, subject_id, parent_session_id, result_session_id,
                  terminal_state, terminal_detail, world_revision,
                  world_result_json AS world_result, result_hash, occurred_at,
                  delivery_state, attempts, next_attempt_at, claim_owner,
                  claim_token, lease_expires_at, heartbeat_at, delivered_at,
                  last_error
             FROM terminal_outcome WHERE job_id = ?""",
        (job_id,),
    ).fetchone()
    if existing is not None:
        persisted = _decode_outcome_row(existing)
        expected = _job_outcome_in_transaction(
            db, job_id, frozen_world_revision=persisted["world_revision"]
        )
        for field in (
            "schema_version", "outcome_id", "job_id", "boundary_event_id",
            "provider_name", "subject_id", "parent_session_id", "result_session_id",
            "terminal_state", "terminal_detail", "world_revision", "world_result",
            "result_hash", "occurred_at",
        ):
            if persisted[field] != expected[field]:
                raise ValueError("persisted terminal outcome does not match terminal job")
        from ..trust.clarification_service import (
            synchronize_clarification_in_transaction,
        )

        synchronize_clarification_in_transaction(db, persisted)
        return persisted
    expected = _job_outcome_in_transaction(db, job_id)
    db.execute(
        """INSERT INTO terminal_outcome (
              outcome_id, schema_version, job_id, boundary_event_id, provider_name,
              subject_id, parent_session_id, result_session_id, terminal_state,
              terminal_detail, world_revision, world_result_json, result_hash,
              occurred_at, delivery_state, attempts, next_attempt_at, claim_owner,
              claim_token, lease_expires_at, heartbeat_at, delivered_at, last_error
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0,
                     NULL, NULL, NULL, NULL, NULL, NULL, NULL)""",
        (
            expected["outcome_id"], expected["schema_version"], expected["job_id"],
            expected["boundary_event_id"], expected["provider_name"], expected["subject_id"],
            expected["parent_session_id"], expected["result_session_id"],
            expected["terminal_state"], expected["terminal_detail"], expected["world_revision"],
            _canonical_json(expected["world_result"]), expected["result_hash"],
            expected["occurred_at"],
        ),
    )
    from ..trust.clarification_service import synchronize_clarification_in_transaction

    synchronize_clarification_in_transaction(db, expected)
    return expected


def _decode_outcome_row(row: sqlite3.Row | tuple[object, ...]) -> TerminalOutcomeV1:
    names = (
        "schema_version", "outcome_id", "job_id", "boundary_event_id",
        "provider_name", "subject_id", "parent_session_id", "result_session_id",
        "terminal_state", "terminal_detail", "world_revision", "world_result",
        "result_hash", "occurred_at", "delivery_state", "attempts",
        "next_attempt_at", "claim_owner", "claim_token", "lease_expires_at",
        "heartbeat_at", "delivered_at", "last_error",
    )
    raw = dict(zip(names, tuple(row), strict=True))
    world_result = _decode_canonical_mapping(raw["world_result"], "world_result_json")
    outcome = cast(TerminalOutcomeV1, raw)
    outcome["world_result"] = world_result
    if outcome["schema_version"] != TERMINAL_OUTCOME_SCHEMA_VERSION:
        raise ValueError("terminal outcome schema_version is invalid")
    if outcome["terminal_state"] not in TERMINAL_STATES:
        raise ValueError("terminal outcome terminal_state is invalid")
    _validated_terminal_detail(
        outcome["terminal_state"], outcome["terminal_detail"]
    )
    if outcome["delivery_state"] not in {
        "pending", "processing", "delivered", "retry", "dead"
    }:
        raise ValueError("terminal outcome delivery_state is invalid")
    if _hash(_canonical_json(_result_payload(outcome))) != outcome["result_hash"]:
        raise ValueError("terminal outcome result_hash does not match canonical payload")
    if _derive_outcome_id(outcome["job_id"], outcome["result_hash"]) != outcome["outcome_id"]:
        raise ValueError("terminal outcome outcome_id does not match canonical payload")
    return outcome


class TerminalOutcomeStore:
    """Core-controlled, token-fenced delivery state for durable outcomes."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _connect(self, *, query_only: bool = False) -> sqlite3.Connection:
        db = sqlite3.connect(
            str(self.db_path), timeout=BUSY_TIMEOUT_MS / 1000.0, isolation_level=None
        )
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        if query_only:
            db.execute("PRAGMA query_only = ON")
        return db

    @staticmethod
    def _rollback(db: sqlite3.Connection) -> None:
        if db.in_transaction:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass

    def get_terminal_outcome(self, job_id: str) -> TerminalOutcomeV1:
        db = self._connect(query_only=True)
        try:
            row = db.execute(
                """SELECT schema_version, outcome_id, job_id, boundary_event_id,
                          provider_name, subject_id, parent_session_id, result_session_id,
                          terminal_state, terminal_detail, world_revision,
                          world_result_json AS world_result, result_hash, occurred_at,
                          delivery_state, attempts, next_attempt_at, claim_owner,
                          claim_token, lease_expires_at, heartbeat_at, delivered_at,
                          last_error FROM terminal_outcome WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError("terminal outcome does not exist")
            return _decode_outcome_row(row)
        finally:
            db.close()

    def recover_stale_terminal_outcomes(self) -> int:
        """Release expired delivery claims; Core chooses ready/dead transition."""

        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            now = _now_text()
            rows = db.execute(
                """SELECT outcome_id, attempts FROM terminal_outcome
                     WHERE delivery_state = 'processing'
                       AND lease_expires_at <= ? ORDER BY occurred_at, outcome_id""",
                (now,),
            ).fetchall()
            recovered = 0
            for row in rows:
                state = "dead" if int(row["attempts"]) >= DELIVERY_MAX_ATTEMPTS else "retry"
                cursor = db.execute(
                    """UPDATE terminal_outcome
                           SET delivery_state = ?, next_attempt_at = CASE WHEN ? = 'retry' THEN ? ELSE NULL END,
                               claim_owner = NULL, claim_token = NULL, lease_expires_at = NULL,
                               heartbeat_at = NULL, last_error = 'delivery_lease_expired'
                         WHERE outcome_id = ? AND delivery_state = 'processing'
                           AND lease_expires_at <= ?""",
                    (state, state, now, row["outcome_id"], now),
                )
                recovered += cursor.rowcount
            db.execute("COMMIT")
            return recovered
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    def claim_terminal_outcomes(
        self, claim_owner: str, *, limit: int = 8
    ) -> list[TerminalOutcomeV1]:
        claim_owner = claim_owner.strip()
        if not claim_owner or len(claim_owner) > 255:
            raise ValueError("claim_owner must be a non-empty string up to 255 characters")
        if limit < 1 or limit > 128:
            raise ValueError("limit must be between 1 and 128")
        self.recover_stale_terminal_outcomes()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            now = _now_text()
            candidate_rows = db.execute(
                """SELECT outcome_id FROM terminal_outcome
                     WHERE delivery_state IN ('pending', 'retry')
                       AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                     ORDER BY COALESCE(next_attempt_at, occurred_at), occurred_at, outcome_id
                     LIMIT ?""",
                (now, limit),
            ).fetchall()
            claimed_ids: list[str] = []
            lease = to_iso_z(
                datetime.now(timezone.utc) + timedelta(seconds=DELIVERY_LEASE_SECONDS)
            )
            for row in candidate_rows:
                outcome_id = str(row["outcome_id"])
                token = uuid4().hex
                cursor = db.execute(
                    """UPDATE terminal_outcome
                           SET delivery_state = 'processing', attempts = attempts + 1,
                               next_attempt_at = NULL, claim_owner = ?, claim_token = ?,
                               lease_expires_at = ?, heartbeat_at = ?, last_error = NULL
                         WHERE outcome_id = ? AND delivery_state IN ('pending', 'retry')
                           AND (next_attempt_at IS NULL OR next_attempt_at <= ?)""",
                    (claim_owner, token, lease, now, outcome_id, now),
                )
                if cursor.rowcount == 1:
                    claimed_ids.append(outcome_id)
            outcomes: list[TerminalOutcomeV1] = []
            for outcome_id in claimed_ids:
                row = db.execute(
                    """SELECT schema_version, outcome_id, job_id, boundary_event_id,
                              provider_name, subject_id, parent_session_id, result_session_id,
                              terminal_state, terminal_detail, world_revision,
                              world_result_json AS world_result, result_hash, occurred_at,
                              delivery_state, attempts, next_attempt_at, claim_owner,
                              claim_token, lease_expires_at, heartbeat_at, delivered_at,
                              last_error FROM terminal_outcome WHERE outcome_id = ?""",
                    (outcome_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("claimed terminal outcome disappeared")
                outcomes.append(_decode_outcome_row(row))
            db.execute("COMMIT")
            return outcomes
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    def heartbeat_terminal_outcome(self, outcome_id: str, claim_token: str) -> bool:
        now = _now_text()
        lease = to_iso_z(datetime.now(timezone.utc) + timedelta(seconds=DELIVERY_LEASE_SECONDS))
        return self._fenced_update(
            """UPDATE terminal_outcome SET heartbeat_at = ?, lease_expires_at = ?
                 WHERE outcome_id = ? AND delivery_state = 'processing'
                   AND claim_token = ? AND lease_expires_at > ?""",
            (now, lease, outcome_id, claim_token, now),
        )

    def ack_terminal_outcome(self, outcome_id: str, claim_token: str) -> bool:
        now = _now_text()
        return self._fenced_update(
            """UPDATE terminal_outcome SET delivery_state = 'delivered',
                       next_attempt_at = NULL, claim_owner = NULL, claim_token = NULL,
                       lease_expires_at = NULL, heartbeat_at = NULL, delivered_at = ?,
                       last_error = NULL
                 WHERE outcome_id = ? AND delivery_state = 'processing'
                   AND claim_token = ? AND lease_expires_at > ?""",
            (now, outcome_id, claim_token, now),
        )

    def nack_terminal_outcome(
        self, outcome_id: str, claim_token: str, *, error_type: str
    ) -> bool:
        if not _ERROR_CODE.fullmatch(error_type):
            raise ValueError("error_type must be a stable machine code")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            now = _now_text()
            row = db.execute(
                """SELECT attempts FROM terminal_outcome WHERE outcome_id = ?
                     AND delivery_state = 'processing' AND claim_token = ?
                     AND lease_expires_at > ?""",
                (outcome_id, claim_token, now),
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return False
            attempts = int(row["attempts"])
            state = "dead" if attempts >= DELIVERY_MAX_ATTEMPTS else "retry"
            next_attempt = (
                None
                if state == "dead"
                else to_iso_z(
                    datetime.now(timezone.utc)
                    + timedelta(seconds=DELIVERY_RETRY_BACKOFF_SECONDS[attempts - 1])
                )
            )
            cursor = db.execute(
                """UPDATE terminal_outcome SET delivery_state = ?, next_attempt_at = ?,
                           claim_owner = NULL, claim_token = NULL, lease_expires_at = NULL,
                           heartbeat_at = NULL, last_error = ?
                     WHERE outcome_id = ? AND delivery_state = 'processing'
                       AND claim_token = ? AND lease_expires_at > ?""",
                (state, next_attempt, error_type, outcome_id, claim_token, now),
            )
            db.execute("COMMIT")
            return cursor.rowcount == 1
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    def _fenced_update(self, sql: str, params: tuple[object, ...]) -> bool:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(sql, params)
            db.execute("COMMIT")
            return cursor.rowcount == 1
        except BaseException:
            self._rollback(db)
            raise
        finally:
            db.close()

    # Compact runtime/provider aliases.  Tokens are durable fencing authority;
    # a restarted host must not need to reconstruct a claim owner to ACK/NACK.
    claim = claim_terminal_outcomes
    heartbeat = heartbeat_terminal_outcome
    ack = ack_terminal_outcome
    nack = nack_terminal_outcome
