# Sprint 2 — Claim Consumption & Output Integrity

**Date:** 2026-09-22  
**Branch:** `feat/research-engine-sprint2-citations-guardrail`  
**Base:** `b0e331abcd3f947fc4eb8d19e818b2f4b4f4f8d9`  
**Depends on:** Sprint 1 Evidence Foundation

## Goal

Make every material use of a Claim traceable and semantically safe regardless of output type or domain.

Sprint 2 is not a report-formatting sprint.

The invariant is:

```text
OutputFragment
  ↓ derived_from
Claim
  ↓ supported_by / contradicted_by
EvidenceItem
  ↓ extracted_from
SourceRecord
```

## New concepts

### Claim observation semantics

Claims now carry an `observation_type` distinct from epistemic `classification`.

Allowed claim observation types:
- ACTUAL
- ESTIMATE
- FORECAST
- TARGET
- SCENARIO
- COMPANY_GUIDANCE
- MODEL_OUTPUT
- UNKNOWN

If omitted when a Claim is created, the store derives it only when all supporting evidence has one identical observation type. Otherwise it remains UNKNOWN.

This allows legitimate distinctions such as:

```text
classification = VERIFIED
observation_type = FORECAST
```

### OutputRecord

A generic consumer result:
- output_id
- run_id
- consumer_type
- consumer_id (optional)
- output_type
- payload
- metadata
- fragments

The core does not enumerate domains or consumer products.

### OutputFragment

Each material fragment:
- contains content and/or structured payload;
- references one or more claim_ids;
- declares asserted_observation_type;
- exposes machine-readable provenance back to Evidence and Source.

## Semantic guardrail

The consumer cannot silently promote or relabel the semantics of linked Claims.

Examples rejected:

```text
FORECAST → ACTUAL
TARGET → FORECAST
TARGET → COMPANY_GUIDANCE
ESTIMATE → ACTUAL
SCENARIO → FORECAST
MODEL_OUTPUT → ACTUAL
```

If multiple Claims with different observation semantics are consumed in one fragment, the fragment must explicitly use:

`MIXED`

The core also returns a canonical `rendered_content` prefixed with the semantic label. This is a safe generic representation; downstream UIs may render the structured semantic metadata differently.

## Machine provenance

The core does not hard-code footnote syntax.

A returned fragment contains:

```text
fragment_id
claim_ids[]
provenance_refs[]
  claim_id
  observation_type
  evidence[]
    evidence_id
    source_id
    source_url
    publisher
    publication_date
    data_cutoff
    retrieved_at
    retrieval_tool
```

A report UI may turn that into inline citations.
An alert UI may turn it into a source card.
A dashboard may expose an audit drawer.
An API may return the references as JSON.

## MCP surface

Sprint 2 adds:
- `research_create_output`
- `research_get_output`
- `research_audit_output`

Existing research tools remain feature-gated behind `AHMED_RESEARCH_ENABLED`.

## Acceptance cases

### A — Forecast
A FORECAST Claim consumed as ACTUAL must be rejected.

### B — Actual
An ACTUAL Claim may be consumed as ACTUAL.

### C — Target
A TARGET Claim may not be silently promoted to ACTUAL, FORECAST, or COMPANY_GUIDANCE.

### D — Alert
An ALERT must preserve:
`Alert → Claim → Evidence → Source`.

### E — QA
A browser/software observation must use the same core chain without report-specific behavior.

### Domain-agnostic matrix

The same core model is tested for:
- macro/economics;
- trading;
- science;
- software QA;
- competitive intelligence;
- AlSouq.

No domain-specific branch is permitted in the core output model.

## Definition of Done

Sprint 2 is complete when:

1. Every stored output fragment has one or more Claim references.
2. Every Claim used by an output resolves to Evidence.
3. Every EvidenceItem resolves to SourceRecord.
4. Claim observation semantics are available independently of claim classification.
5. Semantic promotion is rejected.
6. Mixed semantics require explicit MIXED status.
7. The output model works for non-report consumers.
8. ResearchRun export contains generic outputs.
9. MCP tools expose the same provenance chain.
10. Unit/CI and end-to-end acceptance pass.

## Explicitly out of scope

- freshness scoring;
- source independence;
- numerical conflict resolution;
- Claim Graph;
- falsification/watch indicators;
- persistent PanWatch state;
- production fallback state machine;
- auth redesign;
- source-locator generalization beyond current active HTTP(S) acquisition paths;
- distributed ID migration;
- Postgres migration.
