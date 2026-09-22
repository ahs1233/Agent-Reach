from __future__ import annotations

import pytest

from agent_reach.toolbox.research import ResearchStore


def _source(
    store: ResearchStore,
    run_id: str,
    *,
    slug: str,
    publisher: str,
    content: str,
    family: str | None = None,
):
    return store.record_source(
        run_id,
        url=f"https://{slug}.example.com/article",
        content=content,
        publisher=publisher,
        source_type="test",
        primary_source=True,
        retrieval_tool="fixture",
        retrieval_method="deterministic",
        retrieval_status="SUCCESS",
        source_family_id=family,
    )


def _evidence(store: ResearchStore, run_id: str, source_id: str, label: str):
    return store.add_evidence(
        run_id,
        source_id=source_id,
        supporting_passage=f"Support {label}.",
        observation_type="ACTUAL",
    )


def _claim(
    store: ResearchStore,
    run_id: str,
    evidence_ids: list[str],
):
    return store.add_claim(
        run_id,
        statement="The event occurred.",
        classification="VERIFIED",
        observation_type="ACTUAL",
        supporting_evidence_ids=evidence_ids,
    )


def test_ten_urls_from_one_publisher_count_as_one_lineage() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Do ten URLs equal ten sources?")
    evidence_ids = []

    for index in range(10):
        source = _source(
            store,
            run["run_id"],
            slug=f"same-publisher-{index}",
            publisher="Wire Service",
            content=f"distinct page representation {index}",
        )
        evidence = _evidence(
            store,
            run["run_id"],
            source["source_id"],
            str(index),
        )
        evidence_ids.append(evidence["evidence_id"])

    claim = _claim(store, run["run_id"], evidence_ids)
    evaluation = store.evaluate_claim_source_independence(claim["claim_id"])

    assert evaluation["supporting_source_count"] == 10
    assert evaluation["supporting_url_count"] == 10
    assert evaluation["effective_lineage_count"] == 1
    assert evaluation["duplicate_or_dependency_reduction"] == 9
    assert evaluation["status"] == "SINGLE_LINEAGE"
    assert evaluation["strict_confidence_basis_source_count"] == 1
    assert evaluation["lineages"][0]["grouping_reasons"] == ["SAME_PUBLISHER"]


def test_different_publishers_are_not_automatically_called_independent() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Are different outlets automatically independent?")
    evidence_ids = []
    for index, publisher in enumerate(("Publisher A", "Publisher B", "Publisher C")):
        source = _source(
            store,
            run["run_id"],
            slug=f"publisher-{index}",
            publisher=publisher,
            content=f"unique content {index}",
        )
        evidence_ids.append(
            _evidence(
                store,
                run["run_id"],
                source["source_id"],
                publisher,
            )["evidence_id"]
        )

    claim = _claim(store, run["run_id"], evidence_ids)
    evaluation = store.evaluate_claim_source_independence(claim["claim_id"])

    assert evaluation["effective_lineage_count"] == 3
    assert evaluation["status"] == "MULTIPLE_LINEAGES_UNVERIFIED"
    assert evaluation["verified_independent_pair_count"] == 0
    assert evaluation["strict_confidence_basis_source_count"] == 1


def test_explicit_dependency_collapses_different_publishers() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Is a copied article an independent source?")
    primary = _source(
        store,
        run["run_id"],
        slug="primary",
        publisher="Original Agency",
        content="original reporting",
    )
    derivative = _source(
        store,
        run["run_id"],
        slug="derivative",
        publisher="Local Outlet",
        content="rewritten reporting",
    )
    e1 = _evidence(store, run["run_id"], primary["source_id"], "primary")
    e2 = _evidence(store, run["run_id"], derivative["source_id"], "copy")
    relationship = store.record_source_relationship(
        run["run_id"],
        source_id=derivative["source_id"],
        related_source_id=primary["source_id"],
        relationship_type="DERIVED_FROM",
        basis="Article attributes the factual report to the original agency.",
    )
    claim = _claim(
        store,
        run["run_id"],
        [e1["evidence_id"], e2["evidence_id"]],
    )

    evaluation = store.evaluate_claim_source_independence(claim["claim_id"])

    assert relationship["relationship_type"] == "DERIVED_FROM"
    assert evaluation["effective_lineage_count"] == 1
    assert evaluation["status"] == "SINGLE_LINEAGE"
    assert "EXPLICIT_DERIVED_FROM" in evaluation["lineages"][0]["grouping_reasons"]


def test_identical_representation_hash_collapses_cross_publisher_duplicates() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Are identical mirrors independent?")
    left = _source(
        store,
        run["run_id"],
        slug="mirror-a",
        publisher="Outlet A",
        content="identical syndicated text",
    )
    right = _source(
        store,
        run["run_id"],
        slug="mirror-b",
        publisher="Outlet B",
        content="identical syndicated text",
    )
    e1 = _evidence(store, run["run_id"], left["source_id"], "a")
    e2 = _evidence(store, run["run_id"], right["source_id"], "b")
    claim = _claim(
        store,
        run["run_id"],
        [e1["evidence_id"], e2["evidence_id"]],
    )

    evaluation = store.evaluate_claim_source_independence(claim["claim_id"])

    assert evaluation["effective_lineage_count"] == 1
    assert "IDENTICAL_REPRESENTATION_HASH" in (
        evaluation["lineages"][0]["grouping_reasons"]
    )


def test_pairwise_independence_must_be_explicit_for_verified_status() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Can independence be verified?")
    sources = []
    evidence_ids = []
    for index, publisher in enumerate(("Alpha", "Beta", "Gamma")):
        source = _source(
            store,
            run["run_id"],
            slug=f"independent-{index}",
            publisher=publisher,
            content=f"independent representation {index}",
        )
        sources.append(source)
        evidence_ids.append(
            _evidence(
                store,
                run["run_id"],
                source["source_id"],
                publisher,
            )["evidence_id"]
        )

    for left, right in ((0, 1), (0, 2), (1, 2)):
        store.record_source_relationship(
            run["run_id"],
            source_id=sources[left]["source_id"],
            related_source_id=sources[right]["source_id"],
            relationship_type="INDEPENDENT_OF",
            basis="Independent primary collection was established.",
        )

    claim = _claim(store, run["run_id"], evidence_ids)
    evaluation = store.evaluate_claim_source_independence(claim["claim_id"])

    assert evaluation["status"] == "VERIFIED_INDEPENDENT_LINEAGES"
    assert evaluation["effective_lineage_count"] == 3
    assert evaluation["verified_independent_pair_count"] == 3
    assert evaluation["total_lineage_pair_count"] == 3
    assert evaluation["strict_confidence_basis_source_count"] == 3


def test_partial_independence_does_not_raise_strict_confidence_basis() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Partial independence")
    sources = []
    evidence_ids = []
    for index, publisher in enumerate(("Alpha", "Beta", "Gamma")):
        source = _source(
            store,
            run["run_id"],
            slug=f"partial-{index}",
            publisher=publisher,
            content=f"partial representation {index}",
        )
        sources.append(source)
        evidence_ids.append(
            _evidence(store, run["run_id"], source["source_id"], publisher)[
                "evidence_id"
            ]
        )

    store.record_source_relationship(
        run["run_id"],
        source_id=sources[0]["source_id"],
        related_source_id=sources[1]["source_id"],
        relationship_type="INDEPENDENT_OF",
        basis="One pair verified.",
    )
    claim = _claim(store, run["run_id"], evidence_ids)
    evaluation = store.evaluate_claim_source_independence(claim["claim_id"])

    assert evaluation["status"] == "PARTIALLY_VERIFIED_INDEPENDENCE"
    assert evaluation["verified_independent_pair_count"] == 1
    assert evaluation["strict_confidence_basis_source_count"] == 1


def test_independence_assertion_rejects_obvious_same_lineage_sources() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Reject contradictory independence")
    left = _source(
        store,
        run["run_id"],
        slug="same-org-a",
        publisher="Same Org",
        content="first",
    )
    right = _source(
        store,
        run["run_id"],
        slug="same-org-b",
        publisher="Same Org",
        content="second",
    )

    with pytest.raises(ValueError, match="cannot assert INDEPENDENT_OF"):
        store.record_source_relationship(
            run["run_id"],
            source_id=left["source_id"],
            related_source_id=right["source_id"],
            relationship_type="INDEPENDENT_OF",
        )


def test_source_relationships_are_run_scoped() -> None:
    store = ResearchStore(":memory:")
    run_a = store.create_run("A")
    run_b = store.create_run("B")
    left = _source(
        store,
        run_a["run_id"],
        slug="run-a",
        publisher="A",
        content="a",
    )
    right = _source(
        store,
        run_b["run_id"],
        slug="run-b",
        publisher="B",
        content="b",
    )

    with pytest.raises(ValueError, match="same run"):
        store.record_source_relationship(
            run_a["run_id"],
            source_id=left["source_id"],
            related_source_id=right["source_id"],
            relationship_type="DERIVED_FROM",
        )


def test_output_preserves_independence_snapshot_and_latest_state() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Historical source independence")
    left = _source(
        store,
        run["run_id"],
        slug="history-a",
        publisher="Alpha",
        content="alpha report",
    )
    right = _source(
        store,
        run["run_id"],
        slug="history-b",
        publisher="Beta",
        content="beta report",
    )
    e1 = _evidence(store, run["run_id"], left["source_id"], "alpha")
    e2 = _evidence(store, run["run_id"], right["source_id"], "beta")
    claim = _claim(
        store,
        run["run_id"],
        [e1["evidence_id"], e2["evidence_id"]],
    )

    initial = store.evaluate_claim_source_independence(claim["claim_id"])
    output = store.create_output(
        run["run_id"],
        consumer_type="answer",
        output_type="ANSWER",
        fragments=[
            {
                "content": "The event occurred.",
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": "ACTUAL",
            }
        ],
    )

    store.record_source_relationship(
        run["run_id"],
        source_id=left["source_id"],
        related_source_id=right["source_id"],
        relationship_type="INDEPENDENT_OF",
        basis="Independent collection later verified.",
    )
    later = store.evaluate_claim_source_independence(claim["claim_id"])
    resolved = store.get_output(output["output_id"])
    provenance = resolved["fragments"][0]["provenance_refs"][0]

    assert initial["status"] == "MULTIPLE_LINEAGES_UNVERIFIED"
    assert later["status"] == "VERIFIED_INDEPENDENT_LINEAGES"
    assert provenance["source_independence_at_output"]["independence_id"] == (
        initial["independence_id"]
    )
    assert provenance["source_independence_at_output"]["status"] == (
        "MULTIPLE_LINEAGES_UNVERIFIED"
    )
    assert provenance["latest_source_independence"]["independence_id"] == (
        later["independence_id"]
    )
    assert provenance["latest_source_independence"]["status"] == (
        "VERIFIED_INDEPENDENT_LINEAGES"
    )


def test_export_includes_relationships_and_latest_claim_evaluation() -> None:
    store = ResearchStore(":memory:")
    run = store.create_run("Export source independence")
    left = _source(
        store,
        run["run_id"],
        slug="export-a",
        publisher="Alpha",
        content="alpha",
    )
    right = _source(
        store,
        run["run_id"],
        slug="export-b",
        publisher="Beta",
        content="beta",
    )
    e1 = _evidence(store, run["run_id"], left["source_id"], "a")
    e2 = _evidence(store, run["run_id"], right["source_id"], "b")
    store.record_source_relationship(
        run["run_id"],
        source_id=left["source_id"],
        related_source_id=right["source_id"],
        relationship_type="INDEPENDENT_OF",
        basis="Verified separately.",
    )
    claim = _claim(
        store,
        run["run_id"],
        [e1["evidence_id"], e2["evidence_id"]],
    )
    evaluation = store.evaluate_claim_source_independence(claim["claim_id"])

    exported = store.export_run(run["run_id"])

    assert len(exported["source_relationships"]) == 1
    assert exported["claims"][0]["latest_source_independence"][
        "independence_id"
    ] == evaluation["independence_id"]
