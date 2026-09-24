"""External production acceptance runner for Ahmed Toolbox.

This script is intentionally run from a separate execution context. It reads the
target URL/token from environment variables, never prints the token, and exits
non-zero if any required production acceptance or security gate fails.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any

import requests


@dataclass
class AcceptanceResult:
    id: int
    name: str
    status: str
    latency_ms: float
    behavior: str
    detail: dict[str, Any]


class AcceptanceClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._next_id = 1

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def post_raw(self, body: bytes, *, timeout: float = 30.0) -> requests.Response:
        return requests.post(
            self.base_url + "/mcp",
            headers=self._headers(),
            data=body,
            timeout=timeout,
        )

    def rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> tuple[dict[str, Any], float]:
        request_id = self._next_id
        self._next_id += 1
        started = time.perf_counter()
        response = requests.post(
            self.base_url + "/mcp",
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
            timeout=timeout,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(f"MCP error: {payload['error']}")
        if payload.get("id") != request_id:
            raise RuntimeError("MCP response id mismatch")
        return payload.get("result") or {}, latency_ms

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 60.0,
    ) -> tuple[Any, float]:
        result, latency_ms = self.rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
        if result.get("isError"):
            text = "\n".join(
                str(block.get("text") or "")
                for block in (result.get("content") or [])
                if isinstance(block, dict)
            )
            raise RuntimeError(f"{name} returned tool error: {text[:1000]}")
        blocks = result.get("content") or []
        if not blocks:
            return result, latency_ms
        text = str(blocks[0].get("text") or "")
        if text.lstrip().startswith(("{", "[")):
            try:
                return json.loads(text), latency_ms
            except json.JSONDecodeError:
                pass
        return text, latency_ms


def run_case(
    case_id: int,
    name: str,
    fn,
) -> AcceptanceResult:
    started = time.perf_counter()
    try:
        behavior, detail, measured = fn()
        return AcceptanceResult(
            id=case_id,
            name=name,
            status="PASS",
            latency_ms=round(measured, 3),
            behavior=behavior,
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 - acceptance boundary
        elapsed = (time.perf_counter() - started) * 1000.0
        return AcceptanceResult(
            id=case_id,
            name=name,
            status="FAIL",
            latency_ms=round(elapsed, 3),
            behavior=f"{type(exc).__name__}: {exc}"[:1200],
            detail={"error_type": type(exc).__name__},
        )


def main() -> None:
    base_url = os.environ.get("TARGET_URL", "").strip()
    token = (
        os.environ.get("TARGET_TOKEN", "").strip()
        or os.environ.get("AHMED_TOOLBOX_TOKEN", "").strip()
    )
    video_url = os.environ.get("TARGET_VIDEO_URL", "").strip()
    if not base_url:
        raise SystemExit("TARGET_URL is required")

    client = AcceptanceClient(base_url, token)
    results: list[AcceptanceResult] = []
    state: dict[str, Any] = {}

    security_started = time.perf_counter()
    try:
        unauthenticated = requests.post(
            base_url + "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "MCP-Protocol-Version": "2025-06-18",
            },
            json={"jsonrpc": "2.0", "id": 0, "method": "ping", "params": {}},
            timeout=15,
        )
        security_latency_ms = (time.perf_counter() - security_started) * 1000.0
        security_preflight = {
            "status": "PASS" if unauthenticated.status_code == 401 else "FAIL",
            "latency_ms": round(security_latency_ms, 3),
            "behavior": (
                "unauthenticated MCP request rejected"
                if unauthenticated.status_code == 401
                else "unauthenticated MCP request was not rejected"
            ),
            "detail": {"http_status": unauthenticated.status_code},
        }
    except Exception as exc:  # noqa: BLE001 - acceptance boundary
        security_preflight = {
            "status": "FAIL",
            "latency_ms": round(
                (time.perf_counter() - security_started) * 1000.0,
                3,
            ),
            "behavior": f"{type(exc).__name__}: {exc}"[:1200],
            "detail": {"error_type": type(exc).__name__},
        }

    def health():
        started = time.perf_counter()
        response = requests.get(base_url + "/health", timeout=15)
        latency = (time.perf_counter() - started) * 1000.0
        payload = response.json()
        assert response.status_code == 200
        assert payload.get("status") == "ok"
        return "HTTP 200 and status=ok", {"service": payload.get("service")}, latency

    results.append(run_case(1, "/health", health))

    def tools_list():
        payload, latency = client.rpc("tools/list")
        names = sorted(
            str(item.get("name") or "")
            for item in payload.get("tools") or []
            if isinstance(item, dict)
        )
        required = {
            "reach_doctor",
            "reach_web_search",
            "reach_read_url",
            "research_start_run",
            "research_record_source",
            "research_add_evidence",
            "research_add_claim",
            "research_create_output",
            "research_audit_output",
            "runtime_status",
            "toolbox_execution_stats",
        }
        missing = sorted(required - set(names))
        assert not missing, f"missing required tools: {missing}"
        state["tools"] = names
        return (
            "MCP tools/list exposed required production tools",
            {"tool_count": len(names)},
            latency,
        )

    results.append(run_case(2, "MCP tools/list", tools_list))

    def doctor():
        payload, latency = client.call_tool("reach_doctor", {})
        assert payload
        return "reach_doctor returned a non-error health payload", {}, latency

    results.append(run_case(3, "reach_doctor", doctor))

    def web_search():
        payload, latency = client.call_tool(
            "reach_web_search",
            {"query": "OpenAI official website", "num_results": 3},
            timeout=45,
        )
        text = json.dumps(payload, ensure_ascii=False, default=str)
        assert len(text) > 50
        state["search_payload"] = payload
        return "live web search returned non-empty results", {"result_size": len(text)}, latency

    results.append(run_case(4, "reach_web_search", web_search))

    def read_url():
        payload, latency = client.call_tool(
            "reach_read_url",
            {"url": "https://example.com", "max_chars": 5000},
            timeout=30,
        )
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        assert "Example Domain" in text
        state["read_text"] = text
        return "Jina/read path returned Example Domain", {"content_size": len(text)}, latency

    results.append(run_case(5, "reach_read_url", read_url))

    def start_research():
        payload, latency = client.call_tool(
            "research_start_run",
            {
                "question": "Production acceptance provenance chain",
                "metadata": {"acceptance": "production", "source": "external-runner"},
            },
        )
        assert payload.get("run_id")
        state["run_id"] = payload["run_id"]
        return "ResearchRun created", {"run_id": payload["run_id"]}, latency

    results.append(run_case(6, "research_start_run", start_research))

    def video_path():
        if not video_url:
            raise RuntimeError("TARGET_VIDEO_URL is not configured")
        payload, latency = client.call_tool(
            "reach_youtube_browser_inspect",
            {
                "url": video_url,
                "include_comments": False,
                "max_comments": 0,
                "timeout_seconds": 75,
            },
            timeout=90,
        )
        assert isinstance(payload, dict)
        assert payload.get("title")
        assert payload.get("visible_text")
        return (
            "real rendered YouTube evidence path returned title and visible text",
            {
                "title_present": True,
                "visible_text_chars": len(str(payload.get("visible_text") or "")),
            },
            latency,
        )

    results.append(run_case(7, "real media/video evidence path", video_path))

    def full_research():
        run_id = state.get("run_id")
        assert run_id, "research_start_run did not produce run_id"
        source_text = state.get("read_text") or "Example Domain"
        source, l1 = client.call_tool(
            "research_record_source",
            {
                "run_id": run_id,
                "url": "https://example.com",
                "content": source_text,
                "retrieval_tool": "reach_read_url",
                "retrieval_method": "jina_reader",
                "retrieval_status": "SUCCESS",
                "publisher": "IANA",
                "source_type": "production_acceptance",
                "primary_source": True,
                "retrieval_history": [
                    {
                        "stage": "RETRIEVAL",
                        "tool": "reach_read_url",
                        "method": "jina_reader",
                        "status": "SUCCESS",
                    }
                ],
            },
        )
        evidence, l2 = client.call_tool(
            "research_add_evidence",
            {
                "run_id": run_id,
                "source_id": source["source_id"],
                "supporting_passage": "Example Domain",
                "structured_fact": {"domain": "example.com", "purpose": "documentation"},
                "observation_type": "ACTUAL",
            },
        )
        claim, l3 = client.call_tool(
            "research_add_claim",
            {
                "run_id": run_id,
                "statement": "example.com is the Example Domain used for documentation examples.",
                "classification": "VERIFIED",
                "observation_type": "ACTUAL",
                "supporting_evidence_ids": [evidence["evidence_id"]],
            },
        )
        output, l4 = client.call_tool(
            "research_create_output",
            {
                "run_id": run_id,
                "consumer_type": "production_acceptance",
                "output_type": "ANSWER",
                "fragments": [
                    {
                        "content": claim["statement"],
                        "claim_ids": [claim["claim_id"]],
                        "asserted_observation_type": "ACTUAL",
                    }
                ],
            },
        )
        audit, l5 = client.call_tool(
            "research_audit_output",
            {"output_id": output["output_id"]},
        )
        assert audit.get("passed") is True
        total = l1 + l2 + l3 + l4 + l5
        return (
            "source→evidence→claim→output→audit completed with audit passed",
            {
                "run_id": run_id,
                "source_id": source["source_id"],
                "evidence_id": evidence["evidence_id"],
                "claim_id": claim["claim_id"],
                "output_id": output["output_id"],
                "audit_passed": True,
            },
            total,
        )

    results.append(run_case(8, "full Research workflow", full_research))

    def malformed():
        started = time.perf_counter()
        response = client.post_raw(b"{", timeout=15)
        latency = (time.perf_counter() - started) * 1000.0
        assert response.status_code == 400
        payload = response.json()
        assert payload.get("error", {}).get("code") == -32700
        return "malformed JSON rejected with HTTP 400 / parse error", {}, latency

    results.append(run_case(9, "malformed request", malformed))

    def concurrent_clients():
        def ping(index: int) -> float:
            local = AcceptanceClient(base_url, token)
            started = time.perf_counter()
            payload, _ = local.rpc("ping", timeout=15)
            elapsed = (time.perf_counter() - started) * 1000.0
            assert payload == {}
            return elapsed

        wall_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=5) as pool:
            latencies = list(pool.map(ping, range(5)))
        wall = (time.perf_counter() - wall_started) * 1000.0
        return (
            "five independent MCP clients completed concurrently",
            {
                "clients": 5,
                "individual_ms": [round(value, 3) for value in latencies],
                "wall_ms": round(wall, 3),
            },
            wall,
        )

    results.append(run_case(10, "5 concurrent clients", concurrent_clients))

    passed = sum(item.status == "PASS" for item in results)
    failed = sum(item.status == "FAIL" for item in results)
    functional_acceptance_passed = failed == 0 and passed == 10
    security_auth_configured = (
        bool(token) and security_preflight["status"] == "PASS"
    )
    report = {
        "schema": "ahmed-toolbox-production-acceptance/v1",
        "target": base_url,
        "required_tests": 10,
        "passed": passed,
        "failed": failed,
        "functional_acceptance_passed": functional_acceptance_passed,
        "security_auth_configured": security_auth_configured,
        "security_preflight": security_preflight,
        "acceptance_passed": functional_acceptance_passed and security_auth_configured,
        "results": [asdict(item) for item in results],
        "known_optional_gap": (
            "reach_media_ingest STT is not production-capable because no transcription "
            "provider is configured; test 7 validates the production-capable Browser Use "
            "video evidence path instead."
        ),
    }
    print("PRODUCTION_ACCEPTANCE_REPORT=" + json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not report["acceptance_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()