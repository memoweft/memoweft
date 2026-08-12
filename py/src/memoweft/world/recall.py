"""Deterministic, graph-first memory reconstruction for MemoWeft Next.

The accepted personal world is the recall authority.  Query text selects an
anchor and a traversal direction; it is never scanned as Evidence and the raw
Evidence ledger is not an anchor-search corpus.  The result retains provenance
references so a caller may hydrate them for inspection after reconstruction.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable, Literal, Mapping, Sequence, cast

from .evolution import (
    AcceptedEvolutionStep,
    RelationshipStateProjection,
    project_relationship_state,
)
from .graph import MemoryWorldGraph
from .model import MemoryTarget, WorldCognition, WorldEvent


QueryIntent = Literal[
    "experience",
    "cause",
    "position",
    "timeline_before",
    "timeline_after",
    "relationship_state",
    "fact",
]
RecallState = Literal["resolved", "ambiguous", "unsupported"]
RecallNodeKind = Literal["entity", "relationship", "event", "cognition"]
ProvenanceRelation = Literal["event_reference", "support", "contradict"]


_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "and",
        "are",
        "did",
        "do",
        "does",
        "i",
        "is",
        "it",
        "me",
        "my",
        "of",
        "remember",
        "she",
        "the",
        "they",
        "we",
        "what",
        "when",
        "where",
        "who",
        "why",
        "you",
    }
)

_CONCEPT_TERMS: Mapping[str, tuple[str, ...]] = {
    "experience": (
        "doing",
        "did",
        "happened",
        "playing",
        "played",
        "using",
        "used",
        "刚才在做",
        "刚刚在做",
        "刚才玩",
        "刚刚玩",
        "在玩",
        "玩过",
        "玩的",
        "做的",
        "用的",
        "发生了什么",
        "干嘛",
    ),
    "cause": ("why", "reason", "cause", "because", "为什么", "为何", "原因", "起因", "怎么会"),
    "conflict": (
        "argue",
        "argued",
        "argument",
        "conflict",
        "disagree",
        "disagreement",
        "吵架",
        "争吵",
        "争执",
        "冲突",
        "分歧",
    ),
    "trip": ("trip", "travel", "journey", "旅行", "旅游", "出行", "行程"),
    "position": ("position", "view", "opinion", "立场", "观点", "想法", "各自"),
    "relationship": ("relationship", "friendship", "关系", "友情", "朋友"),
    "repair": ("apology", "apologize", "repair", "reconcile", "道歉", "修复", "和好"),
    "after": ("after", "later", "next", "之后", "后来", "然后", "后续"),
    "before": ("before", "earlier", "previously", "之前", "先前", "此前"),
    "current": ("current", "currently", "now", "现在", "如今", "目前"),
    "time": ("when", "date", "time", "什么时候", "哪天", "时间"),
    "place": ("where", "place", "destination", "哪里", "哪儿", "地点", "目的地"),
}

_OWNER_TERMS = (" i ", " me ", " my ", " we ", " our ", "我", "我们", "咱们")
_DETAIL_INTENTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("多大", "几岁", "年龄", "how old", "age"), ("岁", "年龄", "生日", "出生", "year old")),
)


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    """A stable, read-only interpretation of one natural-language question."""

    text: str
    normalized_text: str
    intents: tuple[QueryIntent, ...]
    concepts: tuple[str, ...]
    features: tuple[str, ...]
    owner_involved: bool
    resolved_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnchorCandidate:
    """One accepted-world anchor candidate with inspectable scoring reasons."""

    target: MemoryTarget
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecallTraversalEdge:
    source_kind: RecallNodeKind
    source_id: str
    relation: str
    target_kind: RecallNodeKind
    target_id: str


@dataclass(frozen=True, slots=True)
class ProvenanceRef:
    """A graph claim's provenance reference, separate from raw Evidence text."""

    subject_kind: Literal["event", "cognition"]
    subject_id: str
    evidence_id: str
    relation: ProvenanceRelation


@dataclass(frozen=True, slots=True)
class CognitionLineage:
    prior_cognition_id: str
    successor_cognition_id: str
    relation: str


@dataclass(frozen=True, slots=True)
class MemoryReconstruction:
    """The deterministic result of query parsing, anchoring and traversal."""

    status: RecallState
    query: MemoryQuery
    candidates: tuple[AnchorCandidate, ...]
    primary_anchor: AnchorCandidate | None
    reason_code: str | None
    entity_ids: tuple[str, ...] = ()
    relationship_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    current_cognition_ids: tuple[str, ...] = ()
    historical_cognition_ids: tuple[str, ...] = ()
    cognition_lineage: tuple[CognitionLineage, ...] = ()
    relationship_states: tuple[RelationshipStateProjection, ...] = ()
    traversal: tuple[RecallTraversalEdge, ...] = ()
    provenance: tuple[ProvenanceRef, ...] = ()

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.evidence_id for item in self.provenance))


@dataclass(slots=True)
class _CandidateAccumulator:
    score: int
    reasons: set[str]


def parse_memory_query(
    text: str,
    *,
    resolved_entity_ids: Sequence[str] = (),
) -> MemoryQuery:
    """Parse the bounded intent and lexical features used by graph recall."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("memory query must be a non-empty string")
    if isinstance(resolved_entity_ids, (str, bytes)):
        raise TypeError("resolved_entity_ids must be a sequence of ids")
    if any(not isinstance(item, str) or not item.strip() for item in resolved_entity_ids):
        raise ValueError("resolved entity ids must be non-empty strings")
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    concepts = tuple(
        concept
        for concept, terms in _CONCEPT_TERMS.items()
        if any(_term_in_text(term, normalized) for term in terms)
    )
    intents: list[QueryIntent] = []
    if "experience" in concepts:
        intents.append("experience")
    if "cause" in concepts:
        intents.append("cause")
    if "position" in concepts:
        intents.append("position")
    if "before" in concepts:
        intents.append("timeline_before")
    if "after" in concepts:
        intents.append("timeline_after")
    if "current" in concepts or "relationship" in concepts and not intents:
        intents.append("relationship_state")
    if not intents:
        intents.append("fact")
    padded = f" {normalized} "
    return MemoryQuery(
        text=text,
        normalized_text=normalized,
        intents=tuple(intents),
        concepts=concepts,
        features=tuple(sorted(_query_features(normalized))),
        owner_involved=any(term in padded if term.startswith(" ") else term in normalized for term in _OWNER_TERMS),
        resolved_entity_ids=tuple(dict.fromkeys(resolved_entity_ids)),
    )


def reconstruct_memory(
    query: str | MemoryQuery,
    graph: MemoryWorldGraph,
    *,
    superseded_cognition_ids: frozenset[str] = frozenset(),
    cognition_lineage: Sequence[CognitionLineage] = (),
    accepted_evolution_steps: Sequence[AcceptedEvolutionStep] = (),
) -> MemoryReconstruction:
    """Resolve one primary anchor and reconstruct only its relevant local world."""

    if not isinstance(graph, MemoryWorldGraph):
        raise TypeError("graph must be a MemoryWorldGraph")
    parsed = parse_memory_query(query) if isinstance(query, str) else query
    if not isinstance(parsed, MemoryQuery):
        raise TypeError("query must be text or MemoryQuery")
    unknown_resolved = set(parsed.resolved_entity_ids) - set(graph.entities)
    if unknown_resolved:
        raise ValueError("resolved entity id is absent from the accepted graph")
    if any(not isinstance(item, CognitionLineage) for item in cognition_lineage):
        raise TypeError("cognition_lineage must contain CognitionLineage values")
    lineage = tuple(cognition_lineage)
    successors = {
        item.prior_cognition_id: item.successor_cognition_id for item in lineage
    }
    if len(successors) != len(lineage):
        raise ValueError("cognition lineage contains duplicate prior ids")
    if any(prior not in graph.cognitions or successor not in graph.cognitions for prior, successor in successors.items()):
        raise ValueError("cognition successor mapping is not present in the graph")

    candidates, direct_cognition_ids = _rank_anchors(parsed, graph)
    if not candidates:
        return MemoryReconstruction("unsupported", parsed, (), None, "NO_SUPPORTED_ANCHOR")
    preferred = _preferred_candidates(parsed, candidates)
    top_score = preferred[0].score
    tied = tuple(item for item in preferred if item.score == top_score)
    if len(tied) > 1:
        return MemoryReconstruction(
            "ambiguous",
            parsed,
            candidates,
            None,
            "ANCHOR_SCORE_TIE",
        )
    primary = tied[0]
    return _reconstruct_resolved(
        parsed,
        graph,
        candidates,
        primary,
        direct_cognition_ids,
        superseded_cognition_ids,
        lineage,
        tuple(accepted_evolution_steps),
    )


def render_answer_context(
    reconstruction: MemoryReconstruction,
    graph: MemoryWorldGraph,
) -> str:
    """Render graph claims and provenance links without raw Evidence content."""

    if reconstruction.status != "resolved" or reconstruction.primary_anchor is None:
        raise ValueError("only a resolved reconstruction has answer context")
    primary = reconstruction.primary_anchor
    sections: list[str] = [
        "Recall interpretation:\n"
        f"- intents={','.join(reconstruction.query.intents)}\n"
        f"- primary={primary.target.kind}:{primary.target.id}\n"
        f"- anchor_reasons={','.join(primary.reasons)}"
    ]
    sections.append(
        "Entities:\n"
        + "\n".join(
            f"- [{item.id}] {item.kind}: {item.canonical_name}"
            + (f"; aliases={','.join(item.aliases)}" if item.aliases else "")
            for item in (graph.entities[item_id] for item_id in reconstruction.entity_ids)
        )
    )
    state_by_relationship = {
        item.relationship_id: item for item in reconstruction.relationship_states
    }
    relationship_lines: list[str] = []
    for relationship_id in reconstruction.relationship_ids:
        item = graph.relationships[relationship_id]
        source = graph.entities[item.source_entity_id].canonical_name
        target = graph.entities[item.target_entity_id].canonical_name
        state = state_by_relationship.get(item.id)
        suffix = (
            f"; current_state={state.state}; effective_at={state.effective_at}"
            if state is not None
            else ""
        )
        relationship_lines.append(
            f"- [{item.id}] {item.relation_type}: {source} <-> {target}{suffix}"
        )
    sections.append("Relationships:\n" + "\n".join(relationship_lines))
    event_lines: list[str] = []
    for event_id in reconstruction.event_ids:
        event = graph.events[event_id]
        participants = ", ".join(
            f"{graph.entities[item.entity_id].canonical_name}"
            f"({item.role or 'participant'})"
            for item in event.participants
        )
        related = ", ".join(
            graph.entities[item].canonical_name for item in event.related_entity_ids
        )
        facets = "; ".join(
            f"{facet.key}"
            + (
                f"[about={graph.entities[facet.about_entity_id].canonical_name}]"
                if facet.about_entity_id is not None
                else ""
            )
            + f"={facet.value}"
            for facet in event.facets
        )
        event_lines.append(
            f"- [{event.id}] {event.event_type} at {event.occurred_at}: {event.summary}; "
            f"participants={participants}; related={related}; facets={facets}; "
            f"event_evidence_ids={','.join(event.evidence_ids)}"
        )
    sections.append("Events:\n" + "\n".join(event_lines))
    sections.append(
        "Current cognitions:\n"
        + _render_cognitions(reconstruction.current_cognition_ids, graph)
    )
    sections.append(
        "Relevant historical cognitions:\n"
        + _render_cognitions(reconstruction.historical_cognition_ids, graph)
    )
    sections.append(
        "Cognition lineage:\n"
        + "\n".join(
            f"- {item.prior_cognition_id} --{item.relation}--> {item.successor_cognition_id}"
            for item in reconstruction.cognition_lineage
        )
    )
    sections.append(
        "Provenance references (raw Evidence text intentionally omitted):\n"
        + "\n".join(
            f"- {item.subject_kind}:{item.subject_id} --{item.relation}--> {item.evidence_id}"
            for item in reconstruction.provenance
        )
    )
    return "\n\n".join(sections)


def _render_cognitions(
    cognition_ids: Sequence[str],
    graph: MemoryWorldGraph,
) -> str:
    lines: list[str] = []
    for cognition_id in cognition_ids:
        item = graph.cognitions[cognition_id]
        holders = ",".join(
            graph.entities[entity_id].canonical_name
            for entity_id in item.perspective.holder_entity_ids
        )
        perspective = item.perspective.kind + (f"({holders})" if holders else "")
        sources = ",".join(
            f"{source.evidence_id}:{source.relation}" for source in item.sources
        )
        lines.append(
            f"- [{item.id}] target={item.target.kind}:{item.target.id}; "
            f"perspective={perspective}; type={item.content_type}; "
            f"formed_by={item.formed_by}; confidence={item.confidence}; "
            f"cred_status={item.cred_status}; scope={item.scope or ''}; "
            f"valid_at={item.valid_at or ''}; invalid_at={item.invalid_at or ''}; "
            f"content={item.content}; sources={sources}"
        )
    return "\n".join(lines)


def _rank_anchors(
    query: MemoryQuery,
    graph: MemoryWorldGraph,
) -> tuple[tuple[AnchorCandidate, ...], frozenset[str]]:
    accumulated: dict[MemoryTarget, _CandidateAccumulator] = {}
    direct_cognitions: set[str] = set()
    for entity in graph.entities.values():
        score, reasons = _entity_score(query, entity.id, graph)
        _record_candidate(accumulated, MemoryTarget("entity", entity.id), score, reasons)
    for relationship in graph.relationships.values():
        score, reasons = _relationship_score(query, relationship.id, graph)
        _record_candidate(
            accumulated,
            MemoryTarget("relationship", relationship.id),
            score,
            reasons,
        )
    for event in graph.events.values():
        score, reasons = _event_score(query, event, graph)
        _record_candidate(accumulated, MemoryTarget("event", event.id), score, reasons)
    for cognition in graph.cognitions.values():
        score, reasons = _cognition_score(query, cognition, graph)
        if score < 6:
            continue
        direct_cognitions.add(cognition.id)
        _record_candidate(accumulated, cognition.target, score, reasons)
    result = tuple(
        sorted(
            (
                AnchorCandidate(target, value.score, tuple(sorted(value.reasons)))
                for target, value in accumulated.items()
                if value.score >= _anchor_threshold(target.kind)
            ),
            key=lambda item: (
                -item.score,
                _kind_priority(item.target.kind),
                _anchor_semantic_key(graph, item.target),
                item.target.id,
            ),
        )
    )
    return result, frozenset(direct_cognitions)


def _record_candidate(
    accumulated: dict[MemoryTarget, _CandidateAccumulator],
    target: MemoryTarget,
    score: int,
    reasons: Sequence[str],
) -> None:
    if score <= 0:
        return
    previous = accumulated.get(target)
    if previous is None:
        accumulated[target] = _CandidateAccumulator(score, set(reasons))
        return
    previous.score = max(previous.score, score)
    previous.reasons.update(reasons)


def _preferred_candidates(
    query: MemoryQuery,
    candidates: tuple[AnchorCandidate, ...],
) -> tuple[AnchorCandidate, ...]:
    preferred_kind: str | None = None
    if any(
        intent in query.intents
        for intent in (
            "experience",
            "cause",
            "position",
            "timeline_before",
            "timeline_after",
        )
    ):
        preferred_kind = "event"
    elif "relationship_state" in query.intents:
        preferred_kind = "relationship"
    if preferred_kind is not None:
        preferred = tuple(item for item in candidates if item.target.kind == preferred_kind)
        if preferred:
            return preferred
    best_kind = min(_kind_priority(item.target.kind) for item in candidates if item.score == candidates[0].score)
    same_kind = tuple(
        item
        for item in candidates
        if _kind_priority(item.target.kind) == best_kind
    )
    return same_kind


def _reconstruct_resolved(
    query: MemoryQuery,
    graph: MemoryWorldGraph,
    candidates: tuple[AnchorCandidate, ...],
    primary: AnchorCandidate,
    direct_cognition_ids: frozenset[str],
    superseded_ids: frozenset[str],
    cognition_lineage: tuple[CognitionLineage, ...],
    accepted_steps: tuple[AcceptedEvolutionStep, ...],
) -> MemoryReconstruction:
    local = graph.expand(primary.target, depth=1)
    entity_ids: set[str] = set()
    relationship_ids: set[str] = set()
    event_ids: set[str] = set()
    traversal: set[RecallTraversalEdge] = set()

    if primary.target.kind == "event":
        _include_event(
            graph,
            primary.target.id,
            entity_ids,
            relationship_ids,
            event_ids,
            traversal,
        )
        for event_id in _directed_event_ids(query, primary.target.id, accepted_steps):
            _include_event(
                graph,
                event_id,
                entity_ids,
                relationship_ids,
                event_ids,
                traversal,
            )
    elif primary.target.kind == "relationship":
        relationship = graph.relationships[primary.target.id]
        relationship_ids.add(relationship.id)
        for entity_id in (relationship.source_entity_id, relationship.target_entity_id):
            entity_ids.add(entity_id)
            traversal.add(
                RecallTraversalEdge(
                    "relationship",
                    relationship.id,
                    "endpoint",
                    "entity",
                    entity_id,
                )
            )
        event_candidates = {
            item.target.id
            for item in candidates
            if item.target.kind == "event"
            and item.target.id in local.event_ids
            and relationship.id in graph.events[item.target.id].relationship_ids
        }
        if any(intent in query.intents for intent in ("timeline_before", "timeline_after")):
            event_candidates.update(
                _relationship_directional_events(query, relationship.id, accepted_steps)
            )
        for event_id in sorted(event_candidates, key=lambda item: (graph.events[item].occurred_at, item)):
            _include_event(
                graph,
                event_id,
                entity_ids,
                relationship_ids,
                event_ids,
                traversal,
            )
    else:
        entity_ids.add(primary.target.id)
        for candidate in candidates:
            if candidate.target.kind == "event" and candidate.target.id in local.event_ids:
                event = graph.events[candidate.target.id]
                if _event_mentions_entity(event, primary.target.id):
                    _include_event(
                        graph,
                        event.id,
                        entity_ids,
                        relationship_ids,
                        event_ids,
                        traversal,
                    )
            elif candidate.target.kind == "relationship" and candidate.target.id in local.relationship_ids:
                relationship = graph.relationships[candidate.target.id]
                relationship_ids.add(relationship.id)
                entity_ids.update((relationship.source_entity_id, relationship.target_entity_id))

    selected_evidence_ids = {
        evidence_id
        for event_id in event_ids
        for evidence_id in graph.events[event_id].evidence_ids
    }
    context_text = _selection_context_text(query, graph, entity_ids, relationship_ids, event_ids)
    relevant_cognitions = {
        cognition.id
        for cognition in graph.cognitions.values()
        if cognition.target.id
        in (
            entity_ids
            if cognition.target.kind == "entity"
            else relationship_ids
            if cognition.target.kind == "relationship"
            else event_ids
            if cognition.target.kind == "event"
            else set()
        )
        and _cognition_relevant(
            cognition,
            query,
            graph,
            context_text,
            selected_evidence_ids,
            direct_cognition_ids,
            primary.target,
        )
    }
    relevant_cognitions = _transition_closure(
        relevant_cognitions,
        {
            item.prior_cognition_id: item.successor_cognition_id
            for item in cognition_lineage
        },
    )
    current_ids = relevant_cognitions - set(superseded_ids)
    historical_ids = relevant_cognitions & set(superseded_ids)

    for cognition_id in current_ids | historical_ids:
        cognition = graph.cognitions[cognition_id]
        if cognition.target.kind == "world":
            continue
        traversal.add(
            RecallTraversalEdge(
                cast(RecallNodeKind, cognition.target.kind),
                cognition.target.id,
                "current_cognition" if cognition_id in current_ids else "historical_cognition",
                "cognition",
                cognition_id,
            )
        )

    relationship_states = tuple(
        state
        for relationship_id in sorted(relationship_ids)
        for state in (project_relationship_state(graph, accepted_steps, relationship_id),)
        if state is not None
    )
    ordered_events = tuple(sorted(event_ids, key=lambda item: (graph.events[item].occurred_at, item)))
    ordered_current = tuple(sorted(current_ids, key=lambda item: _cognition_sort_key(graph.cognitions[item])))
    ordered_history = tuple(sorted(historical_ids, key=lambda item: _cognition_sort_key(graph.cognitions[item])))
    selected_lineage = tuple(
        sorted(
            (
                item
                for item in cognition_lineage
                if item.prior_cognition_id in relevant_cognitions
                and item.successor_cognition_id in relevant_cognitions
            ),
            key=lambda item: (
                item.prior_cognition_id,
                item.successor_cognition_id,
                item.relation,
            ),
        )
    )
    provenance = _provenance(graph, ordered_events, ordered_current, ordered_history)
    return MemoryReconstruction(
        "resolved",
        query,
        candidates,
        primary,
        None,
        tuple(sorted(entity_ids, key=lambda item: _entity_sort_key(graph, item))),
        tuple(sorted(relationship_ids, key=lambda item: _relationship_sort_key(graph, item))),
        ordered_events,
        ordered_current,
        ordered_history,
        selected_lineage,
        relationship_states,
        tuple(sorted(traversal, key=_traversal_sort_key)),
        provenance,
    )


def _include_event(
    graph: MemoryWorldGraph,
    event_id: str,
    entity_ids: set[str],
    relationship_ids: set[str],
    event_ids: set[str],
    traversal: set[RecallTraversalEdge],
) -> None:
    event = graph.events[event_id]
    event_ids.add(event.id)
    for participant in event.participants:
        entity_ids.add(participant.entity_id)
        traversal.add(
            RecallTraversalEdge(
                "event",
                event.id,
                f"participant:{participant.role or 'unspecified'}",
                "entity",
                participant.entity_id,
            )
        )
    for entity_id in event.related_entity_ids:
        entity_ids.add(entity_id)
        traversal.add(
            RecallTraversalEdge("event", event.id, "related_entity", "entity", entity_id)
        )
    for facet in event.facets:
        if facet.about_entity_id is not None:
            entity_ids.add(facet.about_entity_id)
            traversal.add(
                RecallTraversalEdge(
                    "event",
                    event.id,
                    f"facet:{facet.key}",
                    "entity",
                    facet.about_entity_id,
                )
            )
    for relationship_id in event.relationship_ids:
        relationship_ids.add(relationship_id)
        relationship = graph.relationships[relationship_id]
        entity_ids.update((relationship.source_entity_id, relationship.target_entity_id))
        traversal.add(
            RecallTraversalEdge(
                "event",
                event.id,
                "relationship",
                "relationship",
                relationship_id,
            )
        )


def _directed_event_ids(
    query: MemoryQuery,
    anchor_event_id: str,
    accepted_steps: tuple[AcceptedEvolutionStep, ...],
) -> tuple[str, ...]:
    include_before = "timeline_before" in query.intents
    include_after = "timeline_after" in query.intents
    if not include_before and not include_after:
        return ()
    before: dict[str, set[str]] = {}
    after: dict[str, set[str]] = {}
    for accepted in accepted_steps:
        step = accepted.step
        if step.kind != "event_link" or len(step.predecessor_ids) != 1 or len(step.successor_ids) != 1:
            continue
        prior, successor = step.predecessor_ids[0], step.successor_ids[0]
        after.setdefault(prior, set()).add(successor)
        before.setdefault(successor, set()).add(prior)
    result: set[str] = set()
    frontier = {anchor_event_id}
    adjacency = before if include_before and not include_after else after if include_after and not include_before else None
    if adjacency is None:
        adjacency = {
            key: set(before.get(key, set())) | set(after.get(key, set()))
            for key in set(before) | set(after)
        }
    while frontier:
        current = frontier.pop()
        for neighbor in adjacency.get(current, set()):
            if neighbor != anchor_event_id and neighbor not in result:
                result.add(neighbor)
                frontier.add(neighbor)
    return tuple(sorted(result))


def _relationship_directional_events(
    query: MemoryQuery,
    relationship_id: str,
    accepted_steps: tuple[AcceptedEvolutionStep, ...],
) -> tuple[str, ...]:
    linked = [
        accepted.step
        for accepted in accepted_steps
        if accepted.step.kind == "event_link"
        and accepted.step.subject == MemoryTarget("relationship", relationship_id)
    ]
    if not linked:
        return ()
    event_ids = {item for step in linked for item in (*step.predecessor_ids, *step.successor_ids)}
    return tuple(sorted(event_ids))


def _transition_closure(
    selected: set[str],
    successors: Mapping[str, str],
) -> set[str]:
    result = set(selected)
    predecessors = {successor: prior for prior, successor in successors.items()}
    frontier = list(selected)
    while frontier:
        current = frontier.pop()
        for neighbor in (successors.get(current), predecessors.get(current)):
            if neighbor is not None and neighbor not in result:
                result.add(neighbor)
                frontier.append(neighbor)
    return result


def _cognition_relevant(
    cognition: WorldCognition,
    query: MemoryQuery,
    graph: MemoryWorldGraph,
    context_text: str,
    event_evidence_ids: set[str],
    direct_cognition_ids: frozenset[str],
    primary_target: MemoryTarget,
) -> bool:
    if cognition.id in direct_cognition_ids:
        return True
    if cognition.target == primary_target and cognition.target.kind in {"event", "relationship"}:
        return True
    if cognition.target == primary_target and cognition.target.kind == "entity":
        entity = graph.entities[cognition.target.id]
        if any(
            _contains_label(cognition.content.casefold(), label)
            for label in (entity.canonical_name, *entity.aliases)
        ):
            return True
    if any(source.evidence_id in event_evidence_ids for source in cognition.sources):
        return True
    text = f"{cognition.content} {cognition.scope or ''}"
    if _lexical_score(query.features, text) >= 6:
        return True
    if _concept_overlap(query.concepts, text):
        return True
    return _lexical_score(_query_features(context_text), text) >= 9


def _provenance(
    graph: MemoryWorldGraph,
    event_ids: tuple[str, ...],
    current_ids: tuple[str, ...],
    history_ids: tuple[str, ...],
) -> tuple[ProvenanceRef, ...]:
    refs: set[ProvenanceRef] = set()
    for event_id in event_ids:
        for evidence_id in graph.events[event_id].evidence_ids:
            refs.add(ProvenanceRef("event", event_id, evidence_id, "event_reference"))
    for cognition_id in (*current_ids, *history_ids):
        cognition = graph.cognitions[cognition_id]
        for source in cognition.sources:
            refs.add(
                ProvenanceRef(
                    "cognition",
                    cognition_id,
                    source.evidence_id,
                    source.relation,
                )
            )
    return tuple(
        sorted(
            refs,
            key=lambda item: (
                item.subject_kind,
                item.subject_id,
                item.relation,
                item.evidence_id,
            ),
        )
    )


def _entity_score(
    query: MemoryQuery,
    entity_id: str,
    graph: MemoryWorldGraph,
) -> tuple[int, tuple[str, ...]]:
    entity = graph.entities[entity_id]
    score = 0
    reasons: list[str] = []
    if entity.id in query.resolved_entity_ids:
        score += 36
        reasons.append("entity_identity_resolved")
    if any(_contains_label(query.normalized_text, label) for label in (entity.canonical_name, *entity.aliases)):
        score += 30
        reasons.append("entity_name_exact")
    lexical = min(18, _lexical_score(query.features, " ".join((entity.canonical_name, *entity.aliases, entity.kind))))
    if lexical >= 6:
        score += lexical
        reasons.append("entity_label_overlap")
    return score, tuple(reasons)


def _relationship_score(
    query: MemoryQuery,
    relationship_id: str,
    graph: MemoryWorldGraph,
) -> tuple[int, tuple[str, ...]]:
    relationship = graph.relationships[relationship_id]
    source = graph.entities[relationship.source_entity_id]
    target = graph.entities[relationship.target_entity_id]
    text = " ".join(
        (
            relationship.relation_type,
            source.canonical_name,
            *source.aliases,
            target.canonical_name,
            *target.aliases,
        )
    )
    score = min(18, _lexical_score(query.features, text))
    reasons: list[str] = ["relationship_structure_overlap"] if score >= 6 else []
    resolved = set(query.resolved_entity_ids)
    if resolved.intersection((source.id, target.id)):
        score += 24
        reasons.append("relationship_resolved_endpoint")
    if "relationship" in query.concepts and score:
        score += 12
        reasons.append("relationship_intent")
    if query.owner_involved and graph.world.owner_entity_id in (source.id, target.id) and resolved:
        score += 6
        reasons.append("relationship_owner_and_resolved_party")
    return score, tuple(reasons)


def _event_score(
    query: MemoryQuery,
    event: WorldEvent,
    graph: MemoryWorldGraph,
) -> tuple[int, tuple[str, ...]]:
    participant_names = " ".join(
        graph.entities[item.entity_id].canonical_name for item in event.participants
    )
    related_names = " ".join(
        " ".join((graph.entities[item].canonical_name, *graph.entities[item].aliases))
        for item in event.related_entity_ids
    )
    facet_text = " ".join(f"{item.key} {item.value}" for item in event.facets)
    structured = (
        ("event_type_overlap", event.event_type),
        ("event_summary_overlap", event.summary),
        ("event_facet_overlap", facet_text),
        ("event_participant_overlap", participant_names),
        ("event_related_entity_overlap", related_names),
    )
    score = 0
    reasons: list[str] = []
    for reason, text in structured:
        value = min(18, _lexical_score(query.features, text))
        if value >= 3:
            score += value
            reasons.append(reason)
    event_text = " ".join(item[1] for item in structured)
    for concept in query.concepts:
        if concept in {"cause", "position", "after", "before", "current", "time", "place"}:
            continue
        if _text_has_concept(event_text, concept):
            score += 8
            reasons.append(f"event_concept:{concept}")
    if "cause" in query.intents and any(_facet_is(item.key, "cause") for item in event.facets):
        score += 18
        reasons.append("event_cause_facet")
    if "position" in query.intents and any(_facet_is(item.key, "position") for item in event.facets):
        score += 12
        reasons.append("event_position_facet")
    if "place" in query.concepts and any(_facet_is(item.key, "destination", "place") for item in event.facets):
        score += 8
        reasons.append("event_place_facet")
    resolved = set(query.resolved_entity_ids)
    participant_ids = {item.entity_id for item in event.participants}
    if resolved.intersection(participant_ids):
        score += 24
        reasons.append("event_resolved_participant")
    if query.owner_involved and graph.world.owner_entity_id in participant_ids and score:
        score += 4
        reasons.append("event_owner_participant")
    return score, tuple(dict.fromkeys(reasons))


def _cognition_score(
    query: MemoryQuery,
    cognition: WorldCognition,
    graph: MemoryWorldGraph,
) -> tuple[int, tuple[str, ...]]:
    text = f"{cognition.content} {cognition.scope or ''}"
    score = min(30, _lexical_score(query.features, text))
    reasons: list[str] = ["cognition_content_overlap"] if score >= 6 else []
    declared_names = _content_declared_names(cognition.content)
    declared_match = any(
        _contains_label(query.normalized_text, name) for name in declared_names
    )
    detail_required = any(
        any(_term_in_text(term, query.normalized_text) for term in query_terms)
        for query_terms, _ in _DETAIL_INTENTS
    )
    detail_matches = any(
        any(_term_in_text(term, query.normalized_text) for term in query_terms)
        and any(_term_in_text(term, cognition.content.casefold()) for term in content_terms)
        for query_terms, content_terms in _DETAIL_INTENTS
    )
    if declared_match and detail_required and not detail_matches:
        declared_names = ()
        declared_match = False
    if declared_match:
        score += 24
        reasons.append("cognition_declared_name")
        if detail_matches:
            score += 6
            reasons.append("cognition_detail_intent")
    overlaps = _concept_overlap(query.concepts, text)
    if overlaps:
        score += min(12, 6 * len(overlaps))
        reasons.extend(f"cognition_concept:{item}" for item in overlaps)
    if cognition.target.kind == "entity":
        target = graph.entities[cognition.target.id]
        target_explicit = target.id in query.resolved_entity_ids or any(
            _contains_label(query.normalized_text, label)
            for label in (target.canonical_name, *target.aliases)
        )
        if target.id != graph.world.owner_entity_id and not target_explicit and not declared_names and score < 12:
            return 0, ()
    return score, tuple(dict.fromkeys(reasons))


def _selection_context_text(
    query: MemoryQuery,
    graph: MemoryWorldGraph,
    entity_ids: set[str],
    relationship_ids: set[str],
    event_ids: set[str],
) -> str:
    parts = [query.normalized_text]
    parts.extend(
        " ".join((graph.entities[item].canonical_name, *graph.entities[item].aliases))
        for item in entity_ids
    )
    parts.extend(graph.relationships[item].relation_type for item in relationship_ids)
    for event_id in event_ids:
        event = graph.events[event_id]
        parts.extend((event.event_type, event.summary))
        parts.extend(f"{facet.key} {facet.value}" for facet in event.facets)
    return " ".join(parts)


def _concept_overlap(concepts: Sequence[str], text: str) -> tuple[str, ...]:
    return tuple(concept for concept in concepts if _text_has_concept(text, concept))


def _text_has_concept(text: str, concept: str) -> bool:
    lowered = unicodedata.normalize("NFKC", text).casefold()
    return any(_term_in_text(term, lowered) for term in _CONCEPT_TERMS.get(concept, ()))


def _query_features(text: str) -> frozenset[str]:
    words = {
        token
        for token in re.findall(r"[a-z0-9_]+", text.casefold())
        if len(token) > 1 and token not in _STOP_WORDS
    }
    cjk: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for width in range(2, min(4, len(run)) + 1):
            cjk.update(run[index : index + width] for index in range(len(run) - width + 1))
    return frozenset(words | cjk)


def _lexical_score(features: Iterable[str], text: str) -> int:
    lowered = unicodedata.normalize("NFKC", text).casefold()
    return sum(3 for feature in features if len(feature) > 1 and feature in lowered)


def _term_in_text(term: str, text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", term).casefold()
    if normalized.isascii() and normalized.replace("_", "").isalnum():
        return re.search(rf"(?<![a-z0-9_]){re.escape(normalized)}(?![a-z0-9_])", text) is not None
    return normalized in text


def _contains_label(query_text: str, label: str) -> bool:
    normalized = unicodedata.normalize("NFKC", label).casefold().strip()
    if len(normalized) < 2:
        return False
    return _term_in_text(normalized, query_text)


def _content_declared_names(content: str) -> tuple[str, ...]:
    pattern = re.compile(
        r"(?:名叫|叫|称为|名字(?:叫|是))\s*"
        r"(?P<name>[A-Za-z0-9_-]{2,32}|[\u4e00-\u9fff]{2,8}?)"
        r"(?=[，。！？、；：,\s]|但|也|是|的|今年|已经|$)"
    )
    return tuple(
        match.group("name").casefold().strip()
        for match in pattern.finditer(content)
    )


def _facet_is(key: str, *expected: str) -> bool:
    normalized = key.casefold().strip()
    return normalized in expected


def _anchor_threshold(kind: str) -> int:
    return 9


def _kind_priority(kind: str) -> int:
    return {"event": 0, "relationship": 1, "entity": 2}.get(kind, 3)


def _anchor_semantic_key(graph: MemoryWorldGraph, target: MemoryTarget) -> tuple[str, ...]:
    if target.kind == "event":
        event = graph.events[target.id]
        return (event.event_type.casefold(), event.occurred_at, event.summary.casefold())
    if target.kind == "relationship":
        relationship = graph.relationships[target.id]
        endpoints = sorted(
            (
                graph.entities[relationship.source_entity_id].canonical_name.casefold(),
                graph.entities[relationship.target_entity_id].canonical_name.casefold(),
            )
        )
        return (relationship.relation_type.casefold(), *endpoints)
    entity = graph.entities[target.id]
    return (
        entity.kind.casefold(),
        entity.canonical_name.casefold(),
        *sorted(alias.casefold() for alias in entity.aliases),
    )


def _entity_sort_key(graph: MemoryWorldGraph, entity_id: str) -> tuple[str, str, str]:
    item = graph.entities[entity_id]
    return item.kind.casefold(), item.canonical_name.casefold(), item.id


def _relationship_sort_key(graph: MemoryWorldGraph, relationship_id: str) -> tuple[str, tuple[str, ...], str]:
    item = graph.relationships[relationship_id]
    return item.relation_type.casefold(), _anchor_semantic_key(graph, MemoryTarget("relationship", item.id)), item.id


def _cognition_sort_key(cognition: WorldCognition) -> tuple[str, str, str, str]:
    return cognition.target.kind, cognition.target.id, cognition.content.casefold(), cognition.id


def _traversal_sort_key(item: RecallTraversalEdge) -> tuple[str, str, str, str, str]:
    return item.source_kind, item.source_id, item.relation, item.target_kind, item.target_id


def _event_mentions_entity(event: WorldEvent, entity_id: str) -> bool:
    return (
        entity_id in {item.entity_id for item in event.participants}
        or entity_id in event.related_entity_ids
        or any(item.about_entity_id == entity_id for item in event.facets)
    )


__all__ = [
    "AnchorCandidate",
    "CognitionLineage",
    "MemoryQuery",
    "MemoryReconstruction",
    "ProvenanceRef",
    "QueryIntent",
    "RecallState",
    "RecallTraversalEdge",
    "parse_memory_query",
    "reconstruct_memory",
    "render_answer_context",
]
