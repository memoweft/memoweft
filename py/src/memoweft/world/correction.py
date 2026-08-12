"""Fail-closed proposal boundary for corrections expressed in ordinary chat.

The model is deliberately restricted to classification: it may say that the
current user turn corrects one to four *current* cognition IDs and may attach a
small, closed structural hint.  Trusted code owns everything that could become
memory: the replacement text and evidence ID come verbatim from the current
user turn, while target and perspective come from the selected cognitions.

This module only returns a plan.  It never stages or applies a review.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Final, Literal, NoReturn, cast
import unicodedata

from ..llm import ChatMessage, LLMClient
from .extractor import ConversationTurn
from .loop import MemoryView
from .model import MemoryTarget, Perspective, WorldCognition


StructureHint = Literal["entity_reclassification"]

_CHINESE_CORRECTION_CUES: Final[tuple[str, ...]] = (
    "其实",
    "不是",
    "并非",
    "才是",
    "而是",
    "不对",
    "说错",
    "更正",
    "纠正",
    "应该是",
    "我指的是",
    "我的意思是",
)
_ENGLISH_CORRECTION_CUE = re.compile(
    r"(?<![a-z0-9_])(?:actually|not|isn't|aren't|instead|rather|i\s+mean|correction|was\s+wrong)(?![a-z0-9_])"
)


NATURAL_CORRECTION_RESPONSE_FORMAT: Final[dict[str, Any]] = {
    "type": "json_schema",
    "json_schema": {
        "name": "memoweft_natural_correction_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "is_correction": {"type": "boolean"},
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
            },
            "required": ["is_correction", "prior_cognition_ids", "structure_hints"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True, slots=True)
class CorrectionPlan:
    """A host-materialized, review-only replacement proposal."""

    prior_cognition_ids: tuple[str, ...]
    replacement_text: str
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
        object.__setattr__(self, "args", (f"natural correction proposal rejected: {code}",))


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
        if not _has_explicit_correction_cue(current_user_turn.content):
            return None
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

        proposal = _decode(raw)
        if not proposal.is_correction:
            return None

        selected = _resolve_current(view, proposal.prior_cognition_ids)
        first = selected[0]
        if any(item.target != first.target for item in selected[1:]):
            raise NaturalCorrectionError("mixed_targets")
        if any(item.perspective != first.perspective for item in selected[1:]):
            raise NaturalCorrectionError("mixed_perspectives")
        if "entity_reclassification" in proposal.structure_hints and first.target.kind != "entity":
            raise NaturalCorrectionError("invalid_structure_hint_target")

        return CorrectionPlan(
            prior_cognition_ids=proposal.prior_cognition_ids,
            replacement_text=current_user_turn.content,
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
        if preceding_assistant_turn.conversation_id != current_user_turn.conversation_id:
            raise NaturalCorrectionError("conversation_mismatch")


@dataclass(frozen=True, slots=True)
class _ModelProposal:
    is_correction: bool
    prior_cognition_ids: tuple[str, ...]
    structure_hints: tuple[StructureHint, ...]


def _decode(raw: object) -> _ModelProposal:
    if not isinstance(raw, str):
        raise NaturalCorrectionError("response_not_text")
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise NaturalCorrectionError("invalid_json") from exc
    if not isinstance(value, dict):
        raise NaturalCorrectionError("response_not_object")
    expected = {"is_correction", "prior_cognition_ids", "structure_hints"}
    if set(value) != expected:
        raise NaturalCorrectionError("invalid_top_level_fields")

    flag = value["is_correction"]
    raw_ids = value["prior_cognition_ids"]
    raw_hints = value["structure_hints"]
    if type(flag) is not bool:
        raise NaturalCorrectionError("invalid_correction_flag")
    if not isinstance(raw_ids, list) or any(type(item) is not str or not item.strip() for item in raw_ids):
        raise NaturalCorrectionError("invalid_cognition_ids")
    ids = cast(tuple[str, ...], tuple(raw_ids))
    if len(ids) != len(set(ids)):
        raise NaturalCorrectionError("duplicate_cognition_ids")
    if len(ids) > 4:
        raise NaturalCorrectionError("too_many_cognition_ids")

    if not isinstance(raw_hints, list) or any(type(item) is not str for item in raw_hints):
        raise NaturalCorrectionError("invalid_structure_hints")
    hints_as_strings = cast(tuple[str, ...], tuple(raw_hints))
    if len(hints_as_strings) != len(set(hints_as_strings)) or len(hints_as_strings) > 1:
        raise NaturalCorrectionError("invalid_structure_hints")
    if any(item != "entity_reclassification" for item in hints_as_strings):
        raise NaturalCorrectionError("invalid_structure_hints")
    hints = cast(tuple[StructureHint, ...], hints_as_strings)

    if not flag:
        if ids or hints:
            raise NaturalCorrectionError("non_correction_has_selection")
        return _ModelProposal(False, (), ())
    if not ids:
        raise NaturalCorrectionError("correction_has_no_selection")
    return _ModelProposal(True, ids, hints)


def _resolve_current(view: MemoryView, ids: tuple[str, ...]) -> tuple[WorldCognition, ...]:
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
    candidates = [_cognition_prompt_data(view, cognition) for cognition in sorted(current, key=lambda item: item.id)]
    contract = {
        "task": "判断当前用户消息是否在纠正已有记忆，只选择要被替换的当前 cognition ID。",
        "rules": [
            "当前 user 消息是唯一可用证据；上一条 assistant 消息只用于理解上下文，绝不是证据。",
            "不要生成、改写或摘录 replacement 文本；宿主会逐字使用当前 user 消息。",
            "不要生成 evidence、target、perspective、confidence 或新的记忆 ID。",
            "若不是纠正，is_correction=false，两个数组必须为空。",
            "同一实体的新属性、补充细节或对旧事实的确认都不是纠正，不得选择 prior。",
            "只有当前用户明确否定、替换或撤回旧记忆时，才选择被纠正的 prior。",
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
        },
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


def _cognition_prompt_data(view: MemoryView, cognition: WorldCognition) -> dict[str, object]:
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
    }


def _has_explicit_correction_cue(content: str) -> bool:
    normalized = unicodedata.normalize("NFKC", content).casefold().replace("’", "'")
    return any(cue in normalized for cue in _CHINESE_CORRECTION_CUES) or _ENGLISH_CORRECTION_CUE.search(normalized) is not None


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-standard JSON constant")


__all__ = [
    "CorrectionPlan",
    "NATURAL_CORRECTION_RESPONSE_FORMAT",
    "NaturalCorrectionError",
    "NaturalCorrectionProposer",
    "StructureHint",
]
