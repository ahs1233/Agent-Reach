import json

import pytest

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.orchestration import OrchestrationStore
from agent_reach.toolbox.research import ResearchStore
from agent_reach.toolbox.runtime import RuntimeStore, execute_workflow, validate_workflow


class FakeReach:
    def doctor(self):
        return {"core": {"status": "ok"}}


def _payload(result):
    return json.loads(result["content"][0]["text"])


def _gateway(tmp_path):
    return AhmedToolboxGateway(
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        research_enabled=True,
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=True,
        runtime_store=RuntimeStore(str(tmp_path / "runtime.db")),
        runtime_enabled=True,
    )


def test_runtime_tools_are_listed(tmp_path):
    names = {tool["name"] for tool in _gateway(tmp_path).list_tools()}
    assert {
        "runtime_status",
        "runtime_execute_workflow",
        "runtime_delegate",
        "runtime_memory_put",
        "runtime_memory_search",
        "runtime_session_search",
        "runtime_skill_save",
        "runtime_skill_execute",
    } <= names


def test_runtime_read_only_workflow_collapses_multiple_calls(tmp_path):
    gateway = _gateway(tmp_path)
    result = _payload(gateway.call_tool("runtime_execute_workflow", {
        "steps": [
            {"id": "one", "tool_name": "reach_doctor", "arguments": {}},
            {"id": "two", "tool_name": "reach_doctor", "arguments": {}},
        ],
        "max_parallel": 2,
    }))
    assert result["status"] == "ok"
    assert result["ok_count"] == 2
    assert result["step_count"] == 2


@pytest.mark.parametrize(
    "tool_name",
    [
        "runtime_status",
        "runtime_memory_search",
        "runtime_execute_workflow",
        "orchestration_status",
        "orchestration_execute",
    ],
)
def test_runtime_workflow_rejects_nested_control_plane_tools(tool_name):
    with pytest.raises(ValueError, match="recursive control-plane tool denied"):
        validate_workflow([
            {"id": "nested", "tool_name": tool_name, "arguments": {}},
        ])


def test_runtime_requires_orchestration_for_mutating_research_tool(tmp_path):
    gateway = _gateway(tmp_path)
    result = _payload(gateway.call_tool("runtime_execute_workflow", {
        "steps": [{
            "id": "write",
            "tool_name": "research_record_source",
            "role": "evidence_analyst",
            "arguments": {
                "url": "https://example.com",
                "content": "evidence",
                "retrieval_tool": "fixture",
                "retrieval_method": "fixture",
            },
        }],
    }))
    assert result["status"] == "error"
    text = result["steps"][0]["result"]["content"][0]["text"]
    assert "orchestration_id" in text


def test_runtime_workflow_honors_orchestration_budget(tmp_path):
    gateway = _gateway(tmp_path)
    started = _payload(gateway.call_tool("orchestration_start", {
        "objective": "bounded workflow",
        "mode": "general",
        "budget": {"tool_calls": 1, "network_calls": 0},
    }))
    result = _payload(gateway.call_tool("runtime_execute_workflow", {
        "orchestration_id": started["orchestration_id"],
        "steps": [
            {"id": "one", "tool_name": "reach_doctor", "role": "orchestrator"},
            {"id": "two", "tool_name": "reach_doctor", "role": "orchestrator", "depends_on": ["one"]},
        ],
    }))
    assert result["ok_count"] == 1
    assert result["error_count"] == 1


def test_runtime_learns_successful_workflow_as_versioned_skill(tmp_path):
    gateway = _gateway(tmp_path)
    result = _payload(gateway.call_tool("runtime_execute_workflow", {
        "steps": [{"id": "doctor", "tool_name": "reach_doctor"}],
        "learn_as": "doctor-check",
        "skill_description": "Check toolbox health",
    }))
    assert result["learned_skill"]["name"] == "doctor-check"
    skill = _payload(gateway.call_tool("runtime_skill_get", {"name": "doctor-check"}))
    assert skill["revision"] == 1
    assert skill["successes"] == 1

    executed = _payload(gateway.call_tool("runtime_skill_execute", {"name": "doctor-check"}))
    assert executed["status"] == "ok"
    assert executed["skill"]["score"] > 0.5


def test_runtime_memory_and_session_search(tmp_path):
    gateway = _gateway(tmp_path)
    _payload(gateway.call_tool("runtime_memory_put", {
        "key": "research-rule",
        "content": "Verified claims require supporting evidence.",
        "tags": ["research", "evidence"],
    }))
    found = _payload(gateway.call_tool("runtime_memory_search", {"query": "supporting evidence"}))
    assert found["results"][0]["memory_key"] == "research-rule"

    run = _payload(gateway.call_tool("runtime_execute_workflow", {
        "session_id": "session-test",
        "steps": [{"id": "doctor", "tool_name": "reach_doctor"}],
    }))
    assert run["session_id"] == "session-test"
    sessions = _payload(gateway.call_tool("runtime_session_search", {
        "query": "workflow_hash",
        "session_id": "session-test",
    }))
    assert sessions["results"]


def test_runtime_delegation_creates_isolated_child_journals(tmp_path):
    gateway = _gateway(tmp_path)
    parent = _payload(gateway.call_tool("orchestration_start", {
        "objective": "parallel specialists",
        "mode": "general",
        "budget": {"tool_calls": 5, "network_calls": 0},
    }))
    result = _payload(gateway.call_tool("runtime_delegate", {
        "orchestration_id": parent["orchestration_id"],
        "tasks": [
            {
                "id": "verify-a",
                "objective": "verify A",
                "steps": [{"id": "d", "tool_name": "reach_doctor", "role": "orchestrator"}],
                "budget": {"tool_calls": 1, "network_calls": 0},
            },
            {
                "id": "verify-b",
                "objective": "verify B",
                "steps": [{"id": "d", "tool_name": "reach_doctor", "role": "orchestrator"}],
                "budget": {"tool_calls": 1, "network_calls": 0},
            },
        ],
        "max_parallel": 2,
    }))
    assert result["status"] == "ok"
    child_ids = [task["result"]["child_orchestration_id"] for task in result["tasks"]]
    assert len(set(child_ids)) == 2
    for child_id in child_ids:
        status = _payload(gateway.call_tool("orchestration_status", {"orchestration_id": child_id}))
        assert status["run"]["status"] == "COMPLETE"
        assert status["journal"]["passed"] is True


def test_result_reference_is_resolved_without_model_roundtrip():
    seen = {}

    def execute(name, arguments, role):
        if name == "source":
            return {"isError": False, "value": {"answer": 42}}
        seen["value"] = arguments["value"]
        return {"isError": False, "content": [{"type": "text", "text": "ok"}]}

    result = execute_workflow([
        {"id": "a", "tool_name": "source"},
        {
            "id": "b",
            "tool_name": "sink",
            "depends_on": ["a"],
            "arguments": {"value": {"$step": "a", "path": "result.value.answer"}},
        },
    ], execute)
    assert result["status"] == "ok"
    assert seen["value"] == 42


def test_delegation_normalizes_missing_ids_and_caps_parent_budget(tmp_path):
    gateway = _gateway(tmp_path)
    parent = _payload(gateway.call_tool("orchestration_start", {
        "objective": "bounded delegated budget",
        "mode": "general",
        "budget": {"tool_calls": 1, "network_calls": 0},
    }))
    result = _payload(gateway.call_tool("runtime_delegate", {
        "orchestration_id": parent["orchestration_id"],
        "tasks": [
            {"objective": "first", "steps": [{"tool_name": "reach_doctor"}]},
            {"objective": "second", "steps": [{"tool_name": "reach_doctor"}]},
        ],
        "max_parallel": 2,
    }))

    assert [task["id"] for task in result["tasks"]] == ["task_1", "task_2"]
    children = [
        task["result"]["child_orchestration_id"]
        for task in result["tasks"]
        if "result" in task and "child_orchestration_id" in task["result"]
    ]
    assert len(children) == 2
    child_statuses = [
        _payload(gateway.call_tool("orchestration_status", {"orchestration_id": child_id}))
        for child_id in children
    ]
    assert sum(item["run"]["budget"]["tool_calls"] for item in child_statuses) == 1
    assert sum(item["usage"]["tool_calls"] for item in child_statuses) <= 1


def test_skill_revision_resets_score_and_can_roll_back(tmp_path):
    gateway = _gateway(tmp_path)
    _payload(gateway.call_tool("runtime_skill_save", {
        "name": "adaptive-skill",
        "description": "v1",
        "workflow": [{"id": "doctor", "tool_name": "reach_doctor"}],
    }))
    _payload(gateway.call_tool("runtime_skill_execute", {"name": "adaptive-skill"}))
    v1 = _payload(gateway.call_tool("runtime_skill_get", {"name": "adaptive-skill"}))
    assert v1["revision"] == 1
    assert v1["successes"] == 1
    assert v1["score"] > 0.5
    assert v1["recent_outcomes"][0]["revision"] == 1

    v2 = _payload(gateway.call_tool("runtime_skill_save", {
        "name": "adaptive-skill",
        "description": "v2",
        "workflow": [
            {"id": "doctor-a", "tool_name": "reach_doctor"},
            {"id": "doctor-b", "tool_name": "reach_doctor", "depends_on": ["doctor-a"]},
        ],
        "change_note": "candidate improvement",
    }))
    assert v2["revision"] == 2
    assert v2["successes"] == 0
    assert v2["failures"] == 0
    assert v2["score"] == 0.5

    rolled = _payload(gateway.call_tool("runtime_skill_rollback", {
        "name": "adaptive-skill",
        "revision": 1,
    }))
    assert rolled["revision"] == 3
    assert rolled["description"] == "v1"
    assert len(rolled["workflow"]) == 1
    assert rolled["successes"] == 0
    assert rolled["score"] == 0.5


def test_skill_revision_resets_failures_but_identical_save_preserves_history(tmp_path):
    store = RuntimeStore(str(tmp_path / "runtime.db"))
    workflow = [{"id": "doctor", "tool_name": "reach_doctor"}]
    store.save_skill("revision-stats", "v1", workflow)
    store.record_skill_outcome("revision-stats", False)
    before = store.record_skill_outcome("revision-stats", False)

    unchanged = store.save_skill("revision-stats", "v1", workflow)
    assert unchanged["revision"] == 1
    assert unchanged["failures"] == 2
    assert unchanged["score"] == before["score"]

    revised = store.save_skill("revision-stats", "v2", workflow)
    assert revised["revision"] == 2
    assert revised["successes"] == revised["failures"] == 0
    assert revised["score"] == 0.5
    assert len(revised["recent_outcomes"]) == 2
    assert all(item["revision"] == 1 for item in revised["recent_outcomes"])
