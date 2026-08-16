"""Validated, create-only changesets for a personal-memory world.

``WorldDelta`` is an in-memory boundary between an untrusted producer (for
example an extraction step) and ``MemoryWorldGraph``.  It deliberately has no
storage, update, or model concerns: a delta either fully describes a valid set
of new graph records, or it is rejected before any graph can be changed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import re
from typing import Any, Iterable, Literal, cast

from ..confidence import compute_confidence, derive_cred_status
from ..types import ConfidenceInputs, EvidenceLink, FormedBy
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
from .semantics import is_interpersonal_conflict_type

_CONTENT_TYPES = frozenset({"fact", "preference", "goal", "project", "state", "trait", "hypothesis", "trend"})
_FORMED_BY = frozenset({"stated", "observed", "ruled", "confirmed", "inferred"})
_CRED_STATUSES = frozenset({"candidate", "low", "limited", "stable", "conflicted", "contested"})
_EVIDENCE_RELATIONS = frozenset({"support", "contradict"})
_TARGET_KINDS = frozenset({"world", "entity", "relationship", "event"})
_PERSPECTIVE_KINDS = frozenset({"entity", "joint", "system"})
_STRUCTURED_STATEMENT_KINDS = frozenset(
    {"attribute", "evaluation", "relationship_statement", "event_statement", "naming", "alias"}
)
_CLAIM_POLARITIES = frozenset({"assert", "negate"})
_CLAIM_EPISTEMIC_STATUSES = frozenset({"asserted", "owner_imagined", "reported", "uncertain"})
_MAX_COGNITION_SOURCES = 4
_FORMATION_ORIGINS = frozenset({"user_stated", "assistant_proposed"})
_FORMATION_RESPONSE_ACTS = frozenset({"affirm", "negate", "select", "elaborate", "ask", "none", "other"})
_FORMATION_DECISIONS = frozenset(
    {"exact_user_claim", "assistant_confirmation", "inference_grounding", "user_negation", "unverified"}
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ClaimSpan:
    """A source-local, auditable span supporting one formation decision."""

    start_codepoint: int
    end_codepoint: int
    source_content_sha256: str
    claim_sha256: str


@dataclass(frozen=True, slots=True)
class FormationSourceTrace:
    """The local formation decision for one cognition evidence link."""

    evidence_id: str
    relation: Literal["support", "contradict"]
    proposition_origin_proposal: Literal["user_stated", "assistant_proposed"]
    response_act_proposal: Literal["affirm", "negate", "select", "elaborate", "ask", "none", "other"]
    claim_span: ClaimSpan
    preceding_assistant_turn_id: str | None
    preceding_assistant_content_sha256: str | None
    local_origin_decision: Literal[
        "exact_user_claim", "assistant_confirmation", "inference_grounding", "user_negation", "unverified"
    ]
    decision_code: str


@dataclass(frozen=True, slots=True)
class FormationContentBinding:
    """A local, auditable relationship-side content binding for an inference.

    Bindings annotate an inferred relationship cognition without creating a
    graph record or an additional source of epistemic support.  They carry
    only an entity reference, evidence reference, and verified claim span;
    caller-owned segment identifiers deliberately never enter the sidecar.
    """

    semantic_role: Literal["relationship_side"]
    about_entity_id: str
    evidence_id: str
    claim_span: ClaimSpan


@dataclass(frozen=True, slots=True)
class FormationTrace:
    """Mandatory sidecar proving how one new cognition was formed.

    It is intentionally validated at the delta boundary and never persisted in
    ``MemoryWorldGraph``.  A graph therefore contains domain records only,
    while callers keep this audit material with the accepted delta.
    """

    cognition_id: str
    model_inferred_proposal: bool
    sources: tuple[FormationSourceTrace, ...]
    derived_formed_by: FormedBy
    raw_support_count: int
    effective_support_count: int
    contradict_count: int
    # Keep content bindings keyword-only so inserting this audit detail never
    # rebinds established positional FormationTrace call sites.
    content_bindings: tuple[FormationContentBinding, ...] = field(default=(), kw_only=True)


@dataclass(frozen=True, slots=True)
class UnresolvedReference:
    """A mention that cannot yet safely be resolved to a world object."""

    mention: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SemanticUncertainty:
    """A semantic ambiguity retained instead of being silently guessed away."""

    detail: str
    evidence_ids: tuple[str, ...]


class WorldDeltaValidationError(ValueError):
    """Raised when a delta is not safe to apply.

    ``issues`` contains stable, machine-readable issue codes.  Codes may carry
    a collection index (``event[0]``) so callers can report the rejected input
    without parsing prose from an exception message.
    """

    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = issues
        super().__init__("WorldDelta validation failed: " + ", ".join(issues))


@dataclass(frozen=True, slots=True)
class WorldDelta:
    """A create-only, all-or-nothing addition to one ``MemoryWorldGraph``."""

    world_id: str
    source_evidence_ids: tuple[str, ...]
    new_entities: tuple[Entity, ...] = ()
    new_relationships: tuple[Relationship, ...] = ()
    new_events: tuple[WorldEvent, ...] = ()
    new_cognitions: tuple[WorldCognition, ...] = ()
    # Keep the audit sidecar keyword-only so inserting it does not silently
    # rebind the existing unresolved/uncertainty positional arguments.
    formation_traces: tuple[FormationTrace, ...] = field(default=(), kw_only=True)
    unresolved_references: tuple[UnresolvedReference, ...] = ()
    semantic_uncertainties: tuple[SemanticUncertainty, ...] = ()

    def validate_against(
        self,
        base: MemoryWorldGraph,
        eligible_evidence_ids: Iterable[str],
    ) -> None:
        """Validate this delta completely against ``base`` or raise once.

        The caller-owned allowlist is the sole authority for evidence.  Delta
        provenance is checked *against* it and never widens it.
        """
        issues: list[str] = []

        if not isinstance(base, MemoryWorldGraph):
            raise TypeError("base must be a MemoryWorldGraph")
        self._validate_tuple_fields(issues)
        if issues:
            raise WorldDeltaValidationError(tuple(issues))

        eligible: set[str] = set()
        try:
            for index, evidence_id in enumerate(eligible_evidence_ids):
                if _non_empty_id(evidence_id):
                    eligible.add(evidence_id)
                else:
                    issues.append(f"eligible_evidence_ids[{index}].invalid")
        except TypeError:
            raise TypeError("eligible_evidence_ids must be iterable") from None

        self._validate_world(base, issues)
        self._validate_evidence_ids("source_evidence_ids", self.source_evidence_ids, eligible, issues, required=True)
        self._validate_records(base, eligible, issues)

        if issues:
            raise WorldDeltaValidationError(tuple(issues))

    def apply_to(
        self,
        base: MemoryWorldGraph,
        eligible_evidence_ids: Iterable[str],
    ) -> MemoryWorldGraph:
        """Return a new graph with this fully validated delta applied.

        Validation precedes construction and all writes.  The records are
        frozen, so copying the dictionaries is sufficient to isolate the base
        graph while preserving its immutable world objects.
        """
        self.validate_against(base, eligible_evidence_ids)
        result = MemoryWorldGraph(
            world=base.world,
            entities=base.entities.copy(),
            relationships=base.relationships.copy(),
            events=base.events.copy(),
            cognitions=base.cognitions.copy(),
        )
        for entity in self.new_entities:
            result.add_entity(entity)
        for relationship in self.new_relationships:
            result.add_relationship(relationship)
        for event in self.new_events:
            result.add_event(event)
        for cognition in self.new_cognitions:
            result.add_cognition(cognition)
        return result

    def _validate_tuple_fields(self, issues: list[str]) -> None:
        for name, value in (
            ("source_evidence_ids", self.source_evidence_ids),
            ("new_entities", self.new_entities),
            ("new_relationships", self.new_relationships),
            ("new_events", self.new_events),
            ("new_cognitions", self.new_cognitions),
            ("formation_traces", self.formation_traces),
            ("unresolved_references", self.unresolved_references),
            ("semantic_uncertainties", self.semantic_uncertainties),
        ):
            if type(value) is not tuple:
                issues.append(f"{name}.not_tuple")

    def _validate_world(self, base: MemoryWorldGraph, issues: list[str]) -> None:
        if not isinstance(base.world, PersonalWorld):
            issues.append("base.world.invalid_type")
            return
        if not _non_empty_id(self.world_id):
            issues.append("world_id.invalid")
        if not _non_empty_id(base.world.world_id):
            issues.append("base.world_id.invalid")
        if self.world_id != base.world.world_id:
            issues.append("world_id.mismatch")
        if not _non_empty_id(base.world.owner_entity_id) or base.world.owner_entity_id not in base.entities:
            issues.append("base.owner_entity_id.dangling")

    def _validate_records(
        self,
        base: MemoryWorldGraph,
        eligible: set[str],
        issues: list[str],
    ) -> None:
        entity_ids = set(base.entities)
        relationship_ids = set(base.relationships)
        event_ids = set(base.events)
        cognition_ids = set(base.cognitions)
        all_existing_ids = entity_ids | relationship_ids | event_ids | cognition_ids
        all_new_ids: set[str] = set()

        for kind, records in (
            ("entity", self.new_entities),
            ("relationship", self.new_relationships),
            ("event", self.new_events),
            ("cognition", self.new_cognitions),
        ):
            for index, record in enumerate(records):
                path = f"{kind}[{index}]"
                if not isinstance(record, _RECORD_TYPES[kind]):
                    issues.append(f"{path}.invalid_type")
                    continue
                world_record = cast(Entity | Relationship | WorldEvent | WorldCognition, record)
                record_id = world_record.id
                if not _non_empty_id(record_id):
                    issues.append(f"{path}.id.invalid")
                elif record_id in all_existing_ids:
                    issues.append(f"{path}.id.conflicts_with_base")
                elif record_id in all_new_ids:
                    issues.append(f"{path}.id.duplicate")
                else:
                    all_new_ids.add(record_id)
                if world_record.world_id != self.world_id:
                    issues.append(f"{path}.world_id.mismatch")
                self._validate_id_namespace(kind, world_record, path, issues)
                self._validate_record_shape(path, world_record, issues)

        known_entities = entity_ids | {record.id for record in self.new_entities if isinstance(record, Entity) and _non_empty_id(record.id)}
        known_relationship_records = {
            relationship_id: relationship
            for relationship_id, relationship in base.relationships.items()
            if isinstance(relationship, Relationship)
        }
        known_relationship_records.update(
            {
                record.id: record
                for record in self.new_relationships
                if isinstance(record, Relationship) and _non_empty_id(record.id)
            }
        )
        known_relationships = set(known_relationship_records)
        known_events = event_ids | {record.id for record in self.new_events if isinstance(record, WorldEvent) and _non_empty_id(record.id)}

        for index, relationship in enumerate(self.new_relationships):
            path = f"relationship[{index}]"
            if not isinstance(relationship, Relationship):
                continue
            self._validate_entity_reference(path + ".source_entity_id", relationship.source_entity_id, known_entities, issues)
            self._validate_entity_reference(path + ".target_entity_id", relationship.target_entity_id, known_entities, issues)

        for index, event in enumerate(self.new_events):
            path = f"event[{index}]"
            if not isinstance(event, WorldEvent):
                continue
            self._validate_required_str(path + ".event_type", event.event_type, issues)
            self._validate_required_str(path + ".summary", event.summary, issues)
            self._validate_required_str(path + ".occurred_at", event.occurred_at, issues)
            for participant_index, participant in enumerate(self._tuple_items(path + ".participants", event.participants, issues)):
                if not isinstance(participant, EventParticipant):
                    issues.append(f"{path}.participants[{participant_index}].invalid_type")
                elif not _non_empty_id(participant.entity_id) or participant.entity_id not in known_entities:
                    issues.append(f"{path}.participants[{participant_index}].entity_id.dangling")
                elif participant.role is not None and not isinstance(participant.role, str):
                    issues.append(f"{path}.participants[{participant_index}].role.invalid")
            for entity_index, entity_id in enumerate(self._tuple_items(path + ".related_entity_ids", event.related_entity_ids, issues)):
                self._validate_entity_reference(f"{path}.related_entity_ids[{entity_index}]", entity_id, known_entities, issues)
            for relationship_index, relationship_id in enumerate(self._tuple_items(path + ".relationship_ids", event.relationship_ids, issues)):
                if not _non_empty_id(relationship_id) or relationship_id not in known_relationships:
                    issues.append(f"{path}.relationship_ids[{relationship_index}].dangling")
            for facet_index, facet in enumerate(self._tuple_items(path + ".facets", event.facets, issues)):
                if not isinstance(facet, EventFacet):
                    issues.append(f"{path}.facets[{facet_index}].invalid_type")
                else:
                    self._validate_required_str(f"{path}.facets[{facet_index}].key", facet.key, issues)
                    self._validate_required_str(f"{path}.facets[{facet_index}].value", facet.value, issues)
                    if facet.about_entity_id is not None:
                        self._validate_entity_reference(
                            f"{path}.facets[{facet_index}].about_entity_id", facet.about_entity_id, known_entities, issues
                        )
            self._validate_evidence_ids(path + ".evidence_ids", event.evidence_ids, eligible, issues, required=True)

        for index, cognition in enumerate(self.new_cognitions):
            path = f"cognition[{index}]"
            if not isinstance(cognition, WorldCognition):
                continue
            self._validate_cognition(path, cognition, known_entities, known_relationships, known_events, eligible, issues)

        self._validate_formation_traces(known_relationship_records, base.world.owner_entity_id, issues)
        self._validate_conflict_relationship_projections(
            known_relationship_records,
            base.world.owner_entity_id,
            issues,
        )

        self._validate_issues("unresolved_reference", self.unresolved_references, eligible, issues)
        self._validate_issues("semantic_uncertainty", self.semantic_uncertainties, eligible, issues)

    @staticmethod
    def _validate_entity_reference(path: str, entity_id: Any, known_entities: set[str], issues: list[str]) -> None:
        if not _non_empty_id(entity_id) or entity_id not in known_entities:
            issues.append(path + ".dangling")

    @staticmethod
    def _validate_id_namespace(
        kind: str,
        record: Entity | Relationship | WorldEvent | WorldCognition,
        path: str,
        issues: list[str],
    ) -> None:
        expected_prefix = {
            "relationship": "relationship:",
            "event": "event:",
            "cognition": "cog:",
        }.get(kind)
        if expected_prefix is not None and not record.id.startswith(expected_prefix):
            issues.append(path + ".id.namespace")
        if kind == "entity":
            entity = cast(Entity, record)
            if entity.kind in {"event", "relationship", "cognition", "preference", "trait", "state"}:
                issues.append(path + ".kind.not_entity")
            if record.id.startswith(("event:", "relationship:", "cog:")):
                issues.append(path + ".id.namespace")

    def _validate_cognition(
        self,
        path: str,
        cognition: WorldCognition,
        known_entities: set[str],
        known_relationships: set[str],
        known_events: set[str],
        eligible: set[str],
        issues: list[str],
    ) -> None:
        target = cognition.target
        if not isinstance(target, MemoryTarget):
            issues.append(path + ".target.invalid_type")
        elif not isinstance(target.kind, str) or target.kind not in _TARGET_KINDS:
            issues.append(path + ".target.kind.invalid")
        elif not _non_empty_id(target.id):
            issues.append(path + ".target.id.invalid")
        elif target.kind == "world" and target.id != self.world_id:
            issues.append(path + ".target.id.dangling")
        elif target.kind == "entity" and target.id not in known_entities:
            issues.append(path + ".target.id.dangling")
        elif target.kind == "relationship" and target.id not in known_relationships:
            issues.append(path + ".target.id.dangling")
        elif target.kind == "event" and target.id not in known_events:
            issues.append(path + ".target.id.dangling")

        perspective = cognition.perspective
        holder_ids: tuple[Any, ...] = ()
        if not isinstance(perspective, Perspective):
            issues.append(path + ".perspective.invalid_type")
        else:
            holder_ids = self._tuple_items(path + ".perspective.holder_entity_ids", perspective.holder_entity_ids, issues)
            if not isinstance(perspective.kind, str) or perspective.kind not in _PERSPECTIVE_KINDS:
                issues.append(path + ".perspective.kind.invalid")
            elif perspective.kind == "entity" and len(holder_ids) != 1:
                issues.append(path + ".perspective.holders.invalid")
            elif perspective.kind == "joint" and len(holder_ids) < 2:
                issues.append(path + ".perspective.holders.invalid")
            elif perspective.kind == "system" and holder_ids:
                issues.append(path + ".perspective.holders.invalid")
            for holder_index, holder_id in enumerate(holder_ids):
                self._validate_entity_reference(
                    f"{path}.perspective.holder_entity_ids[{holder_index}]", holder_id, known_entities, issues
                )

        if not isinstance(cognition.content_type, str) or cognition.content_type not in _CONTENT_TYPES:
            issues.append(path + ".content_type.invalid")
        if not isinstance(cognition.formed_by, str) or cognition.formed_by not in _FORMED_BY:
            issues.append(path + ".formed_by.invalid")
        if not isinstance(cognition.cred_status, str) or cognition.cred_status not in _CRED_STATUSES:
            issues.append(path + ".cred_status.invalid")
        if type(cognition.confidence) is not int or not 0 <= cognition.confidence <= 1000:
            issues.append(path + ".confidence.invalid")
        self._validate_required_str(path + ".content", cognition.content, issues)
        self._validate_optional_str(path + ".scope", cognition.scope, issues)
        self._validate_optional_str(path + ".valid_at", cognition.valid_at, issues)
        self._validate_optional_str(path + ".invalid_at", cognition.invalid_at, issues)
        self._validate_structured_claim(path, cognition.structured_claim, perspective, issues)
        sources = self._tuple_items(path + ".sources", cognition.sources, issues)
        if not sources:
            issues.append(path + ".sources.empty")
        elif len(sources) > _MAX_COGNITION_SOURCES:
            issues.append(path + ".sources.too_many")
        source_evidence_ids: set[str] = set()
        has_support_source = False
        for source_index, source in enumerate(sources):
            source_path = f"{path}.sources[{source_index}]"
            if not isinstance(source, EvidenceLink):
                issues.append(source_path + ".invalid_type")
            else:
                if not isinstance(source.relation, str) or source.relation not in _EVIDENCE_RELATIONS:
                    issues.append(source_path + ".relation.invalid")
                elif source.relation == "support":
                    has_support_source = True
                if _non_empty_id(source.evidence_id):
                    if source.evidence_id in source_evidence_ids:
                        issues.append(source_path + ".evidence_id.duplicate")
                    else:
                        source_evidence_ids.add(source.evidence_id)
                self._validate_evidence_id(source_path + ".evidence_id", source.evidence_id, eligible, issues)
        if not has_support_source:
            issues.append(path + ".sources.support.empty")

    def _validate_structured_claim(
        self,
        path: str,
        structured_claim: StructuredClaim | None,
        perspective: Perspective | object,
        issues: list[str],
    ) -> None:
        """Validate the optional semantic envelope without reinterpreting prose.

        The model may propose this closed vocabulary, but accepted-world code
        remains responsible for rejecting malformed values and for preserving
        the epistemic rule that an evaluation cannot become a perspective-free
        system fact.
        """
        if structured_claim is None:
            return
        if not isinstance(structured_claim, StructuredClaim):
            issues.append(path + ".structured_claim.invalid_type")
            return
        if (
            not isinstance(structured_claim.statement_kind, str)
            or structured_claim.statement_kind not in _STRUCTURED_STATEMENT_KINDS
        ):
            issues.append(path + ".structured_claim.statement_kind.invalid")
        self._validate_optional_non_empty_str(path + ".structured_claim.predicate", structured_claim.predicate, issues)
        self._validate_optional_non_empty_str(path + ".structured_claim.value", structured_claim.value, issues)
        if not isinstance(structured_claim.polarity, str) or structured_claim.polarity not in _CLAIM_POLARITIES:
            issues.append(path + ".structured_claim.polarity.invalid")
        if (
            not isinstance(structured_claim.epistemic_status, str)
            or structured_claim.epistemic_status not in _CLAIM_EPISTEMIC_STATUSES
        ):
            issues.append(path + ".structured_claim.epistemic_status.invalid")
        if structured_claim.statement_kind == "evaluation" and (
            not isinstance(perspective, Perspective) or perspective.kind == "system"
        ):
            issues.append(path + ".structured_claim.evaluation.perspective.invalid")

    def _validate_formation_traces(
        self,
        known_relationships: dict[str, Relationship],
        owner_entity_id: str,
        issues: list[str],
    ) -> None:
        """Require one fully auditable formation sidecar per new cognition."""
        cognitions = self._tuple_items("new_cognitions", self.new_cognitions, issues)
        cognition_by_id: dict[str, tuple[int, WorldCognition]] = {}
        for cognition_index, cognition in enumerate(cognitions):
            if isinstance(cognition, WorldCognition) and _non_empty_id(cognition.id):
                cognition_by_id[cognition.id] = (cognition_index, cognition)

        traces = self._tuple_items("formation_traces", self.formation_traces, issues)
        seen_cognition_ids: set[str] = set()
        traced_cognition_ids: set[str] = set()
        trace_by_cognition_id: dict[str, tuple[int, FormationTrace]] = {}
        for trace_index, trace in enumerate(traces):
            path = f"formation_trace[{trace_index}]"
            if not isinstance(trace, FormationTrace):
                issues.append(path + ".invalid_type")
                continue
            trace_cognition_id = trace.cognition_id
            cognition_entry: tuple[int, WorldCognition] | None = None
            if not _non_empty_id(trace_cognition_id):
                issues.append(path + ".cognition_id.invalid")
            else:
                cognition_entry = cognition_by_id.get(trace_cognition_id)
                duplicate_cognition_id = trace_cognition_id in seen_cognition_ids
                if duplicate_cognition_id:
                    issues.append(path + ".cognition_id.duplicate")
                else:
                    seen_cognition_ids.add(trace_cognition_id)
                if cognition_entry is None:
                    issues.append(path + ".cognition_id.not_new")
                elif not duplicate_cognition_id:
                    traced_cognition_ids.add(trace_cognition_id)
                    trace_by_cognition_id[trace_cognition_id] = (trace_index, trace)
            self._validate_formation_trace(path, trace, cognition_entry[1] if cognition_entry is not None else None, issues)

        for cognition_index, cognition in enumerate(cognitions):
            if isinstance(cognition, WorldCognition) and _non_empty_id(cognition.id) and cognition.id not in traced_cognition_ids:
                issues.append(f"cognition[{cognition_index}].formation_trace.missing")

        for trace_index, trace in trace_by_cognition_id.values():
            if trace.model_inferred_proposal is not True:
                continue
            cognition_entry = cognition_by_id.get(trace.cognition_id)
            if cognition_entry is None:
                continue
            cognition = cognition_entry[1]
            if isinstance(cognition.target, MemoryTarget) and cognition.target.kind == "relationship":
                self._validate_inferred_relationship_content_bindings(
                    f"formation_trace[{trace_index}]",
                    trace,
                    cognition,
                    cognition_by_id,
                    trace_by_cognition_id,
                    known_relationships,
                    owner_entity_id,
                    issues,
                )

    def _validate_formation_trace(
        self,
        path: str,
        trace: FormationTrace,
        cognition: WorldCognition | None,
        issues: list[str],
    ) -> None:
        if type(trace.model_inferred_proposal) is not bool:
            issues.append(path + ".model_inferred_proposal.invalid")
        if not isinstance(trace.derived_formed_by, str) or trace.derived_formed_by not in _FORMED_BY:
            issues.append(path + ".derived_formed_by.invalid")
        for count_name in ("raw_support_count", "effective_support_count", "contradict_count"):
            if type(getattr(trace, count_name)) is not int:
                issues.append(path + f".{count_name}.invalid")

        sources = self._tuple_items(path + ".sources", trace.sources, issues)
        for source_index, source in enumerate(sources):
            source_path = f"{path}.sources[{source_index}]"
            if not isinstance(source, FormationSourceTrace):
                issues.append(source_path + ".invalid_type")
                continue
            self._validate_formation_source(source_path, source, issues)

        if cognition is not None:
            self._validate_trace_sources_match_cognition(path, sources, cognition, issues)
        self._validate_content_bindings(path, trace, cognition, sources, issues)

        raw_support_count = sum(
            source.relation == "support" for source in sources if isinstance(source, FormationSourceTrace)
        )
        contradict_count = sum(
            source.relation == "contradict" for source in sources if isinstance(source, FormationSourceTrace)
        )
        if type(trace.raw_support_count) is int and trace.raw_support_count != raw_support_count:
            issues.append(path + ".raw_support_count.mismatch")
        if type(trace.contradict_count) is int and trace.contradict_count != contradict_count:
            issues.append(path + ".contradict_count.mismatch")
        if raw_support_count > 0 and type(trace.effective_support_count) is int and trace.effective_support_count != 1:
            issues.append(path + ".effective_support_count.mismatch")

        support_decisions = tuple(
            source.local_origin_decision
            for source in sources
            if isinstance(source, FormationSourceTrace) and source.relation == "support"
        )
        for source_index, source in enumerate(sources):
            if not isinstance(source, FormationSourceTrace) or source.relation != "contradict":
                continue
            source_path = f"{path}.sources[{source_index}].contradict"
            if source.local_origin_decision == "unverified":
                issues.append(source_path + ".unverified")
            elif source.local_origin_decision == "assistant_confirmation":
                issues.append(source_path + ".assistant_confirmation")

        if trace.model_inferred_proposal is True:
            for source_index, source in enumerate(sources):
                if (
                    isinstance(source, FormationSourceTrace)
                    and source.relation == "support"
                    and source.local_origin_decision != "inference_grounding"
                ):
                    issues.append(f"{path}.sources[{source_index}].inferred_support.invalid_decision")
            expected_formed_by: FormedBy = "inferred"
        else:
            has_direct = "exact_user_claim" in support_decisions
            has_confirmation = "assistant_confirmation" in support_decisions
            if has_direct and has_confirmation:
                issues.append(path + ".support_decisions.mixed")
            for source_index, source in enumerate(sources):
                if (
                    isinstance(source, FormationSourceTrace)
                    and source.relation == "support"
                    and source.local_origin_decision not in {"exact_user_claim", "assistant_confirmation"}
                ):
                    issues.append(f"{path}.sources[{source_index}].non_inferred_support.invalid_decision")
            if any(decision in {"unverified", "user_negation"} for decision in support_decisions):
                issues.append(path + ".derived_formed_by.unverified_support")
            expected_formed_by = "confirmed" if has_confirmation else "stated"
        if isinstance(trace.derived_formed_by, str) and trace.derived_formed_by in _FORMED_BY:
            if trace.derived_formed_by != expected_formed_by:
                issues.append(path + ".derived_formed_by.mismatch")
            if cognition is not None and trace.derived_formed_by != cognition.formed_by:
                issues.append(path + ".derived_formed_by.cognition_mismatch")
            self._validate_trace_epistemics(path, trace, cognition, issues)

    def _validate_conflict_relationship_projections(
        self,
        known_relationships: dict[str, Relationship],
        owner_entity_id: str,
        issues: list[str],
    ) -> None:
        """Require a narrow local projection for a complete two-sided conflict.

        A relationship cognition is otherwise allowed to be a direct claim.  The
        extra restriction applies only when one newly-created
        ``interpersonal_conflict`` event links exactly one owner relationship,
        names exactly its two endpoints, and has one auditable direct claim for
        each side.  At that point the relationship-level record is necessarily
        a system inference from the two independently sourced side claims.

        The guard deliberately operates only on well-typed values.  Earlier
        validators report malformed records; this cross-record pass never puts
        an untrusted value into a set or hash lookup.
        """
        cognitions = self.new_cognitions if isinstance(self.new_cognitions, tuple) else ()
        traces = self.formation_traces if isinstance(self.formation_traces, tuple) else ()
        trace_by_cognition_id = self._unique_formation_traces_by_cognition_id(traces)

        for event_index, event in enumerate(self.new_events if isinstance(self.new_events, tuple) else ()):
            if not isinstance(event, WorldEvent) or not is_interpersonal_conflict_type(event.event_type):
                continue
            relationship = self._conflict_event_relationship(event, known_relationships, owner_entity_id)
            if relationship is None:
                continue
            endpoint_ids = (relationship.source_entity_id, relationship.target_entity_id)
            direct_sides = tuple(
                self._conflict_direct_sides_for_endpoint(cognitions, trace_by_cognition_id, endpoint_id, owner_entity_id)
                for endpoint_id in endpoint_ids
            )
            if len(direct_sides[0]) != 1 or len(direct_sides[1]) != 1:
                continue

            first_cognition, first_trace = direct_sides[0][0]
            second_cognition, second_trace = direct_sides[1][0]
            path = f"event[{event_index}].conflict_relationship_projection"
            if not self._direct_sides_are_independent(first_trace, second_trace):
                issues.append(path + ".direct_evidence.independent.required")
                continue

            first_scope = first_cognition.scope
            second_scope = second_cognition.scope
            if (
                not isinstance(first_scope, str)
                or not isinstance(second_scope, str)
                or not _non_empty_id(first_scope)
                or not _non_empty_id(second_scope)
            ):
                issues.append(path + ".direct_scope.required")
                continue
            if first_scope != second_scope:
                issues.append(path + ".direct_scope.mismatch")
                continue

            inferred_proposals = tuple(
                (cognition, trace)
                for cognition in cognitions
                if self._targets_relationship(cognition, relationship.id)
                and _non_empty_id(cognition.id)
                and (trace := trace_by_cognition_id.get(cognition.id)) is not None
                and trace.model_inferred_proposal is True
            )
            if not inferred_proposals:
                issues.append(path + ".target_cognition.missing")
                continue
            if len(inferred_proposals) != 1:
                issues.append(path + ".target_cognition.count.invalid")
                continue
            cognition, _ = inferred_proposals[0]
            if cognition.content_type != "hypothesis":
                issues.append(path + ".target_cognition.content_type.hypothesis.required")
            if cognition.scope != first_scope:
                issues.append(path + ".target_cognition.scope.mismatch")
            if cognition.formed_by != "inferred":
                issues.append(path + ".target_cognition.formed_by.inferred.required")
            if not self._is_system_perspective(cognition.perspective):
                issues.append(path + ".target_cognition.perspective.system.required")

    @staticmethod
    def _unique_formation_traces_by_cognition_id(
        traces: tuple[FormationTrace, ...],
    ) -> dict[str, FormationTrace]:
        """Return only unambiguous, typed trace entries for cross-record use."""
        result: dict[str, FormationTrace] = {}
        duplicate_ids: set[str] = set()
        for trace in traces:
            if not isinstance(trace, FormationTrace) or not _non_empty_id(trace.cognition_id):
                continue
            if trace.cognition_id in result:
                duplicate_ids.add(trace.cognition_id)
                continue
            result[trace.cognition_id] = trace
        for cognition_id in duplicate_ids:
            result.pop(cognition_id, None)
        return result

    @staticmethod
    def _conflict_event_relationship(
        event: WorldEvent,
        known_relationships: dict[str, Relationship],
        owner_entity_id: str,
    ) -> Relationship | None:
        if not isinstance(event.relationship_ids, tuple) or len(event.relationship_ids) != 1:
            return None
        relationship_id = event.relationship_ids[0]
        if not _non_empty_id(relationship_id):
            return None
        relationship = known_relationships.get(relationship_id)
        if relationship is None:
            return None
        source_entity_id = relationship.source_entity_id
        target_entity_id = relationship.target_entity_id
        if (
            not _non_empty_id(source_entity_id)
            or not _non_empty_id(target_entity_id)
            or source_entity_id == target_entity_id
            or owner_entity_id not in (source_entity_id, target_entity_id)
        ):
            return None
        participants = event.participants
        if not isinstance(participants, tuple) or len(participants) != 2:
            return None
        participant_ids: list[str] = []
        for participant in participants:
            if not isinstance(participant, EventParticipant) or not _non_empty_id(participant.entity_id):
                return None
            participant_ids.append(participant.entity_id)
        if set(participant_ids) != {source_entity_id, target_entity_id}:
            return None
        return relationship

    @staticmethod
    def _targets_relationship(cognition: object, relationship_id: str) -> bool:
        return (
            isinstance(cognition, WorldCognition)
            and isinstance(cognition.target, MemoryTarget)
            and cognition.target.kind == "relationship"
            and cognition.target.id == relationship_id
        )

    @classmethod
    def _conflict_direct_sides_for_endpoint(
        cls,
        cognitions: tuple[WorldCognition, ...],
        trace_by_cognition_id: dict[str, FormationTrace],
        endpoint_id: str,
        owner_entity_id: str,
    ) -> tuple[tuple[WorldCognition, FormationTrace], ...]:
        result: list[tuple[WorldCognition, FormationTrace]] = []
        for cognition in cognitions:
            if not isinstance(cognition, WorldCognition) or not _non_empty_id(cognition.id):
                continue
            trace = trace_by_cognition_id.get(cognition.id)
            if trace is not None and cls._is_conflict_direct_side(cognition, trace, endpoint_id, owner_entity_id):
                result.append((cognition, trace))
        return tuple(result)

    @classmethod
    def _is_conflict_direct_side(
        cls,
        cognition: WorldCognition,
        trace: FormationTrace,
        endpoint_id: str,
        owner_entity_id: str,
    ) -> bool:
        if (
            not isinstance(cognition.target, MemoryTarget)
            or cognition.target.kind != "entity"
            or cognition.target.id != endpoint_id
            or trace.model_inferred_proposal is not False
            or cognition.formed_by != "stated"
            or trace.derived_formed_by != "stated"
            or not cls._is_owner_perspective(cognition.perspective, owner_entity_id)
            or trace.content_bindings != ()
            or trace.raw_support_count != 1
            or trace.effective_support_count != 1
            or trace.contradict_count != 0
            or not isinstance(cognition.sources, tuple)
            or len(cognition.sources) != 1
            or not isinstance(cognition.sources[0], EvidenceLink)
            or cognition.sources[0].relation != "support"
            or not _non_empty_id(cognition.sources[0].evidence_id)
            or not isinstance(trace.sources, tuple)
            or len(trace.sources) != 1
            or not isinstance(trace.sources[0], FormationSourceTrace)
        ):
            return False
        source = trace.sources[0]
        return (
            source.relation == "support"
            and source.local_origin_decision == "exact_user_claim"
            and _non_empty_id(source.evidence_id)
            and cls._is_valid_claim_span(source.claim_span)
            and source.evidence_id == cognition.sources[0].evidence_id
            and source.relation == cognition.sources[0].relation
        )

    @staticmethod
    def _is_owner_perspective(perspective: object, owner_entity_id: str) -> bool:
        return (
            isinstance(perspective, Perspective)
            and perspective.kind == "entity"
            and isinstance(perspective.holder_entity_ids, tuple)
            and perspective.holder_entity_ids == (owner_entity_id,)
        )

    @staticmethod
    def _is_system_perspective(perspective: object) -> bool:
        return (
            isinstance(perspective, Perspective)
            and perspective.kind == "system"
            and isinstance(perspective.holder_entity_ids, tuple)
            and not perspective.holder_entity_ids
        )

    @classmethod
    def _direct_sides_are_independent(cls, first: FormationTrace, second: FormationTrace) -> bool:
        if not isinstance(first.sources, tuple) or not isinstance(second.sources, tuple):
            return False
        if len(first.sources) != 1 or len(second.sources) != 1:
            return False
        first_source = first.sources[0]
        second_source = second.sources[0]
        if (
            not isinstance(first_source, FormationSourceTrace)
            or not isinstance(second_source, FormationSourceTrace)
            or not _non_empty_id(first_source.evidence_id)
            or not _non_empty_id(second_source.evidence_id)
            or not cls._is_valid_claim_span(first_source.claim_span)
            or not cls._is_valid_claim_span(second_source.claim_span)
        ):
            return False
        return cls._evidence_claim_span_identity(
            first_source.evidence_id,
            first_source.claim_span,
        ) != cls._evidence_claim_span_identity(second_source.evidence_id, second_source.claim_span)

    def _validate_content_bindings(
        self,
        path: str,
        trace: FormationTrace,
        cognition: WorldCognition | None,
        sources: tuple[Any, ...],
        issues: list[str],
    ) -> None:
        """Validate inferred relationship-side bindings without counting them.

        A binding is an auditable content locator, not a provenance link.  Its
        evidence must therefore already occur as support in both the cognition
        and the formation trace, while support/count/confidence computation
        remains exclusively based on ``sources``.
        """
        bindings = self._tuple_items(path + ".content_bindings", trace.content_bindings, issues)
        if not bindings:
            return
        if trace.model_inferred_proposal is not True:
            issues.append(path + ".content_bindings.model_inferred_proposal.required")
        if cognition is None or not isinstance(cognition.target, MemoryTarget) or cognition.target.kind != "relationship":
            issues.append(path + ".content_bindings.target.relationship.required")

        cognition_support_ids = {
            source.evidence_id
            for source in (cognition.sources if cognition is not None and isinstance(cognition.sources, tuple) else ())
            if isinstance(source, EvidenceLink) and source.relation == "support" and _non_empty_id(source.evidence_id)
        }
        formation_support_ids = {
            source.evidence_id
            for source in sources
            if isinstance(source, FormationSourceTrace) and source.relation == "support" and _non_empty_id(source.evidence_id)
        }
        seen_about_entity_ids: set[str] = set()
        seen_spans: set[tuple[str, int, int]] = set()
        for binding_index, binding in enumerate(bindings):
            binding_path = f"{path}.content_bindings[{binding_index}]"
            if not isinstance(binding, FormationContentBinding):
                issues.append(binding_path + ".invalid_type")
                continue
            if binding.semantic_role != "relationship_side":
                issues.append(binding_path + ".semantic_role.invalid")
            about_entity_id_valid = _non_empty_id(binding.about_entity_id)
            if not about_entity_id_valid:
                issues.append(binding_path + ".about_entity_id.invalid")
            elif binding.about_entity_id in seen_about_entity_ids:
                issues.append(binding_path + ".about_entity_id.duplicate")
            else:
                seen_about_entity_ids.add(binding.about_entity_id)
            evidence_id_valid = _non_empty_id(binding.evidence_id)
            if not evidence_id_valid:
                issues.append(binding_path + ".evidence_id.invalid")
            else:
                if binding.evidence_id not in cognition_support_ids:
                    issues.append(binding_path + ".evidence_id.not_cognition_support")
                if binding.evidence_id not in formation_support_ids:
                    issues.append(binding_path + ".evidence_id.not_formation_support")
            self._validate_claim_span(binding_path + ".claim_span", binding.claim_span, issues)
            if evidence_id_valid and self._is_valid_claim_span(binding.claim_span):
                span_key = (
                    binding.evidence_id,
                    binding.claim_span.start_codepoint,
                    binding.claim_span.end_codepoint,
                )
                if span_key in seen_spans:
                    issues.append(binding_path + ".claim_span.duplicate")
                else:
                    seen_spans.add(span_key)

    def _validate_inferred_relationship_content_bindings(
        self,
        path: str,
        trace: FormationTrace,
        cognition: WorldCognition,
        cognition_by_id: dict[str, tuple[int, WorldCognition]],
        trace_by_cognition_id: dict[str, tuple[int, FormationTrace]],
        known_relationships: dict[str, Relationship],
        owner_entity_id: str,
        issues: list[str],
    ) -> None:
        """Enforce the formation @10 relationship-side evidence invariant.

        The inferred relationship text is not a model-authored claim.  It is a
        local projection of exactly two independently auditable direct claims,
        one for each relationship endpoint, with the world owner always first.
        """
        bindings = trace.content_bindings if isinstance(trace.content_bindings, tuple) else ()
        if len(bindings) != 2:
            issues.append(path + ".content_bindings.count.invalid")
        if not _non_empty_id(cognition.target.id):
            issues.append(path + ".target.relationship.unresolvable")
            return
        relationship = known_relationships.get(cognition.target.id)
        if relationship is None:
            issues.append(path + ".target.relationship.unresolvable")
            return
        if not _non_empty_id(relationship.source_entity_id) or not _non_empty_id(relationship.target_entity_id):
            issues.append(path + ".content_bindings.relationship.endpoints.invalid")
            return
        if relationship.source_entity_id == relationship.target_entity_id:
            issues.append(path + ".content_bindings.relationship.endpoints.not_distinct")
            return
        endpoint_ids = {relationship.source_entity_id, relationship.target_entity_id}
        if owner_entity_id not in endpoint_ids:
            issues.append(path + ".content_bindings.owner.not_endpoint")

        typed_bindings = tuple(binding for binding in bindings if self._is_valid_content_binding(binding))
        inference_support_sources = tuple(
            source
            for source in (trace.sources if isinstance(trace.sources, tuple) else ())
            if isinstance(source, FormationSourceTrace) and source.relation == "support"
        )
        if (
            len(inference_support_sources) != 1
            or not self._is_valid_inference_grounding_source(inference_support_sources[0])
        ):
            issues.append(path + ".content_bindings.inference_support.invalid")
        else:
            inference_identity = self._evidence_claim_span_identity(
                inference_support_sources[0].evidence_id,
                inference_support_sources[0].claim_span,
            )
            binding_identities = {
                self._evidence_claim_span_identity(binding.evidence_id, binding.claim_span)
                for binding in typed_bindings
                if isinstance(binding.claim_span, ClaimSpan)
            }
            if inference_identity in binding_identities:
                issues.append(path + ".content_bindings.inference_support.claim_span.identity.reused")
        if len(typed_bindings) == 2:
            if {binding.about_entity_id for binding in typed_bindings} != endpoint_ids:
                issues.append(path + ".content_bindings.endpoints.mismatch")
            if typed_bindings[0].claim_span == typed_bindings[1].claim_span:
                issues.append(path + ".content_bindings.claim_span.same_identity")

        direct_contents: dict[str, str] = {}
        for binding_index, binding in enumerate(typed_bindings):
            matches = tuple(
                (candidate, candidate_trace)
                for cognition_id, (_, candidate) in cognition_by_id.items()
                if (trace_entry := trace_by_cognition_id.get(cognition_id)) is not None
                and self._is_matching_direct_relationship_side(
                    candidate,
                    trace_entry[1],
                    binding,
                    owner_entity_id,
                )
                for candidate_trace in (trace_entry[1],)
            )
            if len(matches) != 1:
                issues.append(f"{path}.content_bindings[{binding_index}].direct_match.count.invalid")
                continue
            direct_contents[binding.about_entity_id] = matches[0][0].content

        if owner_entity_id not in endpoint_ids or set(direct_contents) != endpoint_ids:
            return
        other_entity_id = next(entity_id for entity_id in endpoint_ids if entity_id != owner_entity_id)
        expected_content = (
            f"owner-side: {direct_contents[owner_entity_id]}\n"
            f"other-side: {direct_contents[other_entity_id]}\n"
            "relationship inference: scoped contrast/conflict"
        )
        if cognition.content != expected_content:
            issues.append(path + ".content.projection.mismatch")

    @staticmethod
    def _evidence_claim_span_identity(evidence_id: str, claim_span: ClaimSpan) -> tuple[str, int, int, str, str]:
        """Return the full source identity that must not cross claim roles."""
        return (
            evidence_id,
            claim_span.start_codepoint,
            claim_span.end_codepoint,
            claim_span.source_content_sha256,
            claim_span.claim_sha256,
        )

    @staticmethod
    def _is_valid_claim_span(value: Any) -> bool:
        return (
            isinstance(value, ClaimSpan)
            and type(value.start_codepoint) is int
            and type(value.end_codepoint) is int
            and value.start_codepoint >= 0
            and value.start_codepoint < value.end_codepoint
            and _sha256(value.source_content_sha256)
            and _sha256(value.claim_sha256)
        )

    @classmethod
    def _is_valid_content_binding(cls, value: object) -> bool:
        return (
            isinstance(value, FormationContentBinding)
            and value.semantic_role == "relationship_side"
            and _non_empty_id(value.about_entity_id)
            and _non_empty_id(value.evidence_id)
            and cls._is_valid_claim_span(value.claim_span)
        )

    @classmethod
    def _is_valid_inference_grounding_source(cls, value: FormationSourceTrace) -> bool:
        return (
            value.local_origin_decision == "inference_grounding"
            and _non_empty_id(value.evidence_id)
            and cls._is_valid_claim_span(value.claim_span)
        )

    @staticmethod
    def _is_matching_direct_relationship_side(
        cognition: WorldCognition,
        trace: FormationTrace,
        binding: FormationContentBinding,
        owner_entity_id: str,
    ) -> bool:
        if (
            not WorldDelta._is_valid_content_binding(binding)
            or
            not isinstance(cognition.target, MemoryTarget)
            or cognition.target.kind != "entity"
            or cognition.target.id != binding.about_entity_id
            or trace.model_inferred_proposal is not False
            or cognition.formed_by != "stated"
            or trace.derived_formed_by != "stated"
            or cognition.perspective != Perspective("entity", (owner_entity_id,))
            or trace.content_bindings != ()
            or trace.raw_support_count != 1
            or trace.effective_support_count != 1
            or trace.contradict_count != 0
            or not isinstance(cognition.content, str)
            or sha256(cognition.content.encode("utf-8")).hexdigest() != binding.claim_span.claim_sha256
        ):
            return False
        sources = trace.sources if isinstance(trace.sources, tuple) else ()
        if len(sources) != 1:
            return False
        source = sources[0]
        return (
            isinstance(source, FormationSourceTrace)
            and source.relation == "support"
            and source.local_origin_decision == "exact_user_claim"
            and _non_empty_id(source.evidence_id)
            and WorldDelta._is_valid_claim_span(source.claim_span)
            and source.evidence_id == binding.evidence_id
            and source.claim_span == binding.claim_span
        )

    def _validate_formation_source(self, path: str, source: FormationSourceTrace, issues: list[str]) -> None:
        if not _non_empty_id(source.evidence_id):
            issues.append(path + ".evidence_id.invalid")
        if not isinstance(source.relation, str) or source.relation not in _EVIDENCE_RELATIONS:
            issues.append(path + ".relation.invalid")
        if not isinstance(source.proposition_origin_proposal, str) or source.proposition_origin_proposal not in _FORMATION_ORIGINS:
            issues.append(path + ".proposition_origin_proposal.invalid")
        if not isinstance(source.response_act_proposal, str) or source.response_act_proposal not in _FORMATION_RESPONSE_ACTS:
            issues.append(path + ".response_act_proposal.invalid")
        self._validate_claim_span(path + ".claim_span", source.claim_span, issues)

        has_preceding_id = source.preceding_assistant_turn_id is not None
        has_preceding_hash = source.preceding_assistant_content_sha256 is not None
        if has_preceding_id != has_preceding_hash:
            issues.append(path + ".preceding_assistant.incomplete")
        if has_preceding_id and not _non_empty_id(source.preceding_assistant_turn_id):
            issues.append(path + ".preceding_assistant_turn_id.invalid")
        if has_preceding_hash and not _sha256(source.preceding_assistant_content_sha256):
            issues.append(path + ".preceding_assistant_content_sha256.invalid")
        if not isinstance(source.local_origin_decision, str) or source.local_origin_decision not in _FORMATION_DECISIONS:
            issues.append(path + ".local_origin_decision.invalid")
        elif source.local_origin_decision in {"assistant_confirmation", "user_negation"} and not (
            has_preceding_id and has_preceding_hash
        ):
            issues.append(path + ".preceding_assistant.required")
        if not _non_empty_id(source.decision_code):
            issues.append(path + ".decision_code.invalid")

    @staticmethod
    def _validate_claim_span(path: str, claim_span: Any, issues: list[str]) -> None:
        if not isinstance(claim_span, ClaimSpan):
            issues.append(path + ".invalid_type")
            return
        if type(claim_span.start_codepoint) is not int or type(claim_span.end_codepoint) is not int:
            issues.append(path + ".range.invalid")
        elif claim_span.start_codepoint < 0 or claim_span.start_codepoint >= claim_span.end_codepoint:
            issues.append(path + ".range.invalid")
        if not _sha256(claim_span.source_content_sha256):
            issues.append(path + ".source_content_sha256.invalid")
        if not _sha256(claim_span.claim_sha256):
            issues.append(path + ".claim_sha256.invalid")

    @staticmethod
    def _validate_trace_sources_match_cognition(
        path: str,
        trace_sources: tuple[Any, ...],
        cognition: WorldCognition,
        issues: list[str],
    ) -> None:
        cognition_sources = cognition.sources if isinstance(cognition.sources, tuple) else ()
        if len(trace_sources) != len(cognition_sources):
            issues.append(path + ".sources.count.mismatch")
        for source_index, trace_source in enumerate(trace_sources):
            if source_index >= len(cognition_sources) or not isinstance(trace_source, FormationSourceTrace):
                continue
            cognition_source = cognition_sources[source_index]
            source_path = f"{path}.sources[{source_index}]"
            if not isinstance(cognition_source, EvidenceLink):
                continue
            if trace_source.evidence_id != cognition_source.evidence_id:
                issues.append(source_path + ".evidence_id.mismatch")
            if trace_source.relation != cognition_source.relation:
                issues.append(source_path + ".relation.mismatch")

    @staticmethod
    def _validate_trace_epistemics(
        path: str,
        trace: FormationTrace,
        cognition: WorldCognition | None,
        issues: list[str],
    ) -> None:
        if cognition is None or type(trace.effective_support_count) is not int or type(trace.contradict_count) is not int:
            return
        if cognition.content_type not in _CONTENT_TYPES:
            return
        confidence = compute_confidence(
            ConfidenceInputs(
                content_type=cognition.content_type,
                formed_by=trace.derived_formed_by,
                support_count=trace.effective_support_count,
                contradict_count=trace.contradict_count,
            )
        )
        cred_status = derive_cred_status(
            confidence,
            trace.contradict_count,
            cognition.content_type,
            support_count=trace.effective_support_count,
        )
        if cognition.confidence != confidence:
            issues.append(path + ".confidence.mismatch")
        if cognition.cred_status != cred_status:
            issues.append(path + ".cred_status.mismatch")

    def _validate_issues(
        self,
        kind: str,
        records: tuple[UnresolvedReference, ...] | tuple[SemanticUncertainty, ...],
        eligible: set[str],
        issues: list[str],
    ) -> None:
        expected_type = UnresolvedReference if kind == "unresolved_reference" else SemanticUncertainty
        detail_field = "mention" if kind == "unresolved_reference" else "detail"
        for index, record in enumerate(records):
            path = f"{kind}[{index}]"
            if not isinstance(record, expected_type):
                issues.append(path + ".invalid_type")
                continue
            self._validate_required_str(path + f".{detail_field}", getattr(record, detail_field), issues)
            self._validate_evidence_ids(path + ".evidence_ids", record.evidence_ids, eligible, issues, required=True)

    def _validate_evidence_ids(
        self,
        path: str,
        evidence_ids: Any,
        eligible: set[str],
        issues: list[str],
        *,
        required: bool = False,
    ) -> None:
        if not isinstance(evidence_ids, tuple):
            issues.append(path + ".not_tuple")
            return
        if required and not evidence_ids:
            issues.append(path + ".empty")
        seen: set[str] = set()
        for index, evidence_id in enumerate(evidence_ids):
            item_path = f"{path}[{index}]"
            if isinstance(evidence_id, str):
                if evidence_id in seen:
                    issues.append(item_path + ".duplicate")
                else:
                    seen.add(evidence_id)
            self._validate_evidence_id(item_path, evidence_id, eligible, issues)

    @staticmethod
    def _validate_evidence_id(path: str, evidence_id: Any, eligible: set[str], issues: list[str]) -> None:
        if not _non_empty_id(evidence_id):
            issues.append(path + ".invalid")
        elif evidence_id not in eligible:
            issues.append(path + ".not_eligible")

    @staticmethod
    def _tuple_items(path: str, value: Any, issues: list[str]) -> tuple[Any, ...]:
        if not isinstance(value, tuple):
            issues.append(path + ".not_tuple")
            return ()
        return value

    def _validate_record_shape(
        self,
        path: str,
        record: Entity | Relationship | WorldEvent | WorldCognition,
        issues: list[str],
    ) -> None:
        self._validate_required_str(path + ".id", record.id, issues)
        self._validate_required_str(path + ".world_id", record.world_id, issues)
        if isinstance(record, Entity):
            self._validate_required_str(path + ".kind", record.kind, issues)
            self._validate_required_str(path + ".canonical_name", record.canonical_name, issues)
            for index, alias in enumerate(self._tuple_items(path + ".aliases", record.aliases, issues)):
                self._validate_required_str(f"{path}.aliases[{index}]", alias, issues)
        elif isinstance(record, Relationship):
            self._validate_required_str(path + ".relation_type", record.relation_type, issues)
            if type(record.bidirectional) is not bool:
                issues.append(path + ".bidirectional.invalid")
            self._validate_optional_str(path + ".status", record.status, issues)
            self._validate_optional_str(path + ".valid_from", record.valid_from, issues)
            self._validate_optional_str(path + ".valid_to", record.valid_to, issues)

    @staticmethod
    def _validate_required_str(path: str, value: Any, issues: list[str]) -> None:
        if not _non_empty_id(value):
            issues.append(path + ".invalid")

    @staticmethod
    def _validate_optional_str(path: str, value: Any, issues: list[str]) -> None:
        if value is not None and not isinstance(value, str):
            issues.append(path + ".invalid")

    @staticmethod
    def _validate_optional_non_empty_str(path: str, value: Any, issues: list[str]) -> None:
        if value is not None and not _non_empty_id(value):
            issues.append(path + ".invalid")


_RECORD_TYPES: dict[str, type[object]] = {
    "entity": Entity,
    "relationship": Relationship,
    "event": WorldEvent,
    "cognition": WorldCognition,
}


def _non_empty_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256(value: Any) -> bool:
    """Accept exactly a lower-case hexadecimal SHA-256 digest."""
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None
