"""WeftMate DSH bridge — stdio JSON-Lines server entry point.

Run: ``python -m memoweft.integrations.dsh_bridge`` (WeftMate host plugin spawns
this long-lived subprocess; one JSON request per line in, one JSON response per
line out; logs and diagnostics go to stderr and never carry user content).

Request methods:
  initialize      {session_id, dsh_home, platform?, user_id?, auto_route?}
  ingest_boundary {boundary}
  prefetch        {query, session_id?}
  health          {}
  shutdown        {}
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from . import DshBoundaryError, DshMemoWeftRuntime


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


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    runtime = DshMemoWeftRuntime()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _send({"id": None, "ok": False, "error": {"type": "BadRequest", "message": "invalid JSON line"}})
            continue
        request_id = request.get("id")
        method = request.get("method")
        params: Any = request.get("params") if isinstance(request.get("params"), dict) else {}
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
                return 0
            else:
                raise DshBoundaryError(f"unknown method {method!r}")
        except Exception as exc:  # noqa: BLE001 - bridge must never die on a bad request
            _fail(request_id, exc)
    runtime.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
