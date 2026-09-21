from __future__ import annotations

from agent_reach.toolbox.gateway import AhmedToolboxGateway


class _FakeReach:
    def doctor(self):
        return {"web": {"status": "ok"}}


class _FakeRemote:
    def list_tools(self):
        return [
            {
                "name": "fetch",
                "description": "Fetch a page",
                "inputSchema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                },
            }
        ]

    def call_tool(self, name, arguments):
        assert name == "fetch"
        return {
            "content": [{"type": "text", "text": f"fetched {arguments['url']}"}],
            "isError": False,
        }


def test_remote_tools_are_namespaced():
    gateway = AhmedToolboxGateway(
        {"scrapling": _FakeRemote()},
        agent_reach=_FakeReach(),
    )

    names = {tool["name"] for tool in gateway.list_tools()}

    assert "reach_doctor" in names
    assert "reach_read_url" in names
    assert "scrapling__fetch" in names


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


def test_unknown_tool_is_controlled_error():
    gateway = AhmedToolboxGateway(agent_reach=_FakeReach())

    result = gateway.call_tool("missing", {})

    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"]
