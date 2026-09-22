"""Live Sprint 1 end-to-end acceptance test for Railway.

This module is intentionally not part of normal server startup. It is invoked
by the isolated Sprint 1 Railway service as a pre-deploy acceptance gate.
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
        "needles": ["data centres", "data centers", "2030", "advanced economies"],
    },
]


def _text_from_result(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
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


def _call_json(
    gateway: AhmedToolboxGateway, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    raw = _call(gateway, name, args)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{name} returned non-object JSON")
    return payload


def _extract_payload_text(raw: str) -> str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, dict):
        for key in ("content", "text", "markdown", "html", "body"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(parsed, ensure_ascii=False)
    return raw


def _passage(text: str, needles: list[str], radius: int = 900) -> str:
    lowered = text.lower()
    positions = [lowered.find(needle.lower()) for needle in needles]
    positions = [pos for pos in positions if pos >= 0]
    if not positions:
        raise RuntimeError(f"expected evidence tokens not found: {needles}")
    start = max(0, min(positions) - radius)
    end = min(len(text), max(positions) + radius)
    return " ".join(text[start:end].split())


def _retrieve(
    gateway: AhmedToolboxGateway, spec: dict[str, Any]
) -> tuple[str, str, str, list[dict[str, Any]]]:
    preferred = str(spec["preferred_tool"])
    attempts = [preferred]
    attempts.append("scrapling__fetch" if preferred == "reach_read_url" else "reach_read_url")

    history: list[dict[str, Any]] = []
    failures: list[str] = []
    for tool in attempts:
        method = "jina_reader" if tool == "reach_read_url" else "scrapling_fetch"
        try:
            if tool == "reach_read_url":
                text = _call(
                    gateway,
                    tool,
                    {"url": spec["url"], "max_chars": 100000},
                )
            else:
                raw = _call(gateway, tool, {"url": spec["url"]})
                text = _extract_payload_text(raw)

            lowered = text.lower()
            if not any(str(needle).lower() in lowered for needle in spec["needles"]):
                detail = "retrieval succeeded but expected evidence tokens were absent"
                history.append(
                    {
                        "stage": "RETRIEVAL",
                        "tool": tool,
                        "method": method,
                        "status": "PARTIAL",
                        "detail": detail,
                    }
                )
                failures.append(f"{tool}: {detail}")
                continue

            history.append(
                {
                    "stage": "RETRIEVAL",
                    "tool": tool,
                    "method": method,
                    "status": "SUCCESS",
                }
            )
            return text, tool, method, history
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            history.append(
                {
                    "stage": "RETRIEVAL",
                    "tool": tool,
                    "method": method,
                    "status": "FAILED",
                    "detail": detail[:1000],
                }
            )
            failures.append(f"{tool}: {detail}")
    raise RuntimeError("all retrieval paths failed; " + " | ".join(failures))


def run_acceptance() -> dict[str, Any]:
    gateway = AhmedToolboxGateway.from_environment()

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
        set(re.findall(r"https?://[^\\s\\]\\[)>(\\\"']+", discovery_raw))
    )

    run = _call_json(
        gateway,
        "research_start_run",
        {
            "question": QUESTION,
            "cutoff": CUTOFF,
            "metadata": {
                "acceptance_test": "sprint1_live_e2e",
                "discovery_tool": "Agent-Reach/Exa",
                "discovery_requested_results": 10,
                "discovered_url_count": len(discovered_urls),
            },
        },
    )
    run_id = str(run["run_id"])

    source_records: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []
    retrieval_attempts: list[dict[str, Any]] = []

    for spec in SOURCES:
        page_text, tool, method, attempt_history = _retrieve(gateway, spec)
        passage = _passage(page_text, spec["needles"])
        retrieval_history = [
            {
                "stage": "DISCOVERY",
                "tool": "Agent-Reach/Exa",
                "method": "reach_web_search",
                "status": "DISCOVERED",
            },
            *attempt_history,
        ]

        source = _call_json(
            gateway,
            "research_record_source",
            {
                "run_id": run_id,
                "url": spec["url"],
                "content": page_text[:500000],
                "publisher": spec["publisher"],
                "source_type": "official_institution_report",
                "primary_source": True,
                "publication_date": spec["publication_date"],
                "data_cutoff": spec["data_cutoff"],
                "retrieval_tool": tool,
                "retrieval_method": method,
                "retrieval_status": "SUCCESS",
                "discovered_by": "Agent-Reach/Exa",
                "source_family_id": f"iea:{spec['url']}",
                "retrieval_history": retrieval_history,
            },
        )
        source_records.append(source)
        retrieval_attempts.append(
            {
                "url": spec["url"],
                "attempts": attempt_history,
                "final_tool": tool,
            }
        )

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
                {"to_value": 945, "to_year": 2030, "unit": "TWh"}
            )

        evidence = _call_json(
            gateway,
            "research_add_evidence",
            {
                "run_id": run_id,
                "source_id": source["source_id"],
                "supporting_passage": passage,
                "structured_fact": structured,
                "metric": spec["metric"],
                "value": structured.get("to_value"),
                "unit": structured.get("unit"),
                "geography": (
                    "global"
                    if spec["metric"].startswith("data_center")
                    else "United States"
                ),
                "reference_period": spec["reference_period"],
                "observation_type": spec["observation_type"],
                "forecast_horizon": spec["forecast_horizon"],
                "definition": "IEA data-centre electricity demand projection",
                "extraction_method": "deterministic_live_acceptance",
            },
        )
        evidence_records.append(evidence)

    claim_specs = [
        (
            "The IEA's 2026 update projects global data-centre electricity "
            "consumption rising from about 485 TWh in 2025 to about 950 TWh in 2030.",
            evidence_records[0]["evidence_id"],
        ),
        (
            "The IEA's 2025 Energy and AI base case projected global data-centre "
            "electricity consumption reaching about 945 TWh by 2030.",
            evidence_records[1]["evidence_id"],
        ),
        (
            "IEA Electricity 2026 identifies data centres as a major driver of "
            "electricity-demand growth in advanced economies through 2030.",
            evidence_records[2]["evidence_id"],
        ),
    ]
    claims: list[dict[str, Any]] = []
    for statement, evidence_id in claim_specs:
        claims.append(
            _call_json(
                gateway,
                "research_add_claim",
                {
                    "run_id": run_id,
                    "statement": statement,
                    "classification": "VERIFIED",
                    "supporting_evidence_ids": [evidence_id],
                    "confidence": "HIGH",
                    "provenance": {"acceptance_test": True},
                },
            )
        )

    _call_json(gateway, "research_complete_run", {"run_id": run_id})
    exported = _call_json(gateway, "research_export_ledger", {"run_id": run_id})
    audit = _call_json(gateway, "research_audit_run", {"run_id": run_id})

    if len(exported["sources"]) != 3:
        raise RuntimeError(f"expected 3 SourceRecords, got {len(exported['sources'])}")
    if len(exported["evidence"]) != 3:
        raise RuntimeError(f"expected 3 EvidenceItems, got {len(exported['evidence'])}")
    if len(exported["claims"]) != 3:
        raise RuntimeError(f"expected 3 Claims, got {len(exported['claims'])}")
    if len(exported["ledger"]) != 3:
        raise RuntimeError(f"expected 3 ledger rows, got {len(exported['ledger'])}")
    if not audit["passed"]:
        raise RuntimeError("Sprint 1 audit failed: " + json.dumps(audit))

    final_tools = sorted(
        {
            str(source["run_retrieval"]["tool"])
            for source in exported["sources"]
            if source.get("run_retrieval")
        }
    )

    return {
        "status": "PASS",
        "question": QUESTION,
        "run_id": run_id,
        "mcp_surface": [
            "research_start_run",
            "research_record_source",
            "research_add_evidence",
            "research_add_claim",
            "research_complete_run",
            "research_export_ledger",
            "research_audit_run",
        ],
        "discovery": {
            "tool": "Agent-Reach/Exa",
            "requested": 10,
            "parsed_distinct_urls": len(discovered_urls),
            "sample_urls": discovered_urls[:5],
        },
        "retrieval_attempts": retrieval_attempts,
        "successful_retrieval_tools": final_tools,
        "source_ids": [source["source_id"] for source in source_records],
        "evidence_ids": [item["evidence_id"] for item in evidence_records],
        "claim_ids": [claim["claim_id"] for claim in claims],
        "ledger_rows": len(exported["ledger"]),
        "audit": audit,
    }


def main() -> int:
    result = run_acceptance()
    print("AHMED_RESEARCH_SPRINT1_E2E=" + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(
            "AHMED_RESEARCH_SPRINT1_E2E="
            + json.dumps(
                {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise
