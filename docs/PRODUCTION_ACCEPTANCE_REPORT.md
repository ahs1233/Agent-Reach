# Ahmed Toolbox Production Acceptance Report

Date: 2026-09-24  
Repository: `ahs1233/Agent-Reach`  
Branch: `feat/ahmed-toolbox-mcp`  
Target SHA: `f6b9f05e2b1f78325c0b5d0223c5eb2f6c3d9eca`  
Target deployment: `f393233a-ee05-49d0-a107-7b15cf8ed7ca`  
Target URL: `https://agent-reach-production.up.railway.app`

## Acceptance status

**STABILIZATION NOT YET PASSED**

Production deployment, CI, deterministic stress, dual-temporal live acceptance, and skill-policy behavior are verified. The mandatory external authenticated Production Acceptance suite has not completed, so no 100% production acceptance claim is made.

## Required external suite

The repository contains `scripts/production_acceptance.py`, designed to run from a separate host and verify:

1. `/health`
2. MCP `tools/list`
3. `reach_doctor`
4. `reach_web_search`
5. `reach_read_url`
6. `research_start_run`
7. real production-capable Browser Use video evidence path
8. full source → evidence → claim → output → audit Research workflow
9. malformed JSON rejection
10. five concurrent MCP clients

`reach_media_ingest` STT remains not production-capable because no transcription provider is configured; the production-capable rendered Browser Use video path is the media acceptance path.

## What was actually attempted

A new isolated Railway acceptance service was requested from the canonical repository. Railway rejected service creation with:

`Free plan resource provision limit exceeded. Please upgrade to provision more resources!`

A second approach used the existing historical `Ahmed-Research-Sprint2` service as a temporary external runner. Its original pre-deploy acceptance was preserved. Railway configuration-as-code restored its original pre-deploy command during redeploy, so the new `production_acceptance.py` command did not execute. No Production Acceptance result was fabricated from that deployment.

The historical Sprint2 service was restored to its original pre-deploy command:

`python -m agent_reach.toolbox.sprint2_live_acceptance`

Temporary TARGET variables were cleared after the attempt.

## External evidence currently available

Railway observed external `openai-mcp/1.0.0` POST traffic to `/mcp` returning HTTP 200 on the target deployment, with observed durations including 299 ms, 1356 ms and 6009 ms. This proves external MCP traffic reached production, but it is not a substitute for the required named 10-case acceptance suite.

## Remaining blockers before PASS

1. Execute `scripts/production_acceptance.py` from an external runner that can receive `AHMED_TOOLBOX_TOKEN` without exposing it.
2. Require all 10 named acceptance cases to PASS.
3. Re-run or resolve the degraded model-subagent provider deadline if model-subagent live acceptance is included in the final production reliability gate.
4. Record the exact external report and update this document.
