"""V8 formal batch adapter: third-party perspective-holder slice (V5 window).

Owner decisions (2026-08-16): the perspective dimension lands on cognitive
objects only (attribute/preference); all such content stays in the Owner's
World (perspective is attribution, not ownership); perspective enters the
deterministic identity (same claim under different holders coexists);
stable attributes/preferences form, evaluations never do (V3-B line holds).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor,
    cognition_id_for_holder,
    entity_id_for,
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)

_RAW_HELD = "小王说小李是00后"
_RAW_OWN = "小李是00后"


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


def _v8_item(
    kind: str,
    proposition: str,
    *spans: tuple[int, int],
    action: str = "form",
    entity: dict[str, object] | None = None,
    perspective_holder: dict[str, object] | None = None,
    source_entity: dict[str, object] | None = None,
    target_entity: dict[str, object] | None = None,
    relation_type: str | None = None,
    corrects_cognition_id: str | None = None,
    contradicts_cognition_id: str | None = None,
    retract: bool | None = None,
    evidence_id: str = "evidence-1",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "action": action,
        "target": "owner_self",
        "statement_kind": kind,
        "formed_by": "stated",
        "proposition": proposition,
        "supports": [
            {"evidence_id": evidence_id, "start": s, "end": e}
            for s, e in spans
        ],
    }
    if entity is not None:
        item["entity"] = entity
    if perspective_holder is not None:
        item["perspective_holder"] = perspective_holder
    if source_entity is not None:
        item["source_entity"] = source_entity
    if target_entity is not None:
        item["target_entity"] = target_entity
    if relation_type is not None:
        item["relation_type"] = relation_type
    if corrects_cognition_id is not None:
        item["corrects_cognition_id"] = corrects_cognition_id
    if contradicts_cognition_id is not None:
        item["contradicts_cognition_id"] = contradicts_cognition_id
    if retract is not None:
        item["retract"] = retract
    if extra is not None:
        item.update(extra)
    return item


def _batch(*items: Mapping[str, object]) -> dict[str, object]:
    return {"schema_version": 8, "result": "cognitions", "cognitions": list(items)}


def _model(content: dict[str, object]) -> dict[str, object]:
    return {"content": json.dumps(content), "model": "deepseek-v4-flash"}


def test_third_party_held_attribute_forms_with_perspective(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v8_item(
                    "attribute", "小王说小李是00后", (0, 9),
                    entity={"canonical_name": "小李", "kind": "person"},
                    perspective_holder={"canonical_name": "小王", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    item = json.loads(str(row["world_result_json"]))["cognitions"][0]
    holder_id = entity_id_for("owner", "小王")
    target_id = entity_id_for("owner", "小李")
    assert item["target_entity_id"] == target_id
    assert item["perspective_entity_id"] == holder_id
    # The deterministic id carries the perspective dimension.
    assert item["cognition_id"] == cognition_id_for_holder(
        "owner", "attribute", "小王说小李是00后", holder_id
    )
    db = sqlite3.connect(db_path)
    try:
        sidecar = db.execute(
            "SELECT target_entity_id, perspective_entity_id "
            "FROM cognition_target"
        ).fetchone()
        assert sidecar[0] == target_id
        assert sidecar[1] == holder_id
        names = {
            r[0] for r in db.execute("SELECT canonical_name FROM entity").fetchall()
        }
        assert names == {"小王", "小李"}  # both lazily created
    finally:
        db.close()


def test_same_claim_different_holders_coexist(tmp_path: Path) -> None:
    """Owner decision: perspective enters the identity — the owner's own claim
    and a third-party-held claim on the same subject coexist independently."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    held = _v8_item(
        "attribute", "小王说小李是00后", (0, 9),
        entity={"canonical_name": "小李", "kind": "person"},
        perspective_holder={"canonical_name": "小王", "kind": "person"},
    )
    _run(
        db_path, clock, [_model(_batch(held))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
        job_id="job-1",
    )
    own = _v8_item(
        "attribute", "小李是00后", (0, 6),
        entity={"canonical_name": "小李", "kind": "person"},
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(own))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_OWN),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 2
        rows = db.execute(
            "SELECT target_entity_id, perspective_entity_id "
            "FROM cognition_target"
        ).fetchall()
        perspectives = [r[1] for r in rows]
        # One owner_self (perspective NULL), one third-party-held.
        assert None in perspectives
        assert entity_id_for("owner", "小王") in perspectives
    finally:
        db.close()


def test_owner_self_targeted_attribute_has_null_perspective(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v8_item(
                    "attribute", "小李是00后", (0, 6),
                    entity={"canonical_name": "小李", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_OWN),
    )
    item = json.loads(str(_job(db_path)["world_result_json"]))["cognitions"][0]
    assert "perspective_entity_id" not in item
    db = sqlite3.connect(db_path)
    try:
        sidecar = db.execute(
            "SELECT perspective_entity_id FROM cognition_target"
        ).fetchone()
        assert sidecar[0] is None
    finally:
        db.close()


def test_holder_must_not_be_owner(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v8_item(
                    "attribute", "小王说小李是00后", (0, 9),
                    entity={"canonical_name": "小李", "kind": "person"},
                    perspective_holder={"canonical_name": "用户", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "invalid_perspective_holder"
    )


def test_holder_must_not_be_target(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v8_item(
                    "attribute", "小李说小李是00后", (0, 9),
                    entity={"canonical_name": "小李", "kind": "person"},
                    perspective_holder={"canonical_name": "小李", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "小李说小李是00后"),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "perspective_holder_is_target"
    )


def test_holder_name_must_be_in_proposition(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v8_item(
                    "attribute", "小王说小李是00后", (0, 9),
                    entity={"canonical_name": "小李", "kind": "person"},
                    perspective_holder={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "entity_name_not_in_proposition"
    )


def test_perspective_holder_rejected_on_relationship(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v8_item(
        "relationship", "小王是小杨的女朋友", (0, 9),
        source_entity={"canonical_name": "小王", "kind": "person"},
        target_entity={"canonical_name": "小杨", "kind": "person"},
        relation_type="girlfriend",
    )
    item["perspective_holder"] = {"canonical_name": "小李", "kind": "person"}
    script = [_model(_batch(item))]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "小王是小杨的女朋友"),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unexpected_perspective_holder"
    )


def test_perspective_holder_rejected_in_v7_envelope(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v8_item(
        "attribute", "小王说小李是00后", (0, 9),
        entity={"canonical_name": "小李", "kind": "person"},
        perspective_holder={"canonical_name": "小王", "kind": "person"},
    )
    script = [
        {
            "content": json.dumps(
                {"schema_version": 7, "result": "cognitions", "cognitions": [item]}
            ),
            "model": "m",
        }
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unexpected_perspective_holder"
    )


def test_third_party_held_restate_support_merge_and_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v8_item(
        "attribute", "小王说小李是00后", (0, 9),
        entity={"canonical_name": "小李", "kind": "person"},
        perspective_holder={"canonical_name": "小王", "kind": "person"},
    )
    _run(
        db_path, clock, [_model(_batch(item))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
        job_id="job-1",
    )
    first = json.loads(str(_job(db_path, job_id="job-1")["world_result_json"]))
    assert first["world_revision"] == 1
    assert first["cognitions"][0]["confidence"] == 600
    # Exact replay through a fresh job: same-ID support path, no bump.
    _run(db_path, clock, [_model(_batch(item))], ("evidence-1",), job_id="job-2")
    second = json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))
    assert second["world_revision"] == 1
    assert second["cognitions"][0]["cognition_id"] == first["cognitions"][0][
        "cognition_id"
    ]


def test_exact_held_replay_repairs_target_and_perspective_entities(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    held = _v8_item(
        "attribute", "小王说小李是00后", (0, 9),
        entity={"canonical_name": "小李", "kind": "person"},
        perspective_holder={"canonical_name": "小王", "kind": "person"},
    )
    _run(
        db_path, clock, [_model(_batch(held))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
        job_id="job-1",
    )
    target_id = entity_id_for("owner", "小李")
    perspective_id = entity_id_for("owner", "小王")
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute("DELETE FROM entity WHERE id IN (?, ?)", (target_id, perspective_id))
    finally:
        db.close()

    _run(db_path, clock, [_model(_batch(held))], ("evidence-1",), job_id="job-2")

    row = _job(db_path, job_id="job-2")
    outcome = json.loads(str(row["world_result_json"]))
    assert row["state"] == "applied"
    assert outcome["world_revision"] == 2
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM entity WHERE id IN (?, ?)",
            (target_id, perspective_id),
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT COUNT(*) FROM terminal_outcome WHERE job_id = 'job-2'"
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_third_party_held_cognition_retract(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    held = _v8_item(
        "attribute", "小王说小李是00后", (0, 9),
        entity={"canonical_name": "小李", "kind": "person"},
        perspective_holder={"canonical_name": "小王", "kind": "person"},
    )
    _run(
        db_path, clock, [_model(_batch(held))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
        job_id="job-1",
    )
    target = cognition_id_for_holder(
        "owner", "attribute", "小王说小李是00后", entity_id_for("owner", "小王")
    )
    retract = _v8_item(
        "attribute", "那条删掉吧", (0, 5),
        action="correct", retract=True, corrects_cognition_id=target,
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(retract))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", "那条删掉吧"),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        cog = db.execute(
            "SELECT invalid_at FROM cognition WHERE id = ?", (target,)
        ).fetchone()
        assert cog[0] is not None
        assert db.execute(
            "SELECT COUNT(*) FROM retraction WHERE prior_cognition_id = ?",
            (target,),
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_third_party_held_cognition_correct(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    held = _v8_item(
        "attribute", "小王说小李是00后", (0, 9),
        entity={"canonical_name": "小李", "kind": "person"},
        perspective_holder={"canonical_name": "小王", "kind": "person"},
    )
    _run(
        db_path, clock, [_model(_batch(held))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
        job_id="job-1",
    )
    prior = cognition_id_for_holder(
        "owner", "attribute", "小王说小李是00后", entity_id_for("owner", "小王")
    )
    correct = _v8_item(
        "attribute", "小王说小李是95后", (0, 9),
        action="correct", corrects_cognition_id=prior,
        entity={"canonical_name": "小李", "kind": "person"},
        perspective_holder={"canonical_name": "小王", "kind": "person"},
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(correct))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", "小王说小李是95后"),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    item = json.loads(str(row["world_result_json"]))["cognitions"][0]
    replacement = cognition_id_for_holder(
        "owner", "attribute", "小王说小李是95后", entity_id_for("owner", "小王")
    )
    assert item["prior_cognition_id"] == prior
    assert item["replacement_cognition_id"] == replacement
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT invalid_at IS NOT NULL FROM cognition WHERE id = ?",
            (prior,),
        ).fetchone()[0] == 1
        sidecar = db.execute(
            "SELECT perspective_entity_id FROM cognition_target "
            "WHERE cognition_id = ?",
            (replacement,),
        ).fetchone()
        assert sidecar[0] == entity_id_for("owner", "小王")
    finally:
        db.close()


def test_third_party_held_cognition_contradict(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    held = _v8_item(
        "attribute", "小王说小李是00后", (0, 9),
        entity={"canonical_name": "小李", "kind": "person"},
        perspective_holder={"canonical_name": "小王", "kind": "person"},
    )
    _run(
        db_path, clock, [_model(_batch(held))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
        job_id="job-1",
    )
    target = cognition_id_for_holder(
        "owner", "attribute", "小王说小李是00后", entity_id_for("owner", "小王")
    )
    contradict = _v8_item(
        "attribute", "小李不是00后", (0, 7),
        action="contradict", contradicts_cognition_id=target,
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(contradict))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", "小李不是00后"),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    item = json.loads(str(row["world_result_json"]))["cognitions"][0]
    assert item["confidence"] == 0  # downgrade only, never invalidated
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT invalid_at IS NULL FROM cognition WHERE id = ?",
            (target,),
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_exact_contradict_replay_repairs_missing_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    held = _v8_item(
        "attribute", "小王说小李是00后", (0, 9),
        entity={"canonical_name": "小李", "kind": "person"},
        perspective_holder={"canonical_name": "小王", "kind": "person"},
    )
    _run(
        db_path, clock, [_model(_batch(held))], ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_HELD),
        job_id="job-1",
    )
    target = cognition_id_for_holder(
        "owner", "attribute", "小王说小李是00后", entity_id_for("owner", "小王")
    )
    contradict = _v8_item(
        "attribute", "小李不是00后", (0, 7),
        action="contradict", contradicts_cognition_id=target,
        evidence_id="evidence-2",
    )
    _run(
        db_path, clock, [_model(_batch(contradict))], ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", "小李不是00后"),
        job_id="job-2",
    )
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        ledger_id = db.execute(
            "SELECT id FROM evidence_ledger "
            "WHERE content LIKE '%\"relation\":\"contradict\"%'"
        ).fetchone()[0]
        db.execute("DELETE FROM evidence_ledger WHERE id = ?", (ledger_id,))
    finally:
        db.close()

    _run(db_path, clock, [_model(_batch(contradict))], ("evidence-2",), job_id="job-3")

    row = _job(db_path, job_id="job-3")
    outcome = json.loads(str(row["world_result_json"]))
    assert row["state"] == "applied"
    assert outcome["world_revision"] == 3
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM evidence_ledger "
            "WHERE content LIKE '%\"relation\":\"contradict\"%'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM terminal_outcome WHERE job_id = 'job-3'"
        ).fetchone()[0] == 1
    finally:
        db.close()
