from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest
from support.json_assertions import as_object, as_objects

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.dsh_bridge.protocol_v2 import DshRpcV2Server
from memoweft.integrations.trust.query_service import QueryService
from memoweft.integrations.trust.command_service import CommandService
from memoweft.integrations.trust.true_delete import erase_conversation_context
from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from test_dsh_interactions import _boundary, _request
from test_formation_accuracy import _run, _item
from test_hermes_batch_adapter_v5 import _batch, _model


def _db_path(runtime: DshMemoWeftRuntime) -> Path:
    assert runtime.db_path is not None
    return runtime.db_path


def _subject(runtime: DshMemoWeftRuntime) -> str:
    assert runtime.subject_id is not None
    return runtime.subject_id


def _host(runtime: DshMemoWeftRuntime) -> str:
    assert runtime.host_id is not None
    return runtime.host_id


def _runtime(path: Path) -> DshMemoWeftRuntime:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("source", dsh_home=str(path), auto_route=False, model_tier="local")
    return runtime


def _ingest(runtime: DshMemoWeftRuntime, text: str, occurrence: str = "a", session: str = "source") -> dict[str, object]:
    return runtime.ingest_durable_boundary(_boundary(session, occurrence, [
        {"role": "user", "content": text}, {"role": "assistant", "content": "助手虚构了一个火星地址。"}]))


def _recent(runtime: DshMemoWeftRuntime, query: str, tier: str = "local", subject: str | None = None) -> list[Any]:
    assert _db_path(runtime) is not None
    service = QueryService(_db_path(runtime), subject_id=subject or _subject(runtime))
    return as_objects(as_object(service.preview_recall(query, model_tier=tier)["preview"])["recent_evidence"])


def test_pending_exact_user_quote_relevant_bounded_and_formal_replacement(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "我喜欢喝肉桂咖啡。")
        assert [r["text"] for r in _recent(runtime, "喝咖啡加什么？")] == ["我喜欢喝肉桂咖啡。"]
        assert _recent(runtime, "你好") == []
        assert _recent(runtime, "火星地址是什么？") == []
        assert _recent(runtime, "喝咖啡加什么？", subject="another-owner") == []
        def route(messages: Any, *, session_id: str) -> dict[str, object]:
            e = json.loads(messages[-1]["content"])["evidence"][0]
            item = {**_item(e["text"]), "supports": [{"evidence_id": e["id"], "sentence_id": "t0"}]}
            return _model(_batch(item))
        worker = WorldJobWorker(_db_path(runtime), processor=HermesBatchAdapterProcessor(str(_db_path(runtime)), route, model_tier="local"))
        assert worker.run_until_quiescent() == 1
        assert _recent(runtime, "喝咖啡加什么？") == []
        assert "肉桂" in str(as_object(QueryService(_db_path(runtime), subject_id=_subject(runtime)).preview_recall("喝咖啡加什么？")["preview"])["rendered_recall"])
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("column,tier", [("allow_cloud_read", "cloud"), ("allow_local_read", "local"), ("allow_inference", "local"), ("allow_inference", "cloud")])
def test_permissions_are_rechecked_on_every_read(tmp_path: Path, column: str, tier: str) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "我喜欢喝肉桂咖啡。")
        assert _recent(runtime, "喝咖啡加什么？", tier)
        with sqlite3.connect(_db_path(runtime)) as db:
            db.execute(f"UPDATE evidence SET {column}=0")
        assert _recent(runtime, "喝咖啡加什么？", tier) == []
    finally:
        runtime.shutdown()


def test_short_correction_carries_only_readable_antecedent_and_deletion_removes_it(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "我只能周三晚上锻炼。")
        _ingest(runtime, "不对，是周五晚上。", "b")
        rows = _recent(runtime, "下周安排哪天锻炼？")
        assert rows[-1]["text"] == "不对，是周五晚上。"
        assert rows[-1]["preceding_text"] == "我只能周三晚上锻炼。"
        with sqlite3.connect(_db_path(runtime)) as db:
            db.execute("UPDATE evidence SET allow_local_read=0 WHERE raw_content LIKE '%周三%'")
        assert _recent(runtime, "下周安排哪天锻炼？") == []
        erase_conversation_context(str(_db_path(runtime)), subject_id=_subject(runtime), conversation_id="source")
        assert _recent(runtime, "周五晚上？") == []
    finally:
        runtime.shutdown()


def test_deleted_source_never_reappears_even_with_unfinished_job(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "我喜欢喝肉桂咖啡。")
        with sqlite3.connect(_db_path(runtime)) as db:
            db.execute("UPDATE evidence SET deleted_at='2026-10-09T00:00:00Z'")
        assert _recent(runtime, "喝咖啡加什么？") == []
    finally:
        runtime.shutdown()


def test_numeric_correction_inherits_topic_across_sessions_without_inventing_one(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "我做果汁按水糖比8比1。")
        _ingest(runtime, "更正，往后用9比1，原来8比1停用。", "b", "other-session")
        rows = _recent(runtime, "果汁应该怎么配？")
        assert rows[-1]["text"] == "更正，往后用9比1，原来8比1停用。"
        assert rows[-1]["preceding_text"] == "我做果汁按水糖比8比1。"
    finally:
        runtime.shutdown()


def test_no_change_is_reconsidered_once_and_original_failure_is_preserved(tmp_path: Path) -> None:
    row, calls = _run(tmp_path / "world.sqlite3", "我喜欢喝肉桂咖啡。", [
        {"content": '{"schema_version":8,"result":"no_change"}'}, _model(_batch(_item("ignored")))])
    assert row["state"] == "applied"
    assert len(calls) == 2
    stored = json.loads(row["model_result_json"])["formation_rewrite"]
    assert stored["error"]["code"] == "model_no_change"
    assert json.loads(stored["first_result"]["content"])["result"] == "no_change"
    assert stored["final_error"] is None


def test_reversed_owner_relationship_gets_grounded_endpoint_feedback_once(tmp_path: Path) -> None:
    raw = "林舟是我的同学。"
    wrong = {"action": "form", "target": "owner_self", "statement_kind": "relationship", "formed_by": "stated",
        "proposition": raw, "supports": [{"evidence_id": "evidence-1", "sentence_id": "t0"}],
        "source_entity": {"canonical_name": "林舟", "kind": "person"},
        "target_entity": {"canonical_name": "用户", "kind": "person"}, "relation_type": "classmate"}
    correct = {**wrong, "target_entity": {"canonical_name": "林舟", "kind": "person"}}
    del correct["source_entity"]
    row, calls = _run(tmp_path / "world.sqlite3", raw, [_model(_batch(wrong)), _model(_batch(correct))])
    assert row["state"] == "applied"
    assert len(calls) == 2
    feedback = json.loads(calls[1][0][-1]["content"])["compiler_error"]
    assert feedback["code"] == "entity_name_not_in_proposition"
    assert "NEVER set target_entity to 我, 用户 or owner_self" in feedback["instruction"]
    assert json.loads(row["model_result_json"])["formation_rewrite"]["first_result"]["content"] == _model(_batch(wrong))["content"]


def test_true_forget_unfinished_source_cleans_bridge_and_disk_without_erasing_unrelated(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        secret = "MF1UnfinishedSecret"
        _ingest(runtime, f"我喜欢{secret}咖啡。")
        _ingest(runtime, "我喜欢花茶。", "b", "unrelated")
        evidence_id = _recent(runtime, secret)[0]["id"]
        service = CommandService(_db_path(runtime), subject_id=_subject(runtime), host_id=_host(runtime))
        receipt = service.submit_command({"schema_version": 1, "command_id": "forget-pending",
            "subject_id": _subject(runtime), "actor": "owner", "expected_world_revision": 0,
            "operation": "delete_evidence", "target_kind": "evidence", "target_id": evidence_id,
            "payload": {}, "submitted_at": "2026-10-09T00:00:00.000Z"})
        assert receipt["result_state"] == "applied"
        assert _recent(runtime, secret) == []
        assert _recent(runtime, "花茶")
        assert secret.encode() not in _db_path(runtime).read_bytes()
    finally:
        runtime.shutdown()


def test_recent_window_and_whole_quote_limits(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        for i, occurrence in enumerate("abcdef"):
            _ingest(runtime, f"我喜欢咖啡编号{i}。", occurrence, f"s{i}")
        assert len(_recent(runtime, "咖啡")) == 4
        _ingest(runtime, "我喜欢咖啡" + "很香" * 500 + "。", "9", "long")
        assert all(len(row["text"]) < 800 for row in _recent(runtime, "咖啡"))
        with sqlite3.connect(_db_path(runtime)) as db:
            db.execute("UPDATE memory_world_job SET created_at='2000-01-01T00:00:00.000Z'")
        assert _recent(runtime, "咖啡") == []
    finally:
        runtime.shutdown()


def test_batch_keeps_one_snapshot_when_source_permissions_change_between_queries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from memoweft.integrations.hermes import recall
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "以后请叫我小禾。")
        with sqlite3.connect(_db_path(runtime)) as db:
            db.execute("PRAGMA journal_mode=WAL")
        original = recall.recall_world_snapshot
        changed = False
        def concurrent_change(*args: Any, **kwargs: Any) -> Any:
            nonlocal changed
            result = original(*args, **kwargs)
            if not changed:
                changed = True
                with sqlite3.connect(_db_path(runtime)) as db:
                    db.execute("UPDATE evidence SET allow_cloud_read=0")
            return result
        monkeypatch.setattr(recall, "recall_world_snapshot", concurrent_change)
        service = QueryService(_db_path(runtime), subject_id=_subject(runtime))
        batch = service.preview_recall_batch(["怎么称呼我？", "我叫什么？"], model_tier="cloud")
        snapshots: Any = batch["snapshots"]
        assert all(snapshot["preview"]["recent_evidence"] for snapshot in snapshots)
        assert len({snapshot["world_revision"] for snapshot in snapshots}) == 1
        assert _recent(runtime, "怎么称呼我？", "cloud") == []
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("operation", ["delete_evidence", "update_evidence_permissions", "erase_conversation_context"])
def test_rpc_replay_cannot_recover_a_forgotten_or_denied_quote(tmp_path: Path, operation: str) -> None:
    runtime = _runtime(tmp_path)
    server = DshRpcV2Server(runtime)
    try:
        _ingest(runtime, "我喜欢MF1ReplaySecret咖啡。")
        request = _request("same-query-id", "preview_recall_batch", {"queries": ["MF1ReplaySecret"], "model_tier": "cloud"})
        before = server.handle(request)
        assert before["ok"] is True
        assert "MF1ReplaySecret" in json.dumps(before, ensure_ascii=False)
        evidence_id = _recent(runtime, "MF1ReplaySecret")[0]["id"]
        if operation == "erase_conversation_context":
            mutation = server.handle(_request("mutation", operation, {"conversation_id": "source"}))
        else:
            mutation = server.handle(_request("mutation", "submit_command", {"command": {
                "schema_version": 1, "command_id": "mf1-replay-mutation", "subject_id": _subject(runtime),
                "actor": "owner", "expected_world_revision": 0, "operation": operation,
                "target_kind": "evidence", "target_id": evidence_id,
                "payload": {"allow_cloud_read": False} if operation == "update_evidence_permissions" else {},
                "submitted_at": "2026-10-09T00:00:00.000Z"}}))
        assert mutation["ok"] is True
        after = server.handle(request)
        assert after["ok"] is True
        assert "MF1ReplaySecret" not in json.dumps(after, ensure_ascii=False)
    finally:
        runtime.shutdown()
