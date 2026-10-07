"""HTTP-independent routes for the stdlib Memory Experience server."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Protocol
from urllib.parse import parse_qs, urlsplit

from trust_client import TrustClientError


class TrustBoundary(Protocol):
    def call(self, method: str, params: Mapping[str, object] | None = None) -> dict[str, object]: ...


@dataclass(frozen=True)
class RouteResponse:
    status: int
    body: str


def _json(payload: Mapping[str, object], status: int = 200) -> RouteResponse:
    return RouteResponse(status, json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")))


class MemoryExperienceRoutes:
    """Routes whose only business dependency is ``TrustClient``."""

    def __init__(self, client: TrustBoundary) -> None:
        self._client = client

    def handle(self, method: str, target: str, body: Mapping[str, object] | None = None) -> RouteResponse:
        try:
            return self._handle(method.upper(), target, dict(body or {}))
        except TrustClientError as error:
            return _json({"ok": False, "error": {"code": error.code, "message": error.safe_message}}, 409 if error.code == "revision_conflict" else 502)
        except (KeyError, TypeError, ValueError):
            return _json({"ok": False, "error": {"code": "invalid_request", "message": "The request is invalid."}}, 400)
        except Exception:  # noqa: BLE001 - no internal exception/path enters browser payload
            return _json({"ok": False, "error": {"code": "experience_unavailable", "message": "Memory Experience is temporarily unavailable."}}, 502)

    def _handle(self, method: str, target: str, body: dict[str, object]) -> RouteResponse:
        parsed = urlsplit(target)
        path = parsed.path.rstrip("/") or "/"
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
        if method == "GET" and path == "/api/health":
            return _json({"ok": True, "data": self._client.call("health")})
        if method == "GET" and path == "/api/world":
            params: dict[str, object] = {"operation": "list"}
            if query.get("kind"):
                params["object_kind"] = query["kind"]
            if query.get("history") == "true":
                params["include_history"] = True
            return _json({"ok": True, "data": self._client.call("query_world", params)})
        if method == "GET" and path.startswith("/api/world/"):
            pieces = path.split("/")
            if len(pieces) != 5:
                raise ValueError("invalid_world_route")
            kind, item_id = pieces[3], pieces[4]
            item = self._client.call("query_world", {"operation": "get", "object_kind": kind, "item_id": item_id, "include_history": True})
            provenance = self._client.call("query_provenance", {"object_kind": kind, "item_id": item_id})
            return _json({"ok": True, "data": {"item": item, "provenance": provenance}})
        if method == "GET" and path == "/api/evidence":
            return _json({"ok": True, "data": self._client.call("query_evidence", {"operation": "list"})})
        if method == "GET" and path.startswith("/api/evidence/"):
            return _json({"ok": True, "data": self._client.call("query_evidence", {"operation": "get", "evidence_id": path.rsplit("/", 1)[1]})})
        if method == "GET" and path == "/api/jobs":
            return _json({"ok": True, "data": self._client.call("query_jobs", {"operation": "list"})})
        if method == "GET" and path.startswith("/api/jobs/"):
            return _json({"ok": True, "data": self._client.call("query_jobs", {"operation": "get", "job_id": path.rsplit("/", 1)[1]})})
        if method == "GET" and path.startswith("/api/commands/"):
            return _json({"ok": True, "data": self._client.call("query_command_receipt", {"command_id": path.rsplit("/", 1)[1]})})
        if method == "GET" and path == "/api/clarifications":
            params = {key: value for key, value in query.items() if key in {"state", "result_session_id"}}
            return _json({"ok": True, "data": self._client.call("list_clarifications", params)})
        if method == "GET" and path == "/api/recall":
            return _json({"ok": True, "data": self._client.call("preview_recall", {"query": self._recall_query(query.get("q"))})})
        if method == "POST" and path == "/api/commands":
            command = body.get("command")
            if not isinstance(command, Mapping):
                raise ValueError("command_required")
            result = self._client.call("submit_command", {"command": dict(command)})
            receipt = self._receipt(result)
            return _json({"ok": True, "data": {"receipt": receipt, "refresh": self._refresh(self._after_revision(result, receipt), str(body.get("recall_query") or " "))}})
        if method == "POST" and path.startswith("/api/clarifications/") and path.endswith("/answer"):
            pieces = path.split("/")
            if len(pieces) != 5:
                raise ValueError("invalid_clarification_route")
            result = self._client.call("answer_clarification", {"clarification_id": pieces[3], "result_session_id": self._text(body, "result_session_id"), "answer": self._text(body, "answer")})
            receipt = self._receipt(result)
            return _json({"ok": True, "data": {"receipt": receipt, "refresh": self._refresh(self._after_revision(result, receipt), "memory")}})
        if method == "POST" and path == "/api/portable/export":
            params = {"exported_at": body["exported_at"]} if isinstance(body.get("exported_at"), str) else {}
            return _json({"ok": True, "data": self._client.call("portable_export", params)})
        if method == "POST" and path == "/api/portable/plan":
            bundle = body.get("bundle")
            if not isinstance(bundle, Mapping):
                raise ValueError("bundle_required")
            return _json({"ok": True, "data": self._client.call("portable_plan", {"bundle": dict(bundle)})})
        if method == "POST" and path == "/api/portable/apply":
            bundle, plan_hash = body.get("bundle"), body.get("plan_hash")
            if not isinstance(bundle, Mapping) or not isinstance(plan_hash, str):
                raise ValueError("portable_apply_required")
            result = self._client.call("portable_import", {"operation": "apply", "bundle": dict(bundle), "plan_hash": plan_hash})
            receipt = self._receipt(result)
            return _json({"ok": True, "data": {"receipt": receipt, "refresh": self._refresh(self._after_revision(result, receipt), "memory")}})
        return _json({"ok": False, "error": {"code": "not_found", "message": "This Memory Experience route does not exist."}}, 404)

    @staticmethod
    def _text(body: Mapping[str, object], key: str) -> str:
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(key)
        return value

    @staticmethod
    def _receipt(result: Mapping[str, object]) -> dict[str, object]:
        receipt = result.get("receipt", result)
        if not isinstance(receipt, Mapping) or not any(key in receipt for key in ("command_id", "receipt_id", "clarification_id")):
            raise TrustClientError("receipt_unavailable")
        return dict(receipt)

    @staticmethod
    def _after_revision(result: Mapping[str, object], receipt: Mapping[str, object]) -> int:
        for key in ("after_revision", "after_world_revision", "world_revision"):
            value = receipt.get(key, result.get(key))
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        raise TrustClientError("receipt_revision_unavailable")

    @staticmethod
    def _recall_query(value: object) -> str:
        return value.strip() if isinstance(value, str) and value.strip() else "memory"

    def _refresh(self, after_revision: int, recall_query: str) -> dict[str, object]:
        world = self._client.call("query_world", {"operation": "list"})
        evidence = self._client.call("query_evidence", {"operation": "list"})
        recall = self._client.call("preview_recall", {"query": self._recall_query(recall_query)})
        revisions = [value.get("world_revision") for value in (world, evidence, recall) if isinstance(value.get("world_revision"), int)]
        if revisions and any(revision != after_revision for revision in revisions):
            raise TrustClientError("receipt_revision_mismatch")
        return {"world_revision": after_revision, "world": world, "evidence": evidence, "recall": recall}
