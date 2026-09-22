from __future__ import annotations

import json

from agent_reach.toolbox.gateway import (
    AhmedToolboxGateway,
    RemoteMCPClient,
    RemoteMCPConfig,
    RemoteMCPError,
)


class _FakeReach:
    def doctor(self):
        return {"web": {"status": "ok"}}


class _FakeRemote:
    def __init__(self, allowed=("fetch",)):
        self.allowed = set(allowed)

    def is_tool_allowed(self, name):
        return name in self.allowed

    def list_tools(self):
        return [
            {
                "name": "fetch",
                "description": "Fetch a page",
                "inputSchema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                },
            },
            {
                "name": "make_request",
                "description": "Any-method request",
                "inputSchema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                },
            },
        ]

    def call_tool(self, name, arguments):
        assert self.is_tool_allowed(name)
        return {
            "content": [{"type": "text", "text": f"fetched {arguments['url']}"}],
            "isError": False,
        }


class _BrokenRemote(_FakeRemote):
    def list_tools(self):
        raise RemoteMCPError("offline")


class _HTTPResponse:
    def __init__(self, payload, *, headers=None, status_code=200):
        self.content = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP status {self.status_code}")

    def iter_content(self, chunk_size=65536):
        del chunk_size
        yield self.content


class _FakeHTTPSession:
    def __init__(self):
        self.calls = []

    def post(self, url, *, headers, json, timeout, stream):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "json": dict(json),
                "timeout": timeout,
                "stream": stream,
            }
        )
        method = json.get("method")
        if method == "initialize":
            return _HTTPResponse(
                {
                    "jsonrpc": "2.0",
                    "id": json["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                    },
                },
                headers={"Mcp-Session-Id": "session-123"},
            )
        if method == "notifications/initialized":
            return _HTTPResponse({"ok": True})
        if method == "tools/list":
            assert headers["Mcp-Session-Id"] == "session-123"
            assert headers["MCP-Protocol-Version"] == "2025-06-18"
            return _HTTPResponse(
                {
                    "jsonrpc": "2.0",
                    "id": json["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "fetch",
                                "description": "Fetch page",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"url": {"type": "string"}},
                                },
                            }
                        ]
                    },
                }
            )
        raise AssertionError(f"unexpected MCP method: {method}")


def test_remote_tools_are_namespaced_and_filtered():
    gateway = AhmedToolboxGateway(
        {"scrapling": _FakeRemote()},
        agent_reach=_FakeReach(),
    )

    names = {tool["name"] for tool in gateway.list_tools()}

    assert "reach_doctor" in names
    assert "reach_read_url" in names
    assert "reach_web_search" in names
    assert "scrapling__fetch" in names
    assert "scrapling__make_request" not in names


def test_unavailable_remote_does_not_remove_local_tools():
    gateway = AhmedToolboxGateway(
        {"scrapling": _BrokenRemote()},
        agent_reach=_FakeReach(),
    )

    names = {tool["name"] for tool in gateway.list_tools()}

    assert {"reach_doctor", "reach_web_search", "reach_read_url"} <= names
    assert "research_start_run" not in names
    assert "research_export_ledger" not in names


def test_research_tools_are_feature_gated():
    disabled = AhmedToolboxGateway(
        agent_reach=_FakeReach(),
        research_enabled=False,
    )
    enabled = AhmedToolboxGateway(
        agent_reach=_FakeReach(),
        research_enabled=True,
    )

    disabled_names = {tool["name"] for tool in disabled.list_tools()}
    enabled_names = {tool["name"] for tool in enabled.list_tools()}

    assert "research_start_run" not in disabled_names
    assert "research_export_ledger" not in disabled_names
    assert "research_start_run" in enabled_names
    assert "research_export_ledger" in enabled_names


def test_namespaced_tool_calls_are_forwarded():
    gateway = AhmedToolboxGateway(
        {"scrapling": _FakeRemote()},
        agent_reach=_FakeReach(),
    )

    result = gateway.call_tool(
        "scrapling__fetch",
        {"url": "https://example.com"},
    )

    assert result["isError"] is False
    assert result["content"][0]["text"] == "fetched https://example.com"


def test_non_allowlisted_remote_tool_is_blocked():
    gateway = AhmedToolboxGateway(
        {"scrapling": _FakeRemote()},
        agent_reach=_FakeReach(),
    )

    result = gateway.call_tool(
        "scrapling__make_request",
        {"url": "https://example.com"},
    )

    assert result["isError"] is True
    assert "not allowlisted" in result["content"][0]["text"]


def test_remote_client_negotiates_session_and_protocol_headers():
    session = _FakeHTTPSession()
    client = RemoteMCPClient(
        RemoteMCPConfig(
            name="scrapling",
            url="http://127.0.0.1:8000/mcp",
            allow_tools=("fetch",),
        ),
        session=session,
    )

    tools = client.list_tools()

    assert [tool["name"] for tool in tools] == ["fetch"]
    methods = [call["json"].get("method") for call in session.calls]
    assert methods == ["initialize", "notifications/initialized", "tools/list"]


def test_agent_reach_web_search_uses_documented_mcporter_route(monkeypatch):
    gateway = AhmedToolboxGateway(agent_reach=_FakeReach())
    seen = {}

    class _Completed:
        returncode = 0
        stdout = "search results"
        stderr = ""

    def _run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Completed()

    monkeypatch.setattr("agent_reach.toolbox.gateway.shutil.which", lambda name: "/usr/bin/mcporter")
    monkeypatch.setattr("agent_reach.toolbox.gateway.subprocess.run", _run)

    result = gateway.call_tool(
        "reach_web_search",
        {"query": "gold treasury yields", "num_results": 7},
    )

    assert result["isError"] is False
    assert result["content"][0]["text"] == "search results"
    assert seen["argv"] == [
        "/usr/bin/mcporter",
        "call",
        "exa.web_search_exa",
        "query=gold treasury yields",
        "numResults=7",
        "--output",
        "text",
    ]
    assert seen["kwargs"]["timeout"] == 45
    assert seen["kwargs"]["check"] is False
    config_path = seen["kwargs"]["env"]["MCPORTER_CONFIG"].replace("\\", "/")
    assert config_path.endswith("config/mcporter.json")


def test_unknown_tool_is_controlled_error():
    gateway = AhmedToolboxGateway(agent_reach=_FakeReach())

    result = gateway.call_tool("missing", {})

    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"]



def test_agent_reach_web_search_falls_back_to_direct_exa_mcp(monkeypatch):
    gateway = AhmedToolboxGateway(agent_reach=_FakeReach())

    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "mcporter upstream failure"

    monkeypatch.setattr(
        "agent_reach.toolbox.gateway.shutil.which",
        lambda name: "/usr/bin/mcporter",
    )
    monkeypatch.setattr(
        "agent_reach.toolbox.gateway.subprocess.run",
        lambda *args, **kwargs: _Failed(),
    )

    seen = {}

    def fake_call(self, name, arguments):
        seen["name"] = name
        seen["arguments"] = arguments
        return {
            "content": [{"type": "text", "text": "direct exa result"}],
            "isError": False,
        }

    monkeypatch.setattr(RemoteMCPClient, "call_tool", fake_call)

    result = gateway.call_tool(
        "reach_web_search",
        {"query": "gold treasury yields", "num_results": 3},
    )

    assert result["isError"] is False
    assert result["content"][0]["text"] == "direct exa result"
    assert seen["name"] == "web_search_exa"
    assert seen["arguments"] == {
        "query": "gold treasury yields",
        "numResults": 3,
    }


def test_agent_reach_web_search_reports_both_route_failures(monkeypatch):
    gateway = AhmedToolboxGateway(agent_reach=_FakeReach())

    class _Failed:
        returncode = 2
        stdout = ""
        stderr = "config lookup failed"

    monkeypatch.setattr(
        "agent_reach.toolbox.gateway.shutil.which",
        lambda name: "/usr/bin/mcporter",
    )
    monkeypatch.setattr(
        "agent_reach.toolbox.gateway.subprocess.run",
        lambda *args, **kwargs: _Failed(),
    )

    def broken_call(self, name, arguments):
        raise RemoteMCPError("hosted exa unavailable")

    monkeypatch.setattr(RemoteMCPClient, "call_tool", broken_call)

    result = gateway.call_tool(
        "reach_web_search",
        {"query": "OpenAI official site", "num_results": 1},
    )

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "both routes" in text
    assert "config lookup failed" in text
    assert "hosted exa unavailable" in text



def test_retrieval_fallback_tool_is_exposed() -> None:
    gateway = AhmedToolboxGateway(
        {"scrapling": _FakeRemote(allowed=("fetch", "stealthy_fetch"))},
        agent_reach=_FakeReach(),
    )

    names = {tool["name"] for tool in gateway.list_tools()}

    assert "reach_retrieve_url" in names


def test_retrieval_fallback_tool_escalates_through_gateway(monkeypatch) -> None:
    gateway = AhmedToolboxGateway(
        {"scrapling": _FakeRemote(allowed=("fetch", "stealthy_fetch"))},
        agent_reach=_FakeReach(),
    )

    def broken_read(self, url):
        del self, url
        raise RuntimeError("jina blocked")

    monkeypatch.setattr(
        "agent_reach.toolbox.gateway.WebChannel.read",
        broken_read,
    )

    result = gateway.call_tool(
        "reach_retrieve_url",
        {
            "url": "https://example.com",
            "min_chars": 1,
        },
    )

    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "SUCCESS"
    assert payload["final_tool"] == "scrapling__fetch"
    assert payload["attempts"][0]["tool"] == "reach_read_url"
    assert payload["attempts"][0]["status"] == "FAILED"
    assert payload["attempts"][1]["status"] == "SUCCESS"
