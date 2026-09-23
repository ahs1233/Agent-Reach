"""Dual-temporal intelligence primitives for Ahmed Research Engine.

This module is deliberately domain-agnostic and side-effect free. It separates:
- LIVE / RECENT recency validation,
- HISTORICAL contextual validity,
- STRUCTURAL still-in-force validity,
- current-state contradiction resolution,
- temporal fusion classification,
- final live refresh gating.

It does not replace semantic integrity, source independence, or evidence provenance.
Those remain owned by the existing ResearchStore layers.
"""

from __future__ import annotations

from typing import Any

from .freshness import evaluate_freshness, parse_temporal_interval

TEMPORAL_BUCKETS = {"LIVE", "RECENT", "HISTORICAL", "STRUCTURAL"}

TEMPORAL_VALIDITY_STATUSES = {
    "FRESH",
    "STALE",
    "UNCERTAIN",
    "UNKNOWN",
    "HISTORICALLY_VALID",
    "HISTORICAL_CONTEXT_CHANGED",
    "STRUCTURALLY_VALID",
    "STRUCTURALLY_INVALID",
}

FUSION_CLASSIFICATIONS = {
    "PATTERN_CONTINUATION",
    "PATTERN_DEVIATION",
    "STRUCTURAL_BREAK",
    "TEMPORARY_ANOMALY",
    "UNRESOLVED",
}

AUTHORITY_RANK = {
    "UNKNOWN": 0,
    "SEARCH_INDEX": 1,
    "SECONDARY": 2,
    "CREDIBLE_SECONDARY": 3,
    "PRIMARY": 4,
    "OFFICIAL": 5,
    "OFFICIAL_LIVE": 6,
}

CURRENT_BUCKET_RANK = {
    "HISTORICAL": 0,
    "STRUCTURAL": 1,
    "RECENT": 2,
    "LIVE": 3,
}


def normalize_temporal_bucket(value: str) -> str:
    bucket = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if bucket not in TEMPORAL_BUCKETS:
        raise ValueError(
            "temporal_bucket must be one of: " + ", ".join(sorted(TEMPORAL_BUCKETS))
        )
    return bucket


def normalize_authority(value: str | None) -> str:
    authority = str(value or "UNKNOWN").strip().upper().replace("-", "_").replace(" ", "_")
    if authority not in AUTHORITY_RANK:
        raise ValueError(
            "source_authority must be one of: " + ", ".join(sorted(AUTHORITY_RANK))
        )
    return authority


def _truthy_tristate(value: bool | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _timestamp_score(value: str | None) -> float:
    interval = parse_temporal_interval(value)
    if interval is None:
        return float("-inf")
    return interval.end.timestamp()


def evaluate_temporal_validity(
    *,
    temporal_bucket: str,
    data_cutoff: str | None = None,
    publication_date: str | None = None,
    retrieved_at: str | None = None,
    as_of: str | None = None,
    freshness_policy: str | None = None,
    max_age_seconds: int | float | None = None,
    latest_known_cutoff: str | None = None,
    authority_status: str | None = None,
    provenance_complete: bool = True,
    historiographical_conflict: bool = False,
    later_evidence_changes_interpretation: bool | None = None,
    still_in_force: bool | None = None,
    current_reality_conflict: bool = False,
) -> dict[str, Any]:
    """Evaluate evidence using bucket-specific validity rules.

    LIVE and RECENT use the existing freshness engine.
    HISTORICAL never becomes stale merely because it is old.
    STRUCTURAL validity depends on whether the rule/institution/treaty remains in force.
    """

    bucket = normalize_temporal_bucket(temporal_bucket)
    authority = normalize_authority(authority_status)

    if bucket in {"LIVE", "RECENT"}:
        policy = freshness_policy or (
            "breaking_news" if bucket == "LIVE" else "custom_max_age"
        )
        if policy == "custom_max_age" and max_age_seconds is None:
            max_age_seconds = 14 * 24 * 60 * 60
        freshness = evaluate_freshness(
            data_cutoff=data_cutoff,
            publication_date=publication_date,
            retrieved_at=retrieved_at,
            policy_name=policy,
            as_of=as_of,
            max_age_seconds=max_age_seconds,
            latest_known_cutoff=latest_known_cutoff,
        )
        status = freshness["status"]
        reason = freshness["reason_code"]
        return {
            "temporal_bucket": bucket,
            "validity_status": status,
            "validity_reason": reason,
            "source_authority": authority,
            "freshness": freshness,
            "historical_context_status": None,
            "structural_status": None,
        }

    if bucket == "HISTORICAL":
        if not provenance_complete:
            status = "UNKNOWN"
            reason = "HISTORICAL_PROVENANCE_INCOMPLETE"
        elif authority == "UNKNOWN":
            status = "UNCERTAIN"
            reason = "HISTORICAL_AUTHORITY_UNASSESSED"
        elif authority == "SEARCH_INDEX":
            status = "UNCERTAIN"
            reason = "HISTORICAL_AUTHORITY_TOO_WEAK"
        elif historiographical_conflict:
            status = "UNCERTAIN"
            reason = "HISTORIOGRAPHICAL_CONFLICT"
        elif _truthy_tristate(later_evidence_changes_interpretation) is True:
            status = "HISTORICAL_CONTEXT_CHANGED"
            reason = "LATER_EVIDENCE_MATERIALLY_CHANGES_INTERPRETATION"
        else:
            status = "HISTORICALLY_VALID"
            reason = "PERIOD_APPROPRIATE_EVIDENCE"
        return {
            "temporal_bucket": bucket,
            "validity_status": status,
            "validity_reason": reason,
            "source_authority": authority,
            "freshness": None,
            "historical_context_status": status,
            "structural_status": None,
        }

    if current_reality_conflict:
        status = "STRUCTURALLY_INVALID"
        reason = "CONFLICTS_WITH_CURRENT_REALITY"
    elif still_in_force is True:
        status = "STRUCTURALLY_VALID"
        reason = "STILL_IN_FORCE"
    elif still_in_force is False:
        status = "STRUCTURALLY_INVALID"
        reason = "NO_LONGER_IN_FORCE"
    else:
        status = "UNKNOWN"
        reason = "STILL_IN_FORCE_UNVERIFIED"

    return {
        "temporal_bucket": bucket,
        "validity_status": status,
        "validity_reason": reason,
        "source_authority": authority,
        "freshness": None,
        "historical_context_status": None,
        "structural_status": status,
    }


def _candidate_rank(candidate: dict[str, Any]) -> tuple[int, int, int, float, float]:
    bucket = normalize_temporal_bucket(str(candidate.get("temporal_bucket") or ""))
    authority = normalize_authority(candidate.get("source_authority"))
    freshness_status = str(candidate.get("freshness_status") or "UNKNOWN").upper()
    validity_rank = {
        "FRESH": 4,
        "STRUCTURALLY_VALID": 3,
        "HISTORICALLY_VALID": 2,
        "UNCERTAIN": 1,
        "UNKNOWN": 0,
        "AGE_REPORTED": 0,
        "STALE": -1,
        "STRUCTURALLY_INVALID": -2,
        "HISTORICAL_CONTEXT_CHANGED": -2,
    }.get(freshness_status, 0)
    observed = _timestamp_score(
        str(candidate.get("observation_time") or candidate.get("data_cutoff") or "")
    )
    retrieved = _timestamp_score(str(candidate.get("retrieved_at") or ""))
    return (
        validity_rank,
        AUTHORITY_RANK[authority],
        CURRENT_BUCKET_RANK[bucket],
        observed,
        retrieved,
    )


def resolve_current_state_contradiction(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Choose the best current-state evidence without equating freshness with truth.

    Ranking is conservative and deterministic:
    valid current evidence -> source authority -> temporal bucket -> observation time
    -> retrieval time. Exact ties with conflicting values remain unresolved.
    """

    if not candidates:
        raise ValueError("at least one contradiction candidate is required")

    normalized: list[dict[str, Any]] = []
    for item in candidates:
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            raise ValueError("every contradiction candidate requires evidence_id")
        candidate = dict(item)
        candidate["temporal_bucket"] = normalize_temporal_bucket(
            str(candidate.get("temporal_bucket") or "")
        )
        candidate["source_authority"] = normalize_authority(
            candidate.get("source_authority")
        )
        candidate["_rank"] = _candidate_rank(candidate)
        normalized.append(candidate)

    ordered = sorted(
        normalized,
        key=lambda item: (item["_rank"], item["evidence_id"]),
        reverse=True,
    )
    top = ordered[0]
    top_rank = top["_rank"]
    tied = [item for item in ordered if item["_rank"] == top_rank]
    tied_values = {repr(item.get("claim_value")) for item in tied}

    if len(tied) > 1 and len(tied_values) > 1:
        status = "UNRESOLVED"
        winner_id = None
        reason = "TOP_RANK_TIE_WITH_CONFLICTING_VALUES"
    else:
        status = "RESOLVED"
        winner_id = top["evidence_id"]
        reason = "BEST_VALID_CURRENT_STATE_EVIDENCE"

    rendered = []
    for item in ordered:
        rendered.append(
            {
                key: value
                for key, value in item.items()
                if key != "_rank"
            }
            | {"rank": list(item["_rank"])}
        )

    return {
        "status": status,
        "winner_evidence_id": winner_id,
        "reason": reason,
        "ordered_candidates": rendered,
        "losing_evidence_ids": [
            item["evidence_id"]
            for item in ordered
            if item["evidence_id"] != winner_id
        ],
    }


def temporal_fusion(
    *,
    historical_alignment: str,
    structural_change: str,
    persistence: str,
    changed_now: str,
    unchanged: str,
    structural_constraints: list[str] | None = None,
    contradicting_evidence_ids: list[str] | None = None,
    falsifiers: list[str] | None = None,
) -> dict[str, Any]:
    """Classify how a current event relates to historical and structural context."""

    alignment = str(historical_alignment or "").strip().upper()
    structure = str(structural_change or "").strip().upper()
    persistence_value = str(persistence or "").strip().upper()

    if alignment not in {"CONSISTENT", "DEVIATES", "UNKNOWN"}:
        raise ValueError("historical_alignment must be CONSISTENT, DEVIATES, or UNKNOWN")
    if structure not in {"CONFIRMED", "POSSIBLE", "NONE", "UNKNOWN"}:
        raise ValueError("structural_change must be CONFIRMED, POSSIBLE, NONE, or UNKNOWN")
    if persistence_value not in {"TRANSIENT", "PERSISTENT", "UNKNOWN"}:
        raise ValueError("persistence must be TRANSIENT, PERSISTENT, or UNKNOWN")

    if structure == "CONFIRMED":
        classification = "STRUCTURAL_BREAK"
        reason = "CONFIRMED_STRUCTURAL_CHANGE"
    elif alignment == "CONSISTENT" and structure in {"NONE", "UNKNOWN"}:
        classification = "PATTERN_CONTINUATION"
        reason = "CURRENT_EVENT_FITS_HISTORICAL_PATTERN"
    elif alignment == "DEVIATES" and persistence_value == "TRANSIENT":
        classification = "TEMPORARY_ANOMALY"
        reason = "DEVIATION_WITH_TRANSIENT_EVIDENCE"
    elif alignment == "DEVIATES" and persistence_value == "PERSISTENT":
        classification = "PATTERN_DEVIATION"
        reason = "DEVIATION_WITH_PERSISTENT_EVIDENCE"
    else:
        classification = "UNRESOLVED"
        reason = "INSUFFICIENT_EVIDENCE_FOR_TEMPORAL_CLASSIFICATION"

    return {
        "classification": classification,
        "reason": reason,
        "questions": {
            "what_changed_now": str(changed_now or "").strip(),
            "what_did_not_change": str(unchanged or "").strip(),
            "consistent_with_historical_behavior": alignment,
            "structural_change": structure,
            "persistence": persistence_value,
            "structural_constraints": list(structural_constraints or []),
            "contradicting_evidence_ids": list(contradicting_evidence_ids or []),
            "future_falsifiers": list(falsifiers or []),
        },
    }


def final_live_refresh_gate(
    *,
    temporal_bucket: str,
    source_authority: str,
    data_cutoff: str | None,
    publication_date: str | None,
    refreshed_at: str | None,
    has_final_refresh_event: bool,
    as_of: str | None = None,
    max_age_seconds: int | float = 6 * 60 * 60,
) -> dict[str, Any]:
    """Require an explicit final refresh before a LIVE answer may be finalized."""

    bucket = normalize_temporal_bucket(temporal_bucket)
    authority = normalize_authority(source_authority)
    if bucket != "LIVE":
        return {
            "passed": True,
            "status": "NOT_REQUIRED",
            "reason": "FINAL_LIVE_REFRESH_ONLY_APPLIES_TO_LIVE_BUCKET",
        }
    if not has_final_refresh_event:
        return {
            "passed": False,
            "status": "BLOCKED",
            "reason": "FINAL_LIVE_REFRESH_EVENT_MISSING",
        }
    if authority not in {"OFFICIAL_LIVE", "OFFICIAL", "PRIMARY"}:
        return {
            "passed": False,
            "status": "BLOCKED",
            "reason": "FINAL_LIVE_SOURCE_NOT_AUTHORITATIVE_ENOUGH",
        }

    freshness = evaluate_freshness(
        data_cutoff=data_cutoff,
        publication_date=publication_date,
        retrieved_at=refreshed_at,
        policy_name="custom_max_age",
        as_of=as_of,
        max_age_seconds=max_age_seconds,
    )
    passed = freshness["status"] == "FRESH"
    return {
        "passed": passed,
        "status": "PASSED" if passed else "BLOCKED",
        "reason": (
            "FINAL_LIVE_REFRESH_FRESH"
            if passed
            else f"FINAL_LIVE_REFRESH_{freshness['status']}"
        ),
        "freshness": freshness,
    }


def bounded_temporal_budget(
    *,
    max_total_retrieval_calls: int = 12,
    max_calls_per_lane: int = 3,
    max_fallback_attempts_per_source: int = 5,
) -> dict[str, int]:
    """Validate and expose the default deep-but-bounded research budget."""

    values = {
        "max_total_retrieval_calls": int(max_total_retrieval_calls),
        "max_calls_per_lane": int(max_calls_per_lane),
        "max_fallback_attempts_per_source": int(max_fallback_attempts_per_source),
    }
    if not 4 <= values["max_total_retrieval_calls"] <= 40:
        raise ValueError("max_total_retrieval_calls must be between 4 and 40")
    if not 1 <= values["max_calls_per_lane"] <= 10:
        raise ValueError("max_calls_per_lane must be between 1 and 10")
    if not 1 <= values["max_fallback_attempts_per_source"] <= 8:
        raise ValueError("max_fallback_attempts_per_source must be between 1 and 8")
    return values
