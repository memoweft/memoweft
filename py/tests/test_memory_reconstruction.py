"""Stage 5 graph-first memory reconstruction contracts."""
from __future__ import annotations

from dataclasses import replace

from memoweft.types import EvidenceLink
from memoweft.world import (
    AcceptedEvolutionStep,
    CognitionLineage,
    Entity,
    EventFacet,
    EventParticipant,
    EvolutionStep,
    MemoryReconstruction,
    MemoryTarget,
    MemoryWorldGraph,
    PersonalWorld,
    Perspective,
    Relationship,
    WorldCognition,
    WorldEvent,
    parse_memory_query,
    reconstruct_memory,
    render_answer_context,
)
from test_world_model_golden_nanjing import build_nanjing_world


def _specific_experience_world(
    *,
    specific_name: str,
    category_name: str,
    summary: str,
    facet_key: str,
) -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld("world:detail", "person:owner"))
    for entity in (
        Entity("person:owner", "world:detail", "person", "Owner"),
        Entity("entity:specific", "world:detail", "activity", specific_name),
        Entity("entity:category", "world:detail", "activity", category_name),
    ):
        graph.add_entity(entity)
    graph.add_event(
        WorldEvent(
            "event:specific-experience",
            "world:detail",
            "lived_occurrence",
            summary,
            "2026-08-11T08:00:00+00:00",
            (EventParticipant("person:owner", "participant"),),
            ("entity:specific",),
            facets=(EventFacet(facet_key, category_name),),
            evidence_ids=("e-detail",),
        )
    )
    return graph


def test_experience_detail_query_prefers_specific_event_over_generic_category_entity() -> None:
    cases = (
        (
            _specific_experience_world(
                specific_name="幻塔纪元",
                category_name="游戏",
                summary="Owner刚才在玩幻塔纪元",
                facet_key="activity_type",
            ),
            "我刚刚在玩的是什么游戏？",
        ),
        (
            _specific_experience_world(
                specific_name="云笺",
                category_name="软件",
                summary="Owner刚才在使用云笺",
                facet_key="tool_type",
            ),
            "刚才我用的是什么软件？",
        ),
    )

    for graph, text in cases:
        result = reconstruct_memory(text, graph)

        assert result.status == "resolved"
        assert result.query.intents == ("experience",)
        assert result.primary_anchor is not None
        assert result.primary_anchor.target == MemoryTarget(
            "event", "event:specific-experience"
        )
        assert result.event_ids == ("event:specific-experience",)
        assert "entity:specific" in result.entity_ids


def test_plain_category_lookup_remains_an_entity_query() -> None:
    graph = _specific_experience_world(
        specific_name="幻塔纪元",
        category_name="游戏",
        summary="Owner刚才在玩幻塔纪元",
        facet_key="activity_type",
    )

    result = reconstruct_memory("游戏", graph)

    assert result.status == "resolved"
    assert result.query.intents == ("fact",)
    assert result.primary_anchor is not None
    assert result.primary_anchor.target == MemoryTarget("entity", "entity:category")


def test_nanjing_query_reconstructs_one_experience_from_graph_and_provenance() -> None:
    graph = build_nanjing_world()
    graph.entities["person:friend-x"] = replace(
        graph.entities["person:friend-x"], aliases=("小林",)
    )
    graph.add_cognition(
        WorldCognition(
            "cog:owner-swimming",
            "world:yun",
            MemoryTarget("entity", "person:user"),
            "每周六去游泳",
            "fact",
            "stated",
            600,
            "limited",
            Perspective("entity", ("person:user",)),
            (EvidenceLink("turn-unrelated", "support"),),
            "exercise",
        )
    )

    query = parse_memory_query(
        "Do you remember why she and I argued about the Nanjing trip?"
    )
    result = reconstruct_memory(query, graph)

    assert result.status == "resolved"
    assert result.primary_anchor is not None
    assert result.primary_anchor.target == MemoryTarget(
        "event", "event:nanjing-conflict"
    )
    assert query.intents == ("cause",)
    assert set(result.entity_ids) == {
        "person:user",
        "person:friend-x",
        "activity:nanjing-trip",
        "place:nanjing",
    }
    assert result.relationship_ids == ("relationship:user-friend-x",)
    assert result.event_ids == ("event:nanjing-conflict",)
    assert set(result.current_cognition_ids) == {
        "cog:user-travel-style",
        "cog:friend-planning-style",
        "cog:relationship-planning-friction",
    }
    assert "cog:owner-swimming" not in result.current_cognition_ids
    event = graph.events[result.event_ids[0]]
    assert [item.value for item in event.facets if item.key == "cause"] == [
        "They disagreed about how much a trip should be planned in advance."
    ]
    assert {
        item.about_entity_id for item in event.facets if item.key == "position"
    } == {"person:user", "person:friend-x"}
    assert set(result.evidence_ids) == {"e1", "e2", "e3"}

    context = render_answer_context(result, graph)
    assert "event_cause_facet" in context
    assert "position[about=User]" in context
    assert "position[about=Friend_X]" in context
    assert "cognition:cog:friend-planning-style --support--> e3" in context
    assert "raw Evidence text intentionally omitted" in context
    assert "我和 Friend_X 是朋友，最近一起计划去南京旅行。" not in context


def test_accepted_entity_alias_contributes_to_the_same_event_anchor() -> None:
    graph = build_nanjing_world()
    graph.entities["person:friend-x"] = replace(
        graph.entities["person:friend-x"], aliases=("小林",)
    )

    result = reconstruct_memory("为什么我和小林在南京旅行吵架？", graph)

    assert result.status == "resolved"
    assert result.primary_anchor is not None
    assert result.primary_anchor.target.id == "event:nanjing-conflict"
    assert "person:friend-x" in result.entity_ids


def test_equal_event_anchors_are_ambiguous_and_never_union_worlds() -> None:
    graph = MemoryWorldGraph(PersonalWorld("world:test", "person:owner"))
    for item in (
        Entity("person:owner", "world:test", "person", "Owner"),
        Entity("person:a", "world:test", "person", "Friend A"),
        Entity("person:b", "world:test", "person", "Friend B"),
        Entity("place:nanjing", "world:test", "place", "Nanjing"),
        Entity("activity:trip", "world:test", "activity", "Nanjing trip"),
    ):
        graph.add_entity(item)
    for suffix, friend_id in (("a", "person:a"), ("b", "person:b")):
        graph.add_event(
            WorldEvent(
                f"event:{suffix}",
                "world:test",
                "interpersonal_conflict",
                "Owner argued with a friend about the Nanjing trip.",
                f"2026-01-0{1 if suffix == 'a' else 2}T00:00:00+00:00",
                (
                    EventParticipant("person:owner", "participant"),
                    EventParticipant(friend_id, "participant"),
                ),
                ("activity:trip", "place:nanjing"),
                facets=(EventFacet("cause", "Different travel styles"),),
            )
        )

    result = reconstruct_memory(
        "Why did I argue with a friend about the Nanjing trip?", graph
    )

    assert result.status == "ambiguous"
    assert result.reason_code == "ANCHOR_SCORE_TIE"
    assert result.primary_anchor is None
    assert result.entity_ids == () and result.event_ids == ()
    assert {
        item.target.id
        for item in result.candidates
        if item.target.kind == "event" and item.score == result.candidates[0].score
    } == {"event:a", "event:b"}

    resolved_query = parse_memory_query(
        "Why did I argue with a friend about the Nanjing trip?",
        resolved_entity_ids=("person:a",),
    )
    resolved = reconstruct_memory(resolved_query, graph)
    assert resolved.status == "resolved"
    assert resolved.primary_anchor is not None
    assert resolved.primary_anchor.target.id == "event:a"


def test_narrowing_lineage_separates_current_and_relevant_history() -> None:
    graph = build_nanjing_world()
    prior_id = "cog:friend-planning-style"
    successor = WorldCognition(
        "cog:friend-first-trip-nervous",
        "world:yun",
        MemoryTarget("entity", "person:friend-x"),
        "Friend_X wanted a plan because this was the first Nanjing trip and she was nervous.",
        "state",
        "stated",
        600,
        "limited",
        Perspective("entity", ("person:user",)),
        (EvidenceLink("e4", "support"),),
        "event:nanjing-conflict",
    )
    graph.add_cognition(successor)
    result = reconstruct_memory(
        "Why did we argue about the Nanjing trip?",
        graph,
        superseded_cognition_ids=frozenset({prior_id}),
        cognition_lineage=(CognitionLineage(prior_id, successor.id, "narrows"),),
    )

    assert result.status == "resolved"
    assert prior_id in result.historical_cognition_ids
    assert successor.id in result.current_cognition_ids
    assert result.cognition_lineage == (
        CognitionLineage(prior_id, successor.id, "narrows"),
    )
    assert {"e2", "e3", "e4"}.issubset(result.evidence_ids)
    context = render_answer_context(result, graph)
    assert "Relevant historical cognitions" in context
    assert f"{prior_id} --narrows--> {successor.id}" in context


def test_timeline_after_follows_only_accepted_forward_event_links() -> None:
    graph = MemoryWorldGraph(PersonalWorld("world:timeline", "person:owner"))
    graph.add_entity(Entity("person:owner", "world:timeline", "person", "Owner"))
    graph.add_entity(Entity("person:friend", "world:timeline", "person", "Friend"))
    relationship = Relationship(
        "relationship:friendship",
        "world:timeline",
        "person:owner",
        "person:friend",
        "friend",
        True,
    )
    graph.add_relationship(relationship)
    events = (
        WorldEvent(
            "event:argument",
            "world:timeline",
            "interpersonal_conflict",
            "Owner and Friend had an argument.",
            "2026-01-01T10:00:00+00:00",
            (EventParticipant("person:owner"), EventParticipant("person:friend")),
            relationship_ids=(relationship.id,),
            facets=(EventFacet("cause", "A misunderstanding"),),
            evidence_ids=("e:argument",),
        ),
        WorldEvent(
            "event:apology",
            "world:timeline",
            "apology",
            "Friend apologized.",
            "2026-01-01T11:00:00+00:00",
            (EventParticipant("person:friend"),),
            relationship_ids=(relationship.id,),
            evidence_ids=("e:apology",),
        ),
        WorldEvent(
            "event:repair",
            "world:timeline",
            "relationship_repair",
            "They repaired the friendship.",
            "2026-01-01T12:00:00+00:00",
            (EventParticipant("person:owner"), EventParticipant("person:friend")),
            relationship_ids=(relationship.id,),
            evidence_ids=("e:repair",),
        ),
        WorldEvent(
            "event:unrelated",
            "world:timeline",
            "exercise",
            "Owner went swimming.",
            "2026-01-01T13:00:00+00:00",
            (EventParticipant("person:owner"),),
            evidence_ids=("e:swim",),
        ),
    )
    for event in events:
        graph.add_event(event)
    state_cognition = WorldCognition(
        "cog:repaired",
        "world:timeline",
        MemoryTarget("relationship", relationship.id),
        "The friendship is repaired.",
        "state",
        "stated",
        600,
        "limited",
        Perspective("entity", ("person:owner",)),
        (EvidenceLink("e:repair", "support"),),
        "relationship_state",
    )
    graph.add_cognition(state_cognition)
    accepted = (
        AcceptedEvolutionStep(
            "review:1",
            1,
            EvolutionStep(
                "step:apology",
                "event_link",
                "responds_to",
                MemoryTarget("relationship", relationship.id),
                ("event:argument",),
                ("event:apology",),
                "2026-01-01T11:00:00+00:00",
                ("e:apology",),
            ),
        ),
        AcceptedEvolutionStep(
            "review:2",
            2,
            EvolutionStep(
                "step:repair",
                "event_link",
                "repairs",
                MemoryTarget("relationship", relationship.id),
                ("event:apology",),
                ("event:repair",),
                "2026-01-01T12:00:00+00:00",
                ("e:repair",),
            ),
        ),
        AcceptedEvolutionStep(
            "review:3",
            3,
            EvolutionStep(
                "step:state",
                "relationship_state",
                "repaired",
                MemoryTarget("relationship", relationship.id),
                ("event:repair",),
                (state_cognition.id,),
                "2026-01-01T12:00:00+00:00",
                ("e:repair",),
            ),
        ),
    )

    result = reconstruct_memory(
        "What happened after the argument?",
        graph,
        accepted_evolution_steps=accepted,
    )

    assert result.status == "resolved"
    assert result.primary_anchor is not None
    assert result.primary_anchor.target.id == "event:argument"
    assert result.event_ids == ("event:argument", "event:apology", "event:repair")
    assert "event:unrelated" not in result.event_ids
    assert result.relationship_states[0].state == "repaired"


def test_unsupported_query_has_no_semantic_bundle() -> None:
    result = reconstruct_memory("今天天气如何？", build_nanjing_world())

    assert result.status == "unsupported"
    assert result.reason_code == "NO_SUPPORTED_ANCHOR"
    assert result.primary_anchor is None
    assert result.entity_ids == ()
    assert result.provenance == ()


def test_reconstruction_is_semantically_stable_across_opaque_ids_and_insertion_order() -> None:
    first, first_evidence = _opaque_nanjing_variant(
        {
            "owner": "z9",
            "friend": "y8",
            "trip": "x7",
            "place": "w6",
            "relationship": "r5",
            "event": "v4",
            "cognition": "c3",
        },
        ("q2", "q1"),
        reverse=False,
    )
    second, second_evidence = _opaque_nanjing_variant(
        {
            "owner": "00",
            "friend": "ff",
            "trip": "01",
            "place": "fe",
            "relationship": "02",
            "event": "fd",
            "cognition": "03",
        },
        ("aa", "zz"),
        reverse=True,
    )

    first_result = reconstruct_memory(
        "Do you remember why she and I argued about the Nanjing trip?", first
    )
    second_result = reconstruct_memory(
        "Do you remember why she and I argued about the Nanjing trip?", second
    )

    assert _semantic_signature(first_result, first, first_evidence) == _semantic_signature(
        second_result, second, second_evidence
    )


def _opaque_nanjing_variant(
    ids: dict[str, str],
    evidence_ids: tuple[str, str],
    *,
    reverse: bool,
) -> tuple[MemoryWorldGraph, dict[str, str]]:
    graph = MemoryWorldGraph(PersonalWorld("opaque-world", ids["owner"]))
    entities = [
        Entity(ids["owner"], "opaque-world", "person", "Owner"),
        Entity(ids["friend"], "opaque-world", "person", "Friend_X"),
        Entity(ids["trip"], "opaque-world", "activity", "Nanjing trip"),
        Entity(ids["place"], "opaque-world", "place", "Nanjing"),
    ]
    for entity in reversed(entities) if reverse else entities:
        graph.add_entity(entity)
    relationship = Relationship(
        ids["relationship"],
        "opaque-world",
        ids["owner"],
        ids["friend"],
        "friend",
        True,
    )
    graph.add_relationship(relationship)
    event = WorldEvent(
        ids["event"],
        "opaque-world",
        "interpersonal_conflict",
        "Owner and Friend_X argued about a Nanjing trip.",
        "2026-01-01T00:00:00+00:00",
        (EventParticipant(ids["owner"]), EventParticipant(ids["friend"])),
        (ids["trip"], ids["place"]),
        (relationship.id,),
        (
            EventFacet("cause", "Different planning preferences"),
            EventFacet("position", "Flexible travel", ids["owner"]),
            EventFacet("position", "Planned itinerary", ids["friend"]),
        ),
        evidence_ids,
    )
    graph.add_event(event)
    graph.add_cognition(
        WorldCognition(
            ids["cognition"],
            "opaque-world",
            MemoryTarget("event", event.id),
            "The conflict came from different planning preferences.",
            "fact",
            "stated",
            600,
            "limited",
            Perspective("entity", (ids["owner"],)),
            (EvidenceLink(evidence_ids[1], "support"),),
        )
    )
    return graph, {evidence_ids[0]: "event-one", evidence_ids[1]: "event-two"}


def _semantic_signature(
    result: MemoryReconstruction,
    graph: MemoryWorldGraph,
    evidence_roles: dict[str, str],
) -> tuple[object, ...]:
    assert result.status == "resolved"
    primary = result.primary_anchor
    assert primary is not None
    return (
        result.status,
        primary.target.kind,
        graph.events[primary.target.id].summary,
        tuple(sorted(graph.entities[item].canonical_name for item in result.entity_ids)),
        tuple(
            sorted(
                (
                    graph.entities[graph.relationships[item].source_entity_id].canonical_name,
                    graph.entities[graph.relationships[item].target_entity_id].canonical_name,
                    graph.relationships[item].relation_type,
                )
                for item in result.relationship_ids
            )
        ),
        tuple(graph.events[item].summary for item in result.event_ids),
        tuple(sorted(graph.cognitions[item].content for item in result.current_cognition_ids)),
        tuple(sorted(evidence_roles[item] for item in result.evidence_ids)),
    )
