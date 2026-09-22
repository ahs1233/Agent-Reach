# Sprint 2 Final Audit — Claim Consumption & Output Integrity

**Date:** 2026-09-22  
**Branch:** `feat/research-engine-sprint2-citations-guardrail`  
**Base:** `b0e331abcd3f947fc4eb8d19e818b2f4b4f4f8d9`

## A. Architectural Audit

### What was already correct

Sprint 1 had already established a useful domain-independent evidence core:
- ResearchRun
- SourceRecord
- EvidenceItem
- Claim
- retrieval provenance
- date semantics
- structured facts
- observation_type on Evidence
- support / contradiction links

### Narrowing that was corrected

The frozen architecture ended its main pipeline in:

`Human Report + Inline Citations`

That is a valid deep-research consumer but was too narrow as the general product boundary.

The corrected architecture treats reports as one consumer among:
- Answer
- Alert
- Dashboard
- Dataset
- API
- Agent
- Automation
- QA
- future PanWatch state updates

AlSouq is one use case and does not define the core schema.

## B. Corrected General Architecture

```text
Acquisition / Interaction
Agent-Reach / Exa / Jina / Scrapling / Patchright
        ↓
Ahmed Research Engine
Source → Evidence → Claim
        ↓
Claim Consumption & Output Integrity
Consumer → OutputFragment
        ↓
Answer / Alert / Dashboard / API / Agent / Automation / QA / PanWatch
```

Acquisition observes and retrieves.
Ahmed Research Engine owns provenance and evidence integrity.
Consumers use Claims without losing provenance or semantics.
PanWatch remains the future persistent state/change-reasoning layer.

## C. Sprint 2 Contract — Implemented

Implemented:
- Claim-level observation semantics independent of epistemic classification.
- Generic OutputRecord and OutputFragment.
- Machine-readable OutputFragment → Claim → Evidence → Source provenance.
- Exact supporting passage and structured_fact available in output provenance.
- Generic structured-payload-only outputs.
- semantic guardrail for consumer outputs.
- VERIFIED Claim guardrail against relabeling a single clear supporting Evidence semantic type.
- explicit MIXED semantics for fragments consuming Claims with different observation types.
- ResearchRun export includes generic outputs.
- MCP surface:
  - research_create_output
  - research_get_output
  - research_audit_output
- migration backfill for legacy Sprint 1 Claims when supporting Evidence has one unambiguous observation type.

Not implemented in Sprint 2:
- freshness scoring
- source independence scoring
- numerical conflict resolution
- Claim Graph
- falsification/watch conditions
- persistent PanWatch state
- production fallback state machine
- authentication redesign
- Postgres migration
- distributed global ID migration
- generic non-HTTP source locator

## D. Cross-domain Implementation Audit

The same core path is tested with no domain-specific core branches for:
- macro/economics
- trading
- scientific research
- software QA/browser observations
- competitive intelligence
- AlSouq

The core consumer path is:

```text
OutputFragment
  ↓ derived_from
Claim
  ↓ supported_by / contradicted_by
EvidenceItem
  ↓ extracted_from
SourceRecord
```

## E. Semantic Integrity

Observation semantics:
- ACTUAL
- ESTIMATE
- FORECAST
- TARGET
- SCENARIO
- COMPANY_GUIDANCE
- MODEL_OUTPUT
- UNKNOWN
- MIXED (consumer-fragment state only)

Rejected examples include:
- FORECAST → ACTUAL
- TARGET → FORECAST
- TARGET → ACTUAL
- TARGET → COMPANY_GUIDANCE
- ESTIMATE → ACTUAL
- SCENARIO → ACTUAL/FORECAST
- MODEL_OUTPUT → ACTUAL

For VERIFIED Claims with one unambiguous supporting Evidence type, a conflicting Claim observation_type is rejected before the Claim is stored.

Derived analytical Claims may explicitly change semantics only when they are not represented as VERIFIED direct observations; provenance can record the semantic derivation.

## F. Content Hash Audit

Current hash semantics are now explicit:

`content_hash_scope = retrieved_representation`

The existing content_hash is also exposed as `representation_hash`.

Therefore:
- same URL + same retrieved representation hash can be reused;
- a changed hash means the retrieved representation changed;
- it does **not by itself** prove the upstream real-world source changed across retrieval methods.

This avoids incorrectly treating Jina-vs-Scrapling representation differences as definitive world-state changes.

A future source-version layer may add:
- source-native revision/version identifiers;
- ETag / Last-Modified;
- normalized semantic hash;
- representation hash.

No speculative migration was added in Sprint 2.

## G. ID Audit

Readable IDs remain:
- R-xxxxxx
- S-xxxxxx
- E-xxxxxx
- C-xxxxxx
- O-xxxxxx
- F-xxxxxx

They are acceptable within the current single SQLite persistence boundary.

They are not claimed to be a future distributed global-ID strategy.

Before multi-database or multi-writer operation, introduce a globally unique internal identifier while optionally retaining readable IDs as display IDs.

## H. Persistence Audit

ResearchStore still owns SQLite persistence directly.

This is acceptable for the current stage but is not presented as the final storage abstraction.

The future seam is:

```text
Domain / Service
  ↓
ResearchRepository contract
  ↓
SQLite now / network database later
```

No Postgres migration was introduced because there is not yet a demonstrated need that justifies the extra operational complexity.

## I. Source Locator Audit

SourceRecord remains HTTP(S)-URL-centered.

This works for the active public-web/API/browser acquisition path.

It is explicit technical debt for:
- local machine tests with no URL;
- database rows;
- event streams;
- offline artifacts.

No fake web URLs should be invented for those future source classes.

## J. Acceptance Evidence

Automated CI covers:
- Python 3.10
- Python 3.11
- Python 3.12
- Python 3.13
- Windows
- wheel packaging/smoke install
- focused Ahmed ToolBox tests

Live isolated Railway acceptance validates:
- Agent-Reach/Exa discovery
- retrieval fallback from Jina anti-bot failure to Scrapling
- real SourceRecord
- real EvidenceItem
- real Claim
- FORECAST → ACTUAL rejection
- ANSWER output provenance audit
- DASHBOARD_STATE output provenance audit

The live chain resolves:

```text
OutputFragment
↓
Claim
↓
Evidence
↓
Source
```

## K. Remaining Known Risks

1. Production Ahmed Toolbox authentication remains a separate security concern.
2. Isolated acceptance databases under `/tmp` are ephemeral and are not production persistence.
3. HTTP(S)-only source identity is not yet universal.
4. Sequential readable IDs are not distributed identifiers.
5. representation_hash alone is not sufficient for PanWatch world-change detection.

None of those issues are hidden or treated as solved by Sprint 2.

## Final Architecture Result

Sprint 2 is complete only when its current HEAD has:
- full CI green;
- focused Ahmed ToolBox CI green;
- isolated Railway live gate PASS;
- PR mergeable.

Once those are green, the generalized chain is:

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

with every implemented link machine-resolvable and auditable.
