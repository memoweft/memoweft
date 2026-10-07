"""N8 DSH RPC v2 envelopes, Core-service bindings, and restart-safe replay."""

from __future__ import annotations

import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping, cast

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.dsh_bridge import entrypoint as dsh_entrypoint
from memoweft.integrations.dsh_bridge.protocol_v2 import (
    DSH_RPC_METHODS,
    DSH_RPC_PROTOCOL,
    DSH_RPC_PROTOCOL_VERSION,
    DSH_RPC_SCHEMA_VERSION,
    DshRpcV2Server,
)
from memoweft.integrations.trust import derive_clarification_id
from memoweft.store import open_db


_T = "2026-08-25T02:00:00.000Z"


def _request(
    request_id: str, method: str, params: Mapping[str, object] | None = None
) -> dict[str, object]:
    return {
        "protocol": DSH_RPC_PROTOCOL,
        "protocol_version": DSH_RPC_PROTOCOL_VERSION,
        "schema_version": DSH_RPC_SCHEMA_VERSION,
        "request_id": request_id,
        "method": method,
        "params": dict(params or {}),
    }


def _initialize(
    server: DshRpcV2Server,
    dsh_home: Path,
    *,
    request_id: str = "initialize-1",
) -> dict[str, object]:
    response = server.handle(
        _request(
            request_id,
            "initialize",
            {
                "session_id": "dsh-session",
                "dsh_home": str(dsh_home),
                "platform": "desktop",
                "user_id": "rpc-owner",
                "auto_route": False,
            },
        )
    )
    assert response["ok"] is True, response
    assert response["result_code"] == "initialized"
    return response


def _boundary() -> dict[str, object]:
    source_messages = [
        {
            "role": "user",
            "content": "我在南京喜欢喝咖啡",
            "source_ref": "source:0",
        }
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "provider_name": "memoweft",
        "parent_session_id": "dsh-session",
        "result_session_id": "dsh-session",
        "mode": "in_place",
        "source_messages": source_messages,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    payload_hash = sha256(canonical.encode("utf-8")).hexdigest()
    return {
        **payload,
        "payload_hash": payload_hash,
        "event_id": "weftmate-compression-boundary-v1:"
        + "b" * 32
        + ":"
        + payload_hash,
    }


def _message_boundary(message_id: str, content: str) -> dict[str, object]:
    payload = {
        key: value for key, value in _boundary().items()
        if key not in {"event_id", "payload_hash"}
    }
    payload["source_messages"] = [
        {"role": "user", "content": content, "source_ref": "source:0", "message_id": message_id}
    ]
    canonical = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    payload_hash = sha256(canonical.encode("utf-8")).hexdigest()
    return {
        **payload,
        "payload_hash": payload_hash,
        "event_id": "weftmate-compression-boundary-v1:" + "b" * 32 + ":" + payload_hash,
    }


def test_rpc_hard_deleted_source_has_dedicated_code_only_for_origin_tombstone(
    tmp_path: Path,
) -> None:
    server = DshRpcV2Server()
    _initialize(server, tmp_path)
    try:
        deleted_boundary = _message_boundary("deleted-message", "private synthetic source")
        accepted = server.handle(_request("accept-deleted-source", "ingest_boundary", {"boundary": deleted_boundary}))
        assert accepted["ok"] is True
        assert server.runtime.db_path is not None
        assert server.runtime.subject_id is not None
        db = open_db(str(server.runtime.db_path))
        try:
            evidence_id = db.execute(
                "SELECT id FROM evidence WHERE raw_content = 'private synthetic source'"
            ).fetchone()[0]
            revision = db.execute("SELECT revision FROM memory_state WHERE singleton = 1").fetchone()
            expected_revision = 0 if revision is None else int(revision[0])
        finally:
            db.close()
        deleted = server.handle(_request("delete-source-command", "submit_command", {
            "command": {
                "schema_version": 1,
                "command_id": "delete-source-command",
                "subject_id": server.runtime.subject_id,
                "actor": "owner",
                "expected_world_revision": expected_revision,
                "operation": "delete_evidence",
                "target_kind": "evidence",
                "target_id": evidence_id,
                "payload": {},
                "submitted_at": _T,
            }
        }))
        assert deleted["ok"] is True
        assert deleted["result"]["receipt"]["result_state"] == "applied"  # type: ignore[index]
        replay = server.handle(_request("replay-deleted-source", "ingest_boundary", {"boundary": deleted_boundary}))
        assert replay["ok"] is False
        assert replay["result_code"] == "hard_deleted_source"
        assert replay["error"] == {"type": "boundary", "code": "hard_deleted_source"}
        assert "private synthetic source" not in json.dumps(replay, ensure_ascii=False)

        ordinary = _message_boundary("ordinary-message", "first ordinary source")
        assert server.handle(_request("accept-ordinary", "ingest_boundary", {"boundary": ordinary}))["ok"] is True
        conflicting = _message_boundary("ordinary-message", "different ordinary source")
        conflict_response = server.handle(_request("conflict-ordinary", "ingest_boundary", {"boundary": conflicting}))
        assert conflict_response["ok"] is False
        assert conflict_response["result_code"] != "hard_deleted_source"
    finally:
        server.runtime.shutdown()


def test_initialize_accepts_one_subject_binding_but_query_methods_cannot_switch_it(
    tmp_path: Path,
) -> None:
    server = DshRpcV2Server()
    initialized = server.handle(
        _request(
            "initialize-owner",
            "initialize",
            {
                "session_id": "memory-experience",
                "dsh_home": str(tmp_path),
                "platform": "memory-web",
                "user_id": "presentation-user",
                "subject_id": "owner",
                "auto_route": False,
            },
        )
    )
    assert initialized["ok"] is True
    assert initialized["result"]["runtime"]["subject_id"] == "owner"  # type: ignore[index]

    switched = server.handle(
        _request(
            "query-other-subject",
            "query_world",
            {"operation": "list", "subject_id": "other"},
        )
    )
    assert switched["ok"] is False
    assert switched["result_code"] == "unexpected_trust_tool_argument"


def test_initialize_fixes_one_clarification_host_and_requests_cannot_switch_it(
    tmp_path: Path,
) -> None:
    server = DshRpcV2Server()
    initialized = server.handle(
        _request(
            "initialize-memory-experience-host",
            "initialize",
            {
                "session_id": "memory-experience",
                "dsh_home": str(tmp_path),
                "platform": "memory-web",
                "user_id": "presentation-user",
                "subject_id": "owner",
                "clarification_host_id": "hermes:cli",
                "auto_route": False,
            },
        )
    )
    assert initialized["ok"] is True
    runtime = initialized["result"]["runtime"]  # type: ignore[index]
    assert runtime["host_id"] == "weftmate:memory-web"
    assert runtime["clarification_host_id"] == "hermes:cli"

    switched = server.handle(
        _request(
            "list-other-host",
            "list_clarifications",
            {"state": "open", "clarification_host_id": "hermes:gateway:weixin"},
        )
    )
    assert switched["ok"] is False
    assert switched["result_code"] == "unexpected_method_parameter"

    untrusted = DshRpcV2Server().handle(
        _request(
            "initialize-untrusted-host",
            "initialize",
            {
                "session_id": "memory-experience",
                "dsh_home": str(tmp_path / "untrusted"),
                "platform": "memory-web",
                "user_id": "presentation-user",
                "subject_id": "owner",
                "clarification_host_id": "weftmate:other-ui",
                "auto_route": False,
            },
        )
    )
    assert untrusted["ok"] is False
    assert untrusted["result_code"] == "untrusted_clarification_host_id"


def _insert_query_and_command_facts(runtime: DshMemoWeftRuntime) -> None:
    assert runtime.db_path is not None
    assert runtime.subject_id is not None
    assert runtime.host_id is not None
    db = open_db(str(runtime.db_path))
    try:
        for evidence_id, content in (
            ("ev-cognition", "用户喜欢喝咖啡"),
            ("ev-command", "这条 Evidence 用于权限命令"),
        ):
            db.execute(
                "INSERT INTO evidence (id, subject_id, source_kind, host_id, "
                "origin_id, occurred_at, recorded_at, raw_content, summary, "
                "allow_local_read, allow_cloud_read, allow_inference) VALUES "
                "(?, ?, 'spoken', ?, ?, ?, ?, ?, ?, 1, 1, 1)",
                (
                    evidence_id,
                    runtime.subject_id,
                    runtime.host_id,
                    "origin:" + evidence_id,
                    _T,
                    _T,
                    content,
                    content,
                ),
            )
        db.execute(
            "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
            "confidence, cred_status, scope, valid_at, invalid_at, asked_at, "
            "archived_at, muted_at, created_at, updated_at) VALUES "
            "('cog-rpc', ?, '用户喜欢喝咖啡', 'preference', 'stated', 600, "
            "'limited', NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)",
            (runtime.subject_id, _T, _T),
        )
        db.execute(
            "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
            "VALUES ('cog-rpc', 'ev-cognition', 'support')"
        )
        db.commit()
    finally:
        db.close()


def _insert_open_clarification(
    runtime: DshMemoWeftRuntime, *, source_job_id: str
) -> str:
    assert runtime.db_path is not None
    assert runtime.subject_id is not None
    clarification_id = derive_clarification_id(source_job_id, "source-outcome-rpc")
    db = open_db(str(runtime.db_path))
    try:
        db.execute(
            "INSERT INTO clarification (clarification_id, source_job_id, "
            "source_outcome_id, subject_id, result_session_id, question, target_hint, "
            "state, answer_evidence_id, follow_up_job_id, opened_at, answered_at, "
            "resolved_at) VALUES (?, ?, 'source-outcome-rpc', ?, "
            "'dsh-session', '你指的是哪一种咖啡？', NULL, 'open', NULL, NULL, ?, "
            "NULL, NULL)",
            (clarification_id, source_job_id, runtime.subject_id, _T),
        )
        db.commit()
    finally:
        db.close()
    return clarification_id


def _spawn_bridge() -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment.pop("DEEPSEEK_API_KEY", None)
    environment.pop("MEMOWEFT_TEST_MODEL_RESPONSE", None)
    return subprocess.Popen(
        [sys.executable, "-m", "memoweft.integrations.dsh_bridge"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _stdio_request(
    process: subprocess.Popen[str], request: Mapping[str, object]
) -> dict[str, object]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(
        json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    process.stdin.flush()
    line = process.stdout.readline()
    assert line, "DSH bridge exited without a response"
    decoded = json.loads(line)
    assert isinstance(decoded, dict)
    return decoded


def _shutdown_process(
    process: subprocess.Popen[str], *, request_id: str
) -> str:
    response = _stdio_request(process, _request(request_id, "shutdown"))
    assert response["ok"] is True
    assert response["result_code"] == "shutdown"
    assert process.wait(timeout=10) == 0
    assert process.stderr is not None
    return cast(str, process.stderr.read())


def test_rpc_v2_protocol_identity_is_frozen() -> None:
    assert DSH_RPC_PROTOCOL == "memoweft.dsh_rpc"
    assert DSH_RPC_PROTOCOL_VERSION == 2
    assert DSH_RPC_SCHEMA_VERSION == 1
    assert dsh_entrypoint.supports_durable_boundaries is True
    assert dsh_entrypoint.supports_dsh_rpc_v2 is True
    assert dsh_entrypoint.dsh_rpc_protocol_version == 2
    assert dsh_entrypoint.dsh_rpc_schema_version == 1
    entrypoint_path = Path(dsh_entrypoint.__file__ or "")
    tree = ast.parse(entrypoint_path.read_text(encoding="utf-8"))
    literal_assignments = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    assert literal_assignments["supports_durable_boundaries"] is True
    assert literal_assignments["supports_dsh_rpc_v2"] is True
    assert literal_assignments["dsh_rpc_protocol_version"] == 2
    assert literal_assignments["dsh_rpc_schema_version"] == 1
    assert DSH_RPC_METHODS == (
        "initialize",
        "capabilities",
        "ingest_boundary",
        "prefetch",
        "query_world",
        "query_evidence",
        "query_provenance",
        "query_jobs",
        "preview_recall",
        "query_interactions",
        "query_interaction",
        "link_interaction_dependencies",
        "submit_command",
        "query_command_receipt",
        "retry_delete_storage_cleanup",
        "list_clarifications",
        "answer_clarification",
        "portable_plan",
        "portable_export",
        "portable_import",
        "health",
        "shutdown",
    )


def test_rpc_v2_envelopes_negotiation_and_request_replay_are_closed() -> None:
    server = DshRpcV2Server()
    query_before_init = server.handle(
        _request("query-before-init", "query_world", {"operation": "list"})
    )
    assert query_before_init == {
        "protocol": DSH_RPC_PROTOCOL,
        "protocol_version": 2,
        "schema_version": 1,
        "request_id": "query-before-init",
        "ok": False,
        "result_code": "not_initialized",
        "world_revision": None,
        "error": {"type": "protocol", "code": "not_initialized"},
    }
    assert (
        server.handle(
            _request("query-before-init", "query_world", {"operation": "list"})
        )
        == query_before_init
    )
    failed_request_conflict = server.handle(
        _request("query-before-init", "query_evidence", {"operation": "list"})
    )
    assert failed_request_conflict["result_code"] == "request_id_conflict"

    capabilities_request = _request("capabilities-1", "capabilities")
    first = server.handle(capabilities_request)
    replay = server.handle(capabilities_request)
    assert replay == first
    assert json.dumps(replay, ensure_ascii=False, separators=(",", ":")) == json.dumps(
        first, ensure_ascii=False, separators=(",", ":")
    )
    assert first["ok"] is True
    assert first["result_code"] == "capabilities"
    assert first["world_revision"] is None
    result = first["result"]
    assert isinstance(result, dict)
    assert result["methods"] == list(DSH_RPC_METHODS)
    assert result["initialized"] is False
    assert result["request_id_conflict"] == "fail_closed"

    conflict = server.handle(_request("capabilities-1", "health"))
    assert conflict["ok"] is False
    assert conflict["result_code"] == "request_id_conflict"
    assert "message" not in conflict["error"]  # type: ignore[operator]

    malformed = _request("malformed-1", "health")
    malformed["extra"] = True
    malformed_response = server.handle(malformed)
    assert malformed_response["ok"] is False
    assert malformed_response["result_code"] == "invalid_request_envelope"
    assert set(malformed_response) == {
        "protocol",
        "protocol_version",
        "schema_version",
        "request_id",
        "ok",
        "result_code",
        "world_revision",
        "error",
    }


def test_rpc_v2_binds_query_command_boundary_and_restart_receipts(tmp_path: Path) -> None:
    server = DshRpcV2Server()
    _initialize(server, tmp_path)
    _insert_query_and_command_facts(server.runtime)

    capabilities = server.handle(_request("capabilities-live", "capabilities"))
    assert capabilities["ok"] is True
    capability_result = capabilities["result"]
    assert isinstance(capability_result, dict)
    assert capability_result["initialized"] is True
    services = capability_result["services"]
    assert isinstance(services, dict)
    subject_id = server.runtime.subject_id
    assert subject_id is not None
    assert services["query"]["subject_id"] == subject_id
    assert services["command"]["subject_id"] == subject_id
    assert services["portable"]["subject_id"] == subject_id

    world = server.handle(
        _request("world-list", "query_world", {"operation": "list"})
    )
    assert world["ok"] is True
    assert world["result_code"] == "query_ok"
    assert world["world_revision"] == 0
    assert [item["item_id"] for item in world["result"]["items"]] == [  # type: ignore[index]
        "cog-rpc"
    ]

    evidence = server.handle(
        _request("evidence-list", "query_evidence", {"operation": "list"})
    )
    assert evidence["ok"] is True
    assert {item["evidence_id"] for item in evidence["result"]["evidence"]} == {  # type: ignore[index]
        "ev-cognition",
        "ev-command",
    }

    provenance = server.handle(
        _request(
            "provenance-1",
            "query_provenance",
            {"object_kind": "cognition", "item_id": "cog-rpc"},
        )
    )
    assert provenance["ok"] is True
    assert provenance["result"]["provenance"][0]["evidence_id"] == "ev-cognition"  # type: ignore[index]

    recall = server.handle(
        _request("prefetch-1", "prefetch", {"query": "咖啡", "session_id": "dsh-session"})
    )
    assert recall["ok"] is True
    assert recall["result"]["count"] == 1  # type: ignore[index]
    preview = server.handle(
        _request("preview-1", "preview_recall", {"query": "咖啡"})
    )
    assert preview["ok"] is True
    assert preview["result"]["preview"]["selected_item_ids"] == [  # type: ignore[index]
        ["cognition", "cog-rpc"]
    ]

    boundary = _boundary()
    accepted = server.handle(
        _request("boundary-1", "ingest_boundary", {"boundary": boundary})
    )
    assert accepted["ok"] is True
    accepted_result = accepted["result"]
    assert isinstance(accepted_result, dict)
    job_id = accepted_result["job_id"]
    jobs = server.handle(
        _request("jobs-list", "query_jobs", {"operation": "list"})
    )
    assert jobs["ok"] is True
    assert job_id in {item["job_id"] for item in jobs["result"]["jobs"]}  # type: ignore[index]
    jobs_world_revision = jobs["world_revision"]
    assert isinstance(jobs_world_revision, int)

    command = {
        "schema_version": 1,
        "command_id": "rpc-command-1",
        "subject_id": subject_id,
        "actor": "owner",
        "expected_world_revision": jobs_world_revision,
        "operation": "update_evidence_permissions",
        "target_kind": "evidence",
        "target_id": "ev-command",
        "payload": {"allow_cloud_read": False},
        "submitted_at": _T,
    }
    submit_request = _request("submit-command-1", "submit_command", {"command": command})
    submitted = server.handle(submit_request)
    assert submitted["ok"] is True
    assert submitted["result_code"] == "command_applied"
    assert server.handle(submit_request) == submitted
    command_receipt = submitted["result"]["receipt"]  # type: ignore[index]

    queried = server.handle(
        _request(
            "command-receipt-1",
            "query_command_receipt",
            {"command_id": "rpc-command-1"},
        )
    )
    assert queried["result"]["receipt"] == command_receipt  # type: ignore[index]

    server.runtime.shutdown()
    restarted = DshRpcV2Server()
    _initialize(restarted, tmp_path, request_id="initialize-2")
    replayed_boundary = restarted.handle(
        _request("boundary-after-restart", "ingest_boundary", {"boundary": boundary})
    )
    assert replayed_boundary["ok"] is True
    assert replayed_boundary["result"]["job_id"] == job_id  # type: ignore[index]
    restarted_receipt = restarted.handle(
        _request(
            "command-after-restart",
            "query_command_receipt",
            {"command_id": "rpc-command-1"},
        )
    )
    assert restarted_receipt["result"]["receipt"] == command_receipt  # type: ignore[index]
    restarted.runtime.shutdown()


def test_rpc_v2_clarification_and_portable_survive_multiple_restarts(tmp_path: Path) -> None:
    first = DshRpcV2Server()
    _initialize(first, tmp_path)
    source = first.handle(
        _request(
            "clarification-source-boundary",
            "ingest_boundary",
            {"boundary": _boundary()},
        )
    )
    assert source["ok"] is True
    clarification_id = _insert_open_clarification(
        first.runtime, source_job_id=source["result"]["job_id"]  # type: ignore[index]
    )

    listed = first.handle(
        _request(
            "clarifications-open",
            "list_clarifications",
            {"result_session_id": "dsh-session", "state": "open"},
        )
    )
    assert listed["ok"] is True
    assert listed["result"]["clarifications"][0]["clarification_id"] == clarification_id  # type: ignore[index]

    answer_request = _request(
        "clarification-answer-1",
        "answer_clarification",
        {
            "clarification_id": clarification_id,
            "result_session_id": "dsh-session",
            "answer": "手冲咖啡",
        },
    )
    answered = first.handle(answer_request)
    assert answered["ok"] is True, answered["result_code"]
    assert answered["result_code"] == "clarification_answered"
    assert first.handle(answer_request) == answered
    answer_receipt = answered["result"]["receipt"]  # type: ignore[index]
    first.runtime.shutdown()

    second = DshRpcV2Server()
    _initialize(second, tmp_path, request_id="initialize-portable")
    answer_replay = second.handle(
        _request(
            "clarification-answer-after-restart",
            "answer_clarification",
            {
                "clarification_id": clarification_id,
                "result_session_id": "dsh-session",
                "answer": "手冲咖啡",
            },
        )
    )
    assert answer_replay["ok"] is True
    replay_receipt = answer_replay["result"]["receipt"]  # type: ignore[index]
    assert replay_receipt["answer_evidence_id"] == answer_receipt["answer_evidence_id"]
    assert replay_receipt["follow_up_job_id"] == answer_receipt["follow_up_job_id"]
    assert replay_receipt["replayed"] is True

    exported = second.handle(
        _request(
            "portable-export-1",
            "portable_export",
            {"exported_at": "2026-08-25T02:10:00.000Z"},
        )
    )
    assert exported["ok"] is True
    bundle = exported["result"]
    assert isinstance(bundle, dict)
    plan = second.handle(
        _request("portable-plan-1", "portable_plan", {"bundle": bundle})
    )
    assert plan["ok"] is True
    plan_result = plan["result"]
    assert isinstance(plan_result, dict)
    assert plan_result["valid"] is True
    applied_request = _request(
        "portable-apply-1",
        "portable_import",
        {
            "operation": "apply",
            "bundle": bundle,
            "plan_hash": plan_result["plan_hash"],
        },
    )
    applied = second.handle(applied_request)
    assert applied["ok"] is True
    assert second.handle(applied_request) == applied
    portable_receipt = applied["result"]
    assert isinstance(portable_receipt, dict)
    second.runtime.shutdown()

    third = DshRpcV2Server()
    _initialize(third, tmp_path, request_id="initialize-receipt")
    receipt_lookup = third.handle(
        _request(
            "portable-receipt-after-restart",
            "portable_import",
            {
                "operation": "get_receipt",
                "receipt_id": portable_receipt["receipt_id"],
            },
        )
    )
    assert receipt_lookup["ok"] is True
    assert receipt_lookup["result"]["result_hash"] == portable_receipt["result_hash"]  # type: ignore[index]
    applied_again = third.handle(
        _request(
            "portable-apply-after-restart",
            "portable_import",
            {
                "operation": "apply",
                "bundle": bundle,
                "plan_hash": plan_result["plan_hash"],
            },
        )
    )
    assert applied_again["ok"] is True
    assert applied_again["result"]["receipt_id"] == portable_receipt["receipt_id"]  # type: ignore[index]
    assert applied_again["result"]["replayed"] is True  # type: ignore[index]
    third.runtime.shutdown()


def test_stdio_subprocess_restart_replays_all_durable_rpc_families(tmp_path: Path) -> None:
    first = _spawn_bridge()
    second: subprocess.Popen[str] | None = None
    try:
        initialized = _stdio_request(
            first,
            _request(
                "stdio-initialize-1",
                "initialize",
                {
                    "session_id": "dsh-session",
                    "dsh_home": str(tmp_path),
                    "platform": "desktop",
                    "user_id": "rpc-owner",
                    "auto_route": False,
                },
            ),
        )
        assert initialized["ok"] is True
        runtime_result = initialized["result"]["runtime"]  # type: ignore[index]
        db_path = Path(runtime_result["db_path"])
        subject_id = runtime_result["subject_id"]
        host_id = runtime_result["host_id"]

        db = open_db(str(db_path))
        try:
            db.execute(
                "INSERT INTO evidence (id, subject_id, source_kind, host_id, origin_id, "
                "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
                "allow_cloud_read, allow_inference) VALUES ('ev-stdio-command', ?, "
                "'spoken', ?, 'origin:stdio', ?, ?, 'stdio command evidence', "
                "'stdio command evidence', 1, 1, 1)",
                (subject_id, host_id, _T, _T),
            )
            db.commit()
        finally:
            db.close()

        boundary = _boundary()
        accepted = _stdio_request(
            first,
            _request("stdio-boundary-1", "ingest_boundary", {"boundary": boundary}),
        )
        assert accepted["ok"] is True
        source_job_id = accepted["result"]["job_id"]  # type: ignore[index]

        clarification_id = derive_clarification_id(
            source_job_id, "stdio-source-outcome"
        )
        db = open_db(str(db_path))
        try:
            db.execute(
                "INSERT INTO clarification (clarification_id, source_job_id, "
                "source_outcome_id, subject_id, result_session_id, question, "
                "target_hint, state, answer_evidence_id, follow_up_job_id, opened_at, "
                "answered_at, resolved_at) VALUES (?, ?, 'stdio-source-outcome', ?, "
                "'dsh-session', '你说的是哪一种咖啡？', NULL, 'open', NULL, NULL, "
                "?, NULL, NULL)",
                (clarification_id, source_job_id, subject_id, _T),
            )
            db.commit()
        finally:
            db.close()

        answered = _stdio_request(
            first,
            _request(
                "stdio-answer-1",
                "answer_clarification",
                {
                    "clarification_id": clarification_id,
                    "result_session_id": "dsh-session",
                    "answer": "手冲咖啡",
                },
            ),
        )
        assert answered["ok"] is True
        answer_receipt = answered["result"]["receipt"]  # type: ignore[index]

        revision = _stdio_request(
            first,
            _request("stdio-revision-1", "query_world", {"operation": "revision"}),
        )
        command = {
            "schema_version": 1,
            "command_id": "stdio-command-1",
            "subject_id": subject_id,
            "actor": "owner",
            "expected_world_revision": revision["world_revision"],
            "operation": "update_evidence_permissions",
            "target_kind": "evidence",
            "target_id": "ev-stdio-command",
            "payload": {"allow_cloud_read": False},
            "submitted_at": _T,
        }
        submitted = _stdio_request(
            first,
            _request("stdio-command-submit", "submit_command", {"command": command}),
        )
        assert submitted["ok"] is True
        command_receipt = submitted["result"]["receipt"]  # type: ignore[index]

        exported = _stdio_request(
            first,
            _request(
                "stdio-portable-export",
                "portable_export",
                {"exported_at": "2026-08-25T02:20:00.000Z"},
            ),
        )
        bundle = exported["result"]
        assert isinstance(bundle, dict)
        plan = _stdio_request(
            first,
            _request("stdio-portable-plan", "portable_plan", {"bundle": bundle}),
        )
        plan_hash = plan["result"]["plan_hash"]  # type: ignore[index]
        imported = _stdio_request(
            first,
            _request(
                "stdio-portable-apply",
                "portable_import",
                {"operation": "apply", "bundle": bundle, "plan_hash": plan_hash},
            ),
        )
        assert imported["ok"] is True
        portable_receipt = imported["result"]
        assert isinstance(portable_receipt, dict)

        legacy_health = _stdio_request(
            first, {"id": "legacy-health", "method": "health", "params": {}}
        )
        assert legacy_health["id"] == "legacy-health"
        assert legacy_health["ok"] is True
        assert "protocol_version" not in legacy_health

        first_stderr = _shutdown_process(first, request_id="stdio-shutdown-1")
        assert "手冲咖啡" not in first_stderr

        second = _spawn_bridge()
        reinitialized = _stdio_request(
            second,
            _request(
                "stdio-initialize-2",
                "initialize",
                {
                    "session_id": "dsh-session",
                    "dsh_home": str(tmp_path),
                    "platform": "desktop",
                    "user_id": "rpc-owner",
                    "auto_route": False,
                },
            ),
        )
        assert reinitialized["ok"] is True

        boundary_replay = _stdio_request(
            second,
            _request(
                "stdio-boundary-replay",
                "ingest_boundary",
                {"boundary": boundary},
            ),
        )
        assert boundary_replay["result"]["job_id"] == source_job_id  # type: ignore[index]

        command_lookup = _stdio_request(
            second,
            _request(
                "stdio-command-lookup",
                "query_command_receipt",
                {"command_id": "stdio-command-1"},
            ),
        )
        assert command_lookup["result"]["receipt"] == command_receipt  # type: ignore[index]

        clarification_replay = _stdio_request(
            second,
            _request(
                "stdio-answer-replay",
                "answer_clarification",
                {
                    "clarification_id": clarification_id,
                    "result_session_id": "dsh-session",
                    "answer": "手冲咖啡",
                },
            ),
        )
        replayed_answer_receipt = clarification_replay["result"]["receipt"]  # type: ignore[index]
        assert (
            replayed_answer_receipt["answer_evidence_id"]
            == answer_receipt["answer_evidence_id"]
        )
        assert (
            replayed_answer_receipt["follow_up_job_id"]
            == answer_receipt["follow_up_job_id"]
        )
        assert replayed_answer_receipt["replayed"] is True

        portable_lookup = _stdio_request(
            second,
            _request(
                "stdio-portable-lookup",
                "portable_import",
                {
                    "operation": "get_receipt",
                    "receipt_id": portable_receipt["receipt_id"],
                },
            ),
        )
        assert portable_lookup["result"]["result_hash"] == portable_receipt["result_hash"]  # type: ignore[index]
        portable_replay = _stdio_request(
            second,
            _request(
                "stdio-portable-replay",
                "portable_import",
                {"operation": "apply", "bundle": bundle, "plan_hash": plan_hash},
            ),
        )
        assert portable_replay["result"]["receipt_id"] == portable_receipt["receipt_id"]  # type: ignore[index]
        assert portable_replay["result"]["replayed"] is True  # type: ignore[index]

        query_after_restart = _stdio_request(
            second,
            _request("stdio-query-after-restart", "query_jobs", {"operation": "list"}),
        )
        assert query_after_restart["ok"] is True
        second_stderr = _shutdown_process(second, request_id="stdio-shutdown-2")
        assert "手冲咖啡" not in second_stderr
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
