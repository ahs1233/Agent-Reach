# Ahmed Toolbox — Full System Inventory

Date: **2026-09-23**

Canonical repository: `ahs1233/Agent-Reach`  
Canonical stabilization branch: `feat/ahmed-toolbox-mcp`  
Inventory base SHA: `7cc5d97638dc145a970d1bee84695b20111b1bf9`

Status vocabulary:
- **WORKING** — verified by current live execution and/or current CI.
- **UNTESTED** — present and exposed, but the specific behavior has not yet been exercised in this stabilization run.
- **BROKEN** — current execution failed.
- **NOT_PRESENT** — absent from canonical code/runtime.
- **DEFERRED** — exists only on a non-canonical branch and is intentionally excluded from the freeze.

Current CI on this SHA:
- `Ahmed ToolBox CI`: **SUCCESS**
- `ci`: **SUCCESS**
- Full regression job is part of current workflows and completed successfully through the workflow run.

## System / Engine Inventory

| Component | Exists | Path | Tested now | Test file(s) | Passes | MCP exposed | Deployed | Integrated with | Status |
|---|---|---|---|---|---|---|---|---|---|
| Agent-Reach acquisition core | yes | `agent_reach/` | yes | broad `tests/test_channels.py`, channel tests | yes via CI | via gateway | yes | Research/Gateway | WORKING |
| Ahmed Toolbox MCP server | yes | `agent_reach/toolbox/server.py` | yes | `tests/test_toolbox_server.py`, `tests/test_mcp_http_discovery.py` | yes via CI | yes | yes | all Toolbox layers | WORKING |
| Research Engine | yes | `agent_reach/toolbox/research.py` | yes | research test suite | yes via CI + live `research_start_run` R-000016 | yes | yes | Runtime/Orchestration/ACE/PanWatch | WORKING |
| Evidence / Provenance | yes | `agent_reach/toolbox/research.py` | yes | `test_research_evidence_foundation.py`, output/audit tests | yes via CI | yes | yes | Research outputs | WORKING |
| Source Independence | yes | `source_independence.py`, `research.py` | yes | `test_research_source_independence*.py` | yes via CI | yes | yes | Research claims | WORKING |
| Freshness / temporal validity | yes | `freshness.py`, `temporal.py`, `research.py` | yes | freshness + dual-temporal tests | yes via CI + production startup acceptance | yes | yes | Research evidence | WORKING |
| Orchestration | yes | `orchestration.py`, `orchestration_mcp.py` | yes | `test_toolbox_orchestration.py` | yes via CI + live smoke `orch_f9ead...` | yes | yes | Runtime/Research/Reach | WORKING |
| Runtime DAG | yes | `runtime.py`, `runtime_mcp.py` | yes | `test_toolbox_runtime.py`, trust-gate tests | yes via CI + live workflow | yes | yes | all local tools | WORKING |
| Durable Memory | yes | `runtime.py` | yes | `test_toolbox_runtime.py` | yes; live write/read marker succeeded | yes | yes | Runtime | WORKING |
| Session Search | yes | `runtime.py` | CI only | `test_toolbox_runtime.py` | yes via CI | yes | yes | Runtime | WORKING |
| Skills / versioning | yes | `runtime.py` | yes | `test_toolbox_runtime.py`, trust-gate | yes via CI + live list | yes | yes | Runtime learning | WORKING |
| Delegation / model subagents | yes | `subagent.py` | startup + CI | `test_toolbox_subagent.py`, startup isolation | yes at startup/CI; deep load pending | yes | yes | Runtime/Orchestration | WORKING |
| ACE | yes | `ace.py`, `ace_mcp.py` | yes | `test_toolbox_ace.py` | yes via CI + live `ace_status` | yes | yes | Research/Runtime | WORKING |
| Media Intelligence v1 | yes | `video.py`, `transcribe.py` | yes | `test_toolbox_video.py`, `test_transcribe.py` | unit tests pass; live ingest fails | yes | yes | yt-dlp + remote STT | BROKEN in production |
| Media Intelligence v2 | not canonical | branch `feat/video-evidence-v2` | branch-only | branch-only media tests | not part of canonical gate | no canonical exposure | no canonical deploy | future media expansion | DEFERRED |

## Acquisition / Integration Inventory

| Integration | Exists in code | Runtime check | Production behavior | Status | Evidence |
|---|---|---|---|---|---|
| Jina Reader | yes | yes | `reach_read_url(example.com)` succeeded | WORKING | live call |
| Exa | yes | yes | `reach_web_search` returned real results | WORKING | live call; Doctor warning is conservative because it does not probe remote connectivity |
| Scrapling fetch | remote MCP | yes | direct call failed `[Errno 11] Resource temporarily unavailable` | BROKEN | live call + Railway logs |
| Scrapling Stealth | remote MCP | yes | direct call failed same resource error | BROKEN | live call + Railway logs |
| Browser Use | yes | yes | YouTube rendered inspection succeeded | WORKING | live `reach_youtube_browser_inspect` |
| yt-dlp | yes | yes | Doctor reports available; production image installs it | WORKING | live Doctor + build logs |
| RSS/feedparser | yes | yes | Doctor reports active backend | WORKING | live Doctor |
| Remote STT (Groq/OpenAI Whisper-compatible) | yes | yes | no provider key configured; live media ingest returns `no transcription provider configured` | BROKEN / NOT_CONFIGURED | live call |
| Local Whisper | no | no | no local Whisper package/model in canonical Docker | NOT_PRESENT | pyproject/Dockerfile |
| ffmpeg | code expects it | no | absent from canonical Docker; Doctor marks xiaoyuzhou off | NOT_PRESENT in production image | Dockerfile + Doctor |
| OCR/Tesseract | no canonical implementation | no | `video.py` explicitly states visual OCR not extracted | NOT_PRESENT | canonical code |
| GitHub CLI inside Railway image | binary present | warn | explicit CLI auth not proven | UNTESTED as CLI auth path | Doctor |
| GitHub connector used by this stabilization | external connector | yes | admin/push verified and commits created | WORKING | connector permission + commits |
| Railway | external connector | yes | config/deploy/log/metrics read successfully | WORKING | Railway connector |
| PanWatch research-data adapter | yes | `integrations/panwatch/src/platform/marketdata/xau_research_provider.py` | Yahoo/GC=F research-data adapter is covered by CI | WORKING | code + CI |
| PanWatch → Ahmed Toolbox Research/Reach | no explicit canonical adapter found | n/a | no canonical calls to `research_start_run` or `reach_web_search` were found in the PanWatch integration tree | NOT_PRESENT | code inventory |
| mcporter/Exa config | yes | `config/mcporter.json` | Exa live search succeeded | WORKING | code + live call |

### Scrapling root cause

Railway service: `Scrapling`  
Image: `ghcr.io/d4vinci/scrapling:0.4.15`  
Memory current/max observed: ~**0.999 GB / 1.0 GB**  
Failure: browser subprocess spawn fails in Playwright/Patchright with `BlockingIOError: [Errno 11] Resource temporarily unavailable`.

This is a real reliability defect and is carried into the Failure Matrix / hardening steps.

## Actual MCP Tool Inventory

All tools below were present in the live MCP runtime, not merely documentation.

| Tool | Code owner/path | CI coverage | Live execution in this stabilization | Deployed | Current status |
|---|---|---|---|---|---|
| reach_doctor | gateway / Agent-Reach doctor | yes | yes | yes | WORKING |
| reach_web_search | gateway / Exa route | yes | yes | yes | WORKING |
| reach_youtube_browser_inspect | gateway + browser_use | yes | yes | yes | WORKING |
| reach_media_ingest | gateway + video.py | yes | yes, failed honestly | yes | BROKEN / NOT_CONFIGURED |
| reach_retrieve_url | retrieval.py + gateway | yes | yes | yes | WORKING |
| reach_read_url | Jina route | yes | yes | yes | WORKING |
| research_start_run | research_mcp.py | yes | yes | yes | WORKING |
| research_record_source | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_record_source_relationship | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_add_evidence | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_add_claim | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_create_output | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_get_output | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_audit_output | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_evaluate_source_independence | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_get_source_independence | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_evaluate_freshness | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_get_freshness | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_export_ledger | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_audit_run | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| research_complete_run | research_mcp.py | yes | Golden pending | yes | WORKING by CI |
| orchestration_start | orchestration_mcp.py | yes | yes | yes | WORKING |
| orchestration_status | orchestration_mcp.py | yes | yes | yes | WORKING |
| orchestration_execute | orchestration_mcp.py | yes | Golden pending | yes | WORKING by CI |
| orchestration_verify | orchestration_mcp.py | yes | Golden pending | yes | WORKING by CI |
| orchestration_handoff | orchestration_mcp.py | yes | Golden pending | yes | WORKING by CI |
| orchestration_complete | orchestration_mcp.py | yes | Golden pending | yes | WORKING by CI |
| runtime_status | runtime_mcp.py | yes | yes | yes | WORKING |
| runtime_execute_workflow | runtime_mcp.py | yes | yes | yes | WORKING |
| runtime_delegate | runtime_mcp.py | yes | Golden pending | yes | WORKING by CI/startup |
| runtime_memory_put | runtime_mcp.py | yes | yes | yes | WORKING |
| runtime_memory_search | runtime_mcp.py | yes | yes | yes | WORKING |
| runtime_session_search | runtime_mcp.py | yes | Golden pending | yes | WORKING by CI |
| runtime_skill_save | runtime_mcp.py | yes | Golden pending | yes | WORKING by CI |
| runtime_skill_list | runtime_mcp.py | yes | yes | yes | WORKING |
| runtime_skill_candidates | runtime_mcp.py | yes | Golden pending | yes | WORKING by CI |
| runtime_skill_get | runtime_mcp.py | yes | Golden pending | yes | WORKING by CI |
| runtime_skill_rollback | runtime_mcp.py | yes | Golden pending | yes | WORKING by CI |
| runtime_skill_execute | runtime_mcp.py | yes | Golden pending | yes | WORKING by CI |
| ace_status | ace_mcp.py | yes | yes | yes | WORKING |
| ace_create_campaign | ace_mcp.py | yes | Golden pending | yes | WORKING by CI |
| ace_research | ace_mcp.py | yes | Golden pending | yes | WORKING by CI |
| ace_generate | ace_mcp.py | yes | Golden pending | yes | WORKING by CI |
| ace_record_metrics | ace_mcp.py | yes | Golden pending | yes | WORKING by CI |
| ace_evaluate | ace_mcp.py | yes | Golden pending | yes | WORKING by CI |
| ace_learn | ace_mcp.py | yes | Golden pending | yes | WORKING by CI |
| ace_next | ace_mcp.py | yes | Golden pending | yes | WORKING by CI |
| ace_report | ace_mcp.py | yes | Golden pending | yes | WORKING by CI |
| ace_case_study | ace_mcp.py | yes | Golden pending | yes | WORKING by CI |
| scrapling bulk_get | remote Scrapling MCP | external | not executed | yes | UNTESTED |
| scrapling fetch | remote Scrapling MCP | external | failed | yes | BROKEN |
| scrapling bulk_fetch | remote Scrapling MCP | external | not executed | yes | UNTESTED |
| scrapling stealthy_fetch | remote Scrapling MCP | external | failed | yes | BROKEN |
| scrapling bulk_stealthy_fetch | remote Scrapling MCP | external | not executed | yes | UNTESTED |

## Branch Inventory

| Branch | Relation to canonical | Unique commits/code | Classification | Stabilization action |
|---|---|---|---|---|
| main | behind canonical by 135 | no ahead commits | obsolete for this stabilization line | do not use |
| feat/ahmed-toolbox-mcp | canonical | n/a | ACTIVE | only stabilization writes here |
| feat/ace-content-experiment-engine | diverged: +8 / -25 | yes | superseded historical feature branch | preserve; no merge during freeze |
| feat/research-engine-sprint1-evidence-foundation | diverged: +25 / -94 | yes | superseded historical sprint | preserve; no merge |
| feat/research-engine-sprint2-citations-guardrail | diverged: +19 / -92 | yes | superseded historical sprint | preserve; no merge |
| feat/research-engine-sprint3-freshness | diverged: +23 / -91 | yes | superseded historical sprint | preserve; no merge |
| feat/research-engine-sprint4-fallback-state-machine | diverged: +18 / -87 | yes | superseded historical sprint | preserve; no merge |
| feat/research-engine-sprint5-source-independence | diverged: +12 / -86 | yes | superseded historical sprint | preserve; no merge |
| fix/research-sprint1-live-fallback | diverged: +1 / -93 | yes | historical fix branch | preserve; no merge |
| test/research-engine-efficiency-benchmark | diverged: +2 / -91 | yes | historical benchmark branch | preserve; no merge |
| ci/dual-temporal-head-20260923 | diverged: +23 / -13 | yes | temporary integration/CI branch, superseded by canonical production line | preserve; no blind merge |
| work/dual-temporal-native-20260923 | diverged: +23 / -13 | yes | temporary work branch, same head family as CI branch | preserve; no blind merge |
| feat/video-evidence-v2 | diverged: +77 / -87 | **yes, substantial** | ACTIVE EXPERIMENTAL / DEFERRED | preserve for post-freeze review; do not merge now |

## Database Inventory

The project uses SQLite stores initialized from code rather than a standalone migrations directory.

### Research DB

| Table | Purpose | Important indexes / FKs | Active use |
|---|---|---|---|
| research_runs | top-level research execution | run_id unique | live R-000016 created |
| sources | canonical/versioned source records | idx_sources_url_version; self FK previous_source_id | code + CI |
| research_run_sources | link sources to runs | PK(run_id,source_id); FKs run/source | code + CI |
| source_relationships | MIRRORS/INDEPENDENT/etc lineage | idx_source_relationships_run; FKs | code + CI |
| retrieval_events | retrieval provenance/history | FKs run/source | code + CI |
| evidence_items | evidence facts/passages | idx_evidence_run_source; FKs run/source | code + CI |
| claims | evidence-backed statements | FK run | code + CI |
| claim_evidence | support/contradiction links | composite PK; FKs | code + CI |
| source_independence_evaluations | persisted independence evaluations | idx_source_independence_claim | code + CI |
| outputs | consumer outputs | idx_outputs_run; FK run | code + CI |
| output_fragments | typed output fragments | FK output | code + CI |
| output_fragment_claims | fragment→claim links | composite PK; FKs | code + CI |
| freshness_evaluations | persisted freshness result | idx_freshness_evidence | code + CI |
| output_fragment_freshness | output freshness snapshot | FKs | code + CI |
| output_fragment_source_independence | output independence snapshot | FKs | code + CI |
| temporal_evaluations | evidence temporal validity | idx_temporal_evidence | code + CI |
| temporal_fusions | multi-evidence temporal fusion | idx_temporal_fusions_run | code + CI |

### Runtime DB

| Table | Purpose | Indexes | Active use |
|---|---|---|---|
| runtime_memory | durable bounded facts/procedures | PK memory_key | live write/read verified |
| runtime_sessions | session trace/search | idx_runtime_sessions_session | workflow session path active |
| runtime_skills | current skill revision/state | PK name | live skill list verified |
| runtime_skill_versions | immutable skill history | PK(name,revision) | code + CI |
| runtime_skill_outcomes | success/failure history | idx_runtime_skill_outcomes | code + CI |
| runtime_skill_candidates | auto-learning candidates | PK candidate_key | code + CI |

### Orchestration DB

| Table | Purpose | Indexes | Active use |
|---|---|---|---|
| orchestration_runs | bounded orchestration state/budget | PK orchestration_id | live smoke verified |
| orchestration_events | append-only hash-linked journal | idx_orch_events_run; unique event_hash | live smoke journal passed |

### ACE DB

| Table | Purpose | Active use |
|---|---|---|
| campaigns | campaign root state | code + CI |
| research_findings | evidence-backed findings | code + CI |
| personas | campaign persona constraints | code + CI |
| hypotheses | explicit experiment hypotheses | code + CI |
| experiments | experiment state | code + CI |
| variants | control/variant definitions | code + CI |
| assets | generated/package assets | code + CI |
| publications | publishing packages/status | code + CI |
| metric_snapshots | observed metrics only | code + CI |
| evaluations | experiment evaluation state | code + CI |
| learnings | persisted experiment learnings | code + CI |
| cost_records | provider/operation cost records | code + CI |

## Important findings carried forward

1. Scrapling is not healthy: both normal and stealth calls fail because the service is at its memory ceiling and cannot spawn browser subprocesses.
2. Media ingest is honest but non-functional in Production: no STT provider is configured, and canonical Docker also lacks ffmpeg.
3. OCR/Tesseract is not a missing dependency bug; it is simply not part of canonical v1. It is excluded from regression acceptance rather than added during freeze.
4. `feat/video-evidence-v2` contains meaningful future media capabilities but is intentionally deferred.
5. Runtime control-plane recursion protection remains required; workflows are bounded and current max subagent limits are 8 turns / 30 tool calls.
6. GitHub write capability for stabilization is the authenticated GitHub connector, not the in-container `gh` path.
