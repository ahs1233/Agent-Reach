from __future__ import annotations

import json

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.research import ResearchStore


def _payload(result: dict) -> dict:
    assert not result.get("isError"), result
    text = "\n".join(
        str(item.get("text") or "")
        for item in result.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text"
    )
    return json.loads(text)


def test_output_integrity_tools_are_exposed_when_research_is_enabled() -> None:
    gateway = AhmedToolboxGateway(
        agent_reach=object(),
        research_store=ResearchStore(":memory:"),
        research_enabled=True,
    )
    names = {tool["name"] for tool in gateway.list_tools()}

    assert "research_create_output" in names
    assert "research_get_output" in names
    assert "research_audit_output" in names


def test_mcp_surface_rejects_semantic_promotion_and_exports_provenance() -> None:
    gateway = AhmedToolboxGateway(
        agent_reach=object(),
        research_store=ResearchStore(":memory:"),
        research_enabled=True,
    )

    run = _payload(
        gateway.call_tool(
            "research_start_run",
            {"question": "Forecast output integrity"},
        )
    )
    source = _payload(
        gateway.call_tool(
            "research_record_source",
            {
                "run_id": run["run_id"],
                "url": "https://example.com/forecast",
                "content": "Forecast says 950 TWh by 2030.",
                "retrieval_tool": "fixture",
                "retrieval_method": "deterministic",
                "retrieval_status": "SUCCESS",
                "publication_date": "2026-09-22",
                "data_cutoff": "2026-09-22",
            },
        )
    )
    evidence = _payload(
        gateway.call_tool(
            "research_add_evidence",
            {
                "run_id": run["run_id"],
                "source_id": source["source_id"],
                "supporting_passage": "Forecast says 950 TWh by 2030.",
                "observation_type": "FORECAST",
                "structured_fact": {
                    "metric": "electricity",
                    "value": 950,
                    "unit": "TWh",
                    "year": 2030,
                },
            },
        )
    )
    claim = _payload(
        gateway.call_tool(
            "research_add_claim",
            {
                "run_id": run["run_id"],
                "statement": "Electricity demand is forecast to reach 950 TWh by 2030.",
                "classification": "VERIFIED",
                "observation_type": "FORECAST",
                "supporting_evidence_ids": [evidence["evidence_id"]],
            },
        )
    )

    rejected = gateway.call_tool(
        "research_create_output",
        {
            "run_id": run["run_id"],
            "consumer_type": "answer_service",
            "output_type": "ANSWER",
            "fragments": [
                {
                    "content": "Electricity demand is 950 TWh in 2030.",
                    "claim_ids": [claim["claim_id"]],
                    "asserted_observation_type": "ACTUAL",
                }
            ],
        },
    )
    assert rejected.get("isError") is True
    assert "semantic promotion/mismatch" in str(rejected)

    output = _payload(
        gateway.call_tool(
            "research_create_output",
            {
                "run_id": run["run_id"],
                "consumer_type": "answer_service",
                "output_type": "ANSWER",
                "fragments": [
                    {
                        "content": "Electricity demand is forecast to reach 950 TWh by 2030.",
                        "claim_ids": [claim["claim_id"]],
                        "asserted_observation_type": "FORECAST",
                    }
                ],
            },
        )
    )

    resolved = _payload(
        gateway.call_tool(
            "research_get_output",
            {"output_id": output["output_id"]},
        )
    )
    audit = _payload(
        gateway.call_tool(
            "research_audit_output",
            {"output_id": output["output_id"]},
        )
    )

    chain = resolved["fragments"][0]["provenance_refs"][0]
    assert chain["claim_id"] == claim["claim_id"]
    assert chain["evidence"][0]["evidence_id"] == evidence["evidence_id"]
    assert chain["evidence"][0]["source_id"] == source["source_id"]
    assert audit["passed"] is True
