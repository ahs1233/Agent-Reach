from __future__ import annotations

import pytest

from agent_reach.toolbox.ace import (
    ACEStore,
    CostAwareRouter,
    LocalManifestProvider,
    ProviderSpec,
    structured_script,
    validate_content_claims,
)
from agent_reach.toolbox.ace_mcp import handle_ace_tool
from agent_reach.toolbox.research import ResearchStore


class _PaidProvider:
    spec = ProviderSpec(
        name="paid",
        capabilities=("storyboard",),
        estimated_cost=1.0,
        quality=0.95,
        latency_seconds=1.0,
        quota="paid",
        paid=True,
        available=True,
    )

    def generate(self, kind, prompt, context):
        return {
            "kind": kind,
            "prompt": prompt,
            "context": context,
        }


class _UnavailableProvider:
    spec = ProviderSpec(
        name="quota_exhausted",
        capabilities=("storyboard",),
        estimated_cost=0.0,
        quality=1.0,
        latency_seconds=0.1,
        quota="0",
        paid=False,
        available=False,
    )

    def generate(self, kind, prompt, context):
        raise AssertionError(
            "unavailable provider must not be called"
        )


def _campaign(store: ACEStore):
    return store.create_campaign(
        "Test an ecommerce app with short-form video",
        objective="conversion",
        platforms=["instagram", "tiktok"],
        free_only=True,
    )


def _experiment(
    store: ACEStore,
    minimum: int = 200,
):
    campaign = _campaign(store)
    hypothesis = store.create_hypothesis(
        campaign["campaign_id"],
        target_audience="online shoppers",
        variable="hook",
        control={"hook": "feature"},
        variant={"hook": "problem"},
        expected_outcome=(
            "problem hook improves retention"
        ),
        primary_metric="completion_rate",
        secondary_metrics=[
            "shares",
            "saves",
            "conversions",
        ],
        minimum_evidence=minimum,
    )
    experiment = store.create_experiment(
        campaign["campaign_id"],
        hypothesis,
    )
    control = store.add_variant(
        experiment["experiment_id"],
        label="CONTROL",
        is_control=True,
        persona_id=None,
        script=structured_script(
            "Feature",
            "Setup",
            "Value",
            "Proof",
            "CTA",
        ),
        variable_value={"hook": "feature"},
        rationale="control",
    )
    variant = store.add_variant(
        experiment["experiment_id"],
        label="VARIANT_A",
        is_control=False,
        persona_id=None,
        script=structured_script(
            "Problem?",
            "Setup",
            "Value",
            "Proof",
            "CTA",
        ),
        variable_value={"hook": "problem"},
        rationale="variant",
    )
    return (
        campaign,
        experiment,
        control,
        variant,
    )


def test_schema_and_traceable_entities(tmp_path):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )
    campaign, experiment, control, variant = (
        _experiment(store)
    )
    finding = store.add_finding(
        campaign["campaign_id"],
        kind="OBSERVATION",
        summary="question hook observed",
        source_ref="E-000001",
        source_url="https://example.com/source",
        retrieval_method="reach_retrieve_url",
        confidence=0.7,
        evidence_ids=["E-000001"],
        metadata={
            "signals": ["question_hook"]
        },
    )
    patterns = store.derive_content_patterns(
        campaign["campaign_id"]
    )

    assert finding["evidence_ids"] == [
        "E-000001"
    ]
    assert patterns[0]["pattern"] == (
        "question_hook"
    )
    assert (
        patterns[0]["causation_claimed"]
        is False
    )
    assert experiment["hypothesis_id"]
    assert (
        control["content_hash"]
        != variant["content_hash"]
    )


def test_free_only_router_refuses_paid_fallback():
    router = CostAwareRouter(
        [
            _UnavailableProvider(),
            _PaidProvider(),
        ]
    )

    with pytest.raises(
        ValueError,
        match="FREE_ONLY",
    ):
        router.route(
            "storyboard",
            free_only=True,
        )

    assert (
        router.route(
            "storyboard",
            free_only=False,
        ).spec.name
        == "paid"
    )


def test_provider_quota_exhausted_fails_safe():
    router = CostAwareRouter(
        [_UnavailableProvider()]
    )

    with pytest.raises(
        ValueError,
        match="no configured",
    ):
        router.route(
            "storyboard",
            free_only=False,
        )


def test_local_provider_is_free_and_honest():
    provider = CostAwareRouter(
        [LocalManifestProvider()]
    ).route(
        "storyboard",
        free_only=True,
    )
    result = provider.generate(
        "storyboard",
        "prompt",
        {},
    )

    assert result["estimated_cost"] == 0
    assert result["media_generated"] is False
    assert result["artifact_type"] == (
        "MANIFEST"
    )


def test_safety_blocks_fake_authority():
    result = validate_content_claims(
        "As a doctor, I guarantee this."
    )

    assert result["allowed"] is False
    assert "fabricated_authority" in (
        result["violations"]
    )


def test_safety_blocks_opaque_ai_character():
    result = validate_content_claims(
        "creative",
        persona_type="AI_CHARACTER",
        transparent_ai=False,
    )

    assert result["allowed"] is False
    assert "ai_character_must_be_transparent" in (
        result["violations"]
    )


def test_duplicate_content_fails_clearly(
    tmp_path,
):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )
    campaign = _campaign(store)
    payload = {"same": True}

    store.add_asset(
        campaign["campaign_id"],
        variant_id=None,
        kind="storyboard",
        provider="local_manifest",
        payload=payload,
    )

    with pytest.raises(
        ValueError,
        match="duplicate content",
    ):
        store.add_asset(
            campaign["campaign_id"],
            variant_id=None,
            kind="storyboard",
            provider="local_manifest",
            payload=payload,
        )


def test_missing_analytics_needs_more_data(
    tmp_path,
):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )
    _, experiment, control, _ = (
        _experiment(store)
    )
    store.record_metrics(
        experiment["experiment_id"],
        control["variant_id"],
        platform="tiktok",
        metrics={
            "views": 1000,
            "completion_rate": 0.5,
        },
    )

    result = store.evaluate(
        experiment["experiment_id"]
    )

    assert result["state"] == (
        "NEEDS_MORE_DATA"
    )
    assert (
        result["winner_variant_id"]
        is None
    )


def test_small_sample_does_not_force_winner(
    tmp_path,
):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )
    _, experiment, control, variant = (
        _experiment(
            store,
            minimum=500,
        )
    )
    store.record_metrics(
        experiment["experiment_id"],
        control["variant_id"],
        platform="tiktok",
        metrics={
            "views": 100,
            "completion_rate": 0.2,
        },
    )
    store.record_metrics(
        experiment["experiment_id"],
        variant["variant_id"],
        platform="tiktok",
        metrics={
            "views": 100,
            "completion_rate": 0.9,
        },
    )

    result = store.evaluate(
        experiment["experiment_id"]
    )

    assert result["state"] == (
        "NEEDS_MORE_DATA"
    )


def test_conflicting_metrics_are_inconclusive(
    tmp_path,
):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )
    _, experiment, control, variant = (
        _experiment(store)
    )
    store.record_metrics(
        experiment["experiment_id"],
        control["variant_id"],
        platform="instagram",
        metrics={
            "reach": 1000,
            "completion_rate": 0.65,
            "likes": 100,
            "shares": 20,
            "saves": 20,
            "profile_visits": 70,
            "conversions": 5,
        },
    )
    store.record_metrics(
        experiment["experiment_id"],
        variant["variant_id"],
        platform="instagram",
        metrics={
            "reach": 1000,
            "completion_rate": 0.66,
            "likes": 98,
            "shares": 22,
            "saves": 19,
            "profile_visits": 72,
            "conversions": 5,
        },
    )

    result = store.evaluate(
        experiment["experiment_id"]
    )

    assert result["state"] == (
        "INCONCLUSIVE"
    )
    assert (
        result["winner_variant_id"]
        is None
    )


def test_clear_winner_produces_learning(
    tmp_path,
):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )
    campaign, experiment, control, variant = (
        _experiment(store)
    )
    store.record_metrics(
        experiment["experiment_id"],
        control["variant_id"],
        platform="tiktok",
        metrics={
            "reach": 5000,
            "completion_rate": 0.20,
            "likes": 100,
            "shares": 10,
            "saves": 10,
            "profile_visits": 20,
            "conversions": 1,
        },
    )
    store.record_metrics(
        experiment["experiment_id"],
        variant["variant_id"],
        platform="tiktok",
        metrics={
            "reach": 5000,
            "completion_rate": 0.90,
            "likes": 1500,
            "shares": 400,
            "saves": 500,
            "profile_visits": 700,
            "conversions": 150,
        },
    )

    result = store.evaluate(
        experiment["experiment_id"]
    )

    assert result["state"] == "WINNER"
    assert (
        result["winner_variant_id"]
        == variant["variant_id"]
    )
    assert (
        result["scores"][
            variant["variant_id"]
        ]["variant_state"]
        == "WINNER"
    )
    assert (
        result["scores"][
            control["variant_id"]
        ]["variant_state"]
        == "LOSER"
    )

    learning = store.learn(
        experiment["experiment_id"],
        result,
    )
    assert learning["what_worked"]

    report = store.report(
        campaign["campaign_id"]
    )
    assert report["learnings"]


def test_research_source_unavailable_does_not_fabricate(
    tmp_path,
):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )
    campaign = _campaign(store)

    def fake_execute(name, arguments):
        del arguments
        assert name == "reach_web_search"
        return {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": "offline",
                }
            ],
        }

    result = handle_ace_tool(
        store,
        "ace_research",
        {
            "campaign_id": (
                campaign["campaign_id"]
            )
        },
        execute_tool=fake_execute,
    )

    assert result["sources_retrieved"] == 0
    assert result["findings"] == []
    assert result["errors"]


def test_research_records_research_engine_provenance(
    tmp_path,
):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )
    research = ResearchStore(
        str(tmp_path / "research.db")
    )
    campaign = _campaign(store)

    def fake_execute(name, arguments):
        del arguments
        if name == "reach_web_search":
            return {
                "isError": False,
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Result "
                            "https://example.com/post"
                        ),
                    }
                ],
            }
        if name == "reach_retrieve_url":
            return {
                "isError": False,
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "How do shoppers compare "
                            "price? Watch this demo "
                            "and try it."
                        ),
                    }
                ],
            }
        raise AssertionError(name)

    result = handle_ace_tool(
        store,
        "ace_research",
        {
            "campaign_id": (
                campaign["campaign_id"]
            ),
            "max_sources": 1,
        },
        research_store=research,
        execute_tool=fake_execute,
    )

    assert result["sources_retrieved"] == 1
    assert result["research_run_id"]
    finding = result["findings"][0]
    assert finding["source_url"] == (
        "https://example.com/post"
    )
    assert finding["evidence_ids"]
    assert "question_hook" in (
        finding["metadata"]["signals"]
    )
    ledger = research.export_ledger_rows(
        result["research_run_id"]
    )
    assert ledger


def test_generate_creates_required_mvp_outputs(
    tmp_path,
):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )
    campaign = store.create_campaign(
        "Market a local shopping app in Iraq",
        platforms=[
            "instagram",
            "tiktok",
        ],
        free_only=True,
    )
    store.add_finding(
        campaign["campaign_id"],
        kind="OBSERVATION",
        summary="observed signals",
        source_ref="src",
        source_url="https://example.com",
        metadata={
            "signals": [
                "question_hook",
                "demonstration",
                "local_language",
            ]
        },
    )

    result = handle_ace_tool(
        store,
        "ace_generate",
        {
            "campaign_id": (
                campaign["campaign_id"]
            )
        },
    )

    assert len(result["hypotheses"]) == 5
    assert len(result["personas"]) == 3
    assert (
        len(result["content_concepts"])
        == 10
    )
    assert (
        len(
            result[
                "selected_initial_experiments"
            ]
        )
        == 3
    )
    assert result["publishing_packages"]
    assert (
        result["creative_router"][
            "media_generated"
        ]
        is False
    )
    assert all(
        item["status"] == "PACKAGE_READY"
        for item in result[
            "publishing_packages"
        ]
    )


def test_case_study_never_claims_winner_without_analytics(
    tmp_path,
):
    store = ACEStore(
        str(tmp_path / "ace.db")
    )

    def fake_execute(name, arguments):
        del arguments
        if name == "reach_web_search":
            return {
                "isError": False,
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "https://example.com/source"
                        ),
                    }
                ],
            }
        if name == "reach_retrieve_url":
            return {
                "isError": False,
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Question? product demo "
                            "price comparison Iraq"
                        ),
                    }
                ],
            }
        raise AssertionError(name)

    result = handle_ace_tool(
        store,
        "ace_case_study",
        {
            "goal": (
                "Market a shopping app in Iraq"
            ),
            "platforms": [
                "instagram",
                "tiktok",
            ],
            "free_only": True,
        },
        execute_tool=fake_execute,
    )

    assert result["evaluation_state"] == (
        "NEEDS_MORE_DATA"
    )
    assert (
        "will not invent a winner"
        in result["evaluation_reason"]
    )
    assert (
        len(
            result["generation"][
                "hypotheses"
            ]
        )
        == 5
    )
