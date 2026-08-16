"""V1 deterministic Recall: read-only accepted World, zero model calls."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, cast

from memoweft.integrations.hermes import (
    HermesMemoWeftRuntime,
    _boundary_payload_hash,
)
from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor,
)
from memoweft.integrations.hermes.recall import (
    format_recall,
    match_cognitions,
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import MutableClock, _job, _policy

_RAW = "用户平时更喜欢冰美式。"


def _route(payload: dict[str, Any]) -> Callable[..., dict[str, object]]:
    def route(messages: list[dict[str, str]], session_id: str) -> dict[str, object]:
        del messages, session_id
        return cast(dict[str, object], payload)

    return route


def _boundary_envelope() -> dict[str, Any]:
    source_messages = [
        {"role": "user", "content": _RAW, "source_ref": "source:0"},
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
            "hermes-compression-boundary-v1:"
            + "a" * 32
            + ":"
            + payload_hash
        ),
    }


def _one_cognition_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "result": "one_cognition",
        "cognition": {
            "target": "owner_self",
            "statement_kind": "preference",
            "proposition": _RAW,
            "supports": [{"evidence_id": "", "start": 0, "end": len(_RAW)}],
        },
    }


def _applied_runtime(tmp_path: Path, clock: MutableClock) -> tuple[HermesMemoWeftRuntime, Path]:
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    runtime = HermesMemoWeftRuntime()
    payload = _one_cognition_payload()
    runtime.initialize(
        "sess",
        hermes_home=str(tmp_path),
        platform="weixin",
        agent_context="primary",
        one_shot_llm=_route({"content": json.dumps(payload), "model": "m"}),
    )
    # Stop the async thread BEFORE ingesting so the job is settled
    # deterministically below (the async processor would otherwise race with
    # an evidence id the test cannot know yet).
    runtime.shutdown()
    receipt = runtime.ingest_durable_boundary(_boundary_envelope())
    assert receipt["job_state"] == "pending"
    evidence_id = json.loads(
        str(
            __import__("sqlite3")
            .connect(db_path)
            .execute("SELECT evidence_ids_json FROM memory_world_job")
            .fetchone()[0]
        )
    )[0]
    job_id = str(
        __import__("sqlite3")
        .connect(db_path)
        .execute("SELECT job_id FROM memory_world_job")
        .fetchone()[0]
    )
    payload["cognition"]["supports"][0]["evidence_id"] = evidence_id
    worker = WorldJobWorker(
        db_path,
        processor=HermesBatchAdapterProcessor(
            str(db_path),
            _route({"content": json.dumps(payload), "model": "m"}),
        ),
        policy=_policy(),
    )
    assert worker.run_until_quiescent() == 1
    row = _job(db_path, job_id=job_id)
    assert row["state"] == "applied", row["last_error_type"]
    return runtime, db_path


def test_match_cognitions_hits_natural_language_query() -> None:
    rows = [
        {"id": "c1", "content": _RAW, "confidence": 600},
        {"id": "c2", "content": "用户每天跑步五公里。", "confidence": 600},
    ]
    items = match_cognitions("用户喜欢什么饮料", rows)
    assert [item["id"] for item in items] == ["c1"]


def test_match_cognitions_is_deterministic_and_thresholded() -> None:
    rows = [
        {"id": "c1", "content": _RAW, "confidence": 600},
        {"id": "c2", "content": "用户喜欢冰美式。", "confidence": 640},
    ]
    first = match_cognitions("喜欢冰美式", rows)
    second = match_cognitions("喜欢冰美式", rows)
    assert first == second
    # Unrelated query: below threshold, nothing leaks.
    assert match_cognitions("今天天气如何", rows) == []


def test_category_expansion_is_fallback_only_and_deterministic() -> None:
    """Owner decision 2026-08-16 (fallback-only): a category word expands to
    member names ONLY when the direct bigram match produced zero hits."""
    rows = [
        {"id": "c1", "content": "用户爱吃荔枝", "confidence": 600},
        {"id": "c2", "content": "小王爱喝咖啡", "confidence": 640},
    ]
    # Direct hits stay direct: the query shares bigrams with c1 only.
    direct = match_cognitions("用户爱吃荔枝", rows)
    assert [item["id"] for item in direct] == ["c1"]
    # No bigram overlap with either row → category fallback expands 水果→荔枝.
    fallback = match_cognitions("喜欢什么水果", rows)
    assert [item["id"] for item in fallback] == ["c1"]
    # Byte-stable across repeated calls.
    assert match_cognitions("喜欢什么水果", rows) == fallback
    # The 饮料 category reaches the coffee row the same way.
    assert [item["id"] for item in match_cognitions("平时喝什么饮料", rows)] == ["c2"]
    # No category, no bigram overlap: nothing leaks.
    assert match_cognitions("今天天气如何", rows) == []


def test_format_recall_is_claims_only() -> None:
    items = [{"id": "c1", "content": _RAW, "score": 0.4}]
    text = format_recall(items)
    assert _RAW in text
    assert "c1" not in text  # no internal IDs


def test_prefetch_recalls_applied_world_and_counts_status(tmp_path: Path) -> None:
    clock = MutableClock()
    runtime, _db_path = _applied_runtime(tmp_path, clock)
    text = runtime.prefetch("用户喜欢什么饮料", session_id="sess")
    assert _RAW in text
    assert runtime.last_recall_count == 1
    # Unrelated query: zero hits, zero injection.
    assert runtime.prefetch("今天天气如何", session_id="sess") == ""
    assert runtime.last_recall_count == 0


def test_prefetch_on_empty_world_returns_empty(tmp_path: Path) -> None:
    runtime = HermesMemoWeftRuntime()
    runtime.initialize(
        "sess",
        hermes_home=str(tmp_path),
        platform="weixin",
        agent_context="primary",
    )
    try:
        assert runtime.prefetch("任意问题", session_id="sess") == ""
        assert runtime.last_recall_count == 0
    finally:
        runtime.shutdown()


def test_prefetch_recalls_world_events(tmp_path: Path) -> None:
    """V4: first-class World Events join the deterministic Recall union."""
    import sqlite3

    runtime = HermesMemoWeftRuntime()
    runtime.initialize(
        "sess",
        hermes_home=str(tmp_path),
        platform="cli",
        agent_context="primary",
    )
    try:
        db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
        subject = str(runtime._ingestor.subject_id)
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO world_event (id, world_id, content, occurred_at, "
            "time_expression, participants_json, objects_json, formed_by, "
            "confidence, cred_status, invalid_at, created_at, updated_at) "
            "VALUES ('we-1', ?, '上周末我和小王去了南京', '2026-08-09', "
            "'上周末', '[]', '[]', 'stated', 600, 'limited', NULL, 't', 't')",
            (subject,),
        )
        db.commit()
        db.close()
        text = runtime.prefetch("上周末去了哪里", session_id="sess")
        assert "南京" in text
        assert runtime.last_recall_count == 1
        # Unrelated query: no event leak.
        assert runtime.prefetch("今天天气如何", session_id="sess") == ""
    finally:
        runtime.shutdown()
