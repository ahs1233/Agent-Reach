# Ahmed Research Engine — vNext Frozen Architecture

**Date:** 2026-09-22  
**Status:** FROZEN / CONTROLLED REFERENCE  
**Scope:** Ahmed Toolbox + future PanWatch integration

## 1. Product definition

Ahmed Toolbox is no longer defined as a collection of tools.

It is the **evidence acquisition, verification, provenance, retrieval, and traceability layer**.

PanWatch is the **persistent intelligence, monitoring, world-model update, and reasoning layer**.

Target architecture:

```text
User Question
    ↓
Research Planner
    ↓
Source Discovery
    ↓
Source Quality Gate
    ↓
Content Retrieval
    ↓
Evidence Extraction
    ↓
Evidence Ledger
    ↓
Cross-source Verification
    ↓
Causal Reasoning
    ↓
Falsification + Watch Indicators
    ↓
Human Report + Inline Citations
    ↓
Audit Ledger + Machine Research State
```

## 2. Research Planner

For each research task, the planner must:
- identify which claims require fresh data;
- classify required source types;
- generate initial research questions / candidate claims;
- assign freshness policy by metric type;
- avoid using heavy tools unless required.

## 3. Source Discovery

Primary discovery layer:
- Agent-Reach / Exa

Discovery does **not** itself count as verification.

## 4. Source Quality Gate

Before evidence is accepted, record and evaluate:
- source type;
- primary vs secondary;
- publication date;
- data cutoff;
- retrieval time;
- geographic relevance;
- methodological transparency;
- directness to the claim;
- relevant definition / denominator / weighting;
- actual vs estimate vs forecast.

Suggested internal baseline quality weights:
- Primary official data: 1.00
- Official institution report: 0.95
- Peer-reviewed research: 0.90
- Company filing: 0.90
- High-quality specialist source: 0.80
- Major media: 0.70
- Analyst/blog: 0.50
- Social post: 0.30
- Unknown: 0.15

Weights are decision support, not a rigid truth score.

## 5. Retrieval Fallback State Machine

```text
DISCOVER
  ↓
JINA
  ├─ success → EXTRACT
  └─ blocked/incomplete
         ↓
SCRAPLING_FETCH
  ├─ success → EXTRACT
  └─ JS/WAF/incomplete
         ↓
STEALTHY_FETCH
  ├─ success → EXTRACT
  └─ interaction required
         ↓
PATCHRIGHT / BROWSER
  ├─ success → EXTRACT
  └─ fail → SOURCE_UNAVAILABLE
```

Rules:
- escalate only for a concrete retrieval reason;
- retain retrieval status and failure reason;
- never imply a source was read if retrieval failed.

## 6. Evidence Ledger — mandatory

Every material claim gets a stable `claim_id`.

Required fields:
- claim_id
- claim
- claim_type
- source_id
- source_url
- source_type
- primary_source
- publication_date
- data_cutoff
- retrieved_at
- tool_used
- retrieval_status
- direct_quote_or_passage
- metric
- unit
- geography
- observation_type
- forecast_horizon
- definition
- supporting_claims[]
- contradicting_claims[]
- confidence
- freshness_score
- verification_count
- source_family_id / independence group

Example:

```text
C-001
Claim: Data-center electricity demand is rising rapidly
Type: VERIFIED
Source: IEA
Publication date: 2026
Data period: 2025–2030
Tool: Scrapling
Evidence: 485 → 950 TWh
Confidence: High
```

## 7. Date semantics

Always separate:
- publication_date
- data_cutoff
- retrieved_at

Freshness is calculated from the age of the **underlying data**, not merely the page publication date.

Example:

```text
Published: 2026-09-10
Data cutoff: 2026-06-30
Retrieved: 2026-09-22
Freshness age: 84 days
```

## 8. Metric-specific freshness policy

Examples:

```text
market_price       → < 1 hour preferred
breaking_news      → < 6 hours
macro_indicator    → latest official release
company_guidance   → latest earnings/report
technology_state   → < 3 months preferred
structural_data    → latest authoritative dataset
academic_evidence  → recency weighted, not recency dominated
```

Freshness must be context-aware, not globally maximized.

## 9. Source independence graph

Verification must count **independent evidence families**, not URLs.

Example:

```text
IMF report
 ├─ Reuters article
 ├─ CNBC article
 └─ Blog quoting Reuters
```

This counts as one evidence family unless the derivative sources add independent evidence.

Independent institutional sources such as IMF, World Bank, BIS, and WTO can count separately when they genuinely use independent methods or datasets.

## 10. Observation-type guardrail

Every numerical claim must carry one of:

- ACTUAL
- ESTIMATE
- FORECAST
- TARGET
- SCENARIO
- COMPANY_GUIDANCE
- MODEL_OUTPUT

The system must never rewrite a forecast as an actual observation.

## 11. Numerical Consistency Engine

When two authoritative values disagree, do not choose a winner immediately.

Check:
- PPP vs market exchange rates;
- fiscal vs calendar year;
- actual vs forecast;
- different data cutoffs;
- geography;
- nominal vs real;
- stock vs flow;
- methodology / weighting;
- denominator / unit;
- revisions.

Output one of:
- same concept / different methodology;
- same concept / different date;
- genuinely conflicting evidence;
- not directly comparable;
- unresolved.

## 12. Claim classification

Allowed analytical labels:

- VERIFIED
- STRONG SIGNAL
- INFERENCE
- SCENARIO
- SPECULATION

These labels describe epistemic status, not writing tone.

## 13. Confidence model

Confidence must derive from evidence, not model style.

Core dimensions:
- Evidence quality
- Source independence
- Freshness
- Causal distance

Illustrative logic:

```text
direct official observation:
evidence high
independence medium-high
freshness high
causal distance = 1.0
→ HIGH

multi-step labor-market inference:
evidence medium
independence medium
freshness high
causal distance lower
→ MEDIUM
```

Do not expose false precision unless calibrated later.

## 14. Claim Graph

Claims must support causal dependencies.

Example:

```text
C001 AI capability ↑
  ↓
C017 compute demand ↑
  ↓
C023 data-center capacity ↑
  ├─→ C031 electricity demand ↑
  │      ↓
  │   C040 grid congestion ↑
  └─→ C045 HBM demand ↑
```

If a parent claim changes materially, downstream dependent claims must be marked for reassessment.

## 15. Falsification layer

Every important inference/scenario should contain:
- expected observable outcome;
- leading indicator;
- what would weaken confidence;
- what would falsify or force reassessment;
- next review date / watch condition where appropriate.

Example:

```text
Claim: AI infrastructure is becoming power-constrained.
Watch:
- grid connection time
- transformer lead time
- data-center cancellation rate
Falsification:
If data-center capacity grows rapidly while queues and power constraints decline materially through 2027–28, reassess.
```

## 16. Required outputs for every deep research study

### A. Human Report
Readable analysis with inline claim citations.

### B. Evidence Ledger
Auditable table / structured view.

### C. Machine Research State

```json
{
  "study_id": "...",
  "cutoff": "2026-09-22",
  "claims": [],
  "sources": [],
  "causal_edges": [],
  "contradictions": [],
  "open_questions": [],
  "watch_indicators": [],
  "falsification_tests": []
}
```

This machine state is the continuity layer for future updates.

## 17. PanWatch integration contract

```text
Ahmed Research Engine
= acquisition + evidence infrastructure

PanWatch
= persistent intelligence + monitoring + reasoning
```

Ahmed Research Engine answers:
- What is the evidence?
- How fresh is it?
- How independent is it?
- What exact passage/metric supports the claim?
- What failed during retrieval?
- What conflicts exist?

PanWatch answers:
- What changed?
- Which claim gained/lost confidence?
- Which causal chain is activating?
- Which scenario is becoming more consistent?
- Which previous claim is now invalid?
- Which watch indicator crossed a threshold?

## 18. Development order — frozen

Do not add new libraries until these layers are established unless a critical missing capability blocks implementation.

Order:

1. Source Provenance
2. Evidence Ledger
3. Inline claim citations
4. Actual vs Forecast guardrail
5. Freshness Engine
6. Fallback State Machine
7. Cross-source / independence verification
8. Numerical / definition conflict resolver
9. Claim Graph
10. Falsification + Watch Indicators
11. Persistent Research State
12. PanWatch monitoring integration

## 19. Core principle

The system must optimize for:

**auditability + evidence quality + causal usefulness + updateability**

—not for number of tools, number of sources, or apparent certainty.

## 20. Definition of success

Ahmed Research Engine is successful when a reader can select any important claim and reconstruct:

- where it came from;
- when the underlying data was current;
- which tool retrieved it;
- the supporting passage/metric;
- whether it was actual, estimate, or forecast;
- which independent sources confirm or contradict it;
- how confidence was derived;
- what causal claims depend on it;
- what would falsify it;
- when it was last verified.

That is the controlled reference for the next development phase.
