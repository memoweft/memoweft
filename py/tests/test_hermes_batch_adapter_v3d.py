"""V6 formal batch adapter: contradict/retract slice (V3-D window).

Owner decisions (2026-08-16): retract forms and invalidates its target
immediately (recall stops returning it; provenance stays queryable);
retract is the replacement-less case of correct (same transition family);
a contradiction downgrades credibility but NEVER invalidates (only explicit
correct/retract does); attribute/preference/relationship are retractable,
naming is not.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from support.json_assertions import as_object, as_objects

from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor,
    _SYSTEM_PROMPT,
    _SYSTEM_PROMPT_EN,
    relationship_id_for,
    entity_id_for,
)
from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.trust.query_service import QueryService
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)

_RAW_COFFEE = "我喜欢喝咖啡"
_RAW_RETRACT = "那条删掉吧"
_RAW_CONTRADICT = "咖啡其实不好喝"
_RAW_REL_RETRACT = "小王不是小杨的女朋友"

_T0 = "2026-08-14T10:00:00.000Z"


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


def _v6_item(
    kind: str,
    proposition: str,
    *spans: tuple[int, int],
    action: str = "form",
    corrects_cognition_id: str | None = None,
    corrects_relationship_id: str | None = None,
    contradicts_cognition_id: str | None = None,
    retract: bool | None = None,
    formed_by: str = "stated",
    evidence_id: str = "evidence-1",
    relation_type: str | None = None,
    source_entity: dict[str, object] | None = None,
    target_entity: dict[str, object] | None = None,
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
    if corrects_cognition_id is not None:
        item["corrects_cognition_id"] = corrects_cognition_id
    if corrects_relationship_id is not None:
        item["corrects_relationship_id"] = corrects_relationship_id
    if contradicts_cognition_id is not None:
        item["contradicts_cognition_id"] = contradicts_cognition_id
    if retract is not None:
        item["retract"] = retract
    if relation_type is not None:
        item["relation_type"] = relation_type
    if source_entity is not None:
        item["source_entity"] = source_entity
    if target_entity is not None:
        item["target_entity"] = target_entity
    if extra is not None:
        item.update(extra)
    return item


def _batch(*items: Mapping[str, object]) -> dict[str, object]:
    return {"schema_version": 6, "result": "cognitions", "cognitions": list(items)}


def _model(content: dict[str, object]) -> dict[str, object]:
    return {"content": json.dumps(content), "model": "deepseek-v4-flash"}


def _seed_cognition(
    db_path: Path, cognition_id: str, content: str, confidence: int = 600
) -> None:
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "origin_id, occurred_at, recorded_at, raw_content, summary, "
        "allow_local_read, allow_cloud_read, allow_inference, deleted_at) "
        "VALUES ('seed-evidence', 'owner', 'spoken', 'hermes:test', 'seed', ?, ?, "
        "'seed evidence', 'seed evidence', 1, 1, 1, NULL)",
        (_T0, _T0),
    )
    db.execute(
        "INSERT OR IGNORE INTO cognition (id, subject_id, content, content_type, "
        "formed_by, confidence, cred_status, valid_at, created_at, updated_at) "
        "VALUES (?, 'owner', ?, 'preference', 'stated', ?, 'limited', ?, ?, ?)",
        (cognition_id, content, confidence, _T0, _T0, _T0),
    )
    db.execute(
        "INSERT OR IGNORE INTO cognition_evidence (cognition_id, evidence_id, "
        "relation) VALUES (?, 'seed-evidence', 'support')",
        (cognition_id,),
    )
    db.close()


def _seed_relationship(db_path: Path, rid: str, content: str) -> None:
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "origin_id, occurred_at, recorded_at, raw_content, summary, "
        "allow_local_read, allow_cloud_read, allow_inference, deleted_at) "
        "VALUES ('seed-evidence', 'owner', 'spoken', 'hermes:test', 'seed', ?, ?, "
        "'seed evidence', 'seed evidence', 1, 1, 1, NULL)",
        (_T0, _T0),
    )
    db.execute(
        "INSERT OR IGNORE INTO relationship (id, world_id, source_entity_id, "
        "target_entity_id, relation_type, content, formed_by, confidence, "
        "cred_status, invalid_at, created_at, updated_at) VALUES (?, 'owner', "
        "?, ?, 'girlfriend', ?, 'stated', 600, 'limited', NULL, ?, ?)",
        (
            rid,
            entity_id_for("owner", "小王"),
            entity_id_for("owner", "小杨"),
            content,
            _T0,
            _T0,
        ),
    )
    db.execute(
        "INSERT OR IGNORE INTO relationship_evidence (relationship_id, evidence_id, relation) "
        "VALUES (?, 'seed-evidence', 'support')",
        (rid,),
    )
    db.close()


# ── retract ─────────────────────────────────────────────────────────────────

def test_retract_cognition_invalidates_and_records_sidecar(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_RETRACT)
        _seed_cognition(path, target, "用户喜欢喝咖啡")

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    target = "cognition-coffee"
    script = [
        _model(
            _batch(
                _v6_item(
                    "preference", "那条删掉吧", (0, 5),
                    action="correct", retract=True,
                    corrects_cognition_id=target,
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    assert row["terminal_state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 1
    item = as_objects(outcome["cognitions"])[0]
    assert item["action"] == "correct"
    assert item["retract"] is True
    assert item["prior_cognition_id"] == target
    db = sqlite3.connect(db_path)
    try:
        cog = db.execute(
            "SELECT invalid_at FROM cognition WHERE id = ?", (target,)
        ).fetchone()
        assert cog[0] is not None  # explicit transition: invalid
        retraction = db.execute(
            "SELECT reason, revision, prior_cognition_id, prior_relationship_id "
            "FROM retraction"
        ).fetchone()
        assert retraction[0] == "retracts"
        assert retraction[1] == 1
        assert retraction[2] == target
        assert retraction[3] is None
        ledgers = [
            json.loads(str(r[0]))
            for r in db.execute("SELECT content FROM evidence_ledger").fetchall()
        ]
        assert any(l["relation"] == "retracts" for l in ledgers)
    finally:
        db.close()


def test_owner_preference_misattributed_to_friend_is_retracted_but_traceable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    target = "cognition-owner-fragrance"
    original = "我挑洗护用品时不喜欢浓烈香味"
    correction = "刚才不喜欢浓香的是朋友，不是我本人"
    script = [
        _model(
            _batch(
                _v6_item(
                    "preference",
                    correction,
                    (0, len(correction)),
                    action="correct",
                    retract=True,
                    corrects_cognition_id=target,
                )
            )
        )
    ]

    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", correction)
        _seed_cognition(path, target, "用户挑洗护用品时不喜欢浓烈香味")
        with sqlite3.connect(path) as db:
            db.execute(
                "UPDATE evidence SET raw_content = ?, summary = ? "
                "WHERE id = 'seed-evidence'",
                (original, original),
            )

    _run(db_path, clock, script, ("evidence-1",), setup)

    with sqlite3.connect(db_path) as db:
        snapshot = recall_world_snapshot(db, "owner", "挑洗护用品先考虑哪些条件")
        stored = {
            str(row[0]) for row in db.execute("SELECT raw_content FROM evidence")
        }
    assert snapshot is not None and snapshot.count == 0
    assert stored == {original, correction}

    trace = QueryService(db_path, subject_id="owner").get_world_item_provenance(
        "cognition", target
    )
    assert {as_object(entry["evidence"])["raw_content"] for entry in as_objects(trace["provenance"])} == {
        original,
        correction,
    }
    assert any(
        transition["transition_kind"] == "retracts"
        for transition in as_objects(trace["transition_history"])
    )


def test_temporary_state_contract_and_unrelated_recall_stay_scoped(tmp_path: Path) -> None:
    assert "这周不想社交" in _SYSTEM_PROMPT
    assert "must not become a permanent attribute" in _SYSTEM_PROMPT_EN

    db_path = tmp_path / "memoweft.sqlite3"
    _initialize_database(db_path)
    _seed_cognition(db_path, "cognition-fragrance", "用户挑洗护用品时不喜欢浓烈香味")
    with sqlite3.connect(db_path) as db:
        snapshot = recall_world_snapshot(db, "owner", "怎样整理 Python 项目的测试目录")
    assert snapshot is not None and snapshot.count == 0


def test_retract_relationship_invalidates(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_REL_RETRACT)
        _seed_relationship(path, rid, "小王是小杨的女朋友")

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小杨"),
    )
    script = [
        _model(
            _batch(
                _v6_item(
                    "relationship", "小王不是小杨的女朋友", (0, 9),
                    action="correct", retract=True,
                    corrects_relationship_id=rid,
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    item = as_objects(json.loads(str(row["world_result_json"]))["cognitions"])[0]
    assert item["prior_relationship_id"] == rid
    db = sqlite3.connect(db_path)
    try:
        rel = db.execute(
            "SELECT invalid_at FROM relationship WHERE id = ?", (rid,)
        ).fetchone()
        assert rel[0] is not None
        retraction = db.execute(
            "SELECT prior_cognition_id, prior_relationship_id FROM retraction"
        ).fetchone()
        assert retraction[0] is None
        assert retraction[1] == rid
    finally:
        db.close()


def test_retract_replay_is_zero_write(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_RETRACT)
        _seed_cognition(path, target, "用户喜欢喝咖啡")

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    target = "cognition-coffee"
    script = [
        _model(
            _batch(
                _v6_item(
                    "preference", "那条删掉吧", (0, 5),
                    action="correct", retract=True,
                    corrects_cognition_id=target,
                )
            )
        )
    ]
    _run(db_path, clock, script, ("evidence-1",), setup, job_id="job-1")
    first = json.loads(str(_job(db_path, job_id="job-1")["world_result_json"]))
    assert first["world_revision"] == 1
    script2 = [
        _model(
            _batch(
                _v6_item(
                    "preference", "那条删掉吧", (0, 5),
                    action="correct", retract=True,
                    corrects_cognition_id=target,
                )
            )
        )
    ]
    _run(db_path, clock, script2, ("evidence-1",), job_id="job-2")
    second = json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))
    assert second["world_revision"] == 1  # no bump
    assert as_objects(second["cognitions"]) == as_objects(first["cognitions"])


def test_retract_naming_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v6_item(
                    "naming", "那条删掉吧", (0, 5),
                    action="correct", retract=True,
                    corrects_cognition_id="cognition-x",
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
        == "unsupported_statement_kind"
    )


def test_retract_unknown_target_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v6_item(
                    "preference", "那条删掉吧", (0, 5),
                    action="correct", retract=True,
                    corrects_cognition_id="cognition-missing",
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
        == "retraction_target_unknown"
    )
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM retraction").fetchone()[0] == 0
    finally:
        db.close()


def test_retract_with_new_value_fields_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v6_item(
                    "relationship", "小王不是小杨的女朋友", (0, 9),
                    action="correct", retract=True,
                    corrects_relationship_id="relationship-x",
                    relation_type="girlfriend",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_REL_RETRACT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unexpected_relation_type"
    )


def test_retract_flag_must_be_boolean(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v6_item(
        "preference", "那条删掉吧", (0, 5),
        action="correct", corrects_cognition_id="cognition-x",
    )
    item["retract"] = "true"  # stringified — rejected
    script = [_model(_batch(item))]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_RETRACT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"] == "invalid_retract"
    )


def test_retract_rejected_in_v5_envelope(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v6_item(
        "preference", "那条删掉吧", (0, 5),
        action="correct", retract=True, corrects_cognition_id="cognition-x",
    )
    script = [
        {
            "content": json.dumps(
                {"schema_version": 5, "result": "cognitions", "cognitions": [item]}
            ),
            "model": "m",
        }
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_RETRACT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"] == "invalid_retract"
    )


# ── contradict ──────────────────────────────────────────────────────────────

def test_contradict_attaches_same_id_and_downgrades_only(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_CONTRADICT)
        _seed_cognition(path, target, "用户喜欢喝咖啡")

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    target = "cognition-coffee"
    script = [
        _model(
            _batch(
                _v6_item(
                    "preference", "咖啡其实不好喝", (0, 7),
                    action="contradict", contradicts_cognition_id=target,
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 1
    item = as_objects(outcome["cognitions"])[0]
    assert item["action"] == "contradict"
    assert item["cognition_id"] == target
    assert item["confidence"] == 0  # contradict pins the weakest carrier to 0
    assert item["cred_status"] == "candidate"
    db = sqlite3.connect(db_path)
    try:
        cog = db.execute(
            "SELECT content, confidence, cred_status, invalid_at "
            "FROM cognition WHERE id = ?",
            (target,),
        ).fetchone()
        assert cog[0] == "用户喜欢喝咖啡"  # content NOT replaced
        assert cog[1] == 0
        assert cog[2] == "candidate"
        assert cog[3] is None  # downgraded but NOT invalidated
        links = db.execute(
            "SELECT relation FROM cognition_evidence WHERE cognition_id = ?",
            (target,),
        ).fetchall()
        assert sorted(str(r[0]) for r in links) == ["contradict", "support"]
        ledgers = [
            json.loads(str(r[0]))
            for r in db.execute("SELECT content FROM evidence_ledger").fetchall()
        ]
        assert any(l["relation"] == "contradict" for l in ledgers)
    finally:
        db.close()


def test_contradict_replay_is_zero_write(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_CONTRADICT)
        _seed_cognition(path, target, "用户喜欢喝咖啡")

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    target = "cognition-coffee"
    payload = _batch(
        _v6_item(
            "preference", "咖啡其实不好喝", (0, 7),
            action="contradict", contradicts_cognition_id=target,
        )
    )
    _run(db_path, clock, [_model(payload)], ("evidence-1",), setup, job_id="job-1")
    first = json.loads(str(_job(db_path, job_id="job-1")["world_result_json"]))
    assert first["world_revision"] == 1
    _run(db_path, clock, [_model(payload)], ("evidence-1",), job_id="job-2")
    second = json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))
    assert second["world_revision"] == 1
    assert as_objects(second["cognitions"]) == as_objects(first["cognitions"])


def _cognition_id(subject: str, kind: str, proposition: str) -> str:
    return "cognition-" + hashlib.sha256(
        json.dumps(
            ["memoweft_cognition_v1", subject, kind, proposition],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_contradict_then_restate_stays_downgraded(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_CONTRADICT)
        _seed_cognition(path, target, "用户喜欢喝咖啡")

    """A later restatement attaches support but must NOT erase the
    contradiction: the chain still pins the weakest carrier to base 0."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    target = _cognition_id("owner", "preference", "用户喜欢喝咖啡")
    _run(
        db_path,
        clock,
        [
            _model(
                _batch(
                    _v6_item(
                        "preference", "咖啡其实不好喝", (0, 7),
                        action="contradict", contradicts_cognition_id=target,
                    )
                )
            )
        ],
        ("evidence-1",),
        setup,
        job_id="job-1",
    )
    # Restate the original proposition in a new boundary (support link).
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
        "allow_cloud_read, allow_inference) VALUES ('evidence-2', 'owner', "
        "'spoken', 'hermes:test', '2026-08-14T12:00:00.000Z', "
        "'2026-08-14T12:00:00.000Z', ?, ?, 1, 1, 1)",
        (_RAW_COFFEE, _RAW_COFFEE),
    )
    db.execute(
        "INSERT OR IGNORE INTO boundary_evidence_content (evidence_id, "
        "raw_content_hash) VALUES ('evidence-2', ?)",
        (hashlib.sha256(_RAW_COFFEE.encode("utf-8")).hexdigest(),),
    )
    db.close()
    restate = _v6_item(
        "preference", "用户喜欢喝咖啡", (0, 6),
        evidence_id="evidence-2",
    )
    _run(db_path, clock, [_model(_batch(restate))], ("evidence-2",), job_id="job-2")
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    item = as_objects(json.loads(str(row["world_result_json"]))["cognitions"])[0]
    assert item["confidence"] == 40  # base 0 + one extra support link
    assert item["cred_status"] == "candidate"
    db = sqlite3.connect(db_path)
    try:
        cog = db.execute(
            "SELECT invalid_at, confidence FROM cognition WHERE id = ?",
            (target,),
        ).fetchone()
        assert cog[0] is None  # still current
        assert cog[1] == 40
    finally:
        db.close()


def test_contradict_relationship_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v6_item(
                    "relationship", "小王不是小杨的女朋友", (0, 9),
                    action="contradict",
                    contradicts_cognition_id="relationship-x",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_REL_RETRACT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unsupported_statement_kind"
    )


def test_contradict_unknown_target_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v6_item(
                    "preference", "咖啡其实不好喝", (0, 7),
                    action="contradict", contradicts_cognition_id="cognition-missing",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_CONTRADICT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "contradiction_target_unknown"
    )


def test_contradict_rejected_in_v5_envelope(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v6_item(
        "preference", "咖啡其实不好喝", (0, 7),
        action="contradict", contradicts_cognition_id="cognition-x",
    )
    script = [
        {
            "content": json.dumps(
                {"schema_version": 5, "result": "cognitions", "cognitions": [item]}
            ),
            "model": "m",
        }
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_CONTRADICT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "invalid_cognition_action"
    )


def test_contradicts_cognition_id_rejected_on_form_items(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v6_item(
        "preference", "用户喜欢喝咖啡", (0, 6),
        contradicts_cognition_id="cognition-x",
    )
    script = [_model(_batch(item))]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_COFFEE),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unexpected_contradiction_target"
    )
