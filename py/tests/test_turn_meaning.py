"""Capability-1 contract tests for structured turn interpretation.

The cases deliberately cover open entity kinds.  No result depends on a
person/pet/project word list: the only identity inputs are a sealed accepted
binding, an exact user span, and an opaque handle catalog.
"""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from typing import Any, cast

import pytest

from memoweft.world.extractor import ConversationTurn
from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.identity_review import (
    EntityIdentityDelta,
    IdentityAuthority,
    IdentityEvidence,
)
from memoweft.world.model import (
    Entity,
    EventFacet,
    EventParticipant,
    MemoryTarget,
    PersonalWorld,
    Perspective,
    Relationship,
    StructuredClaim,
    WorldCognition,
    WorldEvent,
)
from memoweft.types import EvidenceLink
from memoweft.world.turn_meaning import (
    MeaningMention,
    MeaningStatement,
    MeaningValueSpan,
    TurnMeaningError,
    TurnMeaningInterpreter,
    TurnMeaningProposal,
    build_accepted_cognition_handles,
    build_accepted_entity_handles,
    compile_product_turn,
    compile_structured_evaluation_correction,
    decode_turn_meaning,
)


def _turn(
    content: str,
    *,
    turn_id: str = "e:current",
    occurred_at: str = "2026-02-01T00:00:00+00:00",
) -> ConversationTurn:
    return ConversationTurn(turn_id, "conversation:one", "user", content, occurred_at)


def _graph() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld("world:one", "entity:owner"))
    graph.add_entity(Entity("entity:owner", "world:one", "person", "Owner"))
    return graph


class _PromptCapture:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def chat(self, messages: list[object]) -> str:
        self.messages = messages
        return json.dumps({"act": "query", "mentions": [], "claims": []})


def _accepted_view(
    graph: MemoryWorldGraph, *items: tuple[str, str, str]
) -> IdentityAuthority:
    """Return an authority with entities bound by accepted user Evidence."""

    for entity_id, kind, name in items:
        graph.add_entity(Entity(entity_id, "world:one", kind, name))
    authority = IdentityAuthority(graph)
    for index, (entity_id, kind, name) in enumerate(items, start=1):
        content = f"我提到{name}"
        evidence_id = f"e:accepted:{index}"
        occurred_at = f"2026-01-0{index}T00:00:00+00:00"
        authority.register_evidence(
            IdentityEvidence(
                evidence_id,
                "world:one",
                "conversation:one",
                occurred_at,
                "user",
                content,
                "conversation:one",
            )
        )
        start = content.index(name)
        mention = authority.issue_verified_mention(
            evidence_id,
            start,
            start + len(name),
            kind_hint=kind,
            continuity_scope="conversation:one",
        )
        review = authority.stage(
            EntityIdentityDelta.bind("world:one", entity_id, mention), {"owner": "test"}
        )
        authority.decide(
            review.review_id,
            review.result_hash,
            "accept",
            f"2026-01-{index + 1:02d}T00:00:00+00:00",
        )
    return authority


def _proposal(
    *,
    content: str,
    mode: str,
    statement_kind: str | None,
    handles: tuple[str, ...] = (),
    kind: str | None = None,
    mention_text: str | None = None,
) -> TurnMeaningProposal:
    mention_text = mention_text or (
        "这个项目" if mode == "refer" else content.split("，", 1)[0]
    )
    mention_start = content.index(mention_text)
    statement = None
    if statement_kind is not None:
        statement_text = content
        value = None
        if statement_kind == "attribute":
            value_text = content[content.index("，") + 1 :]
            value_start = content.index(value_text)
            value = MeaningValueSpan(
                value_text, value_start, value_start + len(value_text)
            )
        statement = MeaningStatement(
            statement_kind,  # type: ignore[arg-type]
            statement_text,
            content.index(statement_text),
            content.index(statement_text) + len(statement_text),
            value,
        )
    return TurnMeaningProposal(
        "assertion",
        MeaningMention(
            mention_text,
            mention_start,
            mention_start + len(mention_text),
            mode,  # type: ignore[arg-type]
            kind,
            handles,
        ),
        statement,
    )


@pytest.mark.parametrize(
    ("kind", "name"),
    (
        ("person", "林先生"),
        ("animal", "阿团"),
        ("object", "那台相机"),
        ("place", "海边小屋"),
        ("organization", "晨星社"),
        ("project", "北极光计划"),
    ),
)
def test_introduction_accepts_open_entity_kinds_without_a_lexical_patch(
    kind: str, name: str
) -> None:
    graph = _graph()
    content = f"{name}，它很重要"
    plan = compile_product_turn(
        proposal=_proposal(
            content=content, mode="introduce", statement_kind="attribute", kind=kind
        ),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=IdentityAuthority(graph).view(),
        operation_key="operation:one",
    )
    assert plan.state == "candidate"
    assert plan.code == "simple_attribute"
    assert plan.delta is not None
    assert len(plan.delta.new_entities) == 1
    assert plan.delta.new_entities[0].kind == kind
    assert plan.delta.new_cognitions[0].perspective.holder_entity_ids == (
        "entity:owner",
    )
    assert plan.identity_bindings[0].evidence_id == "e:current"
    assert plan.identity_bindings[0].start_codepoint == 0


def test_unique_accepted_continuation_uses_same_entity_and_model_cannot_choose_it() -> (
    None
):
    graph = _graph()
    authority = _accepted_view(graph, ("entity:project", "project", "北极光"))
    view = authority.view()
    handles = build_accepted_entity_handles(
        view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
    )
    content = "这个项目，已经延期"
    plan = compile_product_turn(
        proposal=_proposal(
            content=content, mode="refer", statement_kind="attribute", handles=()
        ),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=view.graph.to_graph(),
        handles=handles,
        identity_view=view,
        operation_key="operation:two",
    )
    assert plan.state == "candidate"
    assert plan.resolved_entity_id == "entity:project"
    assert plan.delta is not None
    assert plan.delta.new_entities == ()
    assert plan.delta.new_cognitions[0].target.id == "entity:project"


def test_two_grounded_antecedents_require_clarification_even_if_model_lists_one_handle() -> (
    None
):
    graph = _graph()
    authority = _accepted_view(
        graph,
        ("entity:project-a", "project", "甲计划"),
        ("entity:project-b", "project", "乙计划"),
    )
    view = authority.view()
    handles = tuple(
        replace(item, recent_in_conversation=True)
        for item in build_accepted_entity_handles(
            view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
        )
    )
    content = "这个项目，已经延期"
    plan = compile_product_turn(
        proposal=_proposal(
            content=content,
            mode="refer",
            statement_kind="attribute",
            handles=(handles[0].handle,),
        ),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=view.graph.to_graph(),
        handles=handles,
        identity_view=view,
        operation_key="operation:three",
    )
    assert (plan.state, plan.code) == ("clarification_required", "referent_ambiguous")
    assert set(plan.candidate_entity_names) == {"甲计划", "乙计划"}


def test_zero_grounded_antecedents_requires_clarification() -> None:
    graph = _graph()
    view = IdentityAuthority(graph).view()
    content = "这个项目，已经延期"
    plan = compile_product_turn(
        proposal=_proposal(content=content, mode="refer", statement_kind="attribute"),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=view,
        operation_key="operation:four",
    )
    assert (plan.state, plan.code, plan.delta) == (
        "clarification_required",
        "referent_unresolved",
        None,
    )


def test_unknown_handle_and_span_mismatch_are_rejected_before_a_candidate_is_created() -> (
    None
):
    graph = _graph()
    view = IdentityAuthority(graph).view()
    with pytest.raises(TurnMeaningError, match="unknown_accepted_handle"):
        compile_product_turn(
            proposal=_proposal(
                content="这个项目，已经延期",
                mode="refer",
                statement_kind="attribute",
                handles=("accepted:invented",),
            ),
            current_user_turn=_turn("这个项目，已经延期"),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=graph,
            handles=(),
            identity_view=view,
            operation_key="operation:five",
        )
    with pytest.raises(TurnMeaningError, match="mention_span_mismatch"):
        decode_turn_meaning(
            '{"act":"assertion","mention":{"text":"假的","start":0,"end":2,"mode":"introduce","kind_hint":"project","accepted_handles":[]},"statement":null}',
            "真实项目",
        )


def test_model_span_indices_are_reanchored_only_when_exact_text_is_unique() -> None:
    content = "我最近在做一项叫雾桥的长期计划。"
    decoded = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mention": {
                    "text": "雾桥",
                    "start": 6,
                    "end": 9,
                    "mode": "introduce",
                    "kind_hint": "project",
                    "accepted_handles": [],
                },
                "statement": {
                    "kind": "naming",
                    "text": content,
                    "start": 0,
                    "end": 21,
                    "value": None,
                },
            },
            ensure_ascii=False,
        ),
        content,
    )
    assert decoded.mention is not None
    assert (decoded.mention.start, decoded.mention.end) == (
        content.index("雾桥"),
        content.index("雾桥") + len("雾桥"),
    )
    assert decoded.statement is not None
    assert (decoded.statement.start, decoded.statement.end) == (0, len(content))

    with pytest.raises(TurnMeaningError, match="mention_span_mismatch"):
        decode_turn_meaning(
            '{"act":"assertion","mention":{"text":"项目","start":0,"end":1,"mode":"refer","kind_hint":"project","accepted_handles":[]},"statement":null}',
            "这个项目不是那个项目",
        )


def test_attribute_value_span_is_reanchored_and_cannot_be_the_refer_subject() -> None:
    content = "这个项目是深蓝色"
    decoded = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mention": {
                    "text": "这个项目",
                    "start": 0,
                    "end": 4,
                    "mode": "refer",
                    "kind_hint": "project",
                    "accepted_handles": [],
                },
                "statement": {
                    "kind": "attribute",
                    "text": content,
                    "start": 0,
                    "end": len(content),
                    "value": {"text": "深蓝色", "start": 0, "end": 3},
                },
            },
            ensure_ascii=False,
        ),
        content,
    )
    assert decoded.statement is not None and decoded.statement.value is not None
    assert (decoded.statement.value.start, decoded.statement.value.end) == (5, 8)

    with pytest.raises(TurnMeaningError, match="attribute_mention_value_overlap"):
        decode_turn_meaning(
            json.dumps(
                {
                    "act": "assertion",
                    "mention": {
                        "text": "深蓝色",
                        "start": 5,
                        "end": 8,
                        "mode": "refer",
                        "kind_hint": "project",
                        "accepted_handles": [],
                    },
                    "statement": {
                        "kind": "attribute",
                        "text": content,
                        "start": 0,
                        "end": len(content),
                        "value": {"text": "深蓝色", "start": 5, "end": 8},
                    },
                },
                ensure_ascii=False,
            ),
            content,
        )


def test_predicate_value_statement_is_hulled_with_subject_for_the_evidence_claim() -> (
    None
):
    graph = _graph()
    authority = _accepted_view(graph, ("entity:project", "project", "北极光"))
    view = authority.view()
    handles = build_accepted_entity_handles(
        view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
    )
    content = "上次那个项目主色是深蓝色"
    mention_text = "上次那个项目"
    statement_text = "主色是深蓝色"
    value_text = "深蓝色"
    proposal = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mention": {
                    "text": mention_text,
                    "start": 99,
                    "end": 100,
                    "mode": "refer",
                    "kind_hint": "project",
                    "accepted_handles": [handles[0].handle],
                },
                "statement": {
                    "kind": "attribute",
                    "text": statement_text,
                    "start": 99,
                    "end": 100,
                    "value": {"text": value_text, "start": 99, "end": 100},
                },
            },
            ensure_ascii=False,
        ),
        content,
    )
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=view.graph.to_graph(),
        handles=handles,
        identity_view=view,
        operation_key="operation:predicate-value",
    )
    assert plan.state == "candidate"
    assert plan.delta is not None
    cognition = plan.delta.new_cognitions[0]
    assert cognition.content == content
    trace = plan.delta.formation_traces[0].sources[0].claim_span
    assert (trace.start_codepoint, trace.end_codepoint) == (0, len(content))


def test_attribute_without_exact_value_fails_closed_even_for_direct_constructed_api() -> (
    None
):
    graph = _graph()
    view = IdentityAuthority(graph).view()
    content = "雾桥是深蓝色"
    proposal = TurnMeaningProposal(
        "assertion",
        MeaningMention("雾桥", 0, 2, "introduce", "project", ()),
        MeaningStatement("attribute", content, 0, len(content)),
    )
    with pytest.raises(TurnMeaningError, match="attribute_value_missing"):
        compile_product_turn(
            proposal=proposal,
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=graph,
            handles=(),
            identity_view=view,
            operation_key="operation:value-missing",
        )
    with pytest.raises(TurnMeaningError, match="attribute_value_missing"):
        decode_turn_meaning(
            json.dumps(
                {
                    "act": "assertion",
                    "mention": {
                        "text": "雾桥",
                        "start": 0,
                        "end": 2,
                        "mode": "introduce",
                        "kind_hint": "project",
                        "accepted_handles": [],
                    },
                    "statement": {
                        "kind": "attribute",
                        "text": content,
                        "start": 0,
                        "end": len(content),
                        "value": None,
                    },
                },
                ensure_ascii=False,
            ),
            content,
        )


def test_query_is_read_only_and_evaluation_is_fail_closed() -> None:
    graph = _graph()
    view = IdentityAuthority(graph).view()
    query = compile_product_turn(
        proposal=TurnMeaningProposal(
            "query", MeaningMention("这个项目", 0, 4, "refer", None, ()), None
        ),
        current_user_turn=_turn("这个项目怎么样？"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=view,
        operation_key="operation:six",
    )
    assert (query.state, query.code, query.delta) == (
        "no_candidate",
        "query_read_only",
        None,
    )
    evaluation = compile_product_turn(
        proposal=_proposal(
            content="北极光，是个无赖的项目",
            mode="introduce",
            statement_kind="evaluation",
            kind="project",
        ),
        current_user_turn=_turn("北极光，是个无赖的项目"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=view,
        operation_key="operation:seven",
    )
    assert (evaluation.state, evaluation.code, evaluation.delta) == (
        "out_of_scope",
        "statement_evaluation_not_supported",
        None,
    )


def test_query_drops_attribute_requested_field_hint_before_attribute_validation() -> (
    None
):
    content = "它的主色是什么？"
    proposal = decode_turn_meaning(
        json.dumps(
            {
                "act": "query",
                "mention": {
                    "text": "它",
                    "start": 0,
                    "end": 1,
                    "mode": "refer",
                    "kind_hint": None,
                    "accepted_handles": [],
                },
                "statement": {
                    "kind": "attribute",
                    "text": "主色",
                    "start": 2,
                    "end": 4,
                    "value": None,
                },
            },
            ensure_ascii=False,
        ),
        content,
    )
    assert proposal.act == "query"
    assert proposal.mention is not None and proposal.mention.text == "它"
    assert proposal.statement is None

    graph = _graph()
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=IdentityAuthority(graph).view(),
        operation_key="operation:query-field-hint",
    )
    assert (plan.state, plan.code, plan.delta) == (
        "no_candidate",
        "query_read_only",
        None,
    )


def test_known_reference_without_simple_attribute_stays_read_only() -> None:
    graph = _graph()
    authority = _accepted_view(graph, ("entity:project", "project", "北极光"))
    view = authority.view()
    handles = build_accepted_entity_handles(
        view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
    )
    content = "北极光"
    plan = compile_product_turn(
        proposal=_proposal(
            content=content,
            mode="refer",
            statement_kind=None,
            handles=(handles[0].handle,),
            mention_text="北极光",
        ),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=view.graph.to_graph(),
        handles=handles,
        identity_view=view,
        operation_key="operation:eight",
    )
    assert (plan.state, plan.code, plan.delta) == (
        "no_candidate",
        "reference_without_attribute",
        None,
    )


def test_multi_claim_bundle_keeps_relation_and_evaluation_without_lexical_special_cases() -> (
    None
):
    content = "我喜欢一个女孩，短发样子很可爱"
    proposal = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mentions": [
                    {
                        "text": "一个女孩",
                        "start": 3,
                        "end": 7,
                        "mode": "introduce",
                        "kind_hint": "person",
                        "accepted_handles": [],
                    }
                ],
                "claims": [
                    {
                        "id": "c:relation",
                        "kind": "relationship",
                        "subject": 0,
                        "text": "我喜欢一个女孩",
                        "start": 0,
                        "end": 7,
                        "value": None,
                        "predicate": {"text": "喜欢", "start": 1, "end": 3},
                        "relationship_direction": "owner_to_focal",
                        "polarity": "affirm",
                        "epistemic_status": "stated",
                        "disposition": "assert",
                        "related_mentions": [],
                        "accepted_entity_handles": [],
                        "prior_cognition_handles": [],
                    },
                    {
                        "id": "c:attribute",
                        "kind": "attribute",
                        "subject": 0,
                        "text": "短发样子很可爱",
                        "start": 8,
                        "end": len(content),
                        "value": {"text": "短发", "start": 8, "end": 10},
                        "polarity": "affirm",
                        "epistemic_status": "stated",
                        "disposition": "assert",
                        "related_mentions": [],
                        "accepted_entity_handles": [],
                        "prior_cognition_handles": [],
                    },
                    {
                        "id": "c:evaluation",
                        "kind": "evaluation",
                        "subject": 0,
                        "text": "样子很可爱",
                        "start": 10,
                        "end": len(content),
                        "value": {"text": "很可爱", "start": 12, "end": len(content)},
                        "evaluation_target_claim": 1,
                        "polarity": "affirm",
                        "epistemic_status": "stated",
                        "disposition": "assert",
                        "related_mentions": [],
                        "accepted_entity_handles": [],
                        "prior_cognition_handles": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        content,
    )
    graph = _graph()
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=IdentityAuthority(graph).view(),
        operation_key="multi",
    )
    assert plan.state == "candidate" and plan.claim_bundle is not None
    assert [item.kind for item in plan.claim_bundle.claims] == [
        "relationship",
        "attribute",
        "evaluation",
    ]
    assert plan.claim_bundle.to_data()["version"] == 3
    assert plan.delta is not None
    assert len(plan.delta.new_relationships) == 1
    assert plan.delta.new_relationships[0].id.startswith("relationship:")
    applied = plan.delta.apply_to(graph, ("e:current",))
    assert plan.delta.new_relationships[0].id in applied.relationships
    assert {item.target.kind for item in plan.delta.new_cognitions} == {
        "entity",
        "relationship",
    }
    assert {
        item.structured_claim.statement_kind
        for item in plan.delta.new_cognitions
        if item.structured_claim is not None
    } >= {"attribute", "relationship_statement", "evaluation"}


def test_residual_correction_rejects_the_whole_bundle_before_a_sibling_write() -> None:
    graph = _graph()
    authority = _accepted_view(graph, ("entity:girl", "person", "她"))
    view = authority.view()
    handles = build_accepted_entity_handles(
        view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
    )
    content = "短发是我想象出来的，她不是短发，她是长发"
    proposal = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mentions": [
                    {
                        "text": "她",
                        "start": 16,
                        "end": 17,
                        "mode": "refer",
                        "kind_hint": "person",
                        "accepted_handles": [handles[0].handle],
                    }
                ],
                "claims": [
                    {
                        "id": "c:imagined",
                        "kind": "attribute",
                        "subject": 0,
                        "text": "短发是我想象出来的",
                        "start": 0,
                        "end": 9,
                        "value": {"text": "短发", "start": 0, "end": 2},
                        "polarity": "affirm",
                        "epistemic_status": "owner_imagined",
                        "disposition": "correction",
                        "related_mentions": [],
                        "accepted_entity_handles": [],
                        "prior_cognition_handles": [],
                    },
                    {
                        "id": "c:negate",
                        "kind": "attribute",
                        "subject": 0,
                        "text": "她不是短发",
                        "start": 10,
                        "end": 15,
                        "value": {"text": "短发", "start": 13, "end": 15},
                        "polarity": "negate",
                        "epistemic_status": "stated",
                        "disposition": "correction",
                        "related_mentions": [],
                        "accepted_entity_handles": [],
                        "prior_cognition_handles": [],
                    },
                    {
                        "id": "c:replacement",
                        "kind": "attribute",
                        "subject": 0,
                        "text": "她是长发",
                        "start": 16,
                        "end": 20,
                        "value": {"text": "长发", "start": 18, "end": 20},
                        "polarity": "affirm",
                        "epistemic_status": "stated",
                        "disposition": "assert",
                        "related_mentions": [],
                        "accepted_entity_handles": [],
                        "prior_cognition_handles": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        content,
    )
    before = view.graph.graph_hash
    with pytest.raises(
        TurnMeaningError,
        match="residual_correction_requires_correction_boundary",
    ):
        compile_product_turn(
            proposal=proposal,
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=view.graph.to_graph(),
            handles=handles,
            identity_view=view,
            operation_key="correction",
        )
    assert view.graph.graph_hash == before


@pytest.mark.parametrize(
    (
        "content",
        "mentions",
        "predicate",
        "direction",
        "expected_source",
        "expected_target",
        "existing",
    ),
    (
        (
            "李华负责北极光计划。",
            (("李华", "introduce", "person"), ("北极光计划", "refer", "project")),
            "负责",
            "subject_to_related",
            "李华",
            "北极光计划",
            ("entity:aurora", "project", "北极光计划"),
        ),
        (
            "星港项目属于晨星社。",
            (
                ("星港项目", "introduce", "project"),
                ("晨星社", "introduce", "organization"),
            ),
            "属于",
            "subject_to_related",
            "星港项目",
            "晨星社",
            None,
        ),
        (
            "李华认识王强。",
            (("李华", "introduce", "person"), ("王强", "introduce", "person")),
            "认识",
            "subject_to_related",
            "李华",
            "王强",
            None,
        ),
    ),
)
def test_relationship_compiler_uses_two_general_mentions_with_stable_direction_and_exact_provenance(
    content: str,
    mentions: tuple[tuple[str, str, str], ...],
    predicate: str,
    direction: str,
    expected_source: str,
    expected_target: str,
    existing: tuple[str, str, str] | None,
) -> None:
    """Relationship endpoints come from mention roles, never the Owner shortcut."""

    graph = _graph()
    authority = (
        _accepted_view(graph, existing)
        if existing is not None
        else IdentityAuthority(graph)
    )
    view = authority.view()
    handles = build_accepted_entity_handles(
        view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
    )
    decoded_mentions: list[dict[str, object]] = []
    for text, mode, kind in mentions:
        accepted = [handles[0].handle] if mode == "refer" else []
        decoded_mentions.append(
            {
                "text": text,
                "start": content.index(text),
                "end": content.index(text) + len(text),
                "mode": mode,
                "kind_hint": kind,
                "accepted_handles": accepted,
            }
        )
    raw = {
        "act": "assertion",
        "mentions": decoded_mentions,
        "claims": [
            {
                "id": "relationship:general",
                "kind": "relationship",
                "subject": 0,
                "text": content,
                "start": 0,
                "end": len(content),
                "value": None,
                "predicate": {
                    "text": predicate,
                    "start": content.index(predicate),
                    "end": content.index(predicate) + len(predicate),
                },
                "occurred_at": None,
                "relationship_direction": direction,
                "polarity": "affirm",
                "epistemic_status": "stated",
                "disposition": "assert",
                "related_mentions": [1],
                "accepted_entity_handles": [],
                "prior_cognition_handles": [],
            }
        ],
    }
    proposal = decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content)
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=view.graph.to_graph(),
        handles=handles,
        identity_view=view,
        operation_key="relationship:stable",
    )
    alternate_raw = json.loads(json.dumps(raw, ensure_ascii=False))
    alternate_raw["claims"][0]["id"] = "model-renamed-this-claim"
    again = compile_product_turn(
        proposal=decode_turn_meaning(
            json.dumps(alternate_raw, ensure_ascii=False), content
        ),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=view.graph.to_graph(),
        handles=handles,
        identity_view=view,
        operation_key="relationship:stable",
    )

    assert (
        plan.state == "candidate"
        and plan.delta is not None
        and plan.claim_bundle is not None
    )
    assert len(plan.delta.new_relationships) == 1
    relationship = plan.delta.new_relationships[0]
    # The model may label claims for its own explanation, but cannot choose or
    # perturb a formal Relationship identifier.
    assert relationship.id == again.delta.new_relationships[0].id  # type: ignore[union-attr]
    entity_names = {item.id: item.canonical_name for item in plan.delta.new_entities}
    entity_names.update(
        {
            item.id: item.canonical_name
            for item in view.graph.to_graph().entities.values()
        }
    )
    assert (
        entity_names[relationship.source_entity_id],
        entity_names[relationship.target_entity_id],
        relationship.relation_type,
    ) == (expected_source, expected_target, predicate)
    claim = plan.claim_bundle.to_data()["claims"][0]  # type: ignore[index]
    assert claim["kind"] == "relationship"
    assert claim["start"] == 0 and claim["end"] == len(content)
    assert claim["predicate"] == {
        "text": predicate,
        "start": content.index(predicate),
        "end": content.index(predicate) + len(predicate),
    }
    trace = plan.delta.formation_traces[0].sources[0].claim_span
    assert trace is not None and (
        trace.start_codepoint,
        trace.end_codepoint,
    ) == (0, len(content))
    assert trace.source_content_sha256 == sha256(content.encode("utf-8")).hexdigest()
    assert trace.claim_sha256 == sha256(content.encode("utf-8")).hexdigest()


def test_relationship_identity_requires_exact_direction_and_relation_type() -> None:
    """An unrelated edge between the same endpoints is never reused as authority."""

    content = "黎明社协作星港项目。"
    graph = _graph()
    authority = _accepted_view(
        graph,
        ("entity:dawn", "organization", "黎明社"),
        ("entity:starport", "project", "星港项目"),
    )
    view = authority.view()
    handles = build_accepted_entity_handles(
        view,
        conversation_id="conversation:one",
        world_hash=view.graph.graph_hash,
    )
    handle_by_name = {item.canonical_name: item.handle for item in handles}
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "黎明社",
                "start": 0,
                "end": 3,
                "mode": "refer",
                "kind_hint": "organization",
                "accepted_handles": [handle_by_name["黎明社"]],
            },
            {
                "text": "星港项目",
                "start": 5,
                "end": 9,
                "mode": "refer",
                "kind_hint": "project",
                "accepted_handles": [handle_by_name["星港项目"]],
            },
        ],
        "claims": [
            {
                "id": "model:edge",
                "kind": "relationship",
                "subject": 0,
                "text": content,
                "start": 0,
                "end": len(content),
                "value": None,
                "predicate": {"text": "协作", "start": 3, "end": 5},
                "occurred_at": None,
                "relationship_direction": "subject_to_related",
                "polarity": "affirm",
                "epistemic_status": "stated",
                "disposition": "assert",
                "related_mentions": [1],
                "accepted_entity_handles": [],
                "prior_cognition_handles": [],
            }
        ],
    }
    proposal = decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content)

    mismatched = view.graph.to_graph()
    mismatched.add_relationship(
        Relationship(
            "relationship:different-type",
            "world:one",
            "entity:dawn",
            "entity:starport",
            "支持",
        )
    )
    mismatched.add_relationship(
        Relationship(
            "relationship:reverse-direction",
            "world:one",
            "entity:starport",
            "entity:dawn",
            "协作",
        )
    )
    created = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=mismatched,
        handles=handles,
        identity_view=view,
        operation_key="relationship:exact-key",
    )
    assert created.state == "candidate" and created.delta is not None
    assert len(created.delta.new_relationships) == 1
    assert created.delta.new_relationships[0].id not in {
        "relationship:different-type",
        "relationship:reverse-direction",
    }

    exact = view.graph.to_graph()
    exact.add_relationship(
        Relationship(
            "relationship:exact",
            "world:one",
            "entity:dawn",
            "entity:starport",
            "协作",
        )
    )
    exact.add_relationship(
        Relationship(
            "relationship:historical-exact",
            "world:one",
            "entity:dawn",
            "entity:starport",
            "协作",
            status="ended",
        )
    )
    reused = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=exact,
        handles=handles,
        identity_view=view,
        operation_key="relationship:exact-key",
    )
    assert reused.state == "candidate" and reused.delta is not None
    assert reused.delta.new_relationships == ()
    assert reused.delta.new_cognitions[0].target.id == "relationship:exact"

    duplicate_current = MemoryWorldGraph(
        world=exact.world,
        entities=exact.entities.copy(),
        relationships=exact.relationships.copy(),
        events=exact.events.copy(),
        cognitions=exact.cognitions.copy(),
    )
    duplicate_current.add_relationship(
        Relationship(
            "relationship:second-current",
            "world:one",
            "entity:dawn",
            "entity:starport",
            "协作",
        )
    )
    ambiguous = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=duplicate_current,
        handles=handles,
        identity_view=view,
        operation_key="relationship:exact-key",
    )
    assert (ambiguous.state, ambiguous.code, ambiguous.delta) == (
        "clarification_required",
        "relationship_identity_ambiguous",
        None,
    )


def test_relationship_compiler_does_not_reuse_an_ended_exact_edge() -> None:
    content = "黎明社协作星港项目。"
    graph = _graph()
    authority = _accepted_view(
        graph,
        ("entity:dawn", "organization", "黎明社"),
        ("entity:starport", "project", "星港项目"),
    )
    view = authority.view()
    handles = build_accepted_entity_handles(
        view,
        conversation_id="conversation:one",
        world_hash=view.graph.graph_hash,
    )
    handle_by_name = {item.canonical_name: item.handle for item in handles}
    ended_graph = view.graph.to_graph()
    ended_graph.add_relationship(
        Relationship(
            "relationship:ended",
            "world:one",
            "entity:dawn",
            "entity:starport",
            "协作",
            status="ended",
        )
    )
    proposal = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mentions": [
                    {
                        "text": "黎明社",
                        "start": 0,
                        "end": 3,
                        "mode": "refer",
                        "kind_hint": "organization",
                        "accepted_handles": [handle_by_name["黎明社"]],
                    },
                    {
                        "text": "星港项目",
                        "start": 5,
                        "end": 9,
                        "mode": "refer",
                        "kind_hint": "project",
                        "accepted_handles": [handle_by_name["星港项目"]],
                    },
                ],
                "claims": [
                    {
                        "id": "model:current-edge",
                        "kind": "relationship",
                        "subject": 0,
                        "text": content,
                        "start": 0,
                        "end": len(content),
                        "value": None,
                        "predicate": {"text": "协作", "start": 3, "end": 5},
                        "occurred_at": None,
                        "relationship_direction": "subject_to_related",
                        "polarity": "affirm",
                        "epistemic_status": "stated",
                        "disposition": "assert",
                        "related_mentions": [1],
                        "accepted_entity_handles": [],
                        "prior_cognition_handles": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        content,
    )
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=ended_graph,
        handles=handles,
        identity_view=view,
        operation_key="relationship:new-after-ended",
    )
    assert plan.state == "candidate" and plan.delta is not None
    assert len(plan.delta.new_relationships) == 1
    assert plan.delta.new_relationships[0].id != "relationship:ended"
    assert plan.delta.new_cognitions[0].target.id == plan.delta.new_relationships[0].id


def test_explicit_symmetric_relationship_normalizes_reverse_mentions_but_directed_does_not() -> (
    None
):
    """Symmetry is opt-in language meaning, never inferred from relation words."""

    graph = _graph()
    authority = _accepted_view(
        graph,
        # The semantic source deliberately has the lexically *larger* opaque
        # ID: a symmetric edge must still store its canonical endpoint order.
        ("entity:z-li", "person", "李华"),
        ("entity:a-wang", "person", "王强"),
    )
    view = authority.view()
    handles = build_accepted_entity_handles(
        view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
    )
    handle = {item.canonical_name: item.handle for item in handles}

    def proposal(
        content: str, first: str, second: str, *, symmetric: bool
    ) -> TurnMeaningProposal:
        return decode_turn_meaning(
            json.dumps(
                {
                    "act": "assertion",
                    "mentions": [
                        {
                            "text": first,
                            "start": content.index(first),
                            "end": content.index(first) + len(first),
                            "mode": "refer",
                            "kind_hint": "person",
                            "accepted_handles": [handle[first]],
                        },
                        {
                            "text": second,
                            "start": content.index(second),
                            "end": content.index(second) + len(second),
                            "mode": "refer",
                            "kind_hint": "person",
                            "accepted_handles": [handle[second]],
                        },
                    ],
                    "claims": [
                        {
                            "id": "model:relationship",
                            "kind": "relationship",
                            "subject": 0,
                            "text": content,
                            "start": 0,
                            "end": len(content),
                            "value": None,
                            "predicate": {
                                "text": "认识",
                                "start": content.index("认识"),
                                "end": content.index("认识") + 2,
                            },
                            "occurred_at": None,
                            "relationship_direction": "subject_to_related",
                            # This bounded field is the only authorization for endpoint
                            # normalization; its absence/default remains directed.
                            "relationship_symmetric": symmetric,
                            "polarity": "affirm",
                            "epistemic_status": "stated",
                            "disposition": "assert",
                            "related_mentions": [1],
                            "accepted_entity_handles": [],
                            "prior_cognition_handles": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            content,
        )

    first_text, second_text = "李华认识王强。", "王强认识李华。"
    first = compile_product_turn(
        proposal=proposal(first_text, "李华", "王强", symmetric=True),
        current_user_turn=_turn(first_text, turn_id="e:symmetric:one"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=view.graph.to_graph(),
        handles=handles,
        identity_view=view,
        operation_key="symmetric:one",
    )
    assert first.state == "candidate" and first.delta is not None
    relationship = first.delta.new_relationships[0]
    assert relationship.bidirectional is True
    assert (relationship.source_entity_id, relationship.target_entity_id) == (
        "entity:a-wang",
        "entity:z-li",
    )
    applied = first.delta.apply_to(view.graph.to_graph(), ("e:symmetric:one",))
    second = compile_product_turn(
        proposal=proposal(second_text, "王强", "李华", symmetric=True),
        current_user_turn=_turn(second_text, turn_id="e:symmetric:two"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=applied,
        handles=handles,
        identity_view=view,
        operation_key="symmetric:two",
    )
    assert second.state == "candidate" and second.delta is not None
    assert second.delta.new_relationships == ()
    assert second.delta.new_cognitions[0].target.id == relationship.id

    directed = compile_product_turn(
        proposal=proposal(second_text, "王强", "李华", symmetric=False),
        current_user_turn=_turn(second_text, turn_id="e:directed:reverse"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=applied,
        handles=handles,
        identity_view=view,
        operation_key="directed:reverse",
    )
    assert directed.state == "candidate" and directed.delta is not None
    assert directed.delta.new_relationships[0].bidirectional is False
    assert directed.delta.new_relationships[0].id != relationship.id


def test_relationship_symmetric_must_be_a_boolean_and_never_weakens_invalid_no_write_boundary() -> (
    None
):
    content = "李华认识王强。"
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
            {
                "text": "王强",
                "start": 4,
                "end": 6,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
        ],
        "claims": [
            {
                "id": "model:invalid-symmetric",
                "kind": "relationship",
                "subject": 0,
                "text": content,
                "start": 0,
                "end": len(content),
                "value": None,
                "predicate": {"text": "认识", "start": 2, "end": 4},
                "occurred_at": None,
                "relationship_direction": "subject_to_related",
                "relationship_symmetric": "yes",
                "polarity": "affirm",
                "epistemic_status": "stated",
                "disposition": "assert",
                "related_mentions": [1],
                "accepted_entity_handles": [],
                "prior_cognition_handles": [],
            }
        ],
    }
    with pytest.raises(TurnMeaningError, match="relationship_symmetric"):
        decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content)


def test_reestablished_ended_relationship_is_a_successor_with_typed_evolution_link() -> (
    None
):
    """Reactivation is history-preserving succession, not mutation or reuse."""

    content = "黎明社再次协作星港项目。"
    graph = _graph()
    authority = _accepted_view(
        graph,
        ("entity:dawn", "organization", "黎明社"),
        ("entity:starport", "project", "星港项目"),
    )
    view = authority.view()
    handles = build_accepted_entity_handles(
        view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
    )
    by_name = {item.canonical_name: item.handle for item in handles}
    ended = view.graph.to_graph()
    ended.add_relationship(
        Relationship(
            "relationship:former",
            "world:one",
            "entity:dawn",
            "entity:starport",
            "协作",
            status="ended",
            valid_to="2026-02-01T00:00:00+00:00",
        )
    )
    proposal = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mentions": [
                    {
                        "text": "黎明社",
                        "start": 0,
                        "end": 3,
                        "mode": "refer",
                        "kind_hint": "organization",
                        "accepted_handles": [by_name["黎明社"]],
                    },
                    {
                        "text": "星港项目",
                        "start": 7,
                        "end": 11,
                        "mode": "refer",
                        "kind_hint": "project",
                        "accepted_handles": [by_name["星港项目"]],
                    },
                ],
                "claims": [
                    {
                        "id": "model:reestablished",
                        "kind": "relationship",
                        "subject": 0,
                        "text": content,
                        "start": 0,
                        "end": len(content),
                        "value": None,
                        "predicate": {"text": "协作", "start": 5, "end": 7},
                        "occurred_at": None,
                        "relationship_direction": "subject_to_related",
                        "relationship_symmetric": False,
                        "polarity": "affirm",
                        "epistemic_status": "stated",
                        "disposition": "assert",
                        "related_mentions": [1],
                        "accepted_entity_handles": [],
                        "prior_cognition_handles": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        content,
    )
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content, occurred_at="2026-02-02T00:00:00+00:00"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=ended,
        handles=handles,
        identity_view=view,
        operation_key="relationship:reestablished",
    )
    assert plan.state == "candidate" and plan.delta is not None
    successor = plan.delta.new_relationships[0]
    assert successor.id != "relationship:former" and successor.status in {
        None,
        "active",
    }
    # A product bundle must expose a typed old -> new relationship evolution,
    # so SQLite can derive current/history without erasing the former segment.
    assert len(plan.evolution_steps) == 1
    step = plan.evolution_steps[0]
    assert (step.kind, step.relation, step.subject.kind, step.subject.id) == (
        "relationship_successor",
        "reestablished",
        "relationship",
        successor.id,
    )
    assert step.predecessor_ids == ("relationship:former",)
    assert step.successor_ids == (successor.id,)
    assert step.effective_at == "2026-02-02T00:00:00+00:00"
    assert step.evidence_ids == ("e:current",)


def test_relationship_predicate_cannot_overlap_an_endpoint_mention() -> None:
    content = "李华负责北极光计划。"
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
            {
                "text": "北极光计划",
                "start": 4,
                "end": 9,
                "mode": "introduce",
                "kind_hint": "project",
                "accepted_handles": [],
            },
        ],
        "claims": [
            {
                "id": "model:bad-predicate",
                "kind": "relationship",
                "subject": 0,
                "text": content,
                "start": 0,
                "end": len(content),
                "value": None,
                "predicate": {"text": "李华", "start": 0, "end": 2},
                "occurred_at": None,
                "relationship_direction": "subject_to_related",
                "polarity": "affirm",
                "epistemic_status": "stated",
                "disposition": "assert",
                "related_mentions": [1],
                "accepted_entity_handles": [],
                "prior_cognition_handles": [],
            }
        ],
    }
    graph = _graph()
    proposal = decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content)
    with pytest.raises(
        TurnMeaningError, match="relationship_predicate_endpoint_overlap"
    ):
        compile_product_turn(
            proposal=proposal,
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=graph,
            handles=(),
            identity_view=IdentityAuthority(graph).view(),
            operation_key="relationship:bad-predicate",
        )


def test_v2_query_without_entity_mentions_is_a_read_only_plan() -> None:
    proposal = decode_turn_meaning(
        json.dumps({"act": "query", "mentions": [], "claims": []}),
        "最近怎么样？",
    )
    graph = _graph()
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn("最近怎么样？"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=IdentityAuthority(graph).view(),
        operation_key="query:no-entity",
    )
    assert (plan.state, plan.code, plan.delta) == (
        "no_candidate",
        "query_read_only",
        None,
    )


@pytest.mark.parametrize(
    "case", ("query", "hypothetical", "negate", "invalid-span", "ambiguous-refer")
)
def test_fresh_relationship_endpoint_non_assertions_or_invalidity_never_create_world_objects(
    case: str,
) -> None:
    """A fresh endpoint is not an escape hatch around assertion and identity checks."""

    content = "李华负责北极光计划。"
    graph = _graph()
    authority = (
        _accepted_view(
            graph,
            ("entity:a", "project", "北极光计划"),
            ("entity:b", "project", "北极光计划B"),
        )
        if case == "ambiguous-refer"
        else IdentityAuthority(graph)
    )
    view = authority.view()
    handles = build_accepted_entity_handles(
        view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
    )
    second = "这个项目" if case == "ambiguous-refer" else "北极光计划"
    if case == "ambiguous-refer":
        content = "李华负责这个项目。"
    raw: dict[str, object] = {
        "act": "query" if case == "query" else "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
            {
                "text": second,
                "start": content.index(second),
                "end": content.index(second) + len(second),
                "mode": "refer" if case == "ambiguous-refer" else "introduce",
                "kind_hint": "project",
                "accepted_handles": [],
            },
        ],
        "claims": [
            {
                "id": "relationship:unsafe",
                "kind": "relationship",
                "subject": 0,
                "text": content,
                "start": 0,
                "end": len(content),
                "value": None,
                "predicate": {
                    "text": "负责",
                    "start": content.index("负责"),
                    "end": content.index("负责") + 2,
                },
                "occurred_at": None,
                "relationship_direction": "subject_to_related",
                "polarity": "negate" if case == "negate" else "affirm",
                "epistemic_status": "owner_imagined"
                if case == "hypothetical"
                else "stated",
                "disposition": "assert",
                "related_mentions": [1],
                "accepted_entity_handles": [],
                "prior_cognition_handles": [],
            }
        ],
    }
    if case == "query":
        raw["claims"] = []
    if case == "invalid-span":
        # It neither matches the supplied offsets nor occurs anywhere in the
        # Evidence, so canonical re-anchoring cannot turn it into a valid span.
        raw["claims"][0]["predicate"] = {"text": "不存在", "start": 0, "end": 3}  # type: ignore[index]
    if case == "ambiguous-refer":
        raw["mentions"][1]["accepted_handles"] = [item.handle for item in handles]  # type: ignore[index]
    if case == "invalid-span":
        with pytest.raises(TurnMeaningError):
            decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content)
        return
    plan = compile_product_turn(
        proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=view.graph.to_graph(),
        handles=handles,
        identity_view=view,
        operation_key=f"relationship:{case}",
    )
    assert plan.delta is None
    assert plan.state in {"no_candidate", "clarification_required", "out_of_scope"}


def _event_proposal(
    content: str,
    mentions: list[dict[str, object]],
    *,
    related_mentions: list[int],
    event_related_roles: list[str],
    owner_participates: bool,
    occurred_at: object,
    normalized_occurred_at: object,
    subject_role: str = "participant",
    predicate: str = "完成了演示",
    polarity: str = "affirm",
    epistemic_status: str = "stated",
) -> TurnMeaningProposal:
    """Build the closed live Event shape without granting it write authority."""

    raw = {
        "act": "assertion",
        "mentions": mentions,
        "claims": [
            {
                "id": "event:formal",
                "kind": "event",
                "subject": 0,
                "text": content,
                "start": 0,
                "end": len(content),
                "value": None,
                "predicate": {
                    "text": predicate,
                    "start": content.index(predicate),
                    "end": content.index(predicate) + len(predicate),
                },
                "occurred_at": occurred_at,
                "normalized_occurred_at": normalized_occurred_at,
                "relationship_direction": None,
                "relationship_symmetric": False,
                "event_owner_participates": owner_participates,
                "event_subject_role": subject_role,
                "event_related_roles": event_related_roles,
                "polarity": polarity,
                "epistemic_status": epistemic_status,
                "disposition": "assert",
                "related_mentions": related_mentions,
                "accepted_entity_handles": [],
                "prior_cognition_handles": [],
            }
        ],
    }
    return decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content)


def test_event_compiler_keeps_owner_participants_place_time_predicate_and_evidence_separate() -> (
    None
):
    """A formal Event makes each role inspectable instead of using an Owner shortcut."""

    content = "我和李华、王强昨天在星港公园完成了演示。"
    mentions: list[dict[str, object]] = [
        {
            "text": "李华",
            "start": content.index("李华"),
            "end": content.index("李华") + 2,
            "mode": "introduce",
            "kind_hint": "person",
            "accepted_handles": [],
        },
        {
            "text": "王强",
            "start": content.index("王强"),
            "end": content.index("王强") + 2,
            "mode": "introduce",
            "kind_hint": "person",
            "accepted_handles": [],
        },
        {
            "text": "星港公园",
            "start": content.index("星港公园"),
            "end": content.index("星港公园") + 4,
            "mode": "introduce",
            "kind_hint": "place",
            "accepted_handles": [],
        },
    ]
    time_span = {
        "text": "昨天",
        "start": content.index("昨天"),
        "end": content.index("昨天") + 2,
    }
    plan = compile_product_turn(
        proposal=_event_proposal(
            content,
            mentions,
            related_mentions=[1, 2],
            event_related_roles=["participant", "related_entity"],
            owner_participates=True,
            occurred_at=time_span,
            normalized_occurred_at="2026-01-31T12:00:00+08:00",
        ),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=_graph(),
        handles=(),
        identity_view=IdentityAuthority(_graph()).view(),
        operation_key="event:owner-participants-place",
    )

    assert (
        plan.state == "candidate"
        and plan.delta is not None
        and plan.claim_bundle is not None
    )
    assert len(plan.delta.new_events) == 1
    event = plan.delta.new_events[0]
    names = {item.id: item.canonical_name for item in plan.delta.new_entities}
    roles_by_name = {
        names.get(item.entity_id, "Owner"): item.role for item in event.participants
    }
    assert roles_by_name == {"Owner": "owner", "李华": "focus", "王强": "participant"}
    assert [names[item] for item in event.related_entity_ids] == ["星港公园"]
    assert event.event_type == "occurrence"
    assert event.occurred_at == "2026-01-31T12:00:00+08:00"
    assert {(item.key, item.value) for item in event.facets} >= {
        ("predicate", "完成了演示")
    }
    assert event.evidence_ids == ("e:current",)
    cognition = plan.delta.new_cognitions[0]
    assert cognition.target.kind == "event" and cognition.target.id == event.id
    assert (
        cognition.structured_claim is not None
        and cognition.structured_claim.predicate == "完成了演示"
    )
    assert cognition.sources[0].evidence_id == "e:current"
    trace = plan.delta.formation_traces[0].sources[0].claim_span
    assert trace is not None and (trace.start_codepoint, trace.end_codepoint) == (
        0,
        len(content),
    )
    resolved = plan.claim_bundle.to_data()["claim_resolutions"][0]  # type: ignore[index]
    assert resolved["target_kind"] == "event" and resolved["target_id"] == event.id


def test_event_compiler_allows_a_third_party_event_without_implicitly_inserting_owner() -> (
    None
):
    content = "李华和王强昨天完成了演示。"
    mentions: list[dict[str, object]] = [
        {
            "text": name,
            "start": content.index(name),
            "end": content.index(name) + 2,
            "mode": "introduce",
            "kind_hint": "person",
            "accepted_handles": [],
        }
        for name in ("李华", "王强")
    ]
    plan = compile_product_turn(
        proposal=_event_proposal(
            content,
            mentions,
            related_mentions=[1],
            event_related_roles=["participant"],
            owner_participates=False,
            occurred_at={
                "text": "昨天",
                "start": content.index("昨天"),
                "end": content.index("昨天") + 2,
            },
            normalized_occurred_at="2026-01-31T12:00:00+08:00",
        ),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=_graph(),
        handles=(),
        identity_view=IdentityAuthority(_graph()).view(),
        operation_key="event:third-party",
    )
    assert plan.state == "candidate" and plan.delta is not None
    event = plan.delta.new_events[0]
    assert "entity:owner" not in {item.entity_id for item in event.participants}
    assert {item.role for item in event.participants} == {"focus", "participant"}
    assert event.related_entity_ids == ()


def test_owner_only_event_keeps_a_place_as_related_entity_without_creating_an_i_entity() -> (
    None
):
    """The user word '我' identifies the Owner, not a fresh person Entity."""

    content = "我昨天去了南京。"
    plan = compile_product_turn(
        proposal=_event_proposal(
            content,
            [
                {
                    "text": "南京",
                    "start": content.index("南京"),
                    "end": content.index("南京") + 2,
                    "mode": "introduce",
                    "kind_hint": "place",
                    "accepted_handles": [],
                }
            ],
            related_mentions=[],
            event_related_roles=[],
            owner_participates=True,
            subject_role="related_entity",
            predicate="去了",
            occurred_at={
                "text": "昨天",
                "start": content.index("昨天"),
                "end": content.index("昨天") + 2,
            },
            normalized_occurred_at="2026-01-31T12:00:00+08:00",
        ),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=_graph(),
        handles=(),
        identity_view=IdentityAuthority(_graph()).view(),
        operation_key="event:owner-only-place",
    )
    assert plan.state == "candidate" and plan.delta is not None
    event = plan.delta.new_events[0]
    assert [(item.entity_id, item.role) for item in event.participants] == [
        ("entity:owner", "owner")
    ]
    assert len(event.related_entity_ids) == 1
    place_id = event.related_entity_ids[0]
    assert [item.canonical_name for item in plan.delta.new_entities] == ["南京"]
    assert place_id == plan.delta.new_entities[0].id
    assert all(item.canonical_name != "我" for item in plan.delta.new_entities)


@pytest.mark.parametrize(
    "case",
    (
        "missing-time",
        "naive-time",
        "invalid-time",
        "role-length",
        "duplicate-role",
        "overlap",
    ),
)
def test_invalid_event_time_or_roles_are_rejected_before_a_world_write(
    case: str,
) -> None:
    content = "李华和王强昨天完成了演示。"
    mentions: list[dict[str, object]] = [
        {
            "text": name,
            "start": content.index(name),
            "end": content.index(name) + 2,
            "mode": "introduce",
            "kind_hint": "person",
            "accepted_handles": [],
        }
        for name in ("李华", "王强")
    ]
    occurred: object = {
        "text": "昨天",
        "start": content.index("昨天"),
        "end": content.index("昨天") + 2,
    }
    normalized: object = "2026-01-31T12:00:00+08:00"
    related = [1]
    roles = ["participant"]
    if case == "missing-time":
        occurred, normalized = None, None
    elif case == "naive-time":
        normalized = "2026-01-31T12:00:00"
    elif case == "invalid-time":
        normalized = "not-a-time"
    elif case == "role-length":
        roles = []
    elif case == "duplicate-role":
        related, roles = [1, 1], ["participant", "participant"]
    elif case == "overlap":
        occurred = {"text": "李华", "start": 0, "end": 2}

    try:
        proposal = _event_proposal(
            content,
            mentions,
            related_mentions=related,
            event_related_roles=roles,
            owner_participates=False,
            occurred_at=occurred,
            normalized_occurred_at=normalized,
        )
        plan = compile_product_turn(
            proposal=proposal,
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=_graph(),
            handles=(),
            identity_view=IdentityAuthority(_graph()).view(),
            operation_key=f"event:invalid:{case}",
        )
    except TurnMeaningError:
        return
    assert plan.delta is None


@pytest.mark.parametrize("kind", ("negate", "imagined"))
def test_fresh_event_mentions_without_an_asserted_event_never_create_entities_or_events(
    kind: str,
) -> None:
    content = "李华昨天完成了演示。"
    proposal = _event_proposal(
        content,
        [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            }
        ],
        related_mentions=[],
        event_related_roles=[],
        owner_participates=False,
        occurred_at={
            "text": "昨天",
            "start": content.index("昨天"),
            "end": content.index("昨天") + 2,
        },
        normalized_occurred_at="2026-01-31T12:00:00+08:00",
        polarity="negate" if kind == "negate" else "affirm",
        epistemic_status="owner_imagined" if kind == "imagined" else "stated",
    )
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=_graph(),
        handles=(),
        identity_view=IdentityAuthority(_graph()).view(),
        operation_key=f"event:{kind}",
    )
    assert plan.delta is None
    assert plan.state in {"no_candidate", "out_of_scope"}


def test_live_event_schema_requires_normalized_time_owner_and_entity_roles() -> None:
    schema = __import__(
        "memoweft.world.turn_meaning", fromlist=["product_turn_response_format"]
    ).product_turn_response_format()
    claim_schema = schema["json_schema"]["schema"]["properties"]["claims"]["items"]
    assert {
        "normalized_occurred_at",
        "event_owner_participates",
        "event_subject_role",
        "event_related_roles",
    } <= set(claim_schema["properties"])
    assert {
        "normalized_occurred_at",
        "event_owner_participates",
        "event_subject_role",
        "event_related_roles",
    } <= set(claim_schema["required"])


def test_turn_meaning_prompt_gives_relative_event_time_an_evidence_clock() -> None:
    model = _PromptCapture()
    current = _turn(
        "昨天我和李华开会了。",
        occurred_at="2026-08-13T09:15:00+08:00",
    )

    TurnMeaningInterpreter(model).interpret([current], current, ())  # type: ignore[arg-type]

    system_message = model.messages[0]
    contract = json.loads(system_message.content)  # type: ignore[attr-defined]
    assert contract["current_user_evidence_occurred_at"] == "2026-08-13T09:15:00+08:00"
    assert any(
        "current_user_evidence_occurred_at" in rule for rule in contract["rules"]
    )


def _evaluation_claim(
    content: str,
    *,
    subject: int = 0,
    target_claim: int | None = None,
    text: str | None = None,
    value: str = "很可靠",
) -> dict[str, object]:
    """Return an exact-span evaluation claim for the product compiler tests."""

    text = text or content
    text_start = content.index(text)
    value_start = content.index(value, text_start)
    return {
        "id": "model:evaluation-label-is-not-authority",
        "kind": "evaluation",
        "subject": subject,
        "text": text,
        "start": text_start,
        "end": text_start + len(text),
        "value": {"text": value, "start": value_start, "end": value_start + len(value)},
        "predicate": None,
        "occurred_at": None,
        "normalized_occurred_at": None,
        "relationship_direction": None,
        "relationship_symmetric": False,
        "event_owner_participates": False,
        "event_subject_role": None,
        "event_related_roles": [],
        "evaluation_target_claim": target_claim,
        "polarity": "affirm",
        "epistemic_status": "stated",
        "disposition": "assert",
        "related_mentions": [],
        "accepted_entity_handles": [],
        "prior_cognition_handles": [],
    }


def test_evaluation_compiler_keeps_exact_value_owner_perspective_and_entity_target() -> (
    None
):
    """An asserted evaluation is a fact about the Entity, never a User profile."""

    content = "李华很可靠。"
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            }
        ],
        "claims": [_evaluation_claim(content)],
    }
    plan = compile_product_turn(
        proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=_graph(),
        handles=(),
        identity_view=IdentityAuthority(_graph()).view(),
        operation_key="evaluation:entity",
    )

    assert (
        plan.state == "candidate"
        and plan.delta is not None
        and plan.claim_bundle is not None
    )
    assert len(plan.delta.new_entities) == 1
    cognition = plan.delta.new_cognitions[0]
    assert cognition.target.kind == "entity"
    assert cognition.target.id == plan.delta.new_entities[0].id
    assert cognition.content == content
    assert cognition.perspective.holder_entity_ids == ("entity:owner",)
    assert cognition.structured_claim is not None
    assert (
        cognition.structured_claim.statement_kind,
        cognition.structured_claim.value,
    ) == ("evaluation", "很可靠")
    resolution = plan.claim_bundle.to_data()["claim_resolutions"][0]  # type: ignore[index]
    assert resolution["target_kind"] == "entity"


def test_first_attribute_keeps_exact_structured_predicate_and_value() -> None:
    """The first Attribute is a typed proposition, not a legacy text-only row."""

    content = "李华的发型是短发。"
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            }
        ],
        "claims": [
            {
                "id": "claim:entity-attribute",
                "kind": "attribute",
                "subject": 0,
                "text": content,
                "start": 0,
                "end": len(content),
                "value": {
                    "text": "短发",
                    "start": content.index("短发"),
                    "end": content.index("短发") + len("短发"),
                },
                "predicate": {
                    "text": "发型",
                    "start": content.index("发型"),
                    "end": content.index("发型") + len("发型"),
                },
                "occurred_at": None,
                "normalized_occurred_at": None,
                "relationship_direction": None,
                "relationship_symmetric": False,
                "event_owner_participates": False,
                "event_subject_role": None,
                "event_related_roles": [],
                "evaluation_target_claim": None,
                "object_reference": None,
                "polarity": "affirm",
                "epistemic_status": "stated",
                "disposition": "assert",
                "related_mentions": [],
                "accepted_entity_handles": [],
                "accepted_object_handles": [],
                "prior_cognition_handles": [],
            }
        ],
    }
    graph = _graph()
    plan = compile_product_turn(
        proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=IdentityAuthority(graph).view(),
        operation_key="attribute:structured-first",
    )

    assert plan.state == "candidate" and plan.delta is not None
    cognition = plan.delta.new_cognitions[0]
    assert cognition.content == content
    assert cognition.structured_claim == StructuredClaim(
        "attribute",
        predicate="发型",
        value="短发",
        polarity="assert",
        epistemic_status="asserted",
    )


def test_cognition_catalog_exposes_only_exact_structured_attributes() -> None:
    """Value-only legacy Attributes cannot be selected as the same slot."""

    graph = _graph()
    perspective = Perspective("entity", ("entity:owner",))
    common = {
        "world_id": "world:one",
        "target": MemoryTarget("entity", "entity:girl"),
        "content_type": "fact",
        "formed_by": "stated",
        "confidence": 600,
        "cred_status": "limited",
        "perspective": perspective,
    }
    incomplete = WorldCognition(
        id="cognition:attribute:value-only",
        content="短发",
        sources=(EvidenceLink("e:attribute:value-only", "support"),),
        structured_claim=StructuredClaim(
            "attribute",
            value="短发",
            polarity="assert",
            epistemic_status="asserted",
        ),
        **cast(Any, common),
    )
    exact = WorldCognition(
        id="cognition:attribute:exact",
        content="发型是短发",
        sources=(EvidenceLink("e:attribute:exact", "support"),),
        structured_claim=StructuredClaim(
            "attribute",
            predicate="发型",
            value="短发",
            polarity="assert",
            epistemic_status="asserted",
        ),
        **cast(Any, common),
    )

    handles = build_accepted_cognition_handles(
        graph,
        (incomplete, exact),
        world_hash=IdentityAuthority(graph).view().graph.graph_hash,
    )

    assert [item.cognition.id for item in handles] == [exact.id]


def test_relationship_statement_disagreement_compiles_one_same_id_evidence_update() -> (
    None
):
    """Explicit proposition disagreement is not Relationship lifecycle mutation."""

    graph = _graph()
    authority = _accepted_view(
        graph,
        ("entity:lihua", "person", "李华"),
        ("entity:xinggang", "project", "星港项目"),
    )
    relationship = Relationship(
        "relationship:lihua-supports-xinggang",
        "world:one",
        "entity:lihua",
        "entity:xinggang",
        "支持",
        False,
    )
    graph.add_relationship(relationship)
    prior = WorldCognition(
        "cognition:relationship:lihua-supports-xinggang",
        "world:one",
        MemoryTarget("relationship", relationship.id),
        "李华支持星港项目",
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", ("entity:owner",)),
        (EvidenceLink("e:relationship:initial", "support"),),
        structured_claim=StructuredClaim(
            "relationship_statement",
            predicate="支持",
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    graph.add_cognition(prior)
    identity_view = authority.view()
    entity_handles = build_accepted_entity_handles(
        identity_view,
        conversation_id="conversation:one",
        world_hash="world-hash:relationship-statement",
    )
    handles_by_name = {item.canonical_name: item.handle for item in entity_handles}
    cognition_handles = build_accepted_cognition_handles(
        graph,
        (prior,),
        world_hash="world-hash:relationship-statement",
    )
    assert [item.cognition.id for item in cognition_handles] == [prior.id]

    content = "我不认同‘李华支持星港项目’这个说法。"
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": content.index("李华"),
                "end": content.index("李华") + len("李华"),
                "mode": "refer",
                "kind_hint": "person",
                "accepted_handles": [handles_by_name["李华"]],
            },
            {
                "text": "星港项目",
                "start": content.index("星港项目"),
                "end": content.index("星港项目") + len("星港项目"),
                "mode": "refer",
                "kind_hint": "project",
                "accepted_handles": [handles_by_name["星港项目"]],
            },
        ],
        "claims": [
            {
                "id": "claim:relationship-statement:disagree",
                "kind": "relationship",
                "subject": 0,
                "text": content[:-1],
                "start": 0,
                "end": len(content) - 1,
                "value": None,
                "predicate": {
                    "text": "支持",
                    "start": content.index("支持"),
                    "end": content.index("支持") + len("支持"),
                },
                "occurred_at": None,
                "normalized_occurred_at": None,
                "relationship_direction": "subject_to_related",
                "relationship_symmetric": False,
                "event_owner_participates": False,
                "event_subject_role": None,
                "event_related_roles": [],
                "evaluation_target_claim": None,
                "object_reference": None,
                "polarity": "negate",
                "epistemic_status": "stated",
                "disposition": "assert",
                "related_mentions": [1],
                "accepted_entity_handles": [],
                "accepted_object_handles": [],
                "prior_cognition_handles": [cognition_handles[0].handle],
            }
        ],
    }

    plan = compile_product_turn(
        proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
        current_user_turn=_turn(content, turn_id="e:relationship:disagree"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=entity_handles,
        identity_view=identity_view,
        operation_key="relationship-statement:disagree",
        accepted_cognition_handles=cognition_handles,
    )

    assert plan.state == "candidate" and plan.delta is not None
    assert plan.delta.new_relationships == ()
    assert plan.delta.new_cognitions == ()
    assert plan.delta.formation_traces == ()
    assert len(plan.cognition_updates) == len(plan.evolution_steps) == 1
    updated = plan.cognition_updates[0]
    assert updated.id == prior.id
    assert updated.structured_claim == prior.structured_claim
    assert updated.sources == prior.sources + (
        EvidenceLink("e:relationship:disagree", "contradict"),
    )
    assert plan.evolution_steps[0].relation == "contradicts"
    assert plan.evolution_steps[0].subject == prior.target


def test_exact_entity_attribute_replacement_compiles_one_typed_successor() -> None:
    """A same-predicate value replacement preserves the prior as history."""

    graph = _graph()
    authority = _accepted_view(graph, ("entity:lihua", "person", "李华"))
    prior = WorldCognition(
        "cognition:attribute:hair",
        "world:one",
        MemoryTarget("entity", "entity:lihua"),
        "李华的发型是短发。",
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", ("entity:owner",)),
        (EvidenceLink("e:attribute:hair", "support"),),
        structured_claim=StructuredClaim(
            "attribute",
            predicate="发型",
            value="短发",
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    graph.add_cognition(prior)
    view = authority.view()
    handles = build_accepted_entity_handles(
        view,
        conversation_id="conversation:one",
        world_hash=view.graph.graph_hash,
    )
    lihua_handle = next(
        item.handle for item in handles if item.entity_id == "entity:lihua"
    )
    cognition_handles = build_accepted_cognition_handles(
        graph,
        (prior,),
        world_hash=view.graph.graph_hash,
    )
    content = "更正：李华的发型是长发。"
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": content.index("李华"),
                "end": content.index("李华") + len("李华"),
                "mode": "refer",
                "kind_hint": "person",
                "accepted_handles": [lihua_handle],
            }
        ],
        "claims": [
            {
                "id": "claim:attribute:hair:replacement",
                "kind": "attribute",
                "subject": 0,
                "text": content,
                "start": 0,
                "end": len(content),
                "value": {
                    "text": "长发",
                    "start": content.index("长发"),
                    "end": content.index("长发") + len("长发"),
                },
                "predicate": {
                    "text": "发型",
                    "start": content.index("发型"),
                    "end": content.index("发型") + len("发型"),
                },
                "occurred_at": None,
                "normalized_occurred_at": None,
                "relationship_direction": None,
                "relationship_symmetric": False,
                "event_owner_participates": False,
                "event_subject_role": None,
                "event_related_roles": [],
                "evaluation_target_claim": None,
                "object_reference": None,
                "polarity": "affirm",
                "epistemic_status": "stated",
                "disposition": "correction",
                "related_mentions": [],
                "accepted_entity_handles": [],
                "accepted_object_handles": [],
                "prior_cognition_handles": [],
            }
        ],
    }
    proposal = decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content)
    value_start = content.index("长发")

    plan = compile_structured_evaluation_correction(
        proposal=proposal,
        prior_cognition=prior,
        current_user_turn=_turn(
            content,
            turn_id="e:attribute:hair:replacement",
        ),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=handles,
        identity_view=view,
        operation_key="attribute:hair:replacement",
        accepted_cognition_handles=cognition_handles,
        replacement_value_span=MeaningValueSpan(
            "长发",
            value_start,
            value_start + len("长发"),
        ),
    )

    assert plan.state == "candidate" and plan.delta is not None
    assert plan.code == "structured_attribute_correction"
    assert len(plan.identity_bindings) == 1
    assert plan.identity_bindings[0].entity_id == "entity:lihua"
    assert len(plan.delta.new_cognitions) == 1
    successor = plan.delta.new_cognitions[0]
    assert successor.id != prior.id
    assert successor.target == prior.target
    assert successor.structured_claim == StructuredClaim(
        "attribute",
        predicate="发型",
        value="长发",
        polarity="assert",
        epistemic_status="asserted",
    )
    assert plan.evolution_steps[0].relation == "corrects"
    assert plan.evolution_steps[0].predecessor_ids == (prior.id,)
    assert plan.evolution_steps[0].successor_ids == (successor.id,)


def test_evaluation_can_depend_on_an_earlier_relationship_claim_in_the_same_turn() -> (
    None
):
    content = "李华和王强是同事，我觉得这段关系很可靠。"
    relationship = {
        "id": "model:relationship-label-is-not-authority",
        "kind": "relationship",
        "subject": 0,
        "text": "李华和王强是同事",
        "start": 0,
        "end": 8,
        "value": None,
        "predicate": {"text": "同事", "start": 6, "end": 8},
        "occurred_at": None,
        "normalized_occurred_at": None,
        "relationship_direction": "subject_to_related",
        "relationship_symmetric": False,
        "event_owner_participates": False,
        "event_subject_role": None,
        "event_related_roles": [],
        "evaluation_target_claim": None,
        "polarity": "affirm",
        "epistemic_status": "stated",
        "disposition": "assert",
        "related_mentions": [1],
        "accepted_entity_handles": [],
        "prior_cognition_handles": [],
    }
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
            {
                "text": "王强",
                "start": 3,
                "end": 5,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
        ],
        "claims": [
            relationship,
            _evaluation_claim(content, target_claim=0, text="我觉得这段关系很可靠"),
        ],
    }
    plan = compile_product_turn(
        proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=_graph(),
        handles=(),
        identity_view=IdentityAuthority(_graph()).view(),
        operation_key="evaluation:relationship-dependency",
    )

    assert (
        plan.state == "candidate"
        and plan.delta is not None
        and plan.claim_bundle is not None
    )
    relationship_id = plan.delta.new_relationships[0].id
    evaluation = next(
        item
        for item in plan.delta.new_cognitions
        if item.structured_claim
        and item.structured_claim.statement_kind == "evaluation"
    )
    assert (
        evaluation.target.kind == "relationship"
        and evaluation.target.id == relationship_id
    )
    assert evaluation.perspective.holder_entity_ids == ("entity:owner",)
    assert (
        plan.claim_bundle.to_data()["claim_resolutions"][1]["target_id"]  # type: ignore[index]
        == relationship_id
    )


def test_structured_replacement_requires_a_changed_value_even_when_prior_metadata_differs() -> (
    None
):
    """A legacy polarity difference cannot turn the same value into a replacement."""

    graph = _graph()
    authority = _accepted_view(
        graph,
        ("entity:lihua", "person", "李华"),
        ("project:xinggang", "project", "星港项目"),
    )
    relationship = Relationship(
        "relationship:lihua-supports-xinggang",
        "world:one",
        "entity:lihua",
        "project:xinggang",
        "支持",
    )
    graph.add_relationship(relationship)
    prior = WorldCognition(
        "cognition:evaluation:legacy-prior",
        "world:one",
        MemoryTarget("relationship", relationship.id),
        "我觉得这段支持很可靠",
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", ("entity:owner",)),
        (EvidenceLink("e:prior", "support"),),
        structured_claim=StructuredClaim(
            "evaluation",
            value="很可靠",
            polarity="negate",
            epistemic_status="asserted",
        ),
    )
    graph.add_cognition(prior)
    identity_view = authority.view()
    world_hash = "sha256:" + "a" * 64
    handles = build_accepted_entity_handles(
        identity_view,
        conversation_id="conversation:one",
        world_hash=world_hash,
    )
    handles_by_id = {item.entity_id: item.handle for item in handles}
    content = "更正：李华支持星港项目，我觉得这段支持很可靠。"
    relationship_text = "李华支持星港项目"
    evaluation_text = "我觉得这段支持很可靠"
    value_text = "很可靠"
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": content.index("李华"),
                "end": content.index("李华") + 2,
                "mode": "refer",
                "kind_hint": "person",
                "accepted_handles": [handles_by_id["entity:lihua"]],
            },
            {
                "text": "星港项目",
                "start": content.index("星港项目"),
                "end": content.index("星港项目") + 4,
                "mode": "refer",
                "kind_hint": "project",
                "accepted_handles": [handles_by_id["project:xinggang"]],
            },
        ],
        "claims": [
            {
                "id": "claim:relationship",
                "kind": "relationship",
                "subject": 0,
                "text": relationship_text,
                "start": content.index(relationship_text),
                "end": content.index(relationship_text) + len(relationship_text),
                "value": None,
                "predicate": {
                    "text": "支持",
                    "start": content.index("支持"),
                    "end": content.index("支持") + 2,
                },
                "occurred_at": None,
                "normalized_occurred_at": None,
                "relationship_direction": "subject_to_related",
                "relationship_symmetric": False,
                "event_owner_participates": False,
                "event_subject_role": None,
                "event_related_roles": [],
                "evaluation_target_claim": None,
                "polarity": "affirm",
                "epistemic_status": "stated",
                "disposition": "assert",
                "related_mentions": [1],
                "accepted_entity_handles": [],
                "prior_cognition_handles": [],
            },
            {
                **_evaluation_claim(
                    content,
                    target_claim=0,
                    text=evaluation_text,
                    value=value_text,
                ),
                "disposition": "correction",
            },
        ],
    }
    proposal = decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content)
    value_start = content.index(value_text)

    with pytest.raises(TurnMeaningError, match="structured_correction_value_unchanged"):
        compile_structured_evaluation_correction(
            proposal=proposal,
            prior_cognition=prior,
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=graph,
            handles=handles,
            identity_view=identity_view,
            operation_key="typed-correction:same-value",
            accepted_cognition_handles=build_accepted_cognition_handles(
                graph,
                (prior,),
                world_hash=world_hash,
            ),
            replacement_value_span=MeaningValueSpan(
                value_text,
                value_start,
                value_start + len(value_text),
            ),
        )


def test_evaluation_can_depend_on_an_earlier_event_claim_in_the_same_turn() -> None:
    content = "李华昨天完成了演示，我觉得这件事很重要。"
    event = {
        "id": "model:event-label-is-not-authority",
        "kind": "event",
        "subject": 0,
        "text": "李华昨天完成了演示",
        "start": 0,
        "end": 9,
        "value": None,
        "predicate": {"text": "完成了演示", "start": 4, "end": 9},
        "occurred_at": {"text": "昨天", "start": 2, "end": 4},
        "normalized_occurred_at": "2026-01-31T12:00:00+08:00",
        "relationship_direction": None,
        "relationship_symmetric": False,
        "event_owner_participates": False,
        "event_subject_role": "participant",
        "event_related_roles": [],
        "evaluation_target_claim": None,
        "polarity": "affirm",
        "epistemic_status": "stated",
        "disposition": "assert",
        "related_mentions": [],
        "accepted_entity_handles": [],
        "prior_cognition_handles": [],
    }
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            }
        ],
        "claims": [
            event,
            _evaluation_claim(
                content, target_claim=0, text="我觉得这件事很重要", value="很重要"
            ),
        ],
    }
    plan = compile_product_turn(
        proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=_graph(),
        handles=(),
        identity_view=IdentityAuthority(_graph()).view(),
        operation_key="evaluation:event-dependency",
    )

    assert plan.state == "candidate" and plan.delta is not None
    event_id = plan.delta.new_events[0].id
    evaluation = next(
        item
        for item in plan.delta.new_cognitions
        if item.structured_claim
        and item.structured_claim.statement_kind == "evaluation"
    )
    assert evaluation.target.kind == "event" and evaluation.target.id == event_id
    assert (
        evaluation.structured_claim is not None
        and evaluation.structured_claim.value == "很重要"
    )


@pytest.mark.parametrize("case", ("missing", "forged", "forward", "subject-mismatch"))
def test_invalid_evaluation_dependency_is_rejected_before_any_world_write(
    case: str,
) -> None:
    content = "李华和王强是同事，我觉得这段关系很可靠。"
    relationship = {
        "id": "relationship:source",
        "kind": "relationship",
        "subject": 0,
        "text": "李华和王强是同事",
        "start": 0,
        "end": 8,
        "value": None,
        "predicate": {"text": "同事", "start": 6, "end": 8},
        "occurred_at": None,
        "normalized_occurred_at": None,
        "relationship_direction": "subject_to_related",
        "relationship_symmetric": False,
        "event_owner_participates": False,
        "event_subject_role": None,
        "event_related_roles": [],
        "evaluation_target_claim": None,
        "polarity": "affirm",
        "epistemic_status": "stated",
        "disposition": "assert",
        "related_mentions": [1],
        "accepted_entity_handles": [],
        "prior_cognition_handles": [],
    }
    evaluation = _evaluation_claim(content, target_claim=0, text="我觉得这段关系很可靠")
    claims: list[dict[str, object]] = [relationship, evaluation]
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
            {
                "text": "王强",
                "start": 3,
                "end": 5,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
        ],
        "claims": claims,
    }
    if case == "missing":
        del evaluation["evaluation_target_claim"]
        with pytest.raises(TurnMeaningError, match="evaluation_target_claim"):
            decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content)
        return
    if case == "forged":
        evaluation["evaluation_target_claim"] = 99
    elif case == "forward":
        claims = [evaluation, relationship]
        evaluation["evaluation_target_claim"] = 1
    elif case == "subject-mismatch":
        evaluation["subject"] = 1
    raw["claims"] = claims
    with pytest.raises(TurnMeaningError, match="evaluation_target"):
        compile_product_turn(
            proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=_graph(),
            handles=(),
            identity_view=IdentityAuthority(_graph()).view(),
            operation_key=f"evaluation:invalid-dependency:{case}",
        )


def test_ambiguous_relationship_dependency_prevents_its_evaluation_from_writing() -> (
    None
):
    """A target index is not enough when its earlier Relationship has no unique World identity."""

    content = "李华和王强是同事，我觉得这段关系很可靠。"
    graph = _graph()
    authority = _accepted_view(
        graph, ("entity:li", "person", "李华"), ("entity:wang", "person", "王强")
    )
    view = authority.view()
    current = view.graph.to_graph()
    current.add_relationship(
        Relationship(
            "relationship:first", "world:one", "entity:li", "entity:wang", "同事"
        )
    )
    current.add_relationship(
        Relationship(
            "relationship:second", "world:one", "entity:li", "entity:wang", "同事"
        )
    )
    handles = build_accepted_entity_handles(
        view, conversation_id="conversation:one", world_hash=view.graph.graph_hash
    )
    by_name = {item.canonical_name: item.handle for item in handles}
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "refer",
                "kind_hint": "person",
                "accepted_handles": [by_name["李华"]],
            },
            {
                "text": "王强",
                "start": 3,
                "end": 5,
                "mode": "refer",
                "kind_hint": "person",
                "accepted_handles": [by_name["王强"]],
            },
        ],
        "claims": [
            {
                "id": "relationship:ambiguous",
                "kind": "relationship",
                "subject": 0,
                "text": "李华和王强是同事",
                "start": 0,
                "end": 8,
                "value": None,
                "predicate": {"text": "同事", "start": 6, "end": 8},
                "occurred_at": None,
                "normalized_occurred_at": None,
                "relationship_direction": "subject_to_related",
                "relationship_symmetric": False,
                "event_owner_participates": False,
                "event_subject_role": None,
                "event_related_roles": [],
                "evaluation_target_claim": None,
                "polarity": "affirm",
                "epistemic_status": "stated",
                "disposition": "assert",
                "related_mentions": [1],
                "accepted_entity_handles": [],
                "prior_cognition_handles": [],
            },
            _evaluation_claim(content, target_claim=0, text="我觉得这段关系很可靠"),
        ],
    }
    plan = compile_product_turn(
        proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=current,
        handles=handles,
        identity_view=view,
        operation_key="evaluation:ambiguous-dependency",
    )
    assert plan.delta is None
    assert plan.state == "clarification_required"


@pytest.mark.parametrize(
    ("case", "text", "value", "expected_error"),
    (
        ("value-outside", "李华", "很可靠", "evaluation_claim_does_not_contain_value"),
        (
            "subject-outside",
            "很可靠",
            "很可靠",
            "evaluation_claim_does_not_contain_subject",
        ),
        (
            "subject-value-overlap",
            "李华很可靠",
            "李华",
            "evaluation_mention_value_overlap",
        ),
    ),
)
def test_direct_entity_evaluation_requires_a_nonoverlapping_subject_and_value_inside_its_claim(
    case: str,
    text: str,
    value: str,
    expected_error: str,
) -> None:
    """A direct evaluation cannot smuggle a value or subject from elsewhere."""

    content = "李华很可靠。"
    claim = _evaluation_claim(content, text=text, value=value)
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            }
        ],
        "claims": [claim],
    }
    with pytest.raises(TurnMeaningError, match=expected_error):
        compile_product_turn(
            proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=_graph(),
            handles=(),
            identity_view=IdentityAuthority(_graph()).view(),
            operation_key=f"evaluation:direct-span:{case}",
        )


def test_evaluation_of_an_earlier_attribute_uses_its_explicit_claim_dependency() -> (
    None
):
    """“样子很可爱” targets the earlier attribute's Entity only by claim index."""

    content = "李华短发，样子很可爱。"
    attribute = {
        "id": "model:attribute-label-is-not-authority",
        "kind": "attribute",
        "subject": 0,
        "text": "李华短发",
        "start": 0,
        "end": 4,
        "value": {"text": "短发", "start": 2, "end": 4},
        "predicate": None,
        "occurred_at": None,
        "normalized_occurred_at": None,
        "relationship_direction": None,
        "relationship_symmetric": False,
        "event_owner_participates": False,
        "event_subject_role": None,
        "event_related_roles": [],
        "evaluation_target_claim": None,
        "polarity": "affirm",
        "epistemic_status": "stated",
        "disposition": "assert",
        "related_mentions": [],
        "accepted_entity_handles": [],
        "prior_cognition_handles": [],
    }
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            }
        ],
        "claims": [
            attribute,
            _evaluation_claim(
                content, target_claim=0, text="样子很可爱", value="很可爱"
            ),
        ],
    }
    plan = compile_product_turn(
        proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=_graph(),
        handles=(),
        identity_view=IdentityAuthority(_graph()).view(),
        operation_key="evaluation:attribute-dependency",
    )

    assert (
        plan.state == "candidate"
        and plan.delta is not None
        and plan.claim_bundle is not None
    )
    evaluation = next(
        item
        for item in plan.delta.new_cognitions
        if item.structured_claim
        and item.structured_claim.statement_kind == "evaluation"
    )
    assert evaluation.target.kind == "entity"
    assert evaluation.target.id == plan.delta.new_entities[0].id
    resolutions = plan.claim_bundle.to_data()["claim_resolutions"]
    assert resolutions[1]["target_id"] == resolutions[0]["target_id"]  # type: ignore[index]
    assert plan.claim_bundle.to_data()["claims"][1]["evaluation_target_claim"] == 0  # type: ignore[index]


@pytest.mark.parametrize("target_kind", ("relationship", "event"))
def test_direct_asserted_evaluation_cannot_depend_on_a_nonwritable_earlier_target(
    target_kind: str,
) -> None:
    if target_kind == "relationship":
        content = "李华和王强是同事，我觉得这段关系很可靠。"
        evaluation_text, evaluation_value = "我觉得这段关系很可靠", "很可靠"
        target: dict[str, object] = {
            "id": "relationship:denied",
            "kind": "relationship",
            "subject": 0,
            "text": "李华和王强是同事",
            "start": 0,
            "end": 8,
            "value": None,
            "predicate": {"text": "同事", "start": 6, "end": 8},
            "occurred_at": None,
            "normalized_occurred_at": None,
            "relationship_direction": "subject_to_related",
            "relationship_symmetric": False,
            "event_owner_participates": False,
            "event_subject_role": None,
            "event_related_roles": [],
            "evaluation_target_claim": None,
            "polarity": "negate",
            "epistemic_status": "stated",
            "disposition": "assert",
            "related_mentions": [1],
            "accepted_entity_handles": [],
            "prior_cognition_handles": [],
        }
    else:
        content = "李华昨天完成了演示，我觉得这件事很重要。"
        evaluation_text, evaluation_value = "我觉得这件事很重要", "很重要"
        target = {
            "id": "event:denied",
            "kind": "event",
            "subject": 0,
            "text": "李华昨天完成了演示",
            "start": 0,
            "end": 9,
            "value": None,
            "predicate": {"text": "完成了演示", "start": 4, "end": 9},
            "occurred_at": {"text": "昨天", "start": 2, "end": 4},
            "normalized_occurred_at": "2026-01-31T12:00:00+08:00",
            "relationship_direction": None,
            "relationship_symmetric": False,
            "event_owner_participates": False,
            "event_subject_role": "participant",
            "event_related_roles": [],
            "evaluation_target_claim": None,
            "polarity": "negate",
            "epistemic_status": "stated",
            "disposition": "assert",
            "related_mentions": [],
            "accepted_entity_handles": [],
            "prior_cognition_handles": [],
        }
    raw = {
        "act": "assertion",
        "mentions": (
            [
                {
                    "text": "李华",
                    "start": 0,
                    "end": 2,
                    "mode": "introduce",
                    "kind_hint": "person",
                    "accepted_handles": [],
                },
                {
                    "text": "王强",
                    "start": 3,
                    "end": 5,
                    "mode": "introduce",
                    "kind_hint": "person",
                    "accepted_handles": [],
                },
            ]
            if target_kind == "relationship"
            else [
                {
                    "text": "李华",
                    "start": 0,
                    "end": 2,
                    "mode": "introduce",
                    "kind_hint": "person",
                    "accepted_handles": [],
                }
            ]
        ),
        "claims": [
            target,
            _evaluation_claim(
                content, target_claim=0, text=evaluation_text, value=evaluation_value
            ),
        ],
    }
    with pytest.raises(TurnMeaningError, match="evaluation_target_claim_not_writable"):
        compile_product_turn(
            proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=_graph(),
            handles=(),
            identity_view=IdentityAuthority(_graph()).view(),
            operation_key=f"evaluation:nonwritable-{target_kind}",
        )


def test_turn_meaning_prompt_explains_explicit_evaluation_target_rules() -> None:
    model = _PromptCapture()
    current = _turn("李华很可靠。")

    TurnMeaningInterpreter(model).interpret([current], current, ())  # type: ignore[arg-type]

    contract = json.loads(model.messages[0].content)  # type: ignore[attr-defined]
    evaluation_rule = next(
        rule for rule in contract["rules"] if "evaluation_target_claim" in rule
    )
    assert "每条 claim 都必须输出 evaluation_target_claim" in evaluation_rule
    assert "直接评价 subject Entity 时为 null" in evaluation_rule
    assert "更早的 attribute/relationship/event" in evaluation_rule


def test_evaluation_dependency_rejects_an_explicit_mention_of_an_unrelated_entity() -> (
    None
):
    """An index for 李华 cannot turn the explicit text “王强很可靠” into 李华's fact."""

    content = "李华很高，王强很可靠。"
    attribute = {
        "id": "attribute:li-height",
        "kind": "attribute",
        "subject": 0,
        "text": "李华很高",
        "start": 0,
        "end": 4,
        "value": {"text": "很高", "start": 2, "end": 4},
        "predicate": None,
        "occurred_at": None,
        "normalized_occurred_at": None,
        "relationship_direction": None,
        "relationship_symmetric": False,
        "event_owner_participates": False,
        "event_subject_role": None,
        "event_related_roles": [],
        "evaluation_target_claim": None,
        "polarity": "affirm",
        "epistemic_status": "stated",
        "disposition": "assert",
        "related_mentions": [],
        "accepted_entity_handles": [],
        "prior_cognition_handles": [],
    }
    raw = {
        "act": "assertion",
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
            {
                "text": "王强",
                "start": 5,
                "end": 7,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
        ],
        "claims": [
            attribute,
            _evaluation_claim(content, target_claim=0, text="王强很可靠"),
        ],
    }
    with pytest.raises(
        TurnMeaningError, match="evaluation_target_explicit_mention_mismatch"
    ):
        compile_product_turn(
            proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=_graph(),
            handles=(),
            identity_view=IdentityAuthority(_graph()).view(),
            operation_key="evaluation:explicit-foreign-mention",
        )


def test_evaluation_dependency_rejects_a_repeated_foreign_entity_surface() -> None:
    """A later repeated name is checked even when the model lists it only once."""

    content = "李华很高，王强在旁边，王强很可靠。"
    first_wang = content.index("王强")
    attribute = {
        "id": "attribute:li-height",
        "kind": "attribute",
        "subject": 0,
        "text": "李华很高",
        "start": 0,
        "end": 4,
        "value": {"text": "很高", "start": 2, "end": 4},
        "predicate": None,
        "occurred_at": None,
        "normalized_occurred_at": None,
        "relationship_direction": None,
        "relationship_symmetric": False,
        "event_owner_participates": False,
        "event_subject_role": None,
        "event_related_roles": [],
        "evaluation_target_claim": None,
        "polarity": "affirm",
        "epistemic_status": "stated",
        "disposition": "assert",
        "related_mentions": [],
        "accepted_entity_handles": [],
        "prior_cognition_handles": [],
    }
    raw = {
        "act": "assertion",
        # The model reports only the first 王强 mention. Trusted validation must
        # still see the same exact surface repeated inside the evaluation claim.
        "mentions": [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
            {
                "text": "王强",
                "start": first_wang,
                "end": first_wang + 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
        ],
        "claims": [
            attribute,
            _evaluation_claim(content, target_claim=0, text="王强很可靠"),
        ],
    }
    with pytest.raises(
        TurnMeaningError, match="evaluation_target_explicit_mention_mismatch"
    ):
        compile_product_turn(
            proposal=decode_turn_meaning(json.dumps(raw, ensure_ascii=False), content),
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=_graph(),
            handles=(),
            identity_view=IdentityAuthority(_graph()).view(),
            operation_key="evaluation:repeated-foreign-surface",
        )


@pytest.mark.parametrize("target_kind", ("relationship", "event"))
def test_evaluation_dependency_allows_an_explicit_mention_of_its_own_target_endpoint(
    target_kind: str,
) -> None:
    """The anti-forgery check keeps valid explicit endpoint references usable."""

    if target_kind == "relationship":
        content = "李华和王强是同事，李华觉得这段关系很可靠。"
        target: dict[str, object] = {
            "id": "relationship:source",
            "kind": "relationship",
            "subject": 0,
            "text": "李华和王强是同事",
            "start": 0,
            "end": 8,
            "value": None,
            "predicate": {"text": "同事", "start": 6, "end": 8},
            "occurred_at": None,
            "normalized_occurred_at": None,
            "relationship_direction": "subject_to_related",
            "relationship_symmetric": False,
            "event_owner_participates": False,
            "event_subject_role": None,
            "event_related_roles": [],
            "evaluation_target_claim": None,
            "polarity": "affirm",
            "epistemic_status": "stated",
            "disposition": "assert",
            "related_mentions": [1],
            "accepted_entity_handles": [],
            "prior_cognition_handles": [],
        }
        # Keep the target's endpoint spans in the evaluation claim, so this is
        # an actual explicit-endpoint case rather than an unmodelled repeated
        # surface word elsewhere in the Evidence.
        evaluation_text, evaluation_value = content, "很可靠"
        mentions = [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
            {
                "text": "王强",
                "start": 3,
                "end": 5,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            },
        ]
    else:
        content = "李华昨天完成了演示，李华觉得这件事很重要。"
        target = {
            "id": "event:source",
            "kind": "event",
            "subject": 0,
            "text": "李华昨天完成了演示",
            "start": 0,
            "end": 9,
            "value": None,
            "predicate": {"text": "完成了演示", "start": 4, "end": 9},
            "occurred_at": {"text": "昨天", "start": 2, "end": 4},
            "normalized_occurred_at": "2026-01-31T12:00:00+08:00",
            "relationship_direction": None,
            "relationship_symmetric": False,
            "event_owner_participates": False,
            "event_subject_role": "participant",
            "event_related_roles": [],
            "evaluation_target_claim": None,
            "polarity": "affirm",
            "epistemic_status": "stated",
            "disposition": "assert",
            "related_mentions": [],
            "accepted_entity_handles": [],
            "prior_cognition_handles": [],
        }
        evaluation_text, evaluation_value = content, "很重要"
        mentions = [
            {
                "text": "李华",
                "start": 0,
                "end": 2,
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            }
        ]
    plan = compile_product_turn(
        proposal=decode_turn_meaning(
            json.dumps(
                {
                    "act": "assertion",
                    "mentions": mentions,
                    "claims": [
                        target,
                        _evaluation_claim(
                            content,
                            target_claim=0,
                            text=evaluation_text,
                            value=evaluation_value,
                        ),
                    ],
                },
                ensure_ascii=False,
            ),
            content,
        ),
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=_graph(),
        handles=(),
        identity_view=IdentityAuthority(_graph()).view(),
        operation_key=f"evaluation:explicit-own-{target_kind}",
    )
    assert plan.state == "candidate" and plan.delta is not None
    evaluation = next(
        item
        for item in plan.delta.new_cognitions
        if item.structured_claim
        and item.structured_claim.statement_kind == "evaluation"
    )
    assert evaluation.target.kind == target_kind


def _indirect_relationship_evaluation_raw(
    content: str,
    *,
    handle: str,
    act: str = "assertion",
    epistemic_status: str = "stated",
    handles: list[str] | None = None,
    object_reference: dict[str, object] | None = None,
    reference: str = "这段关系",
    value: str = "很重要",
    disposition: str = "assert",
) -> dict[str, object]:
    """Return the new closed shape for a cross-turn World-object reference."""

    return {
        "act": act,
        "mentions": [],
        "claims": [
            {
                "id": "model:opaque-object-label-is-not-authority",
                "kind": "evaluation",
                "subject": None,
                "text": content,
                "start": 0,
                "end": len(content),
                "value": {
                    "text": value,
                    "start": content.index(value),
                    "end": content.index(value) + len(value),
                },
                "predicate": None,
                "occurred_at": None,
                "normalized_occurred_at": None,
                "relationship_direction": None,
                "relationship_symmetric": False,
                "event_owner_participates": False,
                "event_subject_role": None,
                "event_related_roles": [],
                "evaluation_target_claim": None,
                "object_reference": object_reference
                or {
                    "text": reference,
                    "start": content.index(reference),
                    "end": content.index(reference) + len(reference),
                },
                "polarity": "affirm",
                "epistemic_status": epistemic_status,
                "disposition": disposition,
                "related_mentions": [],
                "accepted_entity_handles": [],
                "accepted_object_handles": [handle] if handles is None else handles,
                "prior_cognition_handles": [],
            }
        ],
    }


def _relationship_object_fixture(
    *,
    second_relationship: bool = False,
) -> tuple[
    MemoryWorldGraph,
    IdentityAuthority,
    tuple[object, ...],
    Relationship,
]:
    """Build accepted endpoints plus one or two current Relationships."""

    graph = _graph()
    authority = _accepted_view(
        graph,
        ("entity:lihua", "person", "李华"),
        ("project:xinggang", "project", "星港项目"),
        *(("project:beichen", "project", "北辰项目"),) if second_relationship else (),
    )
    relationship = Relationship(
        "relationship:lihua-supports-xinggang",
        "world:one",
        "entity:lihua",
        "project:xinggang",
        "支持",
    )
    graph.add_relationship(relationship)
    if second_relationship:
        graph.add_relationship(
            Relationship(
                "relationship:lihua-supports-beichen",
                "world:one",
                "entity:lihua",
                "project:beichen",
                "支持",
            )
        )
    from memoweft.world.turn_meaning import build_accepted_world_object_handles

    object_handles = build_accepted_world_object_handles(
        graph,
        tuple(graph.relationships.values()),
        world_hash=authority.view().graph.graph_hash,
    )
    return graph, authority, object_handles, relationship


def test_cross_turn_relationship_object_handle_compiles_an_exact_owner_evaluation() -> (
    None
):
    """“这段关系” targets the existing Relationship without a fake Entity mention."""

    graph, authority, object_handles, relationship = _relationship_object_fixture()
    assert len(object_handles) == 1
    content = "我觉得这段关系很重要。"
    proposal = decode_turn_meaning(
        json.dumps(
            _indirect_relationship_evaluation_raw(
                content,
                handle=object_handles[0].handle,  # type: ignore[attr-defined]
            ),
            ensure_ascii=False,
        ),
        content,
    )
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content, turn_id="e:relationship-object"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=build_accepted_entity_handles(
            authority.view(),
            conversation_id="conversation:one",
            world_hash=authority.view().graph.graph_hash,
        ),
        identity_view=authority.view(),
        operation_key="relationship-object:evaluation",
        accepted_object_handles=object_handles,  # type: ignore[arg-type]
    )

    assert plan.state == "candidate" and plan.delta is not None
    assert plan.identity_bindings == ()
    assert plan.claim_bundle is not None
    bundle = plan.claim_bundle.to_data()
    assert bundle["version"] == 3
    assert bundle["focal_entity_id"] == relationship.source_entity_id
    assert bundle["resolved_mentions"] == []
    resolution = bundle["claim_resolutions"][0]  # type: ignore[index]
    assert resolution == {
        "claim_index": 0,
        "subject_entity_id": None,
        "related_entity_ids": [],
        "target_kind": "relationship",
        "target_id": relationship.id,
        "source_entity_id": relationship.source_entity_id,
        "target_entity_id": relationship.target_entity_id,
        "relation_type": relationship.relation_type,
        "bidirectional": relationship.bidirectional,
        "participant_entity_ids": [],
        "object_entity_ids": [],
        "owner_participates": None,
        "event_type": None,
        "occurred_at": None,
    }
    cognition = plan.delta.new_cognitions[0]
    assert cognition.target == MemoryTarget("relationship", relationship.id)
    assert cognition.perspective == Perspective("entity", ("entity:owner",))
    assert cognition.structured_claim == StructuredClaim(
        "evaluation",
        value="很重要",
        polarity="assert",
        epistemic_status="asserted",
    )
    assert cognition.sources == (EvidenceLink("e:relationship-object", "support"),)
    trace = plan.delta.formation_traces[0]
    assert trace.cognition_id == cognition.id
    assert trace.sources[0].claim_span.start_codepoint == 0
    assert trace.sources[0].claim_span.end_codepoint == len(content)


def test_cross_turn_relationship_handle_is_only_a_hint_and_cannot_hide_catalog_ambiguity() -> (
    None
):
    """One model-selected handle cannot become write authority over two current edges."""

    graph, authority, object_handles, _ = _relationship_object_fixture(
        second_relationship=True,
    )
    assert len(object_handles) == 2
    content = "我觉得这段关系很重要。"
    proposal = decode_turn_meaning(
        json.dumps(
            _indirect_relationship_evaluation_raw(
                content,
                handle=object_handles[0].handle,  # type: ignore[attr-defined]
            ),
            ensure_ascii=False,
        ),
        content,
    )
    before = (dict(graph.relationships), dict(graph.cognitions))
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content, turn_id="e:relationship-object:ambiguous"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=authority.view(),
        operation_key="relationship-object:ambiguous",
        accepted_object_handles=object_handles,  # type: ignore[arg-type]
    )

    assert (plan.state, plan.code, plan.delta) == (
        "clarification_required",
        "object_referent_ambiguous",
        None,
    )
    assert (graph.relationships, graph.cognitions) == before


def test_cross_turn_relationship_evaluation_rejects_forged_and_multiple_object_handles() -> (
    None
):
    graph, authority, object_handles, _ = _relationship_object_fixture(
        second_relationship=True,
    )
    content = "我觉得这段关系很重要。"

    forged = decode_turn_meaning(
        json.dumps(
            _indirect_relationship_evaluation_raw(
                content,
                handle="accepted-object:forged",
            ),
            ensure_ascii=False,
        ),
        content,
    )
    with pytest.raises(TurnMeaningError, match="unknown_accepted_object_handle"):
        compile_product_turn(
            proposal=forged,
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=graph,
            handles=(),
            identity_view=authority.view(),
            operation_key="relationship-object:forged",
            accepted_object_handles=object_handles,  # type: ignore[arg-type]
        )

    with pytest.raises(TurnMeaningError, match="accepted_object_handle_ambiguous"):
        decode_turn_meaning(
            json.dumps(
                _indirect_relationship_evaluation_raw(
                    content,
                    handle=object_handles[0].handle,  # type: ignore[attr-defined]
                    handles=[item.handle for item in object_handles],  # type: ignore[attr-defined]
                ),
                ensure_ascii=False,
            ),
            content,
        )


def test_cross_turn_relationship_reference_with_no_eligible_object_requires_clarification() -> (
    None
):
    graph = _graph()
    content = "我觉得这段关系很重要。"
    proposal = decode_turn_meaning(
        json.dumps(
            _indirect_relationship_evaluation_raw(
                content,
                handle="unused",
                handles=[],
            ),
            ensure_ascii=False,
        ),
        content,
    )
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=IdentityAuthority(graph).view(),
        operation_key="relationship-object:unresolved",
        accepted_object_handles=(),
    )

    assert (plan.state, plan.code, plan.delta) == (
        "clarification_required",
        "object_referent_unresolved",
        None,
    )


def test_cross_turn_relationship_handle_cannot_target_an_ended_relationship() -> None:
    active, _, object_handles, relationship = _relationship_object_fixture()
    assert len(object_handles) == 1
    ended = _graph()
    ended_authority = _accepted_view(
        ended,
        ("entity:lihua", "person", "李华"),
        ("project:xinggang", "project", "星港项目"),
    )
    ended.add_relationship(
        replace(
            relationship,
            status="ended",
            valid_to="2026-08-12T00:00:00+00:00",
        )
    )
    content = "我觉得这段关系很重要。"
    proposal = decode_turn_meaning(
        json.dumps(
            _indirect_relationship_evaluation_raw(
                content,
                handle=object_handles[0].handle,  # type: ignore[attr-defined]
            ),
            ensure_ascii=False,
        ),
        content,
    )

    with pytest.raises(TurnMeaningError, match="object_handle_not_current"):
        compile_product_turn(
            proposal=proposal,
            current_user_turn=_turn(content),
            world_id="world:one",
            owner_entity_id="entity:owner",
            base_graph=ended,
            handles=(),
            identity_view=ended_authority.view(),
            operation_key="relationship-object:ended",
            accepted_object_handles=object_handles,  # type: ignore[arg-type]
        )
    assert active.relationships[relationship.id].status != "ended"


@pytest.mark.parametrize(
    ("act", "epistemic_status", "expected_code"),
    (
        ("query", "stated", "query_read_only"),
        ("assertion", "owner_imagined", "no_eligible_world_change"),
    ),
)
def test_cross_turn_relationship_query_and_hypothetical_are_zero_write(
    act: str,
    epistemic_status: str,
    expected_code: str,
) -> None:
    graph, authority, object_handles, _ = _relationship_object_fixture()
    content = "我觉得这段关系很重要。"
    raw = (
        {"act": "query", "mentions": [], "claims": []}
        if act == "query"
        else _indirect_relationship_evaluation_raw(
            content,
            handle=object_handles[0].handle,  # type: ignore[attr-defined]
            epistemic_status=epistemic_status,
        )
    )
    proposal = decode_turn_meaning(
        json.dumps(raw, ensure_ascii=False),
        content,
    )
    before = (dict(graph.relationships), dict(graph.cognitions))
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=authority.view(),
        operation_key=f"relationship-object:zero-write:{act}:{epistemic_status}",
        accepted_object_handles=object_handles,  # type: ignore[arg-type]
    )

    assert plan.state == "no_candidate"
    assert plan.code == expected_code
    assert plan.delta is None
    assert (graph.relationships, graph.cognitions) == before


def test_cross_turn_object_reference_requires_an_exact_evidence_span() -> None:
    _, _, object_handles, _ = _relationship_object_fixture()
    content = "我觉得这段关系很重要。"
    bad_reference = {
        "text": "那段关系",
        "start": content.index("这段关系"),
        "end": content.index("这段关系") + len("这段关系"),
    }
    with pytest.raises(TurnMeaningError, match="value_span_mismatch"):
        decode_turn_meaning(
            json.dumps(
                _indirect_relationship_evaluation_raw(
                    content,
                    handle=object_handles[0].handle,  # type: ignore[attr-defined]
                    object_reference=bad_reference,
                ),
                ensure_ascii=False,
            ),
            content,
        )


def test_turn_meaning_prompt_exposes_only_opaque_current_world_object_handles() -> None:
    from memoweft.world.turn_meaning import MULTI_CLAIM_RESPONSE_FORMAT

    _, _, object_handles, relationship = _relationship_object_fixture()
    current = _turn("我觉得这段关系很重要。")
    model = _PromptCapture()
    TurnMeaningInterpreter(cast(Any, model)).interpret(
        [current],
        current,
        (),
        object_handles=object_handles,  # type: ignore[arg-type]
    )

    contract = json.loads(model.messages[0].content)  # type: ignore[attr-defined]
    assert contract["accepted_object_catalog"] == [
        {
            "handle": object_handles[0].handle,  # type: ignore[attr-defined]
            "kind": "relationship",
            "source_name": "李华",
            "target_name": "星港项目",
            "relation_type": "支持",
            "bidirectional": False,
        }
    ]
    serialized = json.dumps(contract, ensure_ascii=False)
    assert relationship.id not in serialized
    assert any(
        "object_reference" in rule and "accepted_object_handles" in rule
        for rule in contract["rules"]
    )
    claim_schema = MULTI_CLAIM_RESPONSE_FORMAT["json_schema"]["schema"]["properties"][
        "claims"
    ]["items"]
    assert {"object_reference", "accepted_object_handles"} <= set(
        claim_schema["required"]
    )
    assert claim_schema["properties"]["accepted_object_handles"]["maxItems"] == 1


def _event_object_fixture(
    *,
    second_event: bool = False,
) -> tuple[MemoryWorldGraph, IdentityAuthority, tuple[object, ...], WorldEvent]:
    graph = _graph()
    authority = _accepted_view(
        graph,
        ("entity:lihua", "person", "李华"),
        ("project:xinggang", "project", "星港项目"),
    )
    event = WorldEvent(
        "event:xinggang-demo",
        "world:one",
        "occurrence",
        "李华演示了星港项目",
        "2026-01-31T12:00:00+08:00",
        (EventParticipant("entity:lihua", "focus"),),
        ("project:xinggang",),
        facets=(),
        evidence_ids=("e:event:xinggang-demo",),
    )
    graph.add_event(event)
    if second_event:
        graph.add_event(
            WorldEvent(
                "event:xinggang-review",
                "world:one",
                "occurrence",
                "李华复盘了星港项目",
                "2026-02-01T12:00:00+08:00",
                (EventParticipant("entity:lihua", "focus"),),
                ("project:xinggang",),
                facets=(),
                evidence_ids=("e:event:xinggang-review",),
            )
        )
    from memoweft.world.turn_meaning import build_accepted_world_object_handles

    object_handles = build_accepted_world_object_handles(
        graph,
        (),
        tuple(graph.events.values()),
        world_hash=authority.view().graph.graph_hash,
    )
    return graph, authority, object_handles, event


def test_cross_turn_event_object_handle_compiles_an_exact_owner_evaluation() -> None:
    graph, authority, object_handles, event = _event_object_fixture()
    assert len(object_handles) == 1
    content = "我觉得那次演示很有意义。"
    proposal = decode_turn_meaning(
        json.dumps(
            _indirect_relationship_evaluation_raw(
                content,
                handle=object_handles[0].handle,  # type: ignore[attr-defined]
                reference="那次演示",
                value="很有意义",
            ),
            ensure_ascii=False,
        ),
        content,
    )
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content, turn_id="e:event-object"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=authority.view(),
        operation_key="event-object:evaluation",
        accepted_object_handles=object_handles,  # type: ignore[arg-type]
    )

    assert plan.state == "candidate" and plan.delta is not None
    assert plan.identity_bindings == ()
    assert plan.claim_bundle is not None
    bundle = plan.claim_bundle.to_data()
    assert bundle["focal_entity_id"] == event.participants[0].entity_id
    assert bundle["resolved_mentions"] == []
    assert bundle["claim_resolutions"] == [
        {
            "claim_index": 0,
            "subject_entity_id": None,
            "related_entity_ids": [],
            "target_kind": "event",
            "target_id": event.id,
            "source_entity_id": None,
            "target_entity_id": None,
            "relation_type": None,
            "bidirectional": None,
            "participant_entity_ids": ["entity:lihua"],
            "object_entity_ids": ["project:xinggang"],
            "owner_participates": False,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
        }
    ]
    cognition = plan.delta.new_cognitions[0]
    assert cognition.target == MemoryTarget("event", event.id)
    assert cognition.perspective == Perspective("entity", ("entity:owner",))
    assert cognition.structured_claim == StructuredClaim(
        "evaluation",
        value="很有意义",
        polarity="assert",
        epistemic_status="asserted",
    )
    assert cognition.sources == (EvidenceLink("e:event-object", "support"),)


def test_cross_turn_event_statement_disagreement_compiles_one_same_id_evidence_update() -> (
    None
):
    """An Event reference may dispute its exact statement without recreating it."""

    graph, authority, _, original_event = _event_object_fixture()
    event = replace(
        original_event,
        facets=(EventFacet("predicate", "演示"),),
    )
    graph.events[event.id] = event
    prior = WorldCognition(
        "cog:event:xinggang-demo:statement",
        "world:one",
        MemoryTarget("event", event.id),
        event.summary,
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", ("entity:owner",)),
        (EvidenceLink("e:event:xinggang-demo", "support"),),
        structured_claim=StructuredClaim(
            "event_statement",
            predicate="演示",
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    graph.add_cognition(prior)
    world_hash = "snapshot:event-statement"
    from memoweft.world.turn_meaning import build_accepted_world_object_handles

    object_handles = build_accepted_world_object_handles(
        graph,
        (),
        (event,),
        world_hash=world_hash,
    )
    cognition_handles = build_accepted_cognition_handles(
        graph,
        (prior,),
        world_hash=world_hash,
    )
    assert len(object_handles) == len(cognition_handles) == 1
    content = "我不认同‘那次演示确实发生过’这个说法。"
    reference = "那次演示"
    proposal = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mentions": [],
                "claims": [
                    {
                        "id": "claim:event-statement-evidence",
                        "kind": "event",
                        "subject": None,
                        "text": content[:-1],
                        "start": 0,
                        "end": len(content) - 1,
                        "value": None,
                        "predicate": None,
                        "occurred_at": None,
                        "normalized_occurred_at": None,
                        "relationship_direction": None,
                        "relationship_symmetric": False,
                        "event_owner_participates": False,
                        "event_subject_role": None,
                        "event_related_roles": [],
                        "evaluation_target_claim": None,
                        "object_reference": {
                            "text": reference,
                            "start": content.index(reference),
                            "end": content.index(reference) + len(reference),
                        },
                        "polarity": "negate",
                        "epistemic_status": "stated",
                        "disposition": "assert",
                        "related_mentions": [],
                        "accepted_entity_handles": [],
                        "accepted_object_handles": [object_handles[0].handle],
                        "prior_cognition_handles": [cognition_handles[0].handle],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        content,
    )

    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content, turn_id="e:event-statement:contradict"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=authority.view(),
        operation_key="event-statement:contradict",
        accepted_cognition_handles=cognition_handles,
        accepted_object_handles=object_handles,
    )

    assert plan.state == "candidate" and plan.delta is not None
    assert plan.identity_bindings == ()
    assert plan.delta.new_entities == ()
    assert plan.delta.new_relationships == ()
    assert plan.delta.new_events == ()
    assert plan.delta.new_cognitions == ()
    assert plan.delta.formation_traces == ()
    assert len(plan.cognition_updates) == len(plan.evolution_steps) == 1
    updated = plan.cognition_updates[0]
    assert updated.id == prior.id
    assert updated.target == MemoryTarget("event", event.id)
    assert updated.structured_claim == prior.structured_claim
    assert updated.sources == (
        EvidenceLink("e:event:xinggang-demo", "support"),
        EvidenceLink("e:event-statement:contradict", "contradict"),
    )
    step = plan.evolution_steps[0]
    assert step.kind == "cognition_change"
    assert step.relation == "contradicts"
    assert step.subject == MemoryTarget("event", event.id)
    assert step.predecessor_ids == step.successor_ids == (prior.id,)
    assert plan.claim_bundle is not None
    assert plan.claim_bundle.claim_resolutions[0].target_id == event.id


def test_cross_turn_event_handle_cannot_hide_same_kind_catalog_ambiguity() -> None:
    graph, authority, object_handles, _ = _event_object_fixture(second_event=True)
    content = "我觉得那次演示很有意义。"
    proposal = decode_turn_meaning(
        json.dumps(
            _indirect_relationship_evaluation_raw(
                content,
                handle=object_handles[0].handle,  # type: ignore[attr-defined]
                reference="那次演示",
                value="很有意义",
            ),
            ensure_ascii=False,
        ),
        content,
    )
    before = (dict(graph.events), dict(graph.cognitions))
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content, turn_id="e:event-object:ambiguous"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=authority.view(),
        operation_key="event-object:ambiguous",
        accepted_object_handles=object_handles,  # type: ignore[arg-type]
    )

    assert (plan.state, plan.code, plan.delta) == (
        "clarification_required",
        "object_referent_ambiguous",
        None,
    )
    assert (graph.events, graph.cognitions) == before


def test_event_handle_selection_is_not_blocked_by_one_relationship_candidate() -> None:
    graph, authority, event_handles, event = _event_object_fixture()
    relationship = Relationship(
        "relationship:lihua-supports-xinggang",
        "world:one",
        "entity:lihua",
        "project:xinggang",
        "支持",
    )
    graph.add_relationship(relationship)
    from memoweft.world.turn_meaning import build_accepted_world_object_handles

    object_handles = build_accepted_world_object_handles(
        graph,
        (relationship,),
        (event,),
        world_hash=authority.view().graph.graph_hash,
    )
    event_handle = next(
        item
        for item in object_handles
        if item.target.kind == "event"
    )
    content = "我觉得那次演示很有意义。"
    proposal = decode_turn_meaning(
        json.dumps(
            _indirect_relationship_evaluation_raw(
                content,
                handle=event_handle.handle,
                reference="那次演示",
                value="很有意义",
            ),
            ensure_ascii=False,
        ),
        content,
    )
    plan = compile_product_turn(
        proposal=proposal,
        current_user_turn=_turn(content, turn_id="e:event-object:mixed-catalog"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=authority.view(),
        operation_key="event-object:mixed-catalog",
        accepted_object_handles=object_handles,
    )

    assert plan.state == "candidate" and plan.delta is not None
    assert plan.delta.new_cognitions[0].target == MemoryTarget("event", event.id)
    assert len(event_handles) == 1


@pytest.mark.parametrize("target_kind", ("relationship", "event"))
def test_indirect_world_object_evaluation_replacement_compiles_one_typed_successor(
    target_kind: str,
) -> None:
    """A later correction may name the formal object without restating it."""

    target: Relationship | WorldEvent
    if target_kind == "relationship":
        graph, authority, object_handles, target = _relationship_object_fixture()
        content = "更正：我觉得这段关系很不重要。"
        reference = "这段关系"
        old_value = "很重要"
        new_value = "很不重要"
    else:
        graph, authority, object_handles, target = _event_object_fixture()
        content = "更正：我觉得那次演示没有意义。"
        reference = "那次演示"
        old_value = "很有意义"
        new_value = "没有意义"
    world_target = MemoryTarget(cast(Any, target_kind), target.id)
    prior = WorldCognition(
        f"cognition:{target_kind}:prior-evaluation",
        "world:one",
        world_target,
        old_value,
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", ("entity:owner",)),
        (EvidenceLink(f"e:{target_kind}:prior-evaluation", "support"),),
        structured_claim=StructuredClaim(
            "evaluation",
            value=old_value,
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    graph.add_cognition(prior)
    world_hash = authority.view().graph.graph_hash
    cognition_handles = build_accepted_cognition_handles(
        graph,
        (prior,),
        world_hash=world_hash,
    )
    proposal = decode_turn_meaning(
        json.dumps(
            _indirect_relationship_evaluation_raw(
                content,
                handle=object_handles[0].handle,  # type: ignore[attr-defined]
                reference=reference,
                value=new_value,
                disposition="correction",
            ),
            ensure_ascii=False,
        ),
        content,
    )
    value_start = content.index(new_value)

    plan = compile_structured_evaluation_correction(
        proposal=proposal,
        prior_cognition=prior,
        current_user_turn=_turn(
            content,
            turn_id=f"e:{target_kind}:indirect-correction",
        ),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=build_accepted_entity_handles(
            authority.view(),
            conversation_id="conversation:one",
            world_hash=world_hash,
        ),
        identity_view=authority.view(),
        operation_key=f"{target_kind}:indirect-correction",
        accepted_cognition_handles=cognition_handles,
        accepted_object_handles=object_handles,  # type: ignore[arg-type]
        replacement_value_span=MeaningValueSpan(
            new_value,
            value_start,
            value_start + len(new_value),
        ),
    )

    assert plan.state == "candidate" and plan.delta is not None
    assert plan.delta.new_entities == ()
    assert plan.delta.new_relationships == ()
    assert plan.delta.new_events == ()
    assert len(plan.delta.new_cognitions) == 1
    successor = plan.delta.new_cognitions[0]
    assert successor.id != prior.id
    assert successor.target == world_target
    assert successor.perspective == prior.perspective
    assert successor.structured_claim == StructuredClaim(
        "evaluation",
        value=new_value,
        polarity="assert",
        epistemic_status="asserted",
    )
    assert plan.evolution_steps[0].subject == world_target
    assert plan.evolution_steps[0].predecessor_ids == (prior.id,)
    assert plan.evolution_steps[0].successor_ids == (successor.id,)
    assert plan.claim_bundle is not None
    assert plan.claim_bundle.to_data()["claim_resolutions"][0]["target_id"] == target.id  # type: ignore[index]
