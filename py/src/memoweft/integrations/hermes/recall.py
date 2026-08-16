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

from typing import Any, Iterable, Mapping, Sequence, cast

#: Minimum Dice bigram-overlap score for a claim to be recalled.
MIN_SCORE = 0.15

#: Hard caps for the injected block: item count and total characters.
MAX_ITEMS = 5
MAX_OUTPUT_CHARS = 600

_PREFIX = "记忆"

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
        content_bigrams = _bigrams(content)
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
