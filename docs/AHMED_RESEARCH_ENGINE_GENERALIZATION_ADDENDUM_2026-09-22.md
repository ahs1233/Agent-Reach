# Ahmed Research Engine — Generalization Addendum

**Date:** 2026-09-22  
**Status:** CONTROLLED ADDENDUM  
**Applies after frozen reference:** `docs/AHMED_RESEARCH_ENGINE_VNEXT_FROZEN_2026-09-22.md`  
**Baseline:** `b0e331abcd3f947fc4eb8d19e818b2f4b4f4f8d9`

This addendum does **not** rewrite the frozen architecture. It corrects one narrowing that became visible after Sprint 1: the architecture must not assume that the engine exists to write reports or that any single domain (including AlSouq) defines its core model.

## 1. Corrected product definition

Ahmed Research Engine is **general-purpose evidence-backed intelligence infrastructure**.

Its job is to preserve the integrity of observations as they move through:

```text
Observe
  ↓
Acquire
  ↓
Verify
  ↓
Structure
  ↓
Link
  ↓
Reuse
  ↓
Compare
  ↓
Reason
  ↓
Update
  ↓
Trigger
```

It is not:
- a report engine;
- a Deep Research clone;
- an AlSouq-specific system;
- an economics/trading/news-specific system.

A report is one consumer/output. AlSouq is one domain/use case.

## 2. Layer boundaries

### Acquisition / Interaction Layer

Examples: Agent-Reach/Exa, Jina, Scrapling, Patchright.

Responsibility:
- discover material;
- retrieve it;
- interact with a site/application when necessary;
- return observations/representations plus retrieval metadata.

It does **not** decide truth.

Escalation stays capability-driven:

```text
Exa / Agent-Reach discovery
→ Jina for simple readable pages
→ Scrapling fetch when Jina is blocked/incomplete
→ Stealthy fetch for harder dynamic/WAF cases
→ Patchright/browser only when interaction/JS/UI state is required
```

### Ahmed Research Engine

Responsibility:
- Source identity/provenance;
- Evidence structure;
- Claim formation;
- semantic integrity;
- auditability;
- reusable machine references.

Core chain:

```text
Source
  ↓
Evidence
  ↓
Claim
  ↓
Consumer
  ↓
Output / State Update / Trigger
```

### PanWatch

PanWatch is not a scraper.

It consumes structured intelligence:
- Claims;
- Evidence;
- Source versions;
- output/state transitions.

It owns:
- persistent world state;
- change detection;
- confidence movement;
- causal activation;
- contradictions;
- scenarios;
- watch conditions;
- alerts/triggers.

Raw pages may be retained for diagnostics or reprocessing, but they are not PanWatch's primary memory model.

## 3. Architectural audit of Sprint 1

### Strong / already general

The current core correctly separates:
- ResearchRun;
- SourceRecord;
- EvidenceItem;
- Claim.

It also already preserves:
- retrieval provenance;
- publication_date vs data_cutoff vs retrieved_at;
- content hashes;
- structured facts;
- observation_type;
- support/contradiction relationships.

These are domain-agnostic.

### Narrowing detected

The frozen pipeline ends in:
`Human Report + Inline Citations`

and section 16 makes a Human Report the first required output for deep research.

That language is valid for a deep-research use case but is **not** the general product boundary.

The corrected general terminal is:

```text
Evidence-backed consumption
  ├── Answer
  ├── Report
  ├── Alert
  ├── Dashboard state
  ├── Dataset
  ├── API result
  ├── Agent input
  ├── Automation decision
  └── PanWatch state update
```

## 4. Source / Observation / Evidence / Claim / Output

These concepts remain distinct.

### Source
Where the observation originated.

### Observation
What the acquisition layer actually saw.

### Evidence
An auditable representation of an observation that can support or contradict a claim.

### Claim
A proposition that can be evaluated, supported, contradicted, revised, or consumed.

### Output
A use of one or more claims by a consumer.

### Decision on a separate Observation entity

Do **not** add a standalone Observation table yet.

Reason:
Sprint 1 already supports a normalized human-readable observation in `supporting_passage` plus machine structure in `structured_fact`. A new entity would add joins and lifecycle complexity without solving a demonstrated coupling problem today.

Revisit only when one raw observation must independently feed multiple EvidenceItems, or when non-text capture/replay requires its own lifecycle.

## 5. Evidence is not limited to prose

Current EvidenceItem can represent:

- text evidence through `supporting_passage`;
- numeric evidence through metric/value/unit/reference_period/geography;
- structured evidence through `structured_fact`;
- browser/UI observations by storing a concise normalized observation in `supporting_passage` and selectors/events/state in `structured_fact`;
- machine/test evidence in the same pattern.

The passage field should be interpreted as **auditable evidence text**, not necessarily a copied paragraph.

A future `evidence_kind` field may become useful, but Sprint 2 does not need it to satisfy current consumers.

## 6. Source locator limitation

Current SourceRecord is HTTP(S)-URL-centric.

That is sufficient for the current acquisition stack and the Sprint 2 cross-domain acceptance tests, including API/web/browser observations.

It is **not** yet a complete abstraction for:
- local machine tests with no URL;
- database rows;
- internal event streams;
- offline artifacts.

Do not fake these as web pages. A future SourceLocator abstraction should support URI-like identities while preserving the existing URL fields for web sources.

This is recorded debt, not a Sprint 2 blocker.

## 7. Content hash semantics

Current `content_hash` hashes the retrieved representation.

That is useful for:
- exact re-fetch deduplication;
- detecting changed retrieved content under the same acquisition basis.

It is **not sufficient by itself** to prove that the upstream source changed when retrieval representation changes, for example:
- Jina Markdown vs Scrapling HTML;
- different extraction boilerplate;
- tool-version changes.

Therefore:

```text
content_hash change
= candidate representation/source change

not automatically
= upstream truth changed
```

PanWatch must not treat a cross-tool hash change alone as a world-state change.

A future source-version refinement may add representation_hash plus normalized/source-native version signals (ETag, Last-Modified, upstream revision IDs, normalized semantic hash). Sprint 2 will not change hash semantics because that is separate from claim consumption integrity.

## 8. ID audit

Current human IDs:
- R-000001
- S-000001
- E-000001
- C-000001

are safe inside one SQLite database and one process-backed persistence boundary.

They are **not** a globally unique distributed-ID strategy.

Do not replace them in Sprint 2. Before multi-database/multi-writer persistence, introduce a globally unique internal identifier while optionally retaining the readable IDs as display IDs.

## 9. Persistence audit

Current `ResearchStore` contains both domain operations and SQLite persistence.

This is acceptable for Sprint 1/2 scale but is not yet a clean storage interface.

The long-term boundary is:

```text
Domain/service contract
  ↓
ResearchRepository interface
  ↓
SQLite now / network database later
```

Do not migrate to Postgres merely for architectural aesthetics. Introduce the interface when another backend or concurrent multi-worker writes become an actual requirement.

## 10. Sprint 2 reframing

Sprint 2 is renamed:

# Claim Consumption & Output Integrity

It includes:
- machine-readable Claim → Consumer → Output linkage;
- machine-readable provenance references from output fragments back to Claims/Evidence/Sources;
- Actual/Estimate/Forecast/Target/Scenario/Company Guidance/Model Output semantic preservation;
- rejection of semantic promotion when a consumer declares a stronger/different observation type;
- generic outputs that do not assume reports.

It does **not** include:
- freshness scoring;
- source quality scoring;
- source independence;
- numeric conflict resolution;
- claim graph;
- falsification/watch indicators;
- persistent PanWatch state;
- production fallback state machine;
- authentication redesign.

## 11. Consumer model

Sprint 2 introduces a generic output boundary:

```text
OutputRecord
  ├── consumer_type
  ├── consumer_id (optional)
  ├── output_type
  ├── payload (optional)
  └── OutputFragment[]
         ├── content / payload
         ├── asserted_observation_type
         └── claim_ids[]
```

Every material fragment must resolve:

```text
OutputFragment
  ↓ derived_from
Claim
  ↓ supported_by / contradicted_by
EvidenceItem
  ↓ extracted_from
SourceRecord
```

No citation rendering format is hard-coded into the core. A UI may later render the machine references as footnotes, links, tooltips, audit panels, or source cards.

## 12. Semantic integrity rule

Claim observation semantics are distinct from epistemic classification.

Examples:
- classification = VERIFIED, observation_type = FORECAST;
- classification = INFERENCE, observation_type = SCENARIO;
- classification = VERIFIED, observation_type = ACTUAL.

An output fragment must declare the semantics under which it consumes its linked claim(s).

Forbidden silent promotions include:
- FORECAST → ACTUAL;
- TARGET → FORECAST or ACTUAL;
- MODEL_OUTPUT → ACTUAL;
- ESTIMATE → ACTUAL;
- SCENARIO → FORECAST/ACTUAL;
- COMPANY_GUIDANCE → ACTUAL.

If multiple linked claims have different observation types, the fragment must explicitly declare a mixed semantic state rather than flatten them.

## 13. Cross-domain acceptance requirement

The design must work without domain-specific core logic for at least:
- macro/economics;
- trading;
- scientific research;
- software QA/browser observation;
- competitive intelligence;
- AlSouq.

If a domain requires a special core schema branch merely to create Source → Evidence → Claim → Output provenance, treat that as architectural coupling.

## 14. General success condition

A consumer should be able to take any output fragment and reconstruct:

```text
Who consumed it?
What type of output was produced?
Which Claim(s) did it use?
What observation semantics did those Claims carry?
Which EvidenceItem(s) supported or contradicted them?
Which SourceRecord(s) produced the evidence?
How and when were those sources retrieved?
```

This is the generalized integrity boundary for Sprint 2 and later PanWatch integration.
