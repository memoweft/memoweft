"""Golden Case #1: the Nanjing-trip conflict.

The regression target is architectural, not wording-specific.  The same user
conversation must be able to produce independent memory around the friend, the
shared relationship, the trip event and the user.  It must not collapse all
meaning into a list of sentences whose grammatical subject is "the user".
"""

from memoweft.types import EvidenceLink
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


def build_nanjing_world() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld(world_id="world:yun", owner_entity_id="person:user"))

    graph.add_entity(Entity("person:user", "world:yun", "person", "User"))
    graph.add_entity(Entity("person:friend-x", "world:yun", "person", "Friend_X"))
    graph.add_entity(Entity("activity:nanjing-trip", "world:yun", "activity", "Nanjing trip"))
    graph.add_entity(Entity("place:nanjing", "world:yun", "place", "Nanjing"))
    graph.validate_owner()

    graph.add_relationship(
        Relationship(
            id="relationship:user-friend-x",
            world_id="world:yun",
            source_entity_id="person:user",
            target_entity_id="person:friend-x",
            relation_type="friend",
            bidirectional=True,
            status="active",
        )
    )

    graph.add_event(
        WorldEvent(
            id="event:nanjing-conflict",
            world_id="world:yun",
            event_type="interpersonal_conflict",
            summary="User and Friend_X argued while planning a trip to Nanjing because they preferred different travel styles.",
            occurred_at="2026-08-06T12:00:00+08:00",
            participants=(
                EventParticipant("person:user", "participant"),
                EventParticipant("person:friend-x", "participant"),
            ),
            related_entity_ids=("activity:nanjing-trip", "place:nanjing"),
            relationship_ids=("relationship:user-friend-x",),
            facets=(
                EventFacet(
                    "cause",
                    "They disagreed about how much a trip should be planned in advance.",
                ),
                EventFacet(
                    "position",
                    "Prefers driving without a fixed itinerary and values uncertainty in travel.",
                    about_entity_id="person:user",
                ),
                EventFacet(
                    "position",
                    "Prefers making an itinerary and following a travel guide.",
                    about_entity_id="person:friend-x",
                ),
            ),
            evidence_ids=("e1", "e2", "e3"),
        )
    )

    # User memory is still valid, but it is only one region of the world.
    graph.add_cognition(
        WorldCognition(
            id="cog:user-travel-style",
            world_id="world:yun",
            target=MemoryTarget("entity", "person:user"),
            content="旅行时偏好随性、低计划性，并重视未知和临场探索",
            content_type="preference",
            formed_by="stated",
            confidence=640,
            cred_status="limited",
            perspective=Perspective("entity", ("person:user",)),
            sources=(EvidenceLink("e2", "support"), EvidenceLink("e3", "support")),
            scope="travel",
        )
    )

    # This cognition semantically belongs to Friend_X.  "User said" is provenance/
    # perspective metadata, not part of the cognition content itself.
    graph.add_cognition(
        WorldCognition(
            id="cog:friend-planning-style",
            world_id="world:yun",
            target=MemoryTarget("entity", "person:friend-x"),
            content="做事倾向提前规划；旅行时偏好按攻略和行程安排游玩",
            content_type="trait",
            formed_by="stated",
            confidence=600,
            cred_status="limited",
            perspective=Perspective("entity", ("person:user",)),
            sources=(EvidenceLink("e2", "support"), EvidenceLink("e3", "support")),
        )
    )

    # The relationship itself can accumulate memory and patterns.
    graph.add_cognition(
        WorldCognition(
            id="cog:relationship-planning-friction",
            world_id="world:yun",
            target=MemoryTarget("relationship", "relationship:user-friend-x"),
            content="计划性与自由度的差异可能是双方在共同出行中的潜在摩擦点",
            content_type="hypothesis",
            formed_by="inferred",
            confidence=200,
            cred_status="candidate",
            perspective=Perspective("system"),
            sources=(EvidenceLink("e2", "support"), EvidenceLink("e3", "support")),
            scope="travel",
        )
    )

    return graph


def test_nanjing_case_forms_a_personal_world_not_only_a_user_profile() -> None:
    graph = build_nanjing_world()

    assert graph.world.owner_entity_id == "person:user"
    assert "person:friend-x" in graph.entities
    assert "relationship:user-friend-x" in graph.relationships
    assert "event:nanjing-conflict" in graph.events

    friend_memory = graph.cognitions["cog:friend-planning-style"]
    assert friend_memory.target == MemoryTarget("entity", "person:friend-x")
    assert friend_memory.perspective == Perspective("entity", ("person:user",))
    assert "用户认为" not in friend_memory.content

    relationship_memory = graph.cognitions["cog:relationship-planning-friction"]
    assert relationship_memory.target == MemoryTarget(
        "relationship", "relationship:user-friend-x"
    )


def test_event_anchor_expands_the_local_memory_needed_to_answer_why_they_argued() -> None:
    graph = build_nanjing_world()

    recalled = graph.expand(MemoryTarget("event", "event:nanjing-conflict"), depth=1)

    assert recalled.entity_ids == frozenset(
        {"person:user", "person:friend-x", "activity:nanjing-trip", "place:nanjing"}
    )
    assert recalled.relationship_ids == frozenset({"relationship:user-friend-x"})
    assert "event:nanjing-conflict" in recalled.event_ids
    assert {
        "cog:user-travel-style",
        "cog:friend-planning-style",
        "cog:relationship-planning-friction",
    }.issubset(recalled.cognition_ids)

    event = graph.events["event:nanjing-conflict"]
    cause = [facet.value for facet in event.facets if facet.key == "cause"]
    positions = {
        facet.about_entity_id: facet.value
        for facet in event.facets
        if facet.key == "position"
    }
    assert cause == ["They disagreed about how much a trip should be planned in advance."]
    assert "without a fixed itinerary" in positions["person:user"]
    assert "travel guide" in positions["person:friend-x"]


def test_perspective_and_provenance_are_separate_dimensions() -> None:
    graph = build_nanjing_world()
    cognition = graph.cognitions["cog:friend-planning-style"]

    assert cognition.perspective.holder_entity_ids == ("person:user",)
    assert {link.evidence_id for link in cognition.sources} == {"e2", "e3"}
