"""V1 formal batch adapter: at-most-one-call interpretation + fenced Apply."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, cast

from memoweft.integrations.hermes.batch_adapter import (
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


def _route(
    script: list[Any], calls: list[int] | None = None
) -> Callable[..., dict[str, object]]:
    def route(messages: list[dict[str, str]], session_id: str) -> dict[str, object]:
        if calls is not None:
            calls.append(1)
        return cast(dict[str, object], script.pop(0))

    return route


def _one_cognition(
    proposition: str,
    *spans: tuple[int, int],
    kind: str = "preference",
    evidence_id: str = "evidence-1",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "result": "one_cognition",
        "cognition": {
            "target": "owner_self",
            "statement_kind": kind,
            "proposition": proposition,
            "supports": [
                {"evidence_id": evidence_id, "start": s, "end": e}
                for s, e in spans
            ],
        },
    }


def _no_change() -> dict[str, object]:
    return {"schema_version": 1, "result": "no_change"}


def _raw(job_id: str = "job-1", evidence_id: str = "evidence-1") -> str:
    del job_id
    if evidence_id == "evidence-1":
        return "用户平时更喜欢冰美式。"
    return "用户长期习惯是每天早睡。"


def _set_raw(db_path: Path, evidence_id: str, raw: str) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE evidence SET raw_content = ? WHERE id = ?", (raw, evidence_id))
        db.execute("UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = ?",
                   (__import__("hashlib").sha256(raw.encode("utf-8")).hexdigest(), evidence_id))


def _run(db_path: Path, clock: MutableClock, script: list[Any], evidence_ids: tuple[str, ...] = ("evidence-1",)) -> int:
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=evidence_ids)
    _set_raw(db_path, "evidence-1", _raw())
    if evidence_ids != ("evidence-1",):
        db = sqlite3.connect(db_path, isolation_level=None)
        db.execute(
            "UPDATE evidence SET raw_content = ? WHERE id = ?",
            ("用户长期习惯是每天早睡。", "evidence-2"),
        )
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? "
            "WHERE evidence_id = 'evidence-2'",
            (
                __import__("hashlib")
                .sha256("用户长期习惯是每天早睡。".encode("utf-8"))
                .hexdigest(),
            ),
        )
        db.close()
    processor = HermesBatchAdapterProcessor(str(db_path), _route(script), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    return worker.run_until_quiescent()


def test_model_no_change_persists_model_and_zero_world_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script: list[Any] = [
        {"content": json.dumps(_no_change()), "model": "deepseek-v4-flash"}
    ]
    processed = _run(db_path, clock, script)
    assert processed == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == "model_no_change"
    assert row["model_result_json"] is not None
    assert row["model_name"] == "deepseek-v4-flash"
    assert row["claim_token"] is None
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_state").fetchone()[0] == 0
    finally:
        db.close()


def test_model_clarification_required_is_zero_write_with_terminal_state(tmp_path: Path) -> None:
    """AUTHORITY §3: 模型提出身份/含义无法唯一解析 → clarification_required
    终态、零 World 写入、question 可观察。"""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script: list[Any] = [
        {
            "content": json.dumps(
                {
                    "schema_version": 8,
                    "result": "clarification_required",
                    "question": "你说的\"他\"是指小王还是小杨？",
                }
            ),
            "model": "deepseek-v4-flash",
        }
    ]
    processed = _run(db_path, clock, script)
    assert processed == 1
    row = _job(db_path)
    assert row["state"] == "no_change"  # 运输机语义：零写入已结算
    assert row["terminal_state"] == "clarification_required"
    assert row["terminal_detail"] == "你说的\"他\"是指小王还是小杨？"
    world = json.loads(str(row["world_result_json"]))
    assert world["reason"] == "model_clarification_required"
    assert world["display"] == "你说的\"他\"是指小王还是小杨？"
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_state").fetchone()[0] == 0
    finally:
        db.close()


def test_model_out_of_scope_is_zero_write_with_terminal_state(tmp_path: Path) -> None:
    """AUTHORITY §3: 理解但超出正式合同 → out_of_scope 终态、零 World 写入。"""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script: list[Any] = [
        {
            "content": json.dumps(
                {
                    "schema_version": 8,
                    "result": "out_of_scope",
                    "note": "这是临时日程，不属于稳定属性/偏好/命名/关系/事件",
                }
            ),
            "model": "deepseek-v4-flash",
        }
    ]
    processed = _run(db_path, clock, script)
    assert processed == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert row["terminal_state"] == "out_of_scope"
    assert "临时日程" in str(row["terminal_detail"])
    world = json.loads(str(row["world_result_json"]))
    assert world["reason"] == "model_out_of_scope"
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    finally:
        db.close()


def test_model_non_no_change_terminal_requires_nonempty_bounded_display(
    tmp_path: Path,
) -> None:
    for label, result, field, value in (
        ("clarification-missing", "clarification_required", "question", None),
        ("clarification-empty", "clarification_required", "question", "  "),
        ("scope-too-long", "out_of_scope", "note", "x" * 501),
    ):
        db_path = tmp_path / f"{label}.sqlite3"
        clock = MutableClock()
        model_result: dict[str, object] = {
            "schema_version": 8,
            "result": result,
        }
        if value is not None:
            model_result[field] = value

        assert _run(
            db_path,
            clock,
            [{"content": json.dumps(model_result), "model": "test-model"}],
        ) == 1
        row = _job(db_path)
        assert row["state"] == "no_change"
        assert row["terminal_state"] == "no_change"
        assert row["terminal_detail"] is None
        assert json.loads(str(row["world_result_json"]))["reason"] == (
            "invalid_model_result"
        )


def test_model_bad_question_fails_closed_as_invalid_model_result(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script: list[Any] = [
        {
            "content": json.dumps(
                {"schema_version": 8, "result": "clarification_required", "question": 123}
            ),
            "model": "deepseek-v4-flash",
        }
    ]
    processed = _run(db_path, clock, script)
    assert processed == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert row["terminal_state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == "invalid_model_result"


def test_one_cognition_applies_atomically_with_ledger_and_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "用户平时更喜欢冰美式。"
    script: list[Any] = [
        {
            "content": json.dumps(_one_cognition("用户平时更喜欢冰美式。", (0, len(raw)))),
            "model": "deepseek-v4-flash",
        }
    ]
    processed = _run(db_path, clock, script)
    assert processed == 1
    row = _job(db_path)
    assert row["state"] == "applied"
    assert row["terminal_state"] == "applied"
    assert row["terminal_detail"] is None
    assert row["completed_at"] is not None
    assert row["world_result_json"] is not None
    assert row["result_hash"] is not None
    assert row["result_hash"] == __import__("hashlib").sha256(
        str(row["world_result_json"]).encode("utf-8")
    ).hexdigest()
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["state"] == "applied"
    assert outcome["world_revision"] == 1
    assert outcome["statement_kind"] == "preference"
    assert outcome["confidence"] == 600
    assert outcome["cred_status"] == "limited"
    assert outcome["evidence_count"] == 1
    db = sqlite3.connect(db_path)
    try:
        cog = db.execute("SELECT * FROM cognition").fetchone()
        assert cog is not None
        assert cog[2] == "用户平时更喜欢冰美式。"
        assert cog[3] == "preference"
        assert cog[4] == "stated"
        assert cog[5] == 600
        assert cog[6] == "limited"
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_evidence WHERE relation='support'"
        ).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 1
        rev = db.execute(
            "SELECT revision, snapshot_hash FROM memory_state WHERE singleton = 1"
        ).fetchone()
        assert rev is not None and rev[0] == 1
        assert rev[1] is not None
    finally:
        db.close()


def test_multiple_evidence_is_still_one_route_call(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw1 = "用户平时更喜欢冰美式。"
    raw2 = "用户长期习惯是每天早睡。"
    script: list[Any] = [
        {
            "content": json.dumps(
                {
                    "schema_version": 1,
                    "result": "one_cognition",
                    "cognition": {
                        "target": "owner_self",
                        "statement_kind": "preference",
                        "proposition": "用户平时更喜欢冰美式。",
                        "supports": [
                            {"evidence_id": "evidence-1", "start": 0, "end": len(raw1)},
                            {"evidence_id": "evidence-2", "start": 0, "end": len(raw2)},
                        ],
                    },
                }
            ),
            "model": "deepseek-v4-flash",
        }
    ]
    processed = _run(db_path, clock, script, evidence_ids=("evidence-1", "evidence-2"))
    assert processed == 1
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    # Two supports: +40 confidence, still exactly one physical model request.
    assert outcome["confidence"] == 640
    assert outcome["evidence_count"] == 2
    assert len(script) == 0  # exactly one route call was consumed


def test_invalid_model_result_is_zero_write_no_change(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script: list[Any] = [{"content": "not json at all", "model": "m"}]
    processed = _run(db_path, clock, script)
    assert processed == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == "invalid_model_result"
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    finally:
        db.close()


def test_span_out_of_range_is_zero_write_no_change(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script: list[Any] = [
        {
            "content": json.dumps(_one_cognition("用户喜欢冰美式。", (0, 999))),
            "model": "m",
        }
    ]
    processed = _run(db_path, clock, script)
    assert processed == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == "span_out_of_range"
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    finally:
        db.close()


def test_route_failure_is_terminal_and_never_called_twice(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    calls: list[object] = []

    def failing_route(messages: list[dict[str, str]], session_id: str) -> dict[str, object]:
        calls.append((messages, session_id))
        raise RuntimeError("provider down")

    _initialize_database(db_path)
    _insert_job(db_path, clock)
    processor = HermesBatchAdapterProcessor(str(db_path), failing_route, clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    row = _job(db_path)
    assert row["state"] == "dead"
    assert len(calls) == 1
    # No World writes happened.
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    finally:
        db.close()


def test_keyword_only_route_contract(tmp_path: Path) -> None:
    """The real host route closure makes ``session_id`` keyword-only; the
    processor must honor that exact contract (regression: TypeError when the
    call site passed it positionally)."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    calls: list[str] = []

    def kw_only_route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        del messages
        calls.append(session_id)
        return {"content": json.dumps(_no_change()), "model": "m"}

    _initialize_database(db_path)
    _insert_job(db_path, clock)
    processor = HermesBatchAdapterProcessor(str(db_path), kw_only_route, clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    assert calls == ["session-parent"]
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == "model_no_change"


def test_restatement_attaches_support_and_exact_replay_does_not_bump(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "用户平时更喜欢冰美式。"
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    _set_raw(db_path, "evidence-1", raw)
    processor = HermesBatchAdapterProcessor(
        str(db_path),
        _route([{"content": json.dumps(_one_cognition("用户平时更喜欢冰美式。", (0, len(raw)))), "model": "m"}]),
        clock=clock,
    )
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1

    # A second boundary restates the same proposition with NEW Evidence: the
    # 1.0 same-ID support path attaches it to the existing cognition and
    # recomputes confidence (600 -> 640), so the World revision advances.
    _insert_job(db_path, clock, job_id="job-2", evidence_ids=("evidence-2",))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-2'", (raw,))
    db.execute(
        "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-2'",
        (
            __import__("hashlib").sha256(raw.encode("utf-8")).hexdigest(),
        ),
    )
    db.close()
    processor2 = HermesBatchAdapterProcessor(
        str(db_path),
        _route([{"content": json.dumps(_one_cognition("用户平时更喜欢冰美式。", (0, len(raw)), evidence_id="evidence-2")), "model": "m"}]),
        clock=clock,
    )
    worker2 = WorldJobWorker(db_path, processor=processor2, policy=_policy(), clock=clock)
    assert worker2.run_until_quiescent() == 1
    row2 = _job(db_path, job_id="job-2")
    assert row2["state"] == "applied"
    outcome = json.loads(str(row2["world_result_json"]))
    assert outcome["world_revision"] == 2
    assert outcome["confidence"] == 640
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_evidence"
        ).fetchone()[0] == 2
    finally:
        db.close()

    # Exact replay of the same proposition with the SAME Evidence: no new
    # link, no confidence change, no revision bump — it is a no_change,
    # not a second applied World terminal.
    _insert_job(db_path, clock, job_id="job-3", evidence_ids=("evidence-2",))
    processor3 = HermesBatchAdapterProcessor(
        str(db_path),
        _route([{"content": json.dumps(_one_cognition("用户平时更喜欢冰美式。", (0, len(raw)), evidence_id="evidence-2")), "model": "m"}]),
        clock=clock,
    )
    worker3 = WorldJobWorker(db_path, processor=processor3, policy=_policy(), clock=clock)
    assert worker3.run_until_quiescent() == 1
    row3 = _job(db_path, job_id="job-3")
    assert row3["state"] == "no_change"
    assert row3["terminal_state"] == "no_change"
    assert row3["terminal_detail"] == "no_world_mutation"
    outcome3 = json.loads(str(row3["world_result_json"]))
    assert outcome3["state"] == "no_change"
    assert outcome3["reason"] == "no_world_mutation"
    assert outcome3["world_revision"] == 2  # exact replay: unchanged
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM cognition_evidence"
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT COUNT(*) FROM terminal_outcome WHERE job_id = 'job-3'"
        ).fetchone()[0] == 1
    finally:
        db.close()
