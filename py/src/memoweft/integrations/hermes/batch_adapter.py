"""V3 formal batch adapter: one committed boundary -> typed World changes.

One committed Hermes boundary is interpreted with one physical model request,
plus one feedback rewrite if deterministic compilation rejects it. Each result is
checkpointed durably before any World mutation, then compiled deterministically
(compiler-owned spans, target, identity, and fields) and applied in one fenced
atomic transaction.

V3 semantics (Owner decision 2026-08-16, §4.12): on top of V2 (up to 3 items
per boundary, typed corrects, confirmed 280 contract) the closed contract adds
first-class Entity + Relationship formation — naming and owner-perspective
relationship statements with compiler-owned mention→identity resolution
(deterministic ids from the canonical name; ambiguity or same-name conflict is
zero-write).

Legacy ``schema_version=1``/``2`` envelope shapes remain accepted. Stated
propositions also use their original Evidence rather than model rewrites.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
import hashlib
import json
import logging
import re
import sqlite3
from typing import Any, Callable, Literal, Mapping, Optional, TypedDict

from ...clock import Clock, system_clock, to_iso_z
from ...store.driver import BUSY_TIMEOUT_MS
from ...types import ModelTier
from ..trust.currentness import (
    current_entity_aliases,
    evidence_state,
    linked_evidence,
    world_item_visible,
)
from ..trust.revision import advance_world_revision
from .world_worker import (
    ClaimedWorldJob,
    PermanentWorldJobError,
    WorldJobResult,
)
from .terminal_outcome import persist_terminal_outcome_in_transaction

logger = logging.getLogger(__name__)

#: Current closed interpretation envelope version (V8).  Envelope v1-v7 remain
#: accepted for deterministic replay of in-flight checkpoints.
INTERPRETATION_SCHEMA_VERSION = 8
LEGACY_INTERPRETATION_SCHEMA_VERSION = 1
LEGACY_V2_INTERPRETATION_SCHEMA_VERSION = 2
LEGACY_V3_INTERPRETATION_SCHEMA_VERSION = 3
LEGACY_V4_INTERPRETATION_SCHEMA_VERSION = 4
LEGACY_V5_INTERPRETATION_SCHEMA_VERSION = 5
LEGACY_V6_INTERPRETATION_SCHEMA_VERSION = 6
LEGACY_V7_INTERPRETATION_SCHEMA_VERSION = 7

#: Owner decision (§4.12, 2026-08-15 → 2026-08-16 更新为 5): at most this many
#: supported cognitions per committed boundary. Invalid interpretation may
#: receive one feedback rewrite before the fenced atomic Apply.
MAX_COGNITIONS_PER_BATCH = 5

#: V3 supports Owner self-targeted stable statement kinds plus first-class
#: naming/relationship entries (V3/V4), alias merging (V5), contradict/retract
#: (V6), and the first-class World Event (V7).
SUPPORTED_STATEMENT_KINDS = (
    "attribute", "preference", "naming", "relationship", "alias", "event",
)
#: V2 legacy kinds only (envelope v2 never carried naming/relationship).
LEGACY_V2_STATEMENT_KINDS = ("attribute", "preference")

#: 1.0 deterministic confidence rules (shared config-constants.json):
#: baseByFormedBy stated 600 / confirmed 280, +40 per additional support
#: evidence, cap +200, hard max 1000.  V6 adds the contradict carrier: any
#: same-ID contradictory Evidence pins the chain's weakest carrier to
#: contradict_stated (base 0) — the credibility downgrades but the cognition
#: stays current (Owner decision 2026-08-16: downgrade only, never implicit
#: invalidation).
FORMED_BY_BASES = {"stated": 600, "confirmed": 280, "contradict_stated": 0}
#: Carrier dimension strength (1.0 carrierRank): weakest carrier wins when a
#: same-ID cognition accumulates evidence across carriers.  contradict_stated
#: ranks weakest so any contradiction wins the base.
CARRIER_RANK = {"contradict_stated": -1, "confirmed": 0, "stated": 1}
SUPPORT_STEP = 40
SUPPORT_CAP = 5
CONFIDENCE_HARD_MAX = 1000

#: 1.0 credibility thresholds: (label, floor), highest first.
CRED_THRESHOLDS = (("stable", 750), ("limited", 500), ("low", 300))

#: Bounded same-process retries for a transient SQLite lock during Apply.
#: Retrying Apply never re-calls the model (the checkpoint is durable).
APPLY_BUSY_RETRIES = 3

#: Confirmation spans are short: a long restatement is a ``stated`` carrier.
CONFIRMATION_SPAN_MAX_CHARS = 40

#: OneShotRoute = host-owned strict route.
#: (messages, session_id) -> {"content": str, "model": str, "usage"?: dict}
OneShotRoute = Callable[..., Mapping[str, object]]


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EvidenceSegment(TypedDict):
    id: str
    start: int
    end: int
    text: str


def _evidence_segments(raw: str) -> list[EvidenceSegment]:
    """Deterministic verbatim clauses; models select IDs, never calculate spans."""

    segments: list[EvidenceSegment] = []
    start = 0
    for match in re.finditer(r"[\n，。！？；,!?;]", raw):
        end = match.end()
        if raw[start:end].strip():
            segments.append({"id": f"s{len(segments)}", "start": start, "end": end, "text": raw[start:end]})
        start = end
    if raw[start:].strip():
        segments.append({"id": f"s{len(segments)}", "start": start, "end": len(raw), "text": raw[start:]})
    return segments


# ── deterministic proposition normalization ───────────────────────────────

#: First-person self references normalized to the canonical owner subject.
#: Longest first so "我们"/"咱们" win over the bare "我"/"咱" suffix match.
_FIRST_PERSON_PREFIXES = ("咱们", "我们", "俺们", "本人", "我", "咱", "俺")

#: English first-person tokens replaced at slice start (lowercased matching,
#: word boundary implied by the trailing space).  The replacement casing is the
#: exact contract taught to the model ("The user" / "The user's").
_EN_FIRST_PERSON = (
    ("i ", "The user "),
    ("we ", "The user "),
    ("my ", "The user's "),
    ("our ", "The user's "),
    ("mine ", "The user's "),
    ("me ", "The user "),
    ("us ", "The user "),
    ("myself ", "The user "),
)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)

#: Trailing sentence punctuation ignored when comparing a proposition with its
#: verbatim anchor (the model may or may not copy the final full stop).
_END_PUNCTUATION = "。！？!?~～，,；;：:、 "
_REFERENCE_MENTIONS = ("他", "她", "它", "他们", "她们", "它们", "he", "she", "they", "him", "her", "them")


def _normalize_spoken_name(name: str, slices: list[str]) -> str:
    """Remove a sentence-final particle only when naming syntax proves it."""

    if not name.endswith("吧") or len(name) < 2:
        return name
    stem = name[:-1]
    quoted = re.compile(rf"[\"'“‘] {re.escape(name)} [\"'”’]".replace(" ", ""))
    naming = re.compile(
        rf"(?:叫|称|称呼|喊)(?:我们就)?(?:叫)?(?:他|她|它)?{re.escape(stem)}吧(?:[，。！？,.!?]|$)"
    )
    if any(quoted.search(text) for text in slices):
        return name
    return stem if any(naming.search(text) for text in slices) else name


def _has_reference_mention(slices: list[str]) -> bool:
    for text in slices:
        if any(mention in text for mention in _REFERENCE_MENTIONS[:6]):
            return True
        if any(re.search(rf"\b{mention}\b", text, re.IGNORECASE) for mention in _REFERENCE_MENTIONS[6:]):
            return True
    return False


def _strip_end_punctuation(value: str) -> str:
    return value.strip().rstrip(_END_PUNCTUATION).strip()


def _is_iso_date(value: str) -> bool:
    """Closed contract for V7 event occurred_at: ``YYYY-MM-DD`` only."""
    try:
        datetime.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def entity_id_for(world_id: str, canonical_name: str) -> str:
    """Deterministic entity identity from its canonical name.

    Same name → same id: re-mention is idempotent and same-name conflicts are
    structurally zero-write.  The owner entity uses a distinct derivation so a
    third party literally named "用户" can never collide with it.
    """
    return "entity-" + _hash_text(
        _canonical(["memoweft_entity_v1", world_id, canonical_name])
    )


def owner_entity_id_for(world_id: str) -> str:
    return "entity-" + _hash_text(
        _canonical(["memoweft_owner_entity_v1", world_id])
    )


#: The owner entity's canonical surface name (owner_self perspective).
OWNER_ENTITY_NAME = "用户"
OWNER_ENTITY_KIND = "person"

_OWNER_NAME_PROPOSITION_RE: re.Pattern[str] = re.compile('(?:用户(?:偏好)?称呼自己为[“"\']?([^“”"\'\\s，,。]{1,20})[”"\']?|用户以后叫我[“"\']?([^“”"\'\\s，,。]{1,20})[”"\']?|用户叫[“"\']?([^“”"\'\\s，,。]{1,20})[”"\']?|用户名叫[“"\']?([^“”"\'\\s，,。]{1,20})[”"\']?|用户的?名字(?:是|叫)[“"\']?([^“”"\'\\s，,。]{1,20})[”"\']?|用户(?:的?称呼(?:是|为))[“"\']?([^“”"\'\\s，,。]{1,20})[”"\']?)')

_PURE_INQUIRY_PATTERNS = (
    re.compile(r"^(?:你)?(?:还)?(?:记得|知道|了解)(?:我)?(?:是|叫)?(?:谁|啥|什么|哪些|多少|吗|么)?(?:吧|呀|啊|呢|了吗)?[?？]?$"),
    re.compile(r"^(?:那|所以)?(?:你)?(?:怎么|如何|怎样)?叫我[啥什么]?(?:呢|吧|呀|啊)?[?？]?$"),
    re.compile(r"^(?:那|所以)?(?:我)?(?:叫|是)[啥什么谁哪]?(?:呢|吧|呀|啊)?[?？]?$"),
    re.compile(r"^(?:你)?(?:在吗|你好|您好|哈喽|hello|hi)[!！。~～?？啊呀吧呢]*$", re.I),
    re.compile(r"^(?:好的|好|行|可以|收到|明白|对|嗯|哦|哈哈+|呵呵+|嘻嘻+|谢谢|多谢|thx|thanks|ok|yes)[!！。~～]*$", re.I),
    re.compile(r"^(?:帮我|请帮我|写一个|查一下|搜索|解释一下|这是什么|为什么|怎么做).*"),
)

_DECLARATION_SIGNALS = (
    re.compile(r"我叫[^\s?？，,。]+"),
    re.compile(r"叫我[^\s?？，,。]+"),
    re.compile(r"我的名字[是叫]"),
    re.compile(r"我(?:平时|经常|最|挺|很)?喜欢"),
    re.compile(r"我(?:平时|经常|习惯)"),
    re.compile(r"我(?:今年|目前)?\d+[岁周]"),
    re.compile(r"我(?:有|没有)(?:女朋友|男朋友|老婆|老公|对象)"),
    re.compile(r"(?:分手|在一起|恋爱|结婚|脱单)"),
    re.compile(r"(?:其实|记错|改一下|不是.*是)"),
    re.compile(r"(?:我的?朋友|我的?同事|我的?同学|我女朋友|我男朋友|我老婆|我老公)[叫是]"),
)

def _has_declarative_facts(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if re.match(r"^(?:你)?(?:还)?(?:记得|知道|了解).*?[?？吗么吧呢]$", text):
        if not re.search(r"(?:其实|记错了|改一下|我叫|叫我[^\s?？，,。]+(?<![啥什么]))", text):
            return False
    for pat in _DECLARATION_SIGNALS:
        if pat.search(text):
            if "叫我啥" in text or "叫我什么" in text:
                continue
            return True
    for pat in _PURE_INQUIRY_PATTERNS:
        if pat.search(text):
            return False
    if text.endswith(("?", "？")):
        return False
    if text.endswith(("吗", "么", "呢", "吧")):
        if any(w in text for w in ("谁", "啥", "什么", "怎么", "如何", "哪", "几点", "什么时候", "多少", "是否", "能否", "为什么", "知道", "记得")):
            return False
    return True


def _explicit_source_dates(slices: list[str]) -> set[str]:
    """Calendar dates copied/normalized from user text, without relative guesses."""
    dates: set[str] = set()
    for text in slices:
        for match in re.finditer(r"(?<![0-9])([0-9]{4})(?:年|[-/.])([0-9]{1,2})(?:月|[-/.])([0-9]{1,2})(?:日|号)?(?![0-9])", text):
            value = f"{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}"
            if _is_iso_date(value):
                dates.add(value)
    return dates


def relationship_id_for(
    world_id: str, source_entity_id: str, relation_type: str, target_entity_id: str
) -> str:
    """Deterministic first-class relationship identity."""
    return "relationship-" + _hash_text(
        _canonical(
            [
                "memoweft_relationship_v1",
                world_id,
                source_entity_id,
                relation_type,
                target_entity_id,
            ]
        )
    )


def world_event_id_for(world_id: str, proposition: str) -> str:
    """Deterministic first-class World Event identity from its verbatim
    narrative (V7): restating the same event keeps the same id."""
    return "world-event-" + _hash_text(
        _canonical(["memoweft_world_event_v1", world_id, proposition])
    )


def cognition_id_for_holder(
    world_id: str, kind: str, proposition: str, holder_entity_id: str
) -> str:
    """Deterministic V5 third-party-perspective cognition identity.

    Owner decision 2026-08-16: perspective enters the identity — the same
    proposition held by different holders coexists and evolves independently.
    The owner_self path keeps the V1 derivation (replay compatibility).
    """
    return "cognition-" + _hash_text(
        _canonical(
            [
                "memoweft_cognition_v2",
                world_id,
                kind,
                proposition,
                holder_entity_id,
            ]
        )
    )


def _stated_normalize(span_text: str) -> str:
    """Deterministic stated-anchor normalization of one evidence slice.

    ``proposition == _stated_normalize(slice)`` is the compiler-owned contract
    for ``formed_by=stated`` (validated against the real V1 model output: span
    "我喜欢喝茉莉花茶" -> proposition "用户喜欢喝茉莉花茶").  A first-person
    prefix becomes "用户"; a slice with no subject at all gets "用户" prepended
    (subject-less Chinese statements are natural: "喜欢喝茉莉花茶").

    English (Owner-approved §4.12 rule-localization): the verbatim contract —
    English utterances carry their own subject, so no rewriting applies and the
    user's exact words anchor the memory ("I like jasmine tea" stays "I like
    jasmine tea").  Chinese slices keep the byte-identical legacy behavior.
    """
    text = span_text.strip()
    for prefix in _FIRST_PERSON_PREFIXES:
        if text.startswith(prefix):
            return ("用户" + text[len(prefix):]).strip()
    if "用户" not in text:
        if not _has_cjk(text):
            return text  # English: verbatim, no subject rewrite
        text = "用户" + text
    return text.strip()


def _stated_normalize_no_subject_prepend(span_text: str) -> str:
    """Third-party stated anchor: first-person replacement ONLY.

    The proposition's subject is the third-party entity itself ("小王是女生"),
    so no "用户" subject is prepended — the entity name anchors the subject.
    English slices are already verbatim under the English contract, so this is
    the identity for them.
    """
    text = span_text.strip()
    for prefix in _FIRST_PERSON_PREFIXES:
        if text.startswith(prefix):
            return ("用户" + text[len(prefix):]).strip()
    return text.strip()


#: Leading discourse markers stripped from a confirmed assistant claim.
_CONFIRM_LEADING = ("那么", "所以", "对了", "那", "嗯", "哦", "诶", "呃", "哎")
#: Trailing question/confirmation particles stripped from a confirmed claim.
_CONFIRM_TRAILING_PARTICLES = ("吧", "吗", "呢", "啊", "呀", "哦", "呗", "嘛")
_CONFIRM_TRAILING_PUNCTUATION = "？！。，, !?~～、"
#: A claim that is really a question cannot anchor a confirmed proposition.
_INTERROGATIVE_MARKERS = (
    "怎么样", "什么", "哪个", "哪种", "为什么", "怎么", "多少", "几点",
)


def _confirm_normalize(claim: str) -> str:
    """Deterministic proposition derivation from one verbatim assistant claim.

    Strip leading discourse markers and trailing particles/punctuation, then
    replace second-person address with "用户".  The model is told this exact
    rule and the compiler enforces equality, so a confirmed proposition can
    never be a model paraphrase of the assistant's proposal.
    """
    text = claim.strip()
    while True:
        stripped = False
        for marker in _CONFIRM_LEADING:
            if text.startswith(marker):
                text = text[len(marker):].lstrip()
                stripped = True
                break
        if not stripped:
            break
    while text:
        if text[-1] in _CONFIRM_TRAILING_PUNCTUATION:
            text = text[:-1].rstrip()
            continue
        stripped = False
        for particle in _CONFIRM_TRAILING_PARTICLES:
            if text.endswith(particle):
                text = text[:-len(particle)].rstrip()
                stripped = True
                break
        if not stripped:
            break
    text = text.replace("您", "用户").replace("妳", "用户").replace("你", "用户").strip()
    # English claims keep their wording (verbatim contract): the user's
    # confirmation anchors the assistant's exact claim, minus question tails
    # already stripped above — no pronoun rewriting for English.
    return text


def _confirm_claim_is_proposition(claim: str) -> bool:
    """Reject assistant claims that are questions, not proposals."""
    normalized = _confirm_normalize(claim)
    if not normalized:
        return False
    return not any(marker in normalized for marker in _INTERROGATIVE_MARKERS)


# ── deterministic confirmation-span predicate ─────────────────────────────

_AFFIRM_RE = re.compile(
    r"^(?:对|对的|对呀|对哦|对啊|是|是的|是呀|嗯|嗯嗯|好|好的|好呀|好啊|好嘞|"
    r"行|行呀|行啊|可以|可以呀|没问题|没错|不错|就是|必须|必须的|"
    r"right|yes|yeah|yep|yup|correct|exactly|indeed|true|sure|ok|okay|"
    r"confirm|confirmed)$",
    re.IGNORECASE,
)

#: CJK negation markers (substring scan).  "没错"/"不错"/"没问题" are
#: affirmatives and are removed before the scan.  Single-char markers 不/没 are
#: intentionally kept: the predicate errs fail-closed.
_NEGATION_CJK = ("不对", "不是", "才怪", "错了", "不", "没")
_NEGATION_EN_RE = re.compile(
    r"\b(?:no|not|never|nope|wrong|don't|doesn't|isn't|aren't|wasn't)\b",
    re.IGNORECASE,
)

_SPAN_PUNCTUATION = re.compile(r"[\s，。！？!?,.:;；、~～「」『』]+")


def _is_confirmation_span(span_text: str, assistant_claim: str) -> bool:
    """Deterministic predicate: is this user slice a bare confirmation?

    A bare confirmation is short, negation-free, and either a pure affirmative
    token or shares a two-character run with the assistant's claim (e.g.
    "对，就是G6" shares "G6" with "那你开的是小鹏G6吧？").  Assistant guessing
    right is never Evidence: the slice is still the user's own words.
    """
    stripped = _SPAN_PUNCTUATION.sub("", span_text)
    if not stripped or len(stripped) > CONFIRMATION_SPAN_MAX_CHARS:
        return False
    if _AFFIRM_RE.match(stripped):
        return True
    cleaned = stripped.replace("没错", "").replace("不错", "").replace("没问题", "")
    for marker in _NEGATION_CJK:
        if marker in cleaned:
            return False
    if _NEGATION_EN_RE.search(cleaned):
        return False
    bigrams = {stripped[i:i + 2] for i in range(len(stripped) - 1)}
    return any(bigram in assistant_claim for bigram in bigrams)


# ── compiled batch model ───────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class BatchItem:
    """One compiler-verified item: a form or a typed corrects."""

    action: str  # "form" | "correct"
    proposition: str
    statement_kind: str
    formed_by: str  # "stated" | "confirmed"
    # (evidence_id, start, end, exact_slice_text) — all verified by the compiler.
    supports: tuple[tuple[str, int, int, str], ...]
    corrects_cognition_id: Optional[str] = None
    assistant_claim: Optional[str] = None
    #: naming items: the entity this item names/affirms.  Also set on V4
    #: third-party attribute items (the proposition's subject entity).
    entity_canonical_name: Optional[str] = None
    entity_kind: Optional[str] = None
    #: relationship items: relation type + target entity canonical name.
    relation_type: Optional[str] = None
    target_canonical_name: Optional[str] = None
    target_entity_kind: Optional[str] = None
    #: V4 third-party↔third-party relationships: source endpoint entity.
    source_canonical_name: Optional[str] = None
    source_entity_kind: Optional[str] = None
    #: V5 alias items: explicit equivalence between two existing entity names.
    #: The apply side resolves which side is canonical by earlier formation.
    alias_of_canonical_name: Optional[str] = None
    alias_of_kind: Optional[str] = None
    #: V5 relationship 改口替换 (corrects): the prior relationship id.
    corrects_relationship_id: Optional[str] = None
    #: V6 retract: a correct with NO replacement (Owner decision 2026-08-16:
    #: retract = correct special case).  Prior id rides the corrects_* field.
    retract: bool = False
    #: V6 contradict: same-ID contradictory Evidence against one current
    #: attribute/preference cognition (downgrade only, never invalidates).
    contradicts_cognition_id: Optional[str] = None
    #: V7 event items: (canonical_name, kind) pairs for participants/objects.
    #: "用户" resolves to the owner entity at apply time.
    event_participants: tuple[tuple[str, str], ...] = ()
    event_objects: tuple[tuple[str, str], ...] = ()
    #: V7 event time facets: resolved ISO date and the verbatim time phrase
    #: from the proposition (both optional; Owner decision: time may be empty).
    event_occurred_at: Optional[str] = None
    event_time_expression: Optional[str] = None
    #: V7 event retract target (retract = correct special case, prior object id).
    corrects_event_id: Optional[str] = None
    #: Temporal superseding: this new cognition supersedes a prior cognition.
    supersedes_cognition_id: Optional[str] = None
    #: Temporal superseding: this new relationship supersedes a prior relationship.
    supersedes_relationship_id: Optional[str] = None
    #: V5 third-party perspective holder (targeted attribute/preference only):
    #: the entity holding this view.  None == owner_self.
    perspective_holder_name: Optional[str] = None
    perspective_holder_kind: Optional[str] = None

    @property
    def content_type(self) -> str:
        return self.statement_kind

    def cognition_id(self, subject_id: str) -> str:
        # Same deterministic derivation as V1 ("memoweft_cognition_v1") so a
        # V1-formed cognition restated through V3 keeps the same ID and takes
        # the same-ID support path instead of duplicating.  V5 third-party
        # perspective holders take the v2 derivation (holder in the identity).
        if self.perspective_holder_name is not None:
            return cognition_id_for_holder(
                subject_id,
                self.statement_kind,
                self.proposition,
                entity_id_for(subject_id, self.perspective_holder_name),
            )
        return "cognition-" + _hash_text(
            _canonical(
                [
                    "memoweft_cognition_v1",
                    subject_id,
                    self.statement_kind,
                    self.proposition,
                ]
            )
        )

    def apply_identity(self, subject_id: str) -> str:
        """Deterministic per-item apply identity (dup-detection anchor)."""
        if self.retract:
            prior = (
                self.corrects_cognition_id
                or self.corrects_relationship_id
                or self.corrects_event_id
            )
            assert prior is not None
            return "retract-" + str(prior)
        if self.contradicts_cognition_id is not None:
            return "contradict-" + str(self.contradicts_cognition_id)
        if self.statement_kind == "event":
            return world_event_id_for(subject_id, self.proposition)
        if self.statement_kind == "naming":
            assert self.entity_canonical_name is not None
            return entity_id_for(subject_id, self.entity_canonical_name)
        if self.statement_kind == "relationship":
            assert self.relation_type is not None
            assert self.target_canonical_name is not None
            source_id = (
                entity_id_for(subject_id, self.source_canonical_name)
                if self.source_canonical_name is not None
                else owner_entity_id_for(subject_id)
            )
            return relationship_id_for(
                subject_id,
                source_id,
                self.relation_type,
                entity_id_for(subject_id, self.target_canonical_name),
            )
        if self.statement_kind == "alias":
            assert self.entity_canonical_name is not None
            assert self.alias_of_canonical_name is not None
            left = entity_id_for(subject_id, self.entity_canonical_name)
            right = entity_id_for(subject_id, self.alias_of_canonical_name)
            first, second = sorted((left, right))
            return "alias-merge-" + _hash_text(
                _canonical(["alias_merge", first, second])
            )
        return self.cognition_id(subject_id)

    def confidence_for(self, support_count: int) -> int:
        base = FORMED_BY_BASES[self.formed_by]
        bonus = min(max(support_count - 1, 0), SUPPORT_CAP) * SUPPORT_STEP
        return min(base + bonus, CONFIDENCE_HARD_MAX)

    def cred_status_for(self, confidence: int) -> str:
        for label, floor in CRED_THRESHOLDS:
            if confidence >= floor:
                return label
        return "candidate"


@dataclass(frozen=True, slots=True)
class _CompiledBatch:
    items: tuple[BatchItem, ...]
    #: True when the envelope was the legacy V1 shape: single form, stated,
    #: and the legacy world-outcome shape (kept for V1 replay compatibility).
    legacy: bool = False
    #: The model envelope's own schema_version (1..5) for outcome stamping.
    envelope_version: int = INTERPRETATION_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class _CompiledOutcome:
    """Compile result: a batch to apply, or a zero-write AUTHORITY §3 terminal."""

    batch: Optional[_CompiledBatch]
    reason: str
    terminal: Literal["no_change", "clarification_required", "out_of_scope"] = "no_change"
    display: Optional[str] = None


@dataclass(frozen=True, slots=True)
class TrustCommandApplyResult:
    """Formal Apply result returned inside a caller-owned command transaction."""

    wrote_any: bool
    results: tuple[dict[str, object], ...]
    transition_ids: tuple[str, ...]


class TrustCommandApplyError(RuntimeError):
    """Stable compiler/Apply refusal for a direct Trust command."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ── model contract ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "你是 MemoWeft 2.0 的批量解释器。思考过程务必保持极简（不超过100字），禁止长篇大论，直接输出单个合法的 JSON 对象。输入包含：当前正式 World 的 cognition 列表"
    "（id/content/statement_kind）、current_entities 列表（id/canonical_name/kind/"
    "aliases）、current_relationships 列表（id/content/relation_type/"
    "source_entity_id/target_entity_id）、current_events 列表（id/content/"
    "occurred_at/time_expression）、同会话此前的 conversation_context（只用于消解指代，"
    "不是 Evidence）、一个压缩边界内的若干条用户原话 Evidence"
    "（每条有唯一 id 和原文 text，可能附带 assistant preceding context）。\n"
    "你的任务是只输出一个 JSON 对象：要么描述 1 到 5 条稳定认知，要么 no_change，"
    "要么 clarification_required（身份/含义无法唯一解析时，附 question），要么 "
    "out_of_scope（理解但超出正式合同时，附 note）。"
    "**只输出 JSON，不要任何解释、不要思考过程、不要多余文字。**\n"
    '{"schema_version":8,"result":"no_change"}\n'
    '{"schema_version":8,"result":"cognitions","cognitions":[<item>, ...]}'
    "（1..5 个 item）\n"
    '{"schema_version":8,"result":"clarification_required","question":"…"}\n'
    '{"schema_version":8,"result":"out_of_scope","note":"…"}\n'
    "item 形状（statement_kind 决定额外字段）：\n"
    '{"action":"form","target":"owner_self",'
    '"statement_kind":"attribute"|"preference"|"naming"|"relationship"|"alias"|'
    '"event","formed_by":"stated"|"confirmed","proposition":"…",'
    '"supports":[{"evidence_id":"...","segment_id":"s0"}],'
    '"corrects_cognition_id":"...","assistant_claim":"...",'
    '"entity":{"canonical_name":"…","kind":"person"},"entity_reference":{"mention":"他"},'
    '"perspective_holder":{"canonical_name":"…","kind":"person"},'
    '"alias_of":{"canonical_name":"…","kind":"person"},'
    '"target_entity":{"canonical_name":"…","kind":"person"},'
    '"source_entity":{"canonical_name":"…","kind":"person"},'
    '"relation_type":"girlfriend","corrects_relationship_id":"...",'
    '"retract":true,"contradicts_cognition_id":"...",'
    '"participants":[{"canonical_name":"…","kind":"person"}],'
    '"objects":[{"canonical_name":"…","kind":"place"}],'
    '"occurred_at":"2026-08-15","time_expression":"昨天",'
    '"corrects_event_id":"..."}\n'
    "规则：\n"
    "1. 只处理：用户本人稳定属性/偏好；第三方稳定属性或偏好（一次性/情景内容不算）；"
    "用户对第三方的命名；用户↔第三方或第三方↔"
    "第三方的稳定关系；用户明确说两个名字**同指一人**的显式等价（如\"杨杨就是"
    "小杨\"）。**对任何人的评价（\"打游戏很厉害\"之类）一律不产出**；除已发生/"
    "已确定事件外的一次性事实、含糊内容一律不产出。逐条审阅 Evidence，凡是明确的都要产出（每边界"
    "最多 5 条；作息/通勤/日程等稳定习惯也算用户稳定属性或偏好，可以产出；愿望/"
    "期待（\"想拥有…\"\"希望能…\"）与情绪、观点不产出。带明确短期范围的临时状态"
    "（如\"这周不想社交\"）也不形成永久 attribute/preference，只保留原始 Evidence）。\n"
    "2. supports 的 evidence_id 必须来自输入，并优先选择该 Evidence 给出的 segment_id；"
    "系统从segment原文确定性计算Unicode start/end与stated proposition。旧quote/start/end"
    "仅兼容，不要自行复制、概括或计算。assistant/context永远不是Evidence。\n"
    "3. action=form：形成或复述（复述自动并入同 ID support 链）；只有用户明确说旧"
    "记忆错了并给出新值才用 correct（attribute/preference 用 corrects_cognition_id；"
    "relationship 改口替换用 corrects_relationship_id）。明确撤回（无新值，见第 13 条）"
    "用 correct+retract；只是不同意/怀疑同一命题（见第 14 条）用 contradict。"
    "**看到\"其实…不是…是…\"\"记错了\"\"改一下\"\"不算数\"\"猜错啦\"\"不对\"等改口纠错信号，"
    "或用户表明状态改变（如脱单/在一起了而旧记忆记录单身/没有女朋友），必须使用 "
    "correct 处理旧记忆并更新为新认知，绝不能放任冲突旧记忆并存**。\n"
    "4. formed_by=stated：使用segment_id时，proposition只是必填占位，系统会从所选"
    "segment逐字派生正式命题并完成第一人称归一；模型不要复制、概括或计算命题。"
    "结构字段中的姓名必须能在所选segment或规则10允许的唯一指代上下文中核对。"
    "姓名、称呼、数字、日期必须保留用户原话写法，不得改写或猜测；无法核对就不产出。quote同样逐字派生命题，start/end输入按逐字锚定校验。\n"
    "5. formed_by=confirmed：仅当 assistant 提出命题、用户短确认（无否定词）时用于 "
    "attribute/preference。assistant_claim 必须是 context 的逐字子串；proposition "
    "等于 claim 去语气词/问尾、把\"你/您\"换\"用户\"。assistant 猜对本身不是 Evidence。\n"
    "6. naming：命题如\"用户的朋友叫小王\"（stated）；entity.canonical_name 必须是某"
    "条切片的逐字子串；kind 默认 person。同一实体名已存在就别重复 naming。"
    "**「我的X叫Y」句式：X 是亲密关系词（女朋友/男朋友/老婆/老公/对象）→ 发 "
    "relationship（见第 7 条）；X 是描述词（最好的朋友/同学/同事/室友…）→ 发 "
    "naming。「取名叫X」一律 naming**。\n"
    "7. relationship：命题如\"用户的女朋友是小王\"或\"小王是小杨的女朋友\"（stated，"
    "按用户原话措辞 + 第 4 条归一化；\"我女朋友叫小李\"\"我的女朋友是小李\"= 主语"
    "是用户 → 省略 source_entity）；relation_type 开放（girlfriend/"
    "friend/colleague…）；target_entity.canonical_name 必须逐字在命题里。主语是"
    "用户时**省略** source_entity；主语是第三方时 source_entity.canonical_name "
    "必须逐字在命题里。两端不能同名。**同一 source 已有同类型关系也不拦：用户"
    "怎么说就如实 form 并存（只有明确改口才用 correct，见第 8 条）**。\n"
    "8. relationship 改口替换（correct）：仅当用户明确纠正既有关系并给出新关系（如"
    "\"其实小王不是小杨的女朋友，是小李的女朋友\"）时用 action=correct，"
    "corrects_relationship_id 取 current_relationships 里对应**旧**关系的 id，"
    "**原样逐字复制那个 id 字符串**；relation_type/target_entity/source_entity "
    "描述**新**关系，规则同第 7 条；formed_by 只允许 stated。**proposition 必须"
    "逐字等于支持切片原句**（可含\"不是/其实\"等改口措辞，不要提炼成\"小王是小李"
    "的女朋友\"这类改写句）。**如果用 form 另存新关系，旧关系会仍然存在、两条"
    "冲突并存——这是错误状态，必须用 correct**。\n"
    "9. alias（显式等价）：仅当用户明确说两个名字是同一个人（如\"杨杨就是小杨\"）"
    "且**两个名字都在 current_entities 里**时产出。entity 与 alias_of 各给一个名字"
    "（谁在前无意义，系统自动按更早形成的名字定 canonical）；两个名字都必须逐字在"
    "命题和某条切片里；formed_by 只允许 stated。仅名字相似、简称、猜测指代都不是 "
    "alias。\n"
    "10. 第三方属性/稳定偏好：statement_kind=attribute/preference 且带 entity（如命题\"小王是女生\"，"
    "entity.canonical_name=\"小王\" 必须逐字在切片和命题里；proposition 必须等于切片"
    "本身，只有第一人称才换\"用户\"，**不要**给第三方命题补\"用户\"主语）。不带 entity "
    "的 attribute/preference 才是用户本人。当前segment只用代词时：若同一Evidence前段"
    "只有一个同批naming人物，系统可直接绑定；否则给entity_reference.mention，它必须"
    "逐字在当前segment，且entity必须由同会话先前用户原话唯一指向；歧义时不得猜。\n"
    "11. 同一边界内：命题互异；corrects/contradicts 目标互异；两个 naming 不得同名；"
    "不确定/歧义/锁不定 → 不产出该 item；实在无法唯一解析身份/含义时整批 "
    "clarification_required。\n"
    "12. JSON 卫生：不需要的可选字段（corrects_cognition_id、corrects_relationship_id、"
    "corrects_event_id、contradicts_cognition_id、retract、assistant_claim、entity、"
    "alias_of、entity_reference、source_entity、target_entity、relation_type、participants、objects、"
    "occurred_at、time_expression、perspective_holder）必须**完全省略**，绝不输出"
    "空串/null。target 固定 \"owner_self\"（可省略，缺失视为 owner_self；任何其它值"
    "整体拒绝）。\n"
    "13. retract（明确撤回、无新值）：用户明确说一条已形成的记忆是错的并要撤回/"
    "删除/别记（如\"那条删掉吧\"\"别记我喜欢咖啡了\"\"小王不是小杨的女朋友\"且"
    "**不给新值**）→ action=correct + retract:true + corrects_cognition_id（或关系"
    "用 corrects_relationship_id，取 current_relationships 旧关系 id）。proposition "
    "必须逐字等于撤回原话切片；**不要**补\"用户\"主语。不得带任何新值字段；"
    "naming 不可撤回（名字纠错走 alias）。如果用户明确说先前记在本人名下的属性/"
    "偏好其实属于朋友或其他人、不是本人，而新的第三方内容不符合本合同的正式形成"
    "范围，也必须 retract 对应的本人 cognition；保留整条归属纠正原话作为撤回依据，"
    "不得让错误的本人 cognition 继续 current，也不得把它只当普通 contradict。\n"
    "14. contradict（不同意/怀疑，但没说\"错了\"）：仅当用户对一条**已形成**的 "
    "attribute/preference 表达相反意见/怀疑，且**没有**说记忆错了、也没给新值时，"
    "用 action=contradict + contradicts_cognition_id（current_cognitions 里对应 id）；"
    "proposition 逐字等于反对原话切片、不要补\"用户\"主语。关系/第三方属性不支持 "
    "contradict（关系否定按第 13 条 retract 处理）。分不清 correct/retract/"
    "contradict → 不产出该 item。\n"
    "14b. 时序更替与偏好变迁（如\"那是之前喜欢的，现在我喜欢的是张小姐\"\"以前住北京，现在搬到上海了\"）："
    "用户表达过去的偏好或状态已成为历史、并确立了新偏好时，属于自然演进，绝不要用 correct 将旧记忆完全抹杀/判错。"
    "对新偏好用 action=form + supersedes_cognition_id（取 current_cognitions 里旧项对应 id）；"
    "系统会自动为旧项挂载反证降权保留历史留痕，并将新项建立为当前高置信偏好。\n"
"15. event（已发生/已确定事件，V7）：用户亲述的已发生事件（含叙事/时间/"
    "参与者/对象）用 statement_kind=event（stated）。participants 列参与事件的"
    "第三方实体（名字逐字在命题里，kind 默认 person；\"用户\"可指本人），objects "
    "列参与的对象/地点实体（kind 如 place/thing）。名字要在命题里；没有明确实体"
    "就省略对应列表。时间：仅原话明确包含唯一完整日期时，occurred_at 用 YYYY-MM-DD；"
    "把命题里的时间短语逐字放进 time_expression，不把昨天/上周等相对时间猜成日期。"
    "没有明确日期就省略 occurred_at（事件仍形成）。未发生/不确定是否发生/含糊"
    "内容不产出。**proposition 必须逐字等于支持切片原句——含\"上周末/昨天\"等时间"
    "词在内，一个字都不许增删改写（如切片是\"上周末我和小王去了南京\"，proposition"
    "就必须原样是它）**。event 撤回（说\"没这事/别记了\"）用 correct+retract+"
    "corrects_event_id；event 改口替换（明确说旧事件记错了并给新叙事，如\"其实"
    "上周末没去南京，去的是杭州\"）用 correct+corrects_event_id（取 **current_"
    "events** 里对应旧事件的 id，原样逐字复制那个 id 字符串）+ 新事件的 "
    "participants/objects/occurred_at/time_expression（同 event 字段合同，"
    "proposition=新叙事逐字切片）；event 不"
    "支持 contradict。**用 form 另存新事件会让旧事件仍然存在、两条并存——错误"
    "状态，必须用 correct**。\n"
    "16. perspective holder（第三方视角，V8）：仅当用户转述**第三方对第三方/对"
    "用户**的身份类稳定属性/偏好（如\"小王说小李是00后\"\"小王说我不爱运动\"）时，"
    "用 attribute/preference + entity=被陈述对象（名字逐字在命题）+ "
    "perspective_holder=说话人（第三方名，逐字在命题；**不得**是\"用户\"；"
    "说话人=被陈述对象也不行）。用户自己说的稳定属性/偏好**不要**带 "
    "perspective_holder（那才是 owner_self）。第三方视角的**评价**（\"小王说小李"
    "很厉害\"）仍一律不产出。\n"
    "17. clarification_required：用户明显在陈述需要记住的内容，但身份或含义无法"
    "唯一解析（锁不定）且不产出任何 item 时，整批输出 clarification_required，"
    "question 是一句话中文澄清问题（≤100 字，只问最关键的歧义）。out_of_scope："
    "理解了内容但超出当前正式合同（不属于可归属的稳定属性/偏好/命名/关系/事件）"
    "且用户明显要求记忆时，整批输出 out_of_scope，note 是一句话中文说明（≤100 "
    "字）。两者都零 World 写入；无关的闲聊/情绪/观点/评价仍用 no_change，不要"
    "滥用这两个结果。\n"
    "示例：\n"
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"form","target":"owner_self","statement_kind":"naming",'
    '"formed_by":"stated","proposition":"用户的朋友叫小王",'
    '"entity":{"canonical_name":"小王","kind":"person"},'
    '"supports":[{"evidence_id":"ev1","segment_id":"s0"}]}]}\n'
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"form","target":"owner_self","statement_kind":"relationship",'
    '"formed_by":"stated","proposition":"用户的女朋友叫小李",'
    '"target_entity":{"canonical_name":"小李","kind":"person"},'
    '"relation_type":"girlfriend",'
    '"supports":[{"evidence_id":"ev2","segment_id":"s0"}]}]}'
    "（用户说\"我女朋友叫小李\"\"我的女朋友是小李\"都是这种关系：无 source_entity）\n"
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"correct","target":"owner_self","statement_kind":"relationship",'
    '"formed_by":"stated","proposition":"其实小王不是小杨的女朋友，是小李的女朋友",'
    '"corrects_relationship_id":"<current_relationships 里的旧关系 id>",'
    '"source_entity":{"canonical_name":"小王","kind":"person"},'
    '"target_entity":{"canonical_name":"小李","kind":"person"},'
    '"relation_type":"girlfriend",'
    '"supports":[{"evidence_id":"ev3","start":0,"end":20}]}]}'
    "（改口替换必须 action=correct + 旧关系 id，**绝不**用 form 另存）\n"
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"correct","target":"owner_self","statement_kind":"event",'
    '"formed_by":"stated","proposition":"其实上周末我和小王去的是杭州",'
    '"corrects_event_id":"<current_relationships 同理，world 事件列表里的旧事件 id>",'
    '"participants":[{"canonical_name":"小王","kind":"person"}],'
    '"objects":[{"canonical_name":"杭州","kind":"place"}],'
    '"supports":[{"evidence_id":"ev4","start":0,"end":14}]}]}'
    "（事件改口同样必须 action=correct + 旧事件 id，**绝不**用 form 另存）\n"
)

#: English equivalent of rules 1-17 (Owner-approved §4.12 localization, option B).
#: Semantically equivalent to _SYSTEM_PROMPT; the worked examples are the same
#: cases in English with correctly recomputed codepoint spans.
_SYSTEM_PROMPT_EN = (
    "You are MemoWeft 2.0's batch interpreter. The input contains: the current "
    "formal World's cognition list (id/content/statement_kind), a "
    "current_entities list (id/canonical_name/kind/aliases), a "
    "current_relationships list (id/content/relation_type/source_entity_id/"
    "target_entity_id), a current_events list (id/content/occurred_at/"
    "time_expression), prior same-conversation conversation_context (reference "
    "resolution only, never Evidence), and several verbatim user Evidence utterances from one "
    "compression boundary (each has a unique id and original text, possibly "
    "with assistant preceding context).\n"
    "Your task is to output exactly one JSON object: either 1 to 5 stable "
    "cognitions, or no_change, or clarification_required (when identity or "
    "meaning cannot be uniquely resolved, with a question), or out_of_scope "
    "(understood but outside the formal contract, with a note). "
    "**Output ONLY JSON — no explanations, no chain of thought, no extra "
    "text.**\n"
    '{"schema_version":8,"result":"no_change"}\n'
    '{"schema_version":8,"result":"cognitions","cognitions":[<item>, ...]}'
    " (1..5 items)\n"
    '{"schema_version":8,"result":"clarification_required","question":"…"}\n'
    '{"schema_version":8,"result":"out_of_scope","note":"…"}\n'
    "Item shape (statement_kind decides the extra fields):\n"
    '{"action":"form","target":"owner_self",'
    '"statement_kind":"attribute"|"preference"|"naming"|"relationship"|"alias"|'
    '"event","formed_by":"stated"|"confirmed","proposition":"…",'
    '"supports":[{"evidence_id":"...","segment_id":"s0"}],'
    '"corrects_cognition_id":"...","assistant_claim":"...",'
    '"entity":{"canonical_name":"…","kind":"person"},"entity_reference":{"mention":"they"},'
    '"perspective_holder":{"canonical_name":"…","kind":"person"},'
    '"alias_of":{"canonical_name":"…","kind":"person"},'
    '"target_entity":{"canonical_name":"…","kind":"person"},'
    '"source_entity":{"canonical_name":"…","kind":"person"},'
    '"relation_type":"girlfriend","corrects_relationship_id":"...",'
    '"retract":true,"contradicts_cognition_id":"...",'
    '"participants":[{"canonical_name":"…","kind":"person"}],'
    '"objects":[{"canonical_name":"…","kind":"place"}],'
    '"occurred_at":"2026-08-15","time_expression":"yesterday",'
    '"corrects_event_id":"..."}\n'
    "Rules:\n"
    "1. Only process: the user's own stable attributes/preferences; stable "
    "third-party attributes or preferences (one-off or situational content does "
    "not count); the user's "
    "naming of third parties; stable relationships between the user and a "
    "third party or between third parties; explicit equivalence of two names "
    "for one person stated by the user (e.g. \"Yangyang is Xiaoyang\"). "
    "**Never produce evaluations of anyone (\"plays games really well\" and "
    "the like)**; one-off facts other than happened/confirmed events and vague "
    "content are never produced. "
    "Review the Evidence one by one; produce everything that is definite (at "
    "most 5 per boundary; routines/commutes/schedules and other stable habits "
    "count as stable attributes or preferences and may be produced; wishes/"
    "expectations (\"want to own…\" \"hope to…\"), emotions and opinions are "
    "not produced. A temporary state with an explicit short time scope (for "
    "example, \"I do not want to socialize this week\") must not become a "
    "permanent attribute/preference; retain only its raw Evidence). **Do NOT "
    "split a single claim into fragments — a reason "
    "clause (\"because…\", \"so…\") stays inside its item; two INDEPENDENT "
    "claims in one utterance are separate items, each proposition equal to "
    "its own verbatim slice.**\n"
    "2. supports.evidence_id must come from the input and should select that "
    "Evidence's supplied segment_id. The system derives the verbatim text, Unicode "
    "start/end and stated proposition. Legacy quote/start/end is compatibility only; "
    "do not copy, paraphrase or count text. Assistant/context is never Evidence.\n"
    "3. action=form: form or restate (a restatement auto-merges into the same "
    "ID's support chain); use correct ONLY when the user explicitly says an "
    "old memory is wrong and gives a new value (attribute/preference use "
    "corrects_cognition_id; relationship replacement uses "
    "corrects_relationship_id). An explicit retraction with no new value (see "
    "rule 13) uses correct+retract; mere disagreement or doubt about the same "
    "proposition (see rule 14) uses contradict. **When you see explicit "
    "correction signals such as \"actually… not… is…\", \"I misremembered\", "
    "\"change it\", \"that doesn't count\", you MUST correct/retract the old "
    "memory — never form a second copy with form**.\n"
    "4. formed_by=stated: with segment_id, proposition is only a required placeholder; "
    "the system derives the formal proposition verbatim from the selected segment "
    "and performs owner-pronoun normalization. Do not copy, summarize or calculate it. "
    "Names in structural fields must be verifiable in that segment or through rule "
    "10's unique reference context. Preserve names, forms of address, numbers and dates exactly as written in user evidence; omit unverifiable facts. Quotes also derive verbatim propositions; legacy start/end inputs retain verbatim checks.\n"
    "5. formed_by=confirmed: only for attribute/preference when the assistant "
    "proposed a proposition and the user confirmed it briefly (no negation). "
    "assistant_claim must be a verbatim substring of the context; the "
    "proposition equals the claim with discourse particles and question tails "
    "removed (no pronoun rewriting). The "
    "assistant guessing right is never Evidence by itself.\n"
    "6. naming: propositions like \"My friend is called Wang\" "
    "(stated); entity.canonical_name must be a verbatim substring of a slice; "
    "kind defaults to person. Do not repeat naming when the same entity name "
    "already exists. **\"My X is called Y\" sentences: when X is an intimate "
    "relation word (girlfriend/boyfriend/wife/husband/partner) → emit "
    "relationship (see rule 7); when X is a descriptor (best friend/"
    "classmate/colleague/roommate…) → emit naming. \"Named X\" is always "
    "naming. A sentence like \"X is the kitten/dog I <got/bought> from…, "
    "because…, so I named it X\" is ONE pet naming (kind=animal) whose "
    "proposition equals the ENTIRE sentence — never split it into fragments "
    "and never emit a relationship for a pet.**\n"
    "7. relationship: propositions like \"My girlfriend is called Li\" or "
    "\"Wang is Yang's girlfriend\" (stated, the user's own wording; "
    "\"my girlfriend is called Li\" = the subject is "
    "the user → omit source_entity); relation_type is open (girlfriend/friend/"
    "colleague…); target_entity.canonical_name must be verbatim in the "
    "proposition. When the subject is the user, **omit** source_entity; when "
    "the subject is a third party, source_entity.canonical_name must be "
    "verbatim in the proposition. The two endpoints must not be the same "
    "name. **An existing relationship of the same type from the same source "
    "does not block: record exactly what the user said and let them coexist "
    "(only an explicit correction uses correct, see rule 8)**.\n"
    "8. relationship replacement (correct): ONLY when the user explicitly "
    "corrects an existing relationship and gives a new one (e.g. \"Actually "
    "Wang is not Yang's girlfriend, she is Li's girlfriend\") use "
    "action=correct with corrects_relationship_id copied verbatim from the "
    "**old** relationship's id in current_relationships; relation_type/"
    "target_entity/source_entity describe the **new** relationship per rule 7; "
    "formed_by only stated. **The proposition must equal the supporting slice "
    "verbatim** (it may contain \"not/actually\" correction wording — do not "
    "distill it into a rewritten sentence). **Using form for the new "
    "relationship would leave the old one current — two conflicting rows — "
    "which is a wrong state; you MUST use correct**.\n"
    "9. alias (explicit equivalence): ONLY when the user explicitly says two "
    "names are the same person (e.g. \"Yangyang is Xiaoyang\") and **both "
    "names are in current_entities**. Give one name in entity and the other "
    "in alias_of (order does not matter; the system canonicalizes to the "
    "earlier-formed name); both names must be verbatim in the proposition and "
    "in a slice; formed_by only stated. Mere similarity, abbreviations or "
    "guessed references are NOT alias.\n"
    "10. Third-party attributes/stable preferences: statement_kind=attribute/"
    "preference with "
    "entity (e.g. proposition \"Wang is a girl\", entity.canonical_name="
    "\"Wang\" verbatim in the slice and proposition; the proposition must "
    "equal the slice itself, with no subject rewriting). "
    "An attribute/preference without entity is the user's own. When the current "
    "segment uses only a pronoun, the system may bind it directly when an earlier "
    "segment in the same Evidence has exactly one same-batch naming entity. Otherwise "
    "add entity_reference.mention verbatim from the segment; entity must be uniquely "
    "grounded by prior user turns in the same conversation, otherwise do not guess.\n"
    "11. Within one boundary: propositions are mutually distinct; corrects/"
    "contradicts targets are mutually distinct; two namings must not share a "
    "name; uncertain/ambiguous/unlockable → do not produce that item; if "
    "identity or meaning truly cannot be resolved, emit a whole-batch "
    "clarification_required.\n"
    "12. JSON hygiene: optional fields that are not needed "
    "(corrects_cognition_id, corrects_relationship_id, corrects_event_id, "
    "contradicts_cognition_id, retract, assistant_claim, entity, alias_of, entity_reference, "
    "source_entity, target_entity, relation_type, participants, objects, "
    "occurred_at, time_expression, perspective_holder) MUST be **fully "
    "omitted** — never empty strings or null. target is fixed \"owner_self\" "
    "(omittable; missing means owner_self; any other value rejects the whole "
    "batch).\n"
    "13. retract (explicit retraction, no new value): when the user explicitly "
    "says a formed memory is wrong and to retract/delete/forget it (e.g. "
    "\"delete that one\", \"stop remembering that I like coffee\", \"Wang is "
    "not Yang's girlfriend\" with **no new value**) → action=correct + "
    "retract:true + corrects_cognition_id (or corrects_relationship_id from "
    "current_relationships for relationships). The proposition must equal the "
    "retraction slice verbatim; "
    "carry no new-value fields; naming cannot be retracted (name fixes go "
    "through alias). If the user explicitly says that an attribute/preference "
    "previously assigned to the owner actually belongs to a friend or another "
    "person and not the owner, but that third-party content is outside this "
    "contract's formal formation scope, retract the corresponding owner "
    "cognition anyway. Preserve the complete attribution correction as the "
    "retraction Evidence; never leave the false owner cognition current or "
    "reduce this correction to a mere contradiction.\n"
    "14. contradict (disagree/doubt, without saying \"wrong\"): ONLY when the "
    "user expresses an opposite opinion or doubt about an **already formed** "
    "attribute/preference and does NOT say the memory is wrong or give a new "
    "value, use action=contradict + contradicts_cognition_id (the "
    "corresponding id in current_cognitions); the proposition equals the "
    "opposing slice verbatim. **The disagreeing statement is ONLY a "
    "contradict anchor — never emit it as a new form/cognition, and never "
    "correct or retract the memory it disagrees with.** "
    "Relationships and third-party attributes do not support contradict "
    "(relationship negation goes through rule 13 retract). When correct/"
    "retract/contradict cannot be told apart → do not produce that item.\n"
    "15. event (happened/confirmed events, V7): user-stated events that "
    "happened (with narrative/time/participants/objects) use "
    "statement_kind=event (stated). participants lists the third-party "
    "entities involved (names verbatim in the proposition, kind defaults to "
    "person; \"The user\" may stand for the user themselves), objects lists "
    "the objects/places involved (kind e.g. place/thing). Names must be in "
    "the proposition; omit a list when there are no definite entities. Time: "
    "occurred_at uses YYYY-MM-DD only when user evidence contains one explicit full date. "
    "Keep the time phrase verbatim in time_expression; never guess a date from yesterday "
    "or last week. Omit occurred_at if no full date is explicit (the event still forms). Not-yet-happened/uncertain/"
    "vague content is not produced. **The proposition must equal the "
    "supporting slice verbatim — including \"last weekend/yesterday\" time "
    "words, not a single character added, deleted or rewritten** (if the "
    "slice is \"Last weekend I went to Nanjing with Wang\", the proposition "
    "must be exactly that). Event retraction (\"that never happened/forget "
    "it\") uses correct+retract+corrects_event_id; event replacement (the old "
    "event was wrong and a new narrative is given, e.g. \"Actually last "
    "weekend I did not go to Nanjing — I went to Hangzhou\") uses "
    "correct+corrects_event_id (the **old** event's id from current_events, "
    "copied verbatim) + the new event's participants/objects/occurred_at/"
    "time_expression (same event field contract, proposition = the new "
    "narrative verbatim slice); events do not support contradict. **Using "
    "form for a replacement would leave the old event current — two "
    "conflicting rows — which is a wrong state; you MUST use correct**.\n"
    "16. perspective holder (third-party perspective, V8): ONLY when the user "
    "relays a **third party's** identity-class stable attribute/preference "
    "about another third party or about the user (e.g. \"Wang says Li is "
    "Gen Z\" \"Wang says I don't like sports\"), use attribute/preference + "
    "entity = the person being described (name verbatim in the proposition) "
    "+ perspective_holder = the speaker (third-party name, verbatim in the "
    "proposition; **not** \"The user\"; the speaker must not equal the "
    "described person). The user's own stated attributes/preferences must "
    "**not** carry perspective_holder (those are owner_self). Third-party "
    "perspective **evaluations** (\"Wang says Li is amazing\") are still "
    "never produced.\n"
    "17. clarification_required: when the user is clearly stating something "
    "to remember but the identity or meaning cannot be uniquely resolved "
    "(unlockable) and you produce no item, emit a whole-batch "
    "clarification_required; question is a one-sentence clarification in the "
    "conversation language (<=100 chars, ask only the most critical "
    "ambiguity). out_of_scope: when you understood the content but it is "
    "outside the current formal contract (not an attributable stable "
    "attribute/preference/naming/relationship/event) and the user clearly "
    "asked to remember it, emit a whole-batch out_of_scope; note is a "
    "one-sentence explanation (<=100 chars). Both are zero World writes; "
    "unrelated chitchat/emotions/opinions/evaluations still use no_change — "
    "do not overuse these two results.\n"
    "Examples:\n"
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"form","target":"owner_self","statement_kind":"naming",'
    '"formed_by":"stated","proposition":"My friend is called Wang",'
    '"entity":{"canonical_name":"Wang","kind":"person"},'
    '"supports":[{"evidence_id":"ev1","segment_id":"s0"}]}]}\n'
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"form","target":"owner_self","statement_kind":"relationship",'
    '"formed_by":"stated","proposition":"My girlfriend is called Li",'
    '"target_entity":{"canonical_name":"Li","kind":"person"},'
    '"relation_type":"girlfriend",'
    '"supports":[{"evidence_id":"ev2","segment_id":"s0"}]}]}'
    " (both \"my girlfriend is called Li\" and \"my girlfriend is Li\" are "
    "this relationship: no source_entity)\n"
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"correct","target":"owner_self","statement_kind":"relationship",'
    '"formed_by":"stated","proposition":"Actually Wang is not Yang\'s girlfriend, she is Li\'s girlfriend",'
    '"corrects_relationship_id":"<the old relationship id from current_relationships>",'
    '"source_entity":{"canonical_name":"Wang","kind":"person"},'
    '"target_entity":{"canonical_name":"Li","kind":"person"},'
    '"relation_type":"girlfriend",'
    '"supports":[{"evidence_id":"ev3","start":0,"end":62}]}]}'
    " (replacement MUST use action=correct + the old relationship id, **never** form)\n"
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"correct","target":"owner_self","statement_kind":"event",'
    '"formed_by":"stated","proposition":"Actually last weekend I went to Hangzhou, not Nanjing",'
    '"corrects_event_id":"<likewise the old event id from the world event list>",'
    '"participants":[],'
    '"objects":[{"canonical_name":"Hangzhou","kind":"place"}],'
    '"supports":[{"evidence_id":"ev4","start":0,"end":53}]}]}'
    " (event replacement likewise MUST use action=correct + the old event id, **never** form)\n"
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"form","target":"owner_self","statement_kind":"naming",'
    '"formed_by":"stated","proposition":"Erwu is the kitten I bought from someone back in my hometown, because her birthday is February 5th, so I named her Erwu",'
    '"entity":{"canonical_name":"Erwu","kind":"animal"},'
    '"supports":[{"evidence_id":"ev5","start":0,"end":119}]}]}'
    " (one long pet sentence = ONE naming with the full sentence as the "
    "proposition; never split it and never emit a relationship for a pet)\n"
    '{"schema_version":8,"result":"cognitions","cognitions":['
    '{"action":"contradict","target":"owner_self","statement_kind":"preference",'
    '"formed_by":"stated","proposition":"Coffee actually doesn\'t taste good",'
    '"contradicts_cognition_id":"<the existing cognition id from current_cognitions>",'
    '"supports":[{"evidence_id":"ev6","start":0,"end":34}]}]}'
    " (disagreement = contradict only; the disagreeing words never become a "
    "new memory and the old memory stays current)\n"
)


def _user_payload(
    current_cognitions: list[dict[str, object]],
    current_entities: list[dict[str, object]],
    current_relationships: list[dict[str, object]],
    current_events: list[dict[str, object]],
    evidence: list[dict[str, object]],
    conversation_context: list[dict[str, str]] | None = None,
) -> str:
    return _canonical(
        {
            "subject": "owner",
            "current_cognitions": current_cognitions,
            "current_entities": current_entities,
            "current_relationships": current_relationships,
            "current_events": current_events,
            "evidence": evidence,
            "conversation_context": conversation_context or [],
        }
    )


def _boundary_language(
    db: sqlite3.Connection, job: ClaimedWorldJob, explicit: Optional[str]
) -> str:
    """Deterministic prompt-language choice for one boundary.

    An explicit "zh"/"en" wins; otherwise the boundary's Evidence decides by
    majority script: CJK characters vs ASCII letters (tie → zh, the legacy
    default).  Zero-config for the Hermes chain; hosts that know the user's
    language may pin it explicitly.
    """
    if explicit in ("zh", "en"):
        return explicit
    cjk = 0
    latin = 0
    for evidence_id in job.evidence_ids():
        row = db.execute(
            "SELECT raw_content FROM evidence WHERE id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            continue
        for ch in str(row[0]):
            code = ord(ch)
            if 0x4E00 <= code <= 0x9FFF:
                cjk += 1
            elif ch.isascii() and ch.isalpha():
                latin += 1
    return "zh" if cjk >= latin else "en"


class HermesBatchAdapterProcessor:
    """V3 formal batch adapter (see module docstring)."""

    dispatches_model = True

    def __init__(
        self,
        db_path: str,
        route: OneShotRoute,
        *,
        clock: Clock = system_clock,
        lang: Optional[str] = None,
        model_tier: ModelTier = "cloud",
    ) -> None:
        if lang not in (None, "zh", "en"):
            raise ValueError("lang must be None, 'zh' or 'en'")
        if model_tier not in ("cloud", "local"):
            raise ValueError("model_tier must be 'cloud' or 'local'")
        self._db_path = db_path
        self._route = route
        self._clock = clock
        self._lang = lang
        self.model_tier = model_tier

    # ── entry point ────────────────────────────────────────────────────────

    def process(self, job: ClaimedWorldJob) -> WorldJobResult:
        db = sqlite3.connect(self._db_path, isolation_level=None)
        try:
            db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            return self._process(job, db)
        finally:
            db.close()

    def apply_trust_command_items_in_transaction(
        self,
        db: sqlite3.Connection,
        job: ClaimedWorldJob,
        items: tuple[BatchItem, ...],
        *,
        now_text: str,
    ) -> TrustCommandApplyResult:
        """Reuse compiler-verified correction/retract Apply without a model call.

        The caller owns ``BEGIN IMMEDIATE`` and the durable command receipt.
        This method deliberately does not commit or advance ``memory_state``;
        it writes the same World rows, provenance ledgers and transitions as
        the World Job path, using the caller's next revision.
        """

        if not db.in_transaction:
            raise TrustCommandApplyError("trust_command_transaction_required")
        batch = _CompiledBatch(items=items)
        try:
            for evidence_id in job.evidence_ids():
                self._validate_evidence_in_transaction(db, job, evidence_id)
            for item in items:
                self._validate_supports_in_transaction(db, job, item)
            self._validate_historical_targets_in_transaction(db, job, batch)
            results: list[dict[str, object]] = []
            pending_cognition_transitions: list[tuple[str, str]] = []
            transition_ids: list[str] = []
            wrote_any = False
            for item in items:
                if item.action != "correct" or item.contradicts_cognition_id is not None:
                    raise _ZeroWriteError("unsupported_trust_command_batch_item")
                if item.retract:
                    result, wrote = self._apply_retract(db, job, item, now_text)
                    prior = (
                        item.corrects_cognition_id
                        or item.corrects_relationship_id
                        or item.corrects_event_id
                    )
                    assert prior is not None
                    transition_ids.append(
                        "retraction-" + _hash_text(_canonical(["retracts", prior]))
                    )
                elif item.statement_kind == "relationship":
                    result, wrote = self._apply_relationship_correct(
                        db, job, item, now_text
                    )
                    transition_ids.append(
                        "evidence-ledger-"
                        + _hash_text(
                            _canonical(
                                [
                                    "relationship_correction",
                                    item.corrects_relationship_id,
                                    result["replacement_relationship_id"],
                                ]
                            )
                        )
                    )
                elif item.statement_kind == "event":
                    result, wrote = self._apply_event_correct(db, job, item, now_text)
                    transition_ids.append(
                        "evidence-ledger-"
                        + _hash_text(
                            _canonical(
                                [
                                    "event_correction",
                                    item.corrects_event_id,
                                    result["replacement_event_id"],
                                ]
                            )
                        )
                    )
                else:
                    result, wrote, transition = self._apply_correct(
                        db, job, item, now_text
                    )
                    if transition is not None:
                        pending_cognition_transitions.append(transition)
                results.append(result)
                wrote_any = wrote_any or wrote
            if wrote_any:
                revision = self._current_revision(db) + 1
                for prior_id, replacement_id in pending_cognition_transitions:
                    self._write_transition(db, prior_id, replacement_id, revision)
                    transition_ids.append(
                        "cognition-transition-"
                        + _hash_text(
                            _canonical(["corrects", prior_id, replacement_id])
                        )
                    )
            return TrustCommandApplyResult(
                wrote_any=wrote_any,
                results=tuple(results),
                transition_ids=tuple(transition_ids if wrote_any else ()),
            )
        except _ZeroWriteError as exc:
            raise TrustCommandApplyError(str(exc)) from exc

    def _check_fast_no_change(
        self, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> str | None:
        evidence_payload = self._evidence_payload(job, db)
        if not evidence_payload:
            return "empty_evidence"
        for ev in evidence_payload:
            raw_content = str(ev.get("text") or "").strip()
            if not raw_content:
                return None
            preceding_ai = str(ev.get("context") or "").strip()
            if preceding_ai and re.match(r"^(?:对|是的|没错|正确|对的|嗯嗯|确实|yes|yeah|yep|correct|right|sure)[!！。.\s]*$", raw_content, re.I):
                return None
            if _has_declarative_facts(raw_content):
                return None
        return "pure_inquiry_no_declarative_facts"

    def _sync_owner_alias(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, proposition: str, now_text: str
    ) -> None:
        match = _OWNER_NAME_PROPOSITION_RE.search(proposition)
        if not match:
            return
        name = next((g for g in match.groups() if g), None)
        if not name or len(name) > 30:
            return
        owner_id, _ = self._ensure_owner_entity(db, job, now_text)
        aliases = self._entity_aliases(db, owner_id)
        if name not in aliases:
            aliases.append(name)
            db.execute(
                "UPDATE entity SET aliases_json = ?, updated_at = ? WHERE id = ?",
                (_canonical(aliases), now_text, owner_id),
            )
            ledger_id = "alias-owner-" + _hash_text(_canonical(["owner_alias", owner_id, name]))
            db.execute(
                """INSERT OR REPLACE INTO evidence_ledger (id, content, payload_json)
                   VALUES (?, ?, ?)""",
                (
                    ledger_id,
                    _canonical(
                        {
                            "relation": "alias",
                            "canonical_entity_id": owner_id,
                            "alias_name": name,
                        }
                    ),
                    _canonical(
                        {
                            "schema_version": 1,
                            "boundary_event_id": job.boundary_event_id,
                            "evidence_ids": list(job.evidence_ids()),
                        }
                    ),
                ),
            )

    def _process(self, job: ClaimedWorldJob, db: sqlite3.Connection) -> WorldJobResult:
        payload = self._load_checkpoint(db, job)
        if payload is None:
            fast_reason = self._check_fast_no_change(job, db)
            if fast_reason is not None:
                return WorldJobResult.no_change(fast_reason)
            payload = self._dispatch_once(job, db)
            self._persist_checkpoint(db, job, payload)

        compiled = self._compile_checked(str(payload.get("content") or ""), job, db)
        if (
            compiled.batch is None
            and compiled.terminal == "no_change"
            and compiled.reason != "model_no_change"
            and "formation_rewrite" not in payload
        ):
            first = dict(payload)
            feedback = {
                "code": compiled.reason,
                "instruction": (
                    "Rewrite the complete interpretation JSON once. Fix the reported "
                    "compiler error; every cognition must include a nonempty proposition "
                    "and valid supports. Copy names, forms of address, numbers and dates "
                    "exactly from user evidence. Never invent facts."
                ),
            }
            # Persist the attempt reservation before dispatch: recovery never
            # repeats a rewrite whose response was lost in a crash.
            payload["formation_rewrite"] = {"state": "reserved", "error": feedback}
            self._persist_checkpoint(db, job, payload)
            try:
                payload = self._dispatch_once(job, db, previous=first, feedback=feedback)
                compiled = self._compile_checked(str(payload.get("content") or ""), job, db)
                payload["formation_rewrite"] = {
                    "state": "completed", "error": feedback, "first_result": first,
                    "final_error": compiled.reason if compiled.batch is None else None,
                }
            except Exception as exc:
                # Do not persist provider exception text (it may contain secrets).
                payload = first
                payload["formation_rewrite"] = {
                    "state": "failed", "error": feedback,
                    "failure_type": type(exc).__name__,
                }
            self._persist_checkpoint(db, job, payload)

        model_kwargs: dict[str, Any] = {
            "model_provider": payload.get("provider"),
            "model_name": payload.get("model"),
            "model_usage": payload.get("usage")
            if isinstance(payload.get("usage"), Mapping)
            else None,
            "model_result": payload,
        }

        batch = compiled.batch
        if batch is None:
            if compiled.terminal == "clarification_required":
                return WorldJobResult.clarification_required(
                    compiled.reason, display=compiled.display, **model_kwargs
                )
            if compiled.terminal == "out_of_scope":
                return WorldJobResult.out_of_scope(
                    compiled.reason, display=compiled.display, **model_kwargs
                )
            return WorldJobResult.no_change(compiled.reason, **model_kwargs)
        try:
            applied, outcome = self._apply_atomically(db, job, batch)
        except _ClarificationError as exc:
            return WorldJobResult.clarification_required(
                str(exc), display=exc.display, **model_kwargs
            )
        except _ZeroWriteError as exc:
            return WorldJobResult.no_change(str(exc), **model_kwargs)
        if not applied or outcome is None:
            # Fence lost: the recovering worker replays the checkpoint and
            # applies deterministically.  This settle attempt must not mutate
            # anything (the fenced UPDATE below matches zero rows).
            return WorldJobResult.dead("world_apply_fence_lost", **model_kwargs)
        if outcome["state"] == "no_change":
            return WorldJobResult.no_change(
                "no_world_mutation", world_result=outcome, **model_kwargs
            )
        return WorldJobResult.applied(
            reason="applied",
            world_result=outcome,
            **model_kwargs,
        )

    # ── checkpoint ─────────────────────────────────────────────────────────

    def _load_checkpoint(
        self, db: sqlite3.Connection, job: ClaimedWorldJob
    ) -> Optional[dict[str, object]]:
        row = db.execute(
            "SELECT model_result_json FROM memory_world_job WHERE job_id = ?",
            (job.job_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        try:
            decoded = json.loads(str(row[0]))
        except (TypeError, ValueError) as exc:
            raise PermanentWorldJobError("invalid_stored_model_result") from exc
        if not isinstance(decoded, dict):
            raise PermanentWorldJobError("invalid_stored_model_result")
        return decoded

    def _persist_checkpoint(
        self,
        db: sqlite3.Connection,
        job: ClaimedWorldJob,
        payload: Mapping[str, object],
    ) -> None:
        content = str(payload.get("content") or "")
        usage = payload.get("usage")
        provider = payload.get("provider")
        model = payload.get("model")
        now_text = to_iso_z(self._clock())
        cursor = db.execute(
            """UPDATE memory_world_job
                  SET model_completed_at = ?,
                      model_provider = ?,
                      model_name = ?,
                      model_usage_json = ?,
                      model_result_json = ?,
                      model_result_hash = ?
                WHERE job_id = ?
                  AND state = 'processing'
                  AND claim_owner = ?
                  AND claim_token = ?
                  AND fencing_generation = ?""",
            (
                now_text,
                str(provider) if provider else None,
                str(model) if model else None,
                _canonical(usage) if isinstance(usage, Mapping) else None,
                _canonical(dict(payload)),
                _hash_text(_canonical(dict(payload))),
                job.job_id,
                job.claim_owner,
                job.claim_token,
                job.fencing_generation,
            ),
        )
        if cursor.rowcount != 1:
            # The live fence is gone; recovery dead-letters or replays the
            # claim without ever re-calling the model.
            raise PermanentWorldJobError("checkpoint_fence_lost")

    # ── one-shot dispatch ──────────────────────────────────────────────────

    def _dispatch_once(
        self, job: ClaimedWorldJob, db: sqlite3.Connection, *,
        previous: Optional[Mapping[str, object]] = None,
        feedback: Optional[Mapping[str, object]] = None,
    ) -> dict[str, object]:
        evidence = self._evidence_payload(job, db)
        current = self._current_cognitions_payload(job, db)
        entities = self._current_entities_payload(job, db)
        relationships = self._current_relationships_payload(job, db)
        events = self._current_events_payload(job, db)
        prompt = (
            _SYSTEM_PROMPT
            if _boundary_language(db, job, self._lang) == "zh"
            else _SYSTEM_PROMPT_EN
        )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": _user_payload(
                    current, entities, relationships, events, evidence,
                    self._conversation_context_payload(job, db),
                ),
            },
        ]
        if previous is not None:
            messages.extend([
                {"role": "assistant", "content": str(previous.get("content") or "")},
                {"role": "user", "content": _canonical({
                    "compiler_error": feedback,
                    "source_evidence": evidence,
                })},
            ])
        # The documented OneShotRoute contract makes ``session_id``
        # keyword-only (see the Hermes ``one_shot_llm`` initialize kwarg).
        try:
            out = self._route(messages, session_id=job.parent_session_id)
        except TypeError:
            try:
                out = self._route(messages, job.parent_session_id)
            except TypeError:
                out = self._route(messages)
        if not isinstance(out, Mapping):
            raise PermanentWorldJobError("one_shot_route_invalid_result")
        return dict(out)

    def _evidence_payload(
        self, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> list[dict[str, object]]:
        ids = job.evidence_ids()
        blocks: list[dict[str, object]] = []
        for evidence_id in ids:
            row = db.execute(
                "SELECT raw_content, preceding_ai_context, subject_id, host_id, source_kind, "
                "deleted_at, allow_local_read, allow_cloud_read, allow_inference "
                "FROM evidence WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            if row is None:
                raise PermanentWorldJobError("evidence_missing")
            state = evidence_state(
                {
                    "deleted_at": row[5],
                    "allow_local_read": row[6],
                    "allow_cloud_read": row[7],
                    "allow_inference": row[8],
                },
                surface="formation",
                model_tier=self.model_tier,
            )
            if state is not None:
                raise PermanentWorldJobError(f"{state}_before_dispatch")
            if str(row[2]) != job.subject_id or str(row[3]) != job.host_id:
                raise PermanentWorldJobError("evidence_target_changed_before_dispatch")
            if str(row[4]) != "spoken":
                raise PermanentWorldJobError("evidence_source_kind_changed_before_dispatch")
            block: dict[str, object] = {
                "id": evidence_id,
                "text": str(row[0]),
                "segments": _evidence_segments(str(row[0])),
            }
            if row[1] is not None:
                block["context"] = str(row[1])
            blocks.append(block)
        return blocks

    def _current_cognitions_payload(
        self, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> list[dict[str, object]]:
        rows = db.execute(
            "SELECT c.id, c.content, c.content_type, t.target_entity_id, "
            "t.perspective_entity_id FROM cognition c "
            "LEFT JOIN cognition_target t ON t.cognition_id = c.id "
            "WHERE c.subject_id = ? "
            "AND c.invalid_at IS NULL AND c.archived_at IS NULL AND c.muted_at IS NULL "
            "ORDER BY c.created_at, c.id",
            (job.subject_id,),
        ).fetchall()
        payload: list[dict[str, object]] = []
        for r in rows:
            cognition_id = str(r[0])
            if not world_item_visible(
                db,
                job.subject_id,
                "cognition",
                cognition_id,
                surface="formation",
                model_tier=self.model_tier,
            ):
                continue
            entry: dict[str, object] = {
                "id": cognition_id,
                "content": str(r[1]),
                "statement_kind": str(r[2]),
            }
            if r[3] is not None:
                entry["target_entity_id"] = str(r[3])
            if r[4] is not None:
                entry["perspective_entity_id"] = str(r[4])
            payload.append(entry)
        return payload

    def _conversation_context_entity_names(
        self, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> set[str]:
        """Current entity names explicitly present in prior user turns."""

        prior_turns = self._conversation_context_payload(job, db)
        prior_user_text = "\n".join(
            turn["content"] for turn in prior_turns if turn["role"] == "user"
        )
        mentions = self._current_entity_mentions(job, db)
        return {canonical for mention, canonical in mentions.items() if mention in prior_user_text}

    def _current_entity_mentions(
        self, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> dict[str, str]:
        mentions: dict[str, str] = {}
        for entity in self._current_entities_payload(job, db):
            canonical = entity.get("canonical_name")
            if not isinstance(canonical, str):
                continue
            mentions[canonical] = canonical
            aliases = entity.get("aliases")
            if not isinstance(aliases, list):
                continue
            for alias in aliases:
                if isinstance(alias, str):
                    mentions[alias] = canonical
        return mentions

    def _conversation_context_payload(
        self, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> list[dict[str, str]]:
        evidence_ids = job.evidence_ids()
        if not evidence_ids:
            return []
        placeholders = ",".join("?" for _ in evidence_ids)
        cutoff = db.execute(
            f"SELECT MIN(recorded_at) FROM evidence WHERE subject_id = ? AND id IN ({placeholders})",
            (job.subject_id, *evidence_ids),
        ).fetchone()
        if cutoff is None or cutoff[0] is None:
            return []
        rows = db.execute(
            "SELECT episode_id, context_json FROM interaction_context WHERE subject_id = ? "
            "AND conversation_id = ? AND created_at < ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 4",
            (job.subject_id, job.parent_session_id, str(cutoff[0])),
        ).fetchall()
        turns: list[dict[str, str]] = []
        for row in reversed(rows):
            job_row = db.execute(
                "SELECT evidence_ids_json FROM memory_world_job WHERE boundary_event_id = ? "
                "AND subject_id = ?",
                (str(row[0]), job.subject_id),
            ).fetchone()
            try:
                evidence_ids = json.loads(str(job_row[0])) if job_row is not None else []
            except (TypeError, ValueError):
                continue
            if not isinstance(evidence_ids, list) or not evidence_ids:
                continue
            permitted = True
            for evidence_id in evidence_ids:
                evidence_row = db.execute(
                    "SELECT deleted_at, allow_local_read, allow_cloud_read, allow_inference "
                    "FROM evidence WHERE id = ? AND subject_id = ?",
                    (evidence_id, job.subject_id),
                ).fetchone()
                if evidence_row is None or evidence_state(
                    {
                        "deleted_at": evidence_row[0],
                        "allow_local_read": evidence_row[1],
                        "allow_cloud_read": evidence_row[2],
                        "allow_inference": evidence_row[3],
                    },
                    surface="formation",
                    model_tier=self.model_tier,
                ) is not None:
                    permitted = False
                    break
            if not permitted:
                continue
            try:
                decoded = json.loads(str(row[1]))
            except (TypeError, ValueError):
                continue
            if not isinstance(decoded, list):
                continue
            for turn in decoded:
                if (
                    isinstance(turn, dict)
                    and turn.get("role") in {"user", "assistant"}
                    and isinstance(turn.get("content"), str)
                ):
                    turns.append(
                        {"role": str(turn["role"]), "content": str(turn["content"])}
                    )
        return turns[-8:]

    def _current_entities_payload(
        self, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> list[dict[str, object]]:
        rows = db.execute(
            "SELECT id, canonical_name, kind, aliases_json FROM entity "
            "WHERE world_id = ? AND invalid_at IS NULL "
            "ORDER BY created_at, id",
            (job.subject_id,),
        ).fetchall()
        entity_ids = {
            str(r[0])
            for r in rows
            if world_item_visible(
                db,
                job.subject_id,
                "entity",
                str(r[0]),
                surface="formation",
                model_tier=self.model_tier,
            )
        }
        for cognition in self._current_cognitions_payload(job, db):
            for key in ("target_entity_id", "perspective_entity_id"):
                value = cognition.get(key)
                if isinstance(value, str):
                    entity_ids.add(value)
        for relationship in self._current_relationships_payload(job, db):
            entity_ids.add(str(relationship["source_entity_id"]))
            entity_ids.add(str(relationship["target_entity_id"]))
        for event in self._current_events_payload(job, db):
            for key in ("participants", "objects"):
                values = event.get(key)
                if isinstance(values, list):
                    entity_ids.update(value for value in values if isinstance(value, str))
        payload: list[dict[str, object]] = []
        for r in rows:
            if str(r[0]) not in entity_ids:
                continue
            aliases: list[str] = []
            try:
                decoded = json.loads(str(r[3]) or "[]")
                if isinstance(decoded, list):
                    permitted = set(
                        current_entity_aliases(
                            db,
                            job.subject_id,
                            str(r[0]),
                            surface="formation",
                            model_tier=self.model_tier,
                        )
                    )
                    aliases = [str(x) for x in decoded if str(x) in permitted]
            except (TypeError, ValueError):
                aliases = []
            payload.append(
                {
                    "id": str(r[0]),
                    "canonical_name": str(r[1]),
                    "kind": str(r[2]),
                    "aliases": aliases,
                }
            )
        return payload

    def _current_relationships_payload(
        self, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> list[dict[str, object]]:
        rows = db.execute(
            "SELECT id, content, relation_type, source_entity_id, target_entity_id "
            "FROM relationship "
            "WHERE world_id = ? AND invalid_at IS NULL "
            "ORDER BY created_at, id",
            (job.subject_id,),
        ).fetchall()
        return [
            {
                "id": str(r[0]),
                "content": str(r[1]),
                "relation_type": str(r[2]),
                "source_entity_id": str(r[3]),
                "target_entity_id": str(r[4]),
            }
            for r in rows
            if world_item_visible(
                db,
                job.subject_id,
                "relationship",
                str(r[0]),
                surface="formation",
                model_tier=self.model_tier,
            )
        ]

    def _current_events_payload(
        self, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> list[dict[str, object]]:
        rows = db.execute(
            "SELECT id, content, occurred_at, time_expression, participants_json, objects_json "
            "FROM world_event "
            "WHERE world_id = ? AND invalid_at IS NULL "
            "ORDER BY created_at, id",
            (job.subject_id,),
        ).fetchall()
        payload: list[dict[str, object]] = []
        for r in rows:
            event_id = str(r[0])
            if not world_item_visible(
                db,
                job.subject_id,
                "event",
                event_id,
                surface="formation",
                model_tier=self.model_tier,
            ):
                continue
            def entity_ids(value: object) -> list[str]:
                try:
                    decoded = json.loads(str(value))
                except (TypeError, ValueError):
                    return []
                return [entry for entry in decoded if isinstance(entry, str)] if isinstance(decoded, list) else []

            payload.append(
                {
                    "id": event_id,
                    "content": str(r[1]),
                    "occurred_at": None if r[2] is None else str(r[2]),
                    "time_expression": None if r[3] is None else str(r[3]),
                    "participants": entity_ids(r[4]),
                    "objects": entity_ids(r[5]),
                }
            )
        return payload

    # ── deterministic compile ──────────────────────────────────────────────

    def _compile_checked(
        self, content: str, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> _CompiledOutcome:
        try:
            return self._compile(content, job, db)
        except _ZeroWriteError as exc:
            return _CompiledOutcome(None, str(exc))

    def _compile(
        self, content: str, job: ClaimedWorldJob, db: sqlite3.Connection
    ) -> _CompiledOutcome:
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            return _CompiledOutcome(None, "invalid_model_result")
        if not isinstance(data, dict):
            return _CompiledOutcome(None, "invalid_model_result")
        schema_version = data.get("schema_version")
        if schema_version == LEGACY_INTERPRETATION_SCHEMA_VERSION:
            batch, reason = self._compile_legacy(data, job, db)
            return _CompiledOutcome(batch, reason)
        if schema_version == LEGACY_V2_INTERPRETATION_SCHEMA_VERSION:
            batch, reason = self._compile_batch(data, job, db, extended_kinds=False)
            return _CompiledOutcome(batch, reason)
        if schema_version == LEGACY_V3_INTERPRETATION_SCHEMA_VERSION:
            batch, reason = self._compile_batch(
                data, job, db, extended_kinds=True, third_party=False
            )
            return _CompiledOutcome(batch, reason)
        if schema_version == LEGACY_V4_INTERPRETATION_SCHEMA_VERSION:
            batch, reason = self._compile_batch(
                data, job, db, extended_kinds=True, third_party=True
            )
            return _CompiledOutcome(batch, reason)
        if schema_version == LEGACY_V5_INTERPRETATION_SCHEMA_VERSION:
            batch, reason = self._compile_batch(
                data, job, db, extended_kinds=True, third_party=True,
                alias_support=True,
            )
            return _CompiledOutcome(batch, reason)
        if schema_version == LEGACY_V6_INTERPRETATION_SCHEMA_VERSION:
            batch, reason = self._compile_batch(
                data, job, db, extended_kinds=True, third_party=True,
                alias_support=True, retract_support=True,
                contradict_support=True,
            )
            return _CompiledOutcome(batch, reason)
        if schema_version == LEGACY_V7_INTERPRETATION_SCHEMA_VERSION:
            batch, reason = self._compile_batch(
                data, job, db, extended_kinds=True, third_party=True,
                alias_support=True, retract_support=True,
                contradict_support=True, event_support=True,
            )
            return _CompiledOutcome(batch, reason)
        if schema_version != INTERPRETATION_SCHEMA_VERSION:
            return _CompiledOutcome(None, "invalid_model_result")
        # Current v8 envelope: the model may also propose the two AUTHORITY §3
        # zero-write terminals (clarification_required / out_of_scope).
        result = data.get("result")
        if result == "clarification_required":
            return _CompiledOutcome(
                None,
                "model_clarification_required",
                terminal="clarification_required",
                display=_model_display(data.get("question")),
            )
        if result == "out_of_scope":
            return _CompiledOutcome(
                None,
                "model_out_of_scope",
                terminal="out_of_scope",
                display=_model_display(data.get("note")),
            )
        batch, reason = self._compile_batch(
            data, job, db, extended_kinds=True, third_party=True,
            alias_support=True, retract_support=True, contradict_support=True,
            event_support=True, perspective_support=True,
        )
        return _CompiledOutcome(batch, reason)

    def _compile_legacy(
        self,
        data: dict[str, object],
        job: ClaimedWorldJob,
        db: sqlite3.Connection,
    ) -> tuple[Optional[_CompiledBatch], str]:
        """V1 single stated form, grounded in its original supporting slice."""
        result = data.get("result")
        if result == "no_change":
            return None, "model_no_change"
        if result != "one_cognition":
            return None, "invalid_model_result"
        cog = data.get("cognition")
        if not isinstance(cog, dict):
            return None, "invalid_model_result"
        if cog.get("target") != "owner_self":
            return None, "unsupported_target"
        kind = cog.get("statement_kind")
        if kind not in SUPPORTED_STATEMENT_KINDS:
            return None, "unsupported_statement_kind"
        proposition = cog.get("proposition")
        if not isinstance(proposition, str) or not proposition.strip():
            return None, "invalid_proposition"
        ids = set(job.evidence_ids())
        raw_by_id: dict[str, str] = {}
        for evidence_id in ids:
            row = db.execute(
                "SELECT raw_content FROM evidence WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            if row is not None:
                raw_by_id[evidence_id] = str(row[0])
        parsed, reason = _parse_supports(cog.get("supports"), ids, raw_by_id)
        if parsed is None:
            return None, reason
        if any(support[3] == "" for support in parsed):
            return None, "span_out_of_range"
        supersedes = cog.get("supersedes_cognition_id")
        supersedes_id: Optional[str] = (
            str(supersedes).strip()
            if isinstance(supersedes, str) and str(supersedes).strip()
            else None
        )
        item = BatchItem(
            action="form",
            proposition=_stated_normalize(parsed[0][3]),
            statement_kind=str(kind),
            formed_by="stated",
            supports=parsed,
            supersedes_cognition_id=supersedes_id,
        )
        return (
            _CompiledBatch(
                items=(item,),
                legacy=True,
                envelope_version=LEGACY_INTERPRETATION_SCHEMA_VERSION,
            ),
            "",
        )

    def _compile_batch(
        self,
        data: dict[str, object],
        job: ClaimedWorldJob,
        db: sqlite3.Connection,
        *,
        extended_kinds: bool,
        third_party: bool = False,
        alias_support: bool = False,
        retract_support: bool = False,
        contradict_support: bool = False,
        event_support: bool = False,
        perspective_support: bool = False,
    ) -> tuple[Optional[_CompiledBatch], str]:
        result = data.get("result")
        if result == "no_change":
            return None, "model_no_change"
        if result != "cognitions":
            return None, "invalid_model_result"
        raw_items = data.get("cognitions")
        if not isinstance(raw_items, list) or not raw_items:
            return None, "invalid_cognition_batch"
        if len(raw_items) > MAX_COGNITIONS_PER_BATCH:
            return None, "too_many_cognitions"
        ids = set(job.evidence_ids())
        raw_by_id: dict[str, str] = {}
        context_by_id: dict[str, Optional[str]] = {}
        for evidence_id in ids:
            row = db.execute(
                "SELECT raw_content, preceding_ai_context FROM evidence WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            if row is not None:
                raw_by_id[evidence_id] = str(row[0])
                context_by_id[evidence_id] = (
                    None if row[1] is None else str(row[1])
                )
        items: list[BatchItem] = []
        context_entity_names = self._conversation_context_entity_names(job, db)
        current_entity_mentions = self._current_entity_mentions(job, db)
        batch_entity_names: set[str] = set()
        for candidate_item in raw_items:
            if not isinstance(candidate_item, dict) or candidate_item.get("statement_kind") != "naming":
                continue
            entity = candidate_item.get("entity")
            if not isinstance(entity, dict) or not isinstance(entity.get("canonical_name"), str):
                continue
            candidate_supports, _reason = _parse_supports(candidate_item.get("supports"), ids, raw_by_id)
            if candidate_supports is None:
                continue
            name = str(entity["canonical_name"]).strip()
            batch_entity_names.add(
                _normalize_spoken_name(name, [support[3] for support in candidate_supports])
            )
        for raw_item in raw_items:
            item, reason = self._parse_item(
                raw_item, ids, raw_by_id, context_by_id,
                extended_kinds=extended_kinds, third_party=third_party,
                alias_support=alias_support,
                retract_support=retract_support,
                contradict_support=contradict_support,
                event_support=event_support,
                perspective_support=perspective_support,
                context_entity_names=context_entity_names,
                batch_entity_names=batch_entity_names,
                current_entity_mentions=current_entity_mentions,
            )
            if item is None:
                return None, reason
            items.append(item)

        identities = [item.apply_identity(job.subject_id) for item in items]
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if identities[i] == identities[j]:
                    if (
                        items[i].statement_kind == "naming"
                        and items[j].statement_kind == "naming"
                    ):
                        return None, "duplicate_entity_in_batch"
                    return None, "duplicate_cognition_in_batch"
        correction_targets = [
            str(item.corrects_cognition_id)
            for item in items
            if item.corrects_cognition_id is not None
        ]
        if len(set(correction_targets)) != len(correction_targets):
            return None, "ambiguous_correction_target"
        relationship_correction_targets = [
            str(item.corrects_relationship_id)
            for item in items
            if item.corrects_relationship_id is not None
        ]
        if len(set(relationship_correction_targets)) != len(
            relationship_correction_targets
        ):
            return None, "ambiguous_correction_target"
        contradict_targets = [
            str(item.contradicts_cognition_id)
            for item in items
            if item.contradicts_cognition_id is not None
        ]
        if len(set(contradict_targets)) != len(contradict_targets):
            return None, "ambiguous_contradiction_target"
        event_correction_targets = [
            str(item.corrects_event_id)
            for item in items
            if item.corrects_event_id is not None
        ]
        if len(set(event_correction_targets)) != len(event_correction_targets):
            return None, "ambiguous_correction_target"
        for item in items:
            if item.corrects_cognition_id is not None and (
                item.corrects_cognition_id
                == item.cognition_id(job.subject_id)
            ):
                return None, "correction_target_is_itself"
            if item.corrects_relationship_id is not None and (
                item.corrects_relationship_id
                == item.apply_identity(job.subject_id)
            ):
                return None, "correction_target_is_itself"
            if item.corrects_event_id is not None and (
                item.corrects_event_id == item.apply_identity(job.subject_id)
            ):
                return None, "correction_target_is_itself"
        envelope_version = (
            LEGACY_V2_INTERPRETATION_SCHEMA_VERSION
            if not extended_kinds
            else (
                LEGACY_V3_INTERPRETATION_SCHEMA_VERSION
                if not third_party
                else (
                    LEGACY_V4_INTERPRETATION_SCHEMA_VERSION
                    if not alias_support
                    else (
                        LEGACY_V5_INTERPRETATION_SCHEMA_VERSION
                        if not retract_support or not contradict_support
                        else (
                            LEGACY_V6_INTERPRETATION_SCHEMA_VERSION
                            if not event_support
                            else (
                                LEGACY_V7_INTERPRETATION_SCHEMA_VERSION
                                if not perspective_support
                                else INTERPRETATION_SCHEMA_VERSION
                            )
                        )
                    )
                )
            )
        )
        return (
            _CompiledBatch(
                items=tuple(items),
                envelope_version=envelope_version,
            ),
            "",
        )

    @staticmethod
    def _parse_event_entities(
        raw: object,
        proposition: str,
        slices: list[str],
        *,
        drop_unverified: bool = False,
        cross_source_texts: tuple[str, ...] = (),
    ) -> Optional[tuple[tuple[str, str], ...]]:
        """Parse a V7 event participants/objects list: each entry is an entity
        dict whose canonical_name is verbatim in the proposition AND appears in
        at least one support slice.  Absent list == empty tuple."""
        if raw is None:
            return ()
        if not isinstance(raw, list):
            return None
        result: list[tuple[str, str]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                return None
            name = entry.get("canonical_name")
            if not isinstance(name, str) or not name.strip():
                return None
            name = name.strip()
            kind = entry.get("kind") or "person"
            if not isinstance(kind, str) or not kind.strip():
                return None
            kind = str(kind).strip()
            # The owner marker needs no verbatim mention: the owner is
            # inherent to the world (owner_self perspective) — "用户" is
            # accepted even when the narrative only said "我".
            if name != OWNER_ENTITY_NAME:
                if name not in proposition:
                    if any(name in text for text in cross_source_texts):
                        return None
                    if drop_unverified:
                        continue
                    return None
                if not any(name in slice_text for slice_text in slices):
                    if any(name in text for text in cross_source_texts):
                        return None
                    if drop_unverified:
                        continue
                    return None
            result.append((name, kind))
        return tuple(result)

    def _parse_item(
        self,
        raw_item: object,
        ids: set[str],
        raw_by_id: dict[str, str],
        context_by_id: dict[str, Optional[str]],
        *,
        extended_kinds: bool = False,
        third_party: bool = False,
        alias_support: bool = False,
        retract_support: bool = False,
        contradict_support: bool = False,
        event_support: bool = False,
        perspective_support: bool = False,
        context_entity_names: set[str] | None = None,
        batch_entity_names: set[str] | None = None,
        current_entity_mentions: Mapping[str, str] | None = None,
    ) -> tuple[Optional[BatchItem], str]:
        if not isinstance(raw_item, dict):
            return None, "invalid_cognition_item"
        action = raw_item.get("action")
        if action not in ("form", "correct", "contradict"):
            return None, "invalid_cognition_action"
        if action == "contradict" and not contradict_support:
            return None, "invalid_cognition_action"
        # perspective is 一律 owner_self (Owner decision 2026-08-16 V3): the
        # target field adds no information, so an absent/empty target reads as
        # owner_self (observed live: the real model omits it on alias items).
        # Any explicit non-owner_self value is still rejected.
        target = raw_item.get("target") or "owner_self"
        if target != "owner_self":
            return None, "unsupported_target"
        kind = raw_item.get("statement_kind")
        allowed_kinds = (
            SUPPORTED_STATEMENT_KINDS if extended_kinds else LEGACY_V2_STATEMENT_KINDS
        )
        if kind not in allowed_kinds:
            return None, "unsupported_statement_kind"
        if kind == "alias" and not alias_support:
            return None, "unsupported_statement_kind"
        if kind == "event" and not event_support:
            return None, "unsupported_statement_kind"
        if raw_item.get("alias_of") is not None and kind != "alias":
            return None, "unexpected_alias_of"
        #: V5 perspective holder hygiene: only targeted attribute/preference
        #: form or correct items may carry it (v8 envelope only).
        if (
            raw_item.get("perspective_holder") is not None
            and not (
                perspective_support
                and action in ("form", "correct")
                and kind in ("attribute", "preference")
                and raw_item.get("entity") is not None
            )
        ):
            return None, "unexpected_perspective_holder"
        #: V6 retract flag: correct with NO replacement.  Only boolean ``true``
        #: (or absent) is accepted; LLM stringified "true" is rejected.
        retract_raw = raw_item.get("retract")
        if retract_raw is not None and not isinstance(retract_raw, bool):
            return None, "invalid_retract"
        retract = bool(retract_raw)
        if retract and not retract_support:
            return None, "invalid_retract"
        if retract and action != "correct":
            return None, "invalid_retract"
        if action == "contradict":
            if kind not in ("attribute", "preference"):
                return None, "unsupported_statement_kind"
            if (raw_item.get("formed_by") or "stated") != "stated":
                return None, "invalid_formed_by"
        if (
            raw_item.get("corrects_relationship_id") is not None
            and not (kind == "relationship" and action == "correct")
        ):
            return None, "unexpected_correction_target"
        if (
            raw_item.get("contradicts_cognition_id") is not None
            and action != "contradict"
        ):
            return None, "unexpected_contradiction_target"
        if (
            raw_item.get("corrects_event_id") is not None
            and not (kind == "event" and action == "correct")
        ):
            return None, "unexpected_correction_target"
        if retract and kind == "naming":
            return None, "unsupported_statement_kind"
        proposition = raw_item.get("proposition")
        if not isinstance(proposition, str) or not proposition.strip():
            return None, "invalid_proposition"
        proposition = proposition.strip()
        formed_by = raw_item.get("formed_by") or "stated"
        if formed_by not in FORMED_BY_BASES:
            return None, "invalid_formed_by"
        supersedes = raw_item.get("supersedes_cognition_id")
        if isinstance(supersedes, str) and not supersedes.strip():
            supersedes = None
        supersedes_cognition_id: Optional[str] = supersedes.strip() if isinstance(supersedes, str) and supersedes.strip() else None
        supersedes_rel = raw_item.get("supersedes_relationship_id")
        if isinstance(supersedes_rel, str) and not supersedes_rel.strip():
            supersedes_rel = None
        supersedes_relationship_id: Optional[str] = supersedes_rel.strip() if isinstance(supersedes_rel, str) and supersedes_rel.strip() else None
        if not supersedes_relationship_id and supersedes_cognition_id and (supersedes_cognition_id.startswith("relationship-") or kind == "relationship"):
            supersedes_relationship_id = supersedes_cognition_id
        corrects = raw_item.get("corrects_cognition_id")
        # LLM JSON hygiene: models habitually emit "" for optional fields.
        # An empty string is semantically absent — normalize it instead of
        # failing the whole batch (observed live: a form item carrying
        # ``corrects_cognition_id: ""`` settled no_change).
        if isinstance(corrects, str) and not corrects.strip():
            corrects = None
        corrects_relationship_id: Optional[str] = None
        contradicts_cognition_id: Optional[str] = None
        corrects_event_id: Optional[str] = None
        if action == "contradict":
            if corrects is not None:
                return None, "unexpected_correction_target"
            if retract:
                return None, "invalid_retract"
            raw_ct = raw_item.get("contradicts_cognition_id")
            if not isinstance(raw_ct, str) or not raw_ct.strip():
                return None, "invalid_contradiction_target"
            contradicts_cognition_id = raw_ct.strip()
        elif kind == "event" and action == "correct":
            # V6: events support both retract (Owner decision V4) and correct
            # with replacement (V6 patch — the correct family's last piece).
            if formed_by == "confirmed":
                return None, "invalid_formed_by"
            if corrects is not None:
                return None, "unexpected_correction_target"
            raw_event_target = raw_item.get("corrects_event_id")
            if (
                not isinstance(raw_event_target, str)
                or not raw_event_target.strip()
            ):
                return None, "invalid_correction_target"
            corrects_event_id = raw_event_target.strip()
        elif kind == "relationship" and action == "correct":
            if not alias_support:
                return None, "invalid_cognition_action"
            if formed_by == "confirmed":
                return None, "invalid_formed_by"
            if corrects is not None:
                return None, "unexpected_correction_target"
            raw_rel_target = raw_item.get("corrects_relationship_id")
            if (
                not isinstance(raw_rel_target, str)
                or not raw_rel_target.strip()
            ):
                return None, "invalid_correction_target"
            corrects_relationship_id = raw_rel_target.strip()
        elif action == "correct":
            if formed_by == "confirmed":
                return None, "invalid_formed_by"
            if not isinstance(corrects, str) or not corrects.strip():
                return None, "invalid_correction_target"
        elif corrects is not None:
            return None, "unexpected_correction_target"
        assistant_claim = raw_item.get("assistant_claim")
        if isinstance(assistant_claim, str) and not assistant_claim.strip():
            assistant_claim = None
        if formed_by == "confirmed":
            if action != "form":
                return None, "invalid_formed_by"
            if not isinstance(assistant_claim, str) or not assistant_claim.strip():
                return None, "missing_assistant_claim"
            claim = assistant_claim
        else:
            if assistant_claim is not None:
                return None, "unexpected_assistant_claim"
            claim = None

        parsed, reason = _parse_supports(
            raw_item.get("supports"), ids, raw_by_id
        )
        if parsed is None:
            return None, reason
        parsed = _repair_spans(parsed, proposition, ids, raw_by_id)
        if any(support[3] == "" for support in parsed):
            # A broken span the relocation fallback could not repair.
            return None, "span_out_of_range"

        slices = [support[3] for support in parsed]
        raw_supports = raw_item.get("supports")
        quote_anchored = isinstance(raw_supports, list) and any(
            isinstance(support, dict)
            and (support.get("quote") is not None or support.get("segment_id") is not None)
            for support in raw_supports
        )
        if quote_anchored and formed_by == "stated":
            quote_without_prepend = (
                kind in {"naming", "alias", "event"}
                or retract
                or action == "contradict"
                or raw_item.get("entity") is not None
                or (kind == "relationship" and raw_item.get("source_entity") is not None)
            )
            normalizer = (
                _stated_normalize_no_subject_prepend
                if quote_without_prepend
                else _stated_normalize
            )
            # The model selects evidence, but cannot rewrite facts. Derive all
            # stated propositions from the selected verbatim slice, including
            # owner preferences (e.g. a requested form of address).
            proposition = normalizer(slices[0])

        entity_canonical_name: Optional[str] = None
        entity_kind: Optional[str] = None
        relation_type: Optional[str] = None
        target_canonical_name: Optional[str] = None
        target_entity_kind: Optional[str] = None
        source_canonical_name: Optional[str] = None
        source_entity_kind: Optional[str] = None
        alias_of_canonical_name: Optional[str] = None
        alias_of_kind: Optional[str] = None
        event_participants: tuple[tuple[str, str], ...] = ()
        event_objects: tuple[tuple[str, str], ...] = ()
        event_occurred_at: Optional[str] = None
        event_time_expression: Optional[str] = None
        perspective_holder_name: Optional[str] = None
        perspective_holder_kind: Optional[str] = None
        targeted_attribute = False
        if kind in ("naming", "relationship"):
            # First-class Entity/Relationship entries (V3/V4 windows): the
            # closed contract is stated-only, and entity names must be
            # verbatim substrings of the user's own words.  V5 additionally
            # allows action=correct on relationships (改口替换).
            if kind == "naming" and action != "form":
                return None, "invalid_cognition_action"
            if formed_by != "stated":
                return None, "invalid_formed_by"
            if kind == "naming":
                entity_raw = raw_item.get("entity")
                if not isinstance(entity_raw, dict):
                    return None, "missing_entity"
                entity_canonical_name = entity_raw.get("canonical_name")
                if (
                    not isinstance(entity_canonical_name, str)
                    or not entity_canonical_name.strip()
                ):
                    return None, "invalid_entity_name"
                entity_canonical_name = entity_canonical_name.strip()
                entity_canonical_name = _normalize_spoken_name(
                    entity_canonical_name, slices
                )
                entity_kind = entity_raw.get("kind") or "person"
                if not isinstance(entity_kind, str) or not entity_kind.strip():
                    return None, "invalid_entity_kind"
                entity_kind = str(entity_kind).strip()
                if not any(
                    entity_canonical_name in slice_text for slice_text in slices
                ):
                    return None, "entity_name_not_in_span"
            else:
                if raw_item.get("entity") is not None:
                    return None, "unexpected_entity"
                if action == "correct" and retract:
                    # V6 relationship retract: the prior row is identified by
                    # corrects_relationship_id alone; new-value fields must be
                    # entirely absent.
                    if (
                        raw_item.get("relation_type") is not None
                        or raw_item.get("target_entity") is not None
                        or raw_item.get("source_entity") is not None
                    ):
                        return None, "unexpected_relation_type"
                else:
                    relation_type = raw_item.get("relation_type")
                    if (
                        not isinstance(relation_type, str)
                        or not relation_type.strip()
                    ):
                        return None, "invalid_relation_type"
                    relation_type = str(relation_type).strip()
                    target_raw = raw_item.get("target_entity")
                    if not isinstance(target_raw, dict):
                        return None, "missing_target_entity"
                    target_canonical_name = target_raw.get("canonical_name")
                    if (
                        not isinstance(target_canonical_name, str)
                        or not target_canonical_name.strip()
                    ):
                        return None, "invalid_entity_name"
                    target_canonical_name = target_canonical_name.strip()
                    target_entity_kind = target_raw.get("kind") or "person"
                    if not isinstance(target_entity_kind, str) or not target_entity_kind.strip():
                        return None, "invalid_entity_kind"
                    target_entity_kind = str(target_entity_kind).strip()
                    if target_canonical_name not in proposition:
                        return None, "entity_name_not_in_proposition"
                    source_raw = raw_item.get("source_entity")
                    if source_raw is not None:
                        # V4: third-party↔third-party relationships (Owner decision
                        # 2026-08-16 B).  Both endpoint names must be verbatim in
                        # the proposition, and the endpoints must be distinct.
                        if not third_party:
                            return None, "unexpected_source_entity"
                        if not isinstance(source_raw, dict):
                            return None, "invalid_source_entity"
                        source_canonical_name = source_raw.get("canonical_name")
                        if (
                            not isinstance(source_canonical_name, str)
                            or not source_canonical_name.strip()
                        ):
                            return None, "invalid_entity_name"
                        source_canonical_name = source_canonical_name.strip()
                        source_entity_kind = source_raw.get("kind") or "person"
                        if not isinstance(source_entity_kind, str) or not source_entity_kind.strip():
                            return None, "invalid_entity_kind"
                        source_entity_kind = str(source_entity_kind).strip()
                        if source_canonical_name not in proposition:
                            return None, "entity_name_not_in_proposition"
                        if source_canonical_name == target_canonical_name:
                            return None, "self_relationship"
        elif kind == "alias":
            # V5: explicit-equivalence alias merge (Owner decision 2026-08-16 A).
            # Both names must be verbatim in the proposition AND each name must
            # appear in at least one support slice; the apply side resolves the
            # canonical by earlier formation, so field order carries no meaning.
            if action != "form" or formed_by != "stated":
                return None, "invalid_formed_by"
            if (
                raw_item.get("target_entity") is not None
                or raw_item.get("source_entity") is not None
                or raw_item.get("relation_type") is not None
            ):
                return None, "unexpected_relation_type"
            entity_raw = raw_item.get("entity")
            alias_raw = raw_item.get("alias_of")
            if not isinstance(entity_raw, dict):
                return None, "missing_entity"
            if not isinstance(alias_raw, dict):
                return None, "missing_alias_of"
            entity_canonical_name = entity_raw.get("canonical_name")
            if (
                not isinstance(entity_canonical_name, str)
                or not entity_canonical_name.strip()
            ):
                return None, "invalid_entity_name"
            entity_canonical_name = entity_canonical_name.strip()
            entity_kind = entity_raw.get("kind") or "person"
            if not isinstance(entity_kind, str) or not entity_kind.strip():
                return None, "invalid_entity_kind"
            entity_kind = str(entity_kind).strip()
            alias_of_canonical_name = alias_raw.get("canonical_name")
            if (
                not isinstance(alias_of_canonical_name, str)
                or not alias_of_canonical_name.strip()
            ):
                return None, "invalid_entity_name"
            alias_of_canonical_name = alias_of_canonical_name.strip()
            alias_of_kind = alias_raw.get("kind") or "person"
            if not isinstance(alias_of_kind, str) or not alias_of_kind.strip():
                return None, "invalid_entity_kind"
            alias_of_kind = str(alias_of_kind).strip()
            if entity_canonical_name == alias_of_canonical_name:
                return None, "alias_target_is_itself"
            if (
                entity_canonical_name not in proposition
                or alias_of_canonical_name not in proposition
            ):
                return None, "entity_name_not_in_proposition"
            if not any(
                entity_canonical_name in slice_text for slice_text in slices
            ):
                return None, "entity_name_not_in_span"
            if not any(
                alias_of_canonical_name in slice_text for slice_text in slices
            ):
                return None, "entity_name_not_in_span"
        elif kind == "event":
            # V7 first-class World Event (Owner decisions 2026-08-16):
            # stated-only narrative; participants/objects bound by entity
            # canonical names (verbatim in the proposition, each name in at
            # least one slice); time facets optional (time may be empty).
            # V6: action=correct (replacement) carries the same value fields.
            if action == "correct" and retract:
                if (
                    raw_item.get("participants") is not None
                    or raw_item.get("objects") is not None
                    or raw_item.get("occurred_at") is not None
                    or raw_item.get("time_expression") is not None
                    or raw_item.get("entity") is not None
                    or raw_item.get("target_entity") is not None
                    or raw_item.get("source_entity") is not None
                    or raw_item.get("relation_type") is not None
                ):
                    return None, "unexpected_event_field"
            else:
                if action not in ("form", "correct") or formed_by != "stated":
                    return None, "invalid_formed_by"
                if (
                    raw_item.get("entity") is not None
                    or raw_item.get("target_entity") is not None
                    or raw_item.get("source_entity") is not None
                    or raw_item.get("relation_type") is not None
                ):
                    return None, "unexpected_relation_type"
                participants_raw = raw_item.get("participants")
                objects_raw = raw_item.get("objects")
                if participants_raw is not None and not isinstance(
                    participants_raw, list
                ):
                    return None, "invalid_event_participants"
                if objects_raw is not None and not isinstance(objects_raw, list):
                    return None, "invalid_event_objects"
                segment_backed = isinstance(raw_supports, list) and any(
                    isinstance(support, dict) and support.get("segment_id") is not None
                    for support in raw_supports
                )
                support_ids = {support[0] for support in parsed}
                cross_source_texts = tuple(
                    text for evidence_id, text in raw_by_id.items()
                    if evidence_id not in support_ids
                )
                parsed_participants = self._parse_event_entities(
                    participants_raw, proposition, slices,
                    drop_unverified=segment_backed,
                    cross_source_texts=cross_source_texts,
                )
                if parsed_participants is None:
                    return None, "invalid_event_participants"
                event_participants = parsed_participants
                parsed_objects = self._parse_event_entities(
                    objects_raw, proposition, slices,
                    drop_unverified=segment_backed,
                    cross_source_texts=cross_source_texts,
                )
                if parsed_objects is None:
                    return None, "invalid_event_objects"
                event_objects = parsed_objects
                names: list[str] = []
                for name, _kind in event_participants + event_objects:
                    if name in names:
                        return None, "duplicate_event_entity"
                    names.append(name)
                occurred_raw = raw_item.get("occurred_at")
                if occurred_raw is not None:
                    if not isinstance(occurred_raw, str) or not _is_iso_date(
                        occurred_raw.strip()
                    ):
                        return None, "invalid_event_time"
                    # Keep the original time expression below, but do not store
                    # a calendar date guessed from "yesterday" or a wrong date.
                    # This field is optional; only an unambiguous source date
                    # can establish its normalized ISO value.
                    source_dates = _explicit_source_dates(slices)
                    if len(source_dates) == 1:
                        event_occurred_at = next(iter(source_dates))
                time_expr_raw = raw_item.get("time_expression")
                if time_expr_raw is not None:
                    if not isinstance(time_expr_raw, str) or not time_expr_raw.strip():
                        return None, "invalid_event_time"
                    event_time_expression = time_expr_raw.strip()
                    if event_time_expression not in proposition:
                        return None, "event_time_not_in_proposition"
        elif (
            kind == "attribute" or (kind == "preference" and perspective_support)
        ) and third_party and raw_item.get("entity") is not None:
            # V4: third-party identity-class stable attributes (Owner decision
            # 2026-08-16 A).  The entity is the proposition's subject, so the
            # stated anchor does NOT prepend "用户" — the entity name itself
            # is the subject.  V5: action=correct (replacement) may carry the
            # same entity + optional perspective_holder.
            if formed_by != "stated":
                return None, "invalid_formed_by"
            if action not in ("form", "correct"):
                return None, "invalid_cognition_action"
            entity_raw = raw_item.get("entity")
            if not isinstance(entity_raw, dict):
                return None, "missing_entity"
            entity_canonical_name = entity_raw.get("canonical_name")
            if (
                not isinstance(entity_canonical_name, str)
                or not entity_canonical_name.strip()
            ):
                return None, "invalid_entity_name"
            entity_canonical_name = entity_canonical_name.strip()
            entity_kind = entity_raw.get("kind") or "person"
            if not isinstance(entity_kind, str) or not entity_kind.strip():
                return None, "invalid_entity_kind"
            entity_kind = str(entity_kind).strip()
            name_in_span = any(
                entity_canonical_name in slice_text for slice_text in slices
            )
            name_in_proposition = entity_canonical_name in proposition
            name_is_explicit = name_in_span and name_in_proposition
            reference_raw = raw_item.get("entity_reference")
            if not name_is_explicit:
                same_evidence_names = {
                    name
                    for name in (batch_entity_names or set())
                    if any(name in raw_by_id[evidence_id][:start] for evidence_id, start, _end, _slice in parsed)
                }
                same_evidence_names.update(
                    canonical
                    for mention, canonical in (current_entity_mentions or {}).items()
                    if any(
                        mention in raw_by_id[evidence_id][:start]
                        for evidence_id, start, _end, _slice in parsed
                    )
                )
                grounded = (context_entity_names or set()) | same_evidence_names
                if len(grounded) == 1 and entity_canonical_name not in grounded:
                    entity_canonical_name = next(iter(grounded))
                implicit_reference = reference_raw is None and _has_reference_mention(slices)
                if implicit_reference and grounded == {entity_canonical_name}:
                    pass
                elif not isinstance(reference_raw, dict) or set(reference_raw) != {"mention"}:
                    return None, (
                        "entity_name_not_in_span"
                        if not name_in_span
                        else "entity_name_not_in_proposition"
                    )
                else:
                    mention = reference_raw.get("mention")
                    if (
                        not isinstance(mention, str)
                        or not mention.strip()
                        or not any(mention in slice_text for slice_text in slices)
                    ):
                        return None, "invalid_entity_reference"
                    if grounded != {entity_canonical_name}:
                        return None, "ambiguous_entity_reference"
            elif reference_raw is not None:
                return None, "unexpected_entity_reference"
            # V5: optional third-party perspective holder ("小王说小李是00后").
            holder_raw = raw_item.get("perspective_holder")
            if holder_raw is not None:
                if not isinstance(holder_raw, dict):
                    return None, "invalid_perspective_holder"
                perspective_holder_name = holder_raw.get("canonical_name")
                if (
                    not isinstance(perspective_holder_name, str)
                    or not perspective_holder_name.strip()
                ):
                    return None, "invalid_perspective_holder"
                perspective_holder_name = perspective_holder_name.strip()
                perspective_holder_kind = holder_raw.get("kind") or "person"
                if (
                    not isinstance(perspective_holder_kind, str)
                    or not perspective_holder_kind.strip()
                ):
                    return None, "invalid_perspective_holder"
                perspective_holder_kind = str(perspective_holder_kind).strip()
                if perspective_holder_name == OWNER_ENTITY_NAME:
                    return None, "invalid_perspective_holder"
                if perspective_holder_name == entity_canonical_name:
                    return None, "perspective_holder_is_target"
                if perspective_holder_name not in proposition:
                    return None, "entity_name_not_in_proposition"
                if not any(
                    perspective_holder_name in slice_text
                    for slice_text in slices
                ):
                    return None, "entity_name_not_in_span"
            targeted_attribute = True
        else:
            if raw_item.get("entity") is not None:
                return None, "unexpected_entity"
            if raw_item.get("entity_reference") is not None:
                return None, "unexpected_entity_reference"
            if raw_item.get("target_entity") is not None:
                return None, "unexpected_target_entity"
            if raw_item.get("source_entity") is not None:
                return None, "unexpected_source_entity"
            if raw_item.get("relation_type") is not None:
                return None, "unexpected_relation_type"

        if formed_by == "confirmed":
            assert claim is not None
            if not _confirm_claim_is_proposition(claim):
                return None, "invalid_assistant_claim"
            for evidence_id, _start, _end, slice_text in parsed:
                context = context_by_id.get(evidence_id)
                if not context:
                    return None, "assistant_claim_context_missing"
                if claim not in context:
                    return None, "assistant_claim_not_in_context"
                if not _is_confirmation_span(slice_text, claim):
                    return None, "confirmed_span_not_confirmation"
            expected = _confirm_normalize(claim)
            if _strip_end_punctuation(proposition) != _strip_end_punctuation(expected):
                return None, "proposition_mismatch"
        else:
            use_no_prepend = (
                targeted_attribute
                or kind == "alias"
                or kind == "event"
                or kind == "naming"
                or retract
                or action == "contradict"
                or (kind == "relationship" and source_canonical_name is not None)
            )
            if use_no_prepend:
                anchors = [
                    _stated_normalize_no_subject_prepend(slice_text)
                    for slice_text in slices
                ]
            else:
                anchors = [_stated_normalize(slice_text) for slice_text in slices]
            if not any(
                _strip_end_punctuation(proposition)
                == _strip_end_punctuation(anchor)
                for anchor in anchors
            ):
                return None, "proposition_not_anchored"

        return (
            BatchItem(
                action=str(action),
                proposition=proposition,
                statement_kind=str(kind),
                formed_by=str(formed_by),
                supports=parsed,
                corrects_cognition_id=corrects if isinstance(corrects, str) else None,
                assistant_claim=claim,
                entity_canonical_name=entity_canonical_name,
                entity_kind=entity_kind,
                relation_type=relation_type,
                target_canonical_name=target_canonical_name,
                target_entity_kind=target_entity_kind,
                source_canonical_name=source_canonical_name,
                source_entity_kind=source_entity_kind,
                alias_of_canonical_name=alias_of_canonical_name,
                alias_of_kind=alias_of_kind,
                corrects_relationship_id=corrects_relationship_id,
                retract=retract,
                contradicts_cognition_id=contradicts_cognition_id,
                event_participants=event_participants,
                event_objects=event_objects,
                event_occurred_at=event_occurred_at,
                event_time_expression=event_time_expression,
                corrects_event_id=corrects_event_id,
                perspective_holder_name=perspective_holder_name,
                perspective_holder_kind=perspective_holder_kind,
                supersedes_cognition_id=supersedes_cognition_id,
                supersedes_relationship_id=supersedes_relationship_id,
            ),
            "",
        )

    # ── fenced atomic Apply ────────────────────────────────────────────────

    def _apply_atomically(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, batch: _CompiledBatch
    ) -> tuple[bool, Optional[dict[str, object]]]:
        for attempt in range(1, APPLY_BUSY_RETRIES + 1):
            try:
                return self._apply_once(db, job, batch)
            except sqlite3.OperationalError:
                if attempt == APPLY_BUSY_RETRIES:
                    raise
        raise PermanentWorldJobError("world_apply_unreachable")

    def _apply_once(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, batch: _CompiledBatch
    ) -> tuple[bool, Optional[dict[str, object]]]:
        now_text = to_iso_z(self._clock())
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                """SELECT attempts FROM memory_world_job
                    WHERE job_id = ?
                      AND state = 'processing'
                      AND claim_owner = ?
                      AND claim_token = ?
                      AND fencing_generation = ?
                      AND lease_expires_at > ?""",
                (
                    job.job_id,
                    job.claim_owner,
                    job.claim_token,
                    job.fencing_generation,
                    now_text,
                ),
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return False, None

            # The route observed the complete bound Evidence batch, not only
            # the subset the model later cited in item.supports. Revalidate
            # all of it before the first World mutation so a revoked unused
            # input cannot influence an applied result.
            for evidence_id in job.evidence_ids():
                self._validate_evidence_in_transaction(db, job, evidence_id)
            for item in batch.items:
                self._validate_supports_in_transaction(db, job, item)
            self._validate_historical_targets_in_transaction(db, job, batch)

            results: list[dict[str, object]] = []
            pending_transitions: list[tuple[str, str]] = []
            wrote_any = False
            for item in batch.items:
                if item.action == "form":
                    if item.statement_kind in ("naming", "relationship"):
                        result, wrote = self._apply_v3_object(
                            db, job, item, now_text
                        )
                    elif item.statement_kind == "alias":
                        result, wrote = self._apply_alias(db, job, item, now_text)
                    elif item.statement_kind == "event":
                        result, wrote = self._apply_v4_event(db, job, item, now_text)
                    else:
                        result, wrote = self._apply_form(db, job, item, now_text)
                    if item.supersedes_cognition_id is not None:
                        prior_target = str(item.supersedes_cognition_id)
                        prior_cog = db.execute(
                            "SELECT content, subject_id, invalid_at, archived_at, formed_by "
                            "FROM cognition WHERE id = ?",
                            (prior_target,),
                        ).fetchone()
                        if prior_cog is not None and prior_cog[2] is None and prior_cog[3] is None:
                            added = 0
                            for evidence_id, _start, _end, _slice in item.supports:
                                added += _ensure_support_link(
                                    db, prior_target, evidence_id, relation="contradict"
                                )
                            confidence, cred = self._recompute_chain_confidence(
                                db, prior_target, [str(prior_cog[4])]
                            )
                            db.execute(
                                "UPDATE cognition SET confidence = ?, cred_status = ?, updated_at = ? WHERE id = ?",
                                (confidence, cred, now_text, prior_target),
                            )
                            new_cog_id = item.cognition_id(job.subject_id)
                            pending_transitions.append((prior_target, new_cog_id))
                            wrote = True
                    superseded_rel = item.supersedes_relationship_id or (
                        item.supersedes_cognition_id if (
                            item.supersedes_cognition_id and str(item.supersedes_cognition_id).startswith("relationship-")
                        ) else None
                    )
                    if superseded_rel is not None:
                        prior_rel_id = str(superseded_rel)
                        prior_rel = db.execute(
                            "SELECT content, world_id, invalid_at, formed_by FROM relationship WHERE id = ?",
                            (prior_rel_id,),
                        ).fetchone()
                        if prior_rel is not None and prior_rel[2] is None:
                            for evidence_id, _start, _end, _slice in item.supports:
                                db.execute(
                                    "INSERT OR IGNORE INTO relationship_evidence (relationship_id, evidence_id) VALUES (?, ?)",
                                    (prior_rel_id, evidence_id),
                                )
                            db.execute(
                                "UPDATE relationship SET confidence = 0, cred_status = 'candidate', updated_at = ? WHERE id = ?",
                                (now_text, prior_rel_id),
                            )
                            db.execute(
                                """CREATE TABLE IF NOT EXISTS relationship_transitions (
                                    id                          TEXT PRIMARY KEY,
                                    prior_relationship_id       TEXT NOT NULL UNIQUE,
                                    replacement_relationship_id TEXT NOT NULL,
                                    reason                      TEXT NOT NULL,
                                    revision                    INTEGER NOT NULL
                                )"""
                            )
                            new_rel_id = item.apply_identity(job.subject_id) if hasattr(item, "apply_identity") else (
                                item.cognition_id(job.subject_id)
                            )
                            import uuid as _uuid
                            db.execute(
                                "INSERT OR REPLACE INTO relationship_transitions (id, prior_relationship_id, replacement_relationship_id, reason, revision) "
                                "VALUES (?, ?, ?, 'superseded', (SELECT revision FROM memory_state WHERE singleton = 1))",
                                (str(_uuid.uuid4()), prior_rel_id, new_rel_id),
                            )
                            wrote = True
                elif item.action == "contradict":
                    # V6: same-ID contradictory Evidence (downgrade only).
                    result, wrote = self._apply_contradict(
                        db, job, item, now_text
                    )
                elif item.retract:
                    # V6: correct with NO replacement (Owner decision: retract
                    # = correct special case).
                    result, wrote = self._apply_retract(db, job, item, now_text)
                elif item.statement_kind == "relationship":
                    # V5: relationship 改口替换 (corrects a prior relationship).
                    result, wrote = self._apply_relationship_correct(
                        db, job, item, now_text
                    )
                elif item.statement_kind == "event":
                    # V6: event correct (replacement).
                    result, wrote = self._apply_event_correct(
                        db, job, item, now_text
                    )
                else:
                    result, wrote, transition = self._apply_correct(
                        db, job, item, now_text
                    )
                    if transition is not None:
                        pending_transitions.append(transition)
                results.append(result)
                wrote_any = wrote_any or wrote

            revision = self._current_revision(db)
            if wrote_any:
                revision = self._bump_memory_state(db)
                for prior_id, replacement_id in pending_transitions:
                    self._write_transition(db, prior_id, replacement_id, revision)

            terminal_state: Literal["applied", "no_change"] = (
                "applied" if wrote_any else "no_change"
            )
            terminal_detail = None if wrote_any else "no_world_mutation"
            outcome = self._world_outcome(
                job,
                batch,
                results,
                revision,
                state=terminal_state,
                reason=terminal_detail,
            )
            world_json = _canonical(outcome)
            cursor = db.execute(
                """UPDATE memory_world_job
                       SET state = ?,
                           terminal_state = ?,
                           terminal_detail = ?,
                           world_result_json = ?,
                          result_hash = ?,
                          completed_at = ?,
                          last_error_type = NULL,
                          claim_owner = NULL,
                          claim_token = NULL,
                          lease_expires_at = NULL
                    WHERE job_id = ?
                      AND state = 'processing'
                      AND claim_owner = ?
                      AND claim_token = ?
                      AND fencing_generation = ?""",
                (
                    terminal_state,
                    terminal_state,
                    terminal_detail,
                    world_json,
                    _hash_text(world_json),
                    now_text,
                    job.job_id,
                    job.claim_owner,
                    job.claim_token,
                    job.fencing_generation,
                ),
            )
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                return False, None
            persist_terminal_outcome_in_transaction(db, job.job_id)
            db.execute("COMMIT")
            return True, outcome
        except BaseException:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _validate_supports_in_transaction(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem
    ) -> None:
        for evidence_id, start, end, slice_text in item.supports:
            raw = self._validate_evidence_in_transaction(db, job, evidence_id)
            if raw[start:end] != slice_text:
                raise _ZeroWriteError("span_changed_before_apply")

    def _validate_evidence_in_transaction(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, evidence_id: str
    ) -> str:
        row = db.execute(
            "SELECT e.raw_content, e.subject_id, e.host_id, e.source_kind, "
            "e.deleted_at, e.allow_local_read, e.allow_cloud_read, "
            "e.allow_inference, b.raw_content_hash "
            "FROM evidence e "
            "LEFT JOIN boundary_evidence_content b ON b.evidence_id = e.id "
            "WHERE e.id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise _ZeroWriteError("evidence_missing_before_apply")
        state = evidence_state(
            {
                "deleted_at": row[4],
                "allow_local_read": row[5],
                "allow_cloud_read": row[6],
                "allow_inference": row[7],
            },
            surface="formation",
            model_tier=self.model_tier,
        )
        if state is not None:
            raise _ZeroWriteError(f"{state}_before_apply")
        if str(row[1]) != job.subject_id or str(row[2]) != job.host_id:
            raise _ZeroWriteError("evidence_target_changed_before_apply")
        if str(row[3]) != "spoken":
            raise _ZeroWriteError("evidence_source_kind_changed_before_apply")
        bound_hash = row[8]
        if not isinstance(bound_hash, str) or not bound_hash:
            raise _ZeroWriteError("evidence_content_hash_missing_before_apply")
        raw = str(row[0])
        if _hash_text(raw) != bound_hash:
            raise _ZeroWriteError("evidence_content_hash_mismatch_before_apply")
        return raw

    def _validate_historical_targets_in_transaction(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, batch: _CompiledBatch
    ) -> None:
        """Fail closed if a model-selected historical target lost provenance.

        The model saw these current rows in its route payload.  A route callback
        may revoke/delete their supporting Evidence while the job Evidence stays
        valid; therefore target currentness is rechecked in the same Apply
        transaction and before any World mutation.  This deliberately shares
        the exact surface-aware predicate used to construct that payload.
        """

        targets: set[tuple[str, str]] = set()
        for item in batch.items:
            if item.corrects_cognition_id is not None:
                targets.add(("cognition", str(item.corrects_cognition_id)))
            if item.contradicts_cognition_id is not None:
                targets.add(("cognition", str(item.contradicts_cognition_id)))
            if item.corrects_relationship_id is not None:
                targets.add(("relationship", str(item.corrects_relationship_id)))
            if item.corrects_event_id is not None:
                targets.add(("event", str(item.corrects_event_id)))
            if (
                item.statement_kind == "alias"
                and item.entity_canonical_name is not None
                and item.alias_of_canonical_name is not None
            ):
                targets.add(
                    ("entity", entity_id_for(job.subject_id, item.entity_canonical_name))
                )
                targets.add(
                    ("entity", entity_id_for(job.subject_id, item.alias_of_canonical_name))
                )

        for kind, item_id in sorted(targets):
            # Preserve the established deterministic replay/unknown-target
            # outcomes.  The typed apply operation owns those state errors;
            # provenance needs revalidation only for a historical target that
            # is still current and could otherwise be mutated below.
            if not self._historical_target_is_current(db, job.subject_id, kind, item_id):
                continue
            # V3 rows produced before entity-formation provenance existed are
            # preserved for deterministic replay.  Once an Entity carries any
            # formal provenance, however, every link is authoritative and a
            # revoked one must block its alias merge/reanchor.
            if kind == "entity" and not linked_evidence(db, "entity", item_id):
                continue
            if not world_item_visible(
                db,
                job.subject_id,
                kind,  # type: ignore[arg-type]
                item_id,
                surface="formation",
                model_tier=self.model_tier,
            ):
                raise _ZeroWriteError(
                    f"historical_{kind}_provenance_not_current_before_apply"
                )

    @staticmethod
    def _historical_target_is_current(
        db: sqlite3.Connection, subject_id: str, kind: str, item_id: str
    ) -> bool:
        if kind == "cognition":
            row = db.execute(
                "SELECT 1 FROM cognition WHERE id = ? AND subject_id = ? "
                "AND invalid_at IS NULL AND archived_at IS NULL AND muted_at IS NULL",
                (item_id, subject_id),
            ).fetchone()
        elif kind == "relationship":
            row = db.execute(
                "SELECT 1 FROM relationship WHERE id = ? AND world_id = ? "
                "AND invalid_at IS NULL",
                (item_id, subject_id),
            ).fetchone()
        elif kind == "entity":
            row = db.execute(
                "SELECT 1 FROM entity WHERE id = ? AND world_id = ? "
                "AND invalid_at IS NULL",
                (item_id, subject_id),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT 1 FROM world_event WHERE id = ? AND world_id = ? "
                "AND invalid_at IS NULL",
                (item_id, subject_id),
            ).fetchone()
        return row is not None

    @staticmethod
    def _recompute_chain_confidence(
        db: sqlite3.Connection, cognition_id: str, carriers: list[str]
    ) -> tuple[int, str]:
        """Recompute confidence/credibility from the complete evidence chain.

        Any same-ID contradictory link pins the weakest carrier to
        ``contradict_stated`` (base 0) — downgrade only, never invalidation
        (Owner decision 2026-08-16).  Support bonus counts support links only.
        """
        support_count = int(
            db.execute(
                "SELECT COUNT(DISTINCT evidence_id) FROM cognition_evidence "
                "WHERE cognition_id = ? AND relation = 'support'",
                (cognition_id,),
            ).fetchone()[0]
        )
        contradict_count = int(
            db.execute(
                "SELECT COUNT(DISTINCT evidence_id) FROM cognition_evidence "
                "WHERE cognition_id = ? AND relation = 'contradict'",
                (cognition_id,),
            ).fetchone()[0]
        )
        effective = list(carriers)
        if contradict_count > 0:
            effective.append("contradict_stated")
        weakest = min((CARRIER_RANK[c], c) for c in effective)[1]
        confidence = min(
            FORMED_BY_BASES[weakest]
            + min(max(support_count - 1, 0), SUPPORT_CAP) * SUPPORT_STEP,
            CONFIDENCE_HARD_MAX,
        )
        cred = "candidate"
        for label, floor in CRED_THRESHOLDS:
            if confidence >= floor:
                cred = label
                break
        return confidence, cred

    def _apply_form(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem,
        now_text: str,
    ) -> tuple[dict[str, object], bool]:
        cognition_id = item.cognition_id(job.subject_id)
        target_entity_id: Optional[str] = None
        perspective_entity_id: Optional[str] = None
        target_created = False
        perspective_created = False
        if item.entity_canonical_name is not None and item.statement_kind in {"attribute", "preference"}:
            # V4 targeted third-party attribute: the proposition's subject is
            # the entity; resolve/create it and record the target sidecar.
            assert item.entity_kind is not None
            target_entity_id, target_created = self._resolve_target_entity(
                db, job, item.entity_canonical_name, item.entity_kind, now_text
            )
            if item.perspective_holder_name is not None:
                # V5: third-party perspective holder (Owner decision 2026-08-16:
                # holder enters the deterministic identity; the World stays the
                # Owner's).
                assert item.perspective_holder_kind is not None
                perspective_entity_id, perspective_created = self._resolve_target_entity(
                    db, job, item.perspective_holder_name,
                    item.perspective_holder_kind, now_text,
                )
        existing = db.execute(
            "SELECT content, content_type, formed_by, confidence FROM cognition "
            "WHERE id = ?",
            (cognition_id,),
        ).fetchone()
        if existing is None:
            confidence = item.confidence_for(len(item.supports))
            if item.entity_canonical_name is None:
                self._sync_owner_alias(db, job, item.proposition, now_text)
            self._write_cognition(
                db, job, item, cognition_id, confidence, now_text,
                target_entity_id=target_entity_id,
                perspective_entity_id=perspective_entity_id,
            )
            return (
                self._item_outcome(
                    item, cognition_id, confidence=confidence,
                    target_entity_id=target_entity_id,
                    perspective_entity_id=perspective_entity_id,
                ),
                True,
            )
        if (
            str(existing[0]) != item.proposition
            or str(existing[1]) != item.content_type
            or str(existing[2]) not in FORMED_BY_BASES
        ):
            raise _ZeroWriteError("cognition_replay_mismatch")
        if target_entity_id is not None:
            sidecar = db.execute(
                "SELECT target_entity_id, perspective_entity_id "
                "FROM cognition_target WHERE cognition_id = ?",
                (cognition_id,),
            ).fetchone()
            if (
                sidecar is None
                or str(sidecar[0]) != target_entity_id
                or str(sidecar[1] or "") != (perspective_entity_id or "")
            ):
                raise _ZeroWriteError("cognition_replay_mismatch")
        # Same-ID Evidence path (1.0 support semantics): restated propositions
        # attach their new Evidence as support to the existing cognition and
        # recompute confidence from the full chain — no successor, no
        # supersession.  The chain carrier is the weakest carrier across all
        # linked evidence (1.0 derive_formed_by).
        added = 0
        for evidence_id, _start, _end, _slice in item.supports:
            added += _ensure_support_link(db, cognition_id, evidence_id)
        new_confidence, new_cred = self._recompute_chain_confidence(
            db, cognition_id, [str(existing[2]), item.formed_by]
        )
        if added or new_confidence != int(existing[3]):
            db.execute(
                "UPDATE cognition SET confidence = ?, cred_status = ?, "
                "updated_at = ? WHERE id = ?",
                (new_confidence, new_cred, now_text, cognition_id),
            )
            wrote = True
        else:
            # Exact replay: nothing in the World changed.
            wrote = False
        wrote = wrote or target_created or perspective_created
        return (
            self._item_outcome(
                item, cognition_id, confidence=new_confidence,
                cred_status=new_cred, target_entity_id=target_entity_id,
                perspective_entity_id=perspective_entity_id,
            ),
            wrote,
        )

    # ── V3 first-class Entity / Relationship apply ──────────────────────────

    def _ensure_owner_entity(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, now_text: str
    ) -> tuple[str, bool]:
        owner_id = owner_entity_id_for(job.subject_id)
        existing = db.execute(
            "SELECT 1 FROM entity WHERE id = ? AND invalid_at IS NULL",
            (owner_id,),
        ).fetchone()
        if existing is None:
            cursor = db.execute(
                "INSERT OR IGNORE INTO entity (id, world_id, kind, "
                "canonical_name, invalid_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (
                    owner_id,
                    job.subject_id,
                    OWNER_ENTITY_KIND,
                    OWNER_ENTITY_NAME,
                    now_text,
                    now_text,
                ),
            )
            return owner_id, cursor.rowcount == 1
        return owner_id, False

    def _resolve_target_entity(
        self, db: sqlite3.Connection, job: ClaimedWorldJob,
        canonical_name: str, kind: str, now_text: str,
    ) -> tuple[str, bool]:
        entity_id = entity_id_for(job.subject_id, canonical_name)
        existing = db.execute(
            "SELECT kind, invalid_at FROM entity WHERE id = ?", (entity_id,)
        ).fetchone()
        if existing is None:
            cursor = db.execute(
                "INSERT OR IGNORE INTO entity (id, world_id, kind, "
                "canonical_name, invalid_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (
                    entity_id,
                    job.subject_id,
                    kind,
                    canonical_name,
                    now_text,
                    now_text,
                ),
            )
            return entity_id, cursor.rowcount == 1
        if existing[1] is not None:
            raise _ZeroWriteError("entity_not_current")
        if str(existing[0]) != kind:
            raise _ZeroWriteError("entity_kind_mismatch")
        return entity_id, False

    def _reconcile_single_status_cognitions(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem, now_text: str
    ) -> list[tuple[str, str]]:
        partner_name = str(item.target_canonical_name)
        evidence_id = item.supports[0][0] if item.supports else "evidence-auto-transition"
        rows = db.execute(
            "SELECT id, content, content_type, formed_by, confidence FROM cognition "
            "WHERE subject_id = ? AND invalid_at IS NULL AND archived_at IS NULL",
            (job.subject_id,),
        ).fetchall()
        transitions: list[tuple[str, str]] = []
        for cid, content, ctype, fby, conf in rows:
            content_str = str(content)
            if any(w in content_str for w in ("单身", "没有女朋友", "没有男朋友")):
                if content_str in ("用户没有女朋友", "用户目前单身", "用户单身", "用户没有男朋友"):
                    db.execute(
                        "UPDATE cognition SET invalid_at = ?, updated_at = ? WHERE id = ?",
                        (now_text, now_text, cid),
                    )
                    transitions.append((cid, ""))
                else:
                    new_content = re.sub(r"单身状态（没有女朋友）", f"已和{partner_name}在一起", content_str)
                    new_content = re.sub(r"单身状态", f"已和{partner_name}在一起", new_content)
                    new_content = re.sub(r"没有女朋友", f"女朋友是{partner_name}", new_content)
                    new_content = re.sub(r"没有男朋友", f"男朋友是{partner_name}", new_content)
                    if new_content != content_str:
                        db.execute(
                            "UPDATE cognition SET invalid_at = ?, updated_at = ? WHERE id = ?",
                            (now_text, now_text, cid),
                        )
                        replacement_item = BatchItem(
                            action="form",
                            proposition=new_content,
                            statement_kind=ctype,
                            formed_by=fby,
                            supports=((evidence_id, 0, 0, new_content),),
                        )
                        new_id = replacement_item.cognition_id(job.subject_id)
                        db.execute(
                            "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
                            "confidence, cred_status, scope, valid_at, invalid_at, asked_at, archived_at, "
                            "muted_at, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, 'limited', 'unscoped', NULL, NULL, NULL, NULL, NULL, ?, ?)",
                            (new_id, job.subject_id, new_content, ctype, fby, conf, now_text, now_text),
                        )
                        db.execute(
                            "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
                            "VALUES (?, ?, 'support')",
                            (new_id, evidence_id),
                        )
                        t_id = "cognition-transition-" + _hash_text(_canonical(["corrects", cid, new_id]))
                        revision = self._current_revision(db)
                        db.execute(
                            "INSERT OR IGNORE INTO cognition_transitions (id, prior_cognition_id, replacement_cognition_id, reason, revision) "
                            "VALUES (?, ?, ?, 'corrects', ?)",
                            (t_id, cid, new_id, revision),
                        )
                        transitions.append((cid, new_id))
        return transitions

    def _apply_v3_object(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem,
        now_text: str,
    ) -> tuple[dict[str, object], bool]:
        if item.statement_kind == "naming":
            assert item.entity_canonical_name is not None
            assert item.entity_kind is not None
            entity_id, created = self._resolve_target_entity(
                db, job, item.entity_canonical_name, item.entity_kind, now_text
            )
            # The naming proposition is ALSO an Owner cognition about the
            # entity: a recallable `naming` cognition row (content = the
            # anchored proposition) keeps "我最好的朋友是谁" answerable by the
            # deterministic matcher, with its own evidence chain and the
            # same-ID support semantics as any other form.
            cognition_id = item.cognition_id(job.subject_id)
            existing_cog = db.execute(
                "SELECT content, formed_by, confidence FROM cognition WHERE id = ?",
                (cognition_id,),
            ).fetchone()
            final_confidence = item.confidence_for(len(item.supports))
            final_cred = item.cred_status_for(final_confidence)
            if existing_cog is None:
                self._write_cognition(
                    db, job, item, cognition_id, final_confidence, now_text
                )
                wrote = True
            else:
                if (
                    str(existing_cog[0]) != item.proposition
                    or str(existing_cog[1]) not in FORMED_BY_BASES
                ):
                    raise _ZeroWriteError("cognition_replay_mismatch")
                added = 0
                for evidence_id, _start, _end, _slice in item.supports:
                    added += _ensure_support_link(db, cognition_id, evidence_id)
                linked = int(
                    db.execute(
                        "SELECT COUNT(DISTINCT evidence_id) FROM cognition_evidence "
                        "WHERE cognition_id = ?",
                        (cognition_id,),
                    ).fetchone()[0]
                )
                weakest = min(
                    (CARRIER_RANK[str(existing_cog[1])], str(existing_cog[1])),
                    (CARRIER_RANK[item.formed_by], item.formed_by),
                )[1]
                final_confidence = min(
                    FORMED_BY_BASES[weakest]
                    + min(max(linked - 1, 0), SUPPORT_CAP) * SUPPORT_STEP,
                    CONFIDENCE_HARD_MAX,
                )
                final_cred = "candidate"
                for label, floor in CRED_THRESHOLDS:
                    if final_confidence >= floor:
                        final_cred = label
                        break
                if added or final_confidence != int(existing_cog[2]):
                    db.execute(
                        "UPDATE cognition SET confidence = ?, cred_status = ?, "
                        "updated_at = ? WHERE id = ?",
                        (final_confidence, final_cred, now_text, cognition_id),
                    )
                    wrote = True
                else:
                    wrote = False
            if created:
                for evidence_id, start, end, _slice in item.supports:
                    self._write_entity_ledger(
                        db, entity_id, evidence_id, start, end
                    )
            outcome = {
                "action": "form",
                "statement_kind": "naming",
                "entity_id": entity_id,
                "cognition_id": cognition_id,
                "canonical_name": item.entity_canonical_name,
                "entity_kind": item.entity_kind,
                "formed_by": "stated",
                "confidence": final_confidence,
                "cred_status": final_cred,
                "evidence_count": len(item.supports),
            }
            return outcome, wrote or created

        assert item.relation_type is not None
        assert item.target_canonical_name is not None
        assert item.target_entity_kind is not None
        if item.source_canonical_name is not None:
            assert item.source_entity_kind is not None
            source_id, source_created = self._resolve_target_entity(
                db, job, item.source_canonical_name, item.source_entity_kind,
                now_text,
            )
        else:
            source_id, source_created = self._ensure_owner_entity(db, job, now_text)
        target_id, target_created = self._resolve_target_entity(
            db, job, item.target_canonical_name, item.target_entity_kind, now_text
        )
        relationship_id = relationship_id_for(
            job.subject_id, source_id, item.relation_type, target_id
        )
        existing = db.execute(
            "SELECT content, relation_type, formed_by, confidence, "
            "source_entity_id FROM relationship WHERE id = ?",
            (relationship_id,),
        ).fetchone()
        if existing is None:
            confidence = item.confidence_for(len(item.supports))
            db.execute(
                "INSERT INTO relationship (id, world_id, source_entity_id, "
                "target_entity_id, relation_type, content, formed_by, "
                "confidence, cred_status, invalid_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    relationship_id,
                    job.subject_id,
                    source_id,
                    target_id,
                    item.relation_type,
                    item.proposition,
                    item.formed_by,
                    confidence,
                    item.cred_status_for(confidence),
                    now_text,
                    now_text,
                ),
            )
            for evidence_id, start, end, _slice in item.supports:
                _ensure_relationship_support_link(
                    db, relationship_id, evidence_id
                )
                self._write_relationship_ledger(
                    db, relationship_id, evidence_id, start, end
                )
            outcome = self._item_outcome(
                item, relationship_id, confidence=confidence,
                target_entity_id=target_id, source_entity_id=source_id,
            )
            if item.source_canonical_name is None and item.relation_type in (
                "girlfriend", "boyfriend", "spouse", "partner", "wife", "husband"
            ):
                self._reconcile_single_status_cognitions(db, job, item, now_text)
            return outcome, True
        if (
            str(existing[0]) != item.proposition
            or str(existing[1]) != item.relation_type
            or str(existing[2]) not in FORMED_BY_BASES
            or str(existing[4]) != source_id
        ):
            raise _ZeroWriteError("relationship_replay_mismatch")
        added = 0
        for evidence_id, _start, _end, _slice in item.supports:
            added += _ensure_relationship_support_link(
                db, relationship_id, evidence_id
            )
        linked = int(
            db.execute(
                "SELECT COUNT(DISTINCT evidence_id) FROM relationship_evidence "
                "WHERE relationship_id = ?",
                (relationship_id,),
            ).fetchone()[0]
        )
        weakest = min(
            (CARRIER_RANK[str(existing[2])], str(existing[2])),
            (CARRIER_RANK[item.formed_by], item.formed_by),
        )[1]
        new_confidence = min(
            FORMED_BY_BASES[weakest]
            + min(max(linked - 1, 0), SUPPORT_CAP) * SUPPORT_STEP,
            CONFIDENCE_HARD_MAX,
        )
        new_cred = "candidate"
        for label, floor in CRED_THRESHOLDS:
            if new_confidence >= floor:
                new_cred = label
                break
        if added or new_confidence != int(existing[3]):
            db.execute(
                "UPDATE relationship SET confidence = ?, cred_status = ?, "
                "updated_at = ? WHERE id = ?",
                (new_confidence, new_cred, now_text, relationship_id),
            )
            wrote = True
        else:
            wrote = False
        outcome = self._item_outcome(
            item, relationship_id, confidence=new_confidence,
            cred_status=new_cred, target_entity_id=target_id,
            source_entity_id=source_id,
        )
        return outcome, wrote or source_created or target_created

    # ── V5: alias merge ─────────────────────────────────────────────────────

    @staticmethod
    def _alias_merge_ledger_id(canonical_id: str, merged_id: str) -> str:
        return "evidence-ledger-" + _hash_text(
            _canonical(["alias_merge", canonical_id, merged_id])
        )

    @staticmethod
    def _entity_aliases(db: sqlite3.Connection, entity_id: str) -> list[str]:
        row = db.execute(
            "SELECT aliases_json FROM entity WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            raise _ZeroWriteError("alias_entity_unknown")
        try:
            decoded = json.loads(str(row[0]) or "[]")
        except (TypeError, ValueError):
            raise _ZeroWriteError("alias_json_corrupt") from None
        if not isinstance(decoded, list):
            raise _ZeroWriteError("alias_json_corrupt")
        return [str(x) for x in decoded]

    def _alias_outcome(
        self,
        item: BatchItem,
        canonical_id: str,
        canonical_name: str,
        merged_id: str,
        merged_name: str,
        reanchored: int,
        repointed: int,
    ) -> dict[str, object]:
        return {
            "action": "form",
            "statement_kind": "alias",
            "formed_by": item.formed_by,
            "canonical_entity_id": canonical_id,
            "canonical_name": canonical_name,
            "merged_entity_id": merged_id,
            "merged_name": merged_name,
            "reanchored_relationships": reanchored,
            "repointed_cognitions": repointed,
            "evidence_count": len(item.supports),
        }

    def _apply_alias(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem,
        now_text: str,
    ) -> tuple[dict[str, object], bool]:
        """Merge two entity names the user explicitly equated.

        The earlier-formed entity is canonical (Owner decision 2026-08-16 A);
        the later one is invalidated, its name appended to the canonical's
        ``aliases_json``, and every current relationship / cognition_target
        referencing it is re-pointed at the canonical inside this single fenced
        transaction.  Relationship rows preserve the id↔endpoint invariant:
        re-pointing re-anchors the row under a new deterministic id instead of
        mutating the endpoints under the old id.
        """
        assert item.entity_canonical_name is not None
        assert item.entity_kind is not None
        assert item.alias_of_canonical_name is not None
        assert item.alias_of_kind is not None
        left_id = entity_id_for(job.subject_id, item.entity_canonical_name)
        right_id = entity_id_for(job.subject_id, item.alias_of_canonical_name)
        left = db.execute(
            "SELECT canonical_name, kind, invalid_at, created_at, rowid "
            "FROM entity WHERE id = ?",
            (left_id,),
        ).fetchone()
        right = db.execute(
            "SELECT canonical_name, kind, invalid_at, created_at, rowid "
            "FROM entity WHERE id = ?",
            (right_id,),
        ).fetchone()
        if left is None or right is None:
            raise _ZeroWriteError("alias_entity_unknown")
        left_invalid = left[2] is not None
        right_invalid = right[2] is not None
        if left_invalid and right_invalid:
            raise _ZeroWriteError("alias_entities_not_current")
        if left_invalid or right_invalid:
            # Deterministic replay of an already-applied merge: exactly one
            # side survives; the recorded ledger must agree and nothing is
            # written again.
            if left_invalid:
                canonical_id, merged_id = right_id, left_id
                canonical_row, merged_row = right, left
            else:
                canonical_id, merged_id = left_id, right_id
                canonical_row, merged_row = left, right
            ledger = db.execute(
                "SELECT payload_json FROM evidence_ledger WHERE id = ?",
                (self._alias_merge_ledger_id(canonical_id, merged_id),),
            ).fetchone()
            if ledger is None:
                raise _ZeroWriteError("alias_replay_mismatch")
            try:
                payload = json.loads(str(ledger[0]))
            except (TypeError, ValueError):
                raise _ZeroWriteError("alias_replay_mismatch") from None
            if not isinstance(payload, dict):
                raise _ZeroWriteError("alias_replay_mismatch")
            if str(merged_row[0]) not in self._entity_aliases(db, canonical_id):
                raise _ZeroWriteError("alias_replay_mismatch")
            return (
                self._alias_outcome(
                    item,
                    canonical_id,
                    str(canonical_row[0]),
                    merged_id,
                    str(merged_row[0]),
                    int(payload.get("reanchored_relationships") or 0),
                    int(payload.get("repointed_cognitions") or 0),
                ),
                False,
            )
        if str(left[1]) != str(right[1]):
            raise _ZeroWriteError("alias_kind_mismatch")
        # Earlier formation wins; identical timestamps tie-break on insertion
        # order (rowid), which captures true within-transaction order.
        if (str(left[3]), int(left[4])) <= (str(right[3]), int(right[4])):
            canonical_id, merged_id = left_id, right_id
            canonical_row, merged_row = left, right
        else:
            canonical_id, merged_id = right_id, left_id
            canonical_row, merged_row = right, left
        alias_name = str(merged_row[0])
        canonical_name = str(canonical_row[0])
        aliases = self._entity_aliases(db, canonical_id)
        if alias_name not in aliases:
            aliases.append(alias_name)
            ledger = db.execute(
                "UPDATE entity SET aliases_json = ?, updated_at = ? WHERE id = ?",
                (_canonical(aliases), now_text, canonical_id),
            )
        cursor = db.execute(
            "UPDATE entity SET invalid_at = ?, updated_at = ? "
            "WHERE id = ? AND invalid_at IS NULL",
            (now_text, now_text, merged_id),
        )
        if cursor.rowcount != 1:
            raise _ZeroWriteError("alias_entities_not_current")
        repointed = db.execute(
            "UPDATE cognition_target SET target_entity_id = ? "
            "WHERE target_entity_id = ?",
            (canonical_id, merged_id),
        ).rowcount
        reanchored = self._reanchor_relationships(
            db, job, merged_id, canonical_id, now_text
        )
        db.execute(
            "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) "
            "VALUES (?, ?, ?)",
            (
                self._alias_merge_ledger_id(canonical_id, merged_id),
                _canonical(
                    {
                        "relation": "alias",
                        "canonical_entity_id": canonical_id,
                        "merged_entity_id": merged_id,
                        "alias_name": alias_name,
                    }
                ),
                _canonical(
                    {
                        "schema_version": 1,
                        "boundary_event_id": job.boundary_event_id,
                        "evidence_ids": [
                            evidence_id
                            for evidence_id, _s, _e, _t in item.supports
                        ],
                        "reanchored_relationships": reanchored,
                        "repointed_cognitions": repointed,
                    }
                ),
            ),
        )
        return (
            self._alias_outcome(
                item, canonical_id, canonical_name, merged_id, alias_name,
                reanchored, repointed,
            ),
            True,
        )

    def _reanchor_relationships(
        self,
        db: sqlite3.Connection,
        job: ClaimedWorldJob,
        merged_id: str,
        canonical_id: str,
        now_text: str,
    ) -> int:
        rows = db.execute(
            "SELECT id, source_entity_id, target_entity_id, content, "
            "relation_type, formed_by, confidence, cred_status "
            "FROM relationship "
            "WHERE world_id = ? AND invalid_at IS NULL "
            "AND (source_entity_id = ? OR target_entity_id = ?)",
            (job.subject_id, merged_id, merged_id),
        ).fetchall()
        for r in rows:
            old_id = str(r[0])
            new_source = canonical_id if str(r[1]) == merged_id else str(r[1])
            new_target = canonical_id if str(r[2]) == merged_id else str(r[2])
            new_id = relationship_id_for(
                job.subject_id, new_source, str(r[4]), new_target
            )
            cursor = db.execute(
                "UPDATE relationship SET invalid_at = ?, updated_at = ? "
                "WHERE id = ? AND invalid_at IS NULL",
                (now_text, now_text, old_id),
            )
            if cursor.rowcount != 1:
                raise _ZeroWriteError("alias_repoint_race")
            existing = db.execute(
                "SELECT content, relation_type, source_entity_id, "
                "target_entity_id, formed_by, invalid_at FROM relationship "
                "WHERE id = ?",
                (new_id,),
            ).fetchone()
            if existing is not None and existing[5] is None:
                # The re-pointed triple is already a current relationship:
                # merge support chains instead of inserting a duplicate.
                if (
                    str(existing[0]) != str(r[3])
                    or str(existing[1]) != str(r[4])
                    or str(existing[2]) != new_source
                    or str(existing[3]) != new_target
                ):
                    raise _ZeroWriteError("alias_repoint_merge_conflict")
                carriers = [str(existing[4]), str(r[5])]
            elif existing is None:
                db.execute(
                    "INSERT INTO relationship (id, world_id, source_entity_id, "
                    "target_entity_id, relation_type, content, formed_by, "
                    "confidence, cred_status, invalid_at, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        new_id,
                        job.subject_id,
                        new_source,
                        new_target,
                        str(r[4]),
                        str(r[3]),
                        str(r[5]),
                        int(r[6]),
                        str(r[7]),
                        now_text,
                        now_text,
                    ),
                )
                carriers = [str(r[5])]
            else:
                # Revive a historical row under the same deterministic id.
                db.execute(
                    "UPDATE relationship SET world_id = ?, source_entity_id = ?, "
                    "target_entity_id = ?, relation_type = ?, content = ?, "
                    "formed_by = ?, confidence = ?, cred_status = ?, "
                    "invalid_at = NULL, updated_at = ? WHERE id = ?",
                    (
                        job.subject_id,
                        new_source,
                        new_target,
                        str(r[4]),
                        str(r[3]),
                        str(r[5]),
                        int(r[6]),
                        str(r[7]),
                        now_text,
                        new_id,
                    ),
                )
                carriers = [str(existing[4]), str(r[5])]
            db.execute(
                "INSERT OR IGNORE INTO relationship_evidence "
                "(relationship_id, evidence_id, relation) "
                "SELECT ?, evidence_id, relation FROM relationship_evidence "
                "WHERE relationship_id = ?",
                (new_id, old_id),
            )
            linked = int(
                db.execute(
                    "SELECT COUNT(DISTINCT evidence_id) FROM relationship_evidence "
                    "WHERE relationship_id = ?",
                    (new_id,),
                ).fetchone()[0]
            )
            weakest = min((CARRIER_RANK[c], c) for c in carriers)[1]
            confidence = min(
                FORMED_BY_BASES[weakest]
                + min(max(linked - 1, 0), SUPPORT_CAP) * SUPPORT_STEP,
                CONFIDENCE_HARD_MAX,
            )
            cred = "candidate"
            for label, floor in CRED_THRESHOLDS:
                if confidence >= floor:
                    cred = label
                    break
            db.execute(
                "UPDATE relationship SET confidence = ?, cred_status = ?, "
                "updated_at = ? WHERE id = ?",
                (confidence, cred, now_text, new_id),
            )
            db.execute(
                "INSERT OR IGNORE INTO evidence_ledger (id, content, "
                "payload_json) VALUES (?, ?, ?)",
                (
                    "evidence-ledger-"
                    + _hash_text(_canonical(["alias_repoint", old_id, new_id])),
                    _canonical(
                        {
                            "relation": "alias_repoint",
                            "prior_relationship_id": old_id,
                            "replacement_relationship_id": new_id,
                        }
                    ),
                    _canonical(
                        {
                            "schema_version": 1,
                            "canonical_entity_id": canonical_id,
                            "merged_entity_id": merged_id,
                        }
                    ),
                ),
            )
        return len(rows)

    # ── V5: relationship 改口替换 ───────────────────────────────────────────

    def _relationship_correction_outcome(
        self,
        item: BatchItem,
        prior_id: str,
        new_id: str,
        confidence: int,
        *,
        cred_status: Optional[str] = None,
        target_entity_id: Optional[str] = None,
        source_entity_id: Optional[str] = None,
    ) -> dict[str, object]:
        outcome: dict[str, object] = {
            "action": "correct",
            "statement_kind": "relationship",
            "formed_by": item.formed_by,
            "prior_relationship_id": prior_id,
            "replacement_relationship_id": new_id,
            "relation_type": item.relation_type,
            "confidence": confidence,
            "cred_status": (
                cred_status
                if cred_status is not None
                else item.cred_status_for(confidence)
            ),
            "evidence_count": len(item.supports),
        }
        if source_entity_id is not None:
            outcome["source_entity_id"] = source_entity_id
        if target_entity_id is not None:
            outcome["target_entity_id"] = target_entity_id
        return outcome

    def _merge_relationship_supports(
        self,
        db: sqlite3.Connection,
        relationship_id: str,
        item: BatchItem,
        existing_formed_by: str,
        now_text: str,
    ) -> tuple[int, str, bool]:
        added = 0
        for evidence_id, _start, _end, _slice in item.supports:
            added += _ensure_relationship_support_link(
                db, relationship_id, evidence_id
            )
        linked = int(
            db.execute(
                "SELECT COUNT(DISTINCT evidence_id) FROM relationship_evidence "
                "WHERE relationship_id = ?",
                (relationship_id,),
            ).fetchone()[0]
        )
        weakest = min(
            (CARRIER_RANK[existing_formed_by], existing_formed_by),
            (CARRIER_RANK[item.formed_by], item.formed_by),
        )[1]
        confidence = min(
            FORMED_BY_BASES[weakest]
            + min(max(linked - 1, 0), SUPPORT_CAP) * SUPPORT_STEP,
            CONFIDENCE_HARD_MAX,
        )
        cred = "candidate"
        for label, floor in CRED_THRESHOLDS:
            if confidence >= floor:
                cred = label
                break
        current = db.execute(
            "SELECT confidence, cred_status FROM relationship WHERE id = ?",
            (relationship_id,),
        ).fetchone()
        if current is None:
            raise _ZeroWriteError("relationship_correction_replay_mismatch")
        if added or confidence != int(current[0]) or cred != str(current[1]):
            db.execute(
                "UPDATE relationship SET confidence = ?, cred_status = ?, "
                "updated_at = ? WHERE id = ?",
                (confidence, cred, now_text, relationship_id),
            )
            wrote = True
        else:
            wrote = False
        return confidence, cred, wrote

    def _apply_relationship_correct(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem,
        now_text: str,
    ) -> tuple[dict[str, object], bool]:
        assert item.corrects_relationship_id is not None
        assert item.relation_type is not None
        assert item.target_canonical_name is not None
        assert item.target_entity_kind is not None
        prior_id = str(item.corrects_relationship_id)
        new_id = item.apply_identity(job.subject_id)
        prior = db.execute(
            "SELECT content, world_id, invalid_at FROM relationship WHERE id = ?",
            (prior_id,),
        ).fetchone()
        if prior is None:
            raise _ZeroWriteError("relationship_correction_target_unknown")
        source_id = (
            entity_id_for(job.subject_id, item.source_canonical_name)
            if item.source_canonical_name is not None
            else owner_entity_id_for(job.subject_id)
        )
        target_id = entity_id_for(
            job.subject_id, str(item.target_canonical_name)
        )
        if prior[2] is not None:
            # Replay of an already-applied correction: verify the replacement
            # is current and matches, then reproduce the terminal outcome
            # without writing anything.
            new_row = db.execute(
                "SELECT content, relation_type, source_entity_id, "
                "target_entity_id, invalid_at FROM relationship WHERE id = ?",
                (new_id,),
            ).fetchone()
            if new_row is None or new_row[4] is not None:
                raise _ZeroWriteError("relationship_correction_replay_mismatch")
            if (
                str(new_row[0]) != item.proposition
                or str(new_row[1]) != item.relation_type
                or str(new_row[2]) != source_id
                or str(new_row[3]) != target_id
            ):
                raise _ZeroWriteError("relationship_correction_replay_mismatch")
            confidence = item.confidence_for(len(item.supports))
            return (
                self._relationship_correction_outcome(
                    item, prior_id, new_id, confidence,
                    target_entity_id=target_id, source_entity_id=source_id,
                ),
                False,
            )
        if str(prior[1]) != job.subject_id:
            raise _ZeroWriteError("relationship_correction_subject_mismatch")
        if item.proposition == str(prior[0]):
            raise _ZeroWriteError("correction_identical_proposition")
        if item.source_canonical_name is not None:
            assert item.source_entity_kind is not None
            self._resolve_target_entity(
                db, job, item.source_canonical_name, item.source_entity_kind,
                now_text,
            )
        else:
            self._ensure_owner_entity(db, job, now_text)
        self._resolve_target_entity(
            db, job, item.target_canonical_name, item.target_entity_kind,
            now_text,
        )
        cursor = db.execute(
            "UPDATE relationship SET invalid_at = ?, updated_at = ? "
            "WHERE id = ? AND invalid_at IS NULL",
            (now_text, now_text, prior_id),
        )
        if cursor.rowcount != 1:
            raise _ZeroWriteError("relationship_correction_target_not_current")
        confidence = item.confidence_for(len(item.supports))
        existing = db.execute(
            "SELECT content, relation_type, source_entity_id, "
            "target_entity_id, formed_by, invalid_at FROM relationship "
            "WHERE id = ?",
            (new_id,),
        ).fetchone()
        if existing is not None and existing[5] is None:
            # The replacement relationship is already current: the correction
            # re-asserts it, so forward supports instead of inserting.
            if (
                str(existing[0]) != item.proposition
                or str(existing[1]) != item.relation_type
                or str(existing[2]) != source_id
                or str(existing[3]) != target_id
            ):
                raise _ZeroWriteError("relationship_correction_replay_mismatch")
            new_confidence, new_cred, _ = self._merge_relationship_supports(
                db, new_id, item, str(existing[4]), now_text
            )
        else:
            if existing is None:
                db.execute(
                    "INSERT INTO relationship (id, world_id, source_entity_id, "
                    "target_entity_id, relation_type, content, formed_by, "
                    "confidence, cred_status, invalid_at, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        new_id,
                        job.subject_id,
                        source_id,
                        target_id,
                        item.relation_type,
                        item.proposition,
                        item.formed_by,
                        confidence,
                        item.cred_status_for(confidence),
                        now_text,
                        now_text,
                    ),
                )
            else:
                # Revive a historical row under the same deterministic id.
                db.execute(
                    "UPDATE relationship SET world_id = ?, source_entity_id = ?, "
                    "target_entity_id = ?, relation_type = ?, content = ?, "
                    "formed_by = ?, confidence = ?, cred_status = ?, "
                    "invalid_at = NULL, updated_at = ? WHERE id = ?",
                    (
                        job.subject_id,
                        source_id,
                        target_id,
                        item.relation_type,
                        item.proposition,
                        item.formed_by,
                        confidence,
                        item.cred_status_for(confidence),
                        now_text,
                        new_id,
                    ),
                )
            for evidence_id, start, end, _slice in item.supports:
                _ensure_relationship_support_link(db, new_id, evidence_id)
                self._write_relationship_ledger(
                    db, new_id, evidence_id, start, end
                )
            new_confidence = confidence
            new_cred = item.cred_status_for(confidence)
        self._write_relationship_correction_ledger(db, job, prior_id, new_id)
        return (
            self._relationship_correction_outcome(
                item, prior_id, new_id, new_confidence,
                cred_status=new_cred, target_entity_id=target_id,
                source_entity_id=source_id,
            ),
            True,
        )

    # ── V7: first-class World Event ─────────────────────────────────────────

    def _apply_v4_event(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem,
        now_text: str,
    ) -> tuple[dict[str, object], bool]:
        """Form (or support-merge) one first-class World Event.

        The narrative is the verbatim stated proposition; participants/objects
        resolve through the shared mention→identity contract (lazily creating
        entities; "用户" resolves to the owner entity).  Time facets are
        optional (Owner decision 2026-08-16: time may be empty).
        """
        participant_ids: list[str] = []
        entities_created = False
        for name, kind in item.event_participants:
            if name == OWNER_ENTITY_NAME:
                participant_id, created = self._ensure_owner_entity(db, job, now_text)
                participant_ids.append(participant_id)
            else:
                participant_id, created = self._resolve_target_entity(
                    db, job, name, kind, now_text
                )
                participant_ids.append(participant_id)
            entities_created = entities_created or created
        object_ids: list[str] = []
        for name, kind in item.event_objects:
            if name == OWNER_ENTITY_NAME:
                object_id, created = self._ensure_owner_entity(db, job, now_text)
                object_ids.append(object_id)
            else:
                object_id, created = self._resolve_target_entity(
                    db, job, name, kind, now_text
                )
                object_ids.append(object_id)
            entities_created = entities_created or created
        event_id = world_event_id_for(job.subject_id, item.proposition)
        existing = db.execute(
            "SELECT content, formed_by, occurred_at, time_expression, "
            "participants_json, objects_json, confidence FROM world_event "
            "WHERE id = ?",
            (event_id,),
        ).fetchone()
        if existing is None:
            confidence = item.confidence_for(len(item.supports))
            db.execute(
                "INSERT INTO world_event (id, world_id, content, occurred_at, "
                "time_expression, participants_json, objects_json, formed_by, "
                "confidence, cred_status, invalid_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    event_id,
                    job.subject_id,
                    item.proposition,
                    item.event_occurred_at,
                    item.event_time_expression,
                    _canonical(participant_ids),
                    _canonical(object_ids),
                    item.formed_by,
                    confidence,
                    item.cred_status_for(confidence),
                    now_text,
                    now_text,
                ),
            )
            for evidence_id, start, end, _slice in item.supports:
                _ensure_world_event_link(db, event_id, evidence_id)
                ledger_id = "evidence-ledger-" + _hash_text(
                    _canonical(["world_event", event_id, evidence_id, start, end])
                )
                db.execute(
                    "INSERT OR IGNORE INTO evidence_ledger (id, content, "
                    "payload_json) VALUES (?, ?, ?)",
                    (
                        ledger_id,
                        _canonical(
                            {
                                "relation": "support",
                                "world_event_id": event_id,
                                "evidence_id": evidence_id,
                            }
                        ),
                        _canonical(
                            {
                                "schema_version": 1,
                                "start": start,
                                "end": end,
                            }
                        ),
                    ),
                )
            return (
                self._event_outcome(
                    item, event_id, participant_ids, object_ids, confidence
                ),
                True,
            )
        # Same-ID support merge (restated event).
        if (
            str(existing[0]) != item.proposition
            or str(existing[1]) != item.formed_by
            or str(existing[2] or "") != (item.event_occurred_at or "")
            or str(existing[3] or "") != (item.event_time_expression or "")
            or _canonical(json.loads(str(existing[4]) or "[]"))
            != _canonical(participant_ids)
            or _canonical(json.loads(str(existing[5]) or "[]"))
            != _canonical(object_ids)
        ):
            raise _ZeroWriteError("event_replay_mismatch")
        added = 0
        for evidence_id, _start, _end, _slice in item.supports:
            added += _ensure_world_event_link(db, event_id, evidence_id)
        linked = int(
            db.execute(
                "SELECT COUNT(DISTINCT evidence_id) FROM world_event_evidence "
                "WHERE world_event_id = ?",
                (event_id,),
            ).fetchone()[0]
        )
        new_confidence = min(
            FORMED_BY_BASES[item.formed_by]
            + min(max(linked - 1, 0), SUPPORT_CAP) * SUPPORT_STEP,
            CONFIDENCE_HARD_MAX,
        )
        if added or new_confidence != int(existing[6]):
            db.execute(
                "UPDATE world_event SET confidence = ?, cred_status = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    new_confidence,
                    item.cred_status_for(new_confidence),
                    now_text,
                    event_id,
                ),
            )
            wrote = True
        else:
            wrote = False
        wrote = wrote or entities_created
        return (
            self._event_outcome(
                item, event_id, participant_ids, object_ids, new_confidence
            ),
            wrote,
        )

    def _event_outcome(
        self,
        item: BatchItem,
        event_id: str,
        participant_ids: list[str],
        object_ids: list[str],
        confidence: int,
    ) -> dict[str, object]:
        outcome: dict[str, object] = {
            "action": "form",
            "statement_kind": "event",
            "event_id": event_id,
            "formed_by": item.formed_by,
            "confidence": confidence,
            "cred_status": item.cred_status_for(confidence),
            "evidence_count": len(item.supports),
        }
        if item.event_occurred_at is not None:
            outcome["occurred_at"] = item.event_occurred_at
        if item.event_time_expression is not None:
            outcome["time_expression"] = item.event_time_expression
        if participant_ids:
            outcome["participants"] = participant_ids
        if object_ids:
            outcome["objects"] = object_ids
        return outcome

    # ── V6: event correct (replacement) ─────────────────────────────────────

    def _apply_event_correct(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem,
        now_text: str,
    ) -> tuple[dict[str, object], bool]:
        """Replace one current World Event with a corrected narrative.

        Mirrors the relationship 改口替换 precedent: the prior row must be
        current, the replacement carries the same value-field contract as a
        form, and the typed history is the invalid_at transition plus the
        corrects ledger (events have no frozen transitions table).
        """
        prior_id = str(item.corrects_event_id)
        new_id = world_event_id_for(job.subject_id, item.proposition)
        prior = db.execute(
            "SELECT content, world_id, invalid_at FROM world_event WHERE id = ?",
            (prior_id,),
        ).fetchone()
        if prior is None:
            raise _ZeroWriteError("event_correction_target_unknown")
        if prior[2] is not None:
            # Deterministic replay of an already-applied correction.
            new_row = db.execute(
                "SELECT content, invalid_at FROM world_event WHERE id = ?",
                (new_id,),
            ).fetchone()
            if (
                new_row is None
                or new_row[1] is not None
                or str(new_row[0]) != item.proposition
            ):
                raise _ZeroWriteError("event_correction_replay_mismatch")
            confidence = item.confidence_for(len(item.supports))
            return (
                self._event_correction_outcome(
                    item, prior_id, new_id, confidence
                ),
                False,
            )
        if str(prior[1]) != job.subject_id:
            raise _ZeroWriteError("event_correction_subject_mismatch")
        if item.proposition == str(prior[0]):
            raise _ZeroWriteError("correction_identical_proposition")
        participant_ids: list[str] = []
        for name, kind in item.event_participants:
            participant_ids.append(
                self._ensure_owner_entity(db, job, now_text)[0]
                if name == OWNER_ENTITY_NAME
                else self._resolve_target_entity(db, job, name, kind, now_text)[0]
            )
        object_ids: list[str] = []
        for name, kind in item.event_objects:
            object_ids.append(
                self._ensure_owner_entity(db, job, now_text)[0]
                if name == OWNER_ENTITY_NAME
                else self._resolve_target_entity(db, job, name, kind, now_text)[0]
            )
        cursor = db.execute(
            "UPDATE world_event SET invalid_at = ?, updated_at = ? "
            "WHERE id = ? AND invalid_at IS NULL",
            (now_text, now_text, prior_id),
        )
        if cursor.rowcount != 1:
            raise _ZeroWriteError("event_correction_target_not_current")
        confidence = item.confidence_for(len(item.supports))
        existing = db.execute(
            "SELECT content, invalid_at, confidence, cred_status "
            "FROM world_event WHERE id = ?",
            (new_id,),
        ).fetchone()
        if existing is not None and existing[1] is None:
            # The replacement narrative is already current: forward supports.
            if str(existing[0]) != item.proposition:
                raise _ZeroWriteError("event_correction_replay_mismatch")
            added = 0
            for evidence_id, _s, _e, _t in item.supports:
                added += _ensure_world_event_link(db, new_id, evidence_id)
            linked = int(
                db.execute(
                    "SELECT COUNT(DISTINCT evidence_id) FROM world_event_evidence "
                    "WHERE world_event_id = ?",
                    (new_id,),
                ).fetchone()[0]
            )
            new_confidence = min(
                FORMED_BY_BASES[item.formed_by]
                + min(max(linked - 1, 0), SUPPORT_CAP) * SUPPORT_STEP,
                CONFIDENCE_HARD_MAX,
            )
            if added or new_confidence != int(existing[2]):
                db.execute(
                    "UPDATE world_event SET confidence = ?, cred_status = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        new_confidence,
                        item.cred_status_for(new_confidence),
                        now_text,
                        new_id,
                    ),
                )
        else:
            if existing is None:
                db.execute(
                    "INSERT INTO world_event (id, world_id, content, "
                    "occurred_at, time_expression, participants_json, "
                    "objects_json, formed_by, confidence, cred_status, "
                    "invalid_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        new_id,
                        job.subject_id,
                        item.proposition,
                        item.event_occurred_at,
                        item.event_time_expression,
                        _canonical(participant_ids),
                        _canonical(object_ids),
                        item.formed_by,
                        confidence,
                        item.cred_status_for(confidence),
                        now_text,
                        now_text,
                    ),
                )
            else:
                # Revive a historical row under the same deterministic id.
                db.execute(
                    "UPDATE world_event SET world_id = ?, content = ?, "
                    "occurred_at = ?, time_expression = ?, participants_json = ?, "
                    "objects_json = ?, formed_by = ?, confidence = ?, "
                    "cred_status = ?, invalid_at = NULL, updated_at = ? "
                    "WHERE id = ?",
                    (
                        job.subject_id,
                        item.proposition,
                        item.event_occurred_at,
                        item.event_time_expression,
                        _canonical(participant_ids),
                        _canonical(object_ids),
                        item.formed_by,
                        confidence,
                        item.cred_status_for(confidence),
                        now_text,
                        new_id,
                    ),
                )
            for evidence_id, start, end, _slice in item.supports:
                _ensure_world_event_link(db, new_id, evidence_id)
                ledger_id = "evidence-ledger-" + _hash_text(
                    _canonical(["world_event", new_id, evidence_id, start, end])
                )
                db.execute(
                    "INSERT OR IGNORE INTO evidence_ledger (id, content, "
                    "payload_json) VALUES (?, ?, ?)",
                    (
                        ledger_id,
                        _canonical(
                            {
                                "relation": "support",
                                "world_event_id": new_id,
                                "evidence_id": evidence_id,
                            }
                        ),
                        _canonical(
                            {"schema_version": 1, "start": start, "end": end}
                        ),
                    ),
                )
        ledger_id = "evidence-ledger-" + _hash_text(
            _canonical(["event_correction", prior_id, new_id])
        )
        db.execute(
            "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) "
            "VALUES (?, ?, ?)",
            (
                ledger_id,
                _canonical(
                    {
                        "relation": "corrects",
                        "prior_event_id": prior_id,
                        "replacement_event_id": new_id,
                    }
                ),
                _canonical(
                    {
                        "schema_version": 1,
                        "boundary_event_id": job.boundary_event_id,
                    }
                ),
            ),
        )
        return (
            self._event_correction_outcome(
                item, prior_id, new_id, confidence
            ),
            True,
        )

    def _event_correction_outcome(
        self,
        item: BatchItem,
        prior_id: str,
        new_id: str,
        confidence: int,
    ) -> dict[str, object]:
        return {
            "action": "correct",
            "statement_kind": "event",
            "formed_by": item.formed_by,
            "prior_event_id": prior_id,
            "replacement_event_id": new_id,
            "confidence": confidence,
            "cred_status": item.cred_status_for(confidence),
            "evidence_count": len(item.supports),
        }

    # ── V6: retract (correct with NO replacement) ───────────────────────────

    def _apply_retract(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem,
        now_text: str,
    ) -> tuple[dict[str, object], bool]:
        """Invalidate one current cognition/relationship/event on explicit
        retraction.

        Owner decision 2026-08-16: retract is the replacement-less case of
        correct — the prior object goes invalid via an explicit transition,
        Recall stops returning it, and the raw Evidence plus provenance stay
        queryable.  The typed evolution history lives in the Python-owned
        ``retraction`` sidecar (the frozen 1.x ``cognition_transitions`` table
        requires a replacement id and stays untouched).
        """
        is_relationship = item.statement_kind == "relationship"
        is_event = item.statement_kind == "event"
        if is_event:
            prior_id = str(item.corrects_event_id)
            prior_label = "prior_event_id"
        elif is_relationship:
            prior_id = str(item.corrects_relationship_id)
            prior_label = "prior_relationship_id"
        else:
            prior_id = str(item.corrects_cognition_id)
            prior_label = "prior_cognition_id"
        retraction_id = "retraction-" + _hash_text(
            _canonical(["retracts", prior_id])
        )
        if is_event:
            prior = db.execute(
                "SELECT content, world_id, invalid_at FROM world_event "
                "WHERE id = ?",
                (prior_id,),
            ).fetchone()
        elif is_relationship:
            prior = db.execute(
                "SELECT content, world_id, invalid_at FROM relationship "
                "WHERE id = ?",
                (prior_id,),
            ).fetchone()
        else:
            prior = db.execute(
                "SELECT content, subject_id, invalid_at, archived_at "
                "FROM cognition WHERE id = ?",
                (prior_id,),
            ).fetchone()
        if prior is None:
            raise _ZeroWriteError("retraction_target_unknown")
        already_invalid = prior[2] is not None or (
            not is_relationship and not is_event and prior[3] is not None
        )
        if already_invalid:
            # Deterministic replay of an already-applied retract: the recorded
            # sidecar row must agree; nothing is written again.
            row = db.execute(
                "SELECT 1 FROM retraction WHERE id = ?", (retraction_id,)
            ).fetchone()
            if row is None:
                raise _ZeroWriteError("retraction_replay_mismatch")
            return (
                {
                    "action": "correct",
                    "statement_kind": item.statement_kind,
                    "retract": True,
                    prior_label: prior_id,
                    "formed_by": item.formed_by,
                    "evidence_count": len(item.supports),
                },
                False,
            )
        if str(prior[1]) != job.subject_id:
            raise _ZeroWriteError("retraction_target_subject_mismatch")
        revision = self._current_revision(db) + 1
        if is_event:
            cursor = db.execute(
                "UPDATE world_event SET invalid_at = ?, updated_at = ? "
                "WHERE id = ? AND invalid_at IS NULL",
                (now_text, now_text, prior_id),
            )
        elif is_relationship:
            cursor = db.execute(
                "UPDATE relationship SET invalid_at = ?, updated_at = ? "
                "WHERE id = ? AND invalid_at IS NULL",
                (now_text, now_text, prior_id),
            )
        else:
            cursor = db.execute(
                "UPDATE cognition SET invalid_at = ?, updated_at = ? "
                "WHERE id = ? AND invalid_at IS NULL AND archived_at IS NULL",
                (now_text, now_text, prior_id),
            )
        if cursor.rowcount != 1:
            raise _ZeroWriteError("retraction_target_not_current")
        db.execute(
            "INSERT OR IGNORE INTO retraction (id, prior_cognition_id, "
            "prior_relationship_id, prior_event_id, reason, revision, "
            "created_at) VALUES (?, ?, ?, ?, 'retracts', ?, ?)",
            (
                retraction_id,
                prior_id if not is_relationship and not is_event else None,
                prior_id if is_relationship else None,
                prior_id if is_event else None,
                revision,
                now_text,
            ),
        )
        ledger_id = "evidence-ledger-" + _hash_text(
            _canonical(["retracts", prior_id])
        )
        db.execute(
            "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) "
            "VALUES (?, ?, ?)",
            (
                ledger_id,
                _canonical({"relation": "retracts", prior_label: prior_id}),
                _canonical(
                    {
                        "schema_version": 1,
                        "boundary_event_id": job.boundary_event_id,
                        "evidence_ids": [
                            evidence_id
                            for evidence_id, _s, _e, _t in item.supports
                        ],
                    }
                ),
            ),
        )
        return (
            {
                "action": "correct",
                "statement_kind": item.statement_kind,
                "retract": True,
                prior_label: prior_id,
                "formed_by": item.formed_by,
                "evidence_count": len(item.supports),
            },
            True,
        )

    # ── V6: contradict (same-ID contradictory Evidence, downgrade only) ─────

    def _apply_contradict(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem,
        now_text: str,
    ) -> tuple[dict[str, object], bool]:
        """Attach contradictory Evidence to one current cognition's own chain.

        Authority §4.7: contradiction rides the SAME cognition ID (the
        cognition is not replaced); confidence/credibility recompute from the
        complete chain.  Owner decision 2026-08-16: downgrade only — a
        contradiction pins the weakest carrier to base 0 but never invalidates.
        """
        target = str(item.contradicts_cognition_id)
        row = db.execute(
            "SELECT content, subject_id, invalid_at, archived_at, formed_by "
            "FROM cognition WHERE id = ?",
            (target,),
        ).fetchone()
        if row is None:
            raise _ZeroWriteError("contradiction_target_unknown")
        if row[2] is not None or row[3] is not None:
            raise _ZeroWriteError("contradiction_target_not_current")
        if str(row[1]) != job.subject_id:
            raise _ZeroWriteError("contradiction_target_subject_mismatch")
        added = 0
        for evidence_id, _start, _end, _slice in item.supports:
            added += _ensure_support_link(
                db, target, evidence_id, relation="contradict"
            )
        confidence, cred = self._recompute_chain_confidence(
            db, target, [str(row[4])]
        )
        current = db.execute(
            "SELECT confidence, cred_status FROM cognition WHERE id = ?",
            (target,),
        ).fetchone()
        if added or confidence != int(current[0]) or cred != str(current[1]):
            db.execute(
                "UPDATE cognition SET confidence = ?, cred_status = ?, "
                "updated_at = ? WHERE id = ?",
                (confidence, cred, now_text, target),
            )
            wrote = True
        else:
            wrote = False
        for evidence_id, start, end, _slice in item.supports:
            ledger_id = "evidence-ledger-" + _hash_text(
                _canonical(["contradict", target, evidence_id, start, end])
            )
            ledger = db.execute(
                "INSERT OR IGNORE INTO evidence_ledger (id, content, "
                "payload_json) VALUES (?, ?, ?)",
                (
                    ledger_id,
                    _canonical(
                        {
                            "relation": "contradict",
                            "cognition_id": target,
                            "evidence_id": evidence_id,
                        }
                    ),
                    _canonical(
                        {"schema_version": 1, "start": start, "end": end}
                    ),
                ),
            )
            wrote = wrote or ledger.rowcount == 1
        return (
            {
                "action": "contradict",
                "statement_kind": item.statement_kind,
                "cognition_id": target,
                "formed_by": item.formed_by,
                "confidence": confidence,
                "cred_status": cred,
                "evidence_count": len(item.supports),
            },
            wrote,
        )

    def _write_relationship_correction_ledger(
        self,
        db: sqlite3.Connection,
        job: ClaimedWorldJob,
        prior_id: str,
        replacement_id: str,
    ) -> None:
        ledger_id = "evidence-ledger-" + _hash_text(
            _canonical(["relationship_correction", prior_id, replacement_id])
        )
        db.execute(
            "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) "
            "VALUES (?, ?, ?)",
            (
                ledger_id,
                _canonical(
                    {
                        "relation": "corrects",
                        "prior_relationship_id": prior_id,
                        "replacement_relationship_id": replacement_id,
                    }
                ),
                _canonical(
                    {
                        "schema_version": 1,
                        "boundary_event_id": job.boundary_event_id,
                    }
                ),
            ),
        )

    def _write_entity_ledger(
        self,
        db: sqlite3.Connection,
        entity_id: str,
        evidence_id: str,
        start: int,
        end: int,
    ) -> None:
        ledger_id = "evidence-ledger-" + _hash_text(
            _canonical(["entity_formation", entity_id, evidence_id, start, end])
        )
        db.execute(
            "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) "
            "VALUES (?, ?, ?)",
            (
                ledger_id,
                _canonical(
                    {
                        "entity_id": entity_id,
                        "evidence_id": evidence_id,
                        "relation": "support",
                    }
                ),
                _canonical(
                    {
                        "schema_version": 1,
                        "start": start,
                        "end": end,
                    }
                ),
            ),
        )

    def _write_relationship_ledger(
        self,
        db: sqlite3.Connection,
        relationship_id: str,
        evidence_id: str,
        start: int,
        end: int,
    ) -> None:
        ledger_id = "evidence-ledger-" + _hash_text(
            _canonical(
                ["relationship_formation", relationship_id, evidence_id, start, end]
            )
        )
        db.execute(
            "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) "
            "VALUES (?, ?, ?)",
            (
                ledger_id,
                _canonical(
                    {
                        "relationship_id": relationship_id,
                        "evidence_id": evidence_id,
                        "relation": "support",
                    }
                ),
                _canonical(
                    {
                        "schema_version": 1,
                        "start": start,
                        "end": end,
                    }
                ),
            ),
        )

    def _apply_correct(
        self, db: sqlite3.Connection, job: ClaimedWorldJob, item: BatchItem,
        now_text: str,
    ) -> tuple[dict[str, object], bool, Optional[tuple[str, str]]]:
        prior_id = str(item.corrects_cognition_id)
        new_id = item.cognition_id(job.subject_id)
        target_entity_id: Optional[str] = (
            entity_id_for(job.subject_id, item.entity_canonical_name)
            if item.entity_canonical_name is not None
            and item.statement_kind in {"attribute", "preference"}
            else None
        )
        perspective_entity_id: Optional[str] = (
            entity_id_for(job.subject_id, item.perspective_holder_name)
            if item.perspective_holder_name is not None
            else None
        )
        transition = db.execute(
            "SELECT replacement_cognition_id FROM cognition_transitions "
            "WHERE prior_cognition_id = ?",
            (prior_id,),
        ).fetchone()
        if transition is not None:
            # Replay of an already-applied correction: the deterministic
            # checkpoint must reproduce the same terminal outcome without
            # writing anything again.
            if str(transition[0]) != new_id:
                # A fresh correction targeting an already-superseded prior is
                # the dominant reading of this state; a tampered replay is the
                # degenerate one.  Both are zero-write.
                raise _ZeroWriteError("correction_target_not_current")
            exists = db.execute(
                "SELECT 1 FROM cognition WHERE id = ?", (new_id,)
            ).fetchone()
            if exists is None:
                raise _ZeroWriteError("correction_replay_mismatch")
            confidence = item.confidence_for(len(item.supports))
            return (
                self._item_outcome(
                    item, new_id, prior=prior_id, confidence=confidence,
                    target_entity_id=target_entity_id,
                    perspective_entity_id=perspective_entity_id,
                ),
                False,
                None,
            )
        prior = db.execute(
            "SELECT content, subject_id, invalid_at, archived_at FROM cognition "
            "WHERE id = ?",
            (prior_id,),
        ).fetchone()
        if prior is None:
            raise _ZeroWriteError("correction_target_unknown")
        if prior[2] is not None or prior[3] is not None:
            raise _ZeroWriteError("correction_target_not_current")
        if str(prior[1]) != job.subject_id:
            raise _ZeroWriteError("correction_target_subject_mismatch")
        prior_target = db.execute(
            "SELECT target_entity_id, perspective_entity_id FROM cognition_target "
            "WHERE cognition_id = ?",
            (prior_id,),
        ).fetchone()
        prior_target_ids = (
            (None, None)
            if prior_target is None
            else (
                str(prior_target[0]),
                None if prior_target[1] is None else str(prior_target[1]),
            )
        )
        if prior_target_ids != (target_entity_id, perspective_entity_id):
            raise _ZeroWriteError("correction_target_entity_mismatch")
        if item.proposition == str(prior[0]):
            raise _ZeroWriteError("correction_identical_proposition")
        # The replacement's deterministic ID must not collide with a DIFFERENT
        # current cognition (a merge the user never stated).  Lockable? No —
        # zero write (§4.7).
        colliding = db.execute(
            "SELECT 1 FROM cognition WHERE id = ? AND id != ? "
            "AND invalid_at IS NULL AND archived_at IS NULL",
            (new_id, prior_id),
        ).fetchone()
        if colliding is not None:
            raise _ClarificationError(
                "correction_merge_ambiguous",
                "有两个当前认知都可能是这次纠正的目标，需要你澄清指的是哪一个",
            )
        cursor = db.execute(
            "UPDATE cognition SET invalid_at = ?, updated_at = ? "
            "WHERE id = ? AND invalid_at IS NULL AND archived_at IS NULL",
            (now_text, now_text, prior_id),
        )
        if cursor.rowcount != 1:
            raise _ZeroWriteError("correction_target_not_current")
        confidence = item.confidence_for(len(item.supports))
        if item.entity_canonical_name is not None and item.statement_kind in {"attribute", "preference"}:
            # V5: a correction may replace a targeted attribute (with an
            # optional perspective holder); lazily materialize the entities
            # and record the sidecar (ids computed above are deterministic).
            assert item.entity_kind is not None
            self._resolve_target_entity(
                db, job, item.entity_canonical_name, item.entity_kind, now_text
            )
            if item.perspective_holder_name is not None:
                assert item.perspective_holder_kind is not None
                self._resolve_target_entity(
                    db, job, item.perspective_holder_name,
                    item.perspective_holder_kind, now_text,
                )
        if item.entity_canonical_name is None:
            self._sync_owner_alias(db, job, item.proposition, now_text)
        self._write_cognition(
            db, job, item, new_id, confidence, now_text,
            target_entity_id=target_entity_id,
            perspective_entity_id=perspective_entity_id,
        )
        self._write_correction_ledger(db, job, prior_id, new_id)
        return (
            self._item_outcome(
                item, new_id, prior=prior_id, confidence=confidence,
                target_entity_id=target_entity_id,
                perspective_entity_id=perspective_entity_id,
            ),
            True,
            (prior_id, new_id),
        )

    def _item_outcome(
        self,
        item: BatchItem,
        cognition_id: str,
        *,
        prior: Optional[str] = None,
        confidence: Optional[int] = None,
        cred_status: Optional[str] = None,
        target_entity_id: Optional[str] = None,
        source_entity_id: Optional[str] = None,
        perspective_entity_id: Optional[str] = None,
    ) -> dict[str, object]:
        final_confidence = (
            confidence
            if confidence is not None
            else item.confidence_for(len(item.supports))
        )
        final_cred = (
            cred_status
            if cred_status is not None
            else item.cred_status_for(final_confidence)
        )
        outcome: dict[str, object] = {
            "action": item.action,
            "statement_kind": item.statement_kind,
            "formed_by": item.formed_by,
            "confidence": final_confidence,
            "cred_status": final_cred,
            "evidence_count": len(item.supports),
        }
        if item.statement_kind == "relationship":
            outcome["relationship_id"] = cognition_id
            outcome["relation_type"] = item.relation_type
            outcome["target_entity_id"] = target_entity_id
            outcome["source_entity_id"] = source_entity_id
        elif item.action == "form":
            outcome["cognition_id"] = cognition_id
            if target_entity_id is not None:
                outcome["target_entity_id"] = target_entity_id
            if perspective_entity_id is not None:
                outcome["perspective_entity_id"] = perspective_entity_id
        else:
            outcome["prior_cognition_id"] = prior
            outcome["replacement_cognition_id"] = cognition_id
        return outcome

    def _write_cognition(
        self,
        db: sqlite3.Connection,
        job: ClaimedWorldJob,
        item: BatchItem,
        cognition_id: str,
        confidence: int,
        now_text: str,
        *,
        target_entity_id: Optional[str] = None,
        perspective_entity_id: Optional[str] = None,
    ) -> None:
        db.execute(
            """INSERT INTO cognition (
                 id, subject_id, content, content_type, formed_by,
                 confidence, cred_status, scope, valid_at, invalid_at,
                 asked_at, archived_at, muted_at, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL,
                         NULL, NULL, NULL, ?, ?)""",
            (
                cognition_id,
                job.subject_id,
                item.proposition,
                item.content_type,
                item.formed_by,
                confidence,
                item.cred_status_for(confidence),
                now_text,
                now_text,
            ),
        )
        if target_entity_id is not None:
            db.execute(
                "INSERT OR IGNORE INTO cognition_target (cognition_id, "
                "target_entity_id, perspective_entity_id) VALUES (?, ?, ?)",
                (cognition_id, target_entity_id, perspective_entity_id),
            )
        for evidence_id, start, end, _slice in item.supports:
            _ensure_support_link(db, cognition_id, evidence_id)
            ledger_id = "evidence-ledger-" + _hash_text(
                _canonical(["formation", cognition_id, evidence_id, start, end])
            )
            db.execute(
                "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) "
                "VALUES (?, ?, ?)",
                (
                    ledger_id,
                    _canonical(
                        {
                            "cognition_id": cognition_id,
                            "evidence_id": evidence_id,
                            "relation": "support",
                        }
                    ),
                    _canonical(
                        {
                            "schema_version": 1,
                            "start": start,
                            "end": end,
                            "span_hash": _hash_text(_slice),
                        }
                    ),
                ),
            )

    def _write_correction_ledger(
        self,
        db: sqlite3.Connection,
        job: ClaimedWorldJob,
        prior_id: str,
        replacement_id: str,
    ) -> None:
        ledger_id = "evidence-ledger-" + _hash_text(
            _canonical(["correction", prior_id, replacement_id])
        )
        db.execute(
            "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) "
            "VALUES (?, ?, ?)",
            (
                ledger_id,
                _canonical(
                    {
                        "relation": "corrects",
                        "prior_cognition_id": prior_id,
                        "replacement_cognition_id": replacement_id,
                    }
                ),
                _canonical(
                    {
                        "schema_version": 1,
                        "boundary_event_id": job.boundary_event_id,
                    }
                ),
            ),
        )

    def _write_transition(
        self,
        db: sqlite3.Connection,
        prior_id: str,
        replacement_id: str,
        revision: int,
    ) -> None:
        transition_id = "cognition-transition-" + _hash_text(
            _canonical(["corrects", prior_id, replacement_id])
        )
        db.execute(
            "INSERT OR IGNORE INTO cognition_transitions ("
            "id, prior_cognition_id, replacement_cognition_id, reason, revision"
            ") VALUES (?, ?, ?, 'corrects', ?)",
            (transition_id, prior_id, replacement_id, revision),
        )

    def _bump_memory_state(self, db: sqlite3.Connection) -> int:
        return advance_world_revision(db)

    def _current_revision(self, db: sqlite3.Connection) -> int:
        row = db.execute(
            "SELECT revision FROM memory_state WHERE singleton = 1"
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def _world_outcome(
        self,
        job: ClaimedWorldJob,
        batch: _CompiledBatch,
        results: list[dict[str, object]],
        revision: int,
        *,
        state: Literal["applied", "no_change"] = "applied",
        reason: str | None = None,
    ) -> dict[str, object]:
        if batch.legacy:
            item = results[0]
            legacy_outcome: dict[str, object] = {
                "schema_version": LEGACY_INTERPRETATION_SCHEMA_VERSION,
                "state": state,
                "cognition_id": item["cognition_id"],
                "statement_kind": item["statement_kind"],
                "confidence": item["confidence"],
                "cred_status": item["cred_status"],
                "world_revision": revision,
                "evidence_count": item["evidence_count"],
                "boundary_event_id": job.boundary_event_id,
            }
            if reason is not None:
                legacy_outcome["reason"] = reason
            return legacy_outcome
        outcome: dict[str, object] = {
            "schema_version": batch.envelope_version,
            "state": state,
            "world_revision": revision,
            "cognitions": results,
            "boundary_event_id": job.boundary_event_id,
        }
        if reason is not None:
            outcome["reason"] = reason
        return outcome


_SPAN_REPAIR_PREFIXES = ("我", "我们", "咱们", "俺", "本人")


def _repair_spans(
    parsed: tuple[tuple[str, int, int, str], ...],
    proposition: str,
    ids: set[str],
    raw_by_id: Mapping[str, str],
) -> tuple[tuple[str, int, int, str], ...]:
    """Relocate imprecise model spans by value + unique-substring (PM-approved
    fallback, V1-DESIGN §3): keep the "value == verbatim slice" contract while
    tolerating model codepoint off-by-N errors (observed live: span (0,4) for a
    5-codepoint statement).  A candidate that occurs exactly once across the
    batch anchors the span; zero or multiple occurrences leave the span
    untouched (fail-closed downstream).
    """
    candidates: list[str] = [proposition]
    if proposition.startswith("用户"):
        for prefix in _SPAN_REPAIR_PREFIXES:
            candidates.append(prefix + proposition[len("用户"):])
        # Subject-less prepend-anchored statements ("用户刷视频…") also match
        # the bare slice ("刷视频…") in the raw evidence.
        candidates.append(proposition[len("用户"):])
    if proposition.startswith("The user"):
        for token, replacement in _EN_FIRST_PERSON:
            if proposition.startswith(replacement):
                candidates.append(token.rstrip().capitalize() + proposition[len(replacement):])
        # English subject-less prepend-anchored statements also match the bare
        # slice in the raw evidence.
        candidates.append(proposition[len("The user "):])
    repaired = []
    for evidence_id, start, end, slice_text in parsed:
        keep = True
        for candidate in candidates:
            if not candidate:
                continue
            occurrences: list[tuple[str, int]] = []
            for eid in sorted(ids):
                raw = raw_by_id.get(eid, "")
                idx = raw.find(candidate)
                while idx != -1:
                    occurrences.append((eid, idx))
                    idx = raw.find(candidate, idx + 1)
                    if len(occurrences) > 1:
                        break
                if len(occurrences) > 1:
                    break
            if len(occurrences) == 1:
                eid, idx = occurrences[0]
                repaired.append(
                    (eid, idx, idx + len(candidate), candidate)
                )
                keep = False
                break
        if keep:
            repaired.append((evidence_id, start, end, slice_text))
    return tuple(repaired)


def _parse_supports(
    supports: object,
    ids: set[str],
    raw_by_id: Mapping[str, str],
) -> tuple[Optional[tuple[tuple[str, int, int, str], ...]], str]:
    if not isinstance(supports, list) or not supports:
        return None, "no_support_span"
    parsed: list[tuple[str, int, int, str]] = []
    for support in supports:
        if not isinstance(support, dict):
            return None, "invalid_support_span"
        support_evidence_id = support.get("evidence_id")
        segment_id = support.get("segment_id")
        quote = support.get("quote")
        start = support.get("start")
        end = support.get("end")
        if support_evidence_id not in ids or support_evidence_id not in raw_by_id:
            return None, "evidence_out_of_batch"
        raw = raw_by_id[str(support_evidence_id)]
        if segment_id is not None:
            if not isinstance(segment_id, str):
                return None, "invalid_support_segment"
            segment = next(
                (value for value in _evidence_segments(raw) if value["id"] == segment_id),
                None,
            )
            if segment is None:
                return None, "support_segment_not_found"
            parsed.append(
                (
                    str(support_evidence_id),
                    int(segment["start"]),
                    int(segment["end"]),
                    str(segment["text"]),
                )
            )
            continue
        if quote is not None:
            if not isinstance(quote, str) or not quote.strip():
                return None, "invalid_support_quote"
            first = raw.find(quote)
            if first < 0:
                return None, "support_quote_not_found"
            if raw.find(quote, first + 1) >= 0:
                return None, "support_quote_ambiguous"
            parsed.append((str(support_evidence_id), first, first + len(quote), quote))
            continue
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
        ):
            return None, "invalid_support_span"
        if not (0 <= start < end <= len(raw)):
            # Out-of-range spans ride a placeholder entry into
            # ``_repair_spans`` (value + unique-substring relocation, PM-approved
            # fallback); only spans that cannot be relocated fail closed.
            parsed.append((str(support_evidence_id), start, end, ""))
            continue
        slice_text = raw[start:end]
        if not slice_text.strip():
            return None, "empty_span"
        parsed.append((str(support_evidence_id), start, end, slice_text))
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            if parsed[i][0] == parsed[j][0]:
                a0, a1 = parsed[i][1], parsed[i][2]
                b0, b1 = parsed[j][1], parsed[j][2]
                if a0 < b1 and b0 < a1:
                    return None, "overlapping_spans"
    return tuple(parsed), ""


class _ZeroWriteError(Exception):
    """Compiler/validation refusal: zero World writes, settle as no_change."""


class _ClarificationError(_ZeroWriteError):
    """Compiler-detected identity/meaning ambiguity: zero World writes, settle
    as clarification_required (AUTHORITY §3). ``display`` carries the human
    clarifying question."""

    def __init__(self, reason: str, display: str) -> None:
        super().__init__(reason)
        self.display = display


def _model_display(value: object) -> str:
    """Require a bounded human question/note for its non-no-change terminal."""

    if not isinstance(value, str):
        raise _ZeroWriteError("invalid_model_result")
    stripped = value.strip()
    if not stripped or len(stripped) > 500:
        raise _ZeroWriteError("invalid_model_result")
    return stripped


def _ensure_support_link(
    db: sqlite3.Connection,
    cognition_id: str,
    evidence_id: str,
    *,
    relation: str = "support",
) -> int:
    """Attach one same-ID Evidence link idempotently; return 1 when new.

    ``cognition_evidence`` carries no unique constraint, so idempotency is
    enforced application-side (check-then-insert) rather than with
    ``INSERT OR IGNORE``.  V6 contradict links ride the same table with
    ``relation='contradict'`` (authority §4.7: same cognition ID, typed
    evolution history).
    """
    existing = db.execute(
        "SELECT 1 FROM cognition_evidence "
        "WHERE cognition_id = ? AND evidence_id = ? AND relation = ?",
        (cognition_id, evidence_id, relation),
    ).fetchone()
    if existing is not None:
        return 0
    db.execute(
        "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
        "VALUES (?, ?, ?)",
        (cognition_id, evidence_id, relation),
    )
    return 1


def _ensure_relationship_support_link(
    db: sqlite3.Connection, relationship_id: str, evidence_id: str, relation: str = "support"
) -> int:
    """Attach one relationship support link idempotently (same contract as
    ``_ensure_support_link`` for the first-class relationship evidence chain).
    """
    existing = db.execute(
        "SELECT 1 FROM relationship_evidence "
        "WHERE relationship_id = ? AND evidence_id = ?",
        (relationship_id, evidence_id),
    ).fetchone()
    if existing is not None:
        return 0
    db.execute(
        "INSERT INTO relationship_evidence (relationship_id, evidence_id, "
        "relation) VALUES (?, ?, ?)",
        (relationship_id, evidence_id, relation),
    )
    return 1


def _ensure_world_event_link(
    db: sqlite3.Connection, world_event_id: str, evidence_id: str
) -> int:
    """Attach one World Event support link idempotently (same contract as
    ``_ensure_support_link`` for the first-class event evidence chain)."""
    existing = db.execute(
        "SELECT 1 FROM world_event_evidence "
        "WHERE world_event_id = ? AND evidence_id = ?",
        (world_event_id, evidence_id),
    ).fetchone()
    if existing is not None:
        return 0
    db.execute(
        "INSERT INTO world_event_evidence (world_event_id, evidence_id, "
        "relation) VALUES (?, ?, 'support')",
        (world_event_id, evidence_id),
    )
    return 1
