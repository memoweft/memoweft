"""Focused tests for deterministic Gate 1 predicate evaluation."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from memoweft.world.model import EventFacet
from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.predicates import evaluate_predicates
from test_world_model_golden_nanjing import build_nanjing_world


_FIXTURE = Path(__file__).parent / "fixtures" / "next" / "golden-001-nanjing"


def _contract() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((_FIXTURE / "expected-predicates.json").read_text(encoding="utf-8")))


def _roles() -> dict[str, str]:
    return {
        str(turn["turn_id"]): str(turn["role"])
        for line in (_FIXTURE / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
        if (turn := cast(dict[str, Any], json.loads(line)))
    }


def _fixture_graph() -> MemoryWorldGraph:
    graph = build_nanjing_world()
    graph.events["event:nanjing-conflict"] = replace(
        graph.events["event:nanjing-conflict"],
        evidence_ids=("turn-001", "turn-003"),
        facets=(
            EventFacet("cause", "双方对旅行计划方式有分歧。"),
            EventFacet("position", "更喜欢开车，不要固定行程。", "person:user"),
            EventFacet("position", "更喜欢提前做行程，照着旅游攻略走。", "person:friend-x"),
        ),
    )
    for cognition_id, cognition in graph.cognitions.items():
        graph.cognitions[cognition_id] = replace(
            cognition,
            sources=tuple(replace(source, evidence_id="turn-003") for source in cognition.sources),
        )
    return graph


def test_frozen_nanjing_contract_passes_after_fixture_ids_are_used() -> None:
    report = evaluate_predicates(
        _fixture_graph(), _contract(), evidence_allowlist={"turn-001", "turn-003"}, role_by_evidence_id=_roles()
    )

    assert report.passed
    assert not report.violations
    assert {observation.section for observation in report.observations} == {"hard_gate", "required", "forbidden"}


@pytest.mark.parametrize(
    ("forbidden", "mutate"),
    [
        ({"predicate": "assistant_turn_is_evidence"}, "assistant"),
        ({"predicate": "evidence_allowlist_contains", "evidence_id": "turn-001"}, "none"),
        ({"predicate": "cognition_content_contains", "text": "用户认为"}, "content"),
        ({"predicate": "unsupported_entity_expansion", "entity_id": "person:friend-x-partner"}, "entity"),
    ],
)
def test_each_forbidden_predicate_rejects_when_observed(forbidden: dict[str, str], mutate: str) -> None:
    graph = _fixture_graph()
    allowlist = {"turn-001", "turn-003"}
    roles = _roles()
    if mutate == "assistant":
        roles["turn-003"] = "assistant"
    elif mutate == "content":
        cognition = graph.cognitions["cog:user-travel-style"]
        graph.cognitions[cognition.id] = replace(cognition, content="用户认为旅行应随性")
    elif mutate == "entity":
        source = graph.entities["person:friend-x"]
        graph.add_entity(replace(source, id="person:friend-x-partner", canonical_name="Partner"))

    report = evaluate_predicates(
        graph, {"required": [], "forbidden": [forbidden]}, evidence_allowlist=allowlist, role_by_evidence_id=roles
    )

    assert not report.passed
    assert any(observation.section == "forbidden" and not observation.passed for observation in report.observations) or mutate == "assistant"


def test_unknown_assistant_or_outside_allowlist_evidence_fails_closed() -> None:
    graph = _fixture_graph()
    graph.events["event:nanjing-conflict"] = replace(
        graph.events["event:nanjing-conflict"], evidence_ids=("turn-001", "unknown-id")
    )

    report = evaluate_predicates(
        graph, {"required": [], "forbidden": []}, evidence_allowlist={"turn-001"}, role_by_evidence_id={"turn-001": "user"}
    )

    assert not report.passed
    assert report.violations == ("hard_gate:no_self_evidence:unknown_evidence",)


def test_unknown_predicate_fails_closed() -> None:
    report = evaluate_predicates(
        _fixture_graph(), {"required": [{"predicate": "made_up"}], "forbidden": []}, evidence_allowlist={"turn-001", "turn-003"}, role_by_evidence_id=_roles()
    )

    assert not report.passed
    assert "required:made_up:unknown_predicate" in report.violations


def test_extra_predicate_field_and_non_authoritative_role_fail_closed() -> None:
    graph = _fixture_graph()
    contract = {
        "required": [{"predicate": "entity_exists", "entity_id": "person:user", "ignored": True}],
        "forbidden": [],
    }
    bad_contract = evaluate_predicates(
        graph,
        contract,
        evidence_allowlist={"turn-001", "turn-003"},
        role_by_evidence_id=_roles(),
    )
    roles = _roles()
    roles["turn-003"] = "system"
    bad_role = evaluate_predicates(
        graph,
        {"required": [], "forbidden": []},
        evidence_allowlist={"turn-001", "turn-003"},
        role_by_evidence_id=roles,
    )

    assert not bad_contract.passed
    assert "required:entity_exists:invalid_fields" in bad_contract.violations
    assert not bad_role.passed
    assert bad_role.violations == ("hard_gate:no_self_evidence:invalid_evidence_role",)


def test_collection_reordering_has_a_stable_report() -> None:
    contract = _contract()
    reordered = {"forbidden": list(reversed(contract["forbidden"])), "required": list(reversed(contract["required"]))}
    allowlist = {"turn-003", "turn-001"}
    roles = _roles()

    assert evaluate_predicates(
        _fixture_graph(), contract, evidence_allowlist=allowlist, role_by_evidence_id=roles
    ) == evaluate_predicates(
        _fixture_graph(), reordered, evidence_allowlist=allowlist, role_by_evidence_id=roles
    )
