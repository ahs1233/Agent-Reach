import json
from types import SimpleNamespace

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.orchestration import OrchestrationStore
from agent_reach.toolbox.research import ResearchStore
from agent_reach.toolbox.runtime import RuntimeStore
from agent_reach.toolbox.subagent import SubagentModelClient, SubagentModelConfig


class _Response:
    def __init__(self, payload, status_code=200):
        self._body = json.dumps(payload).encode()
        self.status_code = status_code

    def iter_content(self, chunk_size=65536):
        del chunk_size
        yield self._body

    def close(self):
        pass


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class _FakeReach:
    def doctor(self):
        return {"core": {"status": "ok"}}


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def test_openai_compatible_subagent_executes_tool_then_finishes():
    session = _Session([
        _Response({
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "reach_doctor", "arguments": "{}"},
                    }],
                }
            }]
        }),
        _Response({
            "choices": [{
                "message": {
                    "content": "Toolbox health is OK.",
                }
            }]
        }),
    ])
    client = SubagentModelClient(
        SubagentModelConfig(
            base_url="https://provider.example/v1",
            api_key="secret-value",
            model="test-model",
        ),
        session=session,
    )
    seen = []

    result = client.run(
        goal="Check toolbox health.",
        role="orchestrator",
        tools=[{
            "name": "reach_doctor",
            "description": "Health check",
            "inputSchema": {"type": "object", "properties": {}},
        }],
        execute_tool=lambda name, arguments, role: (
            seen.append((name, arguments, role))
            or {"isError": False, "content": [{"type": "text", "text": "ok"}]}
        ),
    )

    assert result["status"] == "ok"
    assert result["tool_call_count"] == 1
    assert result["summary"] == "Toolbox health is OK."
    assert seen == [("reach_doctor", {}, "orchestrator")]
    assert len(session.calls) == 2
    second_messages = session.calls[1][1]["json"]["messages"]
    assert any(message.get("role") == "tool" for message in second_messages)
    assert "secret-value" not in json.dumps(session.calls[0][1]["json"])


class _FakeModelClient:
    def __init__(self):
        self.config = SimpleNamespace(available=True)
        self.calls = []

    def status(self):
        return {
            "available": True,
            "model": "fake-model",
            "provider_host": "fake.local",
            "max_turns": 4,
            "max_tool_calls": 4,
        }

    def run(self, *, goal, role, tools, execute_tool, context="", max_turns=None):
        self.calls.append({
            "goal": goal,
            "role": role,
            "tool_names": [tool["name"] for tool in tools],
            "context": context,
            "max_turns": max_turns,
        })
        result = execute_tool("reach_doctor", {}, role)
        assert result["isError"] is False
        return {
            "schema": "ahmed-runtime-model-subagent/v1",
            "status": "ok",
            "summary": "Child completed health verification.",
            "turn_count": 2,
            "tool_call_count": 1,
            "model": "fake-model",
            "trace_hash": "abc",
        }


def test_gateway_model_child_agent_is_isolated_and_budgeted(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=_FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        research_enabled=True,
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
        runtime_store=RuntimeStore(str(tmp_path / "runtime.db")),
        runtime_enabled=True,
    )
    fake_model = _FakeModelClient()
    gateway.subagent_client = fake_model

    parent = _payload(gateway.call_tool("orchestration_start", {
        "objective": "model child test",
        "mode": "general",
        "budget": {"tool_calls": 1, "network_calls": 0},
    }))
    delegated = _payload(gateway.call_tool("runtime_delegate", {
        "orchestration_id": parent["orchestration_id"],
        "tasks": [{
            "id": "child",
            "goal": "Check toolbox health using the allowed tool.",
            "role": "orchestrator",
            "tool_allowlist": ["reach_doctor"],
            "budget": {"tool_calls": 1, "network_calls": 0},
        }],
    }))

    assert delegated["status"] == "ok"
    task = delegated["tasks"][0]
    assert task["status"] == "ok"
    assert task["result"]["summary"] == "Child completed health verification."
    child_id = task["result"]["child_orchestration_id"]
    child = _payload(gateway.call_tool(
        "orchestration_status", {"orchestration_id": child_id}
    ))
    assert child["usage"]["tool_calls"] == 1
    assert child["usage"]["network_calls"] == 0
    assert child["journal"]["passed"] is True
    assert fake_model.calls[0]["tool_names"] == ["reach_doctor"]

    status = _payload(gateway.call_tool("runtime_status", {}))
    assert status["features"]["model_subagents"] is True
    assert status["model_subagent"]["model"] == "fake-model"
