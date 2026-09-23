# Dual-Temporal Intelligence

## Goal

Ahmed Research Engine must combine current state with context without treating
"newest" as complete or "old" as invalid.

```text
LIVE + RECENT + HISTORICAL + STRUCTURAL
-> bucket-specific validity
-> contradiction resolution
-> temporal fusion
-> existing semantic / independence / provenance guards
-> evidence-backed output
```

The feature is domain-agnostic: markets, gold, economics, companies,
technology, AI, science, security, business intelligence, AlSouq, PanWatch,
and other research tasks.

## Reused architecture

Dual-Temporal Intelligence does not replace existing components. It reuses:

- ResearchRun, SourceRecord, EvidenceItem, Claim
- Evidence Ledger and Source Provenance
- Freshness Engine
- Source Independence
- ACTUAL / ESTIMATE / FORECAST / TARGET / SCENARIO / MODEL_OUTPUT semantics
- output audit and retrieval provenance

Native additions are temporal metadata, temporal validity records,
cross-bucket contradiction resolution, temporal fusion records, and a final
LIVE refresh gate.

## Buckets and validity

### LIVE
Minutes/hours/current-session state. Validity is based on data_cutoff and
observation time. Publication date alone does not establish freshness.

### RECENT
Days/weeks/current phase. Uses the existing Freshness Engine with a wider,
explicitly bounded window.

### HISTORICAL
Past episodes and precedent. Old evidence is not stale merely because of age.
Assess authority, provenance, completeness, historiographical conflict, and
whether later evidence materially changed the interpretation.

### STRUCTURAL
Long-lived laws, treaties, institutions, alliances, geography, economic
structure, and durable market mechanics. Assess whether the structure is still
in force and still describes current reality.

SourceRecord can persist `temporal_bucket` and `observation_time`.
EvidenceItem can persist/inherit `temporal_bucket`. Assessments are stored in
`temporal_evaluations`; fusions are stored in `temporal_fusions` and
included in run exports.

## Contradiction resolution

Current-state conflicts are ranked conservatively by:

1. validity
2. source authority
3. temporal relevance
4. observation/data-cutoff time
5. retrieval time

Authority is ordered from search indexes and secondary sources through primary,
official, and official-live sources. Exact top-rank conflicting ties remain
`UNRESOLVED`.

This prevents an older indexed state from defeating a newer official live
state.

## Temporal Fusion

Supported classifications:

- PATTERN_CONTINUATION
- PATTERN_DEVIATION
- STRUCTURAL_BREAK
- TEMPORARY_ANOMALY
- UNRESOLVED

Fusion stores the internal answers to:

1. What changed now?
2. What did not change?
3. Does the event fit historical behavior?
4. Is there a structural break?
5. Which structural constraints remain?
6. Which evidence contradicts the interpretation?
7. Which future evidence would falsify/update it?

A new contact or event does not by itself prove a durable structural change.

## Final Live Refresh Gate

Before a LIVE answer is finalized, evidence must contain a successful
`FINAL_LIVE_REFRESH` retrieval event, use an authoritative source, and have a
fresh data cutoff. Non-LIVE evidence does not require this gate.

## Retrieval fallback

The bounded path is:

```text
original URL
-> reach_read_url / Jina
-> Scrapling fetch
-> Scrapling stealthy
-> optional configured read-only browser adapter
-> targeted search discovery
```

403, anti-bot blocks, 429/rate limits, exceptions/timeouts, empty content, and
partial extraction are recorded rather than aborting the whole research run.

If the original source remains unavailable but search finds alternatives, the
result is `ALTERNATIVE_DISCOVERED`, not SUCCESS. Alternative search output is
never impersonated as content from the blocked URL; the alternative must be
retrieved and recorded as its own SourceRecord.

Browser fallback remains allowlisted/configured instead of exposing an
unrestricted arbitrary browser, preserving the existing trust boundary.

## Semantic and independence guardrails

Temporal reasoning is orthogonal to observation semantics. ACTUAL evidence
cannot be promoted to FORECAST or arbitrary MIXED output by Temporal Fusion.

Source Independence remains authoritative: two URLs are not automatically two
independent sources; mirrors, syndication, derived sources, source families,
and explicit independence relationships still control lineage.

## Performance

Default budget:

- total retrieval calls: 12
- calls per temporal lane: 3
- fallback attempts per source: 5

Temporal validity and fusion perform no network calls. LIVE/RECENT/HISTORICAL/
STRUCTURAL lanes may run in parallel through the Runtime DAG, reusing cached and
deduplicated sources. Nested `runtime_` and `orchestration_` control-plane
execution remains forbidden.

## Integration examples

For gold: LIVE can hold price/order flow/current news; RECENT the last sessions;
HISTORICAL comparable regimes; STRUCTURAL real yields, USD, central-bank demand,
and market structure.

PanWatch can consume exported temporal metadata and fusion results as research
state inputs. Persistent monitoring stays in PanWatch; evidence acquisition,
validation, provenance, and temporal interpretation stay in Ahmed Research
Engine.

## Acceptance invariants

The feature is accepted only when regression tests show that newer official
live evidence wins stale indexed state, same-day evidence can be stale, old
historical evidence can remain valid, structural validity checks current force,
403/429 failures fail soft, semantic and independence guards remain intact,
LIVE output requires final refresh, and call counts remain bounded.
