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


@pytest.mark.parametrize("prior,correction,query", [
    ("我养的阳台盆栽每次浇水用300毫升，这是我现在固定的用量。",
     "前面那个数报大了，应当是150毫升，300毫升作废，后面都以小的这个数为准。", "今天给阳台盆栽浇水，该量多少？"),
    ("每周线上读书会周四19点30分开始。", "刚才说的时间不对，改到20点15分，19点30分取消。", "线上读书会几点开始？"),
    ("陶艺兴趣组的联系人叫林舟。", "那个人名写错了，应该是林洲，不是林舟。", "陶艺兴趣组找谁？"),
    ("我做的手工香皂固定每块48元。", "上一句的价格多报了，应当是36元，48元作废。", "两块手工香皂多少钱？"),
    ("星桥书社寄书到青禾路18号。", "前面那个地址作废，青禾路18号写错了，改成白榆路26号。", "星桥书社收件地址？"),
])
def test_elliptical_correction_is_an_atomic_pair(tmp_path: Path, prior: str, correction: str, query: str) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, prior)
        _ingest(runtime, correction, "b", "another-conversation")
        rows = _recent(runtime, query)
        assert len(rows) == 1
        assert rows[0]["text"] == correction
        assert rows[0]["preceding_text"] == prior
        assert rows[0]["correction_status"] == "certain"
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("same_session", [False, True])
def test_competing_quantity_topics_are_presented_as_uncertain_not_chosen_by_query(tmp_path: Path, same_session: bool) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "阳台盆栽每次浇水300毫升。")
        _ingest(runtime, "我做蛋糕每次用牛奶300毫升。", "b", "source" if same_session else "cake")
        _ingest(runtime, "前面那个数报大了，150毫升才对，300毫升作废。", "c", "source" if same_session else "correction")
        rows = _recent(runtime, "阳台盆栽浇水多少？")
        assert len(rows) == 1
        assert rows[0]["correction_status"] == "ambiguous"
        assert "preceding_text" not in rows[0]
        assert {r["text"] for r in rows[0]["preceding_candidates"]} == {"阳台盆栽每次浇水300毫升。", "我做蛋糕每次用牛奶300毫升。"}
    finally:
        runtime.shutdown()


def test_oversized_ambiguous_pair_never_falls_back_to_the_old_value_alone(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "阳台盆栽每次浇水300毫升。" + "这是长期固定安排。" * 50)
        _ingest(runtime, "我做蛋糕每次用牛奶300毫升。" + "这是长期固定安排。" * 50, "b", "cake")
        _ingest(runtime, "前面那个数报大了，150毫升才对，300毫升作废。", "c", "correction")
        assert _recent(runtime, "阳台盆栽浇水多少？") == []
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("gap", ["time", "turns", "dimension", "unrelated"])
def test_elliptical_correction_does_not_inherit_outside_adjacent_dimension(tmp_path: Path, gap: str) -> None:
    from datetime import datetime, timedelta, timezone
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "阳台盆栽每次浇水300毫升。")
        if gap == "time":
            with sqlite3.connect(_db_path(runtime)) as db:
                db.execute("UPDATE memory_world_job SET created_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat().replace("+00:00", "Z"),))
        elif gap == "turns":
            for i in range(4):
                _ingest(runtime, "今天过得怎么样？", str(i), f"filler-{i}")
        correction = "前面那个数报大了，改成150元。" if gap == "dimension" else "前面那个数报大了，150毫升才对，300毫升作废。"
        if gap == "unrelated":
            correction = "我买的墨水改成150毫升装。"
        _ingest(runtime, correction, "f", "correction")
        rows = _recent(runtime, "阳台盆栽浇水多少？")
        assert all("preceding_text" not in r and "preceding_candidates" not in r for r in rows)
        assert all(r["text"] != correction for r in rows)
    finally:
        runtime.shutdown()


def test_forgetting_correction_pair_removes_both_sources_from_recall_and_disk(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, "MF2Secret盆栽每次浇水397毫升。")
        _ingest(runtime, "前面那个数报大了，改成163毫升，397毫升作废。", "b", "correction")
        pair = _recent(runtime, "MF2Secret盆栽浇水多少？")[0]
        assert pair["correction_status"] == "certain"
        service = CommandService(_db_path(runtime), subject_id=_subject(runtime), host_id=_host(runtime))
        for i, evidence_id in enumerate([pair["id"], pair["preceding_evidence_id"]]):
            revision = QueryService(_db_path(runtime), subject_id=_subject(runtime)).preview_recall("盆栽")["world_revision"]
            receipt = service.submit_command({"schema_version": 1, "command_id": f"mf2-forget-{i}",
                "subject_id": _subject(runtime), "actor": "owner", "expected_world_revision": revision,
                "operation": "delete_evidence", "target_kind": "evidence", "target_id": evidence_id,
                "payload": {}, "submitted_at": "2026-10-09T00:00:00.000Z"})
            assert receipt["result_state"] == "applied"
        assert _recent(runtime, "盆栽毫升") == []
        for secret in ["MF2Secret", "397毫升", "163毫升"]:
            assert secret.encode() not in _db_path(runtime).read_bytes()
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
