from __future__ import annotations

from agent_reach.toolbox.freshness import evaluate_freshness, parse_temporal_interval
from agent_reach.toolbox.research import ResearchStore


def _evidence_with_source(
    store: ResearchStore,
    run_id: str,
    *,
    slug: str,
    data_cutoff: str | None,
    publication_date: str | None = "2026-09-22",
):
    source = store.record_source(
        run_id,
        url=f"https://example.com/{slug}",
        content=f"{slug}:{data_cutoff}",
        publisher="Example",
        publication_date=publication_date,
        data_cutoff=data_cutoff,
        retrieval_tool="fixture",
        retrieval_method="deterministic",
        retrieval_status="SUCCESS",
        retrieved_at="2026-09-22T12:00:00+00:00",
    )
    evidence = store.add_evidence(
        run_id,
        source_id=source["source_id"],
        supporting_passage=f"Evidence for {slug}.",
        observation_type="ACTUAL",
    )
    return source, evidence


def test_temporal_parser_preserves_precision() -> None:
    assert parse_temporal_interval("2026").precision == "YEAR"
    assert parse_temporal_interval("2026-09").precision == "MONTH"
    assert parse_temporal_interval("2026-09-22").precision == "DAY"
    assert parse_temporal_interval("2026-09-22T10:30:00Z").precision == "DATETIME"


def test_market_price_fresh_and_stale_from_data_cutoff() -> None:
    fresh = evaluate_freshness(
        data_cutoff="2026-09-22T11:30:00Z",
        publication_date="2026-09-22",
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="market_price",
        as_of="2026-09-22T12:00:00Z",
    )
    stale = evaluate_freshness(
        data_cutoff="2026-09-22T09:00:00Z",
        publication_date="2026-09-22",
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="market_price",
        as_of="2026-09-22T12:00:00Z",
    )

    assert fresh["status"] == "FRESH"
    assert fresh["reason_code"] == "WITHIN_MAX_AGE"
    assert stale["status"] == "STALE"
    assert stale["reason_code"] == "EXCEEDS_MAX_AGE"


def test_fresh_publication_does_not_hide_old_underlying_data() -> None:
    result = evaluate_freshness(
        data_cutoff="2026-09-01T00:00:00Z",
        publication_date="2026-09-22",
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="breaking_news",
        as_of="2026-09-22T12:00:00Z",
    )
    assert result["status"] == "STALE"
    assert result["basis_field"] == "data_cutoff"


def test_missing_data_cutoff_is_unknown_not_publication_fallback() -> None:
    result = evaluate_freshness(
        data_cutoff=None,
        publication_date="2026-09-22",
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="breaking_news",
        as_of="2026-09-22T12:00:00Z",
    )
    assert result["status"] == "UNKNOWN"
    assert result["reason_code"] == "MISSING_DATA_CUTOFF"


def test_coarse_date_precision_can_be_uncertain_at_boundary() -> None:
    result = evaluate_freshness(
        data_cutoff="2026-09-22",
        publication_date="2026-09-22",
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="market_price",
        as_of="2026-09-22T12:00:00Z",
    )
    assert result["status"] == "UNCERTAIN"
    assert result["reason_code"] == "DATE_PRECISION_CROSSES_FRESHNESS_BOUNDARY"


def test_latest_release_policy_requires_reference_and_detects_outdated_release() -> None:
    unknown = evaluate_freshness(
        data_cutoff="2026-08-31",
        publication_date="2026-09-10",
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="macro_indicator",
        as_of="2026-09-22T12:00:00Z",
    )
    outdated = evaluate_freshness(
        data_cutoff="2026-08-31",
        publication_date="2026-09-10",
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="macro_indicator",
        as_of="2026-09-22T12:00:00Z",
        latest_known_cutoff="2026-09-15",
    )
    current = evaluate_freshness(
        data_cutoff="2026-09-15",
        publication_date="2026-09-16",
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="macro_indicator",
        as_of="2026-09-22T12:00:00Z",
        latest_known_cutoff="2026-09-15",
    )

    assert unknown["status"] == "UNKNOWN"
    assert unknown["reason_code"] == "LATEST_KNOWN_CUTOFF_REQUIRED"
    assert outdated["status"] == "STALE"
    assert outdated["reason_code"] == "OLDER_THAN_LATEST_KNOWN_RELEASE"
    assert current["status"] == "FRESH"
    assert current["reason_code"] == "MATCHES_LATEST_KNOWN_RELEASE"


def test_academic_policy_reports_age_without_declaring_old_evidence_false() -> None:
    result = evaluate_freshness(
        data_cutoff="2018",
        publication_date="2019",
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="academic_evidence",
        as_of="2026-09-22T12:00:00Z",
    )
    assert result["status"] == "AGE_REPORTED"
    assert result["reason_code"] == "RECENCY_REPORTED_NOT_DOMINANT"


def test_persisted_freshness_flows_into_export_and_output_provenance() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Freshness provenance")
    _, evidence = _evidence_with_source(
        store,
        run["run_id"],
        slug="price",
        data_cutoff="2026-09-22T11:30:00Z",
    )
    evaluation = store.evaluate_evidence_freshness(
        evidence["evidence_id"],
        policy_name="market_price",
        as_of="2026-09-22T12:00:00Z",
    )
    claim = store.add_claim(
        run["run_id"],
        statement="Observed price is current.",
        classification="VERIFIED",
        observation_type="ACTUAL",
        supporting_evidence_ids=[evidence["evidence_id"]],
    )
    output = store.create_output(
        run["run_id"],
        consumer_type="dashboard",
        output_type="DASHBOARD_STATE",
        fragments=[
            {
                "content": "Observed price is current.",
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": "ACTUAL",
            }
        ],
    )

    assert evaluation["status"] == "FRESH"
    exported = store.export_run(run["run_id"])
    assert exported["evidence"][0]["latest_freshness"]["freshness_id"] == evaluation["freshness_id"]
    provenance = output["fragments"][0]["provenance_refs"][0]["evidence"][0]
    assert provenance["latest_freshness"]["status"] == "FRESH"
    assert provenance["latest_freshness"]["basis_field"] == "data_cutoff"


def test_custom_max_age_policy() -> None:
    result = evaluate_freshness(
        data_cutoff="2026-09-22T11:00:00Z",
        publication_date=None,
        retrieved_at="2026-09-22T12:00:00Z",
        policy_name="custom_max_age",
        max_age_seconds=7200,
        as_of="2026-09-22T12:00:00Z",
    )
    assert result["status"] == "FRESH"
    assert result["max_age_seconds"] == 7200.0
