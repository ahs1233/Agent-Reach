# Ahmed Toolbox Production Acceptance Report

Date: 2026-09-24  
Repository: `ahs1233/Agent-Reach`  
Branch: `feat/ahmed-toolbox-mcp`  
Production endpoint: `https://agent-reach-production.up.railway.app`

## Acceptance status

**STABILIZATION NOT YET PASSED**

The external functional Production Acceptance suite is complete: **10/10 required functional cases passed from outside Agent-Reach**. Final stabilization is still blocked by a security preflight: the public MCP endpoint currently accepts an unauthenticated request instead of returning HTTP 401.

## External Production Acceptance

External runner: GitHub Actions, run `35964631234`  
External runner commit: `4205a01250556e3bac0d2ef71d6f55abf36830d1`  
Target: public Railway production URL  
Functional result: **10 PASS / 0 FAIL**

| ID | Case | Result | Observed latency |
| ---: | --- | --- | ---: |
| 1 | `/health` | PASS | 35.616 ms |
| 2 | MCP `tools/list` (61 tools) | PASS | 487.709 ms |
| 3 | `reach_doctor` | PASS | 2053.165 ms |
| 4 | live `reach_web_search` | PASS | 1329.357 ms |
| 5 | `reach_read_url` / Example Domain | PASS | 274.671 ms |
| 6 | `research_start_run` | PASS | 38.627 ms |
| 7 | real Browser Use YouTube evidence path | PASS | 6289.825 ms |
| 8 | source → evidence → claim → output → audit | PASS | 208.329 ms |
| 9 | malformed JSON rejected with HTTP 400 | PASS | 35.210 ms |
| 10 | five independent concurrent MCP clients | PASS | 85.171 ms wall |

The Research workflow audit returned `audit_passed=true`. The concurrent-client case completed all five clients successfully.

## Security preflight

Result: **FAIL**

An MCP `ping` sent without an Authorization header returned **HTTP 200**, not HTTP 401.

Independent Railway reference probes also showed:
- reference to `Agent-Reach.RAILWAY_PUBLIC_DOMAIN`: non-empty
- reference to `Agent-Reach.AHMED_TOOLBOX_TOKEN`: empty

The server implementation explicitly permits requests when the configured auth token is empty. Therefore the production endpoint is functionally healthy but currently unauthenticated.

The acceptance runner was hardened so a future run cannot report a false green merely because the runner has a token. Canonical hardening commit:
`4e21fb3796e505c81cd087a63003c3c6a1df0719`

Formatter follow-up:
`fec1818f93c59e9747d45675aa65fcc029d59d9b`

## Model-subagent reliability

A previous production startup observed:
- `SubagentModelError: model provider deadline exceeded`
- delegation duration: 60422.75 ms

The latest verified production startup subsequently logged:
`__AHMED_MODEL_SUBAGENT_STARTUP_ACCEPTANCE__ok`

Code inspection confirms the gateway creates one `SubagentModelClient` and reuses its `requests.Session`; the client is not recreated per delegated call. The earlier timeout is retained as a transient external-provider/network reliability observation rather than attributed to per-call model construction.

## Skill policy

Latest verified production startup state:
- skill: `__startup_doctor_skill__`
- revision: 1
- successes: 10
- failures: 0
- score: 0.9166666667
- trust status: `TRUSTED_PRODUCTION`

No synthetic runs were added to force promotion.

## Known optional gap

`reach_media_ingest` STT remains unavailable without a configured transcription provider. Production media acceptance uses the working rendered Browser Use YouTube evidence path instead.

## Remaining blocker before final PASS

1. Configure a non-empty production `AHMED_TOOLBOX_TOKEN` through a secure secret path and update authorized clients without exposing the value.
2. Re-run the security-aware external acceptance suite.
3. Require the unauthenticated preflight to return HTTP 401 **and** all ten functional cases to remain PASS.
4. Require final canonical CI and Railway deployment to be green.

Until those conditions hold, the required final status remains:

**STABILIZATION NOT YET PASSED**
