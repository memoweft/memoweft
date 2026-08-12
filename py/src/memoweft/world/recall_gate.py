"""Deterministic engineering predicates for the Stage 5 recall gate.

This is deliberately a local, graph-only evaluator.  It does not inspect raw
Evidence content, call a model, or write to the supplied graph.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from memoweft.types import EvidenceLink

from .evolution import AcceptedEvolutionStep, EvolutionStep
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
from .recall import CognitionLineage, reconstruct_memory, render_answer_context


GATE5_PROTOCOL_ID = "gate5-memory-reconstruction-engineering@1"
RAW_TRANSCRIPT_SENTINEL = "RAW_TRANSCRIPT_SENTINEL_GATE5_NEVER_RENDER"


@dataclass(frozen=True, slots=True)
class RecallGateObservation:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RecallGateReport:
    observations: tuple[RecallGateObservation, ...]

    @property
    def violations(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.observations if not item.passed)

    @property
    def passed(self) -> bool:
        return not self.violations


def evaluate_gate5_memory_reconstruction() -> RecallGateReport:
    """Evaluate the fixed, deterministic Stage 5 semantic boundary."""

    observations = (
        _exact_nanjing_bundle(),
        _no_raw_transcript(),
        _ambiguous_events_do_not_bundle(),
        _opaque_id_semantics_are_stable(),
        _narrowing_keeps_current_and_history(),
        _event_only_provenance_is_retained(),
        _reconstruction_is_read_only(),
    )
    return RecallGateReport(observations)


def _exact_nanjing_bundle() -> RecallGateObservation:
    graph = _nanjing_graph()
    result = reconstruct_memory("Why did Owner and Friend_X argue about the Nanjing trip?", graph)
    event = graph.events["event"]
    positions = {facet.about_entity_id for facet in event.facets if facet.key == "position"}
    passed = (
        result.status == "resolved"
        and result.event_ids == (event.id,)
        and set(result.entity_ids) == {"owner", "friend", "trip", "nanjing"}
        and result.relationship_ids == ("friends",)
        and {"cog:owner-position", "cog:friend-position", "cog:shared-friction"} <= set(result.current_cognition_ids)
        and "cog:owner-unrelated" not in result.current_cognition_ids
        and len([facet for facet in event.facets if facet.key == "cause"]) == 1
        and positions == {"owner", "friend"}
        and {"e:event", "e:owner", "e:friend"} <= set(result.evidence_ids)
    )
    return RecallGateObservation("nanjing.exact_reconstruction", passed, "one event with local world, positions, cognitions, and provenance")


def _no_raw_transcript() -> RecallGateObservation:
    graph = _nanjing_graph()
    result = reconstruct_memory("Why did Owner and Friend_X argue about the Nanjing trip?", graph)
    context = render_answer_context(result, graph)
    return RecallGateObservation("rendering.raw_transcript_omitted", RAW_TRANSCRIPT_SENTINEL not in context, "rendered context contains graph claims and evidence ids only")


def _ambiguous_events_do_not_bundle() -> RecallGateObservation:
    graph = _nanjing_graph()
    event = graph.events["event"]
    graph.add_event(replace(event, id="event-2"))
    result = reconstruct_memory("Why did Owner and Friend_X argue about the Nanjing trip?", graph)
    passed = result.status == "ambiguous" and result.primary_anchor is None and not result.event_ids and not result.entity_ids
    return RecallGateObservation("anchors.equal_events_ambiguous", passed, "equal event anchors do not union local worlds")


def _opaque_id_semantics_are_stable() -> RecallGateObservation:
    first = _nanjing_graph(prefix="a", reverse=False)
    second = _nanjing_graph(prefix="z", reverse=True)
    query = "Why did Owner and Friend_X argue about the Nanjing trip?"
    passed = _semantic_digest(reconstruct_memory(query, first), first) == _semantic_digest(reconstruct_memory(query, second), second)
    return RecallGateObservation("semantics.opaque_id_and_order_invariant", passed, "inverse-mapped semantic digest is stable")


def _narrowing_keeps_current_and_history() -> RecallGateObservation:
    graph = _nanjing_graph()
    successor = replace(graph.cognitions["cog:friend-position"], id="cog:friend-narrowed", content="Friend_X wanted a plan for this first trip.", sources=(EvidenceLink("e:narrow", "support"),))
    graph.add_cognition(successor)
    result = reconstruct_memory(
        "Why did Owner and Friend_X argue about the Nanjing trip?", graph,
        superseded_cognition_ids=frozenset({"cog:friend-position"}),
        cognition_lineage=(CognitionLineage("cog:friend-position", successor.id, "narrows"),),
    )
    passed = successor.id in result.current_cognition_ids and "cog:friend-position" in result.historical_cognition_ids and result.cognition_lineage == (CognitionLineage("cog:friend-position", successor.id, "narrows"),)
    return RecallGateObservation("cognition.narrowing_preserves_lineage", passed, "current successor and historical prior remain distinguishable")


def _event_only_provenance_is_retained() -> RecallGateObservation:
    graph = _nanjing_graph()
    result = reconstruct_memory("Why did Owner and Friend_X argue about the Nanjing trip?", graph)
    passed = any(item.subject_kind == "event" and item.evidence_id == "e:event" for item in result.provenance)
    return RecallGateObservation("provenance.event_only_retained", passed, "event evidence remains visible even without cognition duplication")


def _reconstruction_is_read_only() -> RecallGateObservation:
    graph = _nanjing_graph()
    before = _graph_digest(graph)
    reconstruct_memory("Why did Owner and Friend_X argue about the Nanjing trip?", graph)
    return RecallGateObservation("query.graph_immutable", before == _graph_digest(graph), "query and reconstruction do not mutate accepted graph")


def _nanjing_graph(*, prefix: str = "", reverse: bool = False) -> MemoryWorldGraph:
    ident: Callable[[str], str] = lambda value: f"{prefix}:{value}" if prefix else value
    world = PersonalWorld(ident("world"), ident("owner"))
    graph = MemoryWorldGraph(world)
    entities = (
        Entity(ident("owner"), world.world_id, "person", "Owner"),
        Entity(ident("friend"), world.world_id, "person", "Friend_X"),
        Entity(ident("trip"), world.world_id, "activity", "Nanjing trip"),
        Entity(ident("nanjing"), world.world_id, "place", "Nanjing"),
    )
    for entity in reversed(entities) if reverse else entities:
        graph.add_entity(entity)
    relationship = Relationship(ident("friends"), world.world_id, ident("owner"), ident("friend"), "friend", True)
    graph.add_relationship(relationship)
    event = WorldEvent(ident("event"), world.world_id, "interpersonal_conflict", "Owner and Friend_X argued about the Nanjing trip.", "2026-01-01T00:00:00+00:00", (EventParticipant(ident("owner")), EventParticipant(ident("friend"))), (ident("trip"), ident("nanjing")), (relationship.id,), (EventFacet("cause", "Different travel-planning preferences"), EventFacet("position", "Flexible itinerary", ident("owner")), EventFacet("position", "Planned itinerary", ident("friend"))), (ident("e:event"),))
    graph.add_event(event)
    cognitions: tuple[WorldCognition, ...] = (
        WorldCognition(ident("cog:owner-position"), world.world_id, MemoryTarget("event", event.id), "Owner preferred flexibility.", "fact", "stated", 600, "limited", Perspective("entity", (ident("owner"),)), (EvidenceLink(ident("e:owner"), "support"),), event.id),
        WorldCognition(ident("cog:friend-position"), world.world_id, MemoryTarget("event", event.id), "Friend_X preferred a plan.", "fact", "stated", 600, "limited", Perspective("entity", (ident("friend"),)), (EvidenceLink(ident("e:friend"), "support"),), event.id),
        WorldCognition(ident("cog:shared-friction"), world.world_id, MemoryTarget("event", event.id), "Their planning difference caused friction.", "fact", "stated", 600, "limited", Perspective("joint", (ident("owner"), ident("friend"))), (EvidenceLink(ident("e:event"), "support"),), event.id),
        WorldCognition(ident("cog:owner-unrelated"), world.world_id, MemoryTarget("entity", ident("owner")), "Owner swims weekly.", "fact", "stated", 600, "limited", Perspective("entity", (ident("owner"),)), (EvidenceLink(ident("e:unrelated"), "support"),), "exercise"),
    )
    for cognition in reversed(cognitions) if reverse else cognitions:
        graph.add_cognition(cognition)
    return graph


def _semantic_digest(result: object, graph: MemoryWorldGraph) -> tuple[object, ...]:
    from .recall import MemoryReconstruction
    assert isinstance(result, MemoryReconstruction) and result.primary_anchor is not None
    return (result.status, graph.events[result.primary_anchor.target.id].summary, tuple(sorted(graph.entities[item].canonical_name for item in result.entity_ids)), tuple(sorted(graph.cognitions[item].content for item in result.current_cognition_ids)))


def _graph_digest(graph: MemoryWorldGraph) -> tuple[object, ...]:
    return (tuple(sorted(graph.entities.items())), tuple(sorted(graph.relationships.items())), tuple(sorted(graph.events.items())), tuple(sorted(graph.cognitions.items())))
