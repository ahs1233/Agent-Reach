# Ahmed Toolbox Production Acceptance Report

Date: 2026-09-24  
Repository: `ahs1233/Agent-Reach`  
Branch: `feat/ahmed-toolbox-mcp`  
Accepted code SHA: `f3c526dafcd19a3dbf40297fc443984a57046852`  
Production deployment: `112b6d92-6188-43e7-95bf-4897115f3079`  
Production endpoint: `https://agent-reach-production.up.railway.app`

## Acceptance status

**STABILIZATION PASSED**

The required external Production Acceptance gate completed against the public Railway endpoint with authentication enabled and fail-closed server protection active.

## Final external acceptance

Temporary external runner: Railway `Ahmed-Research-Sprint2`  
Acceptance runner deployment: `3839f364-1329-43af-9c01-9dc1f8a92064`  
Runner exit code: `0`

Security preflight:
- unauthenticated MCP request: **HTTP 401**
- security status: **PASS**
- authenticated requests used the production token through a Railway service reference; the token value was not printed or committed.

Functional result: **10 PASS / 0 FAIL**

| ID | Case | Result | Observed latency |
| ---: | --- | --- | ---: |
| 1 | `/health` | PASS | 122.377 ms |
| 2 | MCP `tools/list` (61 tools) | PASS | 574.614 ms |
| 3 | `reach_doctor` | PASS | 2894.298 ms |
| 4 | live `reach_web_search` | PASS | 1363.094 ms |
| 5 | `reach_read_url` / Example Domain | PASS | 270.965 ms |
| 6 | `research_start_run` | PASS | 124.876 ms |
| 7 | real Browser Use YouTube evidence path | PASS | 6056.735 ms |
| 8 | source → evidence → claim → output → audit | PASS | 615.971 ms |
| 9 | malformed JSON rejected with HTTP 400 | PASS | 118.237 ms |
| 10 | five independent concurrent MCP clients | PASS | 307.917 ms wall |

The Research workflow returned `audit_passed=true`.

## Security hardening

Production now has a non-empty `AHMED_TOOLBOX_TOKEN`.

The acceptance runner verifies both:
1. unauthenticated MCP access is rejected with HTTP 401;
2. authenticated functional acceptance remains 10/10.

Permanent server-side fail-closed protection was added in:
`f3c526dafcd19a3dbf40297fc443984a57046852`

A non-loopback Ahmed Toolbox server can no longer start without `AHMED_TOOLBOX_TOKEN`. This prevents a future missing-secret configuration from silently reopening the public MCP endpoint.

## CI and deployment

For `f3c526dafcd19a3dbf40297fc443984a57046852`:
- Ahmed ToolBox CI push run `35966037370`: **SUCCESS**
- general CI push run `35966037293`: **SUCCESS**
- Python 3.10 / 3.11 / 3.12 / 3.13: PASS
- Windows: PASS
- wheel gate: PASS
- Ruff / Ruff format: PASS
- scoped mypy: PASS
- focused tests: PASS
- full regression: PASS
- core stress: PASS
- Railway production deployment `112b6d92-6188-43e7-95bf-4897115f3079`: **SUCCESS**

## Production startup state

Final accepted deployment logged:
- `__AHMED_MODEL_SUBAGENT_STARTUP_ACCEPTANCE__ok`
- dual-temporal live acceptance: `PASSED`
- current-live gate: `PASSED`
- stale-live gate: `BLOCKED`
- contradiction selected current data: true
- Research run audit: true
- MCP listener active on `0.0.0.0:8080/mcp`

Startup skill:
- revision: 1
- successes: 26
- failures: 0
- Bayesian score: 0.9642857143
- trust status: `TRUSTED_PRODUCTION`

## Model-subagent latency observation

An earlier deployment recorded a provider deadline around 60.4 s. Later production startups, including the accepted final code deployment, completed model-subagent startup acceptance successfully.

Code inspection confirmed the gateway creates one `SubagentModelClient` and reuses its HTTP session rather than constructing the model client per delegated call. The earlier timeout remains documented as a transient external-provider/network reliability observation.

## Known optional gap

`reach_media_ingest` STT remains unavailable without a configured transcription provider. This is not part of the required production acceptance path; the production-capable Browser Use video evidence path passed.

## Runner restoration

The temporary `Ahmed-Research-Sprint2` runner was restored to:
- start command: `ahmed-toolbox`
- original Sprint2 pre-deploy acceptance
- healthcheck timeout: 60 s

Restoration deployment `ab14ca21-9ec4-47dc-8085-01d6dad6bb9f`: **SUCCESS**

## Final result

**STABILIZATION PASSED**
