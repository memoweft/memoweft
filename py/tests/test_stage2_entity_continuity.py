"""Deterministic multi-session contract for the first Stage 2 work package.

The assertions derive semantic roles from graph structure, so swapping every
opaque Entity ID does not change the contract.
"""
from __future__ import annotations

import pytest

from memoweft.world import (
    AcceptedEntityReference,
    Entity,
    EntityReferenceResolver,
    EventParticipant,
    MemoryWorldGraph,
    PersonalWorld,
    ReferenceMention,
    Relationship,
    WorldEvent,
)


def _gate_world(suffix: str) -> MemoryWorldGraph:
    world_id = f"world:{suffix}"
    owner_id = f"person:{suffix}:owner"
    mother_id = f"person:{suffix}:parent-a"
    friend_id = f"person:{suffix}:known-a"
    other_id = f"person:{suffix}:known-b"
    trip_id = f"activity:{suffix}:trip"
    place_id = f"place:{suffix}:destination"
    graph = MemoryWorldGraph(PersonalWorld(world_id, owner_id))
    for entity in (
        Entity(owner_id, world_id, "person", "Owner"),
        Entity(mother_id, world_id, "person", "Mother", ("妈妈",)),
        Entity(friend_id, world_id, "person", "Lin", ("小林",)),
        Entity(other_id, world_id, "person", "Zhou", ("小周",)),
        Entity(trip_id, world_id, "activity", "Nanjing trip", ("南京旅游",)),
        Entity(place_id, world_id, "place", "Nanjing", ("南京",)),
    ):
        graph.add_entity(entity)
    graph.validate_owner()
    graph.add_relationship(Relationship(f"rel:{suffix}:mother", world_id, owner_id, mother_id, "child_of"))
    graph.add_relationship(Relationship(f"rel:{suffix}:friend-a", world_id, owner_id, friend_id, "friend", True))
    graph.add_relationship(Relationship(f"rel:{suffix}:friend-b", world_id, owner_id, other_id, "friend", True))
    graph.add_event(
        WorldEvent(
            f"event:{suffix}:trip",
            world_id,
            "trip",
            "Nanjing trip",
            "2026-07-01T08:00:00+08:00",
            participants=(EventParticipant(owner_id), EventParticipant(friend_id)),
            related_entity_ids=(trip_id, place_id),
            relationship_ids=(f"rel:{suffix}:friend-a",),
            evidence_ids=(f"e:{suffix}:trip",),
        )
    )
    return graph


def _role_id(graph: MemoryWorldGraph, relation_type: str) -> str:
    relationship = next(
        relationship
        for relationship in graph.relationships.values()
        if relationship.source_entity_id == graph.world.owner_entity_id
        and relationship.relation_type == relation_type
    )
    return relationship.target_entity_id


def _mention(
    text: str,
    evidence_id: str,
    session: str,
    *,
    occurred_at: str = "2026-08-10T10:00:00+08:00",
    continuity_id: str = "continuity:stage2",
) -> ReferenceMention:
    return ReferenceMention(
        text,
        evidence_id,
        session,
        occurred_at,
        "user",
        continuity_id=continuity_id,
    )


@pytest.mark.parametrize("opaque_suffix", ["alpha", "completely-different-ids"])
def test_alias_then_pronoun_keeps_one_mother_identity_across_three_sessions(
    opaque_suffix: str,
) -> None:
    base = _gate_world(opaque_suffix)
    resolver = EntityReferenceResolver()
    mother_id = _role_id(base, "child_of")
    original_entity_count = len(base.entities)

    session_a = resolver.resolve(
        base,
        _mention(
            "妈妈",
            "e:session-a",
            "session:a",
            occurred_at="2026-08-10T08:00:00+08:00",
        ),
    )
    assert session_a.entity_id == mother_id

    session_b = resolver.resolve(
        base,
        _mention(
            "我妈",
            "e:session-b",
            "session:b",
            occurred_at="2026-08-10T09:00:00+08:00",
        ),
    )
    assert session_b.entity_id == mother_id
    assert session_b.alias_proposal is not None
    preview = session_b.alias_proposal.preview(base, (session_b.mention,))

    history = (
        AcceptedEntityReference(
            mother_id,
            "我妈",
            "e:session-b",
            "session:b",
            "2026-08-10T09:00:00+08:00",
            "user",
            continuity_id="continuity:stage2",
        ),
    )
    session_c = resolver.resolve(preview, _mention("她", "e:session-c", "session:c"), history)

    assert session_c.entity_id == mother_id
    assert len(base.entities) == len(preview.entities) == original_entity_count
    assert "我妈" not in base.entities[mother_id].aliases
    assert "我妈" in preview.entities[mother_id].aliases


@pytest.mark.parametrize("opaque_suffix", ["alpha", "completely-different-ids"])
def test_descriptive_friend_reference_is_id_independent_and_does_not_duplicate(
    opaque_suffix: str,
) -> None:
    base = _gate_world(opaque_suffix)
    friend_id = next(
        relationship.target_entity_id
        for relationship in base.relationships.values()
        if relationship.relation_type == "friend"
        and any(
            relationship.id in event.relationship_ids
            for event in base.events.values()
        )
    )
    count_before = len(base.entities)

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个南京旅游的朋友", "e:later", "session:later"),
    )

    assert resolution.state == "resolved"
    assert resolution.entity_id == friend_id
    assert len(base.entities) == count_before


def test_multi_session_ambiguity_never_selects_or_creates_an_entity() -> None:
    base = _gate_world("ambiguous")
    friend_ids = tuple(
        sorted(
            relationship.target_entity_id
            for relationship in base.relationships.values()
            if relationship.relation_type == "friend"
        )
    )
    history = tuple(
        AcceptedEntityReference(
            entity_id,
            mention,
            "e:session-a",
            "session:a",
            "2026-08-10T09:00:00+08:00",
            "user",
            continuity_id="continuity:stage2",
        )
        for entity_id, mention in zip(friend_ids, ("小林", "小周"), strict=True)
    )
    count_before = len(base.entities)

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("她", "e:session-b", "session:b"),
        history,
    )

    assert resolution.state == "ambiguous"
    assert resolution.entity_id is None
    assert set(resolution.candidate_entity_ids) == set(friend_ids)
    assert resolution.as_unresolved_reference() is not None
    assert len(base.entities) == count_before
