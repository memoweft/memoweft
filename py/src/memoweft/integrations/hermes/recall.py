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
from hashlib import sha256
import json
import re
import sqlite3
from typing import Any, Iterable, Literal, Mapping, Sequence, cast

from ..trust.currentness import (
    current_entity_aliases,
    subject_currentness_facts,
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
    "饮料": ("咖啡", "茶", "奶茶", "拿铁", "茉莉花茶"),
    "家人": ("爸爸", "妈妈", "女朋友", "男朋友", "老婆", "老公"),
    "朋友": ("朋友", "同学", "同事"),
    "车": ("小鹏", "比亚迪", "特斯拉", "SUV"),
}


def _bigrams(text: str) -> set[str]:
    text = text.strip()
    if not text:
        return set()
    if len(text) < 2:
        return {text}
    return {text[i : i + 2] for i in range(len(text) - 1)}


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


def _explicit_query_cues(query: str) -> tuple[str, ...]:
    """Return stable, user-authored local cues from an otherwise long query.

    Recall first scores the complete query exactly as before.  These cues are
    used only after that produces zero hits, preventing surrounding question
    phrasing from diluting an explicitly quoted or punctuation-delimited topic.
    No inference, model call, stemming, or mutable index is involved.
    """
    cues: list[str] = []
    for pattern in _QUOTED_CUE_PATTERNS:
        cues.extend(match.strip() for match in pattern.findall(query))
    separated = tuple(
        part.strip() for part in _CUE_SEPARATOR.split(query) if part.strip()
    )
    cues.extend(separated)
    for part in separated:
        if not part.startswith("关于") or len(part) <= len("关于"):
            continue
        topic = part[len("关于") :]
        topic = _RECALL_QUESTION_SUFFIX.sub("", topic).strip()
        if not topic:
            continue
        cues.append(topic)
        cues.extend(cue.strip() for cue in _TOPIC_CONNECTOR.split(topic))
    for members in _CATEGORY_MEMBERS.values():
        cues.extend(member for member in members if member in query)
    return tuple(
        dict.fromkeys(
            cue for cue in cues if cue and cue != query.strip() and len(cue) <= 128
        )
    )


def _fallback_query_variants(query: str) -> tuple[str, ...]:
    """Preserve category fallback, then try explicit local cues."""
    variants: list[str] = _expanded_queries(query)[1:]
    for cue in _explicit_query_cues(query):
        variants.append(cue)
        variants.extend(_expanded_queries(cue)[1:])
    return tuple(dict.fromkeys(variants))


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
        common = len(query_bigrams & content_bigrams)
        if common == 0:
            continue
        score = 2.0 * common / (len(query_bigrams) + len(content_bigrams))
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
    for variant in _fallback_query_variants(query):
        hits = _score_rows(variant, rows_list)
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
    scored: list[tuple[float, int, str, int, str, str]] = []
    for row in rows:
        content = str(row["content"])
        match_text = _row_match_text(query, row)
        content_bigrams = _bigrams(match_text)
        if not content_bigrams:
            continue
        common = len(query_bigrams & content_bigrams)
        if common == 0:
            continue
        score = 2.0 * common / (len(query_bigrams) + len(content_bigrams))
        if score < MIN_SCORE:
            continue
        kind = str(row["kind"])
        scored.append(
            (
                score,
                int(cast(Any, row["confidence"])),
                str(row["id"]),
                _KIND_ORDER[kind],
                kind,
                content,
            )
        )
    scored.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return [
        {"kind": item[4], "id": item[2], "content": item[5], "score": item[0]}
        for item in scored[:MAX_ITEMS]
    ]


def _score_world_anchor_rows(
    query: str, rows: Iterable[Mapping[str, object]]
) -> list[dict[str, object]]:
    scored: list[tuple[int, int, str, int, str, str]] = []
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
        kind = str(row["kind"])
        scored.append(
            (
                matched_length,
                int(cast(Any, row["confidence"])),
                str(row["id"]),
                _KIND_ORDER[kind],
                kind,
                str(row["content"]),
            )
        )
    scored.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return [
        {
            "kind": item[4],
            "id": item[2],
            "content": item[5],
            "score": float(MIN_SCORE),
        }
        for item in scored[:MAX_ITEMS]
    ]


def _match_world_rows(query: str, rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    rows_list = list(rows)
    query = _query_signal(query)
    hits = _score_world_rows(query, rows_list)
    if hits:
        return hits
    for variant in _fallback_query_variants(query):
        hits = _score_world_rows(variant, rows_list)
        if hits:
            return hits
    return _score_world_anchor_rows(query, rows_list)


def format_recall(items: Sequence[Mapping[str, object]]) -> str:
    """Render the deterministic injected block (claims only, no internals)."""
    if not items:
        return ""
    lines = [f"{_PREFIX}：{item['content']}" for item in items]
    text = "\n".join(lines)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS].rstrip() + "…"
    return text


# ── graph-aware, permission-gated shared recall ─────────────────────────────


def _entity_names(db: sqlite3.Connection, world_id: str, entity_id: str) -> tuple[str, ...]:
    """Canonical name + aliases of one current entity (order-stable, deduped)."""
    if not world_item_visible(
        db, world_id, "entity", entity_id, surface="recall"
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
    names.extend(
        current_entity_aliases(db, world_id, entity_id, surface="recall")
    )
    return tuple(dict.fromkeys(names))


def _graph_match_text(
    db: sqlite3.Connection,
    world_id: str,
    kind: str,
    row_id: str,
    content: str,
) -> str:
    """Compose the searchable text: canonical content + entity names/aliases
    reachable from the row's graph endpoints (relationship endpoints, event
    participants/objects, targeted-attribute entity)."""
    parts: list[str] = [content]
    if kind == "relationship":
        row = db.execute(
            "SELECT source_entity_id, target_entity_id FROM relationship WHERE id = ?",
            (row_id,),
        ).fetchone()
        if row is not None:
            for entity_id in (row[0], row[1]):
                parts.extend(_entity_names(db, world_id, str(entity_id)))
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
            parts.extend(_entity_names(db, world_id, str(row[0])))
    return " ".join(dict.fromkeys(part for part in parts if part))


def _graph_match_anchors(
    db: sqlite3.Connection,
    world_id: str,
    kind: str,
    row_id: str,
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
                anchors.extend(_entity_names(db, world_id, str(entity_id)))
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
            anchors.extend(_entity_names(db, world_id, str(row[0])))
    return tuple(dict.fromkeys(anchor for anchor in anchors if anchor))


def _current_world_rows(
    db: sqlite3.Connection, subject_id: str
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    cognitions = db.execute(
        "SELECT id, content, confidence FROM cognition "
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
                db, subject_id, current_kind, row_id, surface="recall"
            ):
                continue
            content = str(row[1])
            items.append(
                {
                    "kind": current_kind,
                    "id": row_id,
                    "content": content,
                    "confidence": int(row[2]),
                    "match_text": _graph_match_text(
                        db, subject_id, current_kind, row_id, content
                    ),
                    "anchors": _graph_match_anchors(
                        db, subject_id, current_kind, row_id
                    ),
                }
            )
    return items


def recall_world_snapshot(
    db: sqlite3.Connection, subject_id: str, query: str
) -> RecallSnapshotV1 | None:
    """Read one coherent, deterministic Recall snapshot without writes.

    The caller owns connection lifetime. This function opens a deferred read
    transaction so World revision, currentness facts, selected rows and render
    bytes all come from one SQLite view. Any read or validation failure returns
    ``None`` for a host-side fail-closed cache miss.
    """
    transaction_started = not db.in_transaction
    try:
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
                "subject_id": subject_id,
                "facts": facts,
            }
        )
        selected = _match_world_rows(query, _current_world_rows(db, subject_id))
        selected_item_ids = tuple(
            (str(item["kind"]), str(item["id"])) for item in selected
        )
        rendered_recall = format_recall(selected)
        recall_snapshot_token = _digest(
            {
                "schema_version": 1,
                "surface": "recall",
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
    db: sqlite3.Connection, subject_id: str, query: str
) -> tuple[str, int]:
    """Compatibility wrapper for the S1 immutable Recall snapshot API."""
    snapshot = recall_world_snapshot(db, subject_id, query)
    if snapshot is None:
        return "", 0
    return snapshot.rendered_recall, snapshot.count
