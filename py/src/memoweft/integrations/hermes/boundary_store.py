"""Atomic Hermes-boundary acceptance into Evidence and one World Job.

The caller owns envelope parsing and payload-hash verification.  This module
owns the durable local invariant that remains after Hermes acknowledges its
outbox event:

* one ``event_id`` is bound to one payload, formal target, and Evidence batch;
* Evidence, the World Job, and its compact delivery receipt commit together;
* an exact replay returns the immutable first receipt without touching Evidence;
* every mismatch or integrity failure rolls the whole attempt back; and
* this short transaction never performs model, identity, or World work.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import sqlite3
from typing import Literal, Mapping, Sequence, cast

from ...clock import Clock, system_clock, to_iso_z
from ...store.evidence import SqliteEvidenceStore
from ...types import EvidenceInput


InitialJobState = Literal["pending", "no_change"]

_JOB_SCHEMA_VERSION = 1
_RECEIPT_SCHEMA_VERSION = 1
_NO_ELIGIBLE_REASON = "no_eligible_user_evidence"
_CURRENT_JOB_STATES = frozenset(
    {"pending", "processing", "applied", "no_change", "retry", "dead"}
)
#: AUTHORITY §3 terminals persisted in ``memory_world_job.terminal_state``.
#: NULL is legal on non-terminal (pending/processing/retry) and legacy rows.
_CURRENT_TERMINAL_STATES = frozenset(
    {"applied", "no_change", "clarification_required", "out_of_scope", "failed"}
)
_FORMAL_TARGET_KEYS = frozenset(
    {
        "boundary_schema_version",
        "provider_name",
        "parent_session_id",
        "result_session_id",
        "mode",
        "subject_id",
        "host_id",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "event_id",
        "payload_hash",
        "formal_target_hash",
        "evidence_manifest_hash",
        "job_id",
        "job_state",
        "reason",
        "eligible",
        "stored",
        "skipped",
        "evidence_count",
        "accepted_at",
    }
)


class BoundaryStoreInputError(ValueError):
    """A supposedly validated boundary has an invalid local shape."""


class BoundaryReplayMismatchError(RuntimeError):
    """An event id was replayed with a different bound input."""


class BoundaryReceiptIntegrityError(RuntimeError):
    """A stored World Job or its immutable delivery receipt is inconsistent."""


class BoundaryEvidenceConflictError(RuntimeError):
    """An existing Evidence origin disagrees with the boundary candidate."""


@dataclass(frozen=True, slots=True)
class HermesBoundaryFormalTarget:
    """Compiler-owned destination of one committed Hermes boundary."""

    boundary_schema_version: int
    provider_name: str
    parent_session_id: str
    result_session_id: str
    mode: str
    subject_id: str
    host_id: str


@dataclass(frozen=True, slots=True)
class HermesBoundaryEvidenceCandidate:
    """One already-filtered, user-authored ``spoken`` Evidence candidate."""

    origin_id: str
    raw_content: str
    occurred_at: str | None = None
    preceding_ai_context: str | None = None


@dataclass(frozen=True, slots=True)
class ValidatedHermesBoundary:
    """Hash-verified boundary input accepted by :class:`HermesBoundaryStore`."""

    event_id: str
    payload_hash: str
    formal_target: HermesBoundaryFormalTarget
    evidence: tuple[HermesBoundaryEvidenceCandidate, ...]


@dataclass(frozen=True, slots=True)
class HermesBoundaryDeliveryReceipt:
    """Content-free, immutable result of the first committed delivery."""

    schema_version: int
    event_id: str
    payload_hash: str
    formal_target_hash: str
    evidence_manifest_hash: str
    job_id: str
    job_state: InitialJobState
    reason: str | None
    eligible: int
    stored: int
    skipped: int
    evidence_count: int
    accepted_at: str
    receipt_hash: str

    def as_dict(self) -> dict[str, object]:
        """Return the compact mapping safe to hand back to the Hermes outbox."""

        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "payload_hash": self.payload_hash,
            "formal_target_hash": self.formal_target_hash,
            "evidence_manifest_hash": self.evidence_manifest_hash,
            "job_id": self.job_id,
            "job_state": self.job_state,
            "reason": self.reason,
            "eligible": self.eligible,
            "stored": self.stored,
            "skipped": self.skipped,
            "evidence_count": self.evidence_count,
            "accepted_at": self.accepted_at,
            "receipt_hash": self.receipt_hash,
        }


@dataclass(frozen=True, slots=True)
class _StoredBoundaryJob:
    event_id: str
    payload_hash: str
    formal_target_json: str
    formal_target_hash: str
    evidence_ids: tuple[str, ...]
    evidence_manifest_hash: str
    current_state: str
    receipt: HermesBoundaryDeliveryReceipt


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _require_closed_text(value: object, *, field: str, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise BoundaryStoreInputError(f"Hermes boundary {field} is invalid")
    return value


def _is_closed_text(value: object, *, maximum: int = 1024) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum
    )


def _formal_target_data(target: HermesBoundaryFormalTarget) -> dict[str, object]:
    return {
        "boundary_schema_version": target.boundary_schema_version,
        "provider_name": target.provider_name,
        "parent_session_id": target.parent_session_id,
        "result_session_id": target.result_session_id,
        "mode": target.mode,
        "subject_id": target.subject_id,
        "host_id": target.host_id,
    }


def _candidate_manifest_data(
    candidates: Sequence[HermesBoundaryEvidenceCandidate],
) -> list[dict[str, object]]:
    return [
        {
            "origin_id": candidate.origin_id,
            "raw_content": candidate.raw_content,
            "occurred_at": candidate.occurred_at,
            "preceding_ai_context": candidate.preceding_ai_context,
        }
        for candidate in candidates
    ]


def _receipt_data(receipt: HermesBoundaryDeliveryReceipt) -> dict[str, object]:
    return {
        "schema_version": receipt.schema_version,
        "event_id": receipt.event_id,
        "payload_hash": receipt.payload_hash,
        "formal_target_hash": receipt.formal_target_hash,
        "evidence_manifest_hash": receipt.evidence_manifest_hash,
        "job_id": receipt.job_id,
        "job_state": receipt.job_state,
        "reason": receipt.reason,
        "eligible": receipt.eligible,
        "stored": receipt.stored,
        "skipped": receipt.skipped,
        "evidence_count": receipt.evidence_count,
        "accepted_at": receipt.accepted_at,
    }


def _validate_boundary(boundary: ValidatedHermesBoundary) -> None:
    _require_closed_text(boundary.event_id, field="event_id", maximum=255)
    if not _is_sha256(boundary.payload_hash):
        raise BoundaryStoreInputError("Hermes boundary payload_hash is invalid")

    target = boundary.formal_target
    if type(target.boundary_schema_version) is not int or target.boundary_schema_version < 1:
        raise BoundaryStoreInputError("Hermes boundary schema version is invalid")
    if target.provider_name != "memoweft":
        raise BoundaryStoreInputError("Hermes boundary targets a different provider")
    _require_closed_text(target.provider_name, field="provider_name", maximum=128)
    _require_closed_text(target.parent_session_id, field="parent_session_id")
    _require_closed_text(target.result_session_id, field="result_session_id")
    if target.mode not in {"in_place", "rotation"}:
        raise BoundaryStoreInputError("Hermes boundary mode is invalid")
    _require_closed_text(target.subject_id, field="subject_id")
    _require_closed_text(target.host_id, field="host_id")

    candidate_origins: set[str] = set()
    for candidate in boundary.evidence:
        _require_closed_text(candidate.origin_id, field="Evidence origin_id")
        if candidate.origin_id in candidate_origins:
            raise BoundaryStoreInputError(
                "Hermes boundary contains a duplicate Evidence origin"
            )
        candidate_origins.add(candidate.origin_id)
        if not isinstance(candidate.raw_content, str) or not candidate.raw_content.strip():
            raise BoundaryStoreInputError("Hermes boundary Evidence content is invalid")
        if candidate.occurred_at is not None and (
            not isinstance(candidate.occurred_at, str) or not candidate.occurred_at
        ):
            raise BoundaryStoreInputError("Hermes boundary Evidence occurred_at is invalid")
        if candidate.preceding_ai_context is not None and not isinstance(
            candidate.preceding_ai_context, str
        ):
            raise BoundaryStoreInputError(
                "Hermes boundary preceding_ai_context is invalid"
            )


def _decode_canonical_json(raw: object, *, label: str) -> object:
    if not isinstance(raw, str) or not raw:
        raise BoundaryReceiptIntegrityError(f"Stored {label} is missing")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise BoundaryReceiptIntegrityError(f"Stored {label} is not valid JSON") from exc
    try:
        canonical = _canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise BoundaryReceiptIntegrityError(
            f"Stored {label} cannot be canonicalized"
        ) from exc
    if canonical != raw:
        raise BoundaryReceiptIntegrityError(f"Stored {label} is not canonical")
    return value


class HermesBoundaryStore:
    """Accept validated Hermes boundaries using one caller-owned SQLite connection."""

    def __init__(self, db: sqlite3.Connection, *, clock: Clock = system_clock) -> None:
        self._db = db
        self._clock = clock

    def accept(
        self, boundary: ValidatedHermesBoundary
    ) -> HermesBoundaryDeliveryReceipt:
        """Atomically persist Evidence, one World Job, and its first receipt."""

        _validate_boundary(boundary)
        formal_target_json = _canonical_json(
            _formal_target_data(boundary.formal_target)
        )
        formal_target_hash = _sha256_text(formal_target_json)
        evidence_manifest_hash = _sha256_text(
            _canonical_json(_candidate_manifest_data(boundary.evidence))
        )

        self._db.execute("BEGIN IMMEDIATE")
        try:
            existing = self._job_by_event(boundary.event_id)
            if existing is not None:
                stored_job = self._stored_job(existing)
                if (
                    stored_job.payload_hash != boundary.payload_hash
                    or stored_job.formal_target_json != formal_target_json
                    or stored_job.formal_target_hash != formal_target_hash
                    or stored_job.evidence_manifest_hash != evidence_manifest_hash
                ):
                    raise BoundaryReplayMismatchError(
                        "Hermes boundary event_id is bound to a different payload or target"
                    )
                self._db.execute("COMMIT")
                return stored_job.receipt

            evidence_ids, stored, skipped = self._write_evidence(
                boundary.evidence,
                target=boundary.formal_target,
            )
            accepted_at = to_iso_z(self._clock())
            initial_state: InitialJobState = (
                "pending" if boundary.evidence else "no_change"
            )
            reason = None if boundary.evidence else _NO_ELIGIBLE_REASON
            job_id = "memory-world-job-" + _sha256_text(
                _canonical_json(
                    [
                        "memory_world_job_v1",
                        boundary.event_id,
                        boundary.payload_hash,
                        formal_target_hash,
                        evidence_manifest_hash,
                    ]
                )
            )
            receipt = self._build_delivery_receipt(
                boundary=boundary,
                formal_target_hash=formal_target_hash,
                evidence_manifest_hash=evidence_manifest_hash,
                job_id=job_id,
                job_state=initial_state,
                reason=reason,
                stored=stored,
                skipped=skipped,
                evidence_count=len(evidence_ids),
                accepted_at=accepted_at,
            )
            receipt_json = _canonical_json(_receipt_data(receipt))
            if _sha256_text(receipt_json) != receipt.receipt_hash:
                raise BoundaryReceiptIntegrityError(
                    "New Hermes delivery receipt hash is inconsistent"
                )
            self._insert_job(
                boundary=boundary,
                formal_target_json=formal_target_json,
                formal_target_hash=formal_target_hash,
                evidence_ids=evidence_ids,
                receipt=receipt,
                receipt_json=receipt_json,
            )
            self._db.execute("COMMIT")
            return receipt
        except BaseException:
            try:
                self._db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _write_evidence(
        self,
        candidates: Sequence[HermesBoundaryEvidenceCandidate],
        *,
        target: HermesBoundaryFormalTarget,
    ) -> tuple[tuple[str, ...], int, int]:
        store = SqliteEvidenceStore(self._db, clock=self._clock)
        evidence_ids: list[str] = []
        seen_ids: set[str] = set()
        stored = 0
        skipped = 0
        for candidate in candidates:
            existing = self._evidence_by_origin(candidate.origin_id)
            if existing is not None:
                evidence_id = self._matching_evidence_id(
                    existing,
                    candidate=candidate,
                    target=target,
                )
                skipped += 1
            else:
                evidence = store.put(
                    EvidenceInput(
                        subject_id=target.subject_id,
                        source_kind="spoken",
                        host_id=target.host_id,
                        origin_id=candidate.origin_id,
                        occurred_at=candidate.occurred_at,
                        raw_content=candidate.raw_content,
                        preceding_ai_context=candidate.preceding_ai_context,
                    )
                )
                evidence_id = evidence.id
                stored += 1
            self._bind_evidence_content_hash(evidence_id, candidate.raw_content)
            if evidence_id not in seen_ids:
                evidence_ids.append(evidence_id)
                seen_ids.add(evidence_id)
        return tuple(evidence_ids), stored, skipped

    def _bind_evidence_content_hash(self, evidence_id: str, raw_content: str) -> None:
        """Bind the evidence row to the SHA-256 of the exact accepted content.

        Written inside the same acceptance transaction; the formal batch
        compiler re-verifies it before the model call and again before Apply.
        ``OR IGNORE`` keeps the binding idempotent across replay.
        """
        self._db.execute(
            "INSERT OR IGNORE INTO boundary_evidence_content "
            "(evidence_id, raw_content_hash) VALUES (?, ?)",
            (evidence_id, _sha256_text(raw_content)),
        )

    def _matching_evidence_id(
        self,
        row: sqlite3.Row,
        *,
        candidate: HermesBoundaryEvidenceCandidate,
        target: HermesBoundaryFormalTarget,
    ) -> str:
        evidence_id = row["id"]
        occurred_at_matches = (
            candidate.occurred_at is None
            or row["occurred_at"] == candidate.occurred_at
        )
        if (
            not isinstance(evidence_id, str)
            or not evidence_id
            or row["subject_id"] != target.subject_id
            or row["source_kind"] != "spoken"
            or row["host_id"] != target.host_id
            or row["origin_id"] != candidate.origin_id
            or row["raw_content"] != candidate.raw_content
            or not occurred_at_matches
            or row["preceding_ai_context"] != candidate.preceding_ai_context
            or row["corrects_evidence_id"] is not None
            or row["deleted_at"] is not None
        ):
            raise BoundaryEvidenceConflictError(
                "Hermes Evidence origin conflicts with stored content or metadata"
            )
        return evidence_id

    def _build_delivery_receipt(
        self,
        *,
        boundary: ValidatedHermesBoundary,
        formal_target_hash: str,
        evidence_manifest_hash: str,
        job_id: str,
        job_state: InitialJobState,
        reason: str | None,
        stored: int,
        skipped: int,
        evidence_count: int,
        accepted_at: str,
    ) -> HermesBoundaryDeliveryReceipt:
        unhashed = HermesBoundaryDeliveryReceipt(
            schema_version=_RECEIPT_SCHEMA_VERSION,
            event_id=boundary.event_id,
            payload_hash=boundary.payload_hash,
            formal_target_hash=formal_target_hash,
            evidence_manifest_hash=evidence_manifest_hash,
            job_id=job_id,
            job_state=job_state,
            reason=reason,
            eligible=len(boundary.evidence),
            stored=stored,
            skipped=skipped,
            evidence_count=evidence_count,
            accepted_at=accepted_at,
            receipt_hash="",
        )
        receipt_hash = _sha256_text(_canonical_json(_receipt_data(unhashed)))
        return HermesBoundaryDeliveryReceipt(
            schema_version=unhashed.schema_version,
            event_id=unhashed.event_id,
            payload_hash=unhashed.payload_hash,
            formal_target_hash=unhashed.formal_target_hash,
            evidence_manifest_hash=unhashed.evidence_manifest_hash,
            job_id=unhashed.job_id,
            job_state=unhashed.job_state,
            reason=unhashed.reason,
            eligible=unhashed.eligible,
            stored=unhashed.stored,
            skipped=unhashed.skipped,
            evidence_count=unhashed.evidence_count,
            accepted_at=unhashed.accepted_at,
            receipt_hash=receipt_hash,
        )

    def _insert_job(
        self,
        *,
        boundary: ValidatedHermesBoundary,
        formal_target_json: str,
        formal_target_hash: str,
        evidence_ids: Sequence[str],
        receipt: HermesBoundaryDeliveryReceipt,
        receipt_json: str,
    ) -> None:
        evidence_ids_json = _canonical_json(list(evidence_ids))
        is_no_change = receipt.job_state == "no_change"
        world_result_json = (
            _canonical_json(
                {"reason": _NO_ELIGIBLE_REASON, "state": "no_change"}
            )
            if is_no_change
            else None
        )
        self._db.execute(
            """INSERT INTO memory_world_job (
              job_id, job_schema_version, boundary_event_id, boundary_payload_hash,
              boundary_schema_version, provider_name, parent_session_id,
              result_session_id, boundary_mode, formal_target_json,
              formal_target_hash, subject_id, host_id, evidence_ids_json, state,
              attempts, next_attempt_at, fencing_generation, model_task,
              world_result_json, result_hash, delivery_receipt_json,
              delivery_receipt_hash, created_at, completed_at, terminal_state
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                receipt.job_id,
                _JOB_SCHEMA_VERSION,
                boundary.event_id,
                boundary.payload_hash,
                boundary.formal_target.boundary_schema_version,
                boundary.formal_target.provider_name,
                boundary.formal_target.parent_session_id,
                boundary.formal_target.result_session_id,
                boundary.formal_target.mode,
                formal_target_json,
                formal_target_hash,
                boundary.formal_target.subject_id,
                boundary.formal_target.host_id,
                evidence_ids_json,
                receipt.job_state,
                0,
                None if is_no_change else receipt.accepted_at,
                0,
                None if is_no_change else "memory_world",
                world_result_json,
                _sha256_text(world_result_json) if world_result_json is not None else None,
                receipt_json,
                receipt.receipt_hash,
                receipt.accepted_at,
                receipt.accepted_at if is_no_change else None,
                "no_change" if is_no_change else None,
            ),
        )

    def _job_by_event(self, event_id: str) -> sqlite3.Row | None:
        cursor = self._db.cursor()
        cursor.row_factory = sqlite3.Row
        return cast(
            "sqlite3.Row | None",
            cursor.execute(
                "SELECT * FROM memory_world_job WHERE boundary_event_id = ?",
                (event_id,),
            ).fetchone(),
        )

    def _evidence_by_origin(self, origin_id: str) -> sqlite3.Row | None:
        cursor = self._db.cursor()
        cursor.row_factory = sqlite3.Row
        return cast(
            "sqlite3.Row | None",
            cursor.execute(
                "SELECT * FROM evidence WHERE origin_id = ?",
                (origin_id,),
            ).fetchone(),
        )

    def _stored_job(self, row: sqlite3.Row) -> _StoredBoundaryJob:
        if row["job_schema_version"] != _JOB_SCHEMA_VERSION:
            raise BoundaryReceiptIntegrityError("Stored World Job schema is invalid")

        event_id = row["boundary_event_id"]
        payload_hash = row["boundary_payload_hash"]
        formal_target_json = row["formal_target_json"]
        formal_target_hash = row["formal_target_hash"]
        job_id = row["job_id"]
        current_state = row["state"]
        created_at = row["created_at"]
        terminal_state = row["terminal_state"] if "terminal_state" in row.keys() else None
        if (
            not isinstance(event_id, str)
            or not event_id
            or not _is_sha256(payload_hash)
            or not isinstance(formal_target_json, str)
            or not _is_sha256(formal_target_hash)
            or not isinstance(job_id, str)
            or not job_id
            or current_state not in _CURRENT_JOB_STATES
            or not isinstance(created_at, str)
            or not created_at
            or (
                terminal_state is not None
                and terminal_state not in _CURRENT_TERMINAL_STATES
            )
        ):
            raise BoundaryReceiptIntegrityError("Stored World Job has an invalid shape")

        target_data = _decode_canonical_json(
            formal_target_json, label="formal target"
        )
        if not isinstance(target_data, Mapping) or set(target_data) != _FORMAL_TARGET_KEYS:
            raise BoundaryReceiptIntegrityError("Stored formal target has an invalid shape")
        if (
            type(target_data["boundary_schema_version"]) is not int
            or target_data["boundary_schema_version"] < 1
            or target_data["provider_name"] != "memoweft"
            or not _is_closed_text(target_data["provider_name"], maximum=128)
            or not _is_closed_text(target_data["parent_session_id"])
            or not _is_closed_text(target_data["result_session_id"])
            or target_data["mode"] not in {"in_place", "rotation"}
            or not _is_closed_text(target_data["subject_id"])
            or not _is_closed_text(target_data["host_id"])
        ):
            raise BoundaryReceiptIntegrityError(
                "Stored formal target has invalid field values"
            )
        if _sha256_text(formal_target_json) != formal_target_hash:
            raise BoundaryReceiptIntegrityError("Stored formal target hash mismatch")
        if (
            target_data["boundary_schema_version"] != row["boundary_schema_version"]
            or target_data["provider_name"] != row["provider_name"]
            or target_data["parent_session_id"] != row["parent_session_id"]
            or target_data["result_session_id"] != row["result_session_id"]
            or target_data["mode"] != row["boundary_mode"]
            or target_data["subject_id"] != row["subject_id"]
            or target_data["host_id"] != row["host_id"]
        ):
            raise BoundaryReceiptIntegrityError(
                "Stored formal target disagrees with the World Job"
            )

        evidence_data = _decode_canonical_json(
            row["evidence_ids_json"], label="Evidence id batch"
        )
        if (
            not isinstance(evidence_data, list)
            or any(not isinstance(item, str) or not item for item in evidence_data)
            or len(set(evidence_data)) != len(evidence_data)
        ):
            raise BoundaryReceiptIntegrityError(
                "Stored Evidence id batch has an invalid shape"
            )
        evidence_ids = tuple(evidence_data)

        receipt_json = row["delivery_receipt_json"]
        receipt_hash = row["delivery_receipt_hash"]
        if (
            not isinstance(receipt_json, str)
            or not _is_sha256(receipt_hash)
            or _sha256_text(receipt_json) != receipt_hash
        ):
            raise BoundaryReceiptIntegrityError("Stored delivery receipt hash mismatch")
        receipt_data = _decode_canonical_json(
            receipt_json, label="delivery receipt"
        )
        receipt = self._receipt_from_data(receipt_data, receipt_hash=receipt_hash)
        if (
            receipt.event_id != event_id
            or receipt.payload_hash != payload_hash
            or receipt.formal_target_hash != formal_target_hash
            or receipt.job_id != job_id
            or receipt.evidence_count != len(evidence_ids)
            or receipt.accepted_at != created_at
            or (receipt.job_state == "no_change" and current_state != "no_change")
        ):
            raise BoundaryReceiptIntegrityError(
                "Stored delivery receipt disagrees with the World Job"
            )
        return _StoredBoundaryJob(
            event_id=event_id,
            payload_hash=payload_hash,
            formal_target_json=formal_target_json,
            formal_target_hash=formal_target_hash,
            evidence_ids=evidence_ids,
            evidence_manifest_hash=receipt.evidence_manifest_hash,
            current_state=current_state,
            receipt=receipt,
        )

    @staticmethod
    def _receipt_from_data(
        data: object, *, receipt_hash: str
    ) -> HermesBoundaryDeliveryReceipt:
        if not isinstance(data, Mapping) or set(data) != _RECEIPT_KEYS:
            raise BoundaryReceiptIntegrityError(
                "Stored delivery receipt has an invalid shape"
            )
        schema_version = data["schema_version"]
        event_id = data["event_id"]
        payload_hash = data["payload_hash"]
        formal_target_hash = data["formal_target_hash"]
        evidence_manifest_hash = data["evidence_manifest_hash"]
        job_id = data["job_id"]
        job_state = data["job_state"]
        reason = data["reason"]
        eligible = data["eligible"]
        stored = data["stored"]
        skipped = data["skipped"]
        evidence_count = data["evidence_count"]
        accepted_at = data["accepted_at"]
        if (
            type(schema_version) is not int
            or schema_version != _RECEIPT_SCHEMA_VERSION
            or not _is_closed_text(event_id, maximum=255)
            or not _is_sha256(payload_hash)
            or not _is_sha256(formal_target_hash)
            or not _is_sha256(evidence_manifest_hash)
            or not isinstance(job_id, str)
            or not job_id
            or not isinstance(job_state, str)
            or job_state not in {"pending", "no_change"}
            or (reason is not None and not isinstance(reason, str))
            or type(eligible) is not int
            or type(stored) is not int
            or type(skipped) is not int
            or type(evidence_count) is not int
            or min(eligible, stored, skipped, evidence_count) < 0
            or stored + skipped != eligible
            or evidence_count > eligible
            or not isinstance(accepted_at, str)
            or not accepted_at
        ):
            raise BoundaryReceiptIntegrityError(
                "Stored delivery receipt has invalid field values"
            )
        if job_state == "no_change":
            if (
                reason != _NO_ELIGIBLE_REASON
                or eligible != 0
                or stored != 0
                or skipped != 0
                or evidence_count != 0
            ):
                raise BoundaryReceiptIntegrityError(
                    "Stored no-change receipt has invalid counts or reason"
                )
        elif reason is not None or eligible == 0 or evidence_count == 0:
            raise BoundaryReceiptIntegrityError(
                "Stored pending receipt has invalid counts or reason"
            )
        return HermesBoundaryDeliveryReceipt(
            schema_version=_RECEIPT_SCHEMA_VERSION,
            event_id=event_id,
            payload_hash=payload_hash,
            formal_target_hash=formal_target_hash,
            evidence_manifest_hash=evidence_manifest_hash,
            job_id=job_id,
            job_state=cast(InitialJobState, job_state),
            reason=reason,
            eligible=eligible,
            stored=stored,
            skipped=skipped,
            evidence_count=evidence_count,
            accepted_at=accepted_at,
            receipt_hash=receipt_hash,
        )


__all__ = [
    "BoundaryEvidenceConflictError",
    "BoundaryReceiptIntegrityError",
    "BoundaryReplayMismatchError",
    "BoundaryStoreInputError",
    "HermesBoundaryDeliveryReceipt",
    "HermesBoundaryEvidenceCandidate",
    "HermesBoundaryFormalTarget",
    "HermesBoundaryStore",
    "ValidatedHermesBoundary",
]
