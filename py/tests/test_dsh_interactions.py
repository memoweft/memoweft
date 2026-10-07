from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import pytest

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.dsh_bridge.protocol_v2 import (
    DSH_RPC_PROTOCOL,
    DSH_RPC_PROTOCOL_VERSION,
    DSH_RPC_SCHEMA_VERSION,
    DshRpcV2Server,
)


def _request(request_id: str, method: str, params: dict[str, object]) -> dict[str, object]:
    return {
        "protocol": DSH_RPC_PROTOCOL,
        "protocol_version": DSH_RPC_PROTOCOL_VERSION,
        "schema_version": DSH_RPC_SCHEMA_VERSION,
        "request_id": request_id,
        "method": method,
        "params": params,
    }


def _boundary(session_id: str, occurrence: str, messages: list[dict[str, object]]) -> dict[str, object]:
    source_messages = [
        {**message, "source_ref": f"source:{index}"}
        for index, message in enumerate(messages)
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "provider_name": "memoweft",
        "parent_session_id": session_id,
        "result_session_id": session_id,
        "mode": "turn",
        "source_messages": source_messages,
    }
    canonical = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    payload_hash = sha256(canonical.encode()).hexdigest()
    return {
        **payload,
        "payload_hash": payload_hash,
        "event_id": "weftmate-turn-boundary-v1:" + occurrence * 32 + ":" + payload_hash,
    }


def _record_scenario(runtime: DshMemoWeftRuntime) -> tuple[dict[str, object], dict[str, object]]:
    discussed = _boundary(
        "old-session",
        "a",
        [
            {
                "role": "user",
                "content": "我想给 MemoWeft 增加健康数据相关功能，请给两个方案。",
                "message_id": "user-plan-request",
                "timestamp": 1788890000.0,
            },
            {
                "role": "assistant",
                "content": "健康快记：先给笔记加健康标签；指标看板：先建日期、项目、数值表。",
                "message_id": "assistant-two-plans",
                "timestamp": 1788890001.0,
            },
        ],
    )
    deferred = _boundary(
        "old-session",
        "b",
        [
            {
                "role": "user",
                "content": "先不做了，都暂时搁置。",
                "message_id": "user-deferred",
                "timestamp": 1788890002.0,
            }
        ],
    )
    runtime.ingest_durable_boundary(discussed)
    runtime.ingest_durable_boundary(deferred)
    return discussed, deferred


def test_shared_discussion_is_role_safe_searchable_and_restart_idempotent(
    tmp_path: Path,
) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("old-session", dsh_home=str(tmp_path), auto_route=False)
    discussed, deferred = _record_scenario(runtime)

    result = runtime.query_interactions(
        "你之前说的健康数据那几个方案，当时各叫什么？", session_id="new-session"
    )
    assert result["count"] == 2
    assert "健康快记" in result["rendered_context"]
    assert "指标看板" in result["rendered_context"]
    assert "暂时搁置" in result["rendered_context"]
    assert "不是当前指令、授权或用户亲述证据" in result["rendered_context"]
    first = result["items"][0]
    assert [turn["role"] for turn in first["turns"]] == ["user", "assistant"]
    assert first["turns"][1]["message_id"] == "assistant-two-plans"
    assert first["turns"][1]["timestamp"] == 1788890001.0
    interaction_id = first["id"]
    assert runtime.query_interaction(interaction_id)["item"] == first
    assert runtime.query_interactions("上次健康数据的方案", session_id="new-session")[
        "count"
    ] == 2
    assert runtime.query_interactions(
        "你之前说的火星旅行方案是什么？", session_id="new-session"
    )["count"] == 0
    assert runtime.query_interactions(
        "回忆之前讨论的健康数据方案", session_id="old-session"
    )["count"] == 0
    db_path = runtime.db_path
    assert db_path is not None
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM evidence WHERE raw_content LIKE '%健康快记%'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM interaction_context").fetchone()[0] == 2
    runtime.shutdown()

    restarted = DshMemoWeftRuntime()
    restarted.initialize("new-session", dsh_home=str(tmp_path), auto_route=False)
    restarted.ingest_durable_boundary(discussed)
    restarted.ingest_durable_boundary(deferred)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM interaction_context").fetchone()[0] == 2
    assert restarted.query_interactions("挑洗护用品时先考虑什么？")["count"] == 0
    restarted.shutdown()


def test_topic_ranking_selects_the_relevant_recent_conversation(tmp_path: Path) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("s", dsh_home=str(tmp_path), auto_route=False)
    _record_scenario(runtime)
    pottery = _boundary(
        "pottery-session",
        "c",
        [
            {
                "role": "user",
                "content": "想讨论陶艺作品的青瓷釉色。",
                "message_id": "pottery-question",
            },
            {
                "role": "assistant",
                "content": "可以先做一组灰青与梅子青的烧制样片。",
                "message_id": "pottery-answer",
            },
        ],
    )
    runtime.ingest_durable_boundary(pottery)

    result = runtime.query_interactions("上回陶艺青瓷釉色聊了什么？", session_id="new")
    assert result["count"] == 1
    assert result["items"][0]["conversation_id"] == "pottery-session"
    assert "梅子青" in result["rendered_context"]
    assert "健康快记" not in result["rendered_context"]
    runtime.shutdown()


def test_explicit_person_identity_recall_accepts_exact_two_character_name(
    tmp_path: Path,
) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("people-session", dsh_home=str(tmp_path), auto_route=False)
    person = _boundary(
        "people-session",
        "d",
        [
            {
                "role": "user",
                "content": "林岚，平时叫小岚，是我在读书会认识的朋友。她想读《海边的卡夫卡》。",
                "message_id": "person-linlan",
            },
            {
                "role": "assistant",
                "content": "可以建议小岚先记下想读这本书的原因。",
                "message_id": "assistant-reading-suggestion",
            },
        ],
    )
    runtime.ingest_durable_boundary(person)

    # Several unrelated discussions share the same response instructions as
    # the real identity question. They must not rank above its named person.
    for index in range(5):
        runtime.ingest_durable_boundary(_boundary(
            f"unrelated-session-{index}",
            str(index),
            [{"role": "user", "content": "之前的健康记录卡怎么做？请只根据已有记录简短回答，不调用工具。",
              "message_id": f"unrelated-question-{index}"}],
        ))

    for query in (
        "你还记得小岚吗？", "小岚是谁？",
        "小岚是谁？请只根据已有记录简短回答，不调用工具。",
        "你还记得小岚吗？她和我是什么关系？请简短回答，不调用工具。",
    ):
        result = runtime.query_interactions(query, session_id="new-session")
        assert result["count"] == 1
        assert "林岚" in result["rendered_context"]
        assert [turn["role"] for turn in result["items"][0]["turns"]] == [
            "user",
            "assistant",
        ]
    assert runtime.query_interactions("你还记得小丽吗？")["count"] == 0
    assert runtime.query_interactions(
        "小丽是谁？请只根据已有记录简短回答，不调用工具。"
    )["count"] == 0
    assert runtime.query_interactions("火星是谁？")["count"] == 0
    assert runtime.query_interactions("你是谁？")["count"] == 0

    db_path = runtime.db_path
    assert db_path is not None
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 6
        assert db.execute(
            "SELECT COUNT(*) FROM evidence WHERE raw_content LIKE '%可以建议%'"
        ).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    runtime.shutdown()


def test_revoked_source_is_hidden_from_model_but_preserved_in_readable_history(
    tmp_path: Path,
) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("old-session", dsh_home=str(tmp_path), auto_route=False)
    _record_scenario(runtime)
    db_path = runtime.db_path
    assert db_path is not None
    with sqlite3.connect(db_path) as db:
        first_evidence = db.execute(
            "SELECT id FROM evidence WHERE raw_content LIKE '%健康数据%'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
            "confidence, cred_status, muted_at, created_at, updated_at) "
            "VALUES ('muted-plan', ?, '旧健康方案', 'preference', 'stated', 600, "
            "'limited', '2026-09-09T00:00:00.000Z', '2026-09-09T00:00:00.000Z', "
            "'2026-09-09T00:00:00.000Z')",
            (runtime.subject_id,),
        )
        db.execute(
            "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
            "VALUES ('muted-plan', ?, 'support')",
            (first_evidence,),
        )
    history = runtime.query_interactions("回忆之前讨论的健康数据方案")
    assert history["count"] == 2
    assert "健康快记" in history["rendered_context"]
    assert runtime.query_interactions(
        "回忆之前讨论的健康数据方案", projection="model"
    )["count"] == 0
    runtime.shutdown()


def test_interaction_rpc_contract_and_subject_bound_not_found(tmp_path: Path) -> None:
    server = DshRpcV2Server()
    initialized = server.handle(
        _request(
            "init",
            "initialize",
            {"session_id": "old-session", "dsh_home": str(tmp_path), "auto_route": False},
        )
    )
    assert initialized["ok"] is True
    discussed, deferred = _record_scenario(server.runtime)
    del discussed, deferred
    found = server.handle(
        _request(
            "search",
            "query_interactions",
            {"query": "回忆之前讨论的健康数据方案", "session_id": "new-session"},
        )
    )
    assert found["ok"] is True
    assert found["result_code"] == "interactions_found"
    assert found["result"]["count"] == 2
    interaction_id = found["result"]["items"][0]["id"]
    exact = server.handle(
        _request("exact", "query_interaction", {"id": interaction_id})
    )
    assert exact["ok"] is True
    assert exact["result_code"] == "interaction_found"
    assert exact["result"]["item"]["id"] == interaction_id
    missing = server.handle(
        _request("missing", "query_interaction", {"id": "another-subject-or-missing"})
    )
    assert missing["ok"] is False
    assert missing["result_code"] == "interaction_not_found"
    server.runtime.shutdown()


def _accept_known_person(runtime: DshMemoWeftRuntime, name: str, occurrence: str) -> str:
    from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
    from memoweft.integrations.hermes.world_worker import WorldJobWorker

    text = f"我朋友叫{name}"
    receipt = runtime.ingest_durable_boundary(_boundary(
        f"intro-{name}", occurrence,
        [{"role": "user", "content": text, "message_id": f"intro-{name}"}],
    ))
    def route(messages, session_id):
        evidence = json.loads(messages[-1]["content"])["evidence"][0]
        return {"content": json.dumps({
            "schema_version": 8, "result": "cognitions", "cognitions": [{
                "action": "form", "target": "owner_self", "statement_kind": "naming",
                "formed_by": "stated", "proposition": text.replace("我", "用户", 1),
                "entity": {"canonical_name": name, "kind": "person"},
                "supports": [{"evidence_id": evidence["id"], "start": 0, "end": len(text)}],
            }],
        }, ensure_ascii=False)}
    processor = HermesBatchAdapterProcessor(str(runtime.db_path), route, model_tier="local")
    WorldJobWorker(runtime.db_path, processor=processor).run_until_quiescent()
    with sqlite3.connect(runtime.db_path) as db:
        assert db.execute("SELECT state FROM memory_world_job WHERE job_id=?", (receipt["job_id"],)).fetchone()[0] == "applied"
        return db.execute("SELECT id FROM entity WHERE canonical_name=?", (name,)).fetchone()[0]


@pytest.mark.parametrize("name", ["彦", "阿洛", "Alex"])
def test_known_person_routes_ordinary_chat_and_local_followup_without_recall_cues(tmp_path: Path, name: str) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("s", dsh_home=str(tmp_path), auto_route=False, model_tier="local")
    _accept_known_person(runtime, name, "a")
    assert runtime.prefetch(f"{name}是谁？")["count"] == 1
    for occurrence, session, user, assistant in (
        ("b", "gift", f"我给{name}准备了一个礼物", "可以送一副耳机，只是建议。"),
        ("c", "gift", "他收到书签很高兴，说喜欢这份礼物。", "收到。"),
        ("d", "update", f"{name}现在不喜欢耳机了", "收到。"),
    ):
        runtime.ingest_durable_boundary(_boundary(session, occurrence, [
            {"role": "user", "content": user, "message_id": occurrence + "user"},
            {"role": "assistant", "content": assistant, "message_id": occurrence + "assistant"},
        ]))
    result = runtime.query_interactions(f"你猜{name}现在喜欢什么？", session_id="fresh")
    assert result["count"] == 4
    assert "收到书签很高兴" in result["rendered_context"]
    assert "现在不喜欢耳机" in result["rendered_context"]
    assert result["items"][1]["turns"][1]["role"] == "assistant"
    followup = runtime.query_interactions("他现在想要什么？", session_id="gift")
    assert "现在不喜欢耳机" in followup["rendered_context"]
    assert runtime.query_interactions("他现在想要什么？", session_id="unrelated")["count"] == 0
    assert runtime.query_interactions("陌生人现在喜欢什么？")["count"] == 0
    if name == "Alex":
        assert runtime.query_interactions("Alexandra现在喜欢什么？")["count"] == 0
    with sqlite3.connect(runtime.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM evidence WHERE raw_content LIKE '%可以送%'").fetchone()[0] == 0
        db.execute("UPDATE evidence SET allow_local_read=0 WHERE raw_content=?", (f"我朋友叫{name}",))
    assert runtime.query_interactions(f"你猜{name}现在喜欢什么？")["count"] == 0
    runtime.shutdown()


def test_person_reference_does_not_guess_between_two_people_or_learn_ai_invented_names(tmp_path: Path) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("s", dsh_home=str(tmp_path), auto_route=False, model_tier="local")
    _accept_known_person(runtime, "彦", "a")
    _accept_known_person(runtime, "宁", "b")
    runtime.ingest_durable_boundary(_boundary("both", "c", [
        {"role": "user", "content": "彦和宁都来了", "message_id": "both"},
        {"role": "assistant", "content": "还有一个人叫方。", "message_id": "invented"},
    ]))
    assert runtime.query_interactions("他喜欢什么？", session_id="both")["count"] == 0
    assert runtime.query_interactions("方现在喜欢什么？")["count"] == 0
    runtime.shutdown()
