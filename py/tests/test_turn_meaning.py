"""Capability-1 contract tests for structured turn interpretation.

The cases deliberately cover open entity kinds.  No result depends on a
person/pet/project word list: the only identity inputs are a sealed accepted
binding, an exact user span, and an opaque handle catalog.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from memoweft.world.extractor import ConversationTurn
from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.identity_review import EntityIdentityDelta, IdentityAuthority, IdentityEvidence
from memoweft.world.model import Entity, PersonalWorld
from memoweft.world.turn_meaning import (
    MeaningMention,
    MeaningStatement,
    MeaningValueSpan,
    TurnMeaningError,
    TurnMeaningProposal,
    build_accepted_entity_handles,
    compile_product_turn,
    decode_turn_meaning,
)


def _turn(content: str, *, turn_id: str = "e:current", occurred_at: str = "2026-02-01T00:00:00+00:00") -> ConversationTurn:
    return ConversationTurn(turn_id, "conversation:one", "user", content, occurred_at)


def _graph() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld("world:one", "entity:owner"))
    graph.add_entity(Entity("entity:owner", "world:one", "person", "Owner"))
    return graph


def _accepted_view(graph: MemoryWorldGraph, *items: tuple[str, str, str]) -> IdentityAuthority:
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
        review = authority.stage(EntityIdentityDelta.bind("world:one", entity_id, mention), {"owner": "test"})
        authority.decide(review.review_id, review.result_hash, "accept", f"2026-01-{index + 1:02d}T00:00:00+00:00")
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
    mention_text = mention_text or ("这个项目" if mode == "refer" else content.split("，", 1)[0])
    mention_start = content.index(mention_text)
    statement = None
    if statement_kind is not None:
        statement_text = content
        value = None
        if statement_kind == "attribute":
            value_text = content[content.index("，") + 1 :]
            value_start = content.index(value_text)
            value = MeaningValueSpan(value_text, value_start, value_start + len(value_text))
        statement = MeaningStatement(
            statement_kind,  # type: ignore[arg-type]
            statement_text,
            content.index(statement_text),
            content.index(statement_text) + len(statement_text),
            value,
        )
    return TurnMeaningProposal(
        "assertion",
        MeaningMention(mention_text, mention_start, mention_start + len(mention_text), mode, kind, handles),  # type: ignore[arg-type]
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
def test_introduction_accepts_open_entity_kinds_without_a_lexical_patch(kind: str, name: str) -> None:
    graph = _graph()
    content = f"{name}，它很重要"
    plan = compile_product_turn(
        proposal=_proposal(content=content, mode="introduce", statement_kind="attribute", kind=kind),
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
    assert plan.delta.new_cognitions[0].perspective.holder_entity_ids == ("entity:owner",)
    assert plan.identity_bindings[0].evidence_id == "e:current"
    assert plan.identity_bindings[0].start_codepoint == 0


def test_unique_accepted_continuation_uses_same_entity_and_model_cannot_choose_it() -> None:
    graph = _graph()
    authority = _accepted_view(graph, ("entity:project", "project", "北极光"))
    view = authority.view()
    handles = build_accepted_entity_handles(view, conversation_id="conversation:one", world_hash=view.graph.graph_hash)
    content = "这个项目，已经延期"
    plan = compile_product_turn(
        proposal=_proposal(content=content, mode="refer", statement_kind="attribute", handles=()),
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


def test_two_grounded_antecedents_require_clarification_even_if_model_lists_one_handle() -> None:
    graph = _graph()
    authority = _accepted_view(
        graph,
        ("entity:project-a", "project", "甲计划"),
        ("entity:project-b", "project", "乙计划"),
    )
    view = authority.view()
    handles = tuple(
        replace(item, recent_in_conversation=True)
        for item in build_accepted_entity_handles(view, conversation_id="conversation:one", world_hash=view.graph.graph_hash)
    )
    content = "这个项目，已经延期"
    plan = compile_product_turn(
        proposal=_proposal(content=content, mode="refer", statement_kind="attribute", handles=(handles[0].handle,)),
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
    assert (plan.state, plan.code, plan.delta) == ("clarification_required", "referent_unresolved", None)


def test_unknown_handle_and_span_mismatch_are_rejected_before_a_candidate_is_created() -> None:
    graph = _graph()
    view = IdentityAuthority(graph).view()
    with pytest.raises(TurnMeaningError, match="unknown_accepted_handle"):
        compile_product_turn(
            proposal=_proposal(content="这个项目，已经延期", mode="refer", statement_kind="attribute", handles=("accepted:invented",)),
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


def test_predicate_value_statement_is_hulled_with_subject_for_the_evidence_claim() -> None:
    graph = _graph()
    authority = _accepted_view(graph, ("entity:project", "project", "北极光"))
    view = authority.view()
    handles = build_accepted_entity_handles(view, conversation_id="conversation:one", world_hash=view.graph.graph_hash)
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


def test_attribute_without_exact_value_fails_closed_even_for_direct_constructed_api() -> None:
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
                    "statement": {"kind": "attribute", "text": content, "start": 0, "end": len(content), "value": None},
                },
                ensure_ascii=False,
            ),
            content,
        )


def test_query_is_read_only_and_evaluation_is_fail_closed() -> None:
    graph = _graph()
    view = IdentityAuthority(graph).view()
    query = compile_product_turn(
        proposal=TurnMeaningProposal("query", MeaningMention("这个项目", 0, 4, "refer", None, ()), None),
        current_user_turn=_turn("这个项目怎么样？"),
        world_id="world:one",
        owner_entity_id="entity:owner",
        base_graph=graph,
        handles=(),
        identity_view=view,
        operation_key="operation:six",
    )
    assert (query.state, query.code, query.delta) == ("no_candidate", "query_read_only", None)
    evaluation = compile_product_turn(
        proposal=_proposal(content="北极光，是个无赖的项目", mode="introduce", statement_kind="evaluation", kind="project"),
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


def test_query_drops_attribute_requested_field_hint_before_attribute_validation() -> None:
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
    assert (plan.state, plan.code, plan.delta) == ("no_candidate", "query_read_only", None)


def test_known_reference_without_simple_attribute_stays_read_only() -> None:
    graph = _graph()
    authority = _accepted_view(graph, ("entity:project", "project", "北极光"))
    view = authority.view()
    handles = build_accepted_entity_handles(view, conversation_id="conversation:one", world_hash=view.graph.graph_hash)
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
    assert (plan.state, plan.code, plan.delta) == ("no_candidate", "reference_without_attribute", None)


def test_multi_claim_bundle_keeps_relation_and_evaluation_without_lexical_special_cases() -> None:
    content = "我喜欢一个女孩，短发样子很可爱"
    proposal = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mentions": [{"text": "一个女孩", "start": 3, "end": 7, "mode": "introduce", "kind_hint": "person", "accepted_handles": []}],
                "claims": [
                    {"id": "c:relation", "kind": "relationship", "subject": 0, "text": "我喜欢一个女孩", "start": 0, "end": 7, "value": None, "predicate": {"text": "喜欢", "start": 1, "end": 3}, "relationship_direction": "owner_to_focal", "polarity": "affirm", "epistemic_status": "stated", "disposition": "assert", "related_mentions": [], "accepted_entity_handles": [], "prior_cognition_handles": []},
                    {"id": "c:attribute", "kind": "attribute", "subject": 0, "text": "短发样子很可爱", "start": 8, "end": len(content), "value": {"text": "短发", "start": 8, "end": 10}, "polarity": "affirm", "epistemic_status": "stated", "disposition": "assert", "related_mentions": [], "accepted_entity_handles": [], "prior_cognition_handles": []},
                    {"id": "c:evaluation", "kind": "evaluation", "subject": 0, "text": "样子很可爱", "start": 10, "end": len(content), "value": None, "polarity": "affirm", "epistemic_status": "stated", "disposition": "assert", "related_mentions": [], "accepted_entity_handles": [], "prior_cognition_handles": []},
                ],
            },
            ensure_ascii=False,
        ),
        content,
    )
    graph = _graph()
    plan = compile_product_turn(proposal=proposal, current_user_turn=_turn(content), world_id="world:one", owner_entity_id="entity:owner", base_graph=graph, handles=(), identity_view=IdentityAuthority(graph).view(), operation_key="multi")
    assert plan.state == "candidate" and plan.claim_bundle is not None
    assert [item.kind for item in plan.claim_bundle.claims] == ["relationship", "attribute", "evaluation"]
    assert plan.claim_bundle.to_data()["version"] == 1
    assert plan.delta is not None
    assert len(plan.delta.new_relationships) == 1
    assert plan.delta.new_relationships[0].id.startswith("relationship:")
    applied = plan.delta.apply_to(graph, ("e:current",))
    assert plan.delta.new_relationships[0].id in applied.relationships
    assert {item.target.kind for item in plan.delta.new_cognitions} == {"entity", "relationship"}
    assert {item.structured_claim.statement_kind for item in plan.delta.new_cognitions if item.structured_claim is not None} >= {"relationship_statement", "evaluation"}


def test_unaccepted_correction_is_not_a_write_and_following_affirmed_attribute_is_safe() -> None:
    graph = _graph()
    authority = _accepted_view(graph, ("entity:girl", "person", "她"))
    view = authority.view()
    handles = build_accepted_entity_handles(view, conversation_id="conversation:one", world_hash=view.graph.graph_hash)
    content = "短发是我想象出来的，她不是短发，她是长发"
    proposal = decode_turn_meaning(
        json.dumps(
            {
                "act": "assertion",
                "mentions": [{"text": "她", "start": 16, "end": 17, "mode": "refer", "kind_hint": "person", "accepted_handles": [handles[0].handle]}],
                "claims": [
                    {"id": "c:imagined", "kind": "attribute", "subject": 0, "text": "短发是我想象出来的", "start": 0, "end": 9, "value": {"text": "短发", "start": 0, "end": 2}, "polarity": "affirm", "epistemic_status": "owner_imagined", "disposition": "correction", "related_mentions": [], "accepted_entity_handles": [], "prior_cognition_handles": []},
                    {"id": "c:negate", "kind": "attribute", "subject": 0, "text": "她不是短发", "start": 10, "end": 15, "value": {"text": "短发", "start": 13, "end": 15}, "polarity": "negate", "epistemic_status": "stated", "disposition": "correction", "related_mentions": [], "accepted_entity_handles": [], "prior_cognition_handles": []},
                    {"id": "c:replacement", "kind": "attribute", "subject": 0, "text": "她是长发", "start": 16, "end": 20, "value": {"text": "长发", "start": 18, "end": 20}, "polarity": "affirm", "epistemic_status": "stated", "disposition": "assert", "related_mentions": [], "accepted_entity_handles": [], "prior_cognition_handles": []},
                ],
            },
            ensure_ascii=False,
        ),
        content,
    )
    plan = compile_product_turn(proposal=proposal, current_user_turn=_turn(content), world_id="world:one", owner_entity_id="entity:owner", base_graph=view.graph.to_graph(), handles=handles, identity_view=view, operation_key="correction")
    assert plan.state == "candidate" and plan.delta is not None and plan.claim_bundle is not None
    assert plan.delta.new_cognitions[0].content == "她是长发"
    assert [item.disposition for item in plan.claim_bundle.claims[:2]] == ["correction", "correction"]
