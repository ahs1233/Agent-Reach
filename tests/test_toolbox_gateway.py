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
    assert "scrapling__fetch" in names
    assert "scrapling__make_request" not in names


def test_unavailable_remote_does_not_remove_local_tools():
    gateway = AhmedToolboxGateway(
        {"scrapling": _BrokenRemote()},
        agent_reach=_FakeReach(),
    )

    names = {tool["name"] for tool in gateway.list_tools()}

    assert names == {"reach_doctor", "reach_read_url"}


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


def test_unknown_tool_is_controlled_error():
    gateway = AhmedToolboxGateway(agent_reach=_FakeReach())

    result = gateway.call_tool("missing", {})

    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"]
