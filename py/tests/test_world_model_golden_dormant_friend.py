"""Golden Case #5 manual expected-world oracle.

This fixture proves that a low current snapshot does not delete identity or
history.  It does not prove a decay algorithm or a recall-weighting system.
"""

from memoweft.world import (
    Entity,
    EventParticipant,
    MemoryTarget,
    MemoryWorldGraph,
    PersonalWorld,
    Relationship,
    WorldEvent,
)


def build_dormant_friend_world() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(
        PersonalWorld(world_id="world:example", owner_entity_id="person:user")
    )
    graph.add_entity(
        Entity(
            id="person:user",
            world_id="world:example",
            kind="person",
            canonical_name="User",
        )
    )
    graph.add_entity(
        Entity(
            id="person:friend-x",
            world_id="world:example",
            kind="person",
            canonical_name="Friend_X",
        )
    )
    graph.validate_owner()
    graph.add_relationship(
        Relationship(
            id="relationship:user-friend-x",
            world_id="world:example",
            source_entity_id="person:user",
            target_entity_id="person:friend-x",
            relation_type="friend",
            bidirectional=True,
        )
    )
    graph.add_event(
        WorldEvent(
            id="event:graduation",
            world_id="world:example",
            event_type="milestone",
            summary="User and Friend_X celebrated graduation together.",
            occurred_at="2018-06-30T12:00:00+08:00",
            participants=(
                EventParticipant(entity_id="person:user", role="participant"),
                EventParticipant(entity_id="person:friend-x", role="participant"),
            ),
            relationship_ids=("relationship:user-friend-x",),
            evidence_ids=("e:graduation",),
        )
    )
    return graph


def test_manual_oracle_low_current_snapshot_keeps_identity_and_history() -> None:
    graph = build_dormant_friend_world()

    relationship = graph.relationships["relationship:user-friend-x"]
    assert relationship.status is None
    assert not hasattr(relationship, "active_salience")
    assert graph.entities["person:friend-x"].canonical_name == "Friend_X"
    assert "event:graduation" in graph.events

    recalled = graph.expand(MemoryTarget("relationship", relationship.id), depth=1)
    assert "person:friend-x" in recalled.entity_ids
    assert "event:graduation" in recalled.event_ids
