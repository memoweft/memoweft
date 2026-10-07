from __future__ import annotations

import json
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
from threading import Thread
from contextlib import contextmanager

import httpx
import pytest

from memoweft.integrations.dsh_bridge import (
    DshBoundaryError,
    DshMemoWeftRuntime,
    default_one_shot_route,
)
from memoweft.integrations.dsh_bridge.protocol_v2 import (
    DSH_RPC_PROTOCOL,
    DSH_RPC_PROTOCOL_VERSION,
    DSH_RPC_SCHEMA_VERSION,
    DshRpcV2Server,
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker


@contextmanager
def _local_route_server(responses: list[tuple[int, dict[str, str], dict[str, object]]]):
    """Small real HTTP peer: route tests must exercise httpx and response headers."""

    received: list[tuple[str, dict[str, object]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            length = int(self.headers.get("Content-Length", "0"))
            received.append((self.path, json.loads(self.rfile.read(length))))
            status, headers, payload = responses.pop(0)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            received.append((self.path, {}))
            self.send_error(500, "unexpected fallback request")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _boundary() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "provider_name": "memoweft",
        "parent_session_id": "s",
        "result_session_id": "s",
        "mode": "turn",
        "source_messages": [
            {"role": "user", "content": "我喜欢淡香味", "source_ref": "source:0"}
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    payload_hash = sha256(canonical.encode()).hexdigest()
    return {
        **payload,
        "payload_hash": payload_hash,
        "event_id": "weftmate-turn-boundary-v1:" + "b" * 32 + ":" + payload_hash,
    }


def _job_state(db_path: Path) -> str:
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT state FROM memory_world_job").fetchone()
    assert row is not None
    return str(row[0])


def test_local_route_never_falls_back_to_deepseek(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "cloud-secret")
    monkeypatch.delenv("MEMOWEFT_BASE_URL", raising=False)
    monkeypatch.delenv("MEMOWEFT_WORLD_MODEL", raising=False)
    monkeypatch.delenv("MEMOWEFT_API_KEY", raising=False)
    monkeypatch.delenv("MEMOWEFT_API_KEY_ENV", raising=False)

    assert default_one_shot_route(model_tier="local") is None


def test_local_route_accepts_in_memory_key_without_exposing_it(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMOWEFT_BASE_URL", "http://127.0.0.1:18080/v1")
    monkeypatch.setenv("MEMOWEFT_WORLD_MODEL", "weftlearn-qwen3.8-27b")
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [{"message": {"content": '{"result":"no_change"}'}}],
                "usage": {},
            }

    def fake_post(url: str, **kwargs: object) -> _Response:
        captured.update({"url": url, **kwargs})
        return _Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    route = default_one_shot_route(model_tier="local", api_key_override="rpc-secret")
    assert route is not None
    result = route([], session_id="s")
    assert result["model"] == "weftlearn-qwen3.8-27b"
    assert captured["url"] == "http://127.0.0.1:18080/v1/chat/completions"
    assert captured["trust_env"] is False
    assert captured["headers"] == {
        "Authorization": "Bearer rpc-secret",
        "Content-Type": "application/json",
    }
    assert captured["timeout"] == 300.0
    assert captured["json"] == {
        "model": "weftlearn-qwen3.8-27b",
        "messages": [],
        "stream": False,
        "temperature": 0,
        "max_tokens": 4096,
        "enable_thinking": False,
    }

    server = DshRpcV2Server()
    response = server.handle(
        {
            "protocol": DSH_RPC_PROTOCOL,
            "protocol_version": DSH_RPC_PROTOCOL_VERSION,
            "schema_version": DSH_RPC_SCHEMA_VERSION,
            "request_id": "init-local",
            "method": "initialize",
            "params": {
                "session_id": "s",
                "dsh_home": str(tmp_path),
                "model_tier": "local",
                "lang": "zh",
                "model_api_key": "rpc-secret",
            },
        }
    )
    assert response["ok"] is True
    health = server.runtime.health()
    assert health["route_ready"] is True
    assert health["model_tier"] == "local"
    assert "rpc-secret" not in json.dumps(response)
    assert "rpc-secret" not in json.dumps(health)
    server.runtime.shutdown()


def test_follow_current_returns_each_actual_model_from_real_http_response(monkeypatch) -> None:
    responses = [
        (
            200,
            {"X-ModelSwitcher-Model": "occamy-10-35b-a3b"},
            {"choices": [{"message": {"content": "{\"result\":\"no_change\"}"}}], "usage": {}},
        ),
        (
            200,
            {"X-ModelSwitcher-Model": "qwen3.8-27b-ablated-128k"},
            {"choices": [{"message": {"content": "{\"result\":\"no_change\"}"}}], "usage": {}},
        ),
    ]
    with _local_route_server(responses) as (base_url, received):
        monkeypatch.setenv("MEMOWEFT_BASE_URL", base_url)
        monkeypatch.setenv("MEMOWEFT_WORLD_MODEL", "@current")
        route = default_one_shot_route(model_tier="local", api_key_override="test-key")
        assert route is not None
        first = route([], session_id="one")
        second = route([], session_id="two")

    assert first["model"] == "occamy-10-35b-a3b"
    assert second["model"] == "qwen3.8-27b-ablated-128k"
    assert [request[1]["model"] for request in received] == ["@current", "@current"]
    assert [request[0] for request in received] == ["/v1/chat/completions", "/v1/chat/completions"]


@pytest.mark.parametrize("resolved_model", ["", "@current"])
def test_follow_current_rejects_missing_or_alias_model_header(monkeypatch, resolved_model: str) -> None:
    headers = {"X-ModelSwitcher-Model": resolved_model} if resolved_model else {}
    response = {"choices": [{"message": {"content": "{}"}}], "usage": {}}
    with _local_route_server([(200, headers, response)]) as (base_url, _received):
        monkeypatch.setenv("MEMOWEFT_BASE_URL", base_url)
        monkeypatch.setenv("MEMOWEFT_WORLD_MODEL", "@current")
        route = default_one_shot_route(model_tier="local", api_key_override="test-key")
        assert route is not None
        with pytest.raises(DshBoundaryError, match="follow-current response is missing"):
            route([], session_id="s")


def test_follow_current_404_does_not_query_models_or_fall_back(monkeypatch) -> None:
    with _local_route_server([(404, {}, {"error": {"message": "not found"}})]) as (base_url, received):
        monkeypatch.setenv("MEMOWEFT_BASE_URL", base_url)
        monkeypatch.setenv("MEMOWEFT_WORLD_MODEL", "@current")
        route = default_one_shot_route(model_tier="local", api_key_override="test-key")
        assert route is not None
        with pytest.raises(httpx.HTTPStatusError):
            route([], session_id="s")

    assert [request[0] for request in received] == ["/v1/chat/completions"]


def test_follow_current_503_remains_worker_retry_not_no_change(tmp_path: Path, monkeypatch) -> None:
    with _local_route_server([(503, {}, {"error": {"message": "busy"}})]) as (base_url, received):
        monkeypatch.setenv("MEMOWEFT_BASE_URL", base_url)
        monkeypatch.setenv("MEMOWEFT_WORLD_MODEL", "@current")
        monkeypatch.setattr(WorldJobWorker, "start", lambda self: None)
        monkeypatch.setattr(WorldJobWorker, "kick", lambda self: False)
        runtime = DshMemoWeftRuntime()
        runtime.initialize(
            "s",
            dsh_home=str(tmp_path),
            model_tier="local",
            lang="zh",
            model_api_key="test-key",
        )
        runtime.ingest_durable_boundary(_boundary())
        worker = runtime._world_worker
        db_path = runtime.db_path
        assert worker is not None and db_path is not None
        assert worker.run_until_quiescent(max_jobs=1) == 1
        assert _job_state(db_path) == "retry"
        runtime.shutdown()

    assert [request[0] for request in received] == ["/v1/chat/completions"]


def test_cloud_route_rejects_follow_current(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "cloud-test-key")
    monkeypatch.setenv("MEMOWEFT_WORLD_MODEL", "@current")
    with pytest.raises(DshBoundaryError, match="only available for local"):
        default_one_shot_route(model_tier="cloud")


def test_cloud_route_keeps_120_second_timeout(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "cloud-test-key")
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    def fake_post(url: str, **kwargs: object) -> _Response:
        captured.update({"url": url, **kwargs})
        return _Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    route = default_one_shot_route(model_tier="cloud")
    assert route is not None
    route([], session_id="s")
    assert captured["timeout"] == 120.0
    assert captured["json"] == {
        "model": "deepseek-chat",
        "messages": [],
        "stream": False,
        "temperature": 0,
    }


def test_local_length_finish_reason_rejects_partial_json(monkeypatch) -> None:
    monkeypatch.setenv("MEMOWEFT_BASE_URL", "http://127.0.0.1:18080/v1")
    monkeypatch.setenv("MEMOWEFT_WORLD_MODEL", "weftlearn-qwen3.8-27b")

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {"content": '{"schema_version":8,"result":'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"completion_tokens": 4096},
            }

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: _Response())
    route = default_one_shot_route(model_tier="local", api_key_override="test-key")
    assert route is not None
    with pytest.raises(RuntimeError, match="^local_model_output_truncated$"):
        route([], session_id="s")


def test_missing_local_route_leaves_worker_stopped_and_recoverable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MEMOWEFT_BASE_URL", raising=False)
    monkeypatch.delenv("MEMOWEFT_WORLD_MODEL", raising=False)
    monkeypatch.delenv("MEMOWEFT_API_KEY", raising=False)
    monkeypatch.delenv("MEMOWEFT_API_KEY_ENV", raising=False)
    runtime = DshMemoWeftRuntime()
    runtime.initialize("s", dsh_home=str(tmp_path), model_tier="local", lang="zh")

    health = runtime.health()
    assert health["route_ready"] is False
    assert health["route_error"] == "local_route_not_configured"
    assert health["worker_running"] is False
    runtime.ingest_durable_boundary(_boundary())
    db_path = runtime.db_path
    assert db_path is not None
    assert _job_state(db_path) == "pending"
    runtime.shutdown()

    monkeypatch.setenv("MEMOWEFT_TESTING", "1")
    monkeypatch.setenv("MEMOWEFT_TEST_MODEL_RESPONSE", "__smart__")
    monkeypatch.setattr(WorldJobWorker, "start", lambda self: None)
    restored = DshMemoWeftRuntime()
    restored.initialize("s", dsh_home=str(tmp_path), model_tier="local", lang="zh")
    assert restored._world_worker is not None
    assert restored._world_worker.run_until_quiescent() == 1
    assert _job_state(db_path) == "applied"
    restored.shutdown()


def test_local_connection_failure_retries_then_applies_without_duplicate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MEMOWEFT_TESTING", "1")
    monkeypatch.setenv("MEMOWEFT_TEST_MODEL_RESPONSE", "__smart__")
    smart_route = default_one_shot_route(model_tier="local")
    assert smart_route is not None
    available = False

    def intermittent_route(messages: object, session_id: str = ""):
        if not available:
            raise httpx.ConnectError("connection refused")
        return smart_route(messages, session_id=session_id)

    monkeypatch.setattr(WorldJobWorker, "start", lambda self: None)
    monkeypatch.setattr(WorldJobWorker, "kick", lambda self: False)
    runtime = DshMemoWeftRuntime()
    runtime.initialize(
        "s",
        dsh_home=str(tmp_path),
        model_tier="local",
        lang="zh",
        one_shot_llm=intermittent_route,
    )
    runtime.ingest_durable_boundary(_boundary())
    worker = runtime._world_worker
    db_path = runtime.db_path
    assert worker is not None and db_path is not None
    assert worker.run_until_quiescent(max_jobs=1) == 1
    assert _job_state(db_path) == "retry"

    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE memory_world_job SET next_attempt_at = '1970-01-01T00:00:00.000Z'"
        )
    available = True
    assert worker.run_until_quiescent(max_jobs=1) == 1
    assert _job_state(db_path) == "applied"
    with sqlite3.connect(db_path) as db:
        evidence_count = db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        cognition_count = db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0]
    assert evidence_count == cognition_count == 1
    runtime.shutdown()
