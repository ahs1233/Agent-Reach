# Ahmed Toolbox — Failure Matrix

Date: **2026-09-23**  
Phase: **Stabilization & Hardening**

| Dependency / boundary | Failure Mode | Expected Response | Actual Response | Tested | Fixed / status |
|---|---|---|---|---|---|
| Exa | CLI route unavailable / remote failure | direct hosted Exa fallback or explicit error; local core remains alive | live `reach_web_search` currently succeeds; deterministic remote failure isolation is covered by Golden G067/G075 | yes | WORKING; fallback isolation covered |
| Jina | reader error / unavailable | escalate to Scrapling, retain attempt history | deterministic Golden G004/G066 passes; live Jina currently healthy | yes | WORKING |
| Scrapling fetch | process cannot spawn at memory ceiling | fail explicitly, then Stealth/Browser; must not crash Agent-Reach | live calls failed with `[Errno 11] Resource temporarily unavailable`; Railway showed ~1.0GB/1.0GB and subprocess fork failure | yes | RECOVERED after controlled redeploy; recurrence risk open |
| Scrapling Stealth | same process/resource exhaustion | fail explicitly, then Browser; no loop | live Stealth failed before redeploy, then returned HTTP 200 after redeploy | yes | RECOVERED; recurrence risk open |
| Browser Use | unavailable/crash | return bounded tool error and allow fallback chain to continue | deterministic browser failure path covered; live Browser Use currently healthy | yes | WORKING |
| yt-dlp / media | private/unavailable media | honest acquisition error, no invented transcript | media path returns explicit failure; production currently stops earlier because no STT provider is configured | partial | OPEN prerequisite / later live acceptance |
| Remote STT | missing provider / provider failure | explicit unavailable/error; no fake transcript | live `reach_media_ingest` returned `no transcription provider configured` | yes | HONEST FAILURE; production prerequisite missing |
| Local STT | not installed | capability must be NOT_PRESENT | canonical image has no local Whisper | yes | NOT_PRESENT; excluded from gate |
| OCR/Tesseract | not installed | capability must be NOT_PRESENT | canonical code/image has no Tesseract/OCR path | yes | NOT_PRESENT; excluded from gate |
| Railway Agent-Reach | process/container restart | durable SQLite state survives if volume/path is durable; service returns healthy | controlled production restart test deferred to Production Acceptance to avoid repeated disruption | pending | PENDING |
| SQLite | locked database | bounded operational error; no corruption; recovery after lock release | deterministic lock regression added; CI result pending | yes | TEST ADDED |
| PanWatch | consumer unavailable | Agent-Reach local core remains healthy; no reverse dependency | no explicit PanWatch→Toolbox runtime adapter exists on canonical branch | n/a | NOT_PRESENT as runtime dependency |
| MCP protocol | malformed JSON | 400 protocol error, service remains healthy | Golden G071 passes | yes | WORKING |
| MCP protocol | oversized request | 413 before reading oversized body | Golden G072 passes | yes | WORKING |
| MCP auth | invalid/missing bearer when enabled | 401, no tool execution | Golden G073 passes | yes | WORKING |
| Model/subagent | provider/task failure | isolated error; parent/runtime remains alive | Golden G068 passes; startup model-subagent acceptance passes | yes | WORKING; stress limits later |
| Research feature flag | Research disabled | hide Research when all dependent consumers are disabled; local Reach remains | Golden G074 passes after correct dependency configuration | yes | WORKING |
| External remote MCP | one dependency unavailable | remote disappears/fails without removing local core | Golden G067/G075 pass | yes | WORKING |
| Retrieval fallback | all backends unavailable | explicit `SOURCE_UNAVAILABLE`, empty content, full history | deterministic regression added with hard max 5 attempts | yes | TEST ADDED |
| Retrieval fallback | rate limit | classify `RATE_LIMITED`, fail-fast to next backend, no retry loop | deterministic regression added | yes | TEST ADDED |

## Scrapling incident evidence

Before controlled redeploy:
- service image: `ghcr.io/d4vinci/scrapling:0.4.15`
- memory observed: approximately **0.999 GB / 1.0 GB**
- failure: Playwright/Patchright subprocess creation failed with `BlockingIOError: [Errno 11] Resource temporarily unavailable`
- both normal and stealth MCP calls failed.

Recovery action:
- Railway redeploy id: `6763a239-367a-4a3c-b7d2-523eafc630eb`
- deployment reached `SUCCESS`
- post-redeploy normal `fetch`: HTTP 200 on `https://example.com`
- post-redeploy `stealthy_fetch`: HTTP 200 on the same target.

Interpretation: **recovery is proven, permanent remediation is not yet proven**. Stress testing must determine whether memory/process accumulation recurs.

## Fallback policy after hardening

The existing state machine remains authoritative:

`Jina → Scrapling fetch → Scrapling Stealth → configured Browser → targeted search`

No second fallback engine was created.

Hardening adds:
- fixed maximum of **5 total retrieval attempts** including targeted search;
- fail-fast per backend instead of unbounded same-backend retries;
- per-attempt `attempt_index`;
- UTC `started_at`;
- `duration_ms`;
- normalized `reason_code`;
- `next_backend`;
- final explicit `SOURCE_UNAVAILABLE` when all original retrieval paths fail;
- preserved retrieval history for provenance/audit.

This policy intentionally favors bounded escalation over immediate retries on rate limits or resource exhaustion, protecting latency and preventing runaway load.
