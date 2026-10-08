"""V7 formal batch adapter: first-class World Event slice (V4 window).

Owner decisions (2026-08-16): 已发生/已确定 events form (one-time content is
carried here, complementing the V3 cognition exclusions); time facets are
optional (unparseable time → empty, event still forms); participants/objects
bind by entity canonical names with lazy creation; events support retract
(v12 sidecar gains prior_event_id), correct stays a later window.
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
    owner_entity_id_for,
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

_RAW_EVENT = "昨天我和小王去了南京"
_RAW_EVENT_NO_TIME = "我和小王去吃了火锅"
_RAW_RETRACT = "没这事，别记了"


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


def _v7_item(
    proposition: str,
    *spans: tuple[int, int],
    action: str = "form",
    kind: str = "event",
    participants: list[dict[str, object]] | None = None,
    objects: list[dict[str, object]] | None = None,
    occurred_at: str | None = None,
    time_expression: str | None = None,
    retract: bool | None = None,
    corrects_event_id: str | None = None,
    formed_by: str = "stated",
    evidence_id: str = "evidence-1",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "action": action,
        "target": "owner_self",
        "statement_kind": kind,
        "formed_by": formed_by,
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
    if occurred_at is not None:
        item["occurred_at"] = occurred_at
    if time_expression is not None:
        item["time_expression"] = time_expression
    if retract is not None:
        item["retract"] = retract
    if corrects_event_id is not None:
        item["corrects_event_id"] = corrects_event_id
    if extra is not None:
        item.update(extra)
    return item


def _batch(*items: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 7, "result": "cognitions", "cognitions": list(items)}


def _model(content: dict[str, object]) -> dict[str, object]:
    return {"content": json.dumps(content), "model": "deepseek-v4-flash"}


def test_event_forms_with_participants_objects_and_time(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v7_item(
                    "昨天我和小王去了南京", (0, 10),
                    participants=[{"canonical_name": "小王", "kind": "person"}],
                    objects=[{"canonical_name": "南京", "kind": "place"}],
                    occurred_at="2026-08-15",
                    time_expression="昨天",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    assert row["terminal_state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 1
    item = outcome["cognitions"][0]
    assert item["statement_kind"] == "event"
    assert item.get("occurred_at") is None  # "昨天" cannot establish this guessed date.
    assert item["time_expression"] == "昨天"
    assert item["participants"] == [entity_id_for("owner", "小王")]
    assert item["objects"] == [entity_id_for("owner", "南京")]
    assert item["confidence"] == 600
    db = sqlite3.connect(db_path)
    try:
        ev = db.execute(
            "SELECT content, occurred_at, time_expression, participants_json, "
            "objects_json, confidence, cred_status, invalid_at FROM world_event"
        ).fetchone()
        assert ev[0] == "昨天我和小王去了南京"
        assert ev[1] is None
        assert ev[2] == "昨天"
        assert json.loads(str(ev[3])) == [entity_id_for("owner", "小王")]
        assert json.loads(str(ev[4])) == [entity_id_for("owner", "南京")]
        assert ev[5] == 600
        assert ev[7] is None
        # Entities lazily created with their kinds.
        kinds = {
            r[0]: r[1]
            for r in db.execute("SELECT canonical_name, kind FROM entity").fetchall()
        }
        assert kinds == {"小王": "person", "南京": "place"}
        assert db.execute(
            "SELECT COUNT(*) FROM world_event_evidence"
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_exact_event_replay_repairs_participant_and_object_entities(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    event = _v7_item(
        "昨天我和小王去了南京", (0, 10),
        participants=[{"canonical_name": "小王", "kind": "person"}],
        objects=[{"canonical_name": "南京", "kind": "place"}],
        occurred_at="2026-08-15",
        time_expression="昨天",
    )
    _run(
        db_path, clock, [_model(_batch(event))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
        job_id="job-1",
    )
    participant_id = entity_id_for("owner", "小王")
    object_id = entity_id_for("owner", "南京")
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute("DELETE FROM entity WHERE id IN (?, ?)", (participant_id, object_id))
    finally:
        db.close()

    _run(db_path, clock, [_model(_batch(event))], ("evidence-1",), job_id="job-2")

    row = _job(db_path, job_id="job-2")
    outcome = json.loads(str(row["world_result_json"]))
    assert row["state"] == "applied"
    assert outcome["world_revision"] == 2
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM entity WHERE id IN (?, ?)",
            (participant_id, object_id),
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT COUNT(*) FROM terminal_outcome WHERE job_id = 'job-2'"
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_event_forms_with_empty_time(tmp_path: Path) -> None:
    """Owner decision: unparseable/absent time → event still forms, time NULL."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v7_item(
                    "用户和小王去吃了火锅", (0, 9),
                    participants=[{"canonical_name": "小王", "kind": "person"}],
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT_NO_TIME),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    item = json.loads(str(row["world_result_json"]))["cognitions"][0]
    assert "occurred_at" not in item
    assert "time_expression" not in item
    db = sqlite3.connect(db_path)
    try:
        ev = db.execute(
            "SELECT occurred_at, time_expression FROM world_event"
        ).fetchone()
        assert ev[0] is None and ev[1] is None
    finally:
        db.close()


def test_event_owner_participant_resolves_to_owner_entity(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v7_item(
                    "昨天我和小王去了南京", (0, 10),
                    participants=[
                        {"canonical_name": "用户", "kind": "person"},
                        {"canonical_name": "小王", "kind": "person"},
                    ],
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
    )
    item = json.loads(str(_job(db_path)["world_result_json"]))["cognitions"][0]
    assert item["participants"] == [
        owner_entity_id_for("owner"),
        entity_id_for("owner", "小王"),
    ]
    db = sqlite3.connect(db_path)
    try:
        # The owner participant row is materialized like any other endpoint.
        owner = db.execute(
            "SELECT canonical_name FROM entity WHERE id = ?",
            (owner_entity_id_for("owner"),),
        ).fetchone()
        assert owner[0] == "用户"
    finally:
        db.close()


def test_event_invalid_occurred_at_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(_batch(_v7_item("昨天我和小王去了南京", (0, 10), occurred_at="8月1日")))
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"] == "invalid_event_time"
    )


def test_event_time_expression_must_be_in_proposition(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v7_item(
                    "昨天我和小王去了南京", (0, 10),
                    occurred_at="2026-08-15", time_expression="上周末",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "event_time_not_in_proposition"
    )


def test_event_participant_name_must_be_in_proposition(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v7_item(
                    "昨天我和小王去了南京", (0, 10),
                    participants=[{"canonical_name": "小杨", "kind": "person"}],
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "invalid_event_participants"
    )


def test_event_duplicate_entity_across_lists_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v7_item(
                    "昨天我和小王去了小王那里", (0, 12),
                    participants=[{"canonical_name": "小王", "kind": "person"}],
                    objects=[{"canonical_name": "小王", "kind": "place"}],
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "昨天我和小王去了小王那里"),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "duplicate_event_entity"
    )


def test_event_restate_support_merge_and_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v7_item(
        "昨天我和小王去了南京", (0, 10),
        participants=[{"canonical_name": "小王", "kind": "person"}],
        occurred_at="2026-08-15", time_expression="昨天",
    )
    _run(
        db_path, clock, [_model(_batch(item))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
        job_id="job-1",
    )
    assert json.loads(str(_job(db_path, job_id="job-1")["world_result_json"]))[
        "world_revision"
    ] == 1
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
        "allow_cloud_read, allow_inference) VALUES ('evidence-2', 'owner', "
        "'spoken', 'hermes:test', '2026-08-14T12:00:00.000Z', "
        "'2026-08-14T12:00:00.000Z', ?, ?, 1, 1, 1)",
        (_RAW_EVENT, _RAW_EVENT),
    )
    db.execute(
        "INSERT OR IGNORE INTO boundary_evidence_content (evidence_id, "
        "raw_content_hash) VALUES ('evidence-2', ?)",
        (hashlib.sha256(_RAW_EVENT.encode("utf-8")).hexdigest(),),
    )
    db.close()
    restate = _v7_item(
        "昨天我和小王去了南京", (0, 10),
        participants=[{"canonical_name": "小王", "kind": "person"}],
        occurred_at="2026-08-15", time_expression="昨天",
        evidence_id="evidence-2",
    )
    _run(db_path, clock, [_model(_batch(restate))], ("evidence-2",), job_id="job-2")
    outcome = json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))
    assert outcome["world_revision"] == 2
    assert outcome["cognitions"][0]["confidence"] == 640
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM world_event").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM world_event_evidence"
        ).fetchone()[0] == 2
    finally:
        db.close()
    # Exact replay: no bump.
    _run(db_path, clock, [_model(_batch(restate))], ("evidence-2",), job_id="job-3")
    outcome3 = json.loads(str(_job(db_path, job_id="job-3")["world_result_json"]))
    assert outcome3["world_revision"] == 2


def test_event_retract_invalidates_and_records_sidecar(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    form = _v7_item("昨天我和小王去了南京", (0, 10))
    _run(
        db_path, clock, [_model(_batch(form))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
        job_id="job-1",
    )
    event_id = world_event_id_for("owner", "昨天我和小王去了南京")
    retract = _v7_item(
        "没这事，别记了", (0, 6),
        action="correct", retract=True, corrects_event_id=event_id,
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(retract))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_RETRACT),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    item = json.loads(str(row["world_result_json"]))["cognitions"][0]
    assert item["retract"] is True
    assert item["prior_event_id"] == event_id
    db = sqlite3.connect(db_path)
    try:
        ev = db.execute(
            "SELECT invalid_at FROM world_event WHERE id = ?", (event_id,)
        ).fetchone()
        assert ev[0] is not None
        retraction = db.execute(
            "SELECT prior_event_id, prior_cognition_id, prior_relationship_id "
            "FROM retraction"
        ).fetchone()
        assert retraction[0] == event_id
        assert retraction[1] is None
        assert retraction[2] is None
    finally:
        db.close()


def test_event_retract_replay_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    form = _v7_item("昨天我和小王去了南京", (0, 10))
    _run(
        db_path, clock, [_model(_batch(form))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
        job_id="job-1",
    )
    event_id = world_event_id_for("owner", "昨天我和小王去了南京")
    retract = _v7_item(
        "没这事，别记了", (0, 6),
        action="correct", retract=True, corrects_event_id=event_id,
        evidence_id="evidence-2",
    )
    setup2 = lambda path: _set_evidence(path, "evidence-2", _RAW_RETRACT)
    _run(db_path, clock, [_model(_batch(retract))], ("evidence-2",), setup2, job_id="job-2")
    first = json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))
    assert first["world_revision"] == 2
    _run(db_path, clock, [_model(_batch(retract))], ("evidence-2",), job_id="job-3")
    second = json.loads(str(_job(db_path, job_id="job-3")["world_result_json"]))
    assert second["world_revision"] == 2
    assert second["cognitions"] == first["cognitions"]


def test_event_correct_unknown_target_is_zero_write(tmp_path: Path) -> None:
    """V6 supersedes the V4 restriction: event correct with replacement parses;
    an unknown prior id fails closed at apply."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v7_item(
                    "没这事，别记了", (0, 6),
                    action="correct", corrects_event_id="world-event-x",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_RETRACT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "event_correction_target_unknown"
    )


def test_event_rejected_in_v6_envelope(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v7_item("昨天我和小王去了南京", (0, 10))
    script = [
        {
            "content": json.dumps(
                {"schema_version": 6, "result": "cognitions", "cognitions": [item]}
            ),
            "model": "m",
        }
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_EVENT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unsupported_statement_kind"
    )


def test_event_contradict_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v7_item(
                    "昨天我没去南京", (0, 7),
                    action="contradict",
                    extra={"contradicts_cognition_id": "cognition-x"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "昨天我没去南京"),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unsupported_statement_kind"
    )
