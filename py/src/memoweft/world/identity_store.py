"""SQLite persistence for the reviewed identity authority.

The canonical world graph continues to live in ``memory_state``.  This module
stores the complete identity ledger as one content-addressed row, while the
checkpoint's top-level graph is represented only by its identity graph hash.
Pending/review-envelope previews remain complete typed snapshots because they
are part of the immutable review record.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import sqlite3
from threading import RLock
from typing import Any, Callable, Mapping, TypeVar, cast

from ..store.schema import IDENTITY_SCHEMA_SQL
from ..types import EvidenceLink
from .delta import ClaimSpan
from .graph import MemoryWorldGraph
from .identity_review import (
    AcceptedIdentityBinding,
    BindingAssignment,
    EntityIdentityDelta,
    EntityReferenceLocator,
    EntityReferenceRewrite,
    IdentityAuthority,
    IdentityAuthorityState,
    IdentityAuthorityView,
    IdentityDecision,
    IdentityEvidence,
    IdentityGraphSnapshot,
    IdentityRedirect,
    IdentityResolutionContext,
    IdentityReviewValidationError,
    IdentityTombstone,
    IdentityTransition,
    PendingIdentityReview,
    ReviewDecision,
    SplitSuccessor,
    VerifiedReferenceMention,
)
from .model import (
    Entity,
    EventFacet,
    EventParticipant,
    MemoryTarget,
    PersonalWorld,
    Perspective,
    Relationship,
    StructuredClaim,
    WorldCognition,
    WorldEvent,
)


IDENTITY_SCHEMA_VERSION = 1
_CODEC_NAME = "memoweft.identity-authority-state.v1"
_HASH_PREFIX = "sha256:"
_IDENTITY_COLUMNS = (
    "singleton",
    "identity_schema_version",
    "world_id",
    "memory_revision",
    "memory_snapshot_hash",
    "identity_graph_hash",
    "state_json",
    "state_hash",
    "storage_generation",
)


class IdentityPersistenceError(RuntimeError):
    """Base class for stable identity-storage domain failures."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class IdentityPersistenceIntegrityError(IdentityPersistenceError):
    """Stored bytes or cross-store graph metadata failed validation."""


class IdentityPersistenceVersionError(IdentityPersistenceError):
    """The identity table or checkpoint codec has an unsupported version."""


class IdentityPersistenceConflictError(IdentityPersistenceError):
    """A compare-and-swap observed a newer identity or world generation."""


class IdentityPersistenceStateError(IdentityPersistenceError):
    """The shared connection is not in a usable transaction state."""


@dataclass(frozen=True, slots=True)
class ReviewedIdentityBinding:
    """One Owner-reviewed request to bind an exact user-evidence span.

    This deliberately contains only durable, model-independent coordinates.
    The MemoryLoop result hash includes its canonical JSON representation; the
    authoritative ``VerifiedReferenceMention`` is still issued inside the
    accepting SQLite transaction after the evidence record has been checked.
    """

    entity_id: str
    evidence_id: str
    conversation_id: str
    occurred_at: str
    start_codepoint: int
    end_codepoint: int
    kind_hint: str | None = None
    continuity_scope: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityStorageSnapshot:
    """One validated identity row detached from SQLite."""

    state: IdentityAuthorityState
    storage_generation: int
    state_hash: str
    memory_revision: int
    memory_snapshot_hash: str
    identity_graph_hash: str


@dataclass(frozen=True, slots=True)
class _MemoryState:
    graph: MemoryWorldGraph
    revision: int
    snapshot_json: str
    snapshot_hash: str


_ALLOWED_DATACLASSES: tuple[type[Any], ...] = (
    IdentityAuthorityState,
    IdentityEvidence,
    VerifiedReferenceMention,
    EntityReferenceLocator,
    EntityReferenceRewrite,
    BindingAssignment,
    SplitSuccessor,
    EntityIdentityDelta,
    IdentityRedirect,
    IdentityTombstone,
    AcceptedIdentityBinding,
    IdentityTransition,
    IdentityGraphSnapshot,
    PendingIdentityReview,
    IdentityDecision,
    ClaimSpan,
    PersonalWorld,
    Entity,
    Relationship,
    EventParticipant,
    EventFacet,
    WorldEvent,
    MemoryTarget,
    Perspective,
    StructuredClaim,
    WorldCognition,
    EvidenceLink,
)


def _type_tag(value_type: type[Any]) -> str:
    return f"{value_type.__module__}.{value_type.__qualname__}"


_DATACLASS_BY_TAG = {_type_tag(item): item for item in _ALLOWED_DATACLASSES}
_DATACLASS_TYPES = frozenset(_ALLOWED_DATACLASSES)


class SqliteIdentityStore:
    """Low-level single-row identity checkpoint store on a shared connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self._connection = connection
        for statement in IDENTITY_SCHEMA_SQL:
            self._connection.execute(statement)
        _validate_identity_schema_shape(self._connection)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def load(self) -> IdentityStorageSnapshot | None:
        """Load and validate the row against the current ``memory_state``."""

        memory = _load_memory_state(self._connection)
        row = self._select_row()
        return None if row is None else self._decode_row(row, memory)

    def bootstrap(self) -> IdentityStorageSnapshot:
        """Atomically initialize an empty authority, or return the existing row."""

        owns_transaction = not self._connection.in_transaction
        if owns_transaction:
            self._connection.execute("BEGIN IMMEDIATE")
        try:
            memory = _load_memory_state(self._connection)
            row = self._select_row()
            if row is None:
                state = IdentityAuthority(memory.graph).checkpoint()
                snapshot = self._insert_initial(state, memory)
            else:
                snapshot = self._decode_row(row, memory)
            if owns_transaction:
                self._connection.execute("COMMIT")
            return snapshot
        except Exception:
            if owns_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def _select_row(self) -> sqlite3.Row | tuple[Any, ...] | None:
        return cast(
            sqlite3.Row | tuple[Any, ...] | None,
            self._connection.execute(
                """SELECT identity_schema_version, world_id, memory_revision,
                          memory_snapshot_hash, identity_graph_hash, state_json,
                          state_hash, storage_generation
                   FROM identity_state WHERE singleton = 1"""
            ).fetchone(),
        )

    def _decode_row(
        self,
        row: sqlite3.Row | tuple[Any, ...],
        memory: _MemoryState,
    ) -> IdentityStorageSnapshot:
        version = _integer_column(row, 0, "identity_schema_version")
        if version != IDENTITY_SCHEMA_VERSION:
            raise IdentityPersistenceVersionError(
                "IDENTITY_SCHEMA_VERSION_UNSUPPORTED"
            )
        world_id = _text_column(row, 1, "world_id")
        memory_revision = _integer_column(row, 2, "memory_revision")
        memory_snapshot_hash = _text_column(row, 3, "memory_snapshot_hash")
        identity_graph_hash = _text_column(row, 4, "identity_graph_hash")
        state_json = _text_column(row, 5, "state_json")
        state_hash = _text_column(row, 6, "state_hash")
        generation = _integer_column(row, 7, "storage_generation")
        if generation < 1:
            raise IdentityPersistenceIntegrityError(
                "IDENTITY_STORAGE_GENERATION_INVALID"
            )
        if _hash_text(state_json) != state_hash:
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_HASH_MISMATCH")
        if memory_revision != memory.revision or memory_snapshot_hash != memory.snapshot_hash:
            raise IdentityPersistenceIntegrityError("IDENTITY_MEMORY_STATE_DIVERGED")
        current_snapshot = _identity_snapshot(memory.graph)
        if world_id != memory.graph.world.world_id:
            raise IdentityPersistenceIntegrityError("IDENTITY_WORLD_ID_MISMATCH")
        if identity_graph_hash != current_snapshot.graph_hash:
            raise IdentityPersistenceIntegrityError("IDENTITY_GRAPH_HASH_MISMATCH")
        state = _decode_checkpoint(state_json, current_snapshot)
        if (
            state.graph.graph_hash != identity_graph_hash
            or state.graph.world.world_id != world_id
        ):
            raise IdentityPersistenceIntegrityError("IDENTITY_GRAPH_REFERENCE_INVALID")
        try:
            IdentityAuthority.restore(state)
        except (IdentityReviewValidationError, TypeError, ValueError) as error:
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_INVALID") from error
        return IdentityStorageSnapshot(
            state=state,
            storage_generation=generation,
            state_hash=state_hash,
            memory_revision=memory_revision,
            memory_snapshot_hash=memory_snapshot_hash,
            identity_graph_hash=identity_graph_hash,
        )

    def _insert_initial(
        self,
        state: IdentityAuthorityState,
        memory: _MemoryState,
    ) -> IdentityStorageSnapshot:
        state_json = _encode_checkpoint(state)
        state_hash = _hash_text(state_json)
        try:
            self._connection.execute(
                """INSERT INTO identity_state(
                       singleton, identity_schema_version, world_id,
                       memory_revision, memory_snapshot_hash, identity_graph_hash,
                       state_json, state_hash, storage_generation
                   ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    IDENTITY_SCHEMA_VERSION,
                    state.graph.world.world_id,
                    memory.revision,
                    memory.snapshot_hash,
                    state.graph.graph_hash,
                    state_json,
                    state_hash,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise IdentityPersistenceConflictError("IDENTITY_STORAGE_CONFLICT") from error
        return IdentityStorageSnapshot(
            state,
            1,
            state_hash,
            memory.revision,
            memory.snapshot_hash,
            state.graph.graph_hash,
        )

    def _compare_and_swap(
        self,
        expected: IdentityStorageSnapshot,
        state: IdentityAuthorityState,
        *,
        memory_revision: int,
        memory_snapshot_hash: str,
    ) -> IdentityStorageSnapshot:
        state_json = _encode_checkpoint(state)
        state_hash = _hash_text(state_json)
        next_generation = expected.storage_generation + 1
        cursor = self._connection.execute(
            """UPDATE identity_state
               SET world_id = ?, memory_revision = ?, memory_snapshot_hash = ?,
                   identity_graph_hash = ?, state_json = ?, state_hash = ?,
                   storage_generation = ?
               WHERE singleton = 1
                 AND identity_schema_version = ?
                 AND storage_generation = ?
                 AND state_hash = ?
                 AND memory_revision = ?
                 AND memory_snapshot_hash = ?
                 AND identity_graph_hash = ?""",
            (
                state.graph.world.world_id,
                memory_revision,
                memory_snapshot_hash,
                state.graph.graph_hash,
                state_json,
                state_hash,
                next_generation,
                IDENTITY_SCHEMA_VERSION,
                expected.storage_generation,
                expected.state_hash,
                expected.memory_revision,
                expected.memory_snapshot_hash,
                expected.identity_graph_hash,
            ),
        )
        if cursor.rowcount != 1:
            raise IdentityPersistenceConflictError("IDENTITY_STORAGE_CONFLICT")
        return IdentityStorageSnapshot(
            state,
            next_generation,
            state_hash,
            memory_revision,
            memory_snapshot_hash,
            state.graph.graph_hash,
        )


_ResultT = TypeVar("_ResultT")


class PersistentIdentityAuthority:
    """Transactional façade matching the in-memory ``IdentityAuthority`` API."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            raise IdentityPersistenceStateError(
                "IDENTITY_TRANSACTION_ALREADY_ACTIVE"
            )
        self._lock = RLock()
        self._store = SqliteIdentityStore(connection)
        stored = self._store.bootstrap()
        self._authority = IdentityAuthority.restore(stored.state)
        self._stored = stored
        self._context_issuers: list[IdentityAuthority] = []

    @property
    def connection(self) -> sqlite3.Connection:
        return self._store.connection

    @property
    def storage_generation(self) -> int:
        with self._lock:
            self._prepare_read_unlocked()
            return self._stored.storage_generation

    def reload(self) -> IdentityAuthorityView:
        """Refresh this façade after an external same-database world commit."""

        with self._lock:
            if self.connection.in_transaction:
                raise IdentityPersistenceStateError(
                    "IDENTITY_TRANSACTION_ALREADY_ACTIVE"
                )
            self._refresh_unlocked(force=True)
            return self._authority.view()

    def checkpoint(self) -> IdentityAuthorityState:
        with self._lock:
            self._prepare_read_unlocked()
            return self._authority.checkpoint()

    def register_evidence(self, evidence: IdentityEvidence) -> IdentityEvidence:
        return self._mutate(lambda authority: authority.register_evidence(evidence))

    def append_evidence(self, evidence: IdentityEvidence) -> IdentityEvidence:
        return self.register_evidence(evidence)

    def issue_verified_mention(
        self,
        evidence_id: str,
        start_codepoint: int,
        end_codepoint: int,
        *,
        kind_hint: str | None = None,
        continuity_scope: str | None = None,
    ) -> VerifiedReferenceMention:
        return self._mutate(
            lambda authority: authority.issue_verified_mention(
                evidence_id,
                start_codepoint,
                end_codepoint,
                kind_hint=kind_hint,
                continuity_scope=continuity_scope,
            )
        )

    def stage(
        self,
        delta: EntityIdentityDelta,
        review_payload: Any,
    ) -> PendingIdentityReview:
        return self._mutate(lambda authority: authority.stage(delta, review_payload))

    def decide(
        self,
        review_id: str,
        result_hash: str,
        decision: ReviewDecision,
        decided_at: str,
    ) -> IdentityDecision:
        return self._mutate(
            lambda authority: authority.decide(
                review_id,
                result_hash,
                decision,
                decided_at,
            )
        )

    def view(self) -> IdentityAuthorityView:
        with self._lock:
            self._prepare_read_unlocked()
            return self._authority.view()

    def resolution_context(
        self,
        current_mention: VerifiedReferenceMention,
    ) -> IdentityResolutionContext:
        with self._lock:
            self._prepare_read_unlocked()
            context = self._authority.resolution_context(current_mention)
            if all(
                issuer is not self._authority for issuer in self._context_issuers
            ):
                self._context_issuers.append(self._authority)
            return context

    def _mutate(
        self,
        operation: Callable[[IdentityAuthority], _ResultT],
    ) -> _ResultT:
        with self._lock:
            if self.connection.in_transaction:
                raise IdentityPersistenceStateError(
                    "IDENTITY_TRANSACTION_ALREADY_ACTIVE"
                )
            self._refresh_unlocked(force=False)
            candidate = IdentityAuthority.restore(self._authority.checkpoint())
            result = operation(candidate)
            candidate_state = candidate.checkpoint()
            live_state = self._authority.checkpoint()
            graph_changed = (
                candidate_state.graph.graph_hash != live_state.graph.graph_hash
            )
            began = False
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                began = True
                memory = _load_memory_state(self.connection)
                if (
                    memory.revision != self._stored.memory_revision
                    or memory.snapshot_hash != self._stored.memory_snapshot_hash
                    or _identity_snapshot(memory.graph).graph_hash
                    != live_state.graph.graph_hash
                ):
                    raise IdentityPersistenceConflictError(
                        "IDENTITY_MEMORY_CONFLICT"
                    )
                memory_revision = memory.revision
                memory_snapshot_hash = memory.snapshot_hash
                if graph_changed:
                    next_graph = candidate_state.graph.to_graph()
                    snapshot_json = _memory_graph_json(next_graph)
                    snapshot_hash = _hash_text(snapshot_json)
                    memory_revision += 1
                    cursor = self.connection.execute(
                        """UPDATE memory_state
                           SET revision = ?, snapshot_json = ?, snapshot_hash = ?
                           WHERE singleton = 1 AND revision = ? AND snapshot_hash = ?""",
                        (
                            memory_revision,
                            snapshot_json,
                            snapshot_hash,
                            memory.revision,
                            memory.snapshot_hash,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise IdentityPersistenceConflictError(
                            "IDENTITY_MEMORY_CONFLICT"
                        )
                    memory_snapshot_hash = snapshot_hash
                stored = self._store._compare_and_swap(
                    self._stored,
                    candidate_state,
                    memory_revision=memory_revision,
                    memory_snapshot_hash=memory_snapshot_hash,
                )
                self.connection.execute("COMMIT")
                began = False
            except Exception:
                if began and self.connection.in_transaction:
                    self.connection.execute("ROLLBACK")
                raise
            if _context_state_changed(live_state, candidate_state):
                self._retire_issued_contexts_unlocked()
            self._authority = candidate
            self._stored = stored
            return result

    def _prepare_read_unlocked(self) -> None:
        if self.connection.in_transaction:
            raise IdentityPersistenceStateError(
                "IDENTITY_TRANSACTION_ALREADY_ACTIVE"
            )
        self._refresh_unlocked(force=False)

    def _refresh_unlocked(self, *, force: bool) -> None:
        stored = self._store.load()
        if stored is None:
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_MISSING")
        if (
            force
            or stored.storage_generation != self._stored.storage_generation
            or stored.state_hash != self._stored.state_hash
        ):
            authority = IdentityAuthority.restore(stored.state)
            prior_state = self._authority.checkpoint()
            if _context_state_changed(prior_state, stored.state):
                self._retire_issued_contexts_unlocked()
            self._authority = authority
            self._stored = stored

    def _retire_issued_contexts_unlocked(self) -> None:
        for issuer in self._context_issuers:
            _retire_context_issuer(issuer)
        self._context_issuers.clear()


def sync_identity_graph(
    connection: sqlite3.Connection,
    old_graph: MemoryWorldGraph,
    new_graph: MemoryWorldGraph,
    expected_memory_revision: int,
    new_memory_revision: int,
    new_snapshot_hash: str,
) -> None:
    """Rebind a stored identity ledger to a world update in the same transaction.

    ``MemoryLoop`` calls this after updating ``memory_state`` but before commit.
    If no identity row has been bootstrapped yet, the call is intentionally a
    no-op; the first persistent identity façade will initialize from the then
    current world.  With no outer transaction this helper owns one transaction.
    """

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'identity_state'"
    ).fetchone()
    if table is None:
        return
    _validate_identity_schema_shape(connection)
    row = cast(
        sqlite3.Row | tuple[Any, ...] | None,
        connection.execute(
            """SELECT identity_schema_version, world_id, memory_revision,
                      memory_snapshot_hash, identity_graph_hash, state_json,
                      state_hash, storage_generation
               FROM identity_state WHERE singleton = 1"""
        ).fetchone(),
    )
    if row is None:
        return
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        # Re-read after obtaining the write lock when this helper owns it.
        if owns_transaction:
            row = cast(
                sqlite3.Row | tuple[Any, ...] | None,
                connection.execute(
                    """SELECT identity_schema_version, world_id, memory_revision,
                              memory_snapshot_hash, identity_graph_hash, state_json,
                              state_hash, storage_generation
                       FROM identity_state WHERE singleton = 1"""
                ).fetchone(),
            )
            if row is None:
                connection.execute("COMMIT")
                return
        memory = _load_memory_state(connection)
        expected_snapshot_json = _memory_graph_json(new_graph)
        if (
            expected_memory_revision < 0
            or new_memory_revision < 0
            or new_memory_revision != expected_memory_revision + 1
            or memory.revision != new_memory_revision
            or memory.snapshot_hash != new_snapshot_hash
            or _hash_text(expected_snapshot_json) != new_snapshot_hash
        ):
            raise IdentityPersistenceConflictError("IDENTITY_MEMORY_CONFLICT")
        old_snapshot = _identity_snapshot(old_graph)
        old_memory = _MemoryState(
            graph=old_graph,
            revision=expected_memory_revision,
            snapshot_json="",
            snapshot_hash=_text_column(row, 3, "memory_snapshot_hash"),
        )
        store = object.__new__(SqliteIdentityStore)
        store._connection = connection
        stored = store._decode_row(row, old_memory)
        authority = IdentityAuthority.restore(stored.state)
        next_state = authority.checkpoint_with_graph(new_graph)
        store._compare_and_swap(
            stored,
            next_state,
            memory_revision=new_memory_revision,
            memory_snapshot_hash=new_snapshot_hash,
        )
        if stored.identity_graph_hash != old_snapshot.graph_hash:
            raise IdentityPersistenceConflictError("IDENTITY_GRAPH_CONFLICT")
        if owns_transaction:
            connection.execute("COMMIT")
    except Exception:
        if owns_transaction and connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def accept_reviewed_identity_bindings(
    connection: sqlite3.Connection,
    bindings: tuple[ReviewedIdentityBinding, ...],
    evidence_records: Mapping[str, tuple[str, Mapping[str, object] | None]],
    *,
    memory_review_id: str,
    memory_result_hash: str,
    memory_revision: int,
    memory_snapshot_hash: str,
    decided_at: str | None = None,
) -> None:
    """Commit reviewed mention bindings within a caller-owned world transaction.

    ``MemoryLoop.decide`` calls this only after it has applied a reviewed world
    delta and rebound the identity graph with :func:`sync_identity_graph`.  The
    helper intentionally refuses to open, commit, or roll back a transaction:
    failure therefore rolls back the world snapshot, evidence ledger, proposal
    status, and identity CAS as one SQLite unit.

    A first binding may arrive before any public identity façade has been
    instantiated.  In that case ``bootstrap`` creates the identity checkpoint
    from the already-updated in-transaction world, so there is never a durable
    world entity without its accepted source binding after this function
    returns.
    """

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    if not connection.in_transaction:
        raise IdentityPersistenceStateError("IDENTITY_TRANSACTION_REQUIRED")
    if not bindings:
        return
    if not isinstance(memory_review_id, str) or not memory_review_id.strip():
        raise ValueError("memory_review_id must be non-empty")
    if not isinstance(memory_result_hash, str) or not memory_result_hash.strip():
        raise ValueError("memory_result_hash must be non-empty")
    if type(memory_revision) is not int or memory_revision < 0:
        raise ValueError("memory_revision must be a non-negative int")
    if not isinstance(memory_snapshot_hash, str) or not memory_snapshot_hash.strip():
        raise ValueError("memory_snapshot_hash must be non-empty")

    memory = _load_memory_state(connection)
    if (
        memory.revision != memory_revision
        or memory.snapshot_hash != memory_snapshot_hash
    ):
        raise IdentityPersistenceConflictError("IDENTITY_MEMORY_CONFLICT")

    store = SqliteIdentityStore(connection)
    stored = store.bootstrap()
    authority = IdentityAuthority.restore(stored.state)
    if (
        stored.memory_revision != memory_revision
        or stored.memory_snapshot_hash != memory_snapshot_hash
        or stored.identity_graph_hash != _identity_snapshot(memory.graph).graph_hash
    ):
        raise IdentityPersistenceConflictError("IDENTITY_MEMORY_CONFLICT")

    requested_atoms: set[tuple[str, int, int, str]] = set()
    decision_time = _binding_decision_time(
        bindings,
        decided_at,
        tuple(item.decided_at for item in authority.view().decisions),
    )
    for binding in bindings:
        _validate_reviewed_binding(binding, memory.graph, evidence_records)
        atom_key = (
            binding.evidence_id,
            binding.start_codepoint,
            binding.end_codepoint,
            binding.entity_id,
        )
        if atom_key in requested_atoms:
            raise ValueError("identity binding request is duplicated")
        requested_atoms.add(atom_key)
        content, _ = evidence_records[binding.evidence_id]
        identity_evidence = IdentityEvidence(
            binding.evidence_id,
            memory.graph.world.world_id,
            binding.conversation_id,
            binding.occurred_at,
            "user",
            content,
            binding.continuity_scope,
        )
        authority.register_evidence(identity_evidence)
        mention = authority.issue_verified_mention(
            binding.evidence_id,
            binding.start_codepoint,
            binding.end_codepoint,
            kind_hint=binding.kind_hint,
            continuity_scope=binding.continuity_scope,
        )
        pending = authority.stage(
            EntityIdentityDelta.bind(memory.graph.world.world_id, binding.entity_id, mention),
            {
                "memory_review_id": memory_review_id,
                "memory_result_hash": memory_result_hash,
                "binding": _reviewed_binding_data(binding),
            },
        )
        authority.decide(pending.review_id, pending.result_hash, "accept", decision_time)

    store._compare_and_swap(
        stored,
        authority.checkpoint(),
        memory_revision=memory_revision,
        memory_snapshot_hash=memory_snapshot_hash,
    )


def _validate_reviewed_binding(
    binding: ReviewedIdentityBinding,
    graph: MemoryWorldGraph,
    evidence_records: Mapping[str, tuple[str, Mapping[str, object] | None]],
) -> None:
    if not isinstance(binding, ReviewedIdentityBinding):
        raise TypeError("identity bindings must be ReviewedIdentityBinding values")
    for value, name in (
        (binding.entity_id, "entity_id"),
        (binding.evidence_id, "evidence_id"),
        (binding.conversation_id, "conversation_id"),
        (binding.occurred_at, "occurred_at"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"identity binding {name} must be non-empty")
    if binding.entity_id not in graph.entities:
        raise ValueError("identity binding target entity is not in accepted preview")
    try:
        occurred = datetime.fromisoformat(binding.occurred_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("identity binding occurred_at must be an ISO timestamp") from error
    if occurred.tzinfo is None:
        raise ValueError("identity binding occurred_at must include a timezone")
    if (
        type(binding.start_codepoint) is not int
        or type(binding.end_codepoint) is not int
        or binding.start_codepoint < 0
        or binding.end_codepoint <= binding.start_codepoint
    ):
        raise ValueError("identity binding span is invalid")
    if binding.kind_hint is not None and (
        not isinstance(binding.kind_hint, str) or not binding.kind_hint.strip()
    ):
        raise ValueError("identity binding kind_hint is invalid")
    if binding.continuity_scope is not None and (
        not isinstance(binding.continuity_scope, str)
        or not binding.continuity_scope.strip()
    ):
        raise ValueError("identity binding continuity_scope is invalid")
    record = evidence_records.get(binding.evidence_id)
    if record is None:
        raise ValueError("identity binding evidence is not in the reviewed proposal")
    content, metadata = record
    if (
        not isinstance(content, str)
        or binding.end_codepoint > len(content)
        or not content[binding.start_codepoint : binding.end_codepoint].strip()
    ):
        raise ValueError("identity binding evidence span is invalid")
    if not isinstance(metadata, Mapping):
        raise ValueError("identity binding evidence metadata is missing")
    if metadata.get("conversation_id") != binding.conversation_id:
        raise ValueError("identity binding conversation does not match evidence")
    if metadata.get("occurred_at") != binding.occurred_at:
        raise ValueError("identity binding occurrence does not match evidence")
    if metadata.get("continuity_scope") != binding.continuity_scope:
        raise ValueError("identity binding continuity scope does not match evidence")


def _binding_decision_time(
    bindings: tuple[ReviewedIdentityBinding, ...],
    requested: str | None,
    prior_decisions: tuple[str, ...],
) -> str:
    if requested is None:
        latest = datetime.now(timezone.utc)
    else:
        if not isinstance(requested, str) or not requested.strip():
            raise ValueError("decided_at must be a non-empty ISO timestamp")
        try:
            latest = datetime.fromisoformat(requested.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("decided_at must be an ISO timestamp") from error
        if latest.tzinfo is None:
            raise ValueError("decided_at must include a timezone")
        latest = latest.astimezone(timezone.utc)
    for binding in bindings:
        try:
            value = datetime.fromisoformat(binding.occurred_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("identity binding occurred_at must be an ISO timestamp") from error
        if value.tzinfo is None:
            raise ValueError("identity binding occurred_at must include a timezone")
        latest = max(latest, value.astimezone(timezone.utc) + timedelta(microseconds=1))
    for prior in prior_decisions:
        try:
            value = datetime.fromisoformat(prior.replace("Z", "+00:00"))
        except ValueError as error:
            raise IdentityPersistenceIntegrityError(
                "IDENTITY_STATE_DECISION_TIME_INVALID"
            ) from error
        if value.tzinfo is None:
            raise IdentityPersistenceIntegrityError(
                "IDENTITY_STATE_DECISION_TIME_INVALID"
            )
        latest = max(latest, value.astimezone(timezone.utc) + timedelta(microseconds=1))
    return latest.isoformat()


def _reviewed_binding_data(binding: ReviewedIdentityBinding) -> dict[str, object]:
    return {
        "entity_id": binding.entity_id,
        "evidence_id": binding.evidence_id,
        "conversation_id": binding.conversation_id,
        "occurred_at": binding.occurred_at,
        "start_codepoint": binding.start_codepoint,
        "end_codepoint": binding.end_codepoint,
        "kind_hint": binding.kind_hint,
        "continuity_scope": binding.continuity_scope,
    }


def _load_memory_state(connection: sqlite3.Connection) -> _MemoryState:
    try:
        row = cast(
            sqlite3.Row | tuple[Any, ...] | None,
            connection.execute(
                """SELECT revision, snapshot_json, snapshot_hash
                   FROM memory_state WHERE singleton = 1"""
            ).fetchone(),
        )
    except sqlite3.OperationalError as error:
        raise IdentityPersistenceIntegrityError("MEMORY_STATE_MISSING") from error
    if row is None:
        raise IdentityPersistenceIntegrityError("MEMORY_STATE_MISSING")
    revision = _integer_column(row, 0, "revision")
    snapshot_json = _text_column(row, 1, "snapshot_json")
    snapshot_hash = _text_column(row, 2, "snapshot_hash")
    if revision < 0:
        raise IdentityPersistenceIntegrityError("MEMORY_REVISION_INVALID")
    if _hash_text(snapshot_json) != snapshot_hash:
        raise IdentityPersistenceIntegrityError("MEMORY_SNAPSHOT_HASH_MISMATCH")
    try:
        raw = _load_json(snapshot_json)
        if not isinstance(raw, dict):
            raise TypeError("memory snapshot root")
        graph = _memory_graph_from_data(cast(Mapping[str, object], raw))
        graph.validate_owner()
    except (KeyError, TypeError, ValueError) as error:
        raise IdentityPersistenceIntegrityError("MEMORY_SNAPSHOT_INVALID") from error
    return _MemoryState(graph, revision, snapshot_json, snapshot_hash)


def _validate_identity_schema_shape(connection: sqlite3.Connection) -> None:
    rows = tuple(connection.execute("PRAGMA table_info(identity_state)"))
    columns = tuple(cast(str, row[1]) for row in rows)
    if columns != _IDENTITY_COLUMNS:
        raise IdentityPersistenceVersionError("IDENTITY_SCHEMA_SHAPE_UNSUPPORTED")


def _identity_snapshot(graph: MemoryWorldGraph) -> IdentityGraphSnapshot:
    try:
        return IdentityAuthority(graph).view().graph
    except (IdentityReviewValidationError, TypeError, ValueError) as error:
        raise IdentityPersistenceIntegrityError("IDENTITY_GRAPH_INVALID") from error


def _context_state_changed(
    before: IdentityAuthorityState,
    after: IdentityAuthorityState,
) -> bool:
    return (
        before.revision != after.revision
        or before.graph.graph_hash != after.graph.graph_hash
        or before.bindings != after.bindings
    )


def _retire_context_issuer(authority: IdentityAuthority) -> None:
    # Contexts intentionally retain their issuing authority.  The persistent
    # façade swaps in a restored candidate only after COMMIT, so retire the old
    # issuer seal when accepted resolution state changed; otherwise a context
    # could remain valid forever against the detached pre-commit object.
    authority._authority_id = "retired:" + authority._authority_id  # noqa: SLF001


def _memory_graph_json(graph: MemoryWorldGraph) -> str:
    # Lazy imports keep ``MemoryLoop`` free to import ``sync_identity_graph``
    # without a module-initialization cycle.
    from .loop import _graph_json

    return _graph_json(graph)


def _memory_graph_from_data(data: Mapping[str, object]) -> MemoryWorldGraph:
    from .loop import _graph_from_data

    return _graph_from_data(data)


def _encode_checkpoint(state: IdentityAuthorityState) -> str:
    if not isinstance(state, IdentityAuthorityState):
        raise IdentityPersistenceIntegrityError("IDENTITY_STATE_INVALID")
    graph = state.graph
    actual = _identity_snapshot(graph.to_graph())
    if actual.graph_hash != graph.graph_hash:
        raise IdentityPersistenceIntegrityError("IDENTITY_GRAPH_REFERENCE_INVALID")
    state_fields: dict[str, object] = {}
    for item in fields(IdentityAuthorityState):
        value = getattr(state, item.name)
        state_fields[item.name] = (
            {
                "$type": "graph-ref",
                "graph_hash": graph.graph_hash,
            }
            if item.name == "graph"
            else _encode_value(value)
        )
    payload = {
        "codec": _CODEC_NAME,
        "state": {
            "$type": _type_tag(IdentityAuthorityState),
            "fields": state_fields,
        },
    }
    return _canonical_json(payload)


def _decode_checkpoint(
    state_json: str,
    current_graph: IdentityGraphSnapshot,
) -> IdentityAuthorityState:
    try:
        raw = _load_json(state_json)
        if _canonical_json(raw) != state_json:
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_NOT_CANONICAL")
        if not isinstance(raw, dict) or set(raw) != {"codec", "state"}:
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_INVALID")
        if raw["codec"] != _CODEC_NAME:
            raise IdentityPersistenceVersionError("IDENTITY_CODEC_VERSION_UNSUPPORTED")
        value = _decode_value(raw["state"], current_graph)
        if not isinstance(value, IdentityAuthorityState):
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_INVALID")
        if _encode_checkpoint(value) != state_json:
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_NOT_CANONICAL")
        return value
    except IdentityPersistenceError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise IdentityPersistenceIntegrityError("IDENTITY_STATE_INVALID") from error


def _encode_value(value: object) -> object:
    value_type = type(value)
    if value is None or value_type in (str, bool, int):
        return value
    if value_type is float:
        if not math.isfinite(cast(float, value)):
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_VALUE_INVALID")
        return value
    if is_dataclass(value) and value_type in _DATACLASS_TYPES:
        return {
            "$type": _type_tag(value_type),
            "fields": {
                item.name: _encode_value(getattr(value, item.name))
                for item in fields(value)
            },
        }
    if value_type is tuple:
        return {"$type": "tuple", "items": [_encode_value(item) for item in cast(tuple[object, ...], value)]}
    if value_type is list:
        return {"$type": "list", "items": [_encode_value(item) for item in cast(list[object], value)]}
    if value_type is frozenset:
        encoded = [_encode_value(item) for item in cast(frozenset[object], value)]
        encoded.sort(key=_canonical_json)
        return {"$type": "frozenset", "items": encoded}
    if isinstance(value, Mapping):
        items: list[list[object]] = []
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if not isinstance(key, str):
                raise IdentityPersistenceIntegrityError("IDENTITY_STATE_VALUE_INVALID")
            items.append([key, _encode_value(item)])
        return {"$type": "mapping", "items": items}
    raise IdentityPersistenceIntegrityError("IDENTITY_STATE_TYPE_NOT_ALLOWED")


def _decode_value(value: object, current_graph: IdentityGraphSnapshot) -> object:
    value_type = type(value)
    if value is None or value_type in (str, bool, int):
        return value
    if value_type is float:
        if not math.isfinite(cast(float, value)):
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_VALUE_INVALID")
        return value
    if not isinstance(value, dict):
        raise IdentityPersistenceIntegrityError("IDENTITY_STATE_VALUE_INVALID")
    tag = value.get("$type")
    if not isinstance(tag, str):
        raise IdentityPersistenceIntegrityError("IDENTITY_STATE_TYPE_NOT_ALLOWED")
    if tag == "graph-ref":
        if set(value) != {"$type", "graph_hash"}:
            raise IdentityPersistenceIntegrityError("IDENTITY_GRAPH_REFERENCE_INVALID")
        if value["graph_hash"] != current_graph.graph_hash:
            raise IdentityPersistenceIntegrityError("IDENTITY_GRAPH_HASH_MISMATCH")
        return deepcopy(current_graph)
    if tag in ("tuple", "list", "frozenset"):
        if set(value) != {"$type", "items"} or not isinstance(value["items"], list):
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_VALUE_INVALID")
        items = tuple(_decode_value(item, current_graph) for item in value["items"])
        if tag == "tuple":
            return items
        if tag == "list":
            return list(items)
        return frozenset(items)
    if tag == "mapping":
        if set(value) != {"$type", "items"} or not isinstance(value["items"], list):
            raise IdentityPersistenceIntegrityError("IDENTITY_STATE_VALUE_INVALID")
        result: dict[str, object] = {}
        for item in value["items"]:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or item[0] in result
            ):
                raise IdentityPersistenceIntegrityError("IDENTITY_STATE_VALUE_INVALID")
            result[item[0]] = _decode_value(item[1], current_graph)
        return result
    data_type = _DATACLASS_BY_TAG.get(tag)
    if data_type is None:
        raise IdentityPersistenceIntegrityError("IDENTITY_STATE_TYPE_NOT_ALLOWED")
    if set(value) != {"$type", "fields"} or not isinstance(value["fields"], dict):
        raise IdentityPersistenceIntegrityError("IDENTITY_STATE_VALUE_INVALID")
    raw_fields = value["fields"]
    expected_fields = tuple(item.name for item in fields(data_type))
    if set(raw_fields) != set(expected_fields):
        raise IdentityPersistenceIntegrityError("IDENTITY_STATE_FIELDS_INVALID")
    instance = object.__new__(data_type)
    for name in expected_fields:
        object.__setattr__(
            instance,
            name,
            _decode_value(raw_fields[name], current_graph),
        )
    return instance


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise IdentityPersistenceIntegrityError("IDENTITY_STATE_VALUE_INVALID") from error


def _load_json(value: str) -> object:
    try:
        return json.loads(value, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as error:
        raise IdentityPersistenceIntegrityError("IDENTITY_JSON_INVALID") from error


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _hash_text(value: str) -> str:
    return _HASH_PREFIX + sha256(value.encode("utf-8")).hexdigest()


def _column(
    row: sqlite3.Row | tuple[Any, ...],
    index: int,
    name: str,
) -> object:
    if isinstance(row, sqlite3.Row):
        return row[name]
    return row[index]


def _text_column(
    row: sqlite3.Row | tuple[Any, ...],
    index: int,
    name: str,
) -> str:
    value = _column(row, index, name)
    if not isinstance(value, str):
        raise IdentityPersistenceIntegrityError("IDENTITY_ROW_INVALID")
    return value


def _integer_column(
    row: sqlite3.Row | tuple[Any, ...],
    index: int,
    name: str,
) -> int:
    value = _column(row, index, name)
    if type(value) is not int:
        raise IdentityPersistenceIntegrityError("IDENTITY_ROW_INVALID")
    return value


__all__ = [
    "IDENTITY_SCHEMA_VERSION",
    "IdentityPersistenceConflictError",
    "IdentityPersistenceError",
    "IdentityPersistenceIntegrityError",
    "IdentityPersistenceStateError",
    "IdentityPersistenceVersionError",
    "IdentityStorageSnapshot",
    "PersistentIdentityAuthority",
    "ReviewedIdentityBinding",
    "SqliteIdentityStore",
    "accept_reviewed_identity_bindings",
    "sync_identity_graph",
]
