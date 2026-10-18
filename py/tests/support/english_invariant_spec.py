"""Independent acceptance specification; nouns are never framework words."""
import re
import random
from typing import Any

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


def words(text: str) -> set[str]:
    text = text.casefold().replace('’', "'")
    for contraction, full in {"won't": "will not", "can't": "can not", "shan't": "shall not"}.items():
        text = re.sub(r"\b" + re.escape(contraction) + r"\b", full, text)
    text = re.sub(r"\b([a-z]+)n't\b", r"\1 not", text)
    suffix = {"m": "am", "s": "is", "re": "are", "ve": "have", "ll": "will", "d": "would"}
    text = re.sub(r"\b([a-z]+)'(m|s|re|ve|ll|d)\b",
                  lambda match: match[1] + " " + suffix[match[2]], text)
    return set(re.findall(r"[a-z0-9_\-]+", text))


def texts_for(row: dict[str, Any]) -> list[str]:
    texts = [row["content"], row.get("match_text", "")]
    for field in ("anchors", "topic_cues", "predecessor_match_texts"):
        value = row.get(field, ())
        texts.extend([value] if isinstance(value, str) else value)
    return texts


def shared(query: str, row: dict[str, Any]) -> set[str]:
    return words(query) & words(" ".join(texts_for(row)))


def substantive(text: str) -> set[str]:
    """Independent occurrence-level specification, not a production import.

    Only apostrophe constructions introduce generated auxiliary words. A raw
    lexical base still needs the closed set and original-case checks. Any one
    substantive occurrence on either side is sufficient to preserve a hit.
    """
    result = set()
    for token in re.finditer(r"[a-z0-9_\-]+(?:['’][a-z]+)?", text, re.I):
        raw = token[0]
        clause = re.split(r"[.!?\n]", text[:token.start()])[-1]
        root = raw.replace('’', "'").partition("'")[0]
        pronoun = raw.replace('’', "'") in {"I'm", "I've", "I'll", "I'd"}
        protected = (raw.isupper() or (root.isupper() and not pronoun)
                     or (root[0].isupper() and bool(re.search(r"[^\W\d_]", clause))))
        lower = raw.lower().replace('’', "'")
        if lower in {"won't", "can't", "shan't"}:
            if protected:
                result |= words(raw)
            continue
        if re.fullmatch(r"[a-z]+n't", lower):
            base = lower[:-3]
        elif re.fullmatch(r"[a-z]+'(?:m|s|re|ve|ll|d)", lower):
            base = lower.split("'")[0]
        else:
            base = lower
        for part in re.findall(r"[a-z0-9_\-]+", base):
            if protected or part not in FRAME_WORDS:
                result.add(part)
    return result


def check(case: dict[str, Any], current: list[dict[str, Any]], surface: str) -> None:
    selected = {row["id"] for row in current}
    main = set(case["main_" + surface])
    prior = set(case["previous_" + surface])
    rows = {row["id"]: row for row in case["rows"]}
    for missing in main - selected:
        common = shared(case["query"], rows[missing])
        meaningful = substantive(case["query"])
        for text in texts_for(rows[missing]):
            meaningful |= substantive(text)
        assert not common & meaningful, (case["id"], missing, common & meaningful)
    assert selected <= main | prior, (case["id"], "new admission", selected - main - prior)


def generate_pairs() -> list[dict[str, Any]]:
    """Generate the frozen random inputs at test time, without a dependency."""
    rng = random.Random(0x12409)
    vocab = "aquarium saxophone thermos passport version color date camera bicycle pottery telescope suitcase boots compass fossils geology cedar linen orbit sapphire".split()
    frames = [lambda w: f"Do you remember my {w}?", lambda w: f"What's the capacity of my {w}?",
              lambda w: f"What color is my {w}?", lambda w: f"Tell me about {w}.",
              lambda w: f"Which {w} do I use?", lambda w: f"I won't buy {w} before winter.",
              lambda w: f"I bought {w} on 2026-11-06.", lambda w: f"Can you tell me what I know about {w}?"]
    noise = "a curved neck pearl keys leather carrying case padded shoulder strap replacement mouthpiece wooden box beside the stand quiet pump glass heater programmable lamp switches nine evening".split()
    cases = []
    for i in range(1152):
        topic = rng.choice(vocab)
        query = rng.choice(frames)(topic)
        rows = []
        for j in range(1 if i < 1024 else rng.randint(2, 8)):
            content = ((f"My {topic if rng.random() < .6 else rng.choice(vocab)} is blue. "
                        if rng.random() < .8 else "I won't be there for you. ")
                       + " ".join(rng.choices(noise, k=rng.randint(0, 140))))
            row: dict[str, Any] = {"kind": "cognition", "id": f"row-{j}", "content": content,
                                    "confidence": rng.choice([400, 600, 800])}
            if i >= 1024 and rng.random() < .2:
                row["predecessor_match_texts"] = [f"My {rng.choice(vocab)} was green."]
            if i >= 1024 and rng.random() < .15:
                row["topic_cues"] = [rng.choice(vocab)]
            rows.append(row)
        cases.append({"id": f"random-{i:04}", "query": query, "rows": rows})
    return cases
