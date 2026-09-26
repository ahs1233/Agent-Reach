import json

import pytest

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.orchestration import OrchestrationStore
from agent_reach.toolbox.orchestration_mcp import orchestration_tool_specs
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


def test_specialist_shape_is_rejected_before_authorization(tmp_path):
    store = OrchestrationStore(str(tmp_path / "orch.db"))
    with pytest.raises(ValueError, match="specialists"):
        store.create_run(
            "malformed specialists",
            mode="general",
            specialists={
                "orchestrator": {
                    "allowed_tools": ["reach_doctor"],
                },
            },
        )


@pytest.mark.parametrize(
    ("specialists", "message"),
    [
        ({"": ["reach_doctor"]}, "role names"),
        ({"orchestrator": []}, "non-empty array"),
        ({"orchestrator": [""]}, "non-empty array"),
        ({"orchestrator": "reach_doctor"}, "non-empty array"),
    ],
)
def test_specialist_role_and_pattern_validation(tmp_path, specialists, message):
    store = OrchestrationStore(str(tmp_path / "orch.db"))
    with pytest.raises(ValueError, match=message):
        store.create_run(
            "invalid specialist contract",
            mode="general",
            specialists=specialists,
        )


def test_orchestration_start_mcp_schema_requires_role_to_pattern_arrays():
    start = next(spec for spec in orchestration_tool_specs() if spec["name"] == "orchestration_start")
    schema = start["inputSchema"]["properties"]["specialists"]
    assert schema["type"] == "object"
    assert schema["minProperties"] == 1
    assert schema["additionalProperties"]["type"] == "array"
    assert schema["additionalProperties"]["minItems"] == 1
    assert schema["additionalProperties"]["items"] == {"type": "string", "minLength": 1}


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


def test_result_requires_matching_authorization(tmp_path):
    store = OrchestrationStore(str(tmp_path / "orch.db"))
    run = store.create_run("authorization binding", mode="general")
    oid = run["orchestration_id"]
    try:
        store.record_result(oid, 999999, "orchestrator", "reach_doctor", {"isError": False})
    except ValueError as exc:
        assert "matching TOOL_ALLOWED" in str(exc)
    else:
        raise AssertionError("unmatched result must be rejected")


def test_duplicate_result_is_rejected(tmp_path):
    store = OrchestrationStore(str(tmp_path / "orch.db"))
    run = store.create_run("single result", mode="general")
    oid = run["orchestration_id"]
    auth = store.authorize_tool(oid, "orchestrator", "reach_doctor", {})
    store.record_result(
        oid, auth["authorization_event_id"], "orchestrator", "reach_doctor",
        {"isError": False},
    )
    try:
        store.record_result(
            oid, auth["authorization_event_id"], "orchestrator", "reach_doctor",
            {"isError": False},
        )
    except ValueError as exc:
        assert "already has a recorded result" in str(exc)
    else:
        raise AssertionError("duplicate result must be rejected")


def test_verification_fails_with_unresolved_authorization(tmp_path):
    store = OrchestrationStore(str(tmp_path / "orch.db"))
    run = store.create_run("unresolved", mode="general")
    oid = run["orchestration_id"]
    store.authorize_tool(oid, "orchestrator", "reach_doctor", {})
    from agent_reach.toolbox.orchestration import build_verification_report

    report = build_verification_report(store, oid)
    assert report["passed"] is False
    assert report["checks"]["all_authorizations_resolved"] is False


def test_full_general_orchestration_lifecycle(tmp_path):
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    started = _payload(gateway.call_tool("orchestration_start", {
        "objective": "end to end control-plane smoke test",
        "mode": "general",
        "budget": {"tool_calls": 3, "network_calls": 0},
    }))
    oid = started["orchestration_id"]

    executed = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid,
        "role": "orchestrator",
        "tool_name": "reach_doctor",
        "arguments": {},
    }))
    assert executed["executed"] is True
    assert executed["result"]["isError"] is False

    verified = _payload(gateway.call_tool("orchestration_verify", {
        "orchestration_id": oid,
    }))
    assert verified["passed"] is True
    assert verified["checks"]["journal_integrity"] is True
    assert verified["checks"]["all_authorizations_resolved"] is True

    handoff = _payload(gateway.call_tool("orchestration_handoff", {
        "orchestration_id": oid,
    }))
    assert handoff["verification"]["passed"] is True
    assert handoff["usage"]["tool_calls"] == 1

    completed = _payload(gateway.call_tool("orchestration_complete", {
        "orchestration_id": oid,
    }))
    assert completed["completed"] is True
    assert completed["run"]["status"] == "COMPLETE"

    denied_after_complete = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid,
        "role": "orchestrator",
        "tool_name": "reach_doctor",
        "arguments": {},
    }))
    assert denied_after_complete["executed"] is False
    assert denied_after_complete["authorization"]["reason"] == "run_not_active"


def test_full_research_orchestration_with_real_evidence(tmp_path):
    research = ResearchStore(str(tmp_path / "research.db"))
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=research,
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
    )
    started = _payload(gateway.call_tool("orchestration_start", {
        "objective": "prove a research claim through the controlled pipeline",
        "mode": "research",
        "budget": {"tool_calls": 10, "network_calls": 0},
    }))
    oid = started["orchestration_id"]

    source_exec = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "evidence_analyst",
        "tool_name": "research_record_source",
        "arguments": {
            "url": "https://example.com/ecc-proof",
            "content": "Ahmed Toolbox orchestration requires evidence before synthesis.",
            "retrieval_tool": "test_fixture",
            "retrieval_method": "controlled_fixture",
            "publisher": "Test Fixture",
            "source_type": "PRIMARY",
            "primary_source": True,
        },
    }))
    assert source_exec["executed"] is True
    source = json.loads(source_exec["result"]["content"][0]["text"])
    source_id = source["source_id"]

    evidence_exec = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "evidence_analyst",
        "tool_name": "research_add_evidence",
        "arguments": {
            "source_id": source_id,
            "supporting_passage": "Ahmed Toolbox orchestration requires evidence before synthesis.",
            "observation_type": "ACTUAL",
            "extraction_method": "controlled_test",
        },
    }))
    assert evidence_exec["executed"] is True
    evidence = json.loads(evidence_exec["result"]["content"][0]["text"])
    evidence_id = evidence["evidence_id"]

    claim_exec = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "evidence_analyst",
        "tool_name": "research_add_claim",
        "arguments": {
            "statement": "The controlled pipeline contains evidence before synthesis.",
            "classification": "VERIFIED",
            "supporting_evidence_ids": [evidence_id],
            "observation_type": "ACTUAL",
            "confidence": "HIGH",
            "verification_count": 1,
        },
    }))
    assert claim_exec["executed"] is True
    claim = json.loads(claim_exec["result"]["content"][0]["text"])
    claim_id = claim["claim_id"]

    verified = _payload(gateway.call_tool("orchestration_verify", {
        "orchestration_id": oid,
    }))
    assert verified["passed"] is True
    assert verified["checks"]["research_has_sources"] is True
    assert verified["checks"]["research_has_evidence"] is True
    assert verified["checks"]["research_has_claims"] is True
    assert verified["checks"]["claims_have_evidence"] is True
    assert verified["checks"]["verified_claims_checked"] is True

    output_exec = _payload(gateway.call_tool("orchestration_execute", {
        "orchestration_id": oid, "role": "synthesizer",
        "tool_name": "research_create_output",
        "arguments": {
            "consumer_type": "test",
            "output_type": "text",
            "fragments": [{
                "text": "Evidence-backed synthesis.",
                "claim_ids": [claim_id],
                "semantic_type": "ACTUAL",
            }],
        },
    }))
    assert output_exec["executed"] is True

    completed = _payload(gateway.call_tool("orchestration_complete", {
        "orchestration_id": oid,
    }))
    assert completed["completed"] is True
    assert completed["verification"]["passed"] is True
