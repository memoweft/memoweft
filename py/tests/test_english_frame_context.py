"""Context regressions: review token ideas, independently authored facts."""
import json
import random
from typing import Any

import pytest

from memoweft.integrations.hermes import recall
from memoweft.integrations.hermes import recall_english_policy as policy
from support.english_recall_gate import HERE, cases, check, run
from support.english_invariant_spec import check as check_candidate


@pytest.fixture(scope="module")
def context_results() -> list[dict[str, Any]]:
    return run(cases("english-frame-context"))


def test_review_context_collection_frozen_main_floors(context_results: list[dict[str, Any]]) -> None:
    baseline = json.loads((HERE / "english-frame-context-main-baseline.json").read_text(encoding="utf-8"))
    check(cases("english-frame-context"), context_results, baseline, suite="english-frame-context")


@pytest.mark.parametrize("case", cases("english-frame-context"), ids=lambda c: c["id"])
def test_review_29_contexts_retain_main_answer(case: dict[str, Any], context_results: list[dict[str, Any]]) -> None:
    baseline = json.loads((HERE / "english-frame-context-main-baseline.json").read_text(encoding="utf-8"))
    old = next(item for item in baseline["results"] if item["id"] == case["id"])
    current = next(item for item in context_results if item["id"] == case["id"])
    assert old["selected"] == ["target"], "must first reproduce an actual main hit"
    assert current["selected"] == old["selected"]
    rows = [dict(row, kind="cognition", confidence=600) for row in case["memories"]]
    for matcher in (recall._match_world_rows, recall.match_cognitions):
        assert [item["id"] for item in matcher(case["query"], rows)] == ["target"]


@pytest.mark.parametrize("query,content", [
    ("IT's in charge of what?", "IT's responsible for the wireless network."),
    ("IT’s in charge of what?", "IT’s responsible for the wireless network."),
    ("WHO's role?", "WHO's responsible for the vaccination program."),
], ids=["IT-straight", "IT-curly", "WHO-possessive"])
def test_review_apostrophe_root_case_retains_main(query: str, content: str) -> None:
    # The review supplied the IT sentence and its curly-apostrophe counterpart;
    # the WHO question uses a synthetic organization-role fact.
    rows = [dict(id="target", kind="cognition", confidence=600, content=content)]
    for main, current in ((recall._match_world_rows_main, recall._match_world_rows),
                          (recall.match_cognitions_main, recall.match_cognitions)):
        assert [item["id"] for item in main(query, rows)] == ["target"]
        assert [item["id"] for item in current(query, rows)] == ["target"]
    observed = run([dict(id="apostrophe-root", query=query, memories=rows, expect=["target"])])
    assert observed[0]["selected"] == ["target"]


@pytest.mark.parametrize("apostrophe", ["'", "’"])
@pytest.mark.parametrize("root", ["IT", "WHO", "US", "A", "I"])
@pytest.mark.parametrize("side", ["query", "memory"])
def test_uppercase_apostrophe_root_on_either_side_is_substantive(root: str, apostrophe: str, side: str) -> None:
    possessive = root + apostrophe + "s"
    query = possessive + " role?" if side == "query" else root.lower() + " role?"
    content = root.lower() + " coordinates supplies." if side == "query" else possessive + " coordinating supplies."
    row = dict(id="target", kind="cognition", content=content)
    assert policy.compose(query, [row], [row], [], max_items=5) == [row]


def test_does_female_deer_plural_is_an_explicit_known_limit() -> None:
    # Accepted rare noun sense, not an xfail or a negative answer label.
    query = "Where are the does?"
    rows = [dict(id="target", kind="cognition", confidence=600, content="The does sleep in the meadow.")]
    for main, current in ((recall._match_world_rows_main, recall._match_world_rows),
                          (recall.match_cognitions_main, recall.match_cognitions)):
        assert [item["id"] for item in main(query, rows)] == ["target"]
        assert current(query, rows) == [], "documented doe-plural limitation remains"
    observed = run([dict(id="does-known-limit", query=query, memories=rows, expect=["target"])])
    assert observed[0]["selected"] == [] and observed[0]["rank"] == 0


@pytest.mark.parametrize("word", "he be as in at do and for not or a".split())
def test_documented_lowercase_rare_senses_remain_known_limits(word: str) -> None:
    # Accepted limitation, not an xfail/skip or a positive relabelled negative.
    # These lowercase symbols/operators/notes/blood groups are indistinguishable
    # from function words without additional semantic context. Uppercase/title
    # case variants above are protected by the mandatory original-case rule.
    query = f"what is my {word}?"
    rows = [dict(id="target", kind="cognition", confidence=600,
                 content=f"my {word} denotes the label beside the synthetic laboratory specimen.")]
    for main, current in ((recall._match_world_rows_main, recall._match_world_rows),
                          (recall.match_cognitions_main, recall.match_cognitions)):
        assert [item["id"] for item in main(query, rows)] == ["target"]
        # The old improvement already nominates 'not'; compose is permitted,
        # never required, to remove a grammar-only main hit.
        assert [item["id"] for item in current(query, rows)] == (["target"] if word == "not" else [])
    assert policy.compose(query, rows, rows, [], max_items=5) == [], "documented lowercase rare-sense limitation"


@pytest.mark.parametrize("word", "s t d m ll re ve".split())
def test_bare_contraction_fragments_are_substantive(word: str) -> None:
    query = f"what about {word}?"
    row = dict(id="target", kind="cognition", content=f"the {word} label identifies a specimen.")
    assert policy.compose(query, [row], [row], [], max_items=5) == [row]


@pytest.mark.parametrize("word", "May US IT WHO AM Don I A M D".split())
@pytest.mark.parametrize("side", ["query", "memory"])
def test_original_case_on_either_side_protects_shared_word(word: str, side: str) -> None:
    query_word, memory_word = (word, word.lower()) if side == "query" else (word.lower(), word)
    query = f"what about {query_word}?"
    row = dict(id="target", kind="cognition", content=f"the {memory_word} label identifies a specimen.")
    assert policy.compose(query, [row], [row], [], max_items=5) == [row]


def test_title_case_at_sentence_start_is_not_automatically_a_name() -> None:
    row = dict(id="target", kind="cognition", content="who knows the answer.")
    assert policy.compose("Who remembers?", [row], [row], [], max_items=5) == []


@pytest.mark.parametrize("query,content,retain", [
    ("What's ready?", "what's pending.", False),
    ("I won't ski.", "we won't sail.", False),
    ("I'm skiing.", "I'm sailing.", False),
    ("I'll ski.", "I'll sail.", False),
    ("I'd ski.", "I'd sail.", False),
    ("I've skied.", "I've sailed.", False),
    ("i'm skiing.", "i'm sailing.", False),
    ("i'll ski.", "i'll sail.", False),
    ("i'd ski.", "i'd sail.", False),
    ("we're skiing.", "we're sailing.", False),
    ("we've skied.", "we've sailed.", False),
    ("we can't ski.", "we can't sail.", False),
])
def test_apostrophe_grammar_respects_original_root_case(query: str, content: str, retain: bool) -> None:
    # Ordinary personal-pronoun I is conventionally capitalized. Preserve the
    # existing grammar-only contraction behavior rather than treating it as IT.
    row = dict(id="target", kind="cognition", content=content)
    assert policy.compose(query, [row], [row], [], max_items=5) == ([row] if retain else [])


@pytest.mark.parametrize("field", ["match_text", "anchors", "topic_cues", "predecessor_match_texts"])
def test_case_is_preserved_in_each_authorized_projection(field: str) -> None:
    row: dict[str, Any] = dict(id="target", kind="cognition", content="a short correction.")
    row[field] = "the WHO bulletin" if field == "match_text" else ["the WHO bulletin"]
    assert policy.compose("what about who?", [row], [row], [], max_items=5) == [row]


@pytest.mark.parametrize("content", ["who knows. the WHO report arrived.", "the WHO report arrived. who knows."])
def test_one_protected_occurrence_is_sufficient(content: str) -> None:
    row = dict(id="target", kind="cognition", content=content)
    assert policy.compose("what about who?", [row], [row], [], max_items=5) == [row]


@pytest.mark.parametrize("surface", ["world", "cognition"])
def test_640_seeded_case_and_contraction_contexts_follow_independent_invariant(surface: str) -> None:
    rng = random.Random(0x12404)
    vocabulary = "can will may must might mine won don us being tell tells who am it he be as in at do and for not or a s t d m ll re ve thermos version color".split()
    contractions = ["what's", "what’s", "I'm", "I'll", "I'd", "we're", "we've", "won't", "can't", "isn't"]
    main = recall._match_world_rows_main if surface == "world" else recall.match_cognitions_main
    prior = recall._match_world_rows_improved if surface == "world" else recall._match_cognitions_improved
    current = recall._match_world_rows if surface == "world" else recall.match_cognitions
    for index in range(640):
        word = rng.choice(vocabulary + contractions)
        qword = rng.choice([word, word.upper(), word.capitalize()])
        mword = rng.choice([word, word.upper(), word.capitalize()])
        query = rng.choice([f"what about my {qword}?", f"{qword} is what?", f"what? {qword} is what?"])
        body = rng.choice([f"my {mword} denotes the specimen.", f"{mword} denotes my specimen."])
        rows = [dict(id="target", kind="cognition", content=body, confidence=600)]
        case = dict(id=f"context-seed-{index}", query=query, rows=rows,
                    **{"main_" + surface: [item["id"] for item in main(query, rows)],
                       "previous_" + surface: [item["id"] for item in prior(query, rows)]})
        check_candidate(case, [dict(item) for item in current(query, rows)], surface)
