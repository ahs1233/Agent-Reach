"""MCP surface for the Ahmed Hermes-grade selective runtime."""

from __future__ import annotations

import json
from typing import Any, Callable

from .orchestration import OrchestrationStore
from .runtime import RuntimeStore, execute_delegation, execute_workflow, new_session_id


def runtime_tool_specs() -> list[dict[str, Any]]:
    step_schema = {
        "type": "object",
        "required": ["tool_name"],
        "properties": {
            "id": {"type": "string", "minLength": 1, "maxLength": 80},
            "tool_name": {"type": "string", "minLength": 1, "maxLength": 200},
            "role": {"type": "string", "minLength": 1, "maxLength": 80},
            "arguments": {"type": "object"},
            "depends_on": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string", "minLength": 1, "maxLength": 80},
            },
        },
        "additionalProperties": False,
    }
    return [
        {
            "name": "runtime_status",
            "description": "Report Ahmed runtime capabilities and durable store health.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "runtime_execute_workflow",
            "description": (
                "Execute up to 50 Ahmed Toolbox calls as a bounded dependency DAG inside one MCP turn. "
                "Independent steps run in parallel; later arguments can reference prior step results."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["steps"],
                "properties": {
                    "steps": {"type": "array", "minItems": 1, "maxItems": 50, "items": step_schema},
                    "orchestration_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "max_parallel": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
                    "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 900, "default": 120},
                    "fail_fast": {"type": "boolean", "default": True},
                    "learn_as": {"type": "string", "maxLength": 80},
                    "skill_description": {"type": "string", "maxLength": 2000},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "runtime_delegate",
            "description": (
                "Run isolated specialist workstreams concurrently. With a parent orchestration id, each "
                "task receives its own child budget and hash-linked journal while sharing only the linked research run."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["tasks"],
                "properties": {
                    "orchestration_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "max_parallel": {"type": "integer", "minimum": 1, "maximum": 6, "default": 3},
                    "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 1800, "default": 300},
                    "tasks": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "maxLength": 80},
                                "objective": {"type": "string", "maxLength": 2000},
                                "goal": {"type": "string", "minLength": 1, "maxLength": 12000},
                                "role": {"type": "string", "minLength": 1, "maxLength": 80},
                                "context": {"type": "string", "maxLength": 12000},
                                "tool_allowlist": {
                                    "type": "array",
                                    "maxItems": 80,
                                    "items": {"type": "string", "minLength": 1, "maxLength": 200},
                                },
                                "max_turns": {"type": "integer", "minimum": 1, "maximum": 20},
                                "steps": {"type": "array", "minItems": 1, "maxItems": 50, "items": step_schema},
                                "budget": {"type": "object"},
                                "max_parallel": {"type": "integer", "minimum": 1, "maximum": 8},
                                "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 900},
                                "fail_fast": {"type": "boolean"},
                            },
                            "anyOf": [
                                {"required": ["steps"]},
                                {"required": ["goal"]},
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "runtime_memory_put",
            "description": "Store or replace a bounded durable fact/procedure note for future runtime retrieval.",
            "inputSchema": {
                "type": "object",
                "required": ["key", "content"],
                "properties": {
                    "key": {"type": "string", "minLength": 1, "maxLength": 200},
                    "content": {"type": "string", "minLength": 1, "maxLength": 50000},
                    "kind": {"type": "string", "maxLength": 80, "default": "fact"},
                    "tags": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 100}},
                    "metadata": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "runtime_memory_search",
            "description": "Search durable Ahmed runtime memory using FTS5 when available, with LIKE fallback.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "runtime_session_search",
            "description": "Search prior runtime workflow/delegation events without loading all prior context.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "session_id": {"type": "string", "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "runtime_skill_save",
            "description": "Create or version a reusable procedural skill backed by a validated workflow DAG.",
            "inputSchema": {
                "type": "object",
                "required": ["name", "workflow"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "description": {"type": "string", "maxLength": 2000},
                    "workflow": {"type": "array", "minItems": 1, "maxItems": 50, "items": step_schema},
                    "source": {"type": "string", "maxLength": 80, "default": "manual"},
                    "change_note": {"type": "string", "maxLength": 2000},
                    "metadata": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "runtime_skill_list",
            "description": "List learned procedural skills ranked by observed Bayesian success score.",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}},
                "additionalProperties": False,
            },
        },
        {
            "name": "runtime_skill_get",
            "description": "Get the active revision, workflow, and observed outcomes for one runtime skill.",
            "inputSchema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 80}},
                "additionalProperties": False,
            },
        },
        {
            "name": "runtime_skill_rollback",
            "description": "Create a new active revision by rolling a skill back to a known earlier workflow.",
            "inputSchema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "revision": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "runtime_skill_execute",
            "description": "Execute a stored procedural skill and automatically update its observed success score.",
            "inputSchema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "orchestration_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "max_parallel": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
                    "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 900, "default": 120},
                    "fail_fast": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
        },
    ]


def handle_runtime_tool(
    store: RuntimeStore,
    name: str,
    arguments: dict[str, Any],
    *,
    execute_step: Callable[[str, dict[str, Any], str, str | None], dict[str, Any]] | None = None,
    execute_agent: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    agent_status: dict[str, Any] | None = None,
    orchestration_store: OrchestrationStore | None = None,
) -> dict[str, Any] | None:
    if not name.startswith("runtime_"):
        return None

    if name == "runtime_status":
        return {
            "schema": "ahmed-runtime-status/v1",
            "status": "ok",
            "features": {
                "workflow_dag": True,
                "parallel_tool_rpc": True,
                "delegation": True,
                "model_subagents": bool((agent_status or {}).get("available")),
                "durable_memory": True,
                "session_search": True,
                "versioned_skills": True,
                "skill_outcome_learning": True,
                "fts5": bool(store._fts_enabled),
            },
            "model_subagent": agent_status or {"available": False},
        }

    if name == "runtime_memory_put":
        return store.put_memory(
            str(arguments.get("key") or ""),
            str(arguments.get("content") or ""),
            kind=str(arguments.get("kind") or "fact"),
            tags=[str(x) for x in (arguments.get("tags") or [])],
            metadata=dict(arguments.get("metadata") or {}),
        )

    if name == "runtime_memory_search":
        return {
            "query": str(arguments.get("query") or ""),
            "results": store.search_memory(
                str(arguments.get("query") or ""), int(arguments.get("limit") or 10)
            ),
        }

    if name == "runtime_session_search":
        return {
            "query": str(arguments.get("query") or ""),
            "results": store.search_sessions(
                str(arguments.get("query") or ""),
                session_id=(str(arguments.get("session_id")) if arguments.get("session_id") else None),
                limit=int(arguments.get("limit") or 10),
            ),
        }

    if name == "runtime_skill_save":
        return store.save_skill(
            str(arguments.get("name") or ""),
            str(arguments.get("description") or ""),
            list(arguments.get("workflow") or []),
            source=str(arguments.get("source") or "manual"),
            metadata=dict(arguments.get("metadata") or {}),
            change_note=str(arguments.get("change_note") or ""),
        )

    if name == "runtime_skill_list":
        return {"skills": store.list_skills(int(arguments.get("limit") or 50))}

    if name == "runtime_skill_get":
        return store.get_skill(str(arguments.get("name") or ""))

    if name == "runtime_skill_rollback":
        revision = arguments.get("revision")
        return store.rollback_skill(
            str(arguments.get("name") or ""),
            int(revision) if revision is not None else None,
        )

    if execute_step is None and name in {
        "runtime_execute_workflow", "runtime_delegate", "runtime_skill_execute"
    }:
        raise ValueError("execute_step callback is required")

    def run_workflow(
        steps: list[dict[str, Any]],
        *,
        orchestration_id: str | None,
        max_parallel: int,
        timeout_seconds: float,
        fail_fast: bool,
    ) -> dict[str, Any]:
        assert execute_step is not None
        return execute_workflow(
            steps,
            lambda tool_name, tool_args, role: execute_step(
                tool_name, tool_args, role, orchestration_id
            ),
            max_parallel=max_parallel,
            timeout_seconds=timeout_seconds,
            fail_fast=fail_fast,
        )

    if name == "runtime_execute_workflow":
        session_id = str(arguments.get("session_id") or new_session_id())
        orchestration_id = (
            str(arguments.get("orchestration_id")) if arguments.get("orchestration_id") else None
        )
        steps = list(arguments.get("steps") or [])
        result = run_workflow(
            steps,
            orchestration_id=orchestration_id,
            max_parallel=int(arguments.get("max_parallel") or 4),
            timeout_seconds=float(arguments.get("timeout_seconds") or 120),
            fail_fast=bool(arguments.get("fail_fast", True)),
        )
        result["session_id"] = session_id
        if orchestration_id:
            result["orchestration_id"] = orchestration_id
        store.record_session(
            session_id,
            json.dumps(result, ensure_ascii=False, default=str),
            kind="workflow",
            metadata={"workflow_hash": result["workflow_hash"], "status": result["status"]},
        )
        learn_as = str(arguments.get("learn_as") or "").strip()
        if learn_as and result["status"] == "ok":
            skill = store.save_skill(
                learn_as,
                str(arguments.get("skill_description") or "Learned from a successful Ahmed runtime workflow."),
                steps,
                source="successful_workflow",
                metadata={"learned_session_id": session_id, "workflow_hash": result["workflow_hash"]},
                change_note="Captured from successful workflow execution",
            )
            skill = store.record_skill_outcome(learn_as, True)
            result["learned_skill"] = {
                "name": skill["name"], "revision": skill["revision"], "score": skill["score"]
            }
        return result

    if name == "runtime_skill_execute":
        skill_name = str(arguments.get("name") or "")
        skill = store.get_skill(skill_name)
        session_id = str(arguments.get("session_id") or new_session_id("skill"))
        result = run_workflow(
            skill["workflow"],
            orchestration_id=(str(arguments.get("orchestration_id")) if arguments.get("orchestration_id") else None),
            max_parallel=int(arguments.get("max_parallel") or 4),
            timeout_seconds=float(arguments.get("timeout_seconds") or 120),
            fail_fast=bool(arguments.get("fail_fast", True)),
        )
        success = result["status"] == "ok"
        stats = store.record_skill_outcome(skill_name, success)
        result.update({
            "session_id": session_id,
            "skill": {"name": skill_name, "revision": skill["revision"], "score": stats["score"]},
        })
        store.record_session(
            session_id,
            json.dumps(result, ensure_ascii=False, default=str),
            kind="skill_execution",
            metadata={"skill": skill_name, "success": success},
        )
        return result

    if name == "runtime_delegate":
        parent_id = (
            str(arguments.get("orchestration_id")) if arguments.get("orchestration_id") else None
        )
        session_id = str(arguments.get("session_id") or new_session_id("delegate"))
        raw_tasks = list(arguments.get("tasks") or [])
        tasks: list[dict[str, Any]] = []
        seen_task_ids: set[str] = set()
        for index, raw_task in enumerate(raw_tasks):
            if not isinstance(raw_task, dict):
                raise ValueError(f"delegation task {index} must be an object")
            task = dict(raw_task)
            task_id = str(task.get("id") or f"task_{index + 1}")
            if task_id in seen_task_ids:
                raise ValueError(f"duplicate delegation task id: {task_id}")
            seen_task_ids.add(task_id)
            task["id"] = task_id
            tasks.append(task)

        parent_run = None
        child_budget_by_id: dict[str, dict[str, int]] = {}
        if parent_id:
            if orchestration_store is None:
                raise ValueError("delegation with orchestration_id requires orchestration store")
            parent_run = orchestration_store.get_run(parent_id)
            if parent_run.get("status") != "ACTIVE":
                raise ValueError("parent orchestration run is not active")

            usage = orchestration_store.usage(parent_id)
            remaining_tools = max(
                0, int(parent_run["budget"]["tool_calls"]) - int(usage["tool_calls"])
            )
            remaining_network = max(
                0, int(parent_run["budget"]["network_calls"]) - int(usage["network_calls"])
            )
            task_count = max(1, len(tasks))
            for index, task in enumerate(tasks):
                task_id = str(task.get("id") or f"task_{index + 1}")
                tool_cap = remaining_tools // task_count + (
                    1 if index < (remaining_tools % task_count) else 0
                )
                network_cap = remaining_network // task_count + (
                    1 if index < (remaining_network % task_count) else 0
                )
                requested = dict(task.get("budget") or {})
                child_budget_by_id[task_id] = {
                    "tool_calls": min(
                        tool_cap, max(0, int(requested.get("tool_calls", tool_cap)))
                    ),
                    "network_calls": min(
                        network_cap, max(0, int(requested.get("network_calls", network_cap)))
                    ),
                }

        def execute_task(task: dict[str, Any]) -> dict[str, Any]:
            task_id = str(task.get("id") or "task")
            child_id = None
            if parent_run is not None and orchestration_store is not None:
                child = orchestration_store.create_run(
                    str(task.get("objective") or f"delegated:{task_id}"),
                    mode=str(parent_run.get("mode") or "general"),
                    research_run_id=parent_run.get("research_run_id"),
                    budget=child_budget_by_id[task_id],
                    specialists=parent_run.get("specialists"),
                    max_effect_class=str(parent_run.get("max_effect_class") or "SE1"),
                    metadata={
                        "parent_orchestration_id": parent_id,
                        "delegation_task_id": task_id,
                        "budget_allocated_from_parent": True,
                    },
                )
                child_id = child["orchestration_id"]
                orchestration_store.append_event(
                    parent_id,
                    "SUBAGENT_SPAWNED",
                    payload={"task_id": task_id, "child_orchestration_id": child_id},
                )
            if task.get("goal"):
                if child_id is None:
                    raise ValueError(
                        "model-backed delegated tasks require a parent orchestration_id"
                    )
                if execute_agent is None:
                    raise ValueError("model-backed subagent runner is not configured")
                outcome = execute_agent(task, child_id)
            else:
                steps = list(task.get("steps") or [])
                if not steps:
                    raise ValueError("delegation task requires steps or goal")
                outcome = run_workflow(
                    steps,
                    orchestration_id=child_id or parent_id,
                    max_parallel=int(task.get("max_parallel") or 3),
                    timeout_seconds=float(task.get("timeout_seconds") or 180),
                    fail_fast=bool(task.get("fail_fast", True)),
                )
            if child_id and orchestration_store is not None:
                unresolved = orchestration_store.unresolved_authorizations(child_id)
                if not unresolved:
                    orchestration_store.complete(child_id)
                outcome["child_orchestration_id"] = child_id
                orchestration_store.append_event(
                    parent_id,
                    "SUBAGENT_FINISHED",
                    payload={
                        "task_id": task_id,
                        "child_orchestration_id": child_id,
                        "status": outcome.get("status"),
                        "workflow_hash": outcome.get("workflow_hash"),
                    },
                )
            return outcome

        result = execute_delegation(
            tasks,
            execute_task,
            max_parallel=int(arguments.get("max_parallel") or 3),
            timeout_seconds=float(arguments.get("timeout_seconds") or 300),
        )
        result["session_id"] = session_id
        if parent_id:
            result["orchestration_id"] = parent_id
        store.record_session(
            session_id,
            json.dumps(result, ensure_ascii=False, default=str),
            kind="delegation",
            metadata={"task_count": len(tasks), "parent_orchestration_id": parent_id},
        )
        return result

    return None
