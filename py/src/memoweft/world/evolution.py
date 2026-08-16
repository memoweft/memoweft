"""Deterministic, reviewable world-evolution contracts for MemoWeft Next.

Stage 4 keeps canonical history append-only while deriving a current view from
typed transitions.  A transition never mutates a graph by itself: it is bound
to a normal :class:`WorldDelta`, validated against the current graph, then
handed to ``MemoryLoop`` for the existing review/accept transaction.

Relationship state and active salience are projections, not long-lived scalar
truths on ``Relationship``.  Corrections, contradictions and disagreements are
kept distinct so later code can explain *how* the current view was reached.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Mapping, cast

from ..clock import parse_iso_ms
from ..confidence import compute_confidence, derive_cred_status
from ..config import CONFIG
from ..decay import effective_confidence
from ..types import ConfidenceInputs, EvidenceLink, EvidenceRelation
from .delta import WorldDelta
from .graph import MemoryWorldGraph
from .model import MemoryTarget, Perspective, WorldCognition, WorldEvent


EvolutionKind = Literal[
    "relationship_state",
    "relationship_successor",
    "event_link",
    "cognition_change",
]
EvolutionRelation = Literal[
    "active",
    "strained",
    "conflicted",
    "repairing",
    "repaired",
    "dormant",
    "ended",
    "causes",
    "responds_to",
    "repairs",
    "resolves",
    "continues",
    "corrects",
    "narrows",
    "supersedes",
    "contradicts",
    "reaffirms",
    "disagrees_with",
    "reestablished",
]

_RELATIONSHIP_STATES = frozenset(
    {"active", "strained", "conflicted", "repairing", "repaired", "dormant", "ended"}
)
_EVENT_RELATIONS = frozenset({"causes", "responds_to", "repairs", "resolves", "continues"})
_COGNITION_RELATIONS = frozenset(
    {"corrects", "narrows", "supersedes", "contradicts", "reaffirms", "disagrees_with"}
)
_SUPERSEDING_COGNITION_RELATIONS = frozenset({"corrects", "narrows", "supersedes"})
_DAY_MS = 86_400_000


class WorldEvolutionValidationError(ValueError):
    """A stable, path-oriented rejection of an evolution plan or projection."""

    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = issues
        super().__init__("World evolution validation failed: " + ", ".join(issues))


@dataclass(frozen=True, slots=True)
class EvolutionStep:
    """One typed edge in the accepted history/current-state projection."""

    id: str
    kind: EvolutionKind
    relation: EvolutionRelation
    subject: MemoryTarget
    predecessor_ids: tuple[str, ...]
    successor_ids: tuple[str, ...]
    effective_at: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptedEvolutionStep:
    """An evolution step after its exact reviewed proposal was accepted."""

    review_id: str
    revision: int
    step: EvolutionStep


@dataclass(frozen=True, slots=True)
class RelationshipStateProjection:
    relationship_id: str
    state: str
    cognition_id: str
    effective_at: str
    revision: int
    transition_id: str


@dataclass(frozen=True, slots=True)
class CognitionLifecycleProjection:
    cognition_id: str
    is_current: bool
    is_expired: bool
    effective_confidence: int
    active_salience: int


@dataclass(frozen=True, slots=True)
class WorldEvolutionPlan:
    """A normal create-only delta plus the typed meaning of its evolution."""

    delta: WorldDelta
    steps: tuple[EvolutionStep, ...]
    cognition_updates: tuple[WorldCognition, ...] = ()

    def apply_to(
        self,
        base: MemoryWorldGraph,
        eligible_evidence_ids: Iterable[str],
        *,
        superseded_cognition_ids: frozenset[str] = frozenset(),
        known_transition_ids: frozenset[str] = frozenset(),
        ended_relationship_ids: frozenset[str] = frozenset(),
    ) -> MemoryWorldGraph:
        """Validate the complete plan, then return its isolated graph preview."""

        eligible = _validated_string_set(eligible_evidence_ids, "eligible_evidence_ids")
        if type(self.steps) is not tuple or not self.steps:
            raise WorldEvolutionValidationError(("steps.invalid",))
        issues: list[str] = []
        if not isinstance(self.delta, WorldDelta):
            issues.append("delta.invalid_type")
        if not isinstance(base, MemoryWorldGraph):
            issues.append("base.invalid_type")
        if self.delta.world_id != base.world.world_id:
            issues.append("delta.world_id.mismatch")
        preview = self.delta.apply_to(base, eligible)
        updated_cognition_ids = _apply_cognition_updates(
            base,
            preview,
            self.cognition_updates,
            eligible,
            issues,
        )

        known_ids = _validated_string_set(known_transition_ids, "known_transition_ids")
        ended_relationships = _validated_string_set(
            ended_relationship_ids,
            "ended_relationship_ids",
        )
        superseded = _validated_string_set(superseded_cognition_ids, "superseded_cognition_ids")
        new_event_ids = {item.id for item in self.delta.new_events}
        new_relationship_ids = {item.id for item in self.delta.new_relationships}
        new_cognition_ids = {item.id for item in self.delta.new_cognitions}
        plan_evidence_ids = set(self.delta.source_evidence_ids)
        seen_step_ids: set[str] = set()
        consumed_predecessors: set[str] = set()
        referenced_update_ids: set[str] = set()

        for index, step in enumerate(self.steps):
            path = f"step[{index}]"
            if not isinstance(step, EvolutionStep):
                issues.append(path + ".invalid_type")
                continue
            _validate_step_shape(step, path, eligible, plan_evidence_ids, issues)
            if step.id in seen_step_ids:
                issues.append(path + ".id.duplicate")
            else:
                seen_step_ids.add(step.id)
            if step.id in known_ids:
                issues.append(path + ".id.already_accepted")
            if not _target_exists(preview, step.subject):
                issues.append(path + ".subject.dangling")

            if step.kind == "event_link":
                self._validate_event_link(step, path, preview, new_event_ids, issues)
            elif step.kind == "relationship_successor":
                self._validate_relationship_successor(
                    step,
                    path,
                    base,
                    preview,
                    new_relationship_ids,
                    ended_relationships,
                    issues,
                )
            elif step.kind == "relationship_state":
                self._validate_relationship_state(
                    step,
                    path,
                    base,
                    preview,
                    new_cognition_ids,
                    superseded,
                    consumed_predecessors,
                    issues,
                )
            elif step.kind == "cognition_change":
                self._validate_cognition_change(
                    step,
                    path,
                    base,
                    preview,
                    new_cognition_ids,
                    updated_cognition_ids,
                    superseded,
                    consumed_predecessors,
                    referenced_update_ids,
                    issues,
                )
            else:
                issues.append(path + ".kind.invalid")

        if referenced_update_ids != updated_cognition_ids:
            issues.append("cognition_updates.unbound")
        if issues:
            raise WorldEvolutionValidationError(tuple(issues))
        return preview

    @staticmethod
    def _validate_relationship_successor(
        step: EvolutionStep,
        path: str,
        base: MemoryWorldGraph,
        preview: MemoryWorldGraph,
        new_relationship_ids: set[str],
        ended_relationship_ids: set[str],
        issues: list[str],
    ) -> None:
        """Validate one append-only ended -> re-established relationship edge."""

        if step.relation != "reestablished":
            issues.append(path + ".relation.invalid_for_relationship_successor")
        if step.subject.kind != "relationship":
            issues.append(path + ".subject.relationship.required")
        if len(step.predecessor_ids) != 1 or len(step.successor_ids) != 1:
            issues.append(path + ".relationship.cardinality")
            return
        predecessor_id = step.predecessor_ids[0]
        successor_id = step.successor_ids[0]
        predecessor = base.relationships.get(predecessor_id)
        successor = preview.relationships.get(successor_id)
        if predecessor is None:
            issues.append(path + ".predecessor.dangling")
        if successor is None:
            issues.append(path + ".successor.dangling")
        if successor_id not in new_relationship_ids:
            issues.append(path + ".successor.not_new")
        if step.subject != MemoryTarget("relationship", successor_id):
            issues.append(path + ".subject.successor_mismatch")
        if predecessor_id == successor_id:
            issues.append(path + ".relationship.same_id")
        if predecessor is None or successor is None:
            return
        if _relationship_identity(predecessor) != _relationship_identity(successor):
            issues.append(path + ".relationship.identity_mismatch")
        if (
            predecessor.id not in ended_relationship_ids
            and not _relationship_has_ended(
                predecessor,
                step.effective_at,
                path,
                issues,
            )
        ):
            issues.append(path + ".predecessor.not_ended")
        if not _relationship_is_current(successor, step.effective_at, path, issues):
            issues.append(path + ".successor.not_current")
        if successor.valid_from != step.effective_at:
            issues.append(path + ".successor.valid_from_mismatch")

    @staticmethod
    def _validate_event_link(
        step: EvolutionStep,
        path: str,
        preview: MemoryWorldGraph,
        new_event_ids: set[str],
        issues: list[str],
    ) -> None:
        if step.relation not in _EVENT_RELATIONS:
            issues.append(path + ".relation.invalid_for_event_link")
        if step.subject.kind != "relationship" or step.subject.id not in preview.relationships:
            issues.append(path + ".subject.relationship.required")
        if len(step.predecessor_ids) != 1 or len(step.successor_ids) != 1:
            issues.append(path + ".event.cardinality")
            return
        prior = preview.events.get(step.predecessor_ids[0])
        successor = preview.events.get(step.successor_ids[0])
        if prior is None:
            issues.append(path + ".predecessor.dangling")
        if successor is None:
            issues.append(path + ".successor.dangling")
        if step.successor_ids[0] not in new_event_ids:
            issues.append(path + ".successor.not_new")
        if prior is None or successor is None:
            return
        relationship_id = step.subject.id
        if relationship_id not in prior.relationship_ids:
            issues.append(path + ".predecessor.relationship.mismatch")
        if relationship_id not in successor.relationship_ids:
            issues.append(path + ".successor.relationship.mismatch")
        prior_ms = _iso_ms(prior.occurred_at, path + ".predecessor.occurred_at", issues)
        successor_ms = _iso_ms(successor.occurred_at, path + ".successor.occurred_at", issues)
        if prior_ms is not None and successor_ms is not None and successor_ms <= prior_ms:
            issues.append(path + ".temporal_order.invalid")
        if step.effective_at != successor.occurred_at:
            issues.append(path + ".effective_at.successor_mismatch")
        if not set(step.evidence_ids) <= set(successor.evidence_ids):
            issues.append(path + ".evidence.successor_mismatch")

    @staticmethod
    def _validate_relationship_state(
        step: EvolutionStep,
        path: str,
        base: MemoryWorldGraph,
        preview: MemoryWorldGraph,
        new_cognition_ids: set[str],
        superseded: set[str],
        consumed_predecessors: set[str],
        issues: list[str],
    ) -> None:
        if step.relation not in _RELATIONSHIP_STATES:
            issues.append(path + ".relation.invalid_for_relationship_state")
        if step.subject.kind != "relationship" or step.subject.id not in preview.relationships:
            issues.append(path + ".subject.relationship.required")
        if len(step.predecessor_ids) > 1 or len(step.successor_ids) != 1:
            issues.append(path + ".cognition.cardinality")
            return
        successor = preview.cognitions.get(step.successor_ids[0])
        if successor is None:
            issues.append(path + ".successor.dangling")
            return
        if successor.id not in new_cognition_ids:
            issues.append(path + ".successor.not_new")
        if successor.target != step.subject:
            issues.append(path + ".successor.target.mismatch")
        if successor.scope != "relationship_state":
            issues.append(path + ".successor.scope.relationship_state.required")
        _validate_successor_evidence(successor, step, path, issues)
        if not step.predecessor_ids:
            return
        prior_id = step.predecessor_ids[0]
        prior = base.cognitions.get(prior_id)
        if prior is None:
            issues.append(path + ".predecessor.dangling")
            return
        if prior.target != step.subject or prior.scope != "relationship_state":
            issues.append(path + ".predecessor.relationship_state.mismatch")
        _consume_prior(prior_id, path, superseded, consumed_predecessors, issues)

    @staticmethod
    def _validate_cognition_change(
        step: EvolutionStep,
        path: str,
        base: MemoryWorldGraph,
        preview: MemoryWorldGraph,
        new_cognition_ids: set[str],
        updated_cognition_ids: set[str],
        superseded: set[str],
        consumed_predecessors: set[str],
        referenced_update_ids: set[str],
        issues: list[str],
    ) -> None:
        if step.relation not in _COGNITION_RELATIONS:
            issues.append(path + ".relation.invalid_for_cognition_change")
        expected_prior_count = 1 if step.relation in {"narrows", "contradicts", "reaffirms", "disagrees_with"} else None
        if not step.predecessor_ids or len(step.successor_ids) != 1:
            issues.append(path + ".cognition.cardinality")
            return
        if expected_prior_count is not None and len(step.predecessor_ids) != expected_prior_count:
            issues.append(path + ".predecessor.cardinality")
        if len(step.predecessor_ids) > 4:
            issues.append(path + ".predecessor.too_many")
        successor = preview.cognitions.get(step.successor_ids[0])
        if successor is None:
            issues.append(path + ".successor.dangling")
            return
        is_evidence_update = step.relation in {"contradicts", "reaffirms"}
        if is_evidence_update:
            if len(step.predecessor_ids) == 1 and successor.id != step.predecessor_ids[0]:
                issues.append(path + ".successor.must_retain_id")
            if successor.id not in updated_cognition_ids:
                issues.append(path + ".successor.not_updated")
            else:
                referenced_update_ids.add(successor.id)
        elif successor.id not in new_cognition_ids:
            issues.append(path + ".successor.not_new")
        if successor.target != step.subject:
            issues.append(path + ".successor.subject.mismatch")
        _validate_successor_evidence(successor, step, path, issues)

        priors: list[WorldCognition] = []
        for prior_index, prior_id in enumerate(step.predecessor_ids):
            prior = base.cognitions.get(prior_id)
            if prior is None:
                issues.append(f"{path}.predecessor_ids[{prior_index}].dangling")
                continue
            priors.append(prior)
            if prior.target != step.subject:
                issues.append(f"{path}.predecessor_ids[{prior_index}].subject.mismatch")
            if step.relation in _SUPERSEDING_COGNITION_RELATIONS:
                _consume_prior(prior_id, path, superseded, consumed_predecessors, issues)
            elif prior_id in superseded:
                issues.append(f"{path}.predecessor_ids[{prior_index}].already_superseded")
        if not priors:
            return

        first = priors[0]
        if step.relation in {"corrects", "narrows", "supersedes"}:
            for prior_index, prior in enumerate(priors):
                if (
                    successor.target != prior.target
                    or successor.perspective != prior.perspective
                    or successor.content_type != prior.content_type
                ):
                    issues.append(f"{path}.predecessor_ids[{prior_index}].successor_shape.mismatch")
            if step.relation == "corrects" and successor.scope != first.scope:
                issues.append(path + ".successor.scope.correction_mismatch")
            if step.relation == "narrows" and (
                first.scope is not None
                or
                not isinstance(successor.scope, str)
                or not successor.scope.strip()
            ):
                issues.append(path + ".successor.scope.not_narrowed")
        elif step.relation in {"contradicts", "reaffirms"}:
            _validate_versioned_evidence_change(step, path, first, successor, issues)
        elif step.relation == "disagrees_with":
            if successor.target != first.target or successor.content_type != first.content_type:
                issues.append(path + ".successor.disagreement_shape.mismatch")
            if successor.perspective == first.perspective:
                issues.append(path + ".successor.perspective.must_differ")


def _apply_cognition_updates(
    base: MemoryWorldGraph,
    preview: MemoryWorldGraph,
    updates: object,
    eligible: set[str],
    issues: list[str],
) -> set[str]:
    """Apply same-ID evidence versions to the isolated preview.

    These versions are reserved for contradiction/reaffirmation.  They retain
    the cognition's semantic identity while appending reviewed Evidence and
    recomputing confidence.  The corresponding evolution step performs the
    stricter before/after comparison after this structural pass.
    """

    if type(updates) is not tuple:
        issues.append("cognition_updates.not_tuple")
        return set()
    updated_ids: set[str] = set()
    new_ids = {item.id for item in preview.cognitions.values() if item.id not in base.cognitions}
    for index, update in enumerate(cast(tuple[object, ...], updates)):
        path = f"cognition_update[{index}]"
        if not isinstance(update, WorldCognition):
            issues.append(path + ".invalid_type")
            continue
        if not isinstance(update.id, str) or not update.id.strip():
            issues.append(path + ".id.invalid")
            continue
        if update.id in updated_ids:
            issues.append(path + ".id.duplicate")
            continue
        updated_ids.add(update.id)
        if update.id in new_ids or update.id not in base.cognitions:
            issues.append(path + ".id.not_existing")
            continue
        if update.world_id != base.world.world_id:
            issues.append(path + ".world_id.mismatch")
        if not _target_exists(preview, update.target):
            issues.append(path + ".target.dangling")
        perspective = update.perspective
        if not isinstance(perspective, Perspective):
            issues.append(path + ".perspective.invalid_type")
        else:
            if any(holder_id not in preview.entities for holder_id in perspective.holder_entity_ids):
                issues.append(path + ".perspective.holder.dangling")
        if not isinstance(update.content, str) or not update.content.strip():
            issues.append(path + ".content.invalid")
        if type(update.confidence) is not int or not 0 <= update.confidence <= 1000:
            issues.append(path + ".confidence.invalid")
        if type(update.sources) is not tuple or not update.sources:
            issues.append(path + ".sources.invalid")
        else:
            evidence_ids: set[str] = set()
            has_support = False
            for source_index, source in enumerate(update.sources):
                source_path = f"{path}.sources[{source_index}]"
                if not isinstance(source, EvidenceLink):
                    issues.append(source_path + ".invalid_type")
                    continue
                if source.relation == "support":
                    has_support = True
                if source.evidence_id in evidence_ids:
                    issues.append(source_path + ".evidence_id.duplicate")
                evidence_ids.add(source.evidence_id)
                if source.evidence_id not in eligible:
                    issues.append(source_path + ".evidence_id.not_eligible")
            if not has_support:
                issues.append(path + ".sources.support.empty")
        preview.cognitions[update.id] = update
    return updated_ids


def _validate_step_shape(
    step: EvolutionStep,
    path: str,
    eligible: set[str],
    plan_evidence_ids: set[str],
    issues: list[str],
) -> None:
    if not isinstance(step.id, str) or not step.id.startswith("evolution:") or not step.id.strip():
        issues.append(path + ".id.invalid")
    if type(step.predecessor_ids) is not tuple:
        issues.append(path + ".predecessor_ids.not_tuple")
    if type(step.successor_ids) is not tuple:
        issues.append(path + ".successor_ids.not_tuple")
    if type(step.evidence_ids) is not tuple:
        issues.append(path + ".evidence_ids.not_tuple")
    for name, values in (
        ("predecessor_ids", step.predecessor_ids),
        ("successor_ids", step.successor_ids),
        ("evidence_ids", step.evidence_ids),
    ):
        if any(not isinstance(value, str) or not value.strip() for value in values):
            issues.append(path + f".{name}.invalid")
        if len(values) != len(set(values)):
            issues.append(path + f".{name}.duplicate")
    if not step.evidence_ids:
        issues.append(path + ".evidence_ids.empty")
    if not set(step.evidence_ids) <= eligible:
        issues.append(path + ".evidence_ids.not_eligible")
    if not set(step.evidence_ids) <= plan_evidence_ids:
        issues.append(path + ".evidence_ids.not_current_delta")
    _iso_ms(step.effective_at, path + ".effective_at", issues)


def _validate_successor_evidence(
    successor: WorldCognition,
    step: EvolutionStep,
    path: str,
    issues: list[str],
) -> None:
    support_ids = {
        source.evidence_id
        for source in successor.sources
        if isinstance(source, EvidenceLink) and source.relation == "support"
    }
    all_ids = {source.evidence_id for source in successor.sources if isinstance(source, EvidenceLink)}
    required = set(step.evidence_ids)
    expected_relation = "contradict" if step.relation == "contradicts" else "support"
    eligible_ids = {
        source.evidence_id
        for source in successor.sources
        if isinstance(source, EvidenceLink) and source.relation == expected_relation
    }
    if not required <= all_ids:
        issues.append(path + ".successor.evidence.missing")
    if not required <= eligible_ids:
        issues.append(path + f".successor.evidence.{expected_relation}.required")
    if step.kind == "relationship_state" and not required <= support_ids:
        issues.append(path + ".successor.evidence.support.required")


def _validate_versioned_evidence_change(
    step: EvolutionStep,
    path: str,
    prior: WorldCognition,
    successor: WorldCognition,
    issues: list[str],
) -> None:
    if (
        successor.target != prior.target
        or successor.perspective != prior.perspective
        or successor.content != prior.content
        or successor.content_type != prior.content_type
        or successor.formed_by != prior.formed_by
        or successor.scope != prior.scope
        or successor.valid_at != prior.valid_at
        or successor.invalid_at != prior.invalid_at
        or successor.structured_claim != prior.structured_claim
    ):
        issues.append(path + ".successor.version_shape.mismatch")
    relation: EvidenceRelation = "contradict" if step.relation == "contradicts" else "support"
    expected_new = tuple(EvidenceLink(evidence_id, relation) for evidence_id in step.evidence_ids)
    if successor.sources != prior.sources + expected_new:
        issues.append(path + ".successor.sources.version_mismatch")
    support_count = sum(source.relation == "support" for source in successor.sources)
    contradict_count = sum(source.relation == "contradict" for source in successor.sources)
    expected_confidence = compute_confidence(
        ConfidenceInputs(successor.content_type, successor.formed_by, support_count, contradict_count)
    )
    expected_status = derive_cred_status(
        expected_confidence,
        contradict_count,
        successor.content_type,
        support_count=support_count,
    )
    if successor.confidence != expected_confidence:
        issues.append(path + ".successor.confidence.mismatch")
    if successor.cred_status != expected_status:
        issues.append(path + ".successor.cred_status.mismatch")


def _consume_prior(
    prior_id: str,
    path: str,
    superseded: set[str],
    consumed: set[str],
    issues: list[str],
) -> None:
    if prior_id in superseded:
        issues.append(path + ".predecessor.already_superseded")
    if prior_id in consumed:
        issues.append(path + ".predecessor.consumed_twice")
    consumed.add(prior_id)


def _target_exists(graph: MemoryWorldGraph, target: MemoryTarget) -> bool:
    if not isinstance(target, MemoryTarget):
        return False
    if target.kind == "world":
        return target.id == graph.world.world_id
    if target.kind == "entity":
        return target.id in graph.entities
    if target.kind == "relationship":
        return target.id in graph.relationships
    if target.kind == "event":
        return target.id in graph.events
    return False


def _relationship_identity(relationship: Any) -> tuple[str, str, str, bool]:
    source = relationship.source_entity_id
    target = relationship.target_entity_id
    if relationship.bidirectional and target < source:
        source, target = target, source
    return (
        source,
        target,
        " ".join(relationship.relation_type.strip().casefold().split()),
        relationship.bidirectional,
    )


def _relationship_has_ended(
    relationship: Any,
    effective_at: str,
    path: str,
    issues: list[str],
) -> bool:
    if relationship.status == "ended":
        return True
    if relationship.valid_to is None:
        return False
    ended_ms = _iso_ms(relationship.valid_to, path + ".predecessor.valid_to", issues)
    effective_ms = _iso_ms(effective_at, path + ".effective_at", issues)
    return ended_ms is not None and effective_ms is not None and ended_ms <= effective_ms


def _relationship_is_current(
    relationship: Any,
    effective_at: str,
    path: str,
    issues: list[str],
) -> bool:
    if relationship.status not in {None, "active"} or relationship.valid_to is not None:
        return False
    if relationship.valid_from is None:
        return True
    started_ms = _iso_ms(relationship.valid_from, path + ".successor.valid_from", issues)
    effective_ms = _iso_ms(effective_at, path + ".effective_at", issues)
    return started_ms is not None and effective_ms is not None and started_ms <= effective_ms


def _validated_string_set(values: Iterable[str], name: str) -> set[str]:
    try:
        items = tuple(values)
    except TypeError:
        raise WorldEvolutionValidationError((name + ".not_iterable",)) from None
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise WorldEvolutionValidationError((name + ".invalid",))
    return set(items)


def _iso_ms(value: object, path: str, issues: list[str]) -> int | None:
    if not isinstance(value, str) or not value.strip():
        issues.append(path + ".invalid")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        issues.append(path + ".invalid")
        return None
    if parsed.tzinfo is None:
        issues.append(path + ".timezone.required")
        return None
    return parse_iso_ms(value)


def superseding_cognition_pairs(plan: WorldEvolutionPlan) -> tuple[tuple[str, str, str], ...]:
    """Return the exact legacy-current-view edges an accepted plan must store."""

    pairs: list[tuple[str, str, str]] = []
    for step in plan.steps:
        supersedes = (
            step.kind == "relationship_state" and bool(step.predecessor_ids)
        ) or (
            step.kind == "cognition_change" and step.relation in _SUPERSEDING_COGNITION_RELATIONS
        )
        if not supersedes or len(step.successor_ids) != 1:
            continue
        for prior_id in step.predecessor_ids:
            pairs.append((prior_id, step.successor_ids[0], step.relation))
    return tuple(pairs)


def relationship_successor_pairs(
    steps: Iterable[AcceptedEvolutionStep] | Iterable[EvolutionStep],
) -> tuple[tuple[str, str], ...]:
    """Return accepted predecessor -> successor relationship lineage edges."""

    pairs: list[tuple[str, str]] = []
    for item in steps:
        step = item.step if isinstance(item, AcceptedEvolutionStep) else item
        if (
            step.kind == "relationship_successor"
            and step.relation == "reestablished"
            and len(step.predecessor_ids) == 1
            and len(step.successor_ids) == 1
        ):
            pairs.append((step.predecessor_ids[0], step.successor_ids[0]))
    return tuple(pairs)


def accepted_historical_relationship_ids(
    accepted_steps: Iterable[AcceptedEvolutionStep],
) -> frozenset[str]:
    """Project relationship IDs displaced or explicitly ended by accepted steps."""

    accepted = tuple(accepted_steps)
    historical = {
        predecessor_id
        for predecessor_id, _ in relationship_successor_pairs(accepted)
    }
    latest_state: dict[str, AcceptedEvolutionStep] = {}
    for item in accepted:
        step = item.step
        if step.kind != "relationship_state" or step.subject.kind != "relationship":
            continue
        prior = latest_state.get(step.subject.id)
        if prior is None or (item.revision, step.id) > (
            prior.revision,
            prior.step.id,
        ):
            latest_state[step.subject.id] = item
    historical.update(
        relationship_id
        for relationship_id, item in latest_state.items()
        if item.step.relation == "ended"
    )
    return frozenset(historical)


def historical_relationship_ids(
    graph: MemoryWorldGraph,
    accepted_steps: Iterable[AcceptedEvolutionStep],
) -> frozenset[str]:
    """Project the append-only graph into its non-current relationship IDs."""

    accepted = tuple(accepted_steps)
    historical = {
        predecessor_id
        for predecessor_id, _ in relationship_successor_pairs(accepted)
    }
    latest_state: dict[str, AcceptedEvolutionStep] = {}
    for item in accepted:
        step = item.step
        if step.kind != "relationship_state" or step.subject.kind != "relationship":
            continue
        prior = latest_state.get(step.subject.id)
        if prior is None or (item.revision, step.id) > (
            prior.revision,
            prior.step.id,
        ):
            latest_state[step.subject.id] = item
    historical.update(
        relationship_id
        for relationship_id, item in latest_state.items()
        if item.step.relation == "ended"
    )
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    historical.update(
        relationship.id
        for relationship in graph.relationships.values()
        if relationship.id not in latest_state
        and (
            relationship.status == "ended"
            or (
                relationship.valid_to is not None
                and (
                    valid_to := _try_iso_ms(relationship.valid_to)
                ) is not None
                and valid_to <= now_ms
            )
        )
    )
    return frozenset(historical)


def current_relationship_ids(
    graph: MemoryWorldGraph,
    accepted_steps: Iterable[AcceptedEvolutionStep],
) -> frozenset[str]:
    """Project relationships current now, with accepted evolution as authority."""

    accepted = tuple(accepted_steps)
    permanently_historical = set(
        accepted_historical_relationship_ids(accepted)
    )
    accepted_successors = {
        successor_id
        for _, successor_id in relationship_successor_pairs(accepted)
    }
    latest_state: dict[str, AcceptedEvolutionStep] = {}
    for item in accepted:
        step = item.step
        if step.kind != "relationship_state" or step.subject.kind != "relationship":
            continue
        prior = latest_state.get(step.subject.id)
        if prior is None or (item.revision, step.id) > (
            prior.revision,
            prior.step.id,
        ):
            latest_state[step.subject.id] = item
    at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    current: set[str] = set()
    for relationship in graph.relationships.values():
        if relationship.id in permanently_historical:
            continue
        if relationship.id in accepted_successors or relationship.id in latest_state:
            current.add(relationship.id)
            continue
        if relationship.status not in {None, "active"}:
            continue
        valid_from = (
            _try_iso_ms(relationship.valid_from)
            if relationship.valid_from is not None
            else None
        )
        valid_to = (
            _try_iso_ms(relationship.valid_to)
            if relationship.valid_to is not None
            else None
        )
        if relationship.valid_from is not None and valid_from is None:
            continue
        if relationship.valid_to is not None and valid_to is None:
            continue
        if valid_from is not None and valid_from > at_ms:
            continue
        if valid_to is not None and valid_to <= at_ms:
            continue
        current.add(relationship.id)
    return frozenset(current)


def _try_iso_ms(value: str) -> int | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parse_iso_ms(value)


def evolution_plan_to_data(plan: WorldEvolutionPlan) -> dict[str, object]:
    """Encode a plan into the closed canonical shape stored in a review row."""

    if not isinstance(plan, WorldEvolutionPlan):
        raise TypeError("plan must be a WorldEvolutionPlan")
    from .loop import _cognition_to_data, _delta_to_data

    return {
        "delta": _delta_to_data(plan.delta),
        "steps": [evolution_step_to_data(step) for step in plan.steps],
        "cognition_updates": [_cognition_to_data(item) for item in plan.cognition_updates],
    }


def evolution_plan_from_data(raw: Mapping[str, object]) -> WorldEvolutionPlan:
    """Decode a stored plan and reject missing, additional or malformed fields."""

    if not isinstance(raw, Mapping) or set(raw) != {"delta", "steps", "cognition_updates"}:
        raise WorldEvolutionValidationError(("plan.fields.invalid",))
    delta_raw = raw["delta"]
    steps_raw = raw["steps"]
    updates_raw = raw["cognition_updates"]
    if (
        not isinstance(delta_raw, Mapping)
        or not isinstance(steps_raw, list)
        or not isinstance(updates_raw, list)
    ):
        raise WorldEvolutionValidationError(("plan.shape.invalid",))
    from .loop import _cognition_from_data, _delta_from_data

    steps = [
        evolution_step_from_data(item, path=f"plan.steps[{index}]")
        for index, item in enumerate(steps_raw)
    ]
    try:
        updates = tuple(
            _cognition_from_data(cast(Mapping[str, object], item))
            for item in updates_raw
            if isinstance(item, Mapping)
        )
    except (KeyError, TypeError, ValueError) as error:
        raise WorldEvolutionValidationError(("plan.cognition_updates.invalid",)) from error
    if len(updates) != len(updates_raw):
        raise WorldEvolutionValidationError(("plan.cognition_updates.invalid",))
    return WorldEvolutionPlan(_delta_from_data(delta_raw), tuple(steps), updates)


def evolution_step_to_data(step: EvolutionStep) -> dict[str, object]:
    """Encode one closed evolution edge for a hash-bound proposal payload."""

    if not isinstance(step, EvolutionStep):
        raise TypeError("step must be an EvolutionStep")
    return {
        "id": step.id,
        "kind": step.kind,
        "relation": step.relation,
        "subject": {"kind": step.subject.kind, "id": step.subject.id},
        "predecessor_ids": list(step.predecessor_ids),
        "successor_ids": list(step.successor_ids),
        "effective_at": step.effective_at,
        "evidence_ids": list(step.evidence_ids),
    }


def evolution_step_from_data(
    raw: object,
    *,
    path: str = "step",
) -> EvolutionStep:
    """Decode one closed evolution edge without granting it write authority."""

    expected_fields = {
        "id",
        "kind",
        "relation",
        "subject",
        "predecessor_ids",
        "successor_ids",
        "effective_at",
        "evidence_ids",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected_fields:
        raise WorldEvolutionValidationError((path + ".fields.invalid",))
    subject_raw = raw["subject"]
    if not isinstance(subject_raw, Mapping) or set(subject_raw) != {"kind", "id"}:
        raise WorldEvolutionValidationError((path + ".subject.invalid",))
    predecessor_ids = raw["predecessor_ids"]
    successor_ids = raw["successor_ids"]
    evidence_ids = raw["evidence_ids"]
    scalar_values = (
        raw["id"],
        raw["kind"],
        raw["relation"],
        raw["effective_at"],
        subject_raw["kind"],
        subject_raw["id"],
    )
    if any(type(value) is not str for value in scalar_values):
        raise WorldEvolutionValidationError((path + ".scalar.invalid",))
    if any(
        not isinstance(values, list)
        or any(type(value) is not str for value in values)
        for values in (predecessor_ids, successor_ids, evidence_ids)
    ):
        raise WorldEvolutionValidationError((path + ".ids.invalid",))
    return EvolutionStep(
        cast(str, raw["id"]),
        cast(EvolutionKind, raw["kind"]),
        cast(EvolutionRelation, raw["relation"]),
        MemoryTarget(cast(Any, subject_raw["kind"]), cast(str, subject_raw["id"])),
        tuple(cast(list[str], predecessor_ids)),
        tuple(cast(list[str], successor_ids)),
        cast(str, raw["effective_at"]),
        tuple(cast(list[str], evidence_ids)),
    )


def project_relationship_state(
    graph: MemoryWorldGraph,
    accepted_steps: Iterable[AcceptedEvolutionStep],
    relationship_id: str,
) -> RelationshipStateProjection | None:
    """Derive the latest accepted state without consulting ``Relationship.status``."""

    if relationship_id not in graph.relationships:
        raise WorldEvolutionValidationError(("relationship_id.dangling",))
    candidates = [
        accepted
        for accepted in accepted_steps
        if accepted.step.kind == "relationship_state"
        and accepted.step.subject == MemoryTarget("relationship", relationship_id)
    ]
    if not candidates:
        return None
    accepted = max(candidates, key=lambda item: (item.revision, item.step.id))
    step = accepted.step
    if len(step.successor_ids) != 1 or step.relation not in _RELATIONSHIP_STATES:
        raise WorldEvolutionValidationError(("relationship_state.accepted.invalid",))
    cognition = graph.cognitions.get(step.successor_ids[0])
    if cognition is None or cognition.target != step.subject or cognition.scope != "relationship_state":
        raise WorldEvolutionValidationError(("relationship_state.cognition.invalid",))
    return RelationshipStateProjection(
        relationship_id,
        step.relation,
        cognition.id,
        step.effective_at,
        accepted.revision,
        step.id,
    )


def relationship_event_chain(
    graph: MemoryWorldGraph,
    accepted_steps: Iterable[AcceptedEvolutionStep],
    relationship_id: str,
) -> tuple[WorldEvent, ...]:
    """Reconstruct one explicit, non-branching accepted event chain."""

    if relationship_id not in graph.relationships:
        raise WorldEvolutionValidationError(("relationship_id.dangling",))
    steps = [
        accepted.step
        for accepted in accepted_steps
        if accepted.step.kind == "event_link"
        and accepted.step.subject == MemoryTarget("relationship", relationship_id)
    ]
    if not steps:
        return ()
    forward: dict[str, str] = {}
    backward: dict[str, str] = {}
    issues: list[str] = []
    for index, step in enumerate(steps):
        path = f"accepted_event_step[{index}]"
        if len(step.predecessor_ids) != 1 or len(step.successor_ids) != 1:
            issues.append(path + ".cardinality")
            continue
        prior_id, successor_id = step.predecessor_ids[0], step.successor_ids[0]
        if prior_id in forward and forward[prior_id] != successor_id:
            issues.append(path + ".branching")
        if successor_id in backward and backward[successor_id] != prior_id:
            issues.append(path + ".merging")
        forward[prior_id] = successor_id
        backward[successor_id] = prior_id
    starts = set(forward) - set(backward)
    if len(starts) != 1:
        issues.append("event_chain.start.invalid")
    if issues:
        raise WorldEvolutionValidationError(tuple(issues))
    current = next(iter(starts))
    ordered_ids = [current]
    seen: set[str] = set()
    while current in forward:
        if current in seen:
            raise WorldEvolutionValidationError(("event_chain.cycle",))
        seen.add(current)
        current = forward[current]
        ordered_ids.append(current)
    if len(seen) != len(forward):
        raise WorldEvolutionValidationError(("event_chain.disconnected",))
    result: list[WorldEvent] = []
    previous_ms: int | None = None
    for event_id in ordered_ids:
        event = graph.events.get(event_id)
        if event is None or relationship_id not in event.relationship_ids:
            raise WorldEvolutionValidationError(("event_chain.event.invalid",))
        event_ms = _iso_ms(event.occurred_at, "event_chain.occurred_at", issues)
        if event_ms is None:
            raise WorldEvolutionValidationError(tuple(issues))
        if previous_ms is not None and event_ms <= previous_ms:
            raise WorldEvolutionValidationError(("event_chain.temporal_order.invalid",))
        previous_ms = event_ms
        result.append(event)
    return tuple(result)


def project_cognition_lifecycle(
    cognition: WorldCognition,
    *,
    is_superseded: bool,
    now: str,
    last_corroborated_at: str,
) -> CognitionLifecycleProjection:
    """Derive decay, expiration and salience without writing the graph.

    ``last_corroborated_at`` is an explicit ledger/proposal time supplied by
    the caller.  ``valid_at`` remains a claimed semantic validity interval and
    is deliberately not repurposed as a storage update timestamp.
    """

    issues: list[str] = []
    now_ms = _iso_ms(now, "now", issues)
    if now_ms is None:
        raise WorldEvolutionValidationError(tuple(issues))
    corroborated_ms = _iso_ms(last_corroborated_at, "last_corroborated_at", issues)
    if corroborated_ms is not None and corroborated_ms > now_ms:
        issues.append("last_corroborated_at.after_now")
    explicit_expired = False
    if cognition.invalid_at is not None:
        invalid_ms = _iso_ms(cognition.invalid_at, "cognition.invalid_at", issues)
        if invalid_ms is not None:
            explicit_expired = invalid_ms <= now_ms
    if issues:
        raise WorldEvolutionValidationError(tuple(issues))
    natural_expired = False
    expire_days = CONFIG.expire_after_days.get(cognition.content_type)
    if expire_days is not None:
        natural_expired = (now_ms - cast(int, corroborated_ms)) / _DAY_MS > expire_days
    expired = explicit_expired or natural_expired
    effective = effective_confidence(
        cognition.confidence,
        cognition.content_type,
        float(cast(int, corroborated_ms)),
        float(now_ms),
    )
    current = not is_superseded and not expired
    return CognitionLifecycleProjection(
        cognition.id,
        current,
        expired,
        effective,
        effective if current else 0,
    )


__all__ = [
    "AcceptedEvolutionStep",
    "CognitionLifecycleProjection",
    "EvolutionKind",
    "EvolutionRelation",
    "EvolutionStep",
    "RelationshipStateProjection",
    "WorldEvolutionPlan",
    "WorldEvolutionValidationError",
    "evolution_plan_from_data",
    "evolution_plan_to_data",
    "evolution_step_from_data",
    "evolution_step_to_data",
    "accepted_historical_relationship_ids",
    "current_relationship_ids",
    "historical_relationship_ids",
    "project_cognition_lifecycle",
    "project_relationship_state",
    "relationship_successor_pairs",
    "relationship_event_chain",
    "superseding_cognition_pairs",
]
