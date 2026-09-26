from __future__ import annotations

import pytest

from agent_reach.toolbox.research import ResearchStore
from agent_reach.toolbox.retrieval import retrieve_with_fallback
from agent_reach.toolbox.temporal import (
    bounded_temporal_budget,
    temporal_fusion,
)


def _evidence(
    store: ResearchStore,
    run_id: str,
    *,
    slug: str,
    bucket: str,
    data_cutoff: str | None,
    authority: str,
    passage: str,
    publication_date: str | None = "2026-09-23",
    primary_source: bool | None = None,
    observation_time: str | None = None,
    retrieved_at: str = "2026-09-23T12:00:00+00:00",
):
    source = store.record_source(
        run_id,
        url=f"https://example.com/{slug}",
        content=f"{slug}:{passage}:{data_cutoff}",
        publisher=f"Publisher {slug}",
        source_type="fixture",
        primary_source=primary_source,
        publication_date=publication_date,
        data_cutoff=data_cutoff,
        observation_time=observation_time or data_cutoff,
        temporal_bucket=bucket,
        retrieval_tool="fixture",
        retrieval_method="deterministic",
        retrieval_status="SUCCESS",
        metadata={"source_authority": authority},
        retrieved_at=retrieved_at,
    )
    evidence = store.add_evidence(
        run_id,
        source_id=source["source_id"],
        supporting_passage=passage,
        observation_type="ACTUAL",
        temporal_bucket=bucket,
    )
    return source, evidence


def test_a_unga_stale_state_official_live_source_wins() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("UNGA current speaker state")
    _, indexed = _evidence(
        store,
        run["run_id"],
        slug="search-index",
        bucket="LIVE",
        data_cutoff="2026-09-23T11:45:00Z",
        authority="SEARCH_INDEX",
        passage="The leader has not spoken yet.",
    )
    _, official = _evidence(
        store,
        run["run_id"],
        slug="official-live",
        bucket="LIVE",
        data_cutoff="2026-09-23T11:58:00Z",
        authority="OFFICIAL_LIVE",
        passage="The leader has already spoken.",
        primary_source=True,
    )
    store.evaluate_evidence_temporal_validity(
        indexed["evidence_id"],
        as_of="2026-09-23T12:00:00Z",
    )
    store.evaluate_evidence_temporal_validity(
        official["evidence_id"],
        as_of="2026-09-23T12:00:00Z",
    )

    resolved = store.resolve_temporal_contradiction(
        run["run_id"],
        [
            {
                "evidence_id": indexed["evidence_id"],
                "claim_value": "NOT_SPOKEN",
            },
            {
                "evidence_id": official["evidence_id"],
                "claim_value": "ALREADY_SPOKEN",
            },
        ],
    )

    assert resolved["status"] == "RESOLVED"
    assert resolved["winner_evidence_id"] == official["evidence_id"]
    assert resolved["ordered_candidates"][0]["source_authority"] == "OFFICIAL_LIVE"


def test_b_same_day_breaking_news_can_be_stale() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Breaking-news freshness")
    _, evidence = _evidence(
        store,
        run["run_id"],
        slug="same-day-old-news",
        bucket="LIVE",
        data_cutoff="2026-09-23T02:00:00Z",
        authority="CREDIBLE_SECONDARY",
        passage="Same-day report with an old underlying state.",
        publication_date="2026-09-23",
    )

    result = store.evaluate_evidence_temporal_validity(
        evidence["evidence_id"],
        as_of="2026-09-23T12:00:00Z",
    )

    assert result["validity_status"] == "STALE"
    assert result["validity_reason"] == "EXCEEDS_MAX_AGE"
    assert result["freshness"]["publication_date"] == "2026-09-23"


def test_c_old_period_appropriate_historical_source_is_not_stale() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Historical context")
    _, evidence = _evidence(
        store,
        run["run_id"],
        slug="historical-1979",
        bucket="HISTORICAL",
        data_cutoff="1979",
        authority="OFFICIAL",
        passage="Primary historical record from 1979.",
        publication_date="1979",
        primary_source=True,
    )

    result = store.evaluate_evidence_temporal_validity(
        evidence["evidence_id"],
        as_of="2026-09-23T12:00:00Z",
        provenance_complete=True,
    )

    assert result["validity_status"] == "HISTORICALLY_VALID"
    assert result["validity_reason"] == "PERIOD_APPROPRIATE_EVIDENCE"
    assert result["freshness"] is None


def test_d_old_structural_rule_still_in_force_is_valid() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Structural validity")
    _, evidence = _evidence(
        store,
        run["run_id"],
        slug="treaty",
        bucket="STRUCTURAL",
        data_cutoff="1945",
        authority="OFFICIAL",
        passage="The treaty text remains in force.",
        publication_date="1945",
        primary_source=True,
    )

    result = store.evaluate_evidence_temporal_validity(
        evidence["evidence_id"],
        still_in_force=True,
    )

    assert result["validity_status"] == "STRUCTURALLY_VALID"
    assert result["structural_status"] == "STRUCTURALLY_VALID"


def test_e_changed_rule_is_not_current_structural_evidence() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Changed structural rule")
    _, evidence = _evidence(
        store,
        run["run_id"],
        slug="superseded-rule",
        bucket="STRUCTURAL",
        data_cutoff="2001",
        authority="OFFICIAL",
        passage="An older rule that was later amended.",
        publication_date="2001",
        primary_source=True,
    )

    result = store.evaluate_evidence_temporal_validity(
        evidence["evidence_id"],
        still_in_force=False,
    )

    assert result["validity_status"] == "STRUCTURALLY_INVALID"
    assert result["validity_reason"] == "NO_LONGER_IN_FORCE"


def test_f_http_403_fallback_succeeds_without_aborting() -> None:
    calls: list[str] = []

    def caller(name: str, arguments: dict) -> dict:
        del arguments
        calls.append(name)
        if name == "reach_read_url":
            return {"content": [{"type": "text", "text": "HTTP 403 Forbidden"}], "isError": True}
        if name == "scrapling__fetch":
            return {"content": [{"type": "text", "text": "usable " + "x" * 400}], "isError": False}
        raise AssertionError(f"unexpected tool: {name}")

    result = retrieve_with_fallback(
        "https://example.com/blocked",
        call_tool=caller,
        min_chars=200,
    )

    assert result["status"] == "SUCCESS"
    assert result["attempts"][0]["reason_code"] == "HTTP_403_FORBIDDEN"
    assert result["final_tool"] == "scrapling__fetch"
    assert calls == ["reach_read_url", "scrapling__fetch"]


def test_g_429_rate_limit_falls_through_to_targeted_search() -> None:
    calls: list[str] = []

    def caller(name: str, arguments: dict) -> dict:
        calls.append(name)
        if name == "reach_web_search":
            assert arguments["query"] == "current official state"
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "credible alternative https://example.org/current",
                    }
                ],
                "isError": False,
            }
        return {
            "content": [{"type": "text", "text": "429 Too Many Requests - rate limit"}],
            "isError": True,
        }

    result = retrieve_with_fallback(
        "https://example.com/rate-limited",
        call_tool=caller,
        browser_tool="reach_browser_read_url",
        discovery_tool="reach_web_search",
        discovery_query="current official state",
    )

    assert result["status"] == "ALTERNATIVE_DISCOVERED"
    assert result["reason_code"] == "ORIGINAL_SOURCE_UNAVAILABLE_ALTERNATIVES_DISCOVERED"
    assert any(item["reason_code"] == "RATE_LIMITED" for item in result["attempts"])
    assert calls == [
        "reach_read_url",
        "scrapling__fetch",
        "scrapling__stealthy_fetch",
        "reach_browser_read_url",
        "reach_web_search",
    ]


def test_h_temporal_layer_cannot_break_semantic_integrity() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Semantic integrity")
    _, evidence = _evidence(
        store,
        run["run_id"],
        slug="actual",
        bucket="LIVE",
        data_cutoff="2026-09-23T11:55:00Z",
        authority="OFFICIAL_LIVE",
        passage="This is an observed current fact.",
        primary_source=True,
    )
    claim = store.add_claim(
        run["run_id"],
        statement="Observed current fact.",
        classification="VERIFIED",
        observation_type="ACTUAL",
        supporting_evidence_ids=[evidence["evidence_id"]],
    )

    with pytest.raises(ValueError, match="semantic promotion/mismatch"):
        store.create_output(
            run["run_id"],
            consumer_type="answer",
            output_type="ANSWER",
            fragments=[
                {
                    "content": "Observed current fact.",
                    "claim_ids": [claim["claim_id"]],
                    "asserted_observation_type": "FORECAST",
                }
            ],
        )

    with pytest.raises(ValueError, match="semantic promotion/mismatch"):
        store.create_output(
            run["run_id"],
            consumer_type="answer",
            output_type="ANSWER",
            fragments=[
                {
                    "content": "Observed current fact.",
                    "claim_ids": [claim["claim_id"]],
                    "asserted_observation_type": "MIXED",
                }
            ],
        )


def test_i_diplomatic_contact_does_not_imply_structural_normalization() -> None:
    result = temporal_fusion(
        historical_alignment="CONSISTENT",
        structural_change="NONE",
        persistence="UNKNOWN",
        changed_now="Active diplomatic contact is occurring.",
        unchanged="The historically adversarial relationship and constraints remain.",
        structural_constraints=["sanctions", "formal legal constraints"],
        falsifiers=["durable removal of structural constraints"],
    )

    assert result["classification"] == "PATTERN_CONTINUATION"
    assert result["classification"] != "STRUCTURAL_BREAK"
    assert "NORMALIZATION" not in result["classification"]
    assert "historically adversarial" in result["questions"]["what_did_not_change"]


def test_j_temporal_budget_is_deep_but_bounded() -> None:
    budget = bounded_temporal_budget()
    calls: list[str] = []

    def caller(name: str, arguments: dict) -> dict:
        del arguments
        calls.append(name)
        return {
            "content": [{"type": "text", "text": "backend unavailable"}],
            "isError": True,
        }

    result = retrieve_with_fallback(
        "https://example.com/unavailable",
        call_tool=caller,
        browser_tool="reach_browser_read_url",
        discovery_tool="reach_web_search",
    )

    assert budget == {
        "max_total_retrieval_calls": 12,
        "max_calls_per_lane": 3,
        "max_fallback_attempts_per_source": 5,
    }
    assert result["status"] == "SOURCE_UNAVAILABLE"
    assert len(calls) == budget["max_fallback_attempts_per_source"]
    assert not any(name.startswith(("runtime_", "orchestration_")) for name in calls)


def test_final_live_refresh_gate_blocks_then_passes_after_explicit_refresh() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Final live refresh")
    source, evidence = _evidence(
        store,
        run["run_id"],
        slug="official-final",
        bucket="LIVE",
        data_cutoff="2026-09-23T11:58:00Z",
        authority="OFFICIAL_LIVE",
        passage="Official live state.",
        primary_source=True,
    )

    blocked = store.final_live_refresh_gate(
        evidence["evidence_id"],
        as_of="2026-09-23T12:00:00Z",
    )
    assert blocked["passed"] is False
    assert blocked["reason"] == "FINAL_LIVE_REFRESH_EVENT_MISSING"

    store.record_source(
        run["run_id"],
        url=source["canonical_url"],
        content="official-final:Official live state.:2026-09-23T11:58:00Z",
        publisher="Publisher official-final",
        source_type="fixture",
        primary_source=True,
        publication_date="2026-09-23",
        data_cutoff="2026-09-23T11:58:00Z",
        observation_time="2026-09-23T11:58:00Z",
        temporal_bucket="LIVE",
        retrieval_tool="fixture",
        retrieval_method="official-live-refresh",
        retrieval_status="SUCCESS",
        metadata={"source_authority": "OFFICIAL_LIVE"},
        retrieval_history=[
            {
                "stage": "FINAL_LIVE_REFRESH",
                "tool": "fixture",
                "method": "official-live-refresh",
                "status": "SUCCESS",
                "occurred_at": "2026-09-23T11:59:00Z",
            }
        ],
        retrieved_at="2026-09-23T11:59:00Z",
    )

    passed = store.final_live_refresh_gate(
        evidence["evidence_id"],
        as_of="2026-09-23T12:00:00Z",
    )
    assert passed["passed"] is True
    assert passed["reason"] == "FINAL_LIVE_REFRESH_FRESH"


def test_temporal_fusion_is_persisted_and_exported_with_four_lane_coverage() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Four-lane fusion")
    evidence_ids = []
    fixtures = [
        ("live", "LIVE", "2026-09-23T11:55:00Z", "OFFICIAL_LIVE"),
        ("recent", "RECENT", "2026-09-20", "CREDIBLE_SECONDARY"),
        ("history", "HISTORICAL", "1979", "OFFICIAL"),
        ("structure", "STRUCTURAL", "1945", "OFFICIAL"),
    ]
    for slug, bucket, cutoff, authority in fixtures:
        _, evidence = _evidence(
            store,
            run["run_id"],
            slug=slug,
            bucket=bucket,
            data_cutoff=cutoff,
            authority=authority,
            passage=f"{bucket} evidence.",
            primary_source=authority.startswith("OFFICIAL"),
            publication_date=cutoff,
        )
        evidence_ids.append(evidence["evidence_id"])

    fusion = store.create_temporal_fusion(
        run["run_id"],
        evidence_ids=evidence_ids,
        historical_alignment="DEVIATES",
        structural_change="POSSIBLE",
        persistence="UNKNOWN",
        changed_now="A new event occurred.",
        unchanged="Long-term constraints remain.",
        structural_constraints=["constraint"],
        contradicting_evidence_ids=[evidence_ids[1]],
        falsifiers=["future evidence"],
    )

    assert fusion["classification"] == "UNRESOLVED"
    assert all(fusion["coverage"].values())
    exported = store.export_run(run["run_id"])
    assert exported["temporal_fusions"][0]["fusion_id"] == fusion["fusion_id"]
    assert {item["temporal_bucket"] for item in exported["evidence"]} == {
        "LIVE",
        "RECENT",
        "HISTORICAL",
        "STRUCTURAL",
    }


def test_temporal_metadata_flows_into_evidence_ledger_and_audit() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Temporal ledger")
    _, evidence = _evidence(
        store,
        run["run_id"],
        slug="ledger-live",
        bucket="LIVE",
        data_cutoff="2026-09-23T11:59:00Z",
        authority="OFFICIAL_LIVE",
        passage="Live ledger evidence.",
        primary_source=True,
    )
    store.add_claim(
        run["run_id"],
        statement="Live ledger claim.",
        classification="VERIFIED",
        observation_type="ACTUAL",
        supporting_evidence_ids=[evidence["evidence_id"]],
    )

    rows = store.export_ledger_rows(run["run_id"])
    audit = store.audit_run(run["run_id"])

    assert rows[0]["temporal_bucket"] == "LIVE"
    assert rows[0]["observation_time"] == "2026-09-23T11:59:00Z"
    assert audit["passed"] is True
    assert audit["checks"]["temporal_metadata_valid"] is True
