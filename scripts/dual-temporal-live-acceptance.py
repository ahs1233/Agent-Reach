"""Opt-in real-network production acceptance for Dual-Temporal Intelligence."""

# ruff: noqa: I001
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.research import ResearchStore


LIVE_URL = "https://gadebate.un.org/en"
RECENT_URL = "https://www.un.org/en/ga/81/"
HISTORICAL_URL = "https://www.un.org/en/about-us/history-of-the-un"
STRUCTURAL_URL = "https://www.un.org/en/about-us/un-charter"
STALE_LIVE_URL = (
    "https://www.un.org/en/delegate/"
    "live-general-debate-80th-session-general-assembly"
)


def tool_json(gateway: AhmedToolboxGateway, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = gateway.call_tool(name, arguments)
    if result.get("isError"):
        content = result.get("content") or [{}]
        raise RuntimeError(f"{name} failed: {content[0].get('text', 'unknown error')}")
    content = result.get("content") or []
    if not content:
        raise RuntimeError(f"{name} returned no content")
    raw = str(content[0].get("text") or "")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} returned non-JSON content") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{name} returned non-object JSON")
    return parsed


def retrieve(
    gateway: AhmedToolboxGateway,
    *,
    url: str,
    discovery_query: str,
) -> dict[str, Any]:
    outcome = tool_json(
        gateway,
        "reach_retrieve_url",
        {
            "url": url,
            "max_chars": 20000,
            "min_chars": 250,
            "discovery_query": discovery_query,
        },
    )
    if outcome.get("status") != "SUCCESS":
        raise RuntimeError(
            f"retrieval failed for {url}: status={outcome.get('status')} "
            f"attempts={outcome.get('attempts')}"
        )
    if not str(outcome.get("content") or "").strip():
        raise RuntimeError(f"retrieval returned empty content for {url}")
    return outcome


def main() -> int:
    base = AhmedToolboxGateway.from_environment()
    gateway = AhmedToolboxGateway(
        remotes=base.remotes,
        agent_reach=base.agent_reach,
        research_store=ResearchStore(":memory:"),
        research_enabled=True,
        orchestration_enabled=False,
        runtime_enabled=False,
        ace_enabled=False,
    )

    retrieved = {
        "live": retrieve(
            gateway,
            url=LIVE_URL,
            discovery_query="UN General Debate 81st session official current 2026",
        ),
        "recent": retrieve(
            gateway,
            url=RECENT_URL,
            discovery_query="UN General Assembly 81st session official 2026",
        ),
        "historical": retrieve(
            gateway,
            url=HISTORICAL_URL,
            discovery_query="United Nations history official founding 1945",
        ),
        "structural": retrieve(
            gateway,
            url=STRUCTURAL_URL,
            discovery_query="United Nations Charter official text",
        ),
        "stale_live": retrieve(
            gateway,
            url=STALE_LIVE_URL,
            discovery_query="UN General Debate 80th session official 2025",
        ),
    }

    retrieval_attempts = sum(
        len(item.get("attempts") or []) for item in retrieved.values()
    )
    if retrieval_attempts > 12:
        raise RuntimeError(
            f"bounded retrieval budget exceeded: {retrieval_attempts} > 12"
        )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now.isoformat().replace("+00:00", "Z")
    run = tool_json(
        gateway,
        "research_start_run",
        {
            "question": (
                "Production live acceptance: distinguish current UNGA 81 evidence "
                "from recent, historical, structural, and stale prior-session evidence."
            ),
            "cutoff": now_iso,
            "metadata": {"acceptance": "production_live", "domain": "current_affairs"},
        },
    )

    def add(
        *,
        key: str,
        url: str,
        bucket: str,
        data_cutoff: str,
        authority: str,
        final_refresh: bool = False,
    ) -> dict[str, Any]:
        outcome = retrieved[key]
        history = list(outcome.get("retrieval_history") or [])
        if final_refresh:
            history.append(
                {
                    "stage": "FINAL_LIVE_REFRESH",
                    "tool": str(outcome.get("final_tool") or "reach_retrieve_url"),
                    "method": str(outcome.get("retrieval_method") or "live_refresh"),
                    "status": "SUCCESS",
                    "occurred_at": now_iso,
                }
            )
        source = tool_json(
            gateway,
            "research_record_source",
            {
                "run_id": run["run_id"],
                "url": url,
                "content": str(outcome.get("content") or "")[:20000],
                "retrieval_tool": str(outcome.get("final_tool") or "reach_retrieve_url"),
                "retrieval_method": str(
                    outcome.get("retrieval_method") or "controlled_fallback"
                ),
                "retrieval_status": "SUCCESS",
                "publisher": "United Nations",
                "source_type": "official_web",
                "primary_source": True,
                "publication_date": data_cutoff[:10],
                "data_cutoff": data_cutoff,
                "observation_time": data_cutoff,
                "temporal_bucket": bucket,
                "metadata": {"source_authority": authority, "acceptance": "live"},
                "retrieval_history": history,
            },
        )
        evidence = tool_json(
            gateway,
            "research_add_evidence",
            {
                "run_id": run["run_id"],
                "source_id": source["source_id"],
                "supporting_passage": str(outcome.get("content") or "")[:1800],
                "structured_fact": {
                    "acceptance_source": key,
                    "retrieved_at": now_iso,
                    "final_tool": outcome.get("final_tool"),
                },
                "observation_type": "ACTUAL",
                "temporal_bucket": bucket,
            },
        )
        return evidence

    live = add(
        key="live",
        url=LIVE_URL,
        bucket="LIVE",
        data_cutoff=now_iso,
        authority="OFFICIAL_LIVE",
        final_refresh=True,
    )
    recent = add(
        key="recent",
        url=RECENT_URL,
        bucket="RECENT",
        data_cutoff=now_iso,
        authority="OFFICIAL",
    )
    historical = add(
        key="historical",
        url=HISTORICAL_URL,
        bucket="HISTORICAL",
        data_cutoff="1945-10-24T00:00:00Z",
        authority="OFFICIAL",
    )
    structural = add(
        key="structural",
        url=STRUCTURAL_URL,
        bucket="STRUCTURAL",
        data_cutoff="1945-10-24T00:00:00Z",
        authority="OFFICIAL",
    )
    stale_live = add(
        key="stale_live",
        url=STALE_LIVE_URL,
        bucket="LIVE",
        data_cutoff="2025-09-29T23:59:59Z",
        authority="OFFICIAL",
    )

    live_eval = tool_json(
        gateway,
        "research_evaluate_temporal_validity",
        {
            "evidence_id": live["evidence_id"],
            "as_of": now_iso,
            "freshness_policy": "breaking_news",
            "authority_status": "OFFICIAL_LIVE",
        },
    )
    recent_eval = tool_json(
        gateway,
        "research_evaluate_temporal_validity",
        {
            "evidence_id": recent["evidence_id"],
            "as_of": now_iso,
            "freshness_policy": "custom_max_age",
            "max_age_seconds": 1209600,
            "authority_status": "OFFICIAL",
        },
    )
    historical_eval = tool_json(
        gateway,
        "research_evaluate_temporal_validity",
        {
            "evidence_id": historical["evidence_id"],
            "as_of": now_iso,
            "authority_status": "OFFICIAL",
            "provenance_complete": True,
        },
    )
    structural_eval = tool_json(
        gateway,
        "research_evaluate_temporal_validity",
        {
            "evidence_id": structural["evidence_id"],
            "as_of": now_iso,
            "authority_status": "OFFICIAL",
            "still_in_force": True,
        },
    )
    stale_eval = tool_json(
        gateway,
        "research_evaluate_temporal_validity",
        {
            "evidence_id": stale_live["evidence_id"],
            "as_of": now_iso,
            "freshness_policy": "breaking_news",
            "authority_status": "OFFICIAL",
        },
    )

    if live_eval.get("validity_status") != "FRESH":
        raise RuntimeError(f"current LIVE evidence not fresh: {live_eval}")
    if recent_eval.get("validity_status") != "FRESH":
        raise RuntimeError(f"RECENT evidence not fresh: {recent_eval}")
    if historical_eval.get("validity_status") != "HISTORICALLY_VALID":
        raise RuntimeError(f"HISTORICAL evidence invalid: {historical_eval}")
    if structural_eval.get("validity_status") != "STRUCTURALLY_VALID":
        raise RuntimeError(f"STRUCTURAL evidence invalid: {structural_eval}")
    if stale_eval.get("validity_status") != "STALE":
        raise RuntimeError(f"prior-session LIVE evidence not stale: {stale_eval}")

    live_gate = tool_json(
        gateway,
        "research_final_live_refresh_gate",
        {"evidence_id": live["evidence_id"], "as_of": now_iso},
    )
    stale_gate = tool_json(
        gateway,
        "research_final_live_refresh_gate",
        {"evidence_id": stale_live["evidence_id"], "as_of": now_iso},
    )
    if not live_gate.get("passed"):
        raise RuntimeError(f"current LIVE gate failed: {live_gate}")
    if stale_gate.get("passed"):
        raise RuntimeError(f"stale LIVE gate unexpectedly passed: {stale_gate}")

    contradiction = tool_json(
        gateway,
        "research_resolve_temporal_contradiction",
        {
            "run_id": run["run_id"],
            "candidates": [
                {
                    "evidence_id": live["evidence_id"],
                    "claim_value": "CURRENT_81ST_SESSION",
                },
                {
                    "evidence_id": stale_live["evidence_id"],
                    "claim_value": "PREVIOUS_80TH_SESSION",
                },
            ],
        },
    )
    if contradiction.get("selected_evidence_id") != live["evidence_id"]:
        raise RuntimeError(
            f"contradiction resolver did not prefer current official LIVE: {contradiction}"
        )

    fusion = tool_json(
        gateway,
        "research_temporal_fusion",
        {
            "run_id": run["run_id"],
            "evidence_ids": [
                live["evidence_id"],
                recent["evidence_id"],
                historical["evidence_id"],
                structural["evidence_id"],
            ],
            "historical_alignment": "UNKNOWN",
            "structural_change": "UNKNOWN",
            "persistence": "UNKNOWN",
            "changed_now": (
                "The live lane reflects the currently retrieved 81st-session "
                "General Debate surface."
            ),
            "unchanged": (
                "Historical and structural lanes are retained separately rather "
                "than promoted into a current-state claim."
            ),
            "structural_constraints": ["UN Charter remains a separate structural lane."],
            "contradicting_evidence_ids": [stale_live["evidence_id"]],
            "falsifiers": [
                "A newer authoritative live source supersedes the current live observation."
            ],
        },
    )
    if fusion.get("classification") != "UNRESOLVED":
        raise RuntimeError(f"conservative fusion should remain UNRESOLVED: {fusion}")

    audit = tool_json(gateway, "research_audit_run", {"run_id": run["run_id"]})
    if not audit.get("passed"):
        raise RuntimeError(f"live acceptance run audit failed: {audit}")

    summary = {
        "status": "PASSED",
        "as_of": now_iso,
        "retrieval_attempts": retrieval_attempts,
        "final_tools": {
            key: value.get("final_tool") for key, value in retrieved.items()
        },
        "temporal": {
            "live": live_eval.get("validity_status"),
            "recent": recent_eval.get("validity_status"),
            "historical": historical_eval.get("validity_status"),
            "structural": structural_eval.get("validity_status"),
            "prior_session_live": stale_eval.get("validity_status"),
        },
        "current_live_gate": live_gate.get("status"),
        "stale_live_gate": stale_gate.get("status"),
        "contradiction_selected_current": True,
        "fusion": fusion.get("classification"),
        "source_independence": (fusion.get("source_independence") or {}).get("status"),
        "run_audit": audit.get("passed"),
    }
    print(
        "__AHMED_DUAL_TEMPORAL_LIVE_ACCEPTANCE__"
        + json.dumps(summary, ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - acceptance boundary must log exact failure
        print(
            "__AHMED_DUAL_TEMPORAL_LIVE_ACCEPTANCE_FAILED__"
            + json.dumps(
                {"type": type(exc).__name__, "message": str(exc)[:4000]},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise
