"""Stage 4 pure contracts for deterministic, history-preserving world evolution."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from memoweft.confidence import compute_confidence, derive_cred_status
from memoweft.types import ConfidenceInputs, ContentType, EvidenceLink, FormedBy
from typing import cast
from memoweft.world import (
    AcceptedEvolutionStep,
    ClaimSpan,
    Entity,
    EventParticipant,
    EvolutionStep,
    FormationSourceTrace,
    FormationTrace,
    MemoryTarget,
    MemoryWorldGraph,
    PersonalWorld,
    Perspective,
    Relationship,
    StructuredClaim,
    WorldCognition,
    WorldDelta,
    WorldEvent,
    WorldEvolutionPlan,
    WorldEvolutionValidationError,
    evolution_plan_from_data,
    evolution_plan_to_data,
    project_cognition_lifecycle,
    project_relationship_state,
    relationship_event_chain,
    superseding_cognition_pairs,
)


WORLD_ID = "world:yun"
OWNER_ID = "person:yun"
FRIEND_ID = "person:friend-x"
RELATIONSHIP_ID = "relationship:yun-friend-x"


def _score(content_type: str, formed_by: str, support: int, contradict: int) -> tuple[int, str]:
    confidence = compute_confidence(
        ConfidenceInputs(
            cast(ContentType, content_type),
            cast(FormedBy, formed_by),
            support,
            contradict,
        )
    )
    return confidence, derive_cred_status(
        confidence,
        contradict,
        cast(ContentType, content_type),
        support_count=support,
    )


def _cognition(
    cognition_id: str,
    target: MemoryTarget,
    content: str,
    evidence_id: str,
    *,
    content_type: str = "fact",
    perspective: Perspective | None = None,
    scope: str | None = None,
) -> WorldCognition:
    confidence, status = _score(content_type, "stated", 1, 0)
    return WorldCognition(
        cognition_id,
        WORLD_ID,
        target,
        content,
        content_type,  # type: ignore[arg-type]
        "stated",
        confidence,
        status,  # type: ignore[arg-type]
        perspective or Perspective("entity", (OWNER_ID,)),
        sources=(EvidenceLink(evidence_id, "support"),),
        scope=scope,
    )


def _trace(cognition: WorldCognition, evidence_id: str) -> FormationTrace:
    return FormationTrace(
        cognition.id,
        False,
        (
            FormationSourceTrace(
                evidence_id,
                "support",
                "user_stated",
                "elaborate",
                ClaimSpan(
                    0,
                    len(cognition.content),
                    "a" * 64,
                    sha256(cognition.content.encode("utf-8")).hexdigest(),
                ),
                None,
                None,
                "exact_user_claim",
                "formation.exact_user_claim",
            ),
        ),
        "stated",
        1,
        1,
        0,
    )


def _event(event_id: str, event_type: str, summary: str, occurred_at: str, evidence_id: str) -> WorldEvent:
    return WorldEvent(
        event_id,
        WORLD_ID,
        event_type,
        summary,
        occurred_at,
        participants=(
            EventParticipant(OWNER_ID, "participant"),
            EventParticipant(FRIEND_ID, "participant"),
        ),
        relationship_ids=(RELATIONSHIP_ID,),
        evidence_ids=(evidence_id,),
    )


def _relationship_graph() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld(WORLD_ID, OWNER_ID))
    graph.add_entity(Entity(OWNER_ID, WORLD_ID, "person", "Yun"))
    graph.add_entity(Entity(FRIEND_ID, WORLD_ID, "person", "Friend_X"))
    graph.add_relationship(
        Relationship(
            RELATIONSHIP_ID,
            WORLD_ID,
            OWNER_ID,
            FRIEND_ID,
            "friend",
            bidirectional=True,
        )
    )
    graph.add_event(
        _event(
            "event:argument",
            "interpersonal_conflict",
            "Yun and Friend_X argued.",
            "2026-06-01T09:00:00+08:00",
            "e:argument",
        )
    )
    graph.add_cognition(
        _cognition(
            "cog:relationship-strained",
            MemoryTarget("relationship", RELATIONSHIP_ID),
            "The friendship is strained after the argument.",
            "e:argument",
            content_type="state",
            scope="relationship_state",
        )
    )
    return graph


def _event_state_plan(
    *,
    predecessor_event_id: str,
    predecessor_state_id: str,
    event_id: str,
    event_type: str,
    summary: str,
    occurred_at: str,
    evidence_id: str,
    state_id: str,
    state: str,
    event_relation: str,
) -> WorldEvolutionPlan:
    event = _event(event_id, event_type, summary, occurred_at, evidence_id)
    state_cognition = _cognition(
        state_id,
        MemoryTarget("relationship", RELATIONSHIP_ID),
        f"The friendship is {state}.",
        evidence_id,
        content_type="state",
        scope="relationship_state",
    )
    delta = WorldDelta(
        WORLD_ID,
        (evidence_id,),
        new_events=(event,),
        new_cognitions=(state_cognition,),
        formation_traces=(_trace(state_cognition, evidence_id),),
    )
    return WorldEvolutionPlan(
        delta,
        (
            EvolutionStep(
                f"evolution:{event_id}:link",
                "event_link",
                event_relation,  # type: ignore[arg-type]
                MemoryTarget("relationship", RELATIONSHIP_ID),
                (predecessor_event_id,),
                (event_id,),
                occurred_at,
                (evidence_id,),
            ),
            EvolutionStep(
                f"evolution:{event_id}:state",
                "relationship_state",
                state,  # type: ignore[arg-type]
                MemoryTarget("relationship", RELATIONSHIP_ID),
                (predecessor_state_id,),
                (state_id,),
                occurred_at,
                (evidence_id,),
            ),
        ),
    )


def test_argument_apology_repair_keeps_history_and_derives_current_state() -> None:
    base = _relationship_graph()
    apology = _event_state_plan(
        predecessor_event_id="event:argument",
        predecessor_state_id="cog:relationship-strained",
        event_id="event:apology",
        event_type="apology",
        summary="Friend_X apologised to Yun.",
        occurred_at="2026-06-02T09:00:00+08:00",
        evidence_id="e:apology",
        state_id="cog:relationship-repairing",
        state="repairing",
        event_relation="responds_to",
    )
    after_apology = apology.apply_to(base, {"e:apology"})
    repair = _event_state_plan(
        predecessor_event_id="event:apology",
        predecessor_state_id="cog:relationship-repairing",
        event_id="event:repair",
        event_type="relationship_repair",
        summary="Yun and Friend_X repaired the friendship.",
        occurred_at="2026-06-03T09:00:00+08:00",
        evidence_id="e:repair",
        state_id="cog:relationship-repaired",
        state="repaired",
        event_relation="repairs",
    )
    final_graph = repair.apply_to(
        after_apology,
        {"e:repair"},
        superseded_cognition_ids=frozenset({"cog:relationship-strained"}),
        known_transition_ids=frozenset(step.id for step in apology.steps),
    )
    accepted = tuple(
        AcceptedEvolutionStep("review:apology", 1, step) for step in apology.steps
    ) + tuple(AcceptedEvolutionStep("review:repair", 2, step) for step in repair.steps)

    assert final_graph.relationships[RELATIONSHIP_ID].status is None
    assert [
        event.id for event in relationship_event_chain(final_graph, accepted, RELATIONSHIP_ID)
    ] == ["event:argument", "event:apology", "event:repair"]
    state = project_relationship_state(final_graph, accepted, RELATIONSHIP_ID)
    assert state is not None
    assert (state.state, state.cognition_id, state.revision) == (
        "repaired",
        "cog:relationship-repaired",
        2,
    )
    assert set(final_graph.events) == {"event:argument", "event:apology", "event:repair"}
    assert superseding_cognition_pairs(apology) == (
        ("cog:relationship-strained", "cog:relationship-repairing", "repairing"),
    )


def test_nanjing_correction_narrows_scope_and_preserves_prior() -> None:
    graph = _relationship_graph()
    prior = _cognition(
        "cog:friend-planning-style",
        MemoryTarget("entity", FRIEND_ID),
        "Friend_X always needs a fully planned itinerary.",
        "e:planning",
    )
    graph.add_cognition(prior)
    successor = _cognition(
        "cog:friend-planning-style:nanjing",
        prior.target,
        "Friend_X wanted a plan on that first Nanjing trip because they were nervous.",
        "e:nanjing-correction",
        scope="that_nanjing_trip",
    )
    plan = WorldEvolutionPlan(
        WorldDelta(
            WORLD_ID,
            ("e:nanjing-correction",),
            new_cognitions=(successor,),
            formation_traces=(_trace(successor, "e:nanjing-correction"),),
        ),
        (
            EvolutionStep(
                "evolution:nanjing:narrow-planning-style",
                "cognition_change",
                "narrows",
                prior.target,
                (prior.id,),
                (successor.id,),
                "2026-06-04T09:00:00+08:00",
                ("e:nanjing-correction",),
            ),
        ),
    )

    preview = plan.apply_to(graph, {"e:nanjing-correction"})

    assert preview.cognitions[prior.id] == prior
    assert preview.cognitions[successor.id].scope == "that_nanjing_trip"
    assert superseding_cognition_pairs(plan) == (
        (prior.id, successor.id, "narrows"),
    )


def test_contradiction_updates_same_cognition_and_recomputes_confidence() -> None:
    graph = _relationship_graph()
    prior = _cognition(
        "cog:friend-likes-crowds",
        MemoryTarget("entity", FRIEND_ID),
        "Friend_X likes crowded places.",
        "e:crowds",
    )
    graph.add_cognition(prior)
    confidence, status = _score(prior.content_type, prior.formed_by, 1, 1)
    updated = replace(
        prior,
        confidence=confidence,
        cred_status=status,  # type: ignore[arg-type]
        sources=prior.sources + (EvidenceLink("e:not-crowds", "contradict"),),
    )
    plan = WorldEvolutionPlan(
        WorldDelta(WORLD_ID, ("e:not-crowds",)),
        (
            EvolutionStep(
                "evolution:crowds:contradiction",
                "cognition_change",
                "contradicts",
                prior.target,
                (prior.id,),
                (prior.id,),
                "2026-06-05T09:00:00+08:00",
                ("e:not-crowds",),
            ),
        ),
        cognition_updates=(updated,),
    )

    preview = plan.apply_to(graph, {"e:crowds", "e:not-crowds"})

    assert preview.cognitions[prior.id] == updated
    assert updated.confidence < prior.confidence
    assert superseding_cognition_pairs(plan) == ()


def test_cognition_evidence_change_cannot_rewrite_the_structured_proposition() -> None:
    graph = _relationship_graph()
    prior = replace(
        _cognition(
            "cog:structured-evaluation",
            MemoryTarget("relationship", RELATIONSHIP_ID),
            "The owner considers this relationship reliable.",
            "e:evaluation",
        ),
        structured_claim=StructuredClaim(
            "evaluation",
            value="reliable",
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    graph.add_cognition(prior)
    prior_claim = prior.structured_claim
    assert prior_claim is not None
    confidence, status = _score(prior.content_type, prior.formed_by, 1, 1)
    rewritten = replace(
        prior,
        confidence=confidence,
        cred_status=status,  # type: ignore[arg-type]
        sources=prior.sources + (EvidenceLink("e:opposes", "contradict"),),
        structured_claim=replace(prior_claim, value="unreliable"),
    )
    plan = WorldEvolutionPlan(
        WorldDelta(WORLD_ID, ("e:opposes",)),
        (
            EvolutionStep(
                "evolution:structured-evaluation:contradiction",
                "cognition_change",
                "contradicts",
                prior.target,
                (prior.id,),
                (prior.id,),
                "2026-06-05T09:00:00+08:00",
                ("e:opposes",),
            ),
        ),
        cognition_updates=(rewritten,),
    )

    with pytest.raises(WorldEvolutionValidationError, match="version_shape.mismatch"):
        plan.apply_to(graph, {"e:evaluation", "e:opposes"})


def test_perspective_disagreement_keeps_both_cognitions_current() -> None:
    graph = _relationship_graph()
    owner_view = _cognition(
        "cog:owner-view",
        MemoryTarget("relationship", RELATIONSHIP_ID),
        "The argument was about planning clarity.",
        "e:owner-view",
    )
    graph.add_cognition(owner_view)
    friend_view = _cognition(
        "cog:friend-view",
        owner_view.target,
        "The argument was about leaving room to improvise.",
        "e:friend-view",
        perspective=Perspective("entity", (FRIEND_ID,)),
    )
    plan = WorldEvolutionPlan(
        WorldDelta(
            WORLD_ID,
            ("e:friend-view",),
            new_cognitions=(friend_view,),
            formation_traces=(_trace(friend_view, "e:friend-view"),),
        ),
        (
            EvolutionStep(
                "evolution:planning:perspective-disagreement",
                "cognition_change",
                "disagrees_with",
                owner_view.target,
                (owner_view.id,),
                (friend_view.id,),
                "2026-06-05T10:00:00+08:00",
                ("e:friend-view",),
            ),
        ),
    )

    preview = plan.apply_to(graph, {"e:friend-view"})

    assert {owner_view.id, friend_view.id} <= set(preview.cognitions)
    assert superseding_cognition_pairs(plan) == ()


def test_lifecycle_projection_uses_explicit_corroboration_time_without_mutation() -> None:
    cognition = _cognition(
        "cog:temporary-state",
        MemoryTarget("entity", OWNER_ID),
        "Yun is temporarily exhausted.",
        "e:tired",
        content_type="state",
    )
    before = cognition

    projected = project_cognition_lifecycle(
        cognition,
        is_superseded=False,
        last_corroborated_at="2026-06-01T00:00:00+08:00",
        now="2026-06-09T00:00:00+08:00",
    )

    assert projected.is_expired is True
    assert projected.is_current is False
    assert projected.active_salience == 0
    assert cognition == before


def test_plan_codec_is_closed_and_event_cycle_fails() -> None:
    plan = _event_state_plan(
        predecessor_event_id="event:argument",
        predecessor_state_id="cog:relationship-strained",
        event_id="event:apology",
        event_type="apology",
        summary="Friend_X apologised to Yun.",
        occurred_at="2026-06-02T09:00:00+08:00",
        evidence_id="e:apology",
        state_id="cog:relationship-repairing",
        state="repairing",
        event_relation="responds_to",
    )
    encoded = evolution_plan_to_data(plan)
    assert evolution_plan_from_data(encoded) == plan
    encoded["unexpected"] = True
    with pytest.raises(WorldEvolutionValidationError, match="plan.fields.invalid"):
        evolution_plan_from_data(encoded)

    graph = _relationship_graph()
    graph.add_event(
        _event(
            "event:apology",
            "apology",
            "Friend_X apologised to Yun.",
            "2026-06-02T09:00:00+08:00",
            "e:apology",
        )
    )
    cyclic = (
        AcceptedEvolutionStep(
            "review:one",
            1,
            EvolutionStep(
                "evolution:one",
                "event_link",
                "responds_to",
                MemoryTarget("relationship", RELATIONSHIP_ID),
                ("event:argument",),
                ("event:apology",),
                "2026-06-02T09:00:00+08:00",
                ("e:apology",),
            ),
        ),
        AcceptedEvolutionStep(
            "review:two",
            2,
            EvolutionStep(
                "evolution:two",
                "event_link",
                "continues",
                MemoryTarget("relationship", RELATIONSHIP_ID),
                ("event:apology",),
                ("event:argument",),
                "2026-06-03T09:00:00+08:00",
                ("e:argument",),
            ),
        ),
    )
    with pytest.raises(WorldEvolutionValidationError):
        relationship_event_chain(graph, cyclic, RELATIONSHIP_ID)
