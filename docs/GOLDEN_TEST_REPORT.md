# Ahmed Toolbox — Golden Test Report

Date: **2026-09-23**  
Suite schema: `ahmed-golden-suite/v1`  
Canonical branch: `feat/ahmed-toolbox-mcp`

## Suite composition

| Class | Count | Gate behavior |
|---|---:|---|
| Deterministic / core | 49 | Required in reproducible CI |
| Live integration | 12 | Separate live job/manual validation; never silently counted as core PASS |
| Production-only | 1 | Production/staging acceptance only |
| NOT_PRESENT | 18 | Explicitly excluded from regression gate; no feature added during freeze |
| Total | 80 | — |

## Deterministic execution baseline

Golden commit: `f2f0b7b241060c1249c6a1c896e9d660835976a6`  
Harness-fix commit: `8a019260f21499b008c68334323d0253988b0c96`

The first run correctly failed three scenarios:
- G014: test-harness misuse of feedparser (`loads` does not exist).
- G072: client-side BrokenPipe while the server correctly rejected an oversized Content-Length early.
- G074: test left ACE/Orchestration/Runtime enabled; those are designed to require/re-enable Research.

The harness was corrected without weakening product assertions. On the corrected SHA, Ahmed ToolBox CI Full regression reported:

`838 passed, 17 skipped, 16 subtests passed in 18.85s`

The 17 skipped count was generated before G011 was reclassified from live-integration to NOT_PRESENT; the next CI run must show 18 explicit NOT_PRESENT skips.

### Core Golden result

| Result | Count |
|---|---:|
| PASS | 49 |
| FAIL | 0 |
| SKIP | 0 |
| NOT_PRESENT | 0 |
| Deterministic total | 49 |

## Live observations executed during stabilization

| ID | Scenario | Actual result | Status | Evidence |
|---|---|---|---|---|
| G001 | Exa search returns results | Exa returned real current results through `reach_web_search` | PASS | live MCP execution |
| G003 | Jina reads simple page | `reach_read_url(https://example.com)` returned Example Domain | PASS | live MCP execution |
| G015 | reach_doctor health accuracy | Doctor accurately reported Jina/Browser/yt-dlp available and did not falsely promote Exa connectivity | PASS | live MCP execution plus independent Exa call |
| G036 | Public video ingest | `reach_media_ingest` failed with `no transcription provider configured` | FAIL | live MCP execution; feature exists but Production prerequisite is absent |

Other live scenarios remain **SKIP** until their prerequisites/live job are executed. No unexecuted scenario is counted as PASS.

## NOT_PRESENT canonical capabilities

The following are intentionally classified NOT_PRESENT rather than implemented during the freeze:
- G011 Local STT / Local Whisper.
- G013 OCR acquisition test.
- G038–G050 canonical video-v2 capabilities (confidence/frame sampling/OCR/audio-visual merge/deep schema/translation/longitudinal reasoning/knowledge graph/chapters/speaker inference/media verification schema).
- G063 market-specialist routing (no canonical market specialist).
- G076/G077 explicit PanWatch → Ahmed Toolbox Research/Reach adapter calls.

The experimental branch `feat/video-evidence-v2` contains future media work but remains unmerged.

## Current gaps

1. Live media ingest is not Production-capable because no remote STT provider credential is configured and canonical Docker lacks ffmpeg.
2. Scrapling normal and stealth are currently broken by resource/process exhaustion; Golden deterministic fallback behavior passes, but live fallback recovery is not yet acceptable.
3. Production restart recovery (G069), subagent live limit enforcement (G054/G055), and remaining external live tests still need later steps.

## Gate interpretation

**Golden deterministic/core gate: PASS.**  
**Full stabilization gate: NOT YET PASSED.**

This report deliberately separates deterministic PASS from live FAIL/SKIP and NOT_PRESENT.
