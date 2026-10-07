from __future__ import annotations

import ast
import inspect
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

import pytest

from memoweft.integrations.hermes import (
    BoundaryEnvelopeError,
    IncompatibleDatabaseError,
    _boundary_payload_hash,
    _build_provider_class,
)
from memoweft.integrations.hermes.boundary_store import BoundaryEvidenceConflictError
from memoweft.integrations.hermes import entrypoint as hermes_entrypoint
from memoweft.store import open_db, user_version
from memoweft.store.schema import SCHEMA_VERSION


def _rows(db_path: Path) -> list[sqlite3.Row]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        return db.execute(
            "SELECT source_kind, host_id, raw_content, origin_id, preceding_ai_context "
            "FROM evidence ORDER BY recorded_at, id"
        ).fetchall()
    finally:
        db.close()


def _world_job_count(db_path: Path) -> int:
    db = sqlite3.connect(db_path)
    try:
        return int(db.execute("SELECT COUNT(*) FROM memory_world_job").fetchone()[0])
    finally:
        db.close()


def _durable_boundary(
    messages: list[dict[str, object]],
    *,
    occurrence_id: str = "a" * 32,
    parent_session_id: str = "session-a",
    result_session_id: str = "session-a",
    mode: str = "in_place",
) -> dict[str, object]:
    normalized_messages: list[dict[str, object]] = []
    for index, message in enumerate(messages):
        normalized = dict(message)
        normalized.setdefault("synthetic", False)
        normalized["source_ref"] = f"source:{index}"
        normalized_messages.append(normalized)
    boundary: dict[str, object] = {
        "schema_version": 1,
        "provider_name": "memoweft",
        "parent_session_id": parent_session_id,
        "result_session_id": result_session_id,
        "mode": mode,
        "source_messages": normalized_messages,
    }
    boundary["payload_hash"] = _boundary_payload_hash(boundary)
    payload_hash = boundary["payload_hash"]
    assert isinstance(payload_hash, str)
    boundary["event_id"] = (
        f"hermes-compression-boundary-v1:{occurrence_id}:{payload_hash}"
    )
    return boundary


class _BareMemoryProvider:
    def __init__(self) -> None:
        pass


def _initialized_provider(tmp_path: Path, *, platform: str = "weixin") -> Any:
    provider = _build_provider_class(_BareMemoryProvider)()
    provider.initialize(
        "session-a",
        hermes_home=str(tmp_path),
        platform=platform,
        agent_context="primary",
        user_id="owner-platform-id",
    )
    return provider


def test_durable_boundary_stores_real_user_evidence_with_ai_context_and_one_job(
    tmp_path: Path,
) -> None:
    provider = _initialized_provider(tmp_path)
    receipt = provider.on_durable_boundary(
        _durable_boundary(
            [
                {"role": "assistant", "content": "你是说冬吗？", "timestamp": 1.0},
                {
                    "role": "user",
                    "content": "是的",
                    "message_id": "wx-1",
                    "timestamp": 2.0,
                },
            ]
        )
    )

    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    rows = _rows(db_path)
    assert receipt["eligible"] == 1
    assert receipt["stored"] == 1
    assert len(rows) == 1
    assert rows[0]["source_kind"] == "spoken"
    assert rows[0]["host_id"] == "hermes:weixin"
    assert rows[0]["raw_content"] == "是的"
    assert rows[0]["preceding_ai_context"] == "你是说冬吗？"
    assert rows[0]["origin_id"].startswith("hermes:")
    assert _world_job_count(db_path) == 1
    provider.shutdown()


def test_durable_boundary_distinguishes_repeated_user_messages_without_host_ids(
    tmp_path: Path,
) -> None:
    provider = _initialized_provider(tmp_path, platform="cli")
    receipt = provider.on_durable_boundary(
        _durable_boundary(
            [
                {"role": "user", "content": "继续"},
                {"role": "assistant", "content": "好的"},
                {"role": "user", "content": "继续"},
            ]
        )
    )

    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    assert receipt["stored"] == 2
    assert len({row["origin_id"] for row in _rows(db_path)}) == 2
    assert _world_job_count(db_path) == 1
    provider.shutdown()


def test_durable_boundary_conflict_rolls_back_its_evidence_and_job(
    tmp_path: Path,
) -> None:
    provider = _initialized_provider(tmp_path)
    first = _durable_boundary(
        [{"role": "user", "content": "原始内容", "message_id": "wx-stable"}],
        occurrence_id="a" * 32,
    )
    provider.on_durable_boundary(first)

    with pytest.raises(BoundaryEvidenceConflictError):
        provider.on_durable_boundary(
            _durable_boundary(
                [
                    {"role": "user", "content": "本批次也必须回滚", "message_id": "wx-new"},
                    {"role": "user", "content": "被篡改内容", "message_id": "wx-stable"},
                ],
                occurrence_id="b" * 32,
            )
        )

    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    assert [row["raw_content"] for row in _rows(db_path)] == ["原始内容"]
    assert _world_job_count(db_path) == 1
    provider.shutdown()


def test_provider_has_no_per_turn_write_tools_or_recall(tmp_path: Path) -> None:
    class FakeMemoryProvider:
        def __init__(self) -> None:
            pass

        def sync_turn(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
            del messages
            return ""

    provider_class = _build_provider_class(FakeMemoryProvider)
    provider = provider_class()
    provider.initialize(
        "session-a",
        hermes_home=str(tmp_path),
        platform="weixin",
        agent_context="primary",
        user_id="owner-platform-id",
    )

    assert "sync_turn" not in provider_class.__dict__
    provider.sync_turn("user", "assistant")
    assert provider.prefetch("query", session_id="session-a") == ""
    assert provider.system_prompt_block() == ""
    assert [schema["name"] for schema in provider.get_tool_schemas()] == [
        "memoweft_trust_capabilities",
        "memoweft_query_world",
        "memoweft_query_evidence",
        "memoweft_query_jobs",
        "memoweft_preview_recall",
        "memoweft_trust_command_capabilities",
        "memoweft_submit_trust_command",
        "memoweft_get_trust_command_receipt",
    ]
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    assert _rows(db_path) == []

    assert provider.on_pre_compress(
        [{"role": "user", "content": "压缩边界证据", "message_id": "wx-2"}]
    ) == ""
    assert _rows(db_path) == []

    receipt = provider.on_durable_boundary(
        _durable_boundary([{"role": "user", "content": "压缩边界证据"}])
    )
    assert receipt["stored"] == 1
    assert receipt["payload_hash"]
    assert receipt["job_state"] == "pending"
    assert receipt["evidence_count"] == 1
    assert receipt["receipt_hash"]
    assert [row["raw_content"] for row in _rows(db_path)] == ["压缩边界证据"]
    assert _world_job_count(db_path) == 1
    provider.shutdown()


def test_durable_boundary_exact_replay_returns_first_immutable_receipt(
    tmp_path: Path,
) -> None:
    class FakeMemoryProvider:
        def __init__(self) -> None:
            pass

    provider = _build_provider_class(FakeMemoryProvider)()
    provider.initialize(
        "session-a",
        hermes_home=str(tmp_path),
        platform="weixin",
        agent_context="primary",
        user_id="owner-platform-id",
    )
    boundary = _durable_boundary([{"role": "user", "content": "可重放边界"}])

    first = provider.on_durable_boundary(boundary)
    replay = provider.on_durable_boundary(boundary)

    assert replay == first
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    assert len(_rows(db_path)) == 1
    assert _world_job_count(db_path) == 1
    provider.shutdown()


def test_assistant_only_boundary_gets_durable_no_change_job_without_evidence(
    tmp_path: Path,
) -> None:
    class FakeMemoryProvider:
        def __init__(self) -> None:
            pass

    provider = _build_provider_class(FakeMemoryProvider)()
    provider.initialize(
        "session-a",
        hermes_home=str(tmp_path),
        platform="weixin",
        agent_context="primary",
        user_id="owner-platform-id",
    )
    receipt = provider.on_durable_boundary(
        _durable_boundary([{"role": "assistant", "content": "只保留为潜在上下文"}])
    )

    assert receipt["eligible"] == 0
    assert receipt["stored"] == 0
    assert receipt["skipped"] == 0
    assert receipt["evidence_count"] == 0
    assert receipt["job_state"] == "no_change"
    assert receipt["reason"] == "no_eligible_user_evidence"
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    assert _rows(db_path) == []
    assert _world_job_count(db_path) == 1
    provider.shutdown()


def test_durable_boundary_payload_mismatch_fails_closed_before_any_write(
    tmp_path: Path,
) -> None:
    class FakeMemoryProvider:
        def __init__(self) -> None:
            pass

    provider = _build_provider_class(FakeMemoryProvider)()
    provider.initialize(
        "session-a",
        hermes_home=str(tmp_path),
        platform="weixin",
        agent_context="primary",
        user_id="owner-platform-id",
    )
    boundary = _durable_boundary(
        [{"role": "user", "content": "原始压缩边界证据"}]
    )
    messages = boundary["source_messages"]
    assert isinstance(messages, list)
    assert isinstance(messages[0], dict)
    messages[0]["content"] = "被篡改的压缩边界证据"

    with pytest.raises(BoundaryEnvelopeError, match="hash"):
        provider.on_durable_boundary(boundary)

    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    assert _rows(db_path) == []
    assert _world_job_count(db_path) == 0
    provider.shutdown()


def test_durable_boundary_event_id_must_bind_verified_payload_before_any_write(
    tmp_path: Path,
) -> None:
    class FakeMemoryProvider:
        def __init__(self) -> None:
            pass

    provider = _build_provider_class(FakeMemoryProvider)()
    provider.initialize(
        "session-a",
        hermes_home=str(tmp_path),
        platform="weixin",
        agent_context="primary",
        user_id="owner-platform-id",
    )
    boundary = _durable_boundary([{"role": "user", "content": "事件绑定证据"}])
    boundary["event_id"] = "hermes-compression-boundary-v1:" + "b" * 32 + ":" + "0" * 64

    with pytest.raises(BoundaryEnvelopeError, match="event_id"):
        provider.on_durable_boundary(boundary)

    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    assert _rows(db_path) == []
    assert _world_job_count(db_path) == 0
    provider.shutdown()


def test_durable_boundary_out_of_range_timestamp_fails_closed_before_any_write(
    tmp_path: Path,
) -> None:
    provider = _initialized_provider(tmp_path)
    boundary = _durable_boundary(
        [
            {
                "role": "user",
                "content": "时间戳必须保持精确来源语义",
                "timestamp": 1e300,
            }
        ]
    )

    with pytest.raises(BoundaryEnvelopeError, match="UTC range"):
        provider.on_durable_boundary(boundary)

    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    assert _rows(db_path) == []
    assert _world_job_count(db_path) == 0
    provider.shutdown()


def test_non_primary_agent_context_never_creates_runtime_database(tmp_path: Path) -> None:
    class FakeMemoryProvider:
        def __init__(self) -> None:
            pass

    provider = _build_provider_class(FakeMemoryProvider)()
    provider.initialize(
        "subagent-session",
        hermes_home=str(tmp_path),
        platform="cli",
        agent_context="subagent",
        user_id="owner-platform-id",
    )
    with pytest.raises(RuntimeError):
        provider.on_durable_boundary(
            {
                "schema_version": 1,
                "event_id": "boundary-subagent",
                "provider_name": "memoweft",
                "parent_session_id": "subagent-session",
                "source_messages": [{"role": "user", "content": "do not store"}],
            }
        )

    assert not (tmp_path / "memoweft" / "memoweft.sqlite3").exists()


def test_terminal_outcome_capability_is_an_entrypoint_literal() -> None:
    entrypoint_path = Path(hermes_entrypoint.__file__ or "")
    tree = ast.parse(entrypoint_path.read_text(encoding="utf-8"))
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert hermes_entrypoint.supports_terminal_outcomes is True
    assert "supports_terminal_outcomes" in hermes_entrypoint.__all__
    assert isinstance(assignments["supports_terminal_outcomes"], ast.Constant)
    assert assignments["supports_terminal_outcomes"].value is True
    assert hermes_entrypoint.supports_trust_queries is True
    assert "supports_trust_queries" in hermes_entrypoint.__all__
    assert isinstance(assignments["supports_trust_queries"], ast.Constant)
    assert assignments["supports_trust_queries"].value is True
    assert hermes_entrypoint.supports_trust_commands is True
    assert "supports_trust_commands" in hermes_entrypoint.__all__
    assert isinstance(assignments["supports_trust_commands"], ast.Constant)
    assert assignments["supports_trust_commands"].value is True
    assert hermes_entrypoint.supports_clarifications is True
    assert "supports_clarifications" in hermes_entrypoint.__all__
    assert isinstance(assignments["supports_clarifications"], ast.Constant)
    assert assignments["supports_clarifications"].value is True


def test_provider_trust_tools_are_subject_bound_read_only_and_canonical(
    tmp_path: Path,
) -> None:
    provider = _initialized_provider(tmp_path)
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    before = db_path.read_bytes()

    schemas = provider.get_tool_schemas()
    assert [schema["name"] for schema in schemas] == [
        "memoweft_trust_capabilities",
        "memoweft_query_world",
        "memoweft_query_evidence",
        "memoweft_query_jobs",
        "memoweft_preview_recall",
        "memoweft_trust_command_capabilities",
        "memoweft_submit_trust_command",
        "memoweft_get_trust_command_receipt",
    ]
    assert all(schema["parameters"]["additionalProperties"] is False for schema in schemas)

    raw = provider.handle_tool_call("memoweft_trust_capabilities", {})
    capabilities = json.loads(raw)
    assert raw == json.dumps(
        capabilities,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert capabilities["schema_version"] == 1
    assert capabilities["world_revision"] == 0
    assert capabilities["read_only"] is True

    rejected = json.loads(
        provider.handle_tool_call(
            "memoweft_query_world",
            {"operation": "revision", "subject_id": "different-owner"},
        )
    )
    assert rejected == {
        "error": {"code": "unexpected_trust_tool_argument"},
        "ok": False,
        "read_only": True,
        "schema_version": 1,
    }
    assert db_path.read_bytes() == before
    provider.shutdown()


def test_provider_trust_command_tools_submit_and_replay_durable_receipt(
    tmp_path: Path,
) -> None:
    provider = _initialized_provider(tmp_path)
    provider.on_durable_boundary(
        _durable_boundary([{"role": "user", "content": "用户喜欢喝咖啡"}])
    )
    evidence_result = json.loads(
        provider.handle_tool_call("memoweft_query_evidence", {"operation": "list"})
    )
    evidence_id = evidence_result["evidence"][0]["evidence_id"]
    command_args = {
        "command_id": "provider-command-1",
        "actor": "owner",
        "expected_world_revision": evidence_result["world_revision"],
        "operation": "update_evidence_permissions",
        "target_kind": "evidence",
        "target_id": evidence_id,
        "payload": {"allow_cloud_read": False},
        "submitted_at": "2026-08-25T00:00:00.000Z",
    }
    submitted_raw = provider.handle_tool_call(
        "memoweft_submit_trust_command", command_args
    )
    submitted = json.loads(submitted_raw)
    assert submitted_raw == json.dumps(
        submitted,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert submitted["mutation_surface"] is True
    assert submitted["receipt"]["result_state"] == "applied"
    replay = json.loads(
        provider.handle_tool_call("memoweft_submit_trust_command", command_args)
    )
    assert replay["receipt"] == submitted["receipt"]
    lookup = json.loads(
        provider.handle_tool_call(
            "memoweft_get_trust_command_receipt",
            {"command_id": "provider-command-1"},
        )
    )
    assert lookup["mutation_surface"] is False
    assert lookup["receipt"] == submitted["receipt"]
    provider.shutdown()


def test_dynamic_provider_delegates_terminal_outcome_methods_with_frozen_signatures() -> None:
    class RecordingRuntime:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def claim_terminal_outcomes(
            self, claim_owner: str, *, limit: int = 8
        ) -> list[dict[str, object]]:
            self.calls.append(("claim", claim_owner, limit))
            return [{"outcome_id": "outcome-1"}]

        def heartbeat_terminal_outcome(
            self, outcome_id: str, claim_token: str
        ) -> bool:
            self.calls.append(("heartbeat", outcome_id, claim_token))
            return True

        def ack_terminal_outcome(self, outcome_id: str, claim_token: str) -> bool:
            self.calls.append(("ack", outcome_id, claim_token))
            return True

        def nack_terminal_outcome(
            self, outcome_id: str, claim_token: str, *, error_type: str
        ) -> bool:
            self.calls.append(("nack", outcome_id, claim_token, error_type))
            return True

    provider = _build_provider_class(_BareMemoryProvider)()
    runtime = RecordingRuntime()
    provider._runtime = runtime

    assert list(inspect.signature(provider.claim_terminal_outcomes).parameters) == [
        "claim_owner",
        "limit",
    ]
    assert inspect.signature(
        provider.claim_terminal_outcomes
    ).parameters["limit"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(inspect.signature(provider.heartbeat_terminal_outcome).parameters) == [
        "outcome_id",
        "claim_token",
    ]
    assert list(inspect.signature(provider.ack_terminal_outcome).parameters) == [
        "outcome_id",
        "claim_token",
    ]
    assert list(inspect.signature(provider.nack_terminal_outcome).parameters) == [
        "outcome_id",
        "claim_token",
        "error_type",
    ]
    assert inspect.signature(
        provider.nack_terminal_outcome
    ).parameters["error_type"].kind is inspect.Parameter.KEYWORD_ONLY

    assert provider.claim_terminal_outcomes("host-a", limit=3) == [
        {"outcome_id": "outcome-1"}
    ]
    assert provider.heartbeat_terminal_outcome("outcome-1", "token-a") is True
    assert provider.ack_terminal_outcome("outcome-1", "token-a") is True
    assert (
        provider.nack_terminal_outcome(
            "outcome-1", "token-a", error_type="host_event_write_failed"
        )
        is True
    )
    assert runtime.calls == [
        ("claim", "host-a", 3),
        ("heartbeat", "outcome-1", "token-a"),
        ("ack", "outcome-1", "token-a"),
        ("nack", "outcome-1", "token-a", "host_event_write_failed"),
    ]


def test_terminal_outcome_methods_require_initialized_primary_runtime(
    tmp_path: Path,
) -> None:
    provider = _build_provider_class(_BareMemoryProvider)()
    calls: tuple[Callable[[], object], ...] = (
        lambda: provider.claim_terminal_outcomes("host-a"),
        lambda: provider.heartbeat_terminal_outcome("outcome-1", "token-a"),
        lambda: provider.ack_terminal_outcome("outcome-1", "token-a"),
        lambda: provider.nack_terminal_outcome(
            "outcome-1", "token-a", error_type="host_event_write_failed"
        ),
    )
    for call in calls:
        with pytest.raises(RuntimeError, match="terminal outcomes"):
            call()

    provider.initialize(
        "subagent-session",
        hermes_home=str(tmp_path),
        platform="cli",
        agent_context="subagent",
        user_id="owner-platform-id",
    )
    for call in calls:
        with pytest.raises(RuntimeError, match="terminal outcomes"):
            call()
    assert not (tmp_path / "memoweft" / "memoweft.sqlite3").exists()


def test_boundary_receipt_and_terminal_outcome_are_distinct(tmp_path: Path) -> None:
    provider = _initialized_provider(tmp_path)
    receipt = provider.on_durable_boundary(
        _durable_boundary([{"role": "assistant", "content": "仅有助手上下文"}])
    )
    outcomes = provider.claim_terminal_outcomes("host-a")

    assert receipt["job_state"] == "no_change"
    assert "outcome_id" not in receipt
    assert "claim_token" not in receipt
    assert "eligible" in receipt
    assert "receipt_hash" in receipt
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["terminal_state"] == "no_change"
    assert outcome["outcome_id"]
    assert outcome["claim_token"]
    assert "eligible" not in outcome
    assert "receipt_hash" not in outcome
    provider.shutdown()


def test_pending_terminal_outcome_survives_provider_restart_and_ack(
    tmp_path: Path,
) -> None:
    first = _initialized_provider(tmp_path)
    first.on_durable_boundary(
        _durable_boundary([{"role": "assistant", "content": "等待终态投递"}])
    )
    first.shutdown()

    second = _initialized_provider(tmp_path)
    claimed = second.claim_terminal_outcomes("host-after-restart")
    assert len(claimed) == 1
    assert claimed[0]["terminal_state"] == "no_change"
    assert second.ack_terminal_outcome(
        str(claimed[0]["outcome_id"]), str(claimed[0]["claim_token"])
    )
    assert second.claim_terminal_outcomes("host-after-restart") == []
    second.shutdown()


def test_terminal_outcome_stale_token_is_fenced(tmp_path: Path) -> None:
    provider = _initialized_provider(tmp_path)
    provider.on_durable_boundary(
        _durable_boundary([{"role": "assistant", "content": "令牌必须围栏"}])
    )
    claimed = provider.claim_terminal_outcomes("host-a")
    assert len(claimed) == 1
    outcome_id = str(claimed[0]["outcome_id"])
    stale_token = "stale-" + str(claimed[0]["claim_token"])

    assert provider.heartbeat_terminal_outcome(outcome_id, stale_token) is False
    assert provider.ack_terminal_outcome(outcome_id, stale_token) is False
    assert (
        provider.nack_terminal_outcome(
            outcome_id, stale_token, error_type="host_event_write_failed"
        )
        is False
    )
    assert provider.ack_terminal_outcome(
        outcome_id, str(claimed[0]["claim_token"])
    )
    provider.shutdown()


def test_existing_legacy_database_is_rejected_without_schema_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    db_path.parent.mkdir()
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE evidence (id TEXT PRIMARY KEY, raw_content TEXT NOT NULL)")
    db.commit()
    db.close()
    before = db_path.read_bytes()

    with pytest.raises(IncompatibleDatabaseError):
        _initialized_provider(tmp_path, platform="cli")

    assert db_path.read_bytes() == before


def _synthetic_v15_source(tmp_path: Path) -> Path:
    """Build a real v15 physical shape without v16/v17 tables."""

    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    db_path.parent.mkdir()
    db = open_db(str(db_path))
    db.execute("DROP TABLE portable_import_receipt")
    db.execute("DROP TABLE clarification")
    db.execute("DROP TABLE terminal_outcome")
    db.execute("DROP TABLE trust_command_receipt")
    db.execute("DROP TABLE trust_command")
    db.execute("DROP TABLE world_item_lifecycle")
    db.execute("PRAGMA user_version = 15")
    db.close()
    return db_path


def test_existing_v15_database_passes_read_only_preflight_and_migrates(
    tmp_path: Path,
) -> None:
    db_path = _synthetic_v15_source(tmp_path)

    provider = _initialized_provider(tmp_path, platform="cli")
    assert provider is not None
    db = open_db(str(db_path))
    try:
        assert user_version(db) == SCHEMA_VERSION
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'terminal_outcome'"
        ).fetchone() == (1,)
    finally:
        db.close()
    provider.shutdown()


def test_current_v16_outcome_schema_damage_is_rejected_before_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    db_path.parent.mkdir()
    db = open_db(str(db_path))
    db.execute("DROP TABLE portable_import_receipt")
    db.execute("DROP TABLE clarification")
    db.execute("DROP TABLE terminal_outcome")
    db.execute("DROP TABLE trust_command_receipt")
    db.execute("DROP TABLE trust_command")
    db.execute("DROP TABLE world_item_lifecycle")
    db.close()
    before = db_path.read_bytes()

    with pytest.raises(IncompatibleDatabaseError, match="terminal outcome"):
        _initialized_provider(tmp_path, platform="cli")

    assert db_path.read_bytes() == before


def _synthetic_v7_source(tmp_path: Path) -> Path:
    """Build a physical v7 database: current schema minus the v8-v10 tables
    and the v15 terminal observability columns (added after v7)."""
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    db_path.parent.mkdir()
    db = open_db(str(db_path))
    db.execute("DROP TABLE portable_import_receipt")
    db.execute("DROP TABLE clarification")
    db.execute("DROP TABLE terminal_outcome")
    db.execute("DROP TABLE trust_command_receipt")
    db.execute("DROP TABLE trust_command")
    db.execute("DROP TABLE world_item_lifecycle")
    db.execute("DROP TABLE world_event_evidence")
    db.execute("DROP TABLE world_event")
    db.execute("DROP TABLE retraction")
    db.execute("DROP TABLE cognition_target")
    db.execute("DROP TABLE boundary_evidence_content")
    db.execute("DROP TABLE relationship_evidence")
    db.execute("DROP TABLE relationship")
    db.execute("DROP TABLE entity")
    db.execute("ALTER TABLE memory_world_job DROP COLUMN terminal_detail")
    db.execute("ALTER TABLE memory_world_job DROP COLUMN terminal_state")
    db.execute("PRAGMA user_version = 7")
    db.close()
    return db_path


def test_existing_v7_database_is_accepted_and_migrated_to_current(tmp_path: Path) -> None:
    db_path = _synthetic_v7_source(tmp_path)
    provider = _initialized_provider(tmp_path, platform="cli")
    assert provider is not None
    db = open_db(str(db_path))
    try:
        assert user_version(db) == SCHEMA_VERSION
        cols = tuple(
            str(row[1])
            for row in db.execute("PRAGMA table_info(boundary_evidence_content)")
        )
        assert cols == ("evidence_id", "raw_content_hash")
    finally:
        db.close()


def test_existing_v7_database_with_wrong_app_id_is_rejected_unchanged(
    tmp_path: Path,
) -> None:
    db_path = _synthetic_v7_source(tmp_path)
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute("PRAGMA application_id = 0")
    db.close()
    before = db_path.read_bytes()

    with pytest.raises(IncompatibleDatabaseError):
        _initialized_provider(tmp_path, platform="cli")

    assert db_path.read_bytes() == before
