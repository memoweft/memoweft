"""Tests for AI Self-Commitments distillation and query integration."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Literal, TypedDict

from support.json_assertions import as_int, as_objects, as_string, as_string

from memoweft.integrations.dsh_bridge.commitments import (
    distill_commitments_from_messages,
    query_matching_commitments,
    record_commitments_for_episode,
)
from memoweft.integrations.dsh_bridge.interactions import query_interactions
from memoweft.store.driver import open_db
from memoweft.store.interaction_commitment import SqliteInteractionCommitmentStore
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.types import InteractionContextInput, VisibleTurn


class Message(TypedDict):
    role: Literal["user", "assistant", "tool"]
    content: str


def test_distill_commitments_from_messages() -> None:
    messages: list[Message] = [
        {"role": "user", "content": "我们这次项目选什么后端框架好？"},
        {
            "role": "assistant",
            "content": "我建议在本次项目中选用 Axum 框架，因为它的异步性能更好。另外，我会在下周二提醒你复盘理财账户。双方达成一致，采用方案B进行重构。",
        },
    ]

    distilled = distill_commitments_from_messages(messages)
    assert len(distilled) == 3

    kinds = [d[0] for d in distilled]
    assert "recommendation" in kinds
    assert "commitment" in kinds
    assert "agreement" in kinds

    rec = next(d for d in distilled if d[0] == "recommendation")
    assert "AI建议" in rec[1]
    assert "Axum" in rec[1]

    com = next(d for d in distilled if d[0] == "commitment")
    assert "AI承诺" in com[1]
    assert "提醒" in com[1]

    agr = next(d for d in distilled if d[0] == "agreement")
    assert "双方决定" in agr[1]
    assert "方案B" in agr[1]


def test_store_and_query_commitments(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    db = open_db(str(db_path))

    store = SqliteInteractionCommitmentStore(db)
    item = store.record(
        subject_id="user-01",
        conversation_id="conv-01",
        episode_id="ep-01",
        kind="recommendation",
        content="AI建议选用Axum框架",
        raw_quote="我建议在本次项目中选用 Axum 框架",
    )
    assert item.id
    assert item.content == "AI建议选用Axum框架"

    # Idempotent
    item2 = store.record(
        subject_id="user-01",
        conversation_id="conv-01",
        episode_id="ep-01",
        kind="recommendation",
        content="AI建议选用Axum框架",
        raw_quote="我建议在本次项目中选用 Axum 框架",
    )
    assert item2.id == item.id

    # Query
    queried = store.query("user-01", kind="recommendation")
    assert len(queried) == 1
    assert queried[0].content == "AI建议选用Axum框架"

    db.close()


def test_query_interactions_integration(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    db = open_db(str(db_path))

    # Insert boundary job so context is eligible
    ev_ids_json = json.dumps(["ev-1"])
    db.execute(
        """INSERT INTO memory_world_job (
            job_id, job_schema_version, boundary_event_id, boundary_payload_hash,
            boundary_schema_version, provider_name, parent_session_id, result_session_id,
            boundary_mode, formal_target_json, formal_target_hash, subject_id, host_id,
            evidence_ids_json, state, delivery_receipt_json, delivery_receipt_hash, created_at, completed_at
        ) VALUES (
            'job-1', 1, 'ep-01', 'hash', 1, 'memoweft', 'conv-01', 'conv-01',
            'in_place', '{}', 'hash', 'user-01', 'host-01', ?, 'applied', '{}', 'hash', '2026-09-18T00:00:00Z', '2026-09-18T00:00:00Z'
        )""",
        (ev_ids_json,),
    )
    db.execute(
        "INSERT INTO evidence (id, subject_id, source_kind, host_id, occurred_at, recorded_at, raw_content, summary, allow_local_read, allow_cloud_read, allow_inference) "
        "VALUES ('ev-1', 'user-01', 'spoken', 'host-01', '2026-09-18T00:00:00Z', '2026-09-18T00:00:00Z', '原话', '摘要', 1, 1, 1)"
    )

    messages: list[Message] = [
        {"role": "user", "content": "我们这次项目用什么框架？"},
        {"role": "assistant", "content": "我建议在本次项目中选用 Axum 框架。" + "大段废话 " * 30},
    ]

    # Record context
    turns = [
        VisibleTurn(role=m["role"], content=m["content"], source_ref=f"source:{i}")
        for i, m in enumerate(messages)
    ]
    SqliteInteractionContextStore(db).record(
        InteractionContextInput(
            subject_id="user-01",
            conversation_id="conv-01",
            episode_id="ep-01",
            context=turns,
        )
    )

    # Record commitments
    record_commitments_for_episode(
        db,
        subject_id="user-01",
        conversation_id="conv-01",
        episode_id="ep-01",
        messages=messages,
    )
    db.commit()
    db.close()

    # Query interactions with query="建议"
    res = query_interactions(db_path, subject_id="user-01", query="你之前给过什么建议？", session_id="conv-02")
    assert as_int(res["commitment_count"]) >= 1
    assert "commitments" in res
    assert len(as_objects(res["commitments"])) >= 1
    assert "Axum" in as_string(as_objects(res["commitments"])[0]["content"])

    # Verify rendered text
    rendered = str(as_string(res["rendered_context"]))
    assert "[AI历史建议与承诺]" in rendered
    assert "[AI建议] AI建议在本次项目中选用 Axum 框架" in rendered
    # Verify assistant long monologue is truncated
    assert "大段废话 大段废话" not in rendered or "..." in rendered
