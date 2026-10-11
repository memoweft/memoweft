"""Offline tests for the high-risk experiment seam; never touch the blind set."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from runner import CandidateIndex, candidate_route, ORIGINAL_MATCH, ORIGINAL_ROWS, recall, seed


class ForcedIndex:
    def __init__(self, score=1.):
        self.score, self.seen = score, []

    def scores(self, query, rows, compute_b=True):
        self.seen = list(rows)
        return {(r["kind"], r["id"]): (self.score, self.score) for r in rows}


class RouteTests(unittest.TestCase):
    def snapshot(self, case, arm="AB", index=None):
        index = index or ForcedIndex()
        with TemporaryDirectory(prefix="mem5-test-") as folder:
            path = Path(folder) / "synthetic.sqlite3"
            seed(path, case)
            with closing(sqlite3.connect(path)) as db:
                before = db.total_changes
                with candidate_route(index, arm, {"A": .1, "B": .1}, case["query"]):
                    snapshot = recall.recall_world_snapshot(db, "fictional-owner", case["query"], model_tier=case.get("tier", "local"))
                self.assertIsNotNone(snapshot)
                self.assertEqual(before, db.total_changes)
                return snapshot.selected_item_ids, index

    def test_every_lifecycle_gate_after_forced_candidacy(self):
        for state in ["forgotten", "archived", "muted", "invalid", "local_denied", "cloud_denied", "expired", "future", "cross_evidence", "other_account"]:
            with self.subTest(state=state):
                case = {"memories": [{"id": "blocked", "content": "我一直偏好蓝色手账。", "state": state}], "query": "我的记录本偏好是什么？", "tier": "cloud" if state == "cloud_denied" else "local"}
                selected, index = self.snapshot(case)
                self.assertEqual([r["id"] for r in index.seen], ["blocked"])
                self.assertEqual(selected, ())

    def test_successor_currentness_is_reused(self):
        case = {"memories": [{"id": "old", "content": "我喜欢蓝色手账。"}, {"id": "new", "content": "我改为红色手账。", "predecessor": "old"}], "query": "我现在喜欢什么手账？"}
        selected, _ = self.snapshot(case)
        self.assertIn(("cognition", "new"), selected)
        self.assertNotIn(("cognition", "old"), selected)

    def test_original_hits_keep_order_and_name(self):
        case = {"person_name": "樂", "memories": [{"id": "person-fact", "content": "樂是我的同事。"}, {"id": "distractor", "content": "我喜欢蓝色手账。"}], "query": "樂是谁？"}
        original, _ = self.snapshot(case, "baseline")
        extended, _ = self.snapshot(case)
        self.assertEqual(extended[:len(original)], original)
        self.assertIn(("cognition", "person-fact"), extended)
        self.assertNotIn(("cognition", "distractor"), extended)

    def test_no_sidecar_no_semantic_candidate_restores_baseline(self):
        case = {"memories": [{"id": "known", "content": "我喜欢蓝色手账。"}], "query": "我喜欢什么手账？"}
        original, _ = self.snapshot(case, "baseline")
        extended, _ = self.snapshot(case, "A", ForcedIndex(0))
        self.assertEqual(original, extended)

    def test_patches_restore_after_exception(self):
        with self.assertRaises(RuntimeError):
            with candidate_route(ForcedIndex(), "A", {"A": .1, "B": .1}, "synthetic"):
                raise RuntimeError("test")
        self.assertIs(recall._current_world_rows, ORIGINAL_ROWS)
        self.assertIs(recall._match_world_rows, ORIGINAL_MATCH)

    def test_A_never_calls_embedder(self):
        class ForbiddenEmbedder:
            def encode(self, *args, **kwargs):
                raise AssertionError("A must not require a resident embedding model")
        index = CandidateIndex(ForbiddenEmbedder(), {"entries": {}}, [])
        scores = index.scores("蓝色手账怎么选？", [{"kind": "cognition", "id": "synthetic", "content": "我喜欢蓝色手账。"}], compute_b=False)
        self.assertEqual(scores[("cognition", "synthetic")], (0., 0.))


if __name__ == "__main__":
    unittest.main()
