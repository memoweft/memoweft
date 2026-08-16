from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from memoweft.integrations.hermes import (
    BoundaryEnvelopeError,
    IncompatibleDatabaseError,
    _boundary_payload_hash,
    _build_provider_class,
)
from memoweft.integrations.hermes.boundary_store import BoundaryEvidenceConflictError
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
    assert provider.get_tool_schemas() == []
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


def _synthetic_v7_source(tmp_path: Path) -> Path:
    """Build a physical v7 database: current schema minus the v8-v10 tables."""
    db_path = tmp_path / "memoweft" / "memoweft.sqlite3"
    db_path.parent.mkdir()
    db = open_db(str(db_path))
    db.execute("DROP TABLE world_event_evidence")
    db.execute("DROP TABLE world_event")
    db.execute("DROP TABLE retraction")
    db.execute("DROP TABLE cognition_target")
    db.execute("DROP TABLE boundary_evidence_content")
    db.execute("DROP TABLE relationship_evidence")
    db.execute("DROP TABLE relationship")
    db.execute("DROP TABLE entity")
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
