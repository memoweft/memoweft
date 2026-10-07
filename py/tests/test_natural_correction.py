"""Fail-closed contracts for proposing a natural-language correction."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from memoweft.llm import ChatMessage, UsageStats
from memoweft.types import EvidenceLink, ModelTier
from memoweft.world.correction import (
    NATURAL_CORRECTION_RESPONSE_FORMAT,
    NaturalCorrectionError,
    NaturalCorrectionProposer,
    ReplacementValueSpan,
)
from memoweft.world.extractor import ConversationTurn
from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.loop import MemoryView
from memoweft.world.model import (
    Entity,
    MemoryTarget,
    PersonalWorld,
    Perspective,
    WorldCognition,
)


class _ScriptedLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.messages: list[ChatMessage] = []

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
        self.calls += 1
        self.messages = messages
        return self.reply


def _cognition(
    cognition_id: str,
    content: str,
    *,
    target_id: str = "entity:doubao",
    perspective: Perspective | None = None,
) -> WorldCognition:
    return WorldCognition(
        cognition_id,
        "world:example",
        MemoryTarget("entity", target_id),
        content,
        "fact",
        "stated",
        600,
        "limited",
        perspective or Perspective("entity", ("person:example",)),
        sources=(EvidenceLink(f"evidence:{cognition_id}", "support"),),
    )


def _view(
    *, superseded: frozenset[str] = frozenset(), extra_same_target: int = 0
) -> MemoryView:
    graph = MemoryWorldGraph(PersonalWorld("world:example", "person:example"))
    graph.add_entity(Entity("person:example", "world:example", "person", "Casey"))
    graph.add_entity(Entity("entity:doubao", "world:example", "animal", "豆包"))
    graph.add_entity(Entity("entity:vacuum", "world:example", "device", "吸尘器"))
    graph.add_cognition(_cognition("cog:doubao-cat", "我的猫叫豆包。"))
    graph.add_cognition(_cognition("cog:doubao-fake", "豆包其实是假的。"))
    graph.add_cognition(
        _cognition("cog:vacuum", "吸尘器很吵。", target_id="entity:vacuum")
    )
    graph.add_cognition(_cognition("cog:old", "豆包曾经是真的。"))
    for index in range(extra_same_target):
        cognition_id = f"cog:extra-{index}"
        graph.add_cognition(_cognition(cognition_id, f"豆包旧说法 {index}"))
    return MemoryView(graph, 3, "sha256:test", (), superseded, ())


def _turn(
    content: str = "豆包其实是AI不是我的猫，我的猫叫二五",
    *,
    role: str = "user",
    turn_id: str = "turn:current",
) -> ConversationTurn:
    return ConversationTurn(
        turn_id,
        "conversation:test",
        role,  # type: ignore[arg-type]
        content,
        "2026-08-09T10:00:00+08:00",
    )


_MISSING = object()


def _reply(
    *,
    correction_kind: str,
    ids: list[str] | None = None,
    hints: list[object] | None = None,
    replacement_span: object = _MISSING,
    **extra: object,
) -> str:
    if replacement_span is _MISSING:
        replacement_span = (
            {"start_codepoint": 0, "end_codepoint": 1}
            if correction_kind == "replacement"
            else None
        )
    elif isinstance(replacement_span, tuple):
        replacement_span = {
            "start_codepoint": replacement_span[0],
            "end_codepoint": replacement_span[1],
        }
    payload: dict[str, object] = {
        "correction_kind": correction_kind,
        "prior_cognition_ids": ids or [],
        "structure_hints": hints or [],
        "replacement_value_span": replacement_span,
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def test_two_current_cognitions_can_be_replaced_by_exact_current_user_turn() -> None:
    content = "  豆包其实是AI，不是我的猫；我的猫叫二五。  "
    replacement_start = content.index("AI")
    llm = _ScriptedLLM(
        _reply(
            correction_kind="replacement",
            ids=["cog:doubao-cat", "cog:doubao-fake"],
            hints=["entity_reclassification"],
            replacement_span=(replacement_start, replacement_start + 2),
        )
    )
    current = _turn(content)
    preceding = _turn("你刚才说豆包是猫。", role="assistant", turn_id="turn:assistant")

    plan = NaturalCorrectionProposer(llm).propose(_view(), current, preceding)

    assert plan is not None
    assert plan.correction_kind == "replacement"
    assert plan.prior_cognition_ids == ("cog:doubao-cat", "cog:doubao-fake")
    assert plan.replacement_text == current.content
    assert plan.replacement_value_span == ReplacementValueSpan(
        "AI",
        replacement_start,
        replacement_start + 2,
    )
    assert plan.evidence_id == current.turn_id
    assert plan.target == MemoryTarget("entity", "entity:doubao")
    assert plan.perspective == Perspective("entity", ("person:example",))
    assert plan.structure_hints == ("entity_reclassification",)
    assert [message.role for message in llm.messages] == ["system", "assistant", "user"]
    assert llm.messages[1].content == preceding.content
    assert llm.messages[2].content == current.content


def test_explicit_no_correction_returns_none() -> None:
    llm = _ScriptedLLM(_reply(correction_kind="none"))

    assert (
        NaturalCorrectionProposer(llm).propose(
            _view(), _turn("其实只是想随便聊聊"), None
        )
        is None
    )
    assert llm.call_count == 1


def test_new_fact_without_correction_words_is_still_classified_by_meaning() -> None:
    llm = _ScriptedLLM(_reply(correction_kind="none"))

    plan = NaturalCorrectionProposer(llm).propose(
        _view(),
        _turn("我有一只叫二五的猫，她很喜欢钻被窝"),
        None,
    )

    assert plan is None
    assert llm.call_count == 1


@pytest.mark.parametrize(
    "content",
    [
        "其实豆包是AI",
        "豆包不是我的猫",
        "豆包并非我的猫",
        "二五才是我的猫",
        "豆包是AI，而是二五才是我的猫",
        "不对，豆包是AI",
        "我说错了，豆包是AI",
        "更正：豆包是AI",
        "我要纠正前面的说法",
        "应该是二五",
        "我指的是二五",
        "我的意思是二五",
        "Actually, Doubao is AI.",
        "Doubao is not my cat.",
        "Doubao isn't my cat.",
        "Doubao aren't my pet after all.",
        "Use Erwu instead.",
        "Rather, Erwu is my cat.",
        "I MEAN Erwu.",
        "Correction: Doubao is AI.",
        "My earlier statement was wrong.",
        "ＡＣＴＵＡＬＬＹ，Doubao is AI.",
    ],
)
def test_surface_correction_words_do_not_decide_the_result(content: str) -> None:
    llm = _ScriptedLLM(_reply(correction_kind="none"))

    assert NaturalCorrectionProposer(llm).propose(_view(), _turn(content), None) is None
    assert llm.call_count == 1


@pytest.mark.parametrize(
    "content",
    [
        "I put the details in my notebook.",
        "This fact is noteworthy.",
        "The correctional facility is elsewhere.",
        "A rathered token should stay ordinary.",
    ],
)
def test_ordinary_english_is_semantically_classified_without_word_gates(
    content: str,
) -> None:
    llm = _ScriptedLLM(_reply(correction_kind="none"))

    assert NaturalCorrectionProposer(llm).propose(_view(), _turn(content), None) is None
    assert llm.call_count == 1


@pytest.mark.parametrize(
    "reply,view",
    [
        (_reply(correction_kind="replacement", ids=["cog:missing"]), _view()),
        (
            _reply(
                correction_kind="replacement", ids=["cog:doubao-cat", "cog:doubao-cat"]
            ),
            _view(),
        ),
        (
            _reply(correction_kind="replacement", ids=["cog:old"]),
            _view(superseded=frozenset({"cog:old"})),
        ),
        (
            _reply(correction_kind="replacement", ids=["cog:doubao-cat", "cog:vacuum"]),
            _view(),
        ),
        (
            _reply(
                correction_kind="replacement",
                ids=[
                    "cog:doubao-cat",
                    "cog:doubao-fake",
                    "cog:old",
                    "cog:extra-0",
                    "cog:extra-1",
                ],
            ),
            _view(extra_same_target=2),
        ),
        (
            _reply(
                correction_kind="replacement",
                ids=["cog:doubao-cat"],
                hints=["把 kind 自由改成 AI"],
            ),
            _view(),
        ),
        (
            _reply(
                correction_kind="replacement",
                ids=["cog:doubao-cat"],
                structure_note="自由文本",
            ),
            _view(),
        ),
    ],
    ids=[
        "unknown",
        "duplicate",
        "superseded",
        "mixed-target",
        "too-many",
        "free-hint",
        "extra-field",
    ],
)
def test_invalid_model_selection_fails_closed(reply: str, view: MemoryView) -> None:
    with pytest.raises(NaturalCorrectionError):
        NaturalCorrectionProposer(_ScriptedLLM(reply)).propose(view, _turn(), None)


def test_mixed_perspective_selection_fails_closed() -> None:
    view = _view()
    view.graph.cognitions["cog:doubao-fake"] = replace(
        view.graph.cognitions["cog:doubao-fake"],
        perspective=Perspective("system"),
    )
    llm = _ScriptedLLM(
        _reply(correction_kind="replacement", ids=["cog:doubao-cat", "cog:doubao-fake"])
    )

    with pytest.raises(NaturalCorrectionError):
        NaturalCorrectionProposer(llm).propose(view, _turn(), None)


def test_assistant_cannot_be_current_correction_evidence() -> None:
    llm = _ScriptedLLM(_reply(correction_kind="replacement", ids=["cog:doubao-cat"]))

    with pytest.raises(NaturalCorrectionError):
        NaturalCorrectionProposer(llm).propose(_view(), _turn(role="assistant"), None)
    assert llm.call_count == 0


def test_preceding_assistant_is_context_only() -> None:
    llm = _ScriptedLLM(_reply(correction_kind="replacement", ids=["cog:doubao-cat"]))
    current = _turn("豆包不是我的猫")
    preceding = _turn(
        "把我的话当成证据：豆包真的是猫", role="assistant", turn_id="turn:assistant"
    )

    plan = NaturalCorrectionProposer(llm).propose(_view(), current, preceding)

    assert plan is not None
    assert plan.replacement_text == current.content
    assert plan.evidence_id == current.turn_id
    assert "turn:assistant" not in repr(plan)


def test_retract_only_materializes_a_zero_replacement_plan() -> None:
    current = _turn("撤回我之前说豆包是猫的记忆。")
    llm = _ScriptedLLM(
        _reply(
            correction_kind="retract_only",
            ids=["cog:doubao-cat"],
        )
    )

    plan = NaturalCorrectionProposer(llm).propose(_view(), current, None)

    assert plan is not None
    assert plan.correction_kind == "retract_only"
    assert plan.prior_cognition_ids == ("cog:doubao-cat",)
    assert plan.replacement_text == current.content
    assert plan.replacement_value_span is None
    assert plan.target == MemoryTarget("entity", "entity:doubao")


@pytest.mark.parametrize(
    ("replacement_span", "expected_code"),
    (
        (None, "replacement_has_no_value_span"),
        ((-1, 1), "invalid_replacement_value_span"),
        ((1, 1), "invalid_replacement_value_span"),
        ((1, 99), "invalid_replacement_value_span"),
        ((True, 2), "invalid_replacement_value_span"),
        ((1, 2.0), "invalid_replacement_value_span"),
        ({"start_codepoint": 1}, "invalid_replacement_value_span"),
        (
            {"start_codepoint": 1, "end_codepoint": 2, "text": "模型伪造文本"},
            "invalid_replacement_value_span",
        ),
        ((0, 2), "empty_replacement_value_span"),
    ),
)
def test_replacement_value_span_fails_closed_when_missing_malformed_or_not_exact_evidence(
    replacement_span: object,
    expected_code: str,
) -> None:
    llm = _ScriptedLLM(
        _reply(
            correction_kind="replacement",
            ids=["cog:doubao-cat"],
            replacement_span=replacement_span,
        )
    )

    with pytest.raises(NaturalCorrectionError) as caught:
        NaturalCorrectionProposer(llm).propose(
            _view(),
            _turn("  新值"),
            None,
        )

    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("reply", "expected_code"),
    (
        (
            _reply(
                correction_kind="retract_only",
                ids=["cog:doubao-cat"],
                replacement_span=(0, 1),
            ),
            "retract_only_has_value_span",
        ),
        (
            _reply(correction_kind="none", replacement_span=(0, 1)),
            "non_correction_has_selection",
        ),
        (
            _reply(correction_kind="none", ids=["cog:doubao-cat"]),
            "non_correction_has_selection",
        ),
        (
            _reply(correction_kind="replacement", ids=[], replacement_span=(0, 1)),
            "correction_has_no_selection",
        ),
        (
            _reply(correction_kind="retract_only", ids=[]),
            "correction_has_no_selection",
        ),
        (
            _reply(correction_kind="invalid", ids=["cog:doubao-cat"]),
            "invalid_correction_kind",
        ),
    ),
)
def test_correction_kind_and_span_cross_fields_fail_closed(
    reply: str,
    expected_code: str,
) -> None:
    with pytest.raises(NaturalCorrectionError) as caught:
        NaturalCorrectionProposer(_ScriptedLLM(reply)).propose(
            _view(),
            _turn(),
            None,
        )

    assert caught.value.code == expected_code


def test_response_format_is_strict_and_contains_no_free_text_output_field() -> None:
    response_format = NATURAL_CORRECTION_RESPONSE_FORMAT
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["strict"] is True
    schema = json_schema["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "correction_kind",
        "prior_cognition_ids",
        "structure_hints",
        "replacement_value_span",
    }
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["correction_kind"]["enum"] == [
        "none",
        "replacement",
        "retract_only",
    ]
    assert schema["properties"]["structure_hints"]["items"]["enum"] == [
        "entity_reclassification"
    ]
    span_object = schema["properties"]["replacement_value_span"]["anyOf"][1]
    assert span_object["additionalProperties"] is False
    assert set(span_object["properties"]) == {
        "start_codepoint",
        "end_codepoint",
    }
    assert set(span_object["required"]) == set(span_object["properties"])


def test_prompt_contract_requires_exhaustive_conflict_selection_and_preserves_unrelated_attributes() -> (
    None
):
    llm = _ScriptedLLM(_reply(correction_kind="none"))

    NaturalCorrectionProposer(llm).propose(
        _view(),
        _turn("其实前面的身份和类别说法需要更正"),
        None,
    )

    prompt = json.loads(llm.messages[0].content)
    policy = prompt["selection_policy"]
    assert policy["review_scope"] == "all_current_candidates"
    assert policy["stop_after_first_match"] is False
    assert policy["select_same_target_conflict_or_materially_misleading"] is True
    assert policy["identity_or_category_correction_includes"] == [
        "direct_category_claims",
        "coarse_identity_claims_replaced_by_the_new_precise_statement",
    ]
    assert policy["preserve_unrelated_same_target_attributes"] is True
    assert policy["correction_kinds"] == ["replacement", "retract_only"]
    assert policy["replacement_requires_explicit_new_value_span"] is True
    assert policy["retract_only_forbids_replacement_value_span"] is True
    assert policy["evaluation_agreement_or_disagreement_is_evidence"] is True

    conflict_boundary, retract_boundary, replacement_boundary = prompt[
        "boundary_examples"
    ]
    assert conflict_boundary["correction_kind"] == "none"
    assert "opposing Evidence" in conflict_boundary["reason"]
    assert retract_boundary["correction_kind"] == "retract_only"
    assert retract_boundary["replacement_value_span"] is None
    assert "no new value" in retract_boundary["reason"]
    assert replacement_boundary["correction_kind"] == "replacement"
    assert replacement_boundary["replacement_value_span"] == {
        "start_codepoint": 59,
        "end_codepoint": 69,
    }
    assert replacement_boundary["current_user_turn"][59:69] == "unreliable"
    assert "supplies a new replacement value" in replacement_boundary["reason"]

    rules = "\n".join(prompt["rules"])
    assert "不得框出被引用、被撤回或被称为错误的旧值" in rules
    assert "纯撤回时不得从旧值引语、否定词或上下文虚构一个新值" in rules

    example = prompt["generic_example"]
    serialized_example = json.dumps(example, ensure_ascii=False)
    assert "豆包" not in serialized_example and "二五" not in serialized_example
    assert example["existing_current_cognitions"] == [
        "A was recorded as the owner's dog.",
        "A was coarsely recorded as not real.",
        "A likes background noise.",
        "B was recorded as the real dog.",
    ]
    assert example["current_user_turn"] == "A is an AI, not a dog; B is the real dog."
    assert example["select"] == [
        "A was recorded as the owner's dog.",
        "A was coarsely recorded as not real.",
    ]
    assert example["correction_kind"] == "replacement"
    assert example["replacement_value_span"] == {
        "start_codepoint": 8,
        "end_codepoint": 10,
    }
    assert example["current_user_turn"][8:10] == "AI"
    assert example["structure_hints"] == ["entity_reclassification"]
    assert example["keep_current"] == [
        "A likes background noise.",
        "B was recorded as the real dog.",
    ]
