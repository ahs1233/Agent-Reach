# Ahmed Toolbox Production Baseline

Date: 2026-09-24  
Repository: `ahs1233/Agent-Reach`  
Branch: `feat/ahmed-toolbox-mcp`  
Accepted code SHA: `f3c526dafcd19a3dbf40297fc443984a57046852`  
Railway deployment: `112b6d92-6188-43e7-95bf-4897115f3079`  
Public endpoint: `https://agent-reach-production.up.railway.app`

## Final verified state

- Railway production deployment: **SUCCESS**
- External Production Acceptance: **10/10 PASS**
- Security preflight: **PASS**
- unauthenticated MCP request: **HTTP 401**
- authenticated production MCP path: **PASS**
- production auth token: configured
- public-server missing-token behavior: fail closed
- final stabilization status: **STABILIZATION PASSED**

## CI baseline

Accepted SHA `f3c526dafcd19a3dbf40297fc443984a57046852`:
- Ahmed ToolBox CI: SUCCESS
- general CI: SUCCESS
- Python 3.10 / 3.11 / 3.12 / 3.13: PASS
- Windows: PASS
- wheel gate: PASS
- Ruff / format: PASS
- scoped mypy: PASS
- focused tests: PASS
- full regression: PASS
- core stress: PASS

## Runtime verification

Dual-temporal startup acceptance:
- status: PASSED
- current-live gate: PASSED
- stale-live gate: BLOCKED
- contradiction selected current data: true
- run audit: true

Model-subagent startup:
- final accepted deployment: `__AHMED_MODEL_SUBAGENT_STARTUP_ACCEPTANCE__ok`
- earlier ~60.4 s provider deadline retained as a transient reliability observation
- model client / HTTP session are reused rather than recreated per call

Startup skill:
- `__startup_doctor_skill__`
- revision 1
- successes 26
- failures 0
- score 0.9642857143
- `TRUSTED_PRODUCTION`

## External acceptance baseline

Final external run observed:
- 61 production MCP tools
- `reach_doctor`: PASS
- live web search: PASS
- URL retrieval: PASS
- Research provenance workflow + audit: PASS
- Browser Use YouTube evidence: PASS
- malformed JSON handling: PASS
- five concurrent external clients: PASS
- unauthenticated access rejected: PASS

## Resource baseline

Previously captured one-hour Railway sample, 61 points:

| Measurement | Average | Min | Max |
| --- | ---: | ---: | ---: |
| CPU_USAGE | 0.002430 | 0 | 0.014990 |
| MEMORY_USAGE_GB | 0.321071 | 0.304484 | 0.376156 |
| NETWORK_RX_GB/sample | 0.000022783 | 0 | 0.000280968 |
| NETWORK_TX_GB/sample | 0.000006442 | 0 | 0.000034315 |

## Security baseline

The public MCP endpoint requires Bearer authentication.

`agent_reach/toolbox/server.py` now also refuses to start on a non-loopback host if `AHMED_TOOLBOX_TOKEN` is missing. This converts a missing production secret from an open-auth condition into a startup failure.

## Known optional gap

Local STT / `reach_media_ingest` transcription remains unavailable without a configured transcription provider. The supported rendered-browser video evidence path is production-accepted.

## Final result

**STABILIZATION PASSED**
