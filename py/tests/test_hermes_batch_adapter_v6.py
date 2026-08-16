"""V8 envelope V6 patch: event correct (replacement).

V4 deferred event correct; V6 completes the correct family: an explicit
correction with a new narrative replaces the prior World Event (invalid_at
transition + corrects ledger, mirroring the relationship 改口 precedent).
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
    world_event_id_for,
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)

_RAW_EVENT = "上周末我和小王去了南京"
_RAW_CORRECT = "其实上周末我和小王去的是杭州"


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


def _v8_event(
    proposition: str,
    *spans: tuple[int, int],
    action: str = "form",
    participants: list[dict[str, object]] | None = None,
    objects: list[dict[str, object]] | None = None,
    corrects_event_id: str | None = None,
    evidence_id: str = "evidence-1",
) -> dict[str, object]:
    item: dict[str, object] = {
        "action": action,
        "target": "owner_self",
        "statement_kind": "event",
        "formed_by": "stated",
        "proposition": proposition,
        "supports": [
            {"evidence_id": evidence_id, "start": s, "end": e}
            for s, e in spans
        ],
    }
    if participants is not None:
        item["participants"] = participants
    if objects is not None:
        item["objects"] = objects
    if corrects_event_id is not None:
        item["corrects_event_id"] = corrects_event_id
    return item


def _batch(*items: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 8, "result": "cognitions", "cognitions": list(items)}


def _model(content: dict[str, object]) -> dict[str, object]:
    return {"content": json.dumps(content), "model": "deepseek-v4-flash"}


def _form_event(db_path: Path, clock: MutableClock) -> str:
    form = _v8_event(
        "上周末我和小王去了南京", (0, 10),
        participants=[{"canonical_name": "小王", "kind": "person"}],
        objects=[{"canonical_name": "南京", "kind": "place"}],
    )
    _run(
        db_path, clock, [_model(_batch(form))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
        job_id="job-1",
    )
    assert _job(db_path, job_id="job-1")["state"] == "applied"
    return world_event_id_for("owner", "上周末我和小王去了南京")


def test_event_correct_replaces_prior(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    prior_id = _form_event(db_path, clock)
    correct = _v8_event(
        "其实上周末我和小王去的是杭州", (0, 14),
        action="correct", corrects_event_id=prior_id,
        participants=[{"canonical_name": "小王", "kind": "person"}],
        objects=[{"canonical_name": "杭州", "kind": "place"}],
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(correct))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_CORRECT),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 2
    item = outcome["cognitions"][0]
    new_id = world_event_id_for("owner", "其实上周末我和小王去的是杭州")
    assert item["action"] == "correct"
    assert item["prior_event_id"] == prior_id
    assert item["replacement_event_id"] == new_id
    db = sqlite3.connect(db_path)
    try:
        prior = db.execute(
            "SELECT invalid_at FROM world_event WHERE id = ?", (prior_id,)
        ).fetchone()
        assert prior[0] is not None
        new = db.execute(
            "SELECT content, invalid_at, objects_json FROM world_event WHERE id = ?",
            (new_id,),
        ).fetchone()
        assert new[1] is None
        assert new[0] == "其实上周末我和小王去的是杭州"
        assert json.loads(str(new[2])) == [entity_id_for("owner", "杭州")]
        ledgers = [
            json.loads(str(r[0]))
            for r in db.execute("SELECT content FROM evidence_ledger").fetchall()
        ]
        assert any(
            l["relation"] == "corrects"
            and l.get("prior_event_id") == prior_id
            for l in ledgers
        )
    finally:
        db.close()


def test_event_correct_replay_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    prior_id = _form_event(db_path, clock)
    correct = _v8_event(
        "其实上周末我和小王去的是杭州", (0, 14),
        action="correct", corrects_event_id=prior_id,
        participants=[{"canonical_name": "小王", "kind": "person"}],
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(correct))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_CORRECT),
        job_id="job-2",
    )
    first = json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))
    assert first["world_revision"] == 2
    _run(db_path, clock, [_model(_batch(correct))], ("evidence-2",), job_id="job-3")
    second = json.loads(str(_job(db_path, job_id="job-3")["world_result_json"]))
    assert second["world_revision"] == 2
    assert second["cognitions"] == first["cognitions"]


def test_event_correct_merges_into_existing_current(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    prior_id = _form_event(db_path, clock)
    # The replacement narrative is ALREADY current (formed separately).
    form2 = _v8_event(
        "其实上周末我和小王去的是杭州", (0, 14),
        participants=[{"canonical_name": "小王", "kind": "person"}],
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(form2))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_CORRECT),
        job_id="job-2",
    )
    new_id = world_event_id_for("owner", "其实上周末我和小王去的是杭州")
    correct = _v8_event(
        "其实上周末我和小王去的是杭州", (0, 14),
        action="correct", corrects_event_id=prior_id,
        participants=[{"canonical_name": "小王", "kind": "person"}],
        evidence_id="evidence-2",
    )
    _run(db_path, clock, [_model(_batch(correct))], ("evidence-2",), job_id="job-3")
    row = _job(db_path, job_id="job-3")
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM world_event").fetchone()[0] == 2
        merged = db.execute(
            "SELECT confidence FROM world_event WHERE id = ?", (new_id,)
        ).fetchone()
        assert merged[0] == 600  # same single evidence, no extra links
    finally:
        db.close()


def test_event_correct_identical_proposition_is_zero_write(tmp_path: Path) -> None:
    """The new narrative equals the prior content → the deterministic new id
    equals the prior id → compile-time self check rejects."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    prior_id = _form_event(db_path, clock)
    correct = _v8_event(
        "上周末我和小王去了南京", (0, 10),
        action="correct", corrects_event_id=prior_id,
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(correct))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_EVENT),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "correction_target_is_itself"
    )
