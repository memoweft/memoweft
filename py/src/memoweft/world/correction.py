"""Fail-closed proposal boundary for corrections expressed in ordinary chat.

The model is deliberately restricted to classification: it may say that the
current user turn replaces or merely retracts one to four *current* cognition
IDs, point at the exact span of an explicitly supplied replacement value, and
attach a small, closed structural hint.  Trusted code owns everything that
could become memory: replacement text and span text come verbatim from the
current user turn, while target and perspective come from selected cognitions.

This module only returns a plan.  It never stages or applies a review.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Final, Literal, NoReturn, cast

from ..llm import ChatMessage, LLMClient
from .extractor import ConversationTurn
from .loop import MemoryView
from .model import MemoryTarget, Perspective, WorldCognition


StructureHint = Literal["entity_reclassification"]
CorrectionKind = Literal["replacement", "retract_only"]
_ModelCorrectionKind = Literal["none", "replacement", "retract_only"]


NATURAL_CORRECTION_RESPONSE_FORMAT: Final[dict[str, Any]] = {
    "type": "json_schema",
    "json_schema": {
        "name": "memoweft_natural_correction_v2",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "correction_kind": {
                    "type": "string",
                    "enum": ["none", "replacement", "retract_only"],
                },
                "prior_cognition_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 0,
                    "maxItems": 4,
                    "uniqueItems": True,
                },
                "structure_hints": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["entity_reclassification"]},
                    "minItems": 0,
                    "maxItems": 1,
                    "uniqueItems": True,
                },
                "replacement_value_span": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "properties": {
                                "start_codepoint": {
                                    "type": "integer",
                                    "minimum": 0,
                                },
                                "end_codepoint": {
                                    "type": "integer",
                                    "minimum": 1,
                                },
                            },
                            "required": ["start_codepoint", "end_codepoint"],
                            "additionalProperties": False,
                        },
                    ]
                },
            },
            "required": [
                "correction_kind",
                "prior_cognition_ids",
                "structure_hints",
                "replacement_value_span",
            ],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True, slots=True)
class ReplacementValueSpan:
    """One exact, host-materialized replacement value from current Evidence."""

    text: str
    start_codepoint: int
    end_codepoint: int


@dataclass(frozen=True, slots=True)
class CorrectionPlan:
    """A host-materialized, review-only correction proposal."""

    correction_kind: CorrectionKind
    prior_cognition_ids: tuple[str, ...]
    replacement_text: str
    replacement_value_span: ReplacementValueSpan | None
    evidence_id: str
    target: MemoryTarget
    perspective: Perspective
    structure_hints: tuple[StructureHint, ...] = ()


@dataclass(frozen=True, slots=True, init=False)
class NaturalCorrectionError(ValueError):
    """Safe rejection whose message never includes model or conversation text."""

    code: str

    def __init__(self, code: str) -> None:
        object.__setattr__(self, "code", code)
        object.__setattr__(
            self, "args", (f"natural correction proposal rejected: {code}",)
        )


class NaturalCorrectionProposer:
    """Ask an injected model for IDs, then materialize only trusted fields."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def propose(
        self,
        view: MemoryView,
        current_user_turn: ConversationTurn,
        preceding_assistant_turn: ConversationTurn | None,
    ) -> CorrectionPlan | None:
        self._validate_turns(current_user_turn, preceding_assistant_turn)
        current = view.current_cognitions
        if not current:
            return None

        messages = [ChatMessage("system", _system_prompt(view, current))]
        if preceding_assistant_turn is not None:
            messages.append(ChatMessage("assistant", preceding_assistant_turn.content))
        messages.append(ChatMessage("user", current_user_turn.content))
        try:
            raw = self._llm.chat(messages)
        except Exception as exc:
            raise NaturalCorrectionError("model_call_failed") from exc

        proposal = _decode(raw, current_user_turn.content)
        if proposal.correction_kind == "none":
            return None

        selected = _resolve_current(view, proposal.prior_cognition_ids)
        first = selected[0]
        if any(item.target != first.target for item in selected[1:]):
            raise NaturalCorrectionError("mixed_targets")
        if any(item.perspective != first.perspective for item in selected[1:]):
            raise NaturalCorrectionError("mixed_perspectives")
        if (
            "entity_reclassification" in proposal.structure_hints
            and first.target.kind != "entity"
        ):
            raise NaturalCorrectionError("invalid_structure_hint_target")

        return CorrectionPlan(
            correction_kind=proposal.correction_kind,
            prior_cognition_ids=proposal.prior_cognition_ids,
            replacement_text=current_user_turn.content,
            replacement_value_span=proposal.replacement_value_span,
            evidence_id=current_user_turn.turn_id,
            target=first.target,
            perspective=first.perspective,
            structure_hints=proposal.structure_hints,
        )

    @staticmethod
    def _validate_turns(
        current_user_turn: ConversationTurn,
        preceding_assistant_turn: ConversationTurn | None,
    ) -> None:
        if current_user_turn.role != "user":
            raise NaturalCorrectionError("current_turn_not_user")
        if not current_user_turn.turn_id.strip():
            raise NaturalCorrectionError("empty_evidence_id")
        if not current_user_turn.content.strip():
            raise NaturalCorrectionError("empty_replacement_text")
        if preceding_assistant_turn is None:
            return
        if preceding_assistant_turn.role != "assistant":
            raise NaturalCorrectionError("preceding_turn_not_assistant")
        if (
            preceding_assistant_turn.conversation_id
            != current_user_turn.conversation_id
        ):
            raise NaturalCorrectionError("conversation_mismatch")


@dataclass(frozen=True, slots=True)
class _ModelProposal:
    correction_kind: _ModelCorrectionKind
    prior_cognition_ids: tuple[str, ...]
    structure_hints: tuple[StructureHint, ...]
    replacement_value_span: ReplacementValueSpan | None


def _decode(raw: object, evidence_content: str) -> _ModelProposal:
    if not isinstance(raw, str):
        raise NaturalCorrectionError("response_not_text")
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise NaturalCorrectionError("invalid_json") from exc
    if not isinstance(value, dict):
        raise NaturalCorrectionError("response_not_object")
    expected = {
        "correction_kind",
        "prior_cognition_ids",
        "structure_hints",
        "replacement_value_span",
    }
    if set(value) != expected:
        raise NaturalCorrectionError("invalid_top_level_fields")

    kind = value["correction_kind"]
    raw_ids = value["prior_cognition_ids"]
    raw_hints = value["structure_hints"]
    raw_span = value["replacement_value_span"]
    if not isinstance(kind, str) or kind not in {
        "none",
        "replacement",
        "retract_only",
    }:
        raise NaturalCorrectionError("invalid_correction_kind")
    if not isinstance(raw_ids, list) or any(
        type(item) is not str or not item.strip() for item in raw_ids
    ):
        raise NaturalCorrectionError("invalid_cognition_ids")
    ids = cast(tuple[str, ...], tuple(raw_ids))
    if len(ids) != len(set(ids)):
        raise NaturalCorrectionError("duplicate_cognition_ids")
    if len(ids) > 4:
        raise NaturalCorrectionError("too_many_cognition_ids")

    if not isinstance(raw_hints, list) or any(
        type(item) is not str for item in raw_hints
    ):
        raise NaturalCorrectionError("invalid_structure_hints")
    hints_as_strings = cast(tuple[str, ...], tuple(raw_hints))
    if len(hints_as_strings) != len(set(hints_as_strings)) or len(hints_as_strings) > 1:
        raise NaturalCorrectionError("invalid_structure_hints")
    if any(item != "entity_reclassification" for item in hints_as_strings):
        raise NaturalCorrectionError("invalid_structure_hints")
    hints = cast(tuple[StructureHint, ...], hints_as_strings)

    replacement_value_span: ReplacementValueSpan | None = None
    if raw_span is not None:
        if not isinstance(raw_span, dict) or set(raw_span) != {
            "start_codepoint",
            "end_codepoint",
        }:
            raise NaturalCorrectionError("invalid_replacement_value_span")
        start = raw_span["start_codepoint"]
        end = raw_span["end_codepoint"]
        if (
            type(start) is not int
            or type(end) is not int
            or start < 0
            or start >= end
            or end > len(evidence_content)
        ):
            raise NaturalCorrectionError("invalid_replacement_value_span")
        span_text = evidence_content[start:end]
        if not span_text.strip():
            raise NaturalCorrectionError("empty_replacement_value_span")
        replacement_value_span = ReplacementValueSpan(span_text, start, end)

    typed_kind = cast(_ModelCorrectionKind, kind)
    if kind == "none":
        if ids or hints or replacement_value_span is not None:
            raise NaturalCorrectionError("non_correction_has_selection")
        return _ModelProposal("none", (), (), None)
    if not ids:
        raise NaturalCorrectionError("correction_has_no_selection")
    if kind == "replacement" and replacement_value_span is None:
        raise NaturalCorrectionError("replacement_has_no_value_span")
    if kind == "retract_only" and replacement_value_span is not None:
        raise NaturalCorrectionError("retract_only_has_value_span")
    return _ModelProposal(typed_kind, ids, hints, replacement_value_span)


def _resolve_current(
    view: MemoryView, ids: tuple[str, ...]
) -> tuple[WorldCognition, ...]:
    current_by_id = {item.id: item for item in view.current_cognitions}
    selected: list[WorldCognition] = []
    for cognition_id in ids:
        if cognition_id in view.superseded_cognition_ids:
            raise NaturalCorrectionError("superseded_cognition")
        cognition = current_by_id.get(cognition_id)
        if cognition is None:
            raise NaturalCorrectionError("unknown_cognition")
        selected.append(cognition)
    return tuple(selected)


def _system_prompt(view: MemoryView, current: tuple[WorldCognition, ...]) -> str:
    candidates = [
        _cognition_prompt_data(view, cognition)
        for cognition in sorted(current, key=lambda item: item.id)
    ]
    contract = {
        "task": "判断当前用户消息是替换、只撤回，还是不纠正已有记忆；只选择对应的当前 cognition ID。",
        "rules": [
            "当前 user 消息是唯一可用证据；上一条 assistant 消息只用于理解上下文，绝不是证据。",
            "不要生成、改写或摘录 replacement 文本；宿主会逐字使用当前 user 消息。",
            "不要生成 evidence、target、perspective、confidence 或新的记忆 ID。",
            "若不是纠正，correction_kind=none，两个数组必须为空，replacement_value_span 必须为 null。",
            "同一实体的新属性、补充细节或对旧事实的确认都不是纠正，不得选择 prior。",
            "correction_kind=replacement 只表示用户声明旧记忆写错或应被替换，并且当前消息明确给出了实际的新替代值；必须选择对应 prior，并给出新值在当前 user 消息中的精确 Python codepoint 半开区间。",
            "replacement_value_span 只能指向用户实际新说出的值。评价替换要只框出新的评价值本身，而不是整句；不得框出被引用、被撤回或被称为错误的旧值，也不得框出‘撤回’‘更正’等动作词。",
            "correction_kind=retract_only 表示用户明确要求旧 cognition 不再 current，但没有给出任何新替代值；必须选择对应 prior，replacement_value_span 必须为 null。纯撤回时不得从旧值引语、否定词或上下文虚构一个新值。",
            "用户仅仅不同意、反对、质疑、再次赞同或支持一个评价命题，不是 replacement correction；这是同一 cognition 的 contradict/support Evidence，必须 correction_kind=none。",
            "否定词、转折词或 actually/not/不是/其实等表面词本身都不能证明是 correction；必须按整句语义区分替换旧记忆与表达证据立场。",
            "必须逐条审阅全部 current candidates，不能命中第一条后停止。",
            "选择每一条若继续作为 current 会与本轮纠正冲突或产生实质误导的同 target prior。",
            "身份或类别纠正既要选择直接类别声明，也要选择被新精确说法取代的假的、不真实或其他类别等粗略身份说法。",
            "不得顺带选择同 target 下与本轮纠正无关的属性。",
            "若是纠正，只能选择 1 到 4 个候选 ID；多个 ID 必须属于同一 target 和同一 perspective。",
            "structure_hints 只能为空，或在实体类别也需复核时选择 entity_reclassification。",
        ],
        "selection_policy": {
            "review_scope": "all_current_candidates",
            "stop_after_first_match": False,
            "select_same_target_conflict_or_materially_misleading": True,
            "identity_or_category_correction_includes": [
                "direct_category_claims",
                "coarse_identity_claims_replaced_by_the_new_precise_statement",
            ],
            "preserve_unrelated_same_target_attributes": True,
            "correction_kinds": ["replacement", "retract_only"],
            "replacement_requires_explicit_new_value_span": True,
            "retract_only_forbids_replacement_value_span": True,
            "evaluation_agreement_or_disagreement_is_evidence": True,
        },
        "boundary_examples": [
            {
                "current_cognition": "The owner evaluates a relationship as reliable.",
                "current_user_turn": "I do not agree that this relationship is reliable.",
                "correction_kind": "none",
                "reason": "opposing Evidence; keep the cognition and expose the conflict",
            },
            {
                "current_cognition": "The owner evaluates a relationship as reliable.",
                "current_user_turn": "Withdraw my earlier judgment that it was reliable.",
                "correction_kind": "retract_only",
                "replacement_value_span": None,
                "reason": "explicitly retracts the earlier memory but supplies no new value",
            },
            {
                "current_cognition": "The owner evaluates a relationship as reliable.",
                "current_user_turn": "My earlier reliability judgment was wrong; replace it with unreliable.",
                "correction_kind": "replacement",
                "replacement_value_span": {
                    "start_codepoint": 59,
                    "end_codepoint": 69,
                },
                "reason": "explicitly retracts and supplies a new replacement value",
            },
        ],
        "generic_example": {
            "existing_current_cognitions": [
                "A was recorded as the owner's dog.",
                "A was coarsely recorded as not real.",
                "A likes background noise.",
                "B was recorded as the real dog.",
            ],
            "current_user_turn": "A is an AI, not a dog; B is the real dog.",
            "select": [
                "A was recorded as the owner's dog.",
                "A was coarsely recorded as not real.",
            ],
            "correction_kind": "replacement",
            "replacement_value_span": {
                "start_codepoint": 8,
                "end_codepoint": 10,
            },
            "structure_hints": ["entity_reclassification"],
            "keep_current": [
                "A likes background noise.",
                "B was recorded as the real dog.",
            ],
            "instruction": "The example uses descriptions only. Output IDs solely from current_cognition_candidates.",
        },
        "current_cognition_candidates": candidates,
    }
    return json.dumps(contract, ensure_ascii=False, separators=(",", ":"))


def _cognition_prompt_data(
    view: MemoryView, cognition: WorldCognition
) -> dict[str, object]:
    target_entity: dict[str, str] | None = None
    if cognition.target.kind == "entity":
        entity = view.graph.entities.get(cognition.target.id)
        if entity is not None:
            target_entity = {
                "canonical_name": entity.canonical_name,
                "kind": entity.kind,
            }
    return {
        "id": cognition.id,
        "content": cognition.content,
        "target": {"kind": cognition.target.kind, "id": cognition.target.id},
        "target_entity": target_entity,
        "perspective": {
            "kind": cognition.perspective.kind,
            "holder_entity_ids": list(cognition.perspective.holder_entity_ids),
        },
        "structured_claim": (
            None
            if cognition.structured_claim is None
            else {
                "statement_kind": cognition.structured_claim.statement_kind,
                "predicate": cognition.structured_claim.predicate,
                "value": cognition.structured_claim.value,
                "polarity": cognition.structured_claim.polarity,
                "epistemic_status": cognition.structured_claim.epistemic_status,
            }
        ),
    }


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-standard JSON constant")


__all__ = [
    "CorrectionPlan",
    "CorrectionKind",
    "NATURAL_CORRECTION_RESPONSE_FORMAT",
    "NaturalCorrectionError",
    "NaturalCorrectionProposer",
    "ReplacementValueSpan",
    "StructureHint",
]
