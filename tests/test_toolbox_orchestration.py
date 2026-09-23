import json

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.orchestration import OrchestrationStore
from agent_reach.toolbox.research import ResearchStore


class FakeReach:
    def doctor(self):
        return {"ok": True}


def _payload(result):
    return json.loads(result["content"][0]["text"])


def test_orchestration_lists_and_executes_bounded_tool(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        research_enabled=True,
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    names = {tool["name"] for tool in gateway.list_tools()}
    assert {"orchestration_start", "orchestration_execute", "orchestration_verify"} <= names

    started = _payload(gateway.call_tool("orchestration_start", {"objective": "test control plane"}))
    oid = started["orchestration_id"]
    executed = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "orchestrator",
        "tool_name": "reach_doctor", "arguments": {},
    }))
    assert executed["executed"] is True
    assert executed["authorization"]["effect_class"] == "SE0"


def test_orchestration_default_denies_unknown_effect_and_secrets(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    oid = _payload(gateway.call_tool("orchestration_start", {"objective": "deny unsafe"}))["orchestration_id"]

    unknown = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "retriever",
        "tool_name": "unknown__write", "arguments": {},
    }))
    assert unknown["executed"] is False

    secret = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "discoverer",
        "tool_name": "reach_web_search",
        "arguments": {"query": "token=abcdefghijklmnopqrstuvwxyz123456"},
    }))
    assert secret["executed"] is False
    assert secret["authorization"]["reason"] == "secret_canary_detected"


def test_output_is_blocked_until_research_verifies(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    started = _payload(gateway.call_tool("orchestration_start", {"objective": "gate output"}))
    result = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": started["orchestration_id"], "role": "synthesizer",
        "tool_name": "research_create_output",
        "arguments": {"consumer_type": "answer", "output_type": "text", "fragments": []},
    }))
    assert result["executed"] is False
    assert result["reason"] == "verification_gate_failed"


def test_journal_is_hash_linked(tmp_path):
    store = OrchestrationStore(str(tmp_path / "orch.db"))
    run = store.create_run("journal", mode="general")
    store.authorize_tool(run["orchestration_id"], "orchestrator", "reach_doctor", {})
    assert store.verify_journal(run["orchestration_id"])["passed"] is True
