# Sprint 4 — Retrieval Fallback State Machine

**Date:** 2026-09-22  
**Base:** `b9662f608413ce6da9c00c0152e5b40de3d5179c`  
**Branch:** `feat/research-engine-sprint4-fallback-state-machine`

## Goal

Replace ad-hoc retrieval retry logic with one bounded, auditable state machine.

Target path:

```text
JINA
  ├─ success → return content
  └─ fail / blocked / incomplete
        ↓
SCRAPLING_FETCH
  ├─ success → return content
  └─ fail / blocked / incomplete
        ↓
STEALTHY_FETCH
  ├─ success → return content
  └─ fail / blocked / incomplete
        ↓
OPTIONAL BROWSER ADAPTER
  ├─ success → return content
  └─ fail / unavailable → SOURCE_UNAVAILABLE
```

## Core principles

- Retrieval does not decide truth.
- Every escalation must have an explicit recorded reason.
- The attempt count is bounded.
- A failed tool is never represented as a successful read.
- A representation that does not contain caller-required evidence terms is PARTIAL, not SUCCESS.
- Anti-bot/challenge pages are BLOCKED, not valid content.
- Browser escalation is optional and configured externally; the core does not add a new browser dependency.
- When no browser adapter exists and all read-only paths fail, the result explicitly says `SOURCE_UNAVAILABLE` and `browser_required=true`.

## Public read-only tool

Adds:

`reach_retrieve_url`

Inputs:
- url
- max_chars
- min_chars
- required_terms[]
- require_all_terms

Output includes:
- final status
- final tool
- retrieval method
- content
- detailed attempts[]
- ledger-compatible retrieval_history[]
- escalation count
- browser fallback configured/required state

## Content acceptance

A retrieval representation is rejected/escalated when:
- tool returns an error;
- content is empty;
- anti-bot/challenge content is detected;
- content is below the declared minimum length;
- required evidence terms are missing.

The state machine does not use an LLM to decide success.

## Evidence Ledger compatibility

The returned `retrieval_history` uses the existing SourceRecord event contract:

```text
stage
tool
method
status
detail
```

Therefore the result can be passed directly into `research_record_source` without inventing a second provenance format.

## Browser boundary

Sprint 4 deliberately does not install a new browser runtime.

If `AHMED_TOOLBOX_BROWSER_FALLBACK_TOOL` is configured with an allowlisted namespaced gateway tool, it becomes the final stage.

Without that adapter:
- Jina / Scrapling / Stealthy still work normally;
- browser-required cases terminate honestly as SOURCE_UNAVAILABLE.

This keeps the retrieval core tool-agnostic and avoids coupling the evidence model to Patchright.

## Acceptance

Required tests:
1. Jina success stops without escalation.
2. Jina error escalates to Scrapling.
3. Missing expected evidence escalates further.
4. Anti-bot content is marked BLOCKED.
5. All read-only failures end as SOURCE_UNAVAILABLE.
6. Configured browser adapter is used only as the last stage.
7. Remote JSON content is unwrapped before validation.
8. Retrieval history is directly compatible with SourceRecord.
9. Gateway exposes the orchestration tool.
10. Live real-source acceptance preserves final retrieval tool and full attempt history into the Evidence Ledger.

## Out of scope

- source independence
- source quality scoring
- numerical conflict resolution
- Claim Graph
- falsification/watch indicators
- persistent PanWatch state
- authentication redesign
