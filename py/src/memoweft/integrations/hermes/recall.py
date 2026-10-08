"""Deterministic, read-only Recall over the accepted Personal Memory World.

V1 Recall must satisfy the authority contract (§6.5): read the accepted World
only (read-only connection, no schema creation, no initialization of an empty
World), make zero generation-model calls, and return only current,
lifecycle-eligible, permission-allowed formal claims in a byte-stable order
for the same World revision.  It deliberately does NOT search raw Evidence and
does not inject Evidence text, summaries, or internal IDs.

Matching is a deterministic character-bigram overlap score (Dice) computed
entirely in memory — no FTS index writes, no embedder, no model.  A small,
owner-approved semantic category table closes the observed lexical gap
("水果" vs "荔枝" share no bigrams): it expands the QUERY's category words to
member names ONLY when direct bigram matching produced zero hits (Owner
decision 2026-08-16: fallback-only), keeping the matcher deterministic,
model-free, and byte-stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from ...types import ModelTier
from hashlib import sha256
import json
import re
import sqlite3
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence, TypeVar, cast

from ..trust.currentness import (
    current_entity_aliases,
    CurrentnessSurface,
    subject_currentness_facts,
    world_item_evidence_visible,
    world_item_lifecycle,
    world_item_visible,
)

#: Minimum Dice bigram-overlap score for a claim to be recalled.
MIN_SCORE = 0.15

#: Hard caps for the injected block: item count and total characters.
MAX_ITEMS = 5
MAX_OUTPUT_CHARS = 600

_PREFIX = "记忆"
_KIND_ORDER = {"cognition": 0, "relationship": 1, "event": 2}

_QUOTED_CUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'“([^”]+)”'),
    re.compile(r'"([^"]+)"'),
    re.compile(r'‘([^’]+)’'),
    re.compile(r"'([^']+)'"),
    re.compile(r'「([^」]+)」'),
    re.compile(r'『([^』]+)』'),
)
_CUE_SEPARATOR = re.compile(r'[\s,，。！？?!；;：:“”"‘’\'「」『』（）()\[\]【】]+')
_TOPIC_CONNECTOR = re.compile(r'(?:以及|还有|和|与|跟|、)')
_RECALL_QUESTION_SUFFIX = re.compile(
    r"(?:你)?(?:还)?(?:记得|知道|了解)(?:我)?(?:什么|哪些|多少|吗|么)?$"
)
_INQUIRY_PATTERN: re.Pattern[str] = re.compile(
    r"[?？]"
    r"|(?:什么|哪|谁|怎么|怎样|如何|多少|几|么|吗|呢|何|能否|是否|请问|记得|知道|想问|找出|查找|检索)"
    r"|\b(?:what|which|who|whom|whose|where|when|why|how|is|are|do|does|did|can|could|would|will)\b",
    re.IGNORECASE,
)


_REQUEST_PATTERN = re.compile(
    r"(?:推荐|挑选|选择|帮我选|给我选|帮我挑|给我挑)"
    r"|\b(?:recommend|suggest|choose|pick)\b", re.IGNORECASE,
)
_CORRECTION_PATTERN = re.compile(
    r"(?:说错|记错|不对|改一下|改成|作废|不算数|应当是|应为)"
    r"|\b(?:correction|instead|discard|actually)\b", re.IGNORECASE,
)
_NEGATED_VALUE_CUE = re.compile(r"(?:^|[，,。；;\s])([^，,。；;\s]{1,32}?)(?:作废|不算数|不再使用)")
_REQUEST_NOUN_SUFFIX = re.compile(r"的([\u3400-\u4dbf\u4e00-\u9fff]{2,12})[。！？!?？\s]*$")


def _is_inquiry(query: str) -> bool:
    """Return True if query exhibits interrogative or inquiry characteristics."""
    return bool(_INQUIRY_PATTERN.search(query) or _REQUEST_PATTERN.search(query) or _CORRECTION_PATTERN.search(query))


_LATIN_WORD = re.compile(r"[a-zA-Z0-9_\-]+")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

_GENERIC_PREDICATE_BIGRAMS: frozenset[str] = frozenset({
    # Chinese generic predicate & carrier bigrams
    "用户", "户喜", "我喜", "欢的", "最喜", "欢玩", "喜欢", "更喜", "喜好", "偏好", "平时", "习惯", "经常", "觉得", "认为",
    # English generic predicate, auxiliary & carrier words
    "user", "users", "like", "likes", "liked", "prefer", "prefers",
    "is", "are", "am", "was", "were", "be", "been", "being",
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "at", "by",
    "always", "usually", "often", "think", "thinks", "know", "knows",
})
_HISTORICAL_QUERY_PATTERN: re.Pattern[str] = re.compile(
    r"(?:以前|曾经|过去|原先|之前|早先|从前|先前|那时候|旧的|以前的|谈过|聊过|提过|还记得.*吗|(?:喜欢|爱|爱过|交往|在一起|住|待|用|选)过)"
)
def _strip_question_particles(text: str) -> str:
    """Strip trailing question particles in linear time, preserving 什么/怎么.

    Walk backwards instead of searching an overlapping repeated regex: 了么
    can be parsed both as one token and as two, causing exponential retries
    when a long run is followed by a non-particle character.
    """
    end = len(text)
    while end:
        if text[end - 1] in "吗呢吧呀啊啦了?？":
            end -= 1
        elif text[end - 1] == "么" and (end < 2 or text[end - 2] not in "什怎"):
            end -= 1
        elif end >= 2 and text[end - 2:end] in ("了没", "没有"):
            end -= 2
        else:
            break
    return text[:end]


_PRONOUN_PREFIX: re.Pattern[str] = re.compile(r"^(?:我(?:的)?|你(?:的)?|他(?:的)?|她(?:的)?)")
_IDENTITY_QUERY_PATTERN: re.Pattern[str] = re.compile(
    r"(?:我.*是.*[谁哪]|(?:你.*)?叫我.*[啥什么名字]|(?:怎么|如何|怎样).*称呼(?:我)?|(?:怎么|如何|怎样)叫我|我.*叫(?:什么|啥|名字)|我的(?:名字|姓名|称呼)|叫我什么|叫我啥|叫我|我是谁|是谁|你知道我是谁|你知道我是谁吧|记得我是谁|想起来了吗)"
)
_IDENTITY_TARGETS: tuple[str, ...] = (
    "叫我",
    "称呼",
    "名字",
    "我叫",
    "称呼自己为",
)
_QUERY_WRAPPER_PREFIXES: tuple[str, ...] = (
    "请你直接回答",
    "麻烦你直接回答",
    "请直接回答",
    "麻烦你回答",
    "请你回答",
    "麻烦回答",
    "请回答",
    "请告诉我",
    "请问",
    "你知道我",
    "你知道",
    "你记得我",
    "你记得",
    "你还记得我",
    "你还记得",
    "你了解我",
    "你了解",
)
_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_ENTITY_MENTION_SUFFIXES: tuple[str, ...] = (
    "你还记得",
    "你记得",
    "还记得",
    "记得",
    "怎么样",
    "怎么",
    "为什么",
    "为何",
    "最近",
    "现在",
    "以前",
    "后来",
    "已经",
    "呢",
    "吗",
    "么",
    "的",
    "是",
    "有",
    "在",
    "和",
    "与",
    "跟",
    "、",
    "喜欢",
    "想",
    "爱",
    "会",
    "能",
    "曾",
    "都",
    "也",
    "不",
    "还",
    "又",
    "吧",
    "说",
    "要",
)


@dataclass(frozen=True, slots=True)
class RecallSnapshotV1:
    """Immutable, host-opaque deterministic Recall snapshot (S1 interface)."""

    subject_id: str
    world_revision: int
    selected_item_ids: tuple[tuple[str, str], ...]
    currentness_digest: str
    rendered_recall: str
    recall_snapshot_token: str
    count: int

#: Owner-approved small deterministic category table (query-side expansion
#: only; formation is untouched).  Category word → member names.  Matched as
#: exact substrings of the query (deterministic, no segmentation).
_CATEGORY_MEMBERS: dict[str, tuple[str, ...]] = {
    "水果": ("荔枝", "樱桃", "苹果", "西瓜", "草莓", "芒果", "橙子", "香蕉", "梨"),
    "饮料": ("咖啡", "茶", "奶茶", "拿铁", "美式", "冰美式", "茉莉花茶"),
    "食物": ("玉米", "面条", "米饭", "饺子", "火锅", "面包"),
    "家人": ("爸爸", "妈妈", "女朋友", "男朋友", "老婆", "老公"),
    "朋友": ("朋友", "同学", "同事"),
    "车": ("小鹏", "比亚迪", "特斯拉", "SUV"),
    "谁": ("女生", "男生", "人", "朋友"),
    "游戏": ("王者荣耀", "原神", "英雄联盟", "Steam", "主机游戏"),
    "编程语言": ("Rust", "Python", "Go", "TypeScript", "JavaScript", "C++", "Java"),
    "编程": ("Rust", "Python", "Go", "TypeScript", "JavaScript", "C++", "Java", "Axum", "React"),
    "框架": ("Axum", "React", "Vue", "Spring", "FastAPI"),
}


def _bigrams(text: str) -> set[str]:
    """Extract deterministic semantic tokens for multilingual Dice scoring.

    For CJK characters, extracts adjacent character bigrams within contiguous
    CJK runs (segmentation-free n-gram indexing).
    For Latin/alphanumeric words, extracts lowercased whole-word tokens to avoid
    spurious sub-morpheme letter collisions and whitespace boundary artifacts.
    """
    text = text.strip()
    if not text:
        return set()
    tokens: set[str] = set()
    for word in _LATIN_WORD.findall(text):
        tokens.add(word.lower())
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.add(run)
        else:
            for i in range(len(run) - 1):
                tokens.add(run[i : i + 2])
    return tokens


def _query_signal(query: str) -> str:
    """Remove presentation-only request wrappers before lexical scoring."""

    signal = query.strip()
    for prefix in _QUERY_WRAPPER_PREFIXES:
        pattern = re.compile(
            r"(^|[\s。！？?!；;：:,，])" + re.escape(prefix) + r"[\s：:,，]*"
        )
        signal = pattern.sub(lambda match: match.group(1), signal)
    return signal.strip()


def _anchor_is_explicit(query: str, anchor: str) -> bool:
    """Reject a short entity anchor when it only prefixes a longer CJK term."""

    start = 0
    while True:
        index = query.find(anchor, start)
        if index < 0:
            return False
        if anchor.isascii():
            before = query[index - 1] if index else ""
            after = query[index + len(anchor):index + len(anchor) + 1]
            if any(char and char.isascii() and (char.isalnum() or char == "_") for char in (before, after)):
                start = index + 1
                continue
            return True
        elif query[:index].endswith(("给", "跟", "和", "对", "找", "叫", "问", "帮")):
            return True
        suffix = query[index + len(anchor) :]
        if not suffix or not _CJK_CHARACTER.fullmatch(suffix[0]):
            return True
        if suffix.startswith(_ENTITY_MENTION_SUFFIXES):
            return True
        start = index + 1


def _row_match_text(query: str, row: Mapping[str, object]) -> str:
    text = str(row.get("match_text") or row["content"])
    raw_anchors = row.get("anchors") or ()
    anchors: tuple[str, ...]
    if isinstance(raw_anchors, str):
        anchors = (raw_anchors,)
    elif isinstance(raw_anchors, Sequence):
        anchors = tuple(str(anchor) for anchor in raw_anchors)
    else:
        anchors = ()
    for anchor in anchors:
        if anchor and anchor in query and not _anchor_is_explicit(query, anchor):
            text = text.replace(anchor, " ")
    return text


def _expanded_queries(query: str) -> list[str]:
    """Query-side category expansion: original first, then one expansion per
    category word found in the query (fallback-only, deterministic)."""
    variants: list[str] = [query]
    for category, members in _CATEGORY_MEMBERS.items():
        if category not in query:
            continue
        for member in members:
            variants.append(query.replace(category, member, 1))
    return variants


def _row_match_variants(query: str, row: Mapping[str, object]) -> tuple[str, ...]:
    return (_row_match_text(query, row),
            *cast(Sequence[str], row.get("predecessor_match_texts", ())))


def _explicit_query_cues(query: str) -> tuple[str, ...]:
    """Return stable, user-authored local cues from an otherwise long query.

    Recall first scores the complete query exactly as before.  These cues are
    used only after that produces zero hits, preventing surrounding question
    phrasing from diluting an explicitly quoted or punctuation-delimited topic.
    No inference, model call, stemming, or mutable index is involved.
    """
    cues: list[str] = []
    # A recommendation request can name its topic only at the end of a long
    # modifier phrase. Retry that literal noun; never expand to unseen domains.
    if _REQUEST_PATTERN.search(query):
        noun = _REQUEST_NOUN_SUFFIX.search(query)
        if noun:
            cues.append(noun.group(1))
    if _CORRECTION_PATTERN.search(query):
        # The explicitly rejected value is a literal lookup cue for the prior
        # current claim; it is not a new fact and never bypasses permissions.
        cues.extend(match.group(1) for match in _NEGATED_VALUE_CUE.finditer(query))
    for pattern in _QUOTED_CUE_PATTERNS:
        cues.extend(match.strip() for match in pattern.findall(query))
    separated = tuple(
        part.strip() for part in _CUE_SEPARATOR.split(query) if part.strip()
    )
    cues.extend(separated)
    for part in separated:
        stripped = _strip_question_particles(part).strip()
        stripped = _PRONOUN_PREFIX.sub("", stripped).strip()
        if stripped and stripped != query.strip() and len(stripped) <= 128:
            cues.append(stripped)
        if not part.startswith("关于") or len(part) <= len("关于"):
            continue
        topic = part[len("关于") :]
        topic = _RECALL_QUESTION_SUFFIX.sub("", topic).strip()
        if not topic:
            continue
        cues.append(topic)
        cues.extend(cue.strip() for cue in _TOPIC_CONNECTOR.split(topic))
        if topic in ("我", "我自己"):
            cues.extend(_IDENTITY_TARGETS)
    for category, members in _CATEGORY_MEMBERS.items():
        if category in query:
            cues.extend(members)
        cues.extend(member for member in members if member in query)
    if _IDENTITY_QUERY_PATTERN.search(query):
        cues.extend(_IDENTITY_TARGETS)
    return tuple(
        dict.fromkeys(
            cue for cue in cues if cue and cue != query.strip() and len(cue) <= 128
        )
    )


def _fallback_query_variants(query: str) -> tuple[str, ...]:
    """Preserve identity targets first, category fallback, then try explicit local cues."""
    variants: list[str] = []
    if _IDENTITY_QUERY_PATTERN.search(query):
        variants.extend(_IDENTITY_TARGETS)
    variants.extend(_expanded_queries(query)[1:])
    for cue in _explicit_query_cues(query):
        variants.append(cue)
        variants.extend(_expanded_queries(cue)[1:])
    return tuple(dict.fromkeys(variants))


_ScoredRow = TypeVar("_ScoredRow", bound=Mapping[str, object])


def _local_inquiry_matches(
    query: str,
    rows: list[Mapping[str, object]],
    score_rows: Callable[[str, Iterable[Mapping[str, object]]], list[_ScoredRow]],
) -> list[_ScoredRow]:
    """Retry an unanswered long question with short, literal CJK spans.

    A qualifier between two topic words breaks their adjacency in a claim.
    Only rows sharing at least two non-generic bigrams with a local query
    span may participate, so a generic predicate or one shared word cannot
    turn an otherwise unrelated claim into a hit.
    """
    if not _is_inquiry(query):
        return []
    best: dict[str, _ScoredRow] = {}
    for run in _CJK_RUN.findall(query):
        if len(run) < 4:
            continue
        windows = (run,) if len(run) <= 8 else (
            run[index : index + 8] for index in range(len(run) - 7)
        )
        for window in windows:
            informative = _bigrams(window) - _GENERIC_PREDICATE_BIGRAMS
            if len(informative) < 2:
                continue
            eligible = [
                row for row in rows
                if any(len(informative & _bigrams(text)) >= 2
                       for text in _row_match_variants(window, row))
            ]
            for hit in score_rows(window, eligible):
                key = str(hit["id"])
                if key not in best or float(cast(Any, hit["score"])) > float(cast(Any, best[key]["score"])):
                    best[key] = hit
    return sorted(
        best.values(),
        key=lambda hit: (-float(cast(Any, hit["score"])), -int(cast(Any, hit.get("confidence", 600))), str(hit["id"])),
    )[:MAX_ITEMS]


def _compute_overlap_score(
    query_bigrams: set[str],
    content_bigrams: set[str],
    common_bigrams: set[str],
) -> float:
    informative_common = common_bigrams - _GENERIC_PREDICATE_BIGRAMS
    if not informative_common:
        return 0.0
    score = 2.0 * len(common_bigrams) / (len(query_bigrams) + len(content_bigrams))
    informative_query = query_bigrams - _GENERIC_PREDICATE_BIGRAMS
    if informative_query:
        coverage = len(informative_common) / len(informative_query)
        if coverage >= 1.0:
            coverage_score = MIN_SCORE + min(0.35, 0.10 * len(informative_common))
            score = max(score, coverage_score)
        elif coverage >= 0.5 and len(informative_common) >= 2:
            coverage_score = MIN_SCORE + min(0.20, 0.05 * len(informative_common))
            score = max(score, coverage_score)
    return score


def _score_rows(query: str, rows: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    query_bigrams = _bigrams(query)
    if not query_bigrams:
        return []
    scored: list[tuple[float, int, str, str]] = []
    for row in rows:
        content = str(row["content"])
        # Graph-aware matching: score against the composed match text
        # (content + entity names/aliases) while the output stays the
        # canonical content.
        match_text = _row_match_text(query, row)
        content_bigrams = _bigrams(match_text)
        if not content_bigrams:
            continue
        common_bigrams = query_bigrams & content_bigrams
        if not common_bigrams:
            continue
        score = _compute_overlap_score(query_bigrams, content_bigrams, common_bigrams)
        if score < MIN_SCORE:
            continue
        scored.append(
            (score, int(cast(Any, row["confidence"])), str(row["id"]), content)
        )
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [
        {"id": item[2], "content": item[3], "score": item[0]}
        for item in scored[:MAX_ITEMS]
    ]


def _score_anchor_rows(
    query: str, rows: Iterable[Mapping[str, object]]
) -> list[Mapping[str, object]]:
    scored: list[tuple[int, int, str, str]] = []
    for row in rows:
        raw_anchors = row.get("anchors") or ()
        anchors: tuple[str, ...]
        if isinstance(raw_anchors, str):
            anchors = (raw_anchors,)
        elif isinstance(raw_anchors, Sequence):
            anchors = tuple(str(anchor) for anchor in raw_anchors)
        else:
            anchors = ()
        matched_length = max(
            (
                len(anchor)
                for anchor in anchors
                if anchor and _anchor_is_explicit(query, anchor)
            ),
            default=0,
        )
        if matched_length == 0:
            continue
        scored.append(
            (
                matched_length,
                int(cast(Any, row["confidence"])),
                str(row["id"]),
                str(row["content"]),
            )
        )
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [
        {"id": item[2], "content": item[3], "score": float(MIN_SCORE)}
        for item in scored[:MAX_ITEMS]
    ]


def match_cognitions(
    query: str, rows: Iterable[Mapping[str, object]]
) -> list[Mapping[str, object]]:
    """Score current claims against the query; deterministic order.

    Order: score desc, then confidence desc, then id asc.  Byte-stable for the
    same inputs.  Category expansion is a fallback ONLY when the direct
    bigram match produced zero hits.
    """
    rows_list = list(rows)
    query = _query_signal(query)
    hits = _score_rows(query, rows_list)
    if hits:
        return hits
    if not _is_inquiry(query) and len(query) > 12:
        return []
    for variant in _fallback_query_variants(query):
        hits = _score_rows(variant, rows_list)
        if hits:
            return hits
    hits = _local_inquiry_matches(query, rows_list, _score_rows)
    if hits:
        return hits
    return _score_anchor_rows(query, rows_list)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _score_world_rows(query: str, rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    query_bigrams = _bigrams(query)
    if not query_bigrams:
        return []
    scored: list[tuple[float, int, str, int, str, str, bool]] = []
    for row in rows:
        content = str(row["content"])
        # A short correction may omit its topic ("No, Friday evening").
        # Score retained, permission-checked predecessor cues independently;
        # neither their content nor their IDs become selected recall items.
        variants = _row_match_variants(query, row)
        score = max((
            _compute_overlap_score(query_bigrams, bigrams, query_bigrams & bigrams)
            for text in variants if (bigrams := _bigrams(text))
            and query_bigrams & bigrams
        ), default=0.0)
        if score < MIN_SCORE:
            continue
        kind = str(row["kind"])
        scored.append(
            (
                score,
                int(cast(Any, row.get("confidence", 600))),
                str(row["id"]),
                _KIND_ORDER[kind],
                kind,
                content,
                bool(row.get("is_superseded", False)),
            )
        )
    scored.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return [
        {
            "kind": item[4],
            "id": item[2],
            "content": item[5],
            "score": item[0],
            "confidence": item[1],
            "is_superseded": item[6],
        }
        for item in scored[:MAX_ITEMS]
    ]


def _score_world_anchor_rows(
    query: str, rows: Iterable[Mapping[str, object]], *, topic: bool = False,
) -> list[dict[str, object]]:
    scored: list[tuple[int, int, str, int, str, str, bool]] = []
    for row in rows:
        raw_anchors = row.get("anchors") or ()
        anchors: tuple[str, ...]
        if isinstance(raw_anchors, str):
            anchors = (raw_anchors,)
        elif isinstance(raw_anchors, Sequence):
            anchors = tuple(str(anchor) for anchor in raw_anchors)
        else:
            anchors = ()
        matched_length = max(
            (
                len(anchor)
                for anchor in anchors
                if anchor and (_topic_cue_is_explicit(query, anchor) if topic else _anchor_is_explicit(query, anchor))
            ),
            default=0,
        )
        if matched_length == 0:
            continue
        kind = str(row["kind"])
        scored.append(
            (
                matched_length,
                int(cast(Any, row.get("confidence", 600))),
                str(row["id"]),
                _KIND_ORDER[kind],
                kind,
                str(row["content"]),
                bool(row.get("is_superseded", False)),
            )
        )
    scored.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return [
        {
            "kind": item[4],
            "id": item[2],
            "content": item[5],
            "score": float(MIN_SCORE),
            "confidence": item[1],
            "is_superseded": item[6],
        }
        for item in scored[:MAX_ITEMS]
    ]


def _row_anchors(row: Mapping[str, object]) -> tuple[str, ...]:
    raw = row.get("anchors") or ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(str(value) for value in raw) if isinstance(raw, Sequence) else ()


def _topic_cue_is_explicit(query: str, cue: str) -> bool:
    # Topic nouns have grammatical continuations that are not person-name
    # continuations. Keep person prefix protection unchanged.
    if _anchor_is_explicit(query.casefold(), cue.casefold()):
        return True
    if cue.isascii():
        return False
    return any(query[index + len(cue):].startswith(("时", "前", "后", "方面", "相关", "可以", "应该", "该", "能", "要", "怎么", "如何"))
               for index in range(len(query)) if query.startswith(cue, index))


def _match_world_rows(query: str, rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    rows_list = list(rows)
    raw_query = query
    query = _query_signal(query)
    is_historical = bool(_HISTORICAL_QUERY_PATTERN.search(raw_query))
    if not is_historical:
        # Naming a person in a current preference question does not make the
        # superseded value current. Preserve existing "Who is X?" lookups of
        # retained people, rendered explicitly as past memory.
        named_identity = {
            str(anchor) for row in rows_list for anchor in _row_anchors(row)
            if _IDENTITY_QUERY_PATTERN.search(raw_query)
            and _anchor_is_explicit(query.casefold(), str(anchor).casefold())
        }
        rows_list = [
            row for row in rows_list
            if not row.get("is_superseded")
            or bool(named_identity.intersection(_row_anchors(row)))
        ]
    named = {
        str(anchor) for row in rows_list for anchor in _row_anchors(row)
        if _anchor_is_explicit(query.casefold(), str(anchor).casefold())
    }
    if named:
        rows_list = [
            row for row in rows_list
            if named.intersection(_row_anchors(row))
            or (row.get("statement_kind") == "naming" and any(
                _anchor_is_explicit(str(row["content"]).casefold(), name.casefold()) for name in named
            ))
        ]
        identity = _score_world_anchor_rows(query, [row for row in rows_list if row.get("statement_kind") == "naming"])
        relevant = _score_world_rows(query, rows_list)
        anchored = _score_world_anchor_rows(query, rows_list)
        result: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for item in [*identity, *relevant, *anchored]:
            key = (str(item["kind"]), str(item["id"]))
            if key not in seen:
                result.append(item)
                seen.add(key)
        return result[:MAX_ITEMS]
    topic_rows = [
        {**row, "anchors": row.get("topic_cues", ())}
        for row in rows_list if row.get("topic_cues")
    ]
    topic_hits = _score_world_anchor_rows(query, topic_rows, topic=True)
    if topic_hits:
        return topic_hits
    hits = _score_world_rows(query, rows_list)
    if hits:
        return hits
    if not _is_inquiry(query) and len(query) > 12:
        return []
    for variant in _fallback_query_variants(query):
        hits = _score_world_rows(variant, rows_list)
        if hits:
            return hits
    hits = _local_inquiry_matches(query, rows_list, _score_world_rows)
    if hits:
        return hits
    # A retained predecessor may itself be very short ("only Wednesday
    # evenings for exercise"). If the long question shares just its literal
    # topic, use that non-generic cue only after ordinary retrieval missed.
    # This is query-side localization, not a synthesized successor fact.
    if _is_inquiry(query):
        query_topics = _bigrams(query) - _GENERIC_PREDICATE_BIGRAMS
        best: dict[str, dict[str, object]] = {}
        for row in rows_list:
            for text in cast(Sequence[str], row.get("predecessor_match_texts", ())):
                for cue in sorted(query_topics & _bigrams(text)):
                    for hit in _score_world_rows(cue, [row]):
                        candidate_id = str(hit["id"])
                        if candidate_id not in best or float(cast(Any, hit["score"])) > float(cast(Any, best[candidate_id]["score"])):
                            best[candidate_id] = hit
        if best:
            return sorted(best.values(), key=lambda hit: (
                -float(cast(Any, hit["score"])), -int(cast(Any, hit["confidence"])), str(hit["id"])
            ))[:MAX_ITEMS]
    return _score_world_anchor_rows(query, rows_list)


def format_recall(items: Sequence[Mapping[str, object]]) -> str:
    """Render the deterministic injected block (claims only, no internals)."""
    if not items:
        return ""
    lines = []
    for item in items:
        prefix = f"{_PREFIX}（过往）" if (item.get("is_superseded") or int(cast(Any, item.get("confidence", 600))) < 300) else _PREFIX
        lines.append(f"{prefix}：{item['content']}")
    text = "\n".join(lines)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS].rstrip() + "…"
    return text


# ── graph-aware, permission-gated shared recall ─────────────────────────────


def _entity_names(db: sqlite3.Connection, world_id: str, entity_id: str, model_tier: ModelTier = "local") -> tuple[str, ...]:
    """Canonical name + aliases of one current entity (order-stable, deduped)."""
    if not world_item_visible(
        db, world_id, "entity", entity_id, surface="model_cloud" if model_tier == "cloud" else "recall"
    ):
        return ()
    row = db.execute(
        "SELECT canonical_name, aliases_json FROM entity "
        "WHERE id = ? AND world_id = ? AND invalid_at IS NULL",
        (entity_id, world_id),
    ).fetchone()
    if row is None:
        return ()
    names: list[str] = [str(row[0])]
    trusted = current_entity_aliases(db, world_id, entity_id, surface="model_cloud" if model_tier == "cloud" else "recall")
    names.extend(trusted)
    if len(row) > 1 and row[1]:
        try:
            raw_aliases = json.loads(str(row[1]))
            if isinstance(raw_aliases, list):
                ledger_aliases = set()
                for (c,) in db.execute("SELECT content FROM evidence_ledger").fetchall():
                    try:
                        c_dict = json.loads(str(c))
                        if isinstance(c_dict, dict) and c_dict.get("relation") == "alias":
                            alias_val = c_dict.get("alias_name")
                            if isinstance(alias_val, str):
                                ledger_aliases.add(alias_val)
                    except Exception:
                        pass
                for a in raw_aliases:
                    if a and a not in names and a not in ledger_aliases:
                        names.append(str(a))
        except Exception:
            pass
    return tuple(dict.fromkeys(names))


def _graph_match_text(
    db: sqlite3.Connection,
    world_id: str,
    kind: str,
    row_id: str,
    content: str,
    model_tier: ModelTier = "local",
) -> str:
    """Compose the searchable text: canonical content + entity names/aliases
    reachable from the row's graph endpoints (relationship endpoints, event
    participants/objects, targeted-attribute entity)."""
    parts: list[str] = [content]
    if kind == "relationship":
        row = db.execute(
            "SELECT source_entity_id, target_entity_id, relation_type FROM relationship WHERE id = ?",
            (row_id,),
        ).fetchone()
        if row is not None:
            for entity_id in (row[0], row[1]):
                parts.extend(_entity_names(db, world_id, str(entity_id), model_tier))
            if len(row) > 2 and row[2]:
                rel = str(row[2]).strip().lower()
                parts.append(rel)
                synonyms = {
                    "girlfriend": ["女朋友", "女友", "对象", "恋爱", "脱单"],
                    "boyfriend": ["男朋友", "男友", "对象", "恋爱", "脱单"],
                    "partner": ["伴侣", "对象", "恋爱", "脱单"],
                    "spouse": ["配偶", "爱人", "结婚", "已婚"],
                    "wife": ["妻子", "老婆", "太太", "夫人", "已婚"],
                    "husband": ["丈夫", "老公", "先生", "已婚"],
                }
                if rel in synonyms:
                    parts.extend(synonyms[rel])
    elif kind == "event":
        row = db.execute(
            "SELECT participants_json, objects_json FROM world_event WHERE id = ?",
            (row_id,),
        ).fetchone()
        if row is not None:
            for column in (row[0], row[1]):
                try:
                    decoded = json.loads(str(column) or "[]")
                except ValueError:
                    continue
                if not isinstance(decoded, list):
                    continue
                for item in decoded:
                    if isinstance(item, str) and item:
                        if item.startswith("entity-"):
                            parts.extend(_entity_names(db, world_id, item))
                        else:
                            parts.append(item)
                    elif isinstance(item, Mapping) and isinstance(
                        item.get("canonical_name"), str
                    ):
                        parts.append(str(item["canonical_name"]))
    elif kind == "cognition":
        row = db.execute(
            "SELECT target_entity_id FROM cognition_target WHERE cognition_id = ?",
            (row_id,),
        ).fetchone()
        if row is not None and row[0] is not None:
            parts.extend(_entity_names(db, world_id, str(row[0]), model_tier))
        for entity_row in db.execute(
            "SELECT id, canonical_name FROM entity WHERE world_id = ? AND invalid_at IS NULL",
            (world_id,),
        ).fetchall():
            if str(entity_row[1]) in content:
                parts.extend(_entity_names(db, world_id, str(entity_row[0]), model_tier))
    return " ".join(dict.fromkeys(part for part in parts if part))


def _graph_match_anchors(
    db: sqlite3.Connection,
    world_id: str,
    kind: str,
    row_id: str,
    model_tier: ModelTier = "local",
) -> tuple[str, ...]:
    """Current, permission-eligible entity names linked to one World row."""
    anchors: list[str] = []
    if kind == "relationship":
        row = db.execute(
            "SELECT source_entity_id, target_entity_id FROM relationship WHERE id = ?",
            (row_id,),
        ).fetchone()
        if row is not None:
            for entity_id in (row[0], row[1]):
                anchors.extend(_entity_names(db, world_id, str(entity_id), model_tier))
    elif kind == "event":
        row = db.execute(
            "SELECT participants_json, objects_json FROM world_event WHERE id = ?",
            (row_id,),
        ).fetchone()
        if row is not None:
            for column in (row[0], row[1]):
                try:
                    decoded = json.loads(str(column) or "[]")
                except ValueError:
                    continue
                if not isinstance(decoded, list):
                    continue
                for item in decoded:
                    if isinstance(item, str) and item:
                        if item.startswith("entity-"):
                            anchors.extend(_entity_names(db, world_id, item))
                        else:
                            anchors.append(item)
                    elif isinstance(item, Mapping) and isinstance(
                        item.get("canonical_name"), str
                    ):
                        anchors.append(str(item["canonical_name"]))
    elif kind == "cognition":
        row = db.execute(
            "SELECT target_entity_id FROM cognition_target WHERE cognition_id = ?",
            (row_id,),
        ).fetchone()
        if row is not None and row[0] is not None:
            anchors.extend(_entity_names(db, world_id, str(row[0]), model_tier))
        cog_content = db.execute(
            "SELECT content FROM cognition WHERE id = ?", (row_id,)
        ).fetchone()
        if cog_content is not None:
            c_text = str(cog_content[0])
            for entity_row in db.execute(
                "SELECT id, canonical_name FROM entity WHERE world_id = ? AND invalid_at IS NULL AND kind != 'topic'",
                (world_id,),
            ).fetchall():
                if str(entity_row[1]) in c_text:
                    anchors.extend(_entity_names(db, world_id, str(entity_row[0]), model_tier))
    return tuple(dict.fromkeys(anchor for anchor in anchors if anchor))


def _graph_topic_cues(
    db: sqlite3.Connection, subject_id: str, content: str, model_tier: ModelTier,
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        name for (entity_id,) in db.execute(
            "SELECT id FROM entity WHERE world_id=? AND kind='topic' AND invalid_at IS NULL "
            "AND instr(?, canonical_name)>0 ORDER BY id", (subject_id, content),
        ).fetchall()
        for name in _entity_names(db, subject_id, str(entity_id), model_tier)
    ))


def _current_world_rows(
    db: sqlite3.Connection, subject_id: str, model_tier: ModelTier = "local"
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    try:
        transitions = db.execute(
            "SELECT prior_cognition_id FROM cognition_transitions"
        ).fetchall()
        superseded_ids = {str(row[0]) for row in transitions}
    except Exception:
        superseded_ids = set()
    try:
        rel_transitions = db.execute(
            "SELECT prior_relationship_id FROM relationship_transitions"
        ).fetchall()
        superseded_ids.update(str(row[0]) for row in rel_transitions)
    except Exception:
        pass
    cognitions = db.execute(
        "SELECT id, content, confidence, content_type FROM cognition "
        "WHERE subject_id = ? "
        "AND invalid_at IS NULL AND archived_at IS NULL AND muted_at IS NULL",
        (subject_id,),
    ).fetchall()
    relationships = db.execute(
        "SELECT id, content, confidence FROM relationship "
        "WHERE world_id = ? AND invalid_at IS NULL",
        (subject_id,),
    ).fetchall()
    events = db.execute(
        "SELECT id, content, confidence FROM world_event "
        "WHERE world_id = ? AND invalid_at IS NULL",
        (subject_id,),
    ).fetchall()
    for kind, rows in (
        ("cognition", cognitions),
        ("relationship", relationships),
        ("event", events),
    ):
        current_kind = cast(Literal["cognition", "relationship", "event"], kind)
        for row in rows:
            row_id = str(row[0])
            if not world_item_visible(
                db, subject_id, current_kind, row_id, surface="model_cloud" if model_tier == "cloud" else "recall"
            ):
                continue
            content = str(row[1])
            anchors = _graph_match_anchors(db, subject_id, current_kind, row_id, model_tier)
            if kind == "cognition":
                target = db.execute(
                    "SELECT target_entity_id FROM cognition_target WHERE cognition_id = ?", (row_id,)
                ).fetchone()
                names = _entity_names(db, subject_id, str(target[0]), model_tier) if target else ()
                if names and not any(name in content for name in names):
                    content = f"{names[0]}：{content}"
                if str(row[3]) == "naming":
                    anchors = tuple(dict.fromkeys(
                        name for entity in db.execute(
                            "SELECT id FROM entity WHERE world_id=? AND invalid_at IS NULL "
                            "AND instr(?, canonical_name)>0", (subject_id, content),
                        )
                        for name in _entity_names(db, subject_id, str(entity[0]), model_tier)
                        if _anchor_is_explicit(content, name)
                    ))
            predecessor_texts = _predecessor_match_texts(db, subject_id, row_id, model_tier, kind=kind) if kind in {'cognition', 'relationship'} else ()
            items.append(
                {
                    "kind": current_kind,
                    "id": row_id,
                    "content": content,
                    "confidence": int(row[2]),
                    "statement_kind": str(row[3]) if kind == "cognition" else kind,
                    "match_text": _graph_match_text(
                        db, subject_id, current_kind, row_id, content, model_tier
                    ),
                    "anchors": anchors,
                    "topic_cues": tuple(dict.fromkeys(
                        cue for text in (content, *predecessor_texts)
                        for cue in _graph_topic_cues(db, subject_id, text, model_tier)
                    )),
                    "is_superseded": row_id in superseded_ids,
                    "predecessor_match_texts": predecessor_texts,
                }
            )
    return items


def _predecessor_match_texts(
    db: sqlite3.Connection, subject_id: str, current_id: str, model_tier: ModelTier,
    *, surface: CurrentnessSurface | None = None, kind: str = 'cognition',
) -> tuple[str, ...]:
    """Permission-checked transition context, never current successor content.

    Recall uses it only for matching; formation may use it to resolve an
    omitted topic without treating historical wording as new Evidence.
    """
    pending = [current_id]
    seen = {current_id}
    texts: list[str] = []
    surface = surface or ("model_cloud" if model_tier == "cloud" else "recall")
    if kind not in {'cognition', 'relationship'}:
        return ()
    subject_column = 'subject_id' if kind == 'cognition' else 'world_id'
    while pending:
        try:
            rows = db.execute(
                f"SELECT c.id, c.content, c.archived_at, c.muted_at "
                f"FROM {kind}_transitions t JOIN {kind} c ON c.id = t.prior_{kind}_id "
                f"WHERE t.replacement_{kind}_id = ? AND c.{subject_column} = ? "
                "AND t.reason IN ('corrects', 'superseded', 'name_corrected') ORDER BY c.id",
                (pending.pop(), subject_id),
            ).fetchall()
        except sqlite3.OperationalError:
            # Older databases can have no transition table.
            return ()
        for prior_id, content, archived, muted in rows:
            prior_id = str(prior_id)
            if prior_id in seen:
                continue
            seen.add(prior_id)
            if archived is not None or muted is not None or any(
                world_item_lifecycle(db, subject_id, cast(Any, kind), prior_id)
            ):
                continue
            if not world_item_evidence_visible(
                db, subject_id, cast(Any, kind), prior_id, surface=surface, model_tier=model_tier
            ):
                continue
            texts.append(str(content) if surface == "formation" else
                         _graph_match_text(db, subject_id, cast(Any, kind), prior_id, str(content), model_tier))
            pending.append(prior_id)
    return tuple(texts)


def _replacement_explanations(db: sqlite3.Connection, subject_id: str,
                             selected: Sequence[Mapping[str, object]], model_tier: ModelTier) -> str:
    """Explain formal replacement chains only on explicit history questions."""
    lines: list[str] = []
    for item in selected:
        kind = str(item['kind'])
        if kind not in {'cognition', 'relationship'}:
            continue
        table, subject_column = ('cognition', 'subject_id') if kind == 'cognition' else ('relationship', 'world_id')
        if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (kind + '_transitions',)).fetchone():
            continue
        pending = [str(item['id'])]
        seen: set[str] = set()
        while pending:
            successor = pending.pop()
            if successor in seen:
                continue
            seen.add(successor)
            for prior, reason, content in db.execute(
                f'SELECT t.prior_{kind}_id, t.reason, w.content FROM {kind}_transitions t '
                f'JOIN {table} w ON w.id=t.prior_{kind}_id '
                f'WHERE t.replacement_{kind}_id=? AND w.{subject_column}=? ORDER BY t.revision, t.id',
                (successor, subject_id),
            ).fetchall():
                if any(world_item_lifecycle(db, subject_id, cast(Any, kind), str(prior))):
                    continue
                if not world_item_evidence_visible(db, subject_id, cast(Any, kind), str(prior),
                    surface='model_cloud' if model_tier == 'cloud' else 'recall', model_tier=model_tier):
                    continue
                lines.append(f'取代原因（旧理解不再有效）：{content}；后经用户纠正取代（{reason}），以当前理解为准。')
                pending.append(str(prior))
    return '\n'.join(dict.fromkeys(lines))


def recall_world_snapshot(
    db: sqlite3.Connection, subject_id: str, query: str, *, model_tier: ModelTier = "local"
) -> RecallSnapshotV1 | None:
    """Read one coherent, deterministic Recall snapshot without writes.

    The caller owns connection lifetime. This function opens a deferred read
    transaction so World revision, currentness facts, selected rows and render
    bytes all come from one SQLite view. Any read or validation failure returns
    ``None`` for a host-side fail-closed cache miss.
    """
    transaction_started = not db.in_transaction
    try:
        if model_tier not in {"local", "cloud"}:
            return None
        if transaction_started:
            db.execute("BEGIN")
        revision_row = db.execute(
            "SELECT revision FROM memory_state WHERE singleton = 1"
        ).fetchone()
        world_revision = 0 if revision_row is None else int(revision_row[0])
        if world_revision < 0:
            return None
        facts = subject_currentness_facts(db, subject_id)
        currentness_digest = _digest(
            {
                "schema_version": 1,
                "surface": "recall",
                **({"model_tier": model_tier} if model_tier != "local" else {}),
                "subject_id": subject_id,
                "facts": facts,
            }
        )
        selected = _match_world_rows(query, _current_world_rows(db, subject_id, model_tier))
        selected_item_ids = tuple(
            (str(item["kind"]), str(item["id"])) for item in selected
        )
        rendered_recall = format_recall(selected)
        if _HISTORICAL_QUERY_PATTERN.search(query) or re.search(r'为什么|为何|不算|失效|why|invalid', query, re.I):
            explanations = _replacement_explanations(db, subject_id, selected, model_tier)
            rendered_recall = '\n'.join(filter(None, (rendered_recall, explanations)))[:MAX_OUTPUT_CHARS]
        recall_snapshot_token = _digest(
            {
                "schema_version": 1,
                "surface": "recall",
                **({"model_tier": model_tier} if model_tier != "local" else {}),
                "subject_id": subject_id,
                "world_revision": world_revision,
                "selected_item_ids": selected_item_ids,
                "currentness_digest": currentness_digest,
                "rendered_recall": rendered_recall,
            }
        )
        return RecallSnapshotV1(
            subject_id=subject_id,
            world_revision=world_revision,
            selected_item_ids=selected_item_ids,
            currentness_digest=currentness_digest,
            rendered_recall=rendered_recall,
            recall_snapshot_token=recall_snapshot_token,
            count=len(selected_item_ids),
        )
    except (sqlite3.Error, TypeError, ValueError, KeyError):
        return None
    finally:
        if transaction_started:
            try:
                db.rollback()
            except sqlite3.Error:
                pass


def recall_world_text(
    db: sqlite3.Connection, subject_id: str, query: str, *, model_tier: ModelTier = "local"
) -> tuple[str, int]:
    """Compatibility wrapper for the S1 immutable Recall snapshot API."""
    snapshot = recall_world_snapshot(db, subject_id, query, model_tier=model_tier)
    if snapshot is None:
        return "", 0
    return snapshot.rendered_recall, snapshot.count
