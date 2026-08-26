"""The sole Memory Experience boundary to versioned Trust RPC v2.

Routes and browser-facing code never open SQLite or construct service objects.
This adapter can talk to an in-process :class:`DshRpcV2Server` (the normal
operator mode) and deliberately exposes only JSON-native, versioned DTOs.
"""
from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Mapping, Protocol


RPC_PROTOCOL = "memoweft.dsh_rpc"
RPC_PROTOCOL_VERSION = 2
RPC_SCHEMA_VERSION = 1


class RpcServer(Protocol):
    def handle(self, request: object) -> dict[str, object]: ...


class TrustClientError(RuntimeError):
    """Stable, presentation-safe failure from the formal Trust boundary."""

    def __init__(self, code: str) -> None:
        self.code = code if code and code.replace("_", "").isalnum() else "experience_unavailable"
        super().__init__(self.code)

    @property
    def safe_message(self) -> str:
        messages = {
            "revision_conflict": "The Memory World changed. Refresh and try again.",
            "not_initialized": "Memory Experience is not initialized yet.",
            "world_item_not_current": "That memory is no longer current.",
            "evidence_not_found": "That Evidence record is unavailable.",
            "clarification_not_found": "That clarification is unavailable.",
            "portable_plan_stale": "The import plan is stale. Create a new plan before applying.",
        }
        return messages.get(self.code, "Memory Experience is temporarily unavailable.")


@dataclass(frozen=True)
class TrustClient:
    """Small typed façade over one subject-bound DSH RPC v2 server."""

    _server: RpcServer

    @classmethod
    def in_process(cls, server: RpcServer) -> "TrustClient":
        """Bind a pre-created N8 server without exposing its Core internals."""

        return cls(server)

    @classmethod
    def initialized_in_process(cls, initialization: Mapping[str, object]) -> "TrustClient":
        """Create and initialize the approved in-process N8 transport.

        ``initialization`` is intentionally the N8 initialize parameter map;
        the DSH runtime derives its SQLite path and subject identity itself.
        """

        from memoweft.integrations.dsh_bridge.protocol_v2 import DshRpcV2Server

        client = cls(DshRpcV2Server())
        client.call("initialize", dict(initialization))
        return client

    def call(self, method: str, params: Mapping[str, object] | None = None) -> dict[str, object]:
        if not isinstance(method, str) or not method:
            raise TrustClientError("invalid_method")
        request = {
            "protocol": RPC_PROTOCOL,
            "protocol_version": RPC_PROTOCOL_VERSION,
            "schema_version": RPC_SCHEMA_VERSION,
            "request_id": "memory-web:" + secrets.token_urlsafe(18),
            "method": method,
            "params": dict(params or {}),
        }
        try:
            response = self._server.handle(request)
        except Exception:  # noqa: BLE001 - server detail never crosses this boundary
            raise TrustClientError("experience_unavailable") from None
        if not isinstance(response, Mapping):
            raise TrustClientError("invalid_rpc_response")
        if response.get("ok") is not True:
            code = response.get("result_code")
            raise TrustClientError(code if isinstance(code, str) else "experience_unavailable")
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise TrustClientError("invalid_rpc_result")
        return dict(result)

    def query_world(self, *, object_kind: str | None = None, item_id: str | None = None, include_history: bool = False) -> dict[str, object]:
        if item_id is not None:
            if not object_kind:
                raise TrustClientError("invalid_object_kind")
            return self.call("query_world", {"operation": "get", "object_kind": object_kind, "item_id": item_id, "include_history": include_history})
        params: dict[str, object] = {"operation": "list"}
        if object_kind:
            params["object_kind"] = object_kind
        if include_history:
            params["include_history"] = True
        return self.call("query_world", params)
