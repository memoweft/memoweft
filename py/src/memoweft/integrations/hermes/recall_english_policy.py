"""Conservative English candidate composition; scores never veto main hits."""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence, TypeVar

# Closed, explicit grammar vocabulary. No topic or attribute nouns belong here.
FRAME_WORDS = frozenset("""
what which who whom whose where when why how
am is are was were be been being do does did have has had
can could will would should shall may might must
a an the and or but not
of to in on at for from with by about as into
i me my mine we us our ours you your yours he him his she her hers
it its they them their theirs this that these those
remember remembers remembered tell tells told know knows knew remind
anything
s t ll re ve d m won don isn aren wasn weren doesn didn haven hasn hadn wouldn shouldn couldn
""".split())
_WORD = re.compile(r"[a-z0-9_\-]+")
_SUFFIXES = {"m": "am", "s": "is", "re": "are", "ve": "have", "ll": "will", "d": "would"}


def expand_contractions(text: str) -> str:
    """Expand a temporary comparison string, never stored or rendered content."""
    text = text.casefold().replace('’', "'")
    for short, full in (("won't", "will not"), ("can't", "can not"), ("shan't", "shall not")):
        text = re.sub(r"\b" + re.escape(short) + r"\b", full, text)
    text = re.sub(r"\b([a-z]+)n't\b", r"\1 not", text)
    return re.sub(r"\b([a-z]+)'(m|s|re|ve|ll|d)\b",
                  lambda match: match[1] + " " + _SUFFIXES[match[2]], text)


def words(text: str) -> set[str]:
    return set(_WORD.findall(expand_contractions(text)))


def shared_words(query: str, row: Mapping[str, object]) -> set[str]:
    # Include all already-authorized searchable projections. A predecessor or
    # graph anchor can supply a substantive cue even when a short correction
    # omits it from the body. No raw Evidence or assistant text is searched.
    texts = [str(row["content"]), str(row.get("match_text", ""))]
    for field in ("anchors", "topic_cues", "predecessor_match_texts"):
        value = row.get(field, ())
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, Sequence):
            texts.extend(str(part) for part in value)
    return words(query) & words(" ".join(texts))


def key(row: Mapping[str, object]) -> tuple[str, str]:
    return str(row.get("kind", "cognition")), str(row["id"])


R = TypeVar("R", bound=Mapping[str, object])


def compose(query: str, rows: Iterable[Mapping[str, object]], main: list[R],
            prior: list[R], *, max_items: int) -> list[R]:
    """Keep every substantive main candidate; admit only existing improvements.

    A main hit may be removed only if all shared expanded words are grammar.
    A grammar-only hit already supported by the prior improvement may remain
    (e.g. its corroborated inflection match). New scores never remove a main
    hit. Main slots are reserved before any prior-only nomination is admitted.
    """
    by_key = {key(row): row for row in rows}
    prior_keys = {key(row) for row in prior}
    keep = [row for row in main if key(row) in prior_keys or key(row) not in by_key
            or not shared_words(query, by_key[key(row)]) <= FRAME_WORDS]
    main_keys = {key(row) for row in main}
    extras = [row for row in prior if key(row) not in main_keys][:max(0, max_items - len(keep))]
    allowed = {key(row) for row in [*keep, *extras]}
    result: list[R] = []
    seen = set()
    for row in [*prior, *keep]:
        item_key = key(row)
        if item_key in allowed and item_key not in seen:
            result.append(row)
            seen.add(item_key)
    return result


def reserve_main_order(selected: list[R], main: Sequence[Mapping[str, object]]) -> list[R]:
    """Budget fallback: main items first, in their original stable order."""
    by_key = {key(row): row for row in selected}
    main_keys = {key(row) for row in main}
    return ([by_key[key(row)] for row in main if key(row) in by_key]
            + [row for row in selected if key(row) not in main_keys])
