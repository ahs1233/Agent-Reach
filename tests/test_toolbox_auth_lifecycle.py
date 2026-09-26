from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from agent_reach.toolbox.auth import AuthConfig, AuthMiddleware
from agent_reach.toolbox.auth_client import AutoRefreshingBearer
from agent_reach.toolbox.server import ToolboxRequestHandler


class _Gateway:
    def list_tools(self):
        return [
            {
                "name": "runtime_status",
                "description": "runtime status",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]

    def call_tool(self, name, arguments, request_id=None):
        assert name == "runtime_status"
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"status": "ok"}),
                }
            ],
            "isError": False,
        }


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


@contextmanager
def _running_server(port: int, config: AuthConfig):
    handler = type(
        "LifecycleHandler",
        (ToolboxRequestHandler,),
        {
            "gateway": _Gateway(),
            "auth_middleware": AuthMiddleware(config),
        },
    )
    server = _ReusableThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _runtime_status(client: AutoRefreshingBearer, request_id: int):
    response = client.request(
        "POST",
        "/mcp",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        },
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": "runtime_status", "arguments": {}},
        },
        timeout=5,
    )
    response.raise_for_status()
    result = response.json()["result"]
    return json.loads(result["content"][0]["text"])


def test_stateless_access_token_survives_middleware_recreation():
    config = AuthConfig(
        legacy_token="legacy",
        refresh_token="refresh",
        signing_secret="signing-secret",
        access_ttl_seconds=30,
    )
    first = AuthMiddleware(config)
    token = first.issue_access_token(requested_ttl_seconds=30, now=100)["access_token"]

    restarted = AuthMiddleware(config)

    assert restarted.validate_access_token(str(token), now=101)


def test_auth_lifecycle_75_calls_expiry_and_server_restart():
    config = AuthConfig(
        legacy_token="legacy",
        refresh_token="refresh",
        signing_secret="signing-secret",
        access_ttl_seconds=1,
    )

    with _running_server(0, config) as port:
        base_url = f"http://127.0.0.1:{port}"
        client = AutoRefreshingBearer(
            base_url,
            "refresh",
            requested_ttl_seconds=1,
            refresh_skew_seconds=0.05,
        )
        for request_id in range(1, 41):
            assert _runtime_status(client, request_id)["status"] == "ok"
        time.sleep(1.1)
        for request_id in range(41, 76):
            assert _runtime_status(client, request_id)["status"] == "ok"
        assert client.refresh_count >= 2

    # Recreate the HTTP server and auth middleware with the same durable secrets.
    # No server-side session or manual rebind is carried across the restart.
    time.sleep(1.1)
    with _running_server(port, config):
        assert _runtime_status(client, 76)["status"] == "ok"
        assert client.refresh_count >= 3
