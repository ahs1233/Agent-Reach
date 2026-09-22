"""Live Sprint 2 acceptance gate.

Validates the generalized Claim -> Consumer -> Output integrity path against a
real public source while keeping report formatting out of the core contract.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from agent_reach.toolbox.gateway import AhmedToolboxGateway


SOURCE_URL = (
    "https://www.iea.org/reports/key-questions-on-energy-and-ai/executive-summary"
)
QUESTION = "What does the IEA project for global data-centre electricity use by 2030?"


def _text(result: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get("text") or "")
        for item in result.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()


def _call(
    gateway: AhmedToolboxGateway,
    name: str,
    args: dict[str, Any],
) -> str:
    result = gateway.call_tool(name, args)
    if result.get("isError"):
        raise RuntimeError(f"{name} failed: {_text(result)[:1200]}")
    value = _text(result)
    if not value:
        raise RuntimeError(f"{name} returned no text")
    return value


def _call_json(
    gateway: AhmedToolboxGateway,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    return json.loads(_call(gateway, name, args))


def _extract_remote_text(raw: str) -> str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, dict):
        for key in ("content", "text", "markdown", "html", "body"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return raw


def _retrieve(gateway: AhmedToolboxGateway) -> tuple[str, str, str, list[dict[str, Any]]]:
    attempts = [
        ("reach_read_url", "jina_reader"),
        ("scrapling__fetch", "scrapling_fetch"),
        ("scrapling__stealthy_fetch", "scrapling_stealthy_fetch"),
    ]
    history: list[dict[str, Any]] = []
    failures: list[str] = []

    for tool, method in attempts:
        try:
            if tool == "reach_read_url":
                text = _call(
                    gateway,
                    tool,
                    {"url": SOURCE_URL, "max_chars": 100000},
                )
            else:
                text = _extract_remote_text(
                    _call(gateway, tool, {"url": SOURCE_URL})
                )

            lowered = text.lower()
            if "485 twh" not in lowered or "950 twh" not in lowered:
                history.append(
                    {
                        "stage": "RETRIEVAL",
                        "tool": tool,
                        "method": method,
                        "status": "PARTIAL",
                        "detail": "required forecast evidence was not present",
                    }
                )
                failures.append(f"{tool}: incomplete representation")
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
        except Exception as exc:  # noqa: BLE001 - live gate records fallback
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


def _passage(text: str) -> str:
    lowered = text.lower()
    a = lowered.find("485 twh")
    b = lowered.find("950 twh")
    if a < 0 or b < 0:
        raise RuntimeError("required evidence tokens not found")
    start = max(0, min(a, b) - 1000)
    end = min(len(text), max(a, b) + 1000)
    return " ".join(text[start:end].split())


def run_acceptance() -> dict[str, Any]:
    gateway = AhmedToolboxGateway.from_environment()
    names = {tool["name"] for tool in gateway.list_tools()}
    required = {
        "reach_web_search",
        "research_start_run",
        "research_record_source",
        "research_add_evidence",
        "research_add_claim",
        "research_create_output",
        "research_get_output",
        "research_audit_output",
    }
    missing = sorted(required - names)
    if missing:
        raise RuntimeError(f"missing Sprint 2 MCP tools: {missing}")

    discovery = _call(
        gateway,
        "reach_web_search",
        {
            "query": (
                "site:iea.org key questions energy AI data centres "
                "485 TWh 950 TWh 2030"
            ),
            "num_results": 10,
        },
    )
    urls = sorted(
        {
            match.rstrip(".,;:")
            for match in re.findall(
                r"""https?://[^\s\]\[)>(\"'<>]+""",
                discovery,
            )
        }
    )
    iea_urls = [url for url in urls if "iea.org/" in url.lower()]
    source_family_hits = [
        url
        for url in iea_urls
        if "/reports/key-questions-on-energy-and-ai" in url.lower()
    ]
    if len(iea_urls) < 3 or not source_family_hits:
        raise RuntimeError(
            "Exa discovery did not expose the selected IEA source family; "
            f"iea_urls={iea_urls[:10]}"
        )

    page_text, tool, method, retrieval_history = _retrieve(gateway)
    passage = _passage(page_text)

    run = _call_json(
        gateway,
        "research_start_run",
        {
            "question": QUESTION,
            "cutoff": "2026-09-22",
            "metadata": {"acceptance_test": "sprint2_live"},
        },
    )
    source = _call_json(
        gateway,
        "research_record_source",
        {
            "run_id": run["run_id"],
            "url": SOURCE_URL,
            "content": page_text,
            "publisher": "International Energy Agency",
            "source_type": "official_institution_report",
            "primary_source": True,
            "publication_date": "2026-04-16",
            "data_cutoff": "2026-04-16",
            "retrieval_tool": tool,
            "retrieval_method": method,
            "retrieval_status": "SUCCESS",
            "discovered_by": "Agent-Reach/Exa",
            "retrieval_history": [
                {
                    "stage": "DISCOVERY",
                    "tool": "Agent-Reach/Exa",
                    "method": "reach_web_search",
                    "status": "DISCOVERED",
                },
                *retrieval_history,
            ],
        },
    )
    evidence = _call_json(
        gateway,
        "research_add_evidence",
        {
            "run_id": run["run_id"],
            "source_id": source["source_id"],
            "supporting_passage": passage,
            "structured_fact": {
                "metric": "data_center_electricity_consumption",
                "from_value": 485,
                "from_year": 2025,
                "to_value": 950,
                "to_year": 2030,
                "unit": "TWh",
                "observation_type": "FORECAST",
            },
            "metric": "data_center_electricity_consumption",
            "value": 950,
            "unit": "TWh",
            "geography": "global",
            "reference_period": "2025-2030",
            "observation_type": "FORECAST",
            "forecast_horizon": "2030",
            "definition": "IEA data-centre electricity demand projection",
            "extraction_method": "deterministic_live_acceptance",
        },
    )
    claim = _call_json(
        gateway,
        "research_add_claim",
        {
            "run_id": run["run_id"],
            "statement": (
                "The IEA projects global data-centre electricity consumption "
                "rising from about 485 TWh in 2025 to about 950 TWh in 2030."
            ),
            "classification": "VERIFIED",
            "observation_type": "FORECAST",
            "supporting_evidence_ids": [evidence["evidence_id"]],
            "confidence": "HIGH",
        },
    )

    rejected = gateway.call_tool(
        "research_create_output",
        {
            "run_id": run["run_id"],
            "consumer_type": "answer_service",
            "output_type": "ANSWER",
            "fragments": [
                {
                    "content": "Global data-centre electricity use is 950 TWh in 2030.",
                    "claim_ids": [claim["claim_id"]],
                    "asserted_observation_type": "ACTUAL",
                }
            ],
        },
    )
    if not rejected.get("isError"):
        raise RuntimeError("FORECAST -> ACTUAL semantic promotion was not rejected")

    answer = _call_json(
        gateway,
        "research_create_output",
        {
            "run_id": run["run_id"],
            "consumer_type": "answer_service",
            "consumer_id": "live-acceptance-answer",
            "output_type": "ANSWER",
            "fragments": [
                {
                    "content": (
                        "The IEA projects global data-centre electricity use "
                        "at about 950 TWh in 2030."
                    ),
                    "claim_ids": [claim["claim_id"]],
                    "asserted_observation_type": "FORECAST",
                }
            ],
        },
    )
    dashboard = _call_json(
        gateway,
        "research_create_output",
        {
            "run_id": run["run_id"],
            "consumer_type": "dashboard",
            "consumer_id": "live-acceptance-dashboard",
            "output_type": "DASHBOARD_STATE",
            "fragments": [
                {
                    "payload": {
                        "metric": "data_center_electricity_consumption",
                        "value": 950,
                        "unit": "TWh",
                        "year": 2030,
                    },
                    "claim_ids": [claim["claim_id"]],
                    "asserted_observation_type": "FORECAST",
                }
            ],
        },
    )

    answer_resolved = _call_json(
        gateway,
        "research_get_output",
        {"output_id": answer["output_id"]},
    )
    answer_audit = _call_json(
        gateway,
        "research_audit_output",
        {"output_id": answer["output_id"]},
    )
    dashboard_audit = _call_json(
        gateway,
        "research_audit_output",
        {"output_id": dashboard["output_id"]},
    )

    if not answer_audit["passed"] or not dashboard_audit["passed"]:
        raise RuntimeError("one or more output provenance audits failed")

    chain = answer_resolved["fragments"][0]["provenance_refs"][0]
    if chain["claim_id"] != claim["claim_id"]:
        raise RuntimeError("output did not resolve to expected claim")
    if chain["evidence"][0]["evidence_id"] != evidence["evidence_id"]:
        raise RuntimeError("claim did not resolve to expected evidence")
    if chain["evidence"][0]["source_id"] != source["source_id"]:
        raise RuntimeError("evidence did not resolve to expected source")

    return {
        "status": "PASS",
        "discovery": {
            "tool": "Agent-Reach/Exa",
            "parseable_urls": len(urls),
            "iea_urls": len(iea_urls),
            "source_family_hits": source_family_hits[:5],
        },
        "retrieval": {
            "final_tool": tool,
            "attempts": retrieval_history,
        },
        "run_id": run["run_id"],
        "source_id": source["source_id"],
        "evidence_id": evidence["evidence_id"],
        "claim_id": claim["claim_id"],
        "semantic_promotion_rejected": True,
        "outputs": [
            {
                "output_id": answer["output_id"],
                "output_type": answer["output_type"],
                "audit_passed": answer_audit["passed"],
            },
            {
                "output_id": dashboard["output_id"],
                "output_type": dashboard["output_type"],
                "audit_passed": dashboard_audit["passed"],
            },
        ],
        "chain": {
            "output_fragment": answer_resolved["fragments"][0]["fragment_id"],
            "claim": chain["claim_id"],
            "evidence": chain["evidence"][0]["evidence_id"],
            "source": chain["evidence"][0]["source_id"],
        },
    }


def main() -> int:
    result = run_acceptance()
    print("AHMED_RESEARCH_SPRINT2_E2E=" + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(
            "AHMED_RESEARCH_SPRINT2_E2E="
            + json.dumps(
                {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise
