from __future__ import annotations

from agent_reach.toolbox.gateway import AhmedToolboxGateway


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


def test_unknown_tool_is_controlled_error():
    gateway = AhmedToolboxGateway(agent_reach=_FakeReach())

    result = gateway.call_tool("missing", {})

    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"]
