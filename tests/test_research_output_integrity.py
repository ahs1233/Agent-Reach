from __future__ import annotations

import pytest

from agent_reach.toolbox.research import ResearchStore


def _claim(
    store: ResearchStore,
    run_id: str,
    *,
    slug: str,
    observation_type: str,
    statement: str,
    value: float | None = None,
    unit: str | None = None,
    structured_fact: dict | None = None,
):
    source = store.record_source(
        run_id,
        url=f"https://example.com/{slug}",
        content=f"{slug}:{observation_type}:{statement}:{value}:{unit}",
        publisher="Example Primary Source",
        source_type="primary_source",
        primary_source=True,
        publication_date="2026-09-22",
        data_cutoff="2026-09-22",
        retrieval_tool="test-fixture",
        retrieval_method="deterministic",
        retrieval_status="SUCCESS",
    )
    evidence = store.add_evidence(
        run_id,
        source_id=source["source_id"],
        supporting_passage=statement,
        structured_fact=structured_fact or {
            "observation_type": observation_type,
            "value": value,
            "unit": unit,
        },
        metric=slug,
        value=value,
        unit=unit,
        reference_period="2026-09-22",
        observation_type=observation_type,
        extraction_method="deterministic-test",
    )
    claim = store.add_claim(
        run_id,
        statement=statement,
        classification="VERIFIED",
        supporting_evidence_ids=[evidence["evidence_id"]],
        observation_type=observation_type,
        confidence="HIGH",
    )
    return source, evidence, claim


def test_forecast_cannot_be_consumed_as_actual() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Macro forecast guardrail")
    _, _, claim = _claim(
        store,
        run["run_id"],
        slug="macro-debt",
        observation_type="FORECAST",
        statement="Debt is projected to reach 100% of GDP by 2029.",
        value=100,
        unit="% GDP",
    )

    with pytest.raises(ValueError, match="semantic promotion/mismatch"):
        store.create_output(
            run["run_id"],
            consumer_type="answer_service",
            output_type="ANSWER",
            fragments=[
                {
                    "content": "Debt will reach 100% of GDP by 2029.",
                    "claim_ids": [claim["claim_id"]],
                    "asserted_observation_type": "ACTUAL",
                }
            ],
        )

    output = store.create_output(
        run["run_id"],
        consumer_type="answer_service",
        output_type="ANSWER",
        fragments=[
            {
                "content": "Debt is projected to reach 100% of GDP by 2029.",
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": "FORECAST",
            }
        ],
    )

    fragment = output["fragments"][0]
    assert fragment["asserted_observation_type"] == "FORECAST"
    assert fragment["rendered_content"].startswith("Forecast:")
    assert fragment["provenance_refs"][0]["claim_id"] == claim["claim_id"]


def test_actual_can_be_consumed_as_actual() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Trading actual")
    _, _, claim = _claim(
        store,
        run["run_id"],
        slug="market-price",
        observation_type="ACTUAL",
        statement="Observed market price was 4,550.25.",
        value=4550.25,
        unit="USD",
    )

    output = store.create_output(
        run["run_id"],
        consumer_type="market_dashboard",
        output_type="DASHBOARD_STATE",
        fragments=[
            {
                "payload": {"price": 4550.25, "unit": "USD"},
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": "ACTUAL",
            }
        ],
    )

    assert output["fragments"][0]["payload"]["price"] == 4550.25
    assert store.audit_output(output["output_id"])["passed"] is True


def test_target_cannot_be_promoted_to_guidance_or_actual() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Company target")
    _, _, claim = _claim(
        store,
        run["run_id"],
        slug="company-target",
        observation_type="TARGET",
        statement="The company targets 50% renewable electricity by 2030.",
        value=50,
        unit="%",
    )

    for wrong_type in ("ACTUAL", "FORECAST", "COMPANY_GUIDANCE"):
        with pytest.raises(ValueError, match="semantic promotion/mismatch"):
            store.create_output(
                run["run_id"],
                consumer_type="company_monitor",
                output_type="STATE_UPDATE",
                fragments=[
                    {
                        "content": "The company will use 50% renewable electricity.",
                        "claim_ids": [claim["claim_id"]],
                        "asserted_observation_type": wrong_type,
                    }
                ],
            )

    valid = store.create_output(
        run["run_id"],
        consumer_type="company_monitor",
        output_type="STATE_UPDATE",
        fragments=[
            {
                "content": "The company targets 50% renewable electricity by 2030.",
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": "TARGET",
            }
        ],
    )
    assert valid["fragments"][0]["rendered_content"].startswith("Target:")


def test_alert_preserves_claim_evidence_source_chain() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Competitive price watch")
    source, evidence, claim = _claim(
        store,
        run["run_id"],
        slug="competitor-price",
        observation_type="ACTUAL",
        statement="Competitor price changed to 1,500 IQD.",
        value=1500,
        unit="IQD",
        structured_fact={
            "event": "price_change",
            "old_value": 1750,
            "new_value": 1500,
            "unit": "IQD",
        },
    )

    output = store.create_output(
        run["run_id"],
        consumer_type="price_monitor",
        consumer_id="competitor-watch",
        output_type="ALERT",
        payload={"severity": "material"},
        fragments=[
            {
                "content": "Competitor price changed to 1,500 IQD.",
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": "ACTUAL",
            }
        ],
    )

    chain = output["fragments"][0]["provenance_refs"][0]
    assert chain["claim_id"] == claim["claim_id"]
    assert chain["evidence"][0]["evidence_id"] == evidence["evidence_id"]
    assert chain["evidence"][0]["supporting_passage"] == evidence["supporting_passage"]
    assert chain["evidence"][0]["structured_fact"] == evidence["structured_fact"]
    assert chain["evidence"][0]["source_id"] == source["source_id"]
    assert chain["evidence"][0]["content_hash_scope"] == "retrieved_representation"
    assert output["output_type"] == "ALERT"


def test_qa_browser_observation_is_not_report_specific() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Software QA")
    source, evidence, claim = _claim(
        store,
        run["run_id"],
        slug="checkout-ipad",
        observation_type="ACTUAL",
        statement="Checkout button did not complete the action at the tested iPad viewport.",
        structured_fact={
            "evidence_kind": "browser_observation",
            "viewport": {"width": 1024, "height": 1366},
            "selector": "[data-testid='checkout']",
            "action": "click",
            "result": "timeout",
        },
    )

    output = store.create_output(
        run["run_id"],
        consumer_type="qa_agent",
        output_type="QA_RESULT",
        fragments=[
            {
                "payload": {
                    "status": "failed",
                    "condition": "iPad viewport",
                },
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": "ACTUAL",
            }
        ],
    )

    ref = output["fragments"][0]["provenance_refs"][0]["evidence"][0]
    assert ref["source_id"] == source["source_id"]
    assert ref["evidence_id"] == evidence["evidence_id"]
    assert store.audit_output(output["output_id"])["passed"] is True


@pytest.mark.parametrize(
    ("domain", "output_type", "observation_type"),
    [
        ("macro", "ANSWER", "FORECAST"),
        ("trading", "DASHBOARD_STATE", "ACTUAL"),
        ("science", "API_RESULT", "ESTIMATE"),
        ("software_qa", "QA_RESULT", "ACTUAL"),
        ("competitive_intelligence", "ALERT", "ACTUAL"),
        ("alsouq", "AUTOMATION_INPUT", "ACTUAL"),
    ],
)
def test_core_output_model_is_domain_agnostic(
    domain: str,
    output_type: str,
    observation_type: str,
) -> None:
    store = ResearchStore(":memory:")
    run = store.create_run(f"Domain check: {domain}")
    _, _, claim = _claim(
        store,
        run["run_id"],
        slug=domain,
        observation_type=observation_type,
        statement=f"Auditable observation for {domain}.",
    )

    output = store.create_output(
        run["run_id"],
        consumer_type=f"{domain}_consumer",
        output_type=output_type,
        fragments=[
            {
                "content": f"Auditable observation for {domain}.",
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": observation_type,
            }
        ],
    )

    assert output["output_type"] == output_type
    assert output["fragments"][0]["claim_ids"] == [claim["claim_id"]]
    assert store.audit_output(output["output_id"])["passed"] is True


def test_mixed_claim_semantics_require_explicit_mixed_output() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Mixed semantic output")
    _, _, actual_claim = _claim(
        store,
        run["run_id"],
        slug="actual",
        observation_type="ACTUAL",
        statement="Observed value is 10.",
        value=10,
    )
    _, _, forecast_claim = _claim(
        store,
        run["run_id"],
        slug="forecast",
        observation_type="FORECAST",
        statement="Projected value is 20.",
        value=20,
    )

    with pytest.raises(ValueError, match="semantic promotion/mismatch"):
        store.create_output(
            run["run_id"],
            consumer_type="analysis_agent",
            output_type="ANSWER",
            fragments=[
                {
                    "content": "Observed value is 10 and projected value is 20.",
                    "claim_ids": [
                        actual_claim["claim_id"],
                        forecast_claim["claim_id"],
                    ],
                    "asserted_observation_type": "ACTUAL",
                }
            ],
        )

    output = store.create_output(
        run["run_id"],
        consumer_type="analysis_agent",
        output_type="ANSWER",
        fragments=[
            {
                "content": "Observed value is 10 and projected value is 20.",
                "claim_ids": [
                    actual_claim["claim_id"],
                    forecast_claim["claim_id"],
                ],
                "asserted_observation_type": "MIXED",
            }
        ],
    )

    assert output["fragments"][0]["rendered_content"].startswith("Mixed semantics:")


def test_export_run_includes_generic_outputs() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Export output")
    _, _, claim = _claim(
        store,
        run["run_id"],
        slug="export",
        observation_type="ACTUAL",
        statement="Observed state.",
    )
    output = store.create_output(
        run["run_id"],
        consumer_type="api",
        output_type="API_RESULT",
        fragments=[
            {
                "payload": {"state": "observed"},
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": "ACTUAL",
            }
        ],
    )

    exported = store.export_run(run["run_id"])
    assert exported["outputs"][0]["output_id"] == output["output_id"]



def test_sprint1_claim_semantics_can_be_backfilled_from_supporting_evidence() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Sprint 1 migration")
    _, _, claim = _claim(
        store,
        run["run_id"],
        slug="legacy-forecast",
        observation_type="FORECAST",
        statement="Legacy claim was a forecast.",
    )

    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE claims SET observation_type = 'UNKNOWN' WHERE claim_id = ?",
            (claim["claim_id"],),
        )
        store._backfill_claim_observation_types()

    migrated = store.get_claim(claim["claim_id"])
    assert migrated["observation_type"] == "FORECAST"
