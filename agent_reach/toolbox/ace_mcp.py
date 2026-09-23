"""MCP surface and workflow composition for Ahmed Content Experiment Engine."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .ace import (
    ACEStore,
    CostAwareRouter,
    analyze_content_features,
    hashtag_terms,
    infer_content_signals,
    structured_script,
    validate_content_claims,
)
from .research import ResearchStore

ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


def ace_tool_specs() -> list[dict[str, Any]]:
    """Expose ACE as a small task-oriented API instead of separate scripts."""
    return [
        {
            "name": "ace_status",
            "description": (
                "Report ACE availability, FREE_ONLY state, creative routing, "
                "publishing honesty, and analytics behavior."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "ace_create_campaign",
            "description": "Create a traceable content experiment campaign.",
            "inputSchema": {
                "type": "object",
                "required": ["goal"],
                "properties": {
                    "goal": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 5000,
                    },
                    "objective": {
                        "type": "string",
                        "default": "balanced",
                    },
                    "platforms": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "instagram",
                                "tiktok",
                                "youtube_shorts",
                            ],
                        },
                    },
                    "free_only": {
                        "type": "boolean",
                        "default": True,
                    },
                    "metadata": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "ace_research",
            "description": (
                "Run real public research through existing Reach/Browser "
                "retrieval and record Research Engine provenance."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["campaign_id"],
                "properties": {
                    "campaign_id": {"type": "string"},
                    "queries": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {"type": "string"},
                    },
                    "max_sources": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 12,
                        "default": 6,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "ace_generate",
            "description": (
                "Generate evidence-aware personas, hypotheses, concepts, "
                "scripts, experiments, assets, and publishing packages."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["campaign_id"],
                "properties": {
                    "campaign_id": {"type": "string"},
                    "target_audience": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "ace_record_metrics",
            "description": (
                "Record a platform analytics snapshot for one variant. "
                "Missing metrics remain missing."
            ),
            "inputSchema": {
                "type": "object",
                "required": [
                    "experiment_id",
                    "variant_id",
                    "platform",
                    "metrics",
                ],
                "properties": {
                    "experiment_id": {"type": "string"},
                    "variant_id": {"type": "string"},
                    "platform": {"type": "string"},
                    "metrics": {"type": "object"},
                    "age_hours": {
                        "type": "number",
                        "minimum": 0,
                        "default": 24,
                    },
                    "account_size": {
                        "type": "integer",
                        "minimum": 0,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "ace_evaluate",
            "description": (
                "Evaluate variants with objective weights, sample-size "
                "guardrails, and statistical uncertainty."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["experiment_id"],
                "properties": {
                    "experiment_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "ace_learn",
            "description": (
                "Persist what worked, failed, changed, remains uncertain, "
                "and what should be tested next."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["experiment_id"],
                "properties": {
                    "experiment_id": {"type": "string"},
                    "evaluation": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "ace_next",
            "description": (
                "Propose the next isolated-variable experiment from "
                "prior learning."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["campaign_id"],
                "properties": {
                    "campaign_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "ace_report",
            "description": (
                "Return campaign research, experiments, evaluations, "
                "learnings, provenance references, and cost."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["campaign_id"],
                "properties": {
                    "campaign_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "ace_case_study",
            "description": (
                "Run an honest end-to-end ACE planning case: real research, "
                "patterns, hypotheses, scripts, experiments, publishing "
                "packages, and measurement design."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["goal"],
                "properties": {
                    "goal": {"type": "string"},
                    "objective": {
                        "type": "string",
                        "default": "balanced",
                    },
                    "platforms": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "instagram",
                                "tiktok",
                                "youtube_shorts",
                            ],
                        },
                    },
                    "free_only": {
                        "type": "boolean",
                        "default": True,
                    },
                },
                "additionalProperties": False,
            },
        },
    ]


def _text(result: dict[str, Any]) -> str:
    if bool(result.get("isError")):
        return ""
    blocks = result.get("content") or []
    return "\n".join(
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "text"
    ).strip()


def _urls(text: str) -> list[str]:
    matches = re.findall(
        r"https?://[^\s\]\[)<>{}]+",
        text or "",
    )
    out: list[str] = []
    for item in matches:
        cleaned = item.rstrip(".,;:'\"")
        if cleaned not in out:
            out.append(cleaned)
    return out


def _passage(text: str, limit: int = 900) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    return compact[:limit]


def _retrieve_source(
    execute_tool: ToolExecutor,
    url: str,
) -> tuple[str, str, str]:
    lower = url.lower()
    if (
        "youtube.com/" in lower
        or "youtu.be/" in lower
    ):
        result = execute_tool(
            "reach_youtube_browser_inspect",
            {
                "url": url,
                "include_comments": True,
                "max_comments": 8,
                "timeout_seconds": 45,
            },
        )
        body = _text(result)
        if body:
            return (
                body,
                "reach_youtube_browser_inspect",
                "browser-use rendered page plus bounded comments",
            )

    result = execute_tool(
        "reach_retrieve_url",
        {
            "url": url,
            "max_chars": 30000,
            "min_chars": 200,
        },
    )
    body = _text(result)
    return (
        body,
        "reach_retrieve_url",
        "controlled fallback retrieval",
    )


def _default_queries(goal: str) -> list[str]:
    return [
        (
            f"{goal} competitors customer pain points "
            "market alternatives Iraq"
        ),
        (
            f"{goal} Instagram TikTok YouTube Shorts "
            "content hooks demonstrations comments"
        ),
        (
            f"{goal} audience objections recurring questions "
            "reviews buying behavior"
        ),
        (
            f"{goal} short form video price comparison "
            "product demo founder content"
        ),
        (
            f"{goal} influential creators accounts "
            "Instagram TikTok YouTube audience"
        ),
        (
            f"{goal} business model monetization methods "
            "subscriptions commissions advertising"
        ),
    ]


def _research(
    store: ACEStore,
    research_store: ResearchStore | None,
    execute_tool: ToolExecutor | None,
    campaign_id: str,
    queries: list[str] | None,
    max_sources: int,
) -> dict[str, Any]:
    if execute_tool is None:
        raise ValueError(
            "ACE research requires the Ahmed Toolbox retrieval executor"
        )

    campaign = store.get_campaign(campaign_id)
    goal = campaign["goal"]
    selected_queries = queries or _default_queries(goal)

    research_run: dict[str, Any] | None = None
    if research_store is not None:
        research_run = research_store.create_run(
            f"ACE research for campaign {campaign_id}: {goal}",
            metadata={
                "consumer": "ACE",
                "campaign_id": campaign_id,
            },
        )
        store.set_research_run(
            campaign_id,
            research_run["run_id"],
        )

    discovered: list[str] = []
    search_observations: list[dict[str, Any]] = []
    errors: list[str] = []

    for query in selected_queries:
        result = execute_tool(
            "reach_web_search",
            {
                "query": query,
                "num_results": 6,
            },
        )
        body = _text(result)
        if not body:
            errors.append(
                f"search unavailable: {query}"
            )
            continue

        found_urls = _urls(body)
        for url in found_urls:
            if url not in discovered:
                discovered.append(url)
        search_observations.append(
            {
                "query": query,
                "urls": found_urls[:6],
                "retrieval_method": "reach_web_search",
            }
        )

    findings: list[dict[str, Any]] = []
    for url in discovered[:max_sources]:
        body, retrieval_tool, retrieval_method = (
            _retrieve_source(
                execute_tool,
                url,
            )
        )
        if not body:
            errors.append(
                f"source unavailable: {url}"
            )
            continue

        evidence_ids: list[str] = []
        source_ref = url
        if (
            research_store is not None
            and research_run is not None
        ):
            source = research_store.record_source(
                research_run["run_id"],
                url=url,
                content=body,
                retrieval_tool=retrieval_tool,
                retrieval_method=retrieval_method,
                retrieval_status="SUCCESS",
                discovered_by="ace_research",
                metadata={
                    "campaign_id": campaign_id,
                    "consumer": "ACE",
                },
            )
            evidence = research_store.add_evidence(
                research_run["run_id"],
                source_id=source["source_id"],
                supporting_passage=_passage(body),
                structured_fact={
                    "content_signals": infer_content_signals(
                        body
                    ),
                    "content_intelligence": (
                        analyze_content_features(
                            body
                        )
                    ),
                    "source_url": url,
                    "source_scope": (
                        "retrieved public content"
                    ),
                },
                observation_type="ACTUAL",
                extraction_method=(
                    "ACE deterministic content signal extraction"
                ),
            )
            evidence_ids.append(
                evidence["evidence_id"]
            )
            source_ref = evidence["evidence_id"]

        finding = store.add_finding(
            campaign_id,
            kind="OBSERVATION",
            summary=_passage(body, 420),
            source_ref=source_ref,
            source_url=url,
            retrieval_method=retrieval_tool,
            confidence=(
                0.60 if evidence_ids else 0.40
            ),
            evidence_ids=evidence_ids,
            metadata={
                "signals": infer_content_signals(
                    body
                ),
                "content_intelligence": (
                    analyze_content_features(
                        body
                    )
                ),
                "sample_scope": (
                    "retrieved public source"
                ),
                "retrieval_method_detail": (
                    retrieval_method
                ),
            },
        )
        findings.append(finding)

    if (
        research_store is not None
        and research_run is not None
    ):
        research_store.complete_run(
            research_run["run_id"]
        )

    return {
        "campaign_id": campaign_id,
        "research_run_id": (
            research_run["run_id"]
            if research_run
            else None
        ),
        "queries": selected_queries,
        "search_observations": search_observations,
        "sources_discovered": len(discovered),
        "sources_retrieved": len(findings),
        "findings": findings,
        "patterns": store.derive_content_patterns(
            campaign_id
        ),
        "errors": errors,
        "truthfulness": (
            "No source, performance number, comment, or metric "
            "is invented. Unavailable sources remain errors."
        ),
    }


def _personas(
    store: ACEStore,
    campaign_id: str,
    audience: str,
) -> list[dict[str, Any]]:
    common_prohibited = [
        "fabricated testimonials",
        "fake credentials",
        "unverified superiority",
        "misleading financial or medical claims",
    ]
    return [
        store.create_persona(
            campaign_id,
            persona_type="BRAND_ACCOUNT",
            identity={
                "name": "Brand explainer",
                "disclosure": (
                    "official brand creative"
                ),
            },
            audience={
                "description": audience,
            },
            voice={
                "style": "clear, useful, concise",
            },
            visual_language={
                "style": (
                    "product-first demonstrations"
                ),
            },
            content_pillars=[
                "problem",
                "demo",
                "proof",
            ],
            allowed_claims=[
                "verifiable product facts",
            ],
            prohibited_claims=common_prohibited,
            cta_style="single measurable action",
            consistency_rules=[
                "same core promise",
                "no fake authority",
            ],
        ),
        store.create_persona(
            campaign_id,
            persona_type="FOUNDER_ACCOUNT",
            identity={
                "name": "Founder/team voice",
                "disclosure": (
                    "use only when the speaker is "
                    "factually part of the team"
                ),
            },
            audience={
                "description": audience,
            },
            voice={
                "style": (
                    "direct, first-person, transparent"
                ),
            },
            visual_language={
                "style": (
                    "human explanation plus "
                    "product evidence"
                ),
            },
            content_pillars=[
                "why",
                "build",
                "customer problem",
            ],
            allowed_claims=[
                "first-party facts",
            ],
            prohibited_claims=common_prohibited + [
                "invented biography",
            ],
            cta_style="invite trial or feedback",
            consistency_rules=[
                "do not impersonate a founder "
                "who is not present",
            ],
        ),
        store.create_persona(
            campaign_id,
            persona_type="FACELESS_ACCOUNT",
            identity={
                "name": "Faceless demo",
                "disclosure": "brand creative",
            },
            audience={
                "description": audience,
            },
            voice={
                "style": "fast visual proof",
            },
            visual_language={
                "style": (
                    "screen/product demo, captions, "
                    "no fabricated human"
                ),
            },
            content_pillars=[
                "demo",
                "comparison",
                "tips",
            ],
            allowed_claims=[
                "observable demonstrations",
            ],
            prohibited_claims=common_prohibited + [
                "fabricated human experience",
            ],
            cta_style=(
                "show next action on screen"
            ),
            consistency_rules=[
                "visual continuity",
                "source proof where relevant",
            ],
        ),
    ]


def _hypotheses(
    store: ACEStore,
    campaign_id: str,
    audience: str,
    pattern_names: set[str],
    evidence_refs: list[str],
    goal: str,
) -> list[dict[str, Any]]:
    local_context = any(
        token in goal.lower()
        for token in (
            "iraq",
            "iraqi",
            "العراق",
            "السُوگ",
            "السوق",
        )
    )
    local_label = (
        "Iraqi local dialect"
        if local_context
        else "local audience language"
    )
    definitions = [
        (
            "hook_structure",
            {"hook": "feature explanation"},
            {
                "hook": (
                    "problem/solution question"
                )
            },
            (
                "Problem/solution hooks improve "
                "retention versus feature-first intros."
            ),
            "completion_rate",
            {"question_hook", "problem_solution"},
        ),
        (
            "language_style",
            {"language": "formal/standard"},
            {"language": local_label},
            (
                f"{local_label} improves retention "
                "and comments without reducing intent."
            ),
            "completion_rate",
            {"local_language"},
        ),
        (
            "value_frame",
            {"frame": "generic benefit"},
            {
                "frame": (
                    "concrete price/value comparison"
                )
            },
            (
                "Concrete value comparison increases "
                "saves and link intent."
            ),
            "saves",
            {"price_comparison"},
        ),
        (
            "proof_format",
            {"format": "explanation"},
            {"format": "demonstration"},
            (
                "Demonstration outperforms explanation "
                "on completion and shares."
            ),
            "completion_rate",
            {"demonstration"},
        ),
        (
            "speaker_mode",
            {"speaker": "brand"},
            {
                "speaker": (
                    "transparent founder/team voice"
                )
            },
            (
                "Transparent founder/team voice "
                "increases profile visits."
            ),
            "profile_visits",
            set(),
        ),
    ]

    result: list[dict[str, Any]] = []
    for (
        variable,
        control,
        variant,
        expected,
        primary_metric,
        matching_signals,
    ) in definitions:
        matches = len(
            matching_signals & pattern_names
        )
        confidence = min(
            0.75,
            0.45 + 0.10 * matches,
        )
        result.append(
            store.create_hypothesis(
                campaign_id,
                target_audience=audience,
                variable=variable,
                control=control,
                variant=variant,
                expected_outcome=expected,
                primary_metric=primary_metric,
                secondary_metrics=[
                    "shares",
                    "saves",
                    "profile_visits",
                    "link_clicks",
                    "conversions",
                ],
                minimum_evidence=200,
                confidence=confidence,
                evidence_refs=evidence_refs,
            )
        )
    return result


def _concepts(
    goal: str,
) -> list[dict[str, Any]]:
    templates = [
        (
            "Problem in 2 seconds",
            "problem_solution",
        ),
        (
            "Show the product, not a lecture",
            "demonstration",
        ),
        (
            "Price/value comparison",
            "price_comparison",
        ),
        (
            "One objection answered",
            "objection",
        ),
        (
            "Founder why-now",
            "founder",
        ),
        (
            "Three-step how-to",
            "education",
        ),
        (
            "Myth vs reality",
            "education",
        ),
        (
            "Before/after workflow",
            "demonstration",
        ),
        (
            "Local humor around the pain point",
            "entertainment",
        ),
        (
            "Customer question -> visual answer",
            "question_hook",
        ),
    ]
    return [
        {
            "concept_id": f"concept_{index:02d}",
            "title": title,
            "pattern": pattern,
            "goal": goal,
            "causation_claimed": False,
        }
        for index, (title, pattern) in enumerate(
            templates,
            1,
        )
    ]


def _script_pair(
    goal: str,
    hypothesis: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    variable = hypothesis["variable"]
    control_value = next(
        iter(hypothesis["control"].values())
    )
    variant_value = next(
        iter(hypothesis["variant"].values())
    )

    control = structured_script(
        f"{control_value}: {goal}",
        "State the context in one sentence.",
        "Explain one concrete benefit.",
        (
            "Use only verifiable product, screen, "
            "or sourced evidence."
        ),
        "Try the product or visit the profile.",
    )
    variant = structured_script(
        f"{variant_value}: {goal}",
        (
            "Make the audience pain point explicit "
            "without changing the offer."
        ),
        "Deliver one concrete useful takeaway.",
        (
            "Show a real demonstration or cite the "
            "source; do not fabricate proof."
        ),
        "Take one measurable next action.",
    )
    control["experiment_variable"] = variable
    variant["experiment_variable"] = variable
    return control, variant


def _generate(
    store: ACEStore,
    campaign_id: str,
    target_audience: str | None,
) -> dict[str, Any]:
    campaign = store.get_campaign(campaign_id)
    goal = campaign["goal"]
    audience = target_audience or (
        "People most likely to experience the "
        "problem described by the campaign goal"
    )

    patterns = store.derive_content_patterns(
        campaign_id
    )
    findings = store.list_findings(campaign_id)
    evidence_refs = [
        evidence_id
        for finding in findings
        for evidence_id in finding["evidence_ids"]
    ] or [
        finding["finding_id"]
        for finding in findings
    ]
    pattern_names = {
        item["pattern"]
        for item in patterns
    }

    personas = _personas(
        store,
        campaign_id,
        audience,
    )
    hypotheses = _hypotheses(
        store,
        campaign_id,
        audience,
        pattern_names,
        evidence_refs,
        goal,
    )
    concepts = _concepts(goal)

    ranked = sorted(
        hypotheses,
        key=lambda item: (
            item["confidence"],
            item["hypothesis_id"],
        ),
        reverse=True,
    )
    router = CostAwareRouter()

    experiments: list[dict[str, Any]] = []
    publishing_packages: list[
        dict[str, Any]
    ] = []

    for hypothesis in ranked[:3]:
        experiment = store.create_experiment(
            campaign_id,
            hypothesis,
            name=(
                f"Test {hypothesis['variable']}"
            ),
        )
        variable = hypothesis["variable"]
        persona_id = personas[0]["persona_id"]
        if variable == "speaker_mode":
            persona_id = personas[1]["persona_id"]
        elif variable == "proof_format":
            persona_id = personas[2]["persona_id"]

        control_script, variant_script = (
            _script_pair(
                goal,
                hypothesis,
            )
        )
        variants = [
            store.add_variant(
                experiment["experiment_id"],
                label="CONTROL",
                is_control=True,
                persona_id=persona_id,
                script=control_script,
                variable_value=(
                    hypothesis["control"]
                ),
                rationale=(
                    "Baseline for isolated variable "
                    f"{variable}."
                ),
            ),
            store.add_variant(
                experiment["experiment_id"],
                label="VARIANT_A",
                is_control=False,
                persona_id=persona_id,
                script=variant_script,
                variable_value=(
                    hypothesis["variant"]
                ),
                rationale=(
                    "Changes only the main tested "
                    f"variable: {variable}."
                ),
            ),
        ]
        experiment["variants"] = variants
        experiments.append(experiment)

        for variant in variants:
            provider = router.route(
                "storyboard",
                free_only=campaign["free_only"],
            )
            manifest = provider.generate(
                "storyboard",
                (
                    "Create a short-form storyboard "
                    f"for: {goal}"
                ),
                {
                    "script": variant["script"],
                    "platforms": campaign["platforms"],
                    "experiment_id": (
                        experiment["experiment_id"]
                    ),
                    "variant_id": (
                        variant["variant_id"]
                    ),
                },
            )
            asset = store.add_asset(
                campaign_id,
                variant_id=variant["variant_id"],
                kind="storyboard",
                provider=provider.spec.name,
                payload=manifest,
                estimated_cost=(
                    provider.spec.estimated_cost
                ),
            )

            for platform in campaign["platforms"]:
                package = {
                    "platform": platform,
                    "experiment_id": (
                        experiment["experiment_id"]
                    ),
                    "variant_id": (
                        variant["variant_id"]
                    ),
                    "script": variant["script"],
                    "caption": (
                        f"{variant['script']['value']} "
                        f"{variant['script']['cta']}"
                    ),
                    "hashtags": hashtag_terms(goal),
                    "thumbnail": {
                        "text": (
                            variant["script"]["hook"][
                                :80
                            ]
                        ),
                        "provider": "local_manifest",
                        "generated_media": False,
                    },
                    "video_generation": {
                        "status": (
                            "PROVIDER_REQUIRED"
                        ),
                        "storyboard_asset_id": (
                            asset["asset_id"]
                        ),
                    },
                    "voice_generation": {
                        "status": (
                            "PROVIDER_REQUIRED"
                        ),
                        "voice_script": (
                            variant["script"]
                        ),
                    },
                    "recommended_posting_window": (
                        "Rotate variants across matched "
                        "time blocks; do not assume one "
                        "universal best posting hour."
                    ),
                    "publishing_status": (
                        "PACKAGE_READY"
                    ),
                    "note": (
                        "ACE has not published this "
                        "package. Use an official "
                        "platform API or manual "
                        "publishing."
                    ),
                }
                safety = validate_content_claims(
                    json.dumps(
                        package,
                        ensure_ascii=False,
                    )
                )
                if not safety["allowed"]:
                    raise ValueError(
                        "publishing package blocked "
                        "by safety guardrails"
                    )
                publishing_packages.append(
                    store.create_publication_package(
                        experiment[
                            "experiment_id"
                        ],
                        variant,
                        platform=platform,
                        package=package,
                    )
                )

    return {
        "campaign_id": campaign_id,
        "target_audience": audience,
        "patterns": patterns,
        "hypotheses": hypotheses,
        "personas": personas,
        "content_concepts": concepts,
        "selected_initial_experiments": (
            experiments
        ),
        "publishing_packages": (
            publishing_packages
        ),
        "metrics_plan": {
            "retention": [
                "watch_time",
                "average_view_duration",
                "completion_rate",
            ],
            "engagement": [
                "likes",
                "comments",
                "shares",
                "saves",
            ],
            "intent": [
                "profile_visits",
                "link_clicks",
                "followers_gained",
            ],
            "conversion": [
                "conversions",
                "revenue",
            ],
            "winner_rule": (
                "Normalized objective score + "
                "minimum sample + conservative "
                "uncertainty. INCONCLUSIVE is valid."
            ),
        },
        "creative_router": {
            "free_only": campaign["free_only"],
            "selected_provider": (
                "local_manifest"
            ),
            "media_generated": False,
            "provider_contract": (
                "CreativeProvider capability/cost/"
                "quality/latency/quota interface"
            ),
            "note": (
                "Real image/video/TTS bytes require "
                "a configured compatible provider. "
                "ACE does not fake them."
            ),
        },
    }


def _next(
    store: ACEStore,
    campaign_id: str,
) -> dict[str, Any]:
    learning = store.latest_learning(
        campaign_id
    )
    if learning is None:
        return {
            "campaign_id": campaign_id,
            "status": "NEEDS_PRIOR_EVALUATION",
            "next_experiment": (
                "Run an initial experiment and "
                "record analytics before narrowing "
                "the hypothesis."
            ),
        }
    suggestions = (
        learning["what_to_test_next"]
    )
    return {
        "campaign_id": campaign_id,
        "status": "READY",
        "based_on_learning_id": (
            learning["learning_id"]
        ),
        "next_experiment": (
            suggestions[0]
            if suggestions
            else "Refine the winning variable."
        ),
        "rule": (
            "Change one main variable and preserve "
            "the prior winner as control."
        ),
    }


def handle_ace_tool(
    store: ACEStore,
    name: str,
    arguments: dict[str, Any],
    *,
    research_store: ResearchStore | None = None,
    execute_tool: ToolExecutor | None = None,
) -> dict[str, Any] | None:
    if not name.startswith("ace_"):
        return None

    if name == "ace_status":
        provider = CostAwareRouter().route(
            "storyboard",
            free_only=True,
        )
        return {
            "schema": "ace-status/v1",
            "status": "ok",
            "free_only_supported": True,
            "provider": provider.spec.to_dict(),
            "routing_order": [
                "LOCAL",
                "OPEN_SOURCE",
                "FREE_API",
                "FREE_TIER",
                "PAID_API",
            ],
            "creative_capabilities": {
                "built_in_free": [
                    "script",
                    "caption",
                    "storyboard",
                    "thumbnail_spec",
                    "publishing_package",
                ],
                "pluggable": [
                    "image",
                    "video",
                    "voice",
                    "captions",
                    "thumbnails",
                ],
            },
            "publishing": (
                "package-only unless an official "
                "platform API is configured"
            ),
            "analytics_truthfulness": (
                "missing metrics remain missing; "
                "no synthetic winner"
            ),
        }

    if name == "ace_create_campaign":
        return store.create_campaign(
            arguments.get("goal", ""),
            objective=str(
                arguments.get("objective")
                or "balanced"
            ),
            platforms=arguments.get(
                "platforms"
            ),
            free_only=bool(
                arguments.get(
                    "free_only",
                    True,
                )
            ),
            metadata=(
                arguments.get("metadata")
                or {}
            ),
        )

    if name == "ace_research":
        return _research(
            store,
            research_store,
            execute_tool,
            str(arguments["campaign_id"]),
            arguments.get("queries"),
            int(
                arguments.get(
                    "max_sources",
                    6,
                )
            ),
        )

    if name == "ace_generate":
        return _generate(
            store,
            str(arguments["campaign_id"]),
            arguments.get(
                "target_audience"
            ),
        )

    if name == "ace_record_metrics":
        return store.record_metrics(
            str(arguments["experiment_id"]),
            str(arguments["variant_id"]),
            platform=str(
                arguments["platform"]
            ),
            metrics=dict(
                arguments["metrics"]
            ),
            age_hours=float(
                arguments.get(
                    "age_hours",
                    24,
                )
            ),
            account_size=arguments.get(
                "account_size"
            ),
        )

    if name == "ace_evaluate":
        return store.evaluate(
            str(arguments["experiment_id"])
        )

    if name == "ace_learn":
        evaluation = arguments.get(
            "evaluation"
        )
        if not evaluation:
            evaluation = store.evaluate(
                str(
                    arguments[
                        "experiment_id"
                    ]
                )
            )
        return store.learn(
            str(arguments["experiment_id"]),
            dict(evaluation),
        )

    if name == "ace_next":
        return _next(
            store,
            str(arguments["campaign_id"]),
        )

    if name == "ace_report":
        return store.report(
            str(arguments["campaign_id"])
        )

    if name == "ace_case_study":
        campaign = store.create_campaign(
            str(arguments["goal"]),
            objective=str(
                arguments.get("objective")
                or "balanced"
            ),
            platforms=arguments.get(
                "platforms"
            ),
            free_only=bool(
                arguments.get(
                    "free_only",
                    True,
                )
            ),
            metadata={
                "case_study": True,
            },
        )
        research = _research(
            store,
            research_store,
            execute_tool,
            campaign["campaign_id"],
            None,
            6,
        )
        generation = _generate(
            store,
            campaign["campaign_id"],
            None,
        )
        return {
            "schema": "ace-case-study/v1",
            "campaign": campaign,
            "research": research,
            "generation": generation,
            "evaluation_state": (
                "NEEDS_MORE_DATA"
            ),
            "evaluation_reason": (
                "No real post-publication analytics "
                "were supplied, so ACE will not "
                "invent a winner."
            ),
            "report": store.report(
                campaign["campaign_id"]
            ),
        }

    raise ValueError(
        f"unsupported ACE tool: {name}"
    )
