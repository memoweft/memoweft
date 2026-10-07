"""Smooth-error attack tests for MemoWeft's fail-closed discipline layer.

These simulate a *seemingly flawless* model interpretation — spans verbatim,
propositions plausible, formed_by=stated, confidence nominal — and assert that
the discipline layer still refuses to write when it violates an invariant the
model cannot see (and a smart model will confidently get wrong).

This targets the recursive-drift failure: single-step credibility can be
perfect while structural correctness still fails closed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)
from test_hermes_batch_adapter_v3d import (
    _batch,
    _model,
    _run,
    _set_evidence,
    _v6_item,
)


def _route(script: list[Any]) -> Any:
    def route(_m: list[dict[str, str]], _sid: str = "", **kwargs: Any) -> dict[str, object]:
        return cast(dict[str, object], script.pop(0))
    return route


def _seed_evidence_raw(db_path: Path, evidence_id: str, raw: str) -> None:
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute(
            "UPDATE evidence SET raw_content = ? WHERE id = ?", (raw, evidence_id)
        )
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? "
            "WHERE evidence_id = ?",
            (hashlib.sha256(raw.encode("utf-8")).hexdigest(), evidence_id),
        )
    finally:
        db.close()


def _job_reason(db_path: Path) -> str:
    row = _job(db_path)
    wrj = json.loads(str(row["world_result_json"]))
    return str(wrj.get("reason", "<no reason>"))


def _row_counts(db_path: Path, table: str) -> int:
    db = sqlite3.connect(db_path)
    try:
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        db.close()


# ── A1: denial / objection read as a firm driver-brand assertion ────────────
def test_denial_objection_cannot_form_car_brand(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "我可不买小鹏"
    script = [
        _model(
            _batch(
                _v6_item(
                    "attribute",
                    "用户开的车是小鹏G6",
                    (0, len(raw)),
                    evidence_id="evidence-1",
                )
            )
        )
    ]
    _run(
        db_path, clock, list(script), ("evidence-1",),
        lambda path: _seed_evidence_raw(path, "evidence-1", raw),
    )
    row = _job(db_path)
    assert _row_counts(db_path, "cognition") == 0, _job_reason(db_path)
    assert row["state"] == "no_change", _job_reason(db_path)
    print("A1 denial-as-fact →", row["state"], "/", _job_reason(db_path))


# ── A2: hedged / past-tense preference read as a current firm preference ────
def test_hedged_past_preference_cannot_form_current_preference(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "我其实不太喜欢咖啡，以前爱喝"
    # A confident model collapses 'not anymore' into a current positive pref.
    script = [
        _model(
            _batch(
                _v6_item(
                    "preference",
                    "用户喜欢喝咖啡",
                    (0, len(raw)),
                    evidence_id="evidence-1",
                )
            )
        )
    ]
    _run(
        db_path, clock, list(script), ("evidence-1",),
        lambda path: _seed_evidence_raw(path, "evidence-1", raw),
    )
    row = _job(db_path)
    assert _row_counts(db_path, "cognition") == 0, _job_reason(db_path)
    print("A2 hedged-past-pref →", row["state"], "/", _job_reason(db_path))


# ── B: assistant guess recycled as spoken evidence (No-Self-Evidence) ───────
def test_assistant_claim_cannot_form_owner_cognition(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "我猜你的车是小鹏G6，对吧？"  # assistant said the guess, user never confirms
    script = [
        _model(
            _batch(
                _v6_item(
                    "attribute",
                    "用户开的车是小鹏G6",
                    (3, 10),
                    evidence_id="evidence-1",
                )
            )
        )
    ]
    _run(
        db_path, clock, list(script), ("evidence-1",),
        lambda path: _seed_evidence_raw(path, "evidence-1", raw),
    )
    row = _job(db_path)
    assert _row_counts(db_path, "cognition") == 0, _job_reason(db_path)
    print("B assistant-guess-only →", row["state"], "/", _job_reason(db_path))


# ── C: evidence rewritten after the boundary (content-hash drift) ───────────
def test_replay_after_evidence_rewrite_fails_closed(tmp_path: Path) -> None:
    """A job whose stored evidence was edited downstream (hash drift) must be
    caught by the content-hash check: the bound hash is fixed at the boundary,
    so a rewritten raw can no longer match it and the write is refused."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "我喜欢喝茉莉花茶"
    script = [
        _model(
            _batch(
                _v6_item(
                    "preference",
                    "用户喜欢喝茉莉花茶",
                    (0, len(raw)),
                    evidence_id="evidence-1",
                )
            )
        )
    ]
    _run(
        db_path, clock, list(script), ("evidence-1",),
        lambda path: _seed_evidence_raw(path, "evidence-1", raw),
    )
    first = _job(db_path)
    print("C first run →", first["state"], "/", _job_reason(db_path))

    # Rewrite the underlying evidence raw WITHOUT rebinding the boundary hash.
    _set_evidence(db_path, "evidence-1", "我喜欢喝可乐")
    # Reset the surviving job back to pending and re-drive with the SAME script.
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute("DELETE FROM terminal_outcome WHERE job_id='job-1'")
        db.execute(
            "UPDATE memory_world_job SET state='pending', attempts=0, "
            "completed_at=NULL, world_result_json=NULL, result_hash=NULL, "
            "terminal_state=NULL, terminal_detail=NULL, "
            "model_dispatch_started_at=NULL, model_completed_at=NULL, "
            "model_result_json=NULL, model_result_hash=NULL, "
            "model_usage_json=NULL, "
            "claim_owner=NULL, claim_token=NULL, claimed_at=NULL, "
            "lease_expires_at=NULL, heartbeat_at=NULL "
            "WHERE job_id='job-1'"
        )
    finally:
        db.close()
    processor = HermesBatchAdapterProcessor(
        str(db_path), _route(list(script)), clock=clock
    )
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    second = _job(db_path)
    print("C replay-after-drift →", second["state"], "/", _job_reason(db_path))
    # The evidence hash is bound at the boundary; drift changes the raw, and
    # even if a new cognition would be proposed it must refuse to write.
    assert second["state"] != "applied"
