"""Durable clarification request, answer Evidence, and follow-up Job service.

The terminal-outcome transaction calls :func:`synchronize_clarification_in_transaction`.
The public service answers one open record by reusing the normal Hermes boundary
compiler inside the same caller-owned SQLite transaction.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Literal, Mapping, TypedDict, cast

from ...clock import Clock, system_clock, to_iso_z
from ...store import open_db


CLARIFICATION_SCHEMA_VERSION = 1
ClarificationState = Literal["open", "answered", "resolved"]


class ClarificationRecordV1(TypedDict):
    schema_version: int
    clarification_id: str
    source_job_id: str
    source_outcome_id: str
    subject_id: str
    result_session_id: str
    question: str
    target_hint: str | None
    state: ClarificationState
    answer_evidence_id: str | None
    follow_up_job_id: str | None
    opened_at: str
    answered_at: str | None
    resolved_at: str | None


class ClarificationAnswerReceiptV1(TypedDict):
    schema_version: int
    clarification_id: str
    state: str
    result_session_id: str
    answer_evidence_id: str
    follow_up_job_id: str
    replayed: bool


class ClarificationError(ValueError):
    """Stable fail-closed error for an invalid clarification operation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
        raise ClarificationError("clarification_noncanonical_value") from exc


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def derive_clarification_id(source_job_id: str, source_outcome_id: str) -> str:
    """Derive the cross-host clarification identity from immutable Core facts."""

    if not isinstance(source_job_id, str) or not source_job_id:
        raise ClarificationError("clarification_source_job_required")
    if not isinstance(source_outcome_id, str) or not source_outcome_id:
        raise ClarificationError("clarification_source_outcome_required")
    identity = {
        "domain": "memoweft_clarification_v1",
        "source_job_id": source_job_id,
        "source_outcome_id": source_outcome_id,
    }
    return "clarification:v1:" + _hash_text(_canonical_json(identity))


def _row_record(
    row: sqlite3.Row | Mapping[str, object],
) -> ClarificationRecordV1:
    state = row["state"]
    if state not in {"open", "answered", "resolved"}:
        raise ClarificationError("clarification_state_invalid")
    question = row["question"]
    if not isinstance(question, str) or not question.strip() or len(question) > 500:
        raise ClarificationError("clarification_question_invalid")
    expected_id = derive_clarification_id(
        str(row["source_job_id"]), str(row["source_outcome_id"])
    )
    if row["clarification_id"] != expected_id:
        raise ClarificationError("clarification_identity_invalid")
    return {
        "schema_version": CLARIFICATION_SCHEMA_VERSION,
        "clarification_id": str(row["clarification_id"]),
        "source_job_id": str(row["source_job_id"]),
        "source_outcome_id": str(row["source_outcome_id"]),
        "subject_id": str(row["subject_id"]),
        "result_session_id": str(row["result_session_id"]),
        "question": question,
        "target_hint": cast(str | None, row["target_hint"]),
        "state": state,
        "answer_evidence_id": cast(str | None, row["answer_evidence_id"]),
        "follow_up_job_id": cast(str | None, row["follow_up_job_id"]),
        "opened_at": str(row["opened_at"]),
        "answered_at": cast(str | None, row["answered_at"]),
        "resolved_at": cast(str | None, row["resolved_at"]),
    }


def _fetch_record(
    db: sqlite3.Connection, query: str, params: tuple[object, ...]
) -> ClarificationRecordV1 | None:
    """Read one record without depending on the caller's row_factory."""

    cursor = db.execute(query, params)
    raw = cursor.fetchone()
    if raw is None:
        return None
    if isinstance(raw, sqlite3.Row):
        return _row_record(raw)
    mapped = {str(column[0]): raw[index] for index, column in enumerate(cursor.description)}
    return _row_record(mapped)


def synchronize_clarification_in_transaction(
    db: sqlite3.Connection, outcome: Mapping[str, object]
) -> None:
    """Synchronize a terminal outcome with clarification lifecycle atomically."""

    if not db.in_transaction:
        raise ClarificationError("clarification_transaction_required")
    job_id = outcome.get("job_id")
    outcome_id = outcome.get("outcome_id")
    terminal_state = outcome.get("terminal_state")
    occurred_at = outcome.get("occurred_at")
    if not isinstance(job_id, str) or not job_id:
        raise ClarificationError("clarification_terminal_job_invalid")
    if not isinstance(outcome_id, str) or not outcome_id:
        raise ClarificationError("clarification_terminal_outcome_invalid")
    if not isinstance(occurred_at, str) or not occurred_at:
        raise ClarificationError("clarification_terminal_time_invalid")

    parent = _fetch_record(
        db,
        "SELECT * FROM clarification WHERE follow_up_job_id = ?",
        (job_id,),
    )
    if parent is not None:
        record = parent
        if record["state"] == "answered":
            updated = db.execute(
                """UPDATE clarification
                      SET state = 'resolved', resolved_at = ?
                    WHERE clarification_id = ? AND state = 'answered'""",
                (occurred_at, record["clarification_id"]),
            )
            if updated.rowcount != 1:
                raise ClarificationError("clarification_resolution_lost")
        elif record["state"] == "resolved":
            if record["resolved_at"] != occurred_at:
                raise ClarificationError("clarification_resolution_conflict")
        else:
            raise ClarificationError("clarification_follow_up_state_invalid")

    if terminal_state != "clarification_required":
        return
    question = outcome.get("terminal_detail")
    subject_id = outcome.get("subject_id")
    result_session_id = outcome.get("result_session_id")
    if not isinstance(question, str) or not question.strip() or len(question) > 500:
        raise ClarificationError("clarification_question_invalid")
    if not isinstance(subject_id, str) or not subject_id:
        raise ClarificationError("clarification_subject_invalid")
    if not isinstance(result_session_id, str) or not result_session_id:
        raise ClarificationError("clarification_result_session_invalid")
    clarification_id = derive_clarification_id(job_id, outcome_id)
    db.execute(
        """INSERT INTO clarification (
               clarification_id, source_job_id, source_outcome_id, subject_id,
               result_session_id, question, target_hint, state,
               answer_evidence_id, follow_up_job_id, opened_at,
               answered_at, resolved_at
           ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'open', NULL, NULL, ?, NULL, NULL)
           ON CONFLICT(clarification_id) DO NOTHING""",
        (
            clarification_id,
            job_id,
            outcome_id,
            subject_id,
            result_session_id,
            question,
            occurred_at,
        ),
    )
    persisted = _fetch_record(
        db,
        "SELECT * FROM clarification WHERE clarification_id = ?",
        (clarification_id,),
    )
    if persisted is None:
        raise ClarificationError("clarification_open_disappeared")
    record = persisted
    expected = (
        job_id,
        outcome_id,
        subject_id,
        result_session_id,
        question,
        occurred_at,
    )
    actual = (
        record["source_job_id"],
        record["source_outcome_id"],
        record["subject_id"],
        record["result_session_id"],
        record["question"],
        record["opened_at"],
    )
    if actual != expected:
        raise ClarificationError("clarification_open_replay_conflict")


class ClarificationService:
    """Subject/host-bound clarification query and answer service."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        subject_id: str,
        host_id: str,
        clock: Clock = system_clock,
    ) -> None:
        if not isinstance(subject_id, str) or not subject_id.strip():
            raise ClarificationError("clarification_subject_required")
        if not isinstance(host_id, str) or not host_id.strip():
            raise ClarificationError("clarification_host_required")
        self.db_path = Path(db_path)
        self.subject_id = subject_id
        self.host_id = host_id
        self._clock = clock

    def _connect(self) -> sqlite3.Connection:
        db = open_db(str(self.db_path))
        db.row_factory = sqlite3.Row
        return db

    def list_clarifications(
        self,
        *,
        result_session_id: str | None = None,
        state: ClarificationState | None = None,
    ) -> list[ClarificationRecordV1]:
        if state is not None and state not in {"open", "answered", "resolved"}:
            raise ClarificationError("clarification_state_invalid")
        clauses = [
            "c.subject_id = ?",
            "j.subject_id = c.subject_id",
            "j.host_id = ?",
            "j.provider_name = 'memoweft'",
        ]
        params: list[object] = [self.subject_id, self.host_id]
        if result_session_id is not None:
            if not isinstance(result_session_id, str) or not result_session_id.strip():
                raise ClarificationError("clarification_result_session_required")
            clauses.append("c.result_session_id = ?")
            params.append(result_session_id)
        if state is not None:
            clauses.append("c.state = ?")
            params.append(state)
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT c.* FROM clarification AS c "
                "JOIN memory_world_job AS j ON j.job_id = c.source_job_id WHERE "
                + " AND ".join(clauses)
                + " ORDER BY c.opened_at, c.clarification_id",
                tuple(params),
            ).fetchall()
            return [_row_record(row) for row in rows]
        finally:
            db.close()

    def get_clarification(self, clarification_id: str) -> ClarificationRecordV1:
        if not isinstance(clarification_id, str) or not clarification_id:
            raise ClarificationError("clarification_id_required")
        db = self._connect()
        try:
            row = db.execute(
                "SELECT * FROM clarification WHERE clarification_id = ? AND subject_id = ?",
                (clarification_id, self.subject_id),
            ).fetchone()
            if row is None:
                raise ClarificationError("clarification_not_found")
            return _row_record(row)
        finally:
            db.close()

    def answer_latest_for_session(
        self, *, result_session_id: str, answer: str
    ) -> ClarificationAnswerReceiptV1 | None:
        """Answer the newest open question in the exact result session.

        If a restarted host replays the same answer after the first commit, the
        newest answered/resolved record with identical Evidence is returned.
        """

        if not isinstance(result_session_id, str) or not result_session_id.strip():
            raise ClarificationError("clarification_result_session_required")
        if not isinstance(answer, str) or not answer.strip():
            raise ClarificationError("clarification_answer_required")
        db = self._connect()
        try:
            row = db.execute(
                """SELECT * FROM clarification
                    WHERE subject_id = ? AND result_session_id = ? AND state = 'open'
                    ORDER BY opened_at DESC, clarification_id DESC LIMIT 1""",
                (self.subject_id, result_session_id),
            ).fetchone()
            if row is not None:
                clarification_id = str(row["clarification_id"])
            else:
                replay = db.execute(
                    """SELECT c.* FROM clarification AS c
                        JOIN evidence AS e ON e.id = c.answer_evidence_id
                       WHERE c.subject_id = ? AND c.result_session_id = ?
                         AND c.state IN ('answered', 'resolved')
                         AND e.raw_content = ?
                       ORDER BY c.answered_at DESC, c.clarification_id DESC LIMIT 1""",
                    (self.subject_id, result_session_id, answer),
                ).fetchone()
                if replay is None:
                    return None
                return self._answer_receipt(replay, replayed=True)
        finally:
            db.close()
        return self.answer(
            clarification_id=clarification_id,
            result_session_id=result_session_id,
            answer=answer,
        )

    def answer(
        self,
        *,
        clarification_id: str,
        result_session_id: str,
        answer: str,
    ) -> ClarificationAnswerReceiptV1:
        if not isinstance(clarification_id, str) or not clarification_id:
            raise ClarificationError("clarification_id_required")
        if not isinstance(result_session_id, str) or not result_session_id.strip():
            raise ClarificationError("clarification_result_session_required")
        if not isinstance(answer, str) or not answer.strip():
            raise ClarificationError("clarification_answer_required")
        if len(answer) > 100_000:
            raise ClarificationError("clarification_answer_too_large")

        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM clarification WHERE clarification_id = ? AND subject_id = ?",
                (clarification_id, self.subject_id),
            ).fetchone()
            if row is None:
                raise ClarificationError("clarification_not_found")
            record = _row_record(row)
            if record["result_session_id"] != result_session_id:
                raise ClarificationError("clarification_result_session_mismatch")
            if record["state"] != "open":
                evidence = db.execute(
                    "SELECT raw_content FROM evidence WHERE id = ?",
                    (record["answer_evidence_id"],),
                ).fetchone()
                if evidence is None or evidence[0] != answer:
                    raise ClarificationError("clarification_already_answered")
                receipt = self._answer_receipt(row, replayed=True)
                db.execute("COMMIT")
                return receipt

            source = db.execute(
                """SELECT provider_name, subject_id, host_id
                     FROM memory_world_job WHERE job_id = ?""",
                (record["source_job_id"],),
            ).fetchone()
            if source is None:
                raise ClarificationError("clarification_source_job_missing")
            if source["subject_id"] != self.subject_id or source["host_id"] != self.host_id:
                raise ClarificationError("clarification_source_boundary_mismatch")
            if source["provider_name"] != "memoweft":
                raise ClarificationError("clarification_provider_mismatch")

            now = to_iso_z(self._clock())
            answer_hash = _hash_text(answer)
            event_id = "clarification-answer-boundary:v1:" + _hash_text(
                _canonical_json(
                    {
                        "clarification_id": clarification_id,
                        "domain": "memoweft_clarification_answer_boundary_v1",
                    }
                )
            )
            payload_hash = _hash_text(
                _canonical_json(
                    {
                        "answer_sha256": answer_hash,
                        "clarification_id": clarification_id,
                        "question_sha256": _hash_text(record["question"]),
                        "result_session_id": result_session_id,
                        "schema_version": CLARIFICATION_SCHEMA_VERSION,
                    }
                )
            )
            origin_id = "clarification-answer:v1:" + _hash_text(
                _canonical_json(
                    {
                        "clarification_id": clarification_id,
                        "domain": "memoweft_clarification_answer_evidence_v1",
                    }
                )
            )
            # Local import avoids a module cycle: boundary_store imports the
            # terminal-outcome module, which calls this module at settlement.
            from ..hermes.boundary_store import (
                HermesBoundaryEvidenceCandidate,
                HermesBoundaryFormalTarget,
                HermesBoundaryStore,
                ValidatedHermesBoundary,
            )

            boundary = ValidatedHermesBoundary(
                event_id=event_id,
                payload_hash=payload_hash,
                formal_target=HermesBoundaryFormalTarget(
                    boundary_schema_version=1,
                    provider_name="memoweft",
                    parent_session_id=result_session_id,
                    result_session_id=result_session_id,
                    mode="in_place",
                    subject_id=self.subject_id,
                    host_id=self.host_id,
                ),
                evidence=(
                    HermesBoundaryEvidenceCandidate(
                        origin_id=origin_id,
                        raw_content=answer,
                        occurred_at=now,
                        preceding_ai_context=record["question"],
                    ),
                ),
            )
            delivery = HermesBoundaryStore(
                db, clock=self._clock
            ).accept_in_transaction(boundary)
            job = db.execute(
                "SELECT evidence_ids_json FROM memory_world_job WHERE job_id = ?",
                (delivery.job_id,),
            ).fetchone()
            if job is None:
                raise ClarificationError("clarification_follow_up_job_missing")
            try:
                evidence_ids = json.loads(job["evidence_ids_json"])
            except (TypeError, ValueError) as exc:
                raise ClarificationError("clarification_follow_up_evidence_invalid") from exc
            if not isinstance(evidence_ids, list) or len(evidence_ids) != 1 or not isinstance(
                evidence_ids[0], str
            ):
                raise ClarificationError("clarification_follow_up_evidence_invalid")
            updated = db.execute(
                """UPDATE clarification
                      SET state = 'answered', answer_evidence_id = ?,
                          follow_up_job_id = ?, answered_at = ?
                    WHERE clarification_id = ? AND state = 'open'""",
                (evidence_ids[0], delivery.job_id, now, clarification_id),
            )
            if updated.rowcount != 1:
                raise ClarificationError("clarification_answer_lost")
            answered = db.execute(
                "SELECT * FROM clarification WHERE clarification_id = ?",
                (clarification_id,),
            ).fetchone()
            if answered is None:
                raise ClarificationError("clarification_answer_disappeared")
            receipt = self._answer_receipt(answered, replayed=False)
            db.execute("COMMIT")
            return receipt
        except BaseException:
            if db.in_transaction:
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            db.close()

    @staticmethod
    def _answer_receipt(
        row: sqlite3.Row, *, replayed: bool
    ) -> ClarificationAnswerReceiptV1:
        record = _row_record(row)
        answer_evidence_id = record["answer_evidence_id"]
        follow_up_job_id = record["follow_up_job_id"]
        if answer_evidence_id is None or follow_up_job_id is None:
            raise ClarificationError("clarification_answer_receipt_incomplete")
        return {
            "schema_version": CLARIFICATION_SCHEMA_VERSION,
            "clarification_id": record["clarification_id"],
            "state": record["state"],
            "result_session_id": record["result_session_id"],
            "answer_evidence_id": answer_evidence_id,
            "follow_up_job_id": follow_up_job_id,
            "replayed": replayed,
        }


__all__ = [
    "CLARIFICATION_SCHEMA_VERSION",
    "ClarificationAnswerReceiptV1",
    "ClarificationError",
    "ClarificationRecordV1",
    "ClarificationService",
    "derive_clarification_id",
    "synchronize_clarification_in_transaction",
]
