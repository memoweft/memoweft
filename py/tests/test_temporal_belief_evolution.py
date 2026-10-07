"""Tests for temporal belief evolution (supersede & degrade, not delete)."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, cast

import pytest

from memoweft.integrations.hermes import HermesMemoWeftRuntime
from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor,
)
from memoweft.integrations.hermes.recall import (
    _match_world_rows,
    format_recall,
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
    def route(messages: list[dict[str, str]], session_id: str | None = None, **kwargs: Any) -> dict[str, object]:
        del messages, session_id, kwargs
        if calls is not None:
            calls.append(1)
        return cast(dict[str, object], script.pop(0))

    return route


def test_wang_to_zhang_temporal_evolution_v1_legacy(tmp_path: Path) -> None:
    """Verify temporal evolution via legacy schema_version=1 envelope."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)

    # Step 1: User says "我喜欢的女生叫王小姐，她是个温柔的人"
    raw_1 = "我喜欢的女生叫王小姐，她是个温柔的人"
    _insert_job(db_path, clock, job_id="job-1", evidence_ids=("evidence-1",))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (raw_1,))
    db.execute(
        "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-1'",
        (__import__("hashlib").sha256(raw_1.encode("utf-8")).hexdigest(),),
    )
    db.close()

    script_1: list[Any] = [
        {
            "content": json.dumps(
                {
                    "schema_version": 1,
                    "result": "one_cognition",
                    "cognition": {
                        "action": "form",
                        "target": "owner_self",
                        "statement_kind": "preference",
                        "proposition": "用户喜欢的女生是王小姐",
                        "supports": [{"evidence_id": "evidence-1", "start": 0, "end": 10}],
                    },
                }
            ),
            "model": "deepseek-v4-flash",
        }
    ]

    processor_1 = HermesBatchAdapterProcessor(
        str(db_path),
        _route(script_1),
        clock=clock,
    )
    worker_1 = WorldJobWorker(db_path, processor=processor_1, policy=_policy(), clock=clock)
    assert worker_1.run_until_quiescent() == 1

    # Verify Wang is established
    db = sqlite3.connect(db_path)
    wang_row = db.execute("SELECT id, content, confidence, cred_status, invalid_at FROM cognition").fetchone()
    assert wang_row is not None
    wang_id = wang_row[0]
    assert "王小姐" in wang_row[1]
    assert wang_row[2] == 600
    assert wang_row[3] == "limited"
    assert wang_row[4] is None
    db.close()

    # Step 2: Time shift: User says "那是之前喜欢的，我都不和王小姐联系了，现在我喜欢的是张小姐"
    raw_2 = "那是之前喜欢的，我都不和王小姐联系了，现在我喜欢的是张小姐"
    _insert_job(db_path, clock, job_id="job-2", evidence_ids=("evidence-2",))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-2'", (raw_2,))
    db.execute(
        "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-2'",
        (__import__("hashlib").sha256(raw_2.encode("utf-8")).hexdigest(),),
    )
    db.close()

    # Model identifies supersede: form Zhang + supersedes_cognition_id = wang_id
    script_2: list[Any] = [
        {
            "content": json.dumps(
                {
                    "schema_version": 1,
                    "result": "one_cognition",
                    "cognition": {
                        "action": "form",
                        "target": "owner_self",
                        "statement_kind": "preference",
                        "proposition": "用户现在喜欢的女生是张小姐",
                        "supersedes_cognition_id": wang_id,
                        "supports": [{"evidence_id": "evidence-2", "start": 24, "end": 29}],
                    },
                }
            ),
            "model": "deepseek-v4-flash",
        }
    ]

    processor_2 = HermesBatchAdapterProcessor(
        str(db_path),
        _route(script_2),
        clock=clock,
    )
    worker_2 = WorldJobWorker(db_path, processor=processor_2, policy=_policy(), clock=clock)
    assert worker_2.run_until_quiescent() == 1

    # Verify Rules A, B, C:
    db = sqlite3.connect(db_path)
    # Rule A: Evidences are NOT deleted
    assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] >= 2

    # Rule B: Wang is NOT deleted, invalid_at is STILL NULL, confidence is 0
    wang_after = db.execute(
        "SELECT id, content, confidence, cred_status, invalid_at FROM cognition WHERE id = ?",
        (wang_id,),
    ).fetchone()
    assert wang_after is not None
    assert wang_after[4] is None, "Wang invalid_at MUST remain None (evidence never erased)"
    assert wang_after[2] == 0, f"Wang confidence must degrade to 0, got {wang_after[2]}"
    assert wang_after[3] == "candidate", f"Wang cred_status must degrade to candidate, got {wang_after[3]}"

    # Contradict link attached to Wang
    contradict_count = db.execute(
        "SELECT COUNT(*) FROM cognition_evidence WHERE cognition_id = ? AND relation = 'contradict'",
        (wang_id,),
    ).fetchone()[0]
    assert contradict_count == 1, "Wang must have contradict link from new evidence"

    # Rule C: Zhang is formed with high confidence
    zhang_row = db.execute(
        "SELECT id, content, confidence, cred_status, invalid_at FROM cognition WHERE content LIKE '%张小姐%'",
    ).fetchone()
    assert zhang_row is not None
    zhang_id = zhang_row[0]
    assert zhang_row[2] == 600
    assert zhang_row[3] == "limited"
    assert zhang_row[4] is None

    # Transition recorded in cognition_transitions table
    trans_row = db.execute(
        "SELECT prior_cognition_id, replacement_cognition_id FROM cognition_transitions WHERE prior_cognition_id = ?",
        (wang_id,),
    ).fetchone()
    assert trans_row is not None
    assert trans_row[0] == wang_id
    assert trans_row[1] == zhang_id
    db.close()


def test_wang_to_zhang_temporal_evolution_v8_batch(tmp_path: Path) -> None:
    """Verify temporal evolution via current V8 batch envelope."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)

    raw_1 = "用户平时更喜欢王小姐。"
    _insert_job(db_path, clock, job_id="job-1", evidence_ids=("evidence-1",))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (raw_1,))
    db.execute(
        "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-1'",
        (__import__("hashlib").sha256(raw_1.encode("utf-8")).hexdigest(),),
    )
    db.close()

    script_1: list[Any] = [
        {
            "content": json.dumps(
                {
                    "schema_version": 8,
                    "result": "cognitions",
                    "cognitions": [
                        {
                            "action": "form",
                            "target": "owner_self",
                            "statement_kind": "preference",
                            "proposition": "用户平时更喜欢王小姐。",
                            "supports": [{"evidence_id": "evidence-1", "start": 0, "end": len(raw_1)}],
                        }
                    ],
                }
            ),
            "model": "deepseek-v4-flash",
        }
    ]

    processor_1 = HermesBatchAdapterProcessor(
        str(db_path),
        _route(script_1),
        clock=clock,
    )
    worker_1 = WorldJobWorker(db_path, processor=processor_1, policy=_policy(), clock=clock)
    assert worker_1.run_until_quiescent() == 1

    db = sqlite3.connect(db_path)
    wang_row = db.execute("SELECT id, confidence, cred_status FROM cognition WHERE content LIKE '%王小姐%'").fetchone()
    assert wang_row is not None
    wang_id = wang_row[0]
    assert wang_row[1] == 600
    db.close()

    raw_2 = "那是之前喜欢的，用户平时更喜欢的是张小姐。"
    _insert_job(db_path, clock, job_id="job-2", evidence_ids=("evidence-2",))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-2'", (raw_2,))
    db.execute(
        "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-2'",
        (__import__("hashlib").sha256(raw_2.encode("utf-8")).hexdigest(),),
    )
    db.close()

    script_2: list[Any] = [
        {
            "content": json.dumps(
                {
                    "schema_version": 8,
                    "result": "cognitions",
                    "cognitions": [
                        {
                            "action": "form",
                            "target": "owner_self",
                            "statement_kind": "preference",
                            "proposition": "用户平时更喜欢的是张小姐。",
                            "supersedes_cognition_id": wang_id,
                            "supports": [{"evidence_id": "evidence-2", "start": 8, "end": len(raw_2)}],
                        }
                    ],
                }
            ),
            "model": "deepseek-v4-flash",
        }
    ]

    processor_2 = HermesBatchAdapterProcessor(
        str(db_path),
        _route(script_2),
        clock=clock,
    )
    worker_2 = WorldJobWorker(db_path, processor=processor_2, policy=_policy(), clock=clock)
    assert worker_2.run_until_quiescent() == 1

    db = sqlite3.connect(db_path)
    # Wang degraded to 0 and candidate, invalid_at is None
    wang_row = db.execute("SELECT confidence, cred_status, invalid_at FROM cognition WHERE id = ?", (wang_id,)).fetchone()
    assert wang_row[0] == 0
    assert wang_row[1] == "candidate"
    assert wang_row[2] is None
    # Transition exists in cognition_transitions
    trans = db.execute("SELECT replacement_cognition_id FROM cognition_transitions WHERE prior_cognition_id = ?", (wang_id,)).fetchone()
    assert trans is not None
    # Zhang exists with 600 confidence
    zhang_row = db.execute("SELECT confidence, cred_status, invalid_at FROM cognition WHERE content LIKE '%张小姐%'").fetchone()
    assert zhang_row is not None
    assert zhang_row[0] == 600
    assert zhang_row[1] == "limited"
    assert zhang_row[2] is None
    db.close()


def test_temporal_recall_semantics() -> None:
    # Prepare rows representing the state after belief evolution
    rows = [
        {
            "kind": "cognition",
            "id": "wang_id",
            "statement_kind": "preference",
            "content": "用户喜欢的女生是王小姐",
            "confidence": 0,
            "anchors": ("王小姐",),
            "is_superseded": True,
        },
        {
            "kind": "cognition",
            "id": "zhang_id",
            "statement_kind": "preference",
            "content": "用户现在喜欢的女生是张小姐",
            "confidence": 600,
            "anchors": ("张小姐",),
            "is_superseded": False,
        },
    ]

    # Query 1: Present query "我喜欢谁？"
    # Should only return Zhang, Wang is superseded and confidence=0
    present_matches = _match_world_rows("我喜欢谁？", rows)
    matched_ids = [r["id"] for r in present_matches]
    assert "zhang_id" in matched_ids
    assert "wang_id" not in matched_ids

    # Query 2: Historical query "我以前喜欢过谁？"
    # Should return Wang because of temporal keyword "以前"
    hist_matches = _match_world_rows("我以前喜欢过谁？", rows)
    hist_ids = [r["id"] for r in hist_matches]
    assert "wang_id" in hist_ids

    # Format check: Wang must be rendered as 记忆（过往）
    formatted_hist = format_recall(hist_matches)
    assert "记忆（过往）：" in formatted_hist
    assert "王小姐" in formatted_hist

    # Query 3: Explicit named query "王小姐是谁？"
    # Even if present tense, explicit entity query should recall Wang as past memory
    named_matches = _match_world_rows("王小姐是谁？", rows)
    named_ids = [r["id"] for r in named_matches]
    assert "wang_id" in named_ids
    formatted_named = format_recall(named_matches)
    assert "记忆（过往）：" in formatted_named
    assert "王小姐" in formatted_named


def test_relationship_temporal_evolution_wang_to_zhang(tmp_path: Path) -> None:
    """Verify relationship-level temporal belief evolution (Wang -> Zhang)."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)

    # Step 1: User says "我的女朋友叫王小姐"
    raw_1 = "我的女朋友叫王小姐"
    _insert_job(db_path, clock, job_id="job-1", evidence_ids=("evidence-1",))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (raw_1,))
    db.execute(
        "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-1'",
        (__import__("hashlib").sha256(raw_1.encode("utf-8")).hexdigest(),),
    )
    db.close()

    script_1: list[Any] = [
        {
            "content": json.dumps(
                {
                    "schema_version": 8,
                    "result": "cognitions",
                    "cognitions": [
                        {
                            "action": "form",
                            "target": "owner_self",
                            "statement_kind": "relationship",
                            "formed_by": "stated",
                            "proposition": "用户的女朋友叫王小姐",
                            "target_entity": {"canonical_name": "王小姐", "kind": "person"},
                            "relation_type": "girlfriend",
                            "supports": [{"evidence_id": "evidence-1", "start": 0, "end": len(raw_1)}],
                        }
                    ],
                }
            ),
            "model": "deepseek-v4-flash",
        }
    ]

    processor_1 = HermesBatchAdapterProcessor(
        str(db_path),
        _route(script_1),
        clock=clock,
    )
    worker_1 = WorldJobWorker(db_path, processor=processor_1, policy=_policy(), clock=clock)
    assert worker_1.run_until_quiescent() == 1

    db = sqlite3.connect(db_path)
    wang_rel = db.execute("SELECT id, content, confidence, cred_status, invalid_at FROM relationship WHERE target_entity_id IN (SELECT id FROM entity WHERE canonical_name = '王小姐')").fetchone()
    assert wang_rel is not None
    wang_rel_id = wang_rel[0]
    assert wang_rel[2] == 600
    assert wang_rel[4] is None
    db.close()

    # Step 2: Temporal shift: "那是之前谈的，我现在的女朋友叫张小姐"
    raw_2 = "那是之前谈的，我现在的女朋友叫张小姐"
    _insert_job(db_path, clock, job_id="job-2", evidence_ids=("evidence-2",))
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-2'", (raw_2,))
    db.execute(
        "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-2'",
        (__import__("hashlib").sha256(raw_2.encode("utf-8")).hexdigest(),),
    )
    db.close()

    script_2: list[Any] = [
        {
            "content": json.dumps(
                {
                    "schema_version": 8,
                    "result": "cognitions",
                    "cognitions": [
                        {
                            "action": "form",
                            "target": "owner_self",
                            "statement_kind": "relationship",
                            "formed_by": "stated",
                            "proposition": "用户的女朋友叫张小姐",
                            "target_entity": {"canonical_name": "张小姐", "kind": "person"},
                            "relation_type": "girlfriend",
                            "supersedes_relationship_id": wang_rel_id,
                            "supports": [{"evidence_id": "evidence-2", "start": 8, "end": len(raw_2)}],
                        }
                    ],
                }
            ),
            "model": "deepseek-v4-flash",
        }
    ]

    processor_2 = HermesBatchAdapterProcessor(
        str(db_path),
        _route(script_2),
        clock=clock,
    )
    worker_2 = WorldJobWorker(db_path, processor=processor_2, policy=_policy(), clock=clock)
    assert worker_2.run_until_quiescent() == 1

    # Verify state machine transitions
    db = sqlite3.connect(db_path)
    # 1. Evidence is preserved (Append-only)
    ev_count = db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    assert ev_count >= 2

    # 2. Wang relationship is NOT deleted (invalid_at is None) and degraded
    wang_after = db.execute("SELECT confidence, cred_status, invalid_at FROM relationship WHERE id = ?", (wang_rel_id,)).fetchone()
    assert wang_after[2] is None, "Wang relationship invalid_at MUST remain None"
    assert wang_after[0] == 0, f"Wang relationship confidence must degrade to 0, got {wang_after[0]}"
    assert wang_after[1] == "candidate"

    # 3. Zhang relationship established with 600 confidence
    zhang_rel = db.execute("SELECT id, confidence, cred_status, invalid_at FROM relationship WHERE target_entity_id IN (SELECT id FROM entity WHERE canonical_name = '张小姐')").fetchone()
    assert zhang_rel is not None
    assert zhang_rel[1] == 600
    assert zhang_rel[3] is None

    # 4. Transition recorded in relationship_transitions
    trans = db.execute("SELECT replacement_relationship_id FROM relationship_transitions WHERE prior_relationship_id = ?", (wang_rel_id,)).fetchone()
    assert trans is not None
    assert trans[0] == zhang_rel[0]

    # 5. Recall dual-state queries
    from memoweft.integrations.hermes.recall import recall_world_snapshot

    # Present query: "我的女朋友是谁？" -> Only Zhang
    res_present = recall_world_snapshot(db, "owner", "我的女朋友是谁？")
    assert res_present is not None
    assert "张小姐" in res_present.rendered_recall
    assert "王小姐" not in res_present.rendered_recall

    # Historical query: "我以前的女朋友是谁？" -> Wang with 记忆（过往）
    res_hist = recall_world_snapshot(db, "owner", "我以前的女朋友是谁？")
    assert res_hist is not None
    assert "王小姐" in res_hist.rendered_recall
    assert "记忆（过往）：" in res_hist.rendered_recall

    # Explicit query: "王小姐是谁？" -> Wang recalled with 记忆（过往）
    res_named = recall_world_snapshot(db, "owner", "王小姐是谁？")
    assert res_named is not None
    assert "王小姐" in res_named.rendered_recall
    assert "记忆（过往）：" in res_named.rendered_recall

    db.close()
