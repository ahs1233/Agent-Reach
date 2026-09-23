"""MCP surface for Ahmed Research Engine Dual-Temporal Intelligence."""

from __future__ import annotations

from typing import Any

from .research import ResearchStore


def temporal_tool_specs() -> list[dict[str, Any]]:
    buckets = ["LIVE", "RECENT", "HISTORICAL", "STRUCTURAL"]
    return [
        {
            "name": "research_evaluate_temporal_validity",
            "description": (
                "Evaluate EvidenceItem validity with bucket-specific rules. LIVE/RECENT "
                "use Freshness; HISTORICAL uses authority/provenance/context; STRUCTURAL "
                "uses still-in-force/current-reality checks."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["evidence_id"],
                "properties": {
                    "evidence_id": {"type": "string"},
                    "temporal_bucket": {"type": "string", "enum": buckets},
                    "as_of": {"type": "string"},
                    "freshness_policy": {"type": "string"},
                    "max_age_seconds": {"type": "number", "exclusiveMinimum": 0},
                    "latest_known_cutoff": {"type": "string"},
                    "authority_status": {"type": "string"},
                    "provenance_complete": {"type": "boolean", "default": True},
                    "historiographical_conflict": {"type": "boolean", "default": False},
                    "later_evidence_changes_interpretation": {"type": "boolean"},
                    "still_in_force": {"type": "boolean"},
                    "current_reality_conflict": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_resolve_temporal_contradiction",
            "description": (
                "Resolve conflicting current-state evidence using persisted temporal "
                "validity, source authority, bucket, observation time, and retrieval time."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["run_id", "candidates"],
                "properties": {
                    "run_id": {"type": "string"},
                    "candidates": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "required": ["evidence_id", "claim_value"],
                            "properties": {
                                "evidence_id": {"type": "string"},
                                "claim_value": {},
                                "temporal_bucket": {"type": "string", "enum": buckets},
                                "source_authority": {"type": "string"},
                                "freshness_status": {"type": "string"},
                                "observation_time": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_temporal_fusion",
            "description": (
                "Persist a bounded Dual-Temporal fusion classification and the internal "
                "changed/unchanged/constraints/contradictions/falsifiers reasoning fields."
            ),
            "inputSchema": {
                "type": "object",
                "required": [
                    "run_id",
                    "evidence_ids",
                    "historical_alignment",
                    "structural_change",
                    "persistence",
                    "changed_now",
                    "unchanged",
                ],
                "properties": {
                    "run_id": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "historical_alignment": {
                        "type": "string",
                        "enum": ["CONSISTENT", "DEVIATES", "UNKNOWN"],
                    },
                    "structural_change": {
                        "type": "string",
                        "enum": ["CONFIRMED", "POSSIBLE", "NONE", "UNKNOWN"],
                    },
                    "persistence": {
                        "type": "string",
                        "enum": ["TRANSIENT", "PERSISTENT", "UNKNOWN"],
                    },
                    "changed_now": {"type": "string"},
                    "unchanged": {"type": "string"},
                    "structural_constraints": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "contradicting_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "falsifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_final_live_refresh_gate",
            "description": (
                "Block final LIVE output unless authoritative evidence has a successful "
                "FINAL_LIVE_REFRESH retrieval event and a fresh current-state cutoff."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["evidence_id"],
                "properties": {
                    "evidence_id": {"type": "string"},
                    "as_of": {"type": "string"},
                    "max_age_seconds": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "default": 21600,
                    },
                    "source_authority": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_temporal_budget",
            "description": (
                "Validate the deep-but-bounded Dual-Temporal retrieval budget without "
                "performing any retrieval calls."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "max_total_retrieval_calls": {
                        "type": "integer",
                        "minimum": 4,
                        "maximum": 40,
                        "default": 12,
                    },
                    "max_calls_per_lane": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 3,
                    },
                    "max_fallback_attempts_per_source": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                        "default": 5,
                    },
                },
                "additionalProperties": False,
            },
        },
    ]


def handle_temporal_tool(
    store: ResearchStore,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    if name == "research_evaluate_temporal_validity":
        return store.evaluate_evidence_temporal_validity(
            str(arguments.get("evidence_id") or ""),
            temporal_bucket=arguments.get("temporal_bucket"),
            as_of=arguments.get("as_of"),
            freshness_policy=arguments.get("freshness_policy"),
            max_age_seconds=arguments.get("max_age_seconds"),
            latest_known_cutoff=arguments.get("latest_known_cutoff"),
            authority_status=arguments.get("authority_status"),
            provenance_complete=bool(arguments.get("provenance_complete", True)),
            historiographical_conflict=bool(
                arguments.get("historiographical_conflict", False)
            ),
            later_evidence_changes_interpretation=arguments.get(
                "later_evidence_changes_interpretation"
            ),
            still_in_force=arguments.get("still_in_force"),
            current_reality_conflict=bool(
                arguments.get("current_reality_conflict", False)
            ),
        )

    if name == "research_resolve_temporal_contradiction":
        return store.resolve_temporal_contradiction(
            str(arguments.get("run_id") or ""),
            list(arguments.get("candidates") or []),
        )

    if name == "research_temporal_fusion":
        return store.create_temporal_fusion(
            str(arguments.get("run_id") or ""),
            evidence_ids=list(arguments.get("evidence_ids") or []),
            historical_alignment=str(arguments.get("historical_alignment") or ""),
            structural_change=str(arguments.get("structural_change") or ""),
            persistence=str(arguments.get("persistence") or ""),
            changed_now=str(arguments.get("changed_now") or ""),
            unchanged=str(arguments.get("unchanged") or ""),
            structural_constraints=list(arguments.get("structural_constraints") or []),
            contradicting_evidence_ids=list(
                arguments.get("contradicting_evidence_ids") or []
            ),
            falsifiers=list(arguments.get("falsifiers") or []),
        )

    if name == "research_final_live_refresh_gate":
        return store.final_live_refresh_gate(
            str(arguments.get("evidence_id") or ""),
            as_of=arguments.get("as_of"),
            max_age_seconds=float(arguments.get("max_age_seconds") or 21600),
            source_authority=arguments.get("source_authority"),
        )

    if name == "research_temporal_budget":
        return store.temporal_budget(
            max_total_retrieval_calls=int(
                arguments.get("max_total_retrieval_calls") or 12
            ),
            max_calls_per_lane=int(arguments.get("max_calls_per_lane") or 3),
            max_fallback_attempts_per_source=int(
                arguments.get("max_fallback_attempts_per_source") or 5
            ),
        )

    return None
