"""Contract tests for the Stage-1, review-only WorldExtractor."""
from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import asdict, is_dataclass

import pytest

from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.types import ModelTier
from memoweft.world.extractor import (
    ConversationTurn,
    FORMATION_CONTRACT_VERSION,
    WorldExtractionError,
    WorldExtractor,
    _segment_catalog,
    _targeted_repair_instruction,
    world_delta_response_format,
)
from memoweft.world.entity_resolution import AcceptedEntityReference
from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.model import (
    Entity,
    MemoryTarget,
    PersonalWorld,
    Perspective,
    Relationship,
    WorldCognition,
)
from memoweft.world.semantics import is_interpersonal_conflict_type


class ScriptedLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.calls = 0
        self.messages: list[list[ChatMessage]] = []

    @property
    def call_count(self) -> int:
        return self.calls

    @property
    def tier(self) -> ModelTier | None:
        return None

    @property
    def usage(self) -> UsageStats | None:
        return None

    def chat(self, messages: list[ChatMessage]) -> str:
        self.messages.append(messages)
        reply = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return reply


class TimeoutOnSecondLLM(ScriptedLLM):
    def chat(self, messages: list[ChatMessage]) -> str:
        if self.calls == 1:
            self.messages.append(messages)
            self.calls += 1
            raise TimeoutError("simulated")
        return super().chat(messages)


def _base() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld("world:yun", "person:user"))
    graph.add_entity(Entity("person:user", "world:yun", "person", "User"))
    return graph


def _turns() -> list[ConversationTurn]:
    return [
        ConversationTurn("turn:user", "conversation:one", "user", "Friend_X is my friend.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "That sounds meaningful.", "2026-08-08T10:00:01+08:00"),
    ]


def _payload(**override: object) -> str:
    value: dict[str, object] = {
        "world_id": "world:yun",
        "new_entities": [
            {
                "id": "person:friend-x",
                "world_id": "world:yun",
                "kind": "person",
                "canonical_name": "Friend_X",
                "aliases": [],
            }
        ],
        "new_relationships": [],
        "new_events": [],
        "new_cognitions": [],
        "unresolved_references": [],
        "semantic_uncertainties": [],
    }
    value.update(override)
    return json.dumps(value, ensure_ascii=False)


def _trip_payload(
    *,
    anchor: str = "kyoto",
    include_place: bool = True,
    include_place_link: bool = True,
    extra_place_anchors: tuple[str, ...] = (),
    activity_id: str | None = None,
) -> str:
    resolved_activity_id = activity_id or f"activity:{anchor}-trip"
    entities: list[dict[str, object]] = [
        {
            "id": resolved_activity_id,
            "world_id": "world:yun",
            "kind": "activity",
            "canonical_name": f"{anchor.title()} trip",
            "aliases": [],
        }
    ]
    if include_place:
        entities.append(
            {
                "id": f"place:{anchor}",
                "world_id": "world:yun",
                "kind": "place",
                "canonical_name": anchor.title(),
                "aliases": [],
            }
        )
    entities.extend(
        {
            "id": f"place:{place_anchor}",
            "world_id": "world:yun",
            "kind": "place",
            "canonical_name": place_anchor.title(),
            "aliases": [],
        }
        for place_anchor in extra_place_anchors
    )
    related_entity_ids = [resolved_activity_id]
    if include_place_link:
        related_entity_ids.append(f"place:{anchor}")
    return _payload(
        new_entities=entities,
        new_events=[
            {
                "id": f"event:{anchor}-trip-planning",
                "world_id": "world:yun",
                "event_type": "travel_planning",
                "summary": f"Planning a trip anchored at {anchor.title()}.",
                "occurred_at": "2026-08-08T10:00:00+08:00",
                "participants": [{"entity_id": "person:user", "role": "traveler"}],
                "related_entity_ids": related_entity_ids,
                "relationship_ids": [],
                "facets": [
                    {
                        "key": "destination",
                        "value": anchor.title(),
                        "segment_id": None,
                        "about_entity_id": None,
                    }
                ],
                "evidence_ids": ["turn:user"],
            }
        ],
    )


def _trip_turns(anchor: str = "kyoto") -> list[ConversationTurn]:
    return [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            f"I am planning a trip to {anchor.title()}.",
            "2026-08-08T10:00:00+08:00",
        )
    ]


def _source(
    segment_id: str,
    relation: str = "support",
    proposition_origin: str = "user_stated",
    response_act: str = "none",
) -> dict[str, str]:
    segment_ids = {
        "turn:user": "seg-0000", "turn:one": "seg-0000", "turn:two": "seg-0001", "turn:three": "seg-0002",
    }
    if segment_id.startswith("turn:") and segment_id[5:].isdigit():
        resolved_segment_id = f"seg-{int(segment_id[5:]):04d}"
    else:
        resolved_segment_id = segment_ids.get(segment_id, segment_id)
    return {
        "segment_id": resolved_segment_id,
        "relation": relation,
        "proposition_origin": proposition_origin,
        "response_act": response_act,
    }


def _cognition_payload(
    sources: list[dict[str, str]], *, model_inferred: bool = False, content: str | None = None,
    perspective: object | None = None,
) -> str:
    return _payload(
        new_entities=[],
        new_cognitions=[
            {
                "id": "cog:user-preference",
                "world_id": "world:yun",
                "target": {"kind": "entity", "id": "person:user"},
                "content": content,
                "content_type": "preference",
                "model_inferred": model_inferred,
                "perspective": (
                    {"kind": "entity", "holder_entity_ids": ["person:user"]}
                    if perspective is None
                    else perspective
                ),
                "sources": sources,
                "scope": None,
                "valid_at": None,
                "invalid_at": None,
            }
        ],
    )


def _relationship_projection_payload(
    *,
    relationship_content: str | None = None,
    relationship_perspective: object | None = None,
) -> str:
    return _payload(
        new_entities=[
            {
                "id": "person:friend-x",
                "world_id": "world:yun",
                "kind": "person",
                "canonical_name": "Friend_X",
                "aliases": [],
            }
        ],
        new_relationships=[
            {
                "id": "relationship:user-friend-x",
                "world_id": "world:yun",
                "source_entity_id": "person:user",
                "target_entity_id": "person:friend-x",
                "relation_type": "friend",
                "bidirectional": True,
            }
        ],
        new_cognitions=[
            {
                "id": "cog:user-travel",
                "world_id": "world:yun",
                "target": {"kind": "entity", "id": "person:user"},
                "content": None,
                "content_type": "preference",
                "model_inferred": False,
                "perspective": {"kind": "entity", "holder_entity_ids": ["person:friend-x"]},
                "sources": [_source("seg-0000")],
                "scope": "travel",
                "valid_at": None,
                "invalid_at": None,
            },
            {
                "id": "cog:friend-travel",
                "world_id": "world:yun",
                "target": {"kind": "entity", "id": "person:friend-x"},
                "content": None,
                "content_type": "preference",
                "model_inferred": False,
                "perspective": {"kind": "entity", "holder_entity_ids": ["person:friend-x"]},
                "sources": [_source("seg-0001")],
                "scope": "travel",
                "valid_at": None,
                "invalid_at": None,
            },
            {
                "id": "cog:relationship-travel",
                "world_id": "world:yun",
                "target": {"kind": "relationship", "id": "relationship:user-friend-x"},
                "content": relationship_content,
                "content_type": "hypothesis",
                "model_inferred": True,
                "perspective": relationship_perspective,
                "sources": [_source("seg-0002")],
                "scope": "travel",
                "valid_at": None,
                "invalid_at": None,
            },
        ],
    )


def _relationship_projection_turns() -> list[ConversationTurn]:
    return [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "I prefer flexible, unplanned exploration;Friend_X prefers planning routes in advance and following a guide.We argued over this contrast.",
            "2026-08-08T10:00:00+08:00",
        )
    ]


def _conflict_relationship_projection_payload(
    *,
    relationship_model_inferred: bool = True,
    relationship_content_type: str = "hypothesis",
    relationship_scope: str | None = "travel",
    relationship_target_id: str = "relationship:user-friend-x",
    event_type: str = "interpersonal_conflict",
) -> str:
    """Return the complete, narrow conflict shape that activates projection validation."""
    payload = json.loads(_relationship_projection_payload())
    payload["new_events"] = [
        {
            "id": "event:travel-conflict",
            "world_id": "world:yun",
            "event_type": event_type,
            "summary": "A conflict happened.",
            "occurred_at": "2026-08-08T10:00:00+08:00",
            "participants": [
                {"entity_id": "person:user", "role": "participant"},
                {"entity_id": "person:friend-x", "role": "participant"},
            ],
            "related_entity_ids": [],
            "relationship_ids": ["relationship:user-friend-x"],
            "facets": [
                {"key": "cause", "value": "A difference.", "segment_id": None, "about_entity_id": None},
                {"key": "position", "value": None, "segment_id": "seg-0000", "about_entity_id": "person:user"},
                {"key": "position", "value": None, "segment_id": "seg-0001", "about_entity_id": "person:friend-x"},
            ],
            "evidence_ids": ["turn:user"],
        }
    ]
    relationship = payload["new_cognitions"][2]
    relationship["model_inferred"] = relationship_model_inferred
    relationship["content_type"] = relationship_content_type
    relationship["scope"] = relationship_scope
    relationship["target"] = {"kind": "relationship", "id": relationship_target_id}
    if not relationship_model_inferred:
        relationship["content"] = None
        relationship["perspective"] = {"kind": "entity", "holder_entity_ids": ["person:user"]}
        relationship["sources"] = [_source("seg-0002")]
    return json.dumps(payload, ensure_ascii=False)


def _eligible_turns(count: int) -> list[ConversationTurn]:
    return [
        ConversationTurn(f"turn:{index}", "conversation:one", "user", "Friend_X is my friend.", "2026-08-08T10:00:00+08:00")
        for index in range(count)
    ]


def _conflict_payload(position: str, *, segment_id: str = "seg-0000") -> str:
    return _payload(
        new_relationships=[
            {
                "id": "relationship:user-friend-x",
                "world_id": "world:yun",
                "source_entity_id": "person:user",
                "target_entity_id": "person:friend-x",
                "relation_type": "friend",
                "bidirectional": True,
            }
        ],
        new_events=[
            {
                "id": "event:travel-conflict",
                "world_id": "world:yun",
                "event_type": "interpersonal_conflict",
                "summary": "A conflict happened.",
                "occurred_at": "2026-08-08T10:00:00+08:00",
                "participants": [{"entity_id": "person:user", "role": "participant"}],
                "related_entity_ids": [],
                "relationship_ids": ["relationship:user-friend-x"],
                "facets": [
                    {"key": "cause", "value": "A difference.", "segment_id": None, "about_entity_id": None},
                    {"key": "position", "value": None, "segment_id": segment_id, "about_entity_id": "person:user"},
                ],
                "evidence_ids": ["turn:user"],
            }
        ],
    )


def _two_party_conflict_payload(
    *,
    user_position: str | None,
    peer_position: str | None,
    semantic_uncertainties: list[dict[str, object]] | None = None,
) -> str:
    payload = json.loads(_conflict_payload(user_position or "I prefer an open schedule."))
    event = payload["new_events"][0]
    event["participants"] = [
        {"entity_id": "person:user", "role": "participant"},
        {"entity_id": "person:friend-x", "role": "participant"},
    ]
    facets: list[dict[str, object]] = [
        {"key": "cause", "value": "Their schedules conflicted.", "segment_id": None, "about_entity_id": None},
    ]
    if user_position is not None:
        facets.append({"key": "position", "value": None, "segment_id": "seg-0000", "about_entity_id": "person:user"})
    if peer_position is not None:
        facets.append({"key": "position", "value": None, "segment_id": "seg-0001", "about_entity_id": "person:friend-x"})
    event["facets"] = facets
    payload["semantic_uncertainties"] = semantic_uncertainties or []
    return json.dumps(payload, ensure_ascii=False)


def _position_turns(user_content: str, assistant_content: str = "context") -> list[ConversationTurn]:
    return [
        ConversationTurn("turn:user", "conversation:one", "user", user_content, "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:assistant", "conversation:one", "assistant", assistant_content, "2026-08-08T10:00:01+08:00"),
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("interpersonal conflict", True),
        ("InterPersonal---CONFLICT", True),
        ("ＩＮＴＥＲＰＥＲＳＯＮＡＬ＿ＣＯＮＦＬＩＣＴ", True),
        ("人际冲突", True),
        ("争执", True),
        ("吵架", True),
        (None, False),
        ("ordinary_event", False),
    ],
)
def test_interpersonal_conflict_semantics_match_gate14_aliases(value: object, expected: bool) -> None:
    assert is_interpersonal_conflict_type(value) is expected


@pytest.mark.parametrize("event_type", ["interpersonal conflict", "人际冲突", "争执", "吵架"])
def test_conflict_type_aliases_keep_position_materialization_strict(event_type: str) -> None:
    payload = json.loads(_conflict_payload("ignored by trusted materialization"))
    payload["new_events"][0]["event_type"] = event_type
    evidence = "I prefer driving without a fixed plan."

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
        _base(), _position_turns(evidence), {"turn:user"},
    )

    assert delta.new_events[0].facets[1].value == evidence


def test_conflict_position_is_materialized_from_its_complete_caller_owned_segment() -> None:
    payload = json.loads(_conflict_payload("model text must not survive"))
    payload["new_events"][0]["facets"] = [
        {"key": "cause", "value": "A difference.", "segment_id": None, "about_entity_id": None},
        {"key": "position", "value": None, "segment_id": "seg-0000", "about_entity_id": "person:user"},
    ]
    llm = ScriptedLLM([json.dumps(payload)])

    delta = WorldExtractor(llm).extract(
        _base(), _position_turns("I prefer driving without a fixed plan."), {"turn:user"},
    )

    facet = delta.new_events[0].facets[1]
    assert facet.value == "I prefer driving without a fixed plan."
    assert asdict(facet) == {
        "key": "position",
        "value": "I prefer driving without a fixed plan.",
        "about_entity_id": "person:user",
    }
    assert "segment_id" not in json.dumps(asdict(delta), ensure_ascii=False)


def test_response_schema_v8_preserves_the_four_key_facet_wire_contract() -> None:
    response_format = world_delta_response_format()
    facet = response_format["json_schema"]["schema"]["properties"]["new_events"]["items"]["properties"]["facets"]["items"]

    assert response_format["json_schema"]["name"] == "memoweft_world_delta_v9"
    assert set(facet["properties"]) == {"key", "value", "segment_id", "about_entity_id"}
    assert set(facet["required"]) == {"key", "value", "segment_id", "about_entity_id"}
    assert "non-position facet" in facet["properties"]["value"]["description"]
    assert "interpersonal_conflict position" in facet["properties"]["segment_id"]["description"]
    assert "participant Entity ID" in facet["properties"]["about_entity_id"]["description"]


def test_catalog_is_ordered_complete_and_crlf_aware_without_splitting_commas() -> None:
    turns = [
        ConversationTurn("turn:first", "conversation:one", "user", "Alpha\r\nBeta,gamma。Tail", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:second", "conversation:one", "user", "Alpha\r\n", "2026-08-08T10:00:01+08:00"),
    ]

    catalog = _segment_catalog(turns)

    assert [(item.segment_id, item.evidence_id, item.text, item.start_codepoint, item.end_codepoint) for item in catalog] == [
        ("seg-0000", "turn:first", "Alpha\r\n", 0, 7),
        ("seg-0001", "turn:first", "Beta,gamma。", 7, 18),
        ("seg-0002", "turn:first", "Tail", 18, 22),
        ("seg-0003", "turn:second", "Alpha\r\n", 0, 7),
    ]


def test_catalog_excludes_nonlexical_pseudo_segments_but_keeps_substantive_text() -> None:
    turns = [
        ConversationTurn(
            "turn:user", "conversation:one", "user", " \n.\r\n!;😀\n$^&\nA1.", "2026-08-08T10:00:00+08:00",
        ),
    ]

    catalog = _segment_catalog(turns)

    assert [(item.segment_id, item.text) for item in catalog] == [("seg-0000", "A1.")]


@pytest.mark.parametrize("content", ["©™+→", "™"])
def test_catalog_excludes_unicode_compatibility_symbols_from_inferred_grounding(content: str) -> None:
    turns = [ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00")]

    assert _segment_catalog(turns) == ()
    invalid = _cognition_payload(
        [_source("seg-0000")], model_inferred=True, content="An arbitrary inference.",
    )

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("SEGMENT_ID_UNKNOWN@$.new_cognitions[0].sources[0].segment_id",) * 2


def test_catalog_keeps_fullwidth_letters_and_numbers_for_inferred_grounding() -> None:
    content = "Ａ１。"
    turns = [ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00")]
    inferred = _cognition_payload(
        [_source("seg-0000")], model_inferred=True, content="A grounded inference.",
    )

    delta = WorldExtractor(ScriptedLLM([inferred])).extract(_base(), turns, {"turn:user"})

    assert delta.formation_traces[0].sources[0].local_origin_decision == "inference_grounding"


def test_catalog_payload_exposes_only_allowlisted_user_segments_in_order() -> None:
    turns = [
        ConversationTurn("turn:one", "conversation:one", "user", "One.Two", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "context", "2026-08-08T10:00:01+08:00"),
        ConversationTurn("turn:two", "conversation:one", "user", "Three。", "2026-08-08T10:00:02+08:00"),
    ]
    llm = ScriptedLLM([_payload()])

    WorldExtractor(llm).extract(_base(), turns, {"turn:one", "turn:two"})

    payload = json.loads(llm.messages[0][1].content)
    assert payload["eligible_evidence_segments"] == [
        {"segment_id": "seg-0000", "evidence_id": "turn:one", "text": "One."},
        {"segment_id": "seg-0001", "evidence_id": "turn:one", "text": "Two"},
        {"segment_id": "seg-0002", "evidence_id": "turn:two", "text": "Three。"},
    ]


def _event_payload_with(field: str, value: object) -> str:
    payload = json.loads(_conflict_payload("Friend_X is my friend."))
    payload["new_events"][0][field] = value
    return json.dumps(payload, ensure_ascii=False)


def _cognition_payload_with(field: str, value: object) -> str:
    payload = json.loads(_cognition_payload([_source("turn:user")]))
    payload["new_cognitions"][0][field] = value
    return json.dumps(payload, ensure_ascii=False)


def test_first_attempt_decodes_and_validates_a_reviewable_delta_without_mutating_base() -> None:
    base = _base()
    before = copy.deepcopy(base)
    llm = ScriptedLLM([_payload()])

    delta = WorldExtractor(llm).extract(base, _turns(), {"turn:user"})

    assert llm.calls == 1
    assert delta.new_entities[0].id == "person:friend-x"
    assert delta.source_evidence_ids == ("turn:user",)
    assert base == before


def test_trip_activity_with_new_matching_place_and_event_links_passes_without_repair() -> None:
    llm = ScriptedLLM([_trip_payload()])

    delta = WorldExtractor(llm).extract(_base(), _trip_turns(), {"turn:user"})

    assert llm.call_count == 1
    assert {entity.id for entity in delta.new_entities} == {
        "activity:kyoto-trip",
        "place:kyoto",
    }
    assert set(delta.new_events[0].related_entity_ids) == {
        "activity:kyoto-trip",
        "place:kyoto",
    }


def test_trip_activity_accepts_exact_matching_place_already_in_base() -> None:
    base = _base()
    base.add_entity(Entity("place:kyoto", "world:yun", "place", "Kyoto"))
    llm = ScriptedLLM([_trip_payload(include_place=False)])

    delta = WorldExtractor(llm).extract(base, _trip_turns(), {"turn:user"})

    assert llm.call_count == 1
    assert [entity.id for entity in delta.new_entities] == ["activity:kyoto-trip"]
    assert "place:kyoto" in delta.new_events[0].related_entity_ids


def test_trip_activity_rejects_exact_base_place_id_with_the_wrong_kind() -> None:
    base = _base()
    base.add_entity(Entity("place:kyoto", "world:yun", "activity", "Kyoto"))
    invalid = _trip_payload(include_place=False)
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(base, _trip_turns(), {"turn:user"})

    assert llm.call_count == 2
    assert raised.value.codes == (
        "TRIP_ACTIVITY_PLACE_ENTITY_REQUIRED@$.new_entities[0]",
    ) * 2


def test_trip_activity_selects_place_by_exact_id_when_multiple_places_exist() -> None:
    llm = ScriptedLLM([_trip_payload(extra_place_anchors=("osaka",))])

    delta = WorldExtractor(llm).extract(_base(), _trip_turns(), {"turn:user"})

    assert llm.call_count == 1
    assert {entity.id for entity in delta.new_entities} == {
        "activity:kyoto-trip",
        "place:kyoto",
        "place:osaka",
    }
    assert "place:kyoto" in delta.new_events[0].related_entity_ids
    assert "place:osaka" not in delta.new_events[0].related_entity_ids


def test_non_trip_activity_does_not_require_a_matching_place() -> None:
    llm = ScriptedLLM(
        [
            _trip_payload(
                activity_id="activity:kyoto-tour",
                include_place=False,
                include_place_link=False,
            )
        ]
    )

    delta = WorldExtractor(llm).extract(_base(), _trip_turns(), {"turn:user"})

    assert llm.call_count == 1
    assert [entity.id for entity in delta.new_entities] == ["activity:kyoto-tour"]
    assert delta.new_events[0].related_entity_ids == ("activity:kyoto-tour",)


def test_trip_activity_without_exact_matching_place_fails_with_safe_code() -> None:
    invalid = _trip_payload(
        include_place=False,
        include_place_link=False,
        extra_place_anchors=("osaka",),
    )
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _trip_turns(), {"turn:user"})

    assert llm.call_count == 2
    assert raised.value.codes == (
        "TRIP_ACTIVITY_PLACE_ENTITY_REQUIRED@$.new_entities[0]",
    ) * 2


def test_event_linking_trip_activity_without_matching_place_link_fails_safely() -> None:
    invalid = _trip_payload(include_place_link=False)
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _trip_turns(), {"turn:user"})

    assert llm.call_count == 2
    assert raised.value.codes == (
        "TRIP_ACTIVITY_EVENT_PLACE_LINK_REQUIRED@$.new_events[0].related_entity_ids",
    ) * 2


def test_missing_trip_place_uses_one_targeted_repair_then_accepts_model_correction() -> None:
    invalid = _trip_payload(include_place=False, include_place_link=False)
    repaired = _trip_payload()
    llm = ScriptedLLM([invalid, repaired])

    delta = WorldExtractor(llm).extract(_base(), _trip_turns(), {"turn:user"})

    assert llm.call_count == 2
    assert {entity.id for entity in delta.new_entities} == {
        "activity:kyoto-trip",
        "place:kyoto",
    }
    repair = llm.messages[1][-1].content
    assert "TRIP_ACTIVITY_PLACE_ENTITY_REQUIRED@$.new_entities[0]" in repair
    assert "For each Evidence-supported Entity whose ID uses activity:<anchor>-trip" in repair
    assert "Every event whose related_entity_ids includes the trip activity" in repair


def test_decoder_discards_only_an_exact_base_entity_echo_before_create_only_validation() -> None:
    """A model may echo a supplied owner record while proposing a real new Entity.

    The echoed record is not a change.  It is safe to drop only because every
    typed field exactly equals the caller-owned base Entity; a same-ID record
    with any changed field must remain subject to normal create-only rejection.
    """
    payload = _payload(
        new_entities=[
            {
                "id": "animal:pet-two-five",
                "world_id": "world:yun",
                "kind": "animal",
                "canonical_name": "Pet_Two_Five",
                "aliases": [],
            },
            {
                "id": "person:user",
                "world_id": "world:yun",
                "kind": "person",
                "canonical_name": "User",
                "aliases": [],
            },
        ],
    )

    delta = WorldExtractor(ScriptedLLM([payload])).extract(_base(), _turns(), {"turn:user"})

    assert [entity.id for entity in delta.new_entities] == ["animal:pet-two-five"]


def test_decoder_keeps_a_same_id_base_entity_with_changed_fields_for_create_only_rejection() -> None:
    payload = _payload(
        new_entities=[
            {
                "id": "person:user",
                "world_id": "world:yun",
                "kind": "person",
                "canonical_name": "Changed User",
                "aliases": [],
            }
        ],
    )
    llm = ScriptedLLM([payload, payload])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert raised.value.codes == ("DELTA_DOMAIN(entity[0].id.conflicts_with_base)@$",) * 2


def test_decoder_enforces_top_level_response_array_maximum_before_domain_validation() -> None:
    oversized = json.loads(_payload())
    entity = oversized["new_entities"][0]
    oversized["new_entities"] = [{**entity, "id": f"person:friend-{index}"} for index in range(5)]
    llm = ScriptedLLM([json.dumps(oversized), _payload()])

    delta = WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert llm.calls == 2
    assert delta.new_entities[0].id == "person:friend-x"
    assert "ARRAY_MAX_ITEMS@$.new_entities" in llm.messages[1][-1].content


@pytest.mark.parametrize(
    ("invalid", "expected_code"),
    [
        (
            lambda: _payload(new_entities=[{
                "id": "person:friend-x", "world_id": "world:yun", "kind": "person",
                "canonical_name": "Friend_X", "aliases": ["one", "two", "three", "four", "five"],
            }]),
            "ARRAY_MAX_ITEMS@$.new_entities[0].aliases",
        ),
        (
            lambda: _event_payload_with("related_entity_ids", ["person:user", "person:user"]),
            "ARRAY_UNIQUE_ITEMS@$.new_events[0].related_entity_ids",
        ),
        (
            lambda: _event_payload_with("evidence_ids", []),
            "ARRAY_MIN_ITEMS@$.new_events[0].evidence_ids",
        ),
        (
            lambda: _cognition_payload_with("perspective", {"kind": "entity", "holder_entity_ids": ["person:user", "person:user"]}),
            "ARRAY_UNIQUE_ITEMS@$.new_cognitions[0].perspective.holder_entity_ids",
        ),
        (
            lambda: _cognition_payload([]),
            "ARRAY_MIN_ITEMS@$.new_cognitions[0].sources",
        ),
    ],
)
def test_decoder_enforces_representative_nested_response_array_boundaries(
    invalid: Callable[[], str], expected_code: str,
) -> None:
    raw = invalid()
    llm = ScriptedLLM([raw, raw])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert raised.value.codes == (expected_code,) * 2


def test_cognition_confidence_and_cred_status_are_derived_from_one_user_stated_support() -> None:
    llm = ScriptedLLM([_cognition_payload([_source("turn:user")])])

    delta = WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert (delta.new_cognitions[0].formed_by, delta.new_cognitions[0].confidence, delta.new_cognitions[0].cred_status) == (
        "stated",
        600,
        "limited",
    )


def test_cognition_model_inferred_forces_inferred_even_with_user_stated_support() -> None:
    llm = ScriptedLLM([_cognition_payload([_source("turn:user")], model_inferred=True, content="A supported inference.")])

    delta = WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert (delta.new_cognitions[0].formed_by, delta.new_cognitions[0].confidence, delta.new_cognitions[0].cred_status) == (
        "inferred",
        200,
        "candidate",
    )


def test_carrier_confirmation_is_locally_confirmed_despite_model_stated_proposal() -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "Do you like coffee?", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:user", "conversation:one", "user", "对", "2026-08-08T10:00:01+08:00"),
    ]
    llm = ScriptedLLM([_cognition_payload([
        _source("turn:user", proposition_origin="user_stated", response_act="none"),
    ], content="Coffee is preferred.")])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    cognition = delta.new_cognitions[0]
    trace_source = delta.formation_traces[0].sources[0]
    assert (cognition.formed_by, cognition.confidence, cognition.cred_status) == ("confirmed", 280, "candidate")
    assert (trace_source.proposition_origin_proposal, trace_source.response_act_proposal) == ("user_stated", "none")
    assert trace_source.local_origin_decision == "assistant_confirmation"
    assert trace_source.preceding_assistant_turn_id == "turn:assistant"
    artifact = asdict(delta)
    assert "claim_quote" not in json.dumps(artifact, ensure_ascii=False)
    assert "Do you like coffee?" not in json.dumps(artifact, ensure_ascii=False)


def test_owner_trait_attribution_becomes_feedback_event_not_an_unconditional_trait() -> None:
    content = "我有个朋友说我很温柔"
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-10T15:07:13+08:00",
        ),
    ]
    raw = json.loads(
        _cognition_payload(
            [_source("turn:user")],
            model_inferred=True,
            content="我很温柔",
        )
    )
    raw["new_cognitions"][0]["content_type"] = "trait"

    llm = ScriptedLLM([json.dumps(raw, ensure_ascii=False)])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 0
    assert not delta.new_cognitions
    assert not delta.formation_traces
    assert len(delta.new_events) == 1
    feedback = delta.new_events[0]
    assert feedback.event_type == "attributed_feedback"
    assert feedback.summary == content
    assert feedback.summary != "我很温柔"
    assert [(participant.entity_id, participant.role) for participant in feedback.participants] == [
        ("person:user", "recipient"),
    ]
    assert feedback.evidence_ids == ("turn:user",)


def test_qualified_affirmation_materializes_the_preceding_self_trait_as_confirmation() -> None:
    assistant_content = (
        "那看来你朋友很懂你呀。温柔的人总是让人相处起来很舒服的。"
        "你觉得自己是这样的人吗？"
    )
    turns = [
        ConversationTurn(
            "turn:assistant",
            "conversation:one",
            "assistant",
            assistant_content,
            "2026-08-10T15:07:18+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "算是吧",
            "2026-08-10T15:07:36+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("turn:user")]))
    raw["new_cognitions"][0]["content_type"] = "trait"

    delta = WorldExtractor(
        ScriptedLLM([json.dumps(raw, ensure_ascii=False)])
    ).extract(_base(), turns, {"turn:user"})

    cognition = delta.new_cognitions[0]
    trace = delta.formation_traces[0]
    assert cognition.content == "我是温柔的人"
    assert cognition.content != "算是吧"
    assert cognition.content_type == "trait"
    assert cognition.formed_by == trace.derived_formed_by == "confirmed"
    assert cognition.sources == (cognition.sources[0],)
    assert cognition.sources[0].evidence_id == "turn:user"
    assert trace.sources[0].local_origin_decision == "assistant_confirmation"
    assert trace.sources[0].preceding_assistant_turn_id == "turn:assistant"
    assert "turn:assistant" not in {
        source.evidence_id for source in cognition.sources
    }


def test_pure_owner_evaluation_question_is_trusted_no_candidate_even_when_model_proposes_one() -> None:
    content = "你觉得我是温柔的人吗"
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-10T15:15:09+08:00",
        ),
    ]
    raw = json.loads(
        _cognition_payload(
            [_source("turn:user")],
            model_inferred=True,
            content=content,
        )
    )
    raw["new_cognitions"][0]["content_type"] = "trait"
    llm = ScriptedLLM([json.dumps(raw, ensure_ascii=False)])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 1
    assert not delta.new_entities
    assert not delta.new_relationships
    assert not delta.new_events
    assert not delta.new_cognitions
    assert not delta.formation_traces


@pytest.mark.parametrize("carrier", ["对。", "Yes!", "“对。”"])
def test_terminal_punctuation_does_not_promote_confirmation_carriers(carrier: str) -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "Do you like coffee?", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:user", "conversation:one", "user", carrier, "2026-08-08T10:00:01+08:00"),
    ]
    llm = ScriptedLLM([_cognition_payload([
        _source("turn:user", proposition_origin="user_stated", response_act="none"),
    ], content="Coffee is preferred.")])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert (delta.new_cognitions[0].formed_by, delta.new_cognitions[0].confidence) == ("confirmed", 280)
    assert delta.formation_traces[0].sources[0].local_origin_decision == "assistant_confirmation"


@pytest.mark.parametrize("carrier", ["对。", "不。", "“对。”"])
def test_carrier_without_preceding_assistant_cannot_become_a_direct_claim(carrier: str) -> None:
    turns = [ConversationTurn("turn:user", "conversation:one", "user", carrier, "2026-08-08T10:00:00+08:00")]
    invalid = _cognition_payload([
        _source("turn:user", proposition_origin="user_stated", response_act="none"),
    ], content="The user is a billionaire.")
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("FORMATION_UNVERIFIED@$.new_cognitions[0].sources[0]",) * 2


def test_direct_exact_user_claim_is_stated_with_a_bound_span() -> None:
    content = "I prefer decaf coffee."
    turns = [ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00")]
    llm = ScriptedLLM([_cognition_payload([
        _source("turn:user"),
    ])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    trace = delta.formation_traces[0]
    assert delta.new_cognitions[0].formed_by == "stated"
    assert trace.sources[0].local_origin_decision == "exact_user_claim"
    assert (trace.sources[0].claim_span.start_codepoint, trace.sources[0].claim_span.end_codepoint) == (0, len(content))
    assert delta.new_cognitions[0].content == content
    assert "seg-0000" not in json.dumps(asdict(delta), ensure_ascii=False)


def test_same_direct_segment_text_can_repeat_without_raising_effective_support() -> None:
    turns = [
        ConversationTurn("turn:first", "conversation:one", "user", "Same claim.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:second", "conversation:one", "user", "Same claim.", "2026-08-08T10:00:01+08:00"),
    ]
    delta = WorldExtractor(ScriptedLLM([_cognition_payload([_source("seg-0000"), _source("seg-0001")])])).extract(
        _base(), turns, {"turn:first", "turn:second"},
    )

    trace = delta.formation_traces[0]
    assert (delta.new_cognitions[0].content, trace.raw_support_count, trace.effective_support_count) == (
        "Same claim.", 2, 1,
    )


def test_two_direct_catalog_segments_from_one_evidence_fail_closed() -> None:
    turns = [ConversationTurn("turn:user", "conversation:one", "user", "First.Second.", "2026-08-08T10:00:00+08:00")]
    invalid = _cognition_payload([_source("seg-0000"), _source("seg-0001")])
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("FORMATION_EVIDENCE_SEGMENT_DUPLICATE@$.new_cognitions[0].sources",) * 2


def test_inferred_cognition_rejects_two_segments_from_one_evidence_before_delta() -> None:
    turns = [
        ConversationTurn("turn:user", "conversation:one", "user", "I prefer tea.I prefer coffee.", "2026-08-08T10:00:00+08:00"),
    ]
    invalid = _cognition_payload(
        [_source("seg-0000"), _source("seg-0001")], model_inferred=True, content="An arbitrary inference.",
    )

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("FORMATION_EVIDENCE_SEGMENT_DUPLICATE@$.new_cognitions[0].sources",) * 2


def test_cognition_rejects_same_evidence_across_support_and_contradict_before_delta() -> None:
    turns = [ConversationTurn("turn:user", "conversation:one", "user", "I prefer tea.", "2026-08-08T10:00:00+08:00")]
    invalid = _cognition_payload(
        [_source("seg-0000"), _source("seg-0000", relation="contradict")],
        model_inferred=True,
        content="An arbitrary inference.",
    )

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("FORMATION_EVIDENCE_SEGMENT_DUPLICATE@$.new_cognitions[0].sources",) * 2


def test_repair_can_reduce_same_evidence_segments_to_one_resolved_source() -> None:
    turns = [
        ConversationTurn("turn:user", "conversation:one", "user", "I prefer tea.I prefer coffee.", "2026-08-08T10:00:00+08:00"),
    ]
    duplicate = _cognition_payload(
        [_source("seg-0000"), _source("seg-0001")], model_inferred=True, content="An arbitrary inference.",
    )
    repaired = _cognition_payload([_source("seg-0000")], model_inferred=True, content="An arbitrary inference.")
    llm = ScriptedLLM([duplicate, repaired])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert (llm.call_count, delta.new_cognitions[0].formed_by) == (2, "inferred")
    assert "FORMATION_EVIDENCE_SEGMENT_DUPLICATE@$.new_cognitions[0].sources" in llm.messages[1][-1].content


def test_direct_claim_with_model_content_is_rejected_instead_of_overriding_catalog() -> None:
    content = "我不喜欢咖啡。"
    turns = [ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00")]
    invalid = _cognition_payload([
        _source("turn:user"),
    ], content="喜欢咖啡")
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("DIRECT_CONTENT_NOT_NULL@$.new_cognitions[0].content",) * 2


def test_initial_and_repair_prompts_require_catalog_ids_and_direct_null_content() -> None:
    content = "Coffee is good."
    turns = [ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00")]
    unknown = _cognition_payload([_source("seg-9999")])
    complete = _cognition_payload([_source("seg-0000")])
    llm = ScriptedLLM([unknown, complete])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert delta.new_cognitions[0].formed_by == "stated"
    initial = llm.messages[0][0].content
    repair = llm.messages[1][-1].content
    required_instruction = "For a non-inferred direct user claim, cognition.content must be null"
    assert required_instruction in initial
    assert "ELIGIBLE_EVIDENCE_SEGMENTS" in initial
    assert "Select source segment_id only from the original eligible_evidence_segments catalog" in repair
    assert "An inferred cognition needs at least" in initial
    lexicality_rule = "original Unicode code-point category is Letter or Number"
    assert lexicality_rule in initial
    assert "NFKC only for acknowledgement/negation carrier matching" in initial
    evidence_uniqueness_rule = "only the most direct one segment from any one Evidence ID"
    assert evidence_uniqueness_rule in initial
    assert "multiple inferred sources must resolve to different Evidence IDs" in initial
    assert "Keep at most one most-direct segment from each Evidence ID" in repair
    assert "OWNER-TO-THIRD-PARTY REPAIR" not in repair
    assert "TRIP ACTIVITY NAME CHECK" not in repair
    assert len(repair) < 1_500
    assert "claim_quote" not in initial
    assert "claim_quote" not in repair
    assert "SEGMENT_ID_UNKNOWN@$.new_cognitions[0].sources[0].segment_id" in repair


def test_initial_and_repair_prompts_share_the_evidence_bounded_lifecycle_claim_rule() -> None:
    llm = ScriptedLLM(["{not json", _payload()])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    lifecycle_rule = (
        "LIFECYCLE CLAIM RULE: valid_at and invalid_at are lifecycle claims. "
        "Set each to null unless eligible Evidence explicitly states the relevant start or end; "
        "never invent either."
    )
    assert lifecycle_rule in llm.messages[0][0].content
    assert lifecycle_rule in llm.messages[1][-1].content


def test_initial_prompt_has_full_contract_while_repair_stays_targeted() -> None:
    llm = ScriptedLLM(["{not json", _payload()])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    initial = llm.messages[0][0].content
    repair = llm.messages[1][-1].content
    trip_rule = (
        "TRIP ACTIVITY NAME CHECK: a place-anchored trip activity canonical_name must preserve both "
        "the named place anchor and a travel/activity type; copy the shortest eligible Evidence phrase "
        "containing both, never reduce the activity to the place name alone."
    )
    trip_entity_separation_rule = (
        "TRIP ENTITY SEPARATION CHECK: when eligible Evidence names both a destination place and a "
        "persistently referable trip activity, create two distinct Entity objects: one place and one activity; "
        "never merge the trip into the place or the place into the trip. Every related lived event must include "
        "both Entity IDs in related_entity_ids."
    )
    assert "FINAL SELF-CHECK:" in initial
    assert trip_rule in initial
    assert trip_entity_separation_rule in initial
    assert "INFERRED RELATIONSHIP CHECK:" in initial
    assert "set hypothesis content and perspective to null" in initial
    assert "one exact stated direct cognition for each Relationship endpoint" in initial
    assert "relationship_side_segments" not in initial
    assert "CONDITIONAL CONFLICT RELATIONSHIP TRANSACTION:" in initial
    assert "Event positions alone never justify durable Cognitions" in initial
    assert "both complete direct-claim segments belong to the same Evidence ID" in initial
    assert "FINAL SELF-CHECK:" not in repair
    assert trip_rule not in repair
    assert trip_entity_separation_rule not in repair
    assert "TARGETED REPAIR:" in repair
    assert len(repair) < 1_500
    assert 'cause/non-position={\"key\":\"cause\",\"value\":\"<non-empty-supported-cause>\"' in initial
    assert 'interpersonal-conflict position={\"key\":\"position\",\"value\":null' in initial


def test_generic_prompt_and_repair_protocol_do_not_embed_the_frozen_fixture_answer() -> None:
    llm = ScriptedLLM(["{not json", _payload()])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    protocol = llm.messages[0][0].content + "\n" + llm.messages[1][-1].content
    for forbidden in (
        "golden-001-nanjing",
        "Friend_X",
        "南京",
        "turn-001",
        "turn-003",
        "person:friend-x",
        "relationship:user-friend-x",
    ):
        assert forbidden not in protocol


def test_every_targeted_repair_family_is_generic_and_free_of_frozen_fixture_literals() -> None:
    safe_errors = (
        "FACET_VALUE_REQUIRED@$.new_events[0].facets[0].value",
        "POSITION_VALUE_MUST_BE_NULL@$.new_events[0].facets[1].value",
        "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids",
        "RELATIONSHIP_DIRECT_CANDIDATE_MISSING@$.new_cognitions",
        "RELATIONSHIP_BINDING_EVIDENCE_UNSUPPORTED@$.new_cognitions[2].target.id",
        "RELATIONSHIP_HYPOTHESIS_REQUIRED@$.new_cognitions[2].content_type",
        "INFERRED_CONTENT_REQUIRED@$.new_cognitions[1].content",
        "DIRECT_CONTENT_NOT_NULL@$.new_cognitions[0].content",
        "SEGMENT_ID_UNKNOWN@$.new_cognitions[0].sources[0].segment_id",
        "THIRD_PARTY_REFERENCE_UNRESOLVED@$.unresolved_references",
        "TRIP_ACTIVITY_PLACE_ENTITY_REQUIRED@$.new_entities[0]",
        "TRIP_ACTIVITY_EVENT_PLACE_LINK_REQUIRED@$.new_events[0].related_entity_ids",
        "JSON_SYNTAX@$",
    )
    forbidden_literals = (
        "golden-001-nanjing",
        "Friend_X",
        "南京",
        "turn-001",
        "turn-003",
        "person:friend-x",
        "relationship:user-friend-x",
    )

    for safe_error in safe_errors:
        instruction = _targeted_repair_instruction(safe_error)
        assert instruction.startswith("TARGETED REPAIR:")
        assert len(instruction) < 1_600
        for forbidden in forbidden_literals:
            assert forbidden not in instruction


def test_complete_negative_user_sentence_is_a_stated_direct_claim() -> None:
    content = "我不喜欢咖啡。"
    turns = [ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00")]
    llm = ScriptedLLM([_cognition_payload([
        _source("turn:user"),
    ])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert delta.new_cognitions[0].formed_by == "stated"
    assert delta.formation_traces[0].sources[0].decision_code == "CATALOG_DIRECT_SEGMENT"


def test_catalog_direct_content_is_materialized_with_chinese_terminal_punctuation() -> None:
    content = "我喜欢咖啡。"
    turns = [ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00")]
    llm = ScriptedLLM([_cognition_payload([_source("turn:user")])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 1
    assert delta.new_cognitions[0].formed_by == "stated"
    assert delta.new_cognitions[0].content == content


def test_unterminated_final_user_segment_is_a_stated_direct_claim() -> None:
    content = "I prefer decaf coffee"
    turns = [ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00")]
    llm = ScriptedLLM([_cognition_payload([_source("turn:user")])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert delta.new_cognitions[0].formed_by == "stated"


def test_complete_second_catalog_segment_is_stated_and_unknown_segment_fails() -> None:
    content = "First sentence.Second preference."
    turns = [ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00")]
    complete = _cognition_payload([_source("seg-0001")])
    valid = WorldExtractor(ScriptedLLM([complete])).extract(_base(), turns, {"turn:user"})
    unknown = _cognition_payload([_source("seg-9999")])
    llm = ScriptedLLM([unknown, unknown])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert valid.new_cognitions[0].formed_by == "stated"
    assert raised.value.codes == ("SEGMENT_ID_UNKNOWN@$.new_cognitions[0].sources[0].segment_id",) * 2


def test_inferred_relationship_keeps_an_unverified_grounding_trace() -> None:
    delta = WorldExtractor(ScriptedLLM([_relationship_projection_payload()])).extract(
        _base(), _relationship_projection_turns(), {"turn:user"},
    )

    assert delta.new_cognitions[2].formed_by == "inferred"
    assert delta.formation_traces[2].sources[0].local_origin_decision == "inference_grounding"


def test_conflict_relationship_projection_repairs_a_direct_relationship_fact_on_the_bonus_repair() -> None:
    direct = _conflict_relationship_projection_payload(
        relationship_model_inferred=False,
        relationship_content_type="fact",
    )
    wrong_role = _conflict_relationship_projection_payload(relationship_content_type="fact")
    correct = _conflict_relationship_projection_payload()
    llm = ScriptedLLM([direct, wrong_role, correct])

    delta = WorldExtractor(llm).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    assert llm.call_count == 3
    assert delta.new_cognitions[2].formed_by == "inferred"
    assert "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids" in llm.messages[1][-1].content
    assert "RELATIONSHIP_HYPOTHESIS_REQUIRED@$.new_cognitions[2].content_type" in llm.messages[2][-1].content
    assert "A separately and directly supported Relationship fact" in llm.messages[1][-1].content
    assert "it never substitutes for the inferred projection" in llm.messages[1][-1].content
    assert "TARGETED REPAIR: CONDITIONAL CONFLICT RELATIONSHIP TRANSACTION:" in llm.messages[1][-1].content
    assert "exactly one support source" in llm.messages[1][-1].content
    assert "Make the Relationship cognition the single inferred hypothesis" in llm.messages[2][-1].content


def test_conflict_projection_repairs_the_diagnostic_six_failure_sequence_within_three_calls() -> None:
    invalid_facet = json.loads(_conflict_relationship_projection_payload())
    invalid_facet["new_events"][0]["facets"][0]["value"] = None
    direct_relationship_fact = _conflict_relationship_projection_payload(
        relationship_model_inferred=False,
        relationship_content_type="fact",
    )
    llm = ScriptedLLM([
        json.dumps(invalid_facet),
        direct_relationship_fact,
        _conflict_relationship_projection_payload(),
    ])

    delta = WorldExtractor(llm).extract(
        _base(), _relationship_projection_turns(), {"turn:user"},
    )

    assert llm.call_count == 3
    assert delta.new_cognitions[2].formed_by == "inferred"
    assert "If eligible Evidence supports the failing non-position facet" in (
        llm.messages[1][-1].content
    )
    assert "CONDITIONAL CONFLICT RELATIONSHIP TRANSACTION:" in llm.messages[2][-1].content
    assert "both complete direct-claim segments belong to the same Evidence ID" in (
        llm.messages[2][-1].content
    )


def test_conflict_relationship_projection_stops_after_three_safe_contract_failures() -> None:
    invalid = _conflict_relationship_projection_payload(
        relationship_model_inferred=False,
        relationship_content_type="fact",
    )
    llm = ScriptedLLM([invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    code = "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids"
    assert (llm.call_count, raised.value.attempts, raised.value.codes) == (3, 3, (code, code, code))


@pytest.mark.parametrize(
    "invalid",
    [
        lambda: json.dumps({
            **json.loads(_conflict_relationship_projection_payload()),
            "new_cognitions": json.loads(_conflict_relationship_projection_payload())["new_cognitions"][:2],
        }),
        lambda: _conflict_relationship_projection_payload(relationship_scope=None),
        lambda: _conflict_relationship_projection_payload(relationship_scope="work"),
        lambda: _conflict_relationship_projection_payload(
            relationship_model_inferred=False,
            relationship_content_type="fact",
            relationship_target_id="relationship:other",
        ),
    ],
    ids=["missing", "scope-null", "scope-mismatch", "wrong-target"],
)
def test_conflict_relationship_projection_rejects_each_missing_or_wrong_projection_shape(
    invalid: Callable[[], str],
) -> None:
    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid()])).extract(
            _base(), _relationship_projection_turns(), {"turn:user"},
        )

    assert raised.value.codes == (
        "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids",
    ) * 3


def test_conflict_relationship_projection_allows_a_correct_projection_plus_an_extra_direct_relationship_cognition() -> None:
    payload = json.loads(_conflict_relationship_projection_payload())
    extra = copy.deepcopy(payload["new_cognitions"][2])
    extra["id"] = "cog:relationship-direct-extra"
    extra["model_inferred"] = False
    extra["content_type"] = "fact"
    extra["content"] = None
    extra["perspective"] = {"kind": "entity", "holder_entity_ids": ["person:user"]}
    payload["new_cognitions"].append(extra)

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
        _base(), _relationship_projection_turns(), {"turn:user"},
    )

    assert [cognition.formed_by for cognition in delta.new_cognitions] == [
        "stated", "stated", "inferred", "stated",
    ]


def test_conflict_relationship_projection_rejects_two_direct_relationship_cognitions_without_an_inferred_projection() -> None:
    payload = json.loads(_conflict_relationship_projection_payload(
        relationship_model_inferred=False,
        relationship_content_type="fact",
    ))
    extra = copy.deepcopy(payload["new_cognitions"][2])
    extra["id"] = "cog:relationship-direct-second"
    payload["new_cognitions"].append(extra)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
            _base(), _relationship_projection_turns(), {"turn:user"},
        )

    assert raised.value.codes == (
        "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids",
    ) * 3


def test_conflict_relationship_projection_rejects_two_qualifying_inferred_projections() -> None:
    payload = json.loads(_conflict_relationship_projection_payload())
    extra = copy.deepcopy(payload["new_cognitions"][2])
    extra["id"] = "cog:relationship-travel-second"
    payload["new_cognitions"].append(extra)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
            _base(), _relationship_projection_turns(), {"turn:user"},
        )

    assert raised.value.codes == (
        "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids",
    ) * 3


@pytest.mark.parametrize(
    "extra_scope",
    [None, "work"],
    ids=["scope-null", "scope-mismatch"],
)
def test_conflict_relationship_projection_rejects_an_extra_inferred_proposal_even_when_only_one_matches_scope(
    extra_scope: str | None,
) -> None:
    payload = json.loads(_conflict_relationship_projection_payload())
    extra = copy.deepcopy(payload["new_cognitions"][2])
    extra["id"] = "cog:relationship-travel-extra-inferred"
    extra["scope"] = extra_scope
    payload["new_cognitions"].append(extra)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
            _base(), _relationship_projection_turns(), {"turn:user"},
        )

    assert raised.value.codes == (
        "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids",
    ) * 3


@pytest.mark.parametrize("event_type", ["interpersonal conflict", "人际冲突"])
def test_conflict_alias_wrong_direct_relationship_cannot_bypass_projection_contract(event_type: str) -> None:
    invalid = _conflict_relationship_projection_payload(
        relationship_model_inferred=False,
        relationship_content_type="fact",
        event_type=event_type,
    )

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid])).extract(
            _base(), _relationship_projection_turns(), {"turn:user"},
        )

    assert raised.value.codes == (
        "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids",
    ) * 3


def test_conflict_relationship_same_direct_claim_span_uses_aggregate_contract_then_repairs() -> None:
    invalid = json.loads(_conflict_relationship_projection_payload(
        relationship_model_inferred=False,
        relationship_content_type="fact",
    ))
    invalid["new_cognitions"][1]["sources"] = [_source("seg-0000")]
    repaired = _conflict_relationship_projection_payload()
    llm = ScriptedLLM([json.dumps(invalid), repaired])

    delta = WorldExtractor(llm).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    assert llm.call_count == 2
    assert delta.new_cognitions[2].formed_by == "inferred"
    assert "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids" in llm.messages[1][-1].content


def test_ordinary_failure_then_projection_contract_unlocks_the_bonus_repair() -> None:
    direct = _conflict_relationship_projection_payload(
        relationship_model_inferred=False,
        relationship_content_type="fact",
    )
    llm = ScriptedLLM(["{not json", direct, _conflict_relationship_projection_payload()])

    delta = WorldExtractor(llm).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    assert llm.call_count == 3
    assert delta.new_cognitions[2].formed_by == "inferred"
    assert "JSON_SYNTAX@$" in llm.messages[1][-1].content
    assert "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids" in llm.messages[2][-1].content


def test_trip_place_failure_then_projection_contract_unlocks_the_bonus_repair() -> None:
    def with_trip(payload: str, *, include_place: bool, include_place_link: bool) -> str:
        value = json.loads(payload)
        trip = json.loads(
            _trip_payload(
                include_place=include_place,
                include_place_link=include_place_link,
            )
        )
        value["new_entities"].extend(trip["new_entities"])
        value["new_events"][0]["related_entity_ids"] = trip["new_events"][0]["related_entity_ids"]
        return json.dumps(value, ensure_ascii=False)

    direct = _conflict_relationship_projection_payload(
        relationship_model_inferred=False,
        relationship_content_type="fact",
    )
    llm = ScriptedLLM(
        [
            with_trip(direct, include_place=False, include_place_link=False),
            with_trip(direct, include_place=True, include_place_link=True),
            with_trip(
                _conflict_relationship_projection_payload(),
                include_place=True,
                include_place_link=True,
            ),
        ]
    )

    delta = WorldExtractor(llm).extract(
        _base(),
        _relationship_projection_turns(),
        {"turn:user"},
    )

    assert llm.call_count == 3
    assert {entity.id for entity in delta.new_entities} >= {
        "activity:kyoto-trip",
        "place:kyoto",
    }
    assert "TRIP_ACTIVITY_PLACE_ENTITY_REQUIRED@$.new_entities[1]" in llm.messages[1][-1].content
    assert "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@$.new_events[0].relationship_ids" in (
        llm.messages[2][-1].content
    )


def test_conflict_relationship_projection_preserves_wire_order_when_the_complete_projection_is_first() -> None:
    payload = json.loads(_conflict_relationship_projection_payload())
    payload["new_cognitions"] = [
        payload["new_cognitions"][2],
        payload["new_cognitions"][1],
        payload["new_cognitions"][0],
    ]

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
        _base(), _relationship_projection_turns(), {"turn:user"},
    )

    assert [cognition.id for cognition in delta.new_cognitions] == [
        "cog:relationship-travel", "cog:friend-travel", "cog:user-travel",
    ]


def test_non_conflict_direct_relationship_cognition_remains_allowed() -> None:
    payload = json.loads(_relationship_projection_payload())
    relationship = payload["new_cognitions"][2]
    relationship["model_inferred"] = False
    relationship["content_type"] = "fact"
    relationship["content"] = None
    relationship["perspective"] = {"kind": "entity", "holder_entity_ids": ["person:user"]}

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
        _base(), _relationship_projection_turns(), {"turn:user"},
    )

    assert delta.new_cognitions[2].formed_by == "stated"


def test_relationship_projection_materializes_owner_perspectives_and_exact_bound_sides_without_selector() -> None:
    llm = ScriptedLLM([_relationship_projection_payload()])

    delta = WorldExtractor(llm).extract(
        _base(), _relationship_projection_turns(), {"turn:user"},
    )

    user, friend, relationship = delta.new_cognitions
    assert user.perspective.kind == friend.perspective.kind == "entity"
    assert user.perspective.holder_entity_ids == friend.perspective.holder_entity_ids == ("person:user",)
    assert relationship.perspective.kind == "system"
    assert relationship.perspective.holder_entity_ids == ()
    assert relationship.content == (
        "owner-side: I prefer flexible, unplanned exploration;\n"
        "other-side: Friend_X prefers planning routes in advance and following a guide.\n"
        "relationship inference: scoped contrast/conflict"
    )
    trace = delta.formation_traces[2]
    assert trace.raw_support_count == trace.effective_support_count == 1
    assert [(binding.about_entity_id, binding.evidence_id) for binding in trace.content_bindings] == [
        ("person:user", "turn:user"),
        ("person:friend-x", "turn:user"),
    ]
    serialized = json.dumps(asdict(delta), ensure_ascii=False)
    assert "relationship_side_segments" not in serialized
    assert "segment_id" not in serialized
    assert "eligible_evidence_segments" not in serialized
    assert "raw model" not in serialized


def test_relationship_projection_decodes_directs_first_but_preserves_wire_order_and_owner_first_content() -> None:
    payload = json.loads(_relationship_projection_payload())
    payload["new_relationships"][0]["source_entity_id"] = "person:friend-x"
    payload["new_relationships"][0]["target_entity_id"] = "person:user"
    payload["new_cognitions"] = [
        payload["new_cognitions"][2],
        payload["new_cognitions"][1],
        payload["new_cognitions"][0],
    ]

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
        _base(), _relationship_projection_turns(), {"turn:user"},
    )

    assert [cognition.id for cognition in delta.new_cognitions] == [
        "cog:relationship-travel", "cog:friend-travel", "cog:user-travel",
    ]
    assert delta.new_cognitions[0].content.startswith("owner-side: ")
    assert [binding.about_entity_id for binding in delta.formation_traces[0].content_bindings] == [
        "person:user", "person:friend-x",
    ]


def test_relationship_projection_never_uses_model_entity_names_or_aliases_as_content_labels() -> None:
    payload = json.loads(_relationship_projection_payload())
    payload["new_entities"][0]["canonical_name"] = "MODEL-INJECTED-NAME"
    payload["new_entities"][0]["aliases"] = ["MODEL-INJECTED-ALIAS"]

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
        _base(), _relationship_projection_turns(), {"turn:user"},
    )

    content = delta.new_cognitions[2].content
    assert content.startswith("owner-side: ")
    assert "other-side: " in content
    assert "MODEL-INJECTED-NAME" not in content
    assert "MODEL-INJECTED-ALIAS" not in content
    assert "person:friend-x" not in content


def test_relationship_projection_rejects_model_authored_content_without_retaining_it() -> None:
    invalid = _relationship_projection_payload(
        relationship_content="private unsupported model paraphrase",
    )
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    assert raised.value.codes == ("RELATIONSHIP_CONTENT_MUST_BE_NULL@$.new_cognitions[2].content",) * 2
    assert "private unsupported" not in str(raised.value)


@pytest.mark.parametrize(
    "relationship_perspective",
    [
        {"kind": "entity", "holder_entity_ids": ["person:friend-x"]},
        {"kind": "joint", "holder_entity_ids": ["person:user", "person:friend-x"]},
        {"kind": "system", "holder_entity_ids": []},
    ],
)
def test_relationship_projection_ignores_a_structurally_valid_perspective_proposal(
    relationship_perspective: dict[str, object],
) -> None:
    delta = WorldExtractor(ScriptedLLM([
        _relationship_projection_payload(relationship_perspective=relationship_perspective),
    ])).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    relationship = delta.new_cognitions[2]
    assert relationship.perspective.kind == "system"
    assert relationship.perspective.holder_entity_ids == ()
    assert asdict(relationship)["perspective"] == {"kind": "system", "holder_entity_ids": ()}


def test_relationship_projection_rejects_malformed_perspective_before_ignoring_it() -> None:
    invalid = _relationship_projection_payload(
        relationship_perspective={"kind": "system"},
    )
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    assert raised.value.codes == ("MISSING_KEYS(holder_entity_ids)@$.new_cognitions[2].perspective",) * 2


def test_relationship_projection_rejects_self_loop_endpoints_safely_on_both_attempts() -> None:
    invalid = json.loads(_relationship_projection_payload())
    invalid["new_relationships"][0]["target_entity_id"] = "person:user"
    raw = json.dumps(invalid)
    llm = ScriptedLLM([raw, raw])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    assert raised.value.attempts == 2
    assert raised.value.codes == ("RELATIONSHIP_ENDPOINTS_NOT_DISTINCT@$.new_cognitions[2].target.id",) * 2
    assert "person:user" not in str(raised.value)


def test_relationship_projection_repairs_a_self_loop_with_the_single_available_repair() -> None:
    invalid = json.loads(_relationship_projection_payload())
    invalid["new_relationships"][0]["target_entity_id"] = "person:user"
    llm = ScriptedLLM([json.dumps(invalid), _relationship_projection_payload()])

    delta = WorldExtractor(llm).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    assert llm.calls == 2
    assert delta.new_relationships[0].target_entity_id == "person:friend-x"
    assert "RELATIONSHIP_ENDPOINTS_NOT_DISTINCT@$.new_cognitions[2].target.id" in llm.messages[1][-1].content


def test_relationship_projection_rejects_legacy_selector_key_on_both_decode_attempts() -> None:
    invalid = json.loads(_relationship_projection_payload())
    invalid["new_cognitions"][2]["relationship_side_segments"] = []
    raw = json.dumps(invalid)
    llm = ScriptedLLM([raw, raw])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _relationship_projection_turns(), {"turn:user"})

    assert raised.value.codes == ("EXTRA_KEYS(relationship_side_segments)@$.new_cognitions[2]",) * 2


def test_relationship_projection_rejects_missing_direct_endpoint_candidate() -> None:
    invalid = json.loads(_relationship_projection_payload())
    del invalid["new_cognitions"][1]
    raw = json.dumps(invalid)

    llm = ScriptedLLM([raw, raw])
    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(
            _base(), _relationship_projection_turns(), {"turn:user"},
        )

    assert raised.value.codes == ("RELATIONSHIP_DIRECT_CANDIDATE_MISSING@$.new_cognitions",) * 2
    assert "both claim segments share one Evidence ID" in (
        llm.messages[1][-1].content
    )


def test_relationship_projection_rejects_ambiguous_direct_endpoint_candidate() -> None:
    invalid = json.loads(_relationship_projection_payload())
    duplicate = copy.deepcopy(invalid["new_cognitions"][1])
    duplicate["id"] = "cog:friend-travel-duplicate"
    invalid["new_cognitions"].insert(2, duplicate)
    raw = json.dumps(invalid)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([raw, raw])).extract(
            _base(), _relationship_projection_turns(), {"turn:user"},
        )

    assert raised.value.codes == ("RELATIONSHIP_DIRECT_CANDIDATE_AMBIGUOUS@$.new_cognitions",) * 2


def test_relationship_projection_rejects_direct_candidates_with_the_same_span() -> None:
    invalid = json.loads(_relationship_projection_payload())
    invalid["new_cognitions"][1]["sources"] = [_source("seg-0000")]
    raw = json.dumps(invalid)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([raw, raw])).extract(
            _base(), _relationship_projection_turns(), {"turn:user"},
        )

    assert raised.value.codes == ("RELATIONSHIP_SIDE_SPAN_DUPLICATE@$.new_cognitions[2].target.id",) * 2


def test_relationship_projection_requires_its_own_support_to_cover_both_direct_binding_evidence() -> None:
    invalid = json.loads(_relationship_projection_payload())
    invalid["new_cognitions"][0]["sources"] = [_source("seg-0000")]
    invalid["new_cognitions"][1]["sources"] = [_source("seg-0001")]
    invalid["new_cognitions"][2]["sources"] = [_source("seg-0002")]
    turns = [
        ConversationTurn("turn:one", "conversation:one", "user", "An open schedule.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:two", "conversation:one", "user", "A planned route.", "2026-08-08T10:00:01+08:00"),
        ConversationTurn("turn:three", "conversation:one", "user", "The two positions conflict.", "2026-08-08T10:00:02+08:00"),
    ]
    raw = json.dumps(invalid)

    llm = ScriptedLLM([raw, raw])
    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(
            _base(), turns, {"turn:one", "turn:two", "turn:three"},
        )

    assert raised.value.codes == ("RELATIONSHIP_BINDING_EVIDENCE_UNSUPPORTED@$.new_cognitions[2].target.id",) * 2
    assert "If the bindings belong to different Evidence IDs, no valid local repair exists" in (
        llm.messages[1][-1].content
    )
    assert "Never add multiple hypothesis sources" in llm.messages[1][-1].content


def test_cross_evidence_projection_repair_omits_the_unsatisfiable_cognition_transaction() -> None:
    invalid = json.loads(_relationship_projection_payload())
    invalid["new_cognitions"][0]["sources"] = [_source("seg-0000")]
    invalid["new_cognitions"][1]["sources"] = [_source("seg-0001")]
    invalid["new_cognitions"][2]["sources"] = [_source("seg-0002")]
    invalid["new_events"] = [
        {
            "id": "event:travel-conflict",
            "world_id": "world:yun",
            "event_type": "interpersonal_conflict",
            "summary": "A conflict happened.",
            "occurred_at": "2026-08-08T10:00:02+08:00",
            "participants": [
                {"entity_id": "person:user", "role": "participant"},
                {"entity_id": "person:friend-x", "role": "participant"},
            ],
            "related_entity_ids": [],
            "relationship_ids": ["relationship:user-friend-x"],
            "facets": [
                {"key": "cause", "value": "A difference.", "segment_id": None, "about_entity_id": None},
                {"key": "position", "value": None, "segment_id": "seg-0000", "about_entity_id": "person:user"},
                {"key": "position", "value": None, "segment_id": "seg-0001", "about_entity_id": "person:friend-x"},
            ],
            "evidence_ids": ["turn:one", "turn:two", "turn:three"],
        }
    ]
    repaired = copy.deepcopy(invalid)
    repaired["new_cognitions"] = []
    turns = [
        ConversationTurn("turn:one", "conversation:one", "user", "An open schedule.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:two", "conversation:one", "user", "A planned route.", "2026-08-08T10:00:01+08:00"),
        ConversationTurn("turn:three", "conversation:one", "user", "The two positions conflict.", "2026-08-08T10:00:02+08:00"),
    ]
    llm = ScriptedLLM([json.dumps(invalid), json.dumps(repaired)])

    delta = WorldExtractor(llm).extract(
        _base(), turns, {"turn:one", "turn:two", "turn:three"},
    )

    assert llm.calls == 2
    assert delta.new_cognitions == ()
    assert delta.formation_traces == ()
    assert len(delta.new_events) == 1
    assert "no valid local repair exists under this contract" in llm.messages[1][-1].content
    assert "omit the entire three-Cognition projection transaction" in llm.messages[1][-1].content


def test_inferred_catalog_grounding_requires_nonempty_model_content() -> None:
    invalid = _cognition_payload([_source("turn:user")], model_inferred=True)
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert raised.value.codes == ("INFERRED_CONTENT_REQUIRED@$.new_cognitions[0].content",) * 2
    assert "A non-Relationship inferred cognition needs a non-empty supported content string" in (
        llm.messages[1][-1].content
    )


def test_inferred_cognition_cannot_use_confirmation_carrier_without_context() -> None:
    turns = [ConversationTurn("turn:user", "conversation:one", "user", "Yes!", "2026-08-08T10:00:00+08:00")]
    invalid = _cognition_payload([_source("turn:user")], model_inferred=True, content="An arbitrary inference.")

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("INFERENCE_GROUNDING_REQUIRED@$.new_cognitions[0].sources[0]",) * 2


def test_inferred_cognition_cannot_use_negation_carrier_with_preceding_assistant() -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "Do you like coffee?", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:user", "conversation:one", "user", "不。", "2026-08-08T10:00:01+08:00"),
    ]
    invalid = _cognition_payload([_source("turn:user")], model_inferred=True, content="An arbitrary inference.")

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("INFERENCE_GROUNDING_REQUIRED@$.new_cognitions[0].sources[0]",) * 2


def test_inferred_cognition_cannot_use_punctuation_only_segment() -> None:
    turns = [ConversationTurn("turn:user", "conversation:one", "user", ".", "2026-08-08T10:00:00+08:00")]
    invalid = _cognition_payload([_source("turn:user")], model_inferred=True, content="An arbitrary inference.")

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("SEGMENT_ID_UNKNOWN@$.new_cognitions[0].sources[0].segment_id",) * 2


def test_inferred_cognition_cannot_mix_direct_and_confirmation_support() -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "Do you like coffee?", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:confirm", "conversation:one", "user", "对", "2026-08-08T10:00:01+08:00"),
        ConversationTurn("turn:direct", "conversation:one", "user", "I directly prefer tea.", "2026-08-08T10:00:02+08:00"),
    ]
    invalid = _cognition_payload(
        [_source("seg-0000"), _source("seg-0001")], model_inferred=True, content="An arbitrary inference.",
    )

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(
            _base(), turns, {"turn:confirm", "turn:direct"},
        )

    assert raised.value.codes == ("INFERENCE_GROUNDING_REQUIRED@$.new_cognitions[0].sources[0]",) * 2


def test_inferred_cognition_accepts_multiple_distinct_substantive_groundings() -> None:
    turns = [
        ConversationTurn("turn:one", "conversation:one", "user", "I directly prefer tea.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:two", "conversation:one", "user", "I directly prefer coffee.", "2026-08-08T10:00:01+08:00"),
    ]
    inferred = _cognition_payload(
        [_source("seg-0000"), _source("seg-0001")], model_inferred=True, content="An arbitrary inference.",
    )

    delta = WorldExtractor(ScriptedLLM([inferred])).extract(_base(), turns, {"turn:one", "turn:two"})

    trace = delta.formation_traces[0]
    assert (delta.new_cognitions[0].formed_by, trace.raw_support_count, trace.effective_support_count) == (
        "inferred", 2, 1,
    )
    assert {source.local_origin_decision for source in trace.sources} == {"inference_grounding"}


def test_inferred_cognition_requires_at_least_one_support_source() -> None:
    turns = [ConversationTurn("turn:user", "conversation:one", "user", "I prefer tea.", "2026-08-08T10:00:00+08:00")]
    invalid = _cognition_payload(
        [_source("turn:user", relation="contradict")], model_inferred=True, content="An arbitrary inference.",
    )

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("INFERENCE_SUPPORT_REQUIRED@$.new_cognitions[0].sources",) * 2


def test_inferred_cognition_rejects_unverified_contradiction() -> None:
    turns = [
        ConversationTurn("turn:carrier", "conversation:one", "user", "Yes!", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:direct", "conversation:one", "user", "I prefer tea.", "2026-08-08T10:00:01+08:00"),
    ]
    invalid = _cognition_payload(
        [_source("seg-0001"), _source("seg-0000", relation="contradict")],
        model_inferred=True,
        content="An arbitrary inference.",
    )

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(_base(), turns, {"turn:carrier", "turn:direct"})

    assert raised.value.codes == ("INFERENCE_CONTRADICT_UNBOUND@$.new_cognitions[0].sources[1]",) * 2


def test_inferred_cognition_rejects_confirmation_contradiction() -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "Do you like coffee?", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:carrier", "conversation:one", "user", "Yes!", "2026-08-08T10:00:01+08:00"),
        ConversationTurn("turn:direct", "conversation:one", "user", "I prefer tea.", "2026-08-08T10:00:02+08:00"),
    ]
    invalid = _cognition_payload(
        [_source("seg-0001"), _source("seg-0000", relation="contradict")],
        model_inferred=True,
        content="An arbitrary inference.",
    )

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([invalid, invalid])).extract(_base(), turns, {"turn:carrier", "turn:direct"})

    assert raised.value.codes == ("INFERENCE_CONTRADICT_UNBOUND@$.new_cognitions[0].sources[1]",) * 2


def test_inferred_cognition_allows_contextual_negation_contradiction() -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "Do you like coffee?", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:negation", "conversation:one", "user", "不。", "2026-08-08T10:00:01+08:00"),
        ConversationTurn("turn:direct", "conversation:one", "user", "I prefer tea.", "2026-08-08T10:00:02+08:00"),
    ]
    inferred = _cognition_payload(
        [_source("seg-0001"), _source("seg-0000", relation="contradict")],
        model_inferred=True,
        content="The user does not prefer coffee and prefers tea.",
    )

    delta = WorldExtractor(ScriptedLLM([inferred])).extract(_base(), turns, {"turn:negation", "turn:direct"})

    assert [source.local_origin_decision for source in delta.formation_traces[0].sources] == [
        "inference_grounding", "user_negation",
    ]


def test_inferred_cognition_allows_substantive_contradiction_without_counting_it_as_grounding() -> None:
    turns = [
        ConversationTurn("turn:one", "conversation:one", "user", "I prefer tea.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:two", "conversation:one", "user", "I prefer coffee.", "2026-08-08T10:00:01+08:00"),
    ]
    inferred = _cognition_payload(
        [_source("seg-0000"), _source("seg-0001", relation="contradict")],
        model_inferred=True,
        content="The preference is contested.",
    )

    delta = WorldExtractor(ScriptedLLM([inferred])).extract(_base(), turns, {"turn:one", "turn:two"})

    trace = delta.formation_traces[0]
    assert (trace.raw_support_count, trace.effective_support_count, trace.contradict_count) == (1, 1, 1)
    assert {source.local_origin_decision for source in trace.sources} == {"inference_grounding"}


def test_unknown_segment_id_fails_without_exposing_catalog_text() -> None:
    llm = ScriptedLLM([_cognition_payload([_source("seg-9999")])] * 2)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert (llm.call_count, raised.value.attempts) == (2, 2)
    assert raised.value.codes == ("SEGMENT_ID_UNKNOWN@$.new_cognitions[0].sources[0].segment_id",) * 2


def test_repair_cannot_promote_carrier_by_changing_its_model_proposal() -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "Do you like coffee?", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:user", "conversation:one", "user", "对", "2026-08-08T10:00:01+08:00"),
    ]
    invalid = json.loads(_cognition_payload([
        _source("turn:user", proposition_origin="assistant_proposed", response_act="affirm"),
    ], content="Coffee is preferred."))
    invalid["new_cognitions"][0]["formed_by"] = "stated"
    repaired = _cognition_payload([
        _source("turn:user", proposition_origin="user_stated", response_act="none"),
    ], content="Coffee is preferred.")

    delta = WorldExtractor(ScriptedLLM([json.dumps(invalid), repaired])).extract(_base(), turns, {"turn:user"})

    assert delta.new_cognitions[0].formed_by == "confirmed"
    assert delta.formation_traces[0].sources[0].local_origin_decision == "assistant_confirmation"


@pytest.mark.parametrize(
    ("response_act", "user_content", "content", "expected_formed_by", "expected_confidence"),
    [
        ("affirm", "对", "Coffee is preferred.", "confirmed", 280),
    ],
)
def test_assistant_proposed_source_derives_from_immediate_preceding_assistant_context(
    response_act: str, user_content: str, content: str,
    expected_formed_by: str, expected_confidence: int,
) -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "A proposed claim.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:user", "conversation:one", "user", user_content, "2026-08-08T10:00:01+08:00"),
    ]
    llm = ScriptedLLM([
        _cognition_payload([
            _source("turn:user", proposition_origin="assistant_proposed", response_act=response_act),
        ], content=content),
    ])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    cognition = delta.new_cognitions[0]
    assert (cognition.formed_by, cognition.confidence, cognition.cred_status) == (
        expected_formed_by,
        expected_confidence,
        "limited" if expected_confidence == 600 else "candidate",
    )


def test_carrier_negation_cannot_promote_an_unbound_assistant_proposition() -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "Do you like coffee?", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:user", "conversation:one", "user", "不", "2026-08-08T10:00:01+08:00"),
    ]
    invalid = _cognition_payload([
        _source("turn:user", proposition_origin="user_stated", response_act="none"),
    ], content="The user is a billionaire.")
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("FORMATION_UNVERIFIED@$.new_cognitions[0].sources[0]",) * 2


def test_confirmation_catalog_carrier_requires_nonempty_model_content() -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "A proposed claim.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:user", "conversation:one", "user", "对", "2026-08-08T10:00:01+08:00"),
    ]
    invalid = _cognition_payload([_source("turn:user")])
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("CONFIRMATION_CONTENT_REQUIRED@$.new_cognitions[0].content",) * 2


def test_mixed_direct_and_confirmation_supports_fail_closed() -> None:
    turns = [
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "An earlier proposal.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:one", "conversation:one", "user", "对", "2026-08-08T10:00:01+08:00"),
        ConversationTurn("turn:two", "conversation:one", "user", "I state this directly.", "2026-08-08T10:00:02+08:00"),
        ConversationTurn("turn:three", "conversation:one", "user", "A contradictory note.", "2026-08-08T10:00:03+08:00"),
    ]
    invalid = _cognition_payload([
            _source("turn:one", proposition_origin="assistant_proposed", response_act="affirm"),
            _source("turn:two"),
            _source("turn:three", relation="contradict", proposition_origin="user_stated"),
        ], content="I state this directly.")
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:one", "turn:two", "turn:three"})

    assert raised.value.codes == ("FORMATION_MIXED_SUPPORT@$.new_cognitions[0].sources",) * 2


def test_max_schema_supported_sources_remain_limited_below_the_stable_threshold() -> None:
    turns = _eligible_turns(4)
    sources = [_source(turn.turn_id) for turn in turns]
    llm = ScriptedLLM([_cognition_payload(sources)])

    delta = WorldExtractor(llm).extract(_base(), turns, {turn.turn_id for turn in turns})

    assert (delta.new_cognitions[0].confidence, delta.new_cognitions[0].cred_status) == (600, "limited")


@pytest.mark.parametrize(
    ("relations", "expected"),
    [
        (["support", "contradict"], (480, "conflicted")),
        (["support", "support", "contradict"], (480, "conflicted")),
    ],
)
def test_cognition_contradiction_status_is_derived_from_source_counts(
    relations: list[str], expected: tuple[int, str],
) -> None:
    turns = _eligible_turns(len(relations))
    sources = [
        _source(turn.turn_id, relation)
        for turn, relation in zip(turns, relations, strict=True)
    ]
    llm = ScriptedLLM([_cognition_payload(sources)])

    delta = WorldExtractor(llm).extract(_base(), turns, {turn.turn_id for turn in turns})

    assert (delta.new_cognitions[0].confidence, delta.new_cognitions[0].cred_status) == expected


def test_model_supplied_legacy_authority_fields_are_rejected_as_extra_keys() -> None:
    valid = _cognition_payload([_source("turn:user")])
    invalid = json.loads(valid)
    invalid["new_cognitions"][0]["confidence"] = 999
    invalid["new_cognitions"][0]["cred_status"] = "stable"
    invalid["new_cognitions"][0]["formed_by"] = "ruled"
    llm = ScriptedLLM([json.dumps(invalid), valid])

    delta = WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert delta.new_cognitions[0].confidence == 600
    assert "EXTRA_KEYS(confidence,cred_status,formed_by)@$.new_cognitions[0]" in llm.messages[1][-1].content


def test_source_without_segment_id_and_model_trace_are_rejected_on_the_wire() -> None:
    missing_segment = json.loads(_cognition_payload([_source("turn:user")]))
    del missing_segment["new_cognitions"][0]["sources"][0]["segment_id"]
    traced = json.loads(_cognition_payload([_source("turn:user")]))
    traced["formation_traces"] = []
    llm = ScriptedLLM([json.dumps(missing_segment), json.dumps(traced)])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert raised.value.codes == (
        "MISSING_KEYS(segment_id)@$.new_cognitions[0].sources[0]",
        "EXTRA_KEYS(formation_traces)@$",
    )


def test_legacy_evidence_id_and_claim_quote_wire_keys_are_rejected() -> None:
    invalid = json.loads(_cognition_payload([_source("turn:user")]))
    invalid["new_cognitions"][0]["sources"][0].update({
        "evidence_id": "turn:user", "claim_quote": "Friend_X is my friend.",
    })
    valid = _cognition_payload([_source("turn:user")])
    llm = ScriptedLLM([json.dumps(invalid), valid])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert "EXTRA_KEYS(claim_quote,evidence_id)@$.new_cognitions[0].sources[0]" in llm.messages[1][-1].content


def test_assistant_proposed_source_without_immediate_trusted_assistant_context_fails_closed() -> None:
    invalid = _cognition_payload([
        _source("turn:user", proposition_origin="assistant_proposed", response_act="affirm"),
    ], content="A proposed claim.")
    llm = ScriptedLLM([invalid, invalid])
    turns = [
        ConversationTurn("turn:user", "conversation:one", "user", "对", "2026-08-08T10:00:01+08:00"),
    ]

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert raised.value.codes == ("FORMATION_UNVERIFIED@$.new_cognitions[0].sources[0]",) * 2


@pytest.mark.parametrize("proposed_id", ["turn:assistant", "turn:invented"])
def test_cognition_source_cannot_use_assistant_or_invented_evidence_id(proposed_id: str) -> None:
    invalid = _cognition_payload([_source(proposed_id)])
    llm = ScriptedLLM([invalid, _cognition_payload([_source("turn:user")])])

    delta = WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert llm.calls == 2
    assert delta.new_cognitions[0].sources[0].evidence_id == "turn:user"
    assert "SEGMENT_ID_UNKNOWN@$.new_cognitions[0].sources[0].segment_id" in llm.messages[1][-1].content


def test_cognition_without_support_is_rejected_by_delta_after_conservative_local_formation() -> None:
    invalid = _cognition_payload([_source("turn:user", relation="contradict")], content="Unsupported claim.")
    llm = ScriptedLLM([invalid, _payload()])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert llm.calls == 2
    assert "DELTA_DOMAIN(cognition[0].sources.support.empty)@$" in llm.messages[1][-1].content


@pytest.mark.parametrize(
    ("assistant_id", "user_id", "assistant_content", "user_content"),
    [
        ("prior-a", "evidence-b", "A prior proposition.", "对"),
        ("上一轮", "用户证据", "一个先前命题。", "对"),
    ],
)
def test_formation_derivation_is_bound_to_turn_structure_not_ids_or_keywords(
    assistant_id: str, user_id: str, assistant_content: str, user_content: str,
) -> None:
    turns = [
        ConversationTurn(assistant_id, "conversation:one", "assistant", assistant_content, "2026-08-08T10:00:00+08:00"),
        ConversationTurn(user_id, "conversation:one", "user", user_content, "2026-08-08T10:00:01+08:00"),
    ]
    llm = ScriptedLLM([
        _cognition_payload([
            _source("seg-0000", proposition_origin="assistant_proposed", response_act="elaborate"),
        ], content="A prior proposition."),
    ])

    delta = WorldExtractor(llm).extract(_base(), turns, {user_id})

    assert delta.new_cognitions[0].formed_by == "confirmed"


@pytest.mark.parametrize(
    "evidence",
    ["I prefer driving without a fixed plan.", "我更喜欢开车，不要固定行程。"],
)
def test_conflict_position_materializes_the_exact_complete_user_segment(
    evidence: str,
) -> None:
    llm = ScriptedLLM([_conflict_payload("ignored by trusted materialization")])

    delta = WorldExtractor(llm).extract(_base(), _position_turns(evidence), {"turn:user"})

    assert llm.calls == 1
    assert delta.new_events[0].facets[1].value == evidence


def test_conflict_missing_one_participant_position_uses_the_only_repair_and_full_repair_passes() -> None:
    evidence = "I prefer an open schedule. Companion prefers a fixed schedule."
    llm = ScriptedLLM([
        _two_party_conflict_payload(
            user_position="I prefer an open schedule.",
            peer_position=None,
        ),
        _two_party_conflict_payload(
            user_position="I prefer an open schedule.",
            peer_position="Companion prefers a fixed schedule.",
        ),
    ])

    delta = WorldExtractor(llm).extract(_base(), _position_turns(evidence), {"turn:user"})

    assert llm.calls == 2
    assert {facet.about_entity_id for facet in delta.new_events[0].facets if facet.key == "position"} == {
        "person:user",
        "person:friend-x",
    }
    repair = llm.messages[1][-1].content
    assert "CONFLICT_POSITION_COUNT@$.new_events[0].participants[1]" in repair
    assert "If the catalog contains one distinct substantive complete position segment" in repair
    assert "If complete support for every participant is absent, omit the whole conflict event" in repair


def test_conflict_missing_its_single_cause_uses_the_only_repair_and_full_repair_passes() -> None:
    evidence = "I prefer an open schedule. Companion prefers a fixed schedule."
    complete = _two_party_conflict_payload(
        user_position="I prefer an open schedule.",
        peer_position="Companion prefers a fixed schedule.",
    )
    invalid = json.loads(complete)
    invalid["new_events"][0]["facets"] = invalid["new_events"][0]["facets"][1:]
    llm = ScriptedLLM([json.dumps(invalid), complete])

    delta = WorldExtractor(llm).extract(_base(), _position_turns(evidence), {"turn:user"})

    assert llm.calls == 2
    assert [facet.key for facet in delta.new_events[0].facets].count("cause") == 1
    assert "CONFLICT_CAUSE_COUNT@$.new_events[0].facets" in llm.messages[1][-1].content


def test_conflict_missing_a_participant_position_twice_fails_safely() -> None:
    evidence = "I prefer an open schedule. Companion prefers a fixed schedule."
    incomplete = _two_party_conflict_payload(
        user_position="I prefer an open schedule.",
        peer_position=None,
    )
    llm = ScriptedLLM([incomplete, incomplete])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _position_turns(evidence), {"turn:user"})

    assert llm.calls == 2
    assert raised.value.attempts == 2
    assert raised.value.codes == ("CONFLICT_POSITION_COUNT@$.new_events[0].participants[1]",) * 2


def test_conflict_uncertainty_does_not_exempt_a_missing_participant_position() -> None:
    evidence = "I prefer an open schedule. Companion prefers a fixed schedule."
    incomplete = _two_party_conflict_payload(
        user_position="I prefer an open schedule.",
        peer_position=None,
        semantic_uncertainties=[
            {"detail": "The other participant's preference is not supported.", "evidence_ids": ["turn:user"]},
        ],
    )
    llm = ScriptedLLM([
        incomplete,
        _two_party_conflict_payload(
            user_position="I prefer an open schedule.",
            peer_position="Companion prefers a fixed schedule.",
        ),
    ])

    delta = WorldExtractor(llm).extract(_base(), _position_turns(evidence), {"turn:user"})

    assert llm.calls == 2
    assert len([facet for facet in delta.new_events[0].facets if facet.key == "position"]) == 2
    assert "CONFLICT_POSITION_COUNT@$.new_events[0].participants[1]" in llm.messages[1][-1].content


def test_conflict_uncertainty_does_not_allow_a_misattributed_position() -> None:
    evidence = "I prefer an open schedule. Companion prefers a fixed schedule."
    invalid = json.loads(
        _two_party_conflict_payload(
            user_position="I prefer an open schedule.",
            peer_position="Companion prefers a fixed schedule.",
            semantic_uncertainties=[
                {"detail": "An unrelated interpretation is uncertain.", "evidence_ids": ["turn:user"]},
            ],
        )
    )
    invalid["new_events"][0]["facets"][2]["about_entity_id"] = "person:unlisted"
    repaired = _two_party_conflict_payload(
        user_position="I prefer an open schedule.",
        peer_position="Companion prefers a fixed schedule.",
    )
    llm = ScriptedLLM([json.dumps(invalid), repaired])

    delta = WorldExtractor(llm).extract(_base(), _position_turns(evidence), {"turn:user"})

    assert llm.calls == 2
    assert len(delta.new_events[0].facets) == 3
    assert "CONFLICT_POSITION_SUBJECT@$.new_events[0].facets[2].about_entity_id" in llm.messages[1][-1].content


def test_non_null_conflict_position_uses_the_single_repair_budget_without_mutating_base() -> None:
    base = _base()
    before = copy.deepcopy(base)
    invalid = json.loads(_conflict_payload("ignored"))
    invalid["new_events"][0]["facets"][1]["value"] = "I prefer driving."
    llm = ScriptedLLM([json.dumps(invalid), _conflict_payload("ignored")])

    WorldExtractor(llm).extract(base, _position_turns("I prefer driving without a fixed plan."), {"turn:user"})

    assert llm.calls == 2
    assert base == before
    assert "POSITION_VALUE_MUST_BE_NULL@$.new_events[0].facets[1].value" in llm.messages[1][-1].content
    assert "emit exactly one position facet per participant with value null" in (
        llm.messages[1][-1].content
    )


def test_two_non_null_conflict_positions_fail_with_safe_bounded_codes() -> None:
    invalid = json.loads(_conflict_payload("ignored"))
    invalid["new_events"][0]["facets"][1]["value"] = "I prefer driving."
    raw = json.dumps(invalid)
    llm = ScriptedLLM([raw, raw])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(
            _base(), _position_turns("I prefer driving without a fixed plan."), {"turn:user"},
        )

    assert raised.value.attempts == 2
    assert raised.value.codes == ("POSITION_VALUE_MUST_BE_NULL@$.new_events[0].facets[1].value",) * 2


def test_assistant_only_position_segment_does_not_count_as_eligible_evidence() -> None:
    invalid = _conflict_payload("ignored", segment_id="seg-0001")
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(
            _base(), _position_turns("I prefer walking.", "I prefer driving."), {"turn:user"},
        )

    assert raised.value.codes == ("POSITION_SEGMENT_UNKNOWN@$.new_events[0].facets[1].segment_id",) * 2


def test_unknown_position_segment_fails_closed_without_exposing_catalog_text() -> None:
    invalid = _conflict_payload("ignored", segment_id="seg-invented")
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _position_turns("A private position."), {"turn:user"})

    assert raised.value.codes == ("POSITION_SEGMENT_UNKNOWN@$.new_events[0].facets[1].segment_id",) * 2
    assert "private position" not in str(raised.value)


def test_three_key_legacy_facet_wire_object_is_rejected_before_domain_decoding() -> None:
    legacy = json.loads(_conflict_payload("ignored"))
    del legacy["new_events"][0]["facets"][1]["segment_id"]
    valid = _conflict_payload("ignored")
    llm = ScriptedLLM([json.dumps(legacy), valid])

    WorldExtractor(llm).extract(_base(), _position_turns("A supported position."), {"turn:user"})

    assert "MISSING_KEYS(segment_id)@$.new_events[0].facets[1]" in llm.messages[1][-1].content


def test_position_requires_a_substantive_complete_catalog_segment() -> None:
    invalid = _conflict_payload("ignored")
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _position_turns("Yes."), {"turn:user"})

    assert raised.value.codes == ("POSITION_SEGMENT_NOT_SUBSTANTIVE@$.new_events[0].facets[1].segment_id",) * 2


def test_position_segment_evidence_must_belong_to_its_event() -> None:
    turns = [
        ConversationTurn("turn:one", "conversation:one", "user", "First position.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:two", "conversation:one", "user", "Second position.", "2026-08-08T10:00:01+08:00"),
    ]
    invalid = _conflict_payload("ignored", segment_id="seg-0001")
    llm = ScriptedLLM([invalid, invalid])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:one", "turn:two"})

    assert raised.value.codes == ("POSITION_EVIDENCE@$.new_events[0].facets[1].segment_id",) * 2


def test_conflict_cannot_reuse_one_position_segment_for_two_participants() -> None:
    invalid = json.loads(_two_party_conflict_payload(
        user_position="User position.", peer_position="Peer position.",
    ))
    invalid["new_events"][0]["facets"][2]["segment_id"] = "seg-0000"
    raw = json.dumps(invalid)
    llm = ScriptedLLM([raw, raw])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(
            llm,
        ).extract(_base(), _position_turns("User position. Peer position."), {"turn:user"})

    assert raised.value.codes == ("CONFLICT_POSITION_SEGMENT_REUSE@$.new_events[0].facets",) * 2


def test_same_evidence_can_bind_distinct_complete_segments_to_distinct_participants() -> None:
    raw = _two_party_conflict_payload(user_position="User position.", peer_position="Peer position.")
    llm = ScriptedLLM([raw])

    delta = WorldExtractor(llm).extract(
        _base(), _position_turns("User position. Peer position."), {"turn:user"},
    )

    assert [facet.value for facet in delta.new_events[0].facets if facet.key == "position"] == [
        "User position.", " Peer position.",
    ]


def test_duplicate_catalog_text_fails_closed_before_public_position_is_ambiguous() -> None:
    payload = json.loads(_conflict_payload("ignored"))
    payload["new_events"][0]["evidence_ids"] = ["turn:one"]
    raw = json.dumps(payload)
    llm = ScriptedLLM([raw, raw])
    turns = [
        ConversationTurn("turn:one", "conversation:one", "user", "Same position.", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:two", "conversation:one", "user", "Same position.", "2026-08-08T10:00:01+08:00"),
    ]

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:one", "turn:two"})

    assert raised.value.codes == ("POSITION_SEGMENT_AMBIGUOUS@$.new_events[0].facets[1].segment_id",) * 2


@pytest.mark.parametrize("segment_id", ["seg-0000", "seg-invented"])
def test_non_position_conflict_facets_ignore_known_and_unknown_segment_ids(
    segment_id: str,
) -> None:
    payload = json.loads(_conflict_payload("ignored"))
    payload["new_events"][0]["facets"][0]["segment_id"] = segment_id

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload)])).extract(
        _base(), _position_turns("Supported position."), {"turn:user"},
    )

    facet = delta.new_events[0].facets[0]
    assert asdict(facet) == {"key": "cause", "value": "A difference.", "about_entity_id": None}
    serialized = json.dumps(asdict(delta), ensure_ascii=False)
    assert segment_id not in serialized
    assert "segment_id" not in serialized


@pytest.mark.parametrize(
    ("segment_id", "value", "error"),
    [
        ([], "A difference.", "TYPE_STRING@$.new_events[0].facets[0].segment_id"),
        (None, None, "FACET_VALUE_REQUIRED@$.new_events[0].facets[0].value"),
    ],
)
def test_non_position_conflict_facet_still_rejects_malformed_segment_or_null_value(
    segment_id: object, value: object, error: str,
) -> None:
    invalid = json.loads(_conflict_payload("ignored"))
    invalid["new_events"][0]["facets"][0].update({"segment_id": segment_id, "value": value})
    raw = json.dumps(invalid)
    llm = ScriptedLLM([raw, raw])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _position_turns("Supported position."), {"turn:user"})

    assert raised.value.codes == (error,) * 2


def test_non_conflict_facet_uses_normal_value_mode_and_ignores_segment_id() -> None:
    valid = json.loads(_conflict_payload("ignored"))
    event = valid["new_events"][0]
    event["event_type"] = "ordinary_event"
    event["facets"] = [{"key": "outcome", "value": "resolved", "segment_id": None, "about_entity_id": None}]
    delta = WorldExtractor(ScriptedLLM([json.dumps(valid)])).extract(
        _base(), _position_turns("Supported position."), {"turn:user"},
    )
    assert delta.new_events[0].facets[0].value == "resolved"

    redundant = copy.deepcopy(valid)
    redundant["new_events"][0]["facets"][0]["segment_id"] = "seg-invented"
    ignored = WorldExtractor(ScriptedLLM([json.dumps(redundant)])).extract(
        _base(), _position_turns("Supported position."), {"turn:user"},
    )
    assert asdict(ignored.new_events[0].facets[0]) == {
        "key": "outcome", "value": "resolved", "about_entity_id": None,
    }


@pytest.mark.parametrize(
    "invalid",
    [
        "{not json",
        _payload(new_entities=[{"id": "person:friend-x"}]),
    ],
)
def test_syntax_and_schema_failures_share_the_single_repair_call(invalid: str) -> None:
    llm = ScriptedLLM([invalid, _payload()])

    delta = WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert delta.new_entities[0].canonical_name == "Friend_X"
    assert llm.calls == 2


def test_domain_failure_is_repaired_with_the_same_budget() -> None:
    invalid_domain = _payload(
        new_entities=[],
        new_relationships=[
            {
                "id": "relationship:user-missing",
                "world_id": "world:yun",
                "source_entity_id": "person:user",
                "target_entity_id": "person:missing",
                "relation_type": "friend",
                "bidirectional": True,
            }
        ],
    )
    llm = ScriptedLLM([invalid_domain, _payload()])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert llm.calls == 2
    assert llm.messages[1][:-2] == llm.messages[0]
    assert llm.messages[1][-2].role == "assistant"
    assert llm.messages[1][-2].content == invalid_domain
    repair_instruction = llm.messages[1][-1].content
    assert "DELTA_DOMAIN(relationship[0].target_entity_id.dangling)@$" in repair_instruction
    assert "turn:user" not in repair_instruction
    assert "Friend_X is my friend." not in repair_instruction


def test_transport_failure_on_repair_preserves_the_first_safe_failure() -> None:
    llm = TimeoutOnSecondLLM(["{not json"])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert raised.value.attempts == 2
    assert raised.value.codes == ("JSON_SYNTAX@$", "LLM_TIMEOUT@$")


@pytest.mark.parametrize(
    "bad_turn",
    [
        ConversationTurn("turn:user", "conversation:one", "user", " ", "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:user", "conversation:one", "user", "content", " "),
    ],
)
def test_empty_turn_fields_fail_preflight_without_calling_llm(bad_turn: ConversationTurn) -> None:
    llm = ScriptedLLM([_payload()])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), [bad_turn], {"turn:user"})

    assert raised.value.attempts == 0
    assert llm.calls == 0


@pytest.mark.parametrize("invalid", ["```json\n{}\n```", _payload(new_entities=float("nan"))])
def test_fences_and_non_finite_constants_are_strict_and_never_fall_back_to_empty_delta(invalid: str) -> None:
    llm = ScriptedLLM([invalid, "NaN"])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert llm.calls == 2
    assert raised.value.attempts == 2
    assert is_dataclass(raised.value)
    assert raised.value.codes == ("JSON_SYNTAX@$",) * 2


@pytest.mark.parametrize("allowlist", [{"turn:assistant"}, {"turn:tool"}, {"turn:unknown"}])
def test_assistant_and_unknown_allowlist_entries_fail_preflight_without_calling_llm(allowlist: set[str]) -> None:
    llm = ScriptedLLM([_payload()])
    turns = [
        *_turns(),
        ConversationTurn("turn:tool", "conversation:one", "tool", "untrusted tool output", "2026-08-08T10:00:02+08:00"),
    ]

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, allowlist)

    assert raised.value.attempts == 0
    assert llm.calls == 0


def test_assistant_turn_is_visible_context_but_cannot_be_provenance() -> None:
    llm = ScriptedLLM([_payload()])

    delta = WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    initial_text = "\n".join(message.content for message in llm.messages[0])
    assert '"role":"assistant"' in initial_text
    assert "That sounds meaningful." in initial_text
    assert delta.source_evidence_ids == ("turn:user",)
    assert "turn:assistant" not in initial_text.split('"eligible_evidence_ids"', 1)[1]


def test_prompt_contains_the_complete_nullable_and_empty_collection_shape() -> None:
    llm = ScriptedLLM([_payload()])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    system = llm.messages[0][0].content
    assert '"relationship_ids":[]' in system
    assert '"facets":[' in system
    assert '"value":"<non-empty-value>"' in system
    assert "<value-or-null>" not in system
    assert '"invalid_at":null' in system


def test_prompt_contract_requires_evidence_bounded_conflict_objects_and_language() -> None:
    llm = ScriptedLLM([_payload()])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    system = llm.messages[0][0].content
    assert "canonical_name must correspond to a directly mentioned named thing" in system
    assert "Omit an unmentioned background actor" in system
    assert "organization" not in system
    assert "tourism" not in system
    assert "named, persistently referable\n  undertaking" in system
    assert "A verb, travel mode, behavior, preference, or abstract concept\n  is not an Entity" in system
    assert "do not\n  split them into an extra planning event" in system
    assert 'EventFacet keys are exactly\n  "cause" and "position"' in system
    assert 'never use "position-user"' in system
    assert "Preserve every explicitly contrast-defining attribute" in system
    assert "Each facet has exactly key, value, segment_id, about_entity_id." in system
    assert "set value to null and select one substantive" in system
    assert "Never reuse one position" in system
    assert "For every other facet,\n  segment_id should be null and value must be non-empty." in system
    assert "redundant non-null segment_id is ignored: do not treat it as provenance" in system
    assert "evidence_ids must include every\n  eligible turn" in system
    assert "do not cite only a final occurrence turn" in system
    assert "payload's evidence_language is authoritative" in system
    assert "preserve short direct Evidence phrases" in system


def test_prompt_contract_scopes_domain_preferences_and_single_occurrence_patterns() -> None:
    llm = ScriptedLLM([_payload()])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    system = llm.messages[0][0].content
    assert "concise,\n  canonical domain token in scope rather than making the preference global" in system
    assert "travel for a travel-only preference" in system
    assert 'content_type "hypothesis", model_inferred true, perspective null' in system
    assert "concise canonical scope token naming the relevant context" in system
    assert "travel for a travel-only pattern" in system
    assert "never promote it to an unscoped state" in system
    assert "content is null and perspective should be null" in system
    assert "redundant\n  relationship perspective proposal is ignored locally" in system
    assert "derives each side\n  from this delta's exact direct cognitions" in system
    assert "Do not emit formed_by, confidence, or" in system
    assert "model_inferred as a boolean" in system
    assert "proposition_origin is exactly user_stated or assistant_proposed" in system


def test_owner_self_proposition_empty_delta_uses_the_single_repair() -> None:
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "I usually work six days a week.",
            "2026-08-09T10:00:00+08:00",
        ),
    ]
    llm = ScriptedLLM([
        _payload(new_entities=[]),
        _cognition_payload([_source("turn:user")]),
    ])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 2
    assert delta.new_cognitions[0].target.id == "person:user"
    assert "OWNER_MEMORY_COVERAGE_EMPTY@$.new_cognitions" in llm.messages[1][-1].content


def test_owner_self_proposition_still_fails_closed_when_repair_has_no_owner_coverage() -> None:
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "我平时都是单休。",
            "2026-08-09T10:00:00+08:00",
        ),
    ]
    llm = ScriptedLLM([_payload(new_entities=[]), _payload(new_entities=[])])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 2
    assert raised.value.codes == ("OWNER_MEMORY_COVERAGE_EMPTY@$.new_cognitions",) * 2


def test_owner_relationship_coverage_does_not_require_an_extra_owner_cognition() -> None:
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "我有一只猫叫二五。",
            "2026-08-09T10:00:00+08:00",
        ),
    ]
    llm = ScriptedLLM([
        _payload(
            new_entities=[
                {
                    "id": "animal:erwu",
                    "world_id": "world:yun",
                    "kind": "animal",
                    "canonical_name": "二五",
                    "aliases": [],
                }
            ],
            new_relationships=[
                {
                    "id": "relationship:user-erwu",
                    "world_id": "world:yun",
                    "source_entity_id": "person:user",
                    "target_entity_id": "animal:erwu",
                    "relation_type": "owns",
                    "bidirectional": False,
                }
            ],
        ),
    ])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 1
    assert delta.new_relationships[0].source_entity_id == "person:user"


def test_owner_pet_introduction_falls_back_to_animal_and_ownership_after_repeated_bad_event_facets() -> None:
    content = "我有一只小猫"
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-10T15:15:34+08:00",
        ),
    ]
    invalid = _payload(
        new_entities=[
            {
                "id": "animal:local-cat",
                "world_id": "world:yun",
                "kind": "animal",
                "canonical_name": "小猫",
                "aliases": [],
            }
        ],
        new_relationships=[
            {
                "id": "relationship:user-local-cat",
                "world_id": "world:yun",
                "source_entity_id": "person:user",
                "target_entity_id": "animal:local-cat",
                "relation_type": "owns",
                "bidirectional": False,
            }
        ],
        new_events=[
            {
                "id": "event:pet-introduction",
                "world_id": "world:yun",
                "event_type": "pet_ownership",
                "summary": content,
                "occurred_at": "2026-08-10T15:15:34+08:00",
                "participants": [
                    {"entity_id": "person:user", "role": "owner"},
                    {"entity_id": "animal:local-cat", "role": "pet"},
                ],
                "related_entity_ids": [],
                "relationship_ids": ["relationship:user-local-cat"],
                "facets": [
                    {
                        "key": "detail",
                        "value": None,
                        "segment_id": None,
                        "about_entity_id": None,
                    }
                ],
                "evidence_ids": ["turn:user"],
            }
        ],
    )
    llm = ScriptedLLM([invalid, invalid])
    base = _base()

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 2
    assert len(delta.new_entities) == 1
    pet = delta.new_entities[0]
    assert (pet.kind, pet.canonical_name, pet.aliases) == ("animal", "小猫", ())
    assert len(delta.new_relationships) == 1
    ownership = delta.new_relationships[0]
    assert (
        ownership.source_entity_id,
        ownership.target_entity_id,
        ownership.relation_type,
        ownership.bidirectional,
    ) == ("person:user", pet.id, "owns", False)
    assert not delta.new_events
    assert not delta.new_cognitions
    assert base.entities == {
        "person:user": Entity("person:user", "world:yun", "person", "User")
    }


def test_accepted_pet_and_prior_user_mention_bind_a_followup_pronoun_to_exact_pet_facts() -> None:
    base = _base()
    base.add_entity(Entity("animal:pet-cat", "world:yun", "animal", "小猫"))
    base.add_relationship(
        Relationship(
            "relationship:user-pet-cat",
            "world:yun",
            "person:user",
            "animal:pet-cat",
            "owns",
            False,
        )
    )
    content = "她叫二五，是一只小母猫，今年都3岁了"
    turns = [
        ConversationTurn(
            "turn:pet-introduction",
            "conversation:one",
            "user",
            "我有一只小猫",
            "2026-08-10T15:15:34+08:00",
        ),
        ConversationTurn(
            "turn:assistant",
            "conversation:one",
            "assistant",
            "它叫什么名字呀？",
            "2026-08-10T15:15:59+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-10T15:16:23+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("turn:user")], model_inferred=True))
    raw["new_cognitions"][0].update(
        {
            "target": {"kind": "entity", "id": "person:user"},
            "content_type": "fact",
            "perspective": None,
        }
    )

    delta = WorldExtractor(
        ScriptedLLM([json.dumps(raw, ensure_ascii=False)])
    ).extract(base, turns, {"turn:user"})

    assert not delta.new_entities
    assert len(delta.new_cognitions) == len(delta.formation_traces) == 1
    cognition = delta.new_cognitions[0]
    trace = delta.formation_traces[0]
    assert cognition.target == MemoryTarget("entity", "animal:pet-cat")
    assert cognition.content == content
    assert all(token in cognition.content for token in ("二五", "小母猫", "3岁"))
    assert cognition.content_type == "fact"
    assert cognition.formed_by == trace.derived_formed_by == "stated"
    assert cognition.perspective == Perspective("entity", ("person:user",))
    assert cognition.sources[0].evidence_id == "turn:user"
    assert trace.model_inferred_proposal is False
    assert trace.sources[0].local_origin_decision == "exact_user_claim"
    assert trace.sources[0].preceding_assistant_turn_id == "turn:assistant"
    assert "turn:assistant" not in {
        source.evidence_id for source in cognition.sources
    }


def test_stale_optional_event_does_not_block_trusted_accepted_pet_direct_fallback() -> None:
    base = _base()
    base.add_entity(Entity("animal:pet-cat", "world:yun", "animal", "小猫"))
    base.add_relationship(
        Relationship(
            "relationship:user-pet-cat",
            "world:yun",
            "person:user",
            "animal:pet-cat",
            "owns",
            False,
        )
    )
    content = "她叫二五，是一只小母猫，今年都3岁了"
    turns = [
        ConversationTurn(
            "turn:pet-introduction",
            "conversation:one",
            "user",
            "我有一只小猫",
            "2026-08-10T15:15:34+08:00",
        ),
        ConversationTurn(
            "turn:assistant",
            "conversation:one",
            "assistant",
            "它叫什么名字呀？",
            "2026-08-10T15:15:59+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-10T15:16:23+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("turn:user")]))
    raw["new_cognitions"][0].update(
        {
            "id": "cog:pet-details",
            "target": {"kind": "entity", "id": "animal:pet-cat"},
            "content_type": "fact",
            "perspective": None,
        }
    )
    raw["new_events"] = [
        {
            "id": "event:stale-pet-introduction",
            "world_id": "world:yun",
            "event_type": "pet_profile",
            "summary": "我有一只小猫",
            "occurred_at": "2026-08-10T15:15:34+08:00",
            "participants": [
                {"entity_id": "animal:pet-cat", "role": "pet"},
            ],
            "related_entity_ids": [],
            "relationship_ids": ["relationship:user-pet-cat"],
            "facets": [],
            "evidence_ids": ["turn:pet-introduction"],
        }
    ]
    encoded = json.dumps(raw, ensure_ascii=False)
    llm = ScriptedLLM([encoded, encoded])

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 2
    assert not delta.new_events
    assert not delta.new_entities
    assert not delta.new_relationships
    assert len(delta.new_cognitions) == len(delta.formation_traces) == 1
    cognition = delta.new_cognitions[0]
    trace = delta.formation_traces[0]
    assert cognition.target == MemoryTarget("entity", "animal:pet-cat")
    assert cognition.content == content
    assert cognition.content_type == "fact"
    assert cognition.formed_by == trace.derived_formed_by == "stated"
    assert cognition.perspective == Perspective("entity", ("person:user",))
    assert cognition.sources == (cognition.sources[0],)
    assert cognition.sources[0].evidence_id == "turn:user"
    assert trace.cognition_id == cognition.id
    assert trace.model_inferred_proposal is False
    assert trace.sources[0].evidence_id == "turn:user"
    assert trace.sources[0].local_origin_decision == "exact_user_claim"
    encoded_delta = json.dumps(asdict(delta), ensure_ascii=False)
    assert "turn:pet-introduction" not in encoded_delta
    assert base.events == {}
    assert base.cognitions == {}


def test_explicit_owner_third_party_introduction_materializes_reviewable_coverage_when_model_is_empty() -> None:
    content = "我有一个喜欢的人，她给我点了一份甜点，你觉得贵吗？"
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:00:00+08:00",
        ),
    ]
    base = _base()
    repaired = _payload(
        new_entities=[
            {
                "id": "person:romantic-contact",
                "world_id": "world:yun",
                "kind": "person",
                "canonical_name": "喜欢的人",
                "aliases": [],
            }
        ],
        new_relationships=[
            {
                "id": "relationship:user-romantic-contact",
                "world_id": "world:yun",
                "source_entity_id": "person:user",
                "target_entity_id": "person:romantic-contact",
                "relation_type": "romantic_interest",
                "bidirectional": False,
            }
        ],
        new_events=[
            {
                "id": "event:dessert-order",
                "world_id": "world:yun",
                "event_type": "lived_occurrence",
                "summary": "喜欢的人给用户点了一份甜点。",
                "occurred_at": "2026-08-09T10:00:00+08:00",
                "participants": [
                    {"entity_id": "person:user", "role": "recipient"},
                    {"entity_id": "person:romantic-contact", "role": "actor"},
                ],
                "related_entity_ids": [],
                "relationship_ids": ["relationship:user-romantic-contact"],
                "facets": [],
                "evidence_ids": ["turn:user"],
            }
        ],
        new_cognitions=[
            {
                "id": "cog:romantic-contact-dessert-order",
                "world_id": "world:yun",
                "target": {"kind": "entity", "id": "person:romantic-contact"},
                "content": None,
                "content_type": "fact",
                "model_inferred": False,
                "perspective": None,
                "sources": [_source("turn:user")],
                "scope": None,
                "valid_at": None,
                "invalid_at": None,
            }
        ],
    )
    llm = ScriptedLLM([_payload(new_entities=[]), repaired])

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 2
    assert "OWNER_THIRD_PARTY_COVERAGE_EMPTY@$.new_relationships" in llm.messages[1][-1].content
    assert len(delta.new_entities) == 1
    third_party = delta.new_entities[0]
    assert (third_party.kind, third_party.canonical_name) == ("person", "喜欢的人")
    assert len(delta.new_relationships) == 1
    relationship = delta.new_relationships[0]
    assert (
        relationship.source_entity_id,
        relationship.target_entity_id,
        relationship.relation_type,
    ) == ("person:user", third_party.id, "romantic_interest")
    assert len(delta.new_events) == 1
    event = delta.new_events[0]
    assert event.summary == "喜欢的人给用户点了一份甜点。"
    assert {participant.entity_id for participant in event.participants} == {
        "person:user",
        third_party.id,
    }
    assert event.relationship_ids == (relationship.id,)
    assert event.evidence_ids == ("turn:user",)
    assert len(delta.new_cognitions) == len(delta.formation_traces) == 1
    cognition = delta.new_cognitions[0]
    trace = delta.formation_traces[0]
    assert cognition.target.id == third_party.id
    assert cognition.content == content
    assert cognition.formed_by == trace.derived_formed_by == "stated"
    assert cognition.perspective.holder_entity_ids == ("person:user",)
    assert trace.model_inferred_proposal is False
    assert trace.sources[0].local_origin_decision == "exact_user_claim"
    assert base.entities == {"person:user": Entity("person:user", "world:yun", "person", "User")}


def test_explicit_owner_third_party_introduction_uses_trusted_fallback_after_two_empty_repairs() -> None:
    content = "我有一个喜欢的女生，她给我点了一份水果拼盘68，你觉得贵吗"
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:00:00+08:00",
        ),
    ]
    base = _base()
    empty = _payload(new_entities=[])
    llm = ScriptedLLM([empty, empty])

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 2
    assert len(delta.new_entities) == 1
    third_party = delta.new_entities[0]
    assert (third_party.kind, third_party.canonical_name, third_party.aliases) == (
        "person",
        "喜欢的女生",
        (),
    )
    assert len(delta.new_relationships) == 1
    relationship = delta.new_relationships[0]
    assert (
        relationship.source_entity_id,
        relationship.target_entity_id,
        relationship.relation_type,
        relationship.bidirectional,
    ) == ("person:user", third_party.id, "romantic_interest", False)
    assert len(delta.new_events) == 1
    event = delta.new_events[0]
    assert event.summary == "她给我点了一份水果拼盘68"
    assert {participant.entity_id for participant in event.participants} == {
        "person:user",
        third_party.id,
    }
    assert event.relationship_ids == (relationship.id,)
    assert event.evidence_ids == ("turn:user",)
    assert not delta.new_cognitions
    assert not delta.formation_traces
    assert base.entities == {"person:user": Entity("person:user", "world:yun", "person", "User")}


@pytest.mark.parametrize("content", ["她喜欢稳定。", "她反正很温柔。"])
def test_mislabeled_inferred_direct_third_party_statement_uses_unique_person_context(
    content: str,
) -> None:
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的人"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我最近一直在和喜欢的人相处。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:assistant",
            "conversation:one",
            "assistant",
            "明白了。",
            "2026-08-09T10:00:30+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("turn:user")], model_inferred=True))
    raw["new_cognitions"][0]["target"] = {"kind": "entity", "id": "person:user"}
    raw["new_cognitions"][0]["perspective"] = None
    llm = ScriptedLLM([json.dumps(raw, ensure_ascii=False)])

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 1
    cognition = delta.new_cognitions[0]
    trace = delta.formation_traces[0]
    assert cognition.target.id == "person:context-person"
    assert cognition.content == content
    assert cognition.formed_by == trace.derived_formed_by == "stated"
    assert cognition.perspective.holder_entity_ids == ("person:user",)
    assert trace.model_inferred_proposal is False
    assert trace.sources[0].local_origin_decision == "exact_user_claim"


def test_cross_session_accepted_reference_binds_a_current_pronoun_without_old_turn_replay() -> None:
    base = _base()
    base.add_entity(Entity("person:known-across-sessions", "world:yun", "person", "小林"))
    current_content = "她喜欢稳定。"
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:two",
            "user",
            current_content,
            "2026-08-10T10:00:00+08:00",
        ),
    ]
    history = (
        AcceptedEntityReference(
            "person:known-across-sessions",
            "小林",
            "evidence:session-one",
            "conversation:one",
            "2026-08-09T10:00:00+08:00",
            "user",
            continuity_id="continuity:known-person",
        ),
    )
    raw = json.loads(_cognition_payload([_source("turn:user")], model_inferred=True))
    raw["new_cognitions"][0]["target"] = {"kind": "entity", "id": "person:user"}
    raw["new_cognitions"][0]["perspective"] = None
    llm = ScriptedLLM([json.dumps(raw, ensure_ascii=False)])

    delta = WorldExtractor(llm).extract(
        base,
        turns,
        {"turn:user"},
        accepted_entity_references=history,
        reference_continuity_id="continuity:known-person",
    )

    assert llm.calls == 1
    cognition = delta.new_cognitions[0]
    trace = delta.formation_traces[0]
    assert cognition.target == MemoryTarget("entity", "person:known-across-sessions")
    assert cognition.content == current_content
    assert cognition.formed_by == trace.derived_formed_by == "stated"
    assert cognition.perspective == Perspective("entity", ("person:user",))
    assert cognition.sources[0].evidence_id == "turn:user"
    assert "evidence:session-one" not in {
        source.evidence_id for source in cognition.sources
    }
    payload = json.loads(llm.messages[0][1].content)
    assert payload["trusted_third_party_reference"]["entity_id"] == (
        "person:known-across-sessions"
    )
    assert payload["trusted_third_party_reference"]["mention"] == "她"


def test_unresolved_pronoun_requires_the_exact_current_mention_and_evidence() -> None:
    base = _base()
    current_content = "她今天很累。"
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            current_content,
            "2026-08-10T10:00:00+08:00",
        ),
    ]
    wrong = _payload(
        new_entities=[],
        unresolved_references=[
            {"mention": "张三", "evidence_ids": ["turn:user"]},
        ],
    )
    correct = _payload(
        new_entities=[],
        unresolved_references=[
            {"mention": "她", "evidence_ids": ["turn:user"]},
        ],
    )
    llm = ScriptedLLM([wrong, correct])

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 2
    assert delta.unresolved_references[0].mention == "她"
    assert delta.unresolved_references[0].evidence_ids == ("turn:user",)


def test_exact_unresolved_pronoun_does_not_bless_an_extra_unsupported_mention() -> None:
    base = _base()
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "她今天很累。",
            "2026-08-10T10:00:00+08:00",
        ),
    ]
    polluted = _payload(
        new_entities=[],
        unresolved_references=[
            {"mention": "她", "evidence_ids": ["turn:user"]},
            {"mention": "张三", "evidence_ids": ["turn:user"]},
        ],
    )
    exact = _payload(
        new_entities=[],
        unresolved_references=[
            {"mention": "她", "evidence_ids": ["turn:user"]},
        ],
    )
    llm = ScriptedLLM([polluted, exact])

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 2
    assert tuple(
        (reference.mention, reference.evidence_ids)
        for reference in delta.unresolved_references
    ) == (("她", ("turn:user",)),)


def test_short_latin_entity_name_does_not_match_inside_prior_user_words() -> None:
    base = _base()
    base.add_entity(Entity("person:ann", "world:yun", "person", "Ann"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "Planning tomorrow.",
            "2026-08-10T09:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "She is kind.",
            "2026-08-10T10:00:00+08:00",
        ),
    ]
    exact = _payload(
        new_entities=[],
        unresolved_references=[
            {"mention": "She", "evidence_ids": ["turn:user"]},
        ],
    )
    llm = ScriptedLLM([exact])

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 1
    assert delta.new_cognitions == ()
    assert delta.unresolved_references[0].mention == "She"


def test_pronoun_named_entity_cannot_hijack_prior_user_context() -> None:
    base = _base()
    base.add_entity(Entity("person:her", "world:yun", "person", "Her"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "I met her yesterday.",
            "2026-08-10T09:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "She is kind.",
            "2026-08-10T10:00:00+08:00",
        ),
    ]
    exact = _payload(
        new_entities=[],
        unresolved_references=[
            {"mention": "She", "evidence_ids": ["turn:user"]},
        ],
    )

    delta = WorldExtractor(ScriptedLLM([exact])).extract(
        base,
        turns,
        {"turn:user"},
    )

    assert delta.new_cognitions == ()
    assert delta.unresolved_references[0].mention == "She"


def test_explicit_trusted_reference_mode_never_uses_unreviewed_lexical_fallback() -> None:
    base = _base()
    base.add_entity(Entity("person:will", "world:yun", "person", "Will"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "I will travel tomorrow.",
            "2026-08-10T09:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "He is kind.",
            "2026-08-10T10:00:00+08:00",
        ),
    ]
    exact = _payload(
        new_entities=[],
        unresolved_references=[
            {"mention": "He", "evidence_ids": ["turn:user"]},
        ],
    )

    delta = WorldExtractor(ScriptedLLM([exact])).extract(
        base,
        turns,
        {"turn:user"},
        accepted_entity_references=(),
    )

    assert delta.new_cognitions == ()
    assert delta.unresolved_references[0].mention == "He"


def test_unresolved_mentions_on_other_eligible_evidence_must_also_be_grounded() -> None:
    base = _base()
    turns = [
        ConversationTurn(
            "turn:user-pronoun",
            "conversation:one",
            "user",
            "她今天很累。",
            "2026-08-10T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user-weather",
            "conversation:one",
            "user",
            "今天天气不错。",
            "2026-08-10T10:01:00+08:00",
        ),
    ]
    polluted = _payload(
        new_entities=[],
        unresolved_references=[
            {"mention": "她", "evidence_ids": ["turn:user-pronoun"]},
            {"mention": "张三", "evidence_ids": ["turn:user-weather"]},
        ],
    )
    exact = _payload(
        new_entities=[],
        unresolved_references=[
            {"mention": "她", "evidence_ids": ["turn:user-pronoun"]},
        ],
    )
    llm = ScriptedLLM([polluted, exact])

    delta = WorldExtractor(llm).extract(
        base,
        turns,
        {"turn:user-pronoun", "turn:user-weather"},
    )

    assert llm.calls == 2
    assert tuple(reference.mention for reference in delta.unresolved_references) == ("她",)


def test_mixed_reported_direct_clause_is_kept_when_model_mislabels_the_whole_turn_inferred_null() -> None:
    content = "感觉她喜欢我，但是又不喜欢我的感觉，她还说：她喜欢稳定，所以我猜她害怕不确定"
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的女生"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我有一个喜欢的女生。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:assistant",
            "conversation:one",
            "assistant",
            "你可以继续说。",
            "2026-08-09T10:00:30+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("seg-0000")], model_inferred=True))
    raw["new_cognitions"][0].update(
        {
            "target": {"kind": "entity", "id": "person:user"},
            "content_type": "preference",
            "perspective": None,
        }
    )
    llm = ScriptedLLM([json.dumps(raw, ensure_ascii=False)])

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 1
    cognition = delta.new_cognitions[0]
    trace = delta.formation_traces[0]
    assert cognition.target.id == "person:context-person"
    assert cognition.content == "她喜欢稳定"
    assert cognition.formed_by == trace.derived_formed_by == "stated"
    assert cognition.perspective.holder_entity_ids == ("person:user",)
    assert trace.model_inferred_proposal is False
    source = trace.sources[0]
    assert source.evidence_id == "turn:user"
    assert source.local_origin_decision == "exact_user_claim"
    assert (source.claim_span.start_codepoint, source.claim_span.end_codepoint) == (
        content.index("她喜欢稳定"),
        content.index("她喜欢稳定") + len("她喜欢稳定"),
    )
    assert all("害怕不确定" not in candidate.content for candidate in delta.new_cognitions)
    payload = json.loads(llm.messages[0][1].content)
    assert payload["trusted_third_party_reference"] == {
        "state": "resolved",
        "segment_id": "seg-0001",
        "entity_id": "person:context-person",
        "mention": "她",
        "authority": "accepted BASE plus prior user context only; target binding, never Evidence",
    }
    assert payload["eligible_evidence_segments"] == [
        {"segment_id": "seg-0000", "evidence_id": "turn:user", "text": content},
        {"segment_id": "seg-0001", "evidence_id": "turn:user", "text": "她喜欢稳定"},
    ]


def test_exact_reported_preference_span_collapses_a_duplicate_trait_classification() -> None:
    content = "她还说：她喜欢稳定，所以我猜她害怕不确定"
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的人"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我之前提到过喜欢的人。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    payload = json.loads(_cognition_payload([_source("seg-0000")], model_inferred=True))
    preference = payload["new_cognitions"][0]
    preference.update(
        {
            "id": "cog:reported-preference",
            "target": {"kind": "entity", "id": "person:user"},
            "content_type": "preference",
            "perspective": None,
            "scope": "stability",
        }
    )
    duplicate_trait = copy.deepcopy(preference)
    duplicate_trait.update(
        {
            "id": "cog:reported-trait",
            "content_type": "trait",
            "scope": None,
        }
    )
    payload["new_cognitions"] = [preference, duplicate_trait]

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload, ensure_ascii=False)])).extract(
        base, turns, {"turn:user"},
    )

    assert [(value.id, value.content_type, value.scope) for value in delta.new_cognitions] == [
        ("cog:reported-preference", "preference", None),
    ]
    assert [trace.cognition_id for trace in delta.formation_traces] == [
        "cog:reported-preference",
    ]
    cognition = delta.new_cognitions[0]
    trace = delta.formation_traces[0]
    assert cognition.content == "她喜欢稳定"
    assert cognition.target == MemoryTarget("entity", "person:context-person")
    assert cognition.perspective == Perspective("entity", ("person:user",))
    assert trace.sources[0].claim_span.start_codepoint == content.index("她喜欢稳定")


@pytest.mark.parametrize(
    ("first_type", "first_scope", "second_type", "second_scope"),
    [
        ("preference", "stability", "preference", None),
        ("trait", "relationship", "fact", None),
    ],
    ids=["two-preferences-with-different-scopes", "no-model-preference-label"],
)
def test_exact_preference_claim_is_canonical_across_nondeterministic_model_shapes(
    first_type: str,
    first_scope: str | None,
    second_type: str,
    second_scope: str | None,
) -> None:
    content = "她还说：她喜欢稳定，所以我猜她害怕不确定"
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的人"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我之前提到过喜欢的人。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    payload = json.loads(_cognition_payload([_source("seg-0000")], model_inferred=True))
    first = payload["new_cognitions"][0]
    first.update(
        {
            "id": "cog:z-model-shape",
            "target": {"kind": "entity", "id": "person:user"},
            "content_type": first_type,
            "perspective": None,
            "scope": first_scope,
        }
    )
    second = copy.deepcopy(first)
    second.update(
        {
            "id": "cog:a-model-shape",
            "content_type": second_type,
            "scope": second_scope,
        }
    )
    payload["new_cognitions"] = [first, second]

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload, ensure_ascii=False)])).extract(
        base, turns, {"turn:user"},
    )

    assert len(delta.new_cognitions) == len(delta.formation_traces) == 1
    cognition = delta.new_cognitions[0]
    trace = delta.formation_traces[0]
    assert (cognition.id, trace.cognition_id) == (
        "cog:a-model-shape",
        "cog:a-model-shape",
    )
    assert cognition.content == "她喜欢稳定"
    assert cognition.content_type == "preference"
    assert cognition.scope is None
    assert cognition.target == MemoryTarget("entity", "person:context-person")
    assert cognition.perspective == Perspective("entity", ("person:user",))
    assert trace.sources[0].claim_span.start_codepoint == content.index("她喜欢稳定")


def test_duplicate_negative_preference_claim_is_not_canonicalized_as_positive() -> None:
    content = "她还说：她不喜欢冒险，所以我猜她比较谨慎"
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的人"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我之前提到过喜欢的人。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    payload = json.loads(_cognition_payload([_source("seg-0000")], model_inferred=True))
    first = payload["new_cognitions"][0]
    first.update(
        {
            "id": "cog:negative-one",
            "target": {"kind": "entity", "id": "person:user"},
            "content_type": "preference",
            "perspective": None,
        }
    )
    second = copy.deepcopy(first)
    second["id"] = "cog:negative-two"
    payload["new_cognitions"] = [first, second]
    encoded = json.dumps(payload, ensure_ascii=False)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([encoded, encoded])).extract(
            base, turns, {"turn:user"},
        )

    assert raised.value.codes == (
        "DIRECT_COGNITION_DUPLICATE_UNSAFE@$.new_cognitions",
    ) * 2


def test_same_exact_direct_span_with_no_unique_semantic_classification_fails_closed() -> None:
    content = "她还说：她做事靠谱，所以我猜她比较谨慎"
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的人"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我之前提到过喜欢的人。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    payload = json.loads(_cognition_payload([_source("seg-0000")], model_inferred=True))
    fact = payload["new_cognitions"][0]
    fact.update(
        {
            "id": "cog:reported-fact",
            "target": {"kind": "entity", "id": "person:user"},
            "content_type": "fact",
            "perspective": None,
        }
    )
    trait = copy.deepcopy(fact)
    trait.update({"id": "cog:reported-trait", "content_type": "trait"})
    payload["new_cognitions"] = [fact, trait]
    encoded = json.dumps(payload, ensure_ascii=False)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([encoded, encoded])).extract(
            base, turns, {"turn:user"},
        )

    assert raised.value.codes == (
        "DIRECT_COGNITION_DUPLICATE_AMBIGUOUS@$.new_cognitions",
    ) * 2


def test_same_id_direct_duplicates_are_not_collapsed_before_create_only_validation() -> None:
    content = "她还说：她喜欢稳定，所以我猜她害怕不确定"
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的人"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我之前提到过喜欢的人。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    payload = json.loads(_cognition_payload([_source("seg-0000")], model_inferred=True))
    first = payload["new_cognitions"][0]
    first.update(
        {
            "id": "cog:reported-duplicate",
            "target": {"kind": "entity", "id": "person:user"},
            "content_type": "preference",
            "perspective": None,
        }
    )
    payload["new_cognitions"] = [first, copy.deepcopy(first)]
    encoded = json.dumps(payload, ensure_ascii=False)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([encoded, encoded])).extract(
            base, turns, {"turn:user"},
        )

    assert raised.value.attempts == 2
    assert all("cognition[1].id.duplicate" in code for code in raised.value.codes)


def test_mixed_reported_direct_clause_does_not_downgrade_a_separate_supported_guess() -> None:
    content = "她还说：她喜欢稳定，所以我猜她害怕不确定"
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的人"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我之前提到过喜欢的人。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    payload = json.loads(_cognition_payload([_source("seg-0000")], model_inferred=True))
    direct = payload["new_cognitions"][0]
    direct.update(
        {
            "id": "cog:reported-direct",
            "target": {"kind": "entity", "id": "person:user"},
            "content_type": "preference",
            "perspective": None,
        }
    )
    inferred = copy.deepcopy(direct)
    inferred.update(
        {
            "id": "cog:reported-guess",
            "target": {"kind": "entity", "id": "person:context-person"},
            "content": "她可能害怕不确定",
            "content_type": "hypothesis",
            "model_inferred": True,
            "perspective": {"kind": "entity", "holder_entity_ids": ["person:user"]},
        }
    )
    payload["new_cognitions"] = [direct, inferred]

    delta = WorldExtractor(ScriptedLLM([json.dumps(payload, ensure_ascii=False)])).extract(
        base, turns, {"turn:user"},
    )

    assert [(value.content, value.formed_by) for value in delta.new_cognitions] == [
        ("她喜欢稳定", "stated"),
        ("她可能害怕不确定", "inferred"),
    ]
    assert [trace.model_inferred_proposal for trace in delta.formation_traces] == [False, True]


def test_optional_inferred_null_does_not_block_an_exact_reported_third_party_action() -> None:
    content = "我也不知道她喜不喜欢我，因为她还说之前给一个不认识的网友还买过衣服"
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的女生"))
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我有一个喜欢的女生。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("seg-0000")], model_inferred=True))
    raw["new_cognitions"][0].update(
        {
            "target": {"kind": "entity", "id": "person:context-person"},
            "content_type": "hypothesis",
            "perspective": None,
        }
    )
    encoded = json.dumps(raw, ensure_ascii=False)
    llm = ScriptedLLM([encoded, encoded])

    delta = WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert llm.calls == 2
    assert not delta.new_cognitions
    assert not delta.formation_traces
    assert len(delta.new_events) == 1
    event = delta.new_events[0]
    assert event.summary == "她还说之前给一个不认识的网友还买过衣服"
    assert [(participant.entity_id, participant.role) for participant in event.participants] == [
        ("person:context-person", "actor"),
    ]
    assert event.evidence_ids == ("turn:user",)
    assert len(delta.unresolved_references) == 1
    assert delta.unresolved_references[0].mention == "一个不认识的网友"
    assert delta.unresolved_references[0].evidence_ids == ("turn:user",)
    assert delta.semantic_uncertainties[0].detail == (
        "OPTIONAL_INFERENCE_QUARANTINED:INFERRED_CONTENT_REQUIRED"
    )
    assert all("不知道她喜不喜欢我" not in cognition.content for cognition in delta.new_cognitions)


def test_base_cognition_id_collision_is_remapped_without_changing_current_direct_claim() -> None:
    content = "她反正很温柔"
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的女生"))
    base.add_cognition(
        WorldCognition(
            "cog:reused-by-local-model",
            "world:yun",
            MemoryTarget("entity", "person:context-person"),
            "她喜欢稳定",
            "preference",
            "stated",
            600,
            "limited",
            Perspective("entity", ("person:user",)),
        )
    )
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我有一个喜欢的女生。",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:01:00+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("seg-0000")], model_inferred=True))
    raw["new_cognitions"][0].update(
        {
            "id": "cog:reused-by-local-model",
            "target": {"kind": "entity", "id": "person:context-person"},
            "content_type": "trait",
            "perspective": None,
        }
    )
    encoded = json.dumps(raw, ensure_ascii=False)

    first = WorldExtractor(ScriptedLLM([encoded])).extract(base, turns, {"turn:user"})
    second = WorldExtractor(ScriptedLLM([encoded])).extract(base, turns, {"turn:user"})

    assert first.new_cognitions == second.new_cognitions
    cognition = first.new_cognitions[0]
    trace = first.formation_traces[0]
    assert cognition.id == trace.cognition_id
    assert cognition.id.startswith("cog:extracted-")
    assert cognition.id not in base.cognitions
    assert cognition.target == MemoryTarget("entity", "person:context-person")
    assert cognition.content == content
    assert cognition.content_type == "trait"
    assert cognition.formed_by == "stated"
    assert cognition.perspective == Perspective("entity", ("person:user",))
    assert cognition.sources[0].evidence_id == "turn:user"
    assert trace.sources[0].local_origin_decision == "exact_user_claim"
    assert first.semantic_uncertainties[0].detail == (
        "COGNITION_ID_REKEYED_AFTER_BASE_COLLISION"
    )
    assert first.semantic_uncertainties[0].evidence_ids == ("turn:user",)
    assert set(base.cognitions) == {"cog:reused-by-local-model"}


@pytest.mark.parametrize(
    "extra_people",
    [[], [Entity("person:second-context", "world:yun", "person", "另一个人")]],
    ids=["no-third-party", "ambiguous-third-parties"],
)
def test_mislabeled_pronoun_claim_without_one_unique_person_fails_closed(
    extra_people: list[Entity],
) -> None:
    base = _base()
    if extra_people:
        base.add_entity(Entity("person:first-context", "world:yun", "person", "第一个人"))
    for entity in extra_people:
        base.add_entity(entity)
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "第一个人和另一个人都在之前的聊天里出现过。" if extra_people else "之前聊到过一件事。",
            "2026-08-09T10:01:30+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "她很温柔。",
            "2026-08-09T10:02:00+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("turn:user")], model_inferred=True))
    raw["new_cognitions"][0]["target"] = {"kind": "entity", "id": "person:user"}
    raw["new_cognitions"][0]["perspective"] = None
    encoded = json.dumps(raw, ensure_ascii=False)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([encoded, encoded])).extract(base, turns, {"turn:user"})

    assert raised.value.codes == ("INFERRED_CONTENT_REQUIRED@$.new_cognitions[0].content",) * 2


def test_mixed_third_party_guess_is_not_normalized_into_a_direct_claim() -> None:
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的人"))
    content = "她喜欢稳定，所以我猜她害怕不确定。"
    turns = [
        ConversationTurn(
            "turn:context",
            "conversation:one",
            "user",
            "我之前提到过喜欢的人。",
            "2026-08-09T10:02:30+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            content,
            "2026-08-09T10:03:00+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("turn:user")], model_inferred=True))
    raw["new_cognitions"][0]["target"] = {"kind": "entity", "id": "person:context-person"}
    raw["new_cognitions"][0]["perspective"] = None
    encoded = json.dumps(raw, ensure_ascii=False)

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(ScriptedLLM([encoded, encoded])).extract(base, turns, {"turn:user"})

    assert raised.value.codes == ("INFERRED_CONTENT_REQUIRED@$.new_cognitions[0].content",) * 2


def test_assistant_third_party_statement_cannot_supply_direct_evidence_for_a_user_carrier() -> None:
    base = _base()
    base.add_entity(Entity("person:context-person", "world:yun", "person", "喜欢的人"))
    turns = [
        ConversationTurn(
            "turn:assistant",
            "conversation:one",
            "assistant",
            "你说的喜欢的人很温柔。",
            "2026-08-09T10:04:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "她很温柔。",
            "2026-08-09T10:04:01+08:00",
        ),
    ]
    raw = json.loads(_cognition_payload([_source("turn:user")]))
    raw["new_cognitions"][0]["target"] = {"kind": "entity", "id": "person:context-person"}
    raw["new_cognitions"][0]["perspective"] = None
    encoded = json.dumps(raw, ensure_ascii=False)
    llm = ScriptedLLM([encoded, encoded])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(base, turns, {"turn:user"})

    assert raised.value.codes == ("THIRD_PARTY_REFERENCE_UNRESOLVED@$.unresolved_references",) * 2
    assert "do not select or invent an Entity; emit one unresolved_reference" in (
        llm.messages[1][-1].content
    )


def _multi_role_new_person_payload(
    *,
    canonical_name: str,
    relation_type: str,
    mention: str,
) -> str:
    source = _source("seg-0000")
    return _payload(
        new_entities=[
            {
                "id": "person:private-contact",
                "world_id": "world:yun",
                "kind": "person",
                "canonical_name": canonical_name,
                "aliases": [],
            }
        ],
        new_relationships=[
            {
                "id": "relationship:user-private-contact",
                "world_id": "world:yun",
                "source_entity_id": "person:user",
                "target_entity_id": "person:private-contact",
                "relation_type": relation_type,
                "bidirectional": False,
            }
        ],
        new_cognitions=[
            {
                "id": "cog:private-contact-preference",
                "world_id": "world:yun",
                "target": {"kind": "entity", "id": "person:private-contact"},
                "content": None,
                "content_type": "preference",
                "model_inferred": False,
                "perspective": None,
                "sources": [source],
                "scope": None,
                "valid_at": None,
                "invalid_at": None,
            },
            {
                "id": "cog:owner-chosen-name",
                "world_id": "world:yun",
                "target": {"kind": "entity", "id": "person:user"},
                "content": None,
                "content_type": "fact",
                "model_inferred": False,
                "perspective": None,
                "sources": [source],
                "scope": None,
                "valid_at": None,
                "invalid_at": None,
            },
        ],
        unresolved_references=[
            {"mention": mention, "evidence_ids": ["turn:earlier"]},
        ],
    )


@pytest.mark.parametrize(
    (
        "earlier_content",
        "clarification",
        "canonical_name",
        "relation_type",
        "mention",
        "expected_third_party_claim",
        "expected_owner_claim",
    ),
    [
        (
            "她偏爱海盐面包，所以我把昵称改成小盐。",
            "我指的是大学室友，名字暂时不说。",
            "大学室友",
            "roommate",
            "她",
            "她偏爱海盐面包",
            "所以我把昵称改成小盐。",
        ),
        (
            "他爱听爵士乐所以我的游戏名叫蓝调",
            "说的是以前一起排练的乐队搭档，姓名先保密。",
            "乐队搭档",
            "friend",
            "他",
            "他爱听爵士乐",
            "所以我的游戏名叫蓝调",
        ),
        (
            "She prefers cedar tea, so my handle is Cedar.",
            "I mean a former lab partner; the name stays private.",
            "former lab partner",
            "friend",
            "She",
            "She prefers cedar tea",
            "so my handle is Cedar.",
        ),
    ],
    ids=["chinese-comma", "chinese-no-comma", "english-paraphrase"],
)
def test_carried_reference_and_clarification_keep_third_party_and_owner_claims_separate(
    earlier_content: str,
    clarification: str,
    canonical_name: str,
    relation_type: str,
    mention: str,
    expected_third_party_claim: str,
    expected_owner_claim: str,
) -> None:
    base = _base()
    turns = [
        ConversationTurn(
            "turn:earlier",
            "conversation:one",
            "user",
            earlier_content,
            "2026-08-11T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:assistant",
            "conversation:one",
            "assistant",
            "Who do you mean?",
            "2026-08-11T10:00:01+08:00",
        ),
        ConversationTurn(
            "turn:current",
            "conversation:one",
            "user",
            clarification,
            "2026-08-11T10:01:00+08:00",
        ),
    ]
    payload = _multi_role_new_person_payload(
        canonical_name=canonical_name,
        relation_type=relation_type,
        mention=mention,
    )

    delta = WorldExtractor(ScriptedLLM([payload])).extract(
        base,
        turns,
        {"turn:earlier", "turn:current"},
    )

    assert delta.unresolved_references == ()
    assert delta.source_evidence_ids == ("turn:earlier", "turn:current")
    assert delta.new_entities[0].canonical_name == canonical_name
    assert delta.new_relationships[0].relation_type == relation_type
    cognitions = {cognition.target.id: cognition for cognition in delta.new_cognitions}
    assert cognitions["person:private-contact"].content == expected_third_party_claim
    assert cognitions["person:user"].content == expected_owner_claim
    assert all(
        cognition.perspective == Perspective("entity", ("person:user",))
        for cognition in cognitions.values()
    )
    assert all(
        cognition.sources[0].evidence_id == "turn:earlier"
        for cognition in cognitions.values()
    )
    traces = {trace.cognition_id: trace for trace in delta.formation_traces}
    assert (
        traces["cog:private-contact-preference"].sources[0].claim_span.start_codepoint
        == earlier_content.index(expected_third_party_claim)
    )
    assert (
        traces["cog:owner-chosen-name"].sources[0].claim_span.start_codepoint
        == earlier_content.index(expected_owner_claim)
    )
    assert base.entities == {
        "person:user": Entity("person:user", "world:yun", "person", "User"),
    }


def test_mixed_subject_naming_candidate_drops_model_materialized_role_objects() -> None:
    earlier_content = "她偏爱海盐面包，所以我把昵称改成小盐。"
    turns = [
        ConversationTurn(
            "turn:earlier",
            "conversation:one",
            "user",
            earlier_content,
            "2026-08-11T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:assistant",
            "conversation:one",
            "assistant",
            "你说的是谁？",
            "2026-08-11T10:00:01+08:00",
        ),
        ConversationTurn(
            "turn:current",
            "conversation:one",
            "user",
            "我指的是大学室友，名字暂时不说。",
            "2026-08-11T10:01:00+08:00",
        ),
    ]
    payload = json.loads(
        _multi_role_new_person_payload(
            canonical_name="大学室友",
            relation_type="roommate",
            mention="她",
        )
    )
    payload["new_entities"].extend(
        [
            {
                "id": "person:chosen-alias",
                "world_id": "world:yun",
                "kind": "person",
                "canonical_name": "小盐",
                "aliases": [],
            },
            {
                "id": "activity:bread-preference",
                "world_id": "world:yun",
                "kind": "activity",
                "canonical_name": "海盐面包",
                "aliases": [],
            },
            {
                "id": "activity:nickname-label",
                "world_id": "world:yun",
                "kind": "activity",
                "canonical_name": "昵称",
                "aliases": [],
            },
        ]
    )
    payload["new_relationships"].append(
        {
            "id": "relationship:user-chosen-alias",
            "world_id": "world:yun",
            "source_entity_id": "person:user",
            "target_entity_id": "person:chosen-alias",
            "relation_type": "friend",
            "bidirectional": False,
        }
    )
    wrong_owner_claim = copy.deepcopy(payload["new_cognitions"][0])
    wrong_owner_claim.update(
        {
            "id": "cog:owner-wrong-third-party-preference",
            "target": {"kind": "entity", "id": "person:user"},
            "sources": [_source("seg-0002")],
        }
    )
    payload["new_cognitions"][1]["target"] = {
        "kind": "entity",
        "id": "person:chosen-alias",
    }
    payload["new_cognitions"][1]["content_type"] = "preference"
    payload["new_cognitions"][1]["sources"] = [_source("seg-0003")]
    payload["new_cognitions"].append(wrong_owner_claim)
    payload["unresolved_references"] = [
        {"mention": "她", "evidence_ids": ["turn:current"]},
    ]

    delta = WorldExtractor(
        ScriptedLLM([json.dumps(payload, ensure_ascii=False)])
    ).extract(
        _base(),
        turns,
        {"turn:earlier", "turn:current"},
    )

    assert [(entity.id, entity.kind) for entity in delta.new_entities] == [
        ("person:private-contact", "person"),
    ]
    assert [relationship.relation_type for relationship in delta.new_relationships] == [
        "roommate",
    ]
    assert [
        (cognition.target.id, cognition.content, cognition.content_type)
        for cognition in delta.new_cognitions
    ] == [
        ("person:private-contact", "她偏爱海盐面包", "preference"),
        ("person:user", "所以我把昵称改成小盐。", "fact"),
    ]
    assert delta.unresolved_references == ()
    serialized = json.dumps(asdict(delta), ensure_ascii=False)
    assert "person:chosen-alias" not in serialized
    assert "activity:bread-preference" not in serialized
    assert "activity:nickname-label" not in serialized
    assert "cog:owner-wrong-third-party-preference" not in serialized


def test_multiple_third_party_carriers_cannot_be_assigned_to_one_new_person() -> None:
    earlier_content = "她喜欢盐味面包，他喜欢黑咖啡，所以我的昵称叫小盐。"
    turns = [
        ConversationTurn(
            "turn:earlier",
            "conversation:one",
            "user",
            earlier_content,
            "2026-08-11T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:current",
            "conversation:one",
            "user",
            "我说的是大学同学，名字保密。",
            "2026-08-11T10:01:00+08:00",
        ),
    ]
    payload = _multi_role_new_person_payload(
        canonical_name="大学同学",
        relation_type="classmate",
        mention="她",
    )
    llm = ScriptedLLM([payload, payload])

    with pytest.raises(WorldExtractionError) as raised:
        WorldExtractor(llm).extract(
            _base(),
            turns,
            {"turn:earlier", "turn:current"},
        )

    assert raised.value.codes == (
        "THIRD_PARTY_CANDIDATE_BINDING_AMBIGUOUS@$.new_cognitions",
    ) * 2
    assert "Do not assign a comma-coordinated or multi-subject user sentence" in (
        llm.messages[1][-1].content
    )


def test_not_entity_repair_removes_semantic_role_objects_without_relabeling() -> None:
    instruction = _targeted_repair_instruction(
        "DELTA_DOMAIN(entity[2].kind.not_entity)@$",
    )

    assert "Remove every new Entity whose kind is preference, trait, state" in instruction
    assert "Do not repair this by relabeling" in instruction
    assert "exact target-specific cognition" in instruction


@pytest.mark.parametrize(
    "content",
    [
        "我妈平时都是单休。",
        "你猜今天周几？",
        "不对，今天周日。",
        "你好。",
        "我不知道该怎么办。",
        "I have a question.",
    ],
)
def test_non_personal_or_third_party_turns_do_not_require_owner_coverage(content: str) -> None:
    turns = [
        ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-09T10:00:00+08:00"),
    ]
    llm = ScriptedLLM([_payload(new_entities=[])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 1
    assert not delta.new_cognitions


@pytest.mark.parametrize(
    "content",
    [
        "我住上海吗？",
        "Do I live in Shanghai?",
        "我觉得呢？",
    ],
)
def test_pure_self_questions_and_meta_language_do_not_require_owner_coverage(content: str) -> None:
    turns = [
        ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-09T10:00:00+08:00"),
    ]
    llm = ScriptedLLM([_payload(new_entities=[])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 1
    assert not delta.new_cognitions


@pytest.mark.parametrize(
    "content",
    [
        "你还记得吗，我现在住在上海。",
        "我喜欢咖啡，你呢？",
        "我现在很累。",
    ],
)
def test_independent_owner_statement_or_current_state_requires_owner_coverage(content: str) -> None:
    turns = [
        ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-09T10:00:00+08:00"),
    ]
    llm = ScriptedLLM([_payload(new_entities=[]), _cognition_payload([_source("turn:user")])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 2
    assert delta.new_cognitions[0].target.id == "person:user"


def test_self_fact_inside_a_question_requires_owner_coverage() -> None:
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "你觉得我现在住在上海怎么通勤合适？",
            "2026-08-09T10:00:00+08:00",
        ),
    ]
    llm = ScriptedLLM([_payload(new_entities=[]), _cognition_payload([_source("turn:user")])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 2
    assert delta.new_cognitions[0].target.id == "person:user"


def test_mixed_current_exception_and_usual_owner_routine_is_not_silently_empty() -> None:
    turns = [
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "我这个周双休了，因为周六请了一天假，平时都是单休。",
            "2026-08-09T10:00:00+08:00",
        ),
    ]
    llm = ScriptedLLM([_payload(new_entities=[]), _cognition_payload([_source("turn:user")])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 2
    assert delta.new_cognitions[0].target.id == "person:user"


def test_assistant_only_owner_fact_never_activates_the_user_evidence_floor() -> None:
    turns = [
        ConversationTurn(
            "turn:assistant",
            "conversation:one",
            "assistant",
            "I live in Shanghai.",
            "2026-08-09T10:00:00+08:00",
        ),
        ConversationTurn(
            "turn:user",
            "conversation:one",
            "user",
            "好的。",
            "2026-08-09T10:00:01+08:00",
        ),
    ]
    llm = ScriptedLLM([_payload(new_entities=[])])

    delta = WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    assert llm.calls == 1
    assert not delta.new_cognitions


def test_prompt_contract_restores_owner_memory_and_mixed_exception_rules() -> None:
    llm = ScriptedLLM([_payload()])

    WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    system = llm.messages[0][0].content
    assert system.startswith("You are worldExtract@v2")
    assert "explicit user statement about the owner" in system
    assert "time-bounded occurrence, exception, or arrangement" in system
    assert "stable or usual state" in system
    assert "both structures when it explicitly contains both" in system
    assert "pure date/day-of-week correction" in system


@pytest.mark.parametrize(
    ("content", "expected_language"),
    [("A named person prefers a flexible plan.", "en"), ("有人更喜欢开车。", "zh-CN")],
)
def test_payload_derives_evidence_language_from_eligible_evidence(
    content: str, expected_language: str,
) -> None:
    llm = ScriptedLLM([_payload()])
    turns = [
        ConversationTurn("turn:user", "conversation:one", "user", content, "2026-08-08T10:00:00+08:00"),
        ConversationTurn("turn:assistant", "conversation:one", "assistant", "中文上下文", "2026-08-08T10:00:01+08:00"),
    ]

    WorldExtractor(llm).extract(_base(), turns, {"turn:user"})

    payload = json.loads(llm.messages[0][1].content)
    assert payload["evidence_language"] == expected_language
    assert llm.messages[0][0].content.endswith(
        "MANDATORY REQUEST-SPECIFIC OUTPUT LANGUAGE: Every natural-language "
        f"event summary, facet value, and cognition content must be {expected_language}."
    )


def test_response_schema_describes_conflict_facet_key_and_subject_contract() -> None:
    schema = world_delta_response_format()["json_schema"]["schema"]
    key_schema = schema["properties"]["new_events"]["items"]["properties"]["facets"]["items"]["properties"]["key"]

    assert key_schema["description"] == (
        "For an interpersonal_conflict, use only cause or position; "
        "use one position per participant, identified by about_entity_id."
    )


def test_model_cannot_self_declare_the_authoritative_source_set() -> None:
    value = json.loads(_payload())
    value["source_evidence_ids"] = ["turn:assistant"]
    llm = ScriptedLLM([json.dumps(value), _payload()])

    delta = WorldExtractor(llm).extract(_base(), _turns(), {"turn:user"})

    assert llm.calls == 2
    assert delta.source_evidence_ids == ("turn:user",)
    assert "EXTRA_KEYS(source_evidence_ids)@$" in llm.messages[1][-1].content


def test_response_format_matches_the_strict_decoder_shape_and_is_isolated() -> None:
    first = world_delta_response_format()
    schema = first["json_schema"]["schema"]
    assert set(schema["required"]) == {
        "world_id",
        "new_entities",
        "new_relationships",
        "new_events",
        "new_cognitions",
        "unresolved_references",
        "semantic_uncertainties",
    }
    assert schema["additionalProperties"] is False
    assert schema["properties"]["new_entities"]["maxItems"] == 4
    assert schema["properties"]["new_events"]["maxItems"] == 2
    cognition_properties = schema["properties"]["new_cognitions"]["items"]["properties"]
    assert "confidence" not in cognition_properties
    assert "cred_status" not in cognition_properties
    assert "formed_by" not in cognition_properties
    assert cognition_properties["model_inferred"] == {"type": "boolean"}
    assert cognition_properties["content"] == {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}
    nullable_string = {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}
    assert cognition_properties["valid_at"] == nullable_string
    assert cognition_properties["invalid_at"] == nullable_string
    assert cognition_properties["perspective"] == {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["entity", "joint", "system"]},
                    "holder_entity_ids": {
                        "type": "array", "items": {"type": "string", "minLength": 1},
                        "maxItems": 4, "uniqueItems": True,
                    },
                },
                "required": ["kind", "holder_entity_ids"],
                "additionalProperties": False,
            },
            {"type": "null"},
        ]
    }
    assert "relationship_side_segments" not in cognition_properties
    source_properties = cognition_properties["sources"]["items"]["properties"]
    assert set(source_properties) == {"segment_id", "relation", "proposition_origin", "response_act"}
    assert FORMATION_CONTRACT_VERSION == "world-formed-by@12"
    first["json_schema"]["name"] = "mutated"
    assert world_delta_response_format()["json_schema"]["name"] == "memoweft_world_delta_v9"
