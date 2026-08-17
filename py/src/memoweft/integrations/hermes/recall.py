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

import json
import sqlite3
from typing import Any, Iterable, Mapping, Sequence, cast

#: Minimum Dice bigram-overlap score for a claim to be recalled.
MIN_SCORE = 0.15

#: Hard caps for the injected block: item count and total characters.
MAX_ITEMS = 5
MAX_OUTPUT_CHARS = 600

_PREFIX = "记忆"

#: Evidence-link tables keyed by World row kind (permission gate).
_LINK_TABLES: dict[str, tuple[str, str]] = {
    "cognition": ("cognition_evidence", "cognition_id"),
    "relationship": ("relationship_evidence", "relationship_id"),
    "event": ("world_event_evidence", "world_event_id"),
}

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
        match_text = str(row.get("match_text") or content)
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


def match_cognitions(
    query: str, rows: Iterable[Mapping[str, object]]
) -> list[Mapping[str, object]]:
    """Score current claims against the query; deterministic order.

    Order: score desc, then confidence desc, then id asc.  Byte-stable for the
    same inputs.  Category expansion is a fallback ONLY when the direct
    bigram match produced zero hits.
    """
    rows_list = list(rows)
    hits = _score_rows(query, rows_list)
    if hits:
        return hits
    for variant in _expanded_queries(query)[1:]:
        hits = _score_rows(variant, rows_list)
        if hits:
            return hits
    return []


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
    row = db.execute(
        "SELECT canonical_name, aliases_json FROM entity "
        "WHERE id = ? AND world_id = ? AND invalid_at IS NULL",
        (entity_id, world_id),
    ).fetchone()
    if row is None:
        return ()
    names: list[str] = [str(row[0])]
    try:
        aliases = json.loads(str(row[1]) or "[]")
    except ValueError:
        aliases = []
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str) and alias:
                names.append(alias)
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


def _world_row_visible(db: sqlite3.Connection, kind: str, row_id: str) -> bool:
    """Permission gate: a World row is recallable only when it has at least one
    Evidence link and every linked Evidence row allows local reads
    (fail-closed: unproven permission means invisible)."""
    table, column = _LINK_TABLES[kind]
    linked = db.execute(
        f"SELECT evidence_id FROM {table} WHERE {column} = ?", (row_id,)
    ).fetchall()
    if not linked:
        return False
    ids = [str(row[0]) for row in linked]
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT allow_local_read FROM evidence WHERE id IN ({placeholders})", ids
    ).fetchall()
    return len(rows) == len(ids) and all(int(row[0]) == 1 for row in rows)


def recall_world_text(
    db: sqlite3.Connection, subject_id: str, query: str
) -> tuple[str, int]:
    """Graph-aware, permission-gated deterministic Recall over the accepted
    World.  Shared by the Hermes and DSH/WeftMate production chains.

    Read-only connection expected (caller opens ``mode=ro``); zero writes,
    zero model calls; any failure fails closed to ("", 0).
    """
    try:
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
        items: list[dict[str, object]] = []
        for kind, rows in (
            ("cognition", cognitions),
            ("relationship", relationships),
            ("event", events),
        ):
            for row in rows:
                row_id = str(row[0])
                if not _world_row_visible(db, kind, row_id):
                    continue
                content = str(row[1])
                items.append(
                    {
                        "id": row_id,
                        "content": content,
                        "confidence": int(row[2]),
                        "match_text": _graph_match_text(
                            db, subject_id, kind, row_id, content
                        ),
                    }
                )
        hits = match_cognitions(query, items)
        return format_recall(hits), len(hits)
    except sqlite3.Error:
        return "", 0
