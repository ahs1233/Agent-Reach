"""Live Sprint 3 acceptance gate.

Reuses the real Sprint 2 acquisition/provenance path, then verifies that the
same EvidenceItem can be evaluated under different explicit freshness policies
without substituting publication date for the underlying data cutoff.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.sprint2_live_acceptance import (
    run_acceptance as run_sprint2,
)

AS_OF = "2026-09-22T12:00:00Z"


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
    value = _text(result)
    if not value:
        raise RuntimeError(f"{name} returned no text")
    return json.loads(value)


def run_acceptance() -> dict[str, Any]:
    sprint2 = run_sprint2()
    if sprint2.get("status") != "PASS":
        raise RuntimeError("Sprint 2 prerequisite acceptance did not pass")

    gateway = AhmedToolboxGateway.from_environment()
    names = {tool["name"] for tool in gateway.list_tools()}
    required = {
        "research_evaluate_freshness",
        "research_get_freshness",
        "research_get_output",
    }
    missing = sorted(required - names)
    if missing:
        raise RuntimeError(f"missing Sprint 3 MCP tools: {missing}")

    evidence_id = str(sprint2["evidence_id"])

    technology = _call_json(
        gateway,
        "research_evaluate_freshness",
        {
            "evidence_id": evidence_id,
            "policy_name": "technology_state",
            "as_of": AS_OF,
        },
    )
    if technology["basis_field"] != "data_cutoff":
        raise RuntimeError("freshness was not based on data_cutoff")
    if technology["basis_value"] != "2026-04-16":
        raise RuntimeError(
            "unexpected live evidence data cutoff: "
            f"{technology['basis_value']!r}"
        )
    if technology["status"] != "STALE":
        raise RuntimeError(
            "90-day technology-state policy should mark 2026-04-16 evidence "
            f"stale on {AS_OF}; got {technology['status']}"
        )

    custom = _call_json(
        gateway,
        "research_evaluate_freshness",
        {
            "evidence_id": evidence_id,
            "policy_name": "custom_max_age",
            "max_age_seconds": 180 * 24 * 60 * 60,
            "as_of": AS_OF,
        },
    )
    if custom["status"] != "FRESH":
        raise RuntimeError(
            "180-day custom policy should accept the same evidence as fresh; "
            f"got {custom['status']}"
        )

    latest_release = _call_json(
        gateway,
        "research_evaluate_freshness",
        {
            "evidence_id": evidence_id,
            "policy_name": "structural_data",
            "latest_known_cutoff": "2026-04-16",
            "as_of": AS_OF,
        },
    )
    if latest_release["status"] != "FRESH":
        raise RuntimeError(
            "matching latest-release cutoff should be FRESH; "
            f"got {latest_release['status']}"
        )

    latest = _call_json(
        gateway,
        "research_get_freshness",
        {"evidence_id": evidence_id},
    )
    if latest["freshness_id"] != latest_release["freshness_id"]:
        raise RuntimeError("latest freshness lookup did not return newest evaluation")

    answer_output = str(sprint2["outputs"][0]["output_id"])
    resolved = _call_json(
        gateway,
        "research_get_output",
        {"output_id": answer_output},
    )
    freshness = (
        resolved["fragments"][0]["provenance_refs"][0]["evidence"][0][
            "latest_freshness"
        ]
    )
    if not freshness or freshness["freshness_id"] != latest_release["freshness_id"]:
        raise RuntimeError("output provenance did not carry latest freshness evaluation")

    return {
        "status": "PASS",
        "sprint2_prerequisite": "PASS",
        "evidence_id": evidence_id,
        "data_cutoff_basis": technology["basis_value"],
        "technology_state": {
            "status": technology["status"],
            "reason_code": technology["reason_code"],
            "age_min_seconds": technology["age_min_seconds"],
            "age_max_seconds": technology["age_max_seconds"],
        },
        "custom_180d": {
            "status": custom["status"],
            "reason_code": custom["reason_code"],
        },
        "latest_release": {
            "status": latest_release["status"],
            "reason_code": latest_release["reason_code"],
        },
        "same_evidence_policy_sensitive": (
            technology["status"] != custom["status"]
        ),
        "output_provenance_carries_freshness": True,
        "retrieval": sprint2["retrieval"],
    }


def main() -> int:
    result = run_acceptance()
    print(
        "AHMED_RESEARCH_SPRINT3_E2E="
        + json.dumps(result, ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - acceptance reports exact live failure
        print(
            "AHMED_RESEARCH_SPRINT3_E2E="
            + json.dumps(
                {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise
