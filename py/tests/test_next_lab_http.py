"""HTTP boundary tests for the loopback-only Next Lab server."""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Collection, Mapping

LAB = Path(__file__).resolve().parents[2] / "next-lab"
sys.path.insert(0, str(LAB))
from next_lab_core import LabService  # noqa: E402
from next_lab_server import Handler, NextLabHTTPServer  # noqa: E402


def test_handler_host_origin_headers_body_limit_and_routes(tmp_path: Path) -> None:
    Handler.service, Handler.bind_port, Handler.instance_token = LabService(tmp_path), 0, "test-token"
    server = NextLabHTTPServer(("127.0.0.1", 0), Handler)
    Handler.bind_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def call(method: str, path: str, body: bytes | None = None, **headers: str) -> http.client.HTTPResponse:
            c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            c.request(method, path, body=body, headers={"Host": f"127.0.0.1:{server.server_port}", **headers})
            return c.getresponse()
        response = call("GET", "/api/status")
        assert response.status == 200 and response.getheader("X-Frame-Options") == "DENY"
        assert response.getheader("Content-Security-Policy") == "frame-ancestors 'none'"
        world_response = call("GET", "/api/memory-world")
        world = json.loads(world_response.read())
        assert world_response.status == 200 and world["revision"] == 0 and world["worldId"] == "world:next-lab-owner"
        assert call("GET", "/api/status", Host="evil.invalid").status == 403
        payload = b'{"scenarioIds":["golden-1-nanjing"]}'
        run_response = call("POST", "/api/run", payload, Origin=f"http://127.0.0.1:{server.server_port}", **{"Content-Type":"application/json"})
        run = json.loads(run_response.read())
        assert run_response.status == 200
        scenario = run["scenarios"][0]
        review = json.dumps({"runId": run["id"], "scenarioId": "golden-1-nanjing", "worldHash": scenario["worldHash"], "verdict": "needs-discussion", "notes": "HTTP-bound local note"}).encode()
        review_response = call("POST", "/api/review", review, Origin=f"http://127.0.0.1:{server.server_port}", **{"Content-Type":"application/json"})
        assert review_response.status == 200
        bad_review = json.dumps({"runId": run["id"], "scenarioId": "golden-1-nanjing", "worldHash": "sha256:not-the-run", "verdict": "needs-discussion", "notes": "must not bind"}).encode()
        assert call("POST", "/api/review", bad_review, Origin=f"http://127.0.0.1:{server.server_port}", **{"Content-Type":"application/json"}).status == 400
        for origin in (None, "null", "http://evil.invalid"):
            headers = {"Content-Type":"application/json"}
            if origin is not None: headers["Origin"] = origin
            assert call("POST", "/api/run", payload, **headers).status == 403
        try:
            oversized = call("POST", "/api/run", b"x" * 65537, Origin=f"http://127.0.0.1:{server.server_port}", **{"Content-Type":"application/json"}).status
        except ConnectionAbortedError:  # Windows closes an unread over-limit body connection.
            oversized = 400
        assert oversized == 400
        assert call("POST", "/api/nope", payload, Origin=f"http://127.0.0.1:{server.server_port}", **{"Content-Type":"application/json"}).status == 404
        assert call("POST", "/api/memory-decisions", b"{}", Origin=f"http://127.0.0.1:{server.server_port}", **{"Content-Type":"application/json"}).status == 400
        assert call("POST", "/api/memory-queries", b'{"query":"x"}', Origin="http://evil.invalid", **{"Content-Type":"application/json"}).status == 403
        assert call("POST", "/api/memory-corrections", b"{}", Origin=f"http://127.0.0.1:{server.server_port}", **{"Content-Type":"application/json"}).status == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_memory_run_routes_validate_input_and_keep_origin_boundary(tmp_path: Path) -> None:
    from memoweft.world import WorldExtractionError

    def fake_failure(base: object, turns: tuple[object, ...], allowlist: object) -> object:
        raise WorldExtractionError(("TEST_SAFE_FAILURE@$",), attempts=1)

    Handler.service, Handler.bind_port, Handler.instance_token = LabService(tmp_path, memory_run_executor=fake_failure), 0, "test-token"
    server = NextLabHTTPServer(("127.0.0.1", 0), Handler)
    Handler.bind_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def call(path: str, body: Mapping[str, object], origin: str | None) -> http.client.HTTPResponse:
            headers = {"Host": f"127.0.0.1:{server.server_port}", "Content-Type": "application/json"}
            if origin is not None:
                headers["Origin"] = origin
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("POST", path, body=json.dumps(body).encode(), headers=headers)
            return connection.getresponse()

        valid = {"title": "情景", "turns": [{"role": "user", "content": "我喜欢开车"}, {"role": "assistant", "content": "知道了"}]}
        origin = f"http://127.0.0.1:{server.server_port}"
        response = call("/api/memory-runs", valid, origin)
        payload = json.loads(response.read())
        assert response.status == 200 and payload["failure"]["codes"] == ["TEST_SAFE_FAILURE@$"]
        assert payload["assistantContext"][0]["note"] == "仅作上下文，不作为用户证据"
        assert call("/api/memory-runs", valid, "http://evil.invalid").status == 403
        invalid = {"title": "情景", "turns": [{"role": "tool", "content": "不允许"}]}
        assert call("/api/memory-runs", invalid, origin).status == 400
        assert call("/api/memory-evaluations", {"runId": payload["id"], "resultHash": "wrong", "verdict": "correct", "notes": "x"}, origin).status == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_adapter_memory_turn_route_is_loopback_only_and_idempotent(tmp_path: Path) -> None:
    from memoweft.world import Entity, WorldDelta

    observed: dict[str, int] = {"calls": 0}

    class NoCorrection:
        def chat(self, messages: object) -> str:
            return '{"is_correction":false,"prior_cognition_ids":[],"structure_hints":[]}'

    def fake_executor(base: Any, turns: tuple[Any, ...], allowlist: Collection[str]) -> WorldDelta:
        observed["calls"] += 1
        source = next(iter(allowlist))
        world_id = base.world.world_id
        return WorldDelta(
            world_id=world_id,
            source_evidence_ids=(source,),
            new_entities=(Entity("entity:adapter", world_id, "person", "适配器候选"),),
        )

    Handler.service, Handler.bind_port, Handler.instance_token = LabService(
        tmp_path,
        memory_run_executor=fake_executor,
        correction_client_factory=NoCorrection,
    ), 0, "test-token"
    server = NextLabHTTPServer(("127.0.0.1", 0), Handler)
    Handler.bind_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        body = {
            "operationId": "http-adapter-operation-001",
            "sessionId": "http-adapter-session-001",
            "currentUserTurnId": "http-adapter-turn-003",
            "carryForwardEvidenceIds": [],
            "turns": [
                {"turnId": "http-adapter-turn-001", "role": "user", "content": "旧 user 上下文", "occurredAt": "2026-08-09T08:00:00Z"},
                {"turnId": "http-adapter-turn-002", "role": "assistant", "content": "旧 assistant 上下文", "occurredAt": "2026-08-09T08:00:01Z"},
                {"turnId": "http-adapter-turn-003", "role": "user", "content": "当前 user Evidence", "occurredAt": "2026-08-09T08:00:02Z"},
            ],
        }

        def call(payload: Mapping[str, object], request_origin: str) -> http.client.HTTPResponse:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request(
                "POST",
                "/api/adapter-memory-turns",
                body=json.dumps(payload).encode(),
                headers={
                    "Host": f"127.0.0.1:{server.server_port}",
                    "Origin": request_origin,
                    "Content-Type": "application/json",
                },
            )
            return connection.getresponse()

        first_response = call(body, origin)
        first = json.loads(first_response.read())
        assert first_response.status == 200
        assert set(first) == {"memoryProposal", "memoryFailure", "pipeline", "world", "run"}
        assert first["memoryProposal"]["evidence"] == [{"evidenceId": "http-adapter-turn-003", "text": "当前 user Evidence"}]
        retry_response = call(body, origin)
        retry = json.loads(retry_response.read())
        assert retry_response.status == 200 and retry["run"]["id"] == first["run"]["id"]
        assert observed["calls"] == 1
        assert call(body, "http://evil.invalid").status == 403
        assert call({**body, "unexpected": True}, origin).status == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_legacy_evidence_import_route_is_loopback_only_and_enforces_exact_raw_body(tmp_path: Path) -> None:
    from memoweft.world import Entity, WorldDelta

    observed: dict[str, int] = {"calls": 0}

    def fake_executor(base: Any, turns: tuple[Any, ...], allowlist: Collection[str]) -> WorldDelta:
        observed["calls"] += 1
        return WorldDelta(
            world_id=base.world.world_id,
            source_evidence_ids=tuple(sorted(allowlist)),
            new_entities=(Entity("entity:legacy-import", base.world.world_id, "person", "迁移候选"),),
        )

    Handler.service, Handler.bind_port, Handler.instance_token = LabService(tmp_path, memory_run_executor=fake_executor), 0, "test-token"
    server = NextLabHTTPServer(("127.0.0.1", 0), Handler)
    Handler.bind_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        body = {
            "operationId": "http-legacy-import-001",
            "turns": [{"turnId": "http-legacy-import-turn", "role": "user", "content": "这是原始用户陈述。", "occurredAt": "2026-08-10T00:00:00Z"}],
        }

        def call(payload: Mapping[str, object], request_origin: str) -> http.client.HTTPResponse:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request(
                "POST",
                "/api/adapter-legacy-memory-imports",
                body=json.dumps(payload).encode(),
                headers={
                    "Host": f"127.0.0.1:{server.server_port}",
                    "Origin": request_origin,
                    "Content-Type": "application/json",
                },
            )
            return connection.getresponse()

        first_response = call(body, origin)
        first = json.loads(first_response.read())
        assert first_response.status == 200
        assert set(first) == {"memoryProposal", "memoryFailure", "pipeline", "world", "run"}
        assert first["run"]["legacyEvidenceReplay"]["kind"] == "raw-user-evidence-only"
        retry_response = call(body, origin)
        retry = json.loads(retry_response.read())
        assert retry_response.status == 200 and retry["run"]["id"] == first["run"]["id"]
        assert observed["calls"] == 1
        blocked_response = call({
            "operationId": "http-legacy-import-pending-blocked",
            "turns": [{"turnId": "http-legacy-import-blocked-turn", "role": "user", "content": "这条不得进入新的迁移。", "occurredAt": "2026-08-10T00:01:00Z"}],
        }, origin)
        assert blocked_response.status == 400
        assert json.loads(blocked_response.read()) == {"error": "LEGACY_IMPORT_PENDING_REVIEW_EXISTS"}
        assert observed["calls"] == 1
        assert len(Handler.service.memory_runs()["runs"]) == 1
        assert call(body, "http://evil.invalid").status == 403
        assert call({**body, "cognition": "forbidden"}, origin).status == 400
        assert call({"operationId": "http-legacy-import-bad", "turns": []}, origin).status == 400
        oversized = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        oversized.request(
            "POST",
            "/api/adapter-legacy-memory-imports",
            body=b"x" * 65537,
            headers={
                "Host": f"127.0.0.1:{server.server_port}",
                "Origin": origin,
                "Content-Type": "application/json",
            },
        )
        assert oversized.getresponse().status == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_memory_recall_route_is_loopback_only_and_strict(tmp_path: Path) -> None:
    Handler.service, Handler.bind_port, Handler.instance_token = LabService(tmp_path), 0, "test-token"
    server = NextLabHTTPServer(("127.0.0.1", 0), Handler)
    Handler.bind_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"

        def call(payload: Mapping[str, object], request_origin: str) -> http.client.HTTPResponse:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request(
                "POST",
                "/api/memory-recalls",
                body=json.dumps(payload).encode(),
                headers={
                    "Host": f"127.0.0.1:{server.server_port}",
                    "Origin": request_origin,
                    "Content-Type": "application/json",
                },
            )
            return connection.getresponse()

        response = call({"query": "不存在的内容"}, origin)
        assert response.status == 200 and json.loads(response.read()) == {"status": "no_memory", "memories": []}
        assert call({"query": "x", "unexpected": True}, origin).status == 400
        assert call({}, origin).status == 400
        assert call({"query": "x" * 1201}, origin).status == 400
        assert call({"query": "x"}, "http://evil.invalid").status == 403
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_idle_browser_preconnect_cannot_starve_the_single_threaded_server(tmp_path: Path) -> None:
    Handler.service, Handler.bind_port, Handler.instance_token = LabService(tmp_path), 0, "test-token"
    server = NextLabHTTPServer(("127.0.0.1", 0), Handler)
    server.request_read_timeout_seconds = 0.1
    Handler.bind_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    idle = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
    try:
        time.sleep(0.2)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        connection.request("GET", "/api/status", headers={"Host": f"127.0.0.1:{server.server_port}"})
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        connection.close()
    finally:
        idle.close()
        server.shutdown(); server.server_close(); thread.join(2)
