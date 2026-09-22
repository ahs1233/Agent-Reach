"""MCP surface for Ahmed Research Engine evidence and output integrity."""

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
            canonical_url=arguments.get("canonical_url"),
            discovered_by=arguments.get("discovered_by"),
            source_family_id=arguments.get("source_family_id"),
            metadata=arguments.get("metadata") or {},
            retrieval_history=arguments.get("retrieval_history"),
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

    if name == "research_export_ledger":
        return store.export_run(str(arguments.get("run_id") or ""))

    if name == "research_audit_run":
        return store.audit_run(str(arguments.get("run_id") or ""))

    if name == "research_complete_run":
        return store.complete_run(str(arguments.get("run_id") or ""))

    return None
