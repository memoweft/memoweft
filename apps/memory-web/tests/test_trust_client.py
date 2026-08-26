from __future__ import annotations

from pathlib import Path
import sys


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from trust_client import TrustClient, TrustClientError  # noqa: E402
import app as memory_app  # noqa: E402


class RecordingServer:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def handle(self, request: object) -> dict[str, object]:
        assert isinstance(request, dict)
        self.requests.append(request)
        if request["method"] == "query_world":
            return {
                "ok": True,
                "result_code": "query_ok",
                "world_revision": 7,
                "result": {"schema_version": 1, "subject_id": "subject-1", "world_revision": 7, "items": []},
            }
        return {
            "ok": True,
            "result_code": "initialized",
            "world_revision": 0,
            "result": {"runtime": {"subject_id": "subject-1"}},
        }


def test_in_process_client_builds_only_v2_envelopes_and_preserves_dto() -> None:
    server = RecordingServer()
    client = TrustClient.in_process(server)

    result = client.query_world(object_kind="entity")

    assert result["world_revision"] == 7
    request = server.requests[-1]
    assert request["protocol"] == "memoweft.dsh_rpc"
    assert request["protocol_version"] == 2
    assert request["schema_version"] == 1
    assert request["method"] == "query_world"
    assert request["params"] == {"operation": "list", "object_kind": "entity"}


def test_rpc_errors_are_mapped_to_safe_stable_codes() -> None:
    class UnsafeServer:
        def handle(self, request: object) -> dict[str, object]:
            del request
            return {
                "ok": False,
                "result_code": "internal_error",
                "error": {"type": "internal", "message": "C:\\secrets\\world.sqlite3"},
            }

    try:
        TrustClient.in_process(UnsafeServer()).query_world()
    except TrustClientError as error:
        assert error.code == "internal_error"
        assert "sqlite" not in error.safe_message.lower()
    else:
        raise AssertionError("unsafe RPC error must not be presented as success")


def test_create_routes_passes_one_trusted_startup_subject_binding(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def initialized(initialization: dict[str, object]) -> TrustClient:
        captured.update(initialization)
        return TrustClient.in_process(RecordingServer())

    monkeypatch.setattr(
        memory_app.TrustClient, "initialized_in_process", staticmethod(initialized)
    )

    memory_app.create_routes(
        dsh_home="D:/runtime",
        platform="memory-web",
        user_id="presentation-user",
        subject_id="owner",
        hermes_host_id="hermes:cli",
    )

    assert captured == {
        "dsh_home": "D:/runtime",
        "platform": "memory-web",
        "user_id": "presentation-user",
        "subject_id": "owner",
        "clarification_host_id": "hermes:cli",
        "session_id": "memory-experience",
        "auto_route": False,
    }
