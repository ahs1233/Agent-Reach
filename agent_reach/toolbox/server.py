"""Small MCP-compatible HTTP server for Ahmed ToolBox."""

from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .gateway import AhmedToolboxGateway, RemoteMCPError

_MAX_REQUEST_BYTES = 1024 * 1024


def _rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


class ToolboxRequestHandler(BaseHTTPRequestHandler):
    gateway: AhmedToolboxGateway
    auth_token: str = ""

    server_version = "AhmedToolbox/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # Keep the gateway quiet by default; host process logging can wrap it.
        if os.environ.get("AHMED_TOOLBOX_HTTP_LOG", "").lower() in {"1", "true", "yes"}:
            super().log_message(fmt, *args)

    def _authorized(self) -> bool:
        if not self.auth_token:
            return True
        header = self.headers.get("Authorization") or ""
        if not header.lower().startswith("bearer "):
            return False
        supplied = header[7:].strip()
        return hmac.compare_digest(supplied, self.auth_token)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path.rstrip("/") == "/health":
            self._send_json(200, {"status": "ok", "service": "ahmed-toolbox"})
            return
        self._send_json(404, {"status": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path.rstrip("/") not in {"", "/mcp"}:
            self._send_json(404, _rpc_error(None, -32601, "not found"))
            return
        if not self._authorized():
            self._send_json(401, _rpc_error(None, -32001, "unauthorized"))
            return

        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, _rpc_error(None, -32700, "invalid content length"))
            return
        if content_length <= 0 or content_length > _MAX_REQUEST_BYTES:
            self._send_json(413, _rpc_error(None, -32700, "request body too large"))
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except Exception:
            self._send_json(400, _rpc_error(None, -32700, "invalid JSON"))
            return
        if not isinstance(payload, dict):
            self._send_json(400, _rpc_error(None, -32600, "request must be an object"))
            return

        method = payload.get("method")
        req_id = payload.get("id")
        params = payload.get("params") or {}

        if req_id is None and isinstance(method, str) and method.startswith("notifications/"):
            self.send_response(202)
            self.end_headers()
            return

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion") or "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "Ahmed ToolBox", "version": "0.1.0"},
                }
                self._send_json(200, _rpc_result(req_id, result))
                return
            if method == "ping":
                self._send_json(200, _rpc_result(req_id, {}))
                return
            if method == "tools/list":
                self._send_json(200, _rpc_result(req_id, {"tools": self.gateway.list_tools()}))
                return
            if method == "tools/call":
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    self._send_json(200, _rpc_error(req_id, -32602, "arguments must be an object"))
                    return
                self._send_json(
                    200,
                    _rpc_result(req_id, self.gateway.call_tool(name, arguments)),
                )
                return
        except RemoteMCPError as exc:
            self._send_json(200, _rpc_error(req_id, -32002, str(exc)))
            return
        except Exception as exc:  # noqa: BLE001 - protocol boundary
            self._send_json(500, _rpc_error(req_id, -32603, f"internal error: {exc}"))
            return

        self._send_json(200, _rpc_error(req_id, -32601, f"unsupported method: {method}"))


def main() -> None:
    host = os.environ.get("AHMED_TOOLBOX_HOST", "127.0.0.1")
    port = int(
        os.environ.get("PORT")
        or os.environ.get("AHMED_TOOLBOX_PORT", "8765")
    )
    token = os.environ.get("AHMED_TOOLBOX_TOKEN", "")

    gateway = AhmedToolboxGateway.from_environment()

    handler = type(
        "ConfiguredToolboxHandler",
        (ToolboxRequestHandler,),
        {"gateway": gateway, "auth_token": token},
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Ahmed ToolBox MCP listening on http://{host}:{port}/mcp")
    server.serve_forever()


if __name__ == "__main__":
    main()
