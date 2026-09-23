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

### 2. Isolated delegation

`runtime_delegate` runs multiple specialist workstreams concurrently.

When a parent `orchestration_id` is supplied, every delegated workstream receives:
- a distinct child orchestration id;
- an independent tool/network budget;
- an independent hash-linked journal;
- the parent's specialist policy and effect ceiling;
- only the linked Research Engine run is shared.

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
- are versioned when the procedure changes;
- retain success/failure counters;
- use a conservative Bayesian observed-success score;
- update outcome statistics automatically after `runtime_skill_execute`.

The runtime does not autonomously overwrite a procedure merely because one new attempt exists. This is intentional protection against self-corruption.

MCP tools:
- `runtime_skill_save`
- `runtime_skill_list`
- `runtime_skill_get`
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
```

Runtime is also enabled automatically when `AHMED_ORCHESTRATION_ENABLED=1`.

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
