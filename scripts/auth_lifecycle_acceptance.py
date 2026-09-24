"""External authentication lifecycle acceptance for Ahmed Toolbox."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from agent_reach.toolbox.auth_client import AutoRefreshingBearer


class LifecycleFailure(RuntimeError):
    pass


def _rpc(
    client: AutoRefreshingBearer,
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.request(
        "POST",
        "/mcp",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        },
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise LifecycleFailure(f"MCP error: {payload['error']}")
    return payload.get("result") or {}


def _runtime_status(client: AutoRefreshingBearer, request_id: int) -> dict[str, Any]:
    result = _rpc(
        client,
        request_id,
        "tools/call",
        {"name": "runtime_status", "arguments": {}},
    )
    if result.get("isError"):
        raise LifecycleFailure("runtime_status returned isError")
    content = result.get("content") or []
    if not content:
        raise LifecycleFailure("runtime_status returned no content")
    payload = json.loads(content[0]["text"])
    if payload.get("status") != "ok":
        raise LifecycleFailure(f"runtime_status unhealthy: {payload}")
    return payload


def main() -> None:
    base_url = os.environ.get("TARGET_URL", "").strip()
    refresh_token = (
        os.environ.get("TARGET_REFRESH_TOKEN", "").strip()
        or os.environ.get("AHMED_TOOLBOX_REFRESH_TOKEN", "").strip()
    )
    phase = os.environ.get("AUTH_LIFECYCLE_PHASE", "pre").strip().lower()
    call_count = max(50, min(100, int(os.environ.get("AUTH_LIFECYCLE_CALL_COUNT", "75"))))
    wait_seconds = max(0.0, float(os.environ.get("AUTH_LIFECYCLE_WAIT_SECONDS", "6")))
    token_ttl = max(1, int(os.environ.get("AUTH_LIFECYCLE_TOKEN_TTL_SECONDS", "5")))

    if not base_url:
        raise SystemExit("TARGET_URL is required")
    if not refresh_token:
        raise SystemExit("TARGET_REFRESH_TOKEN is required")
    if phase not in {"pre", "post"}:
        raise SystemExit("AUTH_LIFECYCLE_PHASE must be pre or post")

    unauthenticated = requests.post(
        base_url + "/mcp",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        },
        json={"jsonrpc": "2.0", "id": 0, "method": "ping", "params": {}},
        timeout=15,
    )
    if unauthenticated.status_code != 401:
        raise LifecycleFailure(
            f"unauthenticated MCP request returned HTTP {unauthenticated.status_code}"
        )

    client = AutoRefreshingBearer(
        base_url,
        refresh_token,
        requested_ttl_seconds=token_ttl,
        refresh_skew_seconds=0.25,
    )

    started = time.perf_counter()
    if phase == "pre":
        statuses = []
        split = call_count // 2
        for index in range(call_count):
            statuses.append(_runtime_status(client, index + 1)["status"])
            if index + 1 == split and wait_seconds:
                time.sleep(wait_seconds)
        if statuses != ["ok"] * call_count:
            raise LifecycleFailure("one or more runtime_status calls were unhealthy")
        if client.refresh_count < 2:
            raise LifecycleFailure(
                "short-lived access token did not refresh during lifecycle test"
            )
        report = {
            "schema": "ahmed-toolbox-auth-lifecycle/v1",
            "phase": "pre",
            "status": "PASS",
            "calls": call_count,
            "wait_seconds": wait_seconds,
            "requested_token_ttl_seconds": token_ttl,
            "refresh_count": client.refresh_count,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "unauthenticated_http_status": unauthenticated.status_code,
        }
        print("AUTH_LIFECYCLE_PRE=" + json.dumps(report, sort_keys=True))
    else:
        status = _runtime_status(client, 1)
        report = {
            "schema": "ahmed-toolbox-auth-lifecycle/v1",
            "phase": "post",
            "status": "PASS",
            "runtime_status": status.get("status"),
            "refresh_count": client.refresh_count,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "unauthenticated_http_status": unauthenticated.status_code,
        }
        print("AUTH_LIFECYCLE_POST=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
