"""Regression harness protections; no assertions for the rejected vocabulary."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def evaluator() -> Any:
    path = Path(__file__).resolve().parents[2] / "bench/recall-quality/evaluate.py"
    spec = importlib.util.spec_from_file_location("quality_evaluator_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample() -> dict[str, Any]:
    return {"id": "positive", "category": "review_regression", "rank": 1,
            "selected": ["target"], "wrong": [], "must_hit": True}


def test_line_endings_have_the_same_frozen_hash(evaluator: Any) -> None:
    raw = '{"name":"樂"}\n'.encode()
    assert evaluator.fixture_hash(raw) == evaluator.fixture_hash(raw.replace(b"\n", b"\r\n"))
    assert evaluator.fixture_hash(raw) != evaluator.fixture_hash(raw.replace("樂".encode(), "云".encode()))


def test_changed_fixture_is_rejected(evaluator: Any) -> None:
    with pytest.raises(SystemExit, match="frozen fixture changed"):
        evaluator.check_fixtures({"review": "changed"}, {"fixture_hashes": {"review": "frozen"}})


@pytest.mark.parametrize("mutation,reason", [
    ({"selected": [], "rank": 0}, "lost baseline positive"),
    ({"selected": ["target", "wrong"], "wrong": ["wrong"]}, "new false recall"),
    ({"selected": [], "rank": 6}, "required review positive missing"),
])
def test_gate_rejects_per_case_regressions(evaluator: Any, mutation: dict[str, Any], reason: str) -> None:
    row = sample()
    baseline = {"splits": {"review": {"results": [deepcopy(row)]}}}
    row.update(mutation)
    with pytest.raises(SystemExit, match=reason):
        evaluator.check({"splits": {"review": {"results": [row]}}}, baseline)


def test_review_negative_requires_empty_result_even_if_unlabeled(evaluator: Any) -> None:
    row = {**sample(), "id": "negative", "rank": 0, "selected": [], "must_hit": False, "must_abstain": True}
    baseline = {"splits": {"review": {"results": [deepcopy(row)]}}}
    row["selected"] = ["unexpected"]
    with pytest.raises(SystemExit, match="required review abstention failed"):
        evaluator.check({"splits": {"review": {"results": [row]}}}, baseline)


@pytest.mark.parametrize("results", [[], [sample(), sample()]])
def test_missing_and_duplicate_cases_cannot_pass(evaluator: Any, results: list[dict[str, Any]]) -> None:
    baseline = {"splits": {"review": {"results": [sample()]}}}
    with pytest.raises(SystemExit, match="missing, duplicate or unexpected cases"):
        evaluator.check({"splits": {"review": {"results": results}}}, baseline)


def test_exact_mode_detects_selection_changes_but_normal_gate_allows_improvement(evaluator: Any) -> None:
    old = {**sample(), "must_hit": False, "rank": 0, "selected": []}
    baseline = {"splits": {"review": {"results": [old]}}}
    report = {"splits": {"review": {"results": [sample()]}}}
    evaluator.check(report, baseline)
    with pytest.raises(SystemExit, match="differs from recorded baseline"):
        evaluator.check(report, baseline, exact=True)


def test_review_contract_runs_through_production_preview(evaluator: Any) -> None:
    cases = json.loads((evaluator.HERE / "review.json").read_text(encoding="utf-8"))["cases"]
    required = [case for case in cases if case.get("must_hit") or case.get("must_abstain")]
    assert len(required) == 18
    result = evaluator.run(required, repeats=1)
    for row in result["results"]:
        if row["expect"]:
            assert row["rank"] == 1, row["id"]
        else:
            assert row["selected"] == [], row["id"]
