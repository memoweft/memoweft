"""Deterministic Gate 1 predicates for a materialized memory-world graph.

This module deliberately has no extraction, persistence, or model dependency.
It evaluates the graph after a proposed delta has been applied to an isolated
copy, so a caller can reject the delta without changing the base world.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import unicodedata
from typing import Any

from .graph import MemoryWorldGraph


@dataclass(frozen=True, slots=True)
class PredicateObservation:
    """One reproducible predicate result."""

    section: str
    predicate: str
    observed: bool
    passed: bool
    parameters: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PredicateReport:
    """Complete deterministic result, suitable for a review record."""

    observations: tuple[PredicateObservation, ...]
    violations: tuple[str, ...]
    passed: bool


_PREDICATES = frozenset(
    {
        "entity_exists",
        "relationship_exists",
        "event_exists",
        "event_related_entity",
        "event_cause_contains",
        "event_position_contains",
        "cognition_target",
        "evidence_allowlist_contains",
        "assistant_turn_is_evidence",
        "cognition_content_contains",
        "unsupported_entity_expansion",
    }
)

_EXACT_FIELDS: dict[str, tuple[frozenset[str], ...]] = {
    "entity_exists": (frozenset({"predicate", "entity_id"}),),
    "relationship_exists": (
        frozenset({"predicate", "relationship_id"}),
        frozenset({"predicate", "relationship_id", "relation_type"}),
    ),
    "event_exists": (
        frozenset({"predicate", "event_id"}),
        frozenset({"predicate", "event_id", "event_type"}),
    ),
    "event_related_entity": (frozenset({"predicate", "event_id", "entity_id"}),),
    "event_cause_contains": (frozenset({"predicate", "event_id", "text"}),),
    "event_position_contains": (
        frozenset({"predicate", "event_id", "about_entity_id", "text"}),
    ),
    "cognition_target": (
        frozenset({"predicate", "cognition_id", "target_type", "target_id"}),
        frozenset({"predicate", "cognition_id", "target_type", "target_id", "scope"}),
    ),
    "evidence_allowlist_contains": (frozenset({"predicate", "evidence_id"}),),
    "assistant_turn_is_evidence": (frozenset({"predicate"}),),
    "cognition_content_contains": (frozenset({"predicate", "text"}),),
    "unsupported_entity_expansion": (frozenset({"predicate", "entity_id"}),),
}


def evaluate_predicates(
    graph: MemoryWorldGraph,
    contract: Mapping[str, Any],
    *,
    evidence_allowlist: Iterable[str],
    role_by_evidence_id: Mapping[str, str],
) -> PredicateReport:
    """Evaluate a JSON-loaded Gate 1 contract without any I/O.

    Required predicates pass only when observed.  Forbidden predicates pass
    only when not observed.  Invalid contracts, malformed predicates, and
    unknown predicate names are recorded as failures rather than ignored.
    The no-self-evidence rule is always evaluated, even if the contract omits
    ``assistant_turn_is_evidence``.
    """
    observations: list[PredicateObservation] = []
    violations: list[str] = []
    allowlist, allowlist_error = _string_set(evidence_allowlist)
    if allowlist_error:
        observations.append(_invalid_observation("hard_gate", "no_self_evidence", "invalid_allowlist"))
        violations.append("hard_gate:no_self_evidence:invalid_allowlist")
    else:
        hard_passed, reason = _no_self_evidence(graph, allowlist, role_by_evidence_id)
        observations.append(
            PredicateObservation("hard_gate", "no_self_evidence", hard_passed, hard_passed, ())
        )
        if not hard_passed:
            violations.append(f"hard_gate:no_self_evidence:{reason}")

    if not isinstance(contract, Mapping):
        observations.append(_invalid_observation("contract", "contract", "not_mapping"))
        violations.append("contract:not_mapping")
        return _report(observations, violations)

    for section, expectation in (("required", True), ("forbidden", False)):
        entries = contract.get(section)
        if not isinstance(entries, list):
            observations.append(_invalid_observation(section, "contract", f"{section}_not_list"))
            violations.append(f"{section}:contract_not_list")
            continue
        prepared: list[tuple[str, Mapping[str, Any]]] = []
        for item in entries:
            if not isinstance(item, Mapping) or not isinstance(item.get("predicate"), str):
                observations.append(_invalid_observation(section, "invalid", "invalid_predicate"))
                violations.append(f"{section}:invalid_predicate")
                continue
            prepared.append((_canonical(item), item))
        for _, item in sorted(prepared, key=lambda pair: pair[0]):
            predicate = item["predicate"]
            observed, error = _evaluate(graph, predicate, item, allowlist, role_by_evidence_id)
            passed = error is None and observed is expectation
            observations.append(
                PredicateObservation(section, predicate, observed, passed, _parameters(item))
            )
            if not passed:
                suffix = error or ("not_observed" if expectation else "observed")
                violations.append(f"{section}:{predicate}:{suffix}")

    return _report(observations, violations)


def _report(observations: list[PredicateObservation], violations: list[str]) -> PredicateReport:
    return PredicateReport(tuple(observations), tuple(sorted(violations)), not violations)


def _invalid_observation(section: str, predicate: str, reason: str) -> PredicateObservation:
    return PredicateObservation(section, predicate, False, False, (("reason", reason),))


def _evaluate(
    graph: MemoryWorldGraph,
    predicate: str,
    item: Mapping[str, Any],
    allowlist: frozenset[str],
    roles: Mapping[str, str],
) -> tuple[bool, str | None]:
    if predicate not in _PREDICATES:
        return False, "unknown_predicate"
    if frozenset(item) not in _EXACT_FIELDS[predicate]:
        return False, "invalid_fields"
    try:
        if predicate == "entity_exists":
            return _has_id(item, "entity_id") and item["entity_id"] in graph.entities, None
        if predicate == "relationship_exists":
            if not _has_id(item, "relationship_id"):
                return False, "invalid_fields"
            relationship = graph.relationships.get(item["relationship_id"])
            if relationship is None:
                return False, None
            if "relation_type" in item and not _nonempty_string(item["relation_type"]):
                return False, "invalid_fields"
            return ("relation_type" not in item or relationship.relation_type == item["relation_type"]), None
        if predicate == "event_exists":
            if not _has_id(item, "event_id"):
                return False, "invalid_fields"
            event = graph.events.get(item["event_id"])
            if event is None:
                return False, None
            if "event_type" in item and not _nonempty_string(item["event_type"]):
                return False, "invalid_fields"
            return ("event_type" not in item or event.event_type == item["event_type"]), None
        if predicate == "event_related_entity":
            if not (_has_id(item, "event_id") and _has_id(item, "entity_id")):
                return False, "invalid_fields"
            event = graph.events.get(item["event_id"])
            return event is not None and item["entity_id"] in event.related_entity_ids, None
        if predicate in {"event_cause_contains", "event_position_contains"}:
            if not (_has_id(item, "event_id") and _has_text(item)):
                return False, "invalid_fields"
            if predicate == "event_position_contains" and not _has_id(item, "about_entity_id"):
                return False, "invalid_fields"
            event = graph.events.get(item["event_id"])
            if event is None:
                return False, None
            key = "cause" if predicate == "event_cause_contains" else "position"
            return any(
                facet.key == key
                and (predicate == "event_cause_contains" or facet.about_entity_id == item["about_entity_id"])
                and _contains(facet.value, item["text"])
                for facet in event.facets
            ), None
        if predicate == "cognition_target":
            if not all(_has_id(item, field) for field in ("cognition_id", "target_type", "target_id")):
                return False, "invalid_fields"
            cognition = graph.cognitions.get(item["cognition_id"])
            if cognition is None:
                return False, None
            if "scope" in item and not _nonempty_string(item["scope"]):
                return False, "invalid_fields"
            return (
                cognition.target.kind == item["target_type"]
                and cognition.target.id == item["target_id"]
                and ("scope" not in item or cognition.scope == item["scope"])
            ), None
        if predicate == "evidence_allowlist_contains":
            return _has_id(item, "evidence_id") and item["evidence_id"] in allowlist, None
        if predicate == "assistant_turn_is_evidence":
            return any(roles.get(evidence_id) == "assistant" for evidence_id in _used_evidence_ids(graph, allowlist)), None
        if predicate == "cognition_content_contains":
            if not _has_text(item):
                return False, "invalid_fields"
            return any(_contains(cognition.content, item["text"]) for cognition in graph.cognitions.values()), None
        if predicate == "unsupported_entity_expansion":
            if not _has_id(item, "entity_id"):
                return False, "invalid_fields"
            return item["entity_id"] in _referenced_entity_ids(graph), None
    except (AttributeError, TypeError):
        return False, "invalid_graph"
    return False, "unknown_predicate"


def _no_self_evidence(
    graph: MemoryWorldGraph, allowlist: frozenset[str], roles: Mapping[str, str]
) -> tuple[bool, str]:
    try:
        used = _used_evidence_ids(graph, allowlist)
    except (AttributeError, TypeError):
        return False, "invalid_graph"
    for evidence_id in sorted(used):
        role = roles.get(evidence_id)
        if not _nonempty_string(evidence_id) or not isinstance(role, str):
            return False, "unknown_evidence"
        if role == "assistant":
            return False, "assistant_evidence"
        if role not in {"user", "tool"}:
            return False, "invalid_evidence_role"
        if evidence_id not in allowlist:
            return False, "evidence_outside_allowlist"
    return True, ""


def _used_evidence_ids(graph: MemoryWorldGraph, allowlist: frozenset[str]) -> set[str]:
    used = set(allowlist)
    used.update(evidence_id for event in graph.events.values() for evidence_id in event.evidence_ids)
    used.update(link.evidence_id for cognition in graph.cognitions.values() for link in cognition.sources)
    return used


def _referenced_entity_ids(graph: MemoryWorldGraph) -> frozenset[str]:
    ids = set(graph.entities)
    for relationship in graph.relationships.values():
        ids.update((relationship.source_entity_id, relationship.target_entity_id))
    for event in graph.events.values():
        ids.update(participant.entity_id for participant in event.participants)
        ids.update(event.related_entity_ids)
        ids.update(facet.about_entity_id for facet in event.facets if facet.about_entity_id is not None)
    for cognition in graph.cognitions.values():
        if cognition.target.kind == "entity":
            ids.add(cognition.target.id)
        ids.update(cognition.perspective.holder_entity_ids)
    return frozenset(ids)


def _string_set(values: Iterable[str]) -> tuple[frozenset[str], bool]:
    if isinstance(values, (str, bytes)):
        return frozenset(), True
    try:
        result = frozenset(values)
    except TypeError:
        return frozenset(), True
    return result, any(not _nonempty_string(value) for value in result)


def _has_id(item: Mapping[str, Any], field: str) -> bool:
    return _nonempty_string(item.get(field))


def _has_text(item: Mapping[str, Any]) -> bool:
    return _nonempty_string(item.get("text"))


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _contains(haystack: str, needle: str) -> bool:
    return _normalize(needle) in _normalize(haystack)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _parameters(item: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), _canonical(value)) for key, value in item.items() if key != "predicate"))


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
