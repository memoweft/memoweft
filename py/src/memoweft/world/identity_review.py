"""Pure in-memory authority and review boundary for Stage 2 identity edits.

The module deliberately does not depend on ``WorldDelta`` or persistence.  A
candidate identity change is staged against an immutable graph snapshot, then
atomically accepted or rejected by its content-addressed review envelope.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Literal, Mapping, Sequence
import unicodedata
from uuid import uuid4

from .delta import ClaimSpan
from .entity_resolution import (
    AcceptedEntityReference,
    ReferenceMention,
    _base_identity_shape_issues,
)
from .graph import MemoryWorldGraph
from .model import (
    Entity,
    MemoryTarget,
    PersonalWorld,
    Perspective,
    Relationship,
    WorldCognition,
    WorldEvent,
)

IdentityOperation = Literal["bind", "alias", "merge", "split"]
ReviewDecision = Literal["accept", "reject"]
ReviewStatus = Literal["pending", "accepted", "rejected"]
_REFERENCE_SURFACES = frozenset(
    {
        "relationship.source",
        "relationship.target",
        "event.participant",
        "event.related",
        "event.facet_about",
        "cognition.target",
        "cognition.perspective",
    }
)


class IdentityReviewValidationError(ValueError):
    """Fail-closed validation error with stable machine-readable issue codes."""

    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = tuple(sorted(set(issues)))
        super().__init__("Identity review validation failed: " + ", ".join(self.issues))


class IdentityReviewStateError(ValueError):
    """Stable state/integrity errors used by ``decide`` and context projection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class IdentityEvidence:
    """Append-only eligible source material, independent of MemoryLoop Evidence."""

    id: str
    world_id: str
    conversation_id: str
    occurred_at: str
    source_role: str
    content: str
    continuity_scope: str | None = None


@dataclass(frozen=True, slots=True, init=False)
class VerifiedReferenceMention:
    """An authority-issued exact codepoint span; callers cannot construct one."""

    evidence_id: str
    world_id: str
    conversation_id: str
    occurred_at: str
    text: str
    claim_span: ClaimSpan
    atom_hash: str
    continuity_scope: str | None
    kind_hint: str | None
    _issuer_seal: str = field(repr=False, compare=True)

    def __init__(self) -> None:
        raise TypeError("VerifiedReferenceMention is authority-issued")


@dataclass(frozen=True, slots=True)
class EntityReferenceLocator:
    """A typed, stable address for one Entity ID reference in an active graph."""

    surface: Literal[
        "relationship.source",
        "relationship.target",
        "event.participant",
        "event.related",
        "event.facet_about",
        "cognition.target",
        "cognition.perspective",
    ]
    object_id: str
    index: int = 0


@dataclass(frozen=True, slots=True)
class EntityReferenceRewrite:
    locator: EntityReferenceLocator
    expected_entity_id: str
    replacement_entity_id: str


@dataclass(frozen=True, slots=True)
class BindingAssignment:
    """Partition one prior accepted mention during a split.

    ``successor_entity_id=None`` intentionally preserves a historical binding
    while removing it from current resolution context.
    """

    binding_id: str
    successor_entity_id: str | None


@dataclass(frozen=True, slots=True)
class SplitSuccessor:
    entity: Entity
    support_mentions: tuple[VerifiedReferenceMention, ...]


@dataclass(frozen=True, slots=True)
class EntityIdentityDelta:
    """One exact reviewable identity operation.

    Class methods are intentionally the small public construction surface.  The
    generic fields remain visible for serialization/auditing, not mutation.
    """

    world_id: str
    operation: IdentityOperation
    mentions: tuple[VerifiedReferenceMention, ...] = ()
    entity_id: str | None = None
    alias_value: str | None = None
    survivor_entity_id: str | None = None
    absorbed_entity_ids: tuple[str, ...] = ()
    source_entity_id: str | None = None
    successors: tuple[SplitSuccessor, ...] = ()
    rewrites: tuple[EntityReferenceRewrite, ...] = ()
    binding_assignments: tuple[BindingAssignment, ...] = ()

    @classmethod
    def bind(cls, world_id: str, entity_id: str, mention: VerifiedReferenceMention) -> EntityIdentityDelta:
        return cls(world_id, "bind", (mention,), entity_id=entity_id)

    @classmethod
    def alias(
        cls, world_id: str, entity_id: str, alias: str, mentions: tuple[VerifiedReferenceMention, ...]
    ) -> EntityIdentityDelta:
        return cls(world_id, "alias", mentions, entity_id=entity_id, alias_value=alias)

    @classmethod
    def merge(
        cls, world_id: str, survivor_entity_id: str, absorbed_entity_ids: tuple[str, ...], mentions: tuple[VerifiedReferenceMention, ...]
    ) -> EntityIdentityDelta:
        return cls(world_id, "merge", mentions, survivor_entity_id=survivor_entity_id, absorbed_entity_ids=absorbed_entity_ids)

    @classmethod
    def split(
        cls,
        world_id: str,
        source_entity_id: str,
        successors: tuple[SplitSuccessor, ...],
        rewrites: tuple[EntityReferenceRewrite, ...],
        binding_assignments: tuple[BindingAssignment, ...],
    ) -> EntityIdentityDelta:
        return cls(world_id, "split", source_entity_id=source_entity_id, successors=successors, rewrites=rewrites, binding_assignments=binding_assignments)


@dataclass(frozen=True, slots=True)
class IdentityRedirect:
    absorbed_entity_id: str
    survivor_entity_id: str
    review_id: str


@dataclass(frozen=True, slots=True)
class IdentityTombstone:
    """An immutable retired Entity and its direct transition successors."""

    entity_id: str
    successors: tuple[str, ...]
    review_id: str
    retired_entity: Entity


@dataclass(frozen=True, slots=True)
class AcceptedIdentityBinding:
    binding_id: str
    accepted_entity_id: str
    current_entity_id: str | None
    mention: VerifiedReferenceMention
    review_id: str
    result_hash: str
    base_revision: int
    base_graph_hash: str
    accepted_at: str
    result_revision: int = 0
    result_graph_hash: str = ""


@dataclass(frozen=True, slots=True)
class IdentityTransition:
    review_id: str
    operation: IdentityOperation
    from_entity_ids: tuple[str, ...]
    to_entity_ids: tuple[str, ...]
    revision: int
    result_hash: str
    base_graph_hash: str = ""
    result_graph_hash: str = ""
    evidence_atoms: tuple[str, ...] = ()
    rewrite_manifest: tuple[EntityReferenceRewrite, ...] = ()


@dataclass(frozen=True, slots=True)
class IdentityGraphSnapshot:
    world: PersonalWorld
    entities: tuple[Entity, ...]
    relationships: tuple[Relationship, ...]
    events: tuple[WorldEvent, ...]
    cognitions: tuple[WorldCognition, ...]
    graph_hash: str

    def to_graph(self) -> MemoryWorldGraph:
        """Materialize a detached resolver-ready graph from this snapshot."""

        return MemoryWorldGraph(
            world=deepcopy(self.world),
            entities={item.id: deepcopy(item) for item in self.entities},
            relationships={item.id: deepcopy(item) for item in self.relationships},
            events={item.id: deepcopy(item) for item in self.events},
            cognitions={item.id: deepcopy(item) for item in self.cognitions},
        )


@dataclass(frozen=True, slots=True)
class PendingIdentityReview:
    review_id: str
    status: ReviewStatus
    world_id: str
    base_revision: int
    base_graph_hash: str
    delta: EntityIdentityDelta
    evidence_atoms: tuple[str, ...]
    review_payload: Any
    preview: IdentityGraphSnapshot
    preview_bindings: tuple[AcceptedIdentityBinding, ...]
    preview_redirects: tuple[IdentityRedirect, ...]
    preview_tombstones: tuple[IdentityTombstone, ...]
    preview_transition: IdentityTransition
    preview_hash: str
    result_hash: str


@dataclass(frozen=True, slots=True)
class IdentityDecision:
    review_id: str
    status: ReviewStatus
    result_hash: str
    decided_at: str


@dataclass(frozen=True, slots=True)
class IdentityAuthorityView:
    world_id: str
    revision: int
    graph: IdentityGraphSnapshot
    evidence: tuple[IdentityEvidence, ...]
    pending: tuple[PendingIdentityReview, ...]
    review_envelopes: tuple[PendingIdentityReview, ...]
    decisions: tuple[IdentityDecision, ...]
    bindings: tuple[AcceptedIdentityBinding, ...]
    transitions: tuple[IdentityTransition, ...]
    redirects: tuple[IdentityRedirect, ...]
    tombstones: tuple[IdentityTombstone, ...]


@dataclass(frozen=True, slots=True)
class IdentityAuthorityState:
    """Detached, storage-neutral checkpoint of the complete authority state."""

    authority_id: str
    graph: IdentityGraphSnapshot
    revision: int
    evidence: tuple[IdentityEvidence, ...]
    mentions: tuple[VerifiedReferenceMention, ...]
    pending: tuple[PendingIdentityReview, ...]
    review_envelopes: tuple[PendingIdentityReview, ...]
    decisions: tuple[IdentityDecision, ...]
    bindings: tuple[AcceptedIdentityBinding, ...]
    transitions: tuple[IdentityTransition, ...]
    redirects: tuple[IdentityRedirect, ...]
    tombstones: tuple[IdentityTombstone, ...]


@dataclass(frozen=True, slots=True, init=False)
class IdentityResolutionContext:
    """Authority-sealed resolver input for exactly one current mention."""

    current_mention: VerifiedReferenceMention
    world_id: str
    revision: int
    graph_hash: str
    bindings: tuple[AcceptedIdentityBinding, ...]
    seal: str
    _issuer: IdentityAuthority | None = field(repr=False, compare=False, default=None)

    def __init__(self) -> None:
        raise TypeError("IdentityResolutionContext is authority-issued")

    def verify(self, authority: IdentityAuthority) -> bool:
        return isinstance(authority, IdentityAuthority) and authority._verify_context(
            self
        )

    def resolver_inputs(
        self,
        base: MemoryWorldGraph | None = None,
    ) -> IdentityResolverInputs:
        """Validate issuer, registry and exact base, then project trusted DTOs.

        This is the resolver-facing entry point: callers cannot turn a pending,
        rejected, stale, or hand-built context into trusted history.
        """
        issuer = getattr(self, "_issuer", None)
        if not isinstance(issuer, IdentityAuthority):
            raise IdentityReviewStateError("CONTEXT_SEAL_INVALID")
        return issuer._resolver_inputs(self, base)

    def to_accepted_entity_references(self) -> tuple[AcceptedEntityReference, ...]:
        issuer = getattr(self, "_issuer", None)
        if not isinstance(issuer, IdentityAuthority):
            raise IdentityReviewStateError("CONTEXT_SEAL_INVALID")
        return issuer._accepted_references(self)


@dataclass(frozen=True, slots=True)
class IdentityResolverInputs:
    """One fail-closed resolver call's current mention and trusted history."""

    base: MemoryWorldGraph
    current_mention: ReferenceMention
    accepted_history: tuple[AcceptedEntityReference, ...]


class IdentityAuthority:
    """Thread-safe copy-on-write identity authority with no storage dependency."""

    def __init__(self, graph: MemoryWorldGraph) -> None:
        issues = _graph_issues(graph)
        if issues:
            raise IdentityReviewValidationError(issues)
        self._lock = RLock()
        self._authority_id = uuid4().hex
        self._graph = _clone_graph(graph)
        self._revision = 0
        self._evidence: dict[str, IdentityEvidence] = {}
        self._mentions: dict[str, VerifiedReferenceMention] = {}
        self._pending: dict[str, PendingIdentityReview] = {}
        self._review_envelopes: dict[str, PendingIdentityReview] = {}
        self._decisions: dict[str, IdentityDecision] = {}
        self._bindings: tuple[AcceptedIdentityBinding, ...] = ()
        self._transitions: tuple[IdentityTransition, ...] = ()
        self._redirects: tuple[IdentityRedirect, ...] = ()
        self._tombstones: tuple[IdentityTombstone, ...] = ()

    def checkpoint(self) -> IdentityAuthorityState:
        """Return one deeply detached snapshot of every durable authority field."""

        with self._lock:
            return self._checkpoint_with_graph_unlocked(self._graph)

    def checkpoint_with_graph(
        self,
        graph: MemoryWorldGraph,
    ) -> IdentityAuthorityState:
        """Checkpoint the identity ledger against a replacement canonical graph.

        This is the storage integration seam: identity revision and ledgers stay
        unchanged, while pending reviews retain their original base hashes and
        therefore become stale naturally when the replacement graph differs.
        """

        with self._lock:
            issues = _graph_issues(graph)
            if issues:
                raise IdentityReviewValidationError(issues)
            if graph.world.world_id != self._graph.world.world_id:
                raise IdentityReviewValidationError(("state.graph.world_id.mismatch",))
            state = self._checkpoint_with_graph_unlocked(graph)
            type(self).restore(state)
            return state

    @classmethod
    def restore(cls, state: IdentityAuthorityState) -> IdentityAuthority:
        """Validate and hydrate a checkpoint without changing its issuer identity."""

        if not isinstance(state, IdentityAuthorityState):
            raise IdentityReviewValidationError(("state.type.invalid",))
        try:
            detached = deepcopy(state)
            shape_issues = _authority_state_shape_issues(detached)
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as error:
            raise IdentityReviewValidationError(("state.shape.invalid",)) from error
        if shape_issues:
            raise IdentityReviewValidationError(shape_issues)
        try:
            graph = detached.graph.to_graph()
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise IdentityReviewValidationError(("state.graph.invalid",)) from error
        authority = cls(graph)
        authority._authority_id = detached.authority_id
        authority._revision = detached.revision
        authority._evidence = {item.id: deepcopy(item) for item in detached.evidence}
        authority._mentions = {
            item.atom_hash: deepcopy(item) for item in detached.mentions
        }
        authority._pending = {
            item.review_id: deepcopy(item) for item in detached.pending
        }
        authority._review_envelopes = {
            item.review_id: deepcopy(item) for item in detached.review_envelopes
        }
        authority._decisions = {
            item.review_id: deepcopy(item) for item in detached.decisions
        }
        authority._bindings = deepcopy(detached.bindings)
        authority._transitions = deepcopy(detached.transitions)
        authority._redirects = deepcopy(detached.redirects)
        authority._tombstones = deepcopy(detached.tombstones)
        try:
            integrity_issues = authority._restored_state_issues()
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as error:
            raise IdentityReviewValidationError(("state.integrity.invalid",)) from error
        if integrity_issues:
            raise IdentityReviewValidationError(integrity_issues)
        return authority

    def _checkpoint_with_graph_unlocked(
        self,
        graph: MemoryWorldGraph,
    ) -> IdentityAuthorityState:
        return IdentityAuthorityState(
            authority_id=self._authority_id,
            graph=_snapshot(graph),
            revision=self._revision,
            evidence=deepcopy(tuple(self._evidence.values())),
            mentions=deepcopy(tuple(self._mentions.values())),
            pending=deepcopy(tuple(self._pending.values())),
            review_envelopes=deepcopy(tuple(self._review_envelopes.values())),
            decisions=deepcopy(tuple(self._decisions.values())),
            bindings=deepcopy(self._bindings),
            transitions=deepcopy(self._transitions),
            redirects=deepcopy(self._redirects),
            tombstones=deepcopy(self._tombstones),
        )

    def register_evidence(self, evidence: IdentityEvidence) -> IdentityEvidence:
        with self._lock:
            issues = _evidence_issues(evidence, self._graph.world.world_id)
            prior = (
                self._evidence.get(evidence.id)
                if isinstance(evidence, IdentityEvidence) and _nonempty(evidence.id)
                else None
            )
            if prior is not None:
                if prior != evidence:
                    raise IdentityReviewValidationError(("evidence.id.conflict",))
                return deepcopy(prior)
            if issues:
                raise IdentityReviewValidationError(issues)
            internal = deepcopy(evidence)
            self._evidence[evidence.id] = internal
            return deepcopy(internal)

    append_evidence = register_evidence

    def issue_verified_mention(
        self,
        evidence_id: str,
        start_codepoint: int,
        end_codepoint: int,
        *,
        kind_hint: str | None = None,
        continuity_scope: str | None = None,
    ) -> VerifiedReferenceMention:
        with self._lock:
            if not _nonempty(evidence_id):
                raise IdentityReviewValidationError(("mention.evidence_id.invalid",))
            if kind_hint is not None and not _nonempty(kind_hint):
                raise IdentityReviewValidationError(("mention.kind_hint.invalid",))
            evidence = self._evidence.get(evidence_id)
            if evidence is None:
                raise IdentityReviewValidationError(("mention.evidence.unknown",))
            if type(start_codepoint) is not int or type(end_codepoint) is not int or start_codepoint < 0 or end_codepoint <= start_codepoint or end_codepoint > len(evidence.content):
                raise IdentityReviewValidationError(("mention.span.invalid",))
            if continuity_scope is not None and continuity_scope != evidence.continuity_scope:
                raise IdentityReviewValidationError(("mention.continuity_scope.mismatch",))
            continuity_scope = evidence.continuity_scope
            text = evidence.content[start_codepoint:end_codepoint]
            source_hash = _sha(evidence.content)
            claim_hash = _sha(text)
            span = ClaimSpan(start_codepoint, end_codepoint, source_hash, claim_hash)
            atom = _mention_atom_hash(
                evidence=evidence,
                text=text,
                claim_span=span,
                continuity_scope=continuity_scope,
                kind_hint=kind_hint,
            )
            prior = self._mentions.get(atom)
            if prior is not None:
                return deepcopy(prior)
            mention = object.__new__(VerifiedReferenceMention)
            object.__setattr__(mention, "evidence_id", evidence.id)
            object.__setattr__(mention, "world_id", evidence.world_id)
            object.__setattr__(mention, "conversation_id", evidence.conversation_id)
            object.__setattr__(mention, "occurred_at", evidence.occurred_at)
            object.__setattr__(mention, "text", text)
            object.__setattr__(mention, "claim_span", span)
            object.__setattr__(mention, "atom_hash", atom)
            object.__setattr__(mention, "continuity_scope", continuity_scope)
            object.__setattr__(mention, "kind_hint", kind_hint)
            object.__setattr__(
                mention,
                "_issuer_seal",
                _hash_value(
                    {
                        "v": "identity-mention-seal-v1",
                        "authority": self._authority_id,
                        "atom_hash": atom,
                    }
                ),
            )
            self._mentions[atom] = deepcopy(mention)
            return deepcopy(mention)

    def stage(self, delta: EntityIdentityDelta, review_payload: Any) -> PendingIdentityReview:
        with self._lock:
            base = _clone_graph(self._graph)
            base_hash = _graph_hash(base)
            issues = self._authority_delta_issues(delta, base)
            if issues:
                raise IdentityReviewValidationError(issues)
            review_id = uuid4().hex
            preview, new_bindings, redirects, tombstones, transition = self._preview(
                delta,
                base,
                review_id,
            )
            preview_issues = _graph_issues(preview)
            if preview_issues:
                raise IdentityReviewValidationError(preview_issues)
            review_value = _json_value(review_payload)
            snapshot = _snapshot(preview)
            atoms = tuple(sorted(_delta_atoms(delta)))
            pending = PendingIdentityReview(
                review_id=review_id,
                status="pending",
                world_id=base.world.world_id,
                base_revision=self._revision,
                base_graph_hash=base_hash,
                delta=delta,
                evidence_atoms=atoms,
                review_payload=review_value,
                preview=snapshot,
                preview_bindings=new_bindings,
                preview_redirects=redirects,
                preview_tombstones=tombstones,
                preview_transition=transition,
                preview_hash="",
                result_hash="",
            )
            pending = replace(
                pending,
                preview_hash=_pending_preview_hash(pending),
            )
            pending = replace(pending, result_hash=self._pending_hash(pending))
            self._pending[review_id] = deepcopy(pending)
            return deepcopy(pending)

    def decide(self, review_id: str, result_hash: str, decision: ReviewDecision, decided_at: str) -> IdentityDecision:
        with self._lock:
            if not _nonempty(review_id):
                raise IdentityReviewValidationError(("review_id.invalid",))
            pending = self._pending.get(review_id)
            if pending is None:
                if review_id in self._decisions:
                    raise IdentityReviewStateError("REVIEW_ALREADY_DECIDED")
                raise IdentityReviewStateError("REVIEW_UNKNOWN")
            if not isinstance(pending, PendingIdentityReview):
                raise IdentityReviewStateError("RESULT_HASH_MISMATCH")
            if decision not in ("accept", "reject"):
                raise IdentityReviewValidationError(("decision.invalid",))
            decision_time = _timestamp(decided_at)
            if decision_time is None:
                raise IdentityReviewValidationError(("decided_at.invalid",))
            if pending.status != "pending":
                raise IdentityReviewStateError("REVIEW_STATUS_INVALID")
            try:
                preview_hash_matches = (
                    pending.preview_hash == _pending_preview_hash(pending)
                )
                result_hash_matches = pending.result_hash == self._pending_hash(pending)
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise IdentityReviewStateError("RESULT_HASH_MISMATCH") from error
            if (
                result_hash != pending.result_hash
                or not preview_hash_matches
                or not result_hash_matches
            ):
                raise IdentityReviewStateError("RESULT_HASH_MISMATCH")
            evidence_times = tuple(
                parsed
                for mention in _delta_verified_mentions(pending.delta)
                for parsed in (_timestamp(mention.occurred_at),)
                if parsed is not None
            )
            if any(decision_time < evidence_time for evidence_time in evidence_times):
                raise IdentityReviewValidationError(("decided_at.before_evidence",))
            prior_decision_times = tuple(
                parsed
                for item in self._decisions.values()
                for parsed in (_timestamp(item.decided_at),)
                if parsed is not None
            )
            if prior_decision_times and decision_time < max(prior_decision_times):
                raise IdentityReviewValidationError(("decided_at.before_ledger",))
            if decision == "reject":
                outcome = IdentityDecision(review_id, "rejected", result_hash, decided_at)
                next_pending = dict(self._pending)
                del next_pending[review_id]
                next_decisions = dict(self._decisions)
                next_decisions[review_id] = deepcopy(outcome)
                next_review_envelopes = dict(self._review_envelopes)
                next_review_envelopes[review_id] = deepcopy(pending)
                self._pending, self._decisions, self._review_envelopes = (
                    next_pending,
                    next_decisions,
                    next_review_envelopes,
                )
                return deepcopy(outcome)
            current_hash = _graph_hash(self._graph)
            if pending.base_revision != self._revision or pending.base_graph_hash != current_hash:
                raise IdentityReviewStateError("STALE_BASE")
            issues = self._authority_delta_issues(pending.delta, self._graph)
            if issues:
                raise IdentityReviewValidationError(issues)
            preview, bindings, redirects, tombstones, transition = self._preview(
                pending.delta,
                self._graph,
                review_id,
            )
            replay_snapshot = _snapshot(preview)
            replay_preview = replace(
                pending,
                preview=replay_snapshot,
                preview_bindings=bindings,
                preview_redirects=redirects,
                preview_tombstones=tombstones,
                preview_transition=transition,
                preview_hash="",
                result_hash="",
            )
            replay_preview_hash = _pending_preview_hash(replay_preview)
            if (
                replay_snapshot != pending.preview
                or bindings != pending.preview_bindings
                or redirects != pending.preview_redirects
                or tombstones != pending.preview_tombstones
                or transition != pending.preview_transition
                or replay_preview_hash != pending.preview_hash
            ):
                raise IdentityReviewStateError("PREVIEW_HASH_MISMATCH")
            graph_issues = _graph_issues(preview)
            if graph_issues:
                raise IdentityReviewValidationError(graph_issues)
            # Compute every post-decision value before one attribute tuple swap.
            # ``view`` is also lock-protected, so no partial canonical state is
            # observable even if an exception is injected before this point.
            next_revision = self._revision + 1
            result_graph_hash = _graph_hash(preview)
            next_bindings = tuple(
                replace(item, result_hash=result_hash, accepted_at=decided_at, result_revision=next_revision, result_graph_hash=result_graph_hash)
                if item.review_id == review_id
                else item
                for item in bindings
            )
            next_transitions = self._transitions + (
                replace(
                    transition,
                    revision=next_revision,
                    result_hash=result_hash,
                    base_graph_hash=pending.base_graph_hash,
                    result_graph_hash=result_graph_hash,
                    evidence_atoms=pending.evidence_atoms,
                ),
            )
            outcome = IdentityDecision(review_id, "accepted", result_hash, decided_at)
            next_pending = dict(self._pending)
            del next_pending[review_id]
            next_decisions = dict(self._decisions)
            next_decisions[review_id] = outcome
            next_review_envelopes = dict(self._review_envelopes)
            next_review_envelopes[review_id] = deepcopy(pending)
            self._graph, self._revision, self._bindings, self._redirects, self._tombstones, self._transitions, self._pending, self._decisions, self._review_envelopes = (
                preview, next_revision, next_bindings, redirects, tombstones, next_transitions, next_pending, next_decisions, next_review_envelopes,
            )
            return deepcopy(outcome)

    def view(self) -> IdentityAuthorityView:
        with self._lock:
            return IdentityAuthorityView(
                self._graph.world.world_id,
                self._revision,
                _snapshot(self._graph),
                deepcopy(tuple(sorted(self._evidence.values(), key=lambda item: item.id))),
                deepcopy(tuple(sorted(self._pending.values(), key=lambda item: item.review_id))),
                deepcopy(tuple(sorted(self._review_envelopes.values(), key=lambda item: item.review_id))),
                deepcopy(tuple(sorted(self._decisions.values(), key=lambda item: item.review_id))),
                deepcopy(self._bindings),
                deepcopy(self._transitions),
                deepcopy(self._redirects),
                deepcopy(self._tombstones),
            )

    def resolution_context(self, current_mention: VerifiedReferenceMention) -> IdentityResolutionContext:
        with self._lock:
            if not self._mention_matches_registry(current_mention):
                raise IdentityReviewValidationError(("context.current_mention.unverified",))
            current_time = _timestamp(current_mention.occurred_at)
            accepted_decision_times = tuple(
                parsed
                for item in self._decisions.values()
                if item.status == "accepted"
                for parsed in (_timestamp(item.decided_at),)
                if parsed is not None
            )
            if (
                current_time is None
                or accepted_decision_times
                and current_time <= max(accepted_decision_times)
            ):
                raise IdentityReviewValidationError(
                    ("context.current_mention.before_authority_revision",)
                )
            bindings = deepcopy(
                tuple(
                    sorted(
                        (
                            binding
                            for binding in self._bindings
                            if self._binding_visible(binding, current_mention)
                        ),
                        key=lambda item: (
                            item.current_entity_id or "",
                            item.mention.atom_hash,
                            item.binding_id,
                        ),
                    )
                )
            )
            graph_hash = _graph_hash(self._graph)
            seal = self._context_seal(current_mention, self._revision, graph_hash, bindings)
            context = object.__new__(IdentityResolutionContext)
            object.__setattr__(context, "current_mention", deepcopy(current_mention))
            object.__setattr__(context, "world_id", self._graph.world.world_id)
            object.__setattr__(context, "revision", self._revision)
            object.__setattr__(context, "graph_hash", graph_hash)
            object.__setattr__(context, "bindings", bindings)
            object.__setattr__(context, "seal", seal)
            object.__setattr__(context, "_issuer", self)
            return context

    def _preview(self, delta: EntityIdentityDelta, base: MemoryWorldGraph, review_id: str) -> tuple[MemoryWorldGraph, tuple[AcceptedIdentityBinding, ...], tuple[IdentityRedirect, ...], tuple[IdentityTombstone, ...], IdentityTransition]:
        graph = _clone_graph(base)
        bindings = self._bindings
        redirects = self._redirects
        tombstones = self._tombstones
        def new_binding(entity_id: str, mention: VerifiedReferenceMention) -> AcceptedIdentityBinding:
            return AcceptedIdentityBinding(
                _hash_value({"v": "identity-binding-v1", "review": review_id, "entity": entity_id, "atom": mention.atom_hash}),
                entity_id,
                entity_id,
                mention,
                review_id,
                "",
                self._revision,
                _graph_hash(base),
                "",
            )

        def append_binding(
            current: tuple[AcceptedIdentityBinding, ...],
            entity_id: str,
            mention: VerifiedReferenceMention,
        ) -> tuple[AcceptedIdentityBinding, ...]:
            active_targets = {
                item.current_entity_id
                for item in current
                if item.current_entity_id is not None
                and item.mention.atom_hash == mention.atom_hash
            }
            if active_targets:
                if active_targets == {entity_id}:
                    return current
                raise IdentityReviewValidationError(("binding.atom.target_conflict",))
            return current + (new_binding(entity_id, mention),)

        if delta.operation == "bind":
            mention = delta.mentions[0]
            bindings = append_binding(bindings, delta.entity_id or "", mention)
            return graph, bindings, redirects, tombstones, IdentityTransition(review_id, "bind", (), (delta.entity_id or "",), 0, "")
        if delta.operation == "alias":
            entity_id = delta.entity_id or ""
            entity = graph.entities[entity_id]
            assert delta.alias_value is not None
            graph.entities[entity_id] = replace(entity, aliases=entity.aliases + (delta.alias_value,))
            for mention in delta.mentions:
                bindings = append_binding(bindings, entity_id, mention)
            return graph, bindings, redirects, tombstones, IdentityTransition(review_id, "alias", (entity_id,), (entity_id,), 0, "")
        if delta.operation == "merge":
            survivor = delta.survivor_entity_id or ""
            absorbed = delta.absorbed_entity_ids
            mapping = {entity_id: survivor for entity_id in absorbed}
            rewrite_manifest = tuple(
                EntityReferenceRewrite(locator, source_entity_id, survivor)
                for source_entity_id in sorted(mapping)
                for locator in _entity_locators(graph, source_entity_id)
            )
            graph = _rewrite_graph_at_locators(
                graph,
                {
                    rewrite.locator: rewrite.replacement_entity_id
                    for rewrite in rewrite_manifest
                },
            )
            for entity_id in absorbed:
                del graph.entities[entity_id]
            bindings = tuple(replace(item, current_entity_id=mapping.get(item.current_entity_id, item.current_entity_id) if item.current_entity_id is not None else None) for item in bindings)
            for mention in delta.mentions:
                bindings = append_binding(bindings, survivor, mention)
            redirects = tuple(replace(item, survivor_entity_id=survivor) if item.survivor_entity_id in mapping else item for item in redirects)
            redirects = redirects + tuple(
                IdentityRedirect(entity_id, survivor, review_id)
                for entity_id in absorbed
            )
            tombstones = tombstones + tuple(IdentityTombstone(entity_id, (survivor,), review_id, base.entities[entity_id]) for entity_id in absorbed)
            return graph, bindings, redirects, tombstones, IdentityTransition(
                review_id,
                "merge",
                absorbed,
                (survivor,),
                0,
                "",
                rewrite_manifest=rewrite_manifest,
            )
        source = delta.source_entity_id or ""
        rewrite_mapping: dict[EntityReferenceLocator, str] = {rewrite.locator: rewrite.replacement_entity_id for rewrite in delta.rewrites}
        graph = _rewrite_graph_at_locators(graph, rewrite_mapping)
        del graph.entities[source]
        for successor in delta.successors:
            graph.entities[successor.entity.id] = successor.entity
        assignment = {item.binding_id: item.successor_entity_id for item in delta.binding_assignments}
        bindings = tuple(replace(item, current_entity_id=assignment[item.binding_id]) if item.current_entity_id == source else item for item in bindings)
        for successor in delta.successors:
            for mention in successor.support_mentions:
                bindings = append_binding(bindings, successor.entity.id, mention)
        successor_ids = tuple(item.entity.id for item in delta.successors)
        tombstones = tombstones + (IdentityTombstone(source, successor_ids, review_id, base.entities[source]),)
        return graph, bindings, redirects, tombstones, IdentityTransition(
            review_id,
            "split",
            (source,),
            successor_ids,
            0,
            "",
            rewrite_manifest=delta.rewrites,
        )

    def _pending_hash(self, pending: PendingIdentityReview) -> str:
        return _hash_value(
            {
                "v": "identity-review-v1",
                "review_id": pending.review_id,
                "status": pending.status,
                "world_id": pending.world_id,
                "base_revision": pending.base_revision,
                "base_graph_hash": pending.base_graph_hash,
                "delta": _value(pending.delta),
                "evidence_atoms": pending.evidence_atoms,
                "review_payload": pending.review_payload,
                "preview": _pending_preview_value(pending),
                "preview_hash": pending.preview_hash,
            }
        )

    def _mention_matches_registry(self, mention: object) -> bool:
        if not isinstance(mention, VerifiedReferenceMention):
            return False
        if not _nonempty(mention.atom_hash):
            return False
        registered = self._mentions.get(mention.atom_hash)
        if registered is None or registered != mention:
            return False
        evidence = self._evidence.get(mention.evidence_id)
        if evidence is None or evidence.source_role != "user":
            return False
        if (
            mention.world_id != evidence.world_id
            or mention.conversation_id != evidence.conversation_id
            or mention.occurred_at != evidence.occurred_at
            or mention.continuity_scope != evidence.continuity_scope
            or not isinstance(mention.claim_span, ClaimSpan)
            or isinstance(mention.claim_span.start_codepoint, bool)
            or not isinstance(mention.claim_span.start_codepoint, int)
            or isinstance(mention.claim_span.end_codepoint, bool)
            or not isinstance(mention.claim_span.end_codepoint, int)
            or mention.claim_span.start_codepoint < 0
            or mention.claim_span.end_codepoint <= mention.claim_span.start_codepoint
            or mention.claim_span.end_codepoint > len(evidence.content)
            or mention.text
            != evidence.content[
                mention.claim_span.start_codepoint : mention.claim_span.end_codepoint
            ]
        ):
            return False
        expected_atom = _mention_atom_hash(
            evidence=evidence,
            text=mention.text,
            claim_span=mention.claim_span,
            continuity_scope=mention.continuity_scope,
            kind_hint=mention.kind_hint,
        )
        expected_seal = _hash_value(
            {
                "v": "identity-mention-seal-v1",
                "authority": self._authority_id,
                "atom_hash": expected_atom,
            }
        )
        return (
            mention.claim_span.source_content_sha256 == _sha(evidence.content)
            and mention.claim_span.claim_sha256 == _sha(mention.text)
            and mention.atom_hash == expected_atom
            and mention._issuer_seal == expected_seal
        )

    def _authority_delta_issues(self, delta: object, base: MemoryWorldGraph) -> tuple[str, ...]:
        """Add authority-only identity and historic-binding checks to shape checks."""
        issues = list(_delta_issues(delta, base, self._mentions))
        if not isinstance(delta, EntityIdentityDelta):
            return tuple(issues)
        mention_values = delta.mentions if isinstance(delta.mentions, tuple) else ()
        for index, mention in enumerate(mention_values):
            if not self._mention_matches_registry(mention):
                issues.append(f"delta.mentions[{index}].unverified")
        successors = delta.successors if isinstance(delta.successors, tuple) else ()
        for successor_index, successor in enumerate(successors):
            if isinstance(successor, SplitSuccessor):
                if (
                    isinstance(successor.entity, Entity)
                    and _nonempty(successor.entity.id)
                    and successor.entity.id
                    in {
                        *(item.absorbed_entity_id for item in self._redirects),
                        *(item.entity_id for item in self._tombstones),
                    }
                ):
                    issues.append(
                        f"split.successors[{successor_index}].id.retired"
                    )
                support_mentions = (
                    successor.support_mentions
                    if isinstance(successor.support_mentions, tuple)
                    else ()
                )
                for support_index, mention in enumerate(support_mentions):
                    if not self._mention_matches_registry(mention):
                        issues.append(f"split.successors[{successor_index}].support[{support_index}].unverified")
        if delta.operation == "split" and _nonempty(delta.source_entity_id):
            if any(redirect.survivor_entity_id == delta.source_entity_id for redirect in self._redirects):
                issues.append("split.source.redirect_target")
            expected = {
                item.binding_id
                for item in self._bindings
                if item.current_entity_id == delta.source_entity_id
            }
            binding_assignments = (
                delta.binding_assignments
                if isinstance(delta.binding_assignments, tuple)
                else ()
            )
            valid_assignments: list[BindingAssignment] = []
            for index, assignment in enumerate(binding_assignments):
                if not isinstance(assignment, BindingAssignment):
                    issues.append(f"split.binding_assignments[{index}].type.invalid")
                    continue
                if not _nonempty(assignment.binding_id):
                    issues.append(f"split.binding_assignments[{index}].binding_id.invalid")
                    continue
                if assignment.successor_entity_id is not None and not _nonempty(
                    assignment.successor_entity_id
                ):
                    issues.append(
                        f"split.binding_assignments[{index}].successor_entity_id.invalid"
                    )
                    continue
                valid_assignments.append(assignment)
            assignments = [item.binding_id for item in valid_assignments]
            if len(set(assignments)) != len(assignments):
                issues.append("split.binding_assignments.duplicate")
            if set(assignments) != expected:
                issues.append("split.binding_assignments.partition.incomplete")
            successor_ids = {
                item.entity.id
                for item in successors
                if isinstance(item, SplitSuccessor)
                and isinstance(item.entity, Entity)
                and _nonempty(item.entity.id)
            }
            for assignment in valid_assignments:
                if assignment.successor_entity_id is not None and assignment.successor_entity_id not in successor_ids:
                    issues.append("split.binding_assignments.successor.unknown")
        issues.extend(self._projected_atom_target_issues(delta))
        return tuple(sorted(set(issues)))

    def _projected_atom_target_issues(
        self,
        delta: EntityIdentityDelta,
    ) -> tuple[str, ...]:
        """Require each verified Evidence atom to have at most one active target."""

        targets: dict[str, set[str]] = {}
        assignments = {
            item.binding_id: item.successor_entity_id
            for item in delta.binding_assignments
            if isinstance(item, BindingAssignment)
            and _nonempty(item.binding_id)
            and (
                item.successor_entity_id is None
                or _nonempty(item.successor_entity_id)
            )
        } if isinstance(delta.binding_assignments, tuple) else {}
        absorbed = {
            item
            for item in delta.absorbed_entity_ids
            if _nonempty(item)
        } if isinstance(delta.absorbed_entity_ids, tuple) else set()

        for binding in self._bindings:
            target = binding.current_entity_id
            if target is None:
                continue
            if delta.operation == "merge" and target in absorbed:
                target = delta.survivor_entity_id
            elif delta.operation == "split" and target == delta.source_entity_id:
                target = assignments.get(binding.binding_id)
            if isinstance(target, str) and target.strip():
                targets.setdefault(binding.mention.atom_hash, set()).add(target)

        additions: list[tuple[VerifiedReferenceMention, str]] = []
        if delta.operation in ("bind", "alias") and isinstance(
            delta.entity_id, str
        ) and delta.entity_id.strip():
            additions.extend(
                (mention, delta.entity_id)
                for mention in delta.mentions
                if isinstance(mention, VerifiedReferenceMention)
            )
        elif delta.operation == "merge" and isinstance(
            delta.survivor_entity_id, str
        ) and delta.survivor_entity_id.strip():
            additions.extend(
                (mention, delta.survivor_entity_id)
                for mention in delta.mentions
                if isinstance(mention, VerifiedReferenceMention)
            )
        elif delta.operation == "split":
            for successor in delta.successors:
                if not isinstance(successor, SplitSuccessor) or not isinstance(
                    successor.entity, Entity
                ):
                    continue
                support_mentions = (
                    successor.support_mentions
                    if isinstance(successor.support_mentions, tuple)
                    else ()
                )
                additions.extend(
                    (mention, successor.entity.id)
                    for mention in support_mentions
                    if isinstance(mention, VerifiedReferenceMention)
                    and _nonempty(successor.entity.id)
                )
        for mention, target in additions:
            if _nonempty(mention.atom_hash):
                targets.setdefault(mention.atom_hash, set()).add(target)
        if any(len(values) > 1 for values in targets.values()):
            return ("binding.atom.target_conflict",)
        return ()

    def _context_seal(self, mention: VerifiedReferenceMention, revision: int, graph_hash: str, bindings: tuple[AcceptedIdentityBinding, ...]) -> str:
        return _hash_value({"v": "identity-context-v1", "authority": self._authority_id, "mention": mention.atom_hash, "world": self._graph.world.world_id, "revision": revision, "graph_hash": graph_hash, "bindings": _value(bindings)})

    def _verify_context(self, context: IdentityResolutionContext) -> bool:
        with self._lock:
            return self._validated_context_snapshot(context) is not None

    def _validated_context_snapshot(
        self,
        context: object,
    ) -> tuple[
        VerifiedReferenceMention,
        tuple[AcceptedIdentityBinding, ...],
        str,
    ] | None:
        if not isinstance(context, IdentityResolutionContext) or getattr(
            context, "_issuer", None
        ) is not self:
            return None
        try:
            mention = deepcopy(context.current_mention)
            world_id = context.world_id
            revision = context.revision
            graph_hash = context.graph_hash
            bindings = deepcopy(context.bindings)
            seal = context.seal
        except AttributeError:
            return None
        current_graph_hash = _graph_hash(self._graph)
        if (
            world_id != self._graph.world.world_id
            or revision != self._revision
            or graph_hash != current_graph_hash
            or not self._mention_matches_registry(mention)
        ):
            return None
        expected = tuple(
            sorted(
                (
                    binding
                    for binding in self._bindings
                    if self._binding_visible(binding, mention)
                ),
                key=lambda item: (
                    item.current_entity_id or "",
                    item.mention.atom_hash,
                    item.binding_id,
                ),
            )
        )
        if bindings != expected or seal != self._context_seal(
            mention,
            revision,
            graph_hash,
            expected,
        ):
            return None
        return mention, deepcopy(expected), current_graph_hash

    def _project_bindings(self, bindings: tuple[AcceptedIdentityBinding, ...]) -> tuple[AcceptedEntityReference, ...]:
        return tuple(AcceptedEntityReference(item.current_entity_id or "", item.mention.text, item.mention.evidence_id, item.mention.conversation_id, item.mention.occurred_at, "user", continuity_id=item.mention.continuity_scope) for item in bindings if item.current_entity_id is not None)

    def _resolver_inputs(
        self,
        context: IdentityResolutionContext,
        base: MemoryWorldGraph | None,
    ) -> IdentityResolverInputs:
        with self._lock:
            snapshot = self._validated_context_snapshot(context)
            if snapshot is None:
                raise IdentityReviewStateError("CONTEXT_SEAL_INVALID")
            mention, bindings, graph_hash = snapshot
            if base is not None:
                issues = _graph_issues(base)
                if issues:
                    raise IdentityReviewValidationError(issues)
                if _graph_hash(base) != graph_hash:
                    raise IdentityReviewStateError("CONTEXT_BASE_MISMATCH")
            current = ReferenceMention(
                mention.text,
                mention.evidence_id,
                mention.conversation_id,
                mention.occurred_at,
                "user",
                kind_hint=mention.kind_hint,
                continuity_id=mention.continuity_scope,
            )
            return IdentityResolverInputs(
                _clone_graph(self._graph),
                current,
                self._project_bindings(bindings),
            )

    def _accepted_references(
        self,
        context: IdentityResolutionContext,
    ) -> tuple[AcceptedEntityReference, ...]:
        with self._lock:
            snapshot = self._validated_context_snapshot(context)
            if snapshot is None:
                raise IdentityReviewStateError("CONTEXT_SEAL_INVALID")
            _, bindings, _ = snapshot
            return self._project_bindings(bindings)

    @staticmethod
    def _binding_visible(binding: AcceptedIdentityBinding, current: VerifiedReferenceMention) -> bool:
        binding_time = _timestamp(binding.mention.occurred_at)
        accepted_time = _timestamp(binding.accepted_at)
        current_time = _timestamp(current.occurred_at)
        if (
            binding.current_entity_id is None
            or binding_time is None
            or accepted_time is None
            or current_time is None
            or binding_time >= current_time
            or accepted_time >= current_time
        ):
            return False
        return binding.mention.conversation_id == current.conversation_id or (
            binding.mention.continuity_scope is not None
            and binding.mention.continuity_scope == current.continuity_scope
        )

    def _restored_state_issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        world_id = self._graph.world.world_id
        current_graph_hash = _graph_hash(self._graph)

        for atom_hash, mention in self._mentions.items():
            if atom_hash != mention.atom_hash or not self._mention_matches_registry(
                mention
            ):
                issues.append("state.mentions.registry.invalid")

        all_reviews = (*self._pending.values(), *self._review_envelopes.values())
        for review in all_reviews:
            prefix = f"state.review[{review.review_id}]"
            issues.extend(
                _review_checkpoint_issues(
                    review,
                    prefix,
                    world_id,
                    self._mentions,
                    self._pending_hash,
                )
            )

        pending_ids = set(self._pending)
        decision_ids = set(self._decisions)
        envelope_ids = set(self._review_envelopes)
        if pending_ids & decision_ids or pending_ids & envelope_ids:
            issues.append("state.reviews.lanes.overlap")
        if decision_ids != envelope_ids:
            issues.append("state.decisions.envelopes.mismatch")

        accepted_decisions: dict[str, IdentityDecision] = {}
        for review_id, decision in self._decisions.items():
            envelope = self._review_envelopes.get(review_id)
            if (
                decision.review_id != review_id
                or decision.status not in ("accepted", "rejected")
                or _timestamp(decision.decided_at) is None
                or envelope is None
                or decision.result_hash != envelope.result_hash
            ):
                issues.append("state.decision.envelope.invalid")
                continue
            evidence_times = tuple(
                parsed
                for mention in _safe_delta_mentions(envelope.delta)
                for parsed in (_timestamp(mention.occurred_at),)
                if parsed is not None
            )
            decided_time = _timestamp(decision.decided_at)
            if decided_time is not None and any(
                decided_time < evidence_time for evidence_time in evidence_times
            ):
                issues.append("state.decision.before_evidence")
            if decision.status == "accepted":
                accepted_decisions[review_id] = decision

        transition_ids: set[str] = set()
        expected_revision = 1
        for transition in self._transitions:
            envelope = self._review_envelopes.get(transition.review_id)
            transition_decision = accepted_decisions.get(transition.review_id)
            if transition.review_id in transition_ids:
                issues.append("state.transitions.review_id.duplicate")
            transition_ids.add(transition.review_id)
            if (
                transition.revision != expected_revision
                or transition_decision is None
                or envelope is None
                or transition.operation != envelope.delta.operation
                or transition.result_hash != transition_decision.result_hash
                or transition.result_hash != envelope.result_hash
                or transition.base_graph_hash != envelope.base_graph_hash
                or transition.evidence_atoms != envelope.evidence_atoms
                or not _sha256_value(transition.base_graph_hash)
                or not _sha256_value(transition.result_graph_hash)
            ):
                issues.append("state.transition.provenance.invalid")
            expected_revision += 1
        if (
            self._revision != len(self._transitions)
            or transition_ids != set(accepted_decisions)
        ):
            issues.append("state.revision.transitions.mismatch")

        transition_by_review = {
            item.review_id: item for item in self._transitions
        }
        binding_ids: set[str] = set()
        active_atom_targets: dict[str, set[str]] = {}
        for binding in self._bindings:
            binding_decision = accepted_decisions.get(binding.review_id)
            binding_envelope = self._review_envelopes.get(binding.review_id)
            binding_transition = transition_by_review.get(binding.review_id)
            expected_binding_id = _hash_value(
                {
                    "v": "identity-binding-v1",
                    "review": binding.review_id,
                    "entity": binding.accepted_entity_id,
                    "atom": binding.mention.atom_hash,
                }
            )
            if binding.binding_id in binding_ids:
                issues.append("state.bindings.binding_id.duplicate")
            binding_ids.add(binding.binding_id)
            if (
                binding.binding_id != expected_binding_id
                or not self._mention_matches_registry(binding.mention)
                or binding_decision is None
                or binding_envelope is None
                or binding_transition is None
                or binding.result_hash != binding_decision.result_hash
                or binding.accepted_at != binding_decision.decided_at
                or binding.base_revision != binding_envelope.base_revision
                or binding.base_graph_hash != binding_envelope.base_graph_hash
                or binding.result_revision != binding_transition.revision
                or binding.result_graph_hash != binding_transition.result_graph_hash
                or binding.current_entity_id is not None
                and binding.current_entity_id not in self._graph.entities
            ):
                issues.append("state.binding.provenance.invalid")
            if binding.current_entity_id is not None:
                active_atom_targets.setdefault(
                    binding.mention.atom_hash,
                    set(),
                ).add(binding.current_entity_id)
        if any(len(targets) > 1 for targets in active_atom_targets.values()):
            issues.append("state.bindings.atom.target_conflict")

        tombstone_by_id = {item.entity_id: item for item in self._tombstones}
        if len(tombstone_by_id) != len(self._tombstones):
            issues.append("state.tombstones.entity_id.duplicate")
        for tombstone in self._tombstones:
            tombstone_transition = transition_by_review.get(tombstone.review_id)
            if (
                tombstone.retired_entity.id != tombstone.entity_id
                or tombstone.retired_entity.world_id != world_id
                or tombstone.entity_id in self._graph.entities
                or not tombstone.successors
                or len(set(tombstone.successors)) != len(tombstone.successors)
                or tombstone.entity_id in tombstone.successors
                or tombstone_transition is None
                or tombstone_transition.operation not in ("merge", "split")
                or tombstone.entity_id not in tombstone_transition.from_entity_ids
                or tombstone.successors != tombstone_transition.to_entity_ids
            ):
                issues.append("state.tombstone.provenance.invalid")

        absorbed_ids: set[str] = set()
        for redirect in self._redirects:
            redirect_transition = transition_by_review.get(redirect.review_id)
            if redirect.absorbed_entity_id in absorbed_ids:
                issues.append("state.redirects.absorbed_entity_id.duplicate")
            absorbed_ids.add(redirect.absorbed_entity_id)
            if (
                redirect.absorbed_entity_id in self._graph.entities
                or redirect.survivor_entity_id not in self._graph.entities
                or redirect.absorbed_entity_id == redirect.survivor_entity_id
                or redirect_transition is None
                or redirect_transition.operation != "merge"
                or redirect.absorbed_entity_id not in redirect_transition.from_entity_ids
                or redirect.absorbed_entity_id not in tombstone_by_id
            ):
                issues.append("state.redirect.provenance.invalid")

        for review in self._pending.values():
            if (
                review.base_revision == self._revision
                and review.base_graph_hash == current_graph_hash
            ):
                try:
                    delta_issues = self._authority_delta_issues(
                        review.delta,
                        self._graph,
                    )
                    if delta_issues:
                        issues.append("state.pending.current_delta.invalid")
                        continue
                    preview = self._preview(
                        review.delta,
                        self._graph,
                        review.review_id,
                    )
                    if (
                        _snapshot(preview[0]) != review.preview
                        or preview[1] != review.preview_bindings
                        or preview[2] != review.preview_redirects
                        or preview[3] != review.preview_tombstones
                        or preview[4] != review.preview_transition
                    ):
                        issues.append("state.pending.current_preview.mismatch")
                except (AttributeError, KeyError, TypeError, ValueError):
                    issues.append("state.pending.current_preview.invalid")

        return tuple(sorted(set(issues)))


def _authority_state_shape_issues(state: object) -> tuple[str, ...]:
    if not isinstance(state, IdentityAuthorityState):
        return ("state.type.invalid",)
    issues: list[str] = []
    if not _hex_value(state.authority_id, 32):
        issues.append("state.authority_id.invalid")
    if isinstance(state.revision, bool) or not isinstance(state.revision, int) or state.revision < 0:
        issues.append("state.revision.invalid")
    issues.extend(_snapshot_checkpoint_issues(state.graph, "state.graph"))

    collections: tuple[tuple[str, object, type[Any]], ...] = (
        ("evidence", state.evidence, IdentityEvidence),
        ("mentions", state.mentions, VerifiedReferenceMention),
        ("pending", state.pending, PendingIdentityReview),
        ("review_envelopes", state.review_envelopes, PendingIdentityReview),
        ("decisions", state.decisions, IdentityDecision),
        ("bindings", state.bindings, AcceptedIdentityBinding),
        ("transitions", state.transitions, IdentityTransition),
        ("redirects", state.redirects, IdentityRedirect),
        ("tombstones", state.tombstones, IdentityTombstone),
    )
    for name, value, expected_type in collections:
        if not isinstance(value, tuple):
            issues.append(f"state.{name}.not_tuple")
            continue
        if any(not isinstance(item, expected_type) for item in value):
            issues.append(f"state.{name}.item.invalid")

    if issues:
        return tuple(sorted(set(issues)))
    world_id = state.graph.world.world_id

    evidence_ids: set[str] = set()
    for evidence in state.evidence:
        issues.extend(f"state.{issue}" for issue in _evidence_issues(evidence, world_id))
        if _nonempty(evidence.id):
            if evidence.id in evidence_ids:
                issues.append("state.evidence.id.duplicate")
            evidence_ids.add(evidence.id)

    mention_atoms: set[str] = set()
    for mention in state.mentions:
        if not _nonempty(mention.atom_hash):
            issues.append("state.mentions.atom_hash.invalid")
        elif mention.atom_hash in mention_atoms:
            issues.append("state.mentions.atom_hash.duplicate")
        else:
            mention_atoms.add(mention.atom_hash)

    pending_ids: set[str] = set()
    for review in (*state.pending, *state.review_envelopes):
        if not _nonempty(review.review_id):
            issues.append("state.review.review_id.invalid")
        elif review.review_id in pending_ids:
            issues.append("state.review.review_id.duplicate")
        else:
            pending_ids.add(review.review_id)
        if (
            isinstance(review.base_revision, bool)
            or not isinstance(review.base_revision, int)
            or review.base_revision < 0
            or review.base_revision > state.revision
        ):
            issues.append("state.review.base_revision.invalid")

    decision_ids: set[str] = set()
    for decision in state.decisions:
        if not _nonempty(decision.review_id):
            issues.append("state.decision.review_id.invalid")
        elif decision.review_id in decision_ids:
            issues.append("state.decision.review_id.duplicate")
        else:
            decision_ids.add(decision.review_id)

    binding_ids: set[str] = set()
    for binding in state.bindings:
        if not _nonempty(binding.binding_id):
            issues.append("state.binding.binding_id.invalid")
        elif binding.binding_id in binding_ids:
            issues.append("state.bindings.binding_id.duplicate")
        else:
            binding_ids.add(binding.binding_id)
        if not isinstance(binding.mention, VerifiedReferenceMention):
            issues.append("state.binding.mention.invalid")

    transition_ids: set[str] = set()
    for transition in state.transitions:
        if not _nonempty(transition.review_id):
            issues.append("state.transition.review_id.invalid")
        elif transition.review_id in transition_ids:
            issues.append("state.transitions.review_id.duplicate")
        else:
            transition_ids.add(transition.review_id)

    redirect_ids: set[str] = set()
    for redirect in state.redirects:
        if not _nonempty(redirect.absorbed_entity_id):
            issues.append("state.redirect.absorbed_entity_id.invalid")
        elif redirect.absorbed_entity_id in redirect_ids:
            issues.append("state.redirects.absorbed_entity_id.duplicate")
        else:
            redirect_ids.add(redirect.absorbed_entity_id)

    tombstone_ids: set[str] = set()
    for tombstone in state.tombstones:
        if not _nonempty(tombstone.entity_id):
            issues.append("state.tombstone.entity_id.invalid")
        elif tombstone.entity_id in tombstone_ids:
            issues.append("state.tombstones.entity_id.duplicate")
        else:
            tombstone_ids.add(tombstone.entity_id)
    return tuple(sorted(set(issues)))


def _snapshot_checkpoint_issues(
    snapshot: object,
    prefix: str,
) -> tuple[str, ...]:
    if not isinstance(snapshot, IdentityGraphSnapshot):
        return (f"{prefix}.type.invalid",)
    issues: list[str] = []
    if not isinstance(snapshot.world, PersonalWorld):
        issues.append(f"{prefix}.world.type.invalid")
    collections: tuple[tuple[str, object, type[Any]], ...] = (
        ("entities", snapshot.entities, Entity),
        ("relationships", snapshot.relationships, Relationship),
        ("events", snapshot.events, WorldEvent),
        ("cognitions", snapshot.cognitions, WorldCognition),
    )
    for name, value, expected_type in collections:
        if not isinstance(value, tuple):
            issues.append(f"{prefix}.{name}.not_tuple")
        elif any(not isinstance(item, expected_type) for item in value):
            issues.append(f"{prefix}.{name}.item.invalid")
        else:
            ids = tuple(item.id for item in value)
            if any(not _nonempty(item_id) for item_id in ids) or len(set(ids)) != len(ids):
                issues.append(f"{prefix}.{name}.ids.invalid")
    if issues:
        return tuple(sorted(set(issues)))
    try:
        graph = snapshot.to_graph()
        graph_issues = _graph_issues(graph)
        issues.extend(f"{prefix}.{issue}" for issue in graph_issues)
        if snapshot.graph_hash != _graph_hash(graph):
            issues.append(f"{prefix}.graph_hash.mismatch")
    except (AttributeError, KeyError, TypeError, ValueError):
        issues.append(f"{prefix}.invalid")
    return tuple(sorted(set(issues)))


def _review_checkpoint_issues(
    review: PendingIdentityReview,
    prefix: str,
    world_id: str,
    mentions: Mapping[str, VerifiedReferenceMention],
    pending_hash: Any,
) -> tuple[str, ...]:
    issues: list[str] = []
    if (
        review.status != "pending"
        or review.world_id != world_id
        or not _sha256_value(review.base_graph_hash)
        or not isinstance(review.delta, EntityIdentityDelta)
        or not isinstance(review.evidence_atoms, tuple)
        or not isinstance(review.preview_bindings, tuple)
        or any(not isinstance(item, AcceptedIdentityBinding) for item in review.preview_bindings)
        or not isinstance(review.preview_redirects, tuple)
        or any(not isinstance(item, IdentityRedirect) for item in review.preview_redirects)
        or not isinstance(review.preview_tombstones, tuple)
        or any(not isinstance(item, IdentityTombstone) for item in review.preview_tombstones)
        or not isinstance(review.preview_transition, IdentityTransition)
    ):
        issues.append(f"{prefix}.shape.invalid")
        return tuple(issues)
    issues.extend(_snapshot_checkpoint_issues(review.preview, f"{prefix}.preview"))
    referenced_mentions = _safe_delta_mentions(review.delta)
    expected_atoms = tuple(sorted({item.atom_hash for item in referenced_mentions}))
    if (
        review.evidence_atoms != expected_atoms
        or any(mentions.get(item.atom_hash) != item for item in referenced_mentions)
    ):
        issues.append(f"{prefix}.evidence_atoms.invalid")
    preview_entity_ids = {
        item.id for item in review.preview.entities
    } if isinstance(review.preview, IdentityGraphSnapshot) else set()
    if any(
        item.current_entity_id is not None
        and item.current_entity_id not in preview_entity_ids
        for item in review.preview_bindings
    ):
        issues.append(f"{prefix}.preview_bindings.target.invalid")
    if (
        review.preview_transition.review_id != review.review_id
        or review.preview_transition.operation != review.delta.operation
        or review.preview_transition.revision != 0
        or review.preview_transition.result_hash
    ):
        issues.append(f"{prefix}.preview_transition.invalid")
    try:
        if _json_value(review.review_payload) != review.review_payload:
            issues.append(f"{prefix}.review_payload.invalid")
        if review.preview_hash != _pending_preview_hash(review):
            issues.append(f"{prefix}.preview_hash.mismatch")
        if review.result_hash != pending_hash(review):
            issues.append(f"{prefix}.result_hash.mismatch")
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
        issues.append(f"{prefix}.hash.invalid")
    return tuple(sorted(set(issues)))


def _safe_delta_mentions(delta: object) -> tuple[VerifiedReferenceMention, ...]:
    if not isinstance(delta, EntityIdentityDelta):
        return ()
    direct = tuple(
        item
        for item in delta.mentions
        if isinstance(item, VerifiedReferenceMention)
    ) if isinstance(delta.mentions, tuple) else ()
    successor_mentions = tuple(
        mention
        for successor in delta.successors
        if isinstance(successor, SplitSuccessor)
        and isinstance(successor.support_mentions, tuple)
        for mention in successor.support_mentions
        if isinstance(mention, VerifiedReferenceMention)
    ) if isinstance(delta.successors, tuple) else ()
    return direct + successor_mentions


def _sha256_value(value: object) -> bool:
    return _hex_value(value, 64)


def _hex_value(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _evidence_issues(value: object, world_id: str) -> tuple[str, ...]:
    if not isinstance(value, IdentityEvidence):
        return ("evidence.type.invalid",)
    issues: list[str] = []
    for name in ("id", "world_id", "conversation_id", "occurred_at", "content"):
        if not _nonempty(getattr(value, name)):
            issues.append(f"evidence.{name}.invalid")
    if value.world_id != world_id:
        issues.append("evidence.world_id.mismatch")
    if value.source_role != "user":
        issues.append("evidence.source_role.ineligible")
    if _timestamp(value.occurred_at) is None:
        issues.append("evidence.occurred_at.invalid_timestamp")
    if value.continuity_scope is not None and not _nonempty(value.continuity_scope):
        issues.append("evidence.continuity_scope.invalid")
    return tuple(issues)


def _delta_issues(
    delta: object,
    base: MemoryWorldGraph,
    mentions: Mapping[str, VerifiedReferenceMention],
) -> tuple[str, ...]:
    if not isinstance(delta, EntityIdentityDelta):
        return ("delta.type.invalid",)
    issues = list(_graph_issues(base))
    if not _nonempty(delta.world_id) or delta.world_id != base.world.world_id:
        issues.append("delta.world_id.mismatch")
    if delta.operation not in ("bind", "alias", "merge", "split"):
        issues.append("delta.operation.invalid")
        return tuple(issues)

    mention_values = delta.mentions if isinstance(delta.mentions, tuple) else ()
    if not isinstance(delta.mentions, tuple):
        issues.append("delta.mentions.not_tuple")
    for index, mention in enumerate(mention_values):
        atom_hash = (
            mention.atom_hash
            if isinstance(mention, VerifiedReferenceMention)
            and _nonempty(mention.atom_hash)
            else ""
        )
        if not atom_hash or mentions.get(atom_hash) != mention:
            issues.append(f"delta.mentions[{index}].unverified")
    valid_atoms = [
        item.atom_hash
        for item in mention_values
        if isinstance(item, VerifiedReferenceMention) and _nonempty(item.atom_hash)
    ]
    if len(valid_atoms) != len(set(valid_atoms)):
        issues.append("delta.mentions.duplicate")

    for field_name, value in (
        ("absorbed_entity_ids", delta.absorbed_entity_ids),
        ("successors", delta.successors),
        ("rewrites", delta.rewrites),
        ("binding_assignments", delta.binding_assignments),
    ):
        if not isinstance(value, tuple):
            issues.append(f"delta.{field_name}.not_tuple")

    if delta.operation == "bind":
        if not _nonempty(delta.entity_id) or delta.entity_id not in base.entities:
            issues.append("bind.entity.unknown")
        if len(mention_values) != 1:
            issues.append("bind.mentions.count.invalid")
    elif delta.operation == "alias":
        if not _nonempty(delta.entity_id) or delta.entity_id not in base.entities:
            issues.append("alias.entity.unknown")
        if not _nonempty(delta.alias_value):
            issues.append("alias.value.invalid")
        elif (
            isinstance(delta.alias_value, str)
            and isinstance(delta.entity_id, str)
            and delta.entity_id in base.entities
        ):
            key = _identity_key(delta.alias_value)
            target = base.entities[delta.entity_id]
            if any(_identity_key(label) == key for label in (target.canonical_name, *target.aliases)):
                issues.append("alias.value.duplicate")
            for entity in base.entities.values():
                if entity.id != delta.entity_id and any(
                    _identity_key(label) == key
                    for label in (entity.canonical_name, *entity.aliases)
                ):
                    issues.append(f"alias.conflicts_with:{entity.id}")
        if not mention_values:
            issues.append("alias.mentions.empty")
        elif isinstance(delta.alias_value, str) and not any(
            _identity_key(item.text) == _identity_key(delta.alias_value)
            for item in mention_values
            if isinstance(item, VerifiedReferenceMention)
        ):
            issues.append("alias.support.exact_label.required")
    elif delta.operation == "merge":
        survivor = delta.survivor_entity_id
        absorbed = (
            delta.absorbed_entity_ids
            if isinstance(delta.absorbed_entity_ids, tuple)
            else ()
        )
        valid_absorbed = tuple(item for item in absorbed if _nonempty(item))
        if not _nonempty(survivor) or survivor not in base.entities:
            issues.append("merge.survivor.unknown")
        if not mention_values:
            issues.append("merge.mentions.empty")
        if (
            not absorbed
            or len(valid_absorbed) != len(absorbed)
            or len(set(valid_absorbed)) != len(valid_absorbed)
        ):
            issues.append("merge.absorbed.invalid")
        if survivor in valid_absorbed:
            issues.append("merge.self.invalid")
        for entity_id in valid_absorbed:
            if entity_id not in base.entities:
                issues.append("merge.absorbed.unknown")
        if base.world.owner_entity_id in valid_absorbed:
            issues.append("merge.owner.absorbed")
        if isinstance(survivor, str) and survivor in base.entities and any(
            entity_id in base.entities
            and base.entities[entity_id].kind != base.entities[survivor].kind
            for entity_id in valid_absorbed
        ):
            issues.append("merge.kind.mismatch")
    else:
        issues.extend(_split_issues(delta, base, mentions))
    return tuple(issues)


def _split_issues(
    delta: EntityIdentityDelta,
    base: MemoryWorldGraph,
    mentions: Mapping[str, VerifiedReferenceMention],
) -> tuple[str, ...]:
    issues: list[str] = []
    source = delta.source_entity_id
    if not _nonempty(source) or source not in base.entities:
        return ("split.source.unknown",)
    assert isinstance(source, str)
    if source == base.world.owner_entity_id:
        issues.append("split.owner.forbidden")
    successors = delta.successors if isinstance(delta.successors, tuple) else ()
    if len(successors) < 2:
        issues.append("split.successors.count.invalid")
    existing_ids = _all_object_ids(base)
    successor_ids: list[str] = []
    for index, successor in enumerate(successors):
        prefix = f"split.successors[{index}]"
        entity = successor.entity if isinstance(successor, SplitSuccessor) else None
        if not isinstance(entity, Entity):
            issues.append(prefix + ".type.invalid")
            continue
        entity_id_valid = _nonempty(entity.id)
        if entity_id_valid:
            successor_ids.append(entity.id)
        else:
            issues.append(prefix + ".id.invalid")
        if entity_id_valid and (entity.id == source or entity.id in existing_ids):
            issues.append(prefix + ".id.not_fresh")
        aliases_valid = isinstance(entity.aliases, tuple) and all(
            _nonempty(alias) for alias in entity.aliases
        )
        if (
            not _nonempty(entity.world_id)
            or not _nonempty(entity.kind)
            or not _nonempty(entity.canonical_name)
            or not aliases_valid
            or entity.world_id != base.world.world_id
            or entity.kind != base.entities[source].kind
        ):
            issues.append(prefix + ".shape.mismatch")
        support_mentions = (
            successor.support_mentions
            if isinstance(successor.support_mentions, tuple)
            else ()
        )
        if not isinstance(successor.support_mentions, tuple):
            issues.append(prefix + ".support.not_tuple")
        if not support_mentions:
            issues.append(prefix + ".support.empty")
        for mention in support_mentions:
            atom_hash = (
                mention.atom_hash
                if isinstance(mention, VerifiedReferenceMention)
                and _nonempty(mention.atom_hash)
                else ""
            )
            if not atom_hash or mentions.get(atom_hash) != mention:
                issues.append(prefix + ".support.unverified")
        labels = (
            (entity.canonical_name, *entity.aliases)
            if isinstance(entity.aliases, tuple)
            else (entity.canonical_name,)
        )
        for label in labels:
            if _nonempty(label) and not any(
                isinstance(mention, VerifiedReferenceMention)
                and _identity_key(mention.text) == _identity_key(label)
                for mention in support_mentions
            ):
                issues.append(prefix + ".support.exact_label.required")
    if len(set(successor_ids)) != len(successor_ids):
        issues.append("split.successors.id.duplicate")

    expected = set(_entity_locators(base, source))
    rewrites = delta.rewrites if isinstance(delta.rewrites, tuple) else ()
    valid_rewrites: list[EntityReferenceRewrite] = []
    for index, rewrite in enumerate(rewrites):
        if not isinstance(rewrite, EntityReferenceRewrite):
            issues.append(f"split.rewrites[{index}].type.invalid")
            continue
        locator = rewrite.locator
        if (
            not isinstance(locator, EntityReferenceLocator)
            or not isinstance(locator.surface, str)
            or locator.surface not in _REFERENCE_SURFACES
            or not _nonempty(locator.object_id)
            or isinstance(locator.index, bool)
            or not isinstance(locator.index, int)
            or locator.index < 0
        ):
            issues.append(f"split.rewrites[{index}].locator.invalid")
            continue
        valid_rewrites.append(rewrite)
    actual = [item.locator for item in valid_rewrites]
    if len(set(actual)) != len(actual):
        issues.append("split.rewrites.locator.duplicate")
    if set(actual) != expected:
        issues.append("split.rewrites.partition.incomplete")
    successor_id_set = set(successor_ids)
    for rewrite in valid_rewrites:
        if not _nonempty(rewrite.expected_entity_id) or rewrite.expected_entity_id != source:
            issues.append("split.rewrites.expected_old.mismatch")
        if (
            not _nonempty(rewrite.replacement_entity_id)
            or rewrite.replacement_entity_id not in successor_id_set
        ):
            issues.append("split.rewrites.successor.unknown")
    return tuple(issues)


def _entity_locators(graph: MemoryWorldGraph, entity_id: str) -> tuple[EntityReferenceLocator, ...]:
    output: list[EntityReferenceLocator] = []
    for relationship in graph.relationships.values():
        if relationship.source_entity_id == entity_id:
            output.append(EntityReferenceLocator("relationship.source", relationship.id))
        if relationship.target_entity_id == entity_id:
            output.append(EntityReferenceLocator("relationship.target", relationship.id))
    for event in graph.events.values():
        output.extend(EntityReferenceLocator("event.participant", event.id, index) for index, participant in enumerate(event.participants) if participant.entity_id == entity_id)
        output.extend(EntityReferenceLocator("event.related", event.id, index) for index, related in enumerate(event.related_entity_ids) if related == entity_id)
        output.extend(EntityReferenceLocator("event.facet_about", event.id, index) for index, facet in enumerate(event.facets) if facet.about_entity_id == entity_id)
    for cognition in graph.cognitions.values():
        if cognition.target.kind == "entity" and cognition.target.id == entity_id:
            output.append(EntityReferenceLocator("cognition.target", cognition.id))
        output.extend(EntityReferenceLocator("cognition.perspective", cognition.id, index) for index, holder in enumerate(cognition.perspective.holder_entity_ids) if holder == entity_id)
    return tuple(sorted(output, key=lambda item: (item.surface, item.object_id, item.index)))


def _rewrite_graph(graph: MemoryWorldGraph, mapping: Mapping[str, str]) -> MemoryWorldGraph:
    rewrites = {locator: replacement for source in mapping for locator in _entity_locators(graph, source) for replacement in (mapping[source],)}
    candidate = _rewrite_graph_at_locators(graph, rewrites)
    issues = _rewrite_integrity_issues(candidate)
    if issues:
        raise IdentityReviewValidationError(issues)
    return candidate


def _rewrite_graph_at_locators(graph: MemoryWorldGraph, rewrites: Mapping[EntityReferenceLocator, str]) -> MemoryWorldGraph:
    candidate = _clone_graph(graph)
    for relationship_id, relationship in tuple(candidate.relationships.items()):
        source = rewrites.get(EntityReferenceLocator("relationship.source", relationship_id), relationship.source_entity_id)
        target = rewrites.get(EntityReferenceLocator("relationship.target", relationship_id), relationship.target_entity_id)
        candidate.relationships[relationship_id] = replace(relationship, source_entity_id=source, target_entity_id=target)
    for event_id, event in tuple(candidate.events.items()):
        participants = tuple(replace(item, entity_id=rewrites.get(EntityReferenceLocator("event.participant", event_id, index), item.entity_id)) for index, item in enumerate(event.participants))
        related = tuple(rewrites.get(EntityReferenceLocator("event.related", event_id, index), item) for index, item in enumerate(event.related_entity_ids))
        facets = tuple(replace(item, about_entity_id=rewrites.get(EntityReferenceLocator("event.facet_about", event_id, index), item.about_entity_id)) if item.about_entity_id is not None else item for index, item in enumerate(event.facets))
        candidate.events[event_id] = replace(event, participants=participants, related_entity_ids=related, facets=facets)
    for cognition_id, cognition in tuple(candidate.cognitions.items()):
        target_value: MemoryTarget = cognition.target
        if target_value.kind == "entity":
            target_value = replace(target_value, id=rewrites.get(EntityReferenceLocator("cognition.target", cognition_id), target_value.id))
        holders = tuple(rewrites.get(EntityReferenceLocator("cognition.perspective", cognition_id, index), holder) for index, holder in enumerate(cognition.perspective.holder_entity_ids))
        try:
            perspective = Perspective(cognition.perspective.kind, holders)
        except ValueError as error:
            raise IdentityReviewValidationError(("rewrite.cognition.perspective.cardinality_collapse",)) from error
        candidate.cognitions[cognition_id] = replace(cognition, target=target_value, perspective=perspective)
    issues = _rewrite_integrity_issues(candidate)
    if issues:
        raise IdentityReviewValidationError(issues)
    return candidate


def _rewrite_integrity_issues(graph: MemoryWorldGraph) -> tuple[str, ...]:
    issues: list[str] = []
    for relationship in graph.relationships.values():
        if relationship.source_entity_id == relationship.target_entity_id:
            issues.append("rewrite.relationship.self_loop")
    for event in graph.events.values():
        participant_ids = tuple(item.entity_id for item in event.participants)
        if len(set(participant_ids)) != len(participant_ids):
            issues.append("rewrite.event.participants.cardinality_collapse")
        if len(set(event.related_entity_ids)) != len(event.related_entity_ids):
            issues.append("rewrite.event.related.cardinality_collapse")
    for cognition in graph.cognitions.values():
        holders = cognition.perspective.holder_entity_ids
        if len(set(holders)) != len(holders):
            issues.append("rewrite.cognition.holders.cardinality_collapse")
        if cognition.perspective.kind == "joint" and len(holders) < 2:
            issues.append("rewrite.cognition.joint_holder_collapse")
    return tuple(issues)


def _graph_issues(graph: object) -> tuple[str, ...]:
    # Keep one full-graph identity preflight for both resolution and identity
    # transitions.  The resolver validator is deliberately total over malformed
    # runtime values, so public identity-review APIs never leak raw container or
    # attribute exceptions while traversing the same reference surface.
    return tuple(
        f"graph.{issue[5:]}" if issue.startswith("base.") else f"graph.{issue}"
        for issue in _base_identity_shape_issues(graph)  # type: ignore[arg-type]
    )


def _clone_graph(graph: MemoryWorldGraph) -> MemoryWorldGraph:
    return deepcopy(graph)


def _snapshot(graph: MemoryWorldGraph) -> IdentityGraphSnapshot:
    return IdentityGraphSnapshot(
        deepcopy(graph.world),
        deepcopy(tuple(sorted(graph.entities.values(), key=lambda item: item.id))),
        deepcopy(tuple(sorted(graph.relationships.values(), key=lambda item: item.id))),
        deepcopy(tuple(sorted(graph.events.values(), key=lambda item: item.id))),
        deepcopy(tuple(sorted(graph.cognitions.values(), key=lambda item: item.id))),
        _graph_hash(graph),
    )


def _graph_hash(graph: MemoryWorldGraph) -> str:
    return _hash_value({"v": "identity-graph-v1", "world": _value(graph.world), "entities": [_value(item) for item in sorted(graph.entities.values(), key=lambda item: item.id)], "relationships": [_value(item) for item in sorted(graph.relationships.values(), key=lambda item: item.id)], "events": [_value(item) for item in sorted(graph.events.values(), key=lambda item: item.id)], "cognitions": [_graph_hash_cognition_value(item) for item in sorted(graph.cognitions.values(), key=lambda item: item.id)]})


def _graph_hash_cognition_value(cognition: WorldCognition) -> dict[str, Any]:
    """Preserve v1 hashes for legacy cognitions without structured semantics."""

    value = _value(cognition)
    assert isinstance(value, dict)
    return {
        key: item
        for key, item in value.items()
        if key != "structured_claim" or item is not None
    }


def _pending_preview_value(pending: PendingIdentityReview) -> dict[str, Any]:
    return {
        "graph": _value(pending.preview),
        "bindings": _value(pending.preview_bindings),
        "redirects": _value(pending.preview_redirects),
        "tombstones": _value(pending.preview_tombstones),
        "transition": _value(pending.preview_transition),
    }


def _pending_preview_hash(pending: PendingIdentityReview) -> str:
    return _hash_value(
        {
            "v": "identity-transition-preview-v1",
            "preview": _pending_preview_value(pending),
        }
    )


def _all_object_ids(graph: MemoryWorldGraph) -> set[str]:
    return {*graph.entities, *graph.relationships, *graph.events, *graph.cognitions}


def _delta_atoms(delta: EntityIdentityDelta) -> set[str]:
    return {item.atom_hash for item in _delta_verified_mentions(delta)}


def _delta_verified_mentions(
    delta: EntityIdentityDelta,
) -> tuple[VerifiedReferenceMention, ...]:
    return delta.mentions + tuple(
        mention
        for successor in delta.successors
        for mention in successor.support_mentions
    )


def _value(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value]
    if isinstance(value, frozenset):
        return sorted(_value(item) for item in value)
    return value


def _json_value(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError) as error:
        raise IdentityReviewValidationError(("review_payload.not_canonical_json",)) from error


def _hash_value(value: Any) -> str:
    return _sha(json.dumps(_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _mention_atom_hash(
    *,
    evidence: IdentityEvidence,
    text: str,
    claim_span: ClaimSpan,
    continuity_scope: str | None,
    kind_hint: str | None,
) -> str:
    return _hash_value(
        {
            "v": "identity-mention-v1",
            "evidence_id": evidence.id,
            "world_id": evidence.world_id,
            "conversation_id": evidence.conversation_id,
            "occurred_at": evidence.occurred_at,
            "text": text,
            "span": _value(claim_span),
            "continuity_scope": continuity_scope,
            "kind_hint": kind_hint,
        }
    )


def _sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _identity_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().strip().split())


__all__ = [
    "AcceptedIdentityBinding",
    "BindingAssignment",
    "EntityIdentityDelta",
    "EntityReferenceLocator",
    "EntityReferenceRewrite",
    "IdentityAuthority",
    "IdentityAuthorityState",
    "IdentityAuthorityView",
    "IdentityDecision",
    "IdentityEvidence",
    "IdentityGraphSnapshot",
    "IdentityRedirect",
    "IdentityResolutionContext",
    "IdentityResolverInputs",
    "IdentityReviewStateError",
    "IdentityReviewValidationError",
    "IdentityTombstone",
    "IdentityTransition",
    "PendingIdentityReview",
    "SplitSuccessor",
    "VerifiedReferenceMention",
]
