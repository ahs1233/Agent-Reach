# Sprint 3 — Metric-Aware Freshness Engine

**Date:** 2026-09-22  
**Base:** `80b0d98510cf23a9558c1fc3357d809db0bb5317`  
**Branch:** `feat/research-engine-sprint3-freshness`

## Goal

Make freshness an auditable property of Evidence without confusing:
- publication date;
- underlying data cutoff;
- retrieval time.

The engine must answer:

> Is this Evidence current enough for the metric/use case being evaluated?

It must not answer that from page publication date alone.

## Core rule

```text
publication_date != data_cutoff != retrieved_at
```

Freshness uses `data_cutoff` as its evidence-time basis.

If `data_cutoff` is missing or unparseable, the engine returns `UNKNOWN`.
It does **not** silently substitute publication date.

## Policies

Built-in policy profiles:

- `market_price`: max age 1 hour.
- `breaking_news`: max age 6 hours.
- `technology_state`: max age 90 days.
- `macro_indicator`: latest known official release.
- `company_guidance`: latest known release.
- `structural_data`: latest known authoritative release.
- `academic_evidence`: report age without treating recency as a truth gate.
- `custom_max_age`: caller-specified maximum age.

The profiles are policy defaults, not claims that every domain has identical update cadence.

## Date precision

The engine preserves temporal precision.

Examples:

- `2026` = YEAR
- `2026-09` = MONTH
- `2026-09-22` = DAY
- timestamp = DATETIME

A coarse date may straddle the freshness boundary. In that case the engine returns:

`UNCERTAIN / DATE_PRECISION_CROSSES_FRESHNESS_BOUNDARY`

instead of inventing midnight-level precision.

## Status model

- `FRESH`
- `STALE`
- `UNCERTAIN`
- `AGE_REPORTED`
- `UNKNOWN`

No universal numerical freshness score is calculated in Sprint 3.

Returned metadata includes:
- policy name/mode;
- status;
- reason code;
- as-of time;
- data-cutoff basis and precision;
- minimum/maximum possible age from temporal precision;
- policy threshold where applicable;
- latest-known cutoff where applicable.

## Persistence

Every evaluation is immutable and persisted as `FreshnessEvaluation`:

```text
EvidenceItem
  ↓ evaluated_by
FreshnessEvaluation
```

Readable IDs use `FR-xxxxxx`.

The latest evaluation is exposed in:
- ResearchRun export;
- OutputFragment provenance.

Thus a consumer can reconstruct:

```text
Output
↓
Claim
↓
Evidence
├─ Source
└─ FreshnessEvaluation
```

## Latest-release policies

A claim that a macro indicator is current requires knowledge of the latest known release.

Therefore:

```text
macro_indicator + no latest_known_cutoff
→ UNKNOWN
```

This is deliberate. The engine will not infer "latest" merely because a page was retrieved today.

When the caller supplies `latest_known_cutoff`:
- older evidence → STALE;
- matching/overlapping release → FRESH;
- evidence newer than the declared latest reference → FRESH with an explicit reason.

Discovery of the actual latest release belongs to the research/acquisition workflow.

## MCP

Adds:
- `research_evaluate_freshness`
- `research_get_freshness`

Both remain behind the existing `AHMED_RESEARCH_ENABLED` feature gate with the rest of the research write surface.

## Acceptance

Required cases:

1. recent market-price timestamp → FRESH;
2. old market-price timestamp → STALE;
3. fresh publication + old data cutoff → STALE;
4. missing data cutoff → UNKNOWN;
5. coarse date crossing threshold → UNCERTAIN;
6. macro indicator without latest release reference → UNKNOWN;
7. macro indicator older than declared latest release → STALE;
8. academic evidence reports age without automatic stale rejection;
9. evaluation persists and survives run export;
10. consumer provenance carries the latest freshness evaluation;
11. MCP round-trip passes;
12. all Sprint 1 and Sprint 2 tests remain green.

## Explicitly out of scope

- automatic source-independence grouping;
- production retrieval fallback state machine;
- numerical conflict resolution;
- Claim Graph;
- confidence recomputation;
- PanWatch persistence/monitoring;
- Postgres migration.
