"""A small, deliberately deep persistent memory loop for MemoWeft Next.

The module uses the MemoWeft SQLite database.  It stores the canonical *whole world*
snapshot, an append-only evidence ledger, reviewable proposals, and cognition
transitions.  This is intentionally not a repository framework: the point of
the first loop is to make the durable boundary and its failure behaviour easy
to reason about and easy to throw away later.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence, cast
from uuid import uuid4

from ..confidence import compute_confidence, derive_cred_status
from ..llm import ChatMessage, LLMClient
from ..store.driver import open_db
from ..store.schema import MEMORY_LOOP_SCHEMA_SQL
from ..types import ConfidenceInputs, EvidenceLink
from .delta import WorldDelta
from .evolution import (
    AcceptedEvolutionStep,
    WorldEvolutionPlan,
    WorldEvolutionValidationError,
    evolution_plan_from_data,
    evolution_plan_to_data,
    superseding_cognition_pairs,
)
from .graph import MemoryWorldGraph
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
from .recall import (
    CognitionLineage,
    MemoryReconstruction,
    parse_memory_query,
    reconstruct_memory,
    render_answer_context,
)

if TYPE_CHECKING:
    from .identity_store import ReviewedIdentityBinding


Decision = Literal["accept", "reject"]
ReviewKind = Literal["addition", "correction", "evolution", "product_bundle"]
_PRODUCT_TRANSITION_REASONS = frozenset({"corrects", "narrows", "supersedes"})


class MemoryLoopError(RuntimeError):
    """Base error for a safe loop rejection."""


class MemoryLoopIntegrityError(MemoryLoopError):
    """Stored bytes do not match their canonical snapshot hash."""


class ReviewStateError(MemoryLoopError):
    """A review is absent, stale, already decided, or has the wrong hash."""


class EvidenceConflictError(MemoryLoopError):
    """An evidence id was reused with different user content."""


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    id: str
    content: str
    role: Literal["user"] = "user"
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class PendingReview:
    id: str
    kind: ReviewKind
    result_hash: str
    base_revision: int
    review_payload: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class CognitionTransition:
    id: str
    prior_cognition_id: str
    replacement_cognition_id: str
    reason: str
    revision: int


@dataclass(frozen=True, slots=True)
class CognitionTransitionIntent:
    """One reviewed product-bundle replacement edge.

    The adapter supplies only IDs plus the already-validated statement kind;
    this storage boundary revalidates both endpoints against the accepted world
    and the bundle delta before it writes a durable transition.
    """

    prior_cognition_id: str
    successor_cognition_id: str
    reason: str
    statement_kind: str


@dataclass(frozen=True, slots=True)
class ProductClaimSlice:
    """The reviewed records a single offered claim is allowed to own.

    Record IDs refer only to objects newly created by the enclosing
    ``WorldDelta``.  Shared focal entities and required mention bindings are
    declared separately at bundle staging, so a subset cannot silently acquire
    an unrelated record from a neighbouring claim.
    """

    claim_id: str
    entity_ids: tuple[str, ...] = ()
    relationship_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    cognition_ids: tuple[str, ...] = ()
    identity_binding_indices: tuple[int, ...] = ()
    transition_intent_indices: tuple[int, ...] = ()
    depends_on_claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductBundleDecisionReceipt:
    """SQLite-persisted account of an Owner's claim-level choice."""

    review_id: str
    offered_result_hash: str
    decision: Decision
    selected_claim_ids: tuple[str, ...]
    applied_claim_ids: tuple[str, ...]
    final_hash: str


@dataclass(frozen=True, slots=True)
class _ResolvedProductBundleSelection:
    effective_decision: Decision
    accepted_payload: Mapping[str, object] | None
    receipt: ProductBundleDecisionReceipt
    audit_data: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MemoryView:
    graph: MemoryWorldGraph
    revision: int
    snapshot_hash: str
    pending_reviews: tuple[PendingReview, ...]
    superseded_cognition_ids: frozenset[str]
    transitions: tuple[CognitionTransition, ...]
    evolution_steps: tuple[AcceptedEvolutionStep, ...] = ()
    decision_receipt: ProductBundleDecisionReceipt | None = None

    @property
    def current_cognitions(self) -> tuple[WorldCognition, ...]:
        return tuple(
            cognition
            for cognition_id, cognition in self.graph.cognitions.items()
            if cognition_id not in self.superseded_cognition_ids
        )


@dataclass(frozen=True, slots=True)
class MemoryAnswer:
    status: Literal["answered", "recalled", "ambiguous", "no_memory"]
    answer: str | None
    recalled_entities: tuple[Entity, ...]
    recalled_relationships: tuple[Relationship, ...]
    recalled_events: tuple[WorldEvent, ...]
    recalled_cognitions: tuple[WorldCognition, ...]
    evidence_context: tuple[EvidenceRecord, ...]
    history_cognition_ids: tuple[str, ...]
    recalled_history_cognitions: tuple[WorldCognition, ...] = ()
    reconstruction: MemoryReconstruction | None = None


class MemoryLoop:
    """SQLite-backed review → accept/reject → recall/correction loop.

    ``initial_graph`` is only used when the database is new.  A reopened loop
    reconstructs its graph exclusively from the canonical snapshot, and fails
    closed if that snapshot was edited behind its back.
    """

    def __init__(self, database: str | Path | sqlite3.Connection, initial_graph: MemoryWorldGraph) -> None:
        if isinstance(database, sqlite3.Connection):
            self._database: str | None = None
            self._conn = database
            self._owns_connection = False
        else:
            self._database = str(database)
            self._conn = open_db(self._database)
            self._owns_connection = True
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        row = self._conn.execute("SELECT snapshot_json, snapshot_hash FROM memory_state WHERE singleton = 1").fetchone()
        if row is None:
            self._write_initial(initial_graph)
        else:
            self._load_graph()  # Integrity verification eagerly fails closed.

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """The shared SQLite connection used by the world and identity stores."""
        return self._conn

    def __enter__(self) -> "MemoryLoop":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def stage_addition(
        self,
        delta: WorldDelta,
        evidence_records: Sequence[EvidenceRecord],
        review_payload: Mapping[str, object] | None = None,
        *,
        identity_bindings: Sequence["ReviewedIdentityBinding"] = (),
    ) -> PendingReview:
        """Stage a valid addition without modifying the canonical world."""
        graph, revision, _ = self._load_graph()
        records = self._normalize_evidence(evidence_records)
        known_ids = self._evidence_ids()
        self._check_evidence_conflicts(records)
        eligible = known_ids | {record.id for record in records}
        # This is the complete preview validation.  The result is not retained
        # until the Owner explicitly accepts the bound review below.
        preview = delta.apply_to(graph, eligible)
        bindings = self._normalize_identity_bindings(identity_bindings)
        self._validate_identity_bindings(bindings, records, preview)
        payload: dict[str, object] = {
            "delta": _delta_to_data(delta),
            "evidence": [_evidence_to_data(record) for record in records],
        }
        result_hash_parts: list[object] = [
            "addition",
            _delta_json(delta),
            _evidence_json(records),
        ]
        if bindings:
            binding_data = [_identity_binding_to_data(item) for item in bindings]
            payload["identity_bindings"] = binding_data
            result_hash_parts.append(_json(binding_data))
        result_hash_parts.append(review_payload)
        return self._insert_review(
            kind="addition",
            base_revision=revision,
            result_hash=_result_hash(*result_hash_parts),
            payload=payload,
            review_payload=review_payload,
        )

    def stage_product_bundle(
        self,
        delta: WorldDelta,
        evidence_records: Sequence[EvidenceRecord],
        review_payload: Mapping[str, object] | None = None,
        *,
        identity_bindings: Sequence["ReviewedIdentityBinding"] = (),
        transition_intents: Sequence[CognitionTransitionIntent] = (),
        claim_slices: Sequence[ProductClaimSlice] = (),
        always_include_entity_ids: Sequence[str] = (),
        always_include_identity_binding_indices: Sequence[int] = (),
    ) -> PendingReview:
        """Stage one real product write bundle without mutating the world.

        A product turn contributes exactly one current user Evidence record,
        while its reviewed delta may create a connected set of entities,
        relationships, events and cognitions.  All identity bindings and
        cognition replacement intents are part of the same hash-bound review.
        """
        graph, revision, _ = self._load_graph()
        records = self._normalize_evidence(evidence_records)
        if len(records) != 1:
            raise ValueError("product bundle requires exactly one current user EvidenceRecord")
        record = records[0]
        if tuple(delta.source_evidence_ids) != (record.id,):
            raise ValueError("product bundle delta must reference exactly the current Evidence id")
        self._check_evidence_conflicts(records)
        eligible = self._evidence_ids() | {record.id}
        preview = delta.apply_to(graph, eligible)
        bindings = self._normalize_identity_bindings(identity_bindings)
        self._validate_identity_bindings(bindings, records, preview)
        intents = self._normalize_product_transition_intents(transition_intents)
        self._validate_product_transition_intents(graph, delta, intents)
        slices = self._normalize_product_claim_slices(claim_slices)
        always_entities = self._normalize_always_entity_ids(always_include_entity_ids)
        always_binding_indices = self._normalize_always_binding_indices(
            always_include_identity_binding_indices,
        )
        self._validate_product_claim_slices(
            delta,
            bindings,
            intents,
            slices,
            always_entities,
            always_binding_indices,
        )
        binding_data = [_identity_binding_to_data(item) for item in bindings]
        intent_data = [_product_transition_intent_to_data(item) for item in intents]
        slice_data = [_product_claim_slice_to_data(item) for item in slices]
        payload: dict[str, object] = {
            "delta": _delta_to_data(delta),
            "evidence": [_evidence_to_data(record)],
            "identity_bindings": binding_data,
            "transition_intents": intent_data,
            "claim_slices": slice_data,
            "always_include_entity_ids": list(always_entities),
            "always_include_identity_binding_indices": list(always_binding_indices),
        }
        return self._insert_review(
            kind="product_bundle",
            base_revision=revision,
            result_hash=_result_hash(
                "product_bundle",
                _delta_json(delta),
                _evidence_json(records),
                _json(binding_data),
                _json(intent_data),
                _json(slice_data),
                _json(list(always_entities)),
                _json(list(always_binding_indices)),
                review_payload,
            ),
            payload=payload,
            review_payload=review_payload,
        )

    def stage_correction(
        self,
        prior_cognition_id: str,
        correction_text: str,
        new_user_evidence: EvidenceRecord,
        review_payload: Mapping[str, object] | None = None,
    ) -> PendingReview:
        """Compatibility wrapper for a one-cognition correction."""
        return self.stage_correction_bundle(
            (prior_cognition_id,), correction_text, new_user_evidence, review_payload
        )

    def stage_correction_bundle(
        self,
        prior_cognition_ids: Sequence[str],
        correction_text: str,
        new_user_evidence: EvidenceRecord,
        review_payload: Mapping[str, object] | None = None,
    ) -> PendingReview:
        """Stage one replacement that atomically supersedes 1–4 coherent cognitions."""
        graph, revision, _ = self._load_graph()
        prior_ids = tuple(prior_cognition_ids)
        priors = self._validated_correction_priors(graph, prior_ids)
        if not correction_text.strip():
            raise ValueError("correction_text must not be empty")
        record = self._normalize_evidence((new_user_evidence,))[0]
        self._check_evidence_conflicts((record,))
        prior = priors[0]
        confidence = compute_confidence(ConfidenceInputs(prior.content_type, "stated", 1, 0))
        replacement = replace(
            prior,
            id=f"cognition:correction:{uuid4()}",
            content=correction_text,
            formed_by="stated",
            confidence=confidence,
            cred_status=derive_cred_status(confidence, 0, prior.content_type, support_count=1),
            sources=(EvidenceLink(record.id, "support"),),
            invalid_at=None,
        )
        preview = MemoryWorldGraph(
            graph.world,
            graph.entities.copy(),
            graph.relationships.copy(),
            graph.events.copy(),
            graph.cognitions.copy(),
        )
        preview.add_cognition(replacement)
        return self._insert_review(
            kind="correction",
            base_revision=revision,
            result_hash=_result_hash(
                "correction", prior_ids, _cognition_json(replacement), _evidence_json((record,)), review_payload
            ),
            payload={
                "prior_cognition_ids": list(prior_ids),
                "replacement": _cognition_to_data(replacement),
                "evidence": [_evidence_to_data(record)],
            },
            review_payload=review_payload,
        )

    def stage_evolution(
        self,
        plan: WorldEvolutionPlan,
        evidence_records: Sequence[EvidenceRecord],
        review_payload: Mapping[str, object] | None = None,
    ) -> PendingReview:
        """Stage one typed, fully previewed world-evolution plan for review."""

        if not isinstance(plan, WorldEvolutionPlan):
            raise TypeError("plan must be a WorldEvolutionPlan")
        graph, revision, _ = self._load_graph()
        records = self._normalize_evidence(evidence_records)
        self._check_evidence_conflicts(records)
        eligible = self._evidence_ids() | {record.id for record in records}
        accepted_steps = self._accepted_evolution_steps()
        plan.apply_to(
            graph,
            eligible,
            superseded_cognition_ids=self._superseded_ids(),
            known_transition_ids=frozenset(item.step.id for item in accepted_steps),
        )
        plan_data = evolution_plan_to_data(plan)
        return self._insert_review(
            kind="evolution",
            base_revision=revision,
            result_hash=_result_hash(
                "evolution",
                _json(plan_data),
                _evidence_json(records),
                review_payload,
            ),
            payload={
                "plan": plan_data,
                "evidence": [_evidence_to_data(record) for record in records],
            },
            review_payload=review_payload,
        )

    def decide(
        self,
        review_id: str,
        result_hash: str,
        decision: Decision,
        *,
        selected_claim_ids: Sequence[str] | None = None,
    ) -> MemoryView:
        """Atomically accept/reject the exact proposal the user reviewed."""
        if decision not in ("accept", "reject"):
            raise ValueError("decision must be 'accept' or 'reject'")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            review = self._conn.execute("SELECT * FROM proposals WHERE id = ?", (review_id,)).fetchone()
            if review is None:
                raise ReviewStateError("unknown review")
            if review["status"] != "pending":
                raise ReviewStateError("review already decided")
            if review["result_hash"] != result_hash:
                raise ReviewStateError("review result hash mismatch")
            try:
                payload_raw = json.loads(cast(str, review["payload_json"]))
                review_payload_raw = (
                    json.loads(cast(str, review["review_payload_json"]))
                    if review["review_payload_json"] is not None
                    else None
                )
            except json.JSONDecodeError as error:
                raise MemoryLoopIntegrityError("review payload is not valid JSON") from error
            if not isinstance(payload_raw, Mapping):
                raise MemoryLoopIntegrityError("review payload is invalid")
            if review_payload_raw is not None and not isinstance(review_payload_raw, Mapping):
                raise MemoryLoopIntegrityError("review display payload is invalid")
            kind = cast(str, review["kind"])
            recomputed_hash = self._recompute_review_result_hash(
                kind,
                cast(Mapping[str, object], payload_raw),
                cast(Mapping[str, object] | None, review_payload_raw),
            )
            if recomputed_hash != cast(str, review["result_hash"]):
                raise MemoryLoopIntegrityError("review result hash does not match its stored payload")
            graph, revision, _ = self._load_graph()
            accepted_payload: Mapping[str, object] = cast(Mapping[str, object], payload_raw)
            effective_decision = decision
            selection: _ResolvedProductBundleSelection | None = None
            if kind == "product_bundle":
                selection = self._resolve_product_bundle_selection(
                    review_id,
                    result_hash,
                    decision,
                    cast(Mapping[str, object], payload_raw),
                    selected_claim_ids,
                    graph,
                )
                effective_decision = selection.effective_decision
                if selection.accepted_payload is not None:
                    accepted_payload = selection.accepted_payload
            elif selected_claim_ids is not None:
                raise ValueError("selected_claim_ids are only valid for product bundle reviews")
            if effective_decision == "accept":
                if int(review["base_revision"]) != revision:
                    raise ReviewStateError("stale review")
                old_graph = deepcopy(graph)
                old_revision = revision
                self._accept_payload(cast(ReviewKind, kind), accepted_payload, graph, revision + 1)
                revision += 1
                snapshot = _graph_json(graph)
                snapshot_hash = _hash(snapshot)
                self._conn.execute(
                    "UPDATE memory_state SET revision = ?, snapshot_json = ?, snapshot_hash = ? WHERE singleton = 1",
                    (revision, snapshot, snapshot_hash),
                )
                # If identity persistence has been bootstrapped, keep its graph
                # reference in this same transaction.  Otherwise the helper is
                # a no-op and first use bootstraps from the accepted world.
                from .identity_store import sync_identity_graph

                sync_identity_graph(
                    self._conn,
                    old_graph,
                    graph,
                    expected_memory_revision=old_revision,
                    new_memory_revision=revision,
                    new_snapshot_hash=snapshot_hash,
                )
                identity_bindings = _identity_bindings_from_payload(accepted_payload)
                if identity_bindings:
                    from .identity_store import accept_reviewed_identity_bindings

                    records = _evidence_records_from_payload(accepted_payload)
                    # A selection-aware product bundle has two hashes by
                    # design: the immutable offered-bundle hash that proves
                    # what was shown, and the final selection hash that
                    # proves exactly what was committed.  Bind the identity
                    # decision to the latter so its audit metadata cannot
                    # accidentally claim an unselected identity change was
                    # accepted.
                    identity_result_hash = (
                        selection.receipt.final_hash
                        if selection is not None
                        else result_hash
                    )
                    accept_reviewed_identity_bindings(
                        self._conn,
                        identity_bindings,
                        {
                            item.id: (item.content, item.metadata)
                            for item in records
                        },
                        memory_review_id=review_id,
                        memory_result_hash=identity_result_hash,
                        memory_revision=revision,
                        memory_snapshot_hash=snapshot_hash,
                        decided_at=datetime.now(timezone.utc).isoformat(),
                    )
            if selection is not None:
                stored_payload = dict(cast(Mapping[str, object], payload_raw))
                stored_payload["selection"] = dict(selection.audit_data)
                self._conn.execute(
                    "UPDATE proposals SET payload_json = ? WHERE id = ?",
                    (_json(stored_payload), review_id),
                )
            self._conn.execute("UPDATE proposals SET status = ? WHERE id = ?", (effective_decision, review_id))
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        view = self.view()
        return replace(view, decision_receipt=selection.receipt if selection is not None else None)

    def view(self) -> MemoryView:
        graph, revision, snapshot_hash = self._load_graph()
        pending = tuple(self._review_from_row(row) for row in self._conn.execute("SELECT * FROM proposals WHERE status = 'pending' ORDER BY rowid"))
        transitions = tuple(
            CognitionTransition(
                id=cast(str, row["id"]), prior_cognition_id=cast(str, row["prior_cognition_id"]),
                replacement_cognition_id=cast(str, row["replacement_cognition_id"]), reason=cast(str, row["reason"]), revision=int(row["revision"]),
            )
            for row in self._conn.execute("SELECT * FROM cognition_transitions ORDER BY rowid")
        )
        return MemoryView(
            graph,
            revision,
            snapshot_hash,
            pending,
            self._superseded_ids(),
            transitions,
            self._accepted_evolution_steps(),
        )

    def recall(
        self,
        query: str,
        *,
        resolved_entity_ids: Sequence[str] = (),
    ) -> MemoryReconstruction:
        """Reconstruct accepted memory without changing world or ledger state."""

        _, reconstruction = self._reconstruct(query, resolved_entity_ids)
        return reconstruction

    def ask(
        self,
        query: str,
        answer_client: LLMClient | None = None,
        *,
        resolved_entity_ids: Sequence[str] = (),
    ) -> MemoryAnswer:
        """Recall deterministically; only call an answerer for one resolved world."""

        view, reconstruction = self._reconstruct(query, resolved_entity_ids)
        if reconstruction.status == "unsupported":
            return MemoryAnswer("no_memory", None, (), (), (), (), (), (), (), reconstruction)
        if reconstruction.status == "ambiguous":
            return MemoryAnswer("ambiguous", None, (), (), (), (), (), (), (), reconstruction)
        graph = view.graph
        entities = tuple(graph.entities[item_id] for item_id in reconstruction.entity_ids)
        relationships = tuple(
            graph.relationships[item_id] for item_id in reconstruction.relationship_ids
        )
        events = tuple(graph.events[item_id] for item_id in reconstruction.event_ids)
        cognitions = tuple(
            graph.cognitions[item_id]
            for item_id in reconstruction.current_cognition_ids
        )
        history = tuple(
            graph.cognitions[item_id]
            for item_id in reconstruction.historical_cognition_ids
        )
        evidence = self._evidence_for_ids(reconstruction.evidence_ids)
        if answer_client is None:
            return MemoryAnswer(
                "recalled",
                None,
                entities,
                relationships,
                events,
                cognitions,
                evidence,
                reconstruction.historical_cognition_ids,
                history,
                reconstruction,
            )
        context = render_answer_context(reconstruction, graph)
        answer = answer_client.chat(
            [
                ChatMessage(
                    "system",
                    "Answer only from the supplied MemoWeft reconstructed world. "
                    "Treat provenance identifiers as audit links, not additional claims. "
                    "State uncertainty when needed.",
                ),
                ChatMessage("user", f"Question: {query}\n\nReconstructed memory:\n{context}"),
            ]
        )
        return MemoryAnswer(
            "answered",
            answer,
            entities,
            relationships,
            events,
            cognitions,
            evidence,
            reconstruction.historical_cognition_ids,
            history,
            reconstruction,
        )

    def _reconstruct(
        self,
        query: str,
        resolved_entity_ids: Sequence[str],
    ) -> tuple[MemoryView, MemoryReconstruction]:
        view = self.view()
        parsed = parse_memory_query(query, resolved_entity_ids=resolved_entity_ids)
        lineage = tuple(
            CognitionLineage(
                item.prior_cognition_id,
                item.replacement_cognition_id,
                item.reason,
            )
            for item in view.transitions
        )
        reconstruction = reconstruct_memory(
            parsed,
            view.graph,
            superseded_cognition_ids=view.superseded_cognition_ids,
            cognition_lineage=lineage,
            accepted_evolution_steps=view.evolution_steps,
        )
        return view, reconstruction

    def _create_schema(self) -> None:
        for statement in MEMORY_LOOP_SCHEMA_SQL:
            self._conn.execute(statement)

    def _write_initial(self, graph: MemoryWorldGraph) -> None:
        graph.validate_owner()
        snapshot = _graph_json(graph)
        self._conn.execute(
            "INSERT INTO memory_state(singleton, revision, snapshot_json, snapshot_hash) VALUES (1, 0, ?, ?)",
            (snapshot, _hash(snapshot)),
        )

    def _load_graph(self) -> tuple[MemoryWorldGraph, int, str]:
        row = self._conn.execute("SELECT revision, snapshot_json, snapshot_hash FROM memory_state WHERE singleton = 1").fetchone()
        if row is None:
            raise MemoryLoopIntegrityError("memory state missing")
        snapshot = cast(str, row["snapshot_json"])
        stored_hash = cast(str, row["snapshot_hash"])
        if _hash(snapshot) != stored_hash:
            raise MemoryLoopIntegrityError("memory snapshot hash mismatch")
        try:
            graph = _graph_from_data(cast(dict[str, object], json.loads(snapshot)))
            graph.validate_owner()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MemoryLoopIntegrityError("memory snapshot is invalid") from error
        return graph, int(row["revision"]), stored_hash

    def _insert_review(
        self, *, kind: ReviewKind, base_revision: int, result_hash: str, payload: Mapping[str, object],
        review_payload: Mapping[str, object] | None,
    ) -> PendingReview:
        review_id = f"review:{uuid4()}"
        self._conn.execute(
            "INSERT INTO proposals(id, kind, base_revision, result_hash, payload_json, review_payload_json, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (review_id, kind, base_revision, result_hash, _json(payload), _json(review_payload) if review_payload is not None else None),
        )
        return PendingReview(review_id, kind, result_hash, base_revision, review_payload)

    def _accept_payload(
        self, kind: ReviewKind, payload: Mapping[str, object], graph: MemoryWorldGraph, next_revision: int
    ) -> None:
        records = _evidence_records_from_payload(payload)
        self._check_evidence_conflicts(records)
        for record in records:
            self._conn.execute("INSERT OR IGNORE INTO evidence_ledger(id, content, payload_json) VALUES (?, ?, ?)", (record.id, record.content, _json(_evidence_to_data(record))))
        if kind == "addition":
            delta = _delta_from_data(cast(Mapping[str, object], payload["delta"]))
            graph_after = delta.apply_to(graph, self._evidence_ids())
            graph.entities, graph.relationships, graph.events, graph.cognitions = graph_after.entities, graph_after.relationships, graph_after.events, graph_after.cognitions
            return
        if kind == "product_bundle":
            delta_raw = payload.get("delta")
            if not isinstance(delta_raw, Mapping):
                raise MemoryLoopIntegrityError("product bundle delta is missing")
            delta = _delta_from_data(cast(Mapping[str, object], delta_raw))
            if len(records) != 1 or tuple(delta.source_evidence_ids) != (records[0].id,):
                raise MemoryLoopIntegrityError("product bundle Evidence boundary is invalid")
            intents = _product_transition_intents_from_payload(payload)
            self._validate_product_transition_intents(graph, delta, intents)
            graph_after = delta.apply_to(graph, self._evidence_ids())
            graph.entities = graph_after.entities
            graph.relationships = graph_after.relationships
            graph.events = graph_after.events
            graph.cognitions = graph_after.cognitions
            for intent in intents:
                self._conn.execute(
                    "INSERT INTO cognition_transitions(id, prior_cognition_id, replacement_cognition_id, reason, revision) VALUES (?, ?, ?, ?, ?)",
                    (
                        f"transition:{uuid4()}",
                        intent.prior_cognition_id,
                        intent.successor_cognition_id,
                        intent.reason,
                        next_revision,
                    ),
                )
            return
        if kind == "evolution":
            plan_raw = payload.get("plan")
            if not isinstance(plan_raw, Mapping):
                raise MemoryLoopIntegrityError("evolution plan is missing")
            try:
                plan = evolution_plan_from_data(cast(Mapping[str, object], plan_raw))
                accepted_steps = self._accepted_evolution_steps()
                graph_after = plan.apply_to(
                    graph,
                    self._evidence_ids(),
                    superseded_cognition_ids=self._superseded_ids(),
                    known_transition_ids=frozenset(item.step.id for item in accepted_steps),
                )
            except WorldEvolutionValidationError as error:
                raise MemoryLoopIntegrityError("evolution plan no longer matches its reviewed contract") from error
            graph.entities = graph_after.entities
            graph.relationships = graph_after.relationships
            graph.events = graph_after.events
            graph.cognitions = graph_after.cognitions
            for prior_id, successor_id, reason in superseding_cognition_pairs(plan):
                self._conn.execute(
                    "INSERT INTO cognition_transitions(id, prior_cognition_id, replacement_cognition_id, reason, revision) VALUES (?, ?, ?, ?, ?)",
                    (f"transition:{uuid4()}", prior_id, successor_id, reason, next_revision),
                )
            return
        if kind != "correction":
            raise MemoryLoopIntegrityError(f"unsupported review kind: {kind}")
        prior_ids = _correction_prior_ids_from_payload(payload)
        try:
            priors = self._validated_correction_priors(graph, prior_ids)
        except MemoryLoopError as error:
            raise ReviewStateError("correction prior cognition is stale or incoherent") from error
        if len(records) != 1 or records[0].role != "user" or not records[0].id.strip() or not records[0].content.strip():
            raise MemoryLoopIntegrityError("correction must contain exactly one non-empty user evidence record")
        replacement = _cognition_from_data(cast(Mapping[str, object], payload["replacement"]))
        prior = priors[0]
        confidence = compute_confidence(ConfidenceInputs(prior.content_type, "stated", 1, 0))
        expected_sources = (EvidenceLink(records[0].id, "support"),)
        if (
            replacement.target != prior.target
            or replacement.perspective != prior.perspective
            or replacement.content_type != prior.content_type
            or replacement.scope != prior.scope
            or replacement.formed_by != "stated"
            or replacement.confidence != confidence
            or replacement.cred_status != derive_cred_status(confidence, 0, prior.content_type, support_count=1)
            or replacement.sources != expected_sources
            or replacement.invalid_at is not None
        ):
            raise MemoryLoopIntegrityError("correction replacement does not match its reviewed prior bundle")
        graph.add_cognition(replacement)
        for prior_id in prior_ids:
            self._conn.execute(
                "INSERT INTO cognition_transitions(id, prior_cognition_id, replacement_cognition_id, reason, revision) VALUES (?, ?, ?, ?, ?)",
                (f"transition:{uuid4()}", prior_id, replacement.id, "correction", next_revision),
            )
        return

    def _validated_correction_priors(
        self, graph: MemoryWorldGraph, prior_cognition_ids: Sequence[str]
    ) -> tuple[WorldCognition, ...]:
        prior_ids = tuple(prior_cognition_ids)
        if not 1 <= len(prior_ids) <= 4:
            raise MemoryLoopError("correction bundle must contain between 1 and 4 prior cognition ids")
        if any(not isinstance(prior_id, str) or not prior_id.strip() for prior_id in prior_ids):
            raise MemoryLoopError("correction bundle contains an invalid prior cognition id")
        if len(set(prior_ids)) != len(prior_ids):
            raise MemoryLoopError("correction bundle contains duplicate prior cognition ids")
        superseded = self._superseded_ids()
        priors: list[WorldCognition] = []
        for prior_id in prior_ids:
            if prior_id not in graph.cognitions:
                raise MemoryLoopError(f"unknown prior cognition: {prior_id}")
            if prior_id in superseded:
                raise MemoryLoopError(f"cognition is already superseded: {prior_id}")
            priors.append(graph.cognitions[prior_id])
        first = priors[0]
        if any(item.target != first.target for item in priors[1:]):
            raise MemoryLoopError("correction bundle priors must share target")
        if any(item.perspective != first.perspective for item in priors[1:]):
            raise MemoryLoopError("correction bundle priors must share perspective")
        if any(item.content_type != first.content_type for item in priors[1:]):
            raise MemoryLoopError("correction bundle priors must share content_type")
        if any(item.scope != first.scope for item in priors[1:]):
            raise MemoryLoopError("correction bundle priors must share scope")
        return tuple(priors)

    def _accepted_evolution_steps(self) -> tuple[AcceptedEvolutionStep, ...]:
        accepted: list[AcceptedEvolutionStep] = []
        rows = self._conn.execute(
            "SELECT id, base_revision, payload_json FROM proposals "
            "WHERE kind = 'evolution' AND status = 'accept' ORDER BY base_revision, rowid"
        )
        for row in rows:
            try:
                payload = json.loads(cast(str, row["payload_json"]))
                if not isinstance(payload, Mapping) or not isinstance(payload.get("plan"), Mapping):
                    raise TypeError("accepted evolution payload shape")
                plan = evolution_plan_from_data(
                    cast(Mapping[str, object], payload["plan"])
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise MemoryLoopIntegrityError("accepted evolution payload is invalid") from error
            accepted.extend(
                AcceptedEvolutionStep(
                    review_id=cast(str, row["id"]),
                    revision=int(row["base_revision"]) + 1,
                    step=step,
                )
                for step in plan.steps
            )
        return tuple(accepted)

    def _recompute_review_result_hash(
        self,
        kind: str,
        payload: Mapping[str, object],
        review_payload: Mapping[str, object] | None,
    ) -> str:
        try:
            records = _evidence_records_from_payload(payload)
            if kind == "addition":
                allowed = {"delta", "evidence", "identity_bindings"}
                if (
                    set(payload) not in ({"delta", "evidence"}, allowed)
                    or not isinstance(payload.get("delta"), Mapping)
                ):
                    raise TypeError("addition payload shape")
                delta = _delta_from_data(cast(Mapping[str, object], payload["delta"]))
                bindings = _identity_bindings_from_payload(payload)
                parts: list[object] = [
                    "addition",
                    _delta_json(delta),
                    _evidence_json(records),
                ]
                if bindings:
                    parts.append(_json([_identity_binding_to_data(item) for item in bindings]))
                parts.append(review_payload)
                return _result_hash(*parts)
            if kind == "product_bundle":
                allowed = {
                    "delta", "evidence", "identity_bindings", "transition_intents", "claim_slices",
                    "always_include_entity_ids", "always_include_identity_binding_indices",
                }
                payload_without_decision = {
                    key: value for key, value in payload.items() if key != "selection"
                }
                if set(payload_without_decision) != allowed or not isinstance(payload.get("delta"), Mapping):
                    raise TypeError("product bundle payload shape")
                delta = _delta_from_data(cast(Mapping[str, object], payload["delta"]))
                if len(records) != 1 or tuple(delta.source_evidence_ids) != (records[0].id,):
                    raise TypeError("product bundle Evidence boundary")
                bindings = _identity_bindings_from_payload(payload)
                intents = _product_transition_intents_from_payload(payload)
                slices = _product_claim_slices_from_payload(payload)
                always_entities, always_binding_indices = _always_included_product_records_from_payload(payload)
                self._validate_product_claim_slices(
                    delta, bindings, intents, slices, always_entities, always_binding_indices
                )
                return _result_hash(
                    "product_bundle",
                    _delta_json(delta),
                    _evidence_json(records),
                    _json([_identity_binding_to_data(item) for item in bindings]),
                    _json([_product_transition_intent_to_data(item) for item in intents]),
                    _json([_product_claim_slice_to_data(item) for item in slices]),
                    _json(list(always_entities)),
                    _json(list(always_binding_indices)),
                    review_payload,
                )
            if kind == "correction":
                allowed = {"prior_cognition_ids", "replacement", "evidence"}
                legacy_allowed = {"prior_cognition_id", "replacement", "evidence"}
                if set(payload) != allowed and set(payload) != legacy_allowed:
                    raise TypeError("correction payload shape")
                replacement_raw = payload.get("replacement")
                if not isinstance(replacement_raw, Mapping):
                    raise TypeError("correction replacement shape")
                prior_ids = _correction_prior_ids_from_payload(payload)
                replacement = _cognition_from_data(cast(Mapping[str, object], replacement_raw))
                return _result_hash(
                    "correction",
                    prior_ids,
                    _cognition_json(replacement),
                    _evidence_json(records),
                    review_payload,
                )
            if kind == "evolution":
                if set(payload) != {"plan", "evidence"} or not isinstance(payload.get("plan"), Mapping):
                    raise TypeError("evolution payload shape")
                plan = evolution_plan_from_data(cast(Mapping[str, object], payload["plan"]))
                return _result_hash(
                    "evolution",
                    _json(evolution_plan_to_data(plan)),
                    _evidence_json(records),
                    review_payload,
                )
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError("review payload cannot be reconstructed") from error
        raise MemoryLoopIntegrityError(f"unsupported review kind: {kind}")

    def _normalize_evidence(self, records: Sequence[EvidenceRecord]) -> tuple[EvidenceRecord, ...]:
        normalized: list[EvidenceRecord] = []
        ids: set[str] = set()
        for record in records:
            if not isinstance(record, EvidenceRecord) or not record.id.strip() or not record.content.strip() or record.role != "user":
                raise ValueError("evidence records must be non-empty user EvidenceRecord values")
            if record.id in ids:
                raise EvidenceConflictError(f"duplicate evidence id in proposal: {record.id}")
            ids.add(record.id)
            normalized.append(record)
        return tuple(normalized)

    def _normalize_identity_bindings(
        self,
        bindings: Sequence["ReviewedIdentityBinding"],
    ) -> tuple["ReviewedIdentityBinding", ...]:
        from .identity_store import ReviewedIdentityBinding

        normalized: list[ReviewedIdentityBinding] = []
        for binding in bindings:
            if not isinstance(binding, ReviewedIdentityBinding):
                raise ValueError(
                    "identity bindings must be ReviewedIdentityBinding values"
                )
            normalized.append(binding)
        return tuple(normalized)

    def _validate_identity_bindings(
        self,
        bindings: Sequence["ReviewedIdentityBinding"],
        records: Sequence[EvidenceRecord],
        preview: MemoryWorldGraph,
    ) -> None:
        if not bindings:
            return
        from .identity_store import _validate_reviewed_binding

        evidence = {item.id: (item.content, item.metadata) for item in records}
        seen: set[tuple[str, int, int, str]] = set()
        for binding in bindings:
            _validate_reviewed_binding(binding, preview, evidence)
            key = (
                binding.evidence_id,
                binding.start_codepoint,
                binding.end_codepoint,
                binding.entity_id,
            )
            if key in seen:
                raise ValueError("identity binding request is duplicated")
            seen.add(key)

    @staticmethod
    def _normalize_product_transition_intents(
        intents: Sequence[CognitionTransitionIntent],
    ) -> tuple[CognitionTransitionIntent, ...]:
        normalized: list[CognitionTransitionIntent] = []
        prior_ids: set[str] = set()
        for intent in intents:
            if not isinstance(intent, CognitionTransitionIntent):
                raise TypeError("product bundle transition intents must be CognitionTransitionIntent values")
            if not all(
                isinstance(value, str) and value.strip()
                for value in (
                    intent.prior_cognition_id,
                    intent.successor_cognition_id,
                    intent.reason,
                    intent.statement_kind,
                )
            ):
                raise ValueError("product bundle transition intent fields must be non-empty strings")
            if intent.reason not in _PRODUCT_TRANSITION_REASONS:
                raise ValueError("product bundle transition intent reason is unsupported")
            if intent.prior_cognition_id in prior_ids:
                raise ValueError("product bundle transition intents cannot replace one prior twice")
            prior_ids.add(intent.prior_cognition_id)
            normalized.append(intent)
        return tuple(normalized)

    def _validate_product_transition_intents(
        self,
        graph: MemoryWorldGraph,
        delta: WorldDelta,
        intents: Sequence[CognitionTransitionIntent],
    ) -> None:
        """Require real accepted-current priors and bundle-local successors."""
        successors = {item.id: item for item in delta.new_cognitions}
        if len(successors) != len(delta.new_cognitions):
            raise MemoryLoopIntegrityError("product bundle cognition successors are duplicated")
        superseded = self._superseded_ids()
        for intent in intents:
            prior = graph.cognitions.get(intent.prior_cognition_id)
            if prior is None:
                raise MemoryLoopError("product bundle transition prior is not accepted")
            if prior.id in superseded:
                raise MemoryLoopError("product bundle transition prior is already historical")
            successor = successors.get(intent.successor_cognition_id)
            if successor is None:
                raise MemoryLoopError("product bundle transition successor is not a new bundle cognition")
            if successor.target != prior.target:
                raise MemoryLoopError("product bundle transition target is incompatible")
            if successor.perspective != prior.perspective:
                raise MemoryLoopError("product bundle transition perspective is incompatible")
            prior_kind = _structured_claim_statement_kind(prior)
            successor_kind = _structured_claim_statement_kind(successor)
            if prior_kind != intent.statement_kind or successor_kind != intent.statement_kind:
                raise MemoryLoopError("product bundle transition statement_kind is incompatible")

    @staticmethod
    def _normalize_product_claim_slices(
        slices: Sequence[ProductClaimSlice],
    ) -> tuple[ProductClaimSlice, ...]:
        normalized: list[ProductClaimSlice] = []
        seen_claim_ids: set[str] = set()
        for item in slices:
            if not isinstance(item, ProductClaimSlice):
                raise TypeError("product bundle claim slices must be ProductClaimSlice values")
            if not isinstance(item.claim_id, str) or not item.claim_id.strip():
                raise ValueError("product bundle claim_id must be a non-empty string")
            if item.claim_id in seen_claim_ids:
                raise ValueError("product bundle claim_ids must be unique")
            for field_name in (
                "entity_ids", "relationship_ids", "event_ids", "cognition_ids", "depends_on_claim_ids",
            ):
                values = getattr(item, field_name)
                if not isinstance(values, tuple) or any(not isinstance(value, str) or not value.strip() for value in values):
                    raise ValueError(f"product bundle {field_name} must contain non-empty strings")
                if len(set(values)) != len(values):
                    raise ValueError(f"product bundle {field_name} cannot repeat a value")
            for field_name in ("identity_binding_indices", "transition_intent_indices"):
                values = getattr(item, field_name)
                if not isinstance(values, tuple) or any(type(value) is not int or value < 0 for value in values):
                    raise ValueError(f"product bundle {field_name} must contain non-negative integers")
                if len(set(values)) != len(values):
                    raise ValueError(f"product bundle {field_name} cannot repeat a value")
            seen_claim_ids.add(item.claim_id)
            normalized.append(item)
        return tuple(normalized)

    @staticmethod
    def _normalize_always_entity_ids(entity_ids: Sequence[str]) -> tuple[str, ...]:
        values = tuple(entity_ids)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("product bundle always_include_entity_ids must contain non-empty strings")
        if len(set(values)) != len(values):
            raise ValueError("product bundle always_include_entity_ids cannot repeat an entity")
        return values

    @staticmethod
    def _normalize_always_binding_indices(indices: Sequence[int]) -> tuple[int, ...]:
        values = tuple(indices)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("product bundle always_include_identity_binding_indices must contain non-negative integers")
        if len(set(values)) != len(values):
            raise ValueError("product bundle always_include_identity_binding_indices cannot repeat an index")
        return values

    @staticmethod
    def _validate_product_claim_slices(
        delta: WorldDelta,
        bindings: Sequence["ReviewedIdentityBinding"],
        intents: Sequence[CognitionTransitionIntent],
        slices: Sequence[ProductClaimSlice],
        always_entities: Sequence[str],
        always_binding_indices: Sequence[int],
    ) -> None:
        """Make the offered claim map a closed, non-overlapping record set."""
        if not slices:
            if always_entities or always_binding_indices:
                raise ValueError("product bundle always-included records require claim slices")
            return
        by_claim = {item.claim_id: item for item in slices}
        for item in slices:
            if item.claim_id in item.depends_on_claim_ids:
                raise ValueError("product bundle claim cannot depend on itself")
            unknown = set(item.depends_on_claim_ids) - set(by_claim)
            if unknown:
                raise ValueError("product bundle claim dependency is unknown")
        _validate_product_claim_dependency_graph(by_claim)

        expected: dict[str, set[object]] = {
            "entity_ids": {item.id for item in delta.new_entities},
            "relationship_ids": {item.id for item in delta.new_relationships},
            "event_ids": {item.id for item in delta.new_events},
            "cognition_ids": {item.id for item in delta.new_cognitions},
            "identity_binding_indices": set(range(len(bindings))),
            "transition_intent_indices": set(range(len(intents))),
        }
        always: dict[str, set[object]] = {
            "entity_ids": set(always_entities),
            "identity_binding_indices": set(always_binding_indices),
        }
        for field_name, values in always.items():
            if not values <= expected[field_name]:
                raise ValueError(f"product bundle always-included {field_name} are unknown")
        for field_name, expected_values in expected.items():
            owner_by_value: dict[object, str] = {}
            for item in slices:
                for value in getattr(item, field_name):
                    if value not in expected_values:
                        raise ValueError(f"product bundle {field_name} references an unknown record")
                    if value in owner_by_value:
                        raise ValueError(f"product bundle {field_name} assigns one record to multiple claims")
                    owner_by_value[value] = item.claim_id
            always_values = always.get(field_name, set())
            if set(owner_by_value) & always_values:
                raise ValueError(f"product bundle {field_name} cannot be both owned and always included")
            if set(owner_by_value) | always_values != expected_values:
                raise ValueError(f"product bundle {field_name} must cover every offered record")
        if delta.unresolved_references or delta.semantic_uncertainties:
            raise ValueError("selection-aware product bundles cannot contain unmapped uncertainty records")

    def _resolve_product_bundle_selection(
        self,
        review_id: str,
        offered_result_hash: str,
        decision: Decision,
        payload: Mapping[str, object],
        selected_claim_ids: Sequence[str] | None,
        graph: MemoryWorldGraph,
    ) -> _ResolvedProductBundleSelection:
        """Filter a hash-bound offered bundle solely from SQLite payload data."""
        delta_raw = payload.get("delta")
        if not isinstance(delta_raw, Mapping):
            raise MemoryLoopIntegrityError("product bundle delta is missing")
        delta = _delta_from_data(cast(Mapping[str, object], delta_raw))
        records = _evidence_records_from_payload(payload)
        if len(records) != 1 or tuple(delta.source_evidence_ids) != (records[0].id,):
            raise MemoryLoopIntegrityError("product bundle Evidence boundary is invalid")
        bindings = _identity_bindings_from_payload(payload)
        intents = _product_transition_intents_from_payload(payload)
        slices = _product_claim_slices_from_payload(payload)
        always_entities, always_binding_indices = _always_included_product_records_from_payload(payload)
        self._validate_product_claim_slices(delta, bindings, intents, slices, always_entities, always_binding_indices)

        if decision == "reject" and selected_claim_ids not in (None, ()):
            raise ValueError("a rejected product bundle cannot select claims")
        if not slices:
            if selected_claim_ids is not None:
                raise ValueError("product bundle does not expose claim-level selection")
            selected: tuple[str, ...] = ()
            applied: tuple[str, ...] = ()
            effective: Decision = decision
            accepted_payload: Mapping[str, object] | None = payload if decision == "accept" else None
        else:
            selected = _normalize_selected_claim_ids(selected_claim_ids, tuple(item.claim_id for item in slices), decision)
            applied = _resolve_selected_claim_dependencies(selected, slices)
            effective = "reject" if decision == "reject" or not applied else "accept"
            accepted_payload = None
            if effective == "accept":
                accepted_payload = _filtered_product_bundle_payload(
                    delta,
                    records[0],
                    bindings,
                    intents,
                    slices,
                    always_entities,
                    always_binding_indices,
                    applied,
                )
                selected_delta = _delta_from_data(cast(Mapping[str, object], accepted_payload["delta"]))
                selected_bindings = _identity_bindings_from_payload(accepted_payload)
                selected_intents = _product_transition_intents_from_payload(accepted_payload)
                preview = selected_delta.apply_to(graph, self._evidence_ids() | {records[0].id})
                self._validate_identity_bindings(selected_bindings, records, preview)
                self._validate_product_transition_intents(graph, selected_delta, selected_intents)

        final_hash = _product_bundle_selection_hash(
            offered_result_hash,
            effective,
            selected,
            applied,
            accepted_payload,
        )
        receipt = ProductBundleDecisionReceipt(
            review_id,
            offered_result_hash,
            effective,
            selected,
            applied,
            final_hash,
        )
        return _ResolvedProductBundleSelection(
            effective,
            accepted_payload,
            receipt,
            {
                "version": 1,
                "decision": effective,
                "selected_claim_ids": list(selected),
                "applied_claim_ids": list(applied),
                "final_hash": final_hash,
            },
        )

    def _check_evidence_conflicts(self, records: Sequence[EvidenceRecord]) -> None:
        for record in records:
            existing = self._conn.execute("SELECT content FROM evidence_ledger WHERE id = ?", (record.id,)).fetchone()
            if existing is not None and cast(str, existing["content"]) != record.content:
                raise EvidenceConflictError(f"evidence id conflict: {record.id}")

    def _evidence_ids(self) -> set[str]:
        return {cast(str, row["id"]) for row in self._conn.execute("SELECT id FROM evidence_ledger")}

    def _superseded_ids(self) -> frozenset[str]:
        return frozenset(cast(str, row["prior_cognition_id"]) for row in self._conn.execute("SELECT prior_cognition_id FROM cognition_transitions"))

    def _review_from_row(self, row: sqlite3.Row) -> PendingReview:
        raw = row["review_payload_json"]
        payload = cast(Mapping[str, object], json.loads(cast(str, raw))) if raw is not None else None
        return PendingReview(cast(str, row["id"]), cast(ReviewKind, row["kind"]), cast(str, row["result_hash"]), int(row["base_revision"]), payload)

    def _evidence_for_ids(self, evidence_ids: Sequence[str]) -> tuple[EvidenceRecord, ...]:
        ids = tuple(dict.fromkeys(evidence_ids))
        result: list[EvidenceRecord] = []
        for evidence_id in ids:
            row = self._conn.execute("SELECT payload_json FROM evidence_ledger WHERE id = ?", (evidence_id,)).fetchone()
            if row is not None:
                result.append(_evidence_from_data(cast(Mapping[str, object], json.loads(cast(str, row["payload_json"])))))
        return tuple(result)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def _result_hash(*parts: object) -> str:
    return _hash(_json(parts))


def _data(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _data(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, tuple):
        return [_data(item) for item in value]
    if isinstance(value, list):
        return [_data(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _data(item) for key, item in value.items()}
    return value


def _graph_to_data(graph: MemoryWorldGraph) -> dict[str, object]:
    return {
        "world": _data(graph.world),
        "entities": [_data(item) for item in graph.entities.values()],
        "relationships": [_data(item) for item in graph.relationships.values()],
        "events": [_data(item) for item in graph.events.values()],
        "cognitions": [_data(item) for item in graph.cognitions.values()],
    }


def _graph_json(graph: MemoryWorldGraph) -> str:
    return _json(_graph_to_data(graph))


def _graph_from_data(data: Mapping[str, object]) -> MemoryWorldGraph:
    world_raw = cast(Mapping[str, object], data["world"])
    graph = MemoryWorldGraph(PersonalWorld(cast(str, world_raw["world_id"]), cast(str, world_raw["owner_entity_id"])))
    for raw in cast(list[object], data["entities"]): graph.add_entity(_entity_from_data(cast(Mapping[str, object], raw)))
    for raw in cast(list[object], data["relationships"]): graph.add_relationship(_relationship_from_data(cast(Mapping[str, object], raw)))
    for raw in cast(list[object], data["events"]): graph.add_event(_event_from_data(cast(Mapping[str, object], raw)))
    for raw in cast(list[object], data["cognitions"]): graph.add_cognition(_cognition_from_data(cast(Mapping[str, object], raw)))
    return graph


def _entity_from_data(raw: Mapping[str, object]) -> Entity:
    return Entity(cast(str, raw["id"]), cast(str, raw["world_id"]), cast(str, raw["kind"]), cast(str, raw["canonical_name"]), tuple(cast(list[str], raw.get("aliases", []))))


def _relationship_from_data(raw: Mapping[str, object]) -> Relationship:
    return Relationship(cast(str, raw["id"]), cast(str, raw["world_id"]), cast(str, raw["source_entity_id"]), cast(str, raw["target_entity_id"]), cast(str, raw["relation_type"]), bool(raw.get("bidirectional", False)), cast(str | None, raw.get("status")), cast(str | None, raw.get("valid_from")), cast(str | None, raw.get("valid_to")))


def _event_from_data(raw: Mapping[str, object]) -> WorldEvent:
    return WorldEvent(cast(str, raw["id"]), cast(str, raw["world_id"]), cast(str, raw["event_type"]), cast(str, raw["summary"]), cast(str, raw["occurred_at"]), tuple(EventParticipant(cast(str, item["entity_id"]), cast(str | None, item.get("role"))) for item in cast(list[Mapping[str, object]], raw.get("participants", []))), tuple(cast(list[str], raw.get("related_entity_ids", []))), tuple(cast(list[str], raw.get("relationship_ids", []))), tuple(EventFacet(cast(str, item["key"]), cast(str, item["value"]), cast(str | None, item.get("about_entity_id"))) for item in cast(list[Mapping[str, object]], raw.get("facets", []))), tuple(cast(list[str], raw.get("evidence_ids", []))))


def _cognition_to_data(cognition: WorldCognition) -> dict[str, object]: return cast(dict[str, object], _data(cognition))
def _cognition_json(cognition: WorldCognition) -> str: return _json(_cognition_to_data(cognition))
def _cognition_from_data(raw: Mapping[str, object]) -> WorldCognition:
    target = cast(Mapping[str, object], raw["target"]); perspective = cast(Mapping[str, object], raw["perspective"])
    claim_raw = raw.get("structured_claim")
    if claim_raw is None:
        structured_claim = None
    elif isinstance(claim_raw, Mapping):
        try:
            structured_claim = StructuredClaim(
                cast(Any, claim_raw["statement_kind"]),
                cast(str | None, claim_raw.get("predicate")),
                cast(str | None, claim_raw.get("value")),
                cast(Any, claim_raw.get("polarity", "assert")),
                cast(Any, claim_raw.get("epistemic_status", "asserted")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError("cognition structured_claim is invalid") from error
    else:
        raise MemoryLoopIntegrityError("cognition structured_claim is invalid")
    return WorldCognition(cast(str, raw["id"]), cast(str, raw["world_id"]), MemoryTarget(cast(Any, target["kind"]), cast(str, target["id"])), cast(str, raw["content"]), cast(Any, raw["content_type"]), cast(Any, raw["formed_by"]), int(cast(Any, raw["confidence"])), cast(Any, raw["cred_status"]), Perspective(cast(Any, perspective["kind"]), tuple(cast(list[str], perspective.get("holder_entity_ids", [])))), tuple(EvidenceLink(cast(str, item["evidence_id"]), cast(Any, item["relation"])) for item in cast(list[Mapping[str, object]], raw.get("sources", []))), cast(str | None, raw.get("scope")), cast(str | None, raw.get("valid_at")), cast(str | None, raw.get("invalid_at")), structured_claim)


def _delta_to_data(delta: WorldDelta) -> dict[str, object]: return cast(dict[str, object], _data(delta))
def _delta_json(delta: WorldDelta) -> str: return _json(_delta_to_data(delta))
def _delta_from_data(raw: Mapping[str, object]) -> WorldDelta:
    # Formation traces are intentionally audit-only; staged payloads preserve
    # them structurally as JSON but validated additions already checked them.
    # Reconstructing them fully here would duplicate the extractor contract.
    # Revalidation during acceptance still runs against the durable ledger.
    from .delta import ClaimSpan, FormationContentBinding, FormationSourceTrace, FormationTrace, SemanticUncertainty, UnresolvedReference
    traces = tuple(FormationTrace(cast(str, item["cognition_id"]), bool(item["model_inferred_proposal"]), tuple(FormationSourceTrace(cast(str, source["evidence_id"]), cast(Any, source["relation"]), cast(Any, source["proposition_origin_proposal"]), cast(Any, source["response_act_proposal"]), ClaimSpan(int(cast(Any, cast(Mapping[str, object], source["claim_span"])["start_codepoint"])), int(cast(Any, cast(Mapping[str, object], source["claim_span"])["end_codepoint"])), cast(str, cast(Mapping[str, object], source["claim_span"])["source_content_sha256"]), cast(str, cast(Mapping[str, object], source["claim_span"])["claim_sha256"])), cast(str | None, source.get("preceding_assistant_turn_id")), cast(str | None, source.get("preceding_assistant_content_sha256")), cast(Any, source["local_origin_decision"]), cast(str, source["decision_code"])) for source in cast(list[Mapping[str, object]], item["sources"])), cast(Any, item["derived_formed_by"]), int(cast(Any, item["raw_support_count"])), int(cast(Any, item["effective_support_count"])), int(cast(Any, item["contradict_count"])), content_bindings=tuple(FormationContentBinding(cast(Any, binding["semantic_role"]), cast(str, binding["about_entity_id"]), cast(str, binding["evidence_id"]), ClaimSpan(int(cast(Any, cast(Mapping[str, object], binding["claim_span"])["start_codepoint"])), int(cast(Any, cast(Mapping[str, object], binding["claim_span"])["end_codepoint"])), cast(str, cast(Mapping[str, object], binding["claim_span"])["source_content_sha256"]), cast(str, cast(Mapping[str, object], binding["claim_span"])["claim_sha256"]))) for binding in cast(list[Mapping[str, object]], item.get("content_bindings", [])))) for item in cast(list[Mapping[str, object]], raw.get("formation_traces", [])))
    return WorldDelta(cast(str, raw["world_id"]), tuple(cast(list[str], raw["source_evidence_ids"])), tuple(_entity_from_data(cast(Mapping[str, object], item)) for item in cast(list[object], raw.get("new_entities", []))), tuple(_relationship_from_data(cast(Mapping[str, object], item)) for item in cast(list[object], raw.get("new_relationships", []))), tuple(_event_from_data(cast(Mapping[str, object], item)) for item in cast(list[object], raw.get("new_events", []))), tuple(_cognition_from_data(cast(Mapping[str, object], item)) for item in cast(list[object], raw.get("new_cognitions", []))), formation_traces=traces, unresolved_references=tuple(UnresolvedReference(cast(str, item["mention"]), tuple(cast(list[str], item["evidence_ids"]))) for item in cast(list[Mapping[str, object]], raw.get("unresolved_references", []))), semantic_uncertainties=tuple(SemanticUncertainty(cast(str, item["detail"]), tuple(cast(list[str], item["evidence_ids"]))) for item in cast(list[Mapping[str, object]], raw.get("semantic_uncertainties", []))))


def _evidence_to_data(record: EvidenceRecord) -> dict[str, object]: return {"id": record.id, "content": record.content, "role": record.role, "metadata": _data(record.metadata) if record.metadata is not None else None}
def _evidence_json(records: Sequence[EvidenceRecord]) -> str: return _json([_evidence_to_data(item) for item in records])
def _evidence_from_data(raw: Mapping[str, object]) -> EvidenceRecord: return EvidenceRecord(cast(str, raw["id"]), cast(str, raw["content"]), cast(Any, raw.get("role", "user")), cast(Mapping[str, object] | None, raw.get("metadata")))


def _identity_binding_to_data(binding: "ReviewedIdentityBinding") -> dict[str, object]:
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


def _product_transition_intent_to_data(
    intent: CognitionTransitionIntent,
) -> dict[str, object]:
    return {
        "prior_cognition_id": intent.prior_cognition_id,
        "successor_cognition_id": intent.successor_cognition_id,
        "reason": intent.reason,
        "statement_kind": intent.statement_kind,
    }


def _product_claim_slice_to_data(slice_: ProductClaimSlice) -> dict[str, object]:
    return {
        "claim_id": slice_.claim_id,
        "entity_ids": list(slice_.entity_ids),
        "relationship_ids": list(slice_.relationship_ids),
        "event_ids": list(slice_.event_ids),
        "cognition_ids": list(slice_.cognition_ids),
        "identity_binding_indices": list(slice_.identity_binding_indices),
        "transition_intent_indices": list(slice_.transition_intent_indices),
        "depends_on_claim_ids": list(slice_.depends_on_claim_ids),
    }


def _product_claim_slices_from_payload(
    payload: Mapping[str, object],
) -> tuple[ProductClaimSlice, ...]:
    raw = payload.get("claim_slices")
    if not isinstance(raw, list):
        raise MemoryLoopIntegrityError("product bundle claim slices are invalid")
    slices: list[ProductClaimSlice] = []
    required = {
        "claim_id", "entity_ids", "relationship_ids", "event_ids", "cognition_ids",
        "identity_binding_indices", "transition_intent_indices", "depends_on_claim_ids",
    }
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != required:
            raise MemoryLoopIntegrityError("product bundle claim slices are invalid")
        try:
            slices.append(ProductClaimSlice(
                cast(str, item["claim_id"]),
                tuple(cast(list[str], item["entity_ids"])),
                tuple(cast(list[str], item["relationship_ids"])),
                tuple(cast(list[str], item["event_ids"])),
                tuple(cast(list[str], item["cognition_ids"])),
                tuple(cast(list[int], item["identity_binding_indices"])),
                tuple(cast(list[int], item["transition_intent_indices"])),
                tuple(cast(list[str], item["depends_on_claim_ids"])),
            ))
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError("product bundle claim slices are invalid") from error
    try:
        return MemoryLoop._normalize_product_claim_slices(slices)
    except (TypeError, ValueError) as error:
        raise MemoryLoopIntegrityError("product bundle claim slices are invalid") from error


def _always_included_product_records_from_payload(
    payload: Mapping[str, object],
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    try:
        entities = MemoryLoop._normalize_always_entity_ids(
            cast(list[str], payload["always_include_entity_ids"])
        )
        bindings = MemoryLoop._normalize_always_binding_indices(
            cast(list[int], payload["always_include_identity_binding_indices"])
        )
        return entities, bindings
    except (KeyError, TypeError, ValueError) as error:
        raise MemoryLoopIntegrityError("product bundle always-included records are invalid") from error


def _validate_product_claim_dependency_graph(
    slices: Mapping[str, ProductClaimSlice],
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(claim_id: str) -> None:
        if claim_id in visited:
            return
        if claim_id in visiting:
            raise ValueError("product bundle claim dependencies contain a cycle")
        visiting.add(claim_id)
        for dependency in slices[claim_id].depends_on_claim_ids:
            visit(dependency)
        visiting.remove(claim_id)
        visited.add(claim_id)

    for claim_id in slices:
        visit(claim_id)


def _normalize_selected_claim_ids(
    selected_claim_ids: Sequence[str] | None,
    offered_claim_ids: tuple[str, ...],
    decision: Decision,
) -> tuple[str, ...]:
    if decision == "reject":
        return ()
    if selected_claim_ids is None:
        return offered_claim_ids
    if isinstance(selected_claim_ids, str):
        raise ValueError("selected_claim_ids must be a sequence of claim ids")
    requested = tuple(selected_claim_ids)
    if any(not isinstance(item, str) or not item.strip() for item in requested):
        raise ValueError("selected_claim_ids must contain non-empty strings")
    if len(set(requested)) != len(requested):
        raise ValueError("selected_claim_ids cannot contain duplicates")
    unknown = set(requested) - set(offered_claim_ids)
    if unknown:
        raise ValueError("selected_claim_ids contains an unknown claim")
    requested_set = set(requested)
    return tuple(claim_id for claim_id in offered_claim_ids if claim_id in requested_set)


def _resolve_selected_claim_dependencies(
    selected: Sequence[str],
    slices: Sequence[ProductClaimSlice],
) -> tuple[str, ...]:
    by_claim = {item.claim_id: item for item in slices}
    required: set[str] = set()

    def include(claim_id: str) -> None:
        if claim_id in required:
            return
        required.add(claim_id)
        for dependency in by_claim[claim_id].depends_on_claim_ids:
            include(dependency)

    for claim_id in selected:
        include(claim_id)
    return tuple(item.claim_id for item in slices if item.claim_id in required)


def _filtered_product_bundle_payload(
    delta: WorldDelta,
    record: EvidenceRecord,
    bindings: Sequence["ReviewedIdentityBinding"],
    intents: Sequence[CognitionTransitionIntent],
    slices: Sequence[ProductClaimSlice],
    always_entities: Sequence[str],
    always_binding_indices: Sequence[int],
    applied_claim_ids: Sequence[str],
) -> Mapping[str, object]:
    by_claim = {item.claim_id: item for item in slices}
    selected_slices = [by_claim[item] for item in applied_claim_ids]
    entity_ids = set(always_entities)
    relationship_ids: set[str] = set()
    event_ids: set[str] = set()
    cognition_ids: set[str] = set()
    binding_indices = set(always_binding_indices)
    intent_indices: set[int] = set()
    for item in selected_slices:
        entity_ids.update(item.entity_ids)
        relationship_ids.update(item.relationship_ids)
        event_ids.update(item.event_ids)
        cognition_ids.update(item.cognition_ids)
        binding_indices.update(item.identity_binding_indices)
        intent_indices.update(item.transition_intent_indices)
    selected_delta = WorldDelta(
        delta.world_id,
        delta.source_evidence_ids,
        tuple(item for item in delta.new_entities if item.id in entity_ids),
        tuple(item for item in delta.new_relationships if item.id in relationship_ids),
        tuple(item for item in delta.new_events if item.id in event_ids),
        tuple(item for item in delta.new_cognitions if item.id in cognition_ids),
        formation_traces=tuple(
            item for item in delta.formation_traces if item.cognition_id in cognition_ids
        ),
    )
    return {
        "delta": _delta_to_data(selected_delta),
        "evidence": [_evidence_to_data(record)],
        "identity_bindings": [
            _identity_binding_to_data(item)
            for index, item in enumerate(bindings)
            if index in binding_indices
        ],
        "transition_intents": [
            _product_transition_intent_to_data(item)
            for index, item in enumerate(intents)
            if index in intent_indices
        ],
        # The offered mapping remains separately in the immutable proposal
        # payload.  This filtered payload is only the atomic write subset.
        "claim_slices": [],
        "always_include_entity_ids": [],
        "always_include_identity_binding_indices": [],
    }


def _product_bundle_selection_hash(
    offered_result_hash: str,
    decision: Decision,
    selected_claim_ids: Sequence[str],
    applied_claim_ids: Sequence[str],
    accepted_payload: Mapping[str, object] | None,
) -> str:
    if accepted_payload is None:
        write_payload: object = None
    else:
        write_payload = {
            "delta": accepted_payload["delta"],
            "evidence": accepted_payload["evidence"],
            "identity_bindings": accepted_payload["identity_bindings"],
            "transition_intents": accepted_payload["transition_intents"],
        }
    return _result_hash(
        "product_bundle_selection_v1",
        offered_result_hash,
        decision,
        list(selected_claim_ids),
        list(applied_claim_ids),
        write_payload,
    )


def _product_transition_intents_from_payload(
    payload: Mapping[str, object],
) -> tuple[CognitionTransitionIntent, ...]:
    raw = payload.get("transition_intents")
    if not isinstance(raw, list):
        raise MemoryLoopIntegrityError("product bundle transition payload is invalid")
    intents: list[CognitionTransitionIntent] = []
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {
            "prior_cognition_id",
            "successor_cognition_id",
            "reason",
            "statement_kind",
        }:
            raise MemoryLoopIntegrityError("product bundle transition payload is invalid")
        try:
            intent = CognitionTransitionIntent(
                cast(str, item["prior_cognition_id"]),
                cast(str, item["successor_cognition_id"]),
                cast(str, item["reason"]),
                cast(str, item["statement_kind"]),
            )
        except (TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError("product bundle transition payload is invalid") from error
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                intent.prior_cognition_id,
                intent.successor_cognition_id,
                intent.reason,
                intent.statement_kind,
            )
        ):
            raise MemoryLoopIntegrityError("product bundle transition payload is invalid")
        intents.append(intent)
    try:
        return MemoryLoop._normalize_product_transition_intents(intents)
    except (TypeError, ValueError) as error:
        raise MemoryLoopIntegrityError("product bundle transition payload is invalid") from error


def _structured_claim_statement_kind(cognition: WorldCognition) -> str | None:
    """Read the optional model-owned structured claim without inventing one."""
    claim = getattr(cognition, "structured_claim", None)
    kind = getattr(claim, "statement_kind", None)
    return kind if isinstance(kind, str) else None


def _identity_bindings_from_payload(
    payload: Mapping[str, object],
) -> tuple["ReviewedIdentityBinding", ...]:
    raw = payload.get("identity_bindings")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise MemoryLoopIntegrityError("identity binding payload is invalid")
    from .identity_store import ReviewedIdentityBinding

    bindings: list[ReviewedIdentityBinding] = []
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {
            "entity_id",
            "evidence_id",
            "conversation_id",
            "occurred_at",
            "start_codepoint",
            "end_codepoint",
            "kind_hint",
            "continuity_scope",
        }:
            raise MemoryLoopIntegrityError("identity binding payload is invalid")
        if (
            not all(
                isinstance(item[field], str)
                for field in ("entity_id", "evidence_id", "conversation_id", "occurred_at")
            )
            or type(item["start_codepoint"]) is not int
            or type(item["end_codepoint"]) is not int
            or item["kind_hint"] is not None and not isinstance(item["kind_hint"], str)
            or item["continuity_scope"] is not None
            and not isinstance(item["continuity_scope"], str)
        ):
            raise MemoryLoopIntegrityError("identity binding payload is invalid")
        try:
            binding = ReviewedIdentityBinding(
                item["entity_id"],
                item["evidence_id"],
                item["conversation_id"],
                item["occurred_at"],
                item["start_codepoint"],
                item["end_codepoint"],
                item["kind_hint"],
                item["continuity_scope"],
            )
        except (TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError("identity binding payload is invalid") from error
        if (
            type(binding.start_codepoint) is not int
            or type(binding.end_codepoint) is not int
            or binding.kind_hint is not None and not isinstance(binding.kind_hint, str)
            or binding.continuity_scope is not None
            and not isinstance(binding.continuity_scope, str)
        ):
            raise MemoryLoopIntegrityError("identity binding payload is invalid")
        bindings.append(binding)
    return tuple(bindings)


def _evidence_records_from_payload(payload: Mapping[str, object]) -> tuple[EvidenceRecord, ...]:
    raw_records = payload.get("evidence")
    if not isinstance(raw_records, list) or any(not isinstance(item, Mapping) for item in raw_records):
        raise TypeError("evidence payload shape")
    records = tuple(
        _evidence_from_data(cast(Mapping[str, object], item))
        for item in raw_records
    )
    if any(
        record.role != "user"
        or not isinstance(record.id, str)
        or not record.id.strip()
        or not isinstance(record.content, str)
        or not record.content.strip()
        for record in records
    ):
        raise ValueError("evidence payload value")
    if len({record.id for record in records}) != len(records):
        raise ValueError("duplicate evidence payload id")
    return records


def _correction_prior_ids_from_payload(payload: Mapping[str, object]) -> tuple[str, ...]:
    plural = payload.get("prior_cognition_ids")
    if plural is not None:
        if not isinstance(plural, list) or any(not isinstance(item, str) for item in plural):
            raise MemoryLoopIntegrityError("correction prior cognition ids are invalid")
        return tuple(plural)
    singular = payload.get("prior_cognition_id")
    if not isinstance(singular, str):
        raise MemoryLoopIntegrityError("correction prior cognition id is missing")
    return (singular,)
