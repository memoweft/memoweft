from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.world_worker import WorldJobStore, WorldJobWorker
from test_hermes_batch_adapter_v5 import _batch, _model, _set_evidence
from test_hermes_world_worker import MutableClock, _initialize_database, _insert_job, _job, _policy


def _item(proposition: str) -> dict[str, Any]:
    return {
        "action": "form", "target": "owner_self", "statement_kind": "preference",
        "formed_by": "stated", "proposition": proposition,
        "supports": [{"evidence_id": "evidence-1", "segment_id": "s0"}],
    }


def _run(path: Path, raw: str, outputs: list[dict[str, object]]) -> tuple[dict[str, Any], list[Any]]:
    clock = MutableClock()
    _initialize_database(path)
    _insert_job(path, clock)
    _set_evidence(path, "evidence-1", raw)
    calls: list[Any] = []

    def route(messages: Any, *, session_id: str) -> dict[str, object]:
        calls.append((messages, session_id))
        return outputs.pop(0)

    processor = HermesBatchAdapterProcessor(str(path), route, clock=clock)
    worker = WorldJobWorker(path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    return dict(_job(path)), calls


@pytest.mark.parametrize(("raw", "wrong"), [
    ("以后请叫我小禾。", "用户希望被叫作小莓"),
    ("以后请叫我阿岚。", "用户希望被叫作阿朵"),
    ("我喜欢每次跑步12公里。", "用户喜欢每次跑步21公里"),
    ("我喜欢在2026年10月8日庆祝生日。", "用户喜欢在2026年8月10日庆祝生日"),
])
def test_stated_fact_uses_evidence_spelling(tmp_path: Path, raw: str, wrong: str) -> None:
    path = tmp_path / "world.sqlite3"
    row, calls = _run(path, raw, [_model(_batch(_item(wrong)))])
    assert row["state"] == "applied"
    assert len(calls) == 1
    with sqlite3.connect(path) as db:
        content = db.execute("SELECT content FROM cognition").fetchone()[0]
    assert wrong not in content
    assert raw.rstrip("。") in content or raw[1:].rstrip("。") in content


@pytest.mark.parametrize("invalid", ["not JSON", "", "missing_proposition"])
def test_invalid_result_rewrites_once_with_error(tmp_path: Path, invalid: str) -> None:
    path = tmp_path / "world.sqlite3"
    item = _item("ignored")
    del item["proposition"]
    first: dict[str, object] = _model(_batch(item)) if invalid == "missing_proposition" else {"content": invalid}
    row, calls = _run(path, "我喜欢用买菜的例子解释。", [first, _model(_batch(_item("ignored")))])
    assert row["state"] == "applied"
    assert len(calls) == 2
    feedback = json.loads(calls[1][0][-1]["content"])["compiler_error"]
    assert feedback["code"] == ("invalid_proposition" if invalid == "missing_proposition" else "invalid_model_result")
    assert "proposition" in feedback["instruction"]
    assert calls[1][0][-2]["content"] == first["content"]
    stored = json.loads(row["model_result_json"])["formation_rewrite"]
    assert stored["state"] == "completed"
    assert stored["first_result"] == first
    assert stored["final_error"] is None


def test_second_invalid_result_is_zero_write_and_durable(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    row, calls = _run(path, "我喜欢用买菜的例子解释。", [{"content": "bad"}, {"content": "still bad"}])
    assert row["state"] == "no_change"
    assert len(calls) == 2
    stored = json.loads(row["model_result_json"])
    assert stored["formation_rewrite"]["error"]["code"] == "invalid_model_result"
    assert stored["formation_rewrite"]["final_error"] == "invalid_model_result"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM cognition").fetchone()[0] == 0


def test_legacy_envelope_cannot_bypass_fact_grounding(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    raw = "我希望你叫我小禾。"
    legacy = {"schema_version": 1, "result": "one_cognition", "cognition": {
        "target": "owner_self", "statement_kind": "preference", "proposition": "用户希望被叫作小莓",
        "supports": [{"evidence_id": "evidence-1", "start": 0, "end": len(raw)}],
    }}
    row, calls = _run(path, raw, [_model(legacy)])
    assert row["state"] == "applied"
    assert len(calls) == 1
    with sqlite3.connect(path) as db:
        content = db.execute("SELECT content FROM cognition").fetchone()[0]
    assert "小禾" in content
    assert "小莓" not in content


def test_unverifiable_entity_name_is_rejected_then_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    bad = _item("阿朵做展览海报")
    bad.update(statement_kind="attribute", entity={"canonical_name": "阿朵", "kind": "person"})
    good = {**bad, "entity": {"canonical_name": "阿岚", "kind": "person"}}
    row, calls = _run(path, "阿岚做展览海报。", [_model(_batch(bad)), _model(_batch(good))])
    assert row["state"] == "applied"
    assert len(calls) == 2
    with sqlite3.connect(path) as db:
        names = [r[0] for r in db.execute("SELECT canonical_name FROM entity")]
    assert "阿岚" in names
    assert "阿朵" not in names


def test_unverifiable_entity_after_rewrite_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    bad = _item("阿朵做展览海报")
    bad.update(statement_kind="attribute", entity={"canonical_name": "阿朵", "kind": "person"})
    row, calls = _run(path, "阿岚做展览海报。", [_model(_batch(bad)), _model(_batch(bad))])
    assert row["state"] == "no_change"
    assert len(calls) == 2
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM entity").fetchone()[0] == 0


@pytest.mark.parametrize("rewrite_state", ["reserved", "completed"])
def test_recovery_never_repeats_reserved_or_completed_rewrite(tmp_path: Path, rewrite_state: str) -> None:
    path = tmp_path / "world.sqlite3"
    clock = MutableClock()
    _initialize_database(path)
    _insert_job(path, clock)
    store = WorldJobStore(path, policy=_policy(), clock=clock)
    claim = store.claim_one("crashed-worker")
    assert claim is not None
    assert store.mark_dispatch_started(claim)

    def forbidden(*args: Any, **kwargs: Any) -> dict[str, object]:
        pytest.fail("recovery must not dispatch another rewrite")

    processor = HermesBatchAdapterProcessor(str(path), forbidden, clock=clock)
    with sqlite3.connect(path, isolation_level=None) as db:
        processor._persist_checkpoint(db, claim, {
            "content": "bad JSON",
            "formation_rewrite": {"state": rewrite_state, "error": {"code": "invalid_model_result"}},
        })
    clock.advance(11)
    worker = WorldJobWorker(path, processor=processor, policy=_policy(), clock=clock, worker_id="restarted")
    assert worker.run_until_quiescent() == 1
    assert _job(path)["state"] == "no_change"
