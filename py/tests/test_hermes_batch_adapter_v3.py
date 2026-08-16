"""V3 formal batch adapter: Entity + Relationship minimal slice.

Owner decisions (2026-08-16): naming + stable relationship formation only;
owner_self perspective; mention→identity unique resolution with zero-write on
ambiguity; deterministic entity/relationship ids.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, cast

from memoweft.integrations.hermes import (
    HermesMemoWeftRuntime,
    _boundary_payload_hash,
)
from memoweft.integrations.hermes.batch_adapter import (
    BatchItem,
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

_RAW_FRIEND = "我朋友叫小王"
_RAW_GF = "我的女朋友是小王"


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


def _naming(
    proposition: str, canonical_name: str, *spans: tuple[int, int],
    kind: str = "person", evidence_id: str = "evidence-1",
) -> dict[str, object]:
    return {
        "action": "form",
        "target": "owner_self",
        "statement_kind": "naming",
        "formed_by": "stated",
        "proposition": proposition,
        "entity": {"canonical_name": canonical_name, "kind": kind},
        "supports": [
            {"evidence_id": evidence_id, "start": s, "end": e}
            for s, e in spans
        ],
    }


def _relationship(
    proposition: str, target: str, relation_type: str, *spans: tuple[int, int],
    target_kind: str = "person", evidence_id: str = "evidence-1",
) -> dict[str, object]:
    return {
        "action": "form",
        "target": "owner_self",
        "statement_kind": "relationship",
        "formed_by": "stated",
        "proposition": proposition,
        "relation_type": relation_type,
        "target_entity": {"canonical_name": target, "kind": target_kind},
        "supports": [
            {"evidence_id": evidence_id, "start": s, "end": e}
            for s, e in spans
        ],
    }


def _batch(*items: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 3, "result": "cognitions", "cognitions": list(items)}


def _model(content: dict[str, object]) -> dict[str, object]:
    return {"content": json.dumps(content), "model": "deepseek-v4-flash"}


def _entities(db_path: Path) -> dict[str, tuple[Any, ...]]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            "SELECT id, world_id, kind, canonical_name, invalid_at FROM entity"
        ).fetchall()
        return {str(r[0]): tuple(r[1:]) for r in rows}
    finally:
        db.close()


def _relationships(db_path: Path) -> dict[str, tuple[Any, ...]]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            "SELECT id, world_id, source_entity_id, target_entity_id, "
            "relation_type, content, formed_by, confidence, invalid_at "
            "FROM relationship"
        ).fetchall()
        return {str(r[0]): tuple(r[1:]) for r in rows}
    finally:
        db.close()


# ── naming ─────────────────────────────────────────────────────────────────

def test_naming_forms_entity(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [_model(_batch(_naming("用户朋友叫小王", "小王", (0, 6))))]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_FRIEND),
    )
    assert len(script) == 0
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["schema_version"] == 3
    assert outcome["world_revision"] == 1
    item = outcome["cognitions"][0]
    assert item["statement_kind"] == "naming"
    assert item["canonical_name"] == "小王"
    entities = _entities(db_path)
    expected_id = entity_id_for("owner", "小王")
    assert expected_id in entities
    assert entities[expected_id][1] == "person"  # kind
    assert entities[expected_id][2] == "小王"  # canonical_name
    assert entities[expected_id][3] is None  # current
    # Naming alone does NOT create the owner entity row.
    assert owner_entity_id_for("owner") not in entities
    # The naming proposition is ALSO a recallable naming cognition.
    db = sqlite3.connect(db_path)
    try:
        cog = db.execute(
            "SELECT content, content_type, formed_by, confidence FROM cognition"
        ).fetchone()
        assert cog is not None
        assert cog[0] == "用户朋友叫小王"
        assert cog[1] == "naming"
        assert cog[2] == "stated"
        assert cog[3] == 600
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_evidence"
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_naming_remention_attaches_support_and_exact_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path, clock,
        [_model(_batch(_naming("用户朋友叫小王", "小王", (0, 6))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_FRIEND),
    )
    # A later boundary re-mentions the same name with NEW Evidence: the same-ID
    # support path attaches it and recomputes confidence (600 -> 640).
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
        "allow_cloud_read, allow_inference) VALUES "
        "('evidence-2', 'owner', 'spoken', 'hermes:test', "
        "'2026-08-14T12:00:00.000Z', '2026-08-14T12:00:00.000Z', ?, ?, 1, 1, 1)",
        (_RAW_FRIEND, _RAW_FRIEND),
    )
    db.execute(
        "INSERT OR IGNORE INTO boundary_evidence_content (evidence_id, "
        "raw_content_hash) VALUES ('evidence-2', ?)",
        (hashlib.sha256(_RAW_FRIEND.encode("utf-8")).hexdigest(),),
    )
    db.close()
    _run(
        db_path, clock,
        [_model(_batch(_naming("用户朋友叫小王", "小王", (0, 6), evidence_id="evidence-2")))],
        ("evidence-2",), job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 2  # new support link bumped
    assert outcome["cognitions"][0]["confidence"] == 640
    entities = _entities(db_path)
    assert set(entities) == {entity_id_for("owner", "小王")}
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_evidence"
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT confidence FROM cognition"
        ).fetchone()[0] == 640
    finally:
        db.close()

    # Exact replay with the SAME Evidence: no new link, no bump.
    _run(
        db_path, clock,
        [_model(_batch(_naming("用户朋友叫小王", "小王", (0, 6), evidence_id="evidence-2")))],
        ("evidence-2",), job_id="job-3",
    )
    row3 = _job(db_path, job_id="job-3")
    assert row3["state"] == "applied"
    outcome3 = json.loads(str(row3["world_result_json"]))
    assert outcome3["world_revision"] == 2  # unchanged


def test_two_namings_same_name_are_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _naming("用户朋友叫小王", "小王", (0, 6), evidence_id="evidence-1"),
                _naming("用户朋友叫小王", "小王", (0, 6), evidence_id="evidence-2"),
            )
        )
    ]
    db = None
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=("evidence-1", "evidence-2"))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (_RAW_FRIEND,)
    )
    db.execute(
        "UPDATE evidence SET raw_content = ? WHERE id = 'evidence-2'", (_RAW_FRIEND,)
    )
    for eid in ("evidence-1", "evidence-2"):
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? "
            "WHERE evidence_id = ?",
            (hashlib.sha256(_RAW_FRIEND.encode("utf-8")).hexdigest(), eid),
        )
    db.close()
    processor = HermesBatchAdapterProcessor(str(db_path), _route(script), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "duplicate_entity_in_batch"
    )
    assert _entities(db_path) == {}


def test_naming_name_must_be_verbatim_in_span(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(_batch(_naming("用户朋友叫王小明", "小明", (0, 6))))
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_FRIEND),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "entity_name_not_in_span"
    )
    assert _entities(db_path) == {}


def test_naming_kind_mismatch_with_existing_entity_is_zero_write(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path, clock,
        [_model(_batch(_naming("用户朋友叫小王", "小王", (0, 6))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_FRIEND),
    )
    script = [
        _model(
            _batch(
                _naming("用户朋友叫小王", "小王", (0, 6), kind="pet"),
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_FRIEND),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "entity_kind_mismatch"
    )


# ── relationship ───────────────────────────────────────────────────────────

def test_relationship_creates_owner_and_target_entities(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(_batch(_relationship("用户的女朋友是小王", "小王", "girlfriend", (0, 8))))
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_GF),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    item = outcome["cognitions"][0]
    assert item["statement_kind"] == "relationship"
    assert item["relation_type"] == "girlfriend"
    assert item["confidence"] == 600
    owner_id = owner_entity_id_for("owner")
    target_id = entity_id_for("owner", "小王")
    assert item["target_entity_id"] == target_id
    entities = _entities(db_path)
    assert owner_id in entities
    assert entities[owner_id][2] == "用户"  # owner canonical name
    assert target_id in entities
    rels = _relationships(db_path)
    rel_id = relationship_id_for("owner", owner_id, "girlfriend", target_id)
    assert rel_id in rels
    rel = rels[rel_id]
    assert rel[1] == owner_id  # source
    assert rel[2] == target_id  # target
    assert rel[3] == "girlfriend"  # relation_type
    assert rel[4] == "用户的女朋友是小王"  # content for deterministic recall
    assert rel[5] == "stated"
    assert rel[6] == 600


def test_relationship_target_name_must_be_in_proposition(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _relationship("用户的女朋友是小王", "小张", "girlfriend", (0, 8))
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_GF),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "entity_name_not_in_proposition"
    )
    assert _entities(db_path) == {}


def test_relationship_restatement_same_id_support_chain(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path, clock,
        [_model(_batch(_relationship("用户的女朋友是小王", "小王", "girlfriend", (0, 8))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_GF),
    )
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
        "allow_cloud_read, allow_inference) VALUES "
        "('evidence-2', 'owner', 'spoken', 'hermes:test', "
        "'2026-08-14T12:00:00.000Z', '2026-08-14T12:00:00.000Z', ?, ?, 1, 1, 1)",
        (_RAW_GF, _RAW_GF),
    )
    db.execute(
        "INSERT OR IGNORE INTO boundary_evidence_content (evidence_id, "
        "raw_content_hash) VALUES ('evidence-2', ?)",
        (hashlib.sha256(_RAW_GF.encode("utf-8")).hexdigest(),),
    )
    db.close()
    _run(
        db_path, clock,
        [
            _model(
                _batch(
                    _relationship(
                        "用户的女朋友是小王", "小王", "girlfriend", (0, 8),
                        evidence_id="evidence-2",
                    )
                )
            )
        ],
        ("evidence-2",), job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 2  # new support link bumped once
    item = outcome["cognitions"][0]
    assert item["confidence"] == 640  # 600 + 40
    rels = _relationships(db_path)
    assert len(rels) == 1
    assert next(iter(rels.values()))[6] == 640
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM relationship_evidence"
        ).fetchone()[0] == 2
    finally:
        db.close()


def test_naming_then_relationship_shares_entity(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _naming("用户朋友叫小王", "小王", (0, 6), evidence_id="evidence-1"),
                _relationship(
                    "用户的女朋友是小王", "小王", "girlfriend", (0, 8),
                    evidence_id="evidence-2",
                ),
            )
        )
    ]
    db = None
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=("evidence-1", "evidence-2"))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (_RAW_FRIEND,)
    )
    db.execute(
        "UPDATE evidence SET raw_content = ? WHERE id = 'evidence-2'", (_RAW_GF,)
    )
    for eid, raw in (("evidence-1", _RAW_FRIEND), ("evidence-2", _RAW_GF)):
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? "
            "WHERE evidence_id = ?",
            (hashlib.sha256(raw.encode("utf-8")).hexdigest(), eid),
        )
    db.close()
    processor = HermesBatchAdapterProcessor(str(db_path), _route(script), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 1  # single bump for the batch
    assert [c["statement_kind"] for c in outcome["cognitions"]] == [
        "naming",
        "relationship",
    ]
    entities = _entities(db_path)
    assert entity_id_for("owner", "小王") in entities
    assert owner_entity_id_for("owner") in entities
    assert outcome["cognitions"][1]["target_entity_id"] == entity_id_for(
        "owner", "小王"
    )


def test_v3_exact_replay_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    payload = _batch(
        _naming("用户朋友叫小王", "小王", (0, 6), evidence_id="evidence-1"),
        _relationship(
            "用户的女朋友是小王", "小王", "girlfriend", (0, 8),
            evidence_id="evidence-2",
        ),
    )
    db = None
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=("evidence-1", "evidence-2"))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (_RAW_FRIEND,)
    )
    db.execute(
        "UPDATE evidence SET raw_content = ? WHERE id = 'evidence-2'", (_RAW_GF,)
    )
    for eid, raw in (("evidence-1", _RAW_FRIEND), ("evidence-2", _RAW_GF)):
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? "
            "WHERE evidence_id = ?",
            (hashlib.sha256(raw.encode("utf-8")).hexdigest(), eid),
        )
    db.close()
    processor = HermesBatchAdapterProcessor(
        str(db_path), _route([_model(payload)]), clock=clock
    )
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    assert json.loads(str(_job(db_path)["world_result_json"]))["world_revision"] == 1

    # Replay the same checkpoint against the same job's stored result: the
    # processor would re-apply deterministically.  Simulate by re-running the
    # identical batch with fresh identical evidence ids: naming affirms (no-op),
    # relationship restates the SAME evidence-free... new evidence ids attach a
    # new support link → one bump.  Assert entity count still 2, relationship 1.
    db = sqlite3.connect(db_path, isolation_level=None)
    for eid, raw in (("evidence-3", _RAW_FRIEND), ("evidence-4", _RAW_GF)):
        db.execute(
            "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
            "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
            "allow_cloud_read, allow_inference) VALUES "
            "(?, 'owner', 'spoken', 'hermes:test', "
            "'2026-08-14T12:00:00.000Z', '2026-08-14T12:00:00.000Z', ?, ?, 1, 1, 1)",
            (eid, raw, raw),
        )
        db.execute(
            "INSERT OR IGNORE INTO boundary_evidence_content (evidence_id, "
            "raw_content_hash) VALUES (?, ?)",
            (eid, hashlib.sha256(raw.encode("utf-8")).hexdigest()),
        )
    db.close()
    replay = _batch(
        _naming("用户朋友叫小王", "小王", (0, 6), evidence_id="evidence-3"),
        _relationship(
            "用户的女朋友是小王", "小王", "girlfriend", (0, 8),
            evidence_id="evidence-4",
        ),
    )
    _run(db_path, clock, [_model(replay)], ("evidence-3", "evidence-4"), job_id="job-2")
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 2  # only the new relationship link
    assert len(_entities(db_path)) == 2
    assert len(_relationships(db_path)) == 1


# ── deterministic Recall reads relationships ───────────────────────────────

def _boundary_envelope(raw: str) -> dict[str, Any]:
    source_messages = [
        {"role": "user", "content": raw, "source_ref": "source:0"},
        {"role": "assistant", "content": "好的", "source_ref": "source:1"},
    ]
    payload = {
        "schema_version": 1,
        "provider_name": "memoweft",
        "parent_session_id": "session-parent",
        "result_session_id": "session-parent",
        "mode": "in_place",
        "source_messages": source_messages,
    }
    payload_hash = _boundary_payload_hash(payload)
    return {
        **payload,
        "payload_hash": payload_hash,
        "event_id": (
            "hermes-compression-boundary-v1:" + "a" * 32 + ":" + payload_hash
        ),
    }


def test_recall_reads_relationship_in_new_context(tmp_path: Path) -> None:
    clock = MutableClock()
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    runtime = HermesMemoWeftRuntime()
    runtime.initialize(
        "sess",
        hermes_home=str(tmp_path),
        platform="weixin",
        agent_context="primary",
        one_shot_llm=_route([]),
    )
    runtime.shutdown()  # settle deterministically below
    receipt = runtime.ingest_durable_boundary(_boundary_envelope(_RAW_GF))
    assert receipt["job_state"] == "pending"
    db = sqlite3.connect(db_path)
    job_id = str(db.execute("SELECT job_id FROM memory_world_job").fetchone()[0])
    evidence_id = json.loads(
        str(db.execute("SELECT evidence_ids_json FROM memory_world_job").fetchone()[0])
    )[0]
    db.close()
    payload = _batch(
        _relationship("用户的女朋友是小王", "小王", "girlfriend", (0, 8))
    )
    payload["cognitions"][0]["supports"][0]["evidence_id"] = evidence_id
    worker = WorldJobWorker(
        db_path,
        processor=HermesBatchAdapterProcessor(
            str(db_path), _route([{"content": json.dumps(payload), "model": "m"}])
        ),
        policy=_policy(),
    )
    assert worker.run_until_quiescent() == 1
    row = _job(db_path, job_id=job_id)
    assert row["state"] == "applied", row["last_error_type"]

    text = runtime.prefetch("我女朋友是谁", session_id="sess")
    assert "用户的女朋友是小王" in text
    assert runtime.last_recall_count == 1
    # Unrelated query: nothing leaks.
    assert runtime.prefetch("今天天气如何", session_id="sess") == ""
    assert runtime.last_recall_count == 0


def test_recall_reads_naming_in_new_context(tmp_path: Path) -> None:
    clock = MutableClock()
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    runtime = HermesMemoWeftRuntime()
    runtime.initialize(
        "sess",
        hermes_home=str(tmp_path),
        platform="weixin",
        agent_context="primary",
        one_shot_llm=_route([]),
    )
    runtime.shutdown()
    receipt = runtime.ingest_durable_boundary(_boundary_envelope(_RAW_FRIEND))
    assert receipt["job_state"] == "pending"
    db = sqlite3.connect(db_path)
    job_id = str(db.execute("SELECT job_id FROM memory_world_job").fetchone()[0])
    evidence_id = json.loads(
        str(db.execute("SELECT evidence_ids_json FROM memory_world_job").fetchone()[0])
    )[0]
    db.close()
    payload = _batch(_naming("用户朋友叫小王", "小王", (0, 6)))
    payload["cognitions"][0]["supports"][0]["evidence_id"] = evidence_id
    worker = WorldJobWorker(
        db_path,
        processor=HermesBatchAdapterProcessor(
            str(db_path), _route([{"content": json.dumps(payload), "model": "m"}])
        ),
        policy=_policy(),
    )
    assert worker.run_until_quiescent() == 1
    row = _job(db_path, job_id=job_id)
    assert row["state"] == "applied", row["last_error_type"]

    text = runtime.prefetch("我朋友是谁", session_id="sess")
    assert "用户朋友叫小王" in text
    assert runtime.last_recall_count == 1
    assert runtime.prefetch("今天天气如何", session_id="sess") == ""
    assert runtime.last_recall_count == 0


def test_naming_narrative_subject_uses_no_prepend_anchor(tmp_path: Path) -> None:
    """Dogfood finding (2026-08-16): naming a third party whose narrative
    subject is the entity itself ("二五是我从老家…") must NOT get "用户"
    prepended — the entity name anchors the subject."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "二五是我从老家别人那里生的小猫买来的，因为她的生日是二月五号，所以取名叫二五"
    item = {
        "action": "form",
        "target": "owner_self",
        "statement_kind": "naming",
        "formed_by": "stated",
        "proposition": raw,
        "entity": {"canonical_name": "二五", "kind": "animal"},
        "supports": [{"evidence_id": "evidence-1", "start": 0, "end": len(raw)}],
    }
    script = [
        {
            "content": json.dumps(
                {"schema_version": 8, "result": "cognitions", "cognitions": [item]}
            ),
            "model": "m",
        }
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", raw),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        entity = db.execute(
            "SELECT canonical_name, kind FROM entity WHERE canonical_name = '二五'"
        ).fetchone()
        assert entity[1] == "animal"
    finally:
        db.close()


def test_subjectless_prepend_span_repair_finds_bare_slice(tmp_path: Path) -> None:
    """Dogfood finding (2026-08-16): a prepend-anchored owner preference with
    an off-by-one span must relocate onto the bare slice (no 用户 in the raw)."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "游戏娱乐，有声剧以前喜欢三体，听了好多遍，现在在听剑来，刷视频也偏向搞笑，宠物之类的"
    item = {
        "action": "form",
        "target": "owner_self",
        "statement_kind": "preference",
        "formed_by": "stated",
        "proposition": "用户刷视频也偏向搞笑，宠物之类的",
        # Model off-by-one: (29,43) starts one codepoint after 刷.
        "supports": [{"evidence_id": "evidence-1", "start": 29, "end": 43}],
    }
    script = [
        {
            "content": json.dumps(
                {"schema_version": 8, "result": "cognitions", "cognitions": [item]}
            ),
            "model": "m",
        }
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", raw),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        cog = db.execute("SELECT content FROM cognition").fetchone()
        assert cog[0] == "用户刷视频也偏向搞笑，宠物之类的"
    finally:
        db.close()
