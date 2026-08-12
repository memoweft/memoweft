"""Model-free Stage-1 linkage from the frozen transcript through the strong Gate."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path

from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.types import ModelTier
from memoweft.world.delta import WorldDelta
from memoweft.world.extractor import ConversationTurn, WorldExtractor
from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.model import Entity, PersonalWorld
from memoweft.world.nanjing_gate import NanjingGateReport, evaluate_nanjing_gate


_FIXTURE = Path(__file__).parent / "fixtures" / "next" / "golden-001-nanjing"
_ALLOWLIST = frozenset({"turn-001", "turn-003"})


class _ScriptedLLM:
    def __init__(self, replies: str | list[str]) -> None:
        self.replies = [replies] if isinstance(replies, str) else replies
        self.calls = 0
        self.messages: list[list[ChatMessage]] = []

    @property
    def tier(self) -> ModelTier | None:
        return None

    @property
    def call_count(self) -> int:
        return self.calls

    @property
    def usage(self) -> UsageStats | None:
        return None

    def chat(self, messages: list[ChatMessage]) -> str:
        assert messages
        self.messages.append(messages)
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


def _turns() -> list[ConversationTurn]:
    turns: list[ConversationTurn] = []
    for line in (_FIXTURE / "transcript.jsonl").read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        turns.append(
            ConversationTurn(
                item["turn_id"],
                item["conversation_id"],
                item["role"],
                item["content"],
                item["occurred_at"],
            )
        )
    return turns


def _base() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld("world:yun", "person:user"))
    graph.add_entity(Entity("person:user", "world:yun", "person", "User"))
    return graph


def _wire_payload() -> str:
    """A generic-model wire proposal matching the frozen semantic oracle."""
    return json.dumps(
        {
            "world_id": "world:yun",
            "new_entities": [
                {
                    "id": "person:friend-x",
                    "world_id": "world:yun",
                    "kind": "person",
                    "canonical_name": "Friend_X",
                    "aliases": [],
                },
                {
                    "id": "place:nanjing",
                    "world_id": "world:yun",
                    "kind": "place",
                    "canonical_name": "南京",
                    "aliases": [],
                },
                {
                    "id": "activity:nanjing-trip",
                    "world_id": "world:yun",
                    "kind": "activity",
                    "canonical_name": "南京旅行",
                    "aliases": [],
                },
            ],
            "new_relationships": [
                {
                    "id": "relationship:user-friend-x",
                    "world_id": "world:yun",
                    "source_entity_id": "person:user",
                    "target_entity_id": "person:friend-x",
                    "relation_type": "friend",
                    "bidirectional": True,
                }
            ],
            "new_events": [
                {
                    "id": "event:nanjing-conflict",
                    "world_id": "world:yun",
                    "event_type": "interpersonal_conflict",
                    "summary": "关于旅行计划方式的分歧导致的争执",
                    "occurred_at": "2026-08-06T09:01:00+08:00",
                    "participants": [
                        {"entity_id": "person:user", "role": None},
                        {"entity_id": "person:friend-x", "role": None},
                    ],
                    "related_entity_ids": ["place:nanjing", "activity:nanjing-trip"],
                    "relationship_ids": ["relationship:user-friend-x"],
                    "facets": [
                        {
                            "key": "cause",
                            "value": "旅行计划方式的差异",
                            "segment_id": None,
                            "about_entity_id": None,
                        },
                        {
                            "key": "position",
                            "value": None,
                            "segment_id": "seg-0001",
                            "about_entity_id": "person:user",
                        },
                        {
                            "key": "position",
                            "value": None,
                            "segment_id": "seg-0002",
                            "about_entity_id": "person:friend-x",
                        },
                    ],
                    "evidence_ids": ["turn-001", "turn-003"],
                }
            ],
            "new_cognitions": [
                {
                    "id": "cog:user-travel-style",
                    "world_id": "world:yun",
                    "target": {"kind": "entity", "id": "person:user"},
                    "content": None,
                    "content_type": "preference",
                    "model_inferred": False,
                    "perspective": None,
                    "sources": [
                        {
                            "segment_id": "seg-0001",
                            "relation": "support",
                            "proposition_origin": "user_stated",
                            "response_act": "none",
                        }
                    ],
                    "scope": "travel",
                    "valid_at": None,
                    "invalid_at": None,
                },
                {
                    "id": "cog:friend-travel-style",
                    "world_id": "world:yun",
                    "target": {"kind": "entity", "id": "person:friend-x"},
                    "content": None,
                    "content_type": "preference",
                    "model_inferred": False,
                    "perspective": None,
                    "sources": [
                        {
                            "segment_id": "seg-0002",
                            "relation": "support",
                            "proposition_origin": "user_stated",
                            "response_act": "none",
                        }
                    ],
                    "scope": "travel",
                    "valid_at": None,
                    "invalid_at": None,
                },
                {
                    "id": "cog:relationship-travel-friction",
                    "world_id": "world:yun",
                    "target": {
                        "kind": "relationship",
                        "id": "relationship:user-friend-x",
                    },
                    "content": None,
                    "content_type": "hypothesis",
                    "model_inferred": True,
                    "perspective": None,
                    "sources": [
                        {
                            "segment_id": "seg-0003",
                            "relation": "support",
                            "proposition_origin": "user_stated",
                            "response_act": "none",
                        }
                    ],
                    "scope": "travel",
                    "valid_at": None,
                    "invalid_at": None,
                },
            ],
            "unresolved_references": [],
            "semantic_uncertainties": [],
        },
        ensure_ascii=False,
    )


def _evaluate_delta(
    base: MemoryWorldGraph,
    turns: list[ConversationTurn],
    delta: WorldDelta,
) -> tuple[MemoryWorldGraph, NanjingGateReport]:
    preview = delta.apply_to(base, _ALLOWLIST)
    evidence_content = {
        turn.turn_id: turn.content
        for turn in turns
        if turn.turn_id in _ALLOWLIST and turn.role == "user"
    }
    role_by_evidence_id = {turn.turn_id: turn.role for turn in turns}
    preceding_assistant_context = {
        "turn-001": None,
        "turn-003": ("turn-002", next(turn.content for turn in turns if turn.turn_id == "turn-002")),
    }
    report = evaluate_nanjing_gate(
        preview,
        evidence_allowlist=_ALLOWLIST,
        role_by_evidence_id=role_by_evidence_id,
        unresolved_references=delta.unresolved_references,
        semantic_uncertainties=delta.semantic_uncertainties,
        formation_traces=delta.formation_traces,
        evidence_content_by_id=evidence_content,
        preceding_assistant_context_by_evidence_id=preceding_assistant_context,
    )
    return preview, report


def test_frozen_nanjing_wire_passes_real_extractor_materialization_and_strong_gate() -> None:
    turns = _turns()
    base = _base()
    base_before = (
        asdict(base.world),
        tuple(base.entities.items()),
        tuple(base.relationships.items()),
        tuple(base.events.items()),
        tuple(base.cognitions.items()),
    )
    llm = _ScriptedLLM(_wire_payload())

    delta = WorldExtractor(llm).extract(base, turns, _ALLOWLIST)
    preview, report = _evaluate_delta(base, turns, delta)

    assert llm.calls == 1
    assert report.passed
    assert report.violations == ()
    assert all(observation.passed for observation in report.observations)
    observation_codes = {observation.code for observation in report.observations}
    assert {
        "cognitions.exact_targets_perspectives_content",
        "formation.relationship_direct_candidates_unique",
        "formation.relationship_bindings_locally_recomputed",
        "formation.relationship_grounding_independent",
    } <= observation_codes
    assert base_before == (
        asdict(base.world),
        tuple(base.entities.items()),
        tuple(base.relationships.items()),
        tuple(base.events.items()),
        tuple(base.cognitions.items()),
    )
    assert set(base.entities) == {"person:user"}
    assert preview is not base
    assert len(preview.cognitions) == 3


def test_missing_trip_place_repairs_once_then_passes_the_real_strong_gate() -> None:
    correct = json.loads(_wire_payload())
    missing_place = copy.deepcopy(correct)
    missing_place["new_entities"] = [
        entity
        for entity in missing_place["new_entities"]
        if entity["id"] != "place:nanjing"
    ]
    missing_place["new_events"][0]["related_entity_ids"].remove("place:nanjing")
    llm = _ScriptedLLM(
        [
            json.dumps(missing_place, ensure_ascii=False),
            json.dumps(correct, ensure_ascii=False),
        ]
    )
    turns = _turns()
    base = _base()
    base_before = copy.deepcopy(base)

    delta = WorldExtractor(llm).extract(base, turns, _ALLOWLIST)
    _, report = _evaluate_delta(base, turns, delta)

    assert llm.calls == 2
    assert report.passed
    assert report.violations == ()
    assert base == base_before
    assert "TRIP_ACTIVITY_PLACE_ENTITY_REQUIRED@$.new_entities[1]" in (
        llm.messages[1][-1].content
    )
    assert {entity.id for entity in delta.new_entities} >= {
        "activity:nanjing-trip",
        "place:nanjing",
    }
    assert set(delta.new_events[0].related_entity_ids) >= {
        "activity:nanjing-trip",
        "place:nanjing",
    }


def test_diagnostic_six_repair_sequence_passes_the_strong_nanjing_gate() -> None:
    correct = json.loads(_wire_payload())
    invalid_facet = copy.deepcopy(correct)
    invalid_facet["new_events"][0]["facets"][0]["value"] = None
    direct_relationship = copy.deepcopy(correct)
    direct_relationship["new_cognitions"][2].update(
        {
            "content_type": "fact",
            "model_inferred": False,
            "perspective": {"kind": "entity", "holder_entity_ids": ["person:user"]},
            "sources": [
                {
                    "segment_id": "seg-0000",
                    "relation": "support",
                    "proposition_origin": "user_stated",
                    "response_act": "none",
                }
            ],
        }
    )
    llm = _ScriptedLLM(
        [
            json.dumps(invalid_facet, ensure_ascii=False),
            json.dumps(direct_relationship, ensure_ascii=False),
            json.dumps(correct, ensure_ascii=False),
        ]
    )
    turns = _turns()
    base = _base()
    base_before = copy.deepcopy(base)

    delta = WorldExtractor(llm).extract(base, turns, _ALLOWLIST)
    _, report = _evaluate_delta(base, turns, delta)

    assert llm.calls == 3
    assert report.passed
    assert report.violations == ()
    assert base == base_before
    assert "FACET_VALUE_REQUIRED@$.new_events[0].facets[0].value" in llm.messages[1][-1].content
    assert "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids" in (
        llm.messages[2][-1].content
    )
    assert "TARGETED REPAIR: CONDITIONAL CONFLICT RELATIONSHIP TRANSACTION:" in (
        llm.messages[2][-1].content
    )
