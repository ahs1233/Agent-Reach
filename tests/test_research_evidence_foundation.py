from __future__ import annotations

import pytest

from agent_reach.toolbox.research import ResearchStore


def _source(
    store: ResearchStore,
    run_id: str,
    *,
    content: str = "version one",
    publication_date: str = "2026-04-10",
    data_cutoff: str = "2026-03-31",
):
    return store.record_source(
        run_id,
        url="https://example.com/report/",
        content=content,
        publisher="Example Institute",
        source_type="official_institution_report",
        primary_source=True,
        publication_date=publication_date,
        data_cutoff=data_cutoff,
        retrieval_tool="Scrapling",
        retrieval_method="fetch",
        retrieval_status="SUCCESS",
        discovered_by="Exa",
        retrieval_history=[
            {
                "stage": "DISCOVERY",
                "tool": "Exa",
                "method": "web_search",
                "status": "DISCOVERED",
            },
            {
                "stage": "RETRIEVAL",
                "tool": "Scrapling",
                "method": "fetch",
                "status": "SUCCESS",
            },
        ],
    )


def _evidence(store: ResearchStore, run_id: str, source_id: str):
    return store.add_evidence(
        run_id,
        source_id=source_id,
        supporting_passage=(
            "Our projections see electricity consumption rising from "
            "485 TWh in 2025 to 950 TWh in 2030."
        ),
        structured_fact={
            "metric": "data_center_electricity_consumption",
            "from_value": 485,
            "from_year": 2025,
            "to_value": 950,
            "to_year": 2030,
            "unit": "TWh",
            "observation_type": "FORECAST",
        },
        metric="data_center_electricity_consumption",
        value=950,
        unit="TWh",
        geography="global",
        reference_period="2025-2030",
        observation_type="FORECAST",
        forecast_horizon="2030",
        definition="Electricity consumed by data centres",
        extraction_method="structured_manual_test",
    )


def test_acceptance_1_claim_requires_evidence() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Will power constrain AI?", cutoff="2026-09-22")

    with pytest.raises(ValueError, match="at least one evidence_id"):
        store.add_claim(
            run["run_id"],
            statement="AI infrastructure is power constrained.",
            classification="STRONG_SIGNAL",
        )


def test_acceptance_2_evidence_requires_source_link() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Question")

    with pytest.raises(ValueError, match="not linked"):
        store.add_evidence(
            run["run_id"],
            source_id="S-999999",
            supporting_passage="Some evidence.",
            observation_type="ACTUAL",
        )


def test_acceptance_3_source_records_tool_time_and_retrieval_chain() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Question")
    source = _source(store, run["run_id"])

    assert source["retrieval_tool"] == "Scrapling"
    assert source["latest_retrieved_at"]
    assert [event["tool"] for event in source["retrieval_history"]] == ["Exa", "Scrapling"]
    assert [event["stage"] for event in source["retrieval_history"]] == [
        "DISCOVERY",
        "RETRIEVAL",
    ]


def test_acceptance_4_publication_date_and_data_cutoff_are_separate() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Question")
    source = _source(store, run["run_id"])

    assert source["publication_date"] == "2026-04-10"
    assert source["data_cutoff"] == "2026-03-31"
    assert source["publication_date"] != source["data_cutoff"]


def test_acceptance_5_observation_type_is_stored() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Question")
    source = _source(store, run["run_id"])
    evidence = _evidence(store, run["run_id"], source["source_id"])

    assert evidence["observation_type"] == "FORECAST"
    assert evidence["structured_fact"]["observation_type"] == "FORECAST"


def test_acceptance_6_unchanged_content_reuses_source_changed_content_versions() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Question")

    first = _source(store, run["run_id"], content="same content")
    second = _source(store, run["run_id"], content="same content")
    changed = _source(store, run["run_id"], content="updated content")

    assert first["source_id"] == second["source_id"]
    assert first["content_hash"] == second["content_hash"]
    assert changed["source_id"] != first["source_id"]
    assert changed["content_hash"] != first["content_hash"]
    assert changed["version_number"] == 2
    assert changed["previous_source_id"] == first["source_id"]


def test_acceptance_7_export_resolves_claim_to_evidence_to_source() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("How fast is data-centre electricity demand rising?")
    source = _source(store, run["run_id"])
    evidence = _evidence(store, run["run_id"], source["source_id"])
    claim = store.add_claim(
        run["run_id"],
        statement="Data-centre electricity demand is rising rapidly.",
        classification="VERIFIED",
        supporting_evidence_ids=[evidence["evidence_id"]],
        confidence="HIGH",
        provenance={"created_from": "evidence-ledger"},
    )

    exported = store.export_run(run["run_id"])
    ledger = exported["ledger"]

    assert exported["claims"][0]["claim_id"] == claim["claim_id"]
    assert exported["evidence"][0]["source_id"] == source["source_id"]
    assert len(ledger) == 1
    assert ledger[0]["claim_id"] == claim["claim_id"]
    assert ledger[0]["evidence_id"] == evidence["evidence_id"]
    assert ledger[0]["source_id"] == source["source_id"]
    assert ledger[0]["observation_type"] == "FORECAST"
    assert ledger[0]["publication_date"] == "2026-04-10"
    assert ledger[0]["data_cutoff"] == "2026-03-31"

    audit = store.audit_run(run["run_id"])
    assert audit["passed"] is True
    assert all(audit["checks"].values())


def test_claim_cannot_reference_evidence_from_another_run() -> None:
    store = ResearchStore(":memory:")
    run_a = store.create_run("A")
    run_b = store.create_run("B")
    source = _source(store, run_a["run_id"])
    evidence = _evidence(store, run_a["run_id"], source["source_id"])

    with pytest.raises(ValueError, match="same research run"):
        store.add_claim(
            run_b["run_id"],
            statement="Cross-run contamination",
            classification="INFERENCE",
            supporting_evidence_ids=[evidence["evidence_id"]],
        )



def test_export_uses_run_specific_retrieval_provenance_for_reused_source() -> None:
    store = ResearchStore(":memory:")
    run_a = store.create_run("A")
    run_b = store.create_run("B")

    source_a = store.record_source(
        run_a["run_id"],
        url="https://example.com/report",
        content="stable content",
        retrieval_tool="Jina",
        retrieval_method="reader",
        retrieval_status="SUCCESS",
        retrieved_at="2026-09-22T08:00:00+00:00",
    )
    evidence_a = store.add_evidence(
        run_a["run_id"],
        source_id=source_a["source_id"],
        supporting_passage="stable content",
        observation_type="ACTUAL",
    )
    store.add_claim(
        run_a["run_id"],
        statement="Stable claim A",
        classification="VERIFIED",
        supporting_evidence_ids=[evidence_a["evidence_id"]],
    )

    source_b = store.record_source(
        run_b["run_id"],
        url="https://example.com/report",
        content="stable content",
        retrieval_tool="Scrapling",
        retrieval_method="fetch",
        retrieval_status="SUCCESS",
        retrieved_at="2026-09-22T09:00:00+00:00",
    )
    evidence_b = store.add_evidence(
        run_b["run_id"],
        source_id=source_b["source_id"],
        supporting_passage="stable content",
        observation_type="ACTUAL",
    )
    store.add_claim(
        run_b["run_id"],
        statement="Stable claim B",
        classification="VERIFIED",
        supporting_evidence_ids=[evidence_b["evidence_id"]],
    )

    assert source_a["source_id"] == source_b["source_id"]

    ledger_a = store.export_run(run_a["run_id"])["ledger"][0]
    ledger_b = store.export_run(run_b["run_id"])["ledger"][0]

    assert ledger_a["retrieval_tool"] == "Jina"
    assert ledger_a["retrieval_method"] == "reader"
    assert ledger_a["retrieved_at"] == "2026-09-22T08:00:00+00:00"

    assert ledger_b["retrieval_tool"] == "Scrapling"
    assert ledger_b["retrieval_method"] == "fetch"
    assert ledger_b["retrieved_at"] == "2026-09-22T09:00:00+00:00"
