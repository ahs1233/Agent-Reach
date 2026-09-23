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


def test_budget_exhaustion_denies_additional_calls(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    oid = _payload(gateway.call_tool("orchestration_start", {
        "objective": "budget", "mode": "general",
        "budget": {"tool_calls": 1, "network_calls": 0},
    }))["orchestration_id"]
    first = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "orchestrator",
        "tool_name": "reach_doctor", "arguments": {},
    }))
    second = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "orchestrator",
        "tool_name": "reach_doctor", "arguments": {},
    }))
    assert first["executed"] is True
    assert second["executed"] is False
    assert second["authorization"]["reason"] == "tool_budget_exhausted"


def test_role_boundary_denies_cross_lane_tool(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    oid = _payload(gateway.call_tool("orchestration_start", {
        "objective": "role boundary", "mode": "general",
    }))["orchestration_id"]
    denied = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "discoverer",
        "tool_name": "reach_doctor", "arguments": {},
    }))
    assert denied["executed"] is False
    assert denied["authorization"]["reason"] == "tool_not_allowed_for_role"


def test_research_run_scope_cannot_be_swapped(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    started = _payload(gateway.call_tool("orchestration_start", {
        "objective": "scope research",
    }))
    denied = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": started["orchestration_id"], "role": "evidence_analyst",
        "tool_name": "research_record_source",
        "arguments": {
            "run_id": "run_attacker",
            "url": "https://example.com",
            "content": "x",
            "retrieval_tool": "test",
            "retrieval_method": "test",
        },
    }))
    assert denied["executed"] is False
    assert denied["reason"] == "research_run_mismatch"


def test_complete_refuses_unverified_research(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    oid = _payload(gateway.call_tool("orchestration_start", {
        "objective": "must verify",
    }))["orchestration_id"]
    result = _payload(gateway.call_tool("orchestration_complete", {
        "orchestration_id": oid,
    }))
    assert result["completed"] is False
    assert result["verification"]["passed"] is False


def test_journal_detects_tampering(tmp_path):
    store = OrchestrationStore(str(tmp_path / "orch.db"))
    run = store.create_run("tamper detection", mode="general")
    oid = run["orchestration_id"]
    store.authorize_tool(oid, "orchestrator", "reach_doctor", {})
    with store._connect() as conn:
        conn.execute(
            "UPDATE orchestration_events SET payload_json=? WHERE orchestration_id=? AND event_type='TOOL_ALLOWED'",
            ('{"tampered":true}', oid),
        )
    report = store.verify_journal(oid)
    assert report["passed"] is False
    assert report["invalid_event_ids"]


def test_effect_ceiling_blocks_se1_research_mutation(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    started = _payload(gateway.call_tool("orchestration_start", {
        "objective": "read only research", "max_effect_class": "SE0",
    }))
    denied = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": started["orchestration_id"], "role": "evidence_analyst",
        "tool_name": "research_record_source",
        "arguments": {
            "url": "https://example.com", "content": "evidence",
            "retrieval_tool": "test", "retrieval_method": "test",
        },
    }))
    assert denied["executed"] is False
    assert denied["authorization"]["reason"] == "effect_ceiling_exceeded"


def test_network_budget_is_separate_from_tool_budget(tmp_path):
    store = OrchestrationStore(str(tmp_path / "orch.db"))
    run = store.create_run(
        "network budget", mode="general",
        budget={"tool_calls": 5, "network_calls": 0},
    )
    auth = store.authorize_tool(
        run["orchestration_id"], "discoverer", "reach_web_search", {"query": "x"}
    )
    assert auth["allowed"] is False
    assert auth["reason"] == "network_budget_exhausted"


def test_handoff_contains_verification_and_journal_head(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    oid = _payload(gateway.call_tool("orchestration_start", {
        "objective": "handoff", "mode": "general",
    }))["orchestration_id"]
    handoff = _payload(gateway.call_tool("orchestration_handoff", {
        "orchestration_id": oid,
    }))
    assert handoff["schema"] == "ahmed-orchestration-handoff/v1"
    assert handoff["journal_head"]
    assert handoff["verification"]["passed"] is True
