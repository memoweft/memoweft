from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Mapping


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from routes import MemoryExperienceRoutes  # noqa: E402


class FakeTrustClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, params: Mapping[str, object] | None = None) -> dict[str, object]:
        params = dict(params or {})
        self.calls.append((method, params))
        if method == "submit_command":
            return {"receipt": {"command_id": "cmd-1", "after_revision": 4, "result_state": "applied"}}
        if method == "preview_recall":
            recall_query = params.get("query")
            if not isinstance(recall_query, str) or not recall_query.strip():
                raise AssertionError("N4 preview_recall rejects blank query")
            return {"world_revision": 4, "preview": "fresh recall"}
        return {"schema_version": 1, "subject_id": "subject-1", "world_revision": 4, "items": []}


def test_world_detail_uses_client_dtos_not_direct_storage() -> None:
    client = FakeTrustClient()
    response = MemoryExperienceRoutes(client).handle("GET", "/api/world/entity/entity-1")

    assert response.status == 200
    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert ("query_world", {"operation": "get", "object_kind": "entity", "item_id": "entity-1", "include_history": True}) in client.calls
    assert ("query_provenance", {"object_kind": "entity", "item_id": "entity-1"}) in client.calls
    for target in ("/api/recall", "/api/recall?q=%20%20%20"):
        response = MemoryExperienceRoutes(client).handle("GET", target)
        assert response.status == 200
    assert ("preview_recall", {"query": "memory"}) in client.calls


def test_route_returns_safe_error_envelope_without_exception_text() -> None:
    class BrokenClient:
        def call(self, method: str, params: Mapping[str, object] | None = None) -> dict[str, object]:
            del method, params
            raise RuntimeError("C:\\private\\memoweft.sqlite3")

    response = MemoryExperienceRoutes(BrokenClient()).handle("GET", "/api/world")

    assert response.status == 502
    assert json.loads(response.body) == {
        "ok": False,
        "error": {"code": "experience_unavailable", "message": "Memory Experience is temporarily unavailable."},
    }


def test_receipt_revision_mismatch_is_a_safe_route_error() -> None:
    class RevisionMismatchClient(FakeTrustClient):
        def call(self, method: str, params: Mapping[str, object] | None = None) -> dict[str, object]:
            if method == "query_evidence":
                return {"schema_version": 1, "subject_id": "subject-1", "world_revision": 3, "evidence": []}
            return super().call(method, params)

    response = MemoryExperienceRoutes(RevisionMismatchClient()).handle(
        "POST", "/api/commands", {"command": {"operation": "forget_evidence"}}
    )

    assert response.status == 502
    assert json.loads(response.body) == {
        "ok": False,
        "error": {"code": "receipt_revision_mismatch", "message": "Memory Experience is temporarily unavailable."},
    }
