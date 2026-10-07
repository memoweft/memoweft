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
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, NoReturn, Sequence, cast
from uuid import uuid4

from ..asking import (
    EvidenceBrief,
    render_conflict_question,
    render_hypothesis_question,
)
from ..confidence import compute_confidence, derive_cred_status
from ..config import CONFIG, resolve_lang
from ..llm import ChatMessage, LLMClient
from ..store.driver import open_db
from ..store.schema import MEMORY_LOOP_SCHEMA_SQL
from ..types import ConfidenceInputs, CredStatus, EvidenceLink
from .delta import WorldDelta
from .evolution import (
    AcceptedEvolutionStep,
    EvolutionStep,
    WorldEvolutionPlan,
    WorldEvolutionValidationError,
    accepted_historical_relationship_ids,
    evolution_plan_from_data,
    evolution_plan_to_data,
    evolution_step_from_data,
    evolution_step_to_data,
    current_relationship_ids,
    historical_relationship_ids,
    project_cognition_lifecycle,
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
    ProvenanceRef,
    parse_memory_query,
    reconstruct_memory,
    render_answer_context,
)

if TYPE_CHECKING:
    from .identity_store import ReviewedIdentityBinding


Decision = Literal["accept", "reject"]
ReviewKind = Literal["addition", "correction", "evolution", "product_bundle"]
RecallEvidenceSubjectState = Literal["current", "historical", "event"]
RecallEvidenceEnvelopeStatus = Literal["available", "legacy", "missing"]
RecallCognitionTimeAuthorityStatus = Literal["available", "legacy", "missing", "mixed"]
RecallCognitionExclusionReason = Literal[
    "superseded",
    "expired",
    "below_effective_confidence",
]
MemoryAskKind = Literal["hypothesis", "conflict"]
MemoryAskReason = Literal["low_confidence", "unresolved_conflict"]
_PRODUCT_TRANSITION_REASONS = frozenset({"corrects", "narrows", "supersedes"})
_SYSTEM_EVIDENCE_KEYS = frozenset(
    {
        "id",
        "subjectId",
        "sourceKind",
        "hostId",
        "originId",
        "occurredAt",
        "recordedAt",
        "rawContent",
        "summary",
        "allowLocalRead",
        "allowCloudRead",
        "allowInference",
        "correctsEvidenceId",
    }
)


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
class RecallEvidenceTrace:
    """One selected World claim linked to its accepted Evidence provenance.

    ``system_evidence`` is the exact durable adapter envelope stored in the
    World ledger.  It is intentionally an internal read result: outward
    surfaces must apply their own local/cloud raw-content disclosure rule.
    Older rows are reported as ``legacy`` rather than being synthesized or
    backfilled during this read-only path.
    """

    subject_kind: Literal["event", "cognition"]
    subject_id: str
    subject_state: RecallEvidenceSubjectState
    evidence_id: str
    relation: Literal["event_reference", "support", "contradict"]
    envelope_status: RecallEvidenceEnvelopeStatus
    system_evidence: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class RecallCognitionLifecycle:
    """One read-only cognition lifecycle decision used by accepted-World Recall."""

    cognition_id: str
    time_authority_status: RecallCognitionTimeAuthorityStatus
    corroborating_evidence_ids: tuple[str, ...]
    last_corroborated_at: str | None
    stored_confidence: int
    effective_confidence: int | None
    is_current: bool | None
    is_expired: bool | None
    active_salience: int | None
    recall_eligible: bool
    exclusion_reason: RecallCognitionExclusionReason | None


@dataclass(frozen=True, slots=True)
class MemoryAskProposal:
    """One host-facing, read-only clarification candidate from accepted Recall.

    This projection is not an assistant turn, Evidence, or a delivery receipt.
    The host remains responsible for deciding whether to speak it; a later user
    answer must enter through the ordinary durable Evidence and Apply chain.
    """

    cognition_id: str
    kind: MemoryAskKind
    reason: MemoryAskReason
    content: str
    question: str
    support_evidence: tuple[EvidenceBrief, ...]
    contradict_evidence: tuple[EvidenceBrief, ...]
    stored_confidence: int
    effective_confidence: int
    cred_status: CredStatus


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
class DecisionReceipt:
    """Integrity-bound terminal outcome for one durable proposal.

    Unlike ``MemoryView``, this record describes the exact state at the moment
    the proposal became terminal.  It deliberately remains valid after later
    accepted decisions advance the current world revision.
    """

    proposal_id: str
    offered_result_hash: str
    effective_decision: Decision
    world_revision: int
    snapshot_hash: str
    decided_at: str
    receipt_hash: str

    @property
    def revision(self) -> int:
        """Compatibility name for callers projecting a terminal World state."""

        return self.world_revision


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
        current_relationships = current_relationship_ids(
            self.graph,
            self.evolution_steps,
        )
        return tuple(
            cognition
            for cognition_id, cognition in self.graph.cognitions.items()
            if cognition_id not in self.superseded_cognition_ids
            and not (
                cognition.target.kind == "relationship"
                and cognition.target.id not in current_relationships
            )
        )

    @property
    def current_relationships(self) -> tuple[Relationship, ...]:
        current = current_relationship_ids(self.graph, self.evolution_steps)
        return tuple(
            relationship
            for relationship in self.graph.relationships.values()
            if relationship.id in current
        )

    @property
    def historical_relationships(self) -> tuple[Relationship, ...]:
        historical = historical_relationship_ids(self.graph, self.evolution_steps)
        return tuple(
            relationship
            for relationship in self.graph.relationships.values()
            if relationship.id in historical
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
    recalled_history_relationships: tuple[Relationship, ...] = ()
    evidence_traces: tuple[RecallEvidenceTrace, ...] = ()
    cognition_lifecycles: tuple[RecallCognitionLifecycle, ...] = ()


class MemoryLoop:
    """SQLite-backed prepared-change → terminal decision → recall/correction loop.

    ``initial_graph`` is only used when the database is new.  A reopened loop
    reconstructs its graph exclusively from the canonical snapshot, and fails
    closed if that snapshot was edited behind its back.
    """

    def __init__(
        self,
        database: str | Path | sqlite3.Connection,
        initial_graph: MemoryWorldGraph,
        *,
        recall_clock: Callable[[], str] | None = None,
    ) -> None:
        if isinstance(database, sqlite3.Connection):
            self._database: str | None = None
            self._conn = database
            self._owns_connection = False
            # Manual transaction control on borrowed connections: the sqlite3
            # default isolation_level opens implicit transactions, which can make
            # decide()'s BEGIN IMMEDIATE fail ("cannot start a transaction within
            # a transaction") and lets a ROLLBACK leak into the caller's
            # transaction. Normalize only when nothing is open — the setter
            # COMMITs any active transaction, which would leak the other way.
            # A connection that is already mid-transaction stays untouched and
            # decide() contains itself in a SAVEPOINT instead.
            if self._conn.isolation_level is not None and not self._conn.in_transaction:
                self._conn.isolation_level = None
        else:
            self._database = str(database)
            self._conn = open_db(self._database)
            self._owns_connection = True
        self._conn.row_factory = sqlite3.Row
        self._recall_clock = recall_clock or (
            lambda: datetime.now(timezone.utc).isoformat()
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        row = self._conn.execute(
            "SELECT snapshot_json, snapshot_hash FROM memory_state WHERE singleton = 1"
        ).fetchone()
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
        evolution_steps: Sequence[EvolutionStep] = (),
        cognition_updates: Sequence[WorldCognition] = (),
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
        graph, revision, snapshot_hash = self._load_graph()
        if (
            isinstance(review_payload, Mapping)
            and review_payload.get("kind") == "product-bundle"
            and review_payload.get("baseWorldHash") != snapshot_hash
        ):
            raise MemoryLoopError("product bundle base World hash is stale")
        records = self._normalize_evidence(evidence_records)
        if len(records) != 1:
            raise ValueError(
                "product bundle requires exactly one current user EvidenceRecord"
            )
        record = records[0]
        if tuple(delta.source_evidence_ids) != (record.id,):
            raise ValueError(
                "product bundle delta must reference exactly the current Evidence id"
            )
        self._check_evidence_conflicts(records)
        steps = self._normalize_product_evolution_steps(evolution_steps)
        updates = self._normalize_product_cognition_updates(cognition_updates)
        preview = self._validate_product_evolution_steps(
            graph,
            delta,
            record,
            steps,
            updates,
        )
        bindings = self._normalize_identity_bindings(identity_bindings)
        self._validate_adapter_structured_evaluation_contract(
            delta,
            records,
            review_payload,
            graph,
            identity_bindings=bindings,
            evolution_steps=steps,
            cognition_updates=updates,
            current_snapshot_hash=snapshot_hash,
            require_current=True,
        )
        self._validate_identity_bindings(bindings, records, preview)
        intents = self._normalize_product_transition_intents(transition_intents)
        self._validate_product_transition_intents(graph, delta, intents)
        slices = self._normalize_product_claim_slices(claim_slices)
        if (steps or updates) and slices:
            raise ValueError("product evolution does not support claim selection")
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
        evolution_data = [evolution_step_to_data(item) for item in steps]
        update_data = [_cognition_to_data(item) for item in updates]
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
        result_hash_parts: list[object] = [
            "product_bundle",
            _delta_json(delta),
            _evidence_json(records),
            _json(binding_data),
            _json(intent_data),
        ]
        if evolution_data:
            payload["evolution_steps"] = evolution_data
            result_hash_parts.append(_json(evolution_data))
        if update_data:
            payload["cognition_updates"] = update_data
            result_hash_parts.append(_json(update_data))
        result_hash_parts.extend(
            (
                _json(slice_data),
                _json(list(always_entities)),
                _json(list(always_binding_indices)),
                review_payload,
            )
        )
        return self._insert_review(
            kind="product_bundle",
            base_revision=revision,
            result_hash=_result_hash(*result_hash_parts),
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
        if any(item.structured_claim is not None for item in priors):
            raise MemoryLoopError(
                "structured cognition correction requires a typed evolution plan"
            )
        if not correction_text.strip():
            raise ValueError("correction_text must not be empty")
        record = self._normalize_evidence((new_user_evidence,))[0]
        self._check_evidence_conflicts((record,))
        prior = priors[0]
        confidence = compute_confidence(
            ConfidenceInputs(prior.content_type, "stated", 1, 0)
        )
        replacement = replace(
            prior,
            id=f"cognition:correction:{uuid4()}",
            content=correction_text,
            formed_by="stated",
            confidence=confidence,
            cred_status=derive_cred_status(
                confidence, 0, prior.content_type, support_count=1
            ),
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
                "correction",
                prior_ids,
                _cognition_json(replacement),
                _evidence_json((record,)),
                review_payload,
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
        identity_bindings: Sequence["ReviewedIdentityBinding"] = (),
    ) -> PendingReview:
        """Stage one typed, fully previewed world-evolution plan for review."""

        if not isinstance(plan, WorldEvolutionPlan):
            raise TypeError("plan must be a WorldEvolutionPlan")
        if any(step.kind == "relationship_successor" for step in plan.steps):
            raise ValueError(
                "relationship successor steps must use the atomic product bundle"
            )
        graph, revision, base_snapshot_hash = self._load_graph()
        records = self._normalize_evidence(evidence_records)
        self._check_evidence_conflicts(records)
        eligible = self._evidence_ids() | {record.id for record in records}
        accepted_steps = self._accepted_evolution_steps()
        preview = plan.apply_to(
            graph,
            eligible,
            superseded_cognition_ids=self._superseded_ids(),
            known_transition_ids=frozenset(item.step.id for item in accepted_steps),
        )
        bindings = self._normalize_identity_bindings(identity_bindings)
        self._validate_identity_bindings(bindings, records, preview)
        self._validate_evolution_correction_semantics(plan, graph)
        self._validate_adapter_typed_correction_contract(
            plan,
            records,
            review_payload,
            graph,
            identity_bindings=bindings,
            current_snapshot_hash=base_snapshot_hash,
            accepted_evolution_steps=accepted_steps,
        )
        plan_data = evolution_plan_to_data(plan)
        binding_data = [_identity_binding_to_data(item) for item in bindings]
        result_parts: list[object] = [
            "evolution",
            _json(plan_data),
            _evidence_json(records),
        ]
        if bindings:
            result_parts.append(_json(binding_data))
        result_parts.append(review_payload)
        payload: dict[str, object] = {
            "plan": plan_data,
            "evidence": [_evidence_to_data(record) for record in records],
        }
        if bindings:
            payload["identity_bindings"] = binding_data
        return self._insert_review(
            kind="evolution",
            base_revision=revision,
            result_hash=_result_hash(*result_parts),
            payload=payload,
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
        """Atomically finalize the exact hash-bound prepared change."""
        if decision not in ("accept", "reject"):
            raise ValueError("decision must be 'accept' or 'reject'")
        # A borrowed connection may already sit inside the caller's transaction;
        # contain decide() in a SAVEPOINT there so a failure rolls back only the
        # loop's own writes and never the caller's outer transaction.
        use_savepoint = not self._owns_connection and self._conn.in_transaction
        try:
            self._conn.execute(
                "SAVEPOINT memoweft_decide" if use_savepoint else "BEGIN IMMEDIATE"
            )
            review = self._conn.execute(
                "SELECT * FROM proposals WHERE id = ?", (review_id,)
            ).fetchone()
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
                raise MemoryLoopIntegrityError(
                    "review payload is not valid JSON"
                ) from error
            if not isinstance(payload_raw, Mapping):
                raise MemoryLoopIntegrityError("review payload is invalid")
            if review_payload_raw is not None and not isinstance(
                review_payload_raw, Mapping
            ):
                raise MemoryLoopIntegrityError("review display payload is invalid")
            kind = cast(str, review["kind"])
            recomputed_hash = self._recompute_review_result_hash(
                kind,
                cast(Mapping[str, object], payload_raw),
                cast(Mapping[str, object] | None, review_payload_raw),
            )
            if recomputed_hash != cast(str, review["result_hash"]):
                raise MemoryLoopIntegrityError(
                    "review result hash does not match its stored payload"
                )
            graph, revision, snapshot_hash = self._load_graph()
            accepted_payload: Mapping[str, object] = cast(
                Mapping[str, object], payload_raw
            )
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
                raise ValueError(
                    "selected_claim_ids are only valid for product bundle reviews"
                )
            if effective_decision == "accept":
                if int(review["base_revision"]) != revision:
                    raise ReviewStateError("stale review")
                if (
                    kind == "product_bundle"
                    and isinstance(review_payload_raw, Mapping)
                    and review_payload_raw.get("kind") == "product-bundle"
                    and review_payload_raw.get("baseWorldHash") != snapshot_hash
                ):
                    raise MemoryLoopIntegrityError(
                        "product bundle base World hash no longer matches"
                    )
                if kind == "product_bundle":
                    delta_raw = accepted_payload.get("delta")
                    if not isinstance(delta_raw, Mapping):
                        raise MemoryLoopIntegrityError(
                            "product bundle delta is missing"
                        )
                    self._validate_adapter_structured_evaluation_contract(
                        _delta_from_data(cast(Mapping[str, object], delta_raw)),
                        _evidence_records_from_payload(accepted_payload),
                        cast(Mapping[str, object] | None, review_payload_raw),
                        graph,
                        identity_bindings=_identity_bindings_from_payload(
                            accepted_payload
                        ),
                        evolution_steps=_product_evolution_steps_from_payload(
                            accepted_payload
                        ),
                        cognition_updates=_product_cognition_updates_from_payload(
                            accepted_payload
                        ),
                        current_snapshot_hash=snapshot_hash,
                        require_current=True,
                    )
                old_graph = deepcopy(graph)
                old_revision = revision
                self._accept_payload(
                    cast(ReviewKind, kind), accepted_payload, graph, revision + 1
                )
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
                        {item.id: (item.content, item.metadata) for item in records},
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
            self._conn.execute(
                "UPDATE proposals SET status = ? WHERE id = ?",
                (effective_decision, review_id),
            )
            receipt = DecisionReceipt(
                review_id,
                result_hash,
                effective_decision,
                revision,
                snapshot_hash,
                datetime.now(timezone.utc).isoformat(),
                "",
            )
            receipt = replace(receipt, receipt_hash=_decision_receipt_hash(receipt))
            self._conn.execute(
                "INSERT INTO proposal_decision_receipts("
                "proposal_id, offered_result_hash, effective_decision, world_revision, "
                "snapshot_hash, decided_at, receipt_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt.proposal_id,
                    receipt.offered_result_hash,
                    receipt.effective_decision,
                    receipt.world_revision,
                    receipt.snapshot_hash,
                    receipt.decided_at,
                    receipt.receipt_hash,
                ),
            )
            self._conn.execute(
                "RELEASE SAVEPOINT memoweft_decide" if use_savepoint else "COMMIT"
            )
        except Exception:
            if use_savepoint:
                self._conn.execute("ROLLBACK TO SAVEPOINT memoweft_decide")
                self._conn.execute("RELEASE SAVEPOINT memoweft_decide")
            else:
                self._conn.execute("ROLLBACK")
            raise
        view = self.view()
        return replace(
            view, decision_receipt=selection.receipt if selection is not None else None
        )

    def decision_receipt(self, proposal_id: str) -> DecisionReceipt | None:
        """Read one terminal decision without projecting it onto today's world.

        The read verifies the receipt bytes, its bound proposal result hash and
        final status, and the only current-world invariant that remains valid
        after later writes: the world cannot move backwards.  A same-revision
        receipt must still match the current snapshot hash exactly.
        """

        row = self._conn.execute(
            "SELECT receipt.proposal_id, receipt.offered_result_hash, "
            "receipt.effective_decision, receipt.world_revision, receipt.snapshot_hash, "
            "receipt.decided_at, receipt.receipt_hash, proposal.result_hash, proposal.status, "
            "proposal.base_revision "
            "FROM proposal_decision_receipts AS receipt "
            "LEFT JOIN proposals AS proposal ON proposal.id = receipt.proposal_id "
            "WHERE receipt.proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        receipt = self._decision_receipt_from_row(row)
        stored_result_hash = row["result_hash"]
        status = row["status"]
        if (
            not isinstance(stored_result_hash, str)
            or not isinstance(status, str)
            or stored_result_hash != receipt.offered_result_hash
            or status != receipt.effective_decision
        ):
            raise MemoryLoopIntegrityError(
                "decision receipt disagrees with its proposal"
            )
        if receipt.effective_decision == "accept" and (
            not isinstance(row["base_revision"], int)
            or receipt.world_revision != row["base_revision"] + 1
        ):
            raise MemoryLoopIntegrityError(
                "accepted decision receipt has an invalid world revision"
            )
        _, current_revision, current_snapshot_hash = self._load_graph()
        if current_revision < receipt.world_revision:
            raise MemoryLoopIntegrityError(
                "decision receipt is ahead of the current world"
            )
        if (
            current_revision == receipt.world_revision
            and current_snapshot_hash != receipt.snapshot_hash
        ):
            raise MemoryLoopIntegrityError("decision receipt snapshot hash mismatch")
        return receipt

    def view(self) -> MemoryView:
        graph, revision, snapshot_hash = self._load_graph()
        pending = tuple(
            self._review_from_row(row)
            for row in self._conn.execute(
                "SELECT * FROM proposals WHERE status = 'pending' ORDER BY rowid"
            )
        )
        transitions = tuple(
            CognitionTransition(
                id=cast(str, row["id"]),
                prior_cognition_id=cast(str, row["prior_cognition_id"]),
                replacement_cognition_id=cast(str, row["replacement_cognition_id"]),
                reason=cast(str, row["reason"]),
                revision=int(row["revision"]),
            )
            for row in self._conn.execute(
                "SELECT * FROM cognition_transitions ORDER BY rowid"
            )
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

        _, reconstruction, _ = self._reconstruct(query, resolved_entity_ids)
        return reconstruction

    def ask(
        self,
        query: str,
        answer_client: LLMClient | None = None,
        *,
        resolved_entity_ids: Sequence[str] = (),
    ) -> MemoryAnswer:
        """Recall deterministically; only call an answerer for one resolved world."""

        view, reconstruction, cognition_lifecycles = self._reconstruct(
            query, resolved_entity_ids
        )
        if reconstruction.status == "unsupported":
            return MemoryAnswer(
                "no_memory",
                None,
                (),
                (),
                (),
                (),
                (),
                (),
                (),
                reconstruction,
                cognition_lifecycles=cognition_lifecycles,
            )
        if reconstruction.status == "ambiguous":
            return MemoryAnswer(
                "ambiguous",
                None,
                (),
                (),
                (),
                (),
                (),
                (),
                (),
                reconstruction,
                cognition_lifecycles=cognition_lifecycles,
            )
        graph = view.graph
        entities = tuple(
            graph.entities[item_id] for item_id in reconstruction.entity_ids
        )
        relationships = tuple(
            graph.relationships[item_id] for item_id in reconstruction.relationship_ids
        )
        historical_relationships = tuple(
            graph.relationships[item_id]
            for item_id in reconstruction.historical_relationship_ids
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
        evidence_traces = self._recall_evidence_traces(reconstruction, evidence)
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
                historical_relationships,
                evidence_traces,
                cognition_lifecycles,
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
                ChatMessage(
                    "user", f"Question: {query}\n\nReconstructed memory:\n{context}"
                ),
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
            historical_relationships,
            evidence_traces,
            cognition_lifecycles,
        )

    def propose_ask(
        self,
        query: str,
        *,
        resolved_entity_ids: Sequence[str] = (),
    ) -> MemoryAskProposal | None:
        """Project at most one clarification from this query's accepted Recall.

        Selection is deterministic and deliberately stricter than ordinary
        recall: the cognition must be current, lifecycle-eligible, backed by a
        complete modern Evidence envelope, and authorized for local reading
        and inference.  Explicit conflict revisiting wins over an ordinary
        low-confidence hypothesis because it represents unresolved opposing
        Evidence, but remains a distinct observable reason.

        The method performs no model call and no write.  Repeated calls return
        the same projection at the same World and clock; they do not claim that
        the host delivered the question or maintain a hidden ``asked_at``.
        """

        if CONFIG.asking.max_asks < 1:
            return None
        view, reconstruction, lifecycles = self._reconstruct(
            query, resolved_entity_ids
        )
        if reconstruction.status != "resolved":
            return None
        lifecycle_by_id = {item.cognition_id: item for item in lifecycles}
        candidates: list[tuple[int, int, str, MemoryAskProposal]] = []
        for cognition_id in reconstruction.current_cognition_ids:
            cognition = view.graph.cognitions[cognition_id]
            lifecycle = lifecycle_by_id.get(cognition_id)
            if (
                lifecycle is None
                or lifecycle.time_authority_status != "available"
                or not lifecycle.recall_eligible
                or lifecycle.effective_confidence is None
            ):
                continue
            evidence = self._asking_evidence(cognition)
            if evidence is None:
                continue
            support, contradict = evidence
            if cognition.cred_status == "conflicted":
                if not support or not contradict:
                    continue
                kind: MemoryAskKind = "conflict"
                reason: MemoryAskReason = "unresolved_conflict"
                priority = 0
                question = render_conflict_question(
                    cognition.content,
                    support,
                    contradict,
                    resolve_lang(),
                )
            elif (
                cognition.content_type == "hypothesis"
                and cognition.cred_status in CONFIG.asking.askable_statuses
                and CONFIG.asking.confidence_band.min
                <= lifecycle.effective_confidence
                <= CONFIG.asking.confidence_band.max
            ):
                if not support or contradict:
                    continue
                kind = "hypothesis"
                reason = "low_confidence"
                priority = 1
                question = render_hypothesis_question(
                    cognition.content,
                    support,
                    resolve_lang(),
                )
            else:
                continue
            proposal = MemoryAskProposal(
                cognition.id,
                kind,
                reason,
                cognition.content,
                question,
                support,
                contradict,
                cognition.confidence,
                lifecycle.effective_confidence,
                cognition.cred_status,
            )
            candidates.append(
                (
                    priority,
                    -lifecycle.effective_confidence,
                    cognition.id,
                    proposal,
                )
            )
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[:3])[3]

    def _asking_evidence(
        self,
        cognition: WorldCognition,
    ) -> tuple[tuple[EvidenceBrief, ...], tuple[EvidenceBrief, ...]] | None:
        """Hydrate one complete, single-subject, locally authorized source set."""

        evidence_ids = tuple(dict.fromkeys(item.evidence_id for item in cognition.sources))
        records = self._evidence_for_ids(evidence_ids)
        if len(records) != len(evidence_ids):
            return None
        by_id = {record.id: record for record in records}
        briefs: list[tuple[Literal["support", "contradict"], EvidenceBrief]] = []
        subject_ids: set[str] = set()
        for source in cognition.sources:
            record = by_id.get(source.evidence_id)
            if record is None:
                return None
            envelope = _validated_recall_system_evidence(record)
            if envelope is None:
                return None
            if (
                envelope.get("allowLocalRead") is not True
                or envelope.get("allowInference") is not True
            ):
                return None
            subject_id = envelope.get("subjectId")
            summary = envelope.get("summary")
            raw_content = envelope.get("rawContent")
            assert isinstance(subject_id, str)
            assert isinstance(summary, str)
            assert isinstance(raw_content, str)
            subject_ids.add(subject_id)
            briefs.append(
                (
                    source.relation,
                    EvidenceBrief(source.evidence_id, summary or raw_content),
                )
            )
        if len(subject_ids) != 1:
            return None
        return (
            tuple(brief for relation, brief in briefs if relation == "support"),
            tuple(brief for relation, brief in briefs if relation == "contradict"),
        )

    def _reconstruct(
        self,
        query: str,
        resolved_entity_ids: Sequence[str],
    ) -> tuple[
        MemoryView,
        MemoryReconstruction,
        tuple[RecallCognitionLifecycle, ...],
    ]:
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
        now = self._validated_recall_now()
        inactive_ids: set[str] = set()
        lifecycle_by_id: dict[str, RecallCognitionLifecycle] = {}
        for _ in range(len(view.graph.cognitions) + 1):
            reconstruction = reconstruct_memory(
                parsed,
                view.graph,
                superseded_cognition_ids=view.superseded_cognition_ids,
                inactive_cognition_ids=frozenset(inactive_ids),
                cognition_lineage=lineage,
                accepted_evolution_steps=view.evolution_steps,
            )
            candidate_targets = {item.target for item in reconstruction.candidates}
            selected_ids = {
                *reconstruction.current_cognition_ids,
                *reconstruction.historical_cognition_ids,
                *(
                    cognition.id
                    for cognition in view.graph.cognitions.values()
                    if cognition.target in candidate_targets
                ),
            }
            for cognition_id in sorted(selected_ids - set(lifecycle_by_id)):
                lifecycle_by_id[cognition_id] = self._project_recall_cognition_lifecycle(
                    view,
                    view.graph.cognitions[cognition_id],
                    now=now,
                )
            newly_inactive = {
                cognition_id
                for cognition_id, lifecycle in lifecycle_by_id.items()
                if lifecycle.exclusion_reason
                in {"expired", "below_effective_confidence"}
            } - inactive_ids
            if not newly_inactive:
                return (
                    view,
                    reconstruction,
                    tuple(lifecycle_by_id[item] for item in sorted(lifecycle_by_id)),
                )
            inactive_ids.update(newly_inactive)
        raise MemoryLoopIntegrityError("recall cognition lifecycle filtering did not converge")

    def _validated_recall_now(self) -> str:
        try:
            now = self._recall_clock()
        except Exception as error:
            raise MemoryLoopIntegrityError(
                "recall cognition lifecycle time is unavailable"
            ) from error
        if not isinstance(now, str) or not now or len(now) > 64:
            raise MemoryLoopIntegrityError("recall cognition lifecycle time is invalid")
        try:
            parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
        except ValueError as error:
            raise MemoryLoopIntegrityError(
                "recall cognition lifecycle time is invalid"
            ) from error
        if parsed.tzinfo is None:
            raise MemoryLoopIntegrityError("recall cognition lifecycle time is invalid")
        return now

    def _project_recall_cognition_lifecycle(
        self,
        view: MemoryView,
        cognition: WorldCognition,
        *,
        now: str,
    ) -> RecallCognitionLifecycle:
        support_ids = tuple(
            dict.fromkeys(
                source.evidence_id
                for source in cognition.sources
                if source.relation == "support"
            )
        )
        records = {record.id: record for record in self._evidence_for_ids(support_ids)}
        statuses: list[Literal["available", "legacy", "missing"]] = []
        recorded_times: list[str] = []
        for evidence_id in support_ids:
            record = records.get(evidence_id)
            if record is None:
                statuses.append("missing")
                continue
            system_evidence = _validated_recall_system_evidence(record)
            if system_evidence is None:
                statuses.append("legacy")
                continue
            statuses.append("available")
            recorded_at = system_evidence.get("recordedAt")
            assert isinstance(recorded_at, str)
            recorded_times.append(recorded_at)
        if not statuses:
            time_status: RecallCognitionTimeAuthorityStatus = "missing"
        elif len(set(statuses)) == 1:
            time_status = statuses[0]
        else:
            time_status = "mixed"
        is_superseded = cognition.id in view.superseded_cognition_ids
        if time_status != "available":
            return RecallCognitionLifecycle(
                cognition.id,
                time_status,
                support_ids,
                None,
                cognition.confidence,
                None,
                None,
                None,
                None,
                not is_superseded,
                "superseded" if is_superseded else None,
            )
        last_corroborated_at = max(
            recorded_times,
            key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")),
        )
        try:
            projected = project_cognition_lifecycle(
                cognition,
                is_superseded=is_superseded,
                now=now,
                last_corroborated_at=last_corroborated_at,
            )
        except WorldEvolutionValidationError as error:
            raise MemoryLoopIntegrityError(
                "recall cognition lifecycle time is invalid"
            ) from error
        exclusion_reason: RecallCognitionExclusionReason | None = None
        if is_superseded:
            exclusion_reason = "superseded"
        elif projected.is_expired:
            exclusion_reason = "expired"
        elif projected.effective_confidence < CONFIG.min_effective_confidence:
            exclusion_reason = "below_effective_confidence"
        return RecallCognitionLifecycle(
            cognition.id,
            "available",
            support_ids,
            last_corroborated_at,
            cognition.confidence,
            projected.effective_confidence,
            projected.is_current,
            projected.is_expired,
            projected.active_salience,
            exclusion_reason is None,
            exclusion_reason,
        )

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
        row = self._conn.execute(
            "SELECT revision, snapshot_json, snapshot_hash FROM memory_state WHERE singleton = 1"
        ).fetchone()
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
        self,
        *,
        kind: ReviewKind,
        base_revision: int,
        result_hash: str,
        payload: Mapping[str, object],
        review_payload: Mapping[str, object] | None,
    ) -> PendingReview:
        review_id = f"review:{uuid4()}"
        self._conn.execute(
            "INSERT INTO proposals(id, kind, base_revision, result_hash, payload_json, review_payload_json, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (
                review_id,
                kind,
                base_revision,
                result_hash,
                _json(payload),
                _json(review_payload) if review_payload is not None else None,
            ),
        )
        return PendingReview(
            review_id, kind, result_hash, base_revision, review_payload
        )

    def _accept_payload(
        self,
        kind: ReviewKind,
        payload: Mapping[str, object],
        graph: MemoryWorldGraph,
        next_revision: int,
    ) -> None:
        records = _evidence_records_from_payload(payload)
        self._check_evidence_conflicts(records)
        for record in records:
            self._conn.execute(
                "INSERT OR IGNORE INTO evidence_ledger(id, content, payload_json) VALUES (?, ?, ?)",
                (record.id, record.content, _json(_evidence_to_data(record))),
            )
        if kind == "addition":
            delta = _delta_from_data(cast(Mapping[str, object], payload["delta"]))
            graph_after = delta.apply_to(graph, self._evidence_ids())
            graph.entities, graph.relationships, graph.events, graph.cognitions = (
                graph_after.entities,
                graph_after.relationships,
                graph_after.events,
                graph_after.cognitions,
            )
            return
        if kind == "product_bundle":
            delta_raw = payload.get("delta")
            if not isinstance(delta_raw, Mapping):
                raise MemoryLoopIntegrityError("product bundle delta is missing")
            delta = _delta_from_data(cast(Mapping[str, object], delta_raw))
            if len(records) != 1 or tuple(delta.source_evidence_ids) != (
                records[0].id,
            ):
                raise MemoryLoopIntegrityError(
                    "product bundle Evidence boundary is invalid"
                )
            intents = _product_transition_intents_from_payload(payload)
            self._validate_product_transition_intents(graph, delta, intents)
            evolution_steps = _product_evolution_steps_from_payload(payload)
            cognition_updates = _product_cognition_updates_from_payload(payload)
            graph_after = self._validate_product_evolution_steps(
                graph,
                delta,
                records[0],
                evolution_steps,
                cognition_updates,
            )
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
                if any(step.kind == "relationship_successor" for step in plan.steps):
                    raise WorldEvolutionValidationError(
                        ("relationship_successor.product_bundle_required",)
                    )
                self._validate_evolution_correction_semantics(plan, graph)
                accepted_steps = self._accepted_evolution_steps()
                graph_after = plan.apply_to(
                    graph,
                    self._evidence_ids(),
                    superseded_cognition_ids=self._superseded_ids(),
                    known_transition_ids=frozenset(
                        item.step.id for item in accepted_steps
                    ),
                )
            except WorldEvolutionValidationError as error:
                raise MemoryLoopIntegrityError(
                    "evolution plan no longer matches its reviewed contract"
                ) from error
            graph.entities = graph_after.entities
            graph.relationships = graph_after.relationships
            graph.events = graph_after.events
            graph.cognitions = graph_after.cognitions
            for prior_id, successor_id, reason in superseding_cognition_pairs(plan):
                self._conn.execute(
                    "INSERT INTO cognition_transitions(id, prior_cognition_id, replacement_cognition_id, reason, revision) VALUES (?, ?, ?, ?, ?)",
                    (
                        f"transition:{uuid4()}",
                        prior_id,
                        successor_id,
                        reason,
                        next_revision,
                    ),
                )
            return
        if kind != "correction":
            raise MemoryLoopIntegrityError(f"unsupported review kind: {kind}")
        prior_ids = _correction_prior_ids_from_payload(payload)
        try:
            priors = self._validated_correction_priors(graph, prior_ids)
        except MemoryLoopError as error:
            raise ReviewStateError(
                "correction prior cognition is stale or incoherent"
            ) from error
        if (
            len(records) != 1
            or records[0].role != "user"
            or not records[0].id.strip()
            or not records[0].content.strip()
        ):
            raise MemoryLoopIntegrityError(
                "correction must contain exactly one non-empty user evidence record"
            )
        replacement = _cognition_from_data(
            cast(Mapping[str, object], payload["replacement"])
        )
        prior = priors[0]
        if any(item.structured_claim is not None for item in priors):
            raise MemoryLoopIntegrityError(
                "structured cognition correction cannot use the legacy replacement payload"
            )
        confidence = compute_confidence(
            ConfidenceInputs(prior.content_type, "stated", 1, 0)
        )
        expected_sources = (EvidenceLink(records[0].id, "support"),)
        if (
            replacement.target != prior.target
            or replacement.perspective != prior.perspective
            or replacement.content_type != prior.content_type
            or replacement.scope != prior.scope
            or replacement.formed_by != "stated"
            or replacement.confidence != confidence
            or replacement.cred_status
            != derive_cred_status(confidence, 0, prior.content_type, support_count=1)
            or replacement.sources != expected_sources
            or replacement.invalid_at is not None
        ):
            raise MemoryLoopIntegrityError(
                "correction replacement does not match its reviewed prior bundle"
            )
        graph.add_cognition(replacement)
        for prior_id in prior_ids:
            self._conn.execute(
                "INSERT INTO cognition_transitions(id, prior_cognition_id, replacement_cognition_id, reason, revision) VALUES (?, ?, ?, ?, ?)",
                (
                    f"transition:{uuid4()}",
                    prior_id,
                    replacement.id,
                    "correction",
                    next_revision,
                ),
            )
        return

    def _validated_correction_priors(
        self, graph: MemoryWorldGraph, prior_cognition_ids: Sequence[str]
    ) -> tuple[WorldCognition, ...]:
        prior_ids = tuple(prior_cognition_ids)
        if not 1 <= len(prior_ids) <= 4:
            raise MemoryLoopError(
                "correction bundle must contain between 1 and 4 prior cognition ids"
            )
        if any(
            not isinstance(prior_id, str) or not prior_id.strip()
            for prior_id in prior_ids
        ):
            raise MemoryLoopError(
                "correction bundle contains an invalid prior cognition id"
            )
        if len(set(prior_ids)) != len(prior_ids):
            raise MemoryLoopError(
                "correction bundle contains duplicate prior cognition ids"
            )
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
            "SELECT id, kind, base_revision, result_hash, payload_json, "
            "review_payload_json FROM proposals "
            "WHERE kind IN ('evolution', 'product_bundle') AND status = 'accept' "
            "ORDER BY base_revision, rowid"
        )
        for row in rows:
            try:
                payload = json.loads(cast(str, row["payload_json"]))
                if not isinstance(payload, Mapping):
                    raise TypeError("accepted evolution payload shape")
                review_payload_raw = row["review_payload_json"]
                review_payload = (
                    json.loads(cast(str, review_payload_raw))
                    if review_payload_raw is not None
                    else None
                )
                if review_payload is not None and not isinstance(
                    review_payload, Mapping
                ):
                    raise TypeError("accepted evolution review payload shape")
                recomputed = self._recompute_review_result_hash(
                    cast(str, row["kind"]),
                    cast(Mapping[str, object], payload),
                    cast(Mapping[str, object] | None, review_payload),
                )
                if recomputed != cast(str, row["result_hash"]):
                    raise TypeError("accepted evolution result hash mismatch")
                if row["kind"] == "evolution":
                    if not isinstance(payload.get("plan"), Mapping):
                        raise TypeError("accepted evolution payload shape")
                    steps = evolution_plan_from_data(
                        cast(Mapping[str, object], payload["plan"])
                    ).steps
                else:
                    steps = _product_evolution_steps_from_payload(payload)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise MemoryLoopIntegrityError(
                    "accepted evolution payload is invalid"
                ) from error
            accepted.extend(
                AcceptedEvolutionStep(
                    review_id=cast(str, row["id"]),
                    revision=int(row["base_revision"]) + 1,
                    step=step,
                )
                for step in steps
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
                if set(payload) not in (
                    {"delta", "evidence"},
                    allowed,
                ) or not isinstance(payload.get("delta"), Mapping):
                    raise TypeError("addition payload shape")
                delta = _delta_from_data(cast(Mapping[str, object], payload["delta"]))
                bindings = _identity_bindings_from_payload(payload)
                addition_parts: list[object] = [
                    "addition",
                    _delta_json(delta),
                    _evidence_json(records),
                ]
                if bindings:
                    addition_parts.append(
                        _json([_identity_binding_to_data(item) for item in bindings])
                    )
                addition_parts.append(review_payload)
                return _result_hash(*addition_parts)
            if kind == "product_bundle":
                base_allowed = {
                    "delta",
                    "evidence",
                    "identity_bindings",
                    "transition_intents",
                    "claim_slices",
                    "always_include_entity_ids",
                    "always_include_identity_binding_indices",
                }
                relationship_evolution_allowed = base_allowed | {"evolution_steps"}
                cognition_evolution_allowed = relationship_evolution_allowed | {
                    "cognition_updates"
                }
                payload_without_decision = {
                    key: value for key, value in payload.items() if key != "selection"
                }
                if frozenset(payload_without_decision) not in {
                    frozenset(base_allowed),
                    frozenset(relationship_evolution_allowed),
                    frozenset(cognition_evolution_allowed),
                } or not isinstance(payload.get("delta"), Mapping):
                    raise TypeError("product bundle payload shape")
                delta = _delta_from_data(cast(Mapping[str, object], payload["delta"]))
                if len(records) != 1 or tuple(delta.source_evidence_ids) != (
                    records[0].id,
                ):
                    raise TypeError("product bundle Evidence boundary")
                bindings = _identity_bindings_from_payload(payload)
                intents = _product_transition_intents_from_payload(payload)
                evolution_steps = _product_evolution_steps_from_payload(payload)
                cognition_updates = _product_cognition_updates_from_payload(payload)
                slices = _product_claim_slices_from_payload(payload)
                if (evolution_steps or cognition_updates) and slices:
                    raise TypeError("product evolution cannot be selectable")
                if cognition_updates and not evolution_steps:
                    raise TypeError("product cognition updates require evolution steps")
                always_entities, always_binding_indices = (
                    _always_included_product_records_from_payload(payload)
                )
                self._validate_product_claim_slices(
                    delta,
                    bindings,
                    intents,
                    slices,
                    always_entities,
                    always_binding_indices,
                )
                graph, _, snapshot_hash = self._load_graph()
                self._validate_adapter_structured_evaluation_contract(
                    delta,
                    records,
                    review_payload,
                    graph,
                    identity_bindings=bindings,
                    evolution_steps=evolution_steps,
                    cognition_updates=cognition_updates,
                    current_snapshot_hash=snapshot_hash,
                    require_current=False,
                )
                product_parts: list[object] = [
                    "product_bundle",
                    _delta_json(delta),
                    _evidence_json(records),
                    _json([_identity_binding_to_data(item) for item in bindings]),
                    _json(
                        [_product_transition_intent_to_data(item) for item in intents]
                    ),
                ]
                if "evolution_steps" in payload_without_decision:
                    product_parts.append(
                        _json(
                            [evolution_step_to_data(item) for item in evolution_steps]
                        )
                    )
                if "cognition_updates" in payload_without_decision:
                    product_parts.append(
                        _json([_cognition_to_data(item) for item in cognition_updates])
                    )
                product_parts.extend(
                    (
                        _json([_product_claim_slice_to_data(item) for item in slices]),
                        _json(list(always_entities)),
                        _json(list(always_binding_indices)),
                        review_payload,
                    )
                )
                return _result_hash(*product_parts)
            if kind == "correction":
                allowed = {"prior_cognition_ids", "replacement", "evidence"}
                legacy_allowed = {"prior_cognition_id", "replacement", "evidence"}
                if set(payload) != allowed and set(payload) != legacy_allowed:
                    raise TypeError("correction payload shape")
                replacement_raw = payload.get("replacement")
                if not isinstance(replacement_raw, Mapping):
                    raise TypeError("correction replacement shape")
                prior_ids = _correction_prior_ids_from_payload(payload)
                replacement = _cognition_from_data(
                    cast(Mapping[str, object], replacement_raw)
                )
                return _result_hash(
                    "correction",
                    prior_ids,
                    _cognition_json(replacement),
                    _evidence_json(records),
                    review_payload,
                )
            if kind == "evolution":
                if set(payload) not in (
                    {"plan", "evidence"},
                    {"plan", "evidence", "identity_bindings"},
                ) or not isinstance(payload.get("plan"), Mapping):
                    raise TypeError("evolution payload shape")
                plan = evolution_plan_from_data(
                    cast(Mapping[str, object], payload["plan"])
                )
                graph, _, snapshot_hash = self._load_graph()
                bindings = _identity_bindings_from_payload(payload)
                self._validate_identity_bindings(bindings, records, graph)
                self._validate_evolution_correction_semantics(plan, graph)
                self._validate_adapter_typed_correction_contract(
                    plan,
                    records,
                    review_payload,
                    graph,
                    current_snapshot_hash=snapshot_hash,
                    accepted_evolution_steps=None,
                    identity_bindings=bindings,
                )
                evolution_parts: list[object] = [
                    "evolution",
                    _json(evolution_plan_to_data(plan)),
                    _evidence_json(records),
                ]
                if bindings:
                    evolution_parts.append(
                        _json([_identity_binding_to_data(item) for item in bindings])
                    )
                evolution_parts.append(review_payload)
                return _result_hash(*evolution_parts)
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError(
                "review payload cannot be reconstructed"
            ) from error
        raise MemoryLoopIntegrityError(f"unsupported review kind: {kind}")

    def _validate_evolution_correction_semantics(
        self,
        plan: WorldEvolutionPlan,
        graph: MemoryWorldGraph,
    ) -> None:
        """Keep typed ``corrects`` from changing or reaffirming its proposition kind.

        Generic evolution remains available for unstructured cognitions.  Once
        either endpoint carries a StructuredClaim, however, a correction must
        preserve its statement kind and actually change the proposition.  This
        check runs both before staging and while reconstructing/accepting stored
        evolution bytes, so a rehashed payload cannot weaken the invariant.
        """

        successors = {item.id: item for item in plan.delta.new_cognitions}
        for step in plan.steps:
            if step.kind != "cognition_change" or step.relation != "corrects":
                continue
            successor = (
                successors.get(step.successor_ids[0])
                if len(step.successor_ids) == 1
                else None
            )
            if successor is None:
                continue
            for prior_id in step.predecessor_ids:
                prior = graph.cognitions.get(prior_id)
                if prior is None:
                    continue
                if (
                    prior.structured_claim is None
                    and successor.structured_claim is None
                ):
                    continue
                if (
                    prior.structured_claim is None
                    or successor.structured_claim is None
                    or prior.structured_claim.statement_kind
                    != successor.structured_claim.statement_kind
                    or prior.structured_claim == successor.structured_claim
                    or (
                        prior.structured_claim.statement_kind == "attribute"
                        and prior.structured_claim.predicate
                        != successor.structured_claim.predicate
                    )
                ):
                    raise MemoryLoopIntegrityError(
                        "structured cognition correction has an invalid proposition change"
                    )

    def _validate_adapter_typed_correction_contract(
        self,
        plan: WorldEvolutionPlan,
        records: Sequence[EvidenceRecord],
        review_payload: Mapping[str, object] | None,
        graph: MemoryWorldGraph,
        *,
        current_snapshot_hash: str,
        accepted_evolution_steps: Sequence[AcceptedEvolutionStep] | None,
        identity_bindings: Sequence["ReviewedIdentityBinding"] = (),
    ) -> None:
        """Revalidate one closed adapter structured cognition correction.

        ``productDisplay`` is signed into the proposal hash, but a signature is
        not semantic authority by itself: an attacker able to edit and rehash a
        pending row must still be unable to make the UI receipt disagree with
        the exact typed plan that SQLite applies.  Ordinary evolution proposals
        are unaffected; this contract is selected only by the adapter's closed
        review-payload kind.
        """

        if (
            review_payload is None
            or review_payload.get("kind") != "adapter-typed-natural-correction"
        ):
            return

        def invalid(detail: str) -> NoReturn:
            raise MemoryLoopIntegrityError(
                "adapter typed correction contract is invalid: " + detail
            )

        if review_payload.get("autoApply") is not True:
            invalid("automatic Apply marker")
        current_evidence_id = review_payload.get("currentEvidenceId")
        display = review_payload.get("productDisplay")
        if (
            not isinstance(current_evidence_id, str)
            or not current_evidence_id
            or not isinstance(display, Mapping)
            or display.get("currentEvidenceId") != current_evidence_id
        ):
            invalid("current Evidence binding")
        if (
            len(records) != 1
            or records[0].role != "user"
            or records[0].id != current_evidence_id
            or tuple(plan.delta.source_evidence_ids) != (current_evidence_id,)
        ):
            invalid("Evidence boundary")
        record = records[0]
        metadata = record.metadata
        occurred_at = (
            metadata.get("occurred_at") if isinstance(metadata, Mapping) else None
        )
        if not isinstance(occurred_at, str) or not occurred_at:
            invalid("Evidence occurred_at")
        if (
            len(plan.steps) != 1
            or plan.steps[0].kind != "cognition_change"
            or plan.steps[0].relation != "corrects"
            or len(plan.delta.new_cognitions) != 1
            or len(plan.delta.formation_traces) != 1
            or plan.cognition_updates
            or plan.delta.new_entities
            or plan.delta.new_relationships
            or plan.delta.new_events
            or plan.delta.unresolved_references
            or plan.delta.semantic_uncertainties
        ):
            invalid("closed plan shape")
        step = plan.steps[0]
        successor = plan.delta.new_cognitions[0]
        trace = plan.delta.formation_traces[0]
        if len(step.predecessor_ids) != 1 or step.successor_ids != (successor.id,):
            invalid("transition cardinality")
        prior = graph.cognitions.get(step.predecessor_ids[0])
        if prior is None:
            invalid("prior cognition")

        # Hash recomputation is deliberately payload-local and must not scan
        # accepted proposals: accepted-step reconstruction itself calls the
        # rehasher.  Staging supplies the already-loaded trusted projection and
        # performs current/stale checks; acceptance repeats them through
        # WorldEvolutionPlan.apply_to in the same transaction.
        if accepted_evolution_steps is not None:
            target_is_current = (
                (
                    prior.target.kind == "entity"
                    and prior.target.id != graph.world.owner_entity_id
                    and prior.target.id in graph.entities
                )
                or (
                    prior.target.kind == "relationship"
                    and prior.target.id
                    in current_relationship_ids(graph, accepted_evolution_steps)
                )
                or (prior.target.kind == "event" and prior.target.id in graph.events)
            )
            if (
                prior.id in self._superseded_ids()
                or successor.id in graph.cognitions
                or not target_is_current
            ):
                invalid("prior is not current")

        owner_perspective = Perspective("entity", (graph.world.owner_entity_id,))
        prior_claim = prior.structured_claim
        attribute_correction = (
            prior.target.kind == "entity"
            and prior.target.id != graph.world.owner_entity_id
            and prior.target.id in graph.entities
            and prior_claim is not None
            and prior_claim.statement_kind == "attribute"
            and isinstance(prior_claim.predicate, str)
            and bool(prior_claim.predicate.strip())
            and isinstance(prior_claim.value, str)
            and bool(prior_claim.value.strip())
            and prior_claim.polarity == "assert"
            and prior_claim.epistemic_status == "asserted"
        )
        evaluation_correction = (
            prior.target.kind in {"relationship", "event"}
            and prior_claim is not None
            and prior_claim.statement_kind == "evaluation"
            and isinstance(prior_claim.value, str)
            and bool(prior_claim.value.strip())
        )
        if (
            not (attribute_correction or evaluation_correction)
            or (
                prior.target.kind == "relationship"
                and prior.target.id not in graph.relationships
            )
            or (prior.target.kind == "event" and prior.target.id not in graph.events)
            or prior.world_id != graph.world.world_id
            or prior.perspective != owner_perspective
            or prior.content_type != "fact"
            or prior.formed_by != "stated"
        ):
            invalid("prior structured cognition shape")
        assert prior_claim is not None
        if not attribute_correction and (
            identity_bindings or display.get("identityBindings", []) != []
        ):
            invalid("unexpected correction identity binding")
        successor_claim = successor.structured_claim
        if (
            successor.id == prior.id
            or successor.world_id != prior.world_id
            or successor.target != prior.target
            or successor.perspective != prior.perspective
            or successor.content_type != prior.content_type
            or successor.scope != prior.scope
            or successor.formed_by != "stated"
            or successor_claim is None
            or successor_claim.statement_kind != prior_claim.statement_kind
            or successor_claim.value == prior_claim.value
            or (
                attribute_correction
                and successor_claim.predicate != prior_claim.predicate
            )
            or successor.sources != (EvidenceLink(current_evidence_id, "support"),)
        ):
            invalid(
                "successor Attribute shape"
                if attribute_correction
                else "successor evaluation shape"
            )
        if (
            step.subject != prior.target
            or step.evidence_ids != (current_evidence_id,)
            or step.effective_at != occurred_at
        ):
            invalid("typed transition binding")

        if (
            trace.cognition_id != successor.id
            or trace.model_inferred_proposal is not False
            or trace.derived_formed_by != "stated"
            or trace.raw_support_count != 1
            or trace.effective_support_count != 1
            or trace.contradict_count != 0
            or trace.content_bindings
            or len(trace.sources) != 1
        ):
            invalid("FormationTrace shape")
        source = trace.sources[0]
        span = source.claim_span
        if (
            source.evidence_id != current_evidence_id
            or source.relation != "support"
            or source.proposition_origin_proposal != "user_stated"
            or source.response_act_proposal != "elaborate"
            or source.preceding_assistant_turn_id is not None
            or source.preceding_assistant_content_sha256 is not None
            or source.local_origin_decision != "exact_user_claim"
            or source.decision_code
            != (
                "product.attribute.exact_user_claim"
                if attribute_correction
                else "product.evaluation.exact_user_claim"
            )
            or not 0 <= span.start_codepoint < span.end_codepoint <= len(record.content)
            or record.content[span.start_codepoint : span.end_codepoint]
            != successor.content
            or span.source_content_sha256
            != sha256(record.content.encode("utf-8")).hexdigest()
            or span.claim_sha256
            != sha256(successor.content.encode("utf-8")).hexdigest()
        ):
            invalid("FormationTrace Evidence span")

        expected_before = _cognition_to_data(prior)
        expected_after = _cognition_to_data(successor)
        expected_candidate_after = {
            **expected_after,
            "evidence": [{"evidenceId": current_evidence_id, "text": record.content}],
        }
        expected_replacement = {
            "priorCognitionId": prior.id,
            "successorCognitionId": successor.id,
            "relation": "corrects",
            "evidenceId": current_evidence_id,
            "before": expected_before,
            "after": expected_after,
        }
        expected_candidate_memory = {
            "entities": [],
            "relationships": [],
            "events": [],
            "cognitions": [expected_candidate_after],
        }
        if display.get("candidateMemory") != expected_candidate_memory:
            invalid("display candidate")
        if display.get("evidence") != [
            {"evidenceId": current_evidence_id, "text": record.content}
        ]:
            invalid("display Evidence")
        if display.get("formation") != [_data(trace)]:
            invalid("display FormationTrace")
        if display.get("evolutionSteps") != [evolution_step_to_data(step)]:
            invalid("display evolution step")
        if display.get("cognitionReplacements") != [expected_replacement]:
            invalid("display replacement")
        if display.get("cognitionEvidenceChanges") != []:
            invalid("display evidence changes")
        if display.get("transitionIntents") != []:
            invalid("display transition intents")
        meaning = display.get("meaning")
        meaning_claims = meaning.get("claims") if isinstance(meaning, Mapping) else None
        if not isinstance(meaning_claims, list):
            invalid("display meaning claims")
        replacement_claims = [
            item
            for item in meaning_claims
            if isinstance(item, Mapping)
            and item.get("kind") == prior_claim.statement_kind
            and item.get("disposition") == "correction"
        ]
        if len(replacement_claims) != 1:
            invalid("display replacement claim")
        replacement_claim = replacement_claims[0]
        claim_start = replacement_claim.get("start")
        claim_end = replacement_claim.get("end")
        claim_text = replacement_claim.get("text")
        value = replacement_claim.get("value")
        if (
            type(claim_start) is not int
            or type(claim_end) is not int
            or not 0 <= claim_start < claim_end <= len(record.content)
            or not isinstance(claim_text, str)
            or record.content[claim_start:claim_end] != claim_text
            or claim_text != successor.content
            or claim_start != span.start_codepoint
            or claim_end != span.end_codepoint
            or not isinstance(value, Mapping)
        ):
            invalid("display replacement claim span")
        value_start = value.get("start")
        value_end = value.get("end")
        value_text = value.get("text")
        if (
            type(value_start) is not int
            or type(value_end) is not int
            or not claim_start <= value_start < value_end <= claim_end
            or not isinstance(value_text, str)
            or record.content[value_start:value_end] != value_text
            or value_text != successor_claim.value
        ):
            invalid("display replacement value span")

        # An indirect replacement has one additional authority boundary: the
        # wording points at a formal Relationship/Event through an opaque,
        # snapshot-bound handle rather than restating the object.  Bind every
        # visible and formal target field back to the exact cognition target
        # that the typed evolution will commit.  A re-signed display fork must
        # therefore fail before Evidence, history, receipt, or World mutation.
        object_reference = replacement_claim.get("object_reference")
        object_handles = replacement_claim.get("accepted_object_handles")
        indirect_object_correction = isinstance(object_reference, Mapping) or (
            isinstance(object_handles, list) and bool(object_handles)
        )
        if attribute_correction:
            claims_bundle = display.get("claims")
            projected_claims = (
                claims_bundle.get("claims")
                if isinstance(claims_bundle, Mapping)
                else None
            )
            resolutions = (
                claims_bundle.get("claim_resolutions")
                if isinstance(claims_bundle, Mapping)
                else None
            )
            resolved_mentions = (
                claims_bundle.get("resolved_mentions")
                if isinstance(claims_bundle, Mapping)
                else None
            )
            meaning_mentions = (
                meaning.get("mentions") if isinstance(meaning, Mapping) else None
            )
            predicate = replacement_claim.get("predicate")
            if (
                len(meaning_claims) != 1
                or not isinstance(meaning_mentions, list)
                or len(meaning_mentions) != 1
                or not isinstance(meaning_mentions[0], Mapping)
                or replacement_claim.get("subject_mention_index") != 0
                or replacement_claim.get("evaluation_target_claim_index") is not None
                or object_reference is not None
                or object_handles != []
                or not isinstance(predicate, Mapping)
                or not isinstance(claims_bundle, Mapping)
                or claims_bundle.get("version") != 3
                or not isinstance(projected_claims, list)
                or len(projected_claims) != 1
                or not isinstance(projected_claims[0], Mapping)
                or not isinstance(resolutions, list)
                or len(resolutions) != 1
                or not isinstance(resolutions[0], Mapping)
                or not isinstance(resolved_mentions, list)
                or len(resolved_mentions) != 1
                or not isinstance(resolved_mentions[0], Mapping)
                or len(identity_bindings) != 1
            ):
                invalid("direct Attribute correction shape")
            mention = cast(Mapping[str, object], meaning_mentions[0])
            projected_claim = cast(Mapping[str, object], projected_claims[0])
            resolution = cast(Mapping[str, object], resolutions[0])
            resolved_mention = cast(Mapping[str, object], resolved_mentions[0])
            binding = identity_bindings[0]
            predicate_start = predicate.get("start")
            predicate_end = predicate.get("end")
            predicate_text = predicate.get("text")
            mention_start = mention.get("start")
            mention_end = mention.get("end")
            mention_text = mention.get("text")
            if (
                type(predicate_start) is not int
                or type(predicate_end) is not int
                or not isinstance(predicate_text, str)
                or not claim_start <= predicate_start < predicate_end <= claim_end
                or record.content[predicate_start:predicate_end] != predicate_text
                or predicate_text != prior_claim.predicate
                or predicate_text != successor_claim.predicate
                or max(value_start, predicate_start) < min(value_end, predicate_end)
                or type(mention_start) is not int
                or type(mention_end) is not int
                or not isinstance(mention_text, str)
                or not claim_start <= mention_start < mention_end <= claim_end
                or record.content[mention_start:mention_end] != mention_text
                or max(value_start, mention_start) < min(value_end, mention_end)
                or max(predicate_start, mention_start) < min(predicate_end, mention_end)
            ):
                invalid("direct Attribute correction spans")

            entity = graph.entities.get(prior.target.id)
            if entity is None or entity.id == graph.world.owner_entity_id:
                invalid("direct Attribute Entity target")
            expected_entity_handle = (
                "accepted:"
                + sha256(
                    (
                        "turn-meaning-handle-v1\0"
                        f"{review_payload.get('baseWorldHash')}\0{entity.id}"
                    ).encode("utf-8")
                ).hexdigest()[:16]
            )
            expected_mention = {
                "text": mention_text,
                "start": mention_start,
                "end": mention_end,
                "mode": "refer",
                "kind_hint": mention.get("kind_hint"),
                "accepted_handles": [expected_entity_handle],
            }
            expected_resolution = {
                "claim_index": 0,
                "subject_entity_id": entity.id,
                "related_entity_ids": [],
                "target_kind": "entity",
                "target_id": entity.id,
                "source_entity_id": None,
                "target_entity_id": None,
                "relation_type": None,
                "bidirectional": None,
                "participant_entity_ids": [],
                "object_entity_ids": [],
                "owner_participates": None,
                "event_type": None,
                "occurred_at": None,
            }
            expected_attribute_object = {
                "kind": "entity",
                "entityId": entity.id,
            }
            expected_attribute_evidence = {
                "evidenceId": record.id,
                "span": {"start": claim_start, "end": claim_end},
                "valueSpan": {"start": value_start, "end": value_end},
                "predicateSpan": {
                    "start": predicate_start,
                    "end": predicate_end,
                },
            }
            expected_perspective = {
                "kind": "entity",
                "holderEntityIds": [graph.world.owner_entity_id],
            }
            session_id = review_payload.get("sessionId")
            if (
                not isinstance(session_id, str)
                or not session_id
                or mention != expected_mention
                or resolution != expected_resolution
                or resolved_mention != {"mention_index": 0, "entity_id": entity.id}
                or claims_bundle.get("focal_entity_id") != entity.id
                or claims_bundle.get("evidence_id") != record.id
                or claims_bundle.get("perspective_holder_entity_id")
                != graph.world.owner_entity_id
                or projected_claim.get("object") != expected_attribute_object
                or projected_claim.get("perspective") != expected_perspective
                or projected_claim.get("evidence") != expected_attribute_evidence
                or projected_claim.get("predicate") != predicate
                or projected_claim.get("value") != value
                or projected_claim.get("disposition") != "correction"
                or projected_claim.get("writeState") != "candidate"
                or projected_claim.get("structuredStatus") != "candidate"
                or projected_claim.get("accepted_object_handles") != []
                or projected_claim.get("object_reference") is not None
                or display.get("target")
                != {"entityId": entity.id, "entityNames": [entity.canonical_name]}
                or display.get("statementKind") != "attribute"
                or display.get("ownerPerspective")
                != {
                    "kind": "entity",
                    "entityIds": [graph.world.owner_entity_id],
                }
                or display.get("identityBindings")
                != [_identity_binding_to_data(binding)]
                or binding.entity_id != entity.id
                or binding.evidence_id != record.id
                or binding.conversation_id != session_id
                or binding.occurred_at != occurred_at
                or binding.start_codepoint != mention_start
                or binding.end_codepoint != mention_end
                or binding.kind_hint != mention.get("kind_hint")
                or binding.continuity_scope != session_id
            ):
                invalid("direct Attribute correction authority")
        elif indirect_object_correction:
            claims_bundle = display.get("claims")
            projected_claims = (
                claims_bundle.get("claims")
                if isinstance(claims_bundle, Mapping)
                else None
            )
            resolutions = (
                claims_bundle.get("claim_resolutions")
                if isinstance(claims_bundle, Mapping)
                else None
            )
            if (
                not isinstance(object_reference, Mapping)
                or not isinstance(object_handles, list)
                or len(object_handles) != 1
                or not isinstance(object_handles[0], str)
                or not object_handles[0]
                or replacement_claim.get("subject_mention_index") is not None
                or replacement_claim.get("evaluation_target_claim_index") is not None
                or not isinstance(claims_bundle, Mapping)
                or claims_bundle.get("version") != 3
                or not isinstance(projected_claims, list)
                or len(projected_claims) != 1
                or not isinstance(resolutions, list)
                or len(resolutions) != 1
                or len(meaning_claims) != 1
            ):
                invalid("indirect correction shape")
            projected_claim = projected_claims[0]
            resolution = resolutions[0]
            if not isinstance(projected_claim, Mapping) or not isinstance(
                resolution, Mapping
            ):
                invalid("indirect correction resolution")

            reference_start = object_reference.get("start")
            reference_end = object_reference.get("end")
            reference_text = object_reference.get("text")
            if (
                type(reference_start) is not int
                or type(reference_end) is not int
                or not isinstance(reference_text, str)
                or not claim_start <= reference_start < reference_end <= claim_end
                or record.content[reference_start:reference_end] != reference_text
                or max(value_start, reference_start) < min(value_end, reference_end)
            ):
                invalid("indirect correction object span")

            if prior.target.kind == "relationship":
                relationship = graph.relationships[prior.target.id]
                expected_object: dict[str, object] = {
                    "kind": "relationship",
                    "relationshipId": relationship.id,
                    "sourceEntityId": relationship.source_entity_id,
                    "targetEntityId": relationship.target_entity_id,
                    "relationType": relationship.relation_type,
                    "bidirectional": relationship.bidirectional,
                }
                expected_resolution_fields: dict[str, object] = {
                    "target_kind": "relationship",
                    "target_id": relationship.id,
                    "source_entity_id": relationship.source_entity_id,
                    "target_entity_id": relationship.target_entity_id,
                    "relation_type": relationship.relation_type,
                    "bidirectional": relationship.bidirectional,
                    "participant_entity_ids": [],
                    "object_entity_ids": [],
                    "owner_participates": None,
                    "event_type": None,
                    "occurred_at": None,
                }
            else:
                event = graph.events[prior.target.id]
                owner_participates = any(
                    item.entity_id == graph.world.owner_entity_id
                    for item in event.participants
                )
                expected_object = {
                    "kind": "event",
                    "eventId": event.id,
                    "participantEntityIds": [
                        item.entity_id for item in event.participants
                    ],
                    "objectEntityIds": list(event.related_entity_ids),
                    "ownerParticipates": owner_participates,
                    "eventType": event.event_type,
                    "occurredAt": event.occurred_at,
                }
                expected_resolution_fields = {
                    "target_kind": "event",
                    "target_id": event.id,
                    "source_entity_id": None,
                    "target_entity_id": None,
                    "relation_type": None,
                    "bidirectional": None,
                    "participant_entity_ids": [
                        item.entity_id for item in event.participants
                    ],
                    "object_entity_ids": list(event.related_entity_ids),
                    "owner_participates": owner_participates,
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at,
                }
            if (
                resolution.get("claim_index") != 0
                or resolution.get("subject_entity_id") is not None
                or resolution.get("related_entity_ids") != []
                or any(
                    resolution.get(key) != expected
                    for key, expected in expected_resolution_fields.items()
                )
                or display.get("target") != expected_object
                or projected_claim.get("object") != expected_object
                or projected_claim.get("accepted_object_handles") != object_handles
                or projected_claim.get("object_reference") != object_reference
                or projected_claim.get("disposition") != "correction"
                or projected_claim.get("writeState") != "candidate"
                or projected_claim.get("structuredStatus") != "candidate"
            ):
                invalid("indirect correction target equality")
            expected_handle = (
                "accepted-object:"
                + sha256(
                    (
                        "turn-meaning-world-object-handle-v1\0"
                        f"{review_payload.get('baseWorldHash')}\0"
                        f"{prior.target.kind}\0{prior.target.id}"
                    ).encode("utf-8")
                ).hexdigest()[:16]
            )
            if object_handles != [expected_handle]:
                invalid("indirect correction opaque handle")

            if accepted_evolution_steps is not None:
                session_id = review_payload.get("sessionId")
                if not isinstance(session_id, str) or not session_id:
                    invalid("indirect correction session")
                current_ids = current_relationship_ids(
                    graph,
                    accepted_evolution_steps,
                )
                source_ids = {
                    source.evidence_id
                    for item in graph.cognitions.values()
                    if (
                        item.target.kind == "relationship"
                        and item.target.id in current_ids
                    )
                    or (item.target.kind == "event" and item.target.id in graph.events)
                    for source in item.sources
                }
                same_session_evidence_ids = {
                    item.id
                    for item in self._evidence_for_ids(tuple(sorted(source_ids)))
                    if item.role == "user"
                    and isinstance(item.metadata, Mapping)
                    and item.metadata.get("conversation_id") == session_id
                }
                eligible_target_ids = {
                    item.target.id
                    for item in graph.cognitions.values()
                    if item.target.kind == prior.target.kind
                    and (
                        item.target.kind != "relationship"
                        or item.target.id in current_ids
                    )
                    and (item.target.kind != "event" or item.target.id in graph.events)
                    and any(
                        source.evidence_id in same_session_evidence_ids
                        for source in item.sources
                    )
                }
                if eligible_target_ids != {prior.target.id}:
                    invalid("indirect correction same-session authority")
        else:
            # A full-restatement correction has no opaque object handle, but
            # its signed v3 product projection still exposes the same formal
            # authority on four surfaces: the restated object claim, the
            # replacement evaluation claim, both compiler resolutions, and
            # the top-level display target.  Validate them against the actual
            # classifier-selected cognition target before the transaction may
            # write Evidence, history, a receipt, or the successor cognition.
            claims_bundle = display.get("claims")
            if isinstance(claims_bundle, Mapping):
                projected_claims = claims_bundle.get("claims")
                resolutions = claims_bundle.get("claim_resolutions")
                replacement_index = next(
                    (
                        index
                        for index, item in enumerate(meaning_claims)
                        if item is replacement_claim
                    ),
                    None,
                )
                target_index = replacement_claim.get("evaluation_target_claim_index")
                if (
                    claims_bundle.get("version") != 3
                    or not isinstance(projected_claims, list)
                    or not isinstance(resolutions, list)
                    or replacement_index is None
                    or type(target_index) is not int
                    or not 0 <= target_index < replacement_index
                    or len(projected_claims) != len(meaning_claims)
                    or len(resolutions) != len(meaning_claims)
                ):
                    invalid("direct correction claim shape")
                projected_target = projected_claims[target_index]
                projected_replacement = projected_claims[replacement_index]
                if not isinstance(projected_target, Mapping) or not isinstance(
                    projected_replacement,
                    Mapping,
                ):
                    invalid("direct correction projected claims")
                resolution_by_index = {
                    item.get("claim_index"): item
                    for item in resolutions
                    if isinstance(item, Mapping)
                    and type(item.get("claim_index")) is int
                }
                if set(resolution_by_index) != set(range(len(meaning_claims))):
                    invalid("direct correction resolutions")
                target_resolution = resolution_by_index[target_index]
                replacement_resolution = resolution_by_index[replacement_index]

                if prior.target.kind == "relationship":
                    relationship = graph.relationships[prior.target.id]
                    expected_object = {
                        "kind": "relationship",
                        "relationshipId": relationship.id,
                        "sourceEntityId": relationship.source_entity_id,
                        "targetEntityId": relationship.target_entity_id,
                        "relationType": relationship.relation_type,
                        "bidirectional": relationship.bidirectional,
                    }
                    expected_resolution_fields = {
                        "target_kind": "relationship",
                        "target_id": relationship.id,
                        "source_entity_id": relationship.source_entity_id,
                        "target_entity_id": relationship.target_entity_id,
                        "relation_type": relationship.relation_type,
                        "bidirectional": relationship.bidirectional,
                        "participant_entity_ids": [],
                        "object_entity_ids": [],
                        "owner_participates": None,
                        "event_type": None,
                        "occurred_at": None,
                    }
                else:
                    event = graph.events[prior.target.id]
                    owner_participates = any(
                        item.entity_id == graph.world.owner_entity_id
                        for item in event.participants
                    )
                    focus_ids = [
                        item.entity_id
                        for item in event.participants
                        if item.role == "focus"
                    ]
                    if len(focus_ids) > 1 or (
                        not focus_ids and not event.related_entity_ids
                    ):
                        invalid("direct correction Event subject")
                    expected_subject_entity_id = (
                        focus_ids[0] if focus_ids else event.related_entity_ids[0]
                    )
                    expected_related_entity_id_set = {
                        item.entity_id
                        for item in event.participants
                        if item.entity_id
                        not in {
                            graph.world.owner_entity_id,
                            expected_subject_entity_id,
                        }
                    } | (set(event.related_entity_ids) - {expected_subject_entity_id})
                    expected_object = {
                        "kind": "event",
                        "eventId": event.id,
                        "participantEntityIds": [
                            item.entity_id for item in event.participants
                        ],
                        "objectEntityIds": list(event.related_entity_ids),
                        "ownerParticipates": owner_participates,
                        "eventType": event.event_type,
                        "occurredAt": event.occurred_at,
                    }
                    expected_resolution_fields = {
                        "target_kind": "event",
                        "target_id": event.id,
                        "source_entity_id": None,
                        "target_entity_id": None,
                        "relation_type": None,
                        "bidirectional": None,
                        "participant_entity_ids": [
                            item.entity_id for item in event.participants
                        ],
                        "object_entity_ids": list(event.related_entity_ids),
                        "owner_participates": owner_participates,
                        "event_type": event.event_type,
                        "occurred_at": event.occurred_at,
                    }
                target_related_entity_ids = target_resolution.get("related_entity_ids")
                replacement_related_entity_ids = replacement_resolution.get(
                    "related_entity_ids"
                )
                target_related_entity_id_set = (
                    set(target_related_entity_ids)
                    if isinstance(target_related_entity_ids, list)
                    else set()
                )
                resolution_roles_invalid = (
                    target_resolution.get("subject_entity_id")
                    != replacement_resolution.get("subject_entity_id")
                    or not isinstance(target_related_entity_ids, list)
                    or not isinstance(replacement_related_entity_ids, list)
                    or any(
                        not isinstance(item, str) or not item
                        for item in target_related_entity_ids
                    )
                    or any(
                        not isinstance(item, str) or not item
                        for item in replacement_related_entity_ids
                    )
                    or len(target_related_entity_ids)
                    != len(target_related_entity_id_set)
                    or len(replacement_related_entity_ids)
                    != len(set(replacement_related_entity_ids))
                )
                if prior.target.kind == "event":
                    resolution_roles_invalid = resolution_roles_invalid or (
                        target_resolution.get("subject_entity_id")
                        != expected_subject_entity_id
                        or target_related_entity_id_set
                        != expected_related_entity_id_set
                        or replacement_related_entity_ids != []
                    )
                if (
                    display.get("target") != expected_object
                    or projected_target.get("object") != expected_object
                    or projected_replacement.get("object") != expected_object
                    or projected_target.get("writeState") != "not-written"
                    or projected_target.get("structuredStatus") != "not-lowered"
                    or projected_replacement.get("writeState") != "candidate"
                    or projected_replacement.get("structuredStatus") != "candidate"
                    or resolution_roles_invalid
                    or any(
                        resolution.get(key) != expected
                        for resolution in (
                            target_resolution,
                            replacement_resolution,
                        )
                        for key, expected in expected_resolution_fields.items()
                    )
                ):
                    invalid("direct correction target equality")
        if (
            accepted_evolution_steps is not None
            and review_payload.get("baseWorldHash") != current_snapshot_hash
        ):
            invalid("base World hash")

    def _validate_adapter_structured_evaluation_contract(
        self,
        delta: WorldDelta,
        records: Sequence[EvidenceRecord],
        review_payload: Mapping[str, object] | None,
        graph: MemoryWorldGraph,
        *,
        identity_bindings: Sequence["ReviewedIdentityBinding"] = (),
        evolution_steps: Sequence[EvolutionStep] = (),
        cognition_updates: Sequence[WorldCognition] = (),
        current_snapshot_hash: str,
        require_current: bool,
    ) -> None:
        """Bind a signed v3 structured cognition display to its World change.

        Opaque identity, cognition and object handles are interpretation input,
        not mutation authority.  A pending-row attacker must not be able to
        rehash a display about target A while the canonical delta updates B.
        Currentness and snapshot checks are stage/accept-only; immutable
        receipts remain replayable after the target later becomes history.
        """

        def invalid(detail: str) -> NoReturn:
            raise MemoryLoopIntegrityError(
                "adapter indirect World object evaluation contract is invalid: "
                + detail
            )

        if review_payload is None or review_payload.get("kind") != "product-bundle":
            # ``stage_product_bundle`` is also a lower-level typed API whose
            # callers may intentionally omit adapter display metadata.  The
            # closed evaluation contract is selected by the adapter's signed
            # review marker; ordinary typed bundles keep their existing
            # storage contract.
            return
        display = review_payload.get("productDisplay")
        claims_bundle = display.get("claims") if isinstance(display, Mapping) else None
        raw_claims = (
            claims_bundle.get("claims") if isinstance(claims_bundle, Mapping) else None
        )
        direct_entity_updates = tuple(
            cognition
            for cognition in cognition_updates
            if cognition.target.kind == "entity"
            and cognition.structured_claim is not None
            and cognition.structured_claim.statement_kind in {"attribute", "evaluation"}
        )
        if direct_entity_updates:
            if (
                not isinstance(display, Mapping)
                or not isinstance(claims_bundle, Mapping)
                or claims_bundle.get("version") != 3
                or not isinstance(raw_claims, list)
                or len(direct_entity_updates) != 1
                or len(evolution_steps) != 1
                or len(cognition_updates) != 1
            ):
                invalid("missing closed v3 direct Entity display")
            self._validate_adapter_direct_entity_cognition_change_contract(
                delta,
                records,
                review_payload,
                graph,
                display=display,
                claims_bundle=claims_bundle,
                raw_claims=raw_claims,
                identity_bindings=identity_bindings,
                evolution_step=evolution_steps[0],
                cognition_update=direct_entity_updates[0],
                current_snapshot_hash=current_snapshot_hash,
                require_current=require_current,
            )
            return
        relationship_statement_updates = tuple(
            cognition
            for cognition in cognition_updates
            if cognition.target.kind == "relationship"
            and cognition.structured_claim is not None
            and cognition.structured_claim.statement_kind == "relationship_statement"
        )
        if relationship_statement_updates:
            if (
                not isinstance(display, Mapping)
                or not isinstance(claims_bundle, Mapping)
                or claims_bundle.get("version") != 3
                or not isinstance(raw_claims, list)
                or len(relationship_statement_updates) != 1
                or len(evolution_steps) != 1
                or len(cognition_updates) != 1
            ):
                invalid("missing closed v3 Relationship statement display")
            self._validate_adapter_relationship_statement_evidence_change_contract(
                delta,
                records,
                review_payload,
                graph,
                display=display,
                claims_bundle=claims_bundle,
                raw_claims=raw_claims,
                identity_bindings=identity_bindings,
                evolution_step=evolution_steps[0],
                cognition_update=relationship_statement_updates[0],
                current_snapshot_hash=current_snapshot_hash,
                require_current=require_current,
            )
            return
        event_statement_updates = tuple(
            cognition
            for cognition in cognition_updates
            if cognition.target.kind == "event"
            and cognition.structured_claim is not None
            and cognition.structured_claim.statement_kind == "event_statement"
        )
        if event_statement_updates:
            if (
                not isinstance(display, Mapping)
                or not isinstance(claims_bundle, Mapping)
                or claims_bundle.get("version") != 3
                or not isinstance(raw_claims, list)
                or len(event_statement_updates) != 1
                or len(evolution_steps) != 1
                or len(cognition_updates) != 1
                or identity_bindings
            ):
                invalid("missing closed v3 Event statement display")
            self._validate_adapter_indirect_object_evidence_change_contract(
                delta,
                records,
                review_payload,
                graph,
                display=display,
                claims_bundle=claims_bundle,
                raw_claims=raw_claims,
                evolution_step=evolution_steps[0],
                cognition_update=event_statement_updates[0],
                current_snapshot_hash=current_snapshot_hash,
                require_current=require_current,
            )
            return
        # Select this contract from the formal World change, not from optional
        # display hints.  A same-turn/full-restatement Relationship claim also
        # emits its own ``relationship_statement`` cognition; an object-handle
        # evaluation does not.  Therefore a Relationship-targeted structured
        # evaluation with no such companion is the actual indirect mutation
        # that must close against the signed v3 display.  Removing the handle,
        # object-reference, or subject markers can no longer make validation
        # disappear after a pending row is re-signed.
        relationship_statement_target_ids = {
            cognition.target.id
            for cognition in delta.new_cognitions
            if cognition.target.kind == "relationship"
            and cognition.structured_claim is not None
            and cognition.structured_claim.statement_kind == "relationship_statement"
        }
        event_statement_target_ids = {
            cognition.target.id
            for cognition in delta.new_cognitions
            if cognition.target.kind == "event"
            and cognition.structured_claim is not None
            and cognition.structured_claim.statement_kind == "event_statement"
        }
        direct_statement_target_ids = (
            relationship_statement_target_ids | event_statement_target_ids
        )
        indirect_object_evaluations = tuple(
            cognition
            for cognition in (*delta.new_cognitions, *cognition_updates)
            if cognition.target.kind in {"relationship", "event"}
            and cognition.target.id not in direct_statement_target_ids
            and cognition.structured_claim is not None
            and cognition.structured_claim.statement_kind == "evaluation"
        )
        if not indirect_object_evaluations:
            return

        declares_object_handle = isinstance(raw_claims, list) and any(
            isinstance(item, Mapping) and item.get("accepted_object_handles")
            for item in raw_claims
        )
        if (
            not isinstance(claims_bundle, Mapping)
            or claims_bundle.get("version") != 3
            or not isinstance(raw_claims, list)
            or not declares_object_handle
        ):
            invalid("missing closed v3 object-reference display")
        if (
            not delta.new_entities
            and not delta.new_relationships
            and not delta.new_events
            and not delta.new_cognitions
            and not delta.formation_traces
            and not delta.unresolved_references
            and not delta.semantic_uncertainties
            and len(evolution_steps) == 1
            and len(cognition_updates) == 1
            and cognition_updates[0].target.kind in {"relationship", "event"}
            and cognition_updates[0].structured_claim is not None
            and cognition_updates[0].structured_claim.statement_kind == "evaluation"
        ):
            self._validate_adapter_indirect_object_evidence_change_contract(
                delta,
                records,
                review_payload,
                graph,
                display=cast(Mapping[str, object], display),
                claims_bundle=cast(Mapping[str, object], claims_bundle),
                raw_claims=raw_claims,
                evolution_step=evolution_steps[0],
                cognition_update=cognition_updates[0],
                current_snapshot_hash=current_snapshot_hash,
                require_current=require_current,
            )
            return

        resolutions = claims_bundle.get("claim_resolutions")
        current_evidence_id = review_payload.get("currentEvidenceId")
        if (
            review_payload.get("autoApply") is not True
            or not isinstance(display, Mapping)
            or display.get("currentEvidenceId") != current_evidence_id
            or len(records) != 1
            or records[0].id != current_evidence_id
            or records[0].role != "user"
            or tuple(delta.source_evidence_ids) != (current_evidence_id,)
            or len(raw_claims) != 1
            or not isinstance(resolutions, list)
            or len(resolutions) != 1
            or delta.new_entities
            or delta.new_relationships
            or delta.new_events
            or len(delta.new_cognitions) != 1
            or len(delta.formation_traces) != 1
            or delta.unresolved_references
            or delta.semantic_uncertainties
            or evolution_steps
            or cognition_updates
        ):
            invalid("closed bundle shape")
        record = records[0]
        claim = raw_claims[0]
        resolution = resolutions[0]
        cognition = delta.new_cognitions[0]
        trace = delta.formation_traces[0]
        if not isinstance(claim, Mapping) or not isinstance(resolution, Mapping):
            invalid("claim resolution shape")
        value = claim.get("value")
        object_reference = claim.get("object_reference")
        object_handles = claim.get("accepted_object_handles")
        if (
            claim.get("kind") != "evaluation"
            or claim.get("subject_mention_index") is not None
            or claim.get("evaluation_target_claim") is not None
            or claim.get("disposition") != "assert"
            or claim.get("polarity") != "affirm"
            or claim.get("epistemic_status") != "stated"
            or not isinstance(value, Mapping)
            or not isinstance(object_reference, Mapping)
            or not isinstance(object_handles, list)
            or len(object_handles) != 1
            or not isinstance(object_handles[0], str)
            or not object_handles[0]
        ):
            invalid("indirect evaluation claim")

        def exact_span(raw: Mapping[str, object], label: str) -> tuple[int, int, str]:
            start = raw.get("start")
            end = raw.get("end")
            text = raw.get("text")
            if (
                type(start) is not int
                or type(end) is not int
                or not isinstance(text, str)
                or not 0 <= start < end <= len(record.content)
                or record.content[start:end] != text
            ):
                invalid(label + " span")
            return start, end, text

        claim_start = claim.get("start")
        claim_end = claim.get("end")
        claim_text = claim.get("text")
        if (
            type(claim_start) is not int
            or type(claim_end) is not int
            or not isinstance(claim_text, str)
            or not 0 <= claim_start < claim_end <= len(record.content)
            or record.content[claim_start:claim_end] != claim_text
        ):
            invalid("claim span")
        value_start, value_end, value_text = exact_span(
            cast(Mapping[str, object], value),
            "value",
        )
        reference_start, reference_end, _ = exact_span(
            cast(Mapping[str, object], object_reference),
            "object reference",
        )
        if (
            not claim_start <= value_start < value_end <= claim_end
            or not claim_start <= reference_start < reference_end <= claim_end
            or max(value_start, reference_start) < min(value_end, reference_end)
        ):
            invalid("claim-local spans")

        target_kind = resolution.get("target_kind")
        target_id = resolution.get("target_id")
        relationship = (
            graph.relationships.get(target_id)
            if target_kind == "relationship" and isinstance(target_id, str)
            else None
        )
        event = (
            graph.events.get(target_id)
            if target_kind == "event" and isinstance(target_id, str)
            else None
        )
        if (
            resolution.get("claim_index") != 0
            or resolution.get("subject_entity_id") is not None
            or resolution.get("related_entity_ids") != []
            or (relationship is None) == (event is None)
        ):
            invalid("World object resolution")
        if relationship is not None:
            expected_resolution: dict[str, object] = {
                "claim_index": 0,
                "subject_entity_id": None,
                "related_entity_ids": [],
                "target_kind": "relationship",
                "target_id": relationship.id,
                "source_entity_id": relationship.source_entity_id,
                "target_entity_id": relationship.target_entity_id,
                "relation_type": relationship.relation_type,
                "bidirectional": relationship.bidirectional,
                "participant_entity_ids": [],
                "object_entity_ids": [],
                "owner_participates": None,
                "event_type": None,
                "occurred_at": None,
            }
            world_target = MemoryTarget("relationship", relationship.id)
            expected_object: dict[str, object] = {
                "kind": "relationship",
                "relationshipId": relationship.id,
                "sourceEntityId": relationship.source_entity_id,
                "targetEntityId": relationship.target_entity_id,
                "relationType": relationship.relation_type,
                "bidirectional": relationship.bidirectional,
            }
        else:
            assert event is not None
            owner_participates = any(
                item.entity_id == graph.world.owner_entity_id
                for item in event.participants
            )
            expected_resolution = {
                "claim_index": 0,
                "subject_entity_id": None,
                "related_entity_ids": [],
                "target_kind": "event",
                "target_id": event.id,
                "source_entity_id": None,
                "target_entity_id": None,
                "relation_type": None,
                "bidirectional": None,
                "participant_entity_ids": [
                    item.entity_id for item in event.participants
                ],
                "object_entity_ids": list(event.related_entity_ids),
                "owner_participates": owner_participates,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
            }
            world_target = MemoryTarget("event", event.id)
            expected_object = {
                "kind": "event",
                "eventId": event.id,
                "participantEntityIds": [item.entity_id for item in event.participants],
                "objectEntityIds": list(event.related_entity_ids),
                "ownerParticipates": owner_participates,
                "eventType": event.event_type,
                "occurredAt": event.occurred_at,
            }
        if resolution != expected_resolution:
            invalid("closed World object resolution")
        owner_perspective = Perspective("entity", (graph.world.owner_entity_id,))
        if (
            cognition.world_id != graph.world.world_id
            or cognition.target != world_target
            or cognition.perspective != owner_perspective
            or cognition.content != claim_text
            or cognition.content_type != "fact"
            or cognition.formed_by != "stated"
            or cognition.sources != (EvidenceLink(record.id, "support"),)
            or cognition.structured_claim is None
            or cognition.structured_claim.statement_kind != "evaluation"
            or cognition.structured_claim.value != value_text
        ):
            invalid("cognition")
        if (
            trace.cognition_id != cognition.id
            or len(trace.sources) != 1
            or trace.sources[0].evidence_id != record.id
            or trace.sources[0].relation != "support"
            or trace.sources[0].claim_span.start_codepoint != claim_start
            or trace.sources[0].claim_span.end_codepoint != claim_end
            or trace.sources[0].claim_span.source_content_sha256
            != sha256(record.content.encode("utf-8")).hexdigest()
            or trace.sources[0].claim_span.claim_sha256
            != sha256(claim_text.encode("utf-8")).hexdigest()
        ):
            invalid("FormationTrace")
        expected_handle = (
            "accepted-object:"
            + sha256(
                (
                    "turn-meaning-world-object-handle-v1\0"
                    f"{review_payload.get('baseWorldHash')}\0"
                    f"{world_target.kind}\0{world_target.id}"
                ).encode("utf-8")
            ).hexdigest()[:16]
        )
        if object_handles != [expected_handle]:
            invalid("opaque handle")
        expected_claim_evidence = {
            "evidenceId": record.id,
            "span": {"start": claim_start, "end": claim_end},
            "valueSpan": {"start": value_start, "end": value_end},
        }
        if (
            claim.get("object") != expected_object
            or claim.get("perspective")
            != {
                "kind": "entity",
                "holderEntityIds": [graph.world.owner_entity_id],
            }
            or claim.get("evidence") != expected_claim_evidence
            or claim.get("structuredStatus") != "candidate"
            or claim.get("writeState") != "candidate"
            or claim.get("accepted_entity_handles") != []
            or claim.get("prior_cognition_handles") != []
            or claim.get("related_mention_indices") != []
            or claim.get("predicate") is not None
            or claim.get("occurred_at") is not None
            or claim.get("normalized_occurred_at") is not None
            or claim.get("relationship_direction") is not None
            or claim.get("relationship_symmetric") is not False
            or claim.get("event_owner_participates") is not False
            or claim.get("event_subject_role") is not None
            or claim.get("event_related_roles") != []
        ):
            invalid("signed claim display")

        expected_candidate = _cognition_to_data(cognition)
        expected_candidate["evidence"] = [
            {
                "evidenceId": record.id,
                "text": record.content,
            }
        ]
        if (
            display.get("candidateMemory")
            != {
                "entities": [],
                "relationships": [],
                "events": [],
                "cognitions": [expected_candidate],
            }
            or display.get("evidence")
            != [{"evidenceId": record.id, "text": record.content}]
            or display.get("formation") != [_data(trace)]
            or display.get("unresolvedReferences") != []
            or display.get("semanticUncertainties") != []
            or display.get("identityBindings") != []
            or display.get("transitionIntents") != []
            or display.get("evolutionSteps") != []
            or display.get("cognitionEvidenceChanges") != []
            or display.get("cognitionReplacements") != []
            or display.get("statementKind") != "evaluation"
            or display.get("ownerPerspective")
            != {
                "kind": "entity",
                "entityIds": [graph.world.owner_entity_id],
            }
            or display.get("target") != expected_object
        ):
            invalid("signed product display")

        expected_meaning_claim = {
            "claim_id": claim.get("id"),
            "kind": "evaluation",
            "subject_mention_index": None,
            "text": claim_text,
            "start": claim_start,
            "end": claim_end,
            "value": value,
            "predicate": None,
            "occurred_at": None,
            "normalized_occurred_at": None,
            "relationship_direction": None,
            "relationship_symmetric": False,
            "event_owner_participates": False,
            "event_subject_role": None,
            "event_related_roles": [],
            "evaluation_target_claim_index": None,
            "object_reference": object_reference,
            "polarity": "affirm",
            "epistemic_status": "stated",
            "disposition": "assert",
            "related_mention_indices": [],
            "accepted_entity_handles": [],
            "accepted_object_handles": object_handles,
            "prior_cognition_handles": [],
        }
        if display.get("meaning") != {
            "act": "assertion",
            "mention": None,
            "statement": None,
            "mentions": [],
            "claims": [expected_meaning_claim],
            "code": "product_claim_bundle",
        }:
            invalid("signed meaning display")

        if require_current:
            accepted_steps = self._accepted_evolution_steps()
            current_ids = current_relationship_ids(graph, accepted_steps)
            if (
                review_payload.get("baseWorldHash") != current_snapshot_hash
                or (
                    world_target.kind == "relationship"
                    and world_target.id not in current_ids
                )
                or (
                    world_target.kind == "event" and world_target.id not in graph.events
                )
            ):
                invalid("current World target")
            session_id = review_payload.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                invalid("session binding")
            source_ids = {
                source.evidence_id
                for item in graph.cognitions.values()
                if (
                    item.target.kind == "relationship" and item.target.id in current_ids
                )
                or (item.target.kind == "event" and item.target.id in graph.events)
                for source in item.sources
            }
            same_session_evidence_ids = {
                item.id
                for item in self._evidence_for_ids(tuple(sorted(source_ids)))
                if item.role == "user"
                and isinstance(item.metadata, Mapping)
                and item.metadata.get("conversation_id") == session_id
            }
            eligible_target_ids = {
                item.target.id
                for item in graph.cognitions.values()
                if item.target.kind == world_target.kind
                and (
                    item.target.kind != "relationship" or item.target.id in current_ids
                )
                and (item.target.kind != "event" or item.target.id in graph.events)
                and any(
                    source.evidence_id in same_session_evidence_ids
                    for source in item.sources
                )
            }
            if eligible_target_ids != {world_target.id}:
                invalid("same-session World object authority")
            preview = delta.apply_to(
                graph,
                self._evidence_ids() | {record.id},
            )
            display_world = {
                "world": _data(preview.world),
                "entities": [
                    _data(item)
                    for item in sorted(
                        preview.entities.values(), key=lambda item: item.id
                    )
                ],
                "relationships": [
                    _data(item)
                    for item in sorted(
                        preview.relationships.values(),
                        key=lambda item: item.id,
                    )
                ],
                "events": [
                    _data(item)
                    for item in sorted(
                        preview.events.values(), key=lambda item: item.id
                    )
                ],
                "cognitions": [
                    _data(item)
                    for item in sorted(
                        preview.cognitions.values(),
                        key=lambda item: item.id,
                    )
                ],
            }
            if display.get("previewWorldHash") != _hash(_json(display_world)):
                invalid("preview World hash")

    def _validate_adapter_relationship_statement_evidence_change_contract(
        self,
        delta: WorldDelta,
        records: Sequence[EvidenceRecord],
        review_payload: Mapping[str, object],
        graph: MemoryWorldGraph,
        *,
        display: Mapping[str, object],
        claims_bundle: Mapping[str, object],
        raw_claims: list[object],
        identity_bindings: Sequence["ReviewedIdentityBinding"],
        evolution_step: EvolutionStep,
        cognition_update: WorldCognition,
        current_snapshot_hash: str,
        require_current: bool,
    ) -> None:
        """Validate same-ID Evidence for one exact Relationship proposition."""

        def invalid(detail: str) -> NoReturn:
            raise MemoryLoopIntegrityError(
                "adapter Relationship statement cognition contract is invalid: "
                + detail
            )

        current_evidence_id = review_payload.get("currentEvidenceId")
        resolutions = claims_bundle.get("claim_resolutions")
        resolved_mentions = claims_bundle.get("resolved_mentions")
        if (
            review_payload.get("autoApply") is not True
            or display.get("currentEvidenceId") != current_evidence_id
            or len(records) != 1
            or records[0].id != current_evidence_id
            or records[0].role != "user"
            or tuple(delta.source_evidence_ids) != (current_evidence_id,)
            or len(raw_claims) != 1
            or not isinstance(resolutions, list)
            or len(resolutions) != 1
            or not isinstance(resolved_mentions, list)
            or len(resolved_mentions) not in {1, 2}
            or len(identity_bindings) != len(resolved_mentions)
            or delta.new_entities
            or delta.new_relationships
            or delta.new_events
            or delta.new_cognitions
            or delta.formation_traces
            or delta.unresolved_references
            or delta.semantic_uncertainties
        ):
            invalid("closed Evidence-change bundle shape")
        record = records[0]
        claim = raw_claims[0]
        resolution = resolutions[0]
        if not isinstance(claim, Mapping) or not isinstance(resolution, Mapping):
            invalid("claim resolution shape")
        if any(not isinstance(item, Mapping) for item in resolved_mentions):
            invalid("resolved mention shape")
        mentions = tuple(cast(Mapping[str, object], item) for item in resolved_mentions)
        prior_handles = claim.get("prior_cognition_handles")
        predicate = claim.get("predicate")
        polarity = claim.get("polarity")
        related_indices = claim.get("related_mention_indices")
        direction = claim.get("relationship_direction")
        symmetric = claim.get("relationship_symmetric")
        if (
            claim.get("kind") != "relationship"
            or claim.get("subject_mention_index") != 0
            or related_indices not in ([], [1])
            or len(mentions) != 1 + len(cast(list[object], related_indices))
            or claim.get("value") is not None
            or not isinstance(predicate, Mapping)
            or claim.get("evaluation_target_claim") is not None
            or claim.get("object_reference") is not None
            or claim.get("accepted_object_handles") != []
            or claim.get("accepted_entity_handles") != []
            or claim.get("disposition") != "assert"
            or polarity not in {"affirm", "negate"}
            or claim.get("epistemic_status") != "stated"
            or not isinstance(prior_handles, list)
            or len(prior_handles) != 1
            or not isinstance(prior_handles[0], str)
            or not prior_handles[0]
            or direction
            not in {"subject_to_related", "owner_to_focal", "focal_to_owner"}
            or type(symmetric) is not bool
            or claim.get("occurred_at") is not None
            or claim.get("normalized_occurred_at") is not None
            or claim.get("event_owner_participates") is not False
            or claim.get("event_subject_role") is not None
            or claim.get("event_related_roles") != []
        ):
            invalid("Relationship statement Evidence-change claim")
        if (direction == "subject_to_related") != (related_indices == [1]):
            invalid("Relationship endpoint role shape")

        claim_start = claim.get("start")
        claim_end = claim.get("end")
        claim_text = claim.get("text")
        predicate_start = predicate.get("start")
        predicate_end = predicate.get("end")
        predicate_text = predicate.get("text")
        if (
            type(claim_start) is not int
            or type(claim_end) is not int
            or not isinstance(claim_text, str)
            or not 0 <= claim_start < claim_end <= len(record.content)
            or record.content[claim_start:claim_end] != claim_text
            or type(predicate_start) is not int
            or type(predicate_end) is not int
            or not isinstance(predicate_text, str)
            or not claim_start <= predicate_start < predicate_end <= claim_end
            or record.content[predicate_start:predicate_end] != predicate_text
        ):
            invalid("claim and predicate spans")

        meaning = display.get("meaning")
        meaning_mentions = (
            meaning.get("mentions") if isinstance(meaning, Mapping) else None
        )
        meaning_claims = meaning.get("claims") if isinstance(meaning, Mapping) else None
        if (
            not isinstance(meaning_mentions, list)
            or len(meaning_mentions) != len(mentions)
            or any(not isinstance(item, Mapping) for item in meaning_mentions)
            or not isinstance(meaning_claims, list)
            or len(meaning_claims) != 1
            or not isinstance(meaning_claims[0], Mapping)
        ):
            invalid("signed meaning shape")
        signed_mentions = tuple(
            cast(Mapping[str, object], item) for item in meaning_mentions
        )
        entity_ids: list[str] = []
        expected_mentions: list[dict[str, object]] = []
        session_id = review_payload.get("sessionId")
        metadata = record.metadata
        occurred_at = (
            metadata.get("occurred_at") if isinstance(metadata, Mapping) else None
        )
        if not isinstance(session_id, str) or not session_id:
            invalid("session binding")
        if not isinstance(occurred_at, str) or not occurred_at:
            invalid("Evidence occurred_at")
        for index, (resolved, mention, binding) in enumerate(
            zip(mentions, signed_mentions, identity_bindings, strict=True)
        ):
            entity_id = resolved.get("entity_id")
            mention_start = mention.get("start")
            mention_end = mention.get("end")
            mention_text = mention.get("text")
            entity = (
                graph.entities.get(entity_id) if isinstance(entity_id, str) else None
            )
            if (
                resolved != {"mention_index": index, "entity_id": entity_id}
                or entity is None
                or entity.id == graph.world.owner_entity_id
                or type(mention_start) is not int
                or type(mention_end) is not int
                or not isinstance(mention_text, str)
                or not claim_start <= mention_start < mention_end <= claim_end
                or record.content[mention_start:mention_end] != mention_text
                or max(mention_start, predicate_start) < min(mention_end, predicate_end)
            ):
                invalid("resolved Relationship mention")
            for earlier in signed_mentions[:index]:
                earlier_start = earlier.get("start")
                earlier_end = earlier.get("end")
                if (
                    type(earlier_start) is not int
                    or type(earlier_end) is not int
                    or max(mention_start, earlier_start) < min(mention_end, earlier_end)
                ):
                    invalid("Relationship mention overlap")
            expected_entity_handle = (
                "accepted:"
                + sha256(
                    (
                        "turn-meaning-handle-v1\0"
                        f"{review_payload.get('baseWorldHash')}\0{entity.id}"
                    ).encode("utf-8")
                ).hexdigest()[:16]
            )
            expected_mention = {
                "text": mention_text,
                "start": mention_start,
                "end": mention_end,
                "mode": "refer",
                "kind_hint": mention.get("kind_hint"),
                "accepted_handles": [expected_entity_handle],
            }
            if mention != expected_mention:
                invalid("opaque Entity handle")
            if (
                binding.entity_id != entity.id
                or binding.evidence_id != record.id
                or binding.conversation_id != session_id
                or binding.occurred_at != occurred_at
                or binding.start_codepoint != mention_start
                or binding.end_codepoint != mention_end
                or binding.kind_hint != mention.get("kind_hint")
                or binding.continuity_scope != session_id
            ):
                invalid("identity binding")
            entity_ids.append(entity.id)
            expected_mentions.append(expected_mention)

        subject_entity_id = entity_ids[0]
        if direction == "subject_to_related":
            semantic_source_id = subject_entity_id
            semantic_target_id = entity_ids[1]
        elif direction == "owner_to_focal":
            semantic_source_id = graph.world.owner_entity_id
            semantic_target_id = subject_entity_id
        else:
            semantic_source_id = subject_entity_id
            semantic_target_id = graph.world.owner_entity_id
        target_id = resolution.get("target_id")
        relationship = (
            graph.relationships.get(target_id) if isinstance(target_id, str) else None
        )
        if relationship is None:
            invalid("Relationship target")
        if symmetric:
            endpoints_match = {
                relationship.source_entity_id,
                relationship.target_entity_id,
            } == {semantic_source_id, semantic_target_id}
        else:
            endpoints_match = (
                relationship.source_entity_id == semantic_source_id
                and relationship.target_entity_id == semantic_target_id
            )
        if (
            not endpoints_match
            or relationship.bidirectional is not symmetric
            or relationship.relation_type != predicate_text
        ):
            invalid("complete Relationship target")
        expected_resolution = {
            "claim_index": 0,
            "subject_entity_id": subject_entity_id,
            "related_entity_ids": entity_ids[1:],
            "target_kind": "relationship",
            "target_id": relationship.id,
            "source_entity_id": relationship.source_entity_id,
            "target_entity_id": relationship.target_entity_id,
            "relation_type": relationship.relation_type,
            "bidirectional": relationship.bidirectional,
            "participant_entity_ids": [],
            "object_entity_ids": [],
            "owner_participates": None,
            "event_type": None,
            "occurred_at": None,
        }
        if (
            resolution != expected_resolution
            or claims_bundle.get("focal_entity_id") != subject_entity_id
            or claims_bundle.get("evidence_id") != record.id
            or claims_bundle.get("perspective_holder_entity_id")
            != graph.world.owner_entity_id
        ):
            invalid("closed Relationship resolution")

        world_target = MemoryTarget("relationship", relationship.id)
        relation = "contradicts" if polarity == "negate" else "reaffirms"
        source_relation: Literal["support", "contradict"] = (
            "contradict" if relation == "contradicts" else "support"
        )
        if (
            evolution_step.kind != "cognition_change"
            or evolution_step.relation != relation
            or evolution_step.subject != world_target
            or evolution_step.predecessor_ids != (cognition_update.id,)
            or evolution_step.successor_ids != (cognition_update.id,)
            or evolution_step.evidence_ids != (record.id,)
            or evolution_step.effective_at != occurred_at
        ):
            invalid("typed cognition Evidence-change step")
        changes = display.get("cognitionEvidenceChanges")
        if not isinstance(changes, list) or len(changes) != 1:
            invalid("signed cognition Evidence change")
        change = changes[0]
        if not isinstance(change, Mapping):
            invalid("signed cognition Evidence change")
        before_raw = change.get("before")
        after_raw = change.get("after")
        if not isinstance(before_raw, Mapping) or not isinstance(after_raw, Mapping):
            invalid("signed cognition snapshots")
        try:
            signed_before = _cognition_from_data(before_raw)
            signed_after = _cognition_from_data(after_raw)
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError(
                "adapter Relationship statement cognition contract is invalid: "
                "signed cognition snapshots"
            ) from error
        if signed_after != cognition_update:
            invalid("signed update snapshot")
        expected_structured = StructuredClaim(
            "relationship_statement",
            predicate=relationship.relation_type,
            polarity="assert",
            epistemic_status="asserted",
        )
        expected_perspective = Perspective(
            "entity",
            (graph.world.owner_entity_id,),
        )
        if (
            signed_before.id != cognition_update.id
            or signed_before.world_id != graph.world.world_id
            or signed_before.target != world_target
            or signed_before.perspective != expected_perspective
            or signed_before.content_type != "fact"
            or signed_before.formed_by != "stated"
            or signed_before.structured_claim != expected_structured
            or any(source.evidence_id == record.id for source in signed_before.sources)
        ):
            invalid("prior Relationship statement cognition")
        expected_sources = signed_before.sources + (
            EvidenceLink(record.id, source_relation),
        )
        support_count = sum(item.relation == "support" for item in expected_sources)
        contradict_count = sum(
            item.relation == "contradict" for item in expected_sources
        )
        expected_confidence = compute_confidence(
            ConfidenceInputs(
                signed_before.content_type,
                signed_before.formed_by,
                support_count,
                contradict_count,
            )
        )
        expected_update = replace(
            signed_before,
            sources=expected_sources,
            confidence=expected_confidence,
            cred_status=derive_cred_status(
                expected_confidence,
                contradict_count,
                signed_before.content_type,
                support_count=support_count,
            ),
        )
        if cognition_update != expected_update:
            invalid("same-ID cognition update")
        if (
            change.get("cognitionId") != cognition_update.id
            or change.get("relation") != relation
            or change.get("evidenceId") != record.id
            or display.get("evolutionSteps") != [_data(evolution_step)]
        ):
            invalid("signed Evidence-change projection")
        expected_prior_handle = (
            "accepted-cognition:"
            + sha256(
                (
                    "turn-meaning-cognition-handle-v1\0"
                    f"{review_payload.get('baseWorldHash')}\0{cognition_update.id}"
                ).encode("utf-8")
            ).hexdigest()[:16]
        )
        if prior_handles != [expected_prior_handle]:
            invalid("opaque cognition handle")

        expected_object = {
            "kind": "relationship",
            "relationshipId": relationship.id,
            "sourceEntityId": relationship.source_entity_id,
            "targetEntityId": relationship.target_entity_id,
            "relationType": relationship.relation_type,
            "bidirectional": relationship.bidirectional,
        }
        expected_evidence = {
            "evidenceId": record.id,
            "span": {"start": claim_start, "end": claim_end},
            "predicateSpan": {
                "start": predicate_start,
                "end": predicate_end,
            },
        }
        if (
            claim.get("object") != expected_object
            or claim.get("perspective")
            != {
                "kind": "entity",
                "holderEntityIds": [graph.world.owner_entity_id],
            }
            or claim.get("evidence") != expected_evidence
            or claim.get("structuredStatus") != "candidate"
            or claim.get("writeState") != "candidate"
        ):
            invalid("signed Relationship claim display")
        if (
            display.get("candidateMemory")
            != {"entities": [], "relationships": [], "events": [], "cognitions": []}
            or display.get("evidence")
            != [{"evidenceId": record.id, "text": record.content}]
            or display.get("formation") != []
            or display.get("unresolvedReferences") != []
            or display.get("semanticUncertainties") != []
            or display.get("identityBindings")
            != [_identity_binding_to_data(item) for item in identity_bindings]
            or display.get("transitionIntents") != []
            or display.get("cognitionReplacements") != []
            or display.get("statementKind") != "relationship"
            or display.get("ownerPerspective")
            != {"kind": "entity", "entityIds": [graph.world.owner_entity_id]}
            or display.get("target") != expected_object
        ):
            invalid("signed product display")
        expected_meaning_claim = {
            "claim_id": claim.get("id"),
            "kind": "relationship",
            "subject_mention_index": 0,
            "text": claim_text,
            "start": claim_start,
            "end": claim_end,
            "value": None,
            "predicate": {
                "text": predicate_text,
                "start": predicate_start,
                "end": predicate_end,
            },
            "occurred_at": None,
            "normalized_occurred_at": None,
            "relationship_direction": direction,
            "relationship_symmetric": symmetric,
            "event_owner_participates": False,
            "event_subject_role": None,
            "event_related_roles": [],
            "evaluation_target_claim_index": None,
            "object_reference": None,
            "polarity": polarity,
            "epistemic_status": "stated",
            "disposition": "assert",
            "related_mention_indices": related_indices,
            "accepted_entity_handles": [],
            "accepted_object_handles": [],
            "prior_cognition_handles": prior_handles,
        }
        if meaning != {
            "act": "assertion",
            "mention": None,
            "statement": None,
            "mentions": expected_mentions,
            "claims": [expected_meaning_claim],
            "code": "relationship_change",
        }:
            invalid("signed meaning display")

        if require_current:
            prior = graph.cognitions.get(cognition_update.id)
            if (
                review_payload.get("baseWorldHash") != current_snapshot_hash
                or relationship.id
                not in current_relationship_ids(
                    graph,
                    self._accepted_evolution_steps(),
                )
                or prior != signed_before
                or cognition_update.id in self._superseded_ids()
            ):
                invalid("current Relationship proposition")
            preview = MemoryWorldGraph(
                graph.world,
                graph.entities.copy(),
                graph.relationships.copy(),
                graph.events.copy(),
                graph.cognitions.copy(),
            )
            preview.cognitions[cognition_update.id] = cognition_update
            display_world = {
                "world": _data(preview.world),
                "entities": [
                    _data(item)
                    for item in sorted(
                        preview.entities.values(),
                        key=lambda item: item.id,
                    )
                ],
                "relationships": [
                    _data(item)
                    for item in sorted(
                        preview.relationships.values(),
                        key=lambda item: item.id,
                    )
                ],
                "events": [
                    _data(item)
                    for item in sorted(
                        preview.events.values(),
                        key=lambda item: item.id,
                    )
                ],
                "cognitions": [
                    _data(item)
                    for item in sorted(
                        preview.cognitions.values(),
                        key=lambda item: item.id,
                    )
                ],
            }
            if display.get("previewWorldHash") != _hash(_json(display_world)):
                invalid("preview World hash")

    def _validate_adapter_direct_entity_cognition_change_contract(
        self,
        delta: WorldDelta,
        records: Sequence[EvidenceRecord],
        review_payload: Mapping[str, object],
        graph: MemoryWorldGraph,
        *,
        display: Mapping[str, object],
        claims_bundle: Mapping[str, object],
        raw_claims: list[object],
        identity_bindings: Sequence["ReviewedIdentityBinding"],
        evolution_step: EvolutionStep,
        cognition_update: WorldCognition,
        current_snapshot_hash: str,
        require_current: bool,
    ) -> None:
        """Validate a direct Entity cognition's same-ID Evidence change."""

        def invalid(detail: str) -> NoReturn:
            raise MemoryLoopIntegrityError(
                "adapter direct Entity cognition contract is invalid: " + detail
            )

        current_evidence_id = review_payload.get("currentEvidenceId")
        resolutions = claims_bundle.get("claim_resolutions")
        resolved_mentions = claims_bundle.get("resolved_mentions")
        if (
            review_payload.get("autoApply") is not True
            or display.get("currentEvidenceId") != current_evidence_id
            or len(records) != 1
            or records[0].id != current_evidence_id
            or records[0].role != "user"
            or tuple(delta.source_evidence_ids) != (current_evidence_id,)
            or len(raw_claims) != 1
            or not isinstance(resolutions, list)
            or len(resolutions) != 1
            or not isinstance(resolved_mentions, list)
            or len(resolved_mentions) != 1
            or len(identity_bindings) != 1
            or delta.new_entities
            or delta.new_relationships
            or delta.new_events
            or delta.new_cognitions
            or delta.formation_traces
            or delta.unresolved_references
            or delta.semantic_uncertainties
        ):
            invalid("closed Evidence-change bundle shape")
        record = records[0]
        claim = raw_claims[0]
        resolution = resolutions[0]
        resolved_mention = resolved_mentions[0]
        binding = identity_bindings[0]
        if (
            not isinstance(claim, Mapping)
            or not isinstance(resolution, Mapping)
            or not isinstance(resolved_mention, Mapping)
        ):
            invalid("claim resolution shape")
        value = claim.get("value")
        predicate = claim.get("predicate")
        prior_handles = claim.get("prior_cognition_handles")
        polarity = claim.get("polarity")
        statement_kind = claim.get("kind")
        if (
            statement_kind not in {"attribute", "evaluation"}
            or claim.get("subject_mention_index") != 0
            or claim.get("evaluation_target_claim") is not None
            or claim.get("object_reference") is not None
            or claim.get("accepted_object_handles") != []
            or claim.get("disposition") != "assert"
            or not isinstance(polarity, str)
            or polarity not in {"affirm", "negate"}
            or claim.get("epistemic_status") != "stated"
            or not isinstance(value, Mapping)
            or not isinstance(prior_handles, list)
            or len(prior_handles) != 1
            or not isinstance(prior_handles[0], str)
            or not prior_handles[0]
            or (statement_kind == "attribute" and not isinstance(predicate, Mapping))
            or (statement_kind == "evaluation" and predicate is not None)
        ):
            invalid("direct Entity cognition Evidence-change claim")

        claim_start = claim.get("start")
        claim_end = claim.get("end")
        claim_text = claim.get("text")
        value_start = value.get("start")
        value_end = value.get("end")
        value_text = value.get("text")
        if (
            type(claim_start) is not int
            or type(claim_end) is not int
            or not isinstance(claim_text, str)
            or not 0 <= claim_start < claim_end <= len(record.content)
            or record.content[claim_start:claim_end] != claim_text
            or type(value_start) is not int
            or type(value_end) is not int
            or not isinstance(value_text, str)
            or not claim_start <= value_start < value_end <= claim_end
            or record.content[value_start:value_end] != value_text
        ):
            invalid("claim and value spans")
        predicate_start: int | None = None
        predicate_end: int | None = None
        predicate_text: str | None = None
        if isinstance(predicate, Mapping):
            raw_predicate_start = predicate.get("start")
            raw_predicate_end = predicate.get("end")
            raw_predicate_text = predicate.get("text")
            if (
                type(raw_predicate_start) is not int
                or type(raw_predicate_end) is not int
                or not isinstance(raw_predicate_text, str)
                or not claim_start
                <= raw_predicate_start
                < raw_predicate_end
                <= claim_end
                or record.content[raw_predicate_start:raw_predicate_end]
                != raw_predicate_text
                or max(value_start, raw_predicate_start)
                < min(value_end, raw_predicate_end)
            ):
                invalid("Attribute predicate span")
            predicate_start = raw_predicate_start
            predicate_end = raw_predicate_end
            predicate_text = raw_predicate_text

        meaning = display.get("meaning")
        meaning_mentions = (
            meaning.get("mentions") if isinstance(meaning, Mapping) else None
        )
        meaning_claims = meaning.get("claims") if isinstance(meaning, Mapping) else None
        if (
            not isinstance(meaning_mentions, list)
            or len(meaning_mentions) != 1
            or not isinstance(meaning_claims, list)
            or len(meaning_claims) != 1
            or not isinstance(meaning_mentions[0], Mapping)
            or not isinstance(meaning_claims[0], Mapping)
        ):
            invalid("signed meaning shape")
        mention = cast(Mapping[str, object], meaning_mentions[0])
        mention_start = mention.get("start")
        mention_end = mention.get("end")
        mention_text = mention.get("text")
        if (
            type(mention_start) is not int
            or type(mention_end) is not int
            or not isinstance(mention_text, str)
            or not claim_start <= mention_start < mention_end <= claim_end
            or record.content[mention_start:mention_end] != mention_text
            or max(value_start, mention_start) < min(value_end, mention_end)
            or (
                predicate_start is not None
                and predicate_end is not None
                and max(predicate_start, mention_start)
                < min(predicate_end, mention_end)
            )
        ):
            invalid("direct Entity mention span")

        target_id = resolution.get("target_id")
        entity = graph.entities.get(target_id) if isinstance(target_id, str) else None
        if entity is None or entity.id == graph.world.owner_entity_id:
            invalid("Entity target")
        world_target = MemoryTarget("entity", entity.id)
        expected_resolution: dict[str, object] = {
            "claim_index": 0,
            "subject_entity_id": entity.id,
            "related_entity_ids": [],
            "target_kind": "entity",
            "target_id": entity.id,
            "source_entity_id": None,
            "target_entity_id": None,
            "relation_type": None,
            "bidirectional": None,
            "participant_entity_ids": [],
            "object_entity_ids": [],
            "owner_participates": None,
            "event_type": None,
            "occurred_at": None,
        }
        if (
            resolution != expected_resolution
            or resolved_mention != {"mention_index": 0, "entity_id": entity.id}
            or claims_bundle.get("focal_entity_id") != entity.id
            or claims_bundle.get("evidence_id") != record.id
            or claims_bundle.get("perspective_holder_entity_id")
            != graph.world.owner_entity_id
        ):
            invalid("closed Entity resolution")

        relation = "contradicts" if polarity == "negate" else "reaffirms"
        source_relation: Literal["support", "contradict"] = (
            "contradict" if relation == "contradicts" else "support"
        )
        metadata = record.metadata
        occurred_at = (
            metadata.get("occurred_at") if isinstance(metadata, Mapping) else None
        )
        if (
            evolution_step.kind != "cognition_change"
            or evolution_step.relation != relation
            or evolution_step.subject != world_target
            or evolution_step.predecessor_ids != (cognition_update.id,)
            or evolution_step.successor_ids != (cognition_update.id,)
            or evolution_step.evidence_ids != (record.id,)
            or not isinstance(occurred_at, str)
            or evolution_step.effective_at != occurred_at
        ):
            invalid("typed cognition Evidence-change step")

        changes = display.get("cognitionEvidenceChanges")
        if not isinstance(changes, list) or len(changes) != 1:
            invalid("signed cognition Evidence change")
        change = changes[0]
        if not isinstance(change, Mapping):
            invalid("signed cognition Evidence change")
        before_raw = change.get("before")
        after_raw = change.get("after")
        if not isinstance(before_raw, Mapping) or not isinstance(after_raw, Mapping):
            invalid("signed cognition snapshots")
        try:
            signed_before = _cognition_from_data(before_raw)
            signed_after = _cognition_from_data(after_raw)
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError(
                "adapter direct Entity cognition contract is invalid: "
                "signed cognition snapshots"
            ) from error
        if signed_after != cognition_update:
            invalid("signed update snapshot")
        expected_perspective = Perspective(
            "entity",
            (graph.world.owner_entity_id,),
        )
        if (
            signed_before.id != cognition_update.id
            or signed_before.world_id != graph.world.world_id
            or signed_before.target != world_target
            or signed_before.perspective != expected_perspective
            or signed_before.content_type != "fact"
            or signed_before.formed_by != "stated"
            or signed_before.structured_claim
            != StructuredClaim(
                cast(Any, statement_kind),
                predicate=predicate_text,
                value=value_text,
                polarity="assert",
                epistemic_status="asserted",
            )
            or any(source.evidence_id == record.id for source in signed_before.sources)
        ):
            invalid("prior cognition")
        expected_sources = signed_before.sources + (
            EvidenceLink(record.id, source_relation),
        )
        support_count = sum(item.relation == "support" for item in expected_sources)
        contradict_count = sum(
            item.relation == "contradict" for item in expected_sources
        )
        expected_confidence = compute_confidence(
            ConfidenceInputs(
                signed_before.content_type,
                signed_before.formed_by,
                support_count,
                contradict_count,
            )
        )
        expected_update = replace(
            signed_before,
            sources=expected_sources,
            confidence=expected_confidence,
            cred_status=derive_cred_status(
                expected_confidence,
                contradict_count,
                signed_before.content_type,
                support_count=support_count,
            ),
        )
        if cognition_update != expected_update:
            invalid("same-ID cognition update")
        if (
            change.get("cognitionId") != cognition_update.id
            or change.get("relation") != relation
            or change.get("evidenceId") != record.id
            or display.get("evolutionSteps") != [_data(evolution_step)]
        ):
            invalid("signed Evidence-change projection")

        expected_entity_handle = (
            "accepted:"
            + sha256(
                (
                    "turn-meaning-handle-v1\0"
                    f"{review_payload.get('baseWorldHash')}\0{entity.id}"
                ).encode("utf-8")
            ).hexdigest()[:16]
        )
        expected_prior_handle = (
            "accepted-cognition:"
            + sha256(
                (
                    "turn-meaning-cognition-handle-v1\0"
                    f"{review_payload.get('baseWorldHash')}\0{cognition_update.id}"
                ).encode("utf-8")
            ).hexdigest()[:16]
        )
        expected_mention = {
            "text": mention_text,
            "start": mention_start,
            "end": mention_end,
            "mode": "refer",
            "kind_hint": mention.get("kind_hint"),
            "accepted_handles": [expected_entity_handle],
        }
        if mention != expected_mention or prior_handles != [expected_prior_handle]:
            invalid("opaque handles")

        expected_object = {"kind": "entity", "entityId": entity.id}
        expected_predicate = (
            None
            if predicate_start is None or predicate_end is None
            else {
                "text": predicate_text,
                "start": predicate_start,
                "end": predicate_end,
            }
        )
        expected_claim_evidence: dict[str, object] = {
            "evidenceId": record.id,
            "span": {"start": claim_start, "end": claim_end},
            "valueSpan": {"start": value_start, "end": value_end},
        }
        if predicate_start is not None and predicate_end is not None:
            expected_claim_evidence["predicateSpan"] = {
                "start": predicate_start,
                "end": predicate_end,
            }
        if (
            claim.get("object") != expected_object
            or claim.get("perspective")
            != {
                "kind": "entity",
                "holderEntityIds": [graph.world.owner_entity_id],
            }
            or claim.get("evidence") != expected_claim_evidence
            or claim.get("structuredStatus") != "candidate"
            or claim.get("writeState") != "candidate"
            or claim.get("accepted_entity_handles") != []
            or claim.get("related_mention_indices") != []
            or claim.get("predicate") != expected_predicate
            or claim.get("occurred_at") is not None
            or claim.get("normalized_occurred_at") is not None
            or claim.get("relationship_direction") is not None
            or claim.get("relationship_symmetric") is not False
            or claim.get("event_owner_participates") is not False
            or claim.get("event_subject_role") is not None
            or claim.get("event_related_roles") != []
        ):
            invalid("signed claim display")

        session_id = review_payload.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            invalid("session binding")
        if (
            binding.entity_id != entity.id
            or binding.evidence_id != record.id
            or binding.conversation_id != session_id
            or binding.occurred_at != occurred_at
            or binding.start_codepoint != mention_start
            or binding.end_codepoint != mention_end
            or binding.kind_hint != mention.get("kind_hint")
            or binding.continuity_scope != session_id
        ):
            invalid("identity binding")
        if (
            display.get("candidateMemory")
            != {"entities": [], "relationships": [], "events": [], "cognitions": []}
            or display.get("evidence")
            != [{"evidenceId": record.id, "text": record.content}]
            or display.get("formation") != []
            or display.get("unresolvedReferences") != []
            or display.get("semanticUncertainties") != []
            or display.get("identityBindings") != [_identity_binding_to_data(binding)]
            or display.get("transitionIntents") != []
            or display.get("cognitionReplacements") != []
            or display.get("statementKind") != statement_kind
            or display.get("ownerPerspective")
            != {"kind": "entity", "entityIds": [graph.world.owner_entity_id]}
            or display.get("target")
            != {
                "entityId": entity.id,
                "entityNames": [entity.canonical_name],
            }
        ):
            invalid("signed product display")
        expected_meaning_claim = {
            "claim_id": claim.get("id"),
            "kind": statement_kind,
            "subject_mention_index": 0,
            "text": claim_text,
            "start": claim_start,
            "end": claim_end,
            "value": value,
            "predicate": expected_predicate,
            "occurred_at": None,
            "normalized_occurred_at": None,
            "relationship_direction": None,
            "relationship_symmetric": False,
            "event_owner_participates": False,
            "event_subject_role": None,
            "event_related_roles": [],
            "evaluation_target_claim_index": None,
            "object_reference": None,
            "polarity": polarity,
            "epistemic_status": "stated",
            "disposition": "assert",
            "related_mention_indices": [],
            "accepted_entity_handles": [],
            "accepted_object_handles": [],
            "prior_cognition_handles": prior_handles,
        }
        if meaning != {
            "act": "assertion",
            "mention": None,
            "statement": None,
            "mentions": [expected_mention],
            "claims": [expected_meaning_claim],
            "code": "product_claim_bundle",
        }:
            invalid("signed meaning display")

        if require_current:
            prior = graph.cognitions.get(cognition_update.id)
            if (
                review_payload.get("baseWorldHash") != current_snapshot_hash
                or graph.entities.get(entity.id) != entity
                or prior != signed_before
                or cognition_update.id in self._superseded_ids()
            ):
                invalid("current Entity target")
            preview = MemoryWorldGraph(
                graph.world,
                graph.entities.copy(),
                graph.relationships.copy(),
                graph.events.copy(),
                graph.cognitions.copy(),
            )
            preview.cognitions[cognition_update.id] = cognition_update
            display_world = {
                "world": _data(preview.world),
                "entities": [
                    _data(item)
                    for item in sorted(
                        preview.entities.values(), key=lambda item: item.id
                    )
                ],
                "relationships": [
                    _data(item)
                    for item in sorted(
                        preview.relationships.values(),
                        key=lambda item: item.id,
                    )
                ],
                "events": [
                    _data(item)
                    for item in sorted(
                        preview.events.values(), key=lambda item: item.id
                    )
                ],
                "cognitions": [
                    _data(item)
                    for item in sorted(
                        preview.cognitions.values(),
                        key=lambda item: item.id,
                    )
                ],
            }
            if display.get("previewWorldHash") != _hash(_json(display_world)):
                invalid("preview World hash")

    def _validate_adapter_indirect_object_evidence_change_contract(
        self,
        delta: WorldDelta,
        records: Sequence[EvidenceRecord],
        review_payload: Mapping[str, object],
        graph: MemoryWorldGraph,
        *,
        display: Mapping[str, object],
        claims_bundle: Mapping[str, object],
        raw_claims: list[object],
        evolution_step: EvolutionStep,
        cognition_update: WorldCognition,
        current_snapshot_hash: str,
        require_current: bool,
    ) -> None:
        """Validate an indirect World-object same-ID Evidence change."""

        def invalid(detail: str) -> NoReturn:
            raise MemoryLoopIntegrityError(
                "adapter indirect World object Evidence contract is invalid: " + detail
            )

        structured_update = cognition_update.structured_claim
        is_event_statement = (
            cognition_update.target.kind == "event"
            and structured_update is not None
            and structured_update.statement_kind == "event_statement"
        )

        current_evidence_id = review_payload.get("currentEvidenceId")
        resolutions = claims_bundle.get("claim_resolutions")
        if (
            review_payload.get("autoApply") is not True
            or display.get("currentEvidenceId") != current_evidence_id
            or len(records) != 1
            or records[0].id != current_evidence_id
            or records[0].role != "user"
            or tuple(delta.source_evidence_ids) != (current_evidence_id,)
            or len(raw_claims) != 1
            or not isinstance(resolutions, list)
            or len(resolutions) != 1
            or delta.new_entities
            or delta.new_relationships
            or delta.new_events
            or delta.new_cognitions
            or delta.formation_traces
            or delta.unresolved_references
            or delta.semantic_uncertainties
        ):
            invalid("closed Evidence-change bundle shape")
        record = records[0]
        claim = raw_claims[0]
        resolution = resolutions[0]
        if not isinstance(claim, Mapping) or not isinstance(resolution, Mapping):
            invalid("claim resolution shape")
        value = claim.get("value")
        object_reference = claim.get("object_reference")
        object_handles = claim.get("accepted_object_handles")
        prior_handles = claim.get("prior_cognition_handles")
        polarity = claim.get("polarity")
        claim_kind = claim.get("kind")
        if (
            claim_kind != ("event" if is_event_statement else "evaluation")
            or claim.get("subject_mention_index") is not None
            or claim.get("evaluation_target_claim") is not None
            or claim.get("disposition") != "assert"
            or not isinstance(polarity, str)
            or polarity not in {"affirm", "negate"}
            or claim.get("epistemic_status") != "stated"
            or (
                value is not None
                if is_event_statement
                else not isinstance(value, Mapping)
            )
            or not isinstance(object_reference, Mapping)
            or not isinstance(object_handles, list)
            or len(object_handles) != 1
            or not isinstance(object_handles[0], str)
            or not object_handles[0]
            or not isinstance(prior_handles, list)
            or len(prior_handles) != 1
            or not isinstance(prior_handles[0], str)
            or not prior_handles[0]
        ):
            invalid("indirect Evidence-change claim")

        def exact_span(raw: Mapping[str, object], label: str) -> tuple[int, int, str]:
            start = raw.get("start")
            end = raw.get("end")
            text = raw.get("text")
            if (
                type(start) is not int
                or type(end) is not int
                or not isinstance(text, str)
                or not 0 <= start < end <= len(record.content)
                or record.content[start:end] != text
            ):
                invalid(label + " span")
            return start, end, text

        claim_start = claim.get("start")
        claim_end = claim.get("end")
        claim_text = claim.get("text")
        if (
            type(claim_start) is not int
            or type(claim_end) is not int
            or not isinstance(claim_text, str)
            or not 0 <= claim_start < claim_end <= len(record.content)
            or record.content[claim_start:claim_end] != claim_text
        ):
            invalid("claim span")
        value_start: int | None = None
        value_end: int | None = None
        value_text: str | None = None
        if not is_event_statement:
            assert isinstance(value, Mapping)
            value_start, value_end, value_text = exact_span(value, "value")
        reference_start, reference_end, _ = exact_span(
            object_reference,
            "object reference",
        )
        if not claim_start <= reference_start < reference_end <= claim_end or (
            not is_event_statement
            and (
                value_start is None
                or value_end is None
                or not claim_start <= value_start < value_end <= claim_end
                or max(value_start, reference_start) < min(value_end, reference_end)
            )
        ):
            invalid("claim-local spans")

        target_kind = resolution.get("target_kind")
        target_id = resolution.get("target_id")
        relationship = (
            graph.relationships.get(target_id)
            if target_kind == "relationship" and isinstance(target_id, str)
            else None
        )
        event = (
            graph.events.get(target_id)
            if target_kind == "event" and isinstance(target_id, str)
            else None
        )
        if (
            resolution.get("claim_index") != 0
            or resolution.get("subject_entity_id") is not None
            or resolution.get("related_entity_ids") != []
            or (relationship is None) == (event is None)
            or (is_event_statement and event is None)
        ):
            invalid("World object target")
        if relationship is not None:
            world_target = MemoryTarget("relationship", relationship.id)
            expected_resolution: dict[str, object] = {
                "claim_index": 0,
                "subject_entity_id": None,
                "related_entity_ids": [],
                "target_kind": "relationship",
                "target_id": relationship.id,
                "source_entity_id": relationship.source_entity_id,
                "target_entity_id": relationship.target_entity_id,
                "relation_type": relationship.relation_type,
                "bidirectional": relationship.bidirectional,
                "participant_entity_ids": [],
                "object_entity_ids": [],
                "owner_participates": None,
                "event_type": None,
                "occurred_at": None,
            }
            expected_object: dict[str, object] = {
                "kind": "relationship",
                "relationshipId": relationship.id,
                "sourceEntityId": relationship.source_entity_id,
                "targetEntityId": relationship.target_entity_id,
                "relationType": relationship.relation_type,
                "bidirectional": relationship.bidirectional,
            }
        else:
            assert event is not None
            owner_participates = any(
                item.entity_id == graph.world.owner_entity_id
                for item in event.participants
            )
            world_target = MemoryTarget("event", event.id)
            expected_resolution = {
                "claim_index": 0,
                "subject_entity_id": None,
                "related_entity_ids": [],
                "target_kind": "event",
                "target_id": event.id,
                "source_entity_id": None,
                "target_entity_id": None,
                "relation_type": None,
                "bidirectional": None,
                "participant_entity_ids": [
                    item.entity_id for item in event.participants
                ],
                "object_entity_ids": list(event.related_entity_ids),
                "owner_participates": owner_participates,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
            }
            expected_object = {
                "kind": "event",
                "eventId": event.id,
                "participantEntityIds": [item.entity_id for item in event.participants],
                "objectEntityIds": list(event.related_entity_ids),
                "ownerParticipates": owner_participates,
                "eventType": event.event_type,
                "occurredAt": event.occurred_at,
            }
            if is_event_statement:
                expected_object.update(
                    {
                        "summary": event.summary,
                        "participantRoles": [
                            {"entityId": item.entity_id, "role": item.role}
                            for item in event.participants
                        ],
                        "relationshipIds": list(event.relationship_ids),
                        "facets": [
                            {
                                "key": facet.key,
                                "value": facet.value,
                                "aboutEntityId": facet.about_entity_id,
                            }
                            for facet in event.facets
                        ],
                    }
                )
        if resolution != expected_resolution:
            invalid("closed World object resolution")
        relation = "contradicts" if polarity == "negate" else "reaffirms"
        source_relation: Literal["support", "contradict"] = (
            "contradict" if relation == "contradicts" else "support"
        )
        metadata = record.metadata
        occurred_at = (
            metadata.get("occurred_at") if isinstance(metadata, Mapping) else None
        )
        if (
            evolution_step.kind != "cognition_change"
            or evolution_step.relation != relation
            or evolution_step.subject != world_target
            or evolution_step.predecessor_ids != (cognition_update.id,)
            or evolution_step.successor_ids != (cognition_update.id,)
            or evolution_step.evidence_ids != (record.id,)
            or not isinstance(occurred_at, str)
            or evolution_step.effective_at != occurred_at
        ):
            invalid("typed cognition Evidence-change step")

        changes = display.get("cognitionEvidenceChanges")
        if not isinstance(changes, list) or len(changes) != 1:
            invalid("signed cognition Evidence change")
        change = changes[0]
        if not isinstance(change, Mapping):
            invalid("signed cognition Evidence change")
        before_raw = change.get("before")
        after_raw = change.get("after")
        if not isinstance(before_raw, Mapping) or not isinstance(after_raw, Mapping):
            invalid("signed cognition snapshots")
        try:
            signed_before = _cognition_from_data(before_raw)
            signed_after = _cognition_from_data(after_raw)
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError(
                "adapter indirect World object Evidence contract is invalid: "
                "signed cognition snapshots"
            ) from error
        if signed_after != cognition_update:
            invalid("signed update snapshot")
        expected_perspective = Perspective(
            "entity",
            (graph.world.owner_entity_id,),
        )
        prior_structured = signed_before.structured_claim
        if is_event_statement:
            assert event is not None
            structured_proposition_is_valid = (
                prior_structured is not None
                and prior_structured.statement_kind == "event_statement"
                and isinstance(prior_structured.predicate, str)
                and bool(prior_structured.predicate)
                and prior_structured.value is None
                and prior_structured.polarity == "assert"
                and prior_structured.epistemic_status == "asserted"
                and sum(
                    facet.key == "predicate"
                    and facet.value == prior_structured.predicate
                    and facet.about_entity_id is None
                    for facet in event.facets
                )
                == 1
            )
        else:
            structured_proposition_is_valid = (
                prior_structured is not None
                and prior_structured.statement_kind == "evaluation"
                and prior_structured.value == value_text
            )
        if (
            signed_before.id != cognition_update.id
            or signed_before.world_id != graph.world.world_id
            or signed_before.target != world_target
            or signed_before.perspective != expected_perspective
            or signed_before.content_type != "fact"
            or signed_before.formed_by != "stated"
            or not structured_proposition_is_valid
            or any(source.evidence_id == record.id for source in signed_before.sources)
        ):
            invalid("prior cognition")
        expected_sources = signed_before.sources + (
            EvidenceLink(record.id, source_relation),
        )
        support_count = sum(item.relation == "support" for item in expected_sources)
        contradict_count = sum(
            item.relation == "contradict" for item in expected_sources
        )
        expected_confidence = compute_confidence(
            ConfidenceInputs(
                signed_before.content_type,
                signed_before.formed_by,
                support_count,
                contradict_count,
            )
        )
        expected_update = replace(
            signed_before,
            sources=expected_sources,
            confidence=expected_confidence,
            cred_status=derive_cred_status(
                expected_confidence,
                contradict_count,
                signed_before.content_type,
                support_count=support_count,
            ),
        )
        if cognition_update != expected_update:
            invalid("same-ID cognition update")
        if (
            change.get("cognitionId") != cognition_update.id
            or change.get("relation") != relation
            or change.get("evidenceId") != record.id
            or display.get("evolutionSteps") != [_data(evolution_step)]
        ):
            invalid("signed Evidence-change projection")

        expected_handle = (
            "accepted-object:"
            + sha256(
                (
                    "turn-meaning-world-object-handle-v1\0"
                    f"{review_payload.get('baseWorldHash')}\0"
                    f"{world_target.kind}\0{world_target.id}"
                ).encode("utf-8")
            ).hexdigest()[:16]
        )
        expected_prior_handle = (
            "accepted-cognition:"
            + sha256(
                (
                    "turn-meaning-cognition-handle-v1\0"
                    f"{review_payload.get('baseWorldHash')}\0{cognition_update.id}"
                ).encode("utf-8")
            ).hexdigest()[:16]
        )
        if object_handles != [expected_handle] or prior_handles != [
            expected_prior_handle
        ]:
            invalid("opaque handles")

        expected_claim_evidence: dict[str, object] = {
            "evidenceId": record.id,
            "span": {"start": claim_start, "end": claim_end},
        }
        if not is_event_statement:
            expected_claim_evidence["valueSpan"] = {
                "start": value_start,
                "end": value_end,
            }
        if (
            claim.get("object") != expected_object
            or claim.get("perspective")
            != {
                "kind": "entity",
                "holderEntityIds": [graph.world.owner_entity_id],
            }
            or claim.get("evidence") != expected_claim_evidence
            or claim.get("structuredStatus") != "candidate"
            or claim.get("writeState") != "candidate"
            or claim.get("accepted_entity_handles") != []
            or claim.get("related_mention_indices") != []
            or claim.get("predicate") is not None
            or claim.get("occurred_at") is not None
            or claim.get("normalized_occurred_at") is not None
            or claim.get("relationship_direction") is not None
            or claim.get("relationship_symmetric") is not False
            or claim.get("event_owner_participates") is not False
            or claim.get("event_subject_role") is not None
            or claim.get("event_related_roles") != []
        ):
            invalid("signed claim display")
        if (
            display.get("candidateMemory")
            != {"entities": [], "relationships": [], "events": [], "cognitions": []}
            or display.get("evidence")
            != [{"evidenceId": record.id, "text": record.content}]
            or display.get("formation") != []
            or display.get("unresolvedReferences") != []
            or display.get("semanticUncertainties") != []
            or display.get("identityBindings") != []
            or display.get("transitionIntents") != []
            or display.get("cognitionReplacements") != []
            or display.get("statementKind")
            != ("event" if is_event_statement else "evaluation")
            or display.get("ownerPerspective")
            != {"kind": "entity", "entityIds": [graph.world.owner_entity_id]}
            or display.get("target") != expected_object
        ):
            invalid("signed product display")
        expected_meaning_claim = {
            "claim_id": claim.get("id"),
            "kind": "event" if is_event_statement else "evaluation",
            "subject_mention_index": None,
            "text": claim_text,
            "start": claim_start,
            "end": claim_end,
            "value": value,
            "predicate": None,
            "occurred_at": None,
            "normalized_occurred_at": None,
            "relationship_direction": None,
            "relationship_symmetric": False,
            "event_owner_participates": False,
            "event_subject_role": None,
            "event_related_roles": [],
            "evaluation_target_claim_index": None,
            "object_reference": object_reference,
            "polarity": polarity,
            "epistemic_status": "stated",
            "disposition": "assert",
            "related_mention_indices": [],
            "accepted_entity_handles": [],
            "accepted_object_handles": object_handles,
            "prior_cognition_handles": prior_handles,
        }
        if display.get("meaning") != {
            "act": "assertion",
            "mention": None,
            "statement": None,
            "mentions": [],
            "claims": [expected_meaning_claim],
            "code": "product_claim_bundle",
        }:
            invalid("signed meaning display")

        if require_current:
            prior = graph.cognitions.get(cognition_update.id)
            if relationship is not None:
                current_ids = current_relationship_ids(
                    graph,
                    self._accepted_evolution_steps(),
                )
                target_is_current = relationship.id in current_ids
            else:
                assert event is not None
                current_ids = frozenset(graph.events)
                target_is_current = graph.events.get(event.id) == event
            if (
                review_payload.get("baseWorldHash") != current_snapshot_hash
                or not target_is_current
                or prior != signed_before
                or cognition_update.id in self._superseded_ids()
            ):
                invalid("current World target")
            session_id = review_payload.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                invalid("session binding")
            source_ids = {
                source.evidence_id
                for item in graph.cognitions.values()
                if item.target.kind == world_target.kind
                and item.target.id in current_ids
                for source in item.sources
            }
            same_session_evidence_ids = {
                item.id
                for item in self._evidence_for_ids(tuple(sorted(source_ids)))
                if item.role == "user"
                and isinstance(item.metadata, Mapping)
                and item.metadata.get("conversation_id") == session_id
            }
            eligible_target_ids = {
                item.target.id
                for item in graph.cognitions.values()
                if item.target.kind == world_target.kind
                and item.target.id in current_ids
                and any(
                    source.evidence_id in same_session_evidence_ids
                    for source in item.sources
                )
            }
            if eligible_target_ids != {world_target.id}:
                invalid("same-session World object authority")
            preview = MemoryWorldGraph(
                graph.world,
                graph.entities.copy(),
                graph.relationships.copy(),
                graph.events.copy(),
                graph.cognitions.copy(),
            )
            preview.cognitions[cognition_update.id] = cognition_update
            display_world = {
                "world": _data(preview.world),
                "entities": [
                    _data(item)
                    for item in sorted(
                        preview.entities.values(), key=lambda item: item.id
                    )
                ],
                "relationships": [
                    _data(item)
                    for item in sorted(
                        preview.relationships.values(),
                        key=lambda item: item.id,
                    )
                ],
                "events": [
                    _data(item)
                    for item in sorted(
                        preview.events.values(), key=lambda item: item.id
                    )
                ],
                "cognitions": [
                    _data(item)
                    for item in sorted(
                        preview.cognitions.values(),
                        key=lambda item: item.id,
                    )
                ],
            }
            if display.get("previewWorldHash") != _hash(_json(display_world)):
                invalid("preview World hash")

    def _normalize_evidence(
        self, records: Sequence[EvidenceRecord]
    ) -> tuple[EvidenceRecord, ...]:
        normalized: list[EvidenceRecord] = []
        ids: set[str] = set()
        for record in records:
            if (
                not isinstance(record, EvidenceRecord)
                or not record.id.strip()
                or not record.content.strip()
                or record.role != "user"
            ):
                raise ValueError(
                    "evidence records must be non-empty user EvidenceRecord values"
                )
            if record.id in ids:
                raise EvidenceConflictError(
                    f"duplicate evidence id in proposal: {record.id}"
                )
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
                raise TypeError(
                    "product bundle transition intents must be CognitionTransitionIntent values"
                )
            if not all(
                isinstance(value, str) and value.strip()
                for value in (
                    intent.prior_cognition_id,
                    intent.successor_cognition_id,
                    intent.reason,
                    intent.statement_kind,
                )
            ):
                raise ValueError(
                    "product bundle transition intent fields must be non-empty strings"
                )
            if intent.reason not in _PRODUCT_TRANSITION_REASONS:
                raise ValueError(
                    "product bundle transition intent reason is unsupported"
                )
            if intent.prior_cognition_id in prior_ids:
                raise ValueError(
                    "product bundle transition intents cannot replace one prior twice"
                )
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
            raise MemoryLoopIntegrityError(
                "product bundle cognition successors are duplicated"
            )
        superseded = self._superseded_ids()
        for intent in intents:
            prior = graph.cognitions.get(intent.prior_cognition_id)
            if prior is None:
                raise MemoryLoopError("product bundle transition prior is not accepted")
            if prior.id in superseded:
                raise MemoryLoopError(
                    "product bundle transition prior is already historical"
                )
            successor = successors.get(intent.successor_cognition_id)
            if successor is None:
                raise MemoryLoopError(
                    "product bundle transition successor is not a new bundle cognition"
                )
            if successor.target != prior.target:
                raise MemoryLoopError(
                    "product bundle transition target is incompatible"
                )
            if successor.perspective != prior.perspective:
                raise MemoryLoopError(
                    "product bundle transition perspective is incompatible"
                )
            prior_kind = _structured_claim_statement_kind(prior)
            successor_kind = _structured_claim_statement_kind(successor)
            if (
                prior_kind != intent.statement_kind
                or successor_kind != intent.statement_kind
            ):
                raise MemoryLoopError(
                    "product bundle transition statement_kind is incompatible"
                )

    @staticmethod
    def _normalize_product_evolution_steps(
        steps: Sequence[EvolutionStep],
    ) -> tuple[EvolutionStep, ...]:
        normalized: list[EvolutionStep] = []
        seen_ids: set[str] = set()
        seen_predecessors: set[str] = set()
        seen_successors: set[str] = set()
        for step in steps:
            if not isinstance(step, EvolutionStep):
                raise TypeError(
                    "product bundle evolution steps must be EvolutionStep values"
                )
            is_relationship_successor = (
                step.kind == "relationship_successor"
                and step.relation == "reestablished"
            )
            is_cognition_evidence_change = (
                step.kind == "cognition_change"
                and step.relation in {"contradicts", "reaffirms"}
            )
            if not (is_relationship_successor or is_cognition_evidence_change):
                raise ValueError(
                    "product bundle evolution step kind or relation is unsupported"
                )
            if not isinstance(step.id, str) or not step.id.strip():
                raise ValueError("product bundle evolution step id is invalid")
            if step.id in seen_ids:
                raise ValueError("product bundle evolution step ids must be unique")
            if len(step.predecessor_ids) != 1 or len(step.successor_ids) != 1:
                raise ValueError(
                    "product bundle evolution requires one predecessor and one successor"
                )
            predecessor_id = step.predecessor_ids[0]
            successor_id = step.successor_ids[0]
            if is_cognition_evidence_change and predecessor_id != successor_id:
                raise ValueError(
                    "product cognition evidence change must retain its cognition id"
                )
            if predecessor_id in seen_predecessors:
                raise ValueError("product bundle cannot update one predecessor twice")
            if successor_id in seen_successors:
                raise ValueError(
                    "product bundle cannot merge predecessors into one successor"
                )
            seen_ids.add(step.id)
            seen_predecessors.add(predecessor_id)
            seen_successors.add(successor_id)
            normalized.append(step)
        return tuple(normalized)

    @staticmethod
    def _normalize_product_cognition_updates(
        updates: Sequence[WorldCognition],
    ) -> tuple[WorldCognition, ...]:
        normalized: list[WorldCognition] = []
        seen_ids: set[str] = set()
        for update in updates:
            if not isinstance(update, WorldCognition):
                raise TypeError(
                    "product bundle cognition updates must be WorldCognition values"
                )
            if not isinstance(update.id, str) or not update.id.strip():
                raise ValueError("product bundle cognition update id is invalid")
            if update.id in seen_ids:
                raise ValueError("product bundle cognition update ids must be unique")
            seen_ids.add(update.id)
            normalized.append(update)
        return tuple(normalized)

    def _validate_product_evolution_steps(
        self,
        graph: MemoryWorldGraph,
        delta: WorldDelta,
        evidence_record: EvidenceRecord,
        steps: Sequence[EvolutionStep],
        cognition_updates: Sequence[WorldCognition] = (),
    ) -> MemoryWorldGraph:
        """Revalidate and preview all typed product evolution atomically."""

        current_relationships = current_relationship_ids(
            graph,
            self._accepted_evolution_steps(),
        )
        newly_created_relationships = {item.id for item in delta.new_relationships}
        relationship_targets = {
            cognition.target.id
            for cognition in (*delta.new_cognitions, *cognition_updates)
            if cognition.target.kind == "relationship"
        }
        if any(
            target_id not in current_relationships
            and target_id not in newly_created_relationships
            for target_id in relationship_targets
        ):
            raise MemoryLoopError("product bundle targets a non-current Relationship")

        if not steps:
            if cognition_updates:
                raise MemoryLoopError(
                    "product bundle cognition updates require evolution steps"
                )
            return delta.apply_to(
                graph,
                self._evidence_ids() | {evidence_record.id},
            )
        accepted_steps = self._accepted_evolution_steps()
        accepted_predecessors = {
            predecessor_id
            for accepted in accepted_steps
            if accepted.step.kind == "relationship_successor"
            for predecessor_id in accepted.step.predecessor_ids
        }
        if any(
            predecessor_id in accepted_predecessors
            for step in steps
            if step.kind == "relationship_successor"
            for predecessor_id in step.predecessor_ids
        ):
            raise MemoryLoopError(
                "product bundle relationship predecessor is already linked"
            )
        metadata = evidence_record.metadata
        occurred_at = (
            metadata.get("occurred_at") if isinstance(metadata, Mapping) else None
        )
        if not isinstance(occurred_at, str) or any(
            step.effective_at != occurred_at for step in steps
        ):
            raise MemoryLoopError(
                "product bundle evolution time is not bound to Evidence"
            )
        try:
            return WorldEvolutionPlan(
                delta,
                tuple(steps),
                tuple(cognition_updates),
            ).apply_to(
                graph,
                self._evidence_ids() | {evidence_record.id},
                superseded_cognition_ids=self._superseded_ids(),
                known_transition_ids=frozenset(item.step.id for item in accepted_steps),
                ended_relationship_ids=accepted_historical_relationship_ids(
                    accepted_steps
                ),
            )
        except WorldEvolutionValidationError as error:
            raise MemoryLoopError("product bundle evolution is invalid") from error

    @staticmethod
    def _normalize_product_claim_slices(
        slices: Sequence[ProductClaimSlice],
    ) -> tuple[ProductClaimSlice, ...]:
        normalized: list[ProductClaimSlice] = []
        seen_claim_ids: set[str] = set()
        for item in slices:
            if not isinstance(item, ProductClaimSlice):
                raise TypeError(
                    "product bundle claim slices must be ProductClaimSlice values"
                )
            if not isinstance(item.claim_id, str) or not item.claim_id.strip():
                raise ValueError("product bundle claim_id must be a non-empty string")
            if item.claim_id in seen_claim_ids:
                raise ValueError("product bundle claim_ids must be unique")
            for field_name in (
                "entity_ids",
                "relationship_ids",
                "event_ids",
                "cognition_ids",
                "depends_on_claim_ids",
            ):
                values = getattr(item, field_name)
                if not isinstance(values, tuple) or any(
                    not isinstance(value, str) or not value.strip() for value in values
                ):
                    raise ValueError(
                        f"product bundle {field_name} must contain non-empty strings"
                    )
                if len(set(values)) != len(values):
                    raise ValueError(
                        f"product bundle {field_name} cannot repeat a value"
                    )
            for field_name in ("identity_binding_indices", "transition_intent_indices"):
                values = getattr(item, field_name)
                if not isinstance(values, tuple) or any(
                    type(value) is not int or value < 0 for value in values
                ):
                    raise ValueError(
                        f"product bundle {field_name} must contain non-negative integers"
                    )
                if len(set(values)) != len(values):
                    raise ValueError(
                        f"product bundle {field_name} cannot repeat a value"
                    )
            seen_claim_ids.add(item.claim_id)
            normalized.append(item)
        return tuple(normalized)

    @staticmethod
    def _normalize_always_entity_ids(entity_ids: Sequence[str]) -> tuple[str, ...]:
        values = tuple(entity_ids)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(
                "product bundle always_include_entity_ids must contain non-empty strings"
            )
        if len(set(values)) != len(values):
            raise ValueError(
                "product bundle always_include_entity_ids cannot repeat an entity"
            )
        return values

    @staticmethod
    def _normalize_always_binding_indices(indices: Sequence[int]) -> tuple[int, ...]:
        values = tuple(indices)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError(
                "product bundle always_include_identity_binding_indices must contain non-negative integers"
            )
        if len(set(values)) != len(values):
            raise ValueError(
                "product bundle always_include_identity_binding_indices cannot repeat an index"
            )
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
                raise ValueError(
                    "product bundle always-included records require claim slices"
                )
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
                raise ValueError(
                    f"product bundle always-included {field_name} are unknown"
                )
        for field_name, expected_values in expected.items():
            owner_by_value: dict[object, str] = {}
            for item in slices:
                for value in getattr(item, field_name):
                    if value not in expected_values:
                        raise ValueError(
                            f"product bundle {field_name} references an unknown record"
                        )
                    if value in owner_by_value:
                        raise ValueError(
                            f"product bundle {field_name} assigns one record to multiple claims"
                        )
                    owner_by_value[value] = item.claim_id
            always_values = always.get(field_name, set())
            if set(owner_by_value) & always_values:
                raise ValueError(
                    f"product bundle {field_name} cannot be both owned and always included"
                )
            if set(owner_by_value) | always_values != expected_values:
                raise ValueError(
                    f"product bundle {field_name} must cover every offered record"
                )
        if delta.unresolved_references or delta.semantic_uncertainties:
            raise ValueError(
                "selection-aware product bundles cannot contain unmapped uncertainty records"
            )

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
            raise MemoryLoopIntegrityError(
                "product bundle Evidence boundary is invalid"
            )
        bindings = _identity_bindings_from_payload(payload)
        intents = _product_transition_intents_from_payload(payload)
        evolution_steps = _product_evolution_steps_from_payload(payload)
        cognition_updates = _product_cognition_updates_from_payload(payload)
        slices = _product_claim_slices_from_payload(payload)
        always_entities, always_binding_indices = (
            _always_included_product_records_from_payload(payload)
        )
        self._validate_product_claim_slices(
            delta, bindings, intents, slices, always_entities, always_binding_indices
        )

        if decision == "reject" and selected_claim_ids not in (None, ()):
            raise ValueError("a rejected product bundle cannot select claims")
        if not slices:
            if selected_claim_ids is not None:
                raise ValueError("product bundle does not expose claim-level selection")
            selected: tuple[str, ...] = ()
            applied: tuple[str, ...] = ()
            effective: Decision = decision
            accepted_payload: Mapping[str, object] | None = (
                payload if decision == "accept" else None
            )
        else:
            if evolution_steps or cognition_updates:
                raise MemoryLoopIntegrityError(
                    "product evolution cannot be claim-selected"
                )
            selected = _normalize_selected_claim_ids(
                selected_claim_ids, tuple(item.claim_id for item in slices), decision
            )
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
                selected_delta = _delta_from_data(
                    cast(Mapping[str, object], accepted_payload["delta"])
                )
                selected_bindings = _identity_bindings_from_payload(accepted_payload)
                selected_intents = _product_transition_intents_from_payload(
                    accepted_payload
                )
                selected_evolution_steps = _product_evolution_steps_from_payload(
                    accepted_payload
                )
                preview = selected_delta.apply_to(
                    graph, self._evidence_ids() | {records[0].id}
                )
                self._validate_identity_bindings(selected_bindings, records, preview)
                self._validate_product_transition_intents(
                    graph, selected_delta, selected_intents
                )
                self._validate_product_evolution_steps(
                    graph,
                    selected_delta,
                    records[0],
                    selected_evolution_steps,
                    _product_cognition_updates_from_payload(accepted_payload),
                )

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
            existing = self._conn.execute(
                "SELECT content, payload_json FROM evidence_ledger WHERE id = ?",
                (record.id,),
            ).fetchone()
            if existing is None:
                continue
            try:
                raw = json.loads(cast(str, existing["payload_json"]))
                if not isinstance(raw, Mapping):
                    raise TypeError("Evidence payload is not an object")
                stored = _evidence_from_data(cast(Mapping[str, object], raw))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise MemoryLoopIntegrityError(
                    f"stored Evidence payload is invalid: {record.id}"
                ) from error
            if cast(str, existing["content"]) != stored.content or _evidence_to_data(
                stored
            ) != _evidence_to_data(record):
                raise EvidenceConflictError(f"evidence id conflict: {record.id}")

    def _evidence_ids(self) -> set[str]:
        return {
            cast(str, row["id"])
            for row in self._conn.execute("SELECT id FROM evidence_ledger")
        }

    def _superseded_ids(self) -> frozenset[str]:
        return frozenset(
            cast(str, row["prior_cognition_id"])
            for row in self._conn.execute(
                "SELECT prior_cognition_id FROM cognition_transitions"
            )
        )

    def _review_from_row(self, row: sqlite3.Row) -> PendingReview:
        raw = row["review_payload_json"]
        payload = (
            cast(Mapping[str, object], json.loads(cast(str, raw)))
            if raw is not None
            else None
        )
        return PendingReview(
            cast(str, row["id"]),
            cast(ReviewKind, row["kind"]),
            cast(str, row["result_hash"]),
            int(row["base_revision"]),
            payload,
        )

    @staticmethod
    def _decision_receipt_from_row(row: sqlite3.Row) -> DecisionReceipt:
        proposal_id = row["proposal_id"]
        offered_result_hash = row["offered_result_hash"]
        effective_decision = row["effective_decision"]
        world_revision = row["world_revision"]
        snapshot_hash = row["snapshot_hash"]
        decided_at = row["decided_at"]
        receipt_hash = row["receipt_hash"]
        if (
            not isinstance(proposal_id, str)
            or not proposal_id
            or not isinstance(offered_result_hash, str)
            or not offered_result_hash
            or effective_decision not in ("accept", "reject")
            or type(world_revision) is not int
            or world_revision < 0
            or not isinstance(snapshot_hash, str)
            or not snapshot_hash
            or not isinstance(decided_at, str)
            or not decided_at
            or not isinstance(receipt_hash, str)
            or not receipt_hash
        ):
            raise MemoryLoopIntegrityError("decision receipt has an invalid shape")
        receipt = DecisionReceipt(
            proposal_id,
            offered_result_hash,
            cast(Decision, effective_decision),
            world_revision,
            snapshot_hash,
            decided_at,
            receipt_hash,
        )
        if _decision_receipt_hash(receipt) != receipt.receipt_hash:
            raise MemoryLoopIntegrityError("decision receipt hash mismatch")
        return receipt

    def _evidence_for_ids(
        self, evidence_ids: Sequence[str]
    ) -> tuple[EvidenceRecord, ...]:
        ids = tuple(dict.fromkeys(evidence_ids))
        result: list[EvidenceRecord] = []
        for evidence_id in ids:
            row = self._conn.execute(
                "SELECT payload_json FROM evidence_ledger WHERE id = ?", (evidence_id,)
            ).fetchone()
            if row is not None:
                result.append(
                    _evidence_from_data(
                        cast(
                            Mapping[str, object],
                            json.loads(cast(str, row["payload_json"])),
                        )
                    )
                )
        return tuple(result)

    @staticmethod
    def _recall_evidence_traces(
        reconstruction: MemoryReconstruction,
        records: Sequence[EvidenceRecord],
    ) -> tuple[RecallEvidenceTrace, ...]:
        """Hydrate only provenance selected by the accepted-World traversal."""

        current_ids = frozenset(reconstruction.current_cognition_ids)
        historical_ids = frozenset(reconstruction.historical_cognition_ids)
        by_id = {record.id: record for record in records}
        traces: list[RecallEvidenceTrace] = []
        for provenance in reconstruction.provenance:
            subject_state: RecallEvidenceSubjectState
            if provenance.subject_kind == "event":
                subject_state = "event"
            elif provenance.subject_id in current_ids:
                subject_state = "current"
            elif provenance.subject_id in historical_ids:
                subject_state = "historical"
            else:  # pragma: no cover - reconstruction owns this closed invariant
                raise MemoryLoopIntegrityError(
                    "recall provenance points outside current/history cognition selection"
                )
            record = by_id.get(provenance.evidence_id)
            if record is None:
                traces.append(
                    _recall_evidence_trace(
                        provenance,
                        subject_state,
                        "missing",
                    )
                )
                continue
            system_evidence = _validated_recall_system_evidence(record)
            traces.append(
                _recall_evidence_trace(
                    provenance,
                    subject_state,
                    "available" if system_evidence is not None else "legacy",
                    system_evidence,
                )
            )
        return tuple(traces)

    def evidence_for_ids(
        self,
        evidence_ids: Sequence[str],
    ) -> tuple[EvidenceRecord, ...]:
        """Read durable Evidence records without changing World or recall state."""

        return self._evidence_for_ids(evidence_ids)


def _recall_evidence_trace(
    provenance: ProvenanceRef,
    subject_state: RecallEvidenceSubjectState,
    envelope_status: RecallEvidenceEnvelopeStatus,
    system_evidence: Mapping[str, object] | None = None,
) -> RecallEvidenceTrace:
    return RecallEvidenceTrace(
        provenance.subject_kind,
        provenance.subject_id,
        subject_state,
        provenance.evidence_id,
        provenance.relation,
        envelope_status,
        system_evidence,
    )


def _validated_recall_system_evidence(
    record: EvidenceRecord,
) -> Mapping[str, object] | None:
    """Return one exact stored envelope, or fail closed on malformed new data."""

    metadata = record.metadata
    if metadata is None or "system_evidence" not in metadata:
        return None
    raw = metadata.get("system_evidence")
    if not isinstance(raw, Mapping) or set(raw) != _SYSTEM_EVIDENCE_KEYS:
        raise MemoryLoopIntegrityError(
            f"Evidence {record.id!r} has an invalid system Evidence envelope"
        )
    for name in ("id", "subjectId", "hostId"):
        value = raw.get(name)
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 200
        ):
            raise MemoryLoopIntegrityError(
                f"Evidence {record.id!r} has an invalid system Evidence {name}"
            )
    for name, maximum in (("originId", 1_000), ("correctsEvidenceId", 200)):
        value = raw.get(name)
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            raise MemoryLoopIntegrityError(
                f"Evidence {record.id!r} has an invalid system Evidence {name}"
            )
    content = raw.get("rawContent")
    summary = raw.get("summary")
    if not isinstance(content, str) or not content or len(content) > 4_000:
        raise MemoryLoopIntegrityError(
            f"Evidence {record.id!r} has invalid system Evidence raw content"
        )
    if not isinstance(summary, str) or len(summary) > 4_000:
        raise MemoryLoopIntegrityError(
            f"Evidence {record.id!r} has an invalid system Evidence summary"
        )
    for name in ("occurredAt", "recordedAt"):
        value = raw.get(name)
        if not isinstance(value, str) or not value or len(value) > 64:
            raise MemoryLoopIntegrityError(
                f"Evidence {record.id!r} has an invalid system Evidence {name}"
            )
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MemoryLoopIntegrityError(
                f"Evidence {record.id!r} has an invalid system Evidence {name}"
            ) from exc
        if parsed.tzinfo is None:
            raise MemoryLoopIntegrityError(
                f"Evidence {record.id!r} has a timezone-free system Evidence {name}"
            )
    for name in ("allowLocalRead", "allowCloudRead", "allowInference"):
        if type(raw.get(name)) is not bool:
            raise MemoryLoopIntegrityError(
                f"Evidence {record.id!r} has an invalid system Evidence {name}"
            )
    occurred_at = metadata.get("occurred_at")
    if (
        raw.get("id") != record.id
        or raw.get("sourceKind") != "spoken"
        or content != record.content
        or raw.get("allowLocalRead") is not True
        or raw.get("allowInference") is not True
        or (occurred_at is not None and occurred_at != raw.get("occurredAt"))
    ):
        raise MemoryLoopIntegrityError(
            f"Evidence {record.id!r} does not match its system Evidence envelope"
        )
    return cast(Mapping[str, object], deepcopy(dict(raw)))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def _result_hash(*parts: object) -> str:
    return _hash(_json(parts))


def _decision_receipt_hash(receipt: DecisionReceipt) -> str:
    return _result_hash(
        "proposal_decision_receipt_v1",
        receipt.proposal_id,
        receipt.offered_result_hash,
        receipt.effective_decision,
        receipt.world_revision,
        receipt.snapshot_hash,
        receipt.decided_at,
    )


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
    graph = MemoryWorldGraph(
        PersonalWorld(
            cast(str, world_raw["world_id"]), cast(str, world_raw["owner_entity_id"])
        )
    )
    for raw in cast(list[object], data["entities"]):
        graph.add_entity(_entity_from_data(cast(Mapping[str, object], raw)))
    for raw in cast(list[object], data["relationships"]):
        graph.add_relationship(_relationship_from_data(cast(Mapping[str, object], raw)))
    for raw in cast(list[object], data["events"]):
        graph.add_event(_event_from_data(cast(Mapping[str, object], raw)))
    for raw in cast(list[object], data["cognitions"]):
        graph.add_cognition(_cognition_from_data(cast(Mapping[str, object], raw)))
    return graph


def _entity_from_data(raw: Mapping[str, object]) -> Entity:
    return Entity(
        cast(str, raw["id"]),
        cast(str, raw["world_id"]),
        cast(str, raw["kind"]),
        cast(str, raw["canonical_name"]),
        tuple(cast(list[str], raw.get("aliases", []))),
    )


def _relationship_from_data(raw: Mapping[str, object]) -> Relationship:
    bidirectional = raw.get("bidirectional", False)
    if type(bidirectional) is not bool:
        raise MemoryLoopIntegrityError("relationship bidirectional value is invalid")
    return Relationship(
        cast(str, raw["id"]),
        cast(str, raw["world_id"]),
        cast(str, raw["source_entity_id"]),
        cast(str, raw["target_entity_id"]),
        cast(str, raw["relation_type"]),
        bidirectional,
        cast(str | None, raw.get("status")),
        cast(str | None, raw.get("valid_from")),
        cast(str | None, raw.get("valid_to")),
    )


def _event_from_data(raw: Mapping[str, object]) -> WorldEvent:
    return WorldEvent(
        cast(str, raw["id"]),
        cast(str, raw["world_id"]),
        cast(str, raw["event_type"]),
        cast(str, raw["summary"]),
        cast(str, raw["occurred_at"]),
        tuple(
            EventParticipant(
                cast(str, item["entity_id"]), cast(str | None, item.get("role"))
            )
            for item in cast(list[Mapping[str, object]], raw.get("participants", []))
        ),
        tuple(cast(list[str], raw.get("related_entity_ids", []))),
        tuple(cast(list[str], raw.get("relationship_ids", []))),
        tuple(
            EventFacet(
                cast(str, item["key"]),
                cast(str, item["value"]),
                cast(str | None, item.get("about_entity_id")),
            )
            for item in cast(list[Mapping[str, object]], raw.get("facets", []))
        ),
        tuple(cast(list[str], raw.get("evidence_ids", []))),
    )


def _cognition_to_data(cognition: WorldCognition) -> dict[str, object]:
    return cast(dict[str, object], _data(cognition))


def _cognition_json(cognition: WorldCognition) -> str:
    return _json(_cognition_to_data(cognition))


def _cognition_from_data(raw: Mapping[str, object]) -> WorldCognition:
    target = cast(Mapping[str, object], raw["target"])
    perspective = cast(Mapping[str, object], raw["perspective"])
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
            raise MemoryLoopIntegrityError(
                "cognition structured_claim is invalid"
            ) from error
    else:
        raise MemoryLoopIntegrityError("cognition structured_claim is invalid")
    return WorldCognition(
        cast(str, raw["id"]),
        cast(str, raw["world_id"]),
        MemoryTarget(cast(Any, target["kind"]), cast(str, target["id"])),
        cast(str, raw["content"]),
        cast(Any, raw["content_type"]),
        cast(Any, raw["formed_by"]),
        int(cast(Any, raw["confidence"])),
        cast(Any, raw["cred_status"]),
        Perspective(
            cast(Any, perspective["kind"]),
            tuple(cast(list[str], perspective.get("holder_entity_ids", []))),
        ),
        tuple(
            EvidenceLink(cast(str, item["evidence_id"]), cast(Any, item["relation"]))
            for item in cast(list[Mapping[str, object]], raw.get("sources", []))
        ),
        cast(str | None, raw.get("scope")),
        cast(str | None, raw.get("valid_at")),
        cast(str | None, raw.get("invalid_at")),
        structured_claim,
    )


def _delta_to_data(delta: WorldDelta) -> dict[str, object]:
    return cast(dict[str, object], _data(delta))


def _delta_json(delta: WorldDelta) -> str:
    return _json(_delta_to_data(delta))


def _delta_from_data(raw: Mapping[str, object]) -> WorldDelta:
    # Formation traces are intentionally audit-only; staged payloads preserve
    # them structurally as JSON but validated additions already checked them.
    # Reconstructing them fully here would duplicate the extractor contract.
    # Revalidation during acceptance still runs against the durable ledger.
    from .delta import (
        ClaimSpan,
        FormationContentBinding,
        FormationSourceTrace,
        FormationTrace,
        SemanticUncertainty,
        UnresolvedReference,
    )

    traces = tuple(
        FormationTrace(
            cast(str, item["cognition_id"]),
            bool(item["model_inferred_proposal"]),
            tuple(
                FormationSourceTrace(
                    cast(str, source["evidence_id"]),
                    cast(Any, source["relation"]),
                    cast(Any, source["proposition_origin_proposal"]),
                    cast(Any, source["response_act_proposal"]),
                    ClaimSpan(
                        int(
                            cast(
                                Any,
                                cast(Mapping[str, object], source["claim_span"])[
                                    "start_codepoint"
                                ],
                            )
                        ),
                        int(
                            cast(
                                Any,
                                cast(Mapping[str, object], source["claim_span"])[
                                    "end_codepoint"
                                ],
                            )
                        ),
                        cast(
                            str,
                            cast(Mapping[str, object], source["claim_span"])[
                                "source_content_sha256"
                            ],
                        ),
                        cast(
                            str,
                            cast(Mapping[str, object], source["claim_span"])[
                                "claim_sha256"
                            ],
                        ),
                    ),
                    cast(str | None, source.get("preceding_assistant_turn_id")),
                    cast(str | None, source.get("preceding_assistant_content_sha256")),
                    cast(Any, source["local_origin_decision"]),
                    cast(str, source["decision_code"]),
                )
                for source in cast(list[Mapping[str, object]], item["sources"])
            ),
            cast(Any, item["derived_formed_by"]),
            int(cast(Any, item["raw_support_count"])),
            int(cast(Any, item["effective_support_count"])),
            int(cast(Any, item["contradict_count"])),
            content_bindings=tuple(
                FormationContentBinding(
                    cast(Any, binding["semantic_role"]),
                    cast(str, binding["about_entity_id"]),
                    cast(str, binding["evidence_id"]),
                    ClaimSpan(
                        int(
                            cast(
                                Any,
                                cast(Mapping[str, object], binding["claim_span"])[
                                    "start_codepoint"
                                ],
                            )
                        ),
                        int(
                            cast(
                                Any,
                                cast(Mapping[str, object], binding["claim_span"])[
                                    "end_codepoint"
                                ],
                            )
                        ),
                        cast(
                            str,
                            cast(Mapping[str, object], binding["claim_span"])[
                                "source_content_sha256"
                            ],
                        ),
                        cast(
                            str,
                            cast(Mapping[str, object], binding["claim_span"])[
                                "claim_sha256"
                            ],
                        ),
                    ),
                )
                for binding in cast(
                    list[Mapping[str, object]], item.get("content_bindings", [])
                )
            ),
        )
        for item in cast(list[Mapping[str, object]], raw.get("formation_traces", []))
    )
    return WorldDelta(
        cast(str, raw["world_id"]),
        tuple(cast(list[str], raw["source_evidence_ids"])),
        tuple(
            _entity_from_data(cast(Mapping[str, object], item))
            for item in cast(list[object], raw.get("new_entities", []))
        ),
        tuple(
            _relationship_from_data(cast(Mapping[str, object], item))
            for item in cast(list[object], raw.get("new_relationships", []))
        ),
        tuple(
            _event_from_data(cast(Mapping[str, object], item))
            for item in cast(list[object], raw.get("new_events", []))
        ),
        tuple(
            _cognition_from_data(cast(Mapping[str, object], item))
            for item in cast(list[object], raw.get("new_cognitions", []))
        ),
        formation_traces=traces,
        unresolved_references=tuple(
            UnresolvedReference(
                cast(str, item["mention"]), tuple(cast(list[str], item["evidence_ids"]))
            )
            for item in cast(
                list[Mapping[str, object]], raw.get("unresolved_references", [])
            )
        ),
        semantic_uncertainties=tuple(
            SemanticUncertainty(
                cast(str, item["detail"]), tuple(cast(list[str], item["evidence_ids"]))
            )
            for item in cast(
                list[Mapping[str, object]], raw.get("semantic_uncertainties", [])
            )
        ),
    )


def _evidence_to_data(record: EvidenceRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "content": record.content,
        "role": record.role,
        "metadata": _data(record.metadata) if record.metadata is not None else None,
    }


def _evidence_json(records: Sequence[EvidenceRecord]) -> str:
    return _json([_evidence_to_data(item) for item in records])


def _evidence_from_data(raw: Mapping[str, object]) -> EvidenceRecord:
    return EvidenceRecord(
        cast(str, raw["id"]),
        cast(str, raw["content"]),
        cast(Any, raw.get("role", "user")),
        cast(Mapping[str, object] | None, raw.get("metadata")),
    )


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


def _product_evolution_steps_from_payload(
    payload: Mapping[str, object],
) -> tuple[EvolutionStep, ...]:
    raw = payload.get("evolution_steps")
    if raw is None and "evolution_steps" not in payload:
        return ()
    if not isinstance(raw, list):
        raise MemoryLoopIntegrityError(
            "product bundle evolution step payload is invalid"
        )
    try:
        steps = tuple(
            evolution_step_from_data(item, path=f"evolution_steps[{index}]")
            for index, item in enumerate(raw)
        )
        return MemoryLoop._normalize_product_evolution_steps(steps)
    except (TypeError, ValueError, WorldEvolutionValidationError) as error:
        raise MemoryLoopIntegrityError(
            "product bundle evolution step payload is invalid"
        ) from error


def _product_cognition_updates_from_payload(
    payload: Mapping[str, object],
) -> tuple[WorldCognition, ...]:
    raw = payload.get("cognition_updates")
    if raw is None and "cognition_updates" not in payload:
        return ()
    if not isinstance(raw, list):
        raise MemoryLoopIntegrityError(
            "product bundle cognition update payload is invalid"
        )
    try:
        updates = tuple(
            _cognition_from_data(cast(Mapping[str, object], item))
            for item in raw
            if isinstance(item, Mapping)
        )
        if len(updates) != len(raw):
            raise TypeError("cognition update item shape")
        return MemoryLoop._normalize_product_cognition_updates(updates)
    except (KeyError, TypeError, ValueError) as error:
        raise MemoryLoopIntegrityError(
            "product bundle cognition update payload is invalid"
        ) from error


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
        "claim_id",
        "entity_ids",
        "relationship_ids",
        "event_ids",
        "cognition_ids",
        "identity_binding_indices",
        "transition_intent_indices",
        "depends_on_claim_ids",
    }
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != required:
            raise MemoryLoopIntegrityError("product bundle claim slices are invalid")
        try:
            slices.append(
                ProductClaimSlice(
                    cast(str, item["claim_id"]),
                    tuple(cast(list[str], item["entity_ids"])),
                    tuple(cast(list[str], item["relationship_ids"])),
                    tuple(cast(list[str], item["event_ids"])),
                    tuple(cast(list[str], item["cognition_ids"])),
                    tuple(cast(list[int], item["identity_binding_indices"])),
                    tuple(cast(list[int], item["transition_intent_indices"])),
                    tuple(cast(list[str], item["depends_on_claim_ids"])),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError(
                "product bundle claim slices are invalid"
            ) from error
    try:
        return MemoryLoop._normalize_product_claim_slices(slices)
    except (TypeError, ValueError) as error:
        raise MemoryLoopIntegrityError(
            "product bundle claim slices are invalid"
        ) from error


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
        raise MemoryLoopIntegrityError(
            "product bundle always-included records are invalid"
        ) from error


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
    return tuple(
        claim_id for claim_id in offered_claim_ids if claim_id in requested_set
    )


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
            item
            for item in delta.formation_traces
            if item.cognition_id in cognition_ids
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
        "evolution_steps": [],
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
        write_payload_data: dict[str, object] = {
            "delta": accepted_payload["delta"],
            "evidence": accepted_payload["evidence"],
            "identity_bindings": accepted_payload["identity_bindings"],
            "transition_intents": accepted_payload["transition_intents"],
        }
        if "evolution_steps" in accepted_payload:
            write_payload_data["evolution_steps"] = accepted_payload["evolution_steps"]
        if "cognition_updates" in accepted_payload:
            write_payload_data["cognition_updates"] = accepted_payload[
                "cognition_updates"
            ]
        write_payload = write_payload_data
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
            raise MemoryLoopIntegrityError(
                "product bundle transition payload is invalid"
            )
        try:
            intent = CognitionTransitionIntent(
                cast(str, item["prior_cognition_id"]),
                cast(str, item["successor_cognition_id"]),
                cast(str, item["reason"]),
                cast(str, item["statement_kind"]),
            )
        except (TypeError, ValueError) as error:
            raise MemoryLoopIntegrityError(
                "product bundle transition payload is invalid"
            ) from error
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                intent.prior_cognition_id,
                intent.successor_cognition_id,
                intent.reason,
                intent.statement_kind,
            )
        ):
            raise MemoryLoopIntegrityError(
                "product bundle transition payload is invalid"
            )
        intents.append(intent)
    try:
        return MemoryLoop._normalize_product_transition_intents(intents)
    except (TypeError, ValueError) as error:
        raise MemoryLoopIntegrityError(
            "product bundle transition payload is invalid"
        ) from error


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
                for field in (
                    "entity_id",
                    "evidence_id",
                    "conversation_id",
                    "occurred_at",
                )
            )
            or type(item["start_codepoint"]) is not int
            or type(item["end_codepoint"]) is not int
            or item["kind_hint"] is not None
            and not isinstance(item["kind_hint"], str)
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
            raise MemoryLoopIntegrityError(
                "identity binding payload is invalid"
            ) from error
        if (
            type(binding.start_codepoint) is not int
            or type(binding.end_codepoint) is not int
            or binding.kind_hint is not None
            and not isinstance(binding.kind_hint, str)
            or binding.continuity_scope is not None
            and not isinstance(binding.continuity_scope, str)
        ):
            raise MemoryLoopIntegrityError("identity binding payload is invalid")
        bindings.append(binding)
    return tuple(bindings)


def _evidence_records_from_payload(
    payload: Mapping[str, object],
) -> tuple[EvidenceRecord, ...]:
    raw_records = payload.get("evidence")
    if not isinstance(raw_records, list) or any(
        not isinstance(item, Mapping) for item in raw_records
    ):
        raise TypeError("evidence payload shape")
    records = tuple(
        _evidence_from_data(cast(Mapping[str, object], item)) for item in raw_records
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


def _correction_prior_ids_from_payload(
    payload: Mapping[str, object],
) -> tuple[str, ...]:
    plural = payload.get("prior_cognition_ids")
    if plural is not None:
        if not isinstance(plural, list) or any(
            not isinstance(item, str) for item in plural
        ):
            raise MemoryLoopIntegrityError("correction prior cognition ids are invalid")
        return tuple(plural)
    singular = payload.get("prior_cognition_id")
    if not isinstance(singular, str):
        raise MemoryLoopIntegrityError("correction prior cognition id is missing")
    return (singular,)
