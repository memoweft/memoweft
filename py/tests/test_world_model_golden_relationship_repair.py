"""Golden Case #4 manual expected-world oracle.

This fixture proves a history-preserving representation, not a globally frozen
relationship-state model or an extraction/retrieval implementation.
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


def build_repaired_friendship_world() -> MemoryWorldGraph:
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
            id="person:friend-x",
            world_id="world:yun",
            kind="person",
            canonical_name="Friend_X",
        )
    )
    graph.validate_owner()
    graph.add_relationship(
        Relationship(
            id="relationship:user-friend-x",
            world_id="world:yun",
            source_entity_id="person:user",
            target_entity_id="person:friend-x",
            relation_type="friend",
            bidirectional=True,
        )
    )
    for event_id, event_type, summary, occurred_at, evidence_id in (
        (
            "event:argument",
            "interpersonal_conflict",
            "User and Friend_X argued.",
            "2026-06-01T09:00:00+08:00",
            "e:argument",
        ),
        (
            "event:apology",
            "apology",
            "Friend_X apologised to User.",
            "2026-06-02T09:00:00+08:00",
            "e:apology",
        ),
        (
            "event:repair",
            "relationship_repair",
            "User and Friend_X repaired the friendship.",
            "2026-06-03T09:00:00+08:00",
            "e:repair",
        ),
        ):
        graph.add_event(
            WorldEvent(
                id=event_id,
                world_id="world:yun",
                event_type=event_type,
                summary=summary,
                occurred_at=occurred_at,
                participants=(
                    EventParticipant(entity_id="person:user", role="participant"),
                    EventParticipant(entity_id="person:friend-x", role="participant"),
                ),
                relationship_ids=("relationship:user-friend-x",),
                facets=(EventFacet(key="relationship_phase", value=event_type),),
                evidence_ids=(evidence_id,),
            )
        )
    graph.add_cognition(
        WorldCognition(
            id="cog:relationship-repaired",
            world_id="world:yun",
            target=MemoryTarget("relationship", "relationship:user-friend-x"),
            content="The friendship was repaired after the conflict and apology.",
            content_type="hypothesis",
            formed_by="confirmed",
            confidence=700,
            cred_status="limited",
            perspective=Perspective("entity", ("person:user",)),
            sources=(EvidenceLink("e:repair", "support"),),
        )
    )
    return graph


def test_manual_oracle_preserves_history_and_targets_repair_cognition() -> None:
    graph = build_repaired_friendship_world()

    relationship = graph.relationships["relationship:user-friend-x"]
    ordered_events = [
        graph.events["event:argument"],
        graph.events["event:apology"],
        graph.events["event:repair"],
    ]
    assert relationship.status is None
    assert set(graph.events) == {"event:argument", "event:apology", "event:repair"}
    assert [event.event_type for event in ordered_events] == [
        "interpersonal_conflict",
        "apology",
        "relationship_repair",
    ]
    assert ordered_events[0].occurred_at < ordered_events[1].occurred_at < ordered_events[2].occurred_at
    assert [event.evidence_ids for event in ordered_events] == [
        ("e:argument",),
        ("e:apology",),
        ("e:repair",),
    ]
    repair_cognition = graph.cognitions["cog:relationship-repaired"]
    assert repair_cognition.target == MemoryTarget("relationship", relationship.id)
    assert repair_cognition.perspective == Perspective("entity", ("person:user",))
    assert repair_cognition.sources == (EvidenceLink("e:repair", "support"),)

    recalled = graph.expand(MemoryTarget("relationship", relationship.id), depth=1)
    assert {"event:argument", "event:apology", "event:repair"}.issubset(recalled.event_ids)
