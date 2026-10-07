"""Structured language interpretation for the bounded product compiler.

The model may point at exact spans and opaque accepted-entity handles.  It does
not receive canonical entity ids and it never creates a ``WorldDelta``.  This
module validates the proposal against the current user Evidence and compiles
only the currently supported World Change shapes:

* introduce one persistent entity, optionally with one simple attribute;
* attach a bounded cognition to one uniquely resolved accepted entity; or
* connect two program-resolved endpoints with a first-class Relationship; or
* record one time-grounded Event with explicit participant/object roles; or
* attach an Owner evaluation to a program-resolved Entity, Relationship, or
  Event target.

Queries and out-of-scope statement kinds are explicit non-writing outcomes.
Ambiguous or unresolved references are clarification requests, never new
entities and never best-score guesses.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Final, Literal, Mapping, NoReturn, Sequence, cast
from uuid import NAMESPACE_URL, uuid5

from ..confidence import compute_confidence, derive_cred_status
from ..llm import ChatMessage, LLMClient
from ..types import ConfidenceInputs, EvidenceLink, EvidenceRelation
from .delta import ClaimSpan, FormationSourceTrace, FormationTrace, WorldDelta
from .entity_resolution import (
    EntityReferenceResolution,
    EntityReferenceResolver,
    ReferenceMention,
)
from .extractor import ConversationTurn
from .evolution import (
    AcceptedEvolutionStep,
    EvolutionStep,
    WorldEvolutionPlan,
    accepted_historical_relationship_ids,
    current_relationship_ids,
)
from .identity_review import IdentityAuthorityView
from .identity_store import ReviewedIdentityBinding
from .graph import MemoryWorldGraph
from .model import (
    Entity,
    EventFacet,
    EventParticipant,
    MemoryTarget,
    Perspective,
    Relationship,
    StructuredClaim,
    WorldCognition,
    WorldEvent,
)


MeaningAct = Literal["query", "assertion", "mixed", "clarification_answer", "other"]
MentionMode = Literal["introduce", "refer"]
StatementKind = Literal[
    "none",
    "naming",
    "alias",
    "attribute",
    "relationship",
    "event",
    "evaluation",
]
ClaimDisposition = Literal["assert", "correction", "ignore"]
ClaimPolarity = Literal["affirm", "negate"]
EpistemicStatus = Literal["stated", "owner_imagined", "reported", "uncertain"]
EventRelatedRole = Literal["participant", "related_entity"]
PlanState = Literal[
    "candidate", "no_candidate", "clarification_required", "out_of_scope"
]


TURN_MEANING_RESPONSE_FORMAT: Final[dict[str, Any]] = {
    "type": "json_schema",
    "json_schema": {
        "name": "memoweft_turn_meaning_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "act": {
                    "type": "string",
                    "enum": [
                        "query",
                        "assertion",
                        "mixed",
                        "clarification_answer",
                        "other",
                    ],
                },
                "mention": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": "The exact subject/topic span. For refer it must identify an accepted entity, never an attribute value.",
                                },
                                "start": {"type": "integer", "minimum": 0},
                                "end": {"type": "integer", "minimum": 1},
                                "mode": {
                                    "type": "string",
                                    "enum": ["introduce", "refer"],
                                },
                                "kind_hint": {
                                    "anyOf": [
                                        {"type": "null"},
                                        {
                                            "type": "string",
                                            "minLength": 1,
                                            "maxLength": 80,
                                        },
                                    ]
                                },
                                "accepted_handles": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                    "maxItems": 8,
                                    "uniqueItems": True,
                                },
                            },
                            "required": [
                                "text",
                                "start",
                                "end",
                                "mode",
                                "kind_hint",
                                "accepted_handles",
                            ],
                            "additionalProperties": False,
                        },
                    ]
                },
                "statement": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "description": "For query this is an optional requested-field hint and is discarded by the program. For assertions it is a classified statement.",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "none",
                                        "naming",
                                        "alias",
                                        "attribute",
                                        "relationship",
                                        "event",
                                        "evaluation",
                                    ],
                                },
                                "text": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": "For attribute, the exact property-predicate span containing value. It may exclude the subject mention.",
                                },
                                "start": {"type": "integer", "minimum": 0},
                                "end": {"type": "integer", "minimum": 1},
                                "value": {
                                    "description": "For assertion attributes, the exact value span. For query requested-field hints it may be null and is ignored.",
                                    "anyOf": [
                                        {"type": "null"},
                                        {
                                            "type": "object",
                                            "properties": {
                                                "text": {
                                                    "type": "string",
                                                    "minLength": 1,
                                                    "description": "The exact non-empty attribute value inside statement.text/span.",
                                                },
                                                "start": {
                                                    "type": "integer",
                                                    "minimum": 0,
                                                },
                                                "end": {
                                                    "type": "integer",
                                                    "minimum": 1,
                                                },
                                            },
                                            "required": ["text", "start", "end"],
                                            "additionalProperties": False,
                                        },
                                    ],
                                },
                            },
                            "required": ["kind", "text", "start", "end", "value"],
                            "additionalProperties": False,
                        },
                    ]
                },
            },
            "required": ["act", "mention", "statement"],
            "additionalProperties": False,
        },
    },
}

# The live product contract.  The older single mention/statement shape remains
# decodable only so already-staged callers can migrate without reinterpretation.
MULTI_CLAIM_RESPONSE_FORMAT: Final[dict[str, Any]] = {
    "type": "json_schema",
    "json_schema": {
        "name": "memoweft_turn_meaning_v3",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "act": {
                    "type": "string",
                    "enum": [
                        "query",
                        "assertion",
                        "mixed",
                        "clarification_answer",
                        "other",
                    ],
                },
                "mentions": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "minLength": 1},
                            "start": {"type": "integer", "minimum": 0},
                            "end": {"type": "integer", "minimum": 1},
                            "mode": {"type": "string", "enum": ["introduce", "refer"]},
                            "kind_hint": {
                                "anyOf": [
                                    {"type": "null"},
                                    {"type": "string", "minLength": 1, "maxLength": 80},
                                ]
                            },
                            "accepted_handles": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "uniqueItems": True,
                                "maxItems": 8,
                            },
                        },
                        "required": [
                            "text",
                            "start",
                            "end",
                            "mode",
                            "kind_hint",
                            "accepted_handles",
                        ],
                        "additionalProperties": False,
                    },
                },
                "claims": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "minLength": 1},
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "naming",
                                    "alias",
                                    "attribute",
                                    "relationship",
                                    "event",
                                    "evaluation",
                                ],
                            },
                            "subject": {
                                "anyOf": [
                                    {"type": "null"},
                                    {"type": "integer", "minimum": 0},
                                ]
                            },
                            "text": {"type": "string", "minLength": 1},
                            "start": {"type": "integer", "minimum": 0},
                            "end": {"type": "integer", "minimum": 1},
                            "value": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "text": {"type": "string", "minLength": 1},
                                            "start": {"type": "integer", "minimum": 0},
                                            "end": {"type": "integer", "minimum": 1},
                                        },
                                        "required": ["text", "start", "end"],
                                        "additionalProperties": False,
                                    },
                                ]
                            },
                            "predicate": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "text": {"type": "string", "minLength": 1},
                                            "start": {"type": "integer", "minimum": 0},
                                            "end": {"type": "integer", "minimum": 1},
                                        },
                                        "required": ["text", "start", "end"],
                                        "additionalProperties": False,
                                    },
                                ]
                            },
                            "occurred_at": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "text": {"type": "string", "minLength": 1},
                                            "start": {"type": "integer", "minimum": 0},
                                            "end": {"type": "integer", "minimum": 1},
                                        },
                                        "required": ["text", "start", "end"],
                                        "additionalProperties": False,
                                    },
                                ]
                            },
                            "normalized_occurred_at": {
                                "anyOf": [
                                    {"type": "null"},
                                    {"type": "string", "minLength": 1},
                                ]
                            },
                            "relationship_direction": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "string",
                                        "enum": [
                                            "owner_to_focal",
                                            "focal_to_owner",
                                            "subject_to_related",
                                        ],
                                    },
                                ]
                            },
                            "relationship_symmetric": {"type": "boolean"},
                            "event_owner_participates": {"type": "boolean"},
                            "event_subject_role": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "string",
                                        "enum": ["participant", "related_entity"],
                                    },
                                ]
                            },
                            "event_related_roles": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": ["participant", "related_entity"],
                                },
                                "maxItems": 8,
                            },
                            "evaluation_target_claim": {
                                "anyOf": [
                                    {"type": "null"},
                                    {"type": "integer", "minimum": 0},
                                ]
                            },
                            "object_reference": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "text": {"type": "string", "minLength": 1},
                                            "start": {"type": "integer", "minimum": 0},
                                            "end": {"type": "integer", "minimum": 1},
                                        },
                                        "required": ["text", "start", "end"],
                                        "additionalProperties": False,
                                    },
                                ]
                            },
                            "polarity": {
                                "type": "string",
                                "enum": ["affirm", "negate"],
                            },
                            "epistemic_status": {
                                "type": "string",
                                "enum": [
                                    "stated",
                                    "owner_imagined",
                                    "reported",
                                    "uncertain",
                                ],
                            },
                            "disposition": {
                                "type": "string",
                                "enum": ["assert", "correction", "ignore"],
                            },
                            "related_mentions": {
                                "type": "array",
                                "items": {"type": "integer", "minimum": 0},
                                "uniqueItems": True,
                            },
                            "accepted_entity_handles": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "uniqueItems": True,
                            },
                            "accepted_object_handles": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "uniqueItems": True,
                                "maxItems": 1,
                            },
                            "prior_cognition_handles": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "uniqueItems": True,
                                "maxItems": 1,
                            },
                        },
                        "required": [
                            "id",
                            "kind",
                            "subject",
                            "text",
                            "start",
                            "end",
                            "value",
                            "predicate",
                            "occurred_at",
                            "normalized_occurred_at",
                            "relationship_direction",
                            "relationship_symmetric",
                            "event_owner_participates",
                            "event_subject_role",
                            "event_related_roles",
                            "evaluation_target_claim",
                            "object_reference",
                            "polarity",
                            "epistemic_status",
                            "disposition",
                            "related_mentions",
                            "accepted_entity_handles",
                            "accepted_object_handles",
                            "prior_cognition_handles",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["act", "mentions", "claims"],
            "additionalProperties": False,
        },
    },
}


class TurnMeaningError(ValueError):
    """A stable, content-free rejection code for an invalid model proposal."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AcceptedEntityHandle:
    handle: str
    entity_id: str
    kind: str
    canonical_name: str
    aliases: tuple[str, ...]
    recent_in_conversation: bool


@dataclass(frozen=True, slots=True)
class AcceptedCognitionHandle:
    """Opaque selection handle for one current accepted cognition."""

    handle: str
    cognition: WorldCognition
    target_context: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AcceptedWorldObjectHandle:
    """Opaque, snapshot-bound selection handle for one current World object."""

    handle: str
    target: MemoryTarget
    target_context: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MeaningMention:
    text: str
    start: int
    end: int
    mode: MentionMode
    kind_hint: str | None
    accepted_handles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MeaningStatement:
    kind: StatementKind
    text: str
    start: int
    end: int
    value: MeaningValueSpan | None = None


@dataclass(frozen=True, slots=True)
class MeaningValueSpan:
    """The exact value portion of an attribute or evaluation statement."""

    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class MeaningClaim:
    """One model-proposed, exact-span claim; never a direct write command."""

    claim_id: str
    kind: StatementKind
    subject_mention_index: int | None
    text: str
    start: int
    end: int
    value: MeaningValueSpan | None
    predicate: MeaningValueSpan | None = None
    occurred_at: MeaningValueSpan | None = None
    normalized_occurred_at: str | None = None
    relationship_direction: (
        Literal[
            "owner_to_focal",
            "focal_to_owner",
            "subject_to_related",
        ]
        | None
    ) = None
    relationship_symmetric: bool = False
    event_owner_participates: bool = False
    event_subject_role: EventRelatedRole | None = None
    event_related_roles: tuple[EventRelatedRole, ...] = ()
    evaluation_target_claim_index: int | None = None
    object_reference: MeaningValueSpan | None = None
    polarity: ClaimPolarity = "affirm"
    epistemic_status: EpistemicStatus = "stated"
    disposition: ClaimDisposition = "assert"
    related_mention_indices: tuple[int, ...] = ()
    accepted_entity_handles: tuple[str, ...] = ()
    accepted_object_handles: tuple[str, ...] = ()
    prior_cognition_handles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductMentionResolution:
    """A program-owned binding from a proposal mention index to an Entity."""

    mention_index: int
    entity_id: str


@dataclass(frozen=True, slots=True)
class ProductClaimResolution:
    """The formal World target compiled for one language-level claim."""

    claim_index: int
    subject_entity_id: str | None
    related_entity_ids: tuple[str, ...]
    target_kind: Literal["entity", "relationship", "event"]
    target_id: str
    source_entity_id: str | None = None
    target_entity_id: str | None = None
    relation_type: str | None = None
    bidirectional: bool | None = None
    participant_entity_ids: tuple[str, ...] = ()
    object_entity_ids: tuple[str, ...] = ()
    owner_participates: bool | None = None
    event_type: str | None = None
    occurred_at: str | None = None


@dataclass(frozen=True, slots=True)
class ProductClaimBundle:
    """Stable handoff material for SQLite/adapter; it contains no authority."""

    focal_entity_id: str
    evidence_id: str
    perspective_holder_entity_id: str
    claims: tuple[MeaningClaim, ...]
    resolved_mentions: tuple[ProductMentionResolution, ...] = ()
    claim_resolutions: tuple[ProductClaimResolution, ...] = ()

    def to_data(self) -> dict[str, object]:
        return {
            "version": 3,
            "focal_entity_id": self.focal_entity_id,
            "evidence_id": self.evidence_id,
            "perspective_holder_entity_id": self.perspective_holder_entity_id,
            "resolved_mentions": [
                {"mention_index": item.mention_index, "entity_id": item.entity_id}
                for item in self.resolved_mentions
            ],
            "claim_resolutions": [
                {
                    "claim_index": item.claim_index,
                    "subject_entity_id": item.subject_entity_id,
                    "related_entity_ids": list(item.related_entity_ids),
                    "target_kind": item.target_kind,
                    "target_id": item.target_id,
                    "source_entity_id": item.source_entity_id,
                    "target_entity_id": item.target_entity_id,
                    "relation_type": item.relation_type,
                    "bidirectional": item.bidirectional,
                    "participant_entity_ids": list(item.participant_entity_ids),
                    "object_entity_ids": list(item.object_entity_ids),
                    "owner_participates": item.owner_participates,
                    "event_type": item.event_type,
                    "occurred_at": item.occurred_at,
                }
                for item in self.claim_resolutions
            ],
            "claims": [
                {
                    "id": item.claim_id,
                    "kind": item.kind,
                    "subject_mention_index": item.subject_mention_index,
                    "text": item.text,
                    "start": item.start,
                    "end": item.end,
                    "value": None
                    if item.value is None
                    else {
                        "text": item.value.text,
                        "start": item.value.start,
                        "end": item.value.end,
                    },
                    "predicate": None
                    if item.predicate is None
                    else {
                        "text": item.predicate.text,
                        "start": item.predicate.start,
                        "end": item.predicate.end,
                    },
                    "occurred_at": None
                    if item.occurred_at is None
                    else {
                        "text": item.occurred_at.text,
                        "start": item.occurred_at.start,
                        "end": item.occurred_at.end,
                    },
                    "normalized_occurred_at": item.normalized_occurred_at,
                    "relationship_direction": item.relationship_direction,
                    "relationship_symmetric": item.relationship_symmetric,
                    "event_owner_participates": item.event_owner_participates,
                    "event_subject_role": item.event_subject_role,
                    "event_related_roles": list(item.event_related_roles),
                    "evaluation_target_claim": item.evaluation_target_claim_index,
                    "object_reference": None
                    if item.object_reference is None
                    else {
                        "text": item.object_reference.text,
                        "start": item.object_reference.start,
                        "end": item.object_reference.end,
                    },
                    "polarity": item.polarity,
                    "epistemic_status": item.epistemic_status,
                    "disposition": item.disposition,
                    "related_mention_indices": list(item.related_mention_indices),
                    "accepted_entity_handles": list(item.accepted_entity_handles),
                    "accepted_object_handles": list(item.accepted_object_handles),
                    "prior_cognition_handles": list(item.prior_cognition_handles),
                }
                for item in self.claims
            ],
        }


@dataclass(frozen=True, slots=True)
class TurnMeaningProposal:
    act: MeaningAct
    mention: MeaningMention | None = None
    statement: MeaningStatement | None = None
    mentions: tuple[MeaningMention, ...] = ()
    claims: tuple[MeaningClaim, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductTurnPlan:
    state: PlanState
    code: str
    proposal: TurnMeaningProposal
    delta: WorldDelta | None = None
    identity_bindings: tuple[ReviewedIdentityBinding, ...] = ()
    resolved_entity_id: str | None = None
    candidate_entity_names: tuple[str, ...] = ()
    claim_bundle: ProductClaimBundle | None = None
    evolution_steps: tuple[EvolutionStep, ...] = ()
    cognition_updates: tuple[WorldCognition, ...] = ()


class TurnMeaningInterpreter:
    """Ask the model for spans and handles, then decode an exact closed shape."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def interpret(
        self,
        turns: Sequence[ConversationTurn],
        current_user_turn: ConversationTurn,
        handles: Sequence[AcceptedEntityHandle],
        cognition_handles: Sequence[AcceptedCognitionHandle] = (),
        *,
        object_handles: Sequence[AcceptedWorldObjectHandle] = (),
        replacement_correction_prior_handle: str | None = None,
    ) -> TurnMeaningProposal:
        if current_user_turn.role != "user":
            raise TurnMeaningError("current_turn_not_user")
        if not current_user_turn.content.strip():
            raise TurnMeaningError("current_turn_empty")
        if not turns or turns[-1] != current_user_turn:
            raise TurnMeaningError("current_turn_not_final")
        if replacement_correction_prior_handle is not None and (
            not isinstance(replacement_correction_prior_handle, str)
            or not replacement_correction_prior_handle.strip()
            or replacement_correction_prior_handle
            not in {item.handle for item in cognition_handles}
        ):
            raise TurnMeaningError("replacement_correction_prior_handle_invalid")
        contract = _prompt_contract(
            turns,
            current_user_turn,
            handles,
            cognition_handles,
            object_handles,
            replacement_correction_prior_handle,
        )
        try:
            raw = self._llm.chat(
                [
                    ChatMessage(
                        "system",
                        json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
                    ),
                    ChatMessage("user", current_user_turn.content),
                ]
            )
        except Exception as exc:
            raise TurnMeaningError("model_call_failed") from exc
        return decode_turn_meaning(raw, current_user_turn.content)


def build_accepted_entity_handles(
    identity_view: IdentityAuthorityView,
    *,
    conversation_id: str,
    world_hash: str,
) -> tuple[AcceptedEntityHandle, ...]:
    """Build opaque handles and mark only accepted same-conversation focus."""

    recent_entity_ids: set[str] = set()
    same_conversation = [
        binding
        for binding in identity_view.bindings
        if binding.current_entity_id is not None
        and binding.mention.conversation_id == conversation_id
    ]
    # The current compiler deliberately has no discourse-ranking policy. Every
    # accepted entity mentioned in this conversation remains a possible
    # antecedent; if there is more than one, the product must clarify instead
    # of silently treating the latest timestamp as the user's intent.
    recent_entity_ids = {
        cast(str, binding.current_entity_id) for binding in same_conversation
    }
    items: list[AcceptedEntityHandle] = []
    for entity in sorted(identity_view.graph.entities, key=lambda item: item.id):
        if entity.id == identity_view.graph.world.owner_entity_id:
            continue
        opaque = (
            "accepted:"
            + sha256(
                f"turn-meaning-handle-v1\0{world_hash}\0{entity.id}".encode("utf-8")
            ).hexdigest()[:16]
        )
        items.append(
            AcceptedEntityHandle(
                opaque,
                entity.id,
                entity.kind,
                entity.canonical_name,
                entity.aliases,
                entity.id in recent_entity_ids,
            )
        )
    return tuple(items)


def build_accepted_cognition_handles(
    graph: MemoryWorldGraph,
    current_cognitions: Sequence[WorldCognition],
    *,
    world_hash: str,
) -> tuple[AcceptedCognitionHandle, ...]:
    """Build opaque handles with enough semantic context for bounded selection."""

    items: list[AcceptedCognitionHandle] = []
    for cognition in sorted(current_cognitions, key=lambda item: item.id):
        # This bounded reconnection can update only an Owner-held, structured
        # evaluation, an exact Attribute proposition, or the exact statement
        # attached to one current Relationship/Event.  Attribute and object-
        # statement predicates must remain non-empty so a generic value or
        # untyped object cannot reconnect to the wrong proposition.  Keeping
        # unsupported cognitions out of the model catalog prevents the model
        # from proposing handles that trusted code must reject and keeps the
        # prompt proportional as the World grows.
        if (
            cognition.world_id != graph.world.world_id
            or cognition.content_type != "fact"
            or cognition.formed_by != "stated"
            or cognition.structured_claim is None
            or cognition.structured_claim.statement_kind
            not in {
                "attribute",
                "evaluation",
                "relationship_statement",
                "event_statement",
            }
            or (
                cognition.structured_claim.statement_kind == "attribute"
                and (
                    not cognition.structured_claim.predicate
                    or not cognition.structured_claim.value
                )
            )
            or (
                cognition.structured_claim.statement_kind == "relationship_statement"
                and (
                    cognition.target.kind != "relationship"
                    or not cognition.structured_claim.predicate
                    or cognition.structured_claim.value is not None
                )
            )
            or (
                cognition.structured_claim.statement_kind == "event_statement"
                and (
                    cognition.target.kind != "event"
                    or cognition.target.id not in graph.events
                    or not cognition.structured_claim.predicate
                    or cognition.structured_claim.value is not None
                )
            )
            or cognition.perspective
            != Perspective("entity", (graph.world.owner_entity_id,))
        ):
            continue
        target = cognition.target
        context: dict[str, object] = {"kind": target.kind}
        if target.kind == "entity":
            entity = graph.entities.get(target.id)
            if entity is not None:
                context.update(
                    canonical_name=entity.canonical_name,
                    entity_kind=entity.kind,
                )
        elif target.kind == "relationship":
            relationship = graph.relationships.get(target.id)
            if relationship is not None:
                source = graph.entities.get(relationship.source_entity_id)
                target_entity = graph.entities.get(relationship.target_entity_id)
                context.update(
                    source_name=(
                        relationship.source_entity_id
                        if source is None
                        else source.canonical_name
                    ),
                    target_name=(
                        relationship.target_entity_id
                        if target_entity is None
                        else target_entity.canonical_name
                    ),
                    relation_type=relationship.relation_type,
                    bidirectional=relationship.bidirectional,
                )
        elif target.kind == "event":
            event = graph.events.get(target.id)
            if event is not None:
                context.update(
                    summary=event.summary,
                    occurred_at=event.occurred_at,
                    participant_names=[
                        graph.entities[item.entity_id].canonical_name
                        if item.entity_id in graph.entities
                        else item.entity_id
                        for item in event.participants
                    ],
                )
        opaque = (
            "accepted-cognition:"
            + sha256(
                f"turn-meaning-cognition-handle-v1\0{world_hash}\0{cognition.id}".encode(
                    "utf-8"
                )
            ).hexdigest()[:16]
        )
        items.append(AcceptedCognitionHandle(opaque, cognition, context))
    return tuple(items)


def build_accepted_world_object_handles(
    graph: MemoryWorldGraph,
    current_relationships: Sequence[Relationship],
    current_events: Sequence[WorldEvent] = (),
    *,
    world_hash: str,
) -> tuple[AcceptedWorldObjectHandle, ...]:
    """Expose current formal objects through opaque, snapshot-bound handles.

    Historical/ended Relationships never enter the model catalog.  Events have
    no current/history transition contract yet, so an exposed Event must still
    be the exact formal object present in this World snapshot.  The compiler
    independently rechecks both object kinds before accepting a selected
    handle.
    """

    items: list[AcceptedWorldObjectHandle] = []
    seen_ids: set[str] = set()
    for relationship in sorted(current_relationships, key=lambda item: item.id):
        if relationship.id in seen_ids:
            raise ValueError("current relationship catalog contains a duplicate id")
        seen_ids.add(relationship.id)
        stored = graph.relationships.get(relationship.id)
        if stored != relationship or relationship.world_id != graph.world.world_id:
            raise ValueError("current relationship catalog is not part of this World")
        source = graph.entities.get(relationship.source_entity_id)
        target = graph.entities.get(relationship.target_entity_id)
        if source is None or target is None:
            raise ValueError("current relationship catalog has a missing endpoint")
        context: dict[str, object] = {
            "kind": "relationship",
            "source_name": source.canonical_name,
            "target_name": target.canonical_name,
            "relation_type": relationship.relation_type,
            "bidirectional": relationship.bidirectional,
        }
        opaque = (
            "accepted-object:"
            + sha256(
                (
                    "turn-meaning-world-object-handle-v1\0"
                    f"{world_hash}\0relationship\0{relationship.id}"
                ).encode("utf-8")
            ).hexdigest()[:16]
        )
        items.append(
            AcceptedWorldObjectHandle(
                opaque,
                MemoryTarget("relationship", relationship.id),
                context,
            )
        )
    for event in sorted(current_events, key=lambda item: item.id):
        if event.id in seen_ids:
            raise ValueError("current World object catalog contains a duplicate id")
        seen_ids.add(event.id)
        stored_event = graph.events.get(event.id)
        if stored_event != event or event.world_id != graph.world.world_id:
            raise ValueError("current event catalog is not part of this World")
        participant_names: list[str] = []
        for participant in event.participants:
            entity = graph.entities.get(participant.entity_id)
            if entity is None:
                raise ValueError("current event catalog has a missing participant")
            participant_names.append(entity.canonical_name)
        related_names: list[str] = []
        for entity_id in event.related_entity_ids:
            entity = graph.entities.get(entity_id)
            if entity is None:
                raise ValueError("current event catalog has a missing related object")
            related_names.append(entity.canonical_name)
        context = {
            "kind": "event",
            "summary": event.summary,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "participant_names": participant_names,
            "related_entity_names": related_names,
        }
        opaque = (
            "accepted-object:"
            + sha256(
                (
                    "turn-meaning-world-object-handle-v1\0"
                    f"{world_hash}\0event\0{event.id}"
                ).encode("utf-8")
            ).hexdigest()[:16]
        )
        items.append(
            AcceptedWorldObjectHandle(
                opaque,
                MemoryTarget("event", event.id),
                context,
            )
        )
    return tuple(items)


def compile_product_turn(
    *,
    proposal: TurnMeaningProposal,
    current_user_turn: ConversationTurn,
    world_id: str,
    owner_entity_id: str,
    base_graph: MemoryWorldGraph,
    handles: Sequence[AcceptedEntityHandle],
    identity_view: IdentityAuthorityView,
    operation_key: str,
    accepted_evolution_steps: Sequence[AcceptedEvolutionStep] = (),
    accepted_cognition_handles: Sequence[AcceptedCognitionHandle] = (),
    accepted_object_handles: Sequence[AcceptedWorldObjectHandle] = (),
    replacement_correction_event_target: WorldEvent | None = None,
) -> ProductTurnPlan:
    """Validate identity and compile only currently supported change shapes."""

    _require_current_user_evidence(current_user_turn)
    if proposal.claims:
        return _compile_claim_bundle(
            proposal=proposal,
            current_user_turn=current_user_turn,
            world_id=world_id,
            owner_entity_id=owner_entity_id,
            base_graph=base_graph,
            handles=handles,
            identity_view=identity_view,
            operation_key=operation_key,
            accepted_evolution_steps=accepted_evolution_steps,
            accepted_cognition_handles=accepted_cognition_handles,
            accepted_object_handles=accepted_object_handles,
            replacement_correction_event_target=(replacement_correction_event_target),
        )
    if proposal.act == "query":
        return ProductTurnPlan("no_candidate", "query_read_only", proposal)
    if proposal.act in {"mixed", "clarification_answer"}:
        return ProductTurnPlan(
            "out_of_scope", f"act_{proposal.act}_not_supported", proposal
        )
    if proposal.act == "other":
        return ProductTurnPlan("no_candidate", "no_world_assertion", proposal)
    mention = proposal.mention
    statement = proposal.statement
    if mention is None:
        return ProductTurnPlan("no_candidate", "assertion_has_no_referent", proposal)
    _verify_span(
        current_user_turn.content, mention.text, mention.start, mention.end, "mention"
    )
    if statement is not None:
        _verify_span(
            current_user_turn.content,
            statement.text,
            statement.start,
            statement.end,
            "statement",
        )
        _verify_statement_roles(current_user_turn.content, mention, statement)
    if statement is not None and statement.kind in {
        "alias",
        "relationship",
        "event",
        "evaluation",
    }:
        return ProductTurnPlan(
            "out_of_scope", f"statement_{statement.kind}_not_supported", proposal
        )

    handle_map = {item.handle: item for item in handles}
    entity_id: str
    entity_name: str
    new_entities: tuple[Entity, ...]
    if mention.mode == "introduce":
        if mention.accepted_handles:
            raise TurnMeaningError("introduction_selected_accepted_handle")
        kind = _normalize_kind(mention.kind_hint)
        if kind is None:
            return ProductTurnPlan(
                "out_of_scope", "introduction_kind_missing", proposal
            )
        entity_id = "entity:" + str(
            uuid5(
                NAMESPACE_URL,
                f"memoweft-product-entity-v1\0{world_id}\0{operation_key}\0{mention.start}\0{mention.end}",
            )
        )
        entity_name = mention.text.strip()
        if not entity_name:
            raise TurnMeaningError("introduction_name_empty")
        new_entities = (Entity(entity_id, world_id, kind, entity_name),)
    else:
        unknown = tuple(
            handle for handle in mention.accepted_handles if handle not in handle_map
        )
        if unknown:
            raise TurnMeaningError("unknown_accepted_handle")
        reported = tuple(handle_map[item] for item in mention.accepted_handles)
        exact = _resolve_exact_reference(
            base_graph,
            current_user_turn,
            mention,
            identity_view,
        )
        if exact is not None:
            selected = tuple(item for item in handles if item.entity_id == exact)
        else:
            # The model may report opaque candidates, but never gets to choose
            # one.  A non-exact continuation is grounded only when the program
            # sees exactly one accepted entity in this conversation and the
            # model did not propose a conflicting or broader candidate set.
            grounded = tuple(item for item in handles if item.recent_in_conversation)
            if len(grounded) == 1 and (
                not reported
                or (
                    len(reported) == 1
                    and reported[0].entity_id == grounded[0].entity_id
                )
            ):
                selected = grounded
            else:
                # A model-reported handle is evidence of interpretation, not
                # identity authority.  Without an exact name/alias or a
                # program-owned accepted discourse binding it cannot select.
                selected = grounded
        if len(selected) != 1:
            names = tuple(dict.fromkeys(item.canonical_name for item in selected))
            return ProductTurnPlan(
                "clarification_required",
                "referent_ambiguous" if selected else "referent_unresolved",
                proposal,
                candidate_entity_names=names,
            )
        entity_id = selected[0].entity_id
        entity_name = selected[0].canonical_name
        new_entities = ()

    binding = ReviewedIdentityBinding(
        entity_id=entity_id,
        evidence_id=current_user_turn.turn_id,
        conversation_id=current_user_turn.conversation_id,
        occurred_at=current_user_turn.occurred_at,
        start_codepoint=mention.start,
        end_codepoint=mention.end,
        kind_hint=_normalize_kind(mention.kind_hint),
        continuity_scope=current_user_turn.conversation_id,
    )
    if statement is None or statement.kind in {"none", "naming"}:
        if not new_entities:
            # The World is not a generic mention log. Repeating a known name
            # without a supported attribute must not create a change.
            return ProductTurnPlan(
                "no_candidate", "reference_without_attribute", proposal
            )
        delta = WorldDelta(
            world_id=world_id,
            source_evidence_ids=(current_user_turn.turn_id,),
            new_entities=new_entities,
        )
        return ProductTurnPlan(
            "candidate",
            "entity_introduction" if new_entities else "reference_binding",
            proposal,
            delta,
            (binding,),
            entity_id,
            (entity_name,),
        )
    if statement.kind != "attribute":
        return ProductTurnPlan("out_of_scope", "statement_kind_not_supported", proposal)
    if statement.value is None:
        raise TurnMeaningError("attribute_value_missing")
    claim, claim_start, claim_end = _attribute_claim_hull(
        current_user_turn.content, mention, statement
    )
    source_hash = sha256(current_user_turn.content.encode("utf-8")).hexdigest()
    claim_hash = sha256(claim.encode("utf-8")).hexdigest()
    cognition_id = "cog:" + str(
        uuid5(
            NAMESPACE_URL,
            f"memoweft-product-attribute-v1\0{world_id}\0{operation_key}\0{claim_start}\0{claim_end}\0{entity_id}",
        )
    )
    confidence = compute_confidence(ConfidenceInputs("fact", "stated", 1, 0))
    cognition = WorldCognition(
        cognition_id,
        world_id,
        MemoryTarget("entity", entity_id),
        claim,
        "fact",
        "stated",
        confidence,
        derive_cred_status(confidence, 0, "fact", support_count=1),
        Perspective("entity", (owner_entity_id,)),
        (EvidenceLink(current_user_turn.turn_id, "support"),),
    )
    trace = FormationTrace(
        cognition_id=cognition_id,
        model_inferred_proposal=False,
        sources=(
            FormationSourceTrace(
                evidence_id=current_user_turn.turn_id,
                relation="support",
                proposition_origin_proposal="user_stated",
                response_act_proposal="elaborate",
                claim_span=ClaimSpan(claim_start, claim_end, source_hash, claim_hash),
                preceding_assistant_turn_id=None,
                preceding_assistant_content_sha256=None,
                local_origin_decision="exact_user_claim",
                decision_code="product.attribute.exact_user_claim",
            ),
        ),
        derived_formed_by="stated",
        raw_support_count=1,
        effective_support_count=1,
        contradict_count=0,
    )
    delta = WorldDelta(
        world_id=world_id,
        source_evidence_ids=(current_user_turn.turn_id,),
        new_entities=new_entities,
        new_cognitions=(cognition,),
        formation_traces=(trace,),
    )
    return ProductTurnPlan(
        "candidate",
        "simple_attribute",
        proposal,
        delta,
        (binding,),
        entity_id,
        (entity_name,),
    )


def compile_structured_evaluation_correction(
    *,
    proposal: TurnMeaningProposal,
    prior_cognition: WorldCognition,
    current_user_turn: ConversationTurn,
    world_id: str,
    owner_entity_id: str,
    base_graph: MemoryWorldGraph,
    handles: Sequence[AcceptedEntityHandle],
    identity_view: IdentityAuthorityView,
    operation_key: str,
    accepted_evolution_steps: Sequence[AcceptedEvolutionStep] = (),
    accepted_cognition_handles: Sequence[AcceptedCognitionHandle] = (),
    accepted_object_handles: Sequence[AcceptedWorldObjectHandle] = (),
    replacement_value_span: MeaningValueSpan | None = None,
) -> ProductTurnPlan:
    """Compile one explicit structured cognition replacement into typed evolution.

    Natural correction has already selected the current prior.  Turn Meaning is
    still responsible only for exact spans and the newly stated proposition;
    trusted code fixes the target, perspective, evidence, ``corrects`` relation,
    IDs and transaction shape.  The ordinary product compiler continues to
    reject residual corrections, so this narrow entry point first proves either
    one directly named, same-predicate Entity Attribute replacement, the closed
    two-claim Relationship/Event evaluation restatement, or one indirect,
    object-handle-bound Relationship/Event evaluation.  It then compiles an
    isolated assertion copy solely to reuse the established identity, object,
    span and target checks.
    """

    _require_current_user_evidence(current_user_turn)
    owner_perspective = Perspective("entity", (owner_entity_id,))
    prior_claim = prior_cognition.structured_claim
    is_attribute_correction = (
        prior_cognition.target.kind == "entity"
        and prior_cognition.target.id != owner_entity_id
        and prior_cognition.target.id in base_graph.entities
        and prior_claim is not None
        and prior_claim.statement_kind == "attribute"
        and isinstance(prior_claim.predicate, str)
        and bool(prior_claim.predicate.strip())
        and isinstance(prior_claim.value, str)
        and bool(prior_claim.value.strip())
        and prior_claim.polarity == "assert"
        and prior_claim.epistemic_status == "asserted"
    )
    is_evaluation_correction = (
        prior_cognition.target.kind in {"relationship", "event"}
        and prior_claim is not None
        and prior_claim.statement_kind == "evaluation"
        and isinstance(prior_claim.value, str)
        and bool(prior_claim.value.strip())
    )
    if (
        prior_cognition.world_id != world_id
        or not (is_attribute_correction or is_evaluation_correction)
        or (
            prior_cognition.target.kind == "relationship"
            and prior_cognition.target.id not in base_graph.relationships
        )
        or (
            prior_cognition.target.kind == "event"
            and prior_cognition.target.id not in base_graph.events
        )
        or prior_cognition.perspective != owner_perspective
        or prior_cognition.content_type != "fact"
        or prior_cognition.formed_by != "stated"
    ):
        raise TurnMeaningError("structured_correction_prior_unsupported")
    assert prior_claim is not None
    matching_prior_handles = tuple(
        item
        for item in accepted_cognition_handles
        if item.cognition.id == prior_cognition.id and item.cognition == prior_cognition
    )
    if len(matching_prior_handles) != 1:
        raise TurnMeaningError("structured_correction_prior_not_current")
    if proposal.act != "assertion" or len(proposal.claims) not in {1, 2}:
        raise TurnMeaningError("structured_correction_claim_shape_unsupported")

    correction_indices = tuple(
        index
        for index, claim in enumerate(proposal.claims)
        if claim.disposition == "correction"
    )
    if len(correction_indices) != 1:
        raise TurnMeaningError("structured_correction_claim_required")
    correction_index = correction_indices[0]
    correction_claim = proposal.claims[correction_index]
    if (
        correction_claim.polarity != "affirm"
        or correction_claim.epistemic_status != "stated"
        or correction_claim.value is None
        or correction_claim.prior_cognition_handles
    ):
        raise TurnMeaningError("structured_correction_evaluation_invalid")
    if (
        replacement_value_span is None
        or correction_claim.value != replacement_value_span
    ):
        raise TurnMeaningError("structured_correction_value_span_mismatch")
    if is_attribute_correction:
        if (
            len(proposal.claims) != 1
            or correction_index != 0
            or correction_claim.kind != "attribute"
            or correction_claim.subject_mention_index is None
            or correction_claim.evaluation_target_claim_index is not None
            or correction_claim.object_reference is not None
            or correction_claim.accepted_object_handles
            or correction_claim.predicate is None
        ):
            raise TurnMeaningError("structured_correction_attribute_claim_invalid")
        if correction_claim.predicate.text != prior_claim.predicate:
            raise TurnMeaningError("structured_correction_attribute_predicate_mismatch")
    elif correction_claim.kind != "evaluation":
        raise TurnMeaningError("structured_correction_evaluation_invalid")
    target_index = correction_claim.evaluation_target_claim_index
    indirect_object_correction = is_evaluation_correction and target_index is None
    if is_attribute_correction:
        pass
    elif indirect_object_correction:
        if (
            len(proposal.claims) != 1
            or proposal.mentions
            or correction_claim.subject_mention_index is not None
            or correction_claim.object_reference is None
            or len(correction_claim.accepted_object_handles) != 1
        ):
            raise TurnMeaningError("structured_correction_object_reference_invalid")
    else:
        assert target_index is not None
        if (
            len(proposal.claims) != 2
            or correction_claim.subject_mention_index is None
            or correction_claim.object_reference is not None
            or correction_claim.accepted_object_handles
        ):
            raise TurnMeaningError("structured_correction_claim_shape_unsupported")
        if target_index >= correction_index:
            raise TurnMeaningError("evaluation_target_claim_must_precede")
        target_claim = proposal.claims[target_index]
        if (
            target_claim.kind not in {"relationship", "event"}
            or target_claim.kind != prior_cognition.target.kind
            or target_claim.disposition != "assert"
            or target_claim.polarity != "affirm"
            or target_claim.epistemic_status != "stated"
            or target_claim.prior_cognition_handles
        ):
            raise TurnMeaningError("structured_correction_relationship_invalid")
        if {target_index, correction_index} != {0, 1}:
            raise TurnMeaningError("structured_correction_claim_shape_unsupported")

    assertion_claims = tuple(
        replace(claim, disposition="assert") if index == correction_index else claim
        for index, claim in enumerate(proposal.claims)
    )
    # The accepted prior is deliberately withheld from the isolated addition
    # compile.  A changed evaluation must be formed from this turn's exact span;
    # the dedicated checks below, not the ordinary reaffirm/contradict selector,
    # bind it back to the classifier-selected current prior.
    isolated_cognition_handles = tuple(
        item
        for item in accepted_cognition_handles
        if item.cognition.id != prior_cognition.id
    )
    compiled = compile_product_turn(
        proposal=replace(proposal, claims=assertion_claims),
        current_user_turn=current_user_turn,
        world_id=world_id,
        owner_entity_id=owner_entity_id,
        base_graph=base_graph,
        handles=handles,
        identity_view=identity_view,
        operation_key=operation_key,
        accepted_evolution_steps=accepted_evolution_steps,
        accepted_cognition_handles=isolated_cognition_handles,
        accepted_object_handles=accepted_object_handles,
        replacement_correction_event_target=(
            base_graph.events[prior_cognition.target.id]
            if not indirect_object_correction and prior_cognition.target.kind == "event"
            else None
        ),
    )
    if compiled.state in {"no_candidate", "clarification_required", "out_of_scope"}:
        return replace(compiled, proposal=proposal)
    if compiled.state != "candidate" or compiled.delta is None:
        raise TurnMeaningError("structured_correction_" + compiled.code)
    raw_delta = compiled.delta
    if (
        raw_delta.new_entities
        or raw_delta.new_relationships
        or raw_delta.new_events
        or compiled.evolution_steps
        or compiled.cognition_updates
    ):
        raise TurnMeaningError("structured_correction_target_mismatch")

    successors = tuple(
        item
        for item in raw_delta.new_cognitions
        if item.target == prior_cognition.target
        and item.perspective == prior_cognition.perspective
        and item.content_type == prior_cognition.content_type
        and item.formed_by == "stated"
        and item.scope == prior_cognition.scope
        and item.structured_claim is not None
        and item.structured_claim.statement_kind == prior_claim.statement_kind
    )
    if len(successors) != 1:
        raise TurnMeaningError("structured_correction_successor_ambiguous")
    successor = successors[0]
    if successor.id == prior_cognition.id:
        raise TurnMeaningError("structured_correction_successor_id_reused")
    successor_claim = successor.structured_claim
    if successor_claim is None or prior_claim is None:
        raise TurnMeaningError("structured_correction_successor_ambiguous")
    if is_attribute_correction and successor_claim.predicate != prior_claim.predicate:
        raise TurnMeaningError("structured_correction_attribute_predicate_mismatch")
    if successor_claim.value == prior_claim.value:
        raise TurnMeaningError("structured_correction_value_unchanged")
    if successor.sources != (EvidenceLink(current_user_turn.turn_id, "support"),):
        raise TurnMeaningError("structured_correction_successor_evidence_invalid")
    unrelated_cognitions = tuple(
        item for item in raw_delta.new_cognitions if item.id != successor.id
    )
    expected_statement_kind = {
        "relationship": "relationship_statement",
        "event": "event_statement",
    }.get(prior_cognition.target.kind)
    if (is_attribute_correction and unrelated_cognitions) or any(
        item.target != prior_cognition.target
        or item.structured_claim is None
        or item.structured_claim.statement_kind != expected_statement_kind
        for item in unrelated_cognitions
    ):
        raise TurnMeaningError("structured_correction_unrelated_world_change")
    successor_traces = tuple(
        item for item in raw_delta.formation_traces if item.cognition_id == successor.id
    )
    if len(successor_traces) != 1:
        raise TurnMeaningError("structured_correction_formation_invalid")

    correction_delta = WorldDelta(
        world_id,
        (current_user_turn.turn_id,),
        new_cognitions=(successor,),
        formation_traces=successor_traces,
    )
    step = EvolutionStep(
        "evolution:"
        + str(
            uuid5(
                NAMESPACE_URL,
                "\0".join(
                    (
                        "memoweft-structured-evaluation-correction-v1",
                        world_id,
                        operation_key,
                        prior_cognition.id,
                        successor.id,
                    )
                ),
            )
        ),
        "cognition_change",
        "corrects",
        prior_cognition.target,
        (prior_cognition.id,),
        (successor.id,),
        current_user_turn.occurred_at,
        (current_user_turn.turn_id,),
    )
    evolution_plan = WorldEvolutionPlan(correction_delta, (step,))
    evolution_plan.apply_to(
        base_graph,
        {current_user_turn.turn_id},
        known_transition_ids=frozenset(
            item.step.id for item in accepted_evolution_steps
        ),
    )
    return ProductTurnPlan(
        "candidate",
        (
            "structured_attribute_correction"
            if is_attribute_correction
            else "structured_evaluation_correction"
        ),
        proposal,
        correction_delta,
        compiled.identity_bindings if is_attribute_correction else (),
        compiled.resolved_entity_id,
        compiled.candidate_entity_names,
        (
            None
            if compiled.claim_bundle is None
            else replace(compiled.claim_bundle, claims=proposal.claims)
        ),
        (step,),
        (),
    )


def decode_turn_meaning(raw: object, evidence_content: str) -> TurnMeaningProposal:
    if not isinstance(raw, str):
        raise TurnMeaningError("response_not_text")
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise TurnMeaningError("invalid_json") from exc
    if not isinstance(value, dict):
        raise TurnMeaningError("invalid_top_level_shape")
    if set(value) == {"act", "mentions", "claims"}:
        return _decode_claim_proposal(value, evidence_content)
    if set(value) != {"act", "mention", "statement"}:
        raise TurnMeaningError("invalid_top_level_shape")
    act = value["act"]
    if act not in {"query", "assertion", "mixed", "clarification_answer", "other"}:
        raise TurnMeaningError("invalid_act")
    mention = _decode_mention(value["mention"], evidence_content)
    if act == "query":
        # A question must never be rejected as a malformed assertion merely
        # because the model marked its requested field as an attribute.  The
        # top-level action and referent remain exact/validated; the optional
        # field hint is intentionally not candidate input and is discarded.
        return TurnMeaningProposal("query", mention, None)
    statement = _decode_statement(value["statement"], evidence_content)
    if act == "assertion" and mention is None:
        raise TurnMeaningError("assertion_has_no_mention")
    if mention is not None and statement is not None:
        _verify_statement_roles(evidence_content, mention, statement)
    return TurnMeaningProposal(cast(MeaningAct, act), mention, statement)


def _decode_claim_proposal(
    value: Mapping[str, object], content: str
) -> TurnMeaningProposal:
    act = value["act"]
    if act not in {"query", "assertion", "mixed", "clarification_answer", "other"}:
        raise TurnMeaningError("invalid_act")
    raw_mentions = value["mentions"]
    raw_claims = value["claims"]
    if not isinstance(raw_mentions, list) or not isinstance(raw_claims, list):
        raise TurnMeaningError("invalid_claim_collection")
    mentions = tuple(_decode_mention(item, content) for item in raw_mentions)
    if any(item is None for item in mentions):
        raise TurnMeaningError("claims_need_mentions")
    decoded_mentions = cast(tuple[MeaningMention, ...], mentions)
    if act == "query":
        if raw_claims:
            raise TurnMeaningError("query_has_claims")
        return TurnMeaningProposal("query", mentions=decoded_mentions)
    if not mentions and not raw_claims:
        raise TurnMeaningError("claims_need_mentions")
    claims = tuple(
        _decode_claim(item, content, len(decoded_mentions)) for item in raw_claims
    )
    if act == "assertion" and not claims:
        raise TurnMeaningError("assertion_has_no_claims")
    return TurnMeaningProposal(act, mentions=decoded_mentions, claims=claims)


def _decode_claim(value: object, content: str, mention_count: int) -> MeaningClaim:
    required = {
        "id",
        "kind",
        "subject",
        "text",
        "start",
        "end",
        "value",
        "polarity",
        "epistemic_status",
        "disposition",
        "related_mentions",
        "accepted_entity_handles",
        "prior_cognition_handles",
    }
    optional = {
        "predicate",
        "occurred_at",
        "normalized_occurred_at",
        "relationship_direction",
        "relationship_symmetric",
        "event_owner_participates",
        "event_subject_role",
        "event_related_roles",
        "evaluation_target_claim",
        "object_reference",
        "accepted_object_handles",
    }
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or not set(value) <= required | optional
    ):
        raise TurnMeaningError("invalid_claim_shape")
    claim_id, kind, subject = value["id"], value["kind"], value["subject"]
    text, start, end = value["text"], value["start"], value["end"]
    polarity, epistemic, disposition = (
        value["polarity"],
        value["epistemic_status"],
        value["disposition"],
    )
    if (
        not isinstance(claim_id, str)
        or not claim_id.strip()
        or kind
        not in {"naming", "alias", "attribute", "relationship", "event", "evaluation"}
    ):
        raise TurnMeaningError("invalid_claim_kind")
    if kind == "evaluation" and "evaluation_target_claim" not in value:
        # The live strict contract requires the model to distinguish a direct
        # Entity evaluation (null) from a dependency on an earlier formal
        # claim (integer).  Silently defaulting a missing field back to Entity
        # would recreate the very target-flattening this field prevents.
        raise TurnMeaningError("evaluation_target_claim_missing")
    if subject is not None and (
        type(subject) is not int or not 0 <= subject < mention_count
    ):
        raise TurnMeaningError("invalid_claim_subject")
    if (
        not isinstance(text, str)
        or not text
        or type(start) is not int
        or type(end) is not int
    ):
        raise TurnMeaningError("invalid_claim_span")
    start, end = _canonical_model_span(content, text, start, end, "claim")
    value_span = _decode_value_span(value["value"], content)
    predicate_span = _decode_value_span(value.get("predicate"), content)
    occurred_at_span = _decode_value_span(value.get("occurred_at"), content)
    object_reference = _decode_value_span(value.get("object_reference"), content)
    entities = _decode_handle_list(
        value["accepted_entity_handles"], "accepted_entity_handles"
    )
    objects = _decode_handle_list(
        value.get("accepted_object_handles", []),
        "accepted_object_handles",
    )
    priors = _decode_handle_list(
        value["prior_cognition_handles"], "prior_cognition_handles"
    )
    if len(objects) > 1:
        raise TurnMeaningError("accepted_object_handle_ambiguous")
    is_object_event_statement = (
        kind == "event"
        and subject is None
        and object_reference is not None
        and len(objects) <= 1
    )
    normalized_occurred_at = value.get("normalized_occurred_at")
    if normalized_occurred_at is not None and (
        not isinstance(normalized_occurred_at, str)
        or not normalized_occurred_at.strip()
    ):
        raise TurnMeaningError("event_normalized_occurred_at_invalid")
    if kind in {"attribute", "evaluation"} and value_span is None:
        raise TurnMeaningError(f"{kind}_value_missing")
    if kind not in {"attribute", "evaluation"} and value_span is not None:
        raise TurnMeaningError("claim_value_unexpected")
    if (
        kind in {"relationship", "event"}
        and predicate_span is None
        and not is_object_event_statement
    ):
        raise TurnMeaningError("claim_predicate_missing")
    direction = value.get("relationship_direction")
    symmetric = value.get("relationship_symmetric", False)
    if type(symmetric) is not bool:
        raise TurnMeaningError("relationship_symmetric_invalid")
    if kind == "relationship" and direction not in {
        "owner_to_focal",
        "focal_to_owner",
        "subject_to_related",
    }:
        raise TurnMeaningError("relationship_direction_missing")
    if kind != "relationship" and direction is not None:
        raise TurnMeaningError("relationship_direction_unexpected")
    if kind != "relationship" and symmetric:
        raise TurnMeaningError("relationship_symmetric_unexpected")
    event_owner_participates = value.get("event_owner_participates", False)
    if type(event_owner_participates) is not bool:
        raise TurnMeaningError("event_owner_participates_invalid")
    event_subject_role = value.get("event_subject_role")
    if event_subject_role is not None and (
        not isinstance(event_subject_role, str)
        or event_subject_role not in {"participant", "related_entity"}
    ):
        raise TurnMeaningError("event_subject_role_invalid")
    raw_event_roles = value.get("event_related_roles", [])
    if not isinstance(raw_event_roles, list) or any(
        not isinstance(item, str) or item not in {"participant", "related_entity"}
        for item in raw_event_roles
    ):
        raise TurnMeaningError("event_related_roles_invalid")
    evaluation_target_claim = value.get("evaluation_target_claim")
    if evaluation_target_claim is not None and (
        type(evaluation_target_claim) is not int or evaluation_target_claim < 0
    ):
        raise TurnMeaningError("evaluation_target_claim_invalid")
    if (
        polarity not in {"affirm", "negate"}
        or epistemic not in {"stated", "owner_imagined", "reported", "uncertain"}
        or disposition not in {"assert", "correction", "ignore"}
    ):
        raise TurnMeaningError("invalid_claim_semantics")
    related = _decode_index_list(
        value["related_mentions"], mention_count, "related_mentions"
    )
    if kind == "event":
        if is_object_event_statement:
            if (
                predicate_span is not None
                or occurred_at_span is not None
                or normalized_occurred_at is not None
                or event_owner_participates
                or event_subject_role is not None
                or raw_event_roles
                or related
            ):
                raise TurnMeaningError("event_statement_reference_shape_invalid")
        else:
            if occurred_at_span is None:
                raise TurnMeaningError("event_occurred_at_missing")
            if normalized_occurred_at is None:
                raise TurnMeaningError("event_normalized_occurred_at_missing")
            if event_subject_role is None:
                raise TurnMeaningError("event_subject_role_missing")
            if len(raw_event_roles) != len(related):
                raise TurnMeaningError("event_related_roles_mismatch")
    elif (
        occurred_at_span is not None
        or normalized_occurred_at is not None
        or event_owner_participates
        or event_subject_role is not None
        or raw_event_roles
    ):
        raise TurnMeaningError("event_fields_unexpected")
    if kind != "evaluation" and evaluation_target_claim is not None:
        raise TurnMeaningError("evaluation_target_claim_unexpected")
    is_object_evaluation = (
        kind == "evaluation"
        and subject is None
        and evaluation_target_claim is None
        and object_reference is not None
        and len(objects) <= 1
    )
    if subject is None and not (is_object_evaluation or is_object_event_statement):
        raise TurnMeaningError("claim_subject_missing")
    if subject is not None and (object_reference is not None or objects):
        raise TurnMeaningError("object_reference_has_entity_subject")
    if (
        kind != "evaluation"
        and not is_object_event_statement
        and (object_reference is not None or objects)
    ):
        raise TurnMeaningError("object_reference_kind_invalid")
    if object_reference is None and objects:
        raise TurnMeaningError("object_reference_handle_mismatch")
    return MeaningClaim(
        claim_id=claim_id,
        kind=cast(StatementKind, kind),
        subject_mention_index=subject,
        text=text,
        start=start,
        end=end,
        value=value_span,
        predicate=predicate_span,
        occurred_at=occurred_at_span,
        normalized_occurred_at=normalized_occurred_at,
        relationship_direction=cast(Any, direction),
        relationship_symmetric=symmetric,
        event_owner_participates=cast(bool, event_owner_participates),
        event_subject_role=cast(EventRelatedRole | None, event_subject_role),
        event_related_roles=tuple(cast(list[EventRelatedRole], raw_event_roles)),
        evaluation_target_claim_index=evaluation_target_claim,
        object_reference=object_reference,
        polarity=cast(ClaimPolarity, polarity),
        epistemic_status=cast(EpistemicStatus, epistemic),
        disposition=cast(ClaimDisposition, disposition),
        related_mention_indices=related,
        accepted_entity_handles=entities,
        accepted_object_handles=objects,
        prior_cognition_handles=priors,
    )


def _decode_index_list(
    value: object, mention_count: int, label: str
) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or any(type(item) is not int or not 0 <= item < mention_count for item in value)
        or len(set(cast(list[int], value))) != len(value)
    ):
        raise TurnMeaningError(f"invalid_{label}")
    return tuple(cast(list[int], value))


def _decode_handle_list(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(cast(list[str], value))) != len(value)
    ):
        raise TurnMeaningError(f"invalid_{label}")
    return tuple(cast(list[str], value))


@dataclass(frozen=True, slots=True)
class _ResolvedProductMention:
    mention_index: int
    entity_id: str
    entity_name: str
    new_entity: Entity | None
    identity_binding: ReviewedIdentityBinding


@dataclass(frozen=True, slots=True)
class _MentionResolutionFailure:
    state: Literal["clarification_required", "out_of_scope"]
    code: str
    candidate_entity_names: tuple[str, ...] = ()


def _compile_claim_bundle(
    *,
    proposal: TurnMeaningProposal,
    current_user_turn: ConversationTurn,
    world_id: str,
    owner_entity_id: str,
    base_graph: MemoryWorldGraph,
    handles: Sequence[AcceptedEntityHandle],
    identity_view: IdentityAuthorityView,
    operation_key: str,
    accepted_evolution_steps: Sequence[AcceptedEvolutionStep],
    accepted_cognition_handles: Sequence[AcceptedCognitionHandle],
    accepted_object_handles: Sequence[AcceptedWorldObjectHandle],
    replacement_correction_event_target: WorldEvent | None,
) -> ProductTurnPlan:
    """Compile exact, program-resolved claims into one connected WorldDelta."""

    if proposal.act == "query":
        return ProductTurnPlan("no_candidate", "query_read_only", proposal)
    if proposal.act != "assertion":
        return ProductTurnPlan(
            "out_of_scope", f"act_{proposal.act}_not_supported", proposal
        )

    handle_map = {item.handle: item for item in handles}
    cognition_handle_map = {item.handle: item for item in accepted_cognition_handles}
    object_handle_map = {item.handle: item for item in accepted_object_handles}
    if len(object_handle_map) != len(accepted_object_handles):
        raise TurnMeaningError("accepted_object_catalog_invalid")
    if replacement_correction_event_target is not None and (
        replacement_correction_event_target.world_id != world_id
        or base_graph.events.get(replacement_correction_event_target.id)
        != replacement_correction_event_target
    ):
        raise TurnMeaningError("structured_correction_event_target_invalid")
    for mention in proposal.mentions:
        _verify_span(
            current_user_turn.content,
            mention.text,
            mention.start,
            mention.end,
            "mention",
        )
        unknown = tuple(
            item for item in mention.accepted_handles if item not in handle_map
        )
        if unknown:
            raise TurnMeaningError("unknown_accepted_handle")

    writable_claim_indices: list[int] = []
    identity_claim_indices: list[int] = []
    used_mention_indices: set[int] = set()
    for claim_index, claim in enumerate(proposal.claims):
        unknown_object_handles = tuple(
            item
            for item in claim.accepted_object_handles
            if item not in object_handle_map
        )
        if unknown_object_handles:
            raise TurnMeaningError("unknown_accepted_object_handle")
        if len(claim.accepted_object_handles) > 1:
            raise TurnMeaningError("accepted_object_handle_ambiguous")
        if claim.object_reference is not None:
            if (
                claim.kind not in {"evaluation", "event"}
                or claim.subject_mention_index is not None
            ):
                raise TurnMeaningError("accepted_object_handle_kind_invalid")
            if claim.evaluation_target_claim_index is not None:
                raise TurnMeaningError("evaluation_target_sources_conflict")
            supported_objects = tuple(
                item
                for item in accepted_object_handles
                if item.target.kind in {"relationship", "event"}
            )
            if not claim.accepted_object_handles:
                return ProductTurnPlan(
                    "clarification_required",
                    (
                        "object_referent_unresolved"
                        if not supported_objects
                        else "object_referent_ambiguous"
                    ),
                    proposal,
                )
            selected_catalog_object = object_handle_map[
                claim.accepted_object_handles[0]
            ]
            same_kind_objects = tuple(
                item
                for item in supported_objects
                if item.target.kind == selected_catalog_object.target.kind
            )
            if len(same_kind_objects) != 1:
                return ProductTurnPlan(
                    "clarification_required",
                    (
                        "object_referent_unresolved"
                        if not same_kind_objects
                        else "object_referent_ambiguous"
                    ),
                    proposal,
                )
            if same_kind_objects[0].handle != claim.accepted_object_handles[0]:
                raise TurnMeaningError("accepted_object_handle_not_unique_candidate")
            selected_target = same_kind_objects[0].target
            if claim.kind == "event" and selected_target.kind != "event":
                raise TurnMeaningError("accepted_object_handle_kind_invalid")
            if selected_target.kind == "relationship":
                if selected_target.id not in current_relationship_ids(
                    base_graph,
                    accepted_evolution_steps,
                ):
                    raise TurnMeaningError("object_handle_not_current")
            elif (
                selected_target.kind != "event"
                or selected_target.id not in base_graph.events
            ):
                raise TurnMeaningError("object_handle_not_current")
        unknown_cognition_handles = tuple(
            item
            for item in claim.prior_cognition_handles
            if item not in cognition_handle_map
        )
        if unknown_cognition_handles:
            raise TurnMeaningError("unknown_prior_cognition_handle")
        if claim.disposition == "correction":
            # Natural correction has already had a semantic, current-World
            # classification pass before Turn Meaning.  A residual correction
            # label must fail the whole bundle, not be ignored while sibling
            # claims partially mutate the World.
            raise TurnMeaningError("residual_correction_requires_correction_boundary")
        if len(claim.prior_cognition_handles) > 1:
            raise TurnMeaningError("prior_cognition_handle_ambiguous")
        if claim.prior_cognition_handles and claim.kind not in {
            "attribute",
            "evaluation",
            "relationship",
            "event",
        }:
            raise TurnMeaningError("prior_cognition_handle_kind_invalid")
        is_direct_assertion = (
            claim.disposition == "assert"
            and claim.polarity == "affirm"
            and claim.epistemic_status == "stated"
            and not (claim.kind == "event" and claim.object_reference is not None)
        )
        is_cognition_evidence_change = (
            claim.kind in {"attribute", "evaluation", "relationship", "event"}
            and len(claim.prior_cognition_handles) == 1
            and claim.disposition == "assert"
            and claim.epistemic_status == "stated"
            and claim.polarity in {"affirm", "negate"}
        )
        if claim.prior_cognition_handles and not is_cognition_evidence_change:
            raise TurnMeaningError("prior_cognition_change_not_writable")
        if (
            claim.kind in {"attribute", "evaluation"}
            and claim.disposition == "assert"
            and claim.epistemic_status == "stated"
            and claim.polarity == "negate"
            and not claim.prior_cognition_handles
        ):
            raise TurnMeaningError(f"{claim.kind}_contradiction_requires_current_prior")
        if claim.evaluation_target_claim_index is not None:
            target_index = claim.evaluation_target_claim_index
            if target_index >= claim_index:
                raise TurnMeaningError("evaluation_target_claim_must_precede")
            target_claim = proposal.claims[target_index]
            if target_claim.kind not in {"attribute", "relationship", "event"}:
                raise TurnMeaningError("evaluation_target_claim_kind_invalid")
            if target_claim.subject_mention_index != claim.subject_mention_index:
                raise TurnMeaningError("evaluation_target_subject_mismatch")
            if (is_direct_assertion or is_cognition_evidence_change) and not (
                target_claim.disposition == "assert"
                and target_claim.polarity == "affirm"
                and target_claim.epistemic_status == "stated"
            ):
                raise TurnMeaningError("evaluation_target_claim_not_writable")
            if target_claim.subject_mention_index is None:
                raise TurnMeaningError("evaluation_target_subject_missing")
            target_endpoint_indices = {
                target_claim.subject_mention_index,
                *target_claim.related_mention_indices,
            }
            for mention_index, mention in enumerate(proposal.mentions):
                if not _spans_overlap(
                    claim.start,
                    claim.end,
                    mention.start,
                    mention.end,
                ):
                    continue
                if not (claim.start <= mention.start < mention.end <= claim.end):
                    raise TurnMeaningError("evaluation_claim_splits_mention")
                if mention_index not in target_endpoint_indices:
                    # A deictic evaluation such as “这段关系很可靠” may contain
                    # no Entity mention and therefore relies on the same-turn
                    # program-owned dependency.  Once the text explicitly
                    # names an Entity, however, it must be one of that target's
                    # endpoints; otherwise a model-supplied index could attach
                    # “王强很可靠” to an earlier claim about 李华.
                    raise TurnMeaningError(
                        "evaluation_target_explicit_mention_mismatch"
                    )
            allowed_surface_occurrences = tuple(
                occurrence
                for endpoint_index in target_endpoint_indices
                for occurrence in _surface_occurrences(
                    current_user_turn.content,
                    proposal.mentions[endpoint_index].text,
                    claim.start,
                    claim.end,
                )
            )
            surface_indices: dict[str, set[int]] = {}
            for mention_index, mention in enumerate(proposal.mentions):
                surface_indices.setdefault(mention.text, set()).add(mention_index)
            for surface, mention_indices in surface_indices.items():
                if mention_indices <= target_endpoint_indices:
                    continue
                for surface_start, surface_end in _surface_occurrences(
                    current_user_turn.content,
                    surface,
                    claim.start,
                    claim.end,
                ):
                    # Ignore a shorter foreign surface only when it is wholly
                    # lexical material inside a longer, explicitly allowed
                    # endpoint surface (for example an Entity named “王” and
                    # the actual target “王强”).  Equal surfaces mapped to both
                    # an allowed and a foreign mention remain ambiguous and
                    # fail closed.
                    if any(
                        allowed_start <= surface_start
                        and surface_end <= allowed_end
                        and allowed_end - allowed_start > surface_end - surface_start
                        for allowed_start, allowed_end in allowed_surface_occurrences
                    ):
                        continue
                    raise TurnMeaningError(
                        "evaluation_target_explicit_mention_mismatch"
                    )
        validation = _validate_product_claim_roles(
            claim,
            proposal.mentions,
            current_user_turn.content,
            handle_map,
        )
        if validation is not None:
            return ProductTurnPlan("out_of_scope", validation, proposal)
        if (is_direct_assertion or is_cognition_evidence_change) and claim.kind in {
            "attribute",
            "evaluation",
            "relationship",
            "event",
        }:
            writable_claim_indices.append(claim_index)
            if claim.subject_mention_index is not None:
                used_mention_indices.add(claim.subject_mention_index)
            used_mention_indices.update(claim.related_mention_indices)
        elif is_direct_assertion and claim.kind == "naming":
            assert claim.subject_mention_index is not None
            identity_claim_indices.append(claim_index)
            used_mention_indices.add(claim.subject_mention_index)

    # A denied, hypothetical, reported, uncertain, correction-shaped, or
    # ignored claim cannot smuggle a fresh Entity into the World merely because
    # the model also labelled its mention as an introduction.
    if not writable_claim_indices and not identity_claim_indices:
        return ProductTurnPlan("no_candidate", "no_eligible_world_change", proposal)

    working_graph = MemoryWorldGraph(
        world=base_graph.world,
        entities=base_graph.entities.copy(),
        relationships=base_graph.relationships.copy(),
        events=base_graph.events.copy(),
        cognitions=base_graph.cognitions.copy(),
    )
    resolved_mentions: dict[int, _ResolvedProductMention] = {}
    for mention_index in sorted(used_mention_indices):
        resolved = _resolve_product_mention(
            mention_index=mention_index,
            mention=proposal.mentions[mention_index],
            current_user_turn=current_user_turn,
            world_id=world_id,
            base_graph=working_graph,
            handles=handles,
            identity_view=identity_view,
            operation_key=operation_key,
        )
        if isinstance(resolved, _MentionResolutionFailure):
            return ProductTurnPlan(
                resolved.state,
                resolved.code,
                proposal,
                candidate_entity_names=resolved.candidate_entity_names,
            )
        resolved_mentions[mention_index] = resolved
        if resolved.new_entity is not None:
            working_graph.add_entity(resolved.new_entity)

    new_entities = tuple(
        item.new_entity
        for item in resolved_mentions.values()
        if item.new_entity is not None
    )
    identity_bindings = tuple(
        item.identity_binding for item in resolved_mentions.values()
    )
    first_claim_index = (
        writable_claim_indices[0]
        if writable_claim_indices
        else identity_claim_indices[0]
    )
    first_claim = proposal.claims[first_claim_index]
    if first_claim.subject_mention_index is None:
        first_selected_object = object_handle_map[
            first_claim.accepted_object_handles[0]
        ]
        if first_selected_object.target.kind == "relationship":
            relationship = base_graph.relationships.get(first_selected_object.target.id)
            if relationship is None:
                raise TurnMeaningError("accepted_object_target_missing")
            focal_id = relationship.source_entity_id
        elif first_selected_object.target.kind == "event":
            event = base_graph.events.get(first_selected_object.target.id)
            if event is None or not event.participants:
                raise TurnMeaningError("accepted_object_target_missing")
            focal_id = event.participants[0].entity_id
        else:
            raise TurnMeaningError("accepted_object_handle_kind_invalid")
    else:
        focal_id = resolved_mentions[first_claim.subject_mention_index].entity_id

    source_hash = sha256(current_user_turn.content.encode("utf-8")).hexdigest()
    confidence = compute_confidence(ConfidenceInputs("fact", "stated", 1, 0))
    new_relationships: list[Relationship] = []
    new_events: list[WorldEvent] = []
    new_cognitions: list[WorldCognition] = []
    cognition_updates: list[WorldCognition] = []
    formation_traces: list[FormationTrace] = []
    claim_resolutions: list[ProductClaimResolution] = []
    compiled_claim_resolutions: dict[int, ProductClaimResolution] = {}
    evolution_steps: list[EvolutionStep] = []
    replacement_event_target_reused = False

    def trace_for(
        cognition_id: str,
        content: str,
        start: int,
        end: int,
        kind: str,
    ) -> FormationTrace:
        return FormationTrace(
            cognition_id,
            False,
            (
                FormationSourceTrace(
                    current_user_turn.turn_id,
                    "support",
                    "user_stated",
                    "elaborate",
                    ClaimSpan(
                        start,
                        end,
                        source_hash,
                        sha256(content.encode("utf-8")).hexdigest(),
                    ),
                    None,
                    None,
                    "exact_user_claim",
                    f"product.{kind}.exact_user_claim",
                ),
            ),
            "stated",
            1,
            1,
            0,
        )

    for claim_index in writable_claim_indices:
        claim = proposal.claims[claim_index]
        subject = (
            None
            if claim.subject_mention_index is None
            else resolved_mentions[claim.subject_mention_index]
        )
        related = tuple(
            resolved_mentions[index] for index in claim.related_mention_indices
        )
        selected_object = (
            None
            if not claim.accepted_object_handles
            else object_handle_map[claim.accepted_object_handles[0]]
        )
        seed_subject = (
            subject.entity_id
            if subject is not None
            else selected_object.target.id
            if selected_object is not None
            else ""
        )
        claim_seed = _program_claim_seed(claim, seed_subject, related)
        if (
            claim.kind == "event"
            and selected_object is not None
            and claim.prior_cognition_handles
        ):
            if selected_object.target.kind != "event":
                raise TurnMeaningError("event_statement_target_mismatch")
            event = base_graph.events.get(selected_object.target.id)
            if event is None:
                raise TurnMeaningError("accepted_object_target_missing")
            event_target = MemoryTarget("event", event.id)
            event_perspective = Perspective("entity", (owner_entity_id,))
            selected = cognition_handle_map[claim.prior_cognition_handles[0]]
            prior = selected.cognition
            if (
                prior.world_id != world_id
                or prior.target != event_target
                or prior.perspective != event_perspective
            ):
                raise TurnMeaningError("prior_cognition_target_mismatch")
            prior_structured = prior.structured_claim
            if (
                prior.content_type != "fact"
                or prior.formed_by != "stated"
                or prior_structured is None
                or prior_structured.statement_kind != "event_statement"
                or not prior_structured.predicate
                or prior_structured.value is not None
                or prior_structured.polarity != "assert"
                or prior_structured.epistemic_status != "asserted"
            ):
                raise TurnMeaningError("prior_cognition_proposition_mismatch")
            matching_predicate_facets = tuple(
                facet
                for facet in event.facets
                if facet.key == "predicate"
                and facet.value == prior_structured.predicate
                and facet.about_entity_id is None
            )
            if len(matching_predicate_facets) != 1:
                raise TurnMeaningError("event_statement_formal_predicate_mismatch")
            compatible_priors = tuple(
                item
                for item in accepted_cognition_handles
                if item.cognition.world_id == world_id
                and item.cognition.target == event_target
                and item.cognition.perspective == event_perspective
                and item.cognition.content_type == "fact"
                and item.cognition.formed_by == "stated"
                and item.cognition.structured_claim == prior_structured
            )
            if len(compatible_priors) != 1:
                raise TurnMeaningError("prior_cognition_match_ambiguous")
            if compatible_priors[0].handle != selected.handle:
                raise TurnMeaningError("prior_cognition_proposition_mismatch")
            if any(
                source.evidence_id == current_user_turn.turn_id
                for source in prior.sources
            ):
                raise TurnMeaningError("prior_cognition_evidence_already_linked")
            event_source_relation: EvidenceRelation = (
                "contradict" if claim.polarity == "negate" else "support"
            )
            updated_sources = prior.sources + (
                EvidenceLink(current_user_turn.turn_id, event_source_relation),
            )
            support_count = sum(
                source.relation == "support" for source in updated_sources
            )
            contradict_count = sum(
                source.relation == "contradict" for source in updated_sources
            )
            updated_confidence = compute_confidence(
                ConfidenceInputs(
                    prior.content_type,
                    prior.formed_by,
                    support_count,
                    contradict_count,
                )
            )
            cognition_updates.append(
                replace(
                    prior,
                    confidence=updated_confidence,
                    cred_status=derive_cred_status(
                        updated_confidence,
                        contradict_count,
                        prior.content_type,
                        support_count=support_count,
                    ),
                    sources=updated_sources,
                )
            )
            evolution_relation = (
                "contradicts" if event_source_relation == "contradict" else "reaffirms"
            )
            evolution_steps.append(
                EvolutionStep(
                    "evolution:"
                    + str(
                        uuid5(
                            NAMESPACE_URL,
                            "\0".join(
                                (
                                    "memoweft-product-cognition-evidence-change-v1",
                                    world_id,
                                    operation_key,
                                    prior.id,
                                    evolution_relation,
                                )
                            ),
                        )
                    ),
                    "cognition_change",
                    cast(Any, evolution_relation),
                    event_target,
                    (prior.id,),
                    (prior.id,),
                    current_user_turn.occurred_at,
                    (current_user_turn.turn_id,),
                )
            )
            resolution = ProductClaimResolution(
                claim_index=claim_index,
                subject_entity_id=None,
                related_entity_ids=(),
                target_kind="event",
                target_id=event.id,
                participant_entity_ids=tuple(
                    item.entity_id for item in event.participants
                ),
                object_entity_ids=event.related_entity_ids,
                owner_participates=any(
                    item.entity_id == owner_entity_id for item in event.participants
                ),
                event_type=event.event_type,
                occurred_at=event.occurred_at,
            )
            claim_resolutions.append(resolution)
            compiled_claim_resolutions[claim_index] = resolution
            continue
        if claim.kind == "relationship":
            assert subject is not None
            assert claim.predicate is not None
            endpoints = _relationship_endpoints(
                claim, subject.entity_id, related, owner_entity_id
            )
            if endpoints is None:
                raise TurnMeaningError("relationship_endpoint_roles_invalid")
            source_entity_id, target_entity_id = endpoints
            if source_entity_id == target_entity_id:
                raise TurnMeaningError("relationship_endpoints_not_distinct")
            relation_type = claim.predicate.text
            bidirectional = claim.relationship_symmetric
            if bidirectional and target_entity_id < source_entity_id:
                source_entity_id, target_entity_id = (
                    target_entity_id,
                    source_entity_id,
                )
            relationship_key = _relationship_parts_key(
                source_entity_id,
                target_entity_id,
                relation_type,
                bidirectional,
            )
            available_relationships = (
                *base_graph.relationships.values(),
                *new_relationships,
            )
            evolution_historical_ids = accepted_historical_relationship_ids(
                accepted_evolution_steps
            )
            accepted_state_relationship_ids = {
                accepted.step.subject.id
                for accepted in accepted_evolution_steps
                if accepted.step.kind == "relationship_state"
                and accepted.step.subject.kind == "relationship"
            }
            matching = tuple(
                item
                for item in available_relationships
                if item.id not in evolution_historical_ids
                if (
                    item.id in accepted_state_relationship_ids
                    or _relationship_is_current_at(
                        item,
                        current_user_turn.occurred_at,
                    )
                )
                and _relationship_identity_key(item) == relationship_key
            )
            if len(matching) > 1:
                return ProductTurnPlan(
                    "clarification_required",
                    "relationship_identity_ambiguous",
                    proposal,
                )
            if matching:
                relationship = matching[0]
            else:
                historical_matching = tuple(
                    item
                    for item in base_graph.relationships.values()
                    if (
                        item.id in evolution_historical_ids
                        or (
                            item.id not in accepted_state_relationship_ids
                            and _relationship_is_ended_at(
                                item,
                                current_user_turn.occurred_at,
                            )
                        )
                    )
                    and _relationship_identity_key(item) == relationship_key
                )
                predecessor: Relationship | None = None
                if historical_matching:
                    predecessor_ids = {
                        predecessor_id
                        for accepted in accepted_evolution_steps
                        if accepted.step.kind == "relationship_successor"
                        for predecessor_id in accepted.step.predecessor_ids
                    }
                    terminal = tuple(
                        item
                        for item in historical_matching
                        if item.id not in predecessor_ids
                    )
                    if len(terminal) == 1:
                        predecessor = terminal[0]
                    else:
                        return ProductTurnPlan(
                            "clarification_required",
                            "relationship_predecessor_ambiguous",
                            proposal,
                        )
                relationship = Relationship(
                    "relationship:"
                    + str(
                        uuid5(
                            NAMESPACE_URL,
                            "\0".join(
                                (
                                    "memoweft-product-relationship-v3",
                                    world_id,
                                    operation_key,
                                    claim_seed,
                                    source_entity_id,
                                    target_entity_id,
                                    _normalize_relationship_type(relation_type),
                                    "bidirectional" if bidirectional else "directed",
                                )
                            ),
                        )
                    ),
                    world_id,
                    source_entity_id,
                    target_entity_id,
                    relation_type,
                    bidirectional,
                    valid_from=(
                        current_user_turn.occurred_at
                        if predecessor is not None
                        else None
                    ),
                )
                new_relationships.append(relationship)
                if predecessor is not None:
                    evolution_steps.append(
                        EvolutionStep(
                            "evolution:"
                            + str(
                                uuid5(
                                    NAMESPACE_URL,
                                    "\0".join(
                                        (
                                            "memoweft-product-relationship-reestablished-v1",
                                            world_id,
                                            operation_key,
                                            predecessor.id,
                                            relationship.id,
                                        )
                                    ),
                                )
                            ),
                            "relationship_successor",
                            "reestablished",
                            MemoryTarget("relationship", relationship.id),
                            (predecessor.id,),
                            (relationship.id,),
                            current_user_turn.occurred_at,
                            (current_user_turn.turn_id,),
                        )
                    )
            cognition_id = "cog:" + str(
                uuid5(
                    NAMESPACE_URL,
                    "\0".join(
                        (
                            "memoweft-product-relationship-cognition-v3",
                            world_id,
                            operation_key,
                            claim_seed,
                            relationship.id,
                        )
                    ),
                )
            )
            relationship_structured = StructuredClaim(
                "relationship_statement",
                predicate=relation_type,
                polarity="assert",
                epistemic_status="asserted",
            )
            relationship_target = MemoryTarget("relationship", relationship.id)
            relationship_perspective = Perspective(
                "entity",
                (owner_entity_id,),
            )
            compatible_priors = tuple(
                item
                for item in accepted_cognition_handles
                if item.cognition.world_id == world_id
                and item.cognition.target == relationship_target
                and item.cognition.perspective == relationship_perspective
                and item.cognition.content_type == "fact"
                and item.cognition.formed_by == "stated"
                and item.cognition.structured_claim == relationship_structured
            )
            if claim.prior_cognition_handles:
                selected = cognition_handle_map[claim.prior_cognition_handles[0]]
                prior = selected.cognition
                if (
                    prior.world_id != world_id
                    or prior.target != relationship_target
                    or prior.perspective != relationship_perspective
                ):
                    raise TurnMeaningError("prior_cognition_target_mismatch")
                if (
                    prior.content_type != "fact"
                    or prior.formed_by != "stated"
                    or prior.structured_claim != relationship_structured
                ):
                    raise TurnMeaningError("prior_cognition_proposition_mismatch")
                if len(compatible_priors) != 1:
                    raise TurnMeaningError("prior_cognition_match_ambiguous")
                if compatible_priors[0].handle != selected.handle:
                    raise TurnMeaningError("prior_cognition_proposition_mismatch")
                if any(
                    source.evidence_id == current_user_turn.turn_id
                    for source in prior.sources
                ):
                    raise TurnMeaningError("prior_cognition_evidence_already_linked")
                relationship_source_relation: EvidenceRelation = (
                    "contradict" if claim.polarity == "negate" else "support"
                )
                updated_sources = prior.sources + (
                    EvidenceLink(
                        current_user_turn.turn_id,
                        relationship_source_relation,
                    ),
                )
                support_count = sum(
                    source.relation == "support" for source in updated_sources
                )
                contradict_count = sum(
                    source.relation == "contradict" for source in updated_sources
                )
                updated_confidence = compute_confidence(
                    ConfidenceInputs(
                        prior.content_type,
                        prior.formed_by,
                        support_count,
                        contradict_count,
                    )
                )
                updated = replace(
                    prior,
                    confidence=updated_confidence,
                    cred_status=derive_cred_status(
                        updated_confidence,
                        contradict_count,
                        prior.content_type,
                        support_count=support_count,
                    ),
                    sources=updated_sources,
                )
                cognition_updates.append(updated)
                cognition_id = prior.id
                evolution_relation = (
                    "contradicts"
                    if relationship_source_relation == "contradict"
                    else "reaffirms"
                )
                evolution_steps.append(
                    EvolutionStep(
                        "evolution:"
                        + str(
                            uuid5(
                                NAMESPACE_URL,
                                "\0".join(
                                    (
                                        "memoweft-product-cognition-evidence-change-v1",
                                        world_id,
                                        operation_key,
                                        prior.id,
                                        evolution_relation,
                                    )
                                ),
                            )
                        ),
                        "cognition_change",
                        cast(Any, evolution_relation),
                        prior.target,
                        (prior.id,),
                        (prior.id,),
                        current_user_turn.occurred_at,
                        (current_user_turn.turn_id,),
                    )
                )
            else:
                new_cognitions.append(
                    WorldCognition(
                        cognition_id,
                        world_id,
                        relationship_target,
                        claim.text,
                        "fact",
                        "stated",
                        confidence,
                        derive_cred_status(
                            confidence,
                            0,
                            "fact",
                            support_count=1,
                        ),
                        relationship_perspective,
                        (EvidenceLink(current_user_turn.turn_id, "support"),),
                        structured_claim=relationship_structured,
                    )
                )
                formation_traces.append(
                    trace_for(
                        cognition_id,
                        claim.text,
                        claim.start,
                        claim.end,
                        "relationship",
                    )
                )
            resolution = ProductClaimResolution(
                claim_index,
                subject.entity_id,
                tuple(item.entity_id for item in related),
                "relationship",
                relationship.id,
                relationship.source_entity_id,
                relationship.target_entity_id,
                relationship.relation_type,
                relationship.bidirectional,
            )
            claim_resolutions.append(resolution)
            compiled_claim_resolutions[claim_index] = resolution
            continue

        if claim.kind == "event":
            assert subject is not None
            assert claim.predicate is not None
            occurred_at = _product_timestamp(claim.normalized_occurred_at)
            if occurred_at is None:
                raise TurnMeaningError("event_normalized_occurred_at_invalid")
            occurred = occurred_at.isoformat()
            event_id = "event:" + str(
                uuid5(
                    NAMESPACE_URL,
                    "\0".join(
                        (
                            "memoweft-product-event-v3",
                            world_id,
                            operation_key,
                            claim_seed,
                        )
                    ),
                )
            )
            participant_pairs: list[tuple[str, str]] = []
            if claim.event_owner_participates:
                participant_pairs.append((owner_entity_id, "owner"))
            related_entity_ids: list[str] = []
            if claim.event_subject_role == "participant":
                participant_pairs.append((subject.entity_id, "focus"))
            elif claim.event_subject_role == "related_entity":
                related_entity_ids.append(subject.entity_id)
            else:
                raise TurnMeaningError("event_subject_role_missing")
            for resolved, role in zip(
                related,
                claim.event_related_roles,
                strict=True,
            ):
                if role == "participant":
                    participant_pairs.append((resolved.entity_id, "participant"))
                else:
                    related_entity_ids.append(resolved.entity_id)
            if not participant_pairs:
                raise TurnMeaningError("event_participant_missing")
            all_event_entity_ids = [
                *[entity_id for entity_id, _ in participant_pairs],
                *related_entity_ids,
            ]
            if len(set(all_event_entity_ids)) != len(all_event_entity_ids):
                raise TurnMeaningError("event_entities_not_distinct")
            candidate_event = WorldEvent(
                event_id,
                world_id,
                "occurrence",
                claim.text,
                occurred,
                tuple(
                    EventParticipant(entity_id, role)
                    for entity_id, role in participant_pairs
                ),
                tuple(related_entity_ids),
                facets=(EventFacet("predicate", claim.predicate.text),),
                evidence_ids=(current_user_turn.turn_id,),
            )
            if replacement_correction_event_target is None:
                event = candidate_event
                new_events.append(event)
            else:
                event = replacement_correction_event_target
                if (
                    candidate_event.world_id != event.world_id
                    or candidate_event.event_type != event.event_type
                    or candidate_event.summary != event.summary
                    or candidate_event.occurred_at != event.occurred_at
                    or candidate_event.participants != event.participants
                    or candidate_event.related_entity_ids != event.related_entity_ids
                    or candidate_event.relationship_ids != event.relationship_ids
                    or candidate_event.facets != event.facets
                ):
                    raise TurnMeaningError(
                        "structured_correction_event_target_mismatch"
                    )
                if replacement_event_target_reused:
                    raise TurnMeaningError(
                        "structured_correction_event_target_ambiguous"
                    )
                replacement_event_target_reused = True
            cognition_id = "cog:" + str(
                uuid5(
                    NAMESPACE_URL,
                    "\0".join(
                        (
                            "memoweft-product-event-cognition-v3",
                            world_id,
                            operation_key,
                            claim_seed,
                            event.id,
                        )
                    ),
                )
            )
            event_structured = StructuredClaim(
                "event_statement",
                predicate=claim.predicate.text,
                polarity="assert",
                epistemic_status="asserted",
            )
            new_cognitions.append(
                WorldCognition(
                    cognition_id,
                    world_id,
                    MemoryTarget("event", event.id),
                    claim.text,
                    "fact",
                    "stated",
                    confidence,
                    derive_cred_status(
                        confidence,
                        0,
                        "fact",
                        support_count=1,
                    ),
                    Perspective("entity", (owner_entity_id,)),
                    (EvidenceLink(current_user_turn.turn_id, "support"),),
                    structured_claim=event_structured,
                )
            )
            formation_traces.append(
                trace_for(cognition_id, claim.text, claim.start, claim.end, "event")
            )
            resolution = ProductClaimResolution(
                claim_index=claim_index,
                subject_entity_id=subject.entity_id,
                related_entity_ids=tuple(item.entity_id for item in related),
                target_kind="event",
                target_id=event.id,
                participant_entity_ids=tuple(
                    item.entity_id for item in event.participants
                ),
                object_entity_ids=event.related_entity_ids,
                owner_participates=claim.event_owner_participates,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
            )
            claim_resolutions.append(resolution)
            compiled_claim_resolutions[claim_index] = resolution
            continue

        content, start, end = (
            _attribute_claim_hull(
                current_user_turn.content,
                proposal.mentions[cast(int, claim.subject_mention_index)],
                MeaningStatement(
                    "attribute",
                    claim.text,
                    claim.start,
                    claim.end,
                    claim.value,
                ),
            )
            if claim.kind == "attribute" and claim.value is not None
            else (claim.text, claim.start, claim.end)
        )
        cognition_target = (
            selected_object.target
            if selected_object is not None
            else MemoryTarget(
                "entity", cast(_ResolvedProductMention, subject).entity_id
            )
        )
        dependency_resolution: ProductClaimResolution | None = None
        if (
            claim.kind == "evaluation"
            and claim.evaluation_target_claim_index is not None
        ):
            dependency_resolution = compiled_claim_resolutions.get(
                claim.evaluation_target_claim_index
            )
            if dependency_resolution is None:
                raise TurnMeaningError("evaluation_target_claim_not_compiled")
            cognition_target = MemoryTarget(
                dependency_resolution.target_kind,
                dependency_resolution.target_id,
            )
        elif selected_object is not None:
            if selected_object.target.kind == "relationship":
                relationship = base_graph.relationships.get(selected_object.target.id)
                if relationship is None:
                    raise TurnMeaningError("accepted_object_target_missing")
                dependency_resolution = ProductClaimResolution(
                    claim_index=claim_index,
                    subject_entity_id=None,
                    related_entity_ids=(),
                    target_kind="relationship",
                    target_id=relationship.id,
                    source_entity_id=relationship.source_entity_id,
                    target_entity_id=relationship.target_entity_id,
                    relation_type=relationship.relation_type,
                    bidirectional=relationship.bidirectional,
                )
            elif selected_object.target.kind == "event":
                event = base_graph.events.get(selected_object.target.id)
                if event is None:
                    raise TurnMeaningError("accepted_object_target_missing")
                dependency_resolution = ProductClaimResolution(
                    claim_index=claim_index,
                    subject_entity_id=None,
                    related_entity_ids=(),
                    target_kind="event",
                    target_id=event.id,
                    participant_entity_ids=tuple(
                        item.entity_id for item in event.participants
                    ),
                    object_entity_ids=event.related_entity_ids,
                    owner_participates=any(
                        item.entity_id == owner_entity_id for item in event.participants
                    ),
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                )
            else:
                return ProductTurnPlan(
                    "out_of_scope",
                    "object_target_kind_not_supported",
                    proposal,
                )
        cognition_id = "cog:" + str(
            uuid5(
                NAMESPACE_URL,
                "\0".join(
                    (
                        "memoweft-product-entity-cognition-v3",
                        world_id,
                        operation_key,
                        claim_seed,
                        cognition_target.kind,
                        cognition_target.id,
                    )
                ),
            )
        )
        structured: StructuredClaim | None = None
        if claim.kind == "attribute":
            structured = StructuredClaim(
                "attribute",
                predicate=(None if claim.predicate is None else claim.predicate.text),
                value=None if claim.value is None else claim.value.text,
                polarity="assert",
                epistemic_status="asserted",
            )
        elif claim.kind == "evaluation":
            structured = StructuredClaim(
                "evaluation",
                value=None if claim.value is None else claim.value.text,
                polarity="assert",
                epistemic_status="asserted",
            )
        perspective = Perspective("entity", (owner_entity_id,))
        compatible_priors = tuple(
            item
            for item in accepted_cognition_handles
            if item.cognition.world_id == world_id
            and item.cognition.target == cognition_target
            and item.cognition.perspective == perspective
            and item.cognition.content_type == "fact"
            and item.cognition.formed_by == "stated"
            and item.cognition.structured_claim == structured
        )
        if claim.prior_cognition_handles:
            selected = cognition_handle_map[claim.prior_cognition_handles[0]]
            prior = selected.cognition
            if (
                prior.world_id != world_id
                or prior.target != cognition_target
                or prior.perspective != perspective
            ):
                raise TurnMeaningError("prior_cognition_target_mismatch")
            if (
                prior.content_type != "fact"
                or prior.formed_by != "stated"
                or prior.structured_claim != structured
            ):
                raise TurnMeaningError("prior_cognition_proposition_mismatch")
            if len(compatible_priors) != 1:
                # The model may select an opaque handle, but it cannot hide a
                # second equally valid current cognition from program-owned
                # ambiguity handling.  Updating either one would make model
                # ordering a write authority.
                raise TurnMeaningError("prior_cognition_match_ambiguous")
            if compatible_priors[0].handle != selected.handle:
                raise TurnMeaningError("prior_cognition_proposition_mismatch")
            if any(
                source.evidence_id == current_user_turn.turn_id
                for source in prior.sources
            ):
                raise TurnMeaningError("prior_cognition_evidence_already_linked")
            source_relation: EvidenceRelation = (
                "contradict" if claim.polarity == "negate" else "support"
            )
            updated_sources = prior.sources + (
                EvidenceLink(current_user_turn.turn_id, source_relation),
            )
            support_count = sum(
                source.relation == "support" for source in updated_sources
            )
            contradict_count = sum(
                source.relation == "contradict" for source in updated_sources
            )
            updated_confidence = compute_confidence(
                ConfidenceInputs(
                    prior.content_type,
                    prior.formed_by,
                    support_count,
                    contradict_count,
                )
            )
            updated = replace(
                prior,
                confidence=updated_confidence,
                cred_status=derive_cred_status(
                    updated_confidence,
                    contradict_count,
                    prior.content_type,
                    support_count=support_count,
                ),
                sources=updated_sources,
            )
            cognition_updates.append(updated)
            cognition_id = prior.id
            evolution_relation = (
                "contradicts" if source_relation == "contradict" else "reaffirms"
            )
            evolution_steps.append(
                EvolutionStep(
                    "evolution:"
                    + str(
                        uuid5(
                            NAMESPACE_URL,
                            "\0".join(
                                (
                                    "memoweft-product-cognition-evidence-change-v1",
                                    world_id,
                                    operation_key,
                                    prior.id,
                                    evolution_relation,
                                )
                            ),
                        )
                    ),
                    "cognition_change",
                    cast(Any, evolution_relation),
                    prior.target,
                    (prior.id,),
                    (prior.id,),
                    current_user_turn.occurred_at,
                    (current_user_turn.turn_id,),
                )
            )
        else:
            if claim.kind in {"attribute", "evaluation"} and compatible_priors:
                # Repeating an accepted proposition is reaffirming Evidence,
                # not permission to fragment the World into a second current
                # cognition merely because the model omitted its opaque handle.
                raise TurnMeaningError(f"{claim.kind}_current_prior_selection_required")
            new_cognitions.append(
                WorldCognition(
                    cognition_id,
                    world_id,
                    cognition_target,
                    content,
                    "fact",
                    "stated",
                    confidence,
                    derive_cred_status(confidence, 0, "fact", support_count=1),
                    perspective,
                    (EvidenceLink(current_user_turn.turn_id, "support"),),
                    structured_claim=structured,
                )
            )
            formation_traces.append(
                trace_for(cognition_id, content, start, end, claim.kind)
            )
        resolution = ProductClaimResolution(
            claim_index=claim_index,
            subject_entity_id=(None if subject is None else subject.entity_id),
            related_entity_ids=(),
            target_kind=cast(
                Literal["entity", "relationship", "event"],
                cognition_target.kind,
            ),
            target_id=cognition_target.id,
            source_entity_id=(
                None
                if dependency_resolution is None
                else dependency_resolution.source_entity_id
            ),
            target_entity_id=(
                None
                if dependency_resolution is None
                else dependency_resolution.target_entity_id
            ),
            relation_type=(
                None
                if dependency_resolution is None
                else dependency_resolution.relation_type
            ),
            bidirectional=(
                None
                if dependency_resolution is None
                else dependency_resolution.bidirectional
            ),
            participant_entity_ids=(
                ()
                if dependency_resolution is None
                else dependency_resolution.participant_entity_ids
            ),
            object_entity_ids=(
                ()
                if dependency_resolution is None
                else dependency_resolution.object_entity_ids
            ),
            owner_participates=(
                None
                if dependency_resolution is None
                else dependency_resolution.owner_participates
            ),
            event_type=(
                None
                if dependency_resolution is None
                else dependency_resolution.event_type
            ),
            occurred_at=(
                None
                if dependency_resolution is None
                else dependency_resolution.occurred_at
            ),
        )
        claim_resolutions.append(resolution)
        compiled_claim_resolutions[claim_index] = resolution

    if (
        replacement_correction_event_target is not None
        and not replacement_event_target_reused
    ):
        raise TurnMeaningError("structured_correction_event_target_missing")
    if not (
        new_entities
        or new_relationships
        or new_events
        or new_cognitions
        or cognition_updates
    ):
        return ProductTurnPlan(
            "no_candidate",
            "reference_without_world_change",
            proposal,
        )
    delta = WorldDelta(
        world_id,
        (current_user_turn.turn_id,),
        new_entities,
        tuple(new_relationships),
        tuple(new_events),
        tuple(new_cognitions),
        formation_traces=tuple(formation_traces),
    )
    bundle = ProductClaimBundle(
        focal_id,
        current_user_turn.turn_id,
        owner_entity_id,
        proposal.claims,
        tuple(
            ProductMentionResolution(index, item.entity_id)
            for index, item in sorted(resolved_mentions.items())
        ),
        tuple(claim_resolutions),
    )
    names = tuple(
        dict.fromkeys(item.entity_name for item in resolved_mentions.values())
    )
    return ProductTurnPlan(
        "candidate",
        (
            "relationship_change"
            if any(
                proposal.claims[index].kind == "relationship"
                for index in writable_claim_indices
            )
            else "product_claim_bundle"
        ),
        proposal,
        delta,
        identity_bindings,
        focal_id,
        names,
        bundle,
        tuple(evolution_steps),
        tuple(cognition_updates),
    )


def _validate_product_claim_roles(
    claim: MeaningClaim,
    mentions: tuple[MeaningMention, ...],
    content: str,
    handle_map: Mapping[str, AcceptedEntityHandle],
) -> str | None:
    """Verify claim-local spans and endpoint roles before resolving identity."""

    _verify_span(content, claim.text, claim.start, claim.end, "claim")
    subject = (
        None
        if claim.subject_mention_index is None
        else mentions[claim.subject_mention_index]
    )
    if claim.object_reference is not None:
        _verify_span(
            content,
            claim.object_reference.text,
            claim.object_reference.start,
            claim.object_reference.end,
            "object_reference",
        )
        if not (
            claim.start
            <= claim.object_reference.start
            < claim.object_reference.end
            <= claim.end
        ):
            raise TurnMeaningError("evaluation_claim_does_not_contain_object_reference")
    if claim.value is not None:
        _verify_span(
            content, claim.value.text, claim.value.start, claim.value.end, "value"
        )
        if claim.kind == "attribute":
            if not (claim.start <= claim.value.start < claim.value.end <= claim.end):
                raise TurnMeaningError("attribute_statement_does_not_contain_value")
            assert subject is not None
            if max(subject.start, claim.value.start) < min(
                subject.end, claim.value.end
            ):
                raise TurnMeaningError("attribute_mention_value_overlap")
        elif claim.kind == "evaluation":
            if not (claim.start <= claim.value.start < claim.value.end <= claim.end):
                raise TurnMeaningError("evaluation_claim_does_not_contain_value")
            if claim.evaluation_target_claim_index is None:
                if subject is not None:
                    if not (claim.start <= subject.start < subject.end <= claim.end):
                        raise TurnMeaningError(
                            "evaluation_claim_does_not_contain_subject"
                        )
                    if _spans_overlap(
                        subject.start,
                        subject.end,
                        claim.value.start,
                        claim.value.end,
                    ):
                        raise TurnMeaningError("evaluation_mention_value_overlap")
                elif claim.object_reference is None:
                    raise TurnMeaningError("evaluation_object_reference_missing")
                elif _spans_overlap(
                    claim.object_reference.start,
                    claim.object_reference.end,
                    claim.value.start,
                    claim.value.end,
                ):
                    raise TurnMeaningError("evaluation_object_reference_value_overlap")
    if claim.predicate is not None:
        _verify_span(
            content,
            claim.predicate.text,
            claim.predicate.start,
            claim.predicate.end,
            "predicate",
        )
        if claim.kind == "attribute":
            if not (
                claim.start <= claim.predicate.start < claim.predicate.end <= claim.end
            ):
                raise TurnMeaningError("attribute_statement_does_not_contain_predicate")
            assert subject is not None
            if _spans_overlap(
                subject.start,
                subject.end,
                claim.predicate.start,
                claim.predicate.end,
            ):
                raise TurnMeaningError("attribute_mention_predicate_overlap")
            if claim.value is not None and _spans_overlap(
                claim.value.start,
                claim.value.end,
                claim.predicate.start,
                claim.predicate.end,
            ):
                raise TurnMeaningError("attribute_predicate_value_overlap")
    if claim.occurred_at is not None:
        _verify_span(
            content,
            claim.occurred_at.text,
            claim.occurred_at.start,
            claim.occurred_at.end,
            "occurred_at",
        )

    endpoint_indices = (
        () if claim.subject_mention_index is None else (claim.subject_mention_index,)
    ) + claim.related_mention_indices
    allowed_handles = {
        handle
        for index in endpoint_indices
        for handle in mentions[index].accepted_handles
    }
    for handle in claim.accepted_entity_handles:
        if handle not in handle_map:
            raise TurnMeaningError("unknown_claim_accepted_handle")
        if handle not in allowed_handles:
            raise TurnMeaningError("claim_handle_not_bound_to_endpoint")

    if claim.kind == "relationship":
        assert subject is not None
        predicate = claim.predicate
        if predicate is None:
            raise TurnMeaningError("claim_predicate_missing")
        if not (claim.start <= predicate.start < predicate.end <= claim.end):
            raise TurnMeaningError("relationship_claim_does_not_contain_predicate")
        if not _relationship_predicate_is_safe(predicate.text):
            raise TurnMeaningError("relationship_predicate_not_semantic")
        if not (claim.start <= subject.start < subject.end <= claim.end):
            raise TurnMeaningError("relationship_claim_does_not_contain_subject")
        if max(subject.start, predicate.start) < min(subject.end, predicate.end):
            raise TurnMeaningError("relationship_predicate_endpoint_overlap")
        if len(claim.related_mention_indices) > 1:
            return "relationship_endpoint_count_not_supported"
        if claim.related_mention_indices:
            related_index = claim.related_mention_indices[0]
            if related_index == claim.subject_mention_index:
                raise TurnMeaningError("relationship_endpoint_mentions_not_distinct")
            related = mentions[related_index]
            if not (claim.start <= related.start < related.end <= claim.end):
                raise TurnMeaningError("relationship_claim_does_not_contain_object")
            if max(related.start, predicate.start) < min(related.end, predicate.end):
                raise TurnMeaningError("relationship_predicate_endpoint_overlap")
            if claim.relationship_direction != "subject_to_related":
                raise TurnMeaningError("relationship_direction_role_mismatch")
        elif claim.relationship_direction not in {
            "owner_to_focal",
            "focal_to_owner",
        }:
            raise TurnMeaningError("relationship_direction_role_mismatch")
    elif claim.kind == "event":
        if claim.object_reference is not None:
            if (
                subject is not None
                or claim.related_mention_indices
                or claim.value is not None
                or claim.predicate is not None
                or claim.occurred_at is not None
                or claim.normalized_occurred_at is not None
                or claim.relationship_direction is not None
                or claim.relationship_symmetric
                or claim.event_owner_participates
                or claim.event_subject_role is not None
                or claim.event_related_roles
                or claim.evaluation_target_claim_index is not None
                or len(claim.accepted_object_handles) != 1
            ):
                raise TurnMeaningError("event_statement_reference_shape_invalid")
            return None
        assert subject is not None
        predicate = claim.predicate
        event_time = claim.occurred_at
        if predicate is None:
            raise TurnMeaningError("claim_predicate_missing")
        if event_time is None:
            raise TurnMeaningError("event_occurred_at_missing")
        if not (claim.start <= predicate.start < predicate.end <= claim.end):
            raise TurnMeaningError("event_claim_does_not_contain_predicate")
        if not _relationship_predicate_is_safe(predicate.text):
            raise TurnMeaningError("event_predicate_not_semantic")
        if not (claim.start <= event_time.start < event_time.end <= claim.end):
            raise TurnMeaningError("event_claim_does_not_contain_occurred_at")
        if _spans_overlap(
            predicate.start,
            predicate.end,
            event_time.start,
            event_time.end,
        ):
            raise TurnMeaningError("event_predicate_time_overlap")
        if _product_timestamp(claim.normalized_occurred_at) is None:
            raise TurnMeaningError("event_normalized_occurred_at_invalid")
        has_non_owner_participant = (
            claim.event_subject_role == "participant"
            or "participant" in claim.event_related_roles
        )
        if not claim.event_owner_participates and not has_non_owner_participant:
            return "event_participant_missing"

        assert claim.subject_mention_index is not None
        event_mention_indices = (
            claim.subject_mention_index,
            *claim.related_mention_indices,
        )
        if len(set(event_mention_indices)) != len(event_mention_indices):
            raise TurnMeaningError("event_entity_mentions_not_distinct")
        event_mentions = tuple(mentions[index] for index in event_mention_indices)
        for mention in event_mentions:
            if not (claim.start <= mention.start < mention.end <= claim.end):
                raise TurnMeaningError("event_claim_does_not_contain_entity")
            if _spans_overlap(
                mention.start,
                mention.end,
                predicate.start,
                predicate.end,
            ):
                raise TurnMeaningError("event_predicate_entity_overlap")
            if _spans_overlap(
                mention.start,
                mention.end,
                event_time.start,
                event_time.end,
            ):
                raise TurnMeaningError("event_time_entity_overlap")
        for index, mention in enumerate(event_mentions):
            for other in event_mentions[index + 1 :]:
                if _spans_overlap(
                    mention.start,
                    mention.end,
                    other.start,
                    other.end,
                ):
                    raise TurnMeaningError("event_entity_mentions_overlap")
    elif claim.related_mention_indices:
        return f"statement_{claim.kind}_multiple_entities_not_supported"
    return None


def _resolve_product_mention(
    *,
    mention_index: int,
    mention: MeaningMention,
    current_user_turn: ConversationTurn,
    world_id: str,
    base_graph: MemoryWorldGraph,
    handles: Sequence[AcceptedEntityHandle],
    identity_view: IdentityAuthorityView,
    operation_key: str,
) -> _ResolvedProductMention | _MentionResolutionFailure:
    """Resolve or introduce one exact mention without granting model authority."""

    handle_map = {item.handle: item for item in handles}
    reported = tuple(handle_map[item] for item in mention.accepted_handles)
    resolution = _resolve_reference(
        base_graph,
        current_user_turn,
        mention,
        identity_view,
    )
    entity_id: str
    entity_name: str
    new_entity: Entity | None = None

    if mention.mode == "introduce":
        if mention.accepted_handles:
            raise TurnMeaningError("introduction_selected_accepted_handle")
        if resolution.state == "resolved" and resolution.entity_id is not None:
            entity_id = resolution.entity_id
            entity_name = base_graph.entities[entity_id].canonical_name
        elif resolution.state == "ambiguous" or resolution.candidates:
            names = tuple(
                dict.fromkeys(
                    base_graph.entities[item.entity_id].canonical_name
                    for item in resolution.candidates
                    if item.entity_id in base_graph.entities
                )
            )
            return _MentionResolutionFailure(
                "clarification_required",
                "introduction_identity_ambiguous",
                names,
            )
        elif (
            resolution.uncertainty_code != "NO_SAFE_ENTITY_CANDIDATE"
            or _is_deictic_reference_surface(mention.text)
        ):
            return _MentionResolutionFailure(
                "clarification_required",
                "introduction_reference_unresolved",
            )
        else:
            kind = _normalize_kind(mention.kind_hint)
            if kind is None:
                return _MentionResolutionFailure(
                    "out_of_scope",
                    "introduction_kind_missing",
                )
            entity_name = mention.text.strip()
            if not entity_name:
                raise TurnMeaningError("introduction_name_empty")
            entity_id = "entity:" + str(
                uuid5(
                    NAMESPACE_URL,
                    "\0".join(
                        (
                            "memoweft-product-entity-v2",
                            world_id,
                            operation_key,
                            str(mention.start),
                            str(mention.end),
                            kind,
                        )
                    ),
                )
            )
            new_entity = Entity(entity_id, world_id, kind, entity_name)
    else:
        selected_ids: tuple[str, ...]
        if resolution.state == "resolved" and resolution.entity_id is not None:
            selected_ids = (resolution.entity_id,)
            if reported and any(
                item.entity_id != resolution.entity_id for item in reported
            ):
                return _MentionResolutionFailure(
                    "clarification_required",
                    "accepted_handle_conflicts_with_resolution",
                )
        elif resolution.candidates:
            selected_ids = tuple(item.entity_id for item in resolution.candidates)
        else:
            kind = _normalize_kind(mention.kind_hint)
            selected_ids = tuple(
                item.entity_id
                for item in handles
                if item.recent_in_conversation
                and (kind is None or _normalize_kind(item.kind) == kind)
            )
        selected_ids = tuple(dict.fromkeys(selected_ids))
        if len(selected_ids) != 1:
            names = tuple(
                dict.fromkeys(
                    base_graph.entities[item].canonical_name
                    for item in selected_ids
                    if item in base_graph.entities
                )
            )
            return _MentionResolutionFailure(
                "clarification_required",
                "referent_ambiguous" if selected_ids else "referent_unresolved",
                names,
            )
        entity_id = selected_ids[0]
        entity_name = base_graph.entities[entity_id].canonical_name

    binding = ReviewedIdentityBinding(
        entity_id=entity_id,
        evidence_id=current_user_turn.turn_id,
        conversation_id=current_user_turn.conversation_id,
        occurred_at=current_user_turn.occurred_at,
        start_codepoint=mention.start,
        end_codepoint=mention.end,
        kind_hint=_normalize_kind(mention.kind_hint),
        continuity_scope=current_user_turn.conversation_id,
    )
    return _ResolvedProductMention(
        mention_index,
        entity_id,
        entity_name,
        new_entity,
        binding,
    )


def _relationship_endpoints(
    claim: MeaningClaim,
    subject_entity_id: str,
    related: tuple[_ResolvedProductMention, ...],
    owner_entity_id: str,
) -> tuple[str, str] | None:
    if related:
        if len(related) != 1:
            return None
        if claim.relationship_direction == "subject_to_related":
            return subject_entity_id, related[0].entity_id
        return None
    if claim.relationship_direction == "owner_to_focal":
        return owner_entity_id, subject_entity_id
    if claim.relationship_direction == "focal_to_owner":
        return subject_entity_id, owner_entity_id
    return None


def _program_claim_seed(
    claim: MeaningClaim,
    subject_entity_id: str,
    related: tuple[_ResolvedProductMention, ...],
) -> str:
    """Derive an operation-local semantic slot without using model claim IDs."""

    parts = [
        claim.kind,
        str(claim.start),
        str(claim.end),
        subject_entity_id,
        *[item.entity_id for item in related],
    ]
    if claim.value is not None:
        parts.extend((str(claim.value.start), str(claim.value.end), claim.value.text))
    if claim.predicate is not None:
        parts.extend(
            (
                str(claim.predicate.start),
                str(claim.predicate.end),
                claim.predicate.text,
            )
        )
    if claim.occurred_at is not None:
        parts.extend(
            (
                str(claim.occurred_at.start),
                str(claim.occurred_at.end),
                claim.occurred_at.text,
            )
        )
    if claim.kind == "event":
        parts.extend(
            (
                claim.normalized_occurred_at or "",
                "owner_participates"
                if claim.event_owner_participates
                else "owner_not_participant",
                claim.event_subject_role or "",
                *claim.event_related_roles,
            )
        )
    if claim.kind == "evaluation":
        parts.append(
            "entity_subject"
            if claim.evaluation_target_claim_index is None
            else f"target_claim:{claim.evaluation_target_claim_index}"
        )
    if claim.relationship_direction is not None:
        parts.append(claim.relationship_direction)
    parts.append("symmetric" if claim.relationship_symmetric else "directed")
    return sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _normalize_relationship_type(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _relationship_predicate_is_safe(value: str) -> bool:
    normalized = _normalize_relationship_type(value)
    return bool(normalized) and any(character.isalnum() for character in normalized)


def _spans_overlap(
    left_start: int,
    left_end: int,
    right_start: int,
    right_end: int,
) -> bool:
    return max(left_start, right_start) < min(left_end, right_end)


def _surface_occurrences(
    content: str,
    surface: str,
    start: int,
    end: int,
) -> tuple[tuple[int, int], ...]:
    """Find exact surface repetitions inside one already-validated claim span."""

    occurrences: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        found = content.find(surface, cursor, end)
        if found < 0:
            break
        surface_end = found + len(surface)
        if surface_end <= end:
            occurrences.append((found, surface_end))
        cursor = found + max(1, len(surface))
    return tuple(occurrences)


def _relationship_parts_key(
    source_entity_id: str,
    target_entity_id: str,
    relation_type: str,
    bidirectional: bool,
) -> tuple[str, str, str, bool]:
    if bidirectional and target_entity_id < source_entity_id:
        source_entity_id, target_entity_id = target_entity_id, source_entity_id
    return (
        source_entity_id,
        target_entity_id,
        _normalize_relationship_type(relation_type),
        bidirectional,
    )


def _relationship_identity_key(
    relationship: Relationship,
) -> tuple[str, str, str, bool]:
    return _relationship_parts_key(
        relationship.source_entity_id,
        relationship.target_entity_id,
        relationship.relation_type,
        relationship.bidirectional,
    )


def _relationship_is_current_at(
    relationship: Relationship,
    occurred_at: str,
) -> bool:
    """Return whether an exact edge is current for this Evidence timestamp."""

    if relationship.status not in {None, "active"}:
        return False
    at_time = _product_timestamp(occurred_at)
    valid_from = _product_timestamp(relationship.valid_from)
    valid_to = _product_timestamp(relationship.valid_to)
    if at_time is None:
        return False
    if relationship.valid_from is not None and valid_from is None:
        return False
    if relationship.valid_to is not None and valid_to is None:
        return False
    if valid_from is not None and at_time < valid_from:
        return False
    if valid_to is not None and at_time >= valid_to:
        return False
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        return False
    return True


def _relationship_is_ended_at(
    relationship: Relationship,
    occurred_at: str,
) -> bool:
    """Return whether an exact historical edge has explicitly ended."""

    if relationship.status == "ended":
        return True
    if relationship.valid_to is None:
        return False
    at_time = _product_timestamp(occurred_at)
    valid_to = _product_timestamp(relationship.valid_to)
    return at_time is not None and valid_to is not None and valid_to <= at_time


def _product_timestamp(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_deictic_reference_surface(value: str) -> bool:
    key = " ".join(value.strip().casefold().split())
    if key in {"this", "that", "these", "those", "here", "there"}:
        return True
    if key.startswith(("this ", "that ", "these ", "those ")):
        return True
    return key.startswith(
        (
            "这个",
            "那个",
            "这些",
            "那些",
            "这位",
            "那位",
            "这只",
            "那只",
            "这台",
            "那台",
            "这项",
            "那项",
            "这家",
            "那家",
            "这座",
            "那座",
            "这份",
            "那份",
            "这条",
            "那条",
        )
    )


def product_turn_response_format() -> dict[str, Any]:
    return MULTI_CLAIM_RESPONSE_FORMAT


def _decode_mention(value: object, content: str) -> MeaningMention | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "text",
        "start",
        "end",
        "mode",
        "kind_hint",
        "accepted_handles",
    }:
        raise TurnMeaningError("invalid_mention_shape")
    text, start, end = value["text"], value["start"], value["end"]
    mode, kind_hint, handles = (
        value["mode"],
        value["kind_hint"],
        value["accepted_handles"],
    )
    if not isinstance(text, str) or not text:
        raise TurnMeaningError("invalid_mention_text")
    if type(start) is not int or type(end) is not int:
        raise TurnMeaningError("invalid_mention_span")
    if mode not in {"introduce", "refer"}:
        raise TurnMeaningError("invalid_mention_mode")
    if kind_hint is not None and (
        not isinstance(kind_hint, str) or not kind_hint.strip()
    ):
        raise TurnMeaningError("invalid_kind_hint")
    if not isinstance(handles, list) or any(
        not isinstance(item, str) or not item for item in handles
    ):
        raise TurnMeaningError("invalid_accepted_handles")
    if len(handles) != len(set(handles)) or len(handles) > 8:
        raise TurnMeaningError("invalid_accepted_handles")
    start, end = _canonical_model_span(content, text, start, end, "mention")
    return MeaningMention(
        text,
        start,
        end,
        cast(MentionMode, mode),
        kind_hint,
        tuple(cast(list[str], handles)),
    )


def _decode_statement(value: object, content: str) -> MeaningStatement | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "kind",
        "text",
        "start",
        "end",
        "value",
    }:
        raise TurnMeaningError("invalid_statement_shape")
    kind, text, start, end = value["kind"], value["text"], value["start"], value["end"]
    if kind not in {
        "none",
        "naming",
        "alias",
        "attribute",
        "relationship",
        "event",
        "evaluation",
    }:
        raise TurnMeaningError("invalid_statement_kind")
    if (
        not isinstance(text, str)
        or not text
        or type(start) is not int
        or type(end) is not int
    ):
        raise TurnMeaningError("invalid_statement_span")
    start, end = _canonical_model_span(content, text, start, end, "statement")
    value_span = _decode_value_span(value["value"], content)
    if kind == "attribute" and value_span is None:
        raise TurnMeaningError("attribute_value_missing")
    if kind != "attribute" and value_span is not None:
        raise TurnMeaningError("non_attribute_has_value")
    return MeaningStatement(cast(StatementKind, kind), text, start, end, value_span)


def _decode_value_span(value: object, content: str) -> MeaningValueSpan | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"text", "start", "end"}:
        raise TurnMeaningError("invalid_value_shape")
    text, start, end = value["text"], value["start"], value["end"]
    if (
        not isinstance(text, str)
        or not text
        or type(start) is not int
        or type(end) is not int
    ):
        raise TurnMeaningError("invalid_value_span")
    start, end = _canonical_model_span(content, text, start, end, "value")
    return MeaningValueSpan(text, start, end)


def _canonical_model_span(
    content: str,
    text: str,
    start: int,
    end: int,
    label: str,
) -> tuple[int, int]:
    """Anchor exact model text without trusting the model to count Unicode.

    Local models commonly return the right verbatim substring with an index
    measured in tokens, bytes, or an inclusive convention.  The program can
    still establish authoritative Evidence coordinates when that exact text
    occurs once.  Repeated or absent text remains unresolved; no fuzzy match
    or semantic guess is allowed.
    """

    if 0 <= start < end <= len(content) and content[start:end] == text:
        return start, end
    positions: list[int] = []
    cursor = 0
    while True:
        position = content.find(text, cursor)
        if position < 0:
            break
        positions.append(position)
        cursor = position + 1
    if len(positions) != 1:
        raise TurnMeaningError(f"{label}_span_mismatch")
    actual_start = positions[0]
    return actual_start, actual_start + len(text)


def _verify_span(content: str, text: str, start: int, end: int, label: str) -> None:
    if start < 0 or end <= start or end > len(content) or content[start:end] != text:
        raise TurnMeaningError(f"{label}_span_mismatch")


def _verify_statement_roles(
    content: str,
    mention: MeaningMention,
    statement: MeaningStatement,
) -> None:
    """Keep subject/topic and attribute value distinct exact Evidence spans."""

    value = statement.value
    if statement.kind == "attribute":
        if value is None:
            return
        _verify_span(content, value.text, value.start, value.end, "value")
        if not (statement.start <= value.start < value.end <= statement.end):
            raise TurnMeaningError("attribute_statement_does_not_contain_value")
        if max(mention.start, value.start) < min(mention.end, value.end):
            raise TurnMeaningError("attribute_mention_value_overlap")
        return
    if value is not None:
        raise TurnMeaningError("non_attribute_has_value")


def _attribute_claim_hull(
    content: str,
    mention: MeaningMention,
    statement: MeaningStatement,
) -> tuple[str, int, int]:
    """Create the exact Evidence claim from a subject plus predicate/value span."""

    start = min(mention.start, statement.start)
    end = max(mention.end, statement.end)
    claim = content[start:end]
    if not claim:
        raise TurnMeaningError("attribute_claim_empty")
    return claim, start, end


def _require_current_user_evidence(turn: ConversationTurn) -> None:
    """Reject malformed ledger input before it can become a candidate source."""

    if turn.role != "user":
        raise TurnMeaningError("current_turn_not_user")
    if not turn.turn_id.strip():
        raise TurnMeaningError("current_turn_id_empty")
    if not turn.conversation_id.strip():
        raise TurnMeaningError("current_conversation_id_empty")
    if not turn.occurred_at.strip():
        raise TurnMeaningError("current_occurred_at_empty")
    if not turn.content.strip():
        raise TurnMeaningError("current_turn_empty")


def _resolve_reference(
    base_graph: Any,
    turn: ConversationTurn,
    mention: MeaningMention,
    identity_view: IdentityAuthorityView,
) -> EntityReferenceResolution:
    accepted_history = tuple(
        item
        for item in _accepted_references(identity_view)
        if item.occurred_at < turn.occurred_at
    )
    return EntityReferenceResolver().resolve(
        base_graph,
        ReferenceMention(
            text=mention.text,
            evidence_id=turn.turn_id,
            conversation_id=turn.conversation_id,
            occurred_at=turn.occurred_at,
            source_role="user",
            kind_hint=_normalize_kind(mention.kind_hint),
            continuity_id=turn.conversation_id,
        ),
        accepted_history,
    )


def _resolve_exact_reference(
    base_graph: Any,
    turn: ConversationTurn,
    mention: MeaningMention,
    identity_view: IdentityAuthorityView,
) -> str | None:
    resolution = _resolve_reference(base_graph, turn, mention, identity_view)
    if resolution.state == "resolved" and resolution.basis in {"canonical", "alias"}:
        return resolution.entity_id
    return None


def _accepted_references(identity_view: IdentityAuthorityView) -> tuple[Any, ...]:
    from .entity_resolution import AcceptedEntityReference

    return tuple(
        AcceptedEntityReference(
            entity_id=binding.current_entity_id,
            mention=binding.mention.text,
            evidence_id=binding.mention.evidence_id,
            conversation_id=binding.mention.conversation_id,
            occurred_at=binding.mention.occurred_at,
            source_role="user",
            continuity_id=binding.mention.continuity_scope,
        )
        for binding in identity_view.bindings
        if binding.current_entity_id is not None
    )


def _normalize_kind(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = "_".join(value.strip().casefold().split())
    if not normalized or len(normalized) > 80:
        return None
    return normalized


def _prompt_contract(
    turns: Sequence[ConversationTurn],
    current: ConversationTurn,
    handles: Sequence[AcceptedEntityHandle],
    cognition_handles: Sequence[AcceptedCognitionHandle] = (),
    object_handles: Sequence[AcceptedWorldObjectHandle] = (),
    replacement_correction_prior_handle: str | None = None,
) -> Mapping[str, object]:
    recent = [{"role": turn.role, "content": turn.content} for turn in turns[-8:-1]]
    catalog = [
        {
            "handle": item.handle,
            "kind": item.kind,
            "canonical_name": item.canonical_name,
            "aliases": list(item.aliases),
            "recent_in_this_conversation": item.recent_in_conversation,
        }
        for item in handles
    ]
    cognition_catalog = [
        {
            "handle": item.handle,
            "content": item.cognition.content,
            "target": dict(item.target_context),
            "perspective": {
                "kind": item.cognition.perspective.kind,
                "holder_entity_ids": list(item.cognition.perspective.holder_entity_ids),
            },
            "structured_claim": (
                None
                if item.cognition.structured_claim is None
                else {
                    "statement_kind": item.cognition.structured_claim.statement_kind,
                    "predicate": item.cognition.structured_claim.predicate,
                    "value": item.cognition.structured_claim.value,
                    "polarity": item.cognition.structured_claim.polarity,
                    "epistemic_status": item.cognition.structured_claim.epistemic_status,
                }
            ),
        }
        for item in cognition_handles
    ]
    object_catalog = [
        {
            "handle": item.handle,
            "kind": item.target.kind,
            **dict(item.target_context),
        }
        for item in object_handles
    ]
    return {
        "task": "只解释最后一条用户消息。输出 mentions[] 和 claims[] 的精确原文 span；不要生成记忆对象、实体ID或数据库操作。",
        "rules": [
            "所有 text/start/end、value、predicate、occurred_at 都是当前用户消息的 Python codepoint 半开区间；只能给精确原文，不能把归一化时间伪装成原文。",
            "mentions 是 subject/topic 的实体提及；refer 必须列所有合理 opaque accepted handles，introduce 必须为空 handles。属性值、颜色、年龄、状态和数字不是 refer mention。",
            "claims 可以有多条，kind 仅为 naming/alias/attribute/relationship/event/evaluation。通常 subject 和 related_mentions 只引用 mentions 的下标；不要用词语猜测关系或事件。仅当 evaluation 通过 accepted_object_catalog 评价跨轮正式 Relationship/Event，或 event 通过同一目录明确反对/确认跨轮 event_statement 命题时，subject 可为 null。",
            "attribute/evaluation 需给出位于该 claim 内的精确 value；relationship 需精确 predicate，若涉及另一实体在 related_mentions 指出。新 Event assertion 必须给精确 predicate、精确 occurred_at 原文 span，以及以 current_user_evidence_occurred_at 为相对时间基准解释出的带时区 normalized_occurred_at；缺时间或无法确定时不要提出新 Event。仅对 accepted event_statement 命题的明确反对/确认是例外：它复用正式 Event，不重述 Event 字段，predicate/occurred_at/normalized_occurred_at 必须为空。",
            "relationship 若有 related_mentions，必须恰好一个另一端实体；subject 是 predicate 的语义 source、related_mentions[0] 是语义 target，并固定用 subject_to_related。被动句按语义角色选 subject，不按词序猜。只有显式 Owner 与单个 subject 的关系才用 owner_to_focal 或 focal_to_owner。",
            "relationship_symmetric 仅在交换关系两端后语义完全不变时为 true；普通关系、方向不确定或无法确认时必须为 false。不要靠固定关系词表补全这个判断。",
            "event_owner_participates 只表示 Owner 确实参加了该事件；第三方事件必须为 false。event_subject_role 标明 subject 是 participant 还是 related_entity；event_related_roles 必须逐项对应 related_mentions。参与者用 participant，地点、项目或其他相关对象用 related_entity。程序会给 subject participant 分配 focus 角色，不要自由发明角色。",
            "每条 claim 都必须输出 evaluation_target_claim。非 evaluation 必须为 null。evaluation 直接评价 subject Entity 时为 null，并让 subject mention 位于 evaluation claim 内；若评价同轮更早的 attribute/relationship/event，则填写那条 claim 的数组下标，且两条 claim 必须使用同一 subject。禁止引用自身、后面的 claim、evaluation 或 naming claim。",
            "每条 claim 都必须输出 object_reference 和 accepted_object_handles。普通 claim 两者分别为 null/[]。当前原文以‘这段关系’或‘那次事情’等措辞直接评价 accepted_object_catalog 中跨轮正式 Relationship/Event 时，evaluation 可令 subject=null、evaluation_target_claim=null，并输出 object_reference 的精确原文 span 与唯一 opaque handle。明确反对/确认 accepted event_statement 命题时也使用这套 Event object reference，但 kind=event、value/predicate/time 为空，并同时选择唯一 prior cognition handle。不要生成或改写 handle；同一对象 kind 的目录为空或有多个可能对象时不要猜。",
            "prior_cognition_handles 只用于本轮对 accepted_cognition_catalog 中同一正式 target、同一 perspective、同一 structured evaluation、精确 structured attribute、精确 relationship_statement 或精确 event_statement 命题的证据变化。Attribute 必须同时按 predicate 和 value 精确匹配；relationship_statement 必须匹配完整 Relationship 两端、方向和关系谓词；event_statement 必须同时用唯一同会话 Event object handle 与 cognition handle 匹配完整正式 Event 及其 statement predicate，而且只在用户明确反对或确认‘这个说法/命题’时使用。现实 Relationship 结束/重建或 Event 未发生、更正、结束/过期等对象变化不是命题 Evidence 变化，不得选择 cognition handle。明确赞同原命题时选唯一 handle 并用 polarity=affirm，明确反对时选唯一 handle 并用 polarity=negate。这两种都是 disposition=assert，因为它们是新 support/contradict Evidence，不是替换旧记忆。没有精确唯一匹配、只是程度变化或表达了不同命题时必须为空。不得用于选择 historical cognition，也不得生成或改写 handle。",
            "polarity 是 affirm 或 negate；epistemic_status 是 stated/owner_imagined/reported/uncertain。正常证据声明用 disposition=assert。若仍看到明确要求撤回、替换旧记忆的 correction，用 disposition=correction 且 prior_cognition_handles 必须为空；程序会拒绝将它当成新事实。不要把 imagined 直接当作客观事实。",
            "replacement_correction_context 非空时，纠正分类器已经选定唯一旧 cognition。你仍必须把新替代命题标为 disposition=correction、prior_cognition_handles=[]，value 必须指向用户明确给出的新替代值，不能指向句中被引用、撤回或称为错误的旧值。若是直接点名 Entity 的 Attribute 替换，只输出一条 attribute correction，subject 指向该 Entity mention，并给出与旧命题相同的精确 predicate 和新的精确 value。完整重述的 Relationship 或 Event 仍作为更早的普通 assert claim，correction evaluation 的 evaluation_target_claim 指向它。若当前原文改用‘这段关系’或‘那次事情’等措辞纠正 accepted_object_catalog 中跨轮正式 Relationship/Event，则只输出这一条 correction evaluation：subject=null、evaluation_target_claim=null，并给出精确 object_reference 和唯一 accepted_object_handle；目录同 kind 不是唯一时不要猜。",
            "纯 query 仍输出 mentions 和空 claims；任何 requested field 只是只读提示，绝不形成候选。",
            "当前用户消息是唯一新 Evidence；历史和 assistant 仅帮助解释。",
            "只输出 JSON schema，不解释。",
        ],
        "accepted_entity_catalog": catalog,
        "accepted_cognition_catalog": cognition_catalog,
        "accepted_object_catalog": object_catalog,
        "replacement_correction_context": (
            None
            if replacement_correction_prior_handle is None
            else {"prior_cognition_handle": replacement_correction_prior_handle}
        ),
        "recent_conversation_context": recent,
        "current_user_evidence": current.content,
        "current_user_evidence_occurred_at": current.occurred_at,
    }


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-standard JSON constant")


__all__ = [
    "AcceptedCognitionHandle",
    "AcceptedEntityHandle",
    "AcceptedWorldObjectHandle",
    "MeaningMention",
    "MeaningClaim",
    "MeaningStatement",
    "MeaningValueSpan",
    "ProductTurnPlan",
    "ProductClaimBundle",
    "ReviewedIdentityBinding",
    "TURN_MEANING_RESPONSE_FORMAT",
    "TurnMeaningError",
    "TurnMeaningInterpreter",
    "TurnMeaningProposal",
    "build_accepted_cognition_handles",
    "build_accepted_entity_handles",
    "build_accepted_world_object_handles",
    "compile_product_turn",
    "compile_structured_evaluation_correction",
    "decode_turn_meaning",
    "product_turn_response_format",
]
