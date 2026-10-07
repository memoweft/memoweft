"""V7 window: relationship pattern/conflict semantics lock (Owner A/A/A/A).

Owner decisions (2026-08-16): zero pattern derivation (what the user states is
stored, both directions stay independent); same-source same-type different-
target relationships MAY coexist as current (no implicit replacement — users
resolve via the existing correct/retract paths); Recall does NOT annotate
conflicts; event semantic-facet queries stay a later window.  These tests
lock the current behavior so it cannot drift.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, cast

from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor,
    entity_id_for,
    relationship_id_for,
)
from memoweft.integrations.hermes.recall import format_recall, match_cognitions
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)


def _route(script: list[Any]) -> Callable[..., dict[str, object]]:
    def route(messages: list[dict[str, str]], session_id: str) -> dict[str, object]:
        del messages, session_id
        return cast(dict[str, object], script.pop(0))

    return route


def _set_evidence(db_path: Path, evidence_id: str, raw: str) -> None:
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


def _run(
    db_path: Path,
    clock: MutableClock,
    script: list[Any],
    evidence_ids: tuple[str, ...],
    setup: Callable[[Path], None] | None = None,
    job_id: str = "job-1",
) -> None:
    _initialize_database(db_path)
    _insert_job(db_path, clock, job_id=job_id, evidence_ids=evidence_ids)
    if setup is not None:
        setup(db_path)
    processor = HermesBatchAdapterProcessor(str(db_path), _route(script), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1


def _v8_rel(
    proposition: str,
    *spans: tuple[int, int],
    source: str,
    target: str,
    relation_type: str,
    evidence_id: str = "evidence-1",
) -> dict[str, object]:
    return {
        "action": "form",
        "target": "owner_self",
        "statement_kind": "relationship",
        "formed_by": "stated",
        "proposition": proposition,
        "supports": [
            {"evidence_id": evidence_id, "start": s, "end": e}
            for s, e in spans
        ],
        "source_entity": {"canonical_name": source, "kind": "person"},
        "target_entity": {"canonical_name": target, "kind": "person"},
        "relation_type": relation_type,
    }


def _batch(*items: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 8, "result": "cognitions", "cognitions": list(items)}


def _model(content: dict[str, object]) -> dict[str, object]:
    return {"content": json.dumps(content), "model": "deepseek-v4-flash"}


def test_no_reverse_pattern_derivation(tmp_path: Path) -> None:
    """Owner Q1A: forming A→B does NOT derive B→A.  Only the stated row."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v8_rel(
                    "小王是小李的女朋友", (0, 9),
                    source="小王", target="小李", relation_type="girlfriend",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "小王是小李的女朋友"),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            "SELECT source_entity_id, target_entity_id, relation_type, "
            "invalid_at FROM relationship"
        ).fetchall()
        assert len(rows) == 1  # no derived reverse row
        assert rows[0][2] == "girlfriend"
        assert rows[0][0] == entity_id_for("owner", "小王")
        assert rows[0][1] == entity_id_for("owner", "小李")
    finally:
        db.close()


def test_same_source_type_different_targets_coexist(tmp_path: Path) -> None:
    """Owner Q2A: conflicting current relationships coexist; no implicit
    invalidation — users resolve via correct/retract."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    first = _v8_rel(
        "小王是小杨的女朋友", (0, 9),
        source="小王", target="小杨", relation_type="girlfriend",
    )
    _run(
        db_path, clock, [_model(_batch(first))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "小王是小杨的女朋友"),
        job_id="job-1",
    )
    second = _v8_rel(
        "小王是小李的女朋友", (0, 9),
        source="小王", target="小李", relation_type="girlfriend",
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(second))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", "小王是小李的女朋友"),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        current = db.execute(
            "SELECT COUNT(*) FROM relationship WHERE invalid_at IS NULL"
        ).fetchone()[0]
        assert current == 2  # both remain current
        # The deterministic ids are distinct (different targets).
        assert relationship_id_for(
            "owner", entity_id_for("owner", "小王"), "girlfriend",
            entity_id_for("owner", "小杨"),
        ) != relationship_id_for(
            "owner", entity_id_for("owner", "小王"), "girlfriend",
            entity_id_for("owner", "小李"),
        )
    finally:
        db.close()


def test_conflict_resolved_via_existing_retract_path(tmp_path: Path) -> None:
    """Owner Q2A: conflicts are resolved EXPLICITLY through the existing
    retract path — no new machinery."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    first = _v8_rel(
        "小王是小杨的女朋友", (0, 9),
        source="小王", target="小杨", relation_type="girlfriend",
    )
    _run(
        db_path, clock, [_model(_batch(first))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "小王是小杨的女朋友"),
        job_id="job-1",
    )
    prior_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小杨"),
    )
    retract = {
        "action": "correct",
        "target": "owner_self",
        "statement_kind": "relationship",
        "formed_by": "stated",
        "proposition": "小王不是小杨的女朋友",
        "supports": [{"evidence_id": "evidence-2", "start": 0, "end": 9}],
        "corrects_relationship_id": prior_rid,
        "retract": True,
    }
    _run(
        db_path, clock, [_model(_batch(retract))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", "小王不是小杨的女朋友"),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        current = db.execute(
            "SELECT COUNT(*) FROM relationship WHERE invalid_at IS NULL"
        ).fetchone()[0]
        assert current == 0
        assert db.execute(
            "SELECT COUNT(*) FROM retraction WHERE prior_relationship_id = ?",
            (prior_rid,),
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_recall_does_not_annotate_conflicts(tmp_path: Path) -> None:
    """Owner Q3A: conflicting current relationships are returned verbatim
    with no extra annotation."""
    rows = [
        {"id": "r1", "content": "小王是小杨的女朋友", "confidence": 600},
        {"id": "r2", "content": "小王是小李的女朋友", "confidence": 600},
    ]
    hits = match_cognitions("小王的女朋友是谁", rows)
    text = format_recall(hits)
    # Both verbatim claims, no conflict marker appended.
    assert "小王是小杨的女朋友" in text
    assert "小王是小李的女朋友" in text
    assert "并存" not in text
    assert "冲突" not in text
