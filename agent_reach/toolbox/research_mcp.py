"""MCP surface for Ahmed Research Engine evidence and integrity layers."""

from __future__ import annotations

from typing import Any

from .research import ResearchStore


def research_tool_specs() -> list[dict[str, Any]]:
    observation_enum = [
        "ACTUAL",
        "ESTIMATE",
        "FORECAST",
        "TARGET",
        "SCENARIO",
        "COMPANY_GUIDANCE",
        "MODEL_OUTPUT",
        "UNKNOWN",
    ]
    classification_enum = [
        "VERIFIED",
        "STRONG_SIGNAL",
        "INFERENCE",
        "SCENARIO",
        "SPECULATION",
    ]
    temporal_bucket_enum = ["LIVE", "RECENT", "HISTORICAL", "STRUCTURAL"]
    return [
        {
            "name": "research_start_run",
            "description": (
                "Start an Ahmed Research Engine run. Use before recording sources, "
                "evidence, or claims so the complete provenance chain has one run_id."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["question"],
                "properties": {
                    "question": {"type": "string", "minLength": 1, "maxLength": 10000},
                    "cutoff": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_record_source",
            "description": (
                "Record one retrieved source version. The content is hashed for "
                "version detection but is not stored as raw page memory. Use the exact "
                "retrieval tool/method and preserve discovery/retrieval history."
            ),
            "inputSchema": {
                "type": "object",
                "required": [
                    "run_id",
                    "url",
                    "content",
                    "retrieval_tool",
                    "retrieval_method",
                ],
                "properties": {
                    "run_id": {"type": "string"},
                    "url": {"type": "string"},
                    "content": {"type": "string", "maxLength": 500000},
                    "retrieval_tool": {"type": "string"},
                    "retrieval_method": {"type": "string"},
                    "retrieval_status": {
                        "type": "string",
                        "enum": [
                            "SUCCESS",
                            "PARTIAL",
                            "BLOCKED",
                            "FAILED",
                            "DISCOVERED",
                            "UNAVAILABLE",
                        ],
                        "default": "SUCCESS",
                    },
                    "publisher": {"type": "string"},
                    "source_type": {"type": "string"},
                    "primary_source": {"type": "boolean"},
                    "publication_date": {"type": "string"},
                    "data_cutoff": {"type": "string"},
                    "observation_time": {"type": "string"},
                    "temporal_bucket": {"type": "string", "enum": temporal_bucket_enum},
                    "canonical_url": {"type": "string"},
                    "discovered_by": {"type": "string"},
                    "source_family_id": {"type": "string"},
                    "metadata": {"type": "object"},
                    "retrieval_history": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["tool", "status"],
                            "properties": {
                                "stage": {"type": "string"},
                                "tool": {"type": "string"},
                                "method": {"type": "string"},
                                "status": {"type": "string"},
                                "occurred_at": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_record_source_relationship",
            "description": (
                "Record an explicit relationship between two sources in the same run. "
                "Use dependency relationships for copied/syndicated/mirrored sources, "
                "or INDEPENDENT_OF only when independence has been established."
            ),
            "inputSchema": {
                "type": "object",
                "required": [
                    "run_id",
                    "source_id",
                    "related_source_id",
                    "relationship_type",
                ],
                "properties": {
                    "run_id": {"type": "string"},
                    "source_id": {"type": "string"},
                    "related_source_id": {"type": "string"},
                    "relationship_type": {
                        "type": "string",
                        "enum": [
                            "DERIVED_FROM",
                            "SYNDICATED_FROM",
                            "MIRRORS",
                            "INDEPENDENT_OF",
                            "CITES",
                            "QUOTES",
                        ],
                    },
                    "basis": {"type": "string", "maxLength": 5000},
                    "metadata": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_add_evidence",
            "description": (
                "Extract an auditable EvidenceItem from a SourceRecord. Store both "
                "the exact supporting passage and a structured_fact object."
            ),
            "inputSchema": {
                "type": "object",
                "required": [
                    "run_id",
                    "source_id",
                    "supporting_passage",
                    "observation_type",
                ],
                "properties": {
                    "run_id": {"type": "string"},
                    "source_id": {"type": "string"},
                    "supporting_passage": {"type": "string", "minLength": 1},
                    "structured_fact": {"type": "object"},
                    "metric": {"type": "string"},
                    "value": {},
                    "unit": {"type": "string"},
                    "geography": {"type": "string"},
                    "reference_period": {"type": "string"},
                    "observation_type": {"type": "string", "enum": observation_enum},
                    "temporal_bucket": {"type": "string", "enum": temporal_bucket_enum},
                    "forecast_horizon": {"type": "string"},
                    "definition": {"type": "string"},
                    "extraction_method": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_add_claim",
            "description": (
                "Create a Claim linked to EvidenceItems. Claims without evidence are "
                "rejected. Sprint 1 stores confidence but does not calculate it."
            ),
            "inputSchema": {
                "type": "object",
                "required": [
                    "run_id",
                    "statement",
                    "classification",
                    "supporting_evidence_ids",
                ],
                "properties": {
                    "run_id": {"type": "string"},
                    "statement": {"type": "string", "minLength": 1},
                    "classification": {"type": "string", "enum": classification_enum},
                    "observation_type": {
                        "type": "string",
                        "enum": observation_enum,
                        "description": (
                            "Semantic status of the claim itself. If omitted, the store "
                            "derives it only when all supporting evidence agrees."
                        ),
                    },
                    "supporting_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "contradicting_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW", "UNASSESSED"],
                        "default": "UNASSESSED",
                    },
                    "confidence_score": {"type": "number"},
                    "freshness_score": {"type": "number"},
                    "verification_count": {"type": "integer", "minimum": 0},
                    "provenance": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_create_output",
            "description": (
                "Create a generic evidence-backed consumer output. This is not report-"
                "specific: use it for answers, alerts, dashboard state, APIs, agents, "
                "automations, QA outputs, or other consumers. Every fragment must link "
                "to Claim IDs and preserve claim observation semantics."
            ),
            "inputSchema": {
                "type": "object",
                "required": [
                    "run_id",
                    "consumer_type",
                    "output_type",
                    "fragments",
                ],
                "properties": {
                    "run_id": {"type": "string"},
                    "consumer_type": {"type": "string", "minLength": 1},
                    "consumer_id": {"type": "string"},
                    "output_type": {"type": "string", "minLength": 1},
                    "payload": {"type": "object"},
                    "metadata": {"type": "object"},
                    "fragments": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["claim_ids"],
                            "properties": {
                                "content": {"type": "string"},
                                "payload": {"type": "object"},
                                "claim_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                },
                                "asserted_observation_type": {
                                    "type": "string",
                                    "enum": observation_enum + ["MIXED"],
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_get_output",
            "description": (
                "Resolve an output back through OutputFragment -> Claim -> Evidence -> "
                "Source machine-readable provenance."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["output_id"],
                "properties": {"output_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "research_audit_output",
            "description": (
                "Audit one consumer output for claim linkage, semantic integrity, "
                "evidence resolution, and source resolution."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["output_id"],
                "properties": {"output_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "research_evaluate_source_independence",
            "description": (
                "Evaluate one Claim's supporting sources conservatively. Different URLs "
                "do not count as independent by themselves. Returns raw source/URL count, "
                "effective lineage count, verified independence pairs, and a strict "
                "confidence-basis source count."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["claim_id"],
                "properties": {"claim_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "research_get_source_independence",
            "description": (
                "Get the latest persisted source-independence evaluation for a Claim."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["claim_id"],
                "properties": {"claim_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "research_evaluate_freshness",
            "description": (
                "Evaluate one EvidenceItem against a metric-specific freshness policy. "
                "Freshness is based on the source data_cutoff, not publication date. "
                "Returns categorical status and age bounds without false precision."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["evidence_id", "policy_name"],
                "properties": {
                    "evidence_id": {"type": "string"},
                    "policy_name": {
                        "type": "string",
                        "enum": [
                            "market_price",
                            "breaking_news",
                            "macro_indicator",
                            "company_guidance",
                            "technology_state",
                            "structural_data",
                            "academic_evidence",
                            "custom_max_age",
                        ],
                    },
                    "as_of": {
                        "type": "string",
                        "description": "ISO date/datetime; defaults to evaluation time.",
                    },
                    "max_age_seconds": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                    },
                    "latest_known_cutoff": {
                        "type": "string",
                        "description": (
                            "Required for LATEST_RELEASE policies to determine whether "
                            "the evidence matches the latest known official release."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "research_get_freshness",
            "description": "Get the latest persisted freshness evaluation for EvidenceItem.",
            "inputSchema": {
                "type": "object",
                "required": ["evidence_id"],
                "properties": {"evidence_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "research_export_ledger",
            "description": (
                "Export one ResearchRun with SourceRecords, EvidenceItems, Claims, "
                "retrieval history, and flattened Evidence Ledger rows."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["run_id"],
                "properties": {"run_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "research_audit_run",
            "description": (
                "Run the Sprint 1 provenance acceptance checks for one ResearchRun."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["run_id"],
                "properties": {"run_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "research_complete_run",
            "description": "Mark a ResearchRun complete without changing its evidence.",
            "inputSchema": {
                "type": "object",
                "required": ["run_id"],
                "properties": {"run_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    ]


def handle_research_tool(
    store: ResearchStore,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    if name == "research_start_run":
        return store.create_run(
            str(arguments.get("question") or ""),
            cutoff=arguments.get("cutoff"),
            metadata=arguments.get("metadata") or {},
        )

    if name == "research_record_source":
        return store.record_source(
            str(arguments.get("run_id") or ""),
            url=str(arguments.get("url") or ""),
            content=str(arguments.get("content") or ""),
            retrieval_tool=str(arguments.get("retrieval_tool") or ""),
            retrieval_method=str(arguments.get("retrieval_method") or ""),
            retrieval_status=str(arguments.get("retrieval_status") or "SUCCESS"),
            publisher=arguments.get("publisher"),
            source_type=arguments.get("source_type"),
            primary_source=arguments.get("primary_source"),
            publication_date=arguments.get("publication_date"),
            data_cutoff=arguments.get("data_cutoff"),
            observation_time=arguments.get("observation_time"),
            temporal_bucket=arguments.get("temporal_bucket"),
            canonical_url=arguments.get("canonical_url"),
            discovered_by=arguments.get("discovered_by"),
            source_family_id=arguments.get("source_family_id"),
            metadata=arguments.get("metadata") or {},
            retrieval_history=arguments.get("retrieval_history"),
        )

    if name == "research_record_source_relationship":
        return store.record_source_relationship(
            str(arguments.get("run_id") or ""),
            source_id=str(arguments.get("source_id") or ""),
            related_source_id=str(arguments.get("related_source_id") or ""),
            relationship_type=str(arguments.get("relationship_type") or ""),
            basis=arguments.get("basis"),
            metadata=arguments.get("metadata") or {},
        )

    if name == "research_add_evidence":
        return store.add_evidence(
            str(arguments.get("run_id") or ""),
            source_id=str(arguments.get("source_id") or ""),
            supporting_passage=str(arguments.get("supporting_passage") or ""),
            observation_type=str(arguments.get("observation_type") or ""),
            structured_fact=arguments.get("structured_fact") or {},
            metric=arguments.get("metric"),
            value=arguments.get("value"),
            unit=arguments.get("unit"),
            geography=arguments.get("geography"),
            reference_period=arguments.get("reference_period"),
            temporal_bucket=arguments.get("temporal_bucket"),
            forecast_horizon=arguments.get("forecast_horizon"),
            definition=arguments.get("definition"),
            extraction_method=arguments.get("extraction_method"),
        )

    if name == "research_add_claim":
        return store.add_claim(
            str(arguments.get("run_id") or ""),
            statement=str(arguments.get("statement") or ""),
            classification=str(arguments.get("classification") or ""),
            observation_type=arguments.get("observation_type"),
            supporting_evidence_ids=list(arguments.get("supporting_evidence_ids") or []),
            contradicting_evidence_ids=list(
                arguments.get("contradicting_evidence_ids") or []
            ),
            confidence=str(arguments.get("confidence") or "UNASSESSED"),
            confidence_score=arguments.get("confidence_score"),
            freshness_score=arguments.get("freshness_score"),
            verification_count=int(arguments.get("verification_count") or 0),
            provenance=arguments.get("provenance") or {},
        )

    if name == "research_create_output":
        return store.create_output(
            str(arguments.get("run_id") or ""),
            consumer_type=str(arguments.get("consumer_type") or ""),
            consumer_id=arguments.get("consumer_id"),
            output_type=str(arguments.get("output_type") or ""),
            payload=arguments.get("payload") or {},
            metadata=arguments.get("metadata") or {},
            fragments=list(arguments.get("fragments") or []),
        )

    if name == "research_get_output":
        return store.get_output(str(arguments.get("output_id") or ""))

    if name == "research_audit_output":
        return store.audit_output(str(arguments.get("output_id") or ""))

    if name == "research_evaluate_source_independence":
        return store.evaluate_claim_source_independence(
            str(arguments.get("claim_id") or "")
        )

    if name == "research_get_source_independence":
        result = store.get_latest_claim_source_independence(
            str(arguments.get("claim_id") or "")
        )
        return result or {
            "claim_id": str(arguments.get("claim_id") or ""),
            "status": "NOT_EVALUATED",
        }

    if name == "research_evaluate_freshness":
        return store.evaluate_evidence_freshness(
            str(arguments.get("evidence_id") or ""),
            policy_name=str(arguments.get("policy_name") or ""),
            as_of=arguments.get("as_of"),
            max_age_seconds=arguments.get("max_age_seconds"),
            latest_known_cutoff=arguments.get("latest_known_cutoff"),
        )

    if name == "research_get_freshness":
        result = store.get_latest_evidence_freshness(
            str(arguments.get("evidence_id") or "")
        )
        return result or {
            "evidence_id": str(arguments.get("evidence_id") or ""),
            "status": "NOT_EVALUATED",
        }

    if name == "research_export_ledger":
        return store.export_run(str(arguments.get("run_id") or ""))

    if name == "research_audit_run":
        return store.audit_run(str(arguments.get("run_id") or ""))

    if name == "research_complete_run":
        return store.complete_run(str(arguments.get("run_id") or ""))

    return None
