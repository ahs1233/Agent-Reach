"""MCP surface for selective ECC-inspired Ahmed orchestration."""

from __future__ import annotations

from typing import Any, Callable

from .orchestration import (
    OUTPUT_TOOLS,
    RUN_SCOPED_TOOLS,
    OrchestrationStore,
    build_verification_report,
    export_handoff,
)
from .research import ResearchStore


def orchestration_tool_specs() -> list[dict[str, Any]]:
    common_id = {"orchestration_id": {"type": "string", "minLength": 1}}
    return [
        {"name": "orchestration_start", "description": "Start a bounded Ahmed orchestration run with specialist lanes and an append-only journal.",
         "inputSchema": {"type": "object", "required": ["objective"], "properties": {
             "objective": {"type": "string", "minLength": 1}, "mode": {"type": "string", "enum": ["research", "general"], "default": "research"},
             "research_run_id": {"type": "string"}, "cutoff": {"type": "string"}, "budget": {"type": "object"},
             "specialists": {"type": "object"}, "max_effect_class": {"type": "string", "enum": ["SE0","SE1","SE2","SE3","SE4"], "default": "SE1"},
             "metadata": {"type": "object"}}, "additionalProperties": False}},
        {"name": "orchestration_status", "description": "Return run state, budgets, and journal integrity.",
         "inputSchema": {"type": "object", "required": ["orchestration_id"], "properties": common_id, "additionalProperties": False}},
        {"name": "orchestration_execute", "description": "Authorize and execute one existing Ahmed Toolbox tool through role, effect, secret, and budget gates.",
         "inputSchema": {"type": "object", "required": ["orchestration_id","role","tool_name"], "properties": {
             **common_id, "role": {"type": "string"}, "tool_name": {"type": "string"}, "arguments": {"type": "object"}}, "additionalProperties": False}},
        {"name": "orchestration_verify", "description": "Run deterministic orchestration and linked Research Engine verification.",
         "inputSchema": {"type": "object", "required": ["orchestration_id"], "properties": common_id, "additionalProperties": False}},
        {"name": "orchestration_handoff", "description": "Export machine-readable handoff state for another agent/session.",
         "inputSchema": {"type": "object", "required": ["orchestration_id"], "properties": common_id, "additionalProperties": False}},
        {"name": "orchestration_complete", "description": "Complete only after deterministic verification passes.",
         "inputSchema": {"type": "object", "required": ["orchestration_id"], "properties": common_id, "additionalProperties": False}},
    ]


def handle_orchestration_tool(
    store: OrchestrationStore,
    name: str,
    arguments: dict[str, Any],
    *,
    research_store: ResearchStore | None = None,
    execute_tool: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if name == "orchestration_start":
        mode = str(arguments.get("mode") or "research")
        research_run_id = arguments.get("research_run_id")
        if mode == "research" and not research_run_id:
            if research_store is None:
                raise ValueError("research store is required for research mode")
            research_run = research_store.create_run(
                str(arguments.get("objective") or ""),
                cutoff=arguments.get("cutoff"),
                metadata={"started_by": "orchestration", **(arguments.get("metadata") or {})},
            )
            research_run_id = research_run["run_id"]
        return store.create_run(
            str(arguments.get("objective") or ""), mode=mode, research_run_id=research_run_id,
            budget=arguments.get("budget"), specialists=arguments.get("specialists"),
            max_effect_class=str(arguments.get("max_effect_class") or "SE1"),
            metadata=arguments.get("metadata") or {},
        )

    if not name.startswith("orchestration_"):
        return None
    orchestration_id = str(arguments.get("orchestration_id") or "")
    if name == "orchestration_status":
        return store.status(orchestration_id)
    if name == "orchestration_verify":
        return build_verification_report(store, orchestration_id, research_store)
    if name == "orchestration_handoff":
        return export_handoff(store, orchestration_id, research_store)
    if name == "orchestration_complete":
        report = build_verification_report(store, orchestration_id, research_store)
        if not report["passed"]:
            return {"completed": False, "verification": report}
        run = store.get_run(orchestration_id)
        if run.get("research_run_id") and research_store is not None:
            research_store.complete_run(run["research_run_id"])
        return {"completed": True, "run": store.complete(orchestration_id), "verification": report}
    if name == "orchestration_execute":
        target = str(arguments.get("tool_name") or "")
        role = str(arguments.get("role") or "")
        tool_args = dict(arguments.get("arguments") or {})
        if target.startswith("orchestration_"):
            return {"executed": False, "reason": "recursive_orchestration_denied"}
        run = store.get_run(orchestration_id)
        if target in RUN_SCOPED_TOOLS and run.get("research_run_id"):
            supplied = tool_args.get("run_id")
            if supplied and supplied != run["research_run_id"]:
                return {"executed": False, "reason": "research_run_mismatch"}
            tool_args["run_id"] = run["research_run_id"]
        if target in OUTPUT_TOOLS:
            preflight = build_verification_report(store, orchestration_id, research_store)
            if not preflight["passed"]:
                return {"executed": False, "reason": "verification_gate_failed", "verification": preflight}
        authorization = store.authorize_tool(orchestration_id, role, target, tool_args)
        if not authorization["allowed"]:
            return {"executed": False, "authorization": authorization}
        if execute_tool is None:
            raise ValueError("execute_tool callback is required")
        try:
            result = execute_tool(target, tool_args)
        except Exception as exc:  # noqa: BLE001
            result = {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
        store.record_result(orchestration_id, authorization["authorization_event_id"], role, target, result)
        return {"executed": True, "authorization": authorization, "result": result}
    return None
