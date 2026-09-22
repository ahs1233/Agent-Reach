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


def test_source_independence_tools_are_feature_gated_with_research() -> None:
    gateway = AhmedToolboxGateway(
        agent_reach=object(),
        research_store=ResearchStore(":memory:"),
        research_enabled=True,
    )
    names = {tool["name"] for tool in gateway.list_tools()}

    assert "research_record_source_relationship" in names
    assert "research_evaluate_source_independence" in names
    assert "research_get_source_independence" in names


def test_source_independence_round_trip_through_mcp() -> None:
    gateway = AhmedToolboxGateway(
        agent_reach=object(),
        research_store=ResearchStore(":memory:"),
        research_enabled=True,
    )
    run = _payload(
        gateway.call_tool(
            "research_start_run",
            {"question": "Do duplicate URLs inflate source confidence?"},
        )
    )

    source_ids = []
    evidence_ids = []
    for index in range(3):
        source = _payload(
            gateway.call_tool(
                "research_record_source",
                {
                    "run_id": run["run_id"],
                    "url": f"https://wire.example.com/{index}",
                    "content": f"wire copy {index}",
                    "publisher": "One Wire",
                    "retrieval_tool": "fixture",
                    "retrieval_method": "deterministic",
                },
            )
        )
        source_ids.append(source["source_id"])
        evidence = _payload(
            gateway.call_tool(
                "research_add_evidence",
                {
                    "run_id": run["run_id"],
                    "source_id": source["source_id"],
                    "supporting_passage": f"Support {index}.",
                    "observation_type": "ACTUAL",
                },
            )
        )
        evidence_ids.append(evidence["evidence_id"])

    claim = _payload(
        gateway.call_tool(
            "research_add_claim",
            {
                "run_id": run["run_id"],
                "statement": "The event occurred.",
                "classification": "VERIFIED",
                "observation_type": "ACTUAL",
                "supporting_evidence_ids": evidence_ids,
            },
        )
    )
    evaluation = _payload(
        gateway.call_tool(
            "research_evaluate_source_independence",
            {"claim_id": claim["claim_id"]},
        )
    )
    latest = _payload(
        gateway.call_tool(
            "research_get_source_independence",
            {"claim_id": claim["claim_id"]},
        )
    )

    assert evaluation["supporting_url_count"] == 3
    assert evaluation["effective_lineage_count"] == 1
    assert evaluation["strict_confidence_basis_source_count"] == 1
    assert evaluation["status"] == "SINGLE_LINEAGE"
    assert latest["independence_id"] == evaluation["independence_id"]
    assert len(source_ids) == 3


def test_explicit_independence_relationship_through_mcp() -> None:
    gateway = AhmedToolboxGateway(
        agent_reach=object(),
        research_store=ResearchStore(":memory:"),
        research_enabled=True,
    )
    run = _payload(
        gateway.call_tool(
            "research_start_run",
            {"question": "Can two source lineages be verified independent?"},
        )
    )

    sources = []
    evidence = []
    for publisher in ("Primary A", "Primary B"):
        source = _payload(
            gateway.call_tool(
                "research_record_source",
                {
                    "run_id": run["run_id"],
                    "url": f"https://{publisher[-1].lower()}.example.com/report",
                    "content": f"{publisher} independently collected data",
                    "publisher": publisher,
                    "retrieval_tool": "fixture",
                    "retrieval_method": "deterministic",
                },
            )
        )
        sources.append(source)
        evidence.append(
            _payload(
                gateway.call_tool(
                    "research_add_evidence",
                    {
                        "run_id": run["run_id"],
                        "source_id": source["source_id"],
                        "supporting_passage": f"{publisher} support.",
                        "observation_type": "ACTUAL",
                    },
                )
            )
        )

    relationship = _payload(
        gateway.call_tool(
            "research_record_source_relationship",
            {
                "run_id": run["run_id"],
                "source_id": sources[0]["source_id"],
                "related_source_id": sources[1]["source_id"],
                "relationship_type": "INDEPENDENT_OF",
                "basis": "Separate primary collection methods were established.",
            },
        )
    )
    claim = _payload(
        gateway.call_tool(
            "research_add_claim",
            {
                "run_id": run["run_id"],
                "statement": "Both primary sources observed the event.",
                "classification": "VERIFIED",
                "observation_type": "ACTUAL",
                "supporting_evidence_ids": [
                    evidence[0]["evidence_id"],
                    evidence[1]["evidence_id"],
                ],
            },
        )
    )
    assessment = _payload(
        gateway.call_tool(
            "research_evaluate_source_independence",
            {"claim_id": claim["claim_id"]},
        )
    )

    assert relationship["relationship_type"] == "INDEPENDENT_OF"
    assert assessment["status"] == "VERIFIED_INDEPENDENT_LINEAGES"
    assert assessment["strict_confidence_basis_source_count"] == 2
