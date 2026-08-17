"""V2 formal batch adapter: multi-cognition batch, typed corrects, confirmed.

Owner decision (§4.12, 2026-08-15): up to 3 supported Owner cognitions per
committed boundary, exactly one physical memory_world request, single fenced
atomic transaction, any failure settles the whole batch as no_change with
zero World writes.
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
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)

_RAW_ICED = "我平时更喜欢冰美式。"
_RAW_SLEEP = "我长期习惯是每天早睡。"
_RAW_COFFEE = "其实我更喜欢喝咖啡。"
_RAW_CONFIRM = "对，就是G6"
_CAR_CLAIM = "那你开的是小鹏G6吧？"


def _route(script: list[Any]) -> Callable[..., dict[str, object]]:
    def route(messages: list[dict[str, str]], session_id: str) -> dict[str, object]:
        del messages, session_id
        return cast(dict[str, object], script.pop(0))

    return route


def _set_evidence(
    db_path: Path, evidence_id: str, raw: str, context: str | None = None
) -> None:
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute(
            "UPDATE evidence SET raw_content = ?, preceding_ai_context = ? "
            "WHERE id = ?",
            (raw, context, evidence_id),
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


def _form(
    proposition: str,
    *spans: tuple[int, int],
    kind: str = "preference",
    formed_by: str = "stated",
    evidence_id: str = "evidence-1",
    assistant_claim: str | None = None,
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
    if assistant_claim is not None:
        item["assistant_claim"] = assistant_claim
    return item


def _correct(
    proposition: str,
    target_id: str,
    *spans: tuple[int, int],
    kind: str = "preference",
    evidence_id: str = "evidence-2",
) -> dict[str, object]:
    return {
        "action": "correct",
        "target": "owner_self",
        "statement_kind": kind,
        "formed_by": "stated",
        "proposition": proposition,
        "corrects_cognition_id": target_id,
        "supports": [
            {"evidence_id": evidence_id, "start": s, "end": e}
            for s, e in spans
        ],
    }


def _batch(*items: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 2, "result": "cognitions", "cognitions": list(items)}


def _model(content: dict[str, object]) -> dict[str, object]:
    return {"content": json.dumps(content), "model": "deepseek-v4-flash"}


def _no_change_model() -> dict[str, object]:
    return _model({"schema_version": 2, "result": "no_change"})


def _cognition_id(proposition: str, kind: str = "preference") -> str:
    return BatchItem(
        action="form",
        proposition=proposition,
        statement_kind=kind,
        formed_by="stated",
        supports=(),
    ).cognition_id("owner")


def _cognitions(db_path: Path) -> dict[str, tuple[Any, ...]]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            "SELECT id, content, content_type, formed_by, confidence, "
            "cred_status, invalid_at, archived_at FROM cognition"
        ).fetchall()
        return {str(r[0]): tuple(r[1:]) for r in rows}
    finally:
        db.close()


# ── batch envelope ─────────────────────────────────────────────────────────

def test_two_forms_apply_in_one_transaction_with_single_bump(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _form("用户平时更喜欢冰美式", (0, 9), evidence_id="evidence-1"),
                _form(
                    "用户长期习惯是每天早睡",
                    (0, 10),
                    evidence_id="evidence-2",
                ),
            )
        )
    ]

    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ICED)
        _set_evidence(path, "evidence-2", _RAW_SLEEP)

    _run(db_path, clock, script, ("evidence-1", "evidence-2"), setup)
    assert len(script) == 0  # exactly one physical model request
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["schema_version"] == 2
    assert outcome["world_revision"] == 1
    assert len(outcome["cognitions"]) == 2
    assert [c["action"] for c in outcome["cognitions"]] == ["form", "form"]
    cognitions = _cognitions(db_path)
    assert len(cognitions) == 2
    assert all(r[2] == "stated" and r[3] == 600 for r in cognitions.values())
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT revision FROM memory_state WHERE singleton = 1"
        ).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 2
    finally:
        db.close()


def test_batch_cap_is_five(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _form("用户平时更喜欢冰美式", (0, 9)),
                _form("用户长期习惯是每天早睡", (0, 9)),
                _form("用户更喜欢喝咖啡", (2, 9)),
                _form("用户更喜欢喝茶", (2, 9)),
                _form("用户更喜欢喝奶茶", (2, 9)),
                _form("用户更喜欢喝果汁", (2, 9)),
            )
        )
    ]
    _run(db_path, clock, script, ("evidence-1",))
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == "too_many_cognitions"
    assert _cognitions(db_path) == {}


def test_empty_batch_is_invalid(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [_model({"schema_version": 2, "result": "cognitions", "cognitions": []})]
    _run(db_path, clock, script, ("evidence-1",))
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "invalid_cognition_batch"
    )


def test_duplicate_cognitions_in_batch_are_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _form("用户平时更喜欢冰美式", (0, 9), evidence_id="evidence-1"),
                _form("用户平时更喜欢冰美式", (0, 9), evidence_id="evidence-2"),
            )
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-1", "evidence-2"),
        lambda path: (
            _set_evidence(path, "evidence-1", _RAW_ICED),
            _set_evidence(path, "evidence-2", _RAW_ICED),
        ),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "duplicate_cognition_in_batch"
    )


def test_model_no_change_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(db_path, clock, [_no_change_model()], ("evidence-1",))
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == "model_no_change"
    assert _cognitions(db_path) == {}


# ── typed corrects ─────────────────────────────────────────────────────────

def test_correction_supersedes_with_lineage(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path,
        clock,
        [_model(_batch(_form("用户平时更喜欢冰美式", (0, 9))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    prior_id = _cognition_id("用户平时更喜欢冰美式")
    replacement_id = _cognition_id("用户更喜欢喝咖啡")

    script = [
        _model(
            _batch(
                _correct(
                    "用户更喜欢喝咖啡", prior_id, (2, 9), evidence_id="evidence-2"
                )
            )
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_COFFEE),
        job_id="job-2",
    )
    assert len(script) == 0
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 2
    corrected = outcome["cognitions"][0]
    assert corrected["action"] == "correct"
    assert corrected["prior_cognition_id"] == prior_id
    assert corrected["replacement_cognition_id"] == replacement_id

    db = sqlite3.connect(db_path)
    try:
        prior = db.execute(
            "SELECT content, invalid_at FROM cognition WHERE id = ?", (prior_id,)
        ).fetchone()
        assert prior is not None
        assert prior[0] == "用户平时更喜欢冰美式"  # history preserved verbatim
        assert prior[1] is not None  # superseded, not deleted
        current = db.execute(
            "SELECT content, formed_by, confidence, invalid_at FROM cognition "
            "WHERE id = ?",
            (replacement_id,),
        ).fetchone()
        assert current is not None
        assert current[0] == "用户更喜欢喝咖啡"
        assert current[1] == "stated"
        assert current[2] == 600
        assert current[3] is None
        transition = db.execute(
            "SELECT prior_cognition_id, replacement_cognition_id, reason, revision "
            "FROM cognition_transitions"
        ).fetchall()
        assert transition == [(prior_id, replacement_id, "corrects", 2)]
        # Snapshot is current-only: the superseded cognition is absent.
        snapshot = json.loads(
            str(
                db.execute(
                    "SELECT snapshot_json FROM memory_state WHERE singleton = 1"
                ).fetchone()[0]
            )
        )
        assert [c["id"] for c in snapshot["cognitions"]] == [replacement_id]
        # Typed correction ledger row.
        ledger = db.execute(
            "SELECT content FROM evidence_ledger WHERE content LIKE '%corrects%'"
        ).fetchone()
        assert ledger is not None
        assert json.loads(str(ledger[0]))["prior_cognition_id"] == prior_id
    finally:
        db.close()


def test_correction_unknown_target_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(_correct("用户更喜欢喝咖啡", "cognition-ghost", (2, 9)))
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_COFFEE),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "correction_target_unknown"
    )
    assert _cognitions(db_path) == {}


def test_correction_of_superseded_target_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path,
        clock,
        [_model(_batch(_form("用户平时更喜欢冰美式", (0, 9))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    prior_id = _cognition_id("用户平时更喜欢冰美式")
    coffee_id = _cognition_id("用户更喜欢喝咖啡")
    tea_id = _cognition_id("用户更喜欢喝茶")
    _run(
        db_path,
        clock,
        [_model(_batch(_correct("用户更喜欢喝咖啡", prior_id, (2, 9))))],
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_COFFEE),
        job_id="job-2",
    )
    # Correcting the already-superseded prior again: zero write.
    _run(
        db_path,
        clock,
        [_model(_batch(_correct("用户更喜欢喝茶", prior_id, (2, 9))))],
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", "其实我更喜欢喝茶。"),
        job_id="job-3",
    )
    row = _job(db_path, job_id="job-3")
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "correction_target_not_current"
    )
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_transitions"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM cognition WHERE id = ?", (tea_id,)
        ).fetchone()[0] == 0
        current = db.execute(
            "SELECT invalid_at FROM cognition WHERE id = ?", (coffee_id,)
        ).fetchone()
        assert current is not None and current[0] is None
    finally:
        db.close()


def test_correction_chain_replaces_the_successor(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path,
        clock,
        [_model(_batch(_form("用户平时更喜欢冰美式", (0, 9))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    prior_id = _cognition_id("用户平时更喜欢冰美式")
    coffee_id = _cognition_id("用户更喜欢喝咖啡")
    tea_id = _cognition_id("用户更喜欢喝茶")
    _run(
        db_path,
        clock,
        [_model(_batch(_correct("用户更喜欢喝咖啡", prior_id, (2, 9))))],
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_COFFEE),
        job_id="job-2",
    )
    _run(
        db_path,
        clock,
        [_model(_batch(_correct("用户更喜欢喝茶", coffee_id, (2, 9))))],
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", "其实我更喜欢喝茶。"),
        job_id="job-3",
    )
    row = _job(db_path, job_id="job-3")
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 3
    db = sqlite3.connect(db_path)
    try:
        transitions = db.execute(
            "SELECT prior_cognition_id, replacement_cognition_id FROM "
            "cognition_transitions ORDER BY revision"
        ).fetchall()
        assert transitions == [
            (prior_id, coffee_id),
            (coffee_id, tea_id),
        ]
        rows = _cognitions(db_path)
        assert rows[prior_id][5] is not None
        assert rows[coffee_id][5] is not None
        assert rows[tea_id][5] is None
    finally:
        db.close()


def test_correction_identical_proposition_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path,
        clock,
        [_model(_batch(_form("用户平时更喜欢冰美式", (0, 9))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    prior_id = _cognition_id("用户平时更喜欢冰美式")
    _run(
        db_path,
        clock,
        [
            _model(
                _batch(
                    _correct(
                        "用户平时更喜欢冰美式",
                        prior_id,
                        (0, 9),
                        evidence_id="evidence-1",
                    )
                )
            )
        ],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "correction_target_is_itself"
    )
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_transitions"
        ).fetchone()[0] == 0
    finally:
        db.close()


def test_correction_merge_collision_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path,
        clock,
        [_model(_batch(_form("用户平时更喜欢冰美式", (0, 9))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    _run(
        db_path,
        clock,
        [_model(_batch(_form("用户更喜欢喝咖啡", (2, 9), evidence_id="evidence-2")))],
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_COFFEE),
        job_id="job-2",
    )
    prior_id = _cognition_id("用户平时更喜欢冰美式")
    # Replacing 冰美式 with a proposition that is ALREADY current under a
    # different cognition id: the merge was never stated → zero write.
    _run(
        db_path,
        clock,
        [_model(_batch(_correct("用户更喜欢喝咖啡", prior_id, (2, 9))))],
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_COFFEE),
        job_id="job-3",
    )
    row = _job(db_path, job_id="job-3")
    assert row["state"] == "no_change"
    assert row["terminal_state"] == "clarification_required"
    world = json.loads(str(row["world_result_json"]))
    assert world["reason"] == "correction_merge_ambiguous"
    assert "澄清" in str(world.get("display"))
    assert row["terminal_detail"] == world["display"]
    rows = _cognitions(db_path)
    assert rows[prior_id][5] is None  # still current, untouched


def test_two_items_correcting_the_same_target_are_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path,
        clock,
        [_model(_batch(_form("用户平时更喜欢冰美式", (0, 9))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    prior_id = _cognition_id("用户平时更喜欢冰美式")
    script = [
        _model(
            _batch(
                _correct("用户更喜欢喝咖啡", prior_id, (2, 9), evidence_id="evidence-2"),
                _correct("用户更喜欢喝茶", prior_id, (2, 9), evidence_id="evidence-3"),
            )
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-2", "evidence-3"),
        lambda path: (
            _set_evidence(path, "evidence-2", _RAW_COFFEE),
            _set_evidence(path, "evidence-3", "其实我更喜欢喝茶。"),
        ),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "ambiguous_correction_target"
    )
    assert _cognitions(db_path)[prior_id][5] is None


# ── confirmed ──────────────────────────────────────────────────────────────

def test_confirmed_forms_at_280_with_context_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _form(
                    "用户开的是小鹏G6",
                    (0, 6),
                    kind="attribute",
                    formed_by="confirmed",
                    assistant_claim=_CAR_CLAIM,
                )
            )
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_CONFIRM, _CAR_CLAIM),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    item = outcome["cognitions"][0]
    assert item["formed_by"] == "confirmed"
    assert item["confidence"] == 280
    assert item["cred_status"] == "candidate"
    rows = _cognitions(db_path)
    assert len(rows) == 1
    only = next(iter(rows.values()))
    assert only[0] == "用户开的是小鹏G6"
    assert only[2] == "confirmed"
    assert only[3] == 280
    # Assistant text never becomes Evidence: the support link points at the
    # USER's confirmation row only.
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_evidence WHERE relation='support'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM evidence WHERE raw_content = ?", (_CAR_CLAIM,)
        ).fetchone()[0] == 0
    finally:
        db.close()


def test_confirmed_claim_not_in_context_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _form(
                    "用户开的是比亚迪",
                    (0, 6),
                    kind="attribute",
                    formed_by="confirmed",
                    assistant_claim="那你开的是比亚迪吧？",
                )
            )
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_CONFIRM, _CAR_CLAIM),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "assistant_claim_not_in_context"
    )
    assert _cognitions(db_path) == {}


def test_confirmed_negated_span_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "不对，不是G6"
    script = [
        _model(
            _batch(
                _form(
                    "用户开的是小鹏G6",
                    (0, 7),
                    kind="attribute",
                    formed_by="confirmed",
                    assistant_claim=_CAR_CLAIM,
                )
            )
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", raw, _CAR_CLAIM),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "confirmed_span_not_confirmation"
    )
    assert _cognitions(db_path) == {}


def test_confirmed_proposition_must_equal_normalized_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _form(
                    "用户开的是比亚迪",
                    (0, 6),
                    kind="attribute",
                    formed_by="confirmed",
                    assistant_claim=_CAR_CLAIM,
                )
            )
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_CONFIRM, _CAR_CLAIM),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"] == "proposition_mismatch"
    )
    assert _cognitions(db_path) == {}


def test_stated_item_with_assistant_claim_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _form("用户开的是小鹏G6", (0, 6), kind="attribute")
    item["assistant_claim"] = _CAR_CLAIM
    script = [_model(_batch(item))]
    _run(
        db_path,
        clock,
        script,
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_CONFIRM, _CAR_CLAIM),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unexpected_assistant_claim"
    )


def test_stated_proposition_must_be_span_anchored(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _form("用户最喜欢冰美式", (0, 9))  # 改写：不是任何切片的归一化
            )
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "proposition_not_anchored"
    )
    assert _cognitions(db_path) == {}


def test_form_with_empty_optional_fields_is_accepted(tmp_path: Path) -> None:
    """LLM JSON hygiene: ``""`` for optional fields means absent (live case:
    a form carrying ``corrects_cognition_id: ""`` + ``assistant_claim: ""``
    must apply, not settle no_change)."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _form("用户平时更喜欢冰美式", (0, 9))
    item["corrects_cognition_id"] = ""
    item["assistant_claim"] = ""
    script = [_model(_batch(item))]
    _run(
        db_path,
        clock,
        script,
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["cognitions"][0]["action"] == "form"
    assert len(_cognitions(db_path)) == 1


def test_correct_with_empty_target_is_invalid(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _correct("用户更喜欢喝咖啡", "", (2, 9), evidence_id="evidence-2")
    script = [_model(_batch(item))]
    _run(
        db_path,
        clock,
        script,
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", _RAW_COFFEE),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "invalid_correction_target"
    )
    assert _cognitions(db_path) == {}


def test_confirmed_with_empty_claim_is_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _form(
        "用户开的是小鹏G6",
        (0, 6),
        kind="attribute",
        formed_by="confirmed",
        assistant_claim="",
    )
    script = [_model(_batch(item))]
    _run(
        db_path,
        clock,
        script,
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_CONFIRM, _CAR_CLAIM),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "missing_assistant_claim"
    )
    assert _cognitions(db_path) == {}


# ── mixed batch, replay, carrier interop ───────────────────────────────────

def test_mixed_form_and_correct_batch_applies_atomically(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path,
        clock,
        [_model(_batch(_form("用户平时更喜欢冰美式", (0, 9))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    prior_id = _cognition_id("用户平时更喜欢冰美式")
    coffee_id = _cognition_id("用户更喜欢喝咖啡")
    sleep_id = _cognition_id("用户长期习惯是每天早睡")
    script = [
        _model(
            _batch(
                _form(
                    "用户长期习惯是每天早睡", (0, 10), evidence_id="evidence-2"
                ),
                _correct(
                    "用户更喜欢喝咖啡", prior_id, (2, 9), evidence_id="evidence-3"
                ),
            )
        )
    ]
    _run(
        db_path,
        clock,
        script,
        ("evidence-2", "evidence-3"),
        lambda path: (
            _set_evidence(path, "evidence-2", _RAW_SLEEP),
            _set_evidence(path, "evidence-3", _RAW_COFFEE),
        ),
        job_id="job-2",
    )
    assert len(script) == 0
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 2  # one bump for the whole batch
    assert [c["action"] for c in outcome["cognitions"]] == ["form", "correct"]
    rows = _cognitions(db_path)
    assert rows[prior_id][5] is not None
    assert rows[coffee_id][5] is None
    assert rows[sleep_id][5] is None


def test_v2_batch_exact_replay_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path,
        clock,
        [_model(_batch(_form("用户平时更喜欢冰美式", (0, 9))))],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ICED),
    )
    prior_id = _cognition_id("用户平时更喜欢冰美式")
    coffee_id = _cognition_id("用户更喜欢喝咖啡")
    batch_payload = _batch(
        _form("用户长期习惯是每天早睡", (0, 10), evidence_id="evidence-2"),
        _correct("用户更喜欢喝咖啡", prior_id, (2, 9), evidence_id="evidence-3"),
    )
    _run(
        db_path,
        clock,
        [_model(batch_payload)],
        ("evidence-2", "evidence-3"),
        lambda path: (
            _set_evidence(path, "evidence-2", _RAW_SLEEP),
            _set_evidence(path, "evidence-3", _RAW_COFFEE),
        ),
        job_id="job-2",
    )
    assert (
        json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))[
            "world_revision"
        ]
        == 2
    )
    # Exact replay of the same batch with fresh identical Evidence: the form
    # restates (no new link — different evidence ids means... this job uses
    # evidence-4/evidence-5, so the form DOES attach a new support link).
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
        "allow_cloud_read, allow_inference) VALUES "
        "('evidence-4', 'owner', 'spoken', 'hermes:test', "
        "'2026-08-14T12:00:00.000Z', '2026-08-14T12:00:00.000Z', ?, ?, 1, 1, 1)",
        (_RAW_SLEEP, _RAW_SLEEP),
    )
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
        "allow_cloud_read, allow_inference) VALUES "
        "('evidence-5', 'owner', 'spoken', 'hermes:test', "
        "'2026-08-14T12:00:00.000Z', '2026-08-14T12:00:00.000Z', ?, ?, 1, 1, 1)",
        (_RAW_COFFEE, _RAW_COFFEE),
    )
    for evidence_id, raw in (("evidence-4", _RAW_SLEEP), ("evidence-5", _RAW_COFFEE)):
        db.execute(
            "INSERT OR IGNORE INTO boundary_evidence_content (evidence_id, "
            "raw_content_hash) VALUES (?, ?)",
            (evidence_id, hashlib.sha256(raw.encode("utf-8")).hexdigest()),
        )
    db.close()
    replay = _batch(
        _form("用户长期习惯是每天早睡", (0, 10), evidence_id="evidence-4"),
        _correct("用户更喜欢喝咖啡", prior_id, (2, 9), evidence_id="evidence-5"),
    )
    _run(
        db_path,
        clock,
        [_model(replay)],
        ("evidence-4", "evidence-5"),
        job_id="job-3",
    )
    row = _job(db_path, job_id="job-3")
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 3  # only the new support link bumped
    db = sqlite3.connect(db_path)
    try:
        # The correction replayed deterministically: still exactly one
        # transition, and the replacement row was not duplicated.
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_transitions"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM cognition WHERE id = ?", (coffee_id,)
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(DISTINCT evidence_id) FROM cognition_evidence "
            "WHERE cognition_id = ?",
            (_cognition_id("用户长期习惯是每天早睡"),),
        ).fetchone()[0] == 2
    finally:
        db.close()


def test_confirmed_restated_as_stated_recomputes_weakest_carrier(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _run(
        db_path,
        clock,
        [
            _model(
                _batch(
                    _form(
                        "用户开的是小鹏G6",
                        (0, 6),
                        kind="attribute",
                        formed_by="confirmed",
                        assistant_claim=_CAR_CLAIM,
                    )
                )
            )
        ],
        ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_CONFIRM, _CAR_CLAIM),
    )
    car_id = _cognition_id("用户开的是小鹏G6", kind="attribute")
    # A later genuine user statement of the same proposition: same-ID support
    # path, chain carrier stays the weakest (confirmed → base 280).
    _run(
        db_path,
        clock,
        [
            _model(
                _batch(
                    _form(
                        "用户开的是小鹏G6",
                        (0, 8),
                        kind="attribute",
                        evidence_id="evidence-2",
                    )
                )
            )
        ],
        ("evidence-2",),
        lambda path: _set_evidence(path, "evidence-2", "我开的是小鹏G6"),
        job_id="job-2",
    )
    row = _job(db_path, job_id="job-2")
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["cognitions"][0]["confidence"] == 320  # 280 + 40
    assert outcome["cognitions"][0]["cred_status"] == "low"
    rows = _cognitions(db_path)
    assert len(rows) == 1
    assert rows[car_id][2] == "confirmed"
    assert rows[car_id][3] == 320


# ── deterministic Recall reads only current ────────────────────────────────

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


def _settle_pending_job(
    db_path: Path, payload: dict[str, Any]
) -> tuple[str, str]:
    db = sqlite3.connect(db_path)
    row = db.execute(
        "SELECT job_id, evidence_ids_json FROM memory_world_job "
        "WHERE state = 'pending' ORDER BY created_at LIMIT 1"
    ).fetchone()
    assert row is not None, "no pending job to settle"
    job_id = str(row[0])
    evidence_id = json.loads(str(row[1]))[0]
    db.close()
    payload["cognitions"][0]["supports"][0]["evidence_id"] = evidence_id
    worker = WorldJobWorker(
        db_path,
        processor=HermesBatchAdapterProcessor(
            str(db_path), _route([{"content": json.dumps(payload), "model": "m"}])
        ),
        policy=_policy(),
    )
    assert worker.run_until_quiescent() == 1
    db = sqlite3.connect(db_path)
    settled = db.execute(
        "SELECT state, world_result_json FROM memory_world_job WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    db.close()
    assert settled is not None and settled[0] == "applied", settled
    return job_id, evidence_id


def test_recall_reads_only_current_after_correction(tmp_path: Path) -> None:
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
    runtime.shutdown()  # settle deterministically below instead of async
    receipt = runtime.ingest_durable_boundary(_boundary_envelope(_RAW_ICED))
    assert receipt["job_state"] == "pending"
    form_payload = _batch(_form("用户平时更喜欢冰美式", (0, 9)))
    _settle_pending_job(db_path, form_payload)

    db = sqlite3.connect(db_path)
    prior_id = str(db.execute("SELECT id FROM cognition").fetchone()[0])
    db.close()
    receipt2 = runtime.ingest_durable_boundary(_boundary_envelope(_RAW_COFFEE))
    assert receipt2["job_state"] == "pending"
    correct_payload = _batch(_correct("用户更喜欢喝咖啡", prior_id, (2, 9)))
    _settle_pending_job(db_path, correct_payload)

    # Current-only Recall: the corrected value hits, the superseded one leaks
    # nothing.
    text = runtime.prefetch("喜欢喝什么", session_id="sess")
    assert "用户更喜欢喝咖啡" in text
    assert "冰美式" not in text
    assert runtime.last_recall_count == 1
    assert runtime.prefetch("冰美式", session_id="sess") == ""
    assert runtime.last_recall_count == 0
