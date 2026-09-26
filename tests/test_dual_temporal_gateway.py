from __future__ import annotations

from agent_reach.toolbox.gateway import (
    AhmedToolboxGateway,
    RemoteMCPClient,
)


class _FakeReach:
    def doctor(self):
        return {"web": {"status": "ok"}}


def test_temporal_tools_are_exposed_when_research_is_enabled() -> None:
    gateway = AhmedToolboxGateway(
        agent_reach=_FakeReach(),
        research_enabled=True,
    )
    names = {tool["name"] for tool in gateway.list_tools()}

    assert "research_evaluate_temporal_validity" in names
    assert "research_resolve_temporal_contradiction" in names
    assert "research_temporal_fusion" in names
    assert "research_final_live_refresh_gate" in names
    assert "research_temporal_budget" in names


def test_g_exa_429_primary_route_falls_back_to_hosted_mcp(monkeypatch) -> None:
    gateway = AhmedToolboxGateway(agent_reach=_FakeReach())

    class _RateLimited:
        returncode = 1
        stdout = ""
        stderr = "429 Too Many Requests: rate limit exceeded"

    monkeypatch.setattr(
        "agent_reach.toolbox.gateway.shutil.which",
        lambda name: "/usr/bin/mcporter",
    )
    monkeypatch.setattr(
        "agent_reach.toolbox.gateway.subprocess.run",
        lambda *args, **kwargs: _RateLimited(),
    )

    seen = []

    def fake_call(self, name, arguments):
        seen.append((name, arguments))
        return {
            "content": [{"type": "text", "text": "hosted Exa fallback result"}],
            "isError": False,
        }

    monkeypatch.setattr(RemoteMCPClient, "call_tool", fake_call)

    result = gateway.call_tool(
        "reach_web_search",
        {"query": "current official event state", "num_results": 4},
    )

    assert result["isError"] is False
    assert result["content"][0]["text"] == "hosted Exa fallback result"
    assert seen == [
        (
            "web_search_exa",
            {"query": "current official event state", "numResults": 4},
        )
    ]
