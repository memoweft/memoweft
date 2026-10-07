"""Test that stated propositions preserve the Agent's high-level understanding
rather than being mechanically overwritten by verbatim slice fragments.
"""
from __future__ import annotations

import hashlib
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
from test_hermes_batch_adapter_v3d import (
    _batch,
    _model,
)


def _route(script: list[Any]) -> Callable[..., dict[str, object]]:
    def route(messages: list[dict[str, str]], session_id: str = "", **kwargs: Any) -> dict[str, object]:
        del messages, session_id, kwargs
        return cast(dict[str, object], script.pop(0))
    return route


def _set_evidence(db_path: Path, evidence_id: str, raw: str) -> None:
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute(
            "UPDATE evidence SET raw_content = ? WHERE id = ?",
            (raw, evidence_id),
        )
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? "
            "WHERE evidence_id = ?",
            (hashlib.sha256(raw.encode("utf-8")).hexdigest(), evidence_id),
        )
    finally:
        db.close()


def _cognitions(db_path: Path) -> list[dict[str, Any]]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            "SELECT id, content, content_type, formed_by FROM cognition ORDER BY id"
        ).fetchall()
        return [
            {"id": r[0], "content": r[1], "content_type": r[2], "formed_by": r[3]}
            for r in rows
        ]
    finally:
        db.close()


def test_agent_understanding_is_preserved(tmp_path: Path) -> None:
    """Agent's synthesis of multiple habits into a coherent understanding is accepted."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw_utterance = "1.打游戏-王者荣耀，刷抖音、看书听书\n2.习惯了吧\n3.有但是不多"
    
    agent_understanding = "用户平时的娱乐习惯主要是打《王者荣耀》，闲暇时也会刷抖音、看书或听书；平时习惯独处，朋友不多"

    item = {
        "action": "form",
        "target": "owner_self",
        "statement_kind": "preference",
        "formed_by": "stated",
        "proposition": agent_understanding,
        "supports": [
            {"evidence_id": "evidence-1", "segment_id": "s0"},
            {"evidence_id": "evidence-1", "segment_id": "s1"},
        ],
    }

    script = [_model(_batch(item))]

    _initialize_database(db_path)
    _insert_job(db_path, clock, job_id="job-1", evidence_ids=("evidence-1",))
    _set_evidence(db_path, "evidence-1", raw_utterance)

    processor = HermesBatchAdapterProcessor(str(db_path), _route(script), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1

    job_row = _job(db_path)
    assert job_row["state"] == "applied"
    cogs = _cognitions(db_path)
    assert len(cogs) == 1
    # Crucial assertion: the proposition is the Agent's understanding, NOT "用户1.打游戏-王者荣耀，"
    assert cogs[0]["content"] == agent_understanding


def test_empty_proposition_falls_back_to_normalized_slice(tmp_path: Path) -> None:
    """If proposition is omitted or empty placeholder, fallback to normalized slice."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw_utterance = "我平时喜欢喝冰美式"

    item = {
        "action": "form",
        "target": "owner_self",
        "statement_kind": "preference",
        "formed_by": "stated",
        "proposition": "…",  # placeholder
        "supports": [
            {"evidence_id": "evidence-1", "segment_id": "s0"},
        ],
    }

    script = [_model(_batch(item))]

    _initialize_database(db_path)
    _insert_job(db_path, clock, job_id="job-1", evidence_ids=("evidence-1",))
    _set_evidence(db_path, "evidence-1", raw_utterance)

    processor = HermesBatchAdapterProcessor(str(db_path), _route(script), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1

    job_row = _job(db_path)
    assert job_row["state"] == "applied"
    cogs = _cognitions(db_path)
    assert len(cogs) == 1
    assert cogs[0]["content"] == "用户平时喜欢喝冰美式"
