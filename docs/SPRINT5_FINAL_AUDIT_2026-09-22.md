# Sprint 5 Final Audit — Source Independence & Lineage Verification

**Date:** 2026-09-22  
**Base:** `609a6f11710d7c1893ab42645f7ec60e2d10d03a`  
**Branch:** `feat/research-engine-sprint5-source-independence`

## Result

Sprint 5 separates source quantity from source independence.

The central invariant is:

```text
URL count != SourceRecord count != Independent source-lineage count
```

Different URLs no longer qualify as independent confirmation by default.

## Implemented

### Conservative lineage grouping

Supporting sources for a Claim are collapsed when the engine has evidence of a
shared lineage through:

- same canonical URL;
- same retrieved-representation hash;
- same source family;
- same normalized publisher;
- explicit DERIVED_FROM;
- explicit SYNDICATED_FROM;
- explicit MIRRORS.

A different hash never proves independence.

### Explicit relationship ledger

Run-scoped SourceRelationship records support:

- DERIVED_FROM
- SYNDICATED_FROM
- MIRRORS
- INDEPENDENT_OF
- CITES
- QUOTES

Obvious contradictory INDEPENDENT_OF assertions are rejected when two sources
share canonical URL, representation hash, or publisher.

### Claim-level SourceIndependenceEvaluation

Every assessment persists:

- raw supporting source count;
- raw supporting URL count;
- effective lineage count;
- duplicate/dependency reduction;
- lineage membership and grouping reasons;
- explicit verified-independent pairs;
- relationship conflicts;
- strict confidence-basis source count.

Statuses:

- SINGLE_LINEAGE
- MULTIPLE_LINEAGES_UNVERIFIED
- PARTIALLY_VERIFIED_INDEPENDENCE
- VERIFIED_INDEPENDENT_LINEAGES

### Strict confidence basis

Sprint 5 does not calculate universal confidence.

It prevents raw URL count from becoming a confidence multiplier by emitting a
strict source-count input:

- all lineage pairs explicitly independent → effective lineage count;
- otherwise → 1.

This is intentionally conservative.

### Historical output integrity

Output provenance now distinguishes:

- source_independence_at_output
- latest_source_independence

Later discovery of source relationships cannot silently rewrite the independence
state that existed when an output was created.

### MCP

Added:

- research_record_source_relationship
- research_evaluate_source_independence
- research_get_source_independence

They remain behind the existing research feature gate.

## Test matrix

Automated tests cover:

1. ten URLs from one publisher collapse to one lineage;
2. different publishers are not automatically called independent;
3. explicit derivative relation collapses different publishers;
4. identical retrieved representations collapse;
5. pairwise explicit independence is required for fully verified status;
6. partial independence keeps strict confidence basis at one;
7. obvious contradictory independence assertions are rejected;
8. relationships are run-scoped;
9. Output keeps its historical independence snapshot;
10. run export includes relationships and latest Claim assessment;
11. MCP round trips for grouping and explicit independence.

## Live Railway acceptance

The live acceptance uses the real public IEA report through the production
retrieval fallback path.

It retrieves two different URLs for the same underlying IEA document, records
both as SourceRecords, links them with MIRRORS, creates EvidenceItems and a
Claim, and evaluates source independence.

Observed acceptance result:

```text
supporting URLs:                    2
supporting SourceRecords:           2
effective lineages:                 1
strict confidence-basis sources:    1
status:                              SINGLE_LINEAGE
relationship:                        MIRRORS
Output snapshot preserved:          yes
retrieval final tools:               Scrapling / Scrapling
```

This directly verifies that two URLs do not become two independent
confirmations.

## Architectural boundaries

Sprint 5 does not automatically infer:

- corporate ownership trees;
- shared anonymous sources;
- common underlying proprietary datasets;
- coordinated information operations;
- deep editorial dependence from semantic similarity alone.

Such claims require stronger provenance/entity-resolution evidence.

## What remains

The next planned integrity layer is Numerical Conflict Resolution:

```text
same apparent metric
+ different values
→ compare definition
→ units
→ geography
→ reference period
→ actual/estimate/forecast semantics
→ revision/vintage
→ source lineage
→ genuine conflict vs compatible difference
```

Subsequent layers remain Claim Graph, falsification/watch indicators, persistent
PanWatch state, and full PanWatch integration.
