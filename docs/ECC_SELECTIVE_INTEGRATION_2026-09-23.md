# ECC Selective Integration — Ahmed Toolbox
Date: 2026-09-23

## Decision
Do not vendor or install the complete affaan-m/ECC system. Ahmed Toolbox adapts only the control-plane ideas that improve bounded research orchestration while preserving the existing Ahmed Research Engine.

Reviewed ECC reference: main snapshot around `bf70150eb2df8070024e5bdf08e4aa08959e2735`. ECC is MIT licensed.

## Adapted
- Explicit orchestration/session state.
- Specialist lanes with narrow tool permissions.
- Bounded tool/network budgets.
- Effect classes SE0..SE4 with unknown effects denied by default.
- Secret-canary checks before tool execution.
- Append-only hash-linked event journal.
- Deterministic verification before synthesis/completion.
- Machine-readable handoff/status.

## Not imported
- tmux/worktree orchestration.
- Claude-specific session parsing.
- ECC installer/profile/hooks/UI.
- Autonomous candidate execution and release automation.
- General write-capable MCP connectors.
- Provider/economic actions.

## Architecture
Ahmed Orchestration Control Plane
→ Ahmed Toolbox Gateway
→ Agent Reach / allowlisted remote MCPs
→ Ahmed Research Engine provenance + evidence + integrity

The control plane does not replace provenance, Evidence Ledger, Actual/Forecast integrity, freshness, source independence, or retrieval fallback.

## Effect policy
- SE0: read-only inspection/retrieval.
- SE1: bounded local evidence-state mutation.
- SE2..SE4: reserved for stronger side effects and denied unless explicitly enabled.
- Unknown remote effects: default deny.

## MCP surface
- `orchestration_start`
- `orchestration_status`
- `orchestration_execute`
- `orchestration_verify`
- `orchestration_handoff`
- `orchestration_complete`

## Activation
```
AHMED_RESEARCH_ENABLED=true
AHMED_ORCHESTRATION_ENABLED=true
AHMED_ORCHESTRATION_DB_PATH=/data/orchestration.db
```

## Security boundary
This is a gateway policy and audit layer, not an operating-system sandbox. Stronger SE2+ actions require separate capability isolation before enablement.
