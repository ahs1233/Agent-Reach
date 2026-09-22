"""Live Sprint 5 acceptance gate for source-lineage independence."""

from __future__ import annotations

import json
import sys
from typing import Any

from agent_reach.toolbox.gateway import AhmedToolboxGateway


SOURCE_URL = (
    "https://www.iea.org/reports/key-questions-on-energy-and-ai/executive-summary"
)
TRACKING_URL = SOURCE_URL + "?utm_source=sprint5-lineage-acceptance"


def _text(result: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get("text") or "")
        for item in result.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()


def _call_json(
    gateway: AhmedToolboxGateway,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    result = gateway.call_tool(name, args)
    if result.get("isError"):
        raise RuntimeError(f"{name} failed: {_text(result)[:1200]}")
    raw = _text(result)
    if not raw:
        raise RuntimeError(f"{name} returned no text")
    return json.loads(raw)


def _passage(text: str) -> str:
    lowered = text.lower()
    first = lowered.find("485 twh")
    second = lowered.find("950 twh")
    if first < 0 or second < 0:
        raise RuntimeError("required IEA evidence tokens are missing")
    start = max(0, min(first, second) - 700)
    end = min(len(text), max(first, second) + 700)
    return " ".join(text[start:end].split())


def _retrieve(gateway: AhmedToolboxGateway, url: str) -> dict[str, Any]:
    result = _call_json(
        gateway,
        "reach_retrieve_url",
        {
            "url": url,
            "required_terms": ["485 TWh", "950 TWh"],
            "require_all_terms": True,
            "min_chars": 500,
            "max_chars": 100000,
        },
    )
    if result["status"] != "SUCCESS":
        raise RuntimeError(f"retrieval failed for {url}: {result}")
    return result


def run_acceptance() -> dict[str, Any]:
    gateway = AhmedToolboxGateway.from_environment()
    names = {tool["name"] for tool in gateway.list_tools()}
    required = {
        "reach_retrieve_url",
        "research_start_run",
        "research_record_source",
        "research_record_source_relationship",
        "research_add_evidence",
        "research_add_claim",
        "research_evaluate_source_independence",
        "research_create_output",
        "research_get_output",
        "research_export_ledger",
    }
    missing = sorted(required - names)
    if missing:
        raise RuntimeError(f"missing Sprint 5 tools: {missing}")

    first_retrieval = _retrieve(gateway, SOURCE_URL)
    second_retrieval = _retrieve(gateway, TRACKING_URL)

    run = _call_json(
        gateway,
        "research_start_run",
        {
            "question": (
                "Do two URLs representing the same IEA source count as "
                "independent confirmation?"
            ),
            "cutoff": "2026-09-22",
            "metadata": {"acceptance_test": "sprint5_live"},
        },
    )

    sources = []
    evidence = []
    for url, retrieval, label in (
        (SOURCE_URL, first_retrieval, "canonical"),
        (TRACKING_URL, second_retrieval, "tracking_variant"),
    ):
        source = _call_json(
            gateway,
            "research_record_source",
            {
                "run_id": run["run_id"],
                "url": url,
                "content": retrieval["content"],
                "publisher": "International Energy Agency",
                "source_type": "official_institution_report",
                "primary_source": True,
                "publication_date": "2026-04-16",
                "data_cutoff": "2026-04-16",
                "retrieval_tool": retrieval["final_tool"],
                "retrieval_method": retrieval["retrieval_method"],
                "retrieval_status": "SUCCESS",
                "discovered_by": "Sprint5 live acceptance",
                "retrieval_history": retrieval["retrieval_history"],
                "metadata": {"acceptance_variant": label},
            },
        )
        sources.append(source)
        item = _call_json(
            gateway,
            "research_add_evidence",
            {
                "run_id": run["run_id"],
                "source_id": source["source_id"],
                "supporting_passage": _passage(retrieval["content"]),
                "structured_fact": {
                    "from_value": 485,
                    "from_year": 2025,
                    "to_value": 950,
                    "to_year": 2030,
                    "unit": "TWh",
                },
                "metric": "data_center_electricity_consumption",
                "value": 950,
                "unit": "TWh",
                "geography": "global",
                "reference_period": "2025-2030",
                "observation_type": "FORECAST",
                "forecast_horizon": "2030",
                "extraction_method": "sprint5_live_acceptance",
            },
        )
        evidence.append(item)

    if sources[0]["source_id"] == sources[1]["source_id"]:
        raise RuntimeError(
            "tracking URL unexpectedly deduplicated to one SourceRecord; "
            "acceptance requires two URL records before lineage collapse"
        )

    relationship = _call_json(
        gateway,
        "research_record_source_relationship",
        {
            "run_id": run["run_id"],
            "source_id": sources[1]["source_id"],
            "related_source_id": sources[0]["source_id"],
            "relationship_type": "MIRRORS",
            "basis": (
                "Tracking-parameter URL resolves to the same IEA document "
                "and is not independent reporting."
            ),
        },
    )

    claim = _call_json(
        gateway,
        "research_add_claim",
        {
            "run_id": run["run_id"],
            "statement": (
                "The IEA projects global data-centre electricity consumption "
                "at about 950 TWh in 2030."
            ),
            "classification": "VERIFIED",
            "observation_type": "FORECAST",
            "supporting_evidence_ids": [
                evidence[0]["evidence_id"],
                evidence[1]["evidence_id"],
            ],
            "confidence": "HIGH",
        },
    )

    assessment = _call_json(
        gateway,
        "research_evaluate_source_independence",
        {"claim_id": claim["claim_id"]},
    )
    if assessment["supporting_source_count"] != 2:
        raise RuntimeError("expected two supporting SourceRecords")
    if assessment["supporting_url_count"] != 2:
        raise RuntimeError("expected two supporting URLs before lineage collapse")
    if assessment["effective_lineage_count"] != 1:
        raise RuntimeError(
            "duplicate/mirrored IEA URLs were incorrectly counted as "
            "multiple lineages"
        )
    if assessment["strict_confidence_basis_source_count"] != 1:
        raise RuntimeError("raw URL count leaked into strict confidence basis")
    if assessment["status"] != "SINGLE_LINEAGE":
        raise RuntimeError(
            f"expected SINGLE_LINEAGE, got {assessment['status']}"
        )

    output = _call_json(
        gateway,
        "research_create_output",
        {
            "run_id": run["run_id"],
            "consumer_type": "acceptance",
            "output_type": "ANSWER",
            "fragments": [
                {
                    "content": (
                        "The IEA forecast is supported by one effective "
                        "source lineage despite two URLs."
                    ),
                    "claim_ids": [claim["claim_id"]],
                    "asserted_observation_type": "FORECAST",
                }
            ],
        },
    )
    resolved = _call_json(
        gateway,
        "research_get_output",
        {"output_id": output["output_id"]},
    )
    provenance = resolved["fragments"][0]["provenance_refs"][0]
    snapshot = provenance["source_independence_at_output"]
    if not snapshot or snapshot["independence_id"] != assessment["independence_id"]:
        raise RuntimeError(
            "Output did not preserve source independence at creation"
        )

    exported = _call_json(
        gateway,
        "research_export_ledger",
        {"run_id": run["run_id"]},
    )
    relationships = exported["source_relationships"]
    if len(relationships) != 1:
        raise RuntimeError(
            f"expected one exported relationship, got {len(relationships)}"
        )
    if relationships[0]["relationship_id"] != relationship["relationship_id"]:
        raise RuntimeError("source relationship was not preserved in run export")

    return {
        "status": "PASS",
        "run_id": run["run_id"],
        "claim_id": claim["claim_id"],
        "source_ids": [item["source_id"] for item in sources],
        "supporting_url_count": assessment["supporting_url_count"],
        "effective_lineage_count": assessment["effective_lineage_count"],
        "strict_confidence_basis_source_count": assessment[
            "strict_confidence_basis_source_count"
        ],
        "independence_status": assessment["status"],
        "relationship_type": relationship["relationship_type"],
        "output_snapshot_preserved": True,
        "retrieval_tools": [
            first_retrieval["final_tool"],
            second_retrieval["final_tool"],
        ],
    }


def main() -> int:
    result = run_acceptance()
    print(
        "AHMED_RESEARCH_SPRINT5_E2E="
        + json.dumps(result, ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - acceptance reports exact live failure
        print(
            "AHMED_RESEARCH_SPRINT5_E2E="
            + json.dumps(
                {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise
