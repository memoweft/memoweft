"""Focused tests for the frozen pure-memory Gate 2 evaluator."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import memoweft.world.entity_continuity_gate as gate
from memoweft.world.entity_continuity_gate import (
    Gate2CorpusError,
    evaluate_entity_continuity_gate,
    evaluate_gate2_corpus,
    load_gate2_corpus,
)


_CORPUS = Path(__file__).parent / "fixtures" / "next" / "gate2-entity-continuity" / "corpus.json"


def test_frozen_corpus_fully_passes_with_complete_stable_observations() -> None:
    corpus = load_gate2_corpus(_CORPUS)
    first = evaluate_entity_continuity_gate(corpus)
    second = evaluate_entity_continuity_gate(corpus)

    assert first == second
    assert first.passed
    assert first.case_count == 17
    assert first.variant_count == 2
    assert len(first.observations) == first.expected_observation_count == 82
    assert tuple((item.case_id, item.variant_id, item.predicate_id) for item in first.observations) == first.expected_observation_keys
    assert first.observation_complete
    assert first.id_variant_semantically_equivalent
    assert not first.hard_failure_codes
    assert all(item.passed and item.hard_failure_code is None for item in first.observations)
    assert all("amber::91?K" not in item.detail and "cobalt/%E2%98%83/77" not in item.detail for item in first.observations)


def test_checkpoint_brackets_every_case_variant_in_order() -> None:
    corpus = load_gate2_corpus(_CORPUS)
    checkpoints: list[tuple[str, str, str]] = []

    report = evaluate_entity_continuity_gate(corpus, checkpoint=lambda case, variant, phase: checkpoints.append((case, variant, phase)))

    assert report.passed
    expected_pairs = [(case.id, variant.id) for variant in corpus.variants for case in corpus.cases]
    expected = [entry for pair in expected_pairs for entry in ((pair[0], pair[1], "before"), (pair[0], pair[1], "after"))]
    assert len(checkpoints) == 68
    assert checkpoints == expected


def test_evaluator_rejects_forged_validated_corpus_subset() -> None:
    corpus = load_gate2_corpus(_CORPUS)
    forged = replace(corpus, cases=corpus.cases[:1], variants=corpus.variants[:1])

    with pytest.raises(Gate2CorpusError) as captured:
        evaluate_entity_continuity_gate(forged)

    assert captured.value.codes == ("corpus.parsed_catalog.drift",)


def test_invalid_corpus_fails_closed_with_a_report_and_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    raw = cast(dict[str, object], json.loads(_CORPUS.read_text(encoding="utf-8")))
    raw["thresholds"] = {"every_case_passes": True}

    report = evaluate_gate2_corpus(raw)

    assert not report.passed
    assert report.observations[0].case_id == "corpus"
    assert report.hard_failure_codes == ("GATE2_HARD_FAILURE",)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    try:
        load_gate2_corpus(duplicate)
    except Gate2CorpusError as error:
        assert error.codes == ("corpus.duplicate_json_key",)
    else:  # pragma: no cover - assertion branch documents fail-closed API
        raise AssertionError("duplicate JSON keys must not be accepted")


@pytest.mark.parametrize(
    ("field", "value", "expected_detail"),
    [
        ("schema_version", True, "corpus.schema.invalid"),
        ("schema_version", 1.0, "corpus.schema.invalid"),
        ("corpus_id", "gate2-entity-continuity-v1-drift", "corpus.schema.invalid"),
    ],
)
def test_exact_scalar_freeze_rejects_bool_int_confusion_and_corpus_id_drift(field: str, value: object, expected_detail: str) -> None:
    raw = cast(dict[str, object], json.loads(_CORPUS.read_text(encoding="utf-8")))
    raw[field] = value

    report = evaluate_gate2_corpus(raw)

    assert not report.passed
    assert report.observations[0].detail == expected_detail


@pytest.mark.parametrize("value", [1, False, "true"])
def test_threshold_values_must_be_literal_true_booleans(value: object) -> None:
    raw = cast(dict[str, object], json.loads(_CORPUS.read_text(encoding="utf-8")))
    thresholds = cast(dict[str, object], raw["thresholds"])
    thresholds["every_case_passes"] = value

    report = evaluate_gate2_corpus(raw)

    assert not report.passed
    assert report.observations[0].detail == "corpus.thresholds.invalid"


@pytest.mark.parametrize("mutation", ["substitute_predicate", "reorder_cases", "reorder_variants", "omit_predicate"])
def test_catalog_drift_is_rejected_even_when_case_variant_counts_remain_valid(mutation: str) -> None:
    raw = cast(dict[str, object], json.loads(_CORPUS.read_text(encoding="utf-8")))
    cases = cast(list[dict[str, object]], raw["cases"])
    variants = cast(list[dict[str, object]], raw["variants"])
    if mutation == "substitute_predicate":
        predicates = cast(list[str], cases[0]["predicate_ids"])
        predicates[0] = "different.predicate"
    elif mutation == "reorder_cases":
        cases[0], cases[1] = cases[1], cases[0]
    elif mutation == "reorder_variants":
        variants[0], variants[1] = variants[1], variants[0]
    else:
        predicates = cast(list[str], cases[0]["predicate_ids"])
        predicates.pop()

    report = evaluate_gate2_corpus(raw)

    assert not report.passed
    assert report.observations[0].detail in {"corpus.case_catalog.drift", "corpus.variant_catalog.drift"}


def test_all_domain_ids_are_opaque_and_variant_ordering_reverses_target_distractor() -> None:
    role_words = ("owner", "mother", "friend", "other", "trip", "place", "world", "event", "cognition", "relationship")
    for opaque_values in gate._OPAQUE_IDS.values():
        assert all(word not in value.lower() for value in opaque_values.values() for word in role_words)
    amber = gate._OPAQUE_IDS["namespace_amber"]
    cobalt = gate._OPAQUE_IDS["namespace_cobalt"]
    assert amber["friend"] > amber["other"]
    assert cobalt["friend"] < cobalt["other"]
    for variant_id, namespace, reverse in gate._VARIANT_DEFINITIONS:
        variant = gate._Variant(variant_id, namespace, reverse)
        authority, roles = gate._authority(variant)
        gate._mention(authority, roles, "e:opaque", "小林", "2026-01-01T00:00:00+00:00")
        view = authority.view()
        domain_values = [item.id for item in view.graph.entities]
        domain_values.extend(item.id for item in view.graph.relationships)
        domain_values.extend(item.id for item in view.graph.events)
        domain_values.extend(item.id for item in view.graph.cognitions)
        domain_values.extend(item.id for item in view.evidence)
        domain_values.extend(item.conversation_id for item in view.evidence)
        domain_values.extend(item.continuity_scope or "" for item in view.evidence)
        assert all(word not in value.lower() for value in domain_values for word in role_words)
