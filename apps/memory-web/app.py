"""MemoWeft Memory Experience stdlib HTTP server.

The server has no storage access.  It hosts native browser assets and forwards
all data requests through ``MemoryExperienceRoutes`` → ``TrustClient`` → N8.
"""
from __future__ import annotations

from argparse import ArgumentParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Mapping

from routes import MemoryExperienceRoutes, RouteResponse
from trust_client import TrustClient


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"


class MemoryExperienceServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], routes: MemoryExperienceRoutes) -> None:
        super().__init__(address, MemoryExperienceHandler)
        self.routes = routes


class MemoryExperienceHandler(BaseHTTPRequestHandler):
    server: MemoryExperienceServer

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path.startswith("/index.html"):
            self._static("index.html", "text/html; charset=utf-8")
            return
        if self.path.startswith("/static/"):
            relative = self.path.split("?", 1)[0].removeprefix("/static/")
            content_type = "text/css; charset=utf-8" if relative.endswith(".css") else "application/javascript; charset=utf-8"
            self._static(relative, content_type)
            return
        self._send(self.server.routes.handle("GET", self.path))

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 2_000_000:
                raise ValueError("invalid_length")
            raw = self.rfile.read(length)
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(decoded, Mapping):
                raise ValueError("json_object_required")
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self._send(RouteResponse(400, json.dumps({"ok": False, "error": {"code": "invalid_request", "message": "The request is invalid."}})))
            return
        self._send(self.server.routes.handle("POST", self.path, decoded))

    def _static(self, relative: str, content_type: str) -> None:
        candidate = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in candidate.parents or not candidate.is_file():
            self._send(RouteResponse(404, json.dumps({"ok": False, "error": {"code": "not_found", "message": "This resource does not exist."}})))
            return
        content = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send(self, response: RouteResponse) -> None:
        body = response.body.encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def create_routes(
    *,
    dsh_home: str,
    platform: str,
    user_id: str,
    subject_id: str | None = None,
    hermes_host_id: str,
    session_id: str = "memory-experience",
) -> MemoryExperienceRoutes:
    """Initialize one subject-bound N8 adapter for this server process."""

    initialization: dict[str, object] = {
        "dsh_home": dsh_home,
        "platform": platform,
        "user_id": user_id,
        "session_id": session_id,
        "auto_route": False,
        "clarification_host_id": hermes_host_id,
    }
    if subject_id is not None:
        initialization["subject_id"] = subject_id
    client = TrustClient.initialized_in_process(
        initialization
    )
    return MemoryExperienceRoutes(client)


def main() -> int:
    parser = ArgumentParser(description="MemoWeft Memory Experience")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8700)
    parser.add_argument("--dsh-home", default=str(APP_ROOT.parents[2] / "memory-web" / "runtime"))
    parser.add_argument("--platform", default="memory-web")
    parser.add_argument("--user-id", default="local-owner")
    parser.add_argument("--subject-id")
    parser.add_argument("--hermes-host-id", required=True)
    args = parser.parse_args()
    server = MemoryExperienceServer(
        (args.host, args.port),
        create_routes(
            dsh_home=args.dsh_home,
            platform=args.platform,
            user_id=args.user_id,
            subject_id=args.subject_id,
            hermes_host_id=args.hermes_host_id,
        ),
    )
    print(
        f"MemoWeft Memory Experience: http://{args.host}:{args.port} "
        f"(Hermes host: {args.hermes_host_id})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
