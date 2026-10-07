"""Mutation coverage for the slug-independent Nanjing Gate 1 evaluator."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any, Literal, cast

import pytest

from memoweft.types import CredStatus, EvidenceLink
from memoweft.world.delta import ClaimSpan, FormationContentBinding, FormationSourceTrace, FormationTrace
from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.model import (
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
from support.nanjing_gate import (
    NANJING_GATE_CONTRACT_VERSION,
    NanjingGateObservation,
    NanjingGateReport,
    _direct_claim_origin_matches,
    _relationship_projection_text,
    evaluate_nanjing_gate,
)


_ALLOWLIST = frozenset({"turn-001", "turn-003"})
_ROLES = {"turn-001": "user", "turn-003": "user", "evidence-assistant": "assistant"}
_TURN_001_CONTENT = "我和 Friend_X 是朋友，最近一起计划去南京旅行。"
_TURN_002_CONTENT = "听起来你们正在计划一次南京之旅。"
_TURN_003_CONTENT = (
    "我旅行时更喜欢开车、不要固定行程，也很看重未知和临场探索；"
    "Friend_X 更喜欢提前做行程、照着旅游攻略走。我们因为这个计划方式的差异吵起来了。"
)
_EVIDENCE_CONTENT = {"turn-001": _TURN_001_CONTENT, "turn-003": _TURN_003_CONTENT}
_PRECEDING_ASSISTANT_CONTEXT = {"turn-003": ("turn-002", _TURN_002_CONTENT)}


def _claim_span(start_text: str, end_text: str) -> ClaimSpan:
    start = _TURN_003_CONTENT.index(start_text)
    end = _TURN_003_CONTENT.index(end_text, start) + len(end_text)
    return ClaimSpan(
        start,
        end,
        sha256(_TURN_003_CONTENT.encode("utf-8")).hexdigest(),
        sha256(_TURN_003_CONTENT[start:end].encode("utf-8")).hexdigest(),
    )


def _source_trace(
    claim_span: ClaimSpan,
    decision: Literal[
        "exact_user_claim", "assistant_confirmation", "user_negation", "unverified", "inference_grounding"
    ],
) -> FormationSourceTrace:
    return FormationSourceTrace(
        "turn-003",
        "support",
        "user_stated",
        "elaborate",
        claim_span,
        "turn-002",
        sha256(_TURN_002_CONTENT.encode("utf-8")).hexdigest(),
        decision,
        "nanjing-case",
    )


def _formation_traces(graph: MemoryWorldGraph) -> tuple[FormationTrace, ...]:
    return (
        FormationTrace(
            "cognition-a",
            False,
            (_source_trace(_claim_span("我旅行时", "临场探索；"), "exact_user_claim"),),
            "stated",
            1,
            1,
            0,
        ),
        FormationTrace(
            "cognition-b",
            False,
            (_source_trace(_claim_span("Friend_X", "攻略走。"), "exact_user_claim"),),
            "stated",
            1,
            1,
            0,
        ),
        FormationTrace(
            "cognition-c",
            True,
            (_source_trace(_claim_span("我们因为", "差异吵起来了。"), "inference_grounding"),),
            "inferred",
            1,
            1,
            0,
            content_bindings=(
                FormationContentBinding(
                    "relationship_side",
                    "entity-owner-17",
                    "turn-003",
                    _claim_span("我旅行时", "临场探索；"),
                ),
                FormationContentBinding(
                    "relationship_side",
                    "peer-42",
                    "turn-003",
                    _claim_span("Friend_X", "攻略走。"),
                ),
            ),
        ),
    )


def _graph() -> MemoryWorldGraph:
    """A valid graph whose ids deliberately do not resemble generated slugs."""
    graph = MemoryWorldGraph(PersonalWorld("world-varying-id", "entity-owner-17"))
    graph.add_entity(Entity("entity-owner-17", "world-varying-id", "person", "Casey"))
    graph.add_entity(Entity("peer-42", "world-varying-id", "person", "Ｆｒｉｅｎｄ＿Ｘ"))
    graph.add_entity(Entity("activity-72", "world-varying-id", "activity", "南京 旅行"))
    graph.add_entity(Entity("locale-58", "world-varying-id", "place", "NANJING"))
    graph.add_relationship(
        Relationship("edge-93", "world-varying-id", "peer-42", "entity-owner-17", "朋友", bidirectional=True)
    )
    graph.add_event(
        WorldEvent(
            "event-11",
            "world-varying-id",
            "interpersonal conflict",
            "A planning disagreement during the Nanjing trip.",
            "2026-08-06T09:01:00+08:00",
            participants=(EventParticipant("entity-owner-17"), EventParticipant("peer-42")),
            related_entity_ids=("activity-72", "locale-58"),
            relationship_ids=("edge-93",),
            facets=(
                EventFacet("cause", "双方对旅行计划的差异产生分歧。"),
                EventFacet("position", "我旅行时更喜欢开车、不要固定行程，也很看重未知和临场探索；", "entity-owner-17"),
                EventFacet("position", "Friend_X 更喜欢提前做行程、照着旅游攻略走。", "peer-42"),
            ),
            evidence_ids=("turn-001", "turn-003"),
        )
    )
    graph.add_cognition(
        WorldCognition(
            "cognition-a", "world-varying-id", MemoryTarget("entity", "entity-owner-17"),
            "我旅行时更喜欢开车、不要固定行程，也很看重未知和临场探索；", "preference", "stated", 600,
            "limited", Perspective("entity", ("entity-owner-17",)), (EvidenceLink("turn-003", "support"),), "travel",
        )
    )
    graph.add_cognition(
        WorldCognition(
            "cognition-b", "world-varying-id", MemoryTarget("entity", "peer-42"),
            "Friend_X 更喜欢提前做行程、照着旅游攻略走。", "trait", "stated", 600,
            "limited", Perspective("entity", ("entity-owner-17",)), (EvidenceLink("turn-003", "support"),),
        )
    )
    graph.add_cognition(
        WorldCognition(
            "cognition-c", "world-varying-id", MemoryTarget("relationship", "edge-93"),
            "owner-side: 我旅行时更喜欢开车、不要固定行程，也很看重未知和临场探索；\n"
            "other-side: Friend_X 更喜欢提前做行程、照着旅游攻略走。\n"
            "relationship inference: scoped contrast/conflict", "hypothesis", "inferred", 200,
            "candidate", Perspective("system"), (EvidenceLink("turn-003", "support"),), "travel",
        )
    )
    return graph


def _evaluate(
    graph: MemoryWorldGraph,
    *,
    roles: dict[str, str] | None = None,
    unresolved_references: tuple[object, ...] = (),
    semantic_uncertainties: tuple[object, ...] = (),
    formation_traces: tuple[FormationTrace, ...] | None = None,
    evidence_content_by_id: dict[str, str] | None = None,
    preceding_assistant_context_by_evidence_id: dict[str, tuple[str, str] | None] | None = None,
) -> NanjingGateReport:
    return evaluate_nanjing_gate(
        graph,
        evidence_allowlist=_ALLOWLIST,
        role_by_evidence_id=roles or _ROLES,
        unresolved_references=unresolved_references,
        semantic_uncertainties=semantic_uncertainties,
        formation_traces=_formation_traces(graph) if formation_traces is None else formation_traces,
        evidence_content_by_id=_EVIDENCE_CONTENT if evidence_content_by_id is None else evidence_content_by_id,
        preceding_assistant_context_by_evidence_id=(
            _PRECEDING_ASSISTANT_CONTEXT
            if preceding_assistant_context_by_evidence_id is None
            else preceding_assistant_context_by_evidence_id
        ),
    )


def test_semantic_gate_passes_with_bilingual_nfkc_names_and_variable_ids() -> None:
    report = _evaluate(_graph())

    assert report.passed
    assert report.violations == ()
    assert tuple(observation.code for observation in report.observations) == tuple(
        sorted(observation.code for observation in report.observations)
    )


@pytest.mark.parametrize(
    "event_type",
    [
        "interpersonal conflict",
        "interpersonal_conflict",
        "人际冲突",
        "争执",
        "吵架",
        "  INTERPERSONAL---CONFLICT  ",
        "ＩＮＴＥＲＰＥＲＳＯＮＡＬ＿ＣＯＮＦＬＩＣＴ",
    ],
)
def test_semantic_gate_accepts_shared_conflict_aliases_and_normalization_equivalents(
    event_type: str,
) -> None:
    graph = _graph()
    event = graph.events["event-11"]
    graph.events[event.id] = replace(event, event_type=event_type)

    report = _evaluate(graph)

    assert report.passed
    assert "events.exact_conflict" not in report.violations


@pytest.mark.parametrize(
    "event_type",
    ["conflict", "argument", "interpersonal conflict resolved", "争执修复", ""],
)
def test_semantic_gate_rejects_non_conflict_aliases(event_type: str) -> None:
    graph = _graph()
    event = graph.events["event-11"]
    graph.events[event.id] = replace(event, event_type=event_type)

    report = _evaluate(graph)

    assert not report.passed
    assert "events.exact_conflict" in report.violations


def test_gate_contract_version_records_trace_aware_semantic_expansion() -> None:
    assert NANJING_GATE_CONTRACT_VERSION == "gate1-nanjing-semantic-contract@14"


def _observations(graph: MemoryWorldGraph) -> dict[str, NanjingGateObservation]:
    return {observation.code: observation for observation in _evaluate(graph).observations}


def test_atomic_observations_are_complete_safe_and_pass_for_the_frozen_graph() -> None:
    observations = _observations(_graph())
    expected_codes = {
        "graph.runtime_shapes_exact",
        "entities.friend_resolved",
        "entities.trip_activity_resolved",
        "entities.destination_place_resolved",
        "entities.no_extra",
        "relationships.friendship_current",
        "event.participants_exact",
        "event.trip_activity_link",
        "event.destination_place_link",
        "event.friendship_link",
        "event.position_provenance_exact",
        "cognitions.targets_exact",
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
        "cognitions.required_temporally_current",
        "cognitions.direct_perspectives_locally_derived",
        "cognitions.relationship_side_bindings_exact",
        "cognitions.relationship_owner_side_materialized_exact",
        "cognitions.relationship_friend_side_materialized_exact",
        "cognitions.relationship_content_uses_bound_segments",
        "formation.content_bindings_complete",
        "formation.runtime_shapes_exact",
        "formation.content_binding_claim_spans_exact",
        "formation.content_bindings_support_linked",
        "formation.content_bindings_do_not_inflate_support",
        "formation.relationship_direct_candidates_unique",
        "formation.relationship_bindings_locally_recomputed",
        "formation.relationship_grounding_independent",
    }

    assert expected_codes <= set(observations)
    assert all(observations[code].passed for code in expected_codes)
    assert all(
        "南京" not in observations[code].detail
        and "Friend_X" not in observations[code].detail
        and "旅行" not in observations[code].detail
        for code in expected_codes
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "ended"),
        ("status", "inactive"),
        ("valid_from", "2020-01-01T00:00:00+08:00"),
        ("valid_from", "2099-01-01T00:00:00+08:00"),
        ("valid_from", "nonempty-without-lifecycle-evidence"),
        ("valid_to", "2026-08-01T00:00:00+08:00"),
        ("valid_to", "2099-01-01T00:00:00+08:00"),
    ],
)
def test_required_friendship_must_be_current(field: str, value: str) -> None:
    graph = _graph()
    relationship = graph.relationships["edge-93"]
    if field == "status":
        replacement = replace(relationship, status=value)
    elif field == "valid_from":
        replacement = replace(relationship, valid_from=value)
    else:
        assert field == "valid_to"
        replacement = replace(relationship, valid_to=value)
    graph.relationships[relationship.id] = replacement

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert not report.passed
    assert not observations["relationships.friendship_current"].passed
    assert not observations["relationships.exact_friendship"].passed
    assert observations["relationships.friendship_current"].detail == (
        "required friendship is active or status-neutral and has no validity bounds"
    )
    assert value not in observations["relationships.friendship_current"].detail


@pytest.mark.parametrize("status", [None, "active"])
def test_required_friendship_accepts_only_current_statuses(status: str | None) -> None:
    graph = _graph()
    relationship = graph.relationships["edge-93"]
    graph.relationships[relationship.id] = replace(relationship, status=status)

    observations = {
        observation.code: observation.passed for observation in _evaluate(graph).observations
    }

    assert observations["relationships.friendship_current"]
    assert observations["relationships.exact_friendship"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("valid_at", "2020-01-01T00:00:00+08:00"),
        ("valid_at", "2099-01-01T00:00:00+08:00"),
        ("invalid_at", "2020-01-01T00:00:00+08:00"),
        ("invalid_at", "2099-01-01T00:00:00+08:00"),
    ],
)
def test_required_cognitions_must_be_current_and_undated(field: str, value: str) -> None:
    graph = _graph()
    cognition = graph.cognitions["cognition-b"]
    if field == "valid_at":
        replacement = replace(cognition, valid_at=value)
    else:
        assert field == "invalid_at"
        replacement = replace(cognition, invalid_at=value)
    graph.cognitions[cognition.id] = replacement

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert not report.passed
    assert not observations["cognitions.required_temporally_current"].passed
    assert not observations["cognitions.exact_targets_perspectives_content"].passed
    assert observations["cognitions.required_temporally_current"].detail == (
        "all three required cognitions are current and undated"
    )
    assert value not in observations["cognitions.required_temporally_current"].detail


def _position_values(graph: MemoryWorldGraph) -> tuple[str, str]:
    event = graph.events["event-11"]
    positions = tuple(facet.value for facet in event.facets if facet.key == "position")
    assert len(positions) == 2
    return positions


def test_exact_complete_user_segments_pass_independent_position_provenance() -> None:
    report = _evaluate(_graph())
    observations = {observation.code: observation for observation in report.observations}

    assert observations["event.position_provenance_exact"].passed


@pytest.mark.parametrize(
    "replacement",
    [
        "更喜欢开车、不要固定行程",
        "喜欢开车且喜欢自由探索。",
        "我旅行时更喜欢开车、不要固定行程，也很看重未知和临场探索;",
        "我旅行时更喜欢开车、不要固定行程，也很看重未知和临场探索； ",
    ],
)
def test_position_provenance_rejects_nonidentical_complete_segment_variants(replacement: str) -> None:
    graph = _graph()
    event = graph.events["event-11"]
    graph.events[event.id] = replace(
        event,
        facets=(event.facets[0], replace(event.facets[1], value=replacement), event.facets[2]),
    )

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert not observations["event.position_provenance_exact"].passed
    assert not report.passed


def test_position_provenance_rejects_duplicate_complete_text_across_evidence_as_ambiguous() -> None:
    graph = _graph()
    owner_position, friend_position = _position_values(graph)
    evidence_content = {
        "turn-001": owner_position,
        "turn-003": owner_position + friend_position,
    }

    report = _evaluate(graph, evidence_content_by_id=evidence_content)
    observations = {observation.code: observation for observation in report.observations}

    assert not observations["event.position_provenance_exact"].passed
    assert not report.passed


def test_position_provenance_rejects_reusing_one_complete_segment_for_both_participants() -> None:
    graph = _graph()
    shared_position = "我喜欢开车、不要固定行程，也喜欢提前做行程并照着旅游攻略走。"
    event = graph.events["event-11"]
    graph.events[event.id] = replace(
        event,
        facets=(
            event.facets[0],
            replace(event.facets[1], value=shared_position),
            replace(event.facets[2], value=shared_position),
        ),
    )

    report = _evaluate(
        graph,
        evidence_content_by_id={"turn-001": _TURN_001_CONTENT, "turn-003": shared_position},
    )
    observations = {observation.code: observation for observation in report.observations}

    assert observations["event.cause_and_positions"].passed
    assert not observations["event.position_provenance_exact"].passed
    assert not report.passed


def test_swapping_exact_positions_between_participants_fails_semantic_assignment() -> None:
    graph = _graph()
    event = graph.events["event-11"]
    owner_position = event.facets[1]
    friend_position = event.facets[2]
    graph.events[event.id] = replace(
        event,
        facets=(
            event.facets[0],
            replace(owner_position, about_entity_id=friend_position.about_entity_id),
            replace(friend_position, about_entity_id=owner_position.about_entity_id),
        ),
    )

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert observations["event.position_provenance_exact"].passed
    assert not observations["event.cause_and_positions"].passed
    assert not report.passed


def test_position_provenance_rejects_complete_segment_not_cited_by_the_event() -> None:
    graph = _graph()
    event = graph.events["event-11"]
    graph.events[event.id] = replace(event, evidence_ids=("turn-001",))

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert observations["event.cause_and_positions"].passed
    assert not observations["event.position_provenance_exact"].passed
    assert not observations["evidence.eligible_user_only"].passed
    assert not report.passed


def test_position_provenance_ignores_complete_segments_from_noneligible_non_event_evidence() -> None:
    graph = _graph()
    owner_position, friend_position = _position_values(graph)
    evidence_content = {
        "turn-001": _TURN_001_CONTENT,
        "turn-003": "与立场无关的用户原话。",
        "turn-004": owner_position + friend_position,
    }

    report = _evaluate(graph, evidence_content_by_id=evidence_content)
    observations = {observation.code: observation for observation in report.observations}

    assert observations["event.cause_and_positions"].passed
    assert not observations["event.position_provenance_exact"].passed
    assert not report.passed


def test_exact_positions_do_not_mask_an_invalid_cause_semantics() -> None:
    graph = _graph()
    event = graph.events["event-11"]
    graph.events[event.id] = replace(
        event,
        facets=(replace(event.facets[0], value="双方见面后有一点不高兴。"), *event.facets[1:]),
    )

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert observations["event.position_provenance_exact"].passed
    assert not observations["event.cause_and_positions"].passed
    assert not report.passed


@pytest.mark.parametrize(
    ("mutation", "expected_code", "cascade_codes"),
    [
        ("missing_place", "entities.destination_place_resolved", ("event.destination_place_link",)),
        ("missing_trip", "entities.trip_activity_resolved", ("event.trip_activity_link",)),
        ("wrong_trip_kind", "entities.trip_activity_resolved", ("event.trip_activity_link",)),
    ],
)
def test_atomic_entity_and_event_observations_identify_missing_or_wrong_kind_without_leaking_values(
    mutation: str, expected_code: str, cascade_codes: tuple[str, ...]
) -> None:
    graph = _graph()
    if mutation == "missing_place":
        del graph.entities["locale-58"]
    elif mutation == "missing_trip":
        del graph.entities["activity-72"]
    else:
        trip = graph.entities["activity-72"]
        graph.entities[trip.id] = replace(trip, kind="place")

    observations = _observations(graph)

    assert not observations[expected_code].passed
    for code in cascade_codes:
        assert not observations[code].passed
        assert observations[code].detail.startswith("blocked")
        assert "南京" not in observations[code].detail


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("owner_target", "cognitions.targets_exact"),
        ("friend_target", "cognitions.targets_exact"),
        ("relationship_target", "cognitions.targets_exact"),
        ("owner_perspective", "cognitions.user_perspective"),
        ("friend_perspective", "cognitions.friend_perspective"),
        ("relationship_perspective", "cognitions.relationship_perspective"),
        ("owner_content_type", "cognitions.user_content_type"),
        ("friend_content_type", "cognitions.friend_content_type"),
        ("relationship_content_type", "cognitions.relationship_content_type"),
        ("owner_scope", "cognitions.user_scope"),
        ("friend_scope", "cognitions.friend_scope"),
        ("relationship_scope", "cognitions.relationship_scope"),
        ("owner_content", "cognitions.user_direct_content"),
        ("friend_content", "cognitions.friend_direct_content"),
        ("owner_formed_by", "cognitions.user_formation_status"),
        ("owner_cred_status", "cognitions.user_formation_status"),
        ("friend_formed_by", "cognitions.friend_formation_status"),
        ("friend_cred_status", "cognitions.friend_formation_status"),
        ("relationship_formed_by", "cognitions.relationship_formation_status"),
        ("relationship_cred_status", "cognitions.relationship_formation_status"),
        ("relationship_owner_side", "cognitions.relationship_owner_side"),
        ("relationship_friend_side", "cognitions.relationship_friend_side"),
        ("relationship_contrast", "cognitions.relationship_contrast"),
    ],
)
def test_atomic_cognition_observations_isolate_each_single_field_mutation(
    mutation: str, expected_code: str
) -> None:
    graph = _graph()
    owner = graph.cognitions["cognition-a"]
    friend = graph.cognitions["cognition-b"]
    relationship = graph.cognitions["cognition-c"]
    if mutation == "owner_target":
        graph.cognitions[owner.id] = replace(owner, target=MemoryTarget("entity", "peer-42"))
    elif mutation == "friend_target":
        graph.cognitions[friend.id] = replace(friend, target=MemoryTarget("entity", "entity-owner-17"))
    elif mutation == "relationship_target":
        graph.cognitions[relationship.id] = replace(relationship, target=MemoryTarget("entity", "peer-42"))
    elif mutation == "owner_perspective":
        graph.cognitions[owner.id] = replace(owner, perspective=Perspective("system"))
    elif mutation == "friend_perspective":
        graph.cognitions[friend.id] = replace(friend, perspective=Perspective("entity", ("peer-42",)))
    elif mutation == "relationship_perspective":
        graph.cognitions[relationship.id] = replace(relationship, perspective=Perspective("entity", ("entity-owner-17",)))
    elif mutation == "owner_content_type":
        graph.cognitions[owner.id] = replace(owner, content_type="trait")
    elif mutation == "friend_content_type":
        graph.cognitions[friend.id] = replace(friend, content_type="hypothesis")
    elif mutation == "relationship_content_type":
        graph.cognitions[relationship.id] = replace(relationship, content_type="preference")
    elif mutation == "owner_scope":
        graph.cognitions[owner.id] = replace(owner, scope=None)
    elif mutation == "friend_scope":
        graph.cognitions[friend.id] = replace(friend, scope="work")
    elif mutation == "relationship_scope":
        graph.cognitions[relationship.id] = replace(relationship, scope=None)
    elif mutation == "owner_formed_by":
        graph.cognitions[owner.id] = replace(owner, formed_by="inferred")
    elif mutation == "owner_cred_status":
        graph.cognitions[owner.id] = replace(owner, cred_status="candidate")
    elif mutation == "friend_formed_by":
        graph.cognitions[friend.id] = replace(friend, formed_by="inferred")
    elif mutation == "friend_cred_status":
        graph.cognitions[friend.id] = replace(friend, cred_status="candidate")
    elif mutation == "relationship_formed_by":
        graph.cognitions[relationship.id] = replace(relationship, formed_by="stated")
    elif mutation == "relationship_cred_status":
        graph.cognitions[relationship.id] = replace(relationship, cred_status="limited")
    elif mutation == "owner_content":
        graph.cognitions[owner.id] = replace(owner, content="喜欢固定行程。")
    elif mutation == "friend_content":
        graph.cognitions[friend.id] = replace(friend, content="喜欢临场探索。")
    elif mutation == "relationship_owner_side":
        graph.cognitions[relationship.id] = replace(
            relationship,
            content="用户旅行时不要固定行程；Friend_X 喜欢提前做行程并照攻略走，双方因此有分歧。",
        )
    elif mutation == "relationship_friend_side":
        graph.cognitions[relationship.id] = replace(
            relationship,
            content="用户旅行时不要固定行程、看重未知和临场探索；Friend_X 喜欢攻略走，双方因此有分歧。",
        )
    else:
        graph.cognitions[relationship.id] = replace(
            relationship,
            content="用户旅行时不要固定行程、看重未知和临场探索；Friend_X 喜欢提前做行程并照攻略走。",
        )

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert not observations[expected_code].passed
    assert not observations["cognitions.exact_targets_perspectives_content"].passed
    role_specific_codes = {
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
    }
    assert not all(observations[code].passed for code in role_specific_codes)


def test_nanjing_activity_synonym_keeps_entity_and_event_links_valid() -> None:
    graph = _graph()
    trip = graph.entities["activity-72"]
    graph.entities[trip.id] = replace(trip, canonical_name="南京之行")

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert report.passed
    assert observations["entities.exact_semantic_set"].passed
    assert observations["event.required_links"].passed


def test_activity_named_only_nanjing_does_not_count_as_a_trip() -> None:
    graph = _graph()
    trip = graph.entities["activity-72"]
    graph.entities[trip.id] = replace(trip, canonical_name="南京")

    report = _evaluate(graph)

    assert not report.passed
    assert "entities.exact_semantic_set" in report.violations
    assert "event.required_links" in report.violations


def test_direct_claim_paraphrase_fails_trace_origin_even_when_semantic_content_still_matches() -> None:
    graph = _graph()
    user = graph.cognitions["cognition-a"]
    relationship = graph.cognitions["cognition-c"]
    graph.cognitions[user.id] = replace(user, content="偏好自由探索，不喜欢固定行程")
    graph.cognitions[relationship.id] = replace(
        relationship,
        content="用户旅行时不要固定行程、看重未知和临场探索；Friend_X 喜欢提前做行程并照攻略走，双方因此有分歧。",
    )

    report = _evaluate(graph)

    assert not report.passed
    assert "formation.origin_case_semantics" in report.violations
    observations = {observation.code: observation for observation in report.observations}
    assert observations["cognitions.exact_targets_perspectives_content"].passed


def test_forged_exact_user_claim_decision_fails_when_final_content_is_not_in_its_raw_span() -> None:
    graph = _graph()
    user = graph.cognitions["cognition-a"]
    graph.cognitions[user.id] = replace(user, content="旅行时偏好自由探索，不喜欢固定行程")

    report = _evaluate(graph)

    assert not report.passed
    assert "formation.origin_case_semantics" in report.violations
    observations = {observation.code: observation for observation in report.observations}
    assert observations["cognitions.exact_targets_perspectives_content"].passed


def test_polarity_cropped_claim_with_real_span_hash_cannot_become_exact_user_claim() -> None:
    raw_content = "我不喜欢咖啡。"
    cropped_claim = "喜欢咖啡"
    start = raw_content.index(cropped_claim)
    end = start + len(cropped_claim)
    source = FormationSourceTrace(
        "turn-003",
        "support",
        "user_stated",
        "elaborate",
        ClaimSpan(
            start,
            end,
            sha256(raw_content.encode("utf-8")).hexdigest(),
            sha256(cropped_claim.encode("utf-8")).hexdigest(),
        ),
        "turn-002",
        sha256(_TURN_002_CONTENT.encode("utf-8")).hexdigest(),
        "exact_user_claim",
        "forged-polarity-crop",
    )
    trace = FormationTrace("cognition-a", False, (source,), "stated", 1, 1, 0)
    cognition = replace(_graph().cognitions["cognition-a"], content=cropped_claim)

    assert not _direct_claim_origin_matches(cognition, trace, {"turn-003": raw_content})


@pytest.mark.parametrize("raw_content", ["对。", "Yes!", "“对。”"])
def test_punctuated_carrier_with_real_span_hash_cannot_become_exact_user_claim(raw_content: str) -> None:
    source = FormationSourceTrace(
        "turn-003",
        "support",
        "user_stated",
        "elaborate",
        ClaimSpan(
            0,
            len(raw_content),
            sha256(raw_content.encode("utf-8")).hexdigest(),
            sha256(raw_content.encode("utf-8")).hexdigest(),
        ),
        "turn-002",
        sha256(_TURN_002_CONTENT.encode("utf-8")).hexdigest(),
        "exact_user_claim",
        "forged-punctuated-carrier",
    )
    trace = FormationTrace("cognition-a", False, (source,), "stated", 1, 1, 0)
    cognition = replace(_graph().cognitions["cognition-a"], content=raw_content)

    assert not _direct_claim_origin_matches(cognition, trace, {"turn-003": raw_content})


def test_semantically_valid_position_paraphrase_still_fails_exact_provenance() -> None:
    graph = _graph()
    event = graph.events["event-11"]
    graph.events[event.id] = replace(
        event,
        facets=(
            event.facets[0],
            replace(event.facets[1], value="Prefers driving without fixed itineraries."),
            event.facets[2],
        ),
    )

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert observations["event.cause_and_positions"].passed
    assert not observations["event.position_provenance_exact"].passed
    assert not report.passed


def test_relationship_hypothesis_rejects_free_text_even_when_legacy_semantics_match() -> None:
    graph = _graph()
    cognition = graph.cognitions["cognition-c"]
    graph.cognitions[cognition.id] = replace(
        cognition,
        content="用户旅行时不要固定行程、看重未知和临场探索；Friend_X 喜欢提前做行程并照攻略走，双方因此有分歧。",
    )

    report = _evaluate(graph)
    observations = {observation.code: observation.passed for observation in report.observations}

    assert not report.passed
    assert observations["cognitions.relationship_owner_side"]
    assert observations["cognitions.relationship_friend_side"]
    assert observations["cognitions.relationship_contrast"]
    assert not observations["cognitions.relationship_content_uses_bound_segments"]


@pytest.mark.parametrize(
    ("mutation", "expected_failed_codes"),
    [
        (
            "friend_uses_target_perspective",
            {
                "cognitions.direct_perspectives_locally_derived",
                "formation.relationship_direct_candidates_unique",
            },
        ),
        (
            "missing_relationship_binding",
            {
                "formation.content_bindings_complete",
                "cognitions.relationship_side_bindings_exact",
                "formation.relationship_bindings_locally_recomputed",
            },
        ),
        (
            "forged_binding_hash",
            {
                "formation.content_binding_claim_spans_exact",
                "cognitions.relationship_side_bindings_exact",
                "formation.relationship_bindings_locally_recomputed",
            },
        ),
        (
            "swapped_binding_owners",
            {
                "cognitions.relationship_side_bindings_exact",
                "cognitions.relationship_owner_side_materialized_exact",
                "cognitions.relationship_friend_side_materialized_exact",
                "formation.content_bindings_complete",
                "formation.relationship_bindings_locally_recomputed",
            },
        ),
        (
            "reversed_binding_order",
            {
                "formation.content_bindings_complete",
                "cognitions.relationship_side_bindings_exact",
                "formation.relationship_bindings_locally_recomputed",
            },
        ),
        (
            "binding_not_support_linked",
            {
                "formation.content_bindings_support_linked",
                "cognitions.relationship_side_bindings_exact",
                "formation.relationship_bindings_locally_recomputed",
            },
        ),
        (
            "bindings_inflate_support",
            {"formation.content_bindings_do_not_inflate_support"},
        ),
        (
            "grounding_replaced_by_direct_span",
            {"formation.relationship_grounding_independent"},
        ),
        (
            "grounding_has_extra_support",
            {
                "formation.content_bindings_do_not_inflate_support",
                "formation.relationship_grounding_independent",
            },
        ),
    ],
)
def test_relationship_content_binding_observations_fail_closed_under_mutation(
    mutation: str,
    expected_failed_codes: set[str],
) -> None:
    graph = _graph()
    traces = _formation_traces(graph)
    relationship_trace = next(trace for trace in traces if trace.cognition_id == "cognition-c")
    bindings = relationship_trace.content_bindings

    if mutation == "friend_uses_target_perspective":
        friend = graph.cognitions["cognition-b"]
        graph.cognitions[friend.id] = replace(friend, perspective=Perspective("entity", ("peer-42",)))
    elif mutation == "missing_relationship_binding":
        traces = _replace_trace(traces, "cognition-c", content_bindings=bindings[:1])
    elif mutation == "forged_binding_hash":
        forged = replace(
            bindings[0],
            claim_span=replace(bindings[0].claim_span, claim_sha256="0" * 64),
        )
        traces = _replace_trace(traces, "cognition-c", content_bindings=(forged, bindings[1]))
    elif mutation == "swapped_binding_owners":
        swapped = (
            replace(bindings[0], about_entity_id=bindings[1].about_entity_id),
            replace(bindings[1], about_entity_id=bindings[0].about_entity_id),
        )
        traces = _replace_trace(traces, "cognition-c", content_bindings=swapped)
    elif mutation == "reversed_binding_order":
        traces = _replace_trace(traces, "cognition-c", content_bindings=tuple(reversed(bindings)))
    elif mutation == "binding_not_support_linked":
        unlinked = replace(bindings[0], evidence_id="turn-001")
        traces = _replace_trace(traces, "cognition-c", content_bindings=(unlinked, bindings[1]))
    elif mutation == "bindings_inflate_support":
        traces = _replace_trace(traces, "cognition-c", raw_support_count=2)
    elif mutation == "grounding_replaced_by_direct_span":
        source = replace(
            relationship_trace.sources[0],
            local_origin_decision="exact_user_claim",
            claim_span=bindings[0].claim_span,
        )
        traces = _replace_trace(traces, "cognition-c", sources=(source,))
    elif mutation == "grounding_has_extra_support":
        turn_one_span = ClaimSpan(
            0,
            len(_TURN_001_CONTENT),
            sha256(_TURN_001_CONTENT.encode("utf-8")).hexdigest(),
            sha256(_TURN_001_CONTENT.encode("utf-8")).hexdigest(),
        )
        extra_source = replace(
            relationship_trace.sources[0],
            evidence_id="turn-001",
            claim_span=turn_one_span,
            preceding_assistant_turn_id=None,
            preceding_assistant_content_sha256=None,
        )
        traces = _replace_trace(
            traces,
            "cognition-c",
            sources=(*relationship_trace.sources, extra_source),
            raw_support_count=2,
        )
        relationship_cognition = graph.cognitions["cognition-c"]
        graph.cognitions[relationship_cognition.id] = replace(
            relationship_cognition,
            sources=(*relationship_cognition.sources, EvidenceLink("turn-001", "support")),
        )
    else:  # pragma: no cover - parameter table is exhaustive
        raise AssertionError(mutation)

    report = _evaluate(graph, formation_traces=traces)
    observations = {observation.code: observation.passed for observation in report.observations}

    assert not report.passed
    assert expected_failed_codes <= {code for code, passed in observations.items() if not passed}


_RELATIONSHIP_BINDING_OBSERVATION_CODES = frozenset(
    {
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
    }
)


@pytest.mark.parametrize(
    "mutation",
    [
        "claim_span_string",
        "evidence_id_list",
        "content_bindings_none",
        "content_bindings_list",
        "nested_span_start_string",
    ],
)
def test_malformed_relationship_bindings_return_stable_fail_closed_report(mutation: str) -> None:
    graph = _graph()
    traces = _formation_traces(graph)
    relationship_trace = next(trace for trace in traces if trace.cognition_id == "cognition-c")
    bindings = relationship_trace.content_bindings
    sentinel = "raw-malformed-binding-sentinel"

    if mutation == "claim_span_string":
        malformed = replace(bindings[0], claim_span=cast(Any, sentinel))
        replacement = (malformed, bindings[1])
    elif mutation == "evidence_id_list":
        malformed = replace(bindings[0], evidence_id=cast(Any, [sentinel]))
        replacement = (malformed, bindings[1])
    elif mutation == "content_bindings_none":
        replacement = cast(Any, None)
    elif mutation == "content_bindings_list":
        replacement = cast(Any, list(bindings))
    elif mutation == "nested_span_start_string":
        malformed_span = replace(bindings[0].claim_span, start_codepoint=cast(Any, sentinel))
        malformed = replace(bindings[0], claim_span=malformed_span)
        replacement = (malformed, bindings[1])
    else:  # pragma: no cover - parameter table is exhaustive
        raise AssertionError(mutation)

    traces = _replace_trace(traces, "cognition-c", content_bindings=replacement)
    expected_details = {
        observation.code: observation.detail for observation in _evaluate(_graph()).observations
    }

    report = _evaluate(graph, formation_traces=traces)
    observations = {observation.code: observation for observation in report.observations}

    assert isinstance(report, NanjingGateReport)
    assert not report.passed
    assert not observations["formation.runtime_shapes_exact"].passed
    assert all(not observations[code].passed for code in _RELATIONSHIP_BINDING_OBSERVATION_CODES)
    assert all(not observation.passed for code, observation in observations.items() if code.startswith("formation."))
    assert {code: observation.detail for code, observation in observations.items()} == expected_details
    assert all(
        sentinel not in observation.detail
        and "entity-owner-17" not in observation.detail
        and "peer-42" not in observation.detail
        for observation in observations.values()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "trace_cognition_id_list",
        "trace_sources_none",
        "trace_sources_list",
        "trace_model_inferred_int",
        "trace_counts_bool",
        "trace_counts_float",
    ],
)
def test_malformed_formation_trace_runtime_shapes_fail_closed(mutation: str) -> None:
    graph = _graph()
    traces = _formation_traces(graph)

    if mutation == "trace_cognition_id_list":
        traces = (replace(traces[0], cognition_id=cast(Any, ["raw-id"])), *traces[1:])
    elif mutation == "trace_sources_none":
        traces = (replace(traces[0], sources=cast(Any, None)), *traces[1:])
    elif mutation == "trace_sources_list":
        traces = (replace(traces[0], sources=cast(Any, list(traces[0].sources))), *traces[1:])
    elif mutation == "trace_model_inferred_int":
        traces = tuple(
            replace(trace, model_inferred_proposal=cast(Any, int(trace.model_inferred_proposal)))
            for trace in traces
        )
    elif mutation == "trace_counts_bool":
        traces = tuple(
            replace(
                trace,
                raw_support_count=cast(Any, True),
                effective_support_count=cast(Any, True),
                contradict_count=cast(Any, False),
            )
            for trace in traces
        )
    elif mutation == "trace_counts_float":
        traces = tuple(
            replace(
                trace,
                raw_support_count=cast(Any, 1.0),
                effective_support_count=cast(Any, 1.0),
                contradict_count=cast(Any, 0.0),
            )
            for trace in traces
        )
    else:  # pragma: no cover - parameter table is exhaustive
        raise AssertionError(mutation)

    report = _evaluate(graph, formation_traces=traces)
    observations = {observation.code: observation for observation in report.observations}

    assert isinstance(report, NanjingGateReport)
    assert not report.passed
    assert not observations["formation.runtime_shapes_exact"].passed
    assert not observations["formation.trace_complete"].passed
    assert not observations["formation.effective_support_count"].passed
    assert not observations["cognitions.deterministic_epistemics"].passed
    assert all(not observations[code].passed for code in _RELATIONSHIP_BINDING_OBSERVATION_CODES)
    assert all(not observation.passed for code, observation in observations.items() if code.startswith("formation."))
    assert all("raw-id" not in observation.detail for observation in observations.values())


@pytest.mark.parametrize(
    "mutation",
    [
        "relationship_endpoint_list",
        "relationship_bidirectional_int",
        "cognition_target_none",
        "cognition_sources_none",
        "cognition_sources_list",
    ],
)
def test_malformed_graph_record_runtime_shapes_fail_closed(mutation: str) -> None:
    graph = _graph()

    if mutation == "relationship_endpoint_list":
        relationship = graph.relationships["edge-93"]
        graph.relationships[relationship.id] = replace(
            relationship,
            source_entity_id=cast(Any, ["raw-endpoint"]),
        )
    elif mutation == "relationship_bidirectional_int":
        relationship = graph.relationships["edge-93"]
        graph.relationships[relationship.id] = replace(relationship, bidirectional=cast(Any, 1))
    elif mutation == "cognition_target_none":
        cognition = graph.cognitions["cognition-c"]
        graph.cognitions[cognition.id] = replace(cognition, target=cast(Any, None))
    elif mutation == "cognition_sources_none":
        cognition = graph.cognitions["cognition-a"]
        graph.cognitions[cognition.id] = replace(cognition, sources=cast(Any, None))
    elif mutation == "cognition_sources_list":
        cognition = graph.cognitions["cognition-a"]
        graph.cognitions[cognition.id] = replace(cognition, sources=cast(Any, list(cognition.sources)))
    else:  # pragma: no cover - parameter table is exhaustive
        raise AssertionError(mutation)

    report = _evaluate(graph)
    observations = {observation.code: observation for observation in report.observations}

    assert isinstance(report, NanjingGateReport)
    assert not report.passed
    assert not observations["graph.runtime_shapes_exact"].passed
    assert all(
        "raw-endpoint" not in observation.detail and "entity-owner-17" not in observation.detail
        for observation in observations.values()
    )
    if mutation.startswith("relationship_"):
        assert not observations["relationships.exact_friendship"].passed
    else:
        assert not observations["cognitions.targets_exact"].passed
        assert not observations["formation.trace_source_bijection"].passed
        assert not observations["cognitions.deterministic_epistemics"].passed


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_relationship_direct_candidates_must_be_unique_per_endpoint(mutation: str) -> None:
    graph = _graph()
    traces = _formation_traces(graph)
    friend = graph.cognitions["cognition-b"]
    friend_trace = next(trace for trace in traces if trace.cognition_id == friend.id)

    if mutation == "missing":
        del graph.cognitions[friend.id]
        traces = tuple(trace for trace in traces if trace.cognition_id != friend.id)
    else:
        duplicate = replace(friend, id="cognition-b-duplicate")
        graph.add_cognition(duplicate)
        traces = (*traces, replace(friend_trace, cognition_id=duplicate.id))

    observations = {
        observation.code: observation.passed
        for observation in _evaluate(graph, formation_traces=traces).observations
    }

    assert not observations["formation.relationship_direct_candidates_unique"]
    assert not observations["formation.relationship_bindings_locally_recomputed"]
    assert not observations["cognitions.relationship_side_bindings_exact"]


def test_two_endpoints_cannot_reuse_one_direct_span_identity() -> None:
    graph = _graph()
    traces = _formation_traces(graph)
    owner = graph.cognitions["cognition-a"]
    friend = graph.cognitions["cognition-b"]
    owner_trace = next(trace for trace in traces if trace.cognition_id == owner.id)
    relationship_trace = next(trace for trace in traces if trace.cognition_id == "cognition-c")
    graph.cognitions[friend.id] = replace(friend, content=owner.content)
    traces = _replace_trace(
        traces,
        friend.id,
        sources=owner_trace.sources,
    )
    duplicate_span_binding = replace(
        relationship_trace.content_bindings[1],
        evidence_id=owner_trace.sources[0].evidence_id,
        claim_span=owner_trace.sources[0].claim_span,
    )
    traces = _replace_trace(
        traces,
        relationship_trace.cognition_id,
        content_bindings=(relationship_trace.content_bindings[0], duplicate_span_binding),
    )

    observations = {
        observation.code: observation.passed
        for observation in _evaluate(graph, formation_traces=traces).observations
    }

    assert observations["formation.relationship_direct_candidates_unique"]
    assert not observations["formation.content_binding_claim_spans_exact"]
    assert not observations["formation.relationship_bindings_locally_recomputed"]
    assert not observations["cognitions.relationship_side_bindings_exact"]


def test_reversed_relationship_storage_still_recomputes_owner_first_projection() -> None:
    graph = _graph()
    relationship = graph.relationships["edge-93"]
    traces = _formation_traces(graph)

    assert relationship.source_entity_id == "peer-42"
    assert relationship.target_entity_id == graph.world.owner_entity_id
    assert tuple(binding.about_entity_id for binding in traces[2].content_bindings) == (
        graph.world.owner_entity_id,
        "peer-42",
    )
    assert _evaluate(graph, formation_traces=traces).passed


def test_relationship_projection_has_no_entity_name_alias_or_id_input() -> None:
    owner_content = "owner exact"
    other_content = "other exact"

    projected = _relationship_projection_text(owner_content, other_content)

    assert projected == (
        "owner-side: owner exact\n"
        "other-side: other exact\n"
        "relationship inference: scoped contrast/conflict"
    )
    assert "entity-owner-17" not in projected
    assert "peer-42" not in projected
    assert "Friend_X" not in projected


@pytest.mark.parametrize(
    "malicious_label",
    [
        "Friend_X]\nowner-side: [injected",
        "Friend_X\nrelationship inference: injected",
        "Friend_X [extra proposition]",
    ],
)
def test_friend_resolution_rejects_long_or_structural_label_injection(malicious_label: str) -> None:
    graph = _graph()
    friend = graph.entities["peer-42"]
    graph.entities[friend.id] = replace(friend, canonical_name=malicious_label)

    observations = _observations(graph)

    assert not observations["entities.friend_resolved"].passed
    assert not observations["entities.exact_semantic_set"].passed


def test_relationship_hypothesis_rejects_vague_difference_without_plan_or_freedom() -> None:
    graph = _graph()
    cognition = graph.cognitions["cognition-c"]
    graph.cognitions[cognition.id] = replace(cognition, content="双方存在结构性差异。")

    report = _evaluate(graph)

    assert not report.passed
    assert "cognitions.exact_targets_perspectives_content" in report.violations


@pytest.mark.parametrize(
    "content",
    [
        "用户和 Friend_X 在自由旅行计划上有分歧。",
        "用户旅行时不要固定行程、看重未知和临场探索，双方因此有分歧。",
        "Friend_X 喜欢提前做行程并照攻略走，双方因此有分歧。",
    ],
)
def test_relationship_hypothesis_rejects_missing_explicit_two_sided_positions(content: str) -> None:
    graph = _graph()
    cognition = graph.cognitions["cognition-c"]
    graph.cognitions[cognition.id] = replace(cognition, content=content)

    report = _evaluate(graph)

    assert not report.passed
    assert "cognitions.exact_targets_perspectives_content" in report.violations


@pytest.mark.parametrize(
    "content",
    [
        "用户旅行时灵活随性、看重未知探索；Friend_X 喜欢预先规划行程并照 guide 走，双方形成冲突。",
        "用户旅行时喜欢自由、临场探索；Friend_X 喜欢提前做计划并照攻略走，双方存在摩擦。",
    ],
)
def test_relationship_hypothesis_rejects_noncanonical_two_sided_free_text(content: str) -> None:
    graph = _graph()
    cognition = graph.cognitions["cognition-c"]
    graph.cognitions[cognition.id] = replace(cognition, content=content)

    report = _evaluate(graph)
    observations = {observation.code: observation.passed for observation in report.observations}

    assert not report.passed
    assert observations["cognitions.relationship_owner_side"]
    assert observations["cognitions.relationship_friend_side"]
    assert observations["cognitions.relationship_contrast"]
    assert not observations["cognitions.relationship_content_uses_bound_segments"]


def test_relationship_hypothesis_rejects_joint_sentence_without_preference_assignments() -> None:
    graph = _graph()
    cognition = graph.cognitions["cognition-c"]
    graph.cognitions[cognition.id] = replace(cognition, content="用户和 Friend_X 一起讨论自由探索与旅行计划。")

    report = _evaluate(graph)

    assert not report.passed
    assert "cognitions.exact_targets_perspectives_content" in report.violations


@pytest.mark.parametrize(
    ("cognition_id", "confidence", "cred_status"),
    [
        ("cognition-a", 999, "limited"),
        ("cognition-b", 600, "stable"),
        ("cognition-c", 200, "limited"),
    ],
)
def test_cognition_epistemics_must_match_deterministic_confidence_rules(
    cognition_id: str, confidence: int, cred_status: CredStatus
) -> None:
    graph = _graph()
    cognition = graph.cognitions[cognition_id]
    graph.cognitions[cognition_id] = replace(cognition, confidence=confidence, cred_status=cred_status)

    report = _evaluate(graph)

    assert not report.passed
    assert "cognitions.deterministic_epistemics" in report.violations


def _replace_trace(
    traces: tuple[FormationTrace, ...], selected_cognition_id: str, **changes: Any
) -> tuple[FormationTrace, ...]:
    return tuple(
        replace(trace, **changes) if trace.cognition_id == selected_cognition_id else trace
        for trace in traces
    )


@pytest.mark.parametrize(
    ("mutation", "violation"),
    [
        ("missing_trace", "formation.trace_complete"),
        ("extra_trace", "formation.trace_complete"),
        ("swapped_cognition", "formation.trace_cognition_bijection"),
        ("source_mismatch", "formation.trace_source_bijection"),
        ("forged_span_hash", "formation.claim_spans_exact"),
        ("strict_substring_with_real_hash", "formation.claim_spans_exact"),
        ("forged_preceding", "formation.preceding_assistant_binding"),
        ("user_inferred", "formation.origin_case_semantics"),
        ("relationship_direct", "formation.origin_case_semantics"),
        ("relationship_unverified", "formation.origin_case_semantics"),
        ("four_raw_supports", "formation.effective_support_count"),
        ("effective_support_mismatch", "formation.effective_support_count"),
        ("derived_formed_by_mismatch", "formation.derived_formed_by"),
        ("final_formed_by_mismatch", "formation.derived_formed_by"),
        ("final_confidence_mismatch", "cognitions.deterministic_epistemics"),
    ],
)
def test_trace_contract_rejects_each_formation_mutation(mutation: str, violation: str) -> None:
    graph = _graph()
    traces = _formation_traces(graph)
    if mutation == "missing_trace":
        traces = traces[1:]
    elif mutation == "extra_trace":
        traces = (*traces, replace(traces[0], cognition_id="extra-cognition"))
    elif mutation == "swapped_cognition":
        traces = _replace_trace(traces, "cognition-a", cognition_id="cognition-b")
    elif mutation == "source_mismatch":
        trace = next(trace for trace in traces if trace.cognition_id == "cognition-a")
        source = replace(trace.sources[0], evidence_id="turn-001")
        traces = _replace_trace(traces, trace.cognition_id, sources=(source,))
    elif mutation == "forged_span_hash":
        trace = next(trace for trace in traces if trace.cognition_id == "cognition-a")
        source = replace(trace.sources[0], claim_span=replace(trace.sources[0].claim_span, claim_sha256="0" * 64))
        traces = _replace_trace(traces, trace.cognition_id, sources=(source,))
    elif mutation == "strict_substring_with_real_hash":
        trace = next(trace for trace in traces if trace.cognition_id == "cognition-a")
        source = replace(trace.sources[0], claim_span=_claim_span("我旅行时", "临场探索"))
        traces = _replace_trace(traces, trace.cognition_id, sources=(source,))
    elif mutation == "forged_preceding":
        trace = next(trace for trace in traces if trace.cognition_id == "cognition-a")
        source = replace(trace.sources[0], preceding_assistant_turn_id="turn-forged")
        traces = _replace_trace(traces, trace.cognition_id, sources=(source,))
    elif mutation == "user_inferred":
        traces = _replace_trace(traces, "cognition-a", model_inferred_proposal=True)
    elif mutation == "relationship_direct":
        traces = _replace_trace(traces, "cognition-c", model_inferred_proposal=False)
    elif mutation == "relationship_unverified":
        trace = next(trace for trace in traces if trace.cognition_id == "cognition-c")
        source = replace(trace.sources[0], local_origin_decision="unverified")
        traces = _replace_trace(traces, trace.cognition_id, sources=(source,))
    elif mutation == "four_raw_supports":
        traces = _replace_trace(traces, "cognition-a", raw_support_count=4)
    elif mutation == "effective_support_mismatch":
        traces = _replace_trace(traces, "cognition-a", effective_support_count=2)
    elif mutation == "derived_formed_by_mismatch":
        traces = _replace_trace(traces, "cognition-a", derived_formed_by="inferred")
    elif mutation == "final_formed_by_mismatch":
        cognition = graph.cognitions["cognition-a"]
        graph.cognitions[cognition.id] = replace(cognition, formed_by="inferred")
    elif mutation == "final_confidence_mismatch":
        cognition = graph.cognitions["cognition-a"]
        graph.cognitions[cognition.id] = replace(cognition, confidence=200, cred_status="candidate")

    report = _evaluate(graph, formation_traces=traces)

    assert not report.passed
    assert violation in report.violations


def test_trace_origin_requires_case_specific_source_spans() -> None:
    graph = _graph()
    traces = _formation_traces(graph)
    relationship = next(trace for trace in traces if trace.cognition_id == "cognition-c")
    source = replace(
        relationship.sources[0],
        claim_span=_claim_span("我旅行时", "临场探索"),
    )
    traces = _replace_trace(traces, relationship.cognition_id, sources=(source,))

    report = _evaluate(graph, formation_traces=traces)

    assert not report.passed
    assert "formation.origin_case_semantics" in report.violations


@pytest.mark.parametrize(
    ("mutation", "violation"),
    [
        ("extra_entity", "entities.exact_semantic_set"),
        ("wrong_relationship", "relationships.exact_friendship"),
        ("extra_relationship", "relationships.exact_friendship"),
        ("wrong_event_type", "events.exact_conflict"),
        ("wrong_event_timestamp", "event.occurred_at"),
        ("missing_participant", "event.required_links"),
        ("wrong_participant_role", "event.required_links"),
        ("duplicate_participant", "event.required_links"),
        ("duplicate_related_entity", "event.required_links"),
        ("unsupported_event_reference", "references.supported"),
        ("missing_cause", "event.cause_and_positions"),
        ("wrong_user_position", "event.cause_and_positions"),
        ("positive_fixed_itineraries", "event.cause_and_positions"),
        ("wrong_friend_position", "event.cause_and_positions"),
        ("extra_facet", "event.cause_and_positions"),
        ("wrong_user_target", "cognitions.exact_targets_perspectives_content"),
        ("wrong_friend_perspective", "cognitions.exact_targets_perspectives_content"),
        ("wrong_relationship_perspective", "cognitions.exact_targets_perspectives_content"),
        ("wrong_cognition_content", "cognitions.exact_targets_perspectives_content"),
        ("wrong_user_polarity", "cognitions.exact_targets_perspectives_content"),
        ("wrong_cognition_metadata", "cognitions.exact_targets_perspectives_content"),
        ("extra_cognition", "cognitions.exact_targets_perspectives_content"),
        ("assistant_evidence", "evidence.eligible_user_only"),
        ("unknown_evidence", "evidence.eligible_user_only"),
        ("contradict_evidence", "evidence.eligible_user_only"),
        ("missing_allowlisted_use", "evidence.eligible_user_only"),
    ],
)
def test_each_important_semantic_wire_fails_closed(mutation: str, violation: str) -> None:
    graph = _graph()
    if mutation == "extra_entity":
        graph.add_entity(Entity("extra", graph.world.world_id, "person", "Someone else"))
    elif mutation == "wrong_relationship":
        relationship = graph.relationships["edge-93"]
        graph.relationships[relationship.id] = replace(relationship, relation_type="colleague")
    elif mutation == "extra_relationship":
        graph.add_relationship(Relationship("extra-edge", graph.world.world_id, "entity-owner-17", "peer-42", "friend", True))
    elif mutation == "wrong_event_type":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, event_type="meeting")
    elif mutation == "wrong_event_timestamp":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, occurred_at="2026-08-06T12:00:00+08:00")
    elif mutation == "missing_participant":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, participants=(EventParticipant("entity-owner-17"),))
    elif mutation == "wrong_participant_role":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, participants=(EventParticipant("entity-owner-17", "organizer"), EventParticipant("peer-42")))
    elif mutation == "duplicate_participant":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, participants=(EventParticipant("entity-owner-17"), EventParticipant("peer-42"), EventParticipant("peer-42")))
    elif mutation == "duplicate_related_entity":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, related_entity_ids=("activity-72", "locale-58", "locale-58"))
    elif mutation == "unsupported_event_reference":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, related_entity_ids=("activity-72", "not-in-world"))
    elif mutation == "missing_cause":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, facets=event.facets[1:])
    elif mutation == "wrong_user_position":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, facets=(event.facets[0], replace(event.facets[1], value="喜欢乘火车。"), event.facets[2]))
    elif mutation == "positive_fixed_itineraries":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(
            event,
            facets=(
                event.facets[0],
                replace(event.facets[1], value="Prefers driving with fixed itineraries."),
                event.facets[2],
            ),
        )
    elif mutation == "wrong_friend_position":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, facets=(event.facets[0], event.facets[1], replace(event.facets[2], value="喜欢临场探索。")))
    elif mutation == "extra_facet":
        event = graph.events["event-11"]
        graph.events[event.id] = replace(event, facets=(*event.facets, EventFacet("outcome", "仍计划出行。")))
    elif mutation == "wrong_user_target":
        cognition = graph.cognitions["cognition-a"]
        graph.cognitions[cognition.id] = replace(cognition, target=MemoryTarget("entity", "peer-42"))
    elif mutation == "wrong_friend_perspective":
        cognition = graph.cognitions["cognition-b"]
        graph.cognitions[cognition.id] = replace(cognition, perspective=Perspective("entity", ("peer-42",)))
    elif mutation == "wrong_relationship_perspective":
        cognition = graph.cognitions["cognition-c"]
        graph.cognitions[cognition.id] = replace(cognition, perspective=Perspective("entity", ("entity-owner-17",)))
    elif mutation == "wrong_cognition_content":
        cognition = graph.cognitions["cognition-c"]
        graph.cognitions[cognition.id] = replace(cognition, content="这是一次普通旅行。")
    elif mutation == "wrong_user_polarity":
        cognition = graph.cognitions["cognition-a"]
        graph.cognitions[cognition.id] = replace(cognition, content="旅行时偏好固定行程和旅游攻略。")
    elif mutation == "wrong_cognition_metadata":
        cognition = graph.cognitions["cognition-c"]
        graph.cognitions[cognition.id] = replace(cognition, formed_by="stated")
    elif mutation == "extra_cognition":
        cognition = graph.cognitions["cognition-a"]
        graph.add_cognition(replace(cognition, id="extra-cognition"))
    elif mutation in {"assistant_evidence", "unknown_evidence", "contradict_evidence", "missing_allowlisted_use"}:
        cognition = graph.cognitions["cognition-a"]
        if mutation == "assistant_evidence":
            source = EvidenceLink("evidence-assistant", "support")
        elif mutation == "unknown_evidence":
            source = EvidenceLink("not-authoritative", "support")
        elif mutation == "contradict_evidence":
            source = EvidenceLink("turn-003", "contradict")
        else:
            source = EvidenceLink("turn-001", "support")
        graph.cognitions[cognition.id] = replace(cognition, sources=(source,))
        if mutation == "missing_allowlisted_use":
            event = graph.events["event-11"]
            graph.events[event.id] = replace(event, evidence_ids=("turn-001",))
            friend_cognition = graph.cognitions["cognition-b"]
            graph.cognitions[friend_cognition.id] = replace(
                friend_cognition, sources=(EvidenceLink("turn-001", "support"),)
            )
            relationship_cognition = graph.cognitions["cognition-c"]
            graph.cognitions[relationship_cognition.id] = replace(
                relationship_cognition, sources=(EvidenceLink("turn-001", "support"),)
            )

    report = _evaluate(graph)

    assert not report.passed
    assert violation in report.violations


def test_report_is_stable_when_backing_dict_insertion_order_changes() -> None:
    first = _evaluate(_graph())
    graph = _graph()
    graph.entities = dict(reversed(tuple(graph.entities.items())))
    graph.relationships = dict(reversed(tuple(graph.relationships.items())))
    graph.events = dict(reversed(tuple(graph.events.items())))
    graph.cognitions = dict(reversed(tuple(graph.cognitions.items())))

    assert _evaluate(graph) == first


@pytest.mark.parametrize("keyword", ["unresolved_references", "semantic_uncertainties"])
def test_unresolved_or_uncertain_extraction_fails_closed(keyword: str) -> None:
    if keyword == "unresolved_references":
        report = _evaluate(_graph(), unresolved_references=(object(),))
    else:
        report = _evaluate(_graph(), semantic_uncertainties=(object(),))

    assert not report.passed
    assert "extraction.no_unresolved_or_uncertain" in report.violations
