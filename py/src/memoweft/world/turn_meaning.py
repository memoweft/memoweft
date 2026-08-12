"""Structured language interpretation for the first product vertical slice.

The model may point at exact spans and opaque accepted-entity handles.  It does
not receive canonical entity ids and it never creates a ``WorldDelta``.  This
module validates the proposal against the current user Evidence and compiles
only the deliberately small capability-1 boundary:

* introduce one persistent entity, optionally with one simple attribute; or
* attach one simple attribute to one uniquely resolved accepted entity.

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
from ..types import ConfidenceInputs, EvidenceLink
from .delta import ClaimSpan, FormationSourceTrace, FormationTrace, WorldDelta
from .entity_resolution import EntityReferenceResolver, ReferenceMention
from .extractor import ConversationTurn
from .identity_review import IdentityAuthorityView
from .identity_store import ReviewedIdentityBinding
from .graph import MemoryWorldGraph
from .model import Entity, EventParticipant, MemoryTarget, Perspective, Relationship, StructuredClaim, WorldCognition, WorldEvent


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
PlanState = Literal["candidate", "no_candidate", "clarification_required", "out_of_scope"]


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
                    "enum": ["query", "assertion", "mixed", "clarification_answer", "other"],
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
                                    "maxItems": 8,
                                    "uniqueItems": True,
                                },
                            },
                            "required": ["text", "start", "end", "mode", "kind_hint", "accepted_handles"],
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
                                                "start": {"type": "integer", "minimum": 0},
                                                "end": {"type": "integer", "minimum": 1},
                                            },
                                            "required": ["text", "start", "end"],
                                            "additionalProperties": False,
                                        },
                                    ]
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
    "type": "json_schema", "json_schema": {"name": "memoweft_turn_meaning_v2", "strict": True,
        "schema": {"type": "object", "properties": {
            "act": {"type": "string", "enum": ["query", "assertion", "mixed", "clarification_answer", "other"]},
            "mentions": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object", "properties": {
                "text": {"type": "string", "minLength": 1}, "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 1},
                "mode": {"type": "string", "enum": ["introduce", "refer"]}, "kind_hint": {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 80}]},
                "accepted_handles": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True, "maxItems": 8}}, "required": ["text", "start", "end", "mode", "kind_hint", "accepted_handles"], "additionalProperties": False}},
            "claims": {"type": "array", "maxItems": 12, "items": {"type": "object", "properties": {
                "id": {"type": "string", "minLength": 1}, "kind": {"type": "string", "enum": ["naming", "alias", "attribute", "relationship", "event", "evaluation"]}, "subject": {"type": "integer", "minimum": 0},
                "text": {"type": "string", "minLength": 1}, "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 1},
                "value": {"anyOf": [{"type": "null"}, {"type": "object", "properties": {"text": {"type": "string", "minLength": 1}, "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 1}}, "required": ["text", "start", "end"], "additionalProperties": False}]},
                "predicate": {"anyOf": [{"type": "null"}, {"type": "object", "properties": {"text": {"type": "string", "minLength": 1}, "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 1}}, "required": ["text", "start", "end"], "additionalProperties": False}]}, "occurred_at": {"anyOf": [{"type": "null"}, {"type": "object", "properties": {"text": {"type": "string", "minLength": 1}, "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 1}}, "required": ["text", "start", "end"], "additionalProperties": False}]}, "relationship_direction": {"anyOf": [{"type": "null"}, {"type": "string", "enum": ["owner_to_focal", "focal_to_owner"]}]},
                "polarity": {"type": "string", "enum": ["affirm", "negate"]}, "epistemic_status": {"type": "string", "enum": ["stated", "owner_imagined", "reported", "uncertain"]}, "disposition": {"type": "string", "enum": ["assert", "correction", "ignore"]},
                "related_mentions": {"type": "array", "items": {"type": "integer", "minimum": 0}, "uniqueItems": True}, "accepted_entity_handles": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}, "prior_cognition_handles": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}},
                "required": ["id", "kind", "subject", "text", "start", "end", "value", "predicate", "occurred_at", "relationship_direction", "polarity", "epistemic_status", "disposition", "related_mentions", "accepted_entity_handles", "prior_cognition_handles"], "additionalProperties": False}}},
            "required": ["act", "mentions", "claims"], "additionalProperties": False}}}


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
    """The exact value portion of a simple attribute statement."""

    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class MeaningClaim:
    """One model-proposed, exact-span claim; never a direct write command."""

    claim_id: str
    kind: StatementKind
    subject_mention_index: int
    text: str
    start: int
    end: int
    value: MeaningValueSpan | None
    predicate: MeaningValueSpan | None = None
    occurred_at: MeaningValueSpan | None = None
    relationship_direction: Literal["owner_to_focal", "focal_to_owner"] | None = None
    polarity: ClaimPolarity = "affirm"
    epistemic_status: EpistemicStatus = "stated"
    disposition: ClaimDisposition = "assert"
    related_mention_indices: tuple[int, ...] = ()
    accepted_entity_handles: tuple[str, ...] = ()
    prior_cognition_handles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductClaimBundle:
    """Stable handoff material for SQLite/adapter; it contains no authority."""

    focal_entity_id: str
    evidence_id: str
    perspective_holder_entity_id: str
    claims: tuple[MeaningClaim, ...]

    def to_data(self) -> dict[str, object]:
        return {
            "version": 1,
            "focal_entity_id": self.focal_entity_id,
            "evidence_id": self.evidence_id,
            "perspective_holder_entity_id": self.perspective_holder_entity_id,
            "claims": [
                {
                    "id": item.claim_id,
                    "kind": item.kind,
                    "subject_mention_index": item.subject_mention_index,
                    "text": item.text,
                    "start": item.start,
                    "end": item.end,
                    "value": None if item.value is None else {"text": item.value.text, "start": item.value.start, "end": item.value.end},
                    "predicate": None if item.predicate is None else {"text": item.predicate.text, "start": item.predicate.start, "end": item.predicate.end},
                    "occurred_at": None if item.occurred_at is None else {"text": item.occurred_at.text, "start": item.occurred_at.start, "end": item.occurred_at.end},
                    "relationship_direction": item.relationship_direction,
                    "polarity": item.polarity,
                    "epistemic_status": item.epistemic_status,
                    "disposition": item.disposition,
                    "related_mention_indices": list(item.related_mention_indices),
                    "accepted_entity_handles": list(item.accepted_entity_handles),
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


class TurnMeaningInterpreter:
    """Ask the model for spans and handles, then decode an exact closed shape."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def interpret(
        self,
        turns: Sequence[ConversationTurn],
        current_user_turn: ConversationTurn,
        handles: Sequence[AcceptedEntityHandle],
    ) -> TurnMeaningProposal:
        if current_user_turn.role != "user":
            raise TurnMeaningError("current_turn_not_user")
        if not current_user_turn.content.strip():
            raise TurnMeaningError("current_turn_empty")
        if not turns or turns[-1] != current_user_turn:
            raise TurnMeaningError("current_turn_not_final")
        contract = _prompt_contract(turns, current_user_turn, handles)
        try:
            raw = self._llm.chat(
                [
                    ChatMessage("system", json.dumps(contract, ensure_ascii=False, separators=(",", ":"))),
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
    # Capability 1 deliberately has no discourse-ranking policy.  Every
    # accepted entity mentioned in this conversation remains a possible
    # antecedent; if there is more than one, the product must clarify instead
    # of silently treating the latest timestamp as the user's intent.
    recent_entity_ids = {
        cast(str, binding.current_entity_id)
        for binding in same_conversation
    }
    items: list[AcceptedEntityHandle] = []
    for entity in sorted(identity_view.graph.entities, key=lambda item: item.id):
        if entity.id == identity_view.graph.world.owner_entity_id:
            continue
        opaque = "accepted:" + sha256(
            f"turn-meaning-handle-v1\0{world_hash}\0{entity.id}".encode("utf-8")
        ).hexdigest()[:16]
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
) -> ProductTurnPlan:
    """Validate identity and compile only capability-1 candidate shapes."""

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
        )
    if proposal.act == "query":
        return ProductTurnPlan("no_candidate", "query_read_only", proposal)
    if proposal.act in {"mixed", "clarification_answer"}:
        return ProductTurnPlan("out_of_scope", f"act_{proposal.act}_not_supported", proposal)
    if proposal.act == "other":
        return ProductTurnPlan("no_candidate", "no_world_assertion", proposal)
    mention = proposal.mention
    statement = proposal.statement
    if mention is None:
        return ProductTurnPlan("no_candidate", "assertion_has_no_referent", proposal)
    _verify_span(current_user_turn.content, mention.text, mention.start, mention.end, "mention")
    if statement is not None:
        _verify_span(current_user_turn.content, statement.text, statement.start, statement.end, "statement")
        _verify_statement_roles(current_user_turn.content, mention, statement)
    if statement is not None and statement.kind in {"alias", "relationship", "event", "evaluation"}:
        return ProductTurnPlan("out_of_scope", f"statement_{statement.kind}_not_supported", proposal)

    handle_map = {item.handle: item for item in handles}
    entity_id: str
    entity_name: str
    new_entities: tuple[Entity, ...]
    if mention.mode == "introduce":
        if mention.accepted_handles:
            raise TurnMeaningError("introduction_selected_accepted_handle")
        kind = _normalize_kind(mention.kind_hint)
        if kind is None:
            return ProductTurnPlan("out_of_scope", "introduction_kind_missing", proposal)
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
        unknown = tuple(handle for handle in mention.accepted_handles if handle not in handle_map)
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
            if len(grounded) == 1 and (not reported or (len(reported) == 1 and reported[0].entity_id == grounded[0].entity_id)):
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
            # Capability 1 is not a generic mention-log.  Repeating a known
            # name without a supported attribute must not create a candidate.
            return ProductTurnPlan("no_candidate", "reference_without_attribute", proposal)
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
    claim, claim_start, claim_end = _attribute_claim_hull(current_user_turn.content, mention, statement)
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


def _decode_claim_proposal(value: Mapping[str, object], content: str) -> TurnMeaningProposal:
    act = value["act"]
    if act not in {"query", "assertion", "mixed", "clarification_answer", "other"}:
        raise TurnMeaningError("invalid_act")
    raw_mentions = value["mentions"]
    raw_claims = value["claims"]
    if not isinstance(raw_mentions, list) or not isinstance(raw_claims, list):
        raise TurnMeaningError("invalid_claim_collection")
    mentions = tuple(_decode_mention(item, content) for item in raw_mentions)
    if any(item is None for item in mentions) or not mentions:
        raise TurnMeaningError("claims_need_mentions")
    decoded_mentions = cast(tuple[MeaningMention, ...], mentions)
    if act == "query":
        return TurnMeaningProposal("query", mentions=decoded_mentions)
    claims = tuple(_decode_claim(item, content, len(decoded_mentions)) for item in raw_claims)
    if act == "assertion" and not claims:
        raise TurnMeaningError("assertion_has_no_claims")
    return TurnMeaningProposal(act, mentions=decoded_mentions, claims=claims)


def _decode_claim(value: object, content: str, mention_count: int) -> MeaningClaim:
    required = {"id", "kind", "subject", "text", "start", "end", "value", "polarity", "epistemic_status", "disposition", "related_mentions", "accepted_entity_handles", "prior_cognition_handles"}
    optional = {"predicate", "occurred_at", "relationship_direction"}
    if not isinstance(value, dict) or not required <= set(value) or not set(value) <= required | optional:
        raise TurnMeaningError("invalid_claim_shape")
    claim_id, kind, subject = value["id"], value["kind"], value["subject"]
    text, start, end = value["text"], value["start"], value["end"]
    polarity, epistemic, disposition = value["polarity"], value["epistemic_status"], value["disposition"]
    if not isinstance(claim_id, str) or not claim_id.strip() or kind not in {"naming", "alias", "attribute", "relationship", "event", "evaluation"}:
        raise TurnMeaningError("invalid_claim_kind")
    if type(subject) is not int or not 0 <= subject < mention_count:
        raise TurnMeaningError("invalid_claim_subject")
    if not isinstance(text, str) or not text or type(start) is not int or type(end) is not int:
        raise TurnMeaningError("invalid_claim_span")
    start, end = _canonical_model_span(content, text, start, end, "claim")
    value_span = _decode_value_span(value["value"], content)
    predicate_span = _decode_value_span(value.get("predicate"), content)
    occurred_at_span = _decode_value_span(value.get("occurred_at"), content)
    if kind == "attribute" and value_span is None:
        raise TurnMeaningError("attribute_value_missing")
    if kind != "attribute" and value_span is not None:
        raise TurnMeaningError("non_attribute_has_value")
    if kind in {"relationship", "event"} and predicate_span is None:
        raise TurnMeaningError("claim_predicate_missing")
    direction = value.get("relationship_direction")
    if kind == "relationship" and direction not in {"owner_to_focal", "focal_to_owner"}:
        raise TurnMeaningError("relationship_direction_missing")
    if kind != "relationship" and direction is not None:
        raise TurnMeaningError("relationship_direction_unexpected")
    if polarity not in {"affirm", "negate"} or epistemic not in {"stated", "owner_imagined", "reported", "uncertain"} or disposition not in {"assert", "correction", "ignore"}:
        raise TurnMeaningError("invalid_claim_semantics")
    related = _decode_index_list(value["related_mentions"], mention_count, "related_mentions")
    entities = _decode_handle_list(value["accepted_entity_handles"], "accepted_entity_handles")
    priors = _decode_handle_list(value["prior_cognition_handles"], "prior_cognition_handles")
    return MeaningClaim(claim_id, cast(StatementKind, kind), subject, text, start, end, value_span, predicate_span, occurred_at_span, cast(Any, direction), cast(ClaimPolarity, polarity), cast(EpistemicStatus, epistemic), cast(ClaimDisposition, disposition), related, entities, priors)


def _decode_index_list(value: object, mention_count: int, label: str) -> tuple[int, ...]:
    if not isinstance(value, list) or any(type(item) is not int or not 0 <= item < mention_count for item in value) or len(set(cast(list[int], value))) != len(value):
        raise TurnMeaningError(f"invalid_{label}")
    return tuple(cast(list[int], value))


def _decode_handle_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value) or len(set(cast(list[str], value))) != len(value):
        raise TurnMeaningError(f"invalid_{label}")
    return tuple(cast(list[str], value))


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
) -> ProductTurnPlan:
    """Compile one-focus multi-claim input without granting model write authority.

    Only a direct Owner-stated affirmative attribute is lowered through the
    legacy safe writer today.  The complete classified bundle is retained for
    the SQLite product operation; relation/evaluation/correction stay proposed
    material until their dedicated reviewed writers exist.
    """

    if proposal.act == "query":
        return ProductTurnPlan("no_candidate", "query_read_only", proposal)
    if proposal.act != "assertion":
        return ProductTurnPlan("out_of_scope", f"act_{proposal.act}_not_supported", proposal)
    if len(proposal.mentions) != 1:
        return ProductTurnPlan("clarification_required", "one_focal_entity_required", proposal)
    focal = proposal.mentions[0]
    _verify_span(current_user_turn.content, focal.text, focal.start, focal.end, "mention")
    for claim in proposal.claims:
        _verify_span(current_user_turn.content, claim.text, claim.start, claim.end, "claim")
        if claim.subject_mention_index != 0 or claim.related_mention_indices:
            return ProductTurnPlan("out_of_scope", "multiple_entity_claim_not_supported", proposal)
        if claim.value is not None:
            _verify_span(current_user_turn.content, claim.value.text, claim.value.start, claim.value.end, "value")
            if claim.kind == "attribute":
                if not (claim.start <= claim.value.start < claim.value.end <= claim.end):
                    raise TurnMeaningError("attribute_statement_does_not_contain_value")
                if max(focal.start, claim.value.start) < min(focal.end, claim.value.end):
                    raise TurnMeaningError("attribute_mention_value_overlap")
    safe = next(
        (
            item for item in proposal.claims
            if item.kind == "attribute"
            and item.polarity == "affirm"
            and item.epistemic_status == "stated"
            and item.disposition == "assert"
            and item.value is not None
        ),
        None,
    )
    legacy_statement = None
    if safe is not None:
        legacy_statement = MeaningStatement("attribute", safe.text, safe.start, safe.end, safe.value)
    legacy = TurnMeaningProposal("assertion", focal, legacy_statement)
    plan = compile_product_turn(
        proposal=legacy,
        current_user_turn=current_user_turn,
        world_id=world_id,
        owner_entity_id=owner_entity_id,
        base_graph=base_graph,
        handles=handles,
        identity_view=identity_view,
        operation_key=operation_key,
    )
    focal_id = plan.resolved_entity_id
    if focal_id is None:
        return plan
    bundle = ProductClaimBundle(focal_id, current_user_turn.turn_id, owner_entity_id, proposal.claims)
    if plan.delta is None:
        return replace(plan, proposal=proposal, claim_bundle=bundle)
    extra_cognitions: list[WorldCognition] = []
    extra_traces: list[FormationTrace] = []
    extra_relationships: list[Relationship] = []
    extra_events: list[WorldEvent] = []
    for claim in proposal.claims:
        if claim.disposition != "assert" or claim.polarity != "affirm" or claim.epistemic_status != "stated":
            continue
        if claim.kind not in {"attribute", "evaluation", "relationship", "event"}:
            continue
        if safe is claim:
            continue
        source_hash = sha256(current_user_turn.content.encode("utf-8")).hexdigest()
        def trace_for(cognition_id: str, content: str, start: int, end: int) -> FormationTrace:
            return FormationTrace(cognition_id, False, (FormationSourceTrace(current_user_turn.turn_id, "support", "user_stated", "elaborate", ClaimSpan(start, end, source_hash, sha256(content.encode("utf-8")).hexdigest()), None, None, "exact_user_claim", "product.claim.v2"),), "stated", 1, 1, 0)
        confidence = compute_confidence(ConfidenceInputs("fact", "stated", 1, 0))
        if claim.kind == "relationship":
            if claim.predicate is None or claim.relationship_direction is None:
                continue
            existing = tuple(item for item in base_graph.relationships.values() if {item.source_entity_id, item.target_entity_id} == {owner_entity_id, focal_id})
            if len(existing) == 1:
                relationship = existing[0]
            elif not existing:
                source, target = (owner_entity_id, focal_id) if claim.relationship_direction == "owner_to_focal" else (focal_id, owner_entity_id)
                relationship = Relationship("relationship:" + str(uuid5(NAMESPACE_URL, f"memoweft-product-rel-v2\0{world_id}\0{operation_key}\0{claim.claim_id}")), world_id, source, target, claim.predicate.text)
                extra_relationships.append(relationship)
            else:
                continue
            cognition_id = "cog:" + str(uuid5(NAMESPACE_URL, f"memoweft-product-rel-cog-v2\0{world_id}\0{operation_key}\0{claim.claim_id}"))
            structured = StructuredClaim("relationship_statement", predicate=claim.predicate.text, polarity="assert", epistemic_status="asserted")
            extra_cognitions.append(WorldCognition(cognition_id, world_id, MemoryTarget("relationship", relationship.id), claim.text, "fact", "stated", confidence, derive_cred_status(confidence, 0, "fact", support_count=1), Perspective("entity", (owner_entity_id,)), (EvidenceLink(current_user_turn.turn_id, "support"),), structured_claim=structured))
            extra_traces.append(trace_for(cognition_id, claim.text, claim.start, claim.end))
            continue
        if claim.kind == "event":
            if claim.predicate is None:
                continue
            occurred = current_user_turn.occurred_at
            if claim.occurred_at is not None:
                try:
                    occurred = datetime.fromisoformat(claim.occurred_at.text.replace("Z", "+00:00")).isoformat()
                except ValueError:
                    pass
            event = WorldEvent("event:" + str(uuid5(NAMESPACE_URL, f"memoweft-product-event-v2\0{world_id}\0{operation_key}\0{claim.claim_id}")), world_id, "lived_occurrence", claim.text, occurred, (EventParticipant(owner_entity_id, "owner"), EventParticipant(focal_id, "focus")), (owner_entity_id, focal_id), evidence_ids=(current_user_turn.turn_id,))
            extra_events.append(event)
            cognition_id = "cog:" + str(uuid5(NAMESPACE_URL, f"memoweft-product-event-cog-v2\0{world_id}\0{operation_key}\0{claim.claim_id}"))
            structured = StructuredClaim("event_statement", predicate=claim.predicate.text, polarity="assert", epistemic_status="asserted")
            extra_cognitions.append(WorldCognition(cognition_id, world_id, MemoryTarget("event", event.id), claim.text, "fact", "stated", confidence, derive_cred_status(confidence, 0, "fact", support_count=1), Perspective("entity", (owner_entity_id,)), (EvidenceLink(current_user_turn.turn_id, "support"),), structured_claim=structured))
            extra_traces.append(trace_for(cognition_id, claim.text, claim.start, claim.end))
            continue
        content, start, end = _attribute_claim_hull(current_user_turn.content, focal, MeaningStatement("attribute", claim.text, claim.start, claim.end, claim.value)) if claim.kind == "attribute" and claim.value is not None else (claim.text, claim.start, claim.end)
        cognition_id = "cog:" + str(uuid5(NAMESPACE_URL, f"memoweft-product-claim-v2\0{world_id}\0{operation_key}\0{claim.claim_id}"))
        structured = StructuredClaim("attribute", value=None if claim.value is None else claim.value.text, polarity="assert", epistemic_status="asserted") if claim.kind == "attribute" else StructuredClaim("evaluation", value=None if claim.value is None else claim.value.text, polarity="assert", epistemic_status="asserted")
        extra_cognitions.append(WorldCognition(cognition_id, world_id, MemoryTarget("entity", focal_id), content, "fact", "stated", confidence, derive_cred_status(confidence, 0, "fact", support_count=1), Perspective("entity", (owner_entity_id,)), (EvidenceLink(current_user_turn.turn_id, "support"),), structured_claim=structured))
        extra_traces.append(trace_for(cognition_id, content, start, end))
    if extra_cognitions or extra_relationships or extra_events:
        delta = WorldDelta(world_id, plan.delta.source_evidence_ids, plan.delta.new_entities, plan.delta.new_relationships + tuple(extra_relationships), plan.delta.new_events + tuple(extra_events), plan.delta.new_cognitions + tuple(extra_cognitions), formation_traces=plan.delta.formation_traces + tuple(extra_traces))
        return replace(plan, proposal=proposal, delta=delta, claim_bundle=bundle)
    return replace(plan, proposal=proposal, claim_bundle=bundle)


def product_turn_response_format() -> dict[str, Any]:
    return MULTI_CLAIM_RESPONSE_FORMAT


def _decode_mention(value: object, content: str) -> MeaningMention | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "text", "start", "end", "mode", "kind_hint", "accepted_handles"
    }:
        raise TurnMeaningError("invalid_mention_shape")
    text, start, end = value["text"], value["start"], value["end"]
    mode, kind_hint, handles = value["mode"], value["kind_hint"], value["accepted_handles"]
    if not isinstance(text, str) or not text:
        raise TurnMeaningError("invalid_mention_text")
    if type(start) is not int or type(end) is not int:
        raise TurnMeaningError("invalid_mention_span")
    if mode not in {"introduce", "refer"}:
        raise TurnMeaningError("invalid_mention_mode")
    if kind_hint is not None and (not isinstance(kind_hint, str) or not kind_hint.strip()):
        raise TurnMeaningError("invalid_kind_hint")
    if not isinstance(handles, list) or any(not isinstance(item, str) or not item for item in handles):
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
    if not isinstance(value, dict) or set(value) != {"kind", "text", "start", "end", "value"}:
        raise TurnMeaningError("invalid_statement_shape")
    kind, text, start, end = value["kind"], value["text"], value["start"], value["end"]
    if kind not in {"none", "naming", "alias", "attribute", "relationship", "event", "evaluation"}:
        raise TurnMeaningError("invalid_statement_kind")
    if not isinstance(text, str) or not text or type(start) is not int or type(end) is not int:
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
    if not isinstance(text, str) or not text or type(start) is not int or type(end) is not int:
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


def _resolve_exact_reference(
    base_graph: Any,
    turn: ConversationTurn,
    mention: MeaningMention,
    identity_view: IdentityAuthorityView,
) -> str | None:
    accepted_history = tuple(
        item
        for item in _accepted_references(identity_view)
        if item.occurred_at < turn.occurred_at
    )
    resolution = EntityReferenceResolver().resolve(
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
) -> Mapping[str, object]:
    recent = [
        {"role": turn.role, "content": turn.content}
        for turn in turns[-8:-1]
    ]
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
    return {
        "task": "只解释最后一条用户消息。输出 mentions[] 和 claims[] 的精确原文 span；不要生成记忆对象、实体ID或数据库操作。",
        "rules": [
            "所有 text/start/end、value、predicate、occurred_at 都是当前用户消息的 Python codepoint 半开区间；只能给精确原文，不能生成未出现的字段。",
            "mentions 是 subject/topic 的实体提及；refer 必须列所有合理 opaque accepted handles，introduce 必须为空 handles。属性值、颜色、年龄、状态和数字不是 refer mention。",
            "claims 可以有多条，kind 仅为 naming/alias/attribute/relationship/event/evaluation。subject 和 related_mentions 只引用 mentions 的下标；不要用词语猜测关系或事件。",
            "attribute/evaluation 需 value；relationship 需精确 predicate，若涉及另一实体在 related_mentions 指出；event 需精确 predicate 和可解析的 occurred_at。predicate/value/time 都必须是原文 span。",
            "polarity 是 affirm 或 negate；epistemic_status 是 stated/owner_imagined/reported/uncertain。correction 是 disposition，不是 kind：仅当你能给 opaque prior cognition handle 时提出 correction；不要把 imagined 直接当作客观事实。",
            "纯 query 仍输出 mentions 和空 claims；任何 requested field 只是只读提示，绝不形成候选。",
            "当前用户消息是唯一新 Evidence；历史和 assistant 仅帮助解释。",
            "只输出 JSON schema，不解释。",
        ],
        "accepted_entity_catalog": catalog,
        "recent_conversation_context": recent,
        "current_user_evidence": current.content,
    }


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-standard JSON constant")


__all__ = [
    "AcceptedEntityHandle",
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
    "build_accepted_entity_handles",
    "compile_product_turn",
    "decode_turn_meaning",
    "product_turn_response_format",
]
