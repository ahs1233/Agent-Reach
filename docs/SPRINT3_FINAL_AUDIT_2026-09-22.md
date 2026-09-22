# Sprint 3 Final Audit — Metric-Aware Freshness Engine

**Date:** 2026-09-22  
**Base:** `80b0d98510cf23a9558c1fc3357d809db0bb5317`  
**Branch:** `feat/research-engine-sprint3-freshness`

## Result

Sprint 3 turns freshness from a vague age concept into an auditable,
metric-aware Evidence property.

The invariant is:

```text
publication_date != data_cutoff != retrieved_at
```

Freshness is evaluated from `data_cutoff` whenever freshness is requested.
The engine does not silently substitute publication date.

## Implemented

### Policy-aware freshness

Built-in profiles:
- market_price: max age 1 hour
- breaking_news: max age 6 hours
- technology_state: max age 90 days
- macro_indicator: compare with latest known release
- company_guidance: compare with latest known release
- structural_data: compare with latest known release
- academic_evidence: report age without making recency a truth gate
- custom_max_age: caller-specified threshold

### Precision-aware time handling

Supported temporal precision:
- YEAR
- MONTH
- DAY
- DATETIME

Coarse dates are represented as intervals. If an interval crosses a freshness
boundary, the result is `UNCERTAIN` rather than a fabricated precise verdict.

### Freshness statuses

- FRESH
- STALE
- UNCERTAIN
- AGE_REPORTED
- UNKNOWN

The core intentionally does not emit a universal numerical freshness score.

### Persistent evaluation records

Freshness evaluations are immutable records:

```text
EvidenceItem
  ↓ evaluated_by
FreshnessEvaluation (FR-xxxxxx)
```

Each record preserves:
- policy
- mode
- as-of time
- data-cutoff basis
- date precision
- minimum/maximum possible age
- threshold where applicable
- latest-known release reference where applicable
- reason code

### Output audit history

Output provenance distinguishes:

```text
freshness_at_output
vs
latest_freshness
```

This is important because a Claim consumed when its Evidence was fresh may be
re-evaluated as stale later. The old Output must not be rewritten historically.

If no freshness evaluation existed when the Output was created:
- `freshness_at_output = null`
- later evaluations may still appear as `latest_freshness`

### MCP surface

Added:
- `research_evaluate_freshness`
- `research_get_freshness`

They remain behind the existing `AHMED_RESEARCH_ENABLED` research feature gate.

## Verified edge cases

Tests cover:
- recent market price → FRESH
- old market price → STALE
- fresh publication date with old underlying data → STALE
- missing data cutoff → UNKNOWN
- coarse date crossing a threshold → UNCERTAIN
- latest-release policy without latest-known reference → UNKNOWN
- older release than declared latest → STALE
- exact matching latest cutoff → FRESH
- coarse overlap with latest release → UNCERTAIN
- academic evidence → AGE_REPORTED
- custom maximum age
- persistence in run export
- freshness in Output provenance
- immutable freshness snapshot at Output creation
- later freshness evaluation without rewriting historical Output state
- MCP round trip
- all previous Sprint 1 / Sprint 2 behavior remains under CI

## Live acceptance design

The live gate reuses the real Sprint 2 path:

```text
Exa discovery
→ Jina attempt
→ Scrapling fallback when required
→ Source
→ Evidence
→ Claim
→ Output
```

It then evaluates the same real EvidenceItem under different freshness policies.

Expected policy sensitivity:

```text
same Evidence
├─ technology_state (90d) → STALE
├─ custom 180d            → FRESH
└─ matching latest release→ FRESH
```

This demonstrates that freshness is a property of Evidence **under a policy**,
not an intrinsic universal truth label.

The live gate also checks that a later freshness evaluation does not retroactively
invent `freshness_at_output` for an Output created before freshness existed.

## Not solved in Sprint 3

Freshness does not yet answer:
- whether multiple sources are genuinely independent;
- which retrieval fallback should execute automatically;
- how conflicting numeric definitions should be reconciled;
- which Claims causally depend on another Claim;
- what would falsify a Claim;
- how PanWatch should persist and continuously update world state.

These remain subsequent layers in the frozen development order.

## Known architectural limits

1. Latest-release policies require a caller-provided `latest_known_cutoff`.
   The Freshness Engine does not discover releases by itself.
2. Source acquisition quality is separate from freshness.
3. A fresh source can still be wrong; a stale source can still be historically valid.
4. Freshness is Evidence-level. Claim confidence aggregation remains a later concern.
5. SQLite remains the current persistence backend; Sprint 3 does not change the storage strategy.

## Final chain after Sprint 3

```text
Source
↓
Evidence
├─ Freshness history
│   ├─ freshness_at_output
│   └─ latest_freshness
↓
Claim
↓
Consumer
↓
Output / State Update / Trigger
```

Sprint 3 is complete only when its final head has:
- full CI green;
- focused Ahmed ToolBox CI green;
- live isolated Railway acceptance PASS;
- PR mergeable.
