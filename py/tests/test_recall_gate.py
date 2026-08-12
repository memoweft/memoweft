from __future__ import annotations

from memoweft.world.recall_gate import GATE5_PROTOCOL_ID, evaluate_gate5_memory_reconstruction


def test_gate5_fixed_engineering_predicates_pass() -> None:
    report = evaluate_gate5_memory_reconstruction()
    assert GATE5_PROTOCOL_ID.endswith("@1")
    assert report.passed
    assert report.violations == ()
    assert {item.code for item in report.observations} == {
        "nanjing.exact_reconstruction", "rendering.raw_transcript_omitted", "anchors.equal_events_ambiguous", "semantics.opaque_id_and_order_invariant", "cognition.narrowing_preserves_lineage", "provenance.event_only_retained", "query.graph_immutable",
    }
