# Sprint 5 — Source Independence & Lineage Verification

**Date:** 2026-09-22  
**Base:** `609a6f11710d7c1893ab42645f7ec60e2d10d03a`  
**Branch:** `feat/research-engine-sprint5-source-independence`

## Goal

Prevent source-count inflation.

The engine must distinguish:

```text
URL count
!=
Source record count
!=
Independent source-lineage count
```

Ten URLs that all originate from one publisher, one syndicated wire story, one
mirror, or one explicitly known parent source must not be treated as ten
independent confirmations.

## Conservative rule

Different URLs are **not** proof of independence.

A supporting source is collapsed into the same lineage when any of these are
known:

- same canonical URL;
- same retrieved-representation hash;
- same `source_family_id`;
- same normalized publisher;
- explicit `DERIVED_FROM`;
- explicit `SYNDICATED_FROM`;
- explicit `MIRRORS`.

The representation hash remains scoped to the retrieved representation. A hash
match is useful evidence of duplication. A hash mismatch is **not** proof of
independence.

## Explicit source relationships

Supported relationship types:

- `DERIVED_FROM`
- `SYNDICATED_FROM`
- `MIRRORS`
- `INDEPENDENT_OF`
- `CITES`
- `QUOTES`

`CITES` and `QUOTES` are recorded provenance but do not automatically collapse
sources or prove independence.

`INDEPENDENT_OF` is intentionally strict. The store rejects an independence
assertion when two sources already share the same canonical URL, retrieved
representation hash, or normalized publisher.

## Claim-level assessment

The evaluator operates on the **supporting evidence sources of one Claim**.

It returns:

- `supporting_source_count`
- `supporting_url_count`
- `effective_lineage_count`
- `duplicate_or_dependency_reduction`
- lineage membership and grouping reasons
- verified independent lineage pairs
- relationship conflicts
- `strict_confidence_basis_source_count`

Status values:

- `SINGLE_LINEAGE`
- `MULTIPLE_LINEAGES_UNVERIFIED`
- `PARTIALLY_VERIFIED_INDEPENDENCE`
- `VERIFIED_INDEPENDENT_LINEAGES`

## Strict confidence-basis rule

Sprint 5 does **not** create a universal confidence formula.

It does, however, expose a strict source-count input so downstream confidence
logic cannot use raw URL count:

```text
if all lineage pairs are explicitly independent:
    strict_confidence_basis_source_count = effective_lineage_count
else:
    strict_confidence_basis_source_count = 1
```

This is deliberately conservative. It prevents five copied articles from
becoming “five confirmations” while leaving future confidence policy as a
separate layer.

## Persistence

Source relationships are immutable run-scoped records:

```text
Source ──relationship──> Source
```

Claim source-independence assessments are also persisted:

```text
Claim
  ↓ supported by
Evidence
  ↓
Sources
  ↓ assessed as
SourceIndependenceEvaluation (SI-xxxxxx)
```

## Output history

Outputs expose both:

- `source_independence_at_output`
- `latest_source_independence`

This prevents a later discovery of source lineage from silently rewriting the
historical state that existed when an answer, alert, dashboard state, or API
result was produced.

If no assessment existed at output creation, the historical snapshot remains
null even if a later assessment is added.

## MCP

Adds:

- `research_record_source_relationship`
- `research_evaluate_source_independence`
- `research_get_source_independence`

These remain behind the existing research feature gate.

## Acceptance requirements

1. Ten URLs from one publisher → one effective lineage.
2. Different publishers without proof → multiple lineages, independence unverified.
3. Explicit derivative relationship collapses different publishers.
4. Identical representation hash collapses mirror-like copies.
5. Three lineages become verified independent only after all three pairwise
   independence relationships are recorded.
6. Partial independence does not raise the strict confidence-basis count.
7. Obvious same-lineage sources cannot be asserted as independent.
8. Relationships are run-scoped.
9. Output preserves historical independence snapshot.
10. Run export carries source relationships and latest Claim assessment.
11. MCP round trip succeeds.
12. Previous Sprints remain green.

## What Sprint 5 does not claim

It does not automatically infer deep editorial ownership, corporate parentage,
shared anonymous sourcing, common datasets, or coordinated information
operations from text alone.

Those require stronger entity-resolution or external provenance evidence.

## Next layer

After Sprint 5, the next planned layer is the **Numerical Conflict Resolver**:
when independent or dependent sources report different numbers, the engine
must distinguish definition, geography, time period, units, actual vs forecast,
revision, and genuine contradiction before deciding what the conflict means.
