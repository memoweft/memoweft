"""V4 formal batch adapter: third-party world extension (V3-B window).

Owner decisions (2026-08-16): identity-class stable attributes of third
parties form (targeted cognition + cognition_target sidecar); third-party↔
third-party relationships form (arbitrary endpoints); evaluations never form.
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
    relationship_id_for,
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)

_RAW_ATTR = "小王是女生"
_RAW_THIRD_REL = "小王是小杨的女朋友"


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


def _v4_item(
    kind: str,
    proposition: str,
    *spans: tuple[int, int],
    entity: dict[str, object] | None = None,
    source_entity: dict[str, object] | None = None,
    target_entity: dict[str, object] | None = None,
    relation_type: str | None = None,
    formed_by: str = "stated",
    evidence_id: str = "evidence-1",
) -> dict[str, object]:
    item: dict[str, object] = {
        "action": "form",
        "target": "owner_self",
        "statement_kind": kind,
        "formed_by": formed_by,
        "proposition": proposition,
        "supports": [
            {"evidence_id": evidence_id, "start": s, "end": e}
            for s, e in spans
        ],
    }
    if entity is not None:
        item["entity"] = entity
    if source_entity is not None:
        item["source_entity"] = source_entity
    if target_entity is not None:
        item["target_entity"] = target_entity
    if relation_type is not None:
        item["relation_type"] = relation_type
    return item


def _batch(*items: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 4, "result": "cognitions", "cognitions": list(items)}


def _model(content: dict[str, object]) -> dict[str, object]:
    return {"content": json.dumps(content), "model": "deepseek-v4-flash"}


# ── third-party targeted attributes ────────────────────────────────────────

def test_third_party_attribute_forms_targeted_cognition(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v4_item(
                    "attribute", "小王是女生", (0, 5),
                    entity={"canonical_name": "小王", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ATTR),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    item = outcome["cognitions"][0]
    assert item["statement_kind"] == "attribute"
    assert item["target_entity_id"] == entity_id_for("owner", "小王")
    db = sqlite3.connect(db_path)
    try:
        cog = db.execute(
            "SELECT content, content_type, formed_by, confidence FROM cognition"
        ).fetchone()
        assert cog[0] == "小王是女生"
        assert cog[1] == "attribute"
        assert cog[2] == "stated"
        target = db.execute(
            "SELECT target_entity_id FROM cognition_target"
        ).fetchone()
        assert target is not None
        assert target[0] == entity_id_for("owner", "小王")
        # The entity row itself was created.
        assert db.execute(
            "SELECT canonical_name FROM entity WHERE id = ?",
            (entity_id_for("owner", "小王"),),
        ).fetchone()[0] == "小王"
        # No owner entity was created by a targeted attribute alone.
        assert db.execute(
            "SELECT COUNT(*) FROM entity WHERE id = ?",
            (owner_entity_id_for("owner"),),
        ).fetchone()[0] == 0
    finally:
        db.close()


def test_targeted_attribute_name_must_be_in_span_and_proposition(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v4_item(
                    "attribute", "小王是女生", (0, 5),
                    entity={"canonical_name": "小张", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ATTR),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "entity_name_not_in_span"
    )


def test_targeted_attribute_anchor_does_not_prepend_subject(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v4_item(
                    "attribute", "用户小王是女生", (0, 5),
                    entity={"canonical_name": "小王", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ATTR),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "proposition_not_anchored"
    )


def test_preference_with_entity_is_rejected_in_v4(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v4_item(
                    "preference", "小王喜欢喝茶", (0, 6),
                    entity={"canonical_name": "小王", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "小王喜欢喝茶"),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unexpected_entity"
    )


def test_owner_attribute_without_entity_still_works_in_v4(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v4_item("preference", "用户平时更喜欢冰美式", (0, 9))
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "我平时更喜欢冰美式。"),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_target"
        ).fetchone()[0] == 0  # owner-self: no sidecar row
    finally:
        db.close()


# ── third-party ↔ third-party relationships ────────────────────────────────

def test_third_party_relationship_forms_with_both_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v4_item(
                    "relationship", "小王是小杨的女朋友", (0, 9),
                    source_entity={"canonical_name": "小王", "kind": "person"},
                    target_entity={"canonical_name": "小杨", "kind": "person"},
                    relation_type="girlfriend",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_THIRD_REL),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    item = outcome["cognitions"][0]
    source_id = entity_id_for("owner", "小王")
    target_id = entity_id_for("owner", "小杨")
    assert item["source_entity_id"] == source_id
    assert item["target_entity_id"] == target_id
    db = sqlite3.connect(db_path)
    try:
        rel = db.execute(
            "SELECT source_entity_id, target_entity_id, relation_type, content "
            "FROM relationship"
        ).fetchone()
        assert rel is not None
        assert rel[0] == source_id
        assert rel[1] == target_id
        assert rel[2] == "girlfriend"
        assert rel[3] == "小王是小杨的女朋友"
        # Both endpoint entities were lazily created; the OWNER entity was not.
        names = {
            r[0] for r in db.execute("SELECT canonical_name FROM entity").fetchall()
        }
        assert names == {"小王", "小杨"}
    finally:
        db.close()


def test_source_name_must_be_in_proposition(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v4_item(
                    "relationship", "小王是小杨的女朋友", (0, 9),
                    source_entity={"canonical_name": "小李", "kind": "person"},
                    target_entity={"canonical_name": "小杨", "kind": "person"},
                    relation_type="girlfriend",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_THIRD_REL),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "entity_name_not_in_proposition"
    )


def test_self_relationship_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v4_item(
                    "relationship", "小王是小王的女朋友", (0, 9),
                    source_entity={"canonical_name": "小王", "kind": "person"},
                    target_entity={"canonical_name": "小王", "kind": "person"},
                    relation_type="girlfriend",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "小王是小王的女朋友"),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "self_relationship"
    )


def test_v3_envelope_rejects_source_entity(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v4_item(
        "relationship", "小王是小杨的女朋友", (0, 9),
        source_entity={"canonical_name": "小王", "kind": "person"},
        target_entity={"canonical_name": "小杨", "kind": "person"},
        relation_type="girlfriend",
    )
    script = [
        {
            "content": json.dumps(
                {"schema_version": 3, "result": "cognitions", "cognitions": [item]}
            ),
            "model": "m",
        }
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_THIRD_REL),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unexpected_source_entity"
    )


def test_third_party_relationship_restate_and_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    payload = _batch(
        _v4_item(
            "relationship", "小王是小杨的女朋友", (0, 9),
            source_entity={"canonical_name": "小王", "kind": "person"},
            target_entity={"canonical_name": "小杨", "kind": "person"},
            relation_type="girlfriend",
        )
    )
    _run(
        db_path, clock, [_model(payload)], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_THIRD_REL),
    )
    assert json.loads(str(_job(db_path)["world_result_json"]))["world_revision"] == 1
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
        "allow_cloud_read, allow_inference) VALUES "
        "('evidence-2', 'owner', 'spoken', 'hermes:test', "
        "'2026-08-14T12:00:00.000Z', '2026-08-14T12:00:00.000Z', ?, ?, 1, 1, 1)",
        (_RAW_THIRD_REL, _RAW_THIRD_REL),
    )
    db.execute(
        "INSERT OR IGNORE INTO boundary_evidence_content (evidence_id, "
        "raw_content_hash) VALUES ('evidence-2', ?)",
        (hashlib.sha256(_RAW_THIRD_REL.encode("utf-8")).hexdigest(),),
    )
    db.close()
    restate = _batch(
        _v4_item(
            "relationship", "小王是小杨的女朋友", (0, 9),
            source_entity={"canonical_name": "小王", "kind": "person"},
            target_entity={"canonical_name": "小杨", "kind": "person"},
            relation_type="girlfriend",
            evidence_id="evidence-2",
        )
    )
    _run(db_path, clock, [_model(restate)], ("evidence-2",), job_id="job-2")
    outcome = json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))
    assert outcome["world_revision"] == 2  # new support link
    assert outcome["cognitions"][0]["confidence"] == 640
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM relationship").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM relationship_evidence"
        ).fetchone()[0] == 2
    finally:
        db.close()

    # Exact replay: no bump.
    _run(db_path, clock, [_model(restate)], ("evidence-2",), job_id="job-3")
    outcome3 = json.loads(str(_job(db_path, job_id="job-3")["world_result_json"]))
    assert outcome3["world_revision"] == 2


def test_off_by_one_span_is_relocated_by_value(tmp_path: Path) -> None:
    """Real-model case: span (0,4) for the 5-codepoint "小王是女生".  The
    compiler relocates by value + unique substring (PM-approved fallback)."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v4_item(
                    "attribute", "小王是女生", (0, 4),
                    entity={"canonical_name": "小王", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ATTR),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        cog = db.execute("SELECT content FROM cognition").fetchone()
        assert cog[0] == "小王是女生"
    finally:
        db.close()


def test_mixed_v4_batch_targeted_attribute_and_third_party_relationship(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=("evidence-1", "evidence-2"))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (_RAW_ATTR,)
    )
    db.execute(
        "UPDATE evidence SET raw_content = ? WHERE id = 'evidence-2'",
        (_RAW_THIRD_REL,),
    )
    for eid, raw in (("evidence-1", _RAW_ATTR), ("evidence-2", _RAW_THIRD_REL)):
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? "
            "WHERE evidence_id = ?",
            (hashlib.sha256(raw.encode("utf-8")).hexdigest(), eid),
        )
    db.close()
    script = [
        _model(
            _batch(
                _v4_item(
                    "attribute", "小王是女生", (0, 5),
                    entity={"canonical_name": "小王", "kind": "person"},
                    evidence_id="evidence-1",
                ),
                _v4_item(
                    "relationship", "小王是小杨的女朋友", (0, 9),
                    source_entity={"canonical_name": "小王", "kind": "person"},
                    target_entity={"canonical_name": "小杨", "kind": "person"},
                    relation_type="girlfriend",
                    evidence_id="evidence-2",
                ),
            )
        )
    ]
    processor = HermesBatchAdapterProcessor(str(db_path), _route(script), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 1  # single bump
    kinds = [c["statement_kind"] for c in outcome["cognitions"]]
    assert kinds == ["attribute", "relationship"]
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM cognition_target").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM relationship").fetchone()[0] == 1
    finally:
        db.close()
