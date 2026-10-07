from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from memoweft.integrations.dsh_bridge import (
    DshBoundaryError,
    DshMemoWeftRuntime,
)
from memoweft.integrations.dsh_bridge.interactions import InteractionQueryError
from memoweft.integrations.dsh_bridge.protocol_v2 import DshRpcV2Server
from memoweft.store import open_db
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.types import InteractionContextInput, VisibleTurn


EMPTY = {
    "schema_version": 1,
    "capture_status": "complete_empty",
    "world_items": [],
    "interaction_ids": [],
}


def _boundary(
    conversation_id: str,
    occurrence: int,
    messages: list[dict[str, object]],
) -> dict[str, object]:
    source_messages = [
        {**message, "source_ref": f"source:{index}"}
        for index, message in enumerate(messages)
    ]
    payload = {
        "schema_version": 1,
        "provider_name": "memoweft",
        "parent_session_id": conversation_id,
        "result_session_id": conversation_id,
        "mode": "turn",
        "source_messages": source_messages,
    }
    payload_hash = sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        **payload,
        "payload_hash": payload_hash,
        "event_id": (
            f"weftmate-turn-boundary-v1:{occurrence:032x}:{payload_hash}"
        ),
    }


def _runtime(tmp_path: Path) -> DshMemoWeftRuntime:
    runtime = DshMemoWeftRuntime()
    runtime.initialize(
        "active",
        dsh_home=str(tmp_path),
        auto_route=False,
        model_tier="local",
    )
    return runtime


def _ingest(
    runtime: DshMemoWeftRuntime,
    *,
    conversation_id: str,
    occurrence: int,
    user_message_id: str,
    user_content: str,
    assistants: list[tuple[str, str, dict[str, object] | None]] | None = None,
) -> tuple[str, str]:
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": user_content,
            "message_id": user_message_id,
        }
    ]
    for message_id, content, dependencies in assistants or []:
        assistant: dict[str, object] = {
            "role": "assistant",
            "content": content,
            "message_id": message_id,
        }
        if dependencies is not None:
            assistant["model_context_dependencies"] = dependencies
        messages.append(assistant)
    boundary = _boundary(conversation_id, occurrence, messages)
    runtime.ingest_durable_boundary(boundary)
    assert runtime.db_path is not None
    with sqlite3.connect(runtime.db_path) as db:
        row = db.execute(
            "SELECT id, context_hash FROM interaction_context "
            "WHERE subject_id = ? AND conversation_id = ? AND episode_id = ?",
            (runtime.subject_id, conversation_id, boundary["event_id"]),
        ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def _world_cognition(
    runtime: DshMemoWeftRuntime,
    *,
    episode_id: str,
    item_id: str,
    content: str,
) -> None:
    assert runtime.db_path is not None
    with sqlite3.connect(runtime.db_path) as db:
        evidence_ids = json.loads(
            db.execute(
                "SELECT evidence_ids_json FROM memory_world_job "
                "WHERE boundary_event_id = ? AND subject_id = ?",
                (episode_id, runtime.subject_id),
            ).fetchone()[0]
        )
        db.execute(
            "INSERT INTO cognition (id, subject_id, content, content_type, "
            "formed_by, confidence, cred_status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'preference', 'stated', 700, 'supported', ?, ?)",
            (item_id, runtime.subject_id, content, "2026-09-23T00:00:00Z", "2026-09-23T00:00:00Z"),
        )
        db.execute(
            "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
            "VALUES (?, ?, 'support')",
            (item_id, evidence_ids[0]),
        )


def _world_dependency(item_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "capture_status": "complete",
        "world_items": [{"object_kind": "cognition", "item_id": item_id}],
        "interaction_ids": [],
    }


@pytest.mark.parametrize("change", ["retracted", "corrected"])
def test_direct_world_change_filters_model_but_preserves_history(
    tmp_path: Path, change: str
) -> None:
    runtime = _runtime(tmp_path)
    source_id, _ = _ingest(
        runtime,
        conversation_id="source",
        occurrence=1,
        user_message_id="source-user",
        user_content="记录属性代号 Z7。",
    )
    assert runtime.db_path is not None
    with sqlite3.connect(runtime.db_path) as db:
        source_episode = db.execute(
            "SELECT episode_id FROM interaction_context WHERE id = ?", (source_id,)
        ).fetchone()[0]
    _world_cognition(
        runtime,
        episode_id=str(source_episode),
        item_id="layout-world-v1",
        content="月度面板采用星图布局",
    )
    _ingest(
        runtime,
        conversation_id="discussion",
        occurrence=2,
        user_message_id="discussion-user",
        user_content="回顾月度面板布局安排。",
        assistants=[
            (
                "discussion-assistant",
                "月度面板可以继续采用星图布局。",
                _world_dependency("layout-world-v1"),
            )
        ],
    )
    before = runtime.query_interactions(
        "回忆之前讨论的月度面板布局", projection="model"
    )
    assert before["count"] == 1
    assert "星图布局" in before["rendered_context"]

    with sqlite3.connect(runtime.db_path) as db:
        db.execute(
            "UPDATE cognition SET invalid_at = ? WHERE id = ?",
            ("2026-09-23T00:01:00Z", "layout-world-v1"),
        )
        if change == "corrected":
            db.execute(
                "INSERT INTO cognition (id, subject_id, content, content_type, "
                "formed_by, confidence, cred_status, created_at, updated_at) "
                "VALUES ('layout-world-v2', ?, '月度面板采用网格布局', "
                "'preference', 'stated', 700, 'supported', ?, ?)",
                (runtime.subject_id, "2026-09-23T00:01:00Z", "2026-09-23T00:01:00Z"),
            )
            db.execute(
                "INSERT INTO cognition_transitions "
                "(id, prior_cognition_id, replacement_cognition_id, reason, revision) "
                "VALUES ('transition-layout', 'layout-world-v1', 'layout-world-v2', "
                "'corrected', 2)"
            )

    model = runtime.query_interactions(
        "回忆之前讨论的月度面板布局", projection="model"
    )
    history = runtime.query_interactions(
        "回忆之前讨论的月度面板布局", projection="history"
    )
    assert model["count"] == 0
    assert history["count"] == 1
    assert history["items"][0]["dependency_state"] == "stale"
    assert history["items"][0]["turns"][1]["content"] == (
        "月度面板可以继续采用星图布局。"
    )
    managed = runtime.query_interactions(
        "布局", projection="history", search_mode="history_search"
    )
    assert managed["count"] == 1
    assert managed["items"][0]["dependency_state"] == "stale"
    assert runtime.query_interactions("布局", projection="history")["count"] == 0
    assert runtime.query_interactions(
        "无关火山编号", projection="history", search_mode="history_search"
    )["count"] == 0
    with pytest.raises(InteractionQueryError, match="invalid_interaction_query"):
        runtime.query_interactions(
            "布局", projection="model", search_mode="history_search"
        )
    with sqlite3.connect(runtime.db_path) as db:
        assert db.execute(
            "SELECT raw_content FROM evidence WHERE raw_content LIKE '%Z7%'"
        ).fetchone()[0] == "记录属性代号 Z7。"
        assert db.execute(
            "SELECT invalid_at FROM cognition WHERE id = 'layout-world-v1'"
        ).fetchone()[0] == "2026-09-23T00:01:00Z"
    runtime.shutdown()


def test_transitive_dependency_and_partial_turn_filtering(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    source_id, _ = _ingest(
        runtime,
        conversation_id="source",
        occurrence=10,
        user_message_id="source-user",
        user_content="登记来源编号 R4。",
    )
    assert runtime.db_path is not None
    with sqlite3.connect(runtime.db_path) as db:
        source_episode = str(
            db.execute(
                "SELECT episode_id FROM interaction_context WHERE id = ?", (source_id,)
            ).fetchone()[0]
        )
    _world_cognition(
        runtime,
        episode_id=source_episode,
        item_id="board-world",
        content="季度看板使用环形布局",
    )
    first_id, _ = _ingest(
        runtime,
        conversation_id="chain-one",
        occurrence=11,
        user_message_id="chain-one-user",
        user_content="讨论季度看板的基础布局。",
        assistants=[
            (
                "chain-one-assistant",
                "基础方案使用环形布局。",
                _world_dependency("board-world"),
            )
        ],
    )
    transitive = {
        "schema_version": 1,
        "capture_status": "complete",
        "world_items": [],
        "interaction_ids": [first_id],
    }
    second_id, _ = _ingest(
        runtime,
        conversation_id="chain-two",
        occurrence=12,
        user_message_id="chain-two-user",
        user_content="回顾季度看板的传递结论。",
        assistants=[
            (
                "chain-two-assistant",
                "传递结论仍沿用环形布局。",
                transitive,
            )
        ],
    )
    partial_id, _ = _ingest(
        runtime,
        conversation_id="partial",
        occurrence=13,
        user_message_id="partial-user",
        user_content="回顾季度看板的两个备选说明。",
        assistants=[
            (
                "partial-stale",
                "依赖说明沿用环形布局。",
                _world_dependency("board-world"),
            ),
            (
                "partial-independent",
                "独立建议是先做只读原型。",
                dict(EMPTY),
            ),
        ],
    )
    with sqlite3.connect(runtime.db_path) as db:
        db.execute(
            "UPDATE cognition SET invalid_at = '2026-09-23T00:02:00Z' "
            "WHERE id = 'board-world'"
        )

    with pytest.raises(InteractionQueryError, match="interaction_not_found"):
        runtime.query_interaction(second_id, projection="model")
    transitive_history = runtime.query_interaction(second_id, projection="history")
    assert transitive_history["item"]["dependency_state"] == "stale"

    partial_model = runtime.query_interaction(partial_id, projection="model")
    assert partial_model["item"]["dependency_state"] == "partial"
    assert [turn["message_id"] for turn in partial_model["item"]["turns"]] == [
        "partial-user",
        "partial-independent",
    ]
    assert "环形布局" not in partial_model["item"]["turns"][1]["content"]
    partial_history = runtime.query_interaction(partial_id, projection="history")
    assert [turn["message_id"] for turn in partial_history["item"]["turns"]] == [
        "partial-user",
        "partial-stale",
        "partial-independent",
    ]
    runtime.shutdown()


def test_complete_empty_is_retained_and_legacy_unknown_is_history_only(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    complete_id, _ = _ingest(
        runtime,
        conversation_id="independent",
        occurrence=20,
        user_message_id="independent-user",
        user_content="回顾独立排期建议。",
        assistants=[
            (
                "independent-assistant",
                "独立建议是先安排一次短评审。",
                dict(EMPTY),
            )
        ],
    )
    legacy_id, _ = _ingest(
        runtime,
        conversation_id="legacy",
        occurrence=21,
        user_message_id="legacy-user",
        user_content="回顾旧版排期建议。",
        assistants=[
            (
                "legacy-assistant",
                "旧版建议是直接安排长评审。",
                None,
            )
        ],
    )
    assert runtime.query_interaction(complete_id, projection="model")["item"][
        "turns"
    ][1]["content"] == "独立建议是先安排一次短评审。"
    with pytest.raises(InteractionQueryError, match="interaction_not_found"):
        runtime.query_interaction(legacy_id, projection="model")
    history = runtime.query_interaction(legacy_id, projection="history")
    assert history["item"]["dependency_state"] == "legacy_unknown"
    assert history["item"]["turns"][1]["content"] == (
        "旧版建议是直接安排长评审。"
    )
    runtime.shutdown()


def test_history_does_not_restore_deleted_or_unreadable_episode(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    interaction_id, _ = _ingest(
        runtime,
        conversation_id="permission-history",
        occurrence=22,
        user_message_id="permission-user",
        user_content="回顾权限测试记录。",
        assistants=[
            (
                "permission-assistant",
                "权限测试响应。",
                dict(EMPTY),
            )
        ],
    )
    assert runtime.query_interaction(interaction_id, projection="history")["item"]
    assert runtime.query_interactions(
        "权限", projection="history", search_mode="history_search"
    )["count"] == 1
    assert runtime.db_path is not None
    with sqlite3.connect(runtime.db_path) as db:
        episode_id = str(
            db.execute(
                "SELECT episode_id FROM interaction_context WHERE id = ?",
                (interaction_id,),
            ).fetchone()[0]
        )
        evidence_id = json.loads(
            db.execute(
                "SELECT evidence_ids_json FROM memory_world_job "
                "WHERE boundary_event_id = ?",
                (episode_id,),
            ).fetchone()[0]
        )[0]
        db.execute(
            "UPDATE evidence SET allow_local_read = 0 WHERE id = ?",
            (evidence_id,),
        )
    with pytest.raises(InteractionQueryError, match="interaction_not_found"):
        runtime.query_interaction(interaction_id, projection="history")
    assert runtime.query_interactions(
        "权限", projection="history", search_mode="history_search"
    )["count"] == 0
    with sqlite3.connect(runtime.db_path) as db:
        db.execute(
            "UPDATE evidence SET allow_local_read = 1, deleted_at = ? WHERE id = ?",
            ("2026-09-23T00:00:00Z", evidence_id),
        )
    with pytest.raises(InteractionQueryError, match="interaction_not_found"):
        runtime.query_interaction(interaction_id, projection="history")
    runtime.shutdown()


def test_cycle_depth_and_node_limits_fail_closed(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    a_id, a_hash = _ingest(
        runtime,
        conversation_id="cycle-a",
        occurrence=30,
        user_message_id="cycle-a-user",
        user_content="检查循环节点甲。",
        assistants=[("cycle-a-assistant", "循环节点甲响应。", None)],
    )
    b_id, b_hash = _ingest(
        runtime,
        conversation_id="cycle-b",
        occurrence=31,
        user_message_id="cycle-b-user",
        user_content="检查循环节点乙。",
        assistants=[("cycle-b-assistant", "循环节点乙响应。", None)],
    )
    assert runtime.link_interaction_dependencies(
        conversation_id="cycle-a",
        user_message_id="cycle-a-user",
        assistant_message_id="cycle-a-assistant",
        expected_context_hash=a_hash,
        model_context_dependencies={
            "schema_version": 1,
            "capture_status": "complete",
            "world_items": [],
            "interaction_ids": [b_id],
        },
    )["result_state"] == "applied"
    assert runtime.link_interaction_dependencies(
        conversation_id="cycle-b",
        user_message_id="cycle-b-user",
        assistant_message_id="cycle-b-assistant",
        expected_context_hash=b_hash,
        model_context_dependencies={
            "schema_version": 1,
            "capture_status": "complete",
            "world_items": [],
            "interaction_ids": [a_id],
        },
    )["result_state"] == "applied"
    assert runtime.query_interaction(a_id, projection="history")["item"][
        "dependency_state"
    ] == "cycle"
    with pytest.raises(InteractionQueryError, match="interaction_not_found"):
        runtime.query_interaction(a_id, projection="model")

    child_id, _ = _ingest(
        runtime,
        conversation_id="depth-leaf",
        occurrence=40,
        user_message_id="depth-leaf-user",
        user_content="深度叶节点。",
        assistants=[("depth-leaf-assistant", "叶节点响应。", dict(EMPTY))],
    )
    for index in range(9):
        child_id, _ = _ingest(
            runtime,
            conversation_id=f"depth-{index}",
            occurrence=41 + index,
            user_message_id=f"depth-{index}-user",
            user_content=f"深度节点 {index}。",
            assistants=[
                (
                    f"depth-{index}-assistant",
                    f"深度节点 {index} 响应。",
                    {
                        "schema_version": 1,
                        "capture_status": "complete",
                        "world_items": [],
                        "interaction_ids": [child_id],
                    },
                )
            ],
        )
    assert runtime.query_interaction(child_id, projection="history")["item"][
        "dependency_state"
    ] == "depth_limit"

    leaves: list[str] = []
    for index in range(64):
        leaf_id, _ = _ingest(
            runtime,
            conversation_id=f"node-leaf-{index}",
            occurrence=100 + index,
            user_message_id=f"node-leaf-{index}-user",
            user_content=f"节点预算叶 {index}。",
            assistants=[
                (
                    f"node-leaf-{index}-assistant",
                    f"节点预算叶 {index} 响应。",
                    dict(EMPTY),
                )
            ],
        )
        leaves.append(leaf_id)
    root_id, _ = _ingest(
        runtime,
        conversation_id="node-root",
        occurrence=200,
        user_message_id="node-root-user",
        user_content="节点预算根。",
        assistants=[
            (
                "node-root-assistant",
                "节点预算根响应。",
                {
                    "schema_version": 1,
                    "capture_status": "complete",
                    "world_items": [],
                    "interaction_ids": leaves,
                },
            )
        ],
    )
    assert runtime.query_interaction(root_id, projection="history")["item"][
        "dependency_state"
    ] == "node_limit"
    with pytest.raises(InteractionQueryError, match="interaction_not_found"):
        runtime.query_interaction(root_id, projection="model")
    runtime.shutdown()


def test_commitments_share_their_assistant_dependency_gate(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    source_id, _ = _ingest(
        runtime,
        conversation_id="commit-source",
        occurrence=300,
        user_message_id="commit-source-user",
        user_content="登记承诺来源 C3。",
    )
    assert runtime.db_path is not None
    with sqlite3.connect(runtime.db_path) as db:
        source_episode = str(
            db.execute(
                "SELECT episode_id FROM interaction_context WHERE id = ?", (source_id,)
            ).fetchone()[0]
        )
    _world_cognition(
        runtime,
        episode_id=source_episode,
        item_id="commit-world",
        content="发布流程采用双人复核",
    )
    _ingest(
        runtime,
        conversation_id="commit-discussion",
        occurrence=301,
        user_message_id="commit-user",
        user_content="回顾发布流程建议。",
        assistants=[
            (
                "commit-assistant",
                "我建议采用双人复核流程。",
                _world_dependency("commit-world"),
            )
        ],
    )
    before = runtime.query_interactions("你之前给过什么发布建议？", projection="model")
    assert before["commitment_count"] == 1
    assert "双人复核" in before["rendered_context"]
    with sqlite3.connect(runtime.db_path) as db:
        db.execute(
            "UPDATE cognition SET invalid_at = '2026-09-23T00:03:00Z' "
            "WHERE id = 'commit-world'"
        )
    model = runtime.query_interactions("你之前给过什么发布建议？", projection="model")
    history = runtime.query_interactions("你之前给过什么发布建议？", projection="history")
    assert model["commitment_count"] == 0
    assert "双人复核" not in model["rendered_context"]
    assert history["commitment_count"] == 1
    assert history["commitments"][0]["dependency_state"] == "stale"
    runtime.shutdown()


def _request(request_id: str, method: str, params: dict[str, object]) -> dict[str, object]:
    return {
        "protocol": "memoweft.dsh_rpc",
        "protocol_version": 2,
        "schema_version": 1,
        "request_id": request_id,
        "method": method,
        "params": params,
    }


def test_exact_history_enumeration_and_link_rpc_cas(tmp_path: Path) -> None:
    server = DshRpcV2Server()
    initialized = server.handle(
        _request(
            "initialize-link",
            "initialize",
            {
                "session_id": "active",
                "dsh_home": str(tmp_path),
                "auto_route": False,
                "model_tier": "local",
            },
        )
    )
    assert initialized["result"]["capabilities"][
        "interaction_dependency_projection"
    ] == 1
    interaction_id, old_hash = _ingest(
        server.runtime,
        conversation_id="legacy-session",
        occurrence=400,
        user_message_id="legacy-user-400",
        user_content="旧记录精确枚举。",
        assistants=[("legacy-assistant-400", "旧记录响应。", None)],
    )
    exact = server.handle(
        _request(
            "exact-history",
            "query_interactions",
            {
                "conversation_id": "legacy-session",
                "user_message_id": "legacy-user-400",
                "projection": "history",
            },
        )
    )
    assert exact["ok"] is True
    assert exact["result"]["count"] == 1
    item = exact["result"]["items"][0]
    assert item["id"] == interaction_id
    assert item["context_hash"] == old_hash
    assert item["user_message_id"] == "legacy-user-400"
    assert item["assistant_message_id"] == "legacy-assistant-400"

    dependencies = dict(EMPTY)
    params = {
        "conversation_id": "legacy-session",
        "user_message_id": "legacy-user-400",
        "assistant_message_id": "legacy-assistant-400",
        "expected_context_hash": old_hash,
        "model_context_dependencies": dependencies,
    }
    applied = server.handle(
        _request("link-applied", "link_interaction_dependencies", params)
    )
    assert applied["ok"] is True
    assert applied["result"] == {
        "interaction_id": interaction_id,
        "old_context_hash": old_hash,
        "context_hash": applied["result"]["context_hash"],
        "result_state": "applied",
    }
    assert applied["result"]["context_hash"] != old_hash

    no_change = server.handle(
        _request("link-idempotent", "link_interaction_dependencies", params)
    )
    assert no_change["result"]["result_state"] == "no_change"
    assert no_change["result"]["context_hash"] == applied["result"]["context_hash"]

    conflicting = {**params, "model_context_dependencies": {**EMPTY, "capture_status": "withheld"}}
    conflict = server.handle(
        _request("link-conflict", "link_interaction_dependencies", conflicting)
    )
    assert conflict["result"]["result_state"] == "conflict"
    wrong_message = server.handle(
        _request(
            "link-wrong-message",
            "link_interaction_dependencies",
            {**params, "assistant_message_id": "another-assistant"},
        )
    )
    assert wrong_message["result"]["result_state"] == "not_found"

    assert server.runtime.db_path is not None
    with sqlite3.connect(server.runtime.db_path) as db:
        other = SqliteInteractionContextStore(db).record(
            InteractionContextInput(
                subject_id="another-subject",
                conversation_id="foreign-session",
                episode_id="foreign-episode",
                context=[
                    VisibleTurn(
                        role="user",
                        content="外部记录。",
                        message_id="foreign-user",
                    ),
                    VisibleTurn(
                        role="assistant",
                        content="外部响应。",
                        message_id="foreign-assistant",
                    ),
                ],
            )
        )
    wrong_subject = server.handle(
        _request(
            "link-wrong-subject",
            "link_interaction_dependencies",
            {
                "conversation_id": "foreign-session",
                "user_message_id": "foreign-user",
                "assistant_message_id": "foreign-assistant",
                "expected_context_hash": other.context_hash,
                "model_context_dependencies": dependencies,
            },
        )
    )
    assert wrong_subject["result"]["result_state"] == "not_found"
    server.runtime.shutdown()


@pytest.mark.parametrize(
    "bad_dependencies",
    [
        {
            "schema_version": 1,
            "capture_status": "complete_empty",
            "world_items": [{"object_kind": "cognition", "item_id": "x"}],
            "interaction_ids": [],
        },
        {
            "schema_version": 1,
            "capture_status": "complete",
            "world_items": [
                {"object_kind": "cognition", "item_id": "x", "extra": True}
            ],
            "interaction_ids": [],
        },
        {
            "schema_version": 1,
            "capture_status": "complete_empty",
            "world_items": [],
            "interaction_ids": [],
            "world_revision": True,
        },
        {
            "schema_version": 1,
            "capture_status": "complete",
            "world_items": [],
            "interaction_ids": ["x" * 513],
        },
    ],
)
def test_boundary_rejects_non_strict_dependency_dto(
    tmp_path: Path, bad_dependencies: dict[str, object]
) -> None:
    runtime = _runtime(tmp_path)
    boundary = _boundary(
        "invalid-dependency",
        500,
        [
            {
                "role": "user",
                "content": "验证依赖边界。",
                "message_id": "invalid-user",
            },
            {
                "role": "assistant",
                "content": "边界响应。",
                "message_id": "invalid-assistant",
                "model_context_dependencies": bad_dependencies,
            },
        ],
    )
    with pytest.raises(DshBoundaryError, match="model_context_dependencies is invalid"):
        runtime.ingest_durable_boundary(boundary)
    runtime.shutdown()


def test_boundary_rejects_dependency_on_user_role(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    boundary = _boundary(
        "invalid-role",
        501,
        [
            {
                "role": "user",
                "content": "用户消息。",
                "message_id": "invalid-role-user",
                "model_context_dependencies": dict(EMPTY),
            }
        ],
    )
    with pytest.raises(DshBoundaryError, match="assistant-only"):
        runtime.ingest_durable_boundary(boundary)
    runtime.shutdown()
