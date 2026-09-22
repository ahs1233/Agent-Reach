"""Efficiency and integrity benchmark for Ahmed Research Engine.

This benchmark is intentionally isolated from production. It measures the
current SQLite-backed core under representative Source -> Evidence -> Claim ->
Output workloads, exercises semantic-guard attack cases, checks provenance and
source versioning, stresses concurrent callers, and optionally repeats the live
Sprint 2 acquisition path.

Run:
    python -m agent_reach.toolbox.research_efficiency_benchmark
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from agent_reach.toolbox.research import OBSERVATION_TYPES, ResearchStore
from agent_reach.toolbox.sprint2_live_acceptance import run_acceptance


CORE_ITEMS = int(os.environ.get("AHMED_BENCH_CORE_ITEMS", "250"))
CONCURRENCY_WORKERS = int(os.environ.get("AHMED_BENCH_WORKERS", "8"))
CONCURRENCY_ITEMS_PER_WORKER = int(
    os.environ.get("AHMED_BENCH_ITEMS_PER_WORKER", "25")
)
LIVE_ITERATIONS = int(os.environ.get("AHMED_BENCH_LIVE_ITERATIONS", "3"))


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "min_ms": round(min(values), 3) if values else 0.0,
        "p50_ms": round(statistics.median(values), 3) if values else 0.0,
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
        "mean_ms": round(statistics.mean(values), 3) if values else 0.0,
    }


def _make_chain(
    store: ResearchStore,
    run_id: str,
    *,
    key: str,
    observation_type: str,
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    source = store.record_source(
        run_id,
        url=f"https://benchmark.local/source/{key}",
        content=f"benchmark-content:{key}:{observation_type}",
        publisher="Benchmark Fixture",
        source_type="benchmark",
        primary_source=True,
        publication_date="2026-09-22",
        data_cutoff="2026-09-22",
        retrieval_tool="benchmark",
        retrieval_method="synthetic",
        retrieval_status="SUCCESS",
    )
    evidence = store.add_evidence(
        run_id,
        source_id=source["source_id"],
        supporting_passage=f"Auditable benchmark observation {key}.",
        structured_fact={
            "benchmark_key": key,
            "observation_type": observation_type,
            "value": key,
        },
        metric="benchmark_metric",
        value=key,
        unit="synthetic",
        reference_period="2026-09-22",
        observation_type=observation_type,
        extraction_method="benchmark",
    )
    claim = store.add_claim(
        run_id,
        statement=f"Benchmark claim {key}.",
        classification="VERIFIED",
        observation_type=observation_type,
        supporting_evidence_ids=[evidence["evidence_id"]],
        confidence="HIGH",
    )
    output = store.create_output(
        run_id,
        consumer_type="benchmark_consumer",
        output_type="API_RESULT",
        fragments=[
            {
                "payload": {"benchmark_key": key},
                "claim_ids": [claim["claim_id"]],
                "asserted_observation_type": observation_type,
            }
        ],
    )
    audit = store.audit_output(output["output_id"])
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    fragment = output["fragments"][0]
    provenance = fragment["provenance_refs"][0]
    evidence_ref = provenance["evidence"][0]
    ok = (
        audit["passed"]
        and provenance["claim_id"] == claim["claim_id"]
        and evidence_ref["evidence_id"] == evidence["evidence_id"]
        and evidence_ref["source_id"] == source["source_id"]
        and evidence_ref["supporting_passage"] == evidence["supporting_passage"]
        and evidence_ref["content_hash_scope"] == "retrieved_representation"
    )
    return {
        "ok": ok,
        "source_id": source["source_id"],
        "evidence_id": evidence["evidence_id"],
        "claim_id": claim["claim_id"],
        "output_id": output["output_id"],
    }, elapsed_ms


def _core_throughput(store: ResearchStore) -> dict[str, Any]:
    run = store.create_run("Core throughput benchmark")
    observation_types = sorted(OBSERVATION_TYPES)
    latencies: list[float] = []
    failures: list[str] = []

    started = time.perf_counter()
    for index in range(CORE_ITEMS):
        obs = observation_types[index % len(observation_types)]
        result, latency = _make_chain(
            store,
            run["run_id"],
            key=f"core-{index}",
            observation_type=obs,
        )
        latencies.append(latency)
        if not result["ok"]:
            failures.append(f"core-{index}")
    elapsed = time.perf_counter() - started

    export_started = time.perf_counter()
    exported = store.export_run(run["run_id"])
    export_ms = (time.perf_counter() - export_started) * 1000.0
    run_audit = store.audit_run(run["run_id"])

    return {
        "items": CORE_ITEMS,
        "failures": failures,
        "success_rate": round((CORE_ITEMS - len(failures)) / CORE_ITEMS, 6),
        "elapsed_s": round(elapsed, 3),
        "chains_per_second": round(CORE_ITEMS / elapsed, 2) if elapsed else 0.0,
        "chain_latency": _latency_summary(latencies),
        "export_ms": round(export_ms, 3),
        "export_counts": {
            "sources": len(exported["sources"]),
            "evidence": len(exported["evidence"]),
            "claims": len(exported["claims"]),
            "outputs": len(exported["outputs"]),
            "ledger_rows": len(exported["ledger"]),
        },
        "run_audit_passed": run_audit["passed"],
    }


def _semantic_guard_benchmark(store: ResearchStore) -> dict[str, Any]:
    run = store.create_run("Semantic guard benchmark")
    observation_types = sorted(OBSERVATION_TYPES)
    output_types = observation_types + ["MIXED"]
    output_attack_attempts = 0
    output_attacks_blocked = 0
    verified_claim_attack_attempts = 0
    verified_claim_attacks_blocked = 0
    false_positive_rejections = 0
    claims: dict[str, dict[str, Any]] = {}

    for obs in observation_types:
        source = store.record_source(
            run["run_id"],
            url=f"https://benchmark.local/semantic/{obs.lower()}",
            content=f"semantic:{obs}",
            retrieval_tool="benchmark",
            retrieval_method="synthetic",
            retrieval_status="SUCCESS",
        )
        evidence = store.add_evidence(
            run["run_id"],
            source_id=source["source_id"],
            supporting_passage=f"Evidence semantic type is {obs}.",
            observation_type=obs,
        )
        claim = store.add_claim(
            run["run_id"],
            statement=f"Claim semantic type is {obs}.",
            classification="VERIFIED",
            observation_type=obs,
            supporting_evidence_ids=[evidence["evidence_id"]],
        )
        claims[obs] = claim

        for asserted in observation_types:
            if asserted == obs:
                continue
            verified_claim_attack_attempts += 1
            try:
                store.add_claim(
                    run["run_id"],
                    statement=f"Invalid relabel {obs} as {asserted}.",
                    classification="VERIFIED",
                    observation_type=asserted,
                    supporting_evidence_ids=[evidence["evidence_id"]],
                )
            except ValueError:
                verified_claim_attacks_blocked += 1

        for asserted in output_types:
            if asserted == obs:
                continue
            output_attack_attempts += 1
            try:
                store.create_output(
                    run["run_id"],
                    consumer_type="semantic_benchmark",
                    output_type="ANSWER",
                    fragments=[
                        {
                            "content": f"Attempt to consume {obs} as {asserted}.",
                            "claim_ids": [claim["claim_id"]],
                            "asserted_observation_type": asserted,
                        }
                    ],
                )
            except ValueError:
                output_attacks_blocked += 1

        try:
            store.create_output(
                run["run_id"],
                consumer_type="semantic_benchmark",
                output_type="ANSWER",
                fragments=[
                    {
                        "content": f"Correct semantic consumption of {obs}.",
                        "claim_ids": [claim["claim_id"]],
                        "asserted_observation_type": obs,
                    }
                ],
            )
        except ValueError:
            false_positive_rejections += 1

    source = store.record_source(
        run["run_id"],
        url="https://benchmark.local/semantic/inference-transform",
        content="Observed input is 10.",
        retrieval_tool="benchmark",
        retrieval_method="synthetic",
        retrieval_status="SUCCESS",
    )
    evidence = store.add_evidence(
        run["run_id"],
        source_id=source["source_id"],
        supporting_passage="Observed input is 10.",
        observation_type="ACTUAL",
    )
    inference = store.add_claim(
        run["run_id"],
        statement="Model projects a future value from the observed input.",
        classification="INFERENCE",
        observation_type="FORECAST",
        supporting_evidence_ids=[evidence["evidence_id"]],
        provenance={"semantic_derivation": "benchmark transformation"},
    )

    mixed = store.create_output(
        run["run_id"],
        consumer_type="semantic_benchmark",
        output_type="ANSWER",
        fragments=[
            {
                "content": "Actual observation and forecast inference are shown together.",
                "claim_ids": [
                    claims["ACTUAL"]["claim_id"],
                    inference["claim_id"],
                ],
                "asserted_observation_type": "MIXED",
            }
        ],
    )

    total_attacks = output_attack_attempts + verified_claim_attack_attempts
    total_blocked = output_attacks_blocked + verified_claim_attacks_blocked
    return {
        "output_attack_attempts": output_attack_attempts,
        "output_attacks_blocked": output_attacks_blocked,
        "verified_claim_attack_attempts": verified_claim_attack_attempts,
        "verified_claim_attacks_blocked": verified_claim_attacks_blocked,
        "total_attack_block_rate": round(total_blocked / total_attacks, 6),
        "false_positive_rejections": false_positive_rejections,
        "valid_inference_transform_allowed": inference["observation_type"] == "FORECAST",
        "mixed_semantics_allowed": (
            mixed["fragments"][0]["asserted_observation_type"] == "MIXED"
        ),
    }


def _dedupe_and_versioning(store: ResearchStore) -> dict[str, Any]:
    run = store.create_run("Source dedupe/version benchmark")
    source_ids: list[str] = []
    for _ in range(25):
        source = store.record_source(
            run["run_id"],
            url="https://benchmark.local/versioned/source",
            content="unchanged-content-v1",
            retrieval_tool="benchmark",
            retrieval_method="synthetic",
            retrieval_status="SUCCESS",
        )
        source_ids.append(source["source_id"])

    changed = store.record_source(
        run["run_id"],
        url="https://benchmark.local/versioned/source",
        content="changed-content-v2",
        retrieval_tool="benchmark",
        retrieval_method="synthetic",
        retrieval_status="SUCCESS",
    )
    return {
        "identical_refetches": 25,
        "same_source_id_for_identical_content": len(set(source_ids)) == 1,
        "changed_content_created_new_source": changed["source_id"] != source_ids[0],
        "changed_content_version": changed["version_number"],
        "previous_source_link_ok": changed["previous_source_id"] == source_ids[0],
        "retrieval_events_for_v1": len(
            store.get_source(source_ids[0], run_id=run["run_id"])["retrieval_history"]
        ),
    }


def _concurrency_benchmark(store: ResearchStore) -> dict[str, Any]:
    run = store.create_run("Concurrent callers benchmark")
    tasks = CONCURRENCY_WORKERS * CONCURRENCY_ITEMS_PER_WORKER
    latencies: list[float] = []
    errors: list[str] = []
    bad_provenance = 0

    def worker(index: int) -> tuple[dict[str, Any], float]:
        obs = "ACTUAL" if index % 2 == 0 else "FORECAST"
        return _make_chain(
            store,
            run["run_id"],
            key=f"concurrent-{index}",
            observation_type=obs,
        )

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=CONCURRENCY_WORKERS) as pool:
        futures = {pool.submit(worker, index): index for index in range(tasks)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                result, latency = future.result()
                latencies.append(latency)
                if not result["ok"]:
                    bad_provenance += 1
            except Exception as exc:  # noqa: BLE001 - benchmark records failures
                errors.append(f"{index}:{type(exc).__name__}:{exc}")
    elapsed = time.perf_counter() - started

    audit = store.audit_run(run["run_id"])
    return {
        "workers": CONCURRENCY_WORKERS,
        "tasks": tasks,
        "errors": errors[:20],
        "error_count": len(errors),
        "bad_provenance_count": bad_provenance,
        "elapsed_s": round(elapsed, 3),
        "chains_per_second": round(tasks / elapsed, 2) if elapsed else 0.0,
        "chain_latency": _latency_summary(latencies),
        "run_audit_passed": audit["passed"],
    }


def _live_reliability() -> dict[str, Any]:
    iterations: list[dict[str, Any]] = []
    latencies: list[float] = []

    for index in range(LIVE_ITERATIONS):
        started = time.perf_counter()
        try:
            result = run_acceptance()
            latency = (time.perf_counter() - started) * 1000.0
            latencies.append(latency)
            iterations.append(
                {
                    "iteration": index + 1,
                    "status": result["status"],
                    "latency_ms": round(latency, 3),
                    "parseable_urls": result["discovery"]["parseable_urls"],
                    "final_retrieval_tool": result["retrieval"]["final_tool"],
                    "retrieval_attempts": result["retrieval"]["attempts"],
                    "semantic_promotion_rejected": result[
                        "semantic_promotion_rejected"
                    ],
                    "outputs": result["outputs"],
                }
            )
        except Exception as exc:  # noqa: BLE001 - benchmark records live failures
            latency = (time.perf_counter() - started) * 1000.0
            latencies.append(latency)
            iterations.append(
                {
                    "iteration": index + 1,
                    "status": "FAIL",
                    "latency_ms": round(latency, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    passed = sum(1 for item in iterations if item["status"] == "PASS")
    return {
        "iterations": LIVE_ITERATIONS,
        "passed": passed,
        "failed": LIVE_ITERATIONS - passed,
        "success_rate": round(passed / LIVE_ITERATIONS, 6)
        if LIVE_ITERATIONS
        else 0.0,
        "latency": _latency_summary(latencies),
        "details": iterations,
    }


def run_benchmark() -> dict[str, Any]:
    fd, db_path = tempfile.mkstemp(prefix="ahmed-research-benchmark-", suffix=".db")
    os.close(fd)
    Path(db_path).unlink(missing_ok=True)

    store = ResearchStore(db_path)
    started = time.perf_counter()
    try:
        core = _core_throughput(store)
        semantic = _semantic_guard_benchmark(store)
        dedupe = _dedupe_and_versioning(store)
        concurrency = _concurrency_benchmark(store)
        db_size = Path(db_path).stat().st_size if Path(db_path).exists() else 0
    finally:
        store.close()

    live = _live_reliability()
    total_elapsed = time.perf_counter() - started

    integrity_passed = all(
        [
            core["success_rate"] == 1.0,
            core["run_audit_passed"],
            semantic["total_attack_block_rate"] == 1.0,
            semantic["false_positive_rejections"] == 0,
            semantic["valid_inference_transform_allowed"],
            semantic["mixed_semantics_allowed"],
            dedupe["same_source_id_for_identical_content"],
            dedupe["changed_content_created_new_source"],
            dedupe["changed_content_version"] == 2,
            dedupe["previous_source_link_ok"],
            concurrency["error_count"] == 0,
            concurrency["bad_provenance_count"] == 0,
            concurrency["run_audit_passed"],
        ]
    )

    result = {
        "status": "PASS" if integrity_passed else "FAIL",
        "benchmark_version": 1,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
            "cpu_count": os.cpu_count(),
            "db_size_bytes": db_size,
        },
        "core": core,
        "semantic_guard": semantic,
        "dedupe_and_versioning": dedupe,
        "concurrency": concurrency,
        "live_acquisition": live,
        "total_elapsed_s": round(total_elapsed, 3),
        "notes": [
            "Core timings measure the current single-process SQLite implementation.",
            "The ResearchStore RLock serializes access inside one process; concurrency throughput is correctness-oriented, not a distributed-write benchmark.",
            "Live acquisition latency includes Exa discovery and network retrieval/fallback, so it is expected to vary with upstream services.",
            "Live failures are reported separately and do not redefine core integrity.",
        ],
    }

    Path(db_path).unlink(missing_ok=True)
    return result


def main() -> int:
    result = run_benchmark()
    print("AHMED_RESEARCH_EFFICIENCY_BENCHMARK=" + json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
