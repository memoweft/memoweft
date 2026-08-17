"""Hermes memory-provider integration for MemoWeft.

The first integration boundary is deliberately narrow:

* Hermes remains the owner of uncompressed conversation history.
* ``sync_turn`` is inherited as a no-op, so MemoWeft does not write or call a
  model after every turn.
* Only a Hermes boundary that was committed durably with the host transcript
  can persist user-authored messages as MemoWeft ``spoken`` Evidence.
* Assistant text is retained only as ``preceding_ai_context``.  It can help
  interpret a short reply but can never become Evidence by itself.
* Recall and Personal Memory World formation are intentionally not faked here;
  the provider returns no recall until a later slice supplies accepted World
  objects.

Hermes discovers this module through the
``hermes_agent.memory_providers`` package entry-point and calls ``register``.
The Hermes base class is imported lazily so the normal MemoWeft Python package
remains usable without Hermes installed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping, Optional, Sequence

from .boundary_store import (
    HermesBoundaryEvidenceCandidate,
    HermesBoundaryFormalTarget,
    HermesBoundaryStore,
    ValidatedHermesBoundary,
)
from .recall import recall_world_text
from .world_worker import WorldJobWorker
from ...store import open_db
from ...store.schema import (
    BOUNDARY_EVIDENCE_CONTENT_COLUMNS,
    COGNITION_TARGET_COLUMNS,
    ENTITY_COLUMNS,
    MEMORY_WORLD_JOB_COLUMNS,
    PYTHON_APPLICATION_ID,
    RELATIONSHIP_COLUMNS,
    RETRACTION_COLUMNS,
    SCHEMA_VERSION,
    WORLD_EVENT_COLUMNS,
)

logger = logging.getLogger(__name__)

_SUMMARY_PREFIXES = (
    "[CONTEXT COMPACTION — REFERENCE ONLY]",
    "[CONTEXT SUMMARY]:",
)
#: World Job column contract before v15 (no terminal observability columns).
_MEMORY_WORLD_JOB_COLUMNS_PRE_V15 = MEMORY_WORLD_JOB_COLUMNS[:-2]
_CONTINUATION_MARKERS = frozenset(
    {
        "Continue from the compressed conversation context above. "
        "This marker exists because no human user turn was available.",
        "Continue from the compressed conversation context above. "
        "This marker exists because the compacted transcript contained "
        "no preserved user turn.",
    }
)
_BOUNDARY_MODES = frozenset({"in_place", "rotation"})


def _optional_lang(value: Any) -> Optional[str]:
    """Normalize an optional language pin; anything invalid fails closed to
    auto-detection (never crashes provider initialization)."""
    if value in ("zh", "en"):
        return value
    if value is not None:
        logger.warning(
            "MemoWeft lang pin ignored (must be 'zh' or 'en'): %r", value
        )
    return None
_BOUNDARY_EVENT_PREFIX = "hermes-compression-boundary-v1"
_BOUNDARY_KEYS = frozenset(
    {
        "event_id",
        "schema_version",
        "provider_name",
        "parent_session_id",
        "result_session_id",
        "mode",
        "source_messages",
        "payload_hash",
    }
)
_BOUNDARY_MESSAGE_KEYS = frozenset(
    {
        "role",
        "content",
        "timestamp",
        "platform_message_id",
        "message_id",
        "observed",
        "display_kind",
        "synthetic",
        "source_ref",
    }
)


class BoundaryEnvelopeError(ValueError):
    """A durable Hermes boundary does not satisfy the closed v1 envelope."""


class IncompatibleDatabaseError(RuntimeError):
    """An existing database is not a current Python-owned MemoWeft store."""


_REQUIRED_EXISTING_SCHEMA: dict[str, frozenset[str]] = {
    "evidence": frozenset(
        {
            "id",
            "subject_id",
            "source_kind",
            "host_id",
            "origin_id",
            "occurred_at",
            "recorded_at",
            "raw_content",
            "summary",
            "allow_local_read",
            "allow_cloud_read",
            "allow_inference",
            "corrects_evidence_id",
            "preceding_ai_context",
            "deleted_at",
        }
    ),
    "memory_state": frozenset(
        {"singleton", "revision", "snapshot_hash", "snapshot_json"}
    ),
    "proposal_decision_receipts": frozenset(
        {
            "proposal_id",
            "offered_result_hash",
            "effective_decision",
            "world_revision",
            "snapshot_hash",
            "decided_at",
            "receipt_hash",
        }
    ),
}


def _text_content(value: object) -> str:
    """Return text from an OpenAI-style message content value."""

    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _is_synthetic_user_message(message: Mapping[str, object], content: str) -> bool:
    """Fail closed for known Hermes user-role scaffolding."""

    if (
        bool(message.get("synthetic"))
        or bool(message.get("observed"))
        or bool(message.get("_compressed_summary"))
    ):
        return True
    for key, value in message.items():
        if key.startswith("_") and "synthetic" in key and bool(value):
            return True
    stripped = content.strip()
    return stripped.startswith(_SUMMARY_PREFIXES) or stripped in _CONTINUATION_MARKERS


def _occurred_at(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _boundary_payload_hash(boundary: Mapping[str, object]) -> str:
    """Reconstruct Hermes' canonical v1 payload digest (event id excluded)."""

    hash_input = {
        key: boundary[key]
        for key in (
            "schema_version",
            "provider_name",
            "parent_session_id",
            "result_session_id",
            "mode",
            "source_messages",
        )
    }
    canonical = json.dumps(
        hash_input,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _origin_id(
    *,
    message: Mapping[str, object],
    content: str,
    session_id: str,
    message_index: int,
    subject_id: str,
    host_id: str,
    boundary_id: str,
) -> str:
    """Build a stable, opaque origin id without copying host identifiers."""

    message_id = message.get("message_id") or message.get("platform_message_id")
    source_ref = message.get("source_ref")
    row_id = message.get("_row_id")
    timestamp = message.get("timestamp")
    if isinstance(message_id, (str, int)) and not isinstance(message_id, bool):
        identity: tuple[object, ...] = ("platform-message", str(message_id))
    elif isinstance(source_ref, (str, int)) and not isinstance(
        source_ref, bool
    ):
        identity = ("durable-boundary-source", boundary_id, str(source_ref))
    elif isinstance(row_id, int) and not isinstance(row_id, bool):
        identity = ("row", row_id)
    elif isinstance(timestamp, (str, int, float)) and not isinstance(timestamp, bool):
        identity = ("timestamp-content", str(timestamp), sha256(content.encode()).hexdigest())
    else:
        identity = (
            "session-position-content",
            session_id,
            message_index,
            sha256(content.encode()).hexdigest(),
        )
    canonical = json.dumps(
        {
            "host": host_id,
            "subject": subject_id,
            "identity": identity,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "hermes:" + sha256(canonical.encode()).hexdigest()


def _candidates(
    messages: Sequence[Mapping[str, object]],
    *,
    session_id: str,
    subject_id: str,
    host_id: str,
    boundary_id: str,
) -> tuple[HermesBoundaryEvidenceCandidate, ...]:
    candidates: list[HermesBoundaryEvidenceCandidate] = []
    preceding_assistant: str | None = None
    for index, message in enumerate(messages):
        role = message.get("role")
        content = _text_content(message.get("content"))
        # Hermes' durable boundary uses a public ``synthetic`` bit after
        # stripping private host metadata.  Such rows are scaffolding for both
        # roles: they are neither Evidence nor valid preceding AI context.
        if bool(message.get("synthetic")):
            continue
        if role == "assistant":
            if content.strip():
                preceding_assistant = content
            continue
        if role != "user" or not content.strip():
            continue
        if _is_synthetic_user_message(message, content):
            continue
        candidates.append(
            HermesBoundaryEvidenceCandidate(
                raw_content=content,
                origin_id=_origin_id(
                    message=message,
                    content=content,
                    session_id=session_id,
                    message_index=index,
                    subject_id=subject_id,
                    host_id=host_id,
                    boundary_id=boundary_id,
                ),
                occurred_at=_occurred_at(message.get("timestamp")),
                preceding_ai_context=preceding_assistant,
            )
        )
    return tuple(candidates)


def _assert_existing_database_is_current(db_path: Path) -> None:
    """Recognize only migratable Python v6-v13 or physically complete current v14.

    The probe is deliberately read-only and runs before :func:`open_db`.  A
    Python v6-v13 database is identified by its physical marker and may then
    take the explicit stepwise migration to the current version.  TypeScript
    v6, future versions, and same-version partial schemas fail closed before
    a writable connection is opened.
    """

    if not db_path.exists():
        return
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error) as exc:
        raise IncompatibleDatabaseError(
            "Existing MemoWeft database cannot be opened read-only"
        ) from exc
    try:
        db.execute("PRAGMA query_only = ON")
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        app_id = int(db.execute("PRAGMA application_id").fetchone()[0])
        if version not in {6, 7, 8, 9, 10, 11, 12, 13, 14, SCHEMA_VERSION}:
            raise IncompatibleDatabaseError(
                "Existing MemoWeft database is not a supported Python schema version"
            )
        if version == 6 and app_id != 0:
            raise IncompatibleDatabaseError(
                "Existing Python v6 database has an incompatible application id"
            )
        if version in {7, 8, 9, 10, 11, 12, 13, 14, SCHEMA_VERSION} and app_id != PYTHON_APPLICATION_ID:
            raise IncompatibleDatabaseError(
                "Existing Python database has an incompatible application id"
            )
        for table, required_columns in _REQUIRED_EXISTING_SCHEMA.items():
            columns = {
                str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")')
            }
            if not required_columns.issubset(columns):
                raise IncompatibleDatabaseError(
                    "Existing MemoWeft database has an incompatible physical schema"
                )
        if version in {7, 8, 9, 10, 11, 12, 13, 14}:
            job_columns = tuple(
                str(row[1])
                for row in db.execute('PRAGMA table_info("memory_world_job")')
            )
            if job_columns != _MEMORY_WORLD_JOB_COLUMNS_PRE_V15:
                raise IncompatibleDatabaseError(
                    "Existing Python database has an incompatible World Job schema"
                )
        if version == SCHEMA_VERSION:
            job_columns = tuple(
                str(row[1])
                for row in db.execute('PRAGMA table_info("memory_world_job")')
            )
            if job_columns != MEMORY_WORLD_JOB_COLUMNS:
                raise IncompatibleDatabaseError(
                    "Existing Python database has an incompatible World Job schema"
                )
        if version in {8, 9, 10, 11, 12, 13, 14, SCHEMA_VERSION}:
            content_columns = tuple(
                str(row[1])
                for row in db.execute('PRAGMA table_info("boundary_evidence_content")')
            )
            if content_columns != BOUNDARY_EVIDENCE_CONTENT_COLUMNS:
                raise IncompatibleDatabaseError(
                    "Existing Python database has an incompatible content-binding schema"
                )
        if version in {9, 10, 11, 12, 13, 14, SCHEMA_VERSION}:
            relationship_columns = tuple(
                str(row[1]) for row in db.execute('PRAGMA table_info("relationship")')
            )
            if relationship_columns != RELATIONSHIP_COLUMNS:
                raise IncompatibleDatabaseError(
                    "Existing Python database has an incompatible relationship schema"
                )
        if version == SCHEMA_VERSION:
            entity_columns = tuple(
                str(row[1]) for row in db.execute('PRAGMA table_info("entity")')
            )
            if entity_columns != ENTITY_COLUMNS:
                raise IncompatibleDatabaseError(
                    "Existing Python database has an incompatible entity schema"
                )
        if version == SCHEMA_VERSION:
            target_columns = tuple(
                str(row[1]) for row in db.execute('PRAGMA table_info("cognition_target")')
            )
            if target_columns != COGNITION_TARGET_COLUMNS:
                raise IncompatibleDatabaseError(
                    "Existing Python database has an incompatible cognition_target schema"
                )
        if version == SCHEMA_VERSION:
            retraction_columns = tuple(
                str(row[1]) for row in db.execute('PRAGMA table_info("retraction")')
            )
            if retraction_columns != RETRACTION_COLUMNS:
                raise IncompatibleDatabaseError(
                    "Existing Python database has an incompatible retraction schema"
                )
        if version == SCHEMA_VERSION:
            world_event_columns = tuple(
                str(row[1]) for row in db.execute('PRAGMA table_info("world_event")')
            )
            if world_event_columns != WORLD_EVENT_COLUMNS:
                raise IncompatibleDatabaseError(
                    "Existing Python database has an incompatible world_event schema"
                )
    finally:
        db.close()


class _HermesBoundaryRuntimeStore:
    """Private profile binding for the atomic Hermes boundary store only."""

    def __init__(self, db_path: Path, *, subject_id: str, host_id: str) -> None:
        self._db_path = db_path
        self._subject_id = subject_id
        self._host_id = host_id
        self._lock = threading.Lock()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def subject_id(self) -> str:
        """Return the fixed profile subject bound at provider initialization."""

        return self._subject_id

    @property
    def host_id(self) -> str:
        """Return the fixed Hermes host bound at provider initialization."""

        return self._host_id

    def initialize(self) -> None:
        """Create the profile-scoped MemoWeft schema without storing Evidence."""

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        _assert_existing_database_is_current(self._db_path)
        db = open_db(str(self._db_path))
        db.close()

    def accept_durable_boundary(
        self, boundary: ValidatedHermesBoundary
    ) -> dict[str, object]:
        """Commit one already-validated boundary, its Evidence, and one Job.

        This intentionally delegates the sole write transaction to
        :class:`HermesBoundaryStore`.  Do not add an outer transaction here:
        the store's ``BEGIN IMMEDIATE`` is the atomic receipt boundary Hermes
        acknowledges.
        """

        with self._lock:
            db = open_db(str(self._db_path))
            try:
                return HermesBoundaryStore(db).accept(boundary).as_dict()
            finally:
                db.close()


class HermesMemoWeftRuntime:
    """Hermes-independent implementation behind the dynamic provider class."""

    def __init__(self) -> None:
        self._session_id = ""
        self._ingestor: _HermesBoundaryRuntimeStore | None = None
        self._world_worker: WorldJobWorker | None = None
        self._enabled = False
        self._last_recall_count = 0

    @property
    def db_path(self) -> Path | None:
        return self._ingestor.db_path if self._ingestor is not None else None

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        # Hermes initializes a provider once in normal operation.  Keep a
        # re-initialization bounded too, so an old profile worker cannot keep
        # processing after the runtime's destination changes.
        self.shutdown()
        self._ingestor = None
        hermes_home = Path(str(kwargs.get("hermes_home") or "."))
        platform = str(kwargs.get("platform") or "unknown")
        agent_context = str(kwargs.get("agent_context") or "primary")
        self._session_id = session_id
        self._enabled = agent_context == "primary"
        if not self._enabled:
            return

        settings = _load_provider_settings()
        configured_subject = settings.get("subject_id")
        external_user = kwargs.get("user_id_alt") or kwargs.get("user_id")
        identity_source = str(external_user or kwargs.get("agent_identity") or "local-user")
        subject_id = (
            configured_subject.strip()
            if isinstance(configured_subject, str) and configured_subject.strip()
            else "hermes-user-"
            + sha256(f"{platform}:{identity_source}".encode()).hexdigest()[:24]
        )
        db_path = hermes_home / "memoweft" / "memoweft.sqlite3"
        configured_path = settings.get("database_path")
        if isinstance(configured_path, str) and configured_path.strip():
            candidate = Path(configured_path.strip())
            db_path = candidate if candidate.is_absolute() else hermes_home / candidate

        self._ingestor = _HermesBoundaryRuntimeStore(
            db_path,
            subject_id=subject_id,
            host_id=f"hermes:{platform}",
        )
        self._ingestor.initialize()
        # The host may inject its own strict one-shot model route
        # (``one_shot_llm`` initialize kwarg).  With it, the V1 formal batch
        # adapter interprets each committed boundary with at most one physical
        # model request; without it, the production default stays the
        # deterministic model- and World-free no_change processor.
        route = kwargs.get("one_shot_llm")
        processor = None
        if callable(route):
            from .batch_adapter import HermesBatchAdapterProcessor

            processor = HermesBatchAdapterProcessor(
                str(db_path), route, lang=_optional_lang(kwargs.get("lang"))
            )
        self._world_worker = WorldJobWorker(db_path, processor=processor)
        # Recovery is asynchronous: provider initialization must not delay a
        # Hermes turn, and the default processor remains model- and World-free.
        self._world_worker.start()

    def ingest_durable_boundary(
        self, boundary: Mapping[str, object]
    ) -> dict[str, object]:
        """Consume one committed Hermes outbox envelope and return its receipt."""

        if not self._enabled or self._ingestor is None:
            raise RuntimeError("MemoWeft provider is not initialized for durable writes")
        unexpected_keys = set(boundary) - _BOUNDARY_KEYS
        if unexpected_keys:
            raise BoundaryEnvelopeError("Hermes boundary contains unsupported fields")
        schema_version = boundary.get("schema_version")
        event_id = boundary.get("event_id")
        provider_name = boundary.get("provider_name")
        parent_session_id = boundary.get("parent_session_id")
        result_session_id = boundary.get("result_session_id")
        mode = boundary.get("mode")
        source_messages = boundary.get("source_messages")
        payload_hash = boundary.get("payload_hash")
        if schema_version != 1:
            raise BoundaryEnvelopeError("Unsupported Hermes boundary schema version")
        if (
            not isinstance(event_id, str)
            or not event_id
            or event_id != event_id.strip()
            or len(event_id) > 255
        ):
            raise BoundaryEnvelopeError("Hermes boundary event_id is invalid")
        if provider_name != "memoweft":
            raise BoundaryEnvelopeError("Hermes boundary targets a different provider")
        if not isinstance(parent_session_id, str) or not parent_session_id.strip():
            raise BoundaryEnvelopeError("Hermes boundary parent_session_id must be non-empty")
        if not isinstance(result_session_id, str) or not result_session_id.strip():
            raise BoundaryEnvelopeError("Hermes boundary result_session_id must be non-empty")
        if mode not in _BOUNDARY_MODES:
            raise BoundaryEnvelopeError("Hermes boundary mode is unsupported")
        if mode == "in_place" and parent_session_id != result_session_id:
            raise BoundaryEnvelopeError(
                "Hermes in-place boundary must retain its parent session target"
            )
        if mode == "rotation" and parent_session_id == result_session_id:
            raise BoundaryEnvelopeError(
                "Hermes rotation boundary must target a distinct result session"
            )
        if (
            not isinstance(payload_hash, str)
            or len(payload_hash) != 64
            or any(char not in "0123456789abcdef" for char in payload_hash)
        ):
            raise BoundaryEnvelopeError("Hermes boundary payload_hash is invalid")
        if not isinstance(source_messages, list):
            raise BoundaryEnvelopeError("Hermes boundary source_messages must be a list")
        if not source_messages:
            raise BoundaryEnvelopeError("Hermes boundary source_messages must not be empty")
        normalized_messages: list[Mapping[str, object]] = []
        source_refs: set[str] = set()
        for message_index, message in enumerate(source_messages):
            if not isinstance(message, Mapping):
                raise BoundaryEnvelopeError(
                    "Hermes boundary source_messages must contain only mappings"
                )
            if set(message) - _BOUNDARY_MESSAGE_KEYS:
                raise BoundaryEnvelopeError(
                    "Hermes boundary source message contains unsupported fields"
                )
            role = message.get("role")
            content = message.get("content")
            source_ref = message.get("source_ref")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                raise BoundaryEnvelopeError(
                    "Hermes boundary source message must contain a clean role and text"
                )
            if (
                not isinstance(source_ref, str)
                or source_ref != f"source:{message_index}"
                or source_ref in source_refs
            ):
                raise BoundaryEnvelopeError(
                    "Hermes boundary source_ref must be a contiguous source ordinal"
                )
            source_refs.add(source_ref)
            for flag in ("observed", "synthetic"):
                if flag in message and not isinstance(message[flag], bool):
                    raise BoundaryEnvelopeError(
                        "Hermes boundary source flags must be boolean"
                    )
                if message.get(flag) is True:
                    raise BoundaryEnvelopeError(
                        "Hermes boundary cannot deliver observed or synthetic source rows"
                    )
            timestamp = message.get("timestamp")
            if timestamp is not None and (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, (int, float))
                or not math.isfinite(float(timestamp))
            ):
                raise BoundaryEnvelopeError(
                    "Hermes boundary timestamp must be a finite number"
                )
            if timestamp is not None and _occurred_at(timestamp) is None:
                raise BoundaryEnvelopeError(
                    "Hermes boundary timestamp is outside the supported UTC range"
                )
            for scalar_key in (
                "platform_message_id",
                "message_id",
                "display_kind",
            ):
                scalar = message.get(scalar_key)
                if scalar is None:
                    continue
                if not isinstance(scalar, (str, int, float, bool)) or (
                    isinstance(scalar, float) and not math.isfinite(scalar)
                ):
                    raise BoundaryEnvelopeError(
                        "Hermes boundary source identity fields must be finite scalars"
                    )
            normalized_messages.append(dict(message))

        try:
            calculated_payload_hash = _boundary_payload_hash(
                {
                    "schema_version": schema_version,
                    "provider_name": provider_name,
                    "parent_session_id": parent_session_id,
                    "result_session_id": result_session_id,
                    "mode": mode,
                    "source_messages": normalized_messages,
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BoundaryEnvelopeError(
                "Hermes boundary payload cannot be canonicalized"
            ) from exc
        if calculated_payload_hash != payload_hash:
            raise BoundaryEnvelopeError("Hermes boundary payload hash does not match")
        event_parts = event_id.split(":")
        if (
            len(event_parts) != 3
            or event_parts[0] != _BOUNDARY_EVENT_PREFIX
            or len(event_parts[1]) != 32
            or any(char not in "0123456789abcdef" for char in event_parts[1])
            or event_parts[2] != payload_hash
        ):
            raise BoundaryEnvelopeError(
                "Hermes boundary event_id does not bind its occurrence and payload hash"
            )

        candidates = _candidates(
            normalized_messages,
            session_id=parent_session_id,
            subject_id=self._ingestor.subject_id,
            host_id=self._ingestor.host_id,
            boundary_id=event_id,
        )
        accepted_boundary = ValidatedHermesBoundary(
            event_id=event_id,
            payload_hash=payload_hash,
            formal_target=HermesBoundaryFormalTarget(
                boundary_schema_version=schema_version,
                provider_name=provider_name,
                parent_session_id=parent_session_id,
                result_session_id=result_session_id,
                mode=mode,
                subject_id=self._ingestor.subject_id,
                host_id=self._ingestor.host_id,
            ),
            evidence=candidates,
        )
        receipt = self._ingestor.accept_durable_boundary(accepted_boundary)

        # A receipt proves the durable transaction already committed.  Waking
        # the asynchronous worker is best effort and must never turn that
        # committed delivery into a failed Hermes outbox acknowledgement.
        worker = self._world_worker
        if worker is not None:
            try:
                if not worker.kick():
                    logger.warning("MemoWeft World worker was unavailable after receipt")
            except Exception as exc:
                logger.warning(
                    "MemoWeft World worker wake failed: error_type=%s",
                    type(exc).__name__,
                )
        return receipt

    def on_session_switch(self, new_session_id: str) -> None:
        self._session_id = new_session_id

    @property
    def last_recall_count(self) -> int:
        return self._last_recall_count

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Deterministic read-only Recall over the accepted World.

        Zero generation-model calls, zero writes: a read-only connection
        selects only current, lifecycle-eligible claims of this subject and
        scores them in memory.  Any failure fails closed to an empty string —
        Recall must never break the Hermes turn.
        """
        del session_id
        self._last_recall_count = 0
        if not self._enabled or self._ingestor is None:
            return ""
        db_path = self._ingestor.db_path
        if db_path is None:
            return ""
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return ""
        try:
            text, count = recall_world_text(
                db, self._ingestor.subject_id, query
            )
            self._last_recall_count = count
            return text
        except sqlite3.Error:
            return ""
        finally:
            db.close()

    def shutdown(self) -> None:
        """Boundedly stop the local asynchronous World-job worker."""

        worker = self._world_worker
        self._world_worker = None
        if worker is not None:
            worker.shutdown()


def _load_provider_settings() -> dict[str, object]:
    """Read only MemoWeft's non-secret Hermes provider settings."""

    try:
        from hermes_cli.config import load_config  # type: ignore[import-not-found]

        config = load_config()
        memory = config.get("memory", {}) if isinstance(config, dict) else {}
        settings = memory.get("memoweft", {}) if isinstance(memory, dict) else {}
        return dict(settings) if isinstance(settings, dict) else {}
    except Exception:
        return {}


def _build_provider_class(base: type[Any]) -> type[Any]:
    """Create the real Hermes provider without importing Hermes at package import."""

    class MemoWeftMemoryProvider(base):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self._runtime = HermesMemoWeftRuntime()

        @property
        def name(self) -> str:
            return "memoweft"

        def is_available(self) -> bool:
            return True

        def initialize(self, session_id: str, **kwargs: Any) -> None:
            self._runtime.initialize(session_id, **kwargs)

        def system_prompt_block(self) -> str:
            return ""

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            return self._runtime.prefetch(query, session_id=session_id)

        def recall_status(self) -> Any:
            count = self._runtime.last_recall_count
            if count <= 0:
                return None
            # Resolve through the already-imported Hermes base class instead of
            # a second static ``agent.memory_provider`` import (keeps mypy's
            # single-import resolution and the package's lazy Hermes coupling).
            import importlib

            recall_status_cls = getattr(
                importlib.import_module(type(self).__mro__[1].__module__),
                "RecallStatus",
                None,
            )
            if recall_status_cls is None:
                return None
            return recall_status_cls(provider_label="memoweft", count=count)

        def get_tool_schemas(self) -> list[dict[str, Any]]:
            return []

        def on_durable_boundary(
            self, boundary: dict[str, Any]
        ) -> dict[str, object]:
            try:
                receipt = self._runtime.ingest_durable_boundary(boundary)
                logger.info(
                    "MemoWeft durable boundary committed: eligible=%d stored=%d "
                    "skipped=%d job_state=%s",
                    receipt["eligible"],
                    receipt["stored"],
                    receipt["skipped"],
                    receipt["job_state"],
                )
                return receipt
            except Exception as exc:
                # The Hermes outbox manager keeps the event pending for retry.
                # Never log the envelope or message content here.
                logger.warning(
                    "MemoWeft durable boundary failed: error_type=%s",
                    type(exc).__name__,
                )
                raise

        def shutdown(self) -> None:
            self._runtime.shutdown()

        def on_session_switch(
            self,
            new_session_id: str,
            *,
            parent_session_id: str = "",
            reset: bool = False,
            rewound: bool = False,
            **kwargs: Any,
        ) -> None:
            del parent_session_id, reset, rewound, kwargs
            self._runtime.on_session_switch(new_session_id)

        def backup_paths(self) -> list[str]:
            path = self._runtime.db_path
            return [str(path)] if path is not None else []

    MemoWeftMemoryProvider.__name__ = "MemoWeftMemoryProvider"
    MemoWeftMemoryProvider.__qualname__ = "MemoWeftMemoryProvider"
    return MemoWeftMemoryProvider


def register(ctx: Any) -> None:
    """Register MemoWeft with Hermes' memory-provider collector."""

    from agent.memory_provider import MemoryProvider  # type: ignore[import-not-found]

    provider_class = _build_provider_class(MemoryProvider)
    ctx.register_memory_provider(provider_class())


__all__ = [
    "BoundaryEnvelopeError",
    "HermesMemoWeftRuntime",
    "IncompatibleDatabaseError",
    "register",
]
