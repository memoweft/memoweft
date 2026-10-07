"""Golden Case #2 manual expected-world oracle.

This hand-assembled fixture proves only that the Stage 0 domain model can
express the required semantics.  It is not an extraction or retrieval proof.
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


def build_mother_candy_world() -> MemoryWorldGraph:
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
            id="person:mother",
            world_id="world:example",
            kind="person",
            canonical_name="Mother",
        )
    )
    graph.validate_owner()
    graph.add_relationship(
        Relationship(
            id="relationship:user-mother",
            world_id="world:example",
            source_entity_id="person:user",
            target_entity_id="person:mother",
            relation_type="child_of",
            bidirectional=False,
            status="active",
        )
    )
    for event_id, occurred_at, evidence_id in (
        ("event:mother-gives-candy-1", "2026-07-01T08:00:00+08:00", "e:candy-1"),
        ("event:mother-gives-candy-2", "2026-08-01T08:00:00+08:00", "e:candy-2"),
    ):
        graph.add_event(
            WorldEvent(
                id=event_id,
                world_id="world:example",
                event_type="gift",
                summary="Mother gave User candy.",
                occurred_at=occurred_at,
                participants=(
                    EventParticipant(entity_id="person:mother", role="giver"),
                    EventParticipant(entity_id="person:user", role="recipient"),
                ),
                relationship_ids=("relationship:user-mother",),
                facets=(EventFacet(key="gift", value="candy"),),
                evidence_ids=(evidence_id,),
            )
        )
    graph.add_cognition(
        WorldCognition(
            id="cog:mother-user-candy-pattern",
            world_id="world:example",
            target=MemoryTarget("relationship", "relationship:user-mother"),
            content="Mother has repeatedly given User candy.",
            content_type="trend",
            formed_by="inferred",
            confidence=560,
            cred_status="limited",
            perspective=Perspective("entity", ("person:user",)),
            sources=(
                EvidenceLink("e:candy-1", "support"),
                EvidenceLink("e:candy-2", "support"),
            ),
        )
    )
    return graph


def test_manual_oracle_forms_a_third_party_relationship_pattern() -> None:
    graph = build_mother_candy_world()

    relationship = graph.relationships["relationship:user-mother"]
    pattern = graph.cognitions["cog:mother-user-candy-pattern"]
    assert relationship.source_entity_id == "person:user"
    assert relationship.target_entity_id == "person:mother"
    assert relationship.relation_type == "child_of"
    assert relationship.bidirectional is False
    assert pattern.target == MemoryTarget("relationship", relationship.id)
    assert pattern.perspective == Perspective("entity", ("person:user",))
    assert pattern.formed_by == "inferred"
    assert {link.evidence_id for link in pattern.sources} == {"e:candy-1", "e:candy-2"}
    assert {"event:mother-gives-candy-1", "event:mother-gives-candy-2"} == set(graph.events)


def test_manual_oracle_does_not_infer_that_mother_likes_candy() -> None:
    graph = build_mother_candy_world()

    mother_cognitions = [
        cognition
        for cognition in graph.cognitions.values()
        if cognition.target == MemoryTarget("entity", "person:mother")
    ]
    assert mother_cognitions == []
    assert all("likes candy" not in cognition.content.lower() for cognition in graph.cognitions.values())
