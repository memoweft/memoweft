"""English precision/recall floors and byte-identical Chinese production recall."""
import json
from typing import Any

import pytest

from memoweft.integrations.hermes import recall
from support.english_recall_gate import HERE, cases, check, chinese, run


@pytest.fixture(scope="module")
def observed() -> list[dict[str, Any]]:
    return run(cases())


def test_english_precision_recall_and_per_case_main_floors(observed: list[dict[str, Any]]) -> None:
    baseline = json.loads((HERE / "english-main-baseline.json").read_text(encoding="utf-8"))
    items = cases()
    assert len(items) >= 100
    assert len({case["id"] for case in items}) == len(items)
    check(items, observed, baseline)


@pytest.mark.parametrize("case", [case for case in cases() if case.get("required")], ids=lambda c: c["id"])
def test_independent_review_examples(case: dict[str, Any], observed: list[dict[str, Any]]) -> None:
    row = next(row for row in observed if row["id"] == case["id"])
    baseline = json.loads((HERE / "english-main-baseline.json").read_text(encoding="utf-8"))
    main = next(row for row in baseline["results"] if row["id"] == case["id"])
    assert row["rank"] == 1 and set(row["wrong"]) <= set(main["wrong"])


@pytest.mark.parametrize("case_id", ["contraction-negative-0", "contraction-negative-1"])
def test_review_wont_contraction_never_creates_a_won_hit(case_id: str, observed: list[dict[str, Any]]) -> None:
    row = next(row for row in observed if row["id"] == case_id)
    assert row["selected"] == []


def test_all_638_chinese_full_snapshots_are_byte_identical_to_main() -> None:
    baseline = json.loads((HERE / "english-main-baseline.json").read_text(encoding="utf-8"))
    assert len(baseline["chinese_snapshots"]) == 638
    assert chinese(recall) == baseline["chinese_snapshots"]
