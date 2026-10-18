"""Independent review literals plus fact-first inversions, frozen before fixing."""
import json

import pytest

from support.english_recall_gate import HERE, cases, check, digest, run


@pytest.mark.parametrize("suite,count", [("english-review-attributes", 31), ("english-fact-inverse", 30)])
def test_attribute_question_recall_and_false_returns_do_not_regress_main(suite: str, count: int) -> None:
    items = cases(suite)
    assert len(items) == count
    baseline = json.loads((HERE / f"{suite}-main-baseline.json").read_text(encoding="utf-8"))
    check(items, run(items), baseline, suite=suite)


def test_all_fifteen_independent_chinese_mixed_snapshots_match_main() -> None:
    suite = "recall-mixed-review"
    items = cases(suite)
    assert len(items) == 15
    baseline = json.loads((HERE / f"{suite}-main-baseline.json").read_text(encoding="utf-8"))
    assert digest((HERE / f"{suite}.json").read_bytes()) == baseline["fixture_sha256"]
    assert run(items) == baseline["results"]
