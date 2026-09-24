# Ahmed Toolbox — Stabilization & Hardening Phase

> **FEATURE FREEZE ACTIVE**
>
> Freeze start date: **2026-09-23**
>
> No new engines, agents, or non-reliability features may be added until the Production Acceptance Gate passes. Allowed changes are limited to Reliability, Testing, Observability, Security, Failure Recovery, Performance, Regression Prevention, and Production Verification.

## Current Gate Status — 2026-09-24

- Steps 1–9: completed for the stabilization baseline.
- Step 10 functional external acceptance: **10/10 PASS** from GitHub Actions against the public Railway endpoint.
- Security preflight: **FAIL** because an unauthenticated MCP request returned HTTP 200 instead of 401.
- Startup skill: **TRUSTED_PRODUCTION**, revision 1, 10 successes, 0 failures, score 0.9166666667.
- Latest model-subagent startup acceptance: **ok**; the prior 60.4 s provider deadline is retained as a transient reliability observation.
- Temporary Sprint2 acceptance-runner changes were restored successfully.
- Final status: **STABILIZATION NOT YET PASSED** until production authentication is enabled and the security-aware external acceptance rerun passes.

## Ground Truth Baseline

| Field | Actual value | Verification |
|---|---|---|
| Repository | `ahs1233/Agent-Reach` | GitHub connector `get_repo` |
| Canonical stabilization branch | `feat/ahmed-toolbox-mcp` | GitHub branch API |
| HEAD at freeze start | `1604b7a8a83807dcd8391574cb2edabb2fd4ea82` | GitHub branch API |
| GitHub connector permission | `admin` / push=true | GitHub connector permission metadata |
| Local working tree | Not available in this ChatGPT runtime | Container has no external DNS; no local clone exists, so no working-tree state is claimed |
| Railway project | `reasonable-dedication` | Railway project/service API |
| Railway environment | `production` | Railway status API |
| Railway service | `Agent-Reach` | Railway service API |
| Deployment ID | `e7ec562f-68d7-4a68-b363-b90b3f69d28f` | Railway status/deployments API |
| Deployment status | `SUCCESS` | Railway status/deployments API |
| Deployed commit | `1604b7a8a83807dcd8391574cb2edabb2fd4ea82` | Railway deployment metadata |
| Production URL | `https://agent-reach-production.up.railway.app` | Railway domains API |
| Healthcheck configured | `/health`, timeout 180s | Railway service config |
| Runtime | `ok` | live `runtime_status` |
| Runtime subagent limits | max_turns=8; max_tool_calls=30 | live `runtime_status` |
| ACE | `ok` | live `ace_status` |
| Browser Use | `ok` | live `reach_doctor` |
| YouTube / yt-dlp | `ok` | live `reach_doctor` |
| Jina Reader / web | `ok` | live `reach_doctor` |
| RSS | `ok` | live `reach_doctor` |
| Exa | `warn` | configured but Doctor did not perform a live remote connectivity proof |
| GitHub CLI inside Agent-Reach | `warn` | executable found, explicit CLI auth not proven |
| GitHub write path for this stabilization | `WORKING` | authenticated GitHub connector has admin/push permission |
| Scrapling | `UNTESTED` | tools exposed; live execution validation pending |
| PanWatch integration | `UNTESTED` | code path exists; execution validation pending |

## Systems / Engines / Integrations

| Component | Type | Purpose | Branch | Status | Verification |
|---|---|---|---|---|---|
| Agent-Reach | Acquisition system | Acquire web/social/media source material | feat/ahmed-toolbox-mcp | WORKING | Production service deployed; reach_doctor executed; individual channels vary |
| Ahmed Toolbox MCP server | MCP gateway | Expose acquisition/research/runtime/orchestration/ACE capabilities | feat/ahmed-toolbox-mcp | WORKING | Production logs show MCP listening and repeated initialize/tools/list discovery |
| Research Engine | Engine | Source → evidence → claim → output → audit provenance pipeline | feat/ahmed-toolbox-mcp | UNTESTED | Code and MCP tools exist; full E2E execution pending |
| Evidence / Provenance | Research subsystem | Preserve evidence-backed lineage and output auditability | feat/ahmed-toolbox-mcp | UNTESTED | Code/tests present; current-turn E2E pending |
| Source Independence | Research subsystem | Detect source lineage/independence | feat/ahmed-toolbox-mcp | UNTESTED | Code/tests/tools present; current-turn execution pending |
| Freshness / Temporal validity | Research subsystem | Evaluate recency/historical validity without semantic promotion | feat/ahmed-toolbox-mcp | WORKING | Current production startup dual-temporal acceptance logged PASSED |
| Orchestration | Control plane | Bounded specialist routing, budgets, and append-only hash-linked journal | feat/ahmed-toolbox-mcp | UNTESTED | Code/tools present; Golden execution pending |
| Runtime | Execution plane | DAG workflows, delegation, memory, sessions, skills | feat/ahmed-toolbox-mcp | WORKING | live runtime_status=ok; individual behaviors still require Golden tests |
| Durable Memory | Runtime subsystem | Durable key/value procedural/runtime memory | feat/ahmed-toolbox-mcp | UNTESTED | runtime_status advertises capability; write/read test pending |
| Session Search | Runtime subsystem | Search persisted session traces | feat/ahmed-toolbox-mcp | UNTESTED | runtime_status advertises capability; execution test pending |
| Skills | Runtime subsystem | Versioned procedural skills with outcome learning | feat/ahmed-toolbox-mcp | WORKING | runtime_skill_list executed; current skill inventory returned |
| Delegation / Subagents | Runtime subsystem | Bounded model delegation | feat/ahmed-toolbox-mcp | UNTESTED | runtime_status reports provider available; execution/budget tests pending |
| ACE | Application/engine | Content experiment workflow using existing research/runtime layers | feat/ahmed-toolbox-mcp | WORKING | live ace_status=ok; deeper workflow tests pending |
| Media Intelligence | Acquisition/research subsystem | Video/media ingest and evidence extraction | feat/ahmed-toolbox-mcp | UNTESTED | code/tool exists; live media validation pending |
| Jina Reader | Integration | Default URL reader | feat/ahmed-toolbox-mcp | WORKING | reach_doctor web=ok |
| Exa | Integration | Semantic web search | feat/ahmed-toolbox-mcp | UNTESTED | configured; Doctor explicitly warns connectivity not proven |
| Scrapling | Integration | HTTP acquisition fallback | feat/ahmed-toolbox-mcp | UNTESTED | MCP tools exposed; live failure/success tests pending |
| Scrapling Stealth | Integration | Anti-bot escalation fallback | feat/ahmed-toolbox-mcp | UNTESTED | MCP tool exposed; live test pending |
| Browser Use | Integration | Interactive browser fallback | feat/ahmed-toolbox-mcp | WORKING | reach_doctor browser_use=ok; binary present in Railway build |
| yt-dlp | Integration | YouTube/media acquisition | feat/ahmed-toolbox-mcp | WORKING | reach_doctor youtube=ok; Railway build installed yt-dlp |
| RSS/feedparser | Integration | RSS/Atom ingest | feat/ahmed-toolbox-mcp | WORKING | reach_doctor rss=ok |
| GitHub | Integration | Repository/code operations | feat/ahmed-toolbox-mcp | WORKING | external GitHub connector write permission verified; in-container gh auth remains warn |
| Railway | Integration | Production deployment/runtime | feat/ahmed-toolbox-mcp | WORKING | authenticated Railway APIs returned deployment/config/logs/metrics |
| PanWatch adapter | Integration | Allow PanWatch to consume research/reach without moving decision logic into Research | feat/ahmed-toolbox-mcp | UNTESTED | `integrations/panwatch/src/platform/marketdata/xau_research_provider.py` exists; E2E pending |
| Whisper / STT | Integration | Speech-to-text if actually installed/configured | feat/ahmed-toolbox-mcp | UNKNOWN | Must be resolved in Step 2 from code/build/runtime; no success is claimed |
| OCR / Tesseract | Integration | OCR if actually installed/configured | feat/ahmed-toolbox-mcp | UNKNOWN | Must be resolved in Step 2; no success is claimed |

## Actual MCP Tool Inventory at Freeze Start

The following tools were present in the live MCP/plugin runtime for this conversation. Presence means **exposed**, not necessarily execution-verified.

| Component | Type | Purpose | Branch | Status | Verification |
|---|---|---|---|---|---|
| reach_doctor | MCP tool / Reach / Acquisition | Reach / Acquisition capability | feat/ahmed-toolbox-mcp | WORKING | Executed live; returned current channel/backend health. |
| reach_web_search | MCP tool / Reach / Acquisition | Reach / Acquisition capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| reach_youtube_browser_inspect | MCP tool / Reach / Acquisition | Reach / Acquisition capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| reach_media_ingest | MCP tool / Reach / Acquisition | Reach / Acquisition capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| reach_retrieve_url | MCP tool / Reach / Acquisition | Reach / Acquisition capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| reach_read_url | MCP tool / Reach / Acquisition | Reach / Acquisition capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_start_run | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_record_source | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_record_source_relationship | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_add_evidence | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_add_claim | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_create_output | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_get_output | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_audit_output | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_evaluate_source_independence | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_get_source_independence | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_evaluate_freshness | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_get_freshness | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_export_ledger | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_audit_run | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| research_complete_run | MCP tool / Research Engine | Research Engine capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| orchestration_start | MCP tool / Orchestration | Orchestration capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| orchestration_status | MCP tool / Orchestration | Orchestration capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| orchestration_execute | MCP tool / Orchestration | Orchestration capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| orchestration_verify | MCP tool / Orchestration | Orchestration capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| orchestration_handoff | MCP tool / Orchestration | Orchestration capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| orchestration_complete | MCP tool / Orchestration | Orchestration capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_status | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | WORKING | Executed live; returned schema ahmed-runtime-status/v1, status=ok. |
| runtime_execute_workflow | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_delegate | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_memory_put | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_memory_search | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_session_search | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_skill_save | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_skill_list | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | WORKING | Executed live; returned current durable skill record. |
| runtime_skill_candidates | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_skill_get | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_skill_rollback | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| runtime_skill_execute | MCP tool / Runtime | Runtime capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| ace_status | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | WORKING | Executed live; returned schema ace-status/v1, status=ok. |
| ace_create_campaign | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| ace_research | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| ace_generate | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| ace_record_metrics | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| ace_evaluate | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| ace_learn | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| ace_next | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| ace_report | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| ace_case_study | MCP tool / ACE | ACE capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| bulk_get | MCP tool / Scrapling | Scrapling capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| fetch | MCP tool / Scrapling | Scrapling capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| bulk_fetch | MCP tool / Scrapling | Scrapling capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| stealthy_fetch | MCP tool / Scrapling | Scrapling capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |
| bulk_stealthy_fetch | MCP tool / Scrapling | Scrapling capability | feat/ahmed-toolbox-mcp | UNTESTED | Exposed by live MCP tool catalog in this ChatGPT session; execution test pending Golden/Acceptance suite. |


## Skills

| Skill | Revision | Successes | Failures | Bayesian score | Freeze status | Verification |
|---|---:|---:|---:|---:|---|---|
| `__startup_doctor_skill__` | 1 | 2 | 0 | 0.75 | Experimental / Candidate | live `runtime_skill_list`; does not meet ≥10-success Trusted Production policy |

## Workflows / Agents

| Component | Type | Purpose | Status | Verification |
|---|---|---|---|---|
| Runtime DAG workflow engine | Workflow engine | Sequential/parallel bounded tool workflows | WORKING | `runtime_status.features.workflow_dag=true` and parallel_tool_rpc=true; scenario-level tests pending |
| Startup runtime acceptance | Workflow/acceptance | Ensure runtime can initialize without collapsing service | WORKING | Railway logs: `__AHMED_RUNTIME_STARTUP_ACCEPTANCE__ok` |
| Startup model-subagent acceptance | Agent acceptance | Ensure configured model subagent starts independently | WORKING | Railway logs: `__AHMED_MODEL_SUBAGENT_STARTUP_ACCEPTANCE__ok` |
| Dual-temporal live acceptance | Research acceptance | Validate current/stale/historical temporal handling | WORKING | Railway production logs recorded PASSED on current deployment |
| Model subagent | Agent | Bounded specialist delegation | UNTESTED | provider reports available; no current-turn delegated task test yet |

## Freeze Safety Constraints

Every stabilization change must be checked for:
- recursion / control-plane self-invocation;
- runaway tool calls or fallback loops;
- duplicate execution;
- unbounded concurrency;
- memory growth;
- SQLite corruption/lock behavior;
- secret leakage;
- unsafe subprocess arguments;
- privilege escalation / unauthorized writes.

Architecture boundary remains:

`Applications (PanWatch / ACE) → Runtime / Orchestration → Research / Evidence / Provenance → Agent-Reach / Acquisition → External sources`.

## Acceptance Rule

This freeze remains active until **Required Production Acceptance = 100%**. A Railway build/deployment marked SUCCESS is not by itself proof that production behavior passes acceptance.