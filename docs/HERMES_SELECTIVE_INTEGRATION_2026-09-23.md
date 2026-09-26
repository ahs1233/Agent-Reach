# Hermes Selective Integration — Ahmed Toolbox
Date: 2026-09-23

## Goal

Adopt the highest-value agent-runtime ideas from NousResearch/hermes-agent without embedding a second full agent platform inside Agent-Reach.

The implementation is original Ahmed Toolbox code informed by Hermes' public architecture and documentation. Hermes is MIT licensed (Copyright (c) 2025 Nous Research). No Hermes runtime is vendored or required at deployment time.

## Implemented layers

### 1. Programmatic multi-tool workflow execution

`runtime_execute_workflow` executes a bounded dependency DAG of up to 50 Ahmed Toolbox tool calls inside one MCP invocation.

Properties:
- independent steps execute concurrently;
- dependent steps can reference structured outputs from earlier steps with `{"$step":"id","path":"..."}`;
- recursive runtime/orchestration calls are denied;
- output size is bounded;
- global workflow timeout and fail-fast behavior are supported;
- mutating/unknown tools require an orchestration run.

This captures the main context-efficiency benefit of Hermes `execute_code` without exposing arbitrary Python execution.

### 2. Isolated delegation and real child agents

`runtime_delegate` supports two execution modes:

1. deterministic specialist workflow DAGs; and
2. true model-backed child agents with isolated conversation context.

Model-backed children use an operator-configured OpenAI-compatible endpoint. Each
child receives only the tool schemas allowed by its orchestration role plus an
optional additional tool allowlist. Every tool call is executed through the same
Ahmed orchestration authorization path as a normal run.

When a parent `orchestration_id` is supplied, every delegated workstream receives:
- a distinct child orchestration id;
- an independent tool/network budget whose aggregate allocation cannot exceed
  the parent's remaining budget;
- an independent hash-linked journal;
- the parent's specialist policy and effect ceiling;
- only the linked Research Engine run is shared.

Intermediate child tool traffic remains inside the child context. The parent gets
the final bounded result/summary plus audit identifiers, not the child's complete
conversation.

The parent journal records `SUBAGENT_SPAWNED` and `SUBAGENT_FINISHED` events.

### 3. Durable memory and session recall

`RuntimeStore` stores:
- bounded durable memories;
- searchable runtime session events;
- workflow/delegation execution summaries.

SQLite FTS5 is used when available, with a LIKE fallback so deployments do not fail when FTS5 is unavailable.

MCP tools:
- `runtime_memory_put`
- `runtime_memory_search`
- `runtime_session_search`

### 4. Versioned procedural skills

Successful workflows can be captured with `learn_as` or explicitly saved with `runtime_skill_save`.

Skills:
- contain validated workflow DAGs;
- can be learned only from a successfully completed workflow via `learn_as`;
- are versioned when the procedure changes;
- reset active success/failure evidence when a new revision changes the procedure;
- retain per-revision outcome history;
- use a conservative Bayesian observed-success score;
- update outcome statistics automatically after `runtime_skill_execute`;
- support auditable rollback, which creates a new monotonic revision rather than
  deleting history.

The runtime does not promote a failed workflow and does not let a fresh revision
inherit the reputation of the previous procedure. This is intentional protection
against self-corruption.

MCP tools:
- `runtime_skill_save`
- `runtime_skill_list`
- `runtime_skill_get`
- `runtime_skill_rollback`
- `runtime_skill_execute`

### 5. MCP trust/effect gate

Remote MCP configuration now supports:
- `trust`: `untrusted`, `read_only`, or `full`;
- `effect_classes`: per-tool `SE0..SE4` classifications.

Environment-configured unknown remote servers default to `untrusted`.
Known Scrapling read-only tools retain explicit `SE0` classification.

For `untrusted` and `read_only` servers, only explicitly classified `SE0` calls execute directly. Write-capable remote tools require explicit operator `trust=full` configuration and remain subject to orchestration effect ceilings when used in runtime workflows.

## Runtime security invariants

1. Unknown effect class defaults to deny inside orchestration/runtime.
2. Runtime workflows cannot recursively invoke runtime/orchestration control-plane tools.
3. Non-SE0 workflow calls require an `orchestration_id`.
4. Orchestrated calls retain role allowlists, effect ceilings, secret-canary scanning, tool/network budgets, and hash-linked journals.
5. Delegated workstreams do not share intermediate step context.
6. Stored workflow and memory sizes are bounded.
7. Arbitrary Python execution is intentionally not exposed.

## Configuration

Enable explicitly:

```bash
AHMED_RUNTIME_ENABLED=1
AHMED_RUNTIME_DB_PATH=/data/ahmed-runtime.db

# Optional: true model-backed child agents
AHMED_SUBAGENT_BASE_URL=https://provider.example/v1
AHMED_SUBAGENT_API_KEY=...
AHMED_SUBAGENT_MODEL=...
AHMED_SUBAGENT_TIMEOUT_SECONDS=60
AHMED_SUBAGENT_MAX_TURNS=8
AHMED_SUBAGENT_MAX_TOOL_CALLS=30
```

Runtime is also enabled automatically when `AHMED_ORCHESTRATION_ENABLED=1`.
Model-provider secrets are read only from process environment and are never added
to child prompts or tool arguments.

Remote MCP example:

```json
{
  "example": {
    "url": "https://example.invalid/mcp",
    "allow_tools": ["lookup", "create_item"],
    "trust": "full",
    "effect_classes": {
      "lookup": "SE0",
      "create_item": "SE2"
    }
  }
}
```

Do not mark a write-capable server `full` unless it is intentionally operator-approved.

## Relationship to Hermes

Conceptual references reviewed from NousResearch/hermes-agent include:
- programmatic tool calling / execute_code;
- subagent delegation;
- persistent memory and session search;
- procedural skills and learning loop;
- MCP trust gating.

Hermes remains an independent upstream project. Ahmed Toolbox does not claim compatibility with every Hermes feature and does not copy its messaging gateway, desktop UI, model-provider layer, or arbitrary code-execution subsystem.

## Quality target

The target is feature-quality parity for the selected primitives, not source-code parity. Ahmed Toolbox adds its own Research Engine evidence controls, orchestration journal, source provenance, freshness, source independence, and PanWatch integration on top of these runtime primitives.


## Production acceptance (2026-09-23)

The Railway production deployment runs a startup acceptance gate whenever Ahmed
Runtime is enabled. Deployment fails closed if the core runtime surface is not
available or if workflow/memory/skill/orchestration smoke checks fail.

When a model provider is configured, startup additionally creates an isolated
child agent, exposes only `reach_doctor`, requires a real model tool call, and
verifies that exactly one tool call is accounted for in the child's orchestration
budget/journal before the MCP server begins listening.

The production acceptance marker is:

`__AHMED_MODEL_SUBAGENT_STARTUP_ACCEPTANCE__ok`
