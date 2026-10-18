"""Independent acceptance specification; nouns are never framework words."""
import re
import random
from typing import Any

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


def words(text: str) -> set[str]:
    text = text.casefold().replace('’', "'")
    for contraction, full in {"won't": "will not", "can't": "can not", "shan't": "shall not"}.items():
        text = re.sub(r"\b" + re.escape(contraction) + r"\b", full, text)
    text = re.sub(r"\b([a-z]+)n't\b", r"\1 not", text)
    suffix = {"m": "am", "s": "is", "re": "are", "ve": "have", "ll": "will", "d": "would"}
    text = re.sub(r"\b([a-z]+)'(m|s|re|ve|ll|d)\b",
                  lambda match: match[1] + " " + suffix[match[2]], text)
    return set(re.findall(r"[a-z0-9_\-]+", text))


def shared(query: str, row: dict[str, Any]) -> set[str]:
    texts = [row["content"], row.get("match_text", "")]
    for field in ("anchors", "topic_cues", "predecessor_match_texts"):
        value = row.get(field, ())
        texts.extend([value] if isinstance(value, str) else value)
    return words(query) & words(" ".join(texts))


def check(case: dict[str, Any], current: list[dict[str, Any]], surface: str) -> None:
    selected = {row["id"] for row in current}
    main = set(case["main_" + surface])
    prior = set(case["previous_" + surface])
    rows = {row["id"]: row for row in case["rows"]}
    for missing in main - selected:
        common = shared(case["query"], rows[missing])
        assert common <= FRAME_WORDS, (case["id"], missing, common - FRAME_WORDS)
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
