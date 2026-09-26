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


def test_freshness_tools_are_exposed_when_research_is_enabled() -> None:
    gateway = AhmedToolboxGateway(
        agent_reach=object(),
        research_store=ResearchStore(":memory:"),
        research_enabled=True,
    )
    names = {tool["name"] for tool in gateway.list_tools()}
    assert "research_evaluate_freshness" in names
    assert "research_get_freshness" in names


def test_freshness_round_trip_through_mcp_surface() -> None:
    gateway = AhmedToolboxGateway(
        agent_reach=object(),
        research_store=ResearchStore(":memory:"),
        research_enabled=True,
    )
    run = _payload(gateway.call_tool("research_start_run", {"question": "Freshness"}))
    source = _payload(
        gateway.call_tool(
            "research_record_source",
            {
                "run_id": run["run_id"],
                "url": "https://example.com/price",
                "content": "price=100",
                "retrieval_tool": "fixture",
                "retrieval_method": "deterministic",
                "retrieval_status": "SUCCESS",
                "publication_date": "2026-09-22",
                "data_cutoff": "2026-09-22T11:30:00Z",
            },
        )
    )
    evidence = _payload(
        gateway.call_tool(
            "research_add_evidence",
            {
                "run_id": run["run_id"],
                "source_id": source["source_id"],
                "supporting_passage": "Observed price is 100.",
                "observation_type": "ACTUAL",
            },
        )
    )
    evaluation = _payload(
        gateway.call_tool(
            "research_evaluate_freshness",
            {
                "evidence_id": evidence["evidence_id"],
                "policy_name": "market_price",
                "as_of": "2026-09-22T12:00:00Z",
            },
        )
    )
    latest = _payload(
        gateway.call_tool(
            "research_get_freshness",
            {"evidence_id": evidence["evidence_id"]},
        )
    )

    assert evaluation["status"] == "FRESH"
    assert latest["freshness_id"] == evaluation["freshness_id"]
    assert latest["basis_field"] == "data_cutoff"
