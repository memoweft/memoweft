"""V1 deterministic Recall: read-only accepted World, zero model calls."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, cast

import pytest

from memoweft.integrations.hermes import (
    HermesMemoWeftRuntime,
    _boundary_payload_hash,
)
from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor,
)
from memoweft.integrations.hermes.recall import (
    _match_world_rows,
    _strip_question_particles,
    format_recall,
    match_cognitions,
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import MutableClock, _job, _policy

_RAW = "用户平时更喜欢冰美式。"


@pytest.mark.parametrize(("query", "expected"), [
    ("喜欢咖啡了吗", "喜欢咖啡"),
    ("喜欢咖啡了么", "喜欢咖啡"),
    ("喜欢咖啡了没", "喜欢咖啡"),
    ("喜欢咖啡没有", "喜欢咖啡"),
    ("喜欢咖啡吗呢吧呀啊啦了?？", "喜欢咖啡"),
    ("喜欢什么吗", "喜欢什么"),
    ("怎么了？", "怎么"),
    ("么", ""),
    ("", ""),
    ("喜欢咖啡了么x", "喜欢咖啡了么x"),
])
def test_question_particles_preserve_topic_words(query: str, expected: str) -> None:
    assert _strip_question_particles(query) == expected


def test_question_particles_handle_adversarial_repetitions_without_backtracking() -> None:
    # Isolate the security regression so a reintroduced vulnerable regex fails
    # within a bounded time rather than hanging the whole test process.
    subprocess.run(
        [sys.executable, "-c", (
            "from memoweft.integrations.hermes.recall import _strip_question_particles; "
            "suffix = '了么' * 100_000; "
            "assert _strip_question_particles('咖啡' + suffix + 'x') == '咖啡' + suffix + 'x'; "
            "assert _strip_question_particles('咖啡' + suffix) == '咖啡'"
        )],
        check=True,
        timeout=10,
    )


def test_named_person_recall_does_not_mix_owner_or_other_person_preferences() -> None:
    rows = [
        {"kind": "cognition", "id": "owner", "content": "用户喜欢阅读", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "yan", "content": "彦：他喜欢阅读", "confidence": 600, "anchors": ("彦",)},
        {"kind": "cognition", "id": "ning", "content": "宁：她喜欢阅读", "confidence": 600, "anchors": ("宁",)},
    ]
    assert [item["id"] for item in _match_world_rows("你猜彦现在喜欢什么？", rows)] == ["yan"]
    assert {item["id"] for item in _match_world_rows("彦和宁喜欢什么？", rows)} == {"yan", "ning"}


def test_person_recall_keeps_identity_and_other_current_facts_beyond_literal_overlap() -> None:
    rows = [
        {"kind": "cognition", "id": "identity", "statement_kind": "naming", "content": "用户在大学认识的朋友叫彦", "confidence": 600, "anchors": ("彦",)},
        {"kind": "cognition", "id": "changed", "content": "彦：他现在不喜欢阅读了", "confidence": 600, "anchors": ("彦",)},
        {"kind": "cognition", "id": "positive", "content": "彦：他也很喜欢周末和朋友们慢慢拼完一幅大型拼图", "confidence": 600, "anchors": ("彦",)},
    ]
    found = _match_world_rows("你猜彦现在喜欢什么？", rows)
    assert found[0]["id"] == "identity"
    assert {item["id"] for item in found} == {"identity", "changed", "positive"}


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


def test_long_chinese_question_recalls_modified_object_without_topic_leakage() -> None:
    rows = [
        {"kind": "cognition", "id": "cup", "content": "用户喜欢合成蓝色茶杯", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "book", "content": "用户喜欢合成红色书本", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "plate", "content": "用户喜欢绿色茶盘", "confidence": 600, "anchors": ()},
    ]
    query = (
        "请从本次会话可用的账户记忆中找出我喜欢的合成茶杯颜色，"
        "只回答颜色的两个汉字；若没有相关记忆，只回答未知。不调用工具。"
    )
    assert [item["id"] for item in _match_world_rows(query, rows)] == ["cup"]
    assert [item["id"] for item in match_cognitions(query, rows)] == ["cup"]
    unrelated = query.replace("合成茶杯", "合成花瓶")
    assert _match_world_rows(unrelated, rows) == []
    assert match_cognitions(unrelated, rows) == []


def test_match_cognitions_uses_explicit_natural_language_cues_without_long_query_dilution() -> None:
    rows = [
        {
            "id": "c-free",
            "content": "用户是向往自由本身的",
            "confidence": 640,
            "match_text": "用户是向往自由本身的 云",
        },
        {
            "id": "c-coffee",
            "content": "用户平时喜欢喝咖啡",
            "confidence": 640,
        },
    ]

    query = "关于“云”和“自由”，你记得我什么？"
    first = match_cognitions(query, rows)
    second = match_cognitions(query, rows)

    assert [item["id"] for item in first] == ["c-free"]
    assert second == first
    assert [
        item["id"]
        for item in match_cognitions(
            "这是全新会话。请回答：关于云和自由，你记得我什么？", rows
        )
    ] == ["c-free"]
    owner_weixin_query = "关于云和自由你还记得什么？"
    owner_first = match_cognitions(owner_weixin_query, rows)
    owner_second = match_cognitions(owner_weixin_query, rows)
    assert [item["id"] for item in owner_first] == ["c-free"]
    assert owner_second == owner_first
    assert [
        item["id"] for item in match_cognitions("关于咖啡你还记得什么？", rows)
    ] == ["c-coffee"]
    assert match_cognitions("关于路况你还记得什么？", rows) == []
    assert match_cognitions("你还记得什么？", rows) == []
    assert match_cognitions("关于路况，你记得我什么？", rows) == []


def test_match_cognitions_ignores_wrapper_phrases_and_entity_substrings() -> None:
    rows = [
        {
            "id": "c-answer-style",
            "content": "用户要求回答简洁",
            "confidence": 640,
        },
        {
            "id": "c-cloud",
            "content": "用户偏好晨跑",
            "confidence": 640,
            "match_text": "用户偏好晨跑 云",
            "anchors": ("云",),
        },
        {
            "id": "c-xiaowang",
            "content": "小王很守时",
            "confidence": 640,
            "anchors": ("小王",),
        },
    ]

    assert match_cognitions("请回答今天的安排", rows) == []
    assert match_cognitions("请回答：今天天气如何？", rows) == []
    assert match_cognitions("云计算行业趋势", rows) == []
    assert match_cognitions("小王子的故事", rows) == []
    assert [item["id"] for item in match_cognitions("关于云你还记得什么？", rows)] == [
        "c-cloud"
    ]
    assert [item["id"] for item in match_cognitions("小王最近怎么样？", rows)] == [
        "c-xiaowang"
    ]


def test_match_cognitions_can_fall_back_to_a_linked_entity_name() -> None:
    rows = [
        {
            "id": "c-mother",
            "content": "妈妈是个善良的人",
            "confidence": 640,
            "anchors": ("妈妈",),
        },
        {
            "id": "c-friend",
            "content": "小王很守时",
            "confidence": 640,
            "anchors": ("小王",),
        },
    ]

    assert [
        item["id"] for item in match_cognitions("你觉得我妈妈人怎么样", rows)
    ] == ["c-mother"]


def test_match_cognitions_uses_known_entity_terms_without_inference() -> None:
    rows = [
        {"id": "c-mother", "content": "妈妈是个善良的人", "confidence": 640},
        {"id": "c-coffee", "content": "用户喜欢咖啡", "confidence": 640},
    ]

    assert [
        item["id"] for item in match_cognitions("你觉得我妈妈人怎么样", rows)
    ] == ["c-mother"]


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
        assert runtime._ingestor is not None
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
        # Permission gate (v15 recall): the row must carry a visible Evidence
        # link to be recallable — complete the physical fixture.
        db.execute(
            "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
            "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
            "allow_cloud_read, allow_inference) VALUES "
            "('ev-we', ?, 'spoken', 'hermes:test', 't', 't', "
            "'上周末我和小王去了南京', '上周末我和小王去了南京', 1, 1, 1)",
            (subject,),
        )
        db.execute(
            "INSERT OR IGNORE INTO world_event_evidence "
            "(world_event_id, evidence_id, relation) VALUES ('we-1', 'ev-we', 'support')"
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

def test_relationship_particle_and_relation_type_recall(tmp_path: Path) -> None:
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
        assert runtime._ingestor is not None
        subject = str(runtime._ingestor.subject_id)
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO relationship (id, world_id, source_entity_id, target_entity_id, "
            "relation_type, content, formed_by, confidence, cred_status, invalid_at, created_at, updated_at) "
            "VALUES ('rel-1', ?, 'ent-user', 'ent-xw', 'girlfriend', '用户刚和小王在一起了', 'stated', 600, 'limited', NULL, 't', 't')",
            (subject,),
        )
        db.execute(
            "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
            "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
            "allow_cloud_read, allow_inference) VALUES "
            "('ev-rel', ?, 'spoken', 'hermes:test', 't', 't', "
            "'我和小王在一起了', '用户和小王在一起了', 1, 1, 1)",
            (subject,),
        )
        db.execute(
            "INSERT OR IGNORE INTO relationship_evidence "
            "(relationship_id, evidence_id, relation) VALUES ('rel-1', 'ev-rel', 'support')"
        )
        db.commit()
        db.close()

        # Query using particle suffix & question form:
        text1 = runtime.prefetch("我脱单了吗", session_id="sess")
        assert "小王" in text1

        # Query using relation synonym ("女朋友"):
        text2 = runtime.prefetch("你猜我有没有女朋友", session_id="sess")
        assert "小王" in text2
    finally:
        runtime.shutdown()
