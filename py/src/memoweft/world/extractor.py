"""Stage-1, review-only extraction from a turn ledger to a ``WorldDelta``.

This module is deliberately a narrow boundary: it serializes a *read-only*
world and a role-preserving conversation to the LLM, decodes one strictly
specified JSON object, then delegates all graph/domain checks to
``WorldDelta.validate_against``.  It never applies a delta or writes evidence.
"""
from __future__ import annotations

import json
import copy
import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Collection, Literal, Mapping, Sequence, cast

from ..confidence import compute_confidence, derive_cred_status
from ..llm.client import ChatMessage, LLMClient
from ..types import (
    ConfidenceInputs,
    ContentType,
    EvidenceLink,
    FormedBy,
)
from .delta import (
    ClaimSpan,
    FormationContentBinding,
    FormationSourceTrace,
    FormationTrace,
    SemanticUncertainty,
    UnresolvedReference,
    WorldDelta,
    WorldDeltaValidationError,
)
from .entity_resolution import (
    AcceptedEntityReference,
    EntityIdentityValidationError,
    EntityReferenceResolver,
    ReferenceMention,
)
from .graph import MemoryWorldGraph
from .model import (
    Entity,
    EventFacet,
    EventParticipant,
    MemoryTarget,
    Perspective,
    Relationship,
    WorldCognition,
    WorldEvent,
)
from .semantics import is_interpersonal_conflict_type


TurnRole = Literal["user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """An immutable, role-preserving input record for Stage 1 extraction."""

    turn_id: str
    conversation_id: str
    role: TurnRole
    content: str
    occurred_at: str


@dataclass(frozen=True, slots=True, init=False)
class WorldExtractionError(ValueError):
    """Safe terminal extraction failure: no model response or evidence is kept."""

    codes: tuple[str, ...]
    attempts: int

    def __init__(self, codes: Collection[str], attempts: int) -> None:
        safe_codes = tuple(codes)
        object.__setattr__(self, "codes", safe_codes)
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(self, "args", (f"world extraction failed after {attempts} attempt(s): {', '.join(safe_codes)}",))


@dataclass(frozen=True, slots=True)
class _DecodeFailure(Exception):
    code: str
    path: str

    @property
    def safe_code(self) -> str:
        return f"{self.code}@{self.path}"


@dataclass(frozen=True, slots=True)
class _SourceProposal:
    """Model-proposed resolution metadata, consumed before the final delta exists."""

    segment_id: str
    relation: str
    proposition_origin: str
    response_act: str
    path: str


@dataclass(frozen=True, slots=True)
class _EligibleSegment:
    """Caller-owned, complete user-Evidence segment used only at extraction time."""

    segment_id: str
    evidence_id: str
    text: str
    start_codepoint: int
    end_codepoint: int
    source_content: str


@dataclass(frozen=True, slots=True)
class _OwnerThirdPartyIntroduction:
    """One explicit owner-to-person introduction found in current Evidence."""

    segment_id: str
    evidence_id: str
    mention: str
    action_text: str | None


@dataclass(frozen=True, slots=True)
class _OwnerAttributedFeedback:
    """One exact report that another person evaluated the owner."""

    segment_id: str
    evidence_id: str
    text: str


@dataclass(frozen=True, slots=True)
class _OwnerPetIntroduction:
    """One exact durable owner-to-pet introduction in current Evidence."""

    segment_id: str
    evidence_id: str
    mention: str


@dataclass(frozen=True, slots=True)
class _ReportedThirdPartyAction:
    """One exact third-party lived-action span inside current user Evidence."""

    segment: _EligibleSegment
    recipient_mention: str


@dataclass(frozen=True, slots=True)
class _ThirdPartyReferenceContext:
    """Trusted context-only resolution for one current third-person pronoun."""

    state: Literal["not-applicable", "resolved", "unresolved", "ambiguous"]
    segment_id: str | None = None
    evidence_id: str | None = None
    entity_id: str | None = None
    text: str | None = None
    mention: str | None = None
    candidate_binding_allowed: bool = False


_TOP_KEYS = frozenset(
    {
        "world_id",
        "new_entities",
        "new_relationships",
        "new_events",
        "new_cognitions",
        "unresolved_references",
        "semantic_uncertainties",
    }
)
_CONTENT_TYPES = frozenset({"fact", "preference", "goal", "project", "state", "trait", "hypothesis", "trend"})
_TARGET_KINDS = frozenset({"world", "entity", "relationship", "event"})
_PERSPECTIVE_KINDS = frozenset({"entity", "joint", "system"})
_EVIDENCE_RELATIONS = frozenset({"support", "contradict"})
_PROPOSITION_ORIGINS = frozenset({"user_stated", "assistant_proposed"})
_RESPONSE_ACTS = frozenset({"affirm", "negate", "select", "elaborate", "ask", "none", "other"})
_LIFECYCLE_CLAIM_RULE = (
    "LIFECYCLE CLAIM RULE: valid_at and invalid_at are lifecycle claims. "
    "Set each to null unless eligible Evidence explicitly states the relevant start or end; "
    "never invent either."
)
_CONFLICT_RELATIONSHIP_RECIPE = (
    "CONDITIONAL CONFLICT RELATIONSHIP TRANSACTION: Event positions alone never justify durable Cognitions. "
    "Use this transaction only when eligible Evidence independently states one durable, scope-limited direct "
    "claim for each endpoint of one owner-linked Relationship, and both complete direct-claim segments belong "
    "to the same Evidence ID. Then emit together: (1) exactly one direct cognition for each endpoint Entity, "
    "with model_inferred false, content null, one distinct complete user-stated support segment, and the same "
    "non-empty scope; (2) exactly one inferred hypothesis targeting that Relationship, with model_inferred true, "
    "content null, perspective null, the same scope, and exactly one support source. That sole source must be a "
    "third, distinct substantive conflict-or-contrast segment from the same shared Evidence ID; relationship-"
    "establishing Evidence alone is not grounding. If either endpoint claim is not independently durable/direct, "
    "or the two claims exist only in different Evidence IDs, omit the entire three-Cognition projection "
    "transaction; never invent a side, merge Evidence, or add multiple inference sources. The conflict Event may "
    "remain when its own event requirements are supported. A separately and directly supported Relationship fact "
    "may coexist, but it never substitutes for the inferred projection. Trusted code derives exact bindings and "
    "relationship content from the proposed transaction; it never invents the model's semantic projection."
)

# The wire-level source proposal contract is intentionally versioned separately
# from the world model: ``formed_by`` is no longer model-owned.
FORMATION_CONTRACT_VERSION = "world-formed-by@12"
_ORDINARY_MAX_EXTRACTION_ATTEMPTS = 2
_MAX_EXTRACTION_ATTEMPTS = 3

# This is deliberately a high-precision retention floor, not a second semantic
# extractor. It decides only whether an otherwise valid model delta is too empty
# to review for an explicit owner statement. The model still supplies every
# entity, relationship, event, and cognition it proposes.
_ENGLISH_OWNER_MEMORY_PROPOSITION = re.compile(
    r"\b(?:"
    r"i(?:['’]m|\s+am)\s+(?!(?:not\s+)?(?:sure|certain|asking)\b)"
    r"(?:a(?:n)?\b|from\b|based\b|currently\b|usually\b|normally\b|"
    r"generally\b|always\b|off\b|on\s+leave\b|working\b|tired\b|busy\b|"
    r"free\b|unwell\b|sick\b|happy\b|sad\b)|"
    r"i\s+(?:(?:currently|usually|normally|generally|always|often)\s+)?"
    r"(?:live\b|work\b|study\b|prefer\b|like\b|love\b|dislike\b|hate\b|"
    r"own\b|keep\b|plan\b|intend\b|aim\b|need\s+to\b|want\s+to\b)|"
    r"i\s+have\s+(?!a(?:n)?\s+(?:question|issue|problem)\b|questions?\b)"
    r"(?:a\b|an\b|\d|two\b|three\b|my\b|the\b))",
    re.IGNORECASE,
)
_CHINESE_OWNER_DIRECT_PROPOSITION = re.compile(
    r"我(?:现在|目前|平时|通常|一般|默认|一直|最近)?"
    r"(?:是|有(?!一?个?(?:问题|疑问)|事想问)|住(?:在)?|喜欢|偏好|爱|不喜欢|讨厌|"
    r"习惯|通常|平时|一般|默认|一直|目前|正在|计划|打算|准备|希望|目标|"
    r"负责|从事|上班|工作|休息|双休|单休|请假|养(?:了)?|拥有|"
    r"(?:很|非常|有点)?(?:累|忙|闲|生病|不舒服|开心|难过|焦虑|压力大))"
)
_CHINESE_OWNER_PROJECT_PROPOSITION = re.compile(
    r"我(?:正在|目前|一直)?(?:在)?(?:做|开发|维护|负责|从事)(?!什么|吗|呢)(?:[^，。！？?]{1,32})"
)
_CHINESE_OWNER_GOAL_PROPOSITION = re.compile(
    r"我想(?:要|去|成为|完成|做|学习|开始|继续)(?:[^，。！？?]{1,32})"
)
_CHINESE_OWNER_SCHEDULE_PROPOSITION = re.compile(
    r"我(?!妈|妈妈|爸|爸爸|父母|朋友|同事|老板|室友|家人)"
    r"(?:这周|本周|这个(?:周|星期)|今天|目前|现在|最近|平时|通常|一般|默认|一直)?"
    r"(?:[^，。！？?]{0,16})(?:双休|单休|请假|上班|休息|工作)"
)
_CHINESE_OWNER_NAMING_PROPOSITION = re.compile(
    r"(?:^|所以|因此|于是|因而|故而|而|那(?:么)?)\s*(?:"
    r"我(?:的)?(?:网名|昵称|用户名|账号名|游戏名|显示名|ID|代号|笔名|名字)"
    r"(?:就|便|也|才|一直|现在|目前)?(?:叫|叫作|叫做|是|取(?:成|为)?|改成|设成|用了?)"
    r"|我(?:就|便|也|才|一直|现在|目前)?(?:"
    r"给自己(?:的)?(?:网名|昵称|用户名|账号名|游戏名|显示名|ID|代号|笔名|名字)?"
    r"|把(?:自己(?:的)?(?:网名|昵称|用户名|账号名|游戏名|显示名|ID|代号|笔名|名字)?"
    r"|(?:网名|昵称|用户名|账号名|游戏名|显示名|ID|代号|笔名|名字)))"
    r"(?:取(?:了)?(?:个)?|起(?:了)?(?:个)?|改(?:成|为)?|叫(?:作|做)?|用(?:了)?)"
    r"|我(?:就|便|也|才|一直|现在|目前)?(?:用|拿)[^，,。！？?;；]{1,24}"
    r"(?:当|作为)(?:我(?:的)?)?(?:网名|昵称|用户名|账号名|游戏名|显示名|ID|代号|笔名|名字)"
    r")",
    re.IGNORECASE,
)
_ENGLISH_OWNER_NAMING_PROPOSITION = re.compile(
    r"(?:^|\b(?:so|therefore|thus)\b\s*)(?:"
    r"my\s+(?:username|nickname|screen\s+name|handle|display\s+name|alias|codename|pen\s+name)\s+"
    r"(?:is|became|uses?)\b"
    r"|i\s+(?:use|chose|choose|picked|pick|set|named|call)\b[^,.;!?]{0,24}\b"
    r"(?:as\s+)?(?:my\s+)?(?:username|nickname|screen\s+name|handle|display\s+name|alias|codename|pen\s+name)\b"
    r")",
    re.IGNORECASE,
)
_CHINESE_OWNER_THIRD_PARTY_INTRODUCTION = re.compile(
    r"^\s*我(?:有|认识)(?:一|1)?(?:个|位)?"
    r"(?P<mention>(?:喜欢|在意|心仪)的(?:女生|男生|女孩|男孩|人))"
    r"(?P<rest>[^。！？?!;；]*)[。！？?!;；]?\s*$",
    re.IGNORECASE,
)
_CHINESE_OWNER_ATTRIBUTED_FEEDBACK = re.compile(
    r"^\s*我(?:有)?(?:一)?(?:个|位)?"
    r"(?:朋友|同事|家人|亲人|室友|老师|同学|老板)"
    r"(?:说|觉得|认为|评价(?:过)?)"
    r"我[^，,。！？?!;；]{1,24}[。！？?!;；]?\s*$",
    re.IGNORECASE,
)
_CHINESE_OWNER_PET_INTRODUCTION = re.compile(
    r"^\s*我(?:有|养(?:了)?|拥有)(?:一|1)?(?:只|个)?"
    r"(?P<mention>小?(?:猫|狗|兔子|兔|仓鼠|鸟|鹦鹉|乌龟))"
    r"[。！？?!;；]?\s*$",
    re.IGNORECASE,
)
_CHINESE_THIRD_PARTY_LIVED_ACTION = re.compile(
    r"[，,]\s*(?P<action>(?:她|他|TA)[^，,。！？?!;；]{0,12}"
    r"(?:给我|送我|请我|帮我|陪我|带我)[^，,。！？?!;；]{0,48})"
    r"(?=[，,。！？?!;；]|$)",
    re.IGNORECASE,
)
_CHINESE_REPORTED_DIRECT_THIRD_PARTY = re.compile(
    r"(?:她|他|TA)(?:还|曾经|之前|明确)?(?:说|表示|提到|告诉我)\s*[:：]\s*[\"“]?"
    r"(?P<claim>(?:她|他|TA)[^，,。！？?!;；\"”]{1,48})[\"”]?"
    r"(?=(?:[，,]\s*(?:所以)?我(?:猜|觉得|感觉|估计|推测))|[。！？?!;；]|$)",
    re.IGNORECASE,
)
_CHINESE_REPORTED_THIRD_PARTY_ACTION = re.compile(
    r"(?P<action>(?:她|他|TA)(?:还)?说(?:之前)?给"
    r"(?P<recipient>一个(?:不认识的|陌生的)?(?:网友|人|朋友|同事))"
    r"[^，,。！？?!;；]{0,16}(?:买|送|点|带|寄|付|请)(?:过|了)?"
    r"[^，,。！？?!;；]{0,24})",
    re.IGNORECASE,
)
_CORRECTION_OR_NEGATION_MARKER = re.compile(
    r"(?:不对|其实|更正|纠正|改成|不是|不再|\bnot\b|\bactually\b)",
    re.IGNORECASE,
)
_EXPLICIT_PREFERENCE_CLAIM = re.compile(
    r"(?:喜欢|偏好|热爱|"
    r"\b(?:prefer(?:s|red|ring)?|like(?:s|d)?|love(?:s|d)?)\b)",
    re.IGNORECASE,
)
_NEGATED_PREFERENCE_CLAIM = re.compile(
    r"(?:不(?:喜欢|偏好|爱|热爱)|讨厌|厌恶|"
    r"\b(?:dislike(?:s|d)?|hate(?:s|d)?)\b)",
    re.IGNORECASE,
)
_PET_DIRECT_FALLBACK_LIFECYCLE_MARKER = re.compile(
    r"(?:从[^，,。！？?!;；]{0,16}(?:起|开始)|"
    r"(?:曾经|以前|之前|不再|已经不|改名|更名|去世|死亡)|"
    r"\b(?:since|until|formerly|renamed|died)\b)",
    re.IGNORECASE,
)
_CHINESE_DIRECT_THIRD_PARTY = re.compile(
    r"^\s*[\"'“‘（(]?(?P<mention>她|他|TA|对方|"
    r"(?:这|那)(?:个|位)?(?:人|女生|男生|女孩|男孩|姑娘|朋友|同事|室友|老师|医生|同学|邻居))"
    r"(?=\s*\S)",
    re.IGNORECASE,
)
_ENGLISH_DIRECT_THIRD_PARTY = re.compile(
    r"^\s*[\"'(]?(?P<mention>she|he|they)\b",
    re.IGNORECASE,
)
_THIRD_PARTY_INFERENCE_MARKER = re.compile(
    r"(?:我(?:感觉|觉得|猜|估计|推测)|可能|也许|大概|好像|似乎|恐怕|说不定|"
    r"不确定|不知道|\bi\s+(?:think|guess|suspect)\b|\bmaybe\b|\bprobably\b|"
    r"\bseems?\b|\bappears?\b)",
    re.IGNORECASE,
)
_CLAUSE_SEPARATOR = re.compile(r"[，,;；。！？?!\r\n]+")
_ENGLISH_PURE_QUESTION = re.compile(
    r"^\s*(?:do|does|did|am|is|are|can|could|should|would|will|"
    r"where|what|when|why|how)\b",
    re.IGNORECASE,
)
_CHINESE_PURE_OWNER_EVALUATION_QUESTION = re.compile(
    r"^\s*(?:你|您)(?:觉得|认为|感觉|看)"
    r"(?:我|本人)[^，,。！？?!;；]{1,32}(?:吗|么|呢)[？?]?\s*$",
    re.IGNORECASE,
)


def _exact_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_NONEMPTY_STRING: dict[str, Any] = {"type": "string", "minLength": 1}
_NULLABLE_STRING: dict[str, Any] = {
    "anyOf": [_NONEMPTY_STRING, {"type": "null"}],
}
_EVIDENCE_IDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": _NONEMPTY_STRING,
    "minItems": 1,
    "maxItems": 4,
    "uniqueItems": True,
}
_ENTITY_SCHEMA = _exact_schema(
    {
        "id": _NONEMPTY_STRING,
        "world_id": _NONEMPTY_STRING,
        "kind": _NONEMPTY_STRING,
        "canonical_name": _NONEMPTY_STRING,
        "aliases": {
            "type": "array",
            "items": _NONEMPTY_STRING,
            "maxItems": 4,
            "uniqueItems": True,
        },
    }
)
_RELATIONSHIP_SCHEMA = _exact_schema(
    {
        "id": _NONEMPTY_STRING,
        "world_id": _NONEMPTY_STRING,
        "source_entity_id": _NONEMPTY_STRING,
        "target_entity_id": _NONEMPTY_STRING,
        "relation_type": _NONEMPTY_STRING,
        "bidirectional": {"type": "boolean"},
    }
)
_PARTICIPANT_SCHEMA = _exact_schema(
    {"entity_id": _NONEMPTY_STRING, "role": _NULLABLE_STRING}
)
_FACET_SCHEMA = _exact_schema(
    {
        "key": {
            "type": "string",
            "minLength": 1,
            "description": (
                "For an interpersonal_conflict, use only cause or position; "
                "use one position per participant, identified by about_entity_id."
            ),
        },
        "value": {
            "anyOf": [_NONEMPTY_STRING, {"type": "null"}],
            "description": (
                "For cause and every non-position facet, use a non-empty supported string. "
                "For an interpersonal_conflict position, use null."
            ),
        },
        "segment_id": {
            "anyOf": [_NONEMPTY_STRING, {"type": "null"}],
            "description": (
                "For an interpersonal_conflict position, use one distinct substantive eligible segment ID. "
                "For cause and every non-position facet, use null."
            ),
        },
        "about_entity_id": {
            "anyOf": [_NONEMPTY_STRING, {"type": "null"}],
            "description": (
                "For an interpersonal_conflict position, use the represented participant Entity ID. "
                "For an unscoped cause, use null."
            ),
        },
    }
)
_EVENT_SCHEMA = _exact_schema(
    {
        "id": _NONEMPTY_STRING,
        "world_id": _NONEMPTY_STRING,
        "event_type": _NONEMPTY_STRING,
        "summary": _NONEMPTY_STRING,
        "occurred_at": _NONEMPTY_STRING,
        "participants": {"type": "array", "items": _PARTICIPANT_SCHEMA, "maxItems": 6},
        "related_entity_ids": {
            "type": "array",
            "items": _NONEMPTY_STRING,
            "maxItems": 6,
            "uniqueItems": True,
        },
        "relationship_ids": {
            "type": "array",
            "items": _NONEMPTY_STRING,
            "maxItems": 4,
            "uniqueItems": True,
        },
        "facets": {"type": "array", "items": _FACET_SCHEMA, "maxItems": 8},
        "evidence_ids": _EVIDENCE_IDS_SCHEMA,
    }
)
_TARGET_SCHEMA = _exact_schema(
    {"kind": {"type": "string", "enum": sorted(_TARGET_KINDS)}, "id": _NONEMPTY_STRING}
)
_PERSPECTIVE_SCHEMA = _exact_schema(
    {
        "kind": {"type": "string", "enum": sorted(_PERSPECTIVE_KINDS)},
        "holder_entity_ids": {
            "type": "array",
            "items": _NONEMPTY_STRING,
            "maxItems": 4,
            "uniqueItems": True,
        },
    }
)
_NULLABLE_PERSPECTIVE_SCHEMA: dict[str, Any] = {
    "anyOf": [_PERSPECTIVE_SCHEMA, {"type": "null"}],
}
_SOURCE_SCHEMA = _exact_schema(
    {
        "segment_id": _NONEMPTY_STRING,
        "relation": {"type": "string", "enum": sorted(_EVIDENCE_RELATIONS)},
        "proposition_origin": {"type": "string", "enum": sorted(_PROPOSITION_ORIGINS)},
        "response_act": {"type": "string", "enum": sorted(_RESPONSE_ACTS)},
    }
)
_COGNITION_SCHEMA = _exact_schema(
    {
        "id": _NONEMPTY_STRING,
        "world_id": _NONEMPTY_STRING,
        "target": _TARGET_SCHEMA,
        "content": _NULLABLE_STRING,
        "content_type": {"type": "string", "enum": sorted(_CONTENT_TYPES)},
        "model_inferred": {"type": "boolean"},
        "perspective": _NULLABLE_PERSPECTIVE_SCHEMA,
        "sources": {"type": "array", "items": _SOURCE_SCHEMA, "minItems": 1, "maxItems": 4},
        "scope": _NULLABLE_STRING,
        "valid_at": _NULLABLE_STRING,
        "invalid_at": _NULLABLE_STRING,
    }
)
_UNRESOLVED_SCHEMA = _exact_schema(
    {"mention": _NONEMPTY_STRING, "evidence_ids": _EVIDENCE_IDS_SCHEMA}
)
_UNCERTAINTY_SCHEMA = _exact_schema(
    {"detail": _NONEMPTY_STRING, "evidence_ids": _EVIDENCE_IDS_SCHEMA}
)
_WORLD_DELTA_SCHEMA = _exact_schema(
    {
        "world_id": _NONEMPTY_STRING,
        "new_entities": {"type": "array", "items": _ENTITY_SCHEMA, "maxItems": 4},
        "new_relationships": {"type": "array", "items": _RELATIONSHIP_SCHEMA, "maxItems": 2},
        "new_events": {"type": "array", "items": _EVENT_SCHEMA, "maxItems": 2},
        "new_cognitions": {"type": "array", "items": _COGNITION_SCHEMA, "maxItems": 4},
        "unresolved_references": {"type": "array", "items": _UNRESOLVED_SCHEMA, "maxItems": 4},
        "semantic_uncertainties": {"type": "array", "items": _UNCERTAINTY_SCHEMA, "maxItems": 4},
    }
)
_WORLD_DELTA_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "memoweft_world_delta_v9",
        "strict": True,
        "schema": _WORLD_DELTA_SCHEMA,
    },
}


def world_delta_response_format() -> dict[str, Any]:
    """Return an isolated llama.cpp/OpenAI-compatible JSON-schema constraint."""
    return copy.deepcopy(_WORLD_DELTA_RESPONSE_FORMAT)


_SYSTEM_PROMPT = """You are worldExtract@v2, the MemoWeft Next Stage-1 world extractor.
Return ONE AND ONLY ONE JSON object.  Do not use Markdown, code fences, prose,
comments, or a JSON prefix/suffix.  The object must have exactly these top-level
snake_case keys:
world_id, new_entities, new_relationships, new_events,
new_cognitions, unresolved_references, semantic_uncertainties.

Use stable non-empty IDs: entities use kind:slug; relationships use
relationship:slug; events use event:slug; cognitions use cog:slug.  Every
object belongs to the supplied world_id.  Represent target, perspective, and
provenance separately: a cognition target is {kind,id}; its perspective is
{kind,holder_entity_ids}; and its sources are
{segment_id,relation,proposition_origin,response_act}. Every source's
proposition_origin is exactly user_stated or assistant_proposed; response_act
is exactly affirm|negate|select|elaborate|ask|none|other. These are source
proposals for the trusted extractor, not final formation authority. Each
cognition supplies model_inferred as a boolean: true only for a model inference
  instead of a carrier-derived claim. Do not emit formed_by, confidence, or
cred_status; the trusted extractor derives all three after decoding. Assistant
text is context only: it can never be cited as evidence. Cite source segment_id
only from ELIGIBLE_EVIDENCE_SEGMENTS; never invent an ID or quote assistant
text. For a non-inferred direct user claim, cognition.content must be null: the
  trusted extractor materializes it from the selected complete segment. For an
  inferred relationship hypothesis, cognition.content and cognition.perspective
  should both be null: the trusted extractor independently derives both sides
  from this delta's direct cognitions and adds a fixed scoped contrast marker.
  A structurally valid redundant perspective proposal is ignored; it is never
  provenance or formation authority. For any
  other inferred cognition or an assistant-confirmation carrier,
  cognition.content must be a non-empty string. Cite only IDs in the ELIGIBLE_EVIDENCE_SEGMENTS section;
do not self-evidence or invent sources. An inferred cognition needs at least
one support source whose complete catalog segment is substantive (contains a
character whose original Unicode code-point category is Letter or Number and
is not an acknowledgement or negation carrier; use NFKC only for acknowledgement/negation carrier matching. Carriers never ground an inference. Contradicting sources
may describe a substantive claim or a contextual negation, but never an
unbound acknowledgement. For each cognition keep only the most direct one segment from any one Evidence ID; multiple inferred sources must resolve to different Evidence IDs. Express an
ambiguous mention as {mention,evidence_ids} in unresolved_references and an
uncertain interpretation as {detail,evidence_ids} in semantic_uncertainties.

Create only objects absent from BASE collections. Every referenced entity,
relationship, or event ID must either exist in BASE or be created exactly once
in this output.

OWNER MEMORY FLOOR: MemoWeft's primary line is remembering the person who owns
this world while still allowing a broader world model. When eligible Evidence
contains an explicit user statement about the owner’s identity, attribute,
possession, preference, habit, default arrangement, work/life routine,
continuing state, goal, project, or plan, create at least one owner-related
candidate instead of returning an empty delta. A time-bounded occurrence, exception, or arrangement should be a lived Event with the owner as a
participant. A stable or usual state should be a Cognition whose target is
{kind:"entity",id:base_world.owner_entity_id}. A durable owner relationship is
also owner-related coverage; for example, a named pet may be an Entity plus an
owner endpoint Relationship, without a redundant extra cognition. An unnamed
pet keeps the user's exact animal phrase as its Entity name. Pet possession is
not an Event and must not add facets. When one
statement contrasts a current exception with a usual baseline, preserve both structures when it explicitly contains both: the time-bounded Event and the
stable owner Cognition. Questions, greetings, requests, guessing games, pure
conversation management, pure meta-language, bare acknowledgement/negation,
and a pure date/day-of-week correction may correctly return no candidate. A
question still needs a candidate when it independently states an owner fact.

OWNER-TO-THIRD-PARTY FLOOR: A directly mentioned, persistently referable person
description is a concrete Entity even without a proper name. For example, an
owner's exact phrase equivalent to "someone I like" may use that shortest
description as canonical_name. Never use a bare pronoun such as she/he/they as
canonical_name or an alias. When the owner explicitly introduces such a person,
create the person and the directed owner-to-person Relationship; romantic
interest is the owner's stated interest only and never evidence that the other
person reciprocates. If the same Evidence also states a lived action between
them, create one Event with both participants, link the Relationship, and cite
the current eligible Evidence.

THIRD-PARTY DIRECT-CARRIER RULE: A current eligible user segment that directly
states "she/he/they ..." is a direct user-attributed claim, not a model
inference. When trusted_third_party_reference.state is resolved, target exactly
its entity_id, set model_inferred false, content null, and perspective null; the
trusted extractor materializes the complete current user segment and Owner
perspective. The trusted reference is target context only and never Evidence.
When its state is unresolved or ambiguous, do not select any person; emit an
unresolved_reference citing the current eligible Evidence. Assistant text and
non-eligible historical user turns can never be cited as sources. Wording such
as maybe, seems, "I guess", or the Chinese equivalents remains inference and
must not be relabeled as a direct carrier.

Object boundaries are strict:
- new_entities contains only durable things such as person, place, activity,
  project, or device. Never put an event, relationship, cognition,
  preference, trait, state, cause, or position in new_entities. A trip is an
  activity entity, not a separate event/preference entity. Each proposed
  canonical_name must correspond to a directly mentioned named thing or a
  persistently referable concrete person description in eligible Evidence.
  Omit an unmentioned background actor, even when it would normally be
  involved. An activity entity is a named, persistently referable
  undertaking. A verb, travel mode, behavior, preference, or abstract concept
  is not an Entity; keep it in a facet or cognition instead.
  Attribute labels and predicate words are not Entities either: do not create
  an Entity for labels such as nickname/name, preference/liking, state, or
  trait. A nickname value or the object of a preference stays in the exact
  target-specific Cognition unless the Evidence independently introduces it as
  a durable world object. Never model having a nickname as possession of an
  activity Entity.
- new_relationships contains only explicitly supported durable relationships.
  "are friends" uses relation_type "friend". Participation, planning a trip,
  and destination are event fields, not extra Relationship objects.
- new_events contains lived occurrences. Merge turns about one lived situation
  into one event rather than one event per turn. Planning, destination, and
  other context for the same disagreement belong to that conflict event; do not
  split them into an extra planning event unless the Evidence explicitly
  supports a distinct lived occurrence. An interpersonal disagreement/argument
  uses event_type "interpersonal_conflict". Its EventFacet keys are exactly
  "cause" and "position": use one "cause" for the conflict and one "position"
  per participating subject, with that subject's entity ID in about_entity_id.
  Never encode a subject in the key (for example, never use "position-user").
  Preserve every explicitly contrast-defining attribute in each position facet;
  do not reduce a position to a partial summary.
  Each facet has exactly key, value, segment_id, about_entity_id. For every
  interpersonal_conflict position, set value to null and select one substantive
  complete eligible user segment_id; the trusted extractor, not you,
  materializes the public position text verbatim. The chosen segment's
  evidence_id must appear in the event evidence_ids. Never reuse one position
  segment for two participants in the same conflict. For every other facet,
  segment_id should be null and value must be non-empty. A structurally valid
  redundant non-null segment_id is ignored: do not treat it as provenance or
  resolve it against the catalog. If no eligible Evidence
  supports every participant position, do not create the conflict event; you
  may emit semantic_uncertainties instead.
  The two legal facet shapes are exact: a cause is
  {"key":"cause","value":"<non-empty-supported-cause>","segment_id":null,
  "about_entity_id":null}; a participant position is
  {"key":"position","value":null,"segment_id":"<distinct-substantive-segment-id>",
  "about_entity_id":"<participant-entity-id>"}.
  Put people in participants, the trip/place in related_entity_ids, and the
  friendship in relationship_ids. An event's evidence_ids must include every
  eligible turn that supports its occurrence or any linked relationship, trip,
  place, or context; do not cite only a final occurrence turn.
- new_cognitions contains beliefs/preferences/traits/hypotheses. Create
  independently targeted cognitions for explicit self preferences,
  user-attributed third-party tendencies, and a supported relationship pattern.
  When Evidence explicitly limits a preference to a domain, put a concise,
  canonical domain token in scope rather than making the preference global or
  leaving scope null (for example, travel for a travel-only preference). A
  relationship pattern inferred from a single occurrence is provisional: it
  must use content_type "hypothesis", model_inferred true, perspective null,
  and a concise canonical scope token naming the relevant context (for example,
  travel for a travel-only pattern); never promote it to an unscoped state. Its
  content is null and perspective should be null. A structurally valid redundant
  relationship perspective proposal is ignored locally. The trusted extractor derives each side
  from this delta's exact direct cognitions and constructs the contrast. User
  statements about self or another person may set
  perspective null or provide a proposal, but the trusted extractor always
  derives the current owner perspective (the target person is not the holder).
  An inferred relationship pattern targets the Relationship; the trusted
  extractor always derives system perspective.

""" + _CONFLICT_RELATIONSHIP_RECIPE + r"""

IDs use lowercase ASCII kebab-case (convert underscores to hyphens). Entity IDs
are kind:slug. A trip activity is activity:<place>-trip. A friendship ID is
relationship:<source-short>-<target-short>. A place-anchored interpersonal
conflict is event:<place>-conflict. Cognition IDs are cog:<semantic-target-role>-
<topic>; use concise stable topics such as travel-style, planning-style, or
planning-friction. The payload's evidence_language is authoritative for every
natural-language event summary, facet value, and cognition content: use that
language and preserve short direct Evidence phrases where possible instead of
translating or paraphrasing away key terms.

FINAL SELF-CHECK: TRIP ACTIVITY NAME CHECK: a place-anchored trip activity canonical_name must preserve both the named place anchor and a travel/activity type; copy the shortest eligible Evidence phrase containing both, never reduce the activity to the place name alone. TRIP ENTITY SEPARATION CHECK: when eligible Evidence names both a destination place and a persistently referable trip activity, create two distinct Entity objects: one place and one activity; never merge the trip into the place or the place into the trip. Every related lived event must include both Entity IDs in related_entity_ids. INFERRED RELATIONSHIP CHECK: only when the conditional conflict relationship transaction is independently supported, emit one exact stated direct cognition for each Relationship endpoint plus the inferred Relationship hypothesis; set hypothesis content and perspective to null. The trusted extractor preserves exact materialized claims, derives the bindings, and constructs the scoped contrast. Event positions alone do not create durable Cognitions. A structurally valid redundant perspective proposal is ignored and never becomes provenance.

All object dictionaries use exact snake_case keys.  Entity: id, world_id, kind,
canonical_name, aliases.  Relationship: id, world_id, source_entity_id,
target_entity_id, relation_type, bidirectional (do not emit status, valid_from,
or valid_to).  Event: id, world_id, event_type, summary, occurred_at,
participants, related_entity_ids, relationship_ids, facets, evidence_ids.
Participant: entity_id, role. Facet: key, value, segment_id, about_entity_id.
  Cognition:
  id, world_id, target, content, content_type, model_inferred, perspective,
  sources, scope, valid_at, invalid_at. A source has segment_id, relation,
proposition_origin, response_act. Use explicit null for every nullable
field. Allowed enum values: content_type=fact|preference|goal|project|state|
trait|hypothesis|trend; source relation=support|contradict;
proposition_origin=user_stated|assistant_proposed;
response_act=affirm|negate|select|elaborate|ask|none|other;
target kind=world|entity|relationship|event; perspective kind=entity|joint|system.""" + "\n\n" + _LIFECYCLE_CLAIM_RULE

_OUTPUT_SHAPE = r'''Exact output shape (replace sentinel strings with real values; keep every key even when its value is [] or null):
{"world_id":"<world-id>","new_entities":[{"id":"<id>","world_id":"<world-id>","kind":"<kind>","canonical_name":"<name>","aliases":[]}],"new_relationships":[{"id":"<id>","world_id":"<world-id>","source_entity_id":"<id>","target_entity_id":"<id>","relation_type":"<type>","bidirectional":false}],"new_events":[{"id":"<id>","world_id":"<world-id>","event_type":"<type>","summary":"<summary>","occurred_at":"<timestamp>","participants":[{"entity_id":"<id>","role":null}],"related_entity_ids":[],"relationship_ids":[],"facets":[{"key":"<key>","value":"<non-empty-value>","segment_id":null,"about_entity_id":null}],"evidence_ids":["<eligible-evidence-id>"]}],"new_cognitions":[{"id":"<id>","world_id":"<world-id>","target":{"kind":"entity","id":"<id>"},"content":null,"content_type":"fact","model_inferred":false,"perspective":null,"sources":[{"segment_id":"<eligible-segment-id>","relation":"support","proposition_origin":"user_stated","response_act":"none"}],"scope":null,"valid_at":null,"invalid_at":null}],"unresolved_references":[],"semantic_uncertainties":[]}
Conditional facet examples: cause/non-position={"key":"cause","value":"<non-empty-supported-cause>","segment_id":null,"about_entity_id":null}; interpersonal-conflict position={"key":"position","value":null,"segment_id":"<distinct-substantive-eligible-segment-id>","about_entity_id":"<participant-entity-id>"}.
Conflict cognition examples: direct endpoint={"id":"<id>","world_id":"<world-id>","target":{"kind":"entity","id":"<endpoint-entity-id>"},"content":null,"content_type":"preference","model_inferred":false,"perspective":null,"sources":[{"segment_id":"<direct-eligible-segment-id>","relation":"support","proposition_origin":"user_stated","response_act":"none"}],"scope":"<non-empty-shared-scope>","valid_at":null,"invalid_at":null}; inferred relationship={"id":"<id>","world_id":"<world-id>","target":{"kind":"relationship","id":"<linked-relationship-id>"},"content":null,"content_type":"hypothesis","model_inferred":true,"perspective":null,"sources":[{"segment_id":"<independent-conflict-eligible-segment-id>","relation":"support","proposition_origin":"user_stated","response_act":"none"}],"scope":"<non-empty-shared-scope>","valid_at":null,"invalid_at":null}.'''


def _requires_owner_memory_coverage(eligible: Sequence[ConversationTurn]) -> bool:
    """Return whether eligible user Evidence plainly asserts an owner fact.

    This narrow lexical floor intentionally does *not* infer a memory. It only
    distinguishes self propositions from conversational turns that may safely
    produce no candidate, so imperfect local models receive their one normal
    schema-repair opportunity instead of silently dropping an obvious fact.
    """
    return any(
        _is_explicit_owner_memory_proposition(clause)
        for turn in eligible
        for clause in _CLAUSE_SEPARATOR.split(turn.content)
        if clause.strip()
    )


def _is_explicit_owner_memory_proposition(text: str) -> bool:
    normalized = text.strip()
    if _is_pure_self_question_clause(normalized):
        return False
    if (
        _ENGLISH_OWNER_MEMORY_PROPOSITION.search(normalized)
        or _is_explicit_owner_naming_proposition(normalized)
    ):
        return True
    return bool(
        _CHINESE_OWNER_DIRECT_PROPOSITION.search(normalized)
        or _CHINESE_OWNER_PROJECT_PROPOSITION.search(normalized)
        or _CHINESE_OWNER_GOAL_PROPOSITION.search(normalized)
        or _CHINESE_OWNER_SCHEDULE_PROPOSITION.search(normalized)
    )


def _is_explicit_owner_naming_proposition(text: str) -> bool:
    normalized = text.strip()
    return bool(
        _CHINESE_OWNER_NAMING_PROPOSITION.search(normalized)
        or _ENGLISH_OWNER_NAMING_PROPOSITION.search(normalized)
    )


def _is_pure_self_question_clause(text: str) -> bool:
    """Recognize direct self-queries without suppressing a separate fact clause."""
    if _ENGLISH_PURE_QUESTION.match(text):
        return True
    if _CHINESE_PURE_OWNER_EVALUATION_QUESTION.fullmatch(text):
        return True
    return (
        text.startswith("我")
        and (
            text.startswith(("我是否", "我是不是"))
            or text.endswith(("吗", "么", "呢"))
        )
    )


def _is_trusted_question_only_turn(text: str) -> bool:
    """Require every clause to be a self-query before suppressing extraction."""
    clauses = tuple(
        clause.strip()
        for clause in _CLAUSE_SEPARATOR.split(text)
        if clause.strip()
    )
    return bool(clauses) and all(
        _is_pure_self_question_clause(clause) for clause in clauses
    )


def _has_owner_related_coverage(delta: WorldDelta, owner_entity_id: str) -> bool:
    """Check only explicit owner links already proposed by the model."""
    if any(
        cognition.target.kind == "entity" and cognition.target.id == owner_entity_id
        for cognition in delta.new_cognitions
    ):
        return True
    if any(
        owner_entity_id in (relationship.source_entity_id, relationship.target_entity_id)
        for relationship in delta.new_relationships
    ):
        return True
    return any(
        any(participant.entity_id == owner_entity_id for participant in event.participants)
        for event in delta.new_events
    )


def _owner_third_party_introduction(
    catalog: Sequence[_EligibleSegment],
) -> _OwnerThirdPartyIntroduction | None:
    """Recognize one narrow, explicit owner-to-person introduction.

    This is a retention postcondition, not a fallback graph writer.  It only
    recognizes a directly stated relationship whose third party is explicitly
    described in current user Evidence.  All graph records still come from a
    schema-constrained model reply and remain review-only.
    """
    introductions: list[_OwnerThirdPartyIntroduction] = []
    for segment in catalog:
        match = _CHINESE_OWNER_THIRD_PARTY_INTRODUCTION.fullmatch(segment.text)
        if match is None:
            continue
        action_match = _CHINESE_THIRD_PARTY_LIVED_ACTION.search(match.group("rest"))
        introductions.append(
            _OwnerThirdPartyIntroduction(
                segment_id=segment.segment_id,
                evidence_id=segment.evidence_id,
                mention=match.group("mention"),
                action_text=action_match.group("action") if action_match is not None else None,
            )
        )
    return introductions[0] if len(introductions) == 1 else None


def _owner_attributed_feedback(
    catalog: Sequence[_EligibleSegment],
) -> _OwnerAttributedFeedback | None:
    """Recognize one exact attributed evaluation without adopting its trait."""
    candidates = tuple(
        _OwnerAttributedFeedback(
            segment.segment_id,
            segment.evidence_id,
            segment.text,
        )
        for segment in catalog
        if _CHINESE_OWNER_ATTRIBUTED_FEEDBACK.fullmatch(segment.text)
    )
    return candidates[0] if len(candidates) == 1 else None


def _owner_pet_introduction(
    catalog: Sequence[_EligibleSegment],
) -> _OwnerPetIntroduction | None:
    """Recognize one unambiguously introduced, unnamed pet possession."""
    candidates: list[_OwnerPetIntroduction] = []
    for segment in catalog:
        match = _CHINESE_OWNER_PET_INTRODUCTION.fullmatch(segment.text)
        if match is None:
            continue
        candidates.append(
            _OwnerPetIntroduction(
                segment.segment_id,
                segment.evidence_id,
                match.group("mention"),
            )
        )
    return candidates[0] if len(candidates) == 1 else None


def _reported_third_party_action(
    catalog: Sequence[_EligibleSegment],
) -> _ReportedThirdPartyAction | None:
    """Return one exact attributed lived-action span, or fail closed on many."""
    actions: dict[tuple[str, int, int], _ReportedThirdPartyAction] = {}
    for parent in catalog:
        for match in _CHINESE_REPORTED_THIRD_PARTY_ACTION.finditer(parent.text):
            local_start, local_end = match.span("action")
            start = parent.start_codepoint + local_start
            end = parent.start_codepoint + local_end
            key = (parent.evidence_id, start, end)
            actions[key] = _ReportedThirdPartyAction(
                segment=_EligibleSegment(
                    segment_id="trusted-reported-action-0000",
                    evidence_id=parent.evidence_id,
                    text=parent.source_content[start:end],
                    start_codepoint=start,
                    end_codepoint=end,
                    source_content=parent.source_content,
                ),
                recipient_mention=match.group("recipient"),
            )
    return next(iter(actions.values())) if len(actions) == 1 else None


def _is_direct_third_party_statement(text: str) -> bool:
    """Recognize a direct third-person carrier without interpreting its predicate."""
    normalized = text.strip()
    if not normalized or normalized.endswith(("?", "？")):
        return False
    if _THIRD_PARTY_INFERENCE_MARKER.search(normalized):
        return False
    return bool(
        _CHINESE_DIRECT_THIRD_PARTY.match(normalized)
        or _ENGLISH_DIRECT_THIRD_PARTY.match(normalized)
    )


def _third_party_reference_context(
    base: MemoryWorldGraph,
    turns: Sequence[ConversationTurn],
    eligible_ids: Collection[str],
    catalog: Sequence[_EligibleSegment],
    accepted_entity_references: Sequence[AcceptedEntityReference] | None = None,
    reference_continuity_id: str | None = None,
) -> _ThirdPartyReferenceContext:
    """Resolve one current pronoun only from accepted BASE plus prior user text.

    Prior user turns provide reference context but never Evidence.  Assistant
    turns are deliberately ignored even if they repeat an entity name.  A
    most-recent user turn that names two accepted referable entities is ambiguous rather
    than a license to trust the model's preferred target.
    """
    all_direct_segments = tuple(
        segment for segment in catalog if _is_direct_third_party_statement(segment.text)
    )
    # Role-specific exact subspans may coexist with their sentence-level parent
    # in the catalog.  They are one mention, not two competing references.  Use
    # the smallest exact carrier for the same mention and Evidence; genuinely
    # separate carriers remain separate and therefore ambiguous.
    direct_segments = tuple(
        segment
        for segment in all_direct_segments
        if not any(
            other.segment_id != segment.segment_id
            and other.evidence_id == segment.evidence_id
            and segment.start_codepoint <= other.start_codepoint
            and other.end_codepoint <= segment.end_codepoint
            and (
                segment.start_codepoint < other.start_codepoint
                or other.end_codepoint < segment.end_codepoint
            )
            and _normalize_quote(_direct_third_party_mention(other.text) or "")
            == _normalize_quote(_direct_third_party_mention(segment.text) or "")
            for other in all_direct_segments
        )
    )
    if not direct_segments:
        return _ThirdPartyReferenceContext("not-applicable")
    segment = direct_segments[0]
    mention = _direct_third_party_mention(segment.text)
    if len(direct_segments) != 1:
        normalized_mentions = {
            _normalize_quote(candidate_mention)
            for candidate in direct_segments
            if (candidate_mention := _direct_third_party_mention(candidate.text))
        }
        return _ThirdPartyReferenceContext(
            "ambiguous",
            segment.segment_id,
            segment.evidence_id,
            text=segment.text,
            mention=mention,
            candidate_binding_allowed=len(normalized_mentions) == 1,
        )

    referable_entities = tuple(
        entity
        for entity in base.entities.values()
        if (
            entity.id != base.world.owner_entity_id
            and entity.kind.casefold() in {"person", "animal"}
        )
    )
    if not referable_entities:
        return _ThirdPartyReferenceContext(
            "unresolved",
            segment.segment_id,
            segment.evidence_id,
            text=segment.text,
            mention=mention,
            candidate_binding_allowed=True,
        )

    source_index = next(
        (index for index, turn in enumerate(turns) if turn.turn_id == segment.evidence_id),
        None,
    )
    if source_index is None:
        return _ThirdPartyReferenceContext(
            "unresolved",
            segment.segment_id,
            segment.evidence_id,
            text=segment.text,
            mention=mention,
            candidate_binding_allowed=True,
        )
    if mention is not None and accepted_entity_references is not None:
        current_turn = turns[source_index]
        resolution = EntityReferenceResolver().resolve(
            base,
            ReferenceMention(
                text=mention,
                evidence_id=segment.evidence_id,
                conversation_id=current_turn.conversation_id,
                occurred_at=current_turn.occurred_at,
                source_role="user",
                kind_hint="person",
                continuity_id=reference_continuity_id,
            ),
            accepted_entity_references,
        )
        if resolution.state == "resolved":
            return _ThirdPartyReferenceContext(
                "resolved",
                segment.segment_id,
                segment.evidence_id,
                resolution.entity_id,
                segment.text,
                mention,
            )
        if resolution.state == "ambiguous":
            return _ThirdPartyReferenceContext(
                "ambiguous",
                segment.segment_id,
                segment.evidence_id,
                text=segment.text,
                mention=mention,
            )
        return _ThirdPartyReferenceContext(
            "unresolved",
            segment.segment_id,
            segment.evidence_id,
            text=segment.text,
            mention=mention,
        )
    eligible = set(eligible_ids)
    for turn in reversed(turns[:source_index]):
        if turn.role != "user" or turn.turn_id in eligible:
            continue
        matches = tuple(
            entity
            for entity in referable_entities
            if _prior_user_turn_mentions_entity(turn.content, entity)
        )
        if len(matches) == 1:
            return _ThirdPartyReferenceContext(
                "resolved",
                segment.segment_id,
                segment.evidence_id,
                matches[0].id,
                segment.text,
                mention,
            )
        if len(matches) > 1:
            return _ThirdPartyReferenceContext(
                "ambiguous",
                segment.segment_id,
                segment.evidence_id,
                text=segment.text,
                mention=mention,
            )
    return _ThirdPartyReferenceContext(
        "unresolved",
        segment.segment_id,
        segment.evidence_id,
        text=segment.text,
        mention=mention,
    )


def _direct_third_party_mention(text: str) -> str | None:
    match = _CHINESE_DIRECT_THIRD_PARTY.match(text) or _ENGLISH_DIRECT_THIRD_PARTY.match(text)
    return match.group("mention") if match is not None else None


def _prior_user_turn_mentions_entity(content: str, entity: Entity) -> bool:
    labels = (entity.canonical_name, *entity.aliases)
    ignored = {
        "她",
        "他",
        "它",
        "ta",
        "she",
        "her",
        "he",
        "him",
        "they",
        "them",
        "对方",
        "那个人",
    }
    return any(
        len(normalized_label) >= 2
        and normalized_label not in ignored
        and _reference_label_in_text(label, content)
        for label in labels
        if (
            normalized_label := " ".join(
                unicodedata.normalize("NFKC", label).casefold().split()
            )
        )
    )


def _reference_label_in_text(label: str, content: str) -> bool:
    normalized_label = " ".join(
        unicodedata.normalize("NFKC", label).casefold().split()
    )
    normalized_content = " ".join(
        unicodedata.normalize("NFKC", content).casefold().split()
    )
    if not normalized_label:
        return False
    if any("\u3400" <= character <= "\u9fff" for character in normalized_label):
        compact = normalized_label.replace(" ", "")
        return len(compact) >= 2 and compact in normalized_content.replace(" ", "")
    return (
        re.search(
            rf"(?<!\w){re.escape(normalized_label)}(?!\w)",
            normalized_content,
        )
        is not None
    )


def _has_owner_third_party_introduction_coverage(
    delta: WorldDelta,
    base: MemoryWorldGraph,
    introduction: _OwnerThirdPartyIntroduction,
) -> bool:
    """Require the explicit person, owner relation, and any stated lived action."""
    entities = {**base.entities, **{entity.id: entity for entity in delta.new_entities}}
    people = tuple(
        entity
        for entity in entities.values()
        if (
            entity.id != base.world.owner_entity_id
            and entity.kind.casefold() == "person"
            and introduction.mention in (entity.canonical_name, *entity.aliases)
        )
    )
    if len(people) != 1:
        return False
    person_id = people[0].id
    relationships = tuple(
        relationship
        for relationship in (*base.relationships.values(), *delta.new_relationships)
        if (
            relationship.source_entity_id == base.world.owner_entity_id
            and relationship.target_entity_id == person_id
            and relationship.relation_type.replace("-", "_").casefold()
            in {"romantic_interest", "likes", "interested_in"}
        )
    )
    if len(relationships) != 1:
        return False
    if introduction.action_text is None:
        return True
    relationship_id = relationships[0].id
    return any(
        introduction.evidence_id in event.evidence_ids
        and relationship_id in event.relationship_ids
        and {base.world.owner_entity_id, person_id}.issubset(
            {participant.entity_id for participant in event.participants}
        )
        for event in delta.new_events
    )


def _has_resolved_direct_third_party_cognition(
    delta: WorldDelta,
    context: _ThirdPartyReferenceContext,
    owner_entity_id: str,
) -> bool:
    if (
        context.state != "resolved"
        or context.entity_id is None
        or context.evidence_id is None
        or context.text is None
    ):
        return False
    traces = {trace.cognition_id: trace for trace in delta.formation_traces}
    for cognition in delta.new_cognitions:
        trace = traces.get(cognition.id)
        if trace is None or len(trace.sources) != 1:
            continue
        source = trace.sources[0]
        if (
            cognition.target == MemoryTarget("entity", context.entity_id)
            and cognition.content == context.text
            and cognition.formed_by == "stated"
            and cognition.perspective == Perspective("entity", (owner_entity_id,))
            and not trace.model_inferred_proposal
            and trace.derived_formed_by == "stated"
            and source.evidence_id == context.evidence_id
            and source.relation == "support"
            and source.local_origin_decision == "exact_user_claim"
        ):
            return True
    return False


def _has_current_unresolved_reference(
    delta: WorldDelta,
    context: _ThirdPartyReferenceContext,
) -> bool:
    if context.evidence_id is None or context.mention is None:
        return False
    current = tuple(
        reference
        for reference in delta.unresolved_references
        if context.evidence_id in reference.evidence_ids
    )
    return len(current) == 1 and (
        current[0].evidence_ids == (context.evidence_id,)
        and _normalize_quote(current[0].mention) == _normalize_quote(context.mention)
    )


def _candidate_bound_third_party_entity_id(
    delta: WorldDelta,
    base: MemoryWorldGraph,
    context: _ThirdPartyReferenceContext,
) -> str | None:
    """Recognize one reviewable new-person binding for an unresolved mention.

    This does not accept identity or mutate the base.  It only recognizes that
    one proposed person is uniquely linked to the Owner and that an exact
    carried user claim about the unresolved pronoun targets that same person.
    The candidate remains subject to the ordinary program validation and
    transaction boundary.  A diagnostic Review may display it, but is not its
    product write authority.
    """

    if (
        context.state not in {"unresolved", "ambiguous"}
        or context.evidence_id is None
        or context.mention is None
        or context.text is None
        or not context.candidate_binding_allowed
    ):
        return None
    new_people = {
        entity.id
        for entity in delta.new_entities
        if entity.kind.casefold() == "person"
        and entity.id != base.world.owner_entity_id
    }
    if not new_people:
        return None
    linked_people: set[str] = set()
    for relationship in delta.new_relationships:
        endpoints = {
            relationship.source_entity_id,
            relationship.target_entity_id,
        }
        if base.world.owner_entity_id not in endpoints:
            continue
        linked_people.update(endpoints & new_people)
    if len(linked_people) != 1:
        return None
    candidate_id = next(iter(linked_people))
    matching_claims = tuple(
        cognition
        for cognition in delta.new_cognitions
        if (
            cognition.target == MemoryTarget("entity", candidate_id)
            and cognition.formed_by == "stated"
            and cognition.content == context.text
            and cognition.perspective
            == Perspective("entity", (base.world.owner_entity_id,))
            and any(
                source.evidence_id == context.evidence_id
                and source.relation == "support"
                for source in cognition.sources
            )
            and (
                mention := _direct_third_party_mention(cognition.content)
            ) is not None
            and _normalize_quote(mention)
            == _normalize_quote(context.mention)
        )
    )
    return candidate_id if matching_claims else None


_ATTRIBUTE_RELATION_TYPES = frozenset(
    {
        "alias",
        "called",
        "codename",
        "display_name",
        "favorite",
        "handle",
        "likes",
        "name",
        "named",
        "nickname",
        "preference",
        "screen_name",
        "state",
        "trait",
        "username",
        "名字",
        "昵称",
        "网名",
    }
)


def _is_interpersonal_candidate_relationship(
    relationship: Relationship,
    *,
    owner_entity_id: str,
    candidate_entity_id: str,
) -> bool:
    relation_type = re.sub(
        r"[\s-]+",
        "_",
        relationship.relation_type.strip().casefold(),
    )
    return (
        relationship.source_entity_id == owner_entity_id
        and relationship.target_entity_id == candidate_entity_id
        and relation_type not in _ATTRIBUTE_RELATION_TYPES
    )


def _minimal_matching_segments(
    catalog: Sequence[_EligibleSegment],
    *,
    evidence_id: str,
    predicate: Callable[[str], bool],
) -> tuple[_EligibleSegment, ...]:
    matching = tuple(
        segment
        for segment in catalog
        if segment.evidence_id == evidence_id and predicate(segment.text)
    )
    return tuple(
        segment
        for segment in matching
        if not any(
            other.segment_id != segment.segment_id
            and segment.start_codepoint <= other.start_codepoint
            and other.end_codepoint <= segment.end_codepoint
            and (
                segment.start_codepoint < other.start_codepoint
                or other.end_codepoint < segment.end_codepoint
            )
            for other in matching
        )
    )


def _trace_is_exact_segment(
    trace: FormationTrace,
    segment: _EligibleSegment,
) -> bool:
    if len(trace.sources) != 1:
        return False
    source = trace.sources[0]
    return (
        source.evidence_id == segment.evidence_id
        and source.relation == "support"
        and source.local_origin_decision == "exact_user_claim"
        and source.claim_span.start_codepoint == segment.start_codepoint
        and source.claim_span.end_codepoint == segment.end_codepoint
        and source.claim_span.claim_sha256 == _sha256(segment.text)
    )


def _candidate_label_score(
    entity: Entity,
    catalog: Sequence[_EligibleSegment],
    *,
    excluded_evidence_id: str,
) -> int:
    texts = {
        "".join(
            character
            for character in unicodedata.normalize("NFKC", segment.text).casefold()
            if character.isalnum()
        )
        for segment in catalog
        if segment.evidence_id != excluded_evidence_id
    }
    best = 0
    for raw_label in (entity.canonical_name, *entity.aliases):
        label = "".join(
            character
            for character in unicodedata.normalize("NFKC", raw_label).casefold()
            if character.isalnum()
        )
        if len(label) < 2:
            continue
        for text in texts:
            if label in text:
                best = max(best, 1000 + len(label))
                continue
            if any("\u4e00" <= character <= "\u9fff" for character in label):
                label_bigrams = {label[index:index + 2] for index in range(len(label) - 1)}
                text_bigrams = {text[index:index + 2] for index in range(len(text) - 1)}
                best = max(best, 10 * len(label_bigrams & text_bigrams))
            else:
                label_words = {
                    word for word in re.findall(r"[a-z0-9]+", raw_label.casefold())
                    if len(word) >= 3
                }
                text_words = set(re.findall(r"[a-z0-9]+", text))
                best = max(best, sum(len(word) for word in label_words & text_words))
    return best


def _trusted_owner_naming_claim(
    pairs: Sequence[tuple[WorldCognition, FormationTrace]],
    *,
    base: MemoryWorldGraph,
    naming_segment: _EligibleSegment,
    reserved_ids: Collection[str],
) -> tuple[WorldCognition, FormationTrace] | None:
    owner_pairs = tuple(
        pair
        for pair in pairs
        if pair[0].target == MemoryTarget("entity", base.world.owner_entity_id)
    )
    if len(owner_pairs) == 1:
        return owner_pairs[0]
    if owner_pairs:
        return None
    if not pairs:
        return None
    template, trace = min(pairs, key=lambda pair: pair[0].id)
    semantic_identity = json.dumps(
        {
            "world_id": base.world.world_id,
            "target": {"kind": "entity", "id": base.world.owner_entity_id},
            "content_sha256": _sha256(naming_segment.text),
            "evidence_id": naming_segment.evidence_id,
            "start": naming_segment.start_codepoint,
            "end": naming_segment.end_codepoint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    cognition_id = (
        "cog:extracted-"
        + hashlib.sha256(semantic_identity.encode("utf-8")).hexdigest()[:24]
    )
    if cognition_id in reserved_ids:
        return None
    support_count = 1 if any(source.relation == "support" for source in template.sources) else 0
    contradict_count = sum(source.relation == "contradict" for source in template.sources)
    confidence = compute_confidence(
        ConfidenceInputs(
            content_type="fact",
            formed_by="stated",
            support_count=support_count,
            contradict_count=contradict_count,
            hedged=False,
        )
    )
    cred_status = derive_cred_status(
        confidence,
        contradict_count,
        "fact",
        support_count=support_count,
    )
    return (
        replace(
            template,
            id=cognition_id,
            target=MemoryTarget("entity", base.world.owner_entity_id),
            content=naming_segment.text,
            content_type="fact",
            formed_by="stated",
            confidence=confidence,
            cred_status=cred_status,
            perspective=Perspective("entity", (base.world.owner_entity_id,)),
            scope=None,
            valid_at=None,
            invalid_at=None,
        ),
        replace(
            trace,
            cognition_id=cognition_id,
            model_inferred_proposal=False,
            derived_formed_by="stated",
        ),
    )


def _minimalize_mixed_subject_naming_candidate(
    delta: WorldDelta,
    base: MemoryWorldGraph,
    catalog: Sequence[_EligibleSegment],
    context: _ThirdPartyReferenceContext,
) -> WorldDelta:
    """Retain only the two exact claims and one reviewable person relation.

    Some small local models materialize nickname values or preference objects as
    extra Entities even after being given exact role-specific spans.  For this
    narrowly recognized mixed-subject shape, trusted code may remove those
    optional semantic-role objects.  It never invents content, a person, or a
    relationship: every retained record already exists in the decoded candidate
    and remains subject to program validation before automatic Apply.
    """

    if (
        context.state not in {"unresolved", "ambiguous"}
        or not context.candidate_binding_allowed
        or context.segment_id is None
        or context.evidence_id is None
        or context.text is None
        or context.mention is None
    ):
        return delta
    context_segment = next(
        (segment for segment in catalog if segment.segment_id == context.segment_id),
        None,
    )
    if context_segment is None:
        return delta
    naming_segments = _minimal_matching_segments(
        catalog,
        evidence_id=context.evidence_id,
        predicate=_is_explicit_owner_naming_proposition,
    )
    if len(naming_segments) != 1:
        return delta
    naming_segment = naming_segments[0]
    if not (
        context_segment.end_codepoint <= naming_segment.start_codepoint
        or naming_segment.end_codepoint <= context_segment.start_codepoint
    ):
        return delta

    new_people = tuple(
        entity
        for entity in delta.new_entities
        if entity.kind.casefold() == "person"
        and entity.id != base.world.owner_entity_id
    )
    qualifying = tuple(
        (entity, relationship)
        for entity in new_people
        for relationship in delta.new_relationships
        if _is_interpersonal_candidate_relationship(
            relationship,
            owner_entity_id=base.world.owner_entity_id,
            candidate_entity_id=entity.id,
        )
    )
    qualifying_by_person: dict[str, list[tuple[Entity, Relationship]]] = {}
    for pair in qualifying:
        qualifying_by_person.setdefault(pair[0].id, []).append(pair)
    single_relationship_pairs = tuple(
        pairs[0]
        for pairs in qualifying_by_person.values()
        if len(pairs) == 1
    )
    if len(single_relationship_pairs) == 1:
        person, relationship = single_relationship_pairs[0]
    else:
        scored = tuple(
            (
                _candidate_label_score(
                    pair[0],
                    catalog,
                    excluded_evidence_id=context.evidence_id,
                ),
                pair,
            )
            for pair in single_relationship_pairs
        )
        if not scored:
            return delta
        top_score = max(score for score, _ in scored)
        top_pairs = tuple(pair for score, pair in scored if score == top_score)
        if top_score <= 0 or len(top_pairs) != 1:
            return delta
        person, relationship = top_pairs[0]
    traces = {trace.cognition_id: trace for trace in delta.formation_traces}
    person_claims = tuple(
        cognition
        for cognition in delta.new_cognitions
        if cognition.target == MemoryTarget("entity", person.id)
        and cognition.formed_by == "stated"
        and cognition.content == context_segment.text
        and cognition.perspective
        == Perspective("entity", (base.world.owner_entity_id,))
        and (trace := traces.get(cognition.id)) is not None
        and _trace_is_exact_segment(trace, context_segment)
    )
    exact_naming_pairs = tuple(
        (cognition, trace)
        for cognition in delta.new_cognitions
        if cognition.formed_by == "stated"
        and cognition.content == naming_segment.text
        and cognition.perspective
        == Perspective("entity", (base.world.owner_entity_id,))
        and (trace := traces.get(cognition.id)) is not None
        and _trace_is_exact_segment(trace, naming_segment)
    )
    if len(person_claims) != 1:
        return delta
    owner_pair = _trusted_owner_naming_claim(
        exact_naming_pairs,
        base=base,
        naming_segment=naming_segment,
        reserved_ids=(
            set(base.entities)
            | set(base.relationships)
            | set(base.events)
            | set(base.cognitions)
            | {person_claims[0].id}
        ),
    )
    if owner_pair is None:
        return delta
    retained_cognitions = (person_claims[0], owner_pair[0])
    return replace(
        delta,
        new_entities=(person,),
        new_relationships=(relationship,),
        new_events=(),
        new_cognitions=retained_cognitions,
        formation_traces=(traces[person_claims[0].id], owner_pair[1]),
        unresolved_references=tuple(
            reference
            for reference in delta.unresolved_references
            if _normalize_quote(reference.mention)
            != _normalize_quote(context.mention)
        ),
    )


def _normalize_candidate_third_party_binding(
    delta: WorldDelta,
    base: MemoryWorldGraph,
    context: _ThirdPartyReferenceContext,
) -> WorldDelta:
    candidate_id = _candidate_bound_third_party_entity_id(delta, base, context)
    if candidate_id is None or context.evidence_id is None or context.mention is None:
        return delta
    remaining = tuple(
        reference
        for reference in delta.unresolved_references
        if not (
            context.evidence_id in reference.evidence_ids
            and _normalize_quote(reference.mention)
            == _normalize_quote(context.mention)
        )
    )
    if remaining == delta.unresolved_references:
        return delta
    return replace(delta, unresolved_references=remaining)


def _has_unbound_new_person_direct_claim(
    delta: WorldDelta,
    base: MemoryWorldGraph,
    context: _ThirdPartyReferenceContext,
) -> bool:
    """Detect a model assigning an unresolved carrier to a new person by fiat."""

    if context.evidence_id is None:
        return False
    new_person_ids = {
        entity.id
        for entity in delta.new_entities
        if entity.kind.casefold() == "person"
        and entity.id != base.world.owner_entity_id
    }
    return any(
        cognition.target.kind == "entity"
        and cognition.target.id in new_person_ids
        and cognition.formed_by == "stated"
        and _direct_third_party_mention(cognition.content) is not None
        and any(
            source.evidence_id == context.evidence_id
            and source.relation == "support"
            for source in cognition.sources
        )
        for cognition in delta.new_cognitions
    )


def _unresolved_references_are_grounded(
    delta: WorldDelta,
    catalog: Sequence[_EligibleSegment],
) -> bool:
    """Require every unresolved surface form to occur in its cited user Evidence."""

    by_evidence: dict[str, list[str]] = {}
    for segment in catalog:
        by_evidence.setdefault(segment.evidence_id, []).append(
            _normalize_quote(segment.text)
        )
    return all(
        any(
            normalized_mention in segment_text
            for evidence_id in reference.evidence_ids
            for segment_text in by_evidence.get(evidence_id, ())
        )
        for reference in delta.unresolved_references
        if (normalized_mention := _normalize_quote(reference.mention))
    )


def _has_reported_third_party_action_coverage(
    delta: WorldDelta,
    action: _ReportedThirdPartyAction,
    context: _ThirdPartyReferenceContext,
) -> bool:
    if context.state != "resolved" or context.entity_id is None:
        return False
    return any(
        event.summary == action.segment.text
        and action.segment.evidence_id in event.evidence_ids
        and context.entity_id in {participant.entity_id for participant in event.participants}
        for event in delta.new_events
    )


def _is_optional_stale_event_evidence_failure(failure: str) -> bool:
    match = re.fullmatch(r"DELTA_DOMAIN\((?P<issues>[^)]+)\)@\$", failure)
    if match is None:
        return False
    issues = match.group("issues").split(",")
    return bool(issues) and all(
        re.fullmatch(
            r"event\[\d+\]\.evidence_ids\[\d+\]\.not_eligible",
            issue,
        )
        is not None
        for issue in issues
    )


def _trusted_resolved_pet_direct_fallback(
    base: MemoryWorldGraph,
    eligible_ids: tuple[str, ...],
    catalog: Sequence[_EligibleSegment],
    preceding_assistant_context: Mapping[str, tuple[str, str] | None],
    context: _ThirdPartyReferenceContext,
) -> WorldDelta | None:
    """Keep one exact current pet fact after quarantining a stale optional Event."""
    if (
        context.state != "resolved"
        or context.entity_id is None
        or context.segment_id is None
        or context.evidence_id is None
        or context.text is None
        or eligible_ids != (context.evidence_id,)
        or not _is_direct_third_party_statement(context.text)
        or _CORRECTION_OR_NEGATION_MARKER.search(context.text)
        or _PET_DIRECT_FALLBACK_LIFECYCLE_MARKER.search(context.text)
    ):
        return None
    pet = base.entities.get(context.entity_id)
    if pet is None or pet.kind.casefold() != "animal":
        return None
    ownerships = tuple(
        relationship
        for relationship in base.relationships.values()
        if (
            relationship.source_entity_id == base.world.owner_entity_id
            and relationship.target_entity_id == pet.id
            and relationship.relation_type.replace("-", "_").casefold()
            in {"owns", "has_pet", "pet_owner"}
        )
    )
    if len(ownerships) != 1:
        return None
    segment = next(
        (
            candidate
            for candidate in catalog
            if (
                candidate.segment_id == context.segment_id
                and candidate.evidence_id == context.evidence_id
                and candidate.text == context.text
            )
        ),
        None,
    )
    if (
        segment is None
        or len(catalog) != 1
        or segment.start_codepoint != 0
        or segment.end_codepoint != len(segment.source_content)
        or segment.text != segment.source_content
    ):
        return None
    source_trace = _formation_source_trace(
        _SourceProposal(
            segment.segment_id,
            "support",
            "user_stated",
            "none",
            "$.trusted_pet_direct.sources[0]",
        ),
        {segment.segment_id: segment},
        preceding_assistant_context,
        model_inferred=False,
    )
    if source_trace.local_origin_decision != "exact_user_claim":
        return None
    semantic_identity = (
        base.world.world_id
        + "\0"
        + pet.id
        + "\0"
        + segment.evidence_id
        + "\0"
        + str(segment.start_codepoint)
        + "\0"
        + segment.text
    )
    cognition_id = (
        "cog:pet-fact-"
        + hashlib.sha256(semantic_identity.encode("utf-8")).hexdigest()[:20]
    )
    all_base_ids = (
        set(base.entities)
        | set(base.relationships)
        | set(base.events)
        | set(base.cognitions)
    )
    if cognition_id in all_base_ids:
        return None
    confidence = compute_confidence(
        ConfidenceInputs(
            content_type="fact",
            formed_by="stated",
            support_count=1,
            contradict_count=0,
            hedged=False,
        )
    )
    cognition = WorldCognition(
        cognition_id,
        base.world.world_id,
        MemoryTarget("entity", pet.id),
        segment.text,
        "fact",
        "stated",
        confidence,
        derive_cred_status(confidence, 0, "fact", support_count=1),
        Perspective("entity", (base.world.owner_entity_id,)),
        (EvidenceLink(segment.evidence_id, "support"),),
    )
    trace = FormationTrace(
        cognition_id=cognition_id,
        model_inferred_proposal=False,
        sources=(source_trace,),
        derived_formed_by="stated",
        raw_support_count=1,
        effective_support_count=1,
        contradict_count=0,
    )
    delta = WorldDelta(
        world_id=base.world.world_id,
        source_evidence_ids=eligible_ids,
        new_cognitions=(cognition,),
        formation_traces=(trace,),
    )
    try:
        delta.validate_against(base, frozenset(eligible_ids))
    except (WorldDeltaValidationError, ValueError):
        return None
    if not _has_resolved_direct_third_party_cognition(
        delta, context, base.world.owner_entity_id,
    ):
        return None
    return delta


def _trusted_owner_attributed_feedback(
    base: MemoryWorldGraph,
    eligible: Sequence[ConversationTurn],
    eligible_ids: tuple[str, ...],
    feedback: _OwnerAttributedFeedback,
) -> WorldDelta | None:
    """Materialize the attributed speech act, never the underlying owner trait."""
    source_turn = next(
        (turn for turn in eligible if turn.turn_id == feedback.evidence_id),
        None,
    )
    if source_turn is None:
        return None
    event_id = (
        "event:owner-attributed-feedback-"
        + hashlib.sha256(
            (
                base.world.owner_entity_id
                + "\0"
                + feedback.evidence_id
                + "\0"
                + feedback.text
            ).encode("utf-8")
        ).hexdigest()[:16]
    )
    if event_id in base.events:
        return WorldDelta(
            world_id=base.world.world_id,
            source_evidence_ids=eligible_ids,
        )
    delta = WorldDelta(
        world_id=base.world.world_id,
        source_evidence_ids=eligible_ids,
        new_events=(
            WorldEvent(
                event_id,
                base.world.world_id,
                "attributed_feedback",
                feedback.text,
                source_turn.occurred_at,
                (EventParticipant(base.world.owner_entity_id, "recipient"),),
                (),
                (),
                (),
                (feedback.evidence_id,),
            ),
        ),
    )
    try:
        delta.validate_against(base, frozenset(eligible_ids))
    except (WorldDeltaValidationError, ValueError):
        return None
    return delta


def _has_owner_pet_introduction_coverage(
    delta: WorldDelta,
    base: MemoryWorldGraph,
    introduction: _OwnerPetIntroduction,
) -> bool:
    if delta.new_events or delta.new_cognitions:
        return False
    entities = {**base.entities, **{entity.id: entity for entity in delta.new_entities}}
    matching_pets = tuple(
        entity
        for entity in entities.values()
        if (
            entity.id != base.world.owner_entity_id
            and entity.kind.casefold() == "animal"
            and introduction.mention in (entity.canonical_name, *entity.aliases)
        )
    )
    if len(matching_pets) != 1:
        return False
    pet_id = matching_pets[0].id
    ownerships = tuple(
        relationship
        for relationship in (*base.relationships.values(), *delta.new_relationships)
        if (
            relationship.source_entity_id == base.world.owner_entity_id
            and relationship.target_entity_id == pet_id
            and relationship.relation_type.replace("-", "_").casefold()
            in {"owns", "has_pet", "pet_owner"}
        )
    )
    return len(ownerships) == 1


def _trusted_owner_pet_fallback(
    base: MemoryWorldGraph,
    eligible_ids: tuple[str, ...],
    introduction: _OwnerPetIntroduction,
) -> WorldDelta | None:
    """Create one review-only animal plus one-way ownership, never an Event."""
    matching_pets = tuple(
        entity
        for entity in base.entities.values()
        if (
            entity.id != base.world.owner_entity_id
            and entity.kind.casefold() == "animal"
            and introduction.mention in (entity.canonical_name, *entity.aliases)
        )
    )
    if matching_pets:
        return None
    pet_id = (
        "animal:described-"
        + hashlib.sha256(
            (
                base.world.world_id
                + "\0"
                + base.world.owner_entity_id
                + "\0"
                + introduction.evidence_id
                + "\0"
                + introduction.mention
            ).encode("utf-8")
        ).hexdigest()[:16]
    )
    relationship_id = (
        "relationship:owner-pet-"
        + hashlib.sha256(
            (base.world.owner_entity_id + "\0" + pet_id).encode("utf-8")
        ).hexdigest()[:16]
    )
    if (
        pet_id in base.entities
        or relationship_id in base.relationships
        or pet_id in base.relationships
        or relationship_id in base.entities
    ):
        return None
    pet = Entity(
        pet_id,
        base.world.world_id,
        "animal",
        introduction.mention,
    )
    ownership = Relationship(
        relationship_id,
        base.world.world_id,
        base.world.owner_entity_id,
        pet.id,
        "owns",
        False,
    )
    delta = WorldDelta(
        world_id=base.world.world_id,
        source_evidence_ids=eligible_ids,
        new_entities=(pet,),
        new_relationships=(ownership,),
    )
    try:
        delta.validate_against(base, frozenset(eligible_ids))
    except (WorldDeltaValidationError, ValueError):
        return None
    if not _has_owner_pet_introduction_coverage(delta, base, introduction):
        return None
    return delta


def _trusted_reported_third_party_action_fallback(
    base: MemoryWorldGraph,
    eligible: Sequence[ConversationTurn],
    eligible_ids: tuple[str, ...],
    action: _ReportedThirdPartyAction,
    context: _ThirdPartyReferenceContext,
    quarantined_failure: str,
) -> WorldDelta | None:
    """Keep an exact lived action while quarantining one optional inference."""
    if context.state != "resolved" or context.entity_id is None:
        return None
    source_turn = next(
        (turn for turn in eligible if turn.turn_id == action.segment.evidence_id),
        None,
    )
    if source_turn is None or context.entity_id not in base.entities:
        return None
    event_id = (
        "event:reported-third-party-action-"
        + hashlib.sha256(
            (
                action.segment.evidence_id
                + "\0"
                + str(action.segment.start_codepoint)
                + "\0"
                + action.segment.text
            ).encode("utf-8")
        ).hexdigest()[:12]
    )
    if event_id in base.events:
        return None
    delta = WorldDelta(
        world_id=base.world.world_id,
        source_evidence_ids=eligible_ids,
        new_events=(
            WorldEvent(
                event_id,
                base.world.world_id,
                "lived_occurrence",
                action.segment.text,
                source_turn.occurred_at,
                (EventParticipant(context.entity_id, "actor"),),
                (),
                (),
                (),
                (action.segment.evidence_id,),
            ),
        ),
        unresolved_references=(
            UnresolvedReference(
                action.recipient_mention,
                (action.segment.evidence_id,),
            ),
        ),
        semantic_uncertainties=(
            SemanticUncertainty(
                "OPTIONAL_INFERENCE_QUARANTINED:"
                + quarantined_failure.split("@", 1)[0],
                (action.segment.evidence_id,),
            ),
        ),
    )
    try:
        delta.validate_against(base, frozenset(eligible_ids))
    except (WorldDeltaValidationError, ValueError):
        return None
    if not _has_reported_third_party_action_coverage(delta, action, context):
        return None
    return delta


def _trusted_owner_third_party_fallback(
    base: MemoryWorldGraph,
    eligible: Sequence[ConversationTurn],
    eligible_ids: tuple[str, ...],
    introduction: _OwnerThirdPartyIntroduction,
) -> WorldDelta | None:
    """Build one narrow review-only delta after the model misses twice.

    Every public string and temporal value comes from the current eligible user
    Evidence.  The rule materializes only the explicitly introduced person,
    the owner's one-way stated romantic interest, and the explicitly stated
    lived action.  It never creates a reciprocal belief or applies the delta.
    """
    source_turn = next(
        (turn for turn in eligible if turn.turn_id == introduction.evidence_id),
        None,
    )
    if source_turn is None:
        return None
    matching_people = tuple(
        entity
        for entity in base.entities.values()
        if (
            entity.id != base.world.owner_entity_id
            and entity.kind.casefold() == "person"
            and introduction.mention in (entity.canonical_name, *entity.aliases)
        )
    )
    if len(matching_people) > 1:
        return None

    new_entities: tuple[Entity, ...] = ()
    if matching_people:
        person = matching_people[0]
    else:
        person_id = (
            "person:described-"
            + hashlib.sha256(introduction.mention.encode("utf-8")).hexdigest()[:12]
        )
        if person_id in base.entities:
            return None
        person = Entity(
            person_id,
            base.world.world_id,
            "person",
            introduction.mention,
        )
        new_entities = (person,)

    matching_relationships = tuple(
        relationship
        for relationship in base.relationships.values()
        if (
            relationship.source_entity_id == base.world.owner_entity_id
            and relationship.target_entity_id == person.id
            and relationship.relation_type.replace("-", "_").casefold()
            in {"romantic_interest", "likes", "interested_in"}
        )
    )
    if len(matching_relationships) > 1:
        return None
    new_relationships: tuple[Relationship, ...] = ()
    if matching_relationships:
        relationship = matching_relationships[0]
    else:
        relationship_id = (
            "relationship:owner-romantic-interest-"
            + hashlib.sha256(person.id.encode("utf-8")).hexdigest()[:12]
        )
        if relationship_id in base.relationships:
            return None
        relationship = Relationship(
            relationship_id,
            base.world.world_id,
            base.world.owner_entity_id,
            person.id,
            "romantic_interest",
            False,
        )
        new_relationships = (relationship,)

    new_events: tuple[WorldEvent, ...] = ()
    if introduction.action_text is not None:
        event_id = (
            "event:third-party-action-"
            + hashlib.sha256(
                (
                    introduction.evidence_id
                    + "\0"
                    + introduction.action_text
                ).encode("utf-8")
            ).hexdigest()[:12]
        )
        if event_id in base.events:
            return None
        new_events = (
            WorldEvent(
                event_id,
                base.world.world_id,
                "lived_occurrence",
                introduction.action_text,
                source_turn.occurred_at,
                (
                    EventParticipant(base.world.owner_entity_id, "recipient"),
                    EventParticipant(person.id, "actor"),
                ),
                (),
                (relationship.id,),
                (),
                (introduction.evidence_id,),
            ),
        )

    delta = WorldDelta(
        world_id=base.world.world_id,
        source_evidence_ids=eligible_ids,
        new_entities=new_entities,
        new_relationships=new_relationships,
        new_events=new_events,
    )
    try:
        delta.validate_against(base, frozenset(eligible_ids))
    except (WorldDeltaValidationError, ValueError):
        return None
    if not _has_owner_third_party_introduction_coverage(delta, base, introduction):
        return None
    return delta


def _targeted_repair_instruction(safe_error: str) -> str:
    """Translate one safe structural code into a short semantic repair."""
    if safe_error.startswith(
        (
            "TRIP_ACTIVITY_PLACE_ENTITY_REQUIRED@",
            "TRIP_ACTIVITY_EVENT_PLACE_LINK_REQUIRED@",
        )
    ):
        return (
            "TARGETED REPAIR: For each Evidence-supported Entity whose ID uses activity:<anchor>-trip, keep a "
            "distinct place Entity with ID place:<anchor> and kind place; do not repeat it in new_entities when "
            "that exact place already exists in BASE. Every event whose related_entity_ids includes the trip "
            "activity must include the matching place ID there too. If eligible Evidence does not support both "
            "durable objects, omit the trip activity and its event references instead of inventing either one."
        )
    if safe_error.startswith("FACET_VALUE_REQUIRED@"):
        return (
            "TARGETED REPAIR: If eligible Evidence supports the failing non-position facet, keep value as a "
            "non-empty supported string and segment_id as null. Conflict positions use the opposite shape: "
            "value null plus one distinct substantive segment_id and the participant in about_entity_id. "
            "If the event's required cause is unsupported, omit the whole event instead of inventing a value."
        )
    if safe_error.startswith(
        (
            "POSITION_VALUE_MUST_BE_NULL@",
            "POSITION_SEGMENT_REQUIRED@",
            "POSITION_SEGMENT_UNKNOWN@",
            "POSITION_SEGMENT_AMBIGUOUS@",
            "POSITION_SEGMENT_NOT_SUBSTANTIVE@",
            "POSITION_EVIDENCE@",
            "CONFLICT_POSITION_COUNT@",
            "CONFLICT_POSITION_SUBJECT@",
            "CONFLICT_POSITION_SEGMENT_REUSE@",
        )
    ):
        return (
            "TARGETED REPAIR: If the catalog contains one distinct substantive complete position segment for "
            "every participant, preserve the event and emit exactly one position facet per participant with "
            "value null, that participant Entity in about_entity_id, and a segment whose Evidence ID is in "
            "event evidence_ids. Never reuse a segment. If complete support for every participant is absent, "
            "omit the whole conflict event instead of inventing or stretching a span."
        )
    if safe_error.startswith("CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@") or (
        safe_error.startswith("DELTA_DOMAIN(")
        and "conflict_relationship_projection." in safe_error
    ):
        return "TARGETED REPAIR: " + _CONFLICT_RELATIONSHIP_RECIPE
    if safe_error.startswith(
        (
            "RELATIONSHIP_DIRECT_CANDIDATE_MISSING@",
            "RELATIONSHIP_DIRECT_CANDIDATE_AMBIGUOUS@",
            "RELATIONSHIP_SIDE_SPAN_DUPLICATE@",
        )
    ):
        return (
            "TARGETED REPAIR: Only when eligible Evidence independently supports one durable direct claim for "
            "each Relationship endpoint and both claim segments share one Evidence ID, emit exactly one "
            "qualifying endpoint cognition per side: target the endpoint Entity, use model_inferred false, "
            "content null, a distinct complete user_stated segment, and the same non-empty scope. Otherwise omit "
            "the entire projection transaction; never invent a missing side. A direct Relationship fact cannot "
            "replace either endpoint cognition."
        )
    if safe_error.startswith("RELATIONSHIP_BINDING_EVIDENCE_UNSUPPORTED@"):
        return (
            "TARGETED REPAIR: The Relationship hypothesis has exactly one support source. If both direct endpoint "
            "bindings share one Evidence ID, select one third, distinct substantive conflict-or-contrast segment "
            "from that same Evidence ID as the sole hypothesis source. If the bindings belong to different "
            "Evidence IDs, no valid local repair exists under this contract: omit the entire three-Cognition "
            "projection transaction. Never add multiple hypothesis sources or invent same-Evidence claims."
        )
    if safe_error.startswith(
        (
            "RELATIONSHIP_HYPOTHESIS_REQUIRED@",
            "RELATIONSHIP_CONTENT_MUST_BE_NULL@",
        )
    ):
        return (
            "TARGETED REPAIR: Make the Relationship cognition the single inferred hypothesis for this "
            "projection: content_type hypothesis, model_inferred true, content null, perspective null, and "
            "the same non-empty scope as both direct endpoint cognitions."
        )
    if safe_error.startswith("INFERRED_CONTENT_REQUIRED@"):
        return (
            "TARGETED REPAIR: A non-Relationship inferred cognition needs a non-empty supported content "
            "string. Keep content null only for an inferred Relationship hypothesis; omit an optional "
            "inference when eligible Evidence cannot support its content."
        )
    if safe_error.startswith("DIRECT_CONTENT_NOT_NULL@"):
        return (
            "TARGETED REPAIR: For the failing non-inferred direct cognition, set content to null and retain "
            "one exact eligible user-stated source segment so trusted code materializes the claim."
        )
    if (
        safe_error.startswith("DELTA_DOMAIN(")
        and ".kind.not_entity" in safe_error
    ):
        return (
            "TARGETED REPAIR: Remove every new Entity whose kind is preference, trait, state, event, "
            "relationship, or cognition, plus relationships or event references that depend on it. A predicate "
            "label such as nickname, preference, trait, or state is not a durable Entity, and the value of a "
            "nickname or preference remains inside the exact target-specific cognition unless eligible Evidence "
            "independently identifies it as a durable person, animal, place, organization, object, or activity. "
            "Do not repair this by relabeling the semantic-role Entity to an allowed kind. Preserve valid Owner "
            "and third-party cognitions using their exact eligible user-claim segments."
        )
    if safe_error.startswith(
        (
            "SEGMENT_ID_UNKNOWN@",
            "FORMATION_EVIDENCE_SEGMENT_DUPLICATE@",
            "FORMATION_SOURCE_COUNT@",
        )
    ):
        return (
            "TARGETED REPAIR: Select source segment_id only from the original eligible_evidence_segments "
            "catalog. Keep at most one most-direct segment from each Evidence ID and do not invent or copy text."
        )
    if safe_error.startswith("THIRD_PARTY_CANDIDATE_BINDING_AMBIGUOUS@"):
        return (
            "TARGETED REPAIR: Do not assign a comma-coordinated or multi-subject user sentence wholesale to "
            "one new person. If current eligible Evidence uniquely identifies one reviewable new person linked "
            "to the Owner, keep only an exact catalog segment whose direct third-party mention matches the "
            "trusted reference; keep each separate Owner claim on the Owner with its own exact segment. If more "
            "than one direct third-party carrier remains, omit the new-person cognition and preserve exact "
            "unresolved_references instead of guessing."
        )
    if safe_error.startswith("THIRD_PARTY_REFERENCE_UNRESOLVED@"):
        return (
            "TARGETED REPAIR: Read trusted_third_party_reference from the original request. When it is "
            "unresolved or ambiguous, do not select or invent an Entity; emit one unresolved_reference for "
            "the current mention and eligible Evidence."
        )
    return (
        "TARGETED REPAIR: Correct only the named structural code and path while preserving every supported "
        "semantic record that is unrelated to the failure."
    )


class WorldExtractor:
    """Propose, strictly decode and validate a reviewable delta; never mutate ``base``."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def extract(
        self,
        base: MemoryWorldGraph,
        turns: Sequence[ConversationTurn],
        evidence_allowlist: Collection[str],
        *,
        accepted_entity_references: Sequence[AcceptedEntityReference] | None = None,
        reference_continuity_id: str | None = None,
    ) -> WorldDelta:
        eligible = self._preflight(turns, evidence_allowlist)
        eligible_ids = tuple(turn.turn_id for turn in eligible)
        preceding_assistant_context = self._preceding_assistant_context(turns, set(eligible_ids))
        catalog = _segment_catalog(eligible)
        owner_attributed_feedback = _owner_attributed_feedback(catalog)
        if owner_attributed_feedback is not None:
            attributed_delta = _trusted_owner_attributed_feedback(
                base,
                eligible,
                eligible_ids,
                owner_attributed_feedback,
            )
            if attributed_delta is not None:
                return attributed_delta
            return WorldDelta(
                world_id=base.world.world_id,
                source_evidence_ids=eligible_ids,
            )
        trusted_question_only = all(
            _is_trusted_question_only_turn(turn.content)
            for turn in eligible
        )
        owner_third_party_introduction = _owner_third_party_introduction(catalog)
        owner_pet_introduction = _owner_pet_introduction(catalog)
        try:
            third_party_reference = _third_party_reference_context(
                base,
                turns,
                eligible_ids,
                catalog,
                accepted_entity_references,
                reference_continuity_id,
            )
        except EntityIdentityValidationError:
            raise WorldExtractionError(("PREFLIGHT_REFERENCE_HISTORY@$",), attempts=0) from None
        reported_third_party_action = _reported_third_party_action(catalog)
        reported_action_reference = (
            _third_party_reference_context(
                base,
                turns,
                eligible_ids,
                (reported_third_party_action.segment,),
                accepted_entity_references,
                reference_continuity_id,
            )
            if reported_third_party_action is not None
            else _ThirdPartyReferenceContext("not-applicable")
        )
        initial = self._initial_messages(
            base, turns, eligible_ids, catalog, third_party_reference,
        )
        failures: list[str] = []
        rejected_raw: str | None = None

        attempt = 1
        while attempt <= _MAX_EXTRACTION_ATTEMPTS:
            messages = (
                initial
                if attempt == 1
                else self._repair_messages(initial, cast(str, rejected_raw), failures[-1])
            )
            try:
                raw = self._llm.chat(messages)
            except TimeoutError:
                failures.append("LLM_TIMEOUT@$")
                raise WorldExtractionError(failures, attempts=attempt) from None
            except RuntimeError:
                failures.append("LLM_REQUEST@$")
                raise WorldExtractionError(failures, attempts=attempt) from None
            if trusted_question_only:
                return WorldDelta(
                    world_id=base.world.world_id,
                    source_evidence_ids=eligible_ids,
                )
            try:
                delta = self._decode(
                    raw,
                    base,
                    eligible_ids,
                    {segment.segment_id: segment for segment in catalog},
                    preceding_assistant_context,
                    third_party_reference=third_party_reference,
                )
                delta = _minimalize_mixed_subject_naming_candidate(
                    delta,
                    base,
                    catalog,
                    third_party_reference,
                )
                delta = _normalize_candidate_third_party_binding(
                    delta,
                    base,
                    third_party_reference,
                )
                if not _unresolved_references_are_grounded(delta, catalog):
                    raise _DecodeFailure(
                        "UNRESOLVED_REFERENCE_EVIDENCE_MISMATCH",
                        "$.unresolved_references",
                    )
                self._validate_conflict_facets(delta)
                delta.validate_against(base, frozenset(eligible_ids))
                if (
                    owner_third_party_introduction is not None
                    and not _has_owner_third_party_introduction_coverage(
                        delta, base, owner_third_party_introduction,
                    )
                ):
                    raise _DecodeFailure(
                        "OWNER_THIRD_PARTY_COVERAGE_EMPTY", "$.new_relationships",
                    )
                if (
                    owner_pet_introduction is not None
                    and not _has_owner_pet_introduction_coverage(
                        delta, base, owner_pet_introduction,
                    )
                ):
                    raise _DecodeFailure(
                        "OWNER_PET_COVERAGE_EMPTY", "$.new_relationships",
                    )
                if (
                    third_party_reference.state == "resolved"
                    and not _has_resolved_direct_third_party_cognition(
                        delta, third_party_reference, base.world.owner_entity_id,
                    )
                ):
                    raise _DecodeFailure(
                        "THIRD_PARTY_DIRECT_COVERAGE_EMPTY", "$.new_cognitions",
                    )
                candidate_bound_entity_id = (
                    _candidate_bound_third_party_entity_id(
                        delta, base, third_party_reference,
                    )
                    if third_party_reference.state in {"unresolved", "ambiguous"}
                    else None
                )
                if (
                    third_party_reference.state in {"unresolved", "ambiguous"}
                    and candidate_bound_entity_id is None
                    and _has_unbound_new_person_direct_claim(
                        delta, base, third_party_reference,
                    )
                ):
                    raise _DecodeFailure(
                        "THIRD_PARTY_CANDIDATE_BINDING_AMBIGUOUS",
                        "$.new_cognitions",
                    )
                if (
                    third_party_reference.state in {"unresolved", "ambiguous"}
                    and not _has_current_unresolved_reference(delta, third_party_reference)
                    and candidate_bound_entity_id is None
                ):
                    raise _DecodeFailure(
                        "THIRD_PARTY_REFERENCE_UNRESOLVED", "$.unresolved_references",
                    )
                if (
                    reported_third_party_action is not None
                    and reported_action_reference.state == "resolved"
                    and not _has_reported_third_party_action_coverage(
                        delta,
                        reported_third_party_action,
                        reported_action_reference,
                    )
                ):
                    raise _DecodeFailure(
                        "REPORTED_THIRD_PARTY_ACTION_COVERAGE_EMPTY",
                        "$.new_events",
                    )
                if (
                    reported_third_party_action is not None
                    and reported_action_reference.state in {"unresolved", "ambiguous"}
                    and not _has_current_unresolved_reference(
                        delta, reported_action_reference,
                    )
                ):
                    raise _DecodeFailure(
                        "THIRD_PARTY_REFERENCE_UNRESOLVED", "$.unresolved_references",
                    )
                if (
                    _requires_owner_memory_coverage(eligible)
                    and not _has_owner_related_coverage(delta, base.world.owner_entity_id)
                ):
                    raise _DecodeFailure("OWNER_MEMORY_COVERAGE_EMPTY", "$.new_cognitions")
                return delta
            except _DecodeFailure as err:
                failures.append(err.safe_code)
                rejected_raw = raw
            except WorldDeltaValidationError as err:
                # Delta issue codes contain paths/types only, never Evidence
                # content or rejected model values.  Returning a bounded set
                # makes the bounded repair attempts actionable without leaking raw
                # output.
                failures.append(_domain_failure_code(err.issues))
                rejected_raw = raw
            except ValueError:
                # Model/dataclass validation errors are deliberately summarized,
                # rather than exposing the original reply or evidence content.
                failures.append("DELTA_DOMAIN@$")
                rejected_raw = raw
            if attempt == _MAX_EXTRACTION_ATTEMPTS or (
                attempt == _ORDINARY_MAX_EXTRACTION_ATTEMPTS
                and not _has_conflict_projection_bonus_repair(failures)
            ):
                break
            attempt += 1

        if (
            attempt == _ORDINARY_MAX_EXTRACTION_ATTEMPTS
            and len(failures) == _ORDINARY_MAX_EXTRACTION_ATTEMPTS
            and all(_is_optional_stale_event_evidence_failure(failure) for failure in failures)
        ):
            fallback = _trusted_resolved_pet_direct_fallback(
                base,
                eligible_ids,
                catalog,
                preceding_assistant_context,
                third_party_reference,
            )
            if fallback is not None:
                return fallback
        owner_third_party_error = (
            "OWNER_THIRD_PARTY_COVERAGE_EMPTY@$.new_relationships"
        )
        if (
            owner_third_party_introduction is not None
            and failures == [owner_third_party_error] * attempt
        ):
            fallback = _trusted_owner_third_party_fallback(
                base,
                eligible,
                eligible_ids,
                owner_third_party_introduction,
            )
            if fallback is not None:
                return fallback
        if (
            owner_pet_introduction is not None
            and failures
            and all(
                failure.startswith("FACET_VALUE_REQUIRED@$.new_events[")
                or failure == "OWNER_PET_COVERAGE_EMPTY@$.new_relationships"
                for failure in failures
            )
        ):
            fallback = _trusted_owner_pet_fallback(
                base,
                eligible_ids,
                owner_pet_introduction,
            )
            if fallback is not None:
                return fallback
        optional_inference_failures = {
            "INFERRED_CONTENT_REQUIRED@$.new_cognitions[0].content",
            "PERSPECTIVE_REQUIRED@$.new_cognitions[0].perspective",
            "REPORTED_THIRD_PARTY_ACTION_COVERAGE_EMPTY@$.new_events",
        }
        if (
            reported_third_party_action is not None
            and reported_action_reference.state == "resolved"
            and failures
            and all(failure in optional_inference_failures for failure in failures)
        ):
            fallback = _trusted_reported_third_party_action_fallback(
                base,
                eligible,
                eligible_ids,
                reported_third_party_action,
                reported_action_reference,
                failures[-1],
            )
            if fallback is not None:
                return fallback
        raise WorldExtractionError(failures, attempts=attempt)

    @staticmethod
    def _validate_conflict_facets(delta: WorldDelta) -> None:
        for event_index, event in enumerate(delta.new_events):
            if not is_interpersonal_conflict_type(event.event_type):
                continue

            facets_path = f"$.new_events[{event_index}].facets"
            causes = [facet for facet in event.facets if facet.key == "cause"]
            if len(causes) != 1:
                raise _DecodeFailure("CONFLICT_CAUSE_COUNT", facets_path)
            if causes[0].about_entity_id is not None:
                raise _DecodeFailure("CONFLICT_CAUSE_SUBJECT", facets_path)

            participant_ids = {participant.entity_id for participant in event.participants}
            positions_by_subject: dict[str, int] = {}
            for facet_index, facet in enumerate(event.facets):
                facet_path = f"{facets_path}[{facet_index}]"
                if facet.key not in {"cause", "position"}:
                    raise _DecodeFailure("CONFLICT_FACET_KEY", f"{facet_path}.key")
                if facet.key != "position":
                    continue
                subject_id = facet.about_entity_id
                if subject_id is None or subject_id not in participant_ids:
                    raise _DecodeFailure("CONFLICT_POSITION_SUBJECT", f"{facet_path}.about_entity_id")
                positions_by_subject[subject_id] = positions_by_subject.get(subject_id, 0) + 1
            for participant_index, participant in enumerate(event.participants):
                position_count = positions_by_subject.get(participant.entity_id, 0)
                if position_count != 1:
                    raise _DecodeFailure(
                        "CONFLICT_POSITION_COUNT",
                        f"$.new_events[{event_index}].participants[{participant_index}]",
                    )

    @staticmethod
    def _validate_conflict_relationship_projection(
        delta: WorldDelta,
        owner_entity_id: str,
        relationships: Mapping[str, Relationship],
    ) -> None:
        """Require the narrow relationship projection only for a complete conflict shape.

        This is intentionally a cross-record decoder contract rather than a
        generic relationship-cognition rule.  Direct relationship cognitions
        remain valid elsewhere; the contract activates only after a conflict
        event, its one linked relationship, and its two independently grounded
        endpoint direct cognitions already form an unambiguous projection.
        """
        decoded = tuple(zip(delta.new_cognitions, delta.formation_traces, strict=True))
        for event_index, event in enumerate(delta.new_events):
            if not is_interpersonal_conflict_type(event.event_type) or len(event.relationship_ids) != 1:
                continue
            relationship = relationships.get(event.relationship_ids[0])
            if relationship is None:
                continue
            endpoints = (relationship.source_entity_id, relationship.target_entity_id)
            participant_ids = tuple(participant.entity_id for participant in event.participants)
            if (
                len(set(endpoints)) != 2
                or owner_entity_id not in endpoints
                or len(participant_ids) != 2
                or len(set(participant_ids)) != 2
                or set(participant_ids) != set(endpoints)
            ):
                continue
            direct_candidates = {
                endpoint: tuple(
                    (cognition, trace)
                    for cognition, trace in decoded
                    if _is_conflict_projection_direct_candidate(
                        cognition, trace, endpoint, owner_entity_id,
                    )
                )
                for endpoint in endpoints
            }
            if any(len(candidates) != 1 for candidates in direct_candidates.values()):
                continue
            endpoint_candidates = tuple(direct_candidates[endpoint][0] for endpoint in endpoints)
            if endpoint_candidates[0][1].sources[0].claim_span == endpoint_candidates[1][1].sources[0].claim_span:
                raise _DecodeFailure(
                    "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT",
                    f"$.new_events[{event_index}].relationship_ids",
                )
            shared_scopes = {candidate[0].scope for candidate in endpoint_candidates}
            if len(shared_scopes) != 1 or None in shared_scopes:
                continue
            shared_scope = next(iter(shared_scopes))
            inferred_proposals = tuple(
                (cognition, trace)
                for cognition, trace in decoded
                if (
                    cognition.target.kind == "relationship"
                    and cognition.target.id == relationship.id
                    and trace.model_inferred_proposal
                )
            )
            if len(inferred_proposals) != 1:
                raise _DecodeFailure(
                    "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT",
                    f"$.new_events[{event_index}].relationship_ids",
                )
            cognition, trace = inferred_proposals[0]
            if not (
                cognition.content_type == "hypothesis"
                and cognition.scope == shared_scope
                and cognition.formed_by == "inferred"
                and trace.derived_formed_by == "inferred"
                and cognition.perspective == Perspective("system", ())
            ):
                raise _DecodeFailure(
                    "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT",
                    f"$.new_events[{event_index}].relationship_ids",
                )

    @staticmethod
    def _validate_conflict_relationship_direct_span_reuse(
        events: Sequence[WorldEvent],
        owner_entity_id: str,
        relationships: Mapping[str, Relationship],
        decoded: Sequence[tuple[WorldCognition, FormationTrace]],
    ) -> None:
        """Reject duplicate endpoint ClaimSpan identities before inferred decoding.

        An inferred relationship cognition otherwise materializes before the
        final ``WorldDelta`` exists.  This precheck preserves the aggregate
        conflict-contract repair path for the complete conflict shape instead
        of leaking into the generic relationship-side decoder failure.
        """
        for event_index, event in enumerate(events):
            if not is_interpersonal_conflict_type(event.event_type) or len(event.relationship_ids) != 1:
                continue
            relationship = relationships.get(event.relationship_ids[0])
            if relationship is None:
                continue
            endpoints = (relationship.source_entity_id, relationship.target_entity_id)
            participant_ids = tuple(participant.entity_id for participant in event.participants)
            if (
                len(set(endpoints)) != 2
                or owner_entity_id not in endpoints
                or len(participant_ids) != 2
                or len(set(participant_ids)) != 2
                or set(participant_ids) != set(endpoints)
            ):
                continue
            candidates = {
                endpoint: tuple(
                    (cognition, trace)
                    for cognition, trace in decoded
                    if _is_conflict_projection_direct_candidate(
                        cognition, trace, endpoint, owner_entity_id,
                    )
                )
                for endpoint in endpoints
            }
            if any(len(endpoint_candidates) != 1 for endpoint_candidates in candidates.values()):
                continue
            endpoint_candidates = tuple(candidates[endpoint][0] for endpoint in endpoints)
            if endpoint_candidates[0][1].sources[0].claim_span == endpoint_candidates[1][1].sources[0].claim_span:
                raise _DecodeFailure(
                    "CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT",
                    f"$.new_events[{event_index}].relationship_ids",
                )

    @staticmethod
    def _preflight(
        turns: Sequence[ConversationTurn], evidence_allowlist: Collection[str],
    ) -> tuple[ConversationTurn, ...]:
        if not turns:
            raise WorldExtractionError(("PREFLIGHT_EMPTY_TURNS@$",), attempts=0)
        if evidence_allowlist is None or isinstance(evidence_allowlist, str):
            raise WorldExtractionError(("PREFLIGHT_INVALID_ALLOWLIST@$",), attempts=0)
        allowlist = set(evidence_allowlist)
        if not allowlist or any(not isinstance(turn_id, str) or not turn_id for turn_id in allowlist):
            raise WorldExtractionError(("PREFLIGHT_EMPTY_ALLOWLIST@$",), attempts=0)

        if any(not isinstance(turn, ConversationTurn) for turn in turns):
            raise WorldExtractionError(("PREFLIGHT_TURN_TYPE@$",), attempts=0)
        ids = [turn.turn_id for turn in turns]
        if any(not isinstance(turn_id, str) or not turn_id for turn_id in ids) or len(set(ids)) != len(ids):
            raise WorldExtractionError(("PREFLIGHT_TURN_IDS@$",), attempts=0)
        if any(
            not isinstance(turn.content, str)
            or not turn.content
            or not isinstance(turn.occurred_at, str)
            or not turn.occurred_at
            for turn in turns
        ):
            raise WorldExtractionError(("PREFLIGHT_EMPTY_TURN@$",), attempts=0)
        conversations = {turn.conversation_id for turn in turns}
        if len(conversations) != 1 or any(not isinstance(value, str) or not value for value in conversations):
            raise WorldExtractionError(("PREFLIGHT_CONVERSATION@$",), attempts=0)
        by_id = {turn.turn_id: turn for turn in turns}
        unknown = allowlist.difference(by_id)
        if unknown:
            raise WorldExtractionError(("PREFLIGHT_UNKNOWN_EVIDENCE@$",), attempts=0)
        # Stage 1 has no authoritative-tool metadata yet.  A bare ``tool`` role
        # is therefore context only; user turns are the sole Evidence source.
        if any(by_id[turn_id].role != "user" for turn_id in allowlist):
            raise WorldExtractionError(("PREFLIGHT_INELIGIBLE_EVIDENCE@$",), attempts=0)
        if any(turn.role not in {"user", "assistant", "tool"} for turn in turns):
            raise WorldExtractionError(("PREFLIGHT_ROLE@$",), attempts=0)
        if any(not isinstance(turn.content, str) or not turn.content.strip() for turn in turns):
            raise WorldExtractionError(("PREFLIGHT_CONTENT@$",), attempts=0)
        if any(not isinstance(turn.occurred_at, str) or not turn.occurred_at.strip() for turn in turns):
            raise WorldExtractionError(("PREFLIGHT_OCCURRED_AT@$",), attempts=0)
        return tuple(turn for turn in turns if turn.turn_id in allowlist)

    @staticmethod
    def _preceding_assistant_context(
        turns: Sequence[ConversationTurn], eligible_ids: Collection[str],
    ) -> dict[str, tuple[str, str] | None]:
        """Bind each eligible user turn to its immediate trusted assistant turn.

        This uses caller-owned full turn order, never a model-supplied ID or
        model-supplied assistant text. Only the immediately preceding assistant
        turn can establish the assistant-proposed carrier branch.
        """
        eligible = set(eligible_ids)
        contexts: dict[str, tuple[str, str] | None] = {}
        for index, turn in enumerate(turns):
            if turn.turn_id not in eligible:
                continue
            previous = turns[index - 1] if index > 0 else None
            contexts[turn.turn_id] = (
                (previous.turn_id, previous.content)
                if previous is not None and previous.role == "assistant"
                else None
            )
        return contexts

    @staticmethod
    def _initial_messages(
        base: MemoryWorldGraph,
        turns: Sequence[ConversationTurn],
        eligible_ids: tuple[str, ...],
        catalog: Sequence[_EligibleSegment],
        third_party_reference: _ThirdPartyReferenceContext,
    ) -> list[ChatMessage]:
        context = [
            {
                "turn_id": turn.turn_id,
                "conversation_id": turn.conversation_id,
                "role": turn.role,
                "content": turn.content,
                "occurred_at": turn.occurred_at,
                "evidence_eligible": turn.turn_id in eligible_ids,
            }
            for turn in turns
        ]
        evidence_language = (
            "zh-CN"
            if any("\u4e00" <= character <= "\u9fff" for turn in turns if turn.turn_id in eligible_ids for character in turn.content)
            else "en"
        )
        output_language_instruction = (
            "MANDATORY REQUEST-SPECIFIC OUTPUT LANGUAGE: Every natural-language "
            f"event summary, facet value, and cognition content must be {evidence_language}."
        )
        payload = {
            "base_world": asdict(base.world),
            "base_entities": [asdict(value) for value in base.entities.values()],
            "base_relationships": [asdict(value) for value in base.relationships.values()],
            "base_events": [asdict(value) for value in base.events.values()],
            "base_cognitions": [asdict(value) for value in base.cognitions.values()],
            "conversation_context": context,
            "eligible_evidence_ids": eligible_ids,
            "eligible_evidence_segments": [
                {
                    "segment_id": segment.segment_id,
                    "evidence_id": segment.evidence_id,
                    "text": segment.text,
                }
                for segment in catalog
            ],
            "evidence_language": evidence_language,
            "trusted_third_party_reference": {
                "state": third_party_reference.state,
                "segment_id": third_party_reference.segment_id,
                "entity_id": third_party_reference.entity_id,
                "mention": third_party_reference.mention,
                "authority": "accepted BASE plus prior user context only; target binding, never Evidence",
            },
        }
        return [
            ChatMessage(
                role="system",
                content=_SYSTEM_PROMPT + "\n\n" + _OUTPUT_SHAPE + "\n\n" + output_language_instruction,
            ),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        ]

    @staticmethod
    def _repair_messages(
        initial: list[ChatMessage], rejected_raw: str, safe_error: str
    ) -> list[ChatMessage]:
        # Repeat the authorized input and return the rejected local-model reply
        # as transient assistant context so this stateless HTTP request can edit
        # the actual object.  Neither raw text is logged nor retained in the
        # typed error/evidence artifact; the user instruction exposes only a
        # safe structural code/path.
        return [
            *initial,
            ChatMessage(role="assistant", content=rejected_raw),
            ChatMessage(
                role="user",
                content=(
                    "Repair the JSON object immediately above and return only the complete corrected JSON object. "
                    "Keep every supported field unrelated to the named failure unchanged. Never invent a record, "
                    "claim, source, Entity, or Evidence ID; assistant context is never Evidence. Select segment_id "
                    "only from the original eligible_evidence_segments catalog. "
                    + _LIFECYCLE_CLAIM_RULE
                    + " "
                    + _targeted_repair_instruction(safe_error)
                    + " "
                    f"Safe validation error: {safe_error}."
                ),
            ),
        ]

    @staticmethod
    def _decode(
        raw: str,
        base: MemoryWorldGraph,
        eligible_ids: tuple[str, ...],
        segment_catalog: Mapping[str, _EligibleSegment],
        preceding_assistant_context: Mapping[str, tuple[str, str] | None],
        *,
        third_party_reference: _ThirdPartyReferenceContext,
    ) -> WorldDelta:
        try:
            decoded: object = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _DecodeFailure("JSON_SYNTAX", "$") from exc
        root = _object(decoded, "$", _TOP_KEYS)
        world_id = base.world.world_id
        if root["world_id"] != world_id:
            raise _DecodeFailure("WORLD_ID", "$.world_id")
        decoded_entities = tuple(
            _entity(value, f"$.new_entities[{i}]")
            for i, value in enumerate(_array(root["new_entities"], "$.new_entities", max_items=4))
        )
        # Local models occasionally copy a BASE Entity verbatim into
        # ``new_entities`` while also proposing a real addition.  A byte-for-
        # byte typed echo is not a graph change, so omit it before create-only
        # validation.  Do not accept a same-ID replacement: any differing
        # field stays in the delta and is rejected by WorldDelta as an attempt
        # to overwrite a base record.
        new_entities = _without_exact_base_entity_echoes(decoded_entities, base.entities)
        new_relationships = tuple(
            _relationship(value, f"$.new_relationships[{i}]")
            for i, value in enumerate(_array(root["new_relationships"], "$.new_relationships", max_items=2))
        )
        new_events = tuple(
            _event(value, f"$.new_events[{i}]", segment_catalog)
            for i, value in enumerate(_array(root["new_events"], "$.new_events", max_items=2))
        )
        _validate_trip_activity_place_links(base.entities, new_entities, new_events)
        entities_by_id = {**base.entities, **{entity.id: entity for entity in new_entities}}
        relationships_by_id = {
            **base.relationships,
            **{relationship.id: relationship for relationship in new_relationships},
        }
        cognition_values = _array(root["new_cognitions"], "$.new_cognitions", max_items=4)
        non_relationship: dict[int, tuple[WorldCognition, FormationTrace]] = {}
        relationship_values: list[tuple[int, object]] = []
        for i, value in enumerate(cognition_values):
            if _is_inferred_relationship_cognition(value, f"$.new_cognitions[{i}]"):
                relationship_values.append((i, value))
            else:
                non_relationship[i] = _cognition(
                    value,
                    f"$.new_cognitions[{i}]",
                    segment_catalog,
                    preceding_assistant_context,
                    owner_entity_id=base.world.owner_entity_id,
                    entities_by_id=entities_by_id,
                    relationships_by_id=relationships_by_id,
                    third_party_reference=third_party_reference,
                )
        proposed_cognition_id_counts: dict[str, int] = {}
        for value in cognition_values:
            if isinstance(value, dict) and isinstance(value.get("id"), str):
                proposed_id = cast(str, value["id"])
                proposed_cognition_id_counts[proposed_id] = (
                    proposed_cognition_id_counts.get(proposed_id, 0) + 1
                )
        protected_ids = (
            set(base.entities)
            | set(base.relationships)
            | set(base.events)
            | set(base.cognitions)
            | {entity.id for entity in new_entities}
            | {relationship.id for relationship in new_relationships}
            | {event.id for event in new_events}
        )
        relationship_projection_endpoint_ids: set[str] = set()
        for i, value in relationship_values:
            item = _object(
                value,
                f"$.new_cognitions[{i}]",
                frozenset(
                    {
                        "id", "world_id", "target", "content", "content_type",
                        "model_inferred", "perspective", "sources", "scope",
                        "valid_at", "invalid_at",
                    }
                ),
            )
            target = _target(item["target"], f"$.new_cognitions[{i}].target")
            relationship = relationships_by_id.get(target.id)
            if relationship is not None:
                relationship_projection_endpoint_ids.update(
                    (relationship.source_entity_id, relationship.target_entity_id)
                )
        non_relationship_items = _deduplicate_exact_direct_cognitions(
            tuple(non_relationship.items()),
            owner_entity_id=base.world.owner_entity_id,
            proposed_id_counts=proposed_cognition_id_counts,
            protected_ids=protected_ids,
            protected_target_ids=relationship_projection_endpoint_ids,
        )
        non_relationship = dict(non_relationship_items)
        decoded_cognitions: dict[int, tuple[WorldCognition, FormationTrace]] = dict(non_relationship)
        direct_candidates = tuple(value for _, value in non_relationship_items)
        WorldExtractor._validate_conflict_relationship_direct_span_reuse(
            new_events,
            base.world.owner_entity_id,
            relationships_by_id,
            direct_candidates,
        )
        for i, value in relationship_values:
            decoded_cognitions[i] = _cognition(
                value,
                f"$.new_cognitions[{i}]",
                segment_catalog,
                preceding_assistant_context,
                owner_entity_id=base.world.owner_entity_id,
                entities_by_id=entities_by_id,
                relationships_by_id=relationships_by_id,
                direct_candidates=direct_candidates,
                third_party_reference=third_party_reference,
            )
        ordered_cognitions = tuple(
            decoded_cognitions[i] for i in sorted(decoded_cognitions)
        )
        model_uncertainties = tuple(
            _uncertainty(value, f"$.semantic_uncertainties[{i}]")
            for i, value in enumerate(
                _array(root["semantic_uncertainties"], "$.semantic_uncertainties", max_items=4)
            )
        )
        ordered_cognitions, id_uncertainties = _normalize_create_only_cognition_ids(
            ordered_cognitions,
            base,
            uncertainty_capacity=4 - len(model_uncertainties),
        )
        delta = WorldDelta(
            world_id=world_id,
            # The trusted wrapper binds the authorized input set.  The model
            # cannot widen it or fail merely because it echoed IDs in another
            # order; actual event/cognition provenance remains model output.
            source_evidence_ids=eligible_ids,
            new_entities=new_entities,
            new_relationships=new_relationships,
            new_events=new_events,
            new_cognitions=tuple(value[0] for value in ordered_cognitions),
            formation_traces=tuple(value[1] for value in ordered_cognitions),
            unresolved_references=tuple(_unresolved(value, f"$.unresolved_references[{i}]") for i, value in enumerate(_array(root["unresolved_references"], "$.unresolved_references", max_items=4))),
            semantic_uncertainties=model_uncertainties + id_uncertainties,
        )
        WorldExtractor._validate_conflict_relationship_projection(
            delta,
            base.world.owner_entity_id,
            {**base.relationships, **{relationship.id: relationship for relationship in new_relationships}},
        )
        return delta


def _deduplicate_exact_direct_cognitions(
    candidates: Sequence[tuple[int, tuple[WorldCognition, FormationTrace]]],
    *,
    owner_entity_id: str,
    proposed_id_counts: Mapping[str, int],
    protected_ids: Collection[str],
    protected_target_ids: Collection[str],
) -> tuple[tuple[int, tuple[WorldCognition, FormationTrace]], ...]:
    """Collapse only one trusted direct claim emitted under duplicate schemas.

    Exact repeated records keep their first wire-order pair.  An exact positive
    preference carrier is instead canonicalized locally even when every model
    classification or scope differs.  Other competing classifications fail
    closed; no fuzzy text or cross-Evidence merge is attempted.  IDs that
    repeat anywhere in the raw cognition array or collide with another record
    remain visible to the create-only validator instead of being hidden by
    this normalization.
    """
    groups: dict[
        tuple[object, ...],
        list[tuple[int, tuple[WorldCognition, FormationTrace]]],
    ] = {}
    for item in candidates:
        _, (cognition, trace) = item
        key = _exact_direct_cognition_deduplication_key(
            cognition, trace, owner_entity_id,
        )
        if key is None:
            continue
        groups.setdefault(key, []).append(item)

    replacement_by_index: dict[
        int, tuple[int, tuple[WorldCognition, FormationTrace]],
    ] = {}
    discarded_indices: set[int] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        if any(
            proposed_id_counts.get(cognition.id) != 1
            or cognition.id in protected_ids
            or cognition.target.id in protected_target_ids
            for _, (cognition, _) in group
        ):
            continue
        content = group[0][1][0].content
        if (
            _CORRECTION_OR_NEGATION_MARKER.search(content)
            or _NEGATED_PREFERENCE_CLAIM.search(content)
        ):
            raise _DecodeFailure(
                "DIRECT_COGNITION_DUPLICATE_UNSAFE", "$.new_cognitions",
            )
        selected = _select_exact_direct_cognition(group)
        if selected is None:
            raise _DecodeFailure(
                "DIRECT_COGNITION_DUPLICATE_AMBIGUOUS", "$.new_cognitions",
            )
        first_index = group[0][0]
        replacement_by_index[first_index] = (first_index, selected[1])
        discarded_indices.update(index for index, _ in group)

    normalized: list[tuple[int, tuple[WorldCognition, FormationTrace]]] = []
    for item in candidates:
        index, _ = item
        replacement = replacement_by_index.get(index)
        if replacement is not None:
            normalized.append(replacement)
        elif index not in discarded_indices:
            normalized.append(item)
    return tuple(normalized)


def _select_exact_direct_cognition(
    candidates: Sequence[tuple[int, tuple[WorldCognition, FormationTrace]]],
) -> tuple[int, tuple[WorldCognition, FormationTrace]] | None:
    content = candidates[0][1][0].content
    if _EXPLICIT_PREFERENCE_CLAIM.search(content):
        selected = min(candidates, key=lambda candidate: candidate[1][0].id)
        cognition, trace = selected[1]
        scope = _explicit_direct_preference_scope(content, candidates)
        confidence = compute_confidence(
            ConfidenceInputs(
                content_type="preference",
                formed_by=cognition.formed_by,
                support_count=trace.effective_support_count,
                contradict_count=trace.contradict_count,
                hedged=False,
            )
        )
        cred_status = derive_cred_status(
            confidence,
            trace.contradict_count,
            "preference",
            support_count=trace.effective_support_count,
        )
        return (
            selected[0],
            (
                replace(
                    cognition,
                    content_type="preference",
                    confidence=confidence,
                    cred_status=cred_status,
                    scope=scope,
                ),
                trace,
            ),
        )
    semantic_labels = {
        (cognition.content_type, cognition.scope)
        for _, (cognition, _) in candidates
    }
    if len(semantic_labels) == 1:
        return candidates[0]
    return None


def _explicit_direct_preference_scope(
    content: str,
    candidates: Sequence[tuple[int, tuple[WorldCognition, FormationTrace]]],
) -> str | None:
    """Keep one scope only when its literal domain is explicit in the claim."""
    normalized_content = _normalize_quote(content)
    anchored: list[str] = []
    for scope in sorted(
        {
            cognition.scope
            for _, (cognition, _) in candidates
            if cognition.scope is not None
        }
    ):
        normalized_scope = _normalize_quote(scope)
        if not normalized_scope:
            continue
        escaped_scope = re.escape(normalized_scope)
        chinese_context = re.search(
            rf"(?:在|关于|对于|就)?{escaped_scope}(?:时|期间|方面|上|中)",
            normalized_content,
            re.IGNORECASE,
        )
        english_context = re.search(
            rf"\b(?:when|while|during|in|for|about|regarding)\b"
            rf"[^.?!]{{0,24}}\b{escaped_scope}\b",
            normalized_content,
            re.IGNORECASE,
        )
        if chinese_context is not None or english_context is not None:
            anchored.append(scope)
    return anchored[0] if len(anchored) == 1 else None


def _exact_direct_cognition_deduplication_key(
    cognition: WorldCognition,
    trace: FormationTrace,
    owner_entity_id: str,
) -> tuple[object, ...] | None:
    if (
        cognition.target.kind != "entity"
        or cognition.formed_by != "stated"
        or cognition.perspective != Perspective("entity", (owner_entity_id,))
        or cognition.valid_at is not None
        or cognition.invalid_at is not None
        or trace.model_inferred_proposal
        or trace.derived_formed_by != "stated"
        or trace.raw_support_count != 1
        or trace.effective_support_count != 1
        or trace.contradict_count != 0
        or trace.content_bindings
        or len(trace.sources) != 1
        or len(cognition.sources) != 1
    ):
        return None
    source = trace.sources[0]
    if not (
        source.relation == "support"
        and source.proposition_origin_proposal == "user_stated"
        and source.local_origin_decision == "exact_user_claim"
        and cognition.sources[0] == EvidenceLink(source.evidence_id, "support")
        and _sha256(cognition.content) == source.claim_span.claim_sha256
    ):
        return None
    return (
        cognition.world_id,
        cognition.target,
        cognition.content,
        cognition.formed_by,
        cognition.perspective,
        cognition.sources,
        cognition.valid_at,
        cognition.invalid_at,
        trace.model_inferred_proposal,
        trace.sources,
        trace.derived_formed_by,
        trace.raw_support_count,
        trace.effective_support_count,
        trace.contradict_count,
        trace.content_bindings,
    )


def _normalize_create_only_cognition_ids(
    candidates: Sequence[tuple[WorldCognition, FormationTrace]],
    base: MemoryWorldGraph,
    *,
    uncertainty_capacity: int,
) -> tuple[
    tuple[tuple[WorldCognition, FormationTrace], ...],
    tuple[SemanticUncertainty, ...],
]:
    """Rekey one safe exact direct claim instead of overwriting BASE.

    Only the identifier changes. Model-authored/inferred content, confirmation,
    contradictions, lifecycle claims, corrections, relationship targets,
    same-delta duplicate IDs, and cross-kind collisions remain fail-closed.
    """
    id_counts: dict[str, int] = {}
    for cognition, _ in candidates:
        id_counts[cognition.id] = id_counts.get(cognition.id, 0) + 1
    all_base_ids = (
        set(base.entities)
        | set(base.relationships)
        | set(base.events)
        | set(base.cognitions)
    )
    reserved_ids = set(all_base_ids)
    normalized: list[tuple[WorldCognition, FormationTrace]] = []
    uncertainties: list[SemanticUncertainty] = []
    for cognition, trace in candidates:
        should_rekey = (
            cognition.id in base.cognitions
            and cognition.id.startswith("cog:")
            and id_counts[cognition.id] == 1
            and len(uncertainties) < uncertainty_capacity
            and _is_safe_exact_direct_cognition_for_rekey(
                cognition, trace, base.world.owner_entity_id,
            )
        )
        if not should_rekey:
            normalized.append((cognition, trace))
            reserved_ids.add(cognition.id)
            continue
        semantic_identity = json.dumps(
            {
                "world_id": cognition.world_id,
                "target": asdict(cognition.target),
                "content_sha256": _sha256(cognition.content),
                "content_type": cognition.content_type,
                "perspective": asdict(cognition.perspective),
                "scope": cognition.scope,
                "valid_at": cognition.valid_at,
                "invalid_at": cognition.invalid_at,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        replacement_id = (
            "cog:extracted-"
            + hashlib.sha256(semantic_identity.encode("utf-8")).hexdigest()[:24]
        )
        if replacement_id in reserved_ids:
            normalized.append((cognition, trace))
            reserved_ids.add(cognition.id)
            continue
        evidence_ids = tuple(dict.fromkeys(source.evidence_id for source in trace.sources))
        if not evidence_ids:
            normalized.append((cognition, trace))
            reserved_ids.add(cognition.id)
            continue
        normalized.append(
            (
                replace(cognition, id=replacement_id),
                replace(trace, cognition_id=replacement_id),
            )
        )
        reserved_ids.add(replacement_id)
        uncertainties.append(
            SemanticUncertainty(
                "COGNITION_ID_REKEYED_AFTER_BASE_COLLISION",
                evidence_ids,
            )
        )
    return tuple(normalized), tuple(uncertainties)


def _is_safe_exact_direct_cognition_for_rekey(
    cognition: WorldCognition,
    trace: FormationTrace,
    owner_entity_id: str,
) -> bool:
    if (
        cognition.target.kind != "entity"
        or cognition.formed_by != "stated"
        or cognition.perspective != Perspective("entity", (owner_entity_id,))
        or cognition.scope is not None
        or cognition.valid_at is not None
        or cognition.invalid_at is not None
        or trace.model_inferred_proposal
        or trace.derived_formed_by != "stated"
        or trace.raw_support_count != 1
        or trace.effective_support_count != 1
        or trace.contradict_count != 0
        or trace.content_bindings
        or len(trace.sources) != 1
        or _CORRECTION_OR_NEGATION_MARKER.search(cognition.content)
    ):
        return False
    source = trace.sources[0]
    return (
        source.relation == "support"
        and source.local_origin_decision == "exact_user_claim"
        and _sha256(cognition.content) == source.claim_span.claim_sha256
    )


def _without_exact_base_entity_echoes(
    candidates: Sequence[Entity], base_entities: Mapping[str, Entity],
) -> tuple[Entity, ...]:
    """Remove only Entity objects that exactly repeat a caller-owned base record.

    This is intentionally narrower than an ID-based merge: an untrusted model
    cannot use a matching ID to alter any base field, and all non-identical
    same-ID candidates proceed to the regular create-only validator.
    """
    return tuple(
        entity for entity in candidates
        if base_entities.get(entity.id) != entity
    )


def _segment_catalog(eligible: Sequence[ConversationTurn]) -> tuple[_EligibleSegment, ...]:
    """Build deterministic complete segments from caller-owned eligible user turns."""
    segments: list[_EligibleSegment] = []
    for turn in eligible:
        start = 0
        index = 0
        while index < len(turn.content):
            character = turn.content[index]
            if character == "\r" and index + 1 < len(turn.content) and turn.content[index + 1] == "\n":
                end = index + 2
            elif character in ".?!;。！？；\n\r":
                end = index + 1
            else:
                index += 1
                continue
            _append_catalog_segment(segments, turn, start, end)
            start = end
            index = end
        if start < len(turn.content):
            _append_catalog_segment(segments, turn, start, len(turn.content))
        _append_reported_direct_third_party_segments(segments, turn)
    # Keep the long-established sentence segment IDs stable, then append exact
    # role-specific subspans for coordinated multi-claim sentences.  These are
    # still caller-owned Evidence slices; they merely let one user sentence
    # attribute one clause to a third party and another clause to the Owner
    # without materializing the whole sentence as both cognitions.
    for turn in eligible:
        _append_role_claim_subsegments(segments, turn)
    return tuple(segments)


def _append_role_claim_subsegments(
    segments: list[_EligibleSegment], turn: ConversationTurn,
) -> None:
    spans = {match.span() for match in re.finditer(r"[^，,]+", turn.content)}
    # Also split an unpunctuated third-party clause from a following Owner
    # naming clause (for example "she prefers X so my handle is Y").  The
    # boundary comes from a recognized subject/predicate form; no text is
    # generated or paraphrased.
    for pattern in (
        _CHINESE_OWNER_NAMING_PROPOSITION,
        _ENGLISH_OWNER_NAMING_PROPOSITION,
    ):
        for match in pattern.finditer(turn.content):
            if match.start() > 0:
                spans.add((0, match.start()))
                spans.add((match.start(), len(turn.content)))
    candidates: list[tuple[int, int, str]] = []
    for raw_start, raw_end in sorted(spans):
        start, end = raw_start, raw_end
        while start < end and (
            turn.content[start].isspace() or turn.content[start] in "，,;；"
        ):
            start += 1
        while end > start and (
            turn.content[end - 1].isspace() or turn.content[end - 1] in "，,;；"
        ):
            end -= 1
        if start >= end or (start == 0 and end == len(turn.content)):
            continue
        text = turn.content[start:end]
        candidates.append((start, end, text))
    # Do not globally turn commas into semantic boundaries.  This exact-span
    # refinement exists only for the mixed-subject naming shape that caused the
    # live failure: one third-party statement and one Owner naming statement in
    # the same sentence.  Pet descriptions, relationship contrasts, lists, and
    # guesses keep their established sentence-level carrier.
    if not any(_is_direct_third_party_statement(text) for _, _, text in candidates):
        return
    if not any(_is_explicit_owner_naming_proposition(text) for _, _, text in candidates):
        return
    for start, end, text in candidates:
        if not (
            _is_direct_third_party_statement(text)
            or _is_explicit_owner_naming_proposition(text)
        ):
            continue
        if any(
            segment.evidence_id == turn.turn_id
            and segment.start_codepoint == start
            and segment.end_codepoint == end
            for segment in segments
        ):
            continue
        _append_catalog_segment(segments, turn, start, end)


def _append_reported_direct_third_party_segments(
    segments: list[_EligibleSegment],
    turn: ConversationTurn,
) -> None:
    """Expose exact reported direct clauses without promoting adjacent guesses.

    The parent sentence remains in the catalog for model inference grounding.
    A derived entry is only an exact caller-owned span after an explicit speech
    marker and before an explicit owner-inference boundary.  It therefore adds
    no paraphrase and does not turn the adjacent guess into stated Evidence.
    """
    for match in _CHINESE_REPORTED_DIRECT_THIRD_PARTY.finditer(turn.content):
        start, end = match.span("claim")
        if any(
            segment.evidence_id == turn.turn_id
            and segment.start_codepoint == start
            and segment.end_codepoint == end
            for segment in segments
        ):
            continue
        claim = turn.content[start:end]
        if not _is_direct_third_party_statement(claim):
            continue
        _append_catalog_segment(segments, turn, start, end)


def _append_catalog_segment(
    segments: list[_EligibleSegment], turn: ConversationTurn, start: int, end: int,
) -> None:
    """Append catalog text that has a lexical anchor; carrier classification stays local."""
    text = turn.content[start:end]
    if not _is_catalog_segment(text):
        return
    segments.append(
        _EligibleSegment(
            segment_id=f"seg-{len(segments):04d}", evidence_id=turn.turn_id,
            text=text, start_codepoint=start, end_codepoint=end, source_content=turn.content,
        )
    )


def _object(value: object, path: str, keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise _DecodeFailure("TYPE_OBJECT", path)
    if any(not isinstance(key, str) for key in value):
        raise _DecodeFailure("KEY_TYPE", path)
    actual = set(value)
    missing = sorted(keys - actual)
    extra = sorted(actual - keys)
    if missing:
        raise _DecodeFailure("MISSING_KEYS(" + ",".join(missing) + ")", path)
    if extra:
        raise _DecodeFailure("EXTRA_KEYS(" + ",".join(extra) + ")", path)
    return cast(Mapping[str, object], value)


def _is_inferred_relationship_cognition(value: object, path: str) -> bool:
    """Classify only after enforcing the exact current cognition wire shape."""
    item = _object(
        value,
        path,
        frozenset(
            {
                "id", "world_id", "target", "content", "content_type", "model_inferred",
                "perspective", "sources", "scope", "valid_at", "invalid_at",
            }
        ),
    )
    model_inferred = item["model_inferred"]
    if not isinstance(model_inferred, bool):
        raise _DecodeFailure("TYPE_BOOL", f"{path}.model_inferred")
    return model_inferred and _target(item["target"], f"{path}.target").kind == "relationship"


def _normalize_quote(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _array(
    value: object,
    path: str,
    *,
    min_items: int = 0,
    max_items: int | None = None,
    unique_items: bool = False,
) -> list[object]:
    if not isinstance(value, list):
        raise _DecodeFailure("TYPE_ARRAY", path)
    if len(value) < min_items:
        raise _DecodeFailure("ARRAY_MIN_ITEMS", path)
    if max_items is not None and len(value) > max_items:
        raise _DecodeFailure("ARRAY_MAX_ITEMS", path)
    if unique_items and any(item in value[:index] for index, item in enumerate(value)):
        raise _DecodeFailure("ARRAY_UNIQUE_ITEMS", path)
    return value


def _string(value: object, path: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise _DecodeFailure("TYPE_STRING", path)
    return value


def _strings(
    value: object,
    path: str,
    *,
    min_items: int = 0,
    max_items: int | None = None,
    unique_items: bool = False,
) -> list[str]:
    return [
        cast(str, _string(item, f"{path}[{i}]"))
        for i, item in enumerate(
            _array(value, path, min_items=min_items, max_items=max_items, unique_items=unique_items)
        )
    ]


def _enum(value: object, path: str, allowed: frozenset[str]) -> str:
    decoded = _string(value, path)
    if decoded not in allowed:
        raise _DecodeFailure("ENUM", path)
    return decoded


def _entity(value: object, path: str) -> Entity:
    item = _object(value, path, frozenset({"id", "world_id", "kind", "canonical_name", "aliases"}))
    return Entity(cast(str, _string(item["id"], f"{path}.id")), cast(str, _string(item["world_id"], f"{path}.world_id")), cast(str, _string(item["kind"], f"{path}.kind")), cast(str, _string(item["canonical_name"], f"{path}.canonical_name")), tuple(_strings(item["aliases"], f"{path}.aliases", max_items=4, unique_items=True)))


def _trip_activity_place_id(entity_id: str) -> str | None:
    prefix = "activity:"
    suffix = "-trip"
    if not entity_id.startswith(prefix) or not entity_id.endswith(suffix):
        return None
    anchor = entity_id[len(prefix) : -len(suffix)]
    if not anchor or ":" in anchor or any(character.isspace() for character in anchor):
        return None
    return "place:" + anchor


def _validate_trip_activity_place_links(
    base_entities: Mapping[str, Entity],
    new_entities: Sequence[Entity],
    new_events: Sequence[WorldEvent],
) -> None:
    """Reject incomplete model-declared trip/place transactions; never synthesize."""
    available_entities = {**base_entities, **{entity.id: entity for entity in new_entities}}
    trip_places: list[tuple[str, str]] = []
    for entity_index, entity in enumerate(new_entities):
        place_id = _trip_activity_place_id(entity.id)
        if place_id is None:
            continue
        place = available_entities.get(place_id)
        if place is None or place.kind.casefold() != "place":
            raise _DecodeFailure(
                "TRIP_ACTIVITY_PLACE_ENTITY_REQUIRED",
                f"$.new_entities[{entity_index}]",
            )
        trip_places.append((entity.id, place_id))
    for event_index, event in enumerate(new_events):
        related = frozenset(event.related_entity_ids)
        for activity_id, place_id in trip_places:
            if activity_id in related and place_id not in related:
                raise _DecodeFailure(
                    "TRIP_ACTIVITY_EVENT_PLACE_LINK_REQUIRED",
                    f"$.new_events[{event_index}].related_entity_ids",
                )


def _relationship(value: object, path: str) -> Relationship:
    item = _object(value, path, frozenset({"id", "world_id", "source_entity_id", "target_entity_id", "relation_type", "bidirectional"}))
    if not isinstance(item["bidirectional"], bool):
        raise _DecodeFailure("TYPE_BOOL", f"{path}.bidirectional")
    return Relationship(cast(str, _string(item["id"], f"{path}.id")), cast(str, _string(item["world_id"], f"{path}.world_id")), cast(str, _string(item["source_entity_id"], f"{path}.source_entity_id")), cast(str, _string(item["target_entity_id"], f"{path}.target_entity_id")), cast(str, _string(item["relation_type"], f"{path}.relation_type")), item["bidirectional"])


def _event(
    value: object, path: str, segment_catalog: Mapping[str, _EligibleSegment],
) -> WorldEvent:
    item = _object(value, path, frozenset({"id", "world_id", "event_type", "summary", "occurred_at", "participants", "related_entity_ids", "relationship_ids", "facets", "evidence_ids"}))
    event_type = cast(str, _string(item["event_type"], f"{path}.event_type"))
    evidence_ids = tuple(_strings(item["evidence_ids"], f"{path}.evidence_ids", min_items=1, max_items=4, unique_items=True))
    facets = tuple(
        _facet(item_value, f"{path}.facets[{i}]", event_type, evidence_ids, segment_catalog)
        for i, item_value in enumerate(_array(item["facets"], f"{path}.facets", max_items=8))
    )
    if is_interpersonal_conflict_type(event_type):
        position_segment_ids = tuple(
            cast(str, _object(item_value, f"{path}.facets[{i}]", frozenset({"key", "value", "segment_id", "about_entity_id"}))["segment_id"])
            for i, item_value in enumerate(_array(item["facets"], f"{path}.facets", max_items=8))
            if isinstance(item_value, dict) and item_value.get("key") == "position"
        )
        if len(set(position_segment_ids)) != len(position_segment_ids):
            raise _DecodeFailure("CONFLICT_POSITION_SEGMENT_REUSE", f"{path}.facets")
    return WorldEvent(
        cast(str, _string(item["id"], f"{path}.id")), cast(str, _string(item["world_id"], f"{path}.world_id")), event_type, cast(str, _string(item["summary"], f"{path}.summary")), cast(str, _string(item["occurred_at"], f"{path}.occurred_at")),
        tuple(_participant(item_value, f"{path}.participants[{i}]") for i, item_value in enumerate(_array(item["participants"], f"{path}.participants", max_items=6))), tuple(_strings(item["related_entity_ids"], f"{path}.related_entity_ids", max_items=6, unique_items=True)), tuple(_strings(item["relationship_ids"], f"{path}.relationship_ids", max_items=4, unique_items=True)), facets, evidence_ids,
    )


def _participant(value: object, path: str) -> EventParticipant:
    item = _object(value, path, frozenset({"entity_id", "role"}))
    return EventParticipant(cast(str, _string(item["entity_id"], f"{path}.entity_id")), _string(item["role"], f"{path}.role", nullable=True))


def _facet(
    value: object,
    path: str,
    event_type: str,
    event_evidence_ids: Collection[str],
    segment_catalog: Mapping[str, _EligibleSegment],
) -> EventFacet:
    item = _object(value, path, frozenset({"key", "value", "segment_id", "about_entity_id"}))
    key = cast(str, _string(item["key"], f"{path}.key"))
    proposed_value = _string(item["value"], f"{path}.value", nullable=True)
    segment_id = _string(item["segment_id"], f"{path}.segment_id", nullable=True)
    about_entity_id = _string(item["about_entity_id"], f"{path}.about_entity_id", nullable=True)
    if not is_interpersonal_conflict_type(event_type) or key != "position":
        if proposed_value is None:
            raise _DecodeFailure("FACET_VALUE_REQUIRED", f"{path}.value")
        return EventFacet(key, proposed_value, about_entity_id)
    if proposed_value is not None:
        raise _DecodeFailure("POSITION_VALUE_MUST_BE_NULL", f"{path}.value")
    if segment_id is None:
        raise _DecodeFailure("POSITION_SEGMENT_REQUIRED", f"{path}.segment_id")
    segment = segment_catalog.get(segment_id)
    if segment is None:
        raise _DecodeFailure("POSITION_SEGMENT_UNKNOWN", f"{path}.segment_id")
    if not _is_substantive_segment(segment.text):
        raise _DecodeFailure("POSITION_SEGMENT_NOT_SUBSTANTIVE", f"{path}.segment_id")
    if segment.evidence_id not in event_evidence_ids:
        raise _DecodeFailure("POSITION_EVIDENCE", f"{path}.segment_id")
    if sum(candidate.text == segment.text for candidate in segment_catalog.values()) != 1:
        raise _DecodeFailure("POSITION_SEGMENT_AMBIGUOUS", f"{path}.segment_id")
    return EventFacet(key, segment.text, about_entity_id)


def _cognition(
    value: object,
    path: str,
    segment_catalog: Mapping[str, _EligibleSegment],
    preceding_assistant_context: Mapping[str, tuple[str, str] | None],
    *,
    owner_entity_id: str,
    entities_by_id: Mapping[str, Entity],
    relationships_by_id: Mapping[str, Relationship],
    third_party_reference: _ThirdPartyReferenceContext,
    direct_candidates: Sequence[tuple[WorldCognition, FormationTrace]] = (),
) -> tuple[WorldCognition, FormationTrace]:
    item = _object(
        value,
        path,
        frozenset(
            {
                "id", "world_id", "target", "content", "content_type", "model_inferred",
                "perspective", "sources", "scope", "valid_at", "invalid_at",
            }
        ),
    )
    content_type = cast(ContentType, _enum(item["content_type"], f"{path}.content_type", _CONTENT_TYPES))
    proposed_content = _string(item["content"], f"{path}.content", nullable=True)
    proposed_scope = _string(item["scope"], f"{path}.scope", nullable=True)
    target = _target(item["target"], f"{path}.target")
    model_inferred = item["model_inferred"]
    if not isinstance(model_inferred, bool):
        raise _DecodeFailure("TYPE_BOOL", f"{path}.model_inferred")
    proposals = tuple(
        _source_proposal(item_value, f"{path}.sources[{i}]")
        for i, item_value in enumerate(_array(item["sources"], f"{path}.sources", min_items=1, max_items=4))
    )
    trusted_confirmation_content = _trusted_qualified_owner_confirmation(
        proposals=proposals,
        segment_catalog=segment_catalog,
        preceding_assistant_context=preceding_assistant_context,
    )
    if trusted_confirmation_content is not None:
        proposed_content = trusted_confirmation_content
        proposed_scope = None
        content_type = "trait"
        target = MemoryTarget("entity", owner_entity_id)
        model_inferred = False
    trusted_direct = _trusted_direct_third_party_normalization(
        proposed_content=proposed_content,
        target=target,
        proposals=proposals,
        segment_catalog=segment_catalog,
        third_party_reference=third_party_reference,
    )
    if trusted_direct is not None:
        # This is not a generic inferred->direct downgrade.  The current user
        # segment is itself the complete direct carrier, while accepted BASE
        # plus prior *user* context uniquely fixes only its pronoun target.
        target, proposals = trusted_direct
        model_inferred = False
    if not model_inferred and proposed_content is None:
        proposals = _narrow_role_specific_direct_source(
            target=target,
            proposals=proposals,
            segment_catalog=segment_catalog,
            owner_entity_id=owner_entity_id,
            entities_by_id=entities_by_id,
        )
        if (
            target == MemoryTarget("entity", owner_entity_id)
            and len(proposals) == 1
            and (owner_segment := segment_catalog.get(proposals[0].segment_id))
            is not None
            and _is_explicit_owner_naming_proposition(owner_segment.text)
        ):
            # A chosen username/nickname is an Owner fact.  The local model may
            # label it as a preference because the adjacent third-party clause
            # expresses one; the exact target-specific span makes this
            # classification deterministic without rewriting the claim.
            content_type = "fact"
            proposed_scope = None
    trace_sources = tuple(
        _formation_source_trace(
            proposal,
            segment_catalog,
            preceding_assistant_context,
            model_inferred=model_inferred,
        )
        for proposal in proposals
    )
    if len({source.evidence_id for source in trace_sources}) != len(trace_sources):
        raise _DecodeFailure("FORMATION_EVIDENCE_SEGMENT_DUPLICATE", f"{path}.sources")
    sources = tuple(EvidenceLink(source.evidence_id, source.relation) for source in trace_sources)
    support_count = sum(source.relation == "support" for source in sources)
    contradict_count = sum(source.relation == "contradict" for source in sources)
    content_bindings: tuple[FormationContentBinding, ...] = ()
    if model_inferred:
        support_traces = tuple(source for source in trace_sources if source.relation == "support")
        if not support_traces:
            raise _DecodeFailure("INFERENCE_SUPPORT_REQUIRED", f"{path}.sources")
        non_grounding_support = next(
            (source for source in support_traces if source.local_origin_decision != "inference_grounding"),
            None,
        )
        if non_grounding_support is not None:
            source_index = trace_sources.index(non_grounding_support)
            raise _DecodeFailure("INFERENCE_GROUNDING_REQUIRED", f"{path}.sources[{source_index}]")
        invalid_contradict = next(
            (
                source
                for source in trace_sources
                if source.relation == "contradict"
                and source.local_origin_decision in {"unverified", "assistant_confirmation"}
            ),
            None,
        )
        if invalid_contradict is not None:
            source_index = trace_sources.index(invalid_contradict)
            raise _DecodeFailure("INFERENCE_CONTRADICT_UNBOUND", f"{path}.sources[{source_index}]")
        if target.kind == "relationship":
            content, content_bindings = _materialize_relationship_content(
                path=path,
                target=target,
                content_type=content_type,
                proposed_content=proposed_content,
                trace_sources=trace_sources,
                entities_by_id=entities_by_id,
                relationships_by_id=relationships_by_id,
                owner_entity_id=owner_entity_id,
                direct_candidates=direct_candidates,
            )
        else:
            if proposed_content is None:
                raise _DecodeFailure("INFERRED_CONTENT_REQUIRED", f"{path}.content")
            content = proposed_content
        formed_by: FormedBy = "inferred"
    else:
        support_traces = tuple(source for source in trace_sources if source.relation == "support")
        insufficiently_bound = next(
            (
                source
                for source in support_traces
                if source.local_origin_decision in {"unverified", "user_negation"}
            ),
            None,
        )
        if insufficiently_bound is not None:
            source_index = trace_sources.index(insufficiently_bound)
            raise _DecodeFailure("FORMATION_UNVERIFIED", f"{path}.sources[{source_index}]")
        confirmations = tuple(source for source in support_traces if source.local_origin_decision == "assistant_confirmation")
        directs = tuple(source for source in support_traces if source.local_origin_decision == "exact_user_claim")
        if confirmations and directs:
            raise _DecodeFailure("FORMATION_MIXED_SUPPORT", f"{path}.sources")
        if confirmations:
            if proposed_content is None:
                raise _DecodeFailure("CONFIRMATION_CONTENT_REQUIRED", f"{path}.content")
            if _normalize_carrier(proposed_content) in _CONFIRMATION_CARRIERS:
                raise _DecodeFailure(
                    "CONFIRMATION_LITERAL_CARRIER", f"{path}.content",
                )
            content = proposed_content
            formed_by = "confirmed"
        elif directs:
            if proposed_content is not None:
                raise _DecodeFailure("DIRECT_CONTENT_NOT_NULL", f"{path}.content")
            direct_texts = {source.claim_span.claim_sha256 for source in directs}
            if len(direct_texts) != 1:
                raise _DecodeFailure("FORMATION_DIRECT_SEGMENTS_MIXED", f"{path}.sources")
            content = _segment_text_for_trace(directs[0], segment_catalog)
            formed_by = "stated"
        else:
            # The domain validator will reject an empty support set. Preserve
            # its existing safe error path instead of inventing a content rule.
            content = proposed_content or ""
            formed_by = "stated"
    perspective = _materialize_cognition_perspective(
        item["perspective"],
        f"{path}.perspective",
        model_inferred=model_inferred,
        target=target,
        owner_entity_id=owner_entity_id,
    )
    # Multiple sources from a single model proposal describe coverage, rather
    # than independent reinforcement. Contradictions remain fully counted.
    effective_support_count = 1 if support_count else 0
    confidence = compute_confidence(
        ConfidenceInputs(
            content_type=content_type,
            formed_by=formed_by,
            support_count=effective_support_count,
            contradict_count=contradict_count,
            hedged=False,
        )
    )
    cred_status = derive_cred_status(
        confidence, contradict_count, content_type, support_count=effective_support_count,
    )
    cognition_id = cast(str, _string(item["id"], f"{path}.id"))
    cognition = WorldCognition(
        cognition_id, cast(str, _string(item["world_id"], f"{path}.world_id")), target, content, content_type, formed_by, confidence, cred_status, perspective,
        sources, proposed_scope, _string(item["valid_at"], f"{path}.valid_at", nullable=True), _string(item["invalid_at"], f"{path}.invalid_at", nullable=True),
    )
    return cognition, FormationTrace(
        cognition_id=cognition_id,
        model_inferred_proposal=model_inferred,
        sources=trace_sources,
        derived_formed_by=formed_by,
        raw_support_count=support_count,
        effective_support_count=effective_support_count,
        contradict_count=contradict_count,
        content_bindings=content_bindings,
    )


def _narrow_role_specific_direct_source(
    *,
    target: MemoryTarget,
    proposals: Sequence[_SourceProposal],
    segment_catalog: Mapping[str, _EligibleSegment],
    owner_entity_id: str,
    entities_by_id: Mapping[str, Entity],
) -> tuple[_SourceProposal, ...]:
    """Choose one exact nested clause when a sentence carries two subjects.

    The model may point both an Owner claim and a third-party claim at the same
    comma-coordinated Evidence segment.  Trusted code narrows only when exactly
    one already-catalogued exact subspan matches the target role.  It never
    rewrites text, changes Evidence, chooses between multiple matching clauses,
    or narrows relationship/event/inferred targets.
    """

    if target.kind != "entity" or len(proposals) != 1:
        return tuple(proposals)
    proposal = proposals[0]
    if (
        proposal.relation != "support"
        or proposal.proposition_origin != "user_stated"
    ):
        return tuple(proposals)
    parent = segment_catalog.get(proposal.segment_id)
    if parent is None:
        return tuple(proposals)
    if target.id == owner_entity_id:
        role_matches = _is_explicit_owner_naming_proposition
    else:
        entity = entities_by_id.get(target.id)
        if entity is None or entity.kind.casefold() not in {"person", "animal"}:
            return tuple(proposals)
        role_matches = _is_direct_third_party_statement
    nested = tuple(
        segment
        for segment in segment_catalog.values()
        if (
            segment.segment_id != parent.segment_id
            and segment.evidence_id == parent.evidence_id
            and parent.start_codepoint <= segment.start_codepoint
            and segment.end_codepoint <= parent.end_codepoint
            and role_matches(segment.text)
        )
    )
    if len(nested) != 1:
        return tuple(proposals)
    return (replace(proposal, segment_id=nested[0].segment_id),)


def _trusted_direct_third_party_normalization(
    *,
    proposed_content: str | None,
    target: MemoryTarget,
    proposals: Sequence[_SourceProposal],
    segment_catalog: Mapping[str, _EligibleSegment],
    third_party_reference: _ThirdPartyReferenceContext,
) -> tuple[MemoryTarget, tuple[_SourceProposal, ...]] | None:
    """Bind one exact direct pronoun carrier to one trusted context entity.

    The predicate remains wholly user-authored because the eventual cognition
    content is materialized from the selected complete current segment.  No
    normalization occurs for model-authored content, mixed/contradictory
    sources, relationship/event targets, inferred wording, or unresolved
    reference context.
    """
    if (
        third_party_reference.state != "resolved"
        or third_party_reference.entity_id is None
        or third_party_reference.segment_id is None
        or third_party_reference.evidence_id is None
        or proposed_content is not None
        or target.kind != "entity"
        or len(proposals) != 1
    ):
        return None
    proposal = proposals[0]
    if (
        proposal.relation != "support"
        or proposal.proposition_origin != "user_stated"
        or proposal.response_act not in {"none", "elaborate", "other"}
    ):
        return None
    proposed_segment = segment_catalog.get(proposal.segment_id)
    direct_segment = segment_catalog.get(third_party_reference.segment_id)
    if (
        proposed_segment is None
        or direct_segment is None
        or proposed_segment.evidence_id != third_party_reference.evidence_id
        or direct_segment.evidence_id != third_party_reference.evidence_id
        or proposed_segment.start_codepoint > direct_segment.start_codepoint
        or proposed_segment.end_codepoint < direct_segment.end_codepoint
        or not _is_direct_third_party_statement(direct_segment.text)
    ):
        return None
    direct_proposal = _SourceProposal(
        segment_id=direct_segment.segment_id,
        relation=proposal.relation,
        proposition_origin=proposal.proposition_origin,
        response_act=proposal.response_act,
        path=proposal.path,
    )
    return (
        MemoryTarget("entity", third_party_reference.entity_id),
        (direct_proposal,),
    )


def _trusted_qualified_owner_confirmation(
    *,
    proposals: Sequence[_SourceProposal],
    segment_catalog: Mapping[str, _EligibleSegment],
    preceding_assistant_context: Mapping[str, tuple[str, str] | None],
) -> str | None:
    """Resolve one qualified carrier against one immediate assistant trait question.

    The current user carrier remains the sole Evidence source.  Assistant text
    supplies only the proposition being affirmed and is retained in the trace
    as an ID/hash by the ordinary formation resolver.
    """
    if len(proposals) != 1:
        return None
    proposal = proposals[0]
    if (
        proposal.relation != "support"
        or proposal.proposition_origin not in {"user_stated", "assistant_proposed"}
        or proposal.response_act not in {"none", "affirm", "other"}
    ):
        return None
    segment = segment_catalog.get(proposal.segment_id)
    if (
        segment is None
        or _normalize_carrier(segment.text) not in _QUALIFIED_CONFIRMATION_CARRIERS
    ):
        return None
    previous = preceding_assistant_context.get(segment.evidence_id)
    if previous is None:
        return None
    return _owner_trait_proposition_from_assistant(previous[1])


def _owner_trait_proposition_from_assistant(content: str) -> str | None:
    """Extract one explicit self-trait proposition from a narrow Chinese question."""
    normalized = unicodedata.normalize("NFKC", content)
    deictic_question = re.search(
        r"你(?:觉得|认为)(?:自己|你自己)是这样的人吗[？?]?",
        normalized,
        re.IGNORECASE,
    )
    if deictic_question is not None:
        preceding = normalized[:deictic_question.start()]
        traits = tuple(
            dict.fromkeys(
                match.group("trait")
                for match in re.finditer(
                    r"(?P<trait>[\u4e00-\u9fff]{1,8})的人",
                    preceding,
                )
                if match.group("trait") not in {"这样", "那样", "什么样"}
            )
        )
        if len(traits) == 1:
            return f"我是{traits[0]}的人"
        return None
    explicit_question = re.search(
        r"你(?:觉得|认为)(?:自己|你自己)(?:是)?"
        r"(?P<trait>[\u4e00-\u9fff]{1,8}?)(?:的人)?吗[？?]?",
        normalized,
        re.IGNORECASE,
    )
    if explicit_question is None:
        return None
    trait = explicit_question.group("trait")
    if trait in {"这样", "那样", "什么样"}:
        return None
    return f"我是{trait}的人"


def _materialize_cognition_perspective(
    proposal: object,
    path: str,
    *,
    model_inferred: bool,
    target: MemoryTarget,
    owner_entity_id: str,
) -> Perspective:
    """Apply the Stage-1 current-user Evidence perspective policy locally."""
    if proposal is not None:
        # Validate a supplied proposal so the trusted decoder continues to
        # mirror the strict schema, but never let it override a locally covered
        # authority case.
        proposed = _perspective(proposal, path)
    else:
        proposed = None
    if not model_inferred:
        return Perspective("entity", (owner_entity_id,))
    if target.kind == "relationship":
        return Perspective("system", ())
    if proposed is None:
        raise _DecodeFailure("PERSPECTIVE_REQUIRED", path)
    return proposed


def _materialize_relationship_content(
    *,
    path: str,
    target: MemoryTarget,
    content_type: ContentType,
    proposed_content: str | None,
    trace_sources: tuple[FormationSourceTrace, ...],
    entities_by_id: Mapping[str, Entity],
    relationships_by_id: Mapping[str, Relationship],
    owner_entity_id: str,
    direct_candidates: Sequence[tuple[WorldCognition, FormationTrace]],
) -> tuple[str, tuple[FormationContentBinding, ...]]:
    """Build a fixed local relationship projection from exact direct cognitions."""
    if content_type != "hypothesis":
        raise _DecodeFailure("RELATIONSHIP_HYPOTHESIS_REQUIRED", f"{path}.content_type")
    if proposed_content is not None:
        raise _DecodeFailure("RELATIONSHIP_CONTENT_MUST_BE_NULL", f"{path}.content")
    relationship = relationships_by_id.get(target.id)
    if relationship is None:
        raise _DecodeFailure("RELATIONSHIP_TARGET_UNKNOWN", f"{path}.target.id")
    endpoints = (relationship.source_entity_id, relationship.target_entity_id)
    if any(endpoint not in entities_by_id for endpoint in endpoints):
        raise _DecodeFailure("RELATIONSHIP_ENDPOINT_ENTITY_UNKNOWN", f"{path}.target.id")
    if owner_entity_id not in endpoints:
        raise _DecodeFailure("RELATIONSHIP_OWNER_NOT_ENDPOINT", f"{path}.target.id")
    if len(set(endpoints)) != 2:
        raise _DecodeFailure("RELATIONSHIP_ENDPOINTS_NOT_DISTINCT", f"{path}.target.id")
    other_endpoint = next(endpoint for endpoint in endpoints if endpoint != owner_entity_id)
    owner_candidate = _relationship_direct_candidate(
        direct_candidates, owner_entity_id, owner_entity_id,
    )
    other_candidate = _relationship_direct_candidate(
        direct_candidates, other_endpoint, owner_entity_id,
    )
    candidates = (owner_candidate, other_candidate)
    bindings = tuple(
        FormationContentBinding(
            semantic_role="relationship_side",
            about_entity_id=cognition.target.id,
            evidence_id=source.evidence_id,
            claim_span=source.claim_span,
        )
        for cognition, source in candidates
    )
    if bindings[0].claim_span == bindings[1].claim_span:
        raise _DecodeFailure("RELATIONSHIP_SIDE_SPAN_DUPLICATE", f"{path}.target.id")
    support_evidence_ids = {source.evidence_id for source in trace_sources if source.relation == "support"}
    if any(binding.evidence_id not in support_evidence_ids for binding in bindings):
        raise _DecodeFailure("RELATIONSHIP_BINDING_EVIDENCE_UNSUPPORTED", f"{path}.target.id")
    content = (
        f"owner-side: {owner_candidate[0].content}\n"
        f"other-side: {other_candidate[0].content}\n"
        "relationship inference: scoped contrast/conflict"
    )
    return content, bindings


def _relationship_direct_candidate(
    decoded: Sequence[tuple[WorldCognition, FormationTrace]], endpoint_entity_id: str, owner_entity_id: str,
) -> tuple[WorldCognition, FormationSourceTrace]:
    """Return the one independently sufficient direct cognition for an endpoint."""
    matches: list[tuple[WorldCognition, FormationSourceTrace]] = []
    for cognition, trace in decoded:
        support_sources = tuple(
            source for source in trace.sources
            if source.relation == "support" and source.local_origin_decision == "exact_user_claim"
        )
        if (
            trace.model_inferred_proposal
            or cognition.target.kind != "entity"
            or cognition.target.id != endpoint_entity_id
            or cognition.formed_by != "stated"
            or trace.derived_formed_by != "stated"
            or cognition.perspective != Perspective("entity", (owner_entity_id,))
            or trace.content_bindings
            or trace.raw_support_count != 1
            or trace.effective_support_count != 1
            or trace.contradict_count != 0
            or len(support_sources) != 1
            or _sha256(cognition.content) != support_sources[0].claim_span.claim_sha256
        ):
            continue
        matches.append((cognition, support_sources[0]))
    if not matches:
        raise _DecodeFailure("RELATIONSHIP_DIRECT_CANDIDATE_MISSING", "$.new_cognitions")
    if len(matches) != 1:
        raise _DecodeFailure("RELATIONSHIP_DIRECT_CANDIDATE_AMBIGUOUS", "$.new_cognitions")
    return matches[0]


def _is_conflict_projection_direct_candidate(
    cognition: WorldCognition,
    trace: FormationTrace,
    endpoint_entity_id: str,
    owner_entity_id: str,
) -> bool:
    """Recognize one independently grounded endpoint direct for the narrow contract."""
    support_sources = tuple(
        source
        for source in trace.sources
        if source.relation == "support" and source.local_origin_decision == "exact_user_claim"
    )
    return (
        not trace.model_inferred_proposal
        and cognition.target.kind == "entity"
        and cognition.target.id == endpoint_entity_id
        and cognition.formed_by == "stated"
        and trace.derived_formed_by == "stated"
        and cognition.perspective == Perspective("entity", (owner_entity_id,))
        and not trace.content_bindings
        and trace.raw_support_count == 1
        and trace.effective_support_count == 1
        and trace.contradict_count == 0
        and len(support_sources) == 1
        and _sha256(cognition.content) == support_sources[0].claim_span.claim_sha256
    )


_QUALIFIED_CONFIRMATION_CARRIERS = frozenset({
    "算是吧", "应该算是", "基本算是", "可以这么说",
})
_CONFIRMATION_CARRIERS = frozenset({
    "对", "对的", "是", "是的", "是啊", "嗯", "嗯嗯", "没错",
    "yes", "yeah", "yep", "right", "sure", "exactly", "前者", "后者",
}) | _QUALIFIED_CONFIRMATION_CARRIERS
_NEGATION_CARRIERS = frozenset({"不", "不是", "不对", "没有", "no", "nope", "not really"})


def _formation_source_trace(
    proposal: _SourceProposal,
    segment_catalog: Mapping[str, _EligibleSegment],
    preceding_assistant_context: Mapping[str, tuple[str, str] | None],
    *,
    model_inferred: bool,
) -> FormationSourceTrace:
    segment = segment_catalog.get(proposal.segment_id)
    if segment is None:
        raise _DecodeFailure("SEGMENT_ID_UNKNOWN", f"{proposal.path}.segment_id")
    claim_span = _claim_span_for_segment(segment)
    previous = preceding_assistant_context.get(segment.evidence_id)
    normalized_carrier = _normalize_carrier(segment.text)
    if normalized_carrier in _CONFIRMATION_CARRIERS:
        if previous is not None:
            local_origin_decision = "assistant_confirmation"
            decision_code = (
                "CARRIER_QUALIFIED_CONFIRMATION"
                if normalized_carrier in _QUALIFIED_CONFIRMATION_CARRIERS
                else "CARRIER_CONFIRMATION"
            )
        else:
            local_origin_decision = "unverified"
            decision_code = "CARRIER_CONTEXT_MISSING"
    elif normalized_carrier in _NEGATION_CARRIERS:
        if previous is not None:
            local_origin_decision = "user_negation"
            decision_code = "CARRIER_NEGATION"
        else:
            local_origin_decision = "unverified"
            decision_code = "CARRIER_CONTEXT_MISSING"
    elif model_inferred and _is_substantive_segment(segment.text):
        local_origin_decision = "inference_grounding"
        decision_code = "INFERENCE_GROUNDING_SEGMENT"
    elif not _is_substantive_segment(segment.text):
        # Catalog construction excludes these. Keep the resolver fail-closed in
        # case an invalid catalog is ever supplied internally.
        local_origin_decision = "unverified"
        decision_code = "CATALOG_SEGMENT_NON_SUBSTANTIVE"
    else:
        local_origin_decision = "exact_user_claim"
        decision_code = "CATALOG_DIRECT_SEGMENT"
    return FormationSourceTrace(
        evidence_id=segment.evidence_id,
        relation=cast(Literal["support", "contradict"], proposal.relation),
        proposition_origin_proposal=cast(Literal["user_stated", "assistant_proposed"], proposal.proposition_origin),
        response_act_proposal=cast(Literal["affirm", "negate", "select", "elaborate", "ask", "none", "other"], proposal.response_act),
        claim_span=claim_span,
        preceding_assistant_turn_id=previous[0] if previous is not None else None,
        preceding_assistant_content_sha256=_sha256(previous[1]) if previous is not None else None,
        local_origin_decision=cast(
            Literal[
                "exact_user_claim", "assistant_confirmation", "inference_grounding", "user_negation", "unverified",
            ],
            local_origin_decision,
        ),
        decision_code=decision_code,
    )


def _claim_span_for_segment(segment: _EligibleSegment) -> ClaimSpan:
    return ClaimSpan(
        start_codepoint=segment.start_codepoint,
        end_codepoint=segment.end_codepoint,
        source_content_sha256=_sha256(segment.source_content),
        claim_sha256=_sha256(segment.text),
    )


def _segment_text_for_trace(
    trace: FormationSourceTrace,
    segment_catalog: Mapping[str, _EligibleSegment],
) -> str:
    for segment in segment_catalog.values():
        if (
            segment.evidence_id == trace.evidence_id
            and segment.start_codepoint == trace.claim_span.start_codepoint
            and segment.end_codepoint == trace.claim_span.end_codepoint
            and _sha256(segment.text) == trace.claim_span.claim_sha256
        ):
            return segment.text
    raise _DecodeFailure("SEGMENT_TRACE_RESOLUTION", "$.new_cognitions")


def _normalize_carrier(value: str) -> str:
    """Normalize a short acknowledgement without letting punctuation add meaning."""
    normalized = _normalize_quote(value)
    start = 0
    end = len(normalized)
    while start < end and unicodedata.category(normalized[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(normalized[end - 1]).startswith("P"):
        end -= 1
    return normalized[start:end].strip()


def _is_substantive_segment(value: str) -> bool:
    """Return whether a complete catalog segment can ground an inference."""
    carrier = _normalize_carrier(value)
    if carrier in _CONFIRMATION_CARRIERS or carrier in _NEGATION_CARRIERS:
        return False
    return _is_catalog_segment(value)


def _is_catalog_segment(value: str) -> bool:
    """Exclude empty, punctuation, symbol, and emoji-only pseudo-segments."""
    return any(unicodedata.category(character)[0] in {"L", "N"} for character in value)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _target(value: object, path: str) -> MemoryTarget:
    item = _object(value, path, frozenset({"kind", "id"}))
    return MemoryTarget(cast(Literal["world", "entity", "relationship", "event"], _enum(item["kind"], f"{path}.kind", _TARGET_KINDS)), cast(str, _string(item["id"], f"{path}.id")))


def _perspective(value: object, path: str) -> Perspective:
    item = _object(value, path, frozenset({"kind", "holder_entity_ids"}))
    return Perspective(cast(Literal["entity", "joint", "system"], _enum(item["kind"], f"{path}.kind", _PERSPECTIVE_KINDS)), tuple(_strings(item["holder_entity_ids"], f"{path}.holder_entity_ids", max_items=4, unique_items=True)))


def _source_proposal(value: object, path: str) -> _SourceProposal:
    item = _object(value, path, frozenset({"segment_id", "relation", "proposition_origin", "response_act"}))
    return _SourceProposal(
        segment_id=cast(str, _string(item["segment_id"], f"{path}.segment_id")),
        relation=_enum(item["relation"], f"{path}.relation", _EVIDENCE_RELATIONS),
        proposition_origin=_enum(item["proposition_origin"], f"{path}.proposition_origin", _PROPOSITION_ORIGINS),
        response_act=_enum(item["response_act"], f"{path}.response_act", _RESPONSE_ACTS),
        path=path,
    )


def _unresolved(value: object, path: str) -> UnresolvedReference:
    item = _object(value, path, frozenset({"mention", "evidence_ids"}))
    return UnresolvedReference(cast(str, _string(item["mention"], f"{path}.mention")), tuple(_strings(item["evidence_ids"], f"{path}.evidence_ids", min_items=1, max_items=4, unique_items=True)))


def _uncertainty(value: object, path: str) -> SemanticUncertainty:
    item = _object(value, path, frozenset({"detail", "evidence_ids"}))
    return SemanticUncertainty(cast(str, _string(item["detail"], f"{path}.detail")), tuple(_strings(item["evidence_ids"], f"{path}.evidence_ids", min_items=1, max_items=4, unique_items=True)))


def _domain_failure_code(issues: tuple[str, ...]) -> str:
    bounded = issues[:12]
    suffix = ",more" if len(issues) > len(bounded) else ""
    return "DELTA_DOMAIN(" + ",".join(bounded) + suffix + ")@$"


def _has_conflict_projection_bonus_repair(failures: Collection[str]) -> bool:
    """Allow one extra repair only after an aggregate conflict-projection failure."""
    return any(
        failure.startswith("CONFLICT_RELATIONSHIP_PROJECTION_CONTRACT@")
        or (
            failure.startswith("DELTA_DOMAIN(")
            and "conflict_relationship_projection." in failure
        )
        for failure in failures
    )


__all__ = [
    "ConversationTurn",
    "FORMATION_CONTRACT_VERSION",
    "WorldExtractionError",
    "WorldExtractor",
    "world_delta_response_format",
]
