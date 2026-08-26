"""WeftMate DSH bridge — stdio JSON-Lines server entry point.

Run: ``python -m memoweft.integrations.dsh_bridge`` (WeftMate host plugin spawns
this long-lived subprocess; one JSON request per line in, one JSON response per
line out; logs and diagnostics go to stderr and never carry user content).

Versioned requests use ``protocol_v2.DshRpcV2Server``. Existing unversioned
requests retain the pre-N8 response shape for host compatibility.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Mapping

from . import DshBoundaryError, DshMemoWeftRuntime
from .protocol_v2 import DshRpcV2Server


def _send(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _reply(request_id: object, result: object) -> None:
    _send({"id": request_id, "ok": True, "result": result})


def _fail(request_id: object, error: BaseException) -> None:
    _send(
        {
            "id": request_id,
            "ok": False,
            "error": {"type": type(error).__name__, "message": str(error)},
        }
    )


def _legacy_dispatch(
    runtime: DshMemoWeftRuntime,
    request: Mapping[str, object],
) -> bool:
    """Dispatch one pre-N8 unversioned request; return True after shutdown."""

    request_id = request.get("id")
    method = request.get("method")
    raw_params = request.get("params")
    params: dict[str, Any] = dict(raw_params) if isinstance(raw_params, Mapping) else {}
    try:
        if method == "initialize":
            init_params = dict(params)
            session_id = str(init_params.pop("session_id", "") or "")
            _reply(request_id, runtime.initialize(session_id, **init_params))
        elif method == "ingest_boundary":
            boundary = params.get("boundary")
            if not isinstance(boundary, dict):
                raise DshBoundaryError("boundary params must carry a boundary object")
            _reply(request_id, runtime.ingest_durable_boundary(boundary))
        elif method == "prefetch":
            _reply(
                request_id,
                runtime.prefetch(
                    str(params.get("query") or ""),
                    session_id=str(params.get("session_id") or ""),
                ),
            )
        elif method == "list_world":
            _reply(request_id, runtime.list_world())
        elif method == "export_world":
            _reply(request_id, runtime.export_world())
        elif method == "health":
            _reply(request_id, runtime.health())
        elif method == "shutdown":
            runtime.shutdown()
            _reply(request_id, {"ok": True})
            return True
        else:
            raise DshBoundaryError(f"unknown method {method!r}")
    except Exception as exc:  # noqa: BLE001 - legacy bridge must stay alive
        _fail(request_id, exc)
    return False


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = DshRpcV2Server()
    runtime = server.runtime
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _send({"id": None, "ok": False, "error": {"type": "BadRequest", "message": "invalid JSON line"}})
            continue
        if isinstance(request, Mapping) and any(
            key in request for key in ("protocol", "protocol_version", "request_id")
        ):
            _send(server.handle(request))
            if server.shutdown_requested:
                return 0
            continue
        if not isinstance(request, Mapping):
            _fail(None, DshBoundaryError("request must be a JSON object"))
            continue
        if _legacy_dispatch(runtime, request):
            return 0
    runtime.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
