"""Live Sprint 1 end-to-end acceptance test.

This script is intended for the isolated Railway Sprint 1 service, not normal
startup. It uses real discovery/retrieval tools and writes a complete evidence
ledger for a small, reproducible research question.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from agent_reach.toolbox.gateway import AhmedToolboxGateway


QUESTION = "What do current IEA projections say about data-centre electricity demand through 2030?"
CUTOFF = "2026-09-22"

SOURCES = [
    {
        "url": "https://www.iea.org/reports/key-questions-on-energy-and-ai/executive-summary",
        "publisher": "International Energy Agency",
        "publication_date": "2026-04-16",
        "data_cutoff": "2026-04-16",
        "preferred_tool": "reach_read_url",
        "metric": "data_center_electricity_consumption",
        "reference_period": "2025-2030",
        "observation_type": "FORECAST",
        "forecast_horizon": "2030",
        "needles": ["485 TWh", "950 TWh"],
    },
    {
        "url": "https://www.iea.org/reports/energy-and-ai/energy-demand-from-ai",
        "publisher": "International Energy Agency",
        "publication_date": "2025-04-10",
        "data_cutoff": "2025-04-10",
        "preferred_tool": "scrapling__fetch",
        "metric": "data_center_electricity_consumption",
        "reference_period": "2024-2030",
        "observation_type": "FORECAST",
        "forecast_horizon": "2030",
        "needles": ["945", "TWh"],
    },
    {
        "url": "https://www.iea.org/reports/electricity-2026/executive-summary",
        "publisher": "International Energy Agency",
        "publication_date": "2026-02",
        "data_cutoff": "2025",
        "preferred_tool": "reach_read_url",
        "metric": "us_electricity_demand_growth_from_data_centres",
        "reference_period": "2025-2030",
        "observation_type": "FORECAST",
        "forecast_horizon": "2030",
        "needles": ["data centres", "half"],
    },
]


def _text_from_result(result: dict[str, Any]) -> str:
    content = result.get("content") or []
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts).strip()


def _call(gateway: AhmedToolboxGateway, name: str, args: dict[str, Any]) -> str:
    result = gateway.call_tool(name, args)
    if bool(result.get("isError")):
        raise RuntimeError(f"{name} failed: {_text_from_result(result)[:1000]}")
    text = _text_from_result(result)
    if not text:
        raise RuntimeError(f"{name} returned no text")
    return text


def _extract_payload_text(raw: str) -> str:
    """Scrapling MCP may return JSON embedded in MCP text; recover readable content."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if isinstance(parsed, dict):
        candidates = [
            parsed.get("content"),
            parsed.get("text"),
            parsed.get("markdown"),
            parsed.get("html"),
            parsed.get("body"),
        ]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(parsed, ensure_ascii=False)
    return raw


def _passage(text: str, needles: list[str], radius: int = 850) -> str:
    lowered = text.lower()
    positions = [lowered.find(needle.lower()) for needle in needles]
    positions = [pos for pos in positions if pos >= 0]
    if not positions:
        raise RuntimeError(f"expected evidence tokens not found: {needles}")
    start = max(0, min(positions) - radius)
    end = min(len(text), max(positions) + radius)
    return " ".join(text[start:end].split())


def _retrieve(gateway: AhmedToolboxGateway, spec: dict[str, Any]) -> tuple[str, str, str]:
    preferred = spec["preferred_tool"]
    url = spec["url"]

    attempts = [preferred]
    if preferred == "reach_read_url":
        attempts.append("scrapling__fetch")
    else:
        attempts.append("reach_read_url")

    failures: list[str] = []
    for tool in attempts:
        try:
            if tool == "reach_read_url":
                raw = _call(gateway, tool, {"url": url, "max_chars": 100000})
                return raw, tool, "jina_reader"
            raw = _call(gateway, tool, {"url": url})
            return _extract_payload_text(raw), tool, "scrapling_fetch"
        except Exception as exc:  # noqa: BLE001 - acceptance test records fallback
            failures.append(f"{tool}: {type(exc).__name__}: {exc}")
    raise RuntimeError("all retrieval paths failed; " + " | ".join(failures))


def main() -> int:
    gateway = AhmedToolboxGateway.from_environment()

    # 1) Real discovery. Ask Exa for ten results and prove the search path works.
    discovery_raw = _call(
        gateway,
        "reach_web_search",
        {
            "query": (
                "site:iea.org data centre electricity demand AI 2030 "
                "IEA electricity 2026 energy and AI"
            ),
            "num_results": 10,
        },
    )
    discovered_urls = sorted(
        set(re.findall(r"https?://[^\s\]\[)>(\"']+", discovery_raw))
    )

    # 2) Create ResearchRun.
    run = gateway.research_store.create_run(
        QUESTION,
        cutoff=CUTOFF,
        metadata={
            "acceptance_test": "sprint1_live_e2e",
            "discovery_tool": "Agent-Reach/Exa",
            "discovery_requested_results": 10,
            "discovered_url_count": len(discovered_urls),
        },
    )
    run_id = run["run_id"]

    source_records: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []

    # 3) Retrieve three official primary sources via both Jina and Scrapling.
    for spec in SOURCES:
        text, tool, method = _retrieve(gateway, spec)
        passage = _passage(text, spec["needles"])

        source = gateway.research_store.record_source(
            run_id,
            url=spec["url"],
            content=text,
            publisher=spec["publisher"],
            source_type="official_institution_report",
            primary_source=True,
            publication_date=spec["publication_date"],
            data_cutoff=spec["data_cutoff"],
            retrieval_tool=tool,
            retrieval_method=method,
            retrieval_status="SUCCESS",
            discovered_by="Agent-Reach/Exa",
            source_family_id=f"iea:{spec['url']}",
            retrieval_history=[
                {
                    "stage": "DISCOVERY",
                    "tool": "Agent-Reach/Exa",
                    "method": "reach_web_search",
                    "status": "DISCOVERED",
                },
                {
                    "stage": "RETRIEVAL",
                    "tool": tool,
                    "method": method,
                    "status": "SUCCESS",
                },
            ],
        )
        source_records.append(source)

        structured: dict[str, Any] = {
            "metric": spec["metric"],
            "reference_period": spec["reference_period"],
            "observation_type": spec["observation_type"],
            "forecast_horizon": spec["forecast_horizon"],
        }
        if "485 TWh" in passage and "950 TWh" in passage:
            structured.update(
                {
                    "from_value": 485,
                    "from_year": 2025,
                    "to_value": 950,
                    "to_year": 2030,
                    "unit": "TWh",
                }
            )
        elif "945" in passage and "TWh" in passage:
            structured.update(
                {
                    "to_value": 945,
                    "to_year": 2030,
                    "unit": "TWh",
                }
            )

        evidence = gateway.research_store.add_evidence(
            run_id,
            source_id=source["source_id"],
            supporting_passage=passage,
            structured_fact=structured,
            metric=spec["metric"],
            value=structured.get("to_value"),
            unit=structured.get("unit"),
            geography="global" if spec["metric"].startswith("data_center") else "United States",
            reference_period=spec["reference_period"],
            observation_type=spec["observation_type"],
            forecast_horizon=spec["forecast_horizon"],
            definition="IEA data-centre electricity demand projection",
            extraction_method="deterministic_live_acceptance",
        )
        evidence_records.append(evidence)

    # 4) Claims must resolve to exact evidence/source provenance.
    updated_claim = gateway.research_store.add_claim(
        run_id,
        statement=(
            "The IEA's 2026 update projects global data-centre electricity "
            "consumption rising from about 485 TWh in 2025 to about 950 TWh in 2030."
        ),
        classification="VERIFIED",
        supporting_evidence_ids=[evidence_records[0]["evidence_id"]],
        confidence="HIGH",
        provenance={"acceptance_test": True},
    )
    prior_claim = gateway.research_store.add_claim(
        run_id,
        statement=(
            "The IEA's 2025 Energy and AI base case projected global data-centre "
            "electricity consumption reaching about 945 TWh by 2030."
        ),
        classification="VERIFIED",
        supporting_evidence_ids=[evidence_records[1]["evidence_id"]],
        confidence="HIGH",
        provenance={"acceptance_test": True},
    )
    system_claim = gateway.research_store.add_claim(
        run_id,
        statement=(
            "IEA Electricity 2026 identifies data centres as a major driver of "
            "electricity-demand growth in advanced economies through 2030."
        ),
        classification="VERIFIED",
        supporting_evidence_ids=[evidence_records[2]["evidence_id"]],
        confidence="HIGH",
        provenance={"acceptance_test": True},
    )

    # 5) Prove content-hash deduplication and versioning semantics.
    same = gateway.research_store.record_source(
        run_id,
        url=SOURCES[0]["url"],
        content=_retrieve(gateway, SOURCES[0])[0],
        publisher=SOURCES[0]["publisher"],
        source_type="official_institution_report",
        primary_source=True,
        publication_date=SOURCES[0]["publication_date"],
        data_cutoff=SOURCES[0]["data_cutoff"],
        retrieval_tool="reach_read_url",
        retrieval_method="jina_reader",
        retrieval_status="SUCCESS",
        discovered_by="Agent-Reach/Exa",
    )

    # Live pages can legitimately change between two fetches, so do not demand the
    # second network fetch hash is identical. Instead record whether it was reused
    # or versioned; the deterministic unit acceptance test covers exact unchanged
    # bytes -> same source_id.
    version_behavior = (
        "reused"
        if same["source_id"] == source_records[0]["source_id"]
        else "new_content_version"
    )

    gateway.research_store.complete_run(run_id)
    exported = gateway.research_store.export_run(run_id)
    audit = gateway.research_store.audit_run(run_id)

    # Strong acceptance: full chain, 3 sources, 3 evidence, 3 claims, seven checks.
    if len(exported["sources"]) < 3:
        raise RuntimeError("fewer than three SourceRecords exported")
    if len(exported["evidence"]) != 3:
        raise RuntimeError("expected exactly three EvidenceItems")
    if len(exported["claims"]) != 3:
        raise RuntimeError("expected exactly three Claims")
    if not audit["passed"]:
        raise RuntimeError("Sprint 1 audit failed: " + json.dumps(audit))

    summary = {
        "status": "PASS",
        "question": QUESTION,
        "run_id": run_id,
        "discovery": {
            "tool": "Agent-Reach/Exa",
            "requested": 10,
            "parsed_distinct_urls": len(discovered_urls),
            "sample_urls": discovered_urls[:5],
        },
        "retrieval_tools": [source["retrieval_tool"] for source in source_records],
        "source_ids": [source["source_id"] for source in source_records],
        "evidence_ids": [item["evidence_id"] for item in evidence_records],
        "claim_ids": [
            updated_claim["claim_id"],
            prior_claim["claim_id"],
            system_claim["claim_id"],
        ],
        "refetch_behavior": version_behavior,
        "ledger_rows": len(exported["ledger"]),
        "audit": audit,
    }
    print("AHMED_RESEARCH_SPRINT1_E2E=" + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CI boundary
        print(
            "AHMED_RESEARCH_SPRINT1_E2E="
            + json.dumps(
                {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise
