from __future__ import annotations

import json
import math
import os
import resource
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from typing import Any

import requests

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.observability import ExecutionLogStore
from agent_reach.toolbox.research import ResearchStore
from agent_reach.toolbox.retrieval import retrieve_with_fallback
from agent_reach.toolbox.runtime import RuntimeStore


class FakeReach:
    def doctor(self):
        return {"core": {"status": "ok"}}


class LocalHandler(BaseHTTPRequestHandler):
    body = ("local acquisition evidence " + ("x" * 800)).encode()

    def log_message(self, *_args):
        return

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return round(ordered[index], 3)


def summarize(name: str, durations: list[float], failures: list[str], *, extra=None):
    total = len(durations) + len(failures)
    successes = len(durations)
    return {
        "name": name,
        "total_requests": total,
        "successes": successes,
        "failures": len(failures),
        "success_percent": round(successes / total * 100.0, 3) if total else None,
        "p50_ms": round(median(durations), 3) if durations else None,
        "p95_ms": percentile(durations, 0.95),
        "max_latency_ms": round(max(durations), 3) if durations else None,
        "errors": failures[:10],
        **(extra or {}),
    }


def concurrent_retrievals() -> dict[str, Any]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/evidence"

    def caller(name, arguments):
        if name != "reach_read_url":
            return {"content": [{"type": "text", "text": "unexpected fallback"}], "isError": True}
        response = requests.get(arguments["url"], timeout=2)
        response.raise_for_status()
        return {"content": [{"type": "text", "text": response.text}], "isError": False}

    def one(_index):
        started = time.perf_counter()
        result = retrieve_with_fallback(url, call_tool=caller, min_chars=100)
        elapsed = (time.perf_counter() - started) * 1000.0
        if result["status"] != "SUCCESS":
            raise RuntimeError(result["status"])
        return elapsed

    durations, failures = [], []
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(one, index) for index in range(10)]
            for future in futures:
                try:
                    durations.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return summarize("10_concurrent_web_retrievals_local", durations, failures)


def sequential_research_runs(root: Path) -> dict[str, Any]:
    store = ResearchStore(str(root / "research-sequential.db"))
    durations, failures = [], []
    for index in range(20):
        started = time.perf_counter()
        try:
            run = store.create_run(f"stress run {index}")
            store.complete_run(run["run_id"])
            durations.append((time.perf_counter() - started) * 1000.0)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{type(exc).__name__}: {exc}")
    return summarize("20_sequential_research_runs", durations, failures)


def mcp_burst(root: Path) -> dict[str, Any]:
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        observability_store=ExecutionLogStore(str(root / "obs.db")),
        observability_enabled=True,
        research_enabled=False,
        orchestration_enabled=False,
        runtime_enabled=False,
        ace_enabled=False,
    )

    def one(index):
        started = time.perf_counter()
        result = gateway.call_tool("reach_doctor", {}, request_id=f"stress-{index}")
        elapsed = (time.perf_counter() - started) * 1000.0
        if result.get("isError"):
            raise RuntimeError("reach_doctor failed")
        return elapsed

    durations, failures = [], []
    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(one, index) for index in range(50)]
        for future in futures:
            try:
                durations.append(future.result())
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{type(exc).__name__}: {exc}")
    wall = time.perf_counter() - wall_started
    report = summarize("50_mcp_calls", durations, failures, extra={"wall_seconds": round(wall, 3)})
    if wall > 60:
        report["failures"] += 1
        report["errors"].append("wall time exceeded 60 seconds")
        report["success_percent"] = round(100.0 * report["successes"] / 50, 3)
    return report


def evidence_scale(root: Path) -> dict[str, Any]:
    store = ResearchStore(str(root / "research-evidence.db"))
    run = store.create_run("1000 evidence stress")
    source = store.record_source(
        run["run_id"],
        url="https://example.com/stress",
        content="stress source " + ("e" * 2000),
        retrieval_tool="stress",
        retrieval_method="local",
    )
    durations, failures = [], []
    for index in range(1000):
        started = time.perf_counter()
        try:
            store.add_evidence(
                run["run_id"],
                source_id=source["source_id"],
                supporting_passage=f"evidence-{index}",
                observation_type="ACTUAL",
                value=index,
                unit="count",
            )
            durations.append((time.perf_counter() - started) * 1000.0)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{type(exc).__name__}: {exc}")
    audit = store.audit_run(run["run_id"])
    return summarize(
        "1000_evidence_items",
        durations,
        failures,
        extra={"audit_passed": bool(audit["passed"])},
    )


def parallel_dag(root: Path) -> dict[str, Any]:
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        runtime_store=RuntimeStore(str(root / "runtime.db")),
        runtime_enabled=True,
        research_enabled=False,
        orchestration_enabled=False,
        ace_enabled=False,
    )
    steps = [
        {"id": f"branch-{index}", "tool_name": "reach_doctor", "arguments": {}}
        for index in range(10)
    ]
    started = time.perf_counter()
    result = gateway.call_tool(
        "runtime_execute_workflow",
        {
            "steps": steps,
            "max_parallel": 8,
            "timeout_seconds": 30,
            "fail_fast": True,
            "auto_learn": False,
        },
    )
    elapsed = (time.perf_counter() - started) * 1000.0
    payload = json.loads(result["content"][0]["text"])
    failures = [] if payload["status"] == "ok" else [payload["status"]]
    return summarize(
        "10_parallel_dag_branches",
        [elapsed] if not failures else [],
        failures,
        extra={
            "ok_count": payload["ok_count"],
            "step_count": payload["step_count"],
            "workflow_duration_ms": payload["duration_ms"],
        },
    )


def main() -> None:
    process_started = time.process_time()
    with tempfile.TemporaryDirectory(prefix="ahmed-stress-") as tmp:
        root = Path(tmp)
        scenarios = [
            concurrent_retrievals(),
            {
                "name": "5_media_ingestions",
                "status": "SKIP",
                "reason": "live STT/media prerequisites are intentionally absent from deterministic CI",
            },
            sequential_research_runs(root),
            mcp_burst(root),
            {
                "name": "subagent_limit_saturation",
                "status": "SKIP",
                "reason": "external model credential is not used in deterministic CI; bounded limits covered separately",
            },
            evidence_scale(root),
            parallel_dag(root),
        ]

    peak_rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_mb = (
        peak_rss_raw / (1024 * 1024) if os.uname().sysname == "Darwin" else peak_rss_raw / 1024
    )
    report = {
        "schema": "ahmed-toolbox-stress/v1",
        "cpu_seconds": round(time.process_time() - process_started, 3),
        "peak_rss_mb": round(peak_rss_mb, 3),
        "scenarios": scenarios,
    }
    print("AHMED_STRESS_REPORT=" + json.dumps(report, sort_keys=True))
    required = [item for item in scenarios if item.get("status") != "SKIP"]
    if any(item.get("failures", 0) for item in required):
        raise SystemExit(1)
    if any(item.get("success_percent", 0) < 100 for item in required):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
