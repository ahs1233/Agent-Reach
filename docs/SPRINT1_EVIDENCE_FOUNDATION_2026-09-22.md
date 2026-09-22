# Sprint 1 — Evidence Foundation

**Branch:** `feat/research-engine-sprint1-evidence-foundation`  
**Base:** `e15e652f50eadb41c4f3f4e7138029835ad72f6a`  
**Scope:** Source Provenance + Evidence Ledger only.

## Contract

Sprint 1 introduces four public research entities:

- `ResearchRun`
- `SourceRecord`
- `EvidenceItem`
- `Claim`

A source is not evidence. Evidence is not a claim.

The required provenance chain is:

```text
Claim
  ↓ supported_by / contradicted_by
EvidenceItem
  ↓ extracted_from
SourceRecord
  ↓ retrieved_by
Retrieval event(s)
  ↓
ResearchRun
```

## Storage decisions

- SQLite is the Sprint 1 persistence backend because it requires no new runtime dependency.
- The path is configurable through `AHMED_RESEARCH_DB_PATH`.
- The schema is designed so the backend can later be moved to a network database without changing the entity contract.
- PanWatch must consume claims/evidence/source versions, not raw web pages.

## Source versioning

A `SourceRecord` represents a specific content version.

```text
same canonical URL + same content_hash
→ reuse existing SourceRecord

same canonical URL + different content_hash
→ create a new SourceRecord version linked to the previous version
```

The raw page body is not the primary memory object. Sprint 1 stores metadata, hashes, evidence passages, and structured facts.

## Evidence model

Each EvidenceItem stores both:

- `supporting_passage`
- `structured_fact` (JSON)

It also stores the fields needed by later sprints:
- metric
- value
- unit
- geography
- reference_period
- observation_type
- forecast_horizon
- definition

## Observation types

- ACTUAL
- ESTIMATE
- FORECAST
- TARGET
- SCENARIO
- COMPANY_GUIDANCE
- MODEL_OUTPUT
- UNKNOWN

Sprint 1 stores the type; Sprint 2 adds stronger Actual/Forecast writing guardrails.

## Claim model

Each Claim:
- has a stable `claim_id`;
- belongs to exactly one ResearchRun;
- must have at least one supporting or contradicting EvidenceItem;
- stores classification and confidence fields without computing scoring yet.

Classification values:
- VERIFIED
- STRONG_SIGNAL
- INFERENCE
- SCENARIO
- SPECULATION

## Retrieval provenance

Retrieval history is stored as events so a source may preserve:

```text
DISCOVERY: Exa
RETRIEVAL: Jina
RETRIEVAL: Scrapling
...
```

This enables later fallback-state and retrieval-quality analysis without changing SourceRecord.

## Acceptance tests

1. No Claim can be created without evidence.
2. No EvidenceItem can exist without SourceRecord.
3. Every SourceRecord records retrieval tool and retrieval timestamp.
4. publication_date and data_cutoff are separate fields.
5. observation_type is mandatory on EvidenceItem.
6. Re-fetching unchanged content reuses the same SourceRecord; changed content creates a new version.
7. A complete Evidence Ledger can be exported from one ResearchRun.

## Explicitly out of scope

Do not implement in Sprint 1:
- freshness scoring;
- source quality scoring;
- cross-source independence scoring;
- numeric conflict resolution;
- causal graph reasoning;
- inline report citation rendering;
- falsification/watch logic;
- PanWatch monitoring.

The schema includes forward-compatible fields for those layers, but their logic belongs to later sprints.
