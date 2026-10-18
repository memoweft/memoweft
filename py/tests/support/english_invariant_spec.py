"""Independent acceptance specification; nouns are never framework words."""
import re
from typing import Any

FRAME_WORDS = frozenset("""
what which who whom whose where when why how
am is are was were be been being do does did have has had
can could will would should shall may might must
a an the and or but not
of to in on at for from with by about as into
i me my mine we us our ours you your yours he him his she her hers
it its they them their theirs this that these those
remember remembers remembered tell tells told know knows knew recall remind
use uses used like likes liked prefer prefers think thinks
always usually often really actually very anything
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
