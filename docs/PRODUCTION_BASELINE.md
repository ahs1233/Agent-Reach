# Ahmed Toolbox Production Baseline

Date: 2026-09-24  
Repository: `ahs1233/Agent-Reach`  
Branch: `feat/ahmed-toolbox-mcp`  
Baseline code SHA: `fec1818f93c59e9747d45675aa65fcc029d59d9b`  
Public endpoint: `https://agent-reach-production.up.railway.app`

## Current verified state

- External functional Production Acceptance: **10/10 PASS**
- External security preflight: **FAIL** — unauthenticated MCP request returned HTTP 200
- Production auth token reference: resolves empty
- Final stabilization status: **STABILIZATION NOT YET PASSED**
- Sprint2 temporary runner was restored to its original `ahmed-toolbox` start command and its restoration deployment succeeded.

## Runtime verification

Dual-temporal startup acceptance remains verified:
- current-live gate: PASSED
- stale-live gate: BLOCKED
- contradiction selected current data: true
- run audit: true

Startup skill state:
- `__startup_doctor_skill__`
- revision 1
- successes 10
- failures 0
- score 0.9166666667
- `TRUSTED_PRODUCTION`

Model-subagent startup:
- earlier observation: provider deadline at 60422.75 ms
- latest verified observation: `__AHMED_MODEL_SUBAGENT_STARTUP_ACCEPTANCE__ok`
- gateway/client inspection: model client and HTTP session are reused, not recreated per call

## Resource baseline

Previously captured one-hour Railway sample, 61 points:

| Measurement | Average | Min | Max |
| --- | ---: | ---: | ---: |
| CPU_USAGE | 0.002430 | 0 | 0.014990 |
| MEMORY_USAGE_GB | 0.321071 | 0.304484 | 0.376156 |
| NETWORK_RX_GB/sample | 0.000022783 | 0 | 0.000280968 |
| NETWORK_TX_GB/sample | 0.000006442 | 0 | 0.000034315 |

## External acceptance baseline

GitHub Actions external run `35964631234` observed:
- 61 production MCP tools
- `reach_doctor`: PASS
- live web search: PASS
- URL retrieval: PASS
- Research provenance workflow + audit: PASS
- Browser Use YouTube evidence: PASS
- malformed JSON handling: PASS
- five concurrent external clients: PASS

The functional result is accepted as real external evidence. It is not sufficient for final stabilization because the security preflight failed.

## Security blocker

`agent_reach/toolbox/server.py` treats an empty auth token as authorization granted. Production currently resolves `AHMED_TOOLBOX_TOKEN` to an empty value, so the public MCP endpoint is not enforcing Bearer authentication.

The security-aware acceptance runner now verifies an unauthenticated `ping` receives HTTP 401. Final PASS is forbidden until that preflight passes.
