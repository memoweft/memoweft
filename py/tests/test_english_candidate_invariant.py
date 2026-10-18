"""Main substantive candidates survive independently of scores and thresholds."""
import json
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest

from memoweft.integrations.hermes import recall
from support.english_recall_gate import HERE, seed
from support.english_invariant_spec import FRAME_WORDS, check, generate_pairs, words
from support.english_recall_gate import digest
from memoweft.integrations.hermes import recall_english_policy as policy
from memoweft.store import open_db

RANDOM = json.loads((HERE / "english-invariant-random.json").read_text(encoding="utf-8"))["cases"]
LABELLED = json.loads((HERE / "english-invariant-labelled.json").read_text(encoding="utf-8"))["cases"]
SNAPSHOTS = json.loads((HERE / "english-invariant-snapshots.json").read_text(encoding="utf-8"))["cases"]


def test_seeded_generation_and_frozen_main_references() -> None:
    assert len(RANDOM) == 1152 and len(LABELLED) == 248 and len(SNAPSHOTS) == 192
    assert generate_pairs() == [{field: case[field] for field in ("id", "query", "rows")} for case in RANDOM]
    manifest = json.loads((HERE / "english-invariant-manifest.json").read_text(encoding="utf-8"))
    for filename, expected in manifest["fixtures"].items():
        assert digest((HERE / filename).read_bytes()) == expected, filename


@pytest.mark.parametrize("surface", ["world", "cognition"])
@pytest.mark.parametrize("group", [RANDOM, LABELLED], ids=["1152-seeded-random", "all-english-collections"])
def test_every_dropped_main_candidate_is_function_only(surface: str, group: list[dict[str, Any]]) -> None:
    matcher = recall._match_world_rows if surface == "world" else recall.match_cognitions
    baseline = recall._match_world_rows_main if surface == "world" else recall.match_cognitions_main
    for case in group:
        assert [row["id"] for row in baseline(case["query"], case["rows"])] == case["main_" + surface]
        result = matcher(case["query"], case["rows"])
        check(case, cast(list[dict[str, Any]], result), surface)


def test_closed_grammar_vocabulary_contains_no_attribute_nouns() -> None:
    assert policy.FRAME_WORDS == FRAME_WORDS
    assert len(policy.FRAME_WORDS) == 110
    assert not {"version", "date", "color", "size", "year", "brand", "material", "aquarium"} & policy.FRAME_WORDS


@pytest.mark.parametrize("text", ["What's my version?", "What’s my version?", "Nora won't buy boots.",
                                 "I haven't used a telescope.", "I'm here; I'll go; I'd stay; we're ready."])
def test_complete_contraction_expansion_agrees_with_independent_spec(text: str) -> None:
    assert policy.words(text) == words(text)
    if text.startswith(("What's", "What’s")):
        assert {"what", "is", "version"} <= policy.words(text)
    if "won't" in text:
        assert {"will", "not"} <= policy.words(text) and "won" not in policy.words(text)


@pytest.mark.parametrize("case", SNAPSHOTS, ids=lambda c: c["id"])
def test_full_snapshot_preserves_main_through_count_and_character_budgets(tmp_path: Path, case: dict[str, Any]) -> None:
    path = tmp_path / "world.sqlite3"
    seed(path, {"memories": case["rows"]})
    with closing(open_db(str(path))) as db:
        before = list(db.iterdump())
        result = recall.recall_world_snapshot(db, "fictional-owner", case["query"], lang=case["lang"])
        assert result is not None
        check(case, [{"id": item_id} for _, item_id in result.selected_item_ids], "world")
        selected = {item_id for _, item_id in result.selected_item_ids}
        for row in case["rows"]:
            if row["id"] in selected and row["content"] in case["main_text"]:
                assert row["content"] in result.rendered_recall
        assert list(db.iterdump()) == before


def test_optional_improvement_failure_never_invalidates_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    query = "Do you remember my aquarium?"
    rows = [{"id": "target", "kind": "cognition", "content": "My aquarium holds 180 litres.", "confidence": 600}]
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic optional nomination failure")
    monkeypatch.setattr(recall, "_match_world_rows_improved", broken)
    monkeypatch.setattr(recall, "_match_cognitions_improved", broken)
    assert recall._match_world_rows(query, rows) == recall._match_world_rows_main(query, rows)
    assert recall.match_cognitions(query, rows) == recall.match_cognitions_main(query, rows)
    path = tmp_path / "failure.sqlite3"
    seed(path, {"memories": rows})
    with closing(open_db(str(path))) as db:
        snapshot = recall.recall_world_snapshot(db, "fictional-owner", query, lang="en")
        assert snapshot is not None and snapshot.selected_item_ids == (("cognition", "target"),)


def test_arbitrarily_low_new_scores_cannot_remove_substantive_main_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    query = "What's the capacity of my thermos?"
    rows = [{"id": "target", "kind": "cognition", "content": "My thermos holds 750 ml.", "confidence": 600}]
    monkeypatch.setattr(recall, "_lexical_score", lambda query, text: -1000.0)
    for matcher in (recall._match_world_rows, recall.match_cognitions):
        assert [item["id"] for item in matcher(query, rows)] == ["target"]
