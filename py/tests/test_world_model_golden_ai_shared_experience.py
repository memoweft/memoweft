"""Golden Case #3 manual expected-world oracle.

The fixture demonstrates a valid Stage 0 representation.  It does not claim
that the world graph enforces Stage 1 extraction or provenance-ledger writes.
"""

from memoweft.types import (
    Evidence,
    EvidenceLink,
    InteractionContext,
    SemanticResolution,
    VisibleTurn,
)
from memoweft.world import (
    Entity,
    EventFacet,
    EventParticipant,
    MemoryTarget,
    MemoryWorldGraph,
    PersonalWorld,
    Perspective,
    Relationship,
    WorldCognition,
    WorldEvent,
)


def build_confirmed_ai_interpretation_world() -> tuple[
    MemoryWorldGraph, dict[str, Evidence], InteractionContext, SemanticResolution
]:
    user_confirmation = Evidence(
        id="e:user-confirmation",
        subject_id="world:yun",
        source_kind="spoken",
        host_id="local",
        origin_id="turn:user-2",
        occurred_at="2026-08-07T10:01:00+08:00",
        recorded_at="2026-08-07T10:01:01+08:00",
        raw_content="Yes, that planning difference was part of the friction.",
        summary="User confirmed the proposed interpretation.",
        allow_local_read=True,
        allow_cloud_read=False,
        allow_inference=True,
        corrects_evidence_id=None,
    )
    assistant_proposal = "Planning differences may explain the friction."
    interaction_context = InteractionContext(
        id="interaction:planning-interpretation",
        subject_id="world:yun",
        conversation_id="conversation:planning",
        episode_id="episode:planning-1",
        context=[
            VisibleTurn(role="assistant", content=assistant_proposal),
            VisibleTurn(role="user", content=user_confirmation.raw_content),
        ],
        context_hash="manual-oracle-context-hash",
        created_at="2026-08-07T10:01:01+08:00",
    )
    resolution = SemanticResolution(
        id="resolution:user-confirmation",
        evidence_id=user_confirmation.id,
        resolved_content="Planning differences were part of the friction.",
        response_act="affirm",
        prompt_act="propose",
        proposition_origin="assistant_proposed",
        assertion_strength="explicit",
        required_context=assistant_proposal,
        resolver_version="manual-stage-0-oracle",
        created_at="2026-08-07T10:01:01+08:00",
    )

    graph = MemoryWorldGraph(
        PersonalWorld(world_id="world:yun", owner_entity_id="person:user")
    )
    graph.add_entity(
        Entity(
            id="person:user",
            world_id="world:yun",
            kind="person",
            canonical_name="User",
        )
    )
    graph.add_entity(
        Entity(
            id="agent:memoweft",
            world_id="world:yun",
            kind="agent",
            canonical_name="MemoWeft Agent",
        )
    )
    graph.validate_owner()
    graph.add_relationship(
        Relationship(
            id="relationship:user-agent",
            world_id="world:yun",
            source_entity_id="person:user",
            target_entity_id="agent:memoweft",
            relation_type="conversation_partner",
            status="active",
        )
    )
    graph.add_event(
        WorldEvent(
            id="event:planning-interpretation",
            world_id="world:yun",
            event_type="shared_conversation",
            summary="User and MemoWeft Agent discussed an interpretation of travel-planning friction.",
            occurred_at="2026-08-07T10:01:00+08:00",
            participants=(
                EventParticipant(entity_id="person:user", role="participant"),
                EventParticipant(entity_id="agent:memoweft", role="participant"),
            ),
            relationship_ids=("relationship:user-agent",),
            # This is a non-evidence utterance/context, mirrored from
            # InteractionContext only so this manual event is intelligible.
            facets=(
                EventFacet(key="assistant_context", value=assistant_proposal),
                EventFacet(key="user_confirmation", value=user_confirmation.raw_content),
            ),
            evidence_ids=(user_confirmation.id,),
        )
    )
    graph.add_cognition(
        WorldCognition(
            id="cog:confirmed-planning-interpretation",
            world_id="world:yun",
            target=MemoryTarget("event", "event:planning-interpretation"),
            content="Planning differences were confirmed as relevant to the friction.",
            content_type="hypothesis",
            formed_by="confirmed",
            confidence=520,
            cred_status="limited",
            # Gate 0 default: confirmation is held from the user's
            # perspective.  The agent remains proposition origin and event
            # participant, not a second epistemic holder by default.
            perspective=Perspective("entity", ("person:user",)),
            sources=(EvidenceLink(user_confirmation.id, "support"),),
        )
    )
    return graph, {user_confirmation.id: user_confirmation}, interaction_context, resolution


def test_manual_oracle_uses_real_user_evidence_for_a_confirmed_ai_interpretation() -> None:
    graph, evidence_by_id, interaction_context, resolution = build_confirmed_ai_interpretation_world()

    event = graph.events["event:planning-interpretation"]
    cognition = graph.cognitions["cog:confirmed-planning-interpretation"]
    support_ids = {link.evidence_id for link in cognition.sources if link.relation == "support"}
    assert {participant.entity_id for participant in event.participants} == {
        "person:user",
        "agent:memoweft",
    }
    assert cognition.target == MemoryTarget("event", event.id)
    assert cognition.formed_by == "confirmed"
    assert support_ids == {"e:user-confirmation"}
    assert all(
        evidence_id in evidence_by_id
        and evidence_by_id[evidence_id].source_kind == "spoken"
        and evidence_by_id[evidence_id].allow_inference
        for evidence_id in support_ids
    )
    assert resolution.evidence_id in support_ids
    assert resolution.response_act == "affirm"
    assert resolution.proposition_origin == "assistant_proposed"
    assert interaction_context.context[0].role == "assistant"


def test_manual_oracle_keeps_assistant_turn_as_non_evidence_context() -> None:
    graph, evidence_by_id, interaction_context, _ = build_confirmed_ai_interpretation_world()

    event = graph.events["event:planning-interpretation"]
    assistant_context = next(facet for facet in event.facets if facet.key == "assistant_context")
    assert assistant_context.value == interaction_context.context[0].content
    assert event.evidence_ids == ("e:user-confirmation",)
    assert set(evidence_by_id) == {"e:user-confirmation"}
    assert all(turn.role != "assistant" or turn.content == assistant_context.value for turn in interaction_context.context)


def test_manual_oracle_uses_user_perspective_for_ai_confirmed_cognition() -> None:
    graph, _, _, _ = build_confirmed_ai_interpretation_world()

    cognition = graph.cognitions["cog:confirmed-planning-interpretation"]
    assert cognition.perspective == Perspective("entity", ("person:user",))
