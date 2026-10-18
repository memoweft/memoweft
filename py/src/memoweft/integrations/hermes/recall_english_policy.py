"""Conservative English candidate composition; scores never veto main hits."""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence, TypeVar

# Conservative, closed vocabulary, applied to occurrences rather than folded
# strings. Everyday homographs (can/will/mine/may/must/might/us/won/don,
# being, tell/tells) and obsolete contraction stems are deliberately absent.
# Rare lowercase symbol/note/operator senses remain documented limitations.
FRAME_WORDS = frozenset("""
what which who whom whose where when why how
am is are was were be been do does did have has had
could would should shall
a an the and or but not
of to in on at for from with by about as into
i me my we our ours you your yours he him his she her hers
it its they them their theirs this that these those
remember remembers remembered told know knows knew remind
anything
""".split())
_WORD = re.compile(r"[a-z0-9_\-]+")
_SOURCE_WORD = re.compile(r"[a-z0-9_\-]+(?:['’][a-z]+)?", re.IGNORECASE)
_SUFFIXES = {"m": "am", "s": "is", "re": "are", "ve": "have", "ll": "will", "d": "would"}
_NEGATIVE = {"won't": "will not", "can't": "can not", "shan't": "shall not"}


def expand_contractions(text: str) -> str:
    """Expand a temporary comparison string, never stored or rendered content."""
    text = text.casefold().replace('’', "'")
    for short, full in _NEGATIVE.items():
        text = re.sub(r"\b" + re.escape(short) + r"\b", full, text)
    text = re.sub(r"\b([a-z]+)n't\b", r"\1 not", text)
    return re.sub(r"\b([a-z]+)'(m|s|re|ve|ll|d)\b",
                  lambda match: match[1] + " " + _SUFFIXES[match[2]], text)


def words(text: str) -> set[str]:
    return set(_WORD.findall(expand_contractions(text)))


def substantive_words(text: str) -> set[str]:
    """Preserve original case and contraction provenance before comparison.

    Case is read from the root before a straight or curly apostrophe, so a
    lowercase grammatical suffix cannot hide an uppercase acronym. Uppercase
    words (including single letters), and capitalized words after
    another word in the same sentence, are substantive on either side. Bare
    s/t/d/m/ll/re/ve are absent from FRAME_WORDS. Only an actual apostrophe
    construction supplies generated grammatical words: won't has no 'won',
    and I'll generates auxiliary 'will' without treating bare 'will' as grammar.
    """
    result: set[str] = set()
    sentence_start = 0
    for match in _SOURCE_WORD.finditer(text):
        prefix = text[sentence_start:match.start()]
        boundary = max((prefix.rfind(mark) for mark in ".!?\n"), default=-1)
        if boundary >= 0:
            sentence_start += boundary + 1
            prefix = text[sentence_start:match.start()]
        raw = match[0]
        case_root = re.split(r"['’]", raw, maxsplit=1)[0]
        # English capitalizes the personal pronoun I even in ordinary I'm /
        # I've / I'll / I'd. Preserve their existing grammatical treatment;
        # this orthographic convention is not an uppercase acronym cue.
        pronoun_contraction = re.fullmatch(r"I['’](?:m|ve|ll|d)", raw) is not None
        protected = (raw.isupper() or (case_root.isupper() and not pronoun_contraction)
                     or (case_root[0].isupper() and any(char.isalpha() for char in prefix)))
        folded = raw.casefold().replace('’', "'")
        if folded in _NEGATIVE:
            # Whole irregular negative auxiliary, never a free 'won'/'can'.
            if protected:
                result.update(words(raw))
            continue
        negative = re.fullmatch(r"([a-z]+)n't", folded)
        suffix = re.fullmatch(r"([a-z]+)'(m|s|re|ve|ll|d)", folded)
        if negative or suffix:
            construction = negative if negative is not None else suffix
            assert construction is not None
            base = construction[1]
            if protected or base not in FRAME_WORDS:
                result.add(base)
            # Generated not/auxiliary is grammatical only in this construction.
            continue
        for word in _WORD.findall(folded):
            if protected or word not in FRAME_WORDS:
                result.add(word)
    return result


def searchable_texts(row: Mapping[str, object]) -> list[str]:
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
    return texts


def shared_words(query: str, row: Mapping[str, object]) -> set[str]:
    return words(query) & words(" ".join(searchable_texts(row)))


def function_only_shared(query: str, row: Mapping[str, object]) -> bool:
    texts = searchable_texts(row)
    shared = words(query) & words(" ".join(texts))
    substantive = substantive_words(query)
    # Each projection has its own sentence start; joining would invent case
    # context at field boundaries and lose an occurrence's original provenance.
    for text in texts:
        substantive.update(substantive_words(text))
    return not shared & substantive


def key(row: Mapping[str, object]) -> tuple[str, str]:
    return str(row.get("kind", "cognition")), str(row["id"])


R = TypeVar("R", bound=Mapping[str, object])


def compose(query: str, rows: Iterable[Mapping[str, object]], main: list[R],
            prior: list[R], *, max_items: int) -> list[R]:
    """Keep every substantive main candidate; admit only existing improvements.

    A main hit may be removed only if every shared occurrence is grammar in
    its original case and apostrophe context on both query and memory sides.
    A grammar-only hit already supported by the prior improvement may remain
    (e.g. its corroborated inflection match). New scores never remove a main
    hit. Main slots are reserved before any prior-only nomination is admitted.
    """
    by_key = {key(row): row for row in rows}
    prior_keys = {key(row) for row in prior}
    keep = [row for row in main if key(row) in prior_keys or key(row) not in by_key
            or not function_only_shared(query, by_key[key(row)])]
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
