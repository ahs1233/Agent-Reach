"""Live Sprint 4 acceptance gate for the retrieval fallback state machine."""

from __future__ import annotations

import json
import sys
from typing import Any

from agent_reach.toolbox.gateway import AhmedToolboxGateway

SOURCE_URL = (
    "https://www.iea.org/reports/key-questions-on-energy-and-ai/executive-summary"
)


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
    a = lowered.find("485 twh")
    b = lowered.find("950 twh")
    if a < 0 or b < 0:
        raise RuntimeError("required evidence tokens missing after fallback retrieval")
    start = max(0, min(a, b) - 800)
    end = min(len(text), max(a, b) + 800)
    return " ".join(text[start:end].split())


def run_acceptance() -> dict[str, Any]:
    gateway = AhmedToolboxGateway.from_environment()
    names = {tool["name"] for tool in gateway.list_tools()}
    required = {
        "reach_retrieve_url",
        "research_start_run",
        "research_record_source",
        "research_add_evidence",
        "research_add_claim",
        "research_export_ledger",
    }
    missing = sorted(required - names)
    if missing:
        raise RuntimeError(f"missing Sprint 4 tools: {missing}")

    retrieval = _call_json(
        gateway,
        "reach_retrieve_url",
        {
            "url": SOURCE_URL,
            "required_terms": ["485 TWh", "950 TWh"],
            "require_all_terms": True,
            "min_chars": 500,
            "max_chars": 100000,
        },
    )
    if retrieval["status"] != "SUCCESS":
        raise RuntimeError(f"fallback retrieval did not succeed: {retrieval}")
    if retrieval["final_tool"] not in {
        "reach_read_url",
        "scrapling__fetch",
        "scrapling__stealthy_fetch",
    }:
        raise RuntimeError(f"unexpected final retrieval tool: {retrieval['final_tool']}")

    attempts = retrieval["attempts"]
    if not attempts or attempts[-1]["status"] != "SUCCESS":
        raise RuntimeError("fallback attempts do not end in SUCCESS")
    if len(attempts) > 4:
        raise RuntimeError("fallback exceeded bounded attempt count")

    history = retrieval["retrieval_history"]
    if len(history) != len(attempts):
        raise RuntimeError("ledger-compatible retrieval history is incomplete")
    if any(item["stage"] != "RETRIEVAL" for item in history):
        raise RuntimeError("retrieval history stage is not ledger-compatible")

    run = _call_json(
        gateway,
        "research_start_run",
        {
            "question": "Validate production retrieval fallback provenance.",
            "cutoff": "2026-09-22",
            "metadata": {"acceptance_test": "sprint4_live"},
        },
    )
    source = _call_json(
        gateway,
        "research_record_source",
        {
            "run_id": run["run_id"],
            "url": SOURCE_URL,
            "content": retrieval["content"],
            "publisher": "International Energy Agency",
            "source_type": "official_institution_report",
            "primary_source": True,
            "publication_date": "2026-04-16",
            "data_cutoff": "2026-04-16",
            "retrieval_tool": retrieval["final_tool"],
            "retrieval_method": retrieval["retrieval_method"],
            "retrieval_status": "SUCCESS",
            "discovered_by": "Sprint4 live acceptance",
            "retrieval_history": history,
        },
    )
    evidence = _call_json(
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
            "extraction_method": "sprint4_live_acceptance",
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
            "supporting_evidence_ids": [evidence["evidence_id"]],
            "confidence": "HIGH",
        },
    )
    exported = _call_json(
        gateway,
        "research_export_ledger",
        {"run_id": run["run_id"]},
    )
    ledger = exported["ledger"]
    if len(ledger) != 1:
        raise RuntimeError(f"unexpected ledger size: {len(ledger)}")
    row = ledger[0]
    if row["retrieval_tool"] != retrieval["final_tool"]:
        raise RuntimeError("final fallback tool was not preserved in Evidence Ledger")
    source_export = exported["sources"][0]
    exported_history = source_export["retrieval_history"]
    if len(exported_history) != len(history):
        raise RuntimeError("retrieval history was not preserved by SourceRecord")

    return {
        "status": "PASS",
        "final_tool": retrieval["final_tool"],
        "retrieval_method": retrieval["retrieval_method"],
        "attempts": attempts,
        "escalation_count": retrieval["escalation_count"],
        "browser_fallback_configured": retrieval[
            "browser_fallback_configured"
        ],
        "run_id": run["run_id"],
        "source_id": source["source_id"],
        "evidence_id": evidence["evidence_id"],
        "claim_id": claim["claim_id"],
        "ledger_retrieval_tool": row["retrieval_tool"],
        "retrieval_history_preserved": True,
    }


def main() -> int:
    result = run_acceptance()
    print(
        "AHMED_RESEARCH_SPRINT4_E2E="
        + json.dumps(result, ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - acceptance reports exact failure
        print(
            "AHMED_RESEARCH_SPRINT4_E2E="
            + json.dumps(
                {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise
