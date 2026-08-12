"""Deterministic semantic Gate 1 evaluator for frozen Nanjing synthetic v1.

The evaluator intentionally knows the *meaning* of the frozen scenario, but
does not know extraction-generated object ids.  It is a pure in-memory check:
callers provide the authoritative evidence allowlist and evidence roles, then
record the returned immutable report with their experiment result.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import re
import unicodedata

from ..confidence import compute_confidence, derive_cred_status
from ..types import ConfidenceInputs, EvidenceLink
from .delta import ClaimSpan, FormationContentBinding, FormationSourceTrace, FormationTrace
from .graph import MemoryWorldGraph
from .model import (
    Entity,
    EventFacet,
    EventParticipant,
    MemoryTarget,
    PersonalWorld,
    Perspective,
    Relationship,
    WorldCognition,
    WorldEvent,
)
from .semantics import is_interpersonal_conflict_type


@dataclass(frozen=True, slots=True)
class NanjingGateObservation:
    """One stable, auditable Gate-1 condition."""

    code: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class NanjingGateReport:
    """The complete result of one pure Nanjing semantic evaluation."""

    observations: tuple[NanjingGateObservation, ...]
    violations: tuple[str, ...]
    passed: bool


@dataclass(frozen=True, slots=True)
class _RelationshipDirectCandidate:
    """One caller-owned endpoint cognition independently qualified by the Gate."""

    cognition: WorldCognition
    trace: FormationTrace
    binding: FormationContentBinding
    claim: str


_PERSON = frozenset({"person", "人"})
_FRIEND_TYPES = frozenset({"friend", "朋友", "好友"})
_FRIEND_LABELS_NORMALIZED = frozenset({"friend x", "朋友x", "朋友 x"})
NANJING_GATE_CONTRACT_VERSION = "gate1-nanjing-semantic-contract@14"
_FROZEN_ALLOWLIST = frozenset({"turn-001", "turn-003"})
_CONFLICT_OCCURRED_AT = "2026-08-06T09:01:00+08:00"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def evaluate_nanjing_gate(
    graph: MemoryWorldGraph,
    *,
    evidence_allowlist: Iterable[str],
    role_by_evidence_id: Mapping[str, str],
    unresolved_references: Iterable[object] = (),
    semantic_uncertainties: Iterable[object] = (),
    formation_traces: Iterable[FormationTrace] = (),
    evidence_content_by_id: Mapping[str, str] | None = None,
    preceding_assistant_context_by_evidence_id: Mapping[str, tuple[str, str] | None] | None = None,
) -> NanjingGateReport:
    """Evaluate exactly one semantic Nanjing-conflict world, without I/O.

    Object ids may vary completely.  The input nevertheless must contain only
    the owner plus the intended Friend_X, Nanjing-trip and Nanjing-place
    entities; one friendship; one conflict event; and the three intended
    cognitions.  All evidence used by the event or cognitions must be a
    non-empty authoritative user-evidence id in ``evidence_allowlist``.  A
    caller that evaluates a ``WorldDelta`` must also pass its unresolved and
    uncertainty collections; any retained item fails this frozen fixture.
    Formation traces are separately caller-owned audit inputs.  They bind
    every formed cognition to exact codepoint spans of the frozen user turn
    and to the preceding assistant context without promoting that assistant
    context to Evidence.
    """
    observations: list[NanjingGateObservation] = []
    violations: list[str] = []

    def check(code: str, condition: bool, detail: str) -> None:
        observations.append(NanjingGateObservation(code, condition, detail))
        if not condition:
            violations.append(code)

    try:
        allowlist = _valid_allowlist(evidence_allowlist)
    except TypeError:
        allowlist = None
    check("evidence.allowlist", allowlist is not None, "non-empty string set")
    if allowlist is None:
        allowlist = frozenset()
    check("evidence.frozen_allowlist", allowlist == _FROZEN_ALLOWLIST, "turn-001 and turn-003 only")
    check(
        "extraction.no_unresolved_or_uncertain",
        _empty_collection(unresolved_references) and _empty_collection(semantic_uncertainties),
        "no unresolved references or semantic uncertainties",
    )

    try:
        world = graph.world
        raw_entities = tuple(graph.entities.values())
        raw_relationships = tuple(graph.relationships.values())
        raw_events = tuple(graph.events.values())
        raw_cognitions = tuple(graph.cognitions.values())
    except (AttributeError, TypeError):
        return _invalid_graph_report(observations, violations)

    world_shape_exact = _personal_world_runtime_shape_exact(world)
    entities_shape_exact = all(_entity_runtime_shape_exact(entity) for entity in raw_entities)
    relationships_shape_exact = all(
        _relationship_runtime_shape_exact(relationship) for relationship in raw_relationships
    )
    events_shape_exact = all(_event_runtime_shape_exact(event) for event in raw_events)
    cognitions_shape_exact = all(_cognition_runtime_shape_exact(cognition) for cognition in raw_cognitions)
    graph_runtime_shapes_exact = (
        world_shape_exact
        and entities_shape_exact
        and relationships_shape_exact
        and events_shape_exact
        and cognitions_shape_exact
    )
    check(
        "graph.runtime_shapes_exact",
        graph_runtime_shapes_exact,
        "world records use exact domain, scalar, boolean, integer, and tuple shapes",
    )

    owner_id = world.owner_entity_id if world_shape_exact else ""
    world_id = world.world_id if world_shape_exact else ""
    entities = raw_entities if entities_shape_exact else ()
    relationships = raw_relationships if relationships_shape_exact else ()
    events = raw_events if events_shape_exact else ()
    cognitions = raw_cognitions if cognitions_shape_exact else ()
    try:
        owner = graph.entities.get(owner_id) if world_shape_exact and entities_shape_exact else None
    except (AttributeError, TypeError):
        owner = None

    owner_ok = isinstance(owner, Entity) and _norm(owner.kind) in _PERSON and owner.world_id == world_id
    check("owner.person", owner_ok, "world owner resolves to a person in this world")

    friend_candidates = tuple(entity for entity in entities if _is_friend(entity) and entity.id != owner_id)
    trip_candidates = tuple(entity for entity in entities if _is_nanjing_trip(entity))
    place_candidates = tuple(entity for entity in entities if _is_nanjing_place(entity))
    friend_resolved = len(friend_candidates) == 1
    trip_resolved = len(trip_candidates) == 1
    place_resolved = len(place_candidates) == 1
    check("entities.friend_resolved", friend_resolved, "exactly one friend entity resolves")
    check("entities.trip_activity_resolved", trip_resolved, "exactly one trip activity resolves")
    check("entities.destination_place_resolved", place_resolved, "exactly one destination place resolves")
    resolved_entity_ids = {owner_id}
    if friend_resolved:
        resolved_entity_ids.add(friend_candidates[0].id)
    if trip_resolved:
        resolved_entity_ids.add(trip_candidates[0].id)
    if place_resolved:
        resolved_entity_ids.add(place_candidates[0].id)
    no_extra_entities = owner_ok and {entity.id for entity in entities} == resolved_entity_ids
    check("entities.no_extra", no_extra_entities, "no entity outside resolved semantic roles")
    entities_ok = (
        owner_ok
        and friend_resolved
        and trip_resolved
        and place_resolved
        and len(entities) == 4
        and no_extra_entities
    )
    check("entities.exact_semantic_set", entities_ok, "owner, Friend_X, Nanjing trip, Nanjing place only")

    friend = friend_candidates[0] if len(friend_candidates) == 1 else None
    trip = trip_candidates[0] if len(trip_candidates) == 1 else None
    place = place_candidates[0] if len(place_candidates) == 1 else None

    relationship = _single_friendship(relationships, owner_id, friend.id if friend else None, world_id)
    friendship_current = (
        relationship is not None
        and relationship.status in {None, "active"}
        and relationship.valid_from is None
        and relationship.valid_to is None
    )
    check(
        "relationships.friendship_current",
        friendship_current,
        "required friendship is active or status-neutral and has no validity bounds",
    )
    relationships_ok = relationship is not None and len(relationships) == 1 and friendship_current
    check("relationships.exact_friendship", relationships_ok, "one bidirectional user-Friend_X friendship only")

    event = _single_conflict(events, world_id)
    event_ok = event is not None and len(events) == 1
    check("events.exact_conflict", event_ok, "one interpersonal conflict only")

    occurred_at_ok = event is not None and event.occurred_at == _CONFLICT_OCCURRED_AT
    check("event.occurred_at", occurred_at_ok, "conflict occurs at authoritative user turn timestamp")

    participants_exact = _event_participants_exact(event, owner_id, friend)
    trip_activity_link = _event_related_entity_link(event, trip, "trip")
    destination_place_link = _event_related_entity_link(event, place, "place")
    friendship_link = _event_friendship_link(event, relationship)
    check(
        "event.participants_exact",
        participants_exact,
        _event_atomic_detail(event, friend),
    )
    check(
        "event.trip_activity_link",
        trip_activity_link,
        _event_atomic_detail(event, trip),
    )
    check(
        "event.destination_place_link",
        destination_place_link,
        _event_atomic_detail(event, place),
    )
    check(
        "event.friendship_link",
        friendship_link,
        _event_atomic_detail(event, relationship),
    )
    links_ok = participants_exact and trip_activity_link and destination_place_link and friendship_link
    check("event.required_links", links_ok, "both people, Nanjing trip/place, and friendship linked")

    facets_ok = _event_facets(event, owner_id, friend.id if friend else None)
    check("event.cause_and_positions", facets_ok, "planning cause and both travel-style positions")
    position_provenance_exact = _event_position_provenance_exact(
        event,
        allowlist,
        role_by_evidence_id,
        evidence_content_by_id,
    )
    check(
        "event.position_provenance_exact",
        position_provenance_exact,
        "each final position is exactly one distinct caller-owned complete user segment cited by the event",
    )

    cognition_roles = _cognition_roles(
        cognitions,
        owner_id,
        friend.id if friend else None,
        relationship.id if relationship else None,
    )
    cognition_targets_exact = cognition_roles is not None
    check("cognitions.targets_exact", cognition_targets_exact, "three exact cognition target roles")
    required_cognitions_temporally_current = cognition_roles is not None and all(
        cognition.valid_at is None and cognition.invalid_at is None for cognition in cognition_roles
    )
    check(
        "cognitions.required_temporally_current",
        required_cognitions_temporally_current,
        "all three required cognitions are current and undated",
    )
    if cognition_roles is None:
        for code in (
            "cognitions.owner_perspectives",
            "cognitions.content_types",
            "cognitions.scopes",
            "cognitions.direct_contents",
            "cognitions.user_perspective",
            "cognitions.friend_perspective",
            "cognitions.relationship_perspective",
            "cognitions.user_content_type",
            "cognitions.friend_content_type",
            "cognitions.relationship_content_type",
            "cognitions.user_scope",
            "cognitions.friend_scope",
            "cognitions.relationship_scope",
            "cognitions.user_direct_content",
            "cognitions.friend_direct_content",
            "cognitions.user_formation_status",
            "cognitions.friend_formation_status",
            "cognitions.relationship_formation_status",
            "cognitions.relationship_owner_side",
            "cognitions.relationship_friend_side",
            "cognitions.relationship_contrast",
        ):
            check(code, False, "blocked: cognition target roles unresolved")
    else:
        user_cognition, friend_cognition, relationship_cognition = cognition_roles
        user_perspective = _user_perspective_matches(user_cognition, owner_id)
        friend_perspective = _friend_perspective_matches(friend_cognition, owner_id)
        relationship_perspective = _relationship_perspective_matches(relationship_cognition)
        check("cognitions.user_perspective", user_perspective, "user cognition perspective matches required role")
        check("cognitions.friend_perspective", friend_perspective, "friend cognition perspective matches required role")
        check(
            "cognitions.relationship_perspective",
            relationship_perspective,
            "relationship cognition perspective matches required role",
        )
        check(
            "cognitions.owner_perspectives",
            user_perspective and friend_perspective and relationship_perspective,
            "perspectives match required owner and system roles",
        )
        user_content_type = _user_content_type_matches(user_cognition)
        friend_content_type = _friend_content_type_matches(friend_cognition)
        relationship_content_type = _relationship_content_type_matches(relationship_cognition)
        check("cognitions.user_content_type", user_content_type, "user cognition content type matches required role")
        check("cognitions.friend_content_type", friend_content_type, "friend cognition content type matches required role")
        check(
            "cognitions.relationship_content_type",
            relationship_content_type,
            "relationship cognition content type matches required role",
        )
        check(
            "cognitions.content_types",
            user_content_type and friend_content_type and relationship_content_type,
            "content types match required direct and inferred roles",
        )
        user_scope = _user_scope_matches(user_cognition)
        friend_scope = _friend_scope_matches(friend_cognition)
        relationship_scope = _relationship_scope_matches(relationship_cognition)
        check("cognitions.user_scope", user_scope, "user cognition scope matches required role")
        check("cognitions.friend_scope", friend_scope, "friend cognition scope matches required role")
        check("cognitions.relationship_scope", relationship_scope, "relationship cognition scope matches required role")
        check(
            "cognitions.scopes",
            user_scope and friend_scope and relationship_scope,
            "scopes match required travel roles",
        )
        user_direct_content = _user_direct_content_matches(user_cognition)
        friend_direct_content = _friend_direct_content_matches(friend_cognition)
        check("cognitions.user_direct_content", user_direct_content, "user direct cognition content requirements match")
        check("cognitions.friend_direct_content", friend_direct_content, "friend direct cognition content requirements match")
        check(
            "cognitions.direct_contents",
            user_direct_content and friend_direct_content,
            "direct cognition content requirements match",
        )
        check(
            "cognitions.user_formation_status",
            _user_formation_status_matches(user_cognition),
            "user cognition formation and status match required role",
        )
        check(
            "cognitions.friend_formation_status",
            _friend_formation_status_matches(friend_cognition),
            "friend cognition formation and status match required role",
        )
        check(
            "cognitions.relationship_formation_status",
            _relationship_formation_status_matches(relationship_cognition),
            "relationship cognition formation and status match required role",
        )
        check(
            "cognitions.relationship_owner_side",
            _relationship_owner_side_matches(relationship_cognition.content),
            "relationship cognition includes the owner-side preference structure",
        )
        check(
            "cognitions.relationship_friend_side",
            _relationship_friend_side_matches(relationship_cognition.content),
            "relationship cognition includes the friend-side preference structure",
        )
        check(
            "cognitions.relationship_contrast",
            _relationship_contrast_matches(relationship_cognition.content),
            "relationship cognition includes a contrast structure",
        )
    cognition_ok = required_cognitions_temporally_current and _cognitions(
        cognitions,
        owner_id,
        friend.id if friend else None,
        relationship.id if relationship else None,
    )
    check("cognitions.exact_targets_perspectives_content", cognition_ok, "user, Friend_X, relationship cognitions only")

    traces = _formation_trace_tuple(formation_traces)
    formation_runtime_shapes_exact = all(_formation_trace_runtime_shape_exact(trace) for trace in traces)
    check(
        "formation.runtime_shapes_exact",
        formation_runtime_shapes_exact,
        "formation traces use exact domain, scalar, boolean, integer, and tuple shapes",
    )
    if not formation_runtime_shapes_exact:
        traces = ()
    trace_complete_ok = _formation_trace_complete(cognitions, traces)
    check("formation.trace_complete", trace_complete_ok, "exactly three complete formation traces")
    trace_cognition_bijection_ok = _formation_trace_cognition_bijection(cognitions, traces)
    check(
        "formation.trace_cognition_bijection",
        trace_cognition_bijection_ok,
        "each evaluated cognition has exactly one trace by actual cognition id",
    )
    trace_source_bijection_ok = _formation_trace_source_bijection(cognitions, traces)
    check(
        "formation.trace_source_bijection",
        trace_source_bijection_ok,
        "each trace source exactly corresponds to its cognition source",
    )
    claim_spans_ok = _claim_spans_exact(traces, evidence_content_by_id)
    check(
        "formation.claim_spans_exact",
        claim_spans_ok,
        "claim spans are complete caller-owned catalog segments with matching content hashes",
    )
    preceding_binding_ok = _preceding_assistant_binding(
        traces,
        preceding_assistant_context_by_evidence_id,
    )
    check(
        "formation.preceding_assistant_binding",
        preceding_binding_ok,
        "preceding assistant context is bound by caller-provided id and hash only",
    )
    origin_case_semantics_ok = _origin_case_semantics(
        cognitions,
        traces,
        owner_id,
        friend.id if friend else None,
        relationship.id if relationship else None,
        evidence_content_by_id,
    )
    check(
        "formation.origin_case_semantics",
        origin_case_semantics_ok,
        "direct user claims and relationship inference retain their distinct origins",
    )
    derived_formed_by_ok = _derived_formed_by(cognitions, traces)
    check(
        "formation.derived_formed_by",
        derived_formed_by_ok,
        "trace-derived formation agrees with the final cognition formation",
    )
    effective_support_count_ok = _effective_support_counts(traces)
    check(
        "formation.effective_support_count",
        effective_support_count_ok,
        "each case trace has one raw support, one effective support, and no contradiction",
    )

    binding_checks = _relationship_content_binding_checks(
        cognitions,
        traces,
        owner_id,
        friend,
        relationship,
        evidence_content_by_id,
    )
    for code, detail in (
        (
            "cognitions.direct_perspectives_locally_derived",
            "direct current-user cognitions use owner perspective and relationship inference uses system perspective",
        ),
        (
            "cognitions.relationship_side_bindings_exact",
            "relationship side bindings cover both endpoints and match their direct cognitions",
        ),
        (
            "cognitions.relationship_owner_side_materialized_exact",
            "owner side is materialized from its complete caller-owned direct segment",
        ),
        (
            "cognitions.relationship_friend_side_materialized_exact",
            "friend side is materialized from its complete caller-owned direct segment",
        ),
        (
            "cognitions.relationship_content_uses_bound_segments",
            "relationship content exactly follows the fixed local projection of both bound segments",
        ),
        (
            "formation.content_bindings_complete",
            "only the relationship trace carries exactly two relationship-side content bindings",
        ),
        (
            "formation.content_binding_claim_spans_exact",
            "content binding spans and hashes exactly match complete caller-owned segments",
        ),
        (
            "formation.content_bindings_support_linked",
            "each content binding is covered by the relationship cognition support evidence",
        ),
        (
            "formation.content_bindings_do_not_inflate_support",
            "content bindings do not add EvidenceLinks, raw supports, effective supports, or confidence",
        ),
        (
            "formation.relationship_direct_candidates_unique",
            "each relationship endpoint has exactly one locally qualified direct cognition",
        ),
        (
            "formation.relationship_bindings_locally_recomputed",
            "relationship bindings exactly equal the owner-first locally recomputed bindings",
        ),
        (
            "formation.relationship_grounding_independent",
            "relationship inference retains one independent inference-grounding support",
        ),
    ):
        check(code, binding_checks[code], detail)

    cognition_epistemics_ok = _deterministic_cognition_epistemics(cognitions, traces)
    check(
        "cognitions.deterministic_epistemics",
        cognition_epistemics_ok,
        "confidence and cred_status are recomputed from trace-derived formation and effective counts",
    )

    references_ok = _supported_references(graph, owner_id)
    check("references.supported", references_ok, "all world, graph, target and holder references resolve")

    evidence_ok = _eligible_user_evidence(events, cognitions, allowlist, role_by_evidence_id)
    check("evidence.eligible_user_only", evidence_ok, "event and cognition evidence is allowlisted user evidence")

    return _report(observations, violations)


def _invalid_graph_report(
    observations: list[NanjingGateObservation], violations: list[str]
) -> NanjingGateReport:
    observations.append(NanjingGateObservation("graph.valid_shape", False, "MemoryWorldGraph-compatible object"))
    violations.append("graph.valid_shape")
    return _report(observations, violations)


def _report(observations: list[NanjingGateObservation], violations: list[str]) -> NanjingGateReport:
    ordered_observations = tuple(sorted(observations, key=lambda item: item.code))
    ordered_violations = tuple(sorted(set(violations)))
    return NanjingGateReport(ordered_observations, ordered_violations, not ordered_violations)


def _personal_world_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, PersonalWorld)
        and _exact_nonempty_str(value.world_id)
        and _exact_nonempty_str(value.owner_entity_id)
    )


def _entity_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, Entity)
        and _exact_nonempty_str(value.id)
        and _exact_nonempty_str(value.world_id)
        and _exact_nonempty_str(value.kind)
        and _exact_nonempty_str(value.canonical_name)
        and type(value.aliases) is tuple
        and all(_exact_nonempty_str(alias) for alias in value.aliases)
    )


def _relationship_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, Relationship)
        and _exact_nonempty_str(value.id)
        and _exact_nonempty_str(value.world_id)
        and _exact_nonempty_str(value.source_entity_id)
        and _exact_nonempty_str(value.target_entity_id)
        and _exact_nonempty_str(value.relation_type)
        and type(value.bidirectional) is bool
        and _exact_optional_str(value.status)
        and _exact_optional_str(value.valid_from)
        and _exact_optional_str(value.valid_to)
    )


def _event_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, WorldEvent)
        and _exact_nonempty_str(value.id)
        and _exact_nonempty_str(value.world_id)
        and _exact_nonempty_str(value.event_type)
        and type(value.summary) is str
        and _exact_nonempty_str(value.occurred_at)
        and type(value.participants) is tuple
        and all(_event_participant_runtime_shape_exact(participant) for participant in value.participants)
        and _exact_string_tuple(value.related_entity_ids)
        and _exact_string_tuple(value.relationship_ids)
        and type(value.facets) is tuple
        and all(_event_facet_runtime_shape_exact(facet) for facet in value.facets)
        and _exact_string_tuple(value.evidence_ids)
    )


def _event_participant_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, EventParticipant)
        and _exact_nonempty_str(value.entity_id)
        and _exact_optional_str(value.role)
    )


def _event_facet_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, EventFacet)
        and _exact_nonempty_str(value.key)
        and type(value.value) is str
        and _exact_optional_str(value.about_entity_id)
    )


def _cognition_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, WorldCognition)
        and _exact_nonempty_str(value.id)
        and _exact_nonempty_str(value.world_id)
        and _memory_target_runtime_shape_exact(value.target)
        and type(value.content) is str
        and _exact_nonempty_str(value.content_type)
        and _exact_nonempty_str(value.formed_by)
        and type(value.confidence) is int
        and 0 <= value.confidence <= 1000
        and _exact_nonempty_str(value.cred_status)
        and _perspective_runtime_shape_exact(value.perspective)
        and type(value.sources) is tuple
        and all(_evidence_link_runtime_shape_exact(source) for source in value.sources)
        and _exact_optional_str(value.scope)
        and _exact_optional_str(value.valid_at)
        and _exact_optional_str(value.invalid_at)
    )


def _memory_target_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, MemoryTarget)
        and type(value.kind) is str
        and value.kind in {"world", "entity", "relationship", "event"}
        and _exact_nonempty_str(value.id)
    )


def _perspective_runtime_shape_exact(value: object) -> bool:
    if (
        not isinstance(value, Perspective)
        or type(value.kind) is not str
        or value.kind not in {"entity", "joint", "system"}
        or not _exact_string_tuple(value.holder_entity_ids)
    ):
        return False
    if value.kind == "entity":
        return len(value.holder_entity_ids) == 1
    if value.kind == "joint":
        return len(value.holder_entity_ids) >= 2
    return value.holder_entity_ids == ()


def _evidence_link_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, EvidenceLink)
        and _exact_nonempty_str(value.evidence_id)
        and type(value.relation) is str
        and value.relation in {"support", "contradict"}
    )


def _formation_trace_runtime_shape_exact(value: object) -> bool:
    if (
        not isinstance(value, FormationTrace)
        or not _exact_nonempty_str(value.cognition_id)
        or type(value.model_inferred_proposal) is not bool
        or type(value.sources) is not tuple
        or not all(_formation_source_runtime_shape_exact(source) for source in value.sources)
        or not _exact_nonempty_str(value.derived_formed_by)
        or value.derived_formed_by not in {"stated", "observed", "ruled", "confirmed", "inferred"}
        or type(value.raw_support_count) is not int
        or type(value.effective_support_count) is not int
        or type(value.contradict_count) is not int
        or min(value.raw_support_count, value.effective_support_count, value.contradict_count) < 0
        or value.effective_support_count > value.raw_support_count
        or type(value.content_bindings) is not tuple
        or not all(_content_binding_runtime_shape_exact(binding) for binding in value.content_bindings)
    ):
        return False
    support_count = sum(source.relation == "support" for source in value.sources)
    contradict_count = sum(source.relation == "contradict" for source in value.sources)
    return value.raw_support_count == support_count and value.contradict_count == contradict_count


def _formation_source_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, FormationSourceTrace)
        and _exact_nonempty_str(value.evidence_id)
        and type(value.relation) is str
        and value.relation in {"support", "contradict"}
        and type(value.proposition_origin_proposal) is str
        and value.proposition_origin_proposal in {"user_stated", "assistant_proposed"}
        and type(value.response_act_proposal) is str
        and value.response_act_proposal in {"affirm", "negate", "select", "elaborate", "ask", "none", "other"}
        and _claim_span_runtime_shape_exact(value.claim_span)
        and _exact_optional_nonempty_str(value.preceding_assistant_turn_id)
        and (
            value.preceding_assistant_content_sha256 is None
            or _exact_sha256(value.preceding_assistant_content_sha256)
        )
        and type(value.local_origin_decision) is str
        and value.local_origin_decision
        in {"exact_user_claim", "assistant_confirmation", "inference_grounding", "user_negation", "unverified"}
        and _exact_nonempty_str(value.decision_code)
    )


def _content_binding_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, FormationContentBinding)
        and type(value.semantic_role) is str
        and value.semantic_role == "relationship_side"
        and _exact_nonempty_str(value.about_entity_id)
        and _exact_nonempty_str(value.evidence_id)
        and _claim_span_runtime_shape_exact(value.claim_span)
    )


def _claim_span_runtime_shape_exact(value: object) -> bool:
    return (
        isinstance(value, ClaimSpan)
        and type(value.start_codepoint) is int
        and type(value.end_codepoint) is int
        and 0 <= value.start_codepoint < value.end_codepoint
        and _exact_sha256(value.source_content_sha256)
        and _exact_sha256(value.claim_sha256)
    )


def _exact_string_tuple(value: object) -> bool:
    return type(value) is tuple and all(_exact_nonempty_str(item) for item in value)


def _exact_nonempty_str(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _exact_optional_str(value: object) -> bool:
    return value is None or type(value) is str


def _exact_optional_nonempty_str(value: object) -> bool:
    return value is None or _exact_nonempty_str(value)


def _exact_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _valid_allowlist(values: Iterable[str]) -> frozenset[str] | None:
    if isinstance(values, (str, bytes)):
        return None
    result = frozenset(values)
    if not result or any(not _nonempty(value) for value in result):
        return None
    return result


def _empty_collection(values: Iterable[object]) -> bool:
    if isinstance(values, (str, bytes)):
        return False
    try:
        return not tuple(values)
    except TypeError:
        return False


def _labels(entity: Entity) -> tuple[str, ...]:
    return (entity.canonical_name, *entity.aliases)


def _is_friend(entity: Entity) -> bool:
    labels = _labels(entity)
    return _norm(entity.kind) in _PERSON and bool(labels) and all(
        isinstance(label, str) and _norm(label) in _FRIEND_LABELS_NORMALIZED for label in labels
    )


def _is_nanjing_trip(entity: Entity) -> bool:
    return _norm(entity.kind) in {"activity", "活动"} and any(
        _has_phrase(label, "nanjing", "南京")
        and _has_phrase(
            label,
            "trip",
            "travel",
            "journey",
            "outing",
            "旅行",
            "之旅",
            "之行",
            "出行",
            "旅程",
            "旅游",
            "行程",
        )
        for label in _labels(entity)
    )


def _is_nanjing_place(entity: Entity) -> bool:
    return _norm(entity.kind) in {"place", "地点", "城市"} and any(_has_phrase(label, "nanjing", "南京") for label in _labels(entity))


def _single_friendship(
    relationships: tuple[Relationship, ...], owner_id: str, friend_id: str | None, world_id: str
) -> Relationship | None:
    if friend_id is None or len(relationships) != 1:
        return None
    relationship = relationships[0]
    endpoints = {relationship.source_entity_id, relationship.target_entity_id}
    if (
        relationship.world_id != world_id
        or _norm(relationship.relation_type) not in _FRIEND_TYPES
        or not relationship.bidirectional
        or endpoints != {owner_id, friend_id}
    ):
        return None
    return relationship


def _single_conflict(events: tuple[WorldEvent, ...], world_id: str) -> WorldEvent | None:
    if len(events) != 1:
        return None
    event = events[0]
    return event if event.world_id == world_id and is_interpersonal_conflict_type(event.event_type) else None


def _event_links(
    event: WorldEvent | None,
    owner_id: str,
    friend: Entity | None,
    trip: Entity | None,
    place: Entity | None,
    relationship: Relationship | None,
) -> bool:
    return (
        _event_participants_exact(event, owner_id, friend)
        and _event_related_entity_link(event, trip, "trip")
        and _event_related_entity_link(event, place, "place")
        and _event_friendship_link(event, relationship)
    )


def _event_participants_exact(event: WorldEvent | None, owner_id: str, friend: Entity | None) -> bool:
    return (
        event is not None
        and friend is not None
        and len(event.participants) == 2
        and {participant.entity_id for participant in event.participants} == {owner_id, friend.id}
        and all(participant.role in {None, "participant"} for participant in event.participants)
    )


def _event_related_entity_link(event: WorldEvent | None, entity: Entity | None, expected_role: str) -> bool:
    if event is None or entity is None:
        return False
    if expected_role not in {"trip", "place"}:
        return False
    return len(event.related_entity_ids) == 2 and entity.id in event.related_entity_ids


def _event_friendship_link(event: WorldEvent | None, relationship: Relationship | None) -> bool:
    return event is not None and relationship is not None and tuple(event.relationship_ids) == (relationship.id,)


def _event_atomic_detail(event: WorldEvent | None, prerequisite: object | None) -> str:
    if event is None:
        return "blocked: conflict event unavailable"
    if prerequisite is None:
        return "blocked: prerequisite unresolved"
    return "exact required association"


def _event_facets(event: WorldEvent | None, owner_id: str, friend_id: str | None) -> bool:
    if event is None or friend_id is None:
        return False
    causes = tuple(facet.value for facet in event.facets if facet.key == "cause")
    user_positions = tuple(
        facet.value for facet in event.facets if facet.key == "position" and facet.about_entity_id == owner_id
    )
    friend_positions = tuple(
        facet.value for facet in event.facets if facet.key == "position" and facet.about_entity_id == friend_id
    )
    return (
        len(event.facets) == 3
        and {facet.key for facet in event.facets} <= {"cause", "position"}
        and len(causes) == 1
        and _has_phrase(causes[0], "plan", "planning", "计划", "行程")
        and _has_phrase(causes[0], "difference", "disagree", "分歧", "差异", "争执")
        and len(user_positions) == 1
        and _has_phrase(user_positions[0], "drive", "driving", "开车")
        and _has_phrase(
            user_positions[0],
            "no fixed itinerary",
            "without a fixed itinerary",
            "without fixed itinerary",
            "without fixed itineraries",
            "不要固定行程",
            "无固定行程",
        )
        and len(friend_positions) == 1
        and _has_phrase(friend_positions[0], "itinerary", "行程")
        and _has_phrase(friend_positions[0], "travel guide", "guide", "旅游攻略", "攻略")
    )


def _event_position_provenance_exact(
    event: WorldEvent | None,
    allowlist: frozenset[str],
    role_by_evidence_id: Mapping[str, str],
    evidence_content_by_id: Mapping[str, str] | None,
) -> bool:
    """Rebuild the caller-owned position catalog without trusting extraction.

    Position provenance intentionally does not use the extractor's opaque
    segment ids or any model-authored trace.  It reconstructs the frozen
    complete-segment catalog directly from the caller supplied eligible user
    Evidence, then checks final graph values by raw Python string equality.
    This preserves every original codepoint: there is deliberately no
    normalization, substring, fuzzy, or translation fallback here.
    """
    if event is None or not isinstance(evidence_content_by_id, Mapping):
        return False
    try:
        if any(role_by_evidence_id.get(evidence_id) != "user" for evidence_id in allowlist):
            return False
        catalog: list[tuple[str, int, int, str]] = []
        for evidence_id in sorted(allowlist):
            content = evidence_content_by_id.get(evidence_id)
            if not isinstance(content, str):
                return False
            for start, end in _complete_sentence_segment_bounds(content):
                segment = content[start:end]
                if _is_catalog_segment(segment):
                    catalog.append((evidence_id, start, end, segment))

        positions = tuple(facet for facet in event.facets if facet.key == "position")
        if len(positions) != 2 or len(event.facets) != 3:
            return False
        event_evidence_ids = frozenset(event.evidence_ids)
        matched_segment_ids: list[tuple[str, int, int]] = []
        for position in positions:
            if not isinstance(position.value, str):
                return False
            matches = tuple(
                (evidence_id, start, end)
                for evidence_id, start, end, segment in catalog
                if position.value == segment
            )
            if len(matches) != 1:
                return False
            if matches[0][0] not in event_evidence_ids:
                return False
            matched_segment_ids.append(matches[0])
        return matched_segment_ids[0] != matched_segment_ids[1]
    except (AttributeError, TypeError):
        return False


def _cognitions(
    cognitions: tuple[WorldCognition, ...], owner_id: str, friend_id: str | None, relationship_id: str | None
) -> bool:
    roles = _cognition_roles(cognitions, owner_id, friend_id, relationship_id)
    if roles is None:
        return False
    user, friend, relationship = roles
    return (
        _cognition_perspectives_match(user, friend, relationship, owner_id)
        and _cognition_content_types_match(user, friend, relationship)
        and _user_formation_status_matches(user)
        and _cognition_scopes_match(user, friend, relationship)
        and _friend_formation_status_matches(friend)
        and _relationship_formation_status_matches(relationship)
        and _direct_cognition_contents_match(user, friend)
        and _relationship_content_matches(relationship.content)
    )


def _cognition_roles(
    cognitions: tuple[WorldCognition, ...], owner_id: str, friend_id: str | None, relationship_id: str | None
) -> tuple[WorldCognition, WorldCognition, WorldCognition] | None:
    if friend_id is None or relationship_id is None or len(cognitions) != 3:
        return None
    user = tuple(cognition for cognition in cognitions if cognition.target.kind == "entity" and cognition.target.id == owner_id)
    friend = tuple(cognition for cognition in cognitions if cognition.target.kind == "entity" and cognition.target.id == friend_id)
    relationship = tuple(
        cognition for cognition in cognitions if cognition.target.kind == "relationship" and cognition.target.id == relationship_id
    )
    if len(user) == len(friend) == len(relationship) == 1:
        return user[0], friend[0], relationship[0]
    return None


def _cognition_perspectives_match(
    user: WorldCognition, friend: WorldCognition, relationship: WorldCognition, owner_id: str
) -> bool:
    return (
        _user_perspective_matches(user, owner_id)
        and _friend_perspective_matches(friend, owner_id)
        and _relationship_perspective_matches(relationship)
    )


def _user_perspective_matches(cognition: WorldCognition, owner_id: str) -> bool:
    return _owner_perspective(cognition, owner_id)


def _friend_perspective_matches(cognition: WorldCognition, owner_id: str) -> bool:
    return _owner_perspective(cognition, owner_id)


def _relationship_perspective_matches(cognition: WorldCognition) -> bool:
    return cognition.perspective.kind == "system" and cognition.perspective.holder_entity_ids == ()


def _cognition_content_types_match(
    user: WorldCognition, friend: WorldCognition, relationship: WorldCognition
) -> bool:
    return (
        _user_content_type_matches(user)
        and _friend_content_type_matches(friend)
        and _relationship_content_type_matches(relationship)
    )


def _user_content_type_matches(cognition: WorldCognition) -> bool:
    return cognition.content_type == "preference"


def _friend_content_type_matches(cognition: WorldCognition) -> bool:
    return cognition.content_type in {"preference", "trait"}


def _relationship_content_type_matches(cognition: WorldCognition) -> bool:
    return cognition.content_type == "hypothesis"


def _cognition_scopes_match(user: WorldCognition, friend: WorldCognition, relationship: WorldCognition) -> bool:
    return _user_scope_matches(user) and _friend_scope_matches(friend) and _relationship_scope_matches(relationship)


def _user_scope_matches(cognition: WorldCognition) -> bool:
    return cognition.scope == "travel"


def _friend_scope_matches(cognition: WorldCognition) -> bool:
    return cognition.scope in {None, "travel"}


def _relationship_scope_matches(cognition: WorldCognition) -> bool:
    return cognition.scope == "travel"


def _direct_cognition_contents_match(user: WorldCognition, friend: WorldCognition) -> bool:
    return (
        _user_direct_content_matches(user)
        and _friend_direct_content_matches(friend)
    )


def _user_direct_content_matches(cognition: WorldCognition) -> bool:
    return _has_phrase(cognition.content, "flexible", "spontaneous", "随性", "低计划", "不要固定", "自由") and _has_phrase(
        cognition.content, "unknown", "explor", "未知", "探索"
    )


def _friend_direct_content_matches(cognition: WorldCognition) -> bool:
    return _has_phrase(cognition.content, "plan", "itinerary", "规划", "行程") and _has_phrase(
        cognition.content, "guide", "攻略"
    )


def _user_formation_status_matches(cognition: WorldCognition) -> bool:
    return cognition.formed_by == "stated" and cognition.cred_status == "limited"


def _friend_formation_status_matches(cognition: WorldCognition) -> bool:
    return cognition.formed_by == "stated" and cognition.cred_status == "limited"


def _relationship_formation_status_matches(cognition: WorldCognition) -> bool:
    return cognition.formed_by == "inferred" and cognition.cred_status == "candidate"


def _owner_perspective(cognition: WorldCognition, owner_id: str) -> bool:
    return cognition.perspective.kind == "entity" and cognition.perspective.holder_entity_ids == (owner_id,)


def _formation_trace_tuple(values: Iterable[FormationTrace]) -> tuple[object, ...]:
    if isinstance(values, (str, bytes)):
        return ()
    try:
        return tuple(values)
    except TypeError:
        return ()


def _formation_trace_complete(cognitions: tuple[WorldCognition, ...], traces: tuple[object, ...]) -> bool:
    return len(cognitions) == len(traces) == 3 and all(
        _formation_trace_runtime_shape_exact(trace)
        and isinstance(trace, FormationTrace)
        and len(trace.sources) == 1
        for trace in traces
    )


def _formation_trace_cognition_bijection(cognitions: tuple[WorldCognition, ...], traces: tuple[object, ...]) -> bool:
    if (
        len(cognitions) != 3
        or len(traces) != 3
        or not all(_cognition_runtime_shape_exact(cognition) for cognition in cognitions)
        or not all(_formation_trace_runtime_shape_exact(trace) for trace in traces)
    ):
        return False
    trace_ids = tuple(trace.cognition_id for trace in traces if isinstance(trace, FormationTrace))
    cognition_ids = tuple(cognition.id for cognition in cognitions)
    return len(trace_ids) == len(set(trace_ids)) and set(trace_ids) == set(cognition_ids)


def _formation_trace_source_bijection(cognitions: tuple[WorldCognition, ...], traces: tuple[object, ...]) -> bool:
    if (
        len(cognitions) != 3
        or len(traces) != 3
        or not all(_cognition_runtime_shape_exact(cognition) for cognition in cognitions)
        or not all(_formation_trace_runtime_shape_exact(trace) for trace in traces)
    ):
        return False
    trace_by_cognition = _trace_by_cognition_id(traces)
    if trace_by_cognition is None or set(trace_by_cognition) != {cognition.id for cognition in cognitions}:
        return False
    for cognition in cognitions:
        trace = trace_by_cognition[cognition.id]
        trace_sources = tuple((source.evidence_id, source.relation) for source in trace.sources)
        cognition_sources = tuple((source.evidence_id, source.relation) for source in cognition.sources)
        if trace_sources != cognition_sources:
            return False
    return True


def _claim_spans_exact(traces: tuple[object, ...], evidence_content_by_id: Mapping[str, str] | None) -> bool:
    if (
        len(traces) != 3
        or not isinstance(evidence_content_by_id, Mapping)
        or not all(_formation_trace_runtime_shape_exact(trace) for trace in traces)
    ):
        return False
    for trace in traces:
        if not isinstance(trace, FormationTrace):
            return False
        for source in trace.sources:
            try:
                content = evidence_content_by_id.get(source.evidence_id)
                span = source.claim_span
                start = span.start_codepoint
                end = span.end_codepoint
                if type(start) is not int or type(end) is not int or not isinstance(content, str):
                    return False
                if not 0 <= start < end <= len(content):
                    return False
                if (start, end) not in _complete_sentence_segment_bounds(content):
                    return False
                source_hash = sha256(content.encode("utf-8")).hexdigest()
                claim_hash = sha256(content[start:end].encode("utf-8")).hexdigest()
                if span.source_content_sha256 != source_hash or span.claim_sha256 != claim_hash:
                    return False
            except (AttributeError, TypeError, ValueError):
                return False
    return True


def _preceding_assistant_binding(
    traces: tuple[object, ...],
    contexts: Mapping[str, tuple[str, str] | None] | None,
) -> bool:
    if (
        len(traces) != 3
        or not isinstance(contexts, Mapping)
        or not all(_formation_trace_runtime_shape_exact(trace) for trace in traces)
    ):
        return False
    try:
        expected = contexts.get("turn-003")
    except (AttributeError, TypeError):
        return False
    if (
        not isinstance(expected, tuple)
        or len(expected) != 2
        or expected[0] != "turn-002"
        or not isinstance(expected[1], str)
    ):
        return False
    expected_hash = sha256(expected[1].encode("utf-8")).hexdigest()
    for trace in traces:
        if not isinstance(trace, FormationTrace):
            return False
        for source in trace.sources:
            if (
                source.evidence_id != "turn-003"
                or source.preceding_assistant_turn_id != expected[0]
                or source.preceding_assistant_content_sha256 != expected_hash
            ):
                return False
    return True


def _origin_case_semantics(
    cognitions: tuple[WorldCognition, ...],
    traces: tuple[object, ...],
    owner_id: str,
    friend_id: str | None,
    relationship_id: str | None,
    evidence_content_by_id: Mapping[str, str] | None,
) -> bool:
    if friend_id is None or relationship_id is None or not isinstance(evidence_content_by_id, Mapping):
        return False
    trace_by_cognition = _trace_by_cognition_id(traces)
    if trace_by_cognition is None:
        return False
    user = _single_cognition_target(cognitions, "entity", owner_id)
    friend = _single_cognition_target(cognitions, "entity", friend_id)
    relationship = _single_cognition_target(cognitions, "relationship", relationship_id)
    if user is None or friend is None or relationship is None:
        return False
    user_trace = trace_by_cognition.get(user.id)
    friend_trace = trace_by_cognition.get(friend.id)
    relationship_trace = trace_by_cognition.get(relationship.id)
    if user_trace is None or friend_trace is None or relationship_trace is None:
        return False
    return (
        not user_trace.model_inferred_proposal
        and user_trace.derived_formed_by == "stated"
        and _direct_claim_origin_matches(user, user_trace, evidence_content_by_id)
        and _trace_claim_matches(
            user_trace,
            evidence_content_by_id,
            ("不固定", "不要固定", "自由", "随性"),
            ("未知", "探索"),
        )
        and not friend_trace.model_inferred_proposal
        and friend_trace.derived_formed_by == "stated"
        and _direct_claim_origin_matches(friend, friend_trace, evidence_content_by_id)
        and _trace_claim_matches(friend_trace, evidence_content_by_id, ("行程", "规划"), ("攻略",))
        and relationship_trace.model_inferred_proposal
        and relationship_trace.derived_formed_by == "inferred"
        and _all_local_origin_decisions(relationship_trace, "inference_grounding")
        and _trace_claim_matches(
            relationship_trace,
            evidence_content_by_id,
            ("计划", "行程", "规划"),
            ("差异", "吵", "争执"),
        )
    )


def _single_cognition_target(
    cognitions: tuple[WorldCognition, ...], target_kind: str, target_id: str
) -> WorldCognition | None:
    matches = tuple(
        cognition
        for cognition in cognitions
        if cognition.target.kind == target_kind and cognition.target.id == target_id
    )
    return matches[0] if len(matches) == 1 else None


def _all_local_origin_decisions(trace: FormationTrace, expected: str) -> bool:
    return bool(trace.sources) and all(source.local_origin_decision == expected for source in trace.sources)


_CONFIRMATION_CARRIERS = frozenset({
    "对", "对的", "是", "是的", "是啊", "嗯", "嗯嗯", "没错",
    "yes", "yeah", "yep", "right", "sure", "exactly", "前者", "后者",
})
_NEGATION_CARRIERS = frozenset({"不", "不是", "不对", "没有", "no", "nope", "not really"})


def _direct_claim_origin_matches(
    cognition: WorldCognition,
    trace: FormationTrace,
    evidence_content_by_id: Mapping[str, str],
) -> bool:
    """Independently reproduce extractor's direct-user-claim decision.

    A trace label is never sufficient authority.  The raw span must be one
    complete caller-owned sentence segment, and the final cognition must
    normalize exactly to that segment.  Carrier-only confirmation or negation
    remains a response to prior context, not a direct user claim, even if a
    forged trace calls it exact.
    """
    if len(trace.sources) != 1:
        return False
    source = trace.sources[0]
    try:
        content = evidence_content_by_id.get(source.evidence_id)
        span = source.claim_span
        start = span.start_codepoint
        end = span.end_codepoint
        if (
            not isinstance(content, str)
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(content)
        ):
            return False
        claim = content[start:end]
    except (AttributeError, TypeError):
        return False
    normalized_claim = _normalize_claim(claim)
    normalized_content = _normalize_claim(cognition.content)
    recomputed_exact_user_claim = (
        bool(normalized_content)
        and claim in _complete_sentence_segments(content)
        and normalized_content == normalized_claim
        and _strip_carrier_punctuation(normalized_claim) not in _CONFIRMATION_CARRIERS
        and _strip_carrier_punctuation(normalized_claim) not in _NEGATION_CARRIERS
    )
    return recomputed_exact_user_claim and source.local_origin_decision == "exact_user_claim"


def _normalize_claim(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _strip_carrier_punctuation(value: str) -> str:
    """Remove continuous Unicode punctuation at both edges before carrier lookup."""
    start = 0
    end = len(value)
    while start < end and unicodedata.category(value[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(value[end - 1]).startswith("P"):
        end -= 1
    return value[start:end]


def _complete_sentence_segments(content: str) -> tuple[str, ...]:
    """Split caller-owned raw content at complete direct-claim boundaries.

    Sentence terminators belong to the preceding segment.  Commas are
    deliberately not boundaries, so a cropped clause cannot discard polarity
    or a qualifier while still masquerading as a direct user claim.
    """
    segments: list[str] = []
    start = 0
    index = 0
    while index < len(content):
        character = content[index]
        if character == "\r" and index + 1 < len(content) and content[index + 1] == "\n":
            segments.append(content[start : index + 2])
            index += 2
            start = index
            continue
        if character in {"。", ".", "？", "?", "！", "!", "；", ";", "\r", "\n"}:
            segments.append(content[start : index + 1])
            start = index + 1
        index += 1
    if start < len(content):
        segments.append(content[start:])
    return tuple(segment for segment in segments if segment)


def _complete_sentence_segment_bounds(content: str) -> tuple[tuple[int, int], ...]:
    """Rebuild extractor's catalog boundaries, including CRLF as one segment."""
    segments: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(content):
        character = content[index]
        if character == "\r" and index + 1 < len(content) and content[index + 1] == "\n":
            end = index + 2
        elif character in ".?!;。！？；\n\r":
            end = index + 1
        else:
            index += 1
            continue
        segments.append((start, end))
        start = end
        index = end
    if start < len(content):
        segments.append((start, len(content)))
    return tuple(segments)


def _is_catalog_segment(value: str) -> bool:
    """Match the extractor's original-codepoint lexical catalog filter."""
    return any(unicodedata.category(character)[0] in {"L", "N"} for character in value)


def _trace_claim_matches(
    trace: FormationTrace,
    evidence_content_by_id: Mapping[str, str],
    required_first: tuple[str, ...],
    required_second: tuple[str, ...],
) -> bool:
    if len(trace.sources) != 1:
        return False
    source = trace.sources[0]
    try:
        content = evidence_content_by_id.get(source.evidence_id)
        span = source.claim_span
        if not isinstance(content, str):
            return False
        claim = content[span.start_codepoint : span.end_codepoint]
    except (AttributeError, TypeError):
        return False
    return _has_phrase(claim, *required_first) and _has_phrase(claim, *required_second)


def _relationship_content_binding_checks(
    cognitions: tuple[WorldCognition, ...],
    traces: tuple[object, ...],
    owner_id: str,
    friend: Entity | None,
    relationship: Relationship | None,
    evidence_content_by_id: Mapping[str, str] | None,
) -> dict[str, bool]:
    codes = (
        "cognitions.direct_perspectives_locally_derived",
        "cognitions.relationship_side_bindings_exact",
        "cognitions.relationship_owner_side_materialized_exact",
        "cognitions.relationship_friend_side_materialized_exact",
        "cognitions.relationship_content_uses_bound_segments",
        "formation.content_bindings_complete",
        "formation.content_binding_claim_spans_exact",
        "formation.content_bindings_support_linked",
        "formation.content_bindings_do_not_inflate_support",
        "formation.relationship_direct_candidates_unique",
        "formation.relationship_bindings_locally_recomputed",
        "formation.relationship_grounding_independent",
    )
    result = {code: False for code in codes}
    if (
        not _exact_nonempty_str(owner_id)
        or not _entity_runtime_shape_exact(friend)
        or not _relationship_runtime_shape_exact(relationship)
        or type(cognitions) is not tuple
        or not all(_cognition_runtime_shape_exact(cognition) for cognition in cognitions)
        or type(traces) is not tuple
        or not all(_formation_trace_runtime_shape_exact(trace) for trace in traces)
        or not isinstance(evidence_content_by_id, Mapping)
    ):
        return result
    assert isinstance(friend, Entity)
    assert isinstance(relationship, Relationship)
    trace_by_cognition = _trace_by_cognition_id(traces)
    if trace_by_cognition is None:
        return result

    endpoints = {relationship.source_entity_id, relationship.target_entity_id}
    if len(endpoints) != 2 or owner_id not in endpoints:
        return result
    other_id = next(endpoint for endpoint in endpoints if endpoint != owner_id)
    if other_id != friend.id:
        return result

    owner_candidates = _relationship_direct_candidates(
        cognitions,
        trace_by_cognition,
        endpoint_id=owner_id,
        owner_id=owner_id,
        evidence_content_by_id=evidence_content_by_id,
    )
    other_candidates = _relationship_direct_candidates(
        cognitions,
        trace_by_cognition,
        endpoint_id=other_id,
        owner_id=owner_id,
        evidence_content_by_id=evidence_content_by_id,
    )
    candidates_unique = len(owner_candidates) == 1 and len(other_candidates) == 1
    result["formation.relationship_direct_candidates_unique"] = candidates_unique

    relationship_cognitions = tuple(
        cognition
        for cognition in cognitions
        if cognition.target.kind == "relationship" and cognition.target.id == relationship.id
    )
    if len(relationship_cognitions) != 1:
        return result
    relationship_cognition = relationship_cognitions[0]
    relationship_trace = trace_by_cognition.get(relationship_cognition.id)
    if relationship_trace is None:
        return result

    result["cognitions.direct_perspectives_locally_derived"] = (
        candidates_unique
        and relationship_trace.model_inferred_proposal is True
        and _relationship_perspective_matches(relationship_cognition)
    )

    bindings = relationship_trace.content_bindings
    non_relationship_bindings_empty = all(
        trace.content_bindings == ()
        for cognition_id, trace in trace_by_cognition.items()
        if cognition_id != relationship_cognition.id
    )
    complete = (
        non_relationship_bindings_empty
        and type(bindings) is tuple
        and len(bindings) == 2
        and all(_content_binding_runtime_shape_exact(binding) for binding in bindings)
        and tuple(binding.about_entity_id for binding in bindings) == (owner_id, other_id)
    )
    result["formation.content_bindings_complete"] = complete
    typed_bindings = bindings

    claims: list[str] = []
    claim_spans_exact = len(typed_bindings) == 2
    for binding in typed_bindings:
        claim = _content_binding_claim(binding, evidence_content_by_id)
        if claim is None:
            claim_spans_exact = False
        else:
            claims.append(claim)
    actual_span_identities = tuple(_content_binding_span_identity(binding) for binding in typed_bindings)
    claim_spans_exact = (
        claim_spans_exact
        and len(actual_span_identities) == 2
        and actual_span_identities[0] != actual_span_identities[1]
    )
    result["formation.content_binding_claim_spans_exact"] = claim_spans_exact

    cognition_support_ids = {
        source.evidence_id for source in relationship_cognition.sources if source.relation == "support"
    }
    trace_support_ids = {
        source.evidence_id for source in relationship_trace.sources if source.relation == "support"
    }
    support_linked = all(
        binding.evidence_id in cognition_support_ids and binding.evidence_id in trace_support_ids
        for binding in typed_bindings
    ) and len(typed_bindings) == 2
    result["formation.content_bindings_support_linked"] = support_linked
    result["formation.content_bindings_do_not_inflate_support"] = (
        len(relationship_cognition.sources) == 1
        and len(relationship_trace.sources) == 1
        and relationship_trace.raw_support_count == 1
        and relationship_trace.effective_support_count == 1
        and relationship_trace.contradict_count == 0
        and relationship_cognition.confidence == 200
        and relationship_cognition.cred_status == "candidate"
    )

    expected_bindings: tuple[FormationContentBinding, FormationContentBinding] | None = None
    expected_claims: tuple[str, str] | None = None
    expected_span_identities: tuple[tuple[object, ...], tuple[object, ...]] | None = None
    if candidates_unique:
        owner_candidate = owner_candidates[0]
        other_candidate = other_candidates[0]
        expected_bindings = (owner_candidate.binding, other_candidate.binding)
        expected_claims = (owner_candidate.claim, other_candidate.claim)
        expected_span_identities = (
            _content_binding_span_identity(owner_candidate.binding),
            _content_binding_span_identity(other_candidate.binding),
        )

    locally_recomputed = (
        expected_bindings is not None
        and expected_span_identities is not None
        and expected_span_identities[0] != expected_span_identities[1]
        and bindings == expected_bindings
        and support_linked
    )
    result["formation.relationship_bindings_locally_recomputed"] = locally_recomputed

    grounding_independent = _relationship_grounding_is_independent(
        relationship_cognition,
        relationship_trace,
        expected_bindings,
        evidence_content_by_id,
    )
    result["formation.relationship_grounding_independent"] = grounding_independent

    owner_exact = locally_recomputed and len(claims) == 2 and expected_claims is not None and claims[0] == expected_claims[0]
    friend_exact = locally_recomputed and len(claims) == 2 and expected_claims is not None and claims[1] == expected_claims[1]
    result["cognitions.relationship_owner_side_materialized_exact"] = owner_exact
    result["cognitions.relationship_friend_side_materialized_exact"] = friend_exact
    result["cognitions.relationship_side_bindings_exact"] = (
        complete
        and candidates_unique
        and locally_recomputed
        and claim_spans_exact
        and grounding_independent
        and owner_exact
        and friend_exact
    )

    projected = _relationship_projection_text(*expected_claims) if expected_claims is not None else None
    result["cognitions.relationship_content_uses_bound_segments"] = (
        locally_recomputed and projected is not None and relationship_cognition.content == projected
    )
    return result


def _relationship_direct_candidates(
    cognitions: tuple[WorldCognition, ...],
    trace_by_cognition: Mapping[str, FormationTrace],
    *,
    endpoint_id: str,
    owner_id: str,
    evidence_content_by_id: Mapping[str, str],
) -> tuple[_RelationshipDirectCandidate, ...]:
    candidates: list[_RelationshipDirectCandidate] = []
    for cognition in cognitions:
        if cognition.target.kind != "entity" or cognition.target.id != endpoint_id:
            continue
        trace = trace_by_cognition.get(cognition.id)
        if trace is None:
            continue
        candidate = _relationship_direct_candidate(
            cognition,
            trace,
            owner_id=owner_id,
            evidence_content_by_id=evidence_content_by_id,
        )
        if candidate is not None:
            candidates.append(candidate)
    return tuple(candidates)


def _relationship_direct_candidate(
    cognition: WorldCognition,
    trace: FormationTrace,
    *,
    owner_id: str,
    evidence_content_by_id: Mapping[str, str],
) -> _RelationshipDirectCandidate | None:
    if (
        not _cognition_runtime_shape_exact(cognition)
        or not _formation_trace_runtime_shape_exact(trace)
        or trace.model_inferred_proposal is not False
        or cognition.formed_by != "stated"
        or trace.derived_formed_by != "stated"
        or not _owner_perspective(cognition, owner_id)
        or trace.content_bindings != ()
        or trace.raw_support_count != 1
        or trace.effective_support_count != 1
        or trace.contradict_count != 0
        or len(cognition.sources) != 1
        or cognition.sources[0].relation != "support"
        or len(trace.sources) != 1
        or trace.sources[0].relation != "support"
        or trace.sources[0].local_origin_decision != "exact_user_claim"
        or cognition.sources[0].evidence_id != trace.sources[0].evidence_id
    ):
        return None
    source = trace.sources[0]
    binding = FormationContentBinding(
        semantic_role="relationship_side",
        about_entity_id=cognition.target.id,
        evidence_id=source.evidence_id,
        claim_span=source.claim_span,
    )
    claim = _content_binding_claim(binding, evidence_content_by_id)
    if claim is None or cognition.content != claim:
        return None
    return _RelationshipDirectCandidate(cognition, trace, binding, claim)


def _relationship_grounding_is_independent(
    cognition: WorldCognition,
    trace: FormationTrace,
    expected_bindings: tuple[FormationContentBinding, FormationContentBinding] | None,
    evidence_content_by_id: Mapping[str, str],
) -> bool:
    if (
        expected_bindings is None
        or not _cognition_runtime_shape_exact(cognition)
        or not _formation_trace_runtime_shape_exact(trace)
        or not all(_content_binding_runtime_shape_exact(binding) for binding in expected_bindings)
        or trace.model_inferred_proposal is not True
        or cognition.formed_by != "inferred"
        or trace.derived_formed_by != "inferred"
        or len(cognition.sources) != 1
        or cognition.sources[0].relation != "support"
        or len(trace.sources) != 1
        or trace.sources[0].relation != "support"
        or trace.sources[0].local_origin_decision != "inference_grounding"
        or cognition.sources[0].evidence_id != trace.sources[0].evidence_id
    ):
        return False
    source = trace.sources[0]
    grounding_binding = FormationContentBinding(
        semantic_role="relationship_side",
        about_entity_id=cognition.target.id,
        evidence_id=source.evidence_id,
        claim_span=source.claim_span,
    )
    if _content_binding_claim(grounding_binding, evidence_content_by_id) is None:
        return False
    grounding_identity = _content_binding_span_identity(grounding_binding)
    direct_identities = {_content_binding_span_identity(binding) for binding in expected_bindings}
    direct_evidence_ids = {binding.evidence_id for binding in expected_bindings}
    return grounding_identity not in direct_identities and direct_evidence_ids <= {source.evidence_id}


def _content_binding_claim(
    binding: FormationContentBinding,
    evidence_content_by_id: Mapping[str, str],
) -> str | None:
    if not _content_binding_runtime_shape_exact(binding) or not isinstance(evidence_content_by_id, Mapping):
        return None
    try:
        content = evidence_content_by_id.get(binding.evidence_id)
        span = binding.claim_span
        start = span.start_codepoint
        end = span.end_codepoint
        if (
            not isinstance(content, str)
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(content)
            or (start, end) not in _complete_sentence_segment_bounds(content)
        ):
            return None
        claim = content[start:end]
        if (
            not _is_catalog_segment(claim)
            or span.source_content_sha256 != sha256(content.encode("utf-8")).hexdigest()
            or span.claim_sha256 != sha256(claim.encode("utf-8")).hexdigest()
        ):
            return None
        return claim
    except (AttributeError, TypeError, ValueError):
        return None


def _content_binding_span_identity(binding: FormationContentBinding) -> tuple[object, ...]:
    if not _content_binding_runtime_shape_exact(binding):
        return ()
    span = binding.claim_span
    return (
        binding.evidence_id,
        span.start_codepoint,
        span.end_codepoint,
        span.source_content_sha256,
        span.claim_sha256,
    )


def _relationship_projection_text(
    owner_content: str,
    other_content: str,
) -> str:
    return (
        f"owner-side: {owner_content}\n"
        f"other-side: {other_content}\n"
        "relationship inference: scoped contrast/conflict"
    )


def _derived_formed_by(cognitions: tuple[WorldCognition, ...], traces: tuple[object, ...]) -> bool:
    trace_by_cognition = _trace_by_cognition_id(traces)
    return trace_by_cognition is not None and all(
        trace_by_cognition.get(cognition.id) is not None
        and trace_by_cognition[cognition.id].derived_formed_by == cognition.formed_by
        for cognition in cognitions
    )


def _effective_support_counts(traces: tuple[object, ...]) -> bool:
    return len(traces) == 3 and all(
        _formation_trace_runtime_shape_exact(trace)
        and isinstance(trace, FormationTrace)
        and type(trace.raw_support_count) is int
        and type(trace.effective_support_count) is int
        and type(trace.contradict_count) is int
        and trace.raw_support_count == 1
        and trace.effective_support_count == 1
        and trace.contradict_count == 0
        for trace in traces
    )


def _trace_by_cognition_id(traces: tuple[object, ...]) -> dict[str, FormationTrace] | None:
    trace_by_cognition: dict[str, FormationTrace] = {}
    for trace in traces:
        if (
            not _formation_trace_runtime_shape_exact(trace)
            or not isinstance(trace, FormationTrace)
            or trace.cognition_id in trace_by_cognition
        ):
            return None
        trace_by_cognition[trace.cognition_id] = trace
    return trace_by_cognition


def _deterministic_cognition_epistemics(
    cognitions: tuple[WorldCognition, ...], traces: tuple[object, ...]
) -> bool:
    """Recompute final epistemics from trace-derived formation and counts.

    Gate 1 intentionally refuses the circular proof in which final cognition
    fields and their raw source links certify one another.  FormationTrace is
    the audited formation input: it declares derived formation and the count
    after origin handling, while the final cognition must match its result.
    """
    if (
        len(cognitions) != 3
        or len(traces) != 3
        or not all(_cognition_runtime_shape_exact(cognition) for cognition in cognitions)
        or not all(_formation_trace_runtime_shape_exact(trace) for trace in traces)
    ):
        return False
    trace_by_cognition = _trace_by_cognition_id(traces)
    return trace_by_cognition is not None and all(
        cognition.id in trace_by_cognition
        and _has_deterministic_epistemics(cognition, trace_by_cognition[cognition.id])
        for cognition in cognitions
    )


def _has_deterministic_epistemics(cognition: WorldCognition, trace: FormationTrace) -> bool:
    if (
        not _cognition_runtime_shape_exact(cognition)
        or not _formation_trace_runtime_shape_exact(trace)
        or cognition.content_type not in {
        "fact",
        "preference",
        "goal",
        "project",
        "state",
        "trait",
        "hypothesis",
        "trend",
        }
        or cognition.formed_by not in {"stated", "observed", "ruled", "confirmed", "inferred"}
    ):
        return False
    try:
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
    except (KeyError, TypeError, ValueError):
        return False
    return cognition.confidence == confidence and cognition.cred_status == cred_status


def _relationship_content_matches(content: str) -> bool:
    return (
        _relationship_owner_side_matches(content)
        and _relationship_friend_side_matches(content)
        and _relationship_contrast_matches(content)
    )


def _relationship_owner_side_matches(content: str) -> bool:
    return _labeled_clause_matches(
        content,
        ("owner-side", "owner side", "user", "用户"),
        ("不要固定", "无固定", "灵活", "随性", "自由"),
        ("未知", "探索", "临场"),
    )


def _relationship_friend_side_matches(content: str) -> bool:
    return _labeled_clause_matches(
        content,
        ("other-side", "other side", "friend x", "friend_x", "friend-x", "朋友x", "朋友 x"),
        ("提前", "预先"),
        ("计划", "规划", "行程"),
        ("攻略", "guide"),
    )


def _relationship_contrast_matches(content: str) -> bool:
    return _has_phrase(
        content,
        "friction",
        "conflict",
        "difference",
        "disagree",
        "摩擦",
        "分歧",
        "差异",
        "冲突",
        "对照",
        "对比",
        "相反",
        "不同于",
        "相比",
        "vs",
        "versus",
    )


def _labeled_clause_matches(content: str, labels: tuple[str, ...], *requirements: tuple[str, ...]) -> bool:
    clauses = tuple(re.split(r"[。.!?？!；;\r\n]+", content))
    return any(
        _has_phrase(clause, *labels) and all(_has_phrase(clause, *requirement) for requirement in requirements)
        for clause in clauses
    )


def _supported_references(graph: MemoryWorldGraph, owner_id: str) -> bool:
    try:
        if graph.world.owner_entity_id != owner_id or graph.world.world_id == "":
            return False
        entity_ids = set(graph.entities)
        relationship_ids = set(graph.relationships)
        event_ids = set(graph.events)
        for entity in graph.entities.values():
            if entity.world_id != graph.world.world_id:
                return False
        for relationship in graph.relationships.values():
            if relationship.world_id != graph.world.world_id or {relationship.source_entity_id, relationship.target_entity_id} - entity_ids:
                return False
        for event in graph.events.values():
            if event.world_id != graph.world.world_id:
                return False
            if {participant.entity_id for participant in event.participants} - entity_ids:
                return False
            if set(event.related_entity_ids) - entity_ids or set(event.relationship_ids) - relationship_ids:
                return False
            if {facet.about_entity_id for facet in event.facets if facet.about_entity_id is not None} - entity_ids:
                return False
        for cognition in graph.cognitions.values():
            if cognition.world_id != graph.world.world_id:
                return False
            if cognition.target.kind == "world" and cognition.target.id != graph.world.world_id:
                return False
            if cognition.target.kind == "entity" and cognition.target.id not in entity_ids:
                return False
            if cognition.target.kind == "relationship" and cognition.target.id not in relationship_ids:
                return False
            if cognition.target.kind == "event" and cognition.target.id not in event_ids:
                return False
            if cognition.target.kind not in {"world", "entity", "relationship", "event"}:
                return False
            if set(cognition.perspective.holder_entity_ids) - entity_ids:
                return False
        return True
    except (AttributeError, TypeError):
        return False


def _eligible_user_evidence(
    events: tuple[WorldEvent, ...],
    cognitions: tuple[WorldCognition, ...],
    allowlist: frozenset[str],
    roles: Mapping[str, str],
) -> bool:
    try:
        used: list[tuple[str, str]] = []
        for event in events:
            if len(event.evidence_ids) != len(allowlist) or set(event.evidence_ids) != allowlist:
                return False
            used.extend((evidence_id, "support") for evidence_id in event.evidence_ids)
        for cognition in cognitions:
            if len(cognition.sources) != 1 or (
                cognition.sources[0].evidence_id,
                cognition.sources[0].relation,
            ) != ("turn-003", "support"):
                return False
            used.extend((link.evidence_id, link.relation) for link in cognition.sources)
        used_ids = {evidence_id for evidence_id, _ in used}
        return used_ids == allowlist and all(
            _nonempty(evidence_id)
            and relation == "support"
            and evidence_id in allowlist
            and roles.get(evidence_id) == "user"
            for evidence_id, relation in used
        )
    except (AttributeError, TypeError):
        return False


def _has_phrase(value: object, *phrases: str) -> bool:
    if not isinstance(value, str):
        return False
    normalized = _norm(value)
    return any(_norm(phrase) in normalized for phrase in phrases)


def _norm(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[_-]+", " ", normalized)
    return " ".join(normalized.split())


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


__all__ = [
    "NANJING_GATE_CONTRACT_VERSION",
    "NanjingGateObservation",
    "NanjingGateReport",
    "evaluate_nanjing_gate",
]
