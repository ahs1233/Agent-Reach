import json
import threading
from http.server import ThreadingHTTPServer

import pytest
import requests

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.runtime import RuntimeStore
from agent_reach.toolbox.server import ToolboxRequestHandler, _initialize_result

RUNTIME = {
    "runtime_status", "runtime_execute_workflow", "runtime_delegate",
    "runtime_memory_put", "runtime_memory_search", "runtime_session_search",
    "runtime_skill_save", "runtime_skill_list", "runtime_skill_get",
    "runtime_skill_rollback", "runtime_skill_execute",
}


@pytest.mark.parametrize("version", ["2024-11-05", "2025-03-26", "2025-06-18"])
def test_initialize_negotiates_supported_version(version):
    result = _initialize_result({"protocolVersion": version})
    assert result["protocolVersion"] == version
    assert result["capabilities"]["tools"]["listChanged"] is False


def test_initialize_does_not_echo_unsupported_version():
    assert _initialize_result({"protocolVersion": "2099-01-01"})["protocolVersion"] == "2025-06-18"


@pytest.fixture
def endpoint(tmp_path):
    class Reach:
        def doctor(self):
            return {"core": {"status": "ok"}}

    gateway = AhmedToolboxGateway(
        agent_reach=Reach(), runtime_enabled=True, ace_enabled=False,
        runtime_store=RuntimeStore(str(tmp_path / "runtime.db")),
        research_enabled=False, orchestration_enabled=False,
    )
    handler = type("Handler", (ToolboxRequestHandler,), {
        "gateway": gateway, "auth_token": "test-token",
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_discovery_and_runtime_acceptance(endpoint):
    headers = {"Authorization": "Bearer test-token", "Accept": "application/json, text/event-stream"}

    def rpc(method, params=None, notification=False):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            body["id"] = 1
        response = requests.post(endpoint, json=body, headers=headers, timeout=10)
        assert response.headers["Cache-Control"] == "no-store"
        assert response.status_code == (202 if notification else 200)
        if notification:
            assert not response.content
            return None
        payload = response.json()
        assert "error" not in payload
        return payload["result"]

    initialized = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "regression", "version": "1"}})
    headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
    rpc("notifications/initialized", notification=True)
    names = [spec["name"] for spec in rpc("tools/list")["tools"]]
    assert len(names) == len(set(names))
    assert RUNTIME <= set(names)
    status = rpc("tools/call", {"name": "runtime_status"})
    assert not status["isError"]
    assert json.loads(status["content"][0]["text"])["status"] == "ok"
    workflow = rpc("tools/call", {"name": "runtime_execute_workflow", "arguments": {
        "steps": [{"id": "doctor", "tool_name": "reach_doctor", "arguments": {}}],
    }})
    result = json.loads(workflow["content"][0]["text"])
    assert result["status"] == "ok" and result["ok_count"] == 1
    legacy = rpc("tools/call", {"name": "reach_doctor"})
    assert json.loads(legacy["content"][0]["text"])["core"]["status"] == "ok"


def test_http_auth_and_transport_errors(endpoint):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert requests.post(endpoint, json=body, timeout=5).status_code == 401
    headers = {"Authorization": "Bearer wrong"}
    assert requests.post(endpoint, json=body, headers=headers, timeout=5).status_code == 401
    headers = {"Authorization": "Bearer test-token", "MCP-Protocol-Version": "invalid"}
    assert requests.post(endpoint, json=body, headers=headers, timeout=5).status_code == 400
    response = requests.get(endpoint, timeout=5)
    assert response.status_code == 405 and response.headers["Allow"] == "POST"
    headers.pop("MCP-Protocol-Version")
    body["params"] = ["invalid"]
    response = requests.post(endpoint, json=body, headers=headers, timeout=5)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602
