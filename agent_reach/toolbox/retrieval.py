"""Deterministic retrieval fallback state machine for Ahmed Research Engine.

The state machine owns escalation policy, not source truth. It records every
attempt and only returns SUCCESS when the retrieved representation passes
explicit content checks.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

ToolCaller = Callable[[str, dict[str, Any]], dict[str, Any]]

RATE_LIMIT_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "rate-limited",
    "rate_limited",
)

FORBIDDEN_MARKERS = (
    "403",
    "forbidden",
)

ANTI_BOT_MARKERS = (
    "verify you are human",
    "verification required",
    "captcha",
    "access denied",
    "cloudflare",
    "anti-bot",
    "robot check",
    "反爬",
    "验证页",
)

MAX_RETRIEVAL_ATTEMPTS = 5

DEFAULT_STAGES: tuple[tuple[str, str, str], ...] = (
    ("JINA", "reach_read_url", "jina_reader"),
    ("SCRAPLING_FETCH", "scrapling__fetch", "scrapling_fetch"),
    ("STEALTHY_FETCH", "scrapling__stealthy_fetch", "scrapling_stealthy_fetch"),
)


def _result_text(result: dict[str, Any]) -> str:
    text = "\n".join(
        str(item.get("text") or "")
        for item in result.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()
    if not text:
        return ""

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text

    if isinstance(parsed, dict):
        for key in ("content", "text", "markdown", "html", "body", "visible_text"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return text


def _assess_content(
    text: str,
    *,
    min_chars: int,
    required_terms: list[str],
    require_all_terms: bool,
) -> tuple[str, str, list[str]]:
    normalized = text.strip()
    lowered = normalized.lower()

    if not normalized:
        return "FAILED", "EMPTY_CONTENT", []

    if any(marker in lowered for marker in ANTI_BOT_MARKERS):
        return "BLOCKED", "ANTI_BOT_CHALLENGE", []

    if len(normalized) < min_chars:
        return "PARTIAL", "CONTENT_TOO_SHORT", []

    terms = [term.strip() for term in required_terms if term.strip()]
    if terms:
        missing = [term for term in terms if term.lower() not in lowered]
        if require_all_terms and missing:
            return "PARTIAL", "MISSING_REQUIRED_TERMS", missing
        if not require_all_terms and len(missing) == len(terms):
            return "PARTIAL", "NO_REQUIRED_TERM_FOUND", missing

    return "SUCCESS", "CONTENT_ACCEPTED", []


def retrieve_with_fallback(
    url: str,
    *,
    call_tool: ToolCaller,
    max_chars: int = 100000,
    min_chars: int = 200,
    required_terms: list[str] | None = None,
    require_all_terms: bool = True,
    browser_tool: str | None = None,
    discovery_tool: str | None = None,
    discovery_query: str | None = None,
) -> dict[str, Any]:
    """Run the frozen retrieval escalation path and retain attempt provenance."""
    target = str(url or "").strip()
    if not target:
        raise ValueError("url is required")
    if max_chars < 1000 or max_chars > 200000:
        raise ValueError("max_chars must be between 1000 and 200000")
    if min_chars < 1 or min_chars > max_chars:
        raise ValueError("min_chars must be between 1 and max_chars")

    terms = [str(item).strip() for item in (required_terms or []) if str(item).strip()]
    if len(terms) > 20:
        raise ValueError("required_terms supports at most 20 terms")
    if any(len(term) > 500 for term in terms):
        raise ValueError("required_terms entries must be <= 500 characters")

    stages = list(DEFAULT_STAGES)
    if browser_tool:
        stages.append(("BROWSER", str(browser_tool), "browser_fallback"))

    attempts: list[dict[str, Any]] = []
    final_text = ""
    final_tool = None
    final_method = None

    if len(stages) + (1 if discovery_tool else 0) > MAX_RETRIEVAL_ATTEMPTS:
        raise ValueError("retrieval fallback exceeds hard attempt limit")

    for attempt_index, (state, tool_name, method) in enumerate(stages, start=1):
        next_backend = (
            stages[attempt_index][1]
            if attempt_index < len(stages)
            else (str(discovery_tool) if discovery_tool else None)
        )
        attempt_started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        args: dict[str, Any] = {"url": target}
        if tool_name == "reach_read_url":
            args["max_chars"] = max_chars

        started = time.perf_counter()
        try:
            result = call_tool(tool_name, args)
        except Exception as exc:  # noqa: BLE001 - boundary records exact failure
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            attempts.append(
                {
                    "attempt_index": attempt_index,
                    "started_at": attempt_started_at,
                    "state": state,
                    "tool": tool_name,
                    "method": method,
                    "status": "FAILED",
                    "reason_code": "TOOL_EXCEPTION",
                    "detail": f"{type(exc).__name__}: {exc}"[:1200],
                    "duration_ms": round(elapsed_ms, 3),
                    "content_length": 0,
                    "missing_terms": [],
                    "next_backend": None,
                }
            )
            continue

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if result.get("isError"):
            detail = _result_text(result)
            lowered_detail = detail.lower()
            rate_limited = any(marker in lowered_detail for marker in RATE_LIMIT_MARKERS)
            forbidden = any(marker in lowered_detail for marker in FORBIDDEN_MARKERS)
            blocked = forbidden or any(
                marker in lowered_detail for marker in ANTI_BOT_MARKERS
            )
            attempts.append(
                {
                    "attempt_index": attempt_index,
                    "started_at": attempt_started_at,
                    "state": state,
                    "tool": tool_name,
                    "method": method,
                    "status": "BLOCKED" if blocked else "FAILED",
                    "reason_code": (
                        "RATE_LIMITED"
                        if rate_limited
                        else ("HTTP_403_FORBIDDEN" if forbidden else (
                            "ANTI_BOT_CHALLENGE" if blocked else "TOOL_ERROR"
                        ))
                    ),
                    "detail": detail[:1200],
                    "duration_ms": round(elapsed_ms, 3),
                    "content_length": 0,
                    "missing_terms": [],
                    "next_backend": next_backend,
                }
            )
            continue

        text = _result_text(result)
        if len(text) > max_chars:
            text = text[:max_chars]

        status, reason_code, missing = _assess_content(
            text,
            min_chars=min_chars,
            required_terms=terms,
            require_all_terms=require_all_terms,
        )
        attempts.append(
            {
                "attempt_index": attempt_index,
                "started_at": attempt_started_at,
                "state": state,
                "tool": tool_name,
                "method": method,
                "status": status,
                "reason_code": reason_code,
                "detail": None,
                "duration_ms": round(elapsed_ms, 3),
                "content_length": len(text),
                "missing_terms": missing,
                "next_backend": next_backend,
            }
        )

        if status == "SUCCESS":
            final_text = text
            final_tool = tool_name
            final_method = method
            break

    alternative_discovery = ""
    if final_tool is None and discovery_tool:
        query = str(discovery_query or "").strip()
        if not query:
            query = " ".join(terms).strip() or target
        started = time.perf_counter()
        try:
            result = call_tool(
                str(discovery_tool),
                {"query": query[:1000], "num_results": 5},
            )
        except Exception as exc:  # noqa: BLE001 - provenance boundary
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            attempts.append(
                {
                    "attempt_index": len(attempts) + 1,
                    "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "state": "TARGETED_SEARCH",
                    "tool": str(discovery_tool),
                    "method": "targeted_search",
                    "status": "FAILED",
                    "reason_code": "TOOL_EXCEPTION",
                    "detail": f"{type(exc).__name__}: {exc}"[:1200],
                    "duration_ms": round(elapsed_ms, 3),
                    "content_length": 0,
                    "missing_terms": [],
                    "next_backend": next_backend,
                }
            )
        else:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            detail = _result_text(result)
            if result.get("isError"):
                lowered_detail = detail.lower()
                rate_limited = any(
                    marker in lowered_detail for marker in RATE_LIMIT_MARKERS
                )
                attempts.append(
                    {
                        "attempt_index": len(attempts) + 1,
                        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                        "state": "TARGETED_SEARCH",
                        "tool": str(discovery_tool),
                        "method": "targeted_search",
                        "status": "FAILED",
                        "reason_code": (
                            "RATE_LIMITED" if rate_limited else "TOOL_ERROR"
                        ),
                        "detail": detail[:1200],
                        "duration_ms": round(elapsed_ms, 3),
                        "content_length": 0,
                        "missing_terms": [],
                        "next_backend": None,
                    }
                )
            elif detail.strip():
                alternative_discovery = detail[:max_chars]
                attempts.append(
                    {
                        "attempt_index": len(attempts) + 1,
                        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                        "state": "TARGETED_SEARCH",
                        "tool": str(discovery_tool),
                        "method": "targeted_search",
                        "status": "DISCOVERED",
                        "reason_code": "ALTERNATIVE_SOURCE_CANDIDATES",
                        "detail": None,
                        "duration_ms": round(elapsed_ms, 3),
                        "content_length": len(alternative_discovery),
                        "missing_terms": [],
                        "next_backend": None,
                    }
                )
            else:
                attempts.append(
                    {
                        "attempt_index": len(attempts) + 1,
                        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                        "state": "TARGETED_SEARCH",
                        "tool": str(discovery_tool),
                        "method": "targeted_search",
                        "status": "FAILED",
                        "reason_code": "EMPTY_CONTENT",
                        "detail": None,
                        "duration_ms": round(elapsed_ms, 3),
                        "content_length": 0,
                        "missing_terms": [],
                        "next_backend": None,
                    }
                )

    retrieval_history = [
        {
            "stage": "RETRIEVAL",
            "tool": attempt["tool"],
            "method": attempt["method"],
            "status": attempt["status"],
            "detail": (
                attempt["reason_code"]
                + (
                    ": " + ", ".join(attempt["missing_terms"])
                    if attempt["missing_terms"]
                    else ""
                )
                + (
                    ": " + str(attempt["detail"])
                    if attempt["detail"]
                    else ""
                )
            )[:1200],
        }
        for attempt in attempts
    ]

    if final_tool is not None:
        return {
            "status": "SUCCESS",
            "url": target,
            "content": final_text,
            "content_length": len(final_text),
            "final_tool": final_tool,
            "retrieval_method": final_method,
            "attempts": attempts,
            "retrieval_history": retrieval_history,
            "escalation_count": max(0, len(attempts) - 1),
            "max_attempts": MAX_RETRIEVAL_ATTEMPTS,
            "retry_policy": "fail-fast-per-backend",
            "browser_fallback_configured": bool(browser_tool),
            "browser_required": False,
            "alternative_discovery": "",
        }

    if alternative_discovery:
        return {
            "status": "ALTERNATIVE_DISCOVERED",
            "url": target,
            "content": "",
            "content_length": 0,
            "final_tool": str(discovery_tool),
            "retrieval_method": "targeted_search",
            "attempts": attempts,
            "retrieval_history": retrieval_history,
            "escalation_count": max(0, len(attempts) - 1),
            "max_attempts": MAX_RETRIEVAL_ATTEMPTS,
            "retry_policy": "fail-fast-per-backend",
            "browser_fallback_configured": bool(browser_tool),
            "browser_required": False,
            "reason_code": "ORIGINAL_SOURCE_UNAVAILABLE_ALTERNATIVES_DISCOVERED",
            "alternative_discovery": alternative_discovery,
        }

    last_reason = attempts[-1]["reason_code"] if attempts else "NO_ATTEMPTS"
    return {
        "status": "SOURCE_UNAVAILABLE",
        "url": target,
        "content": "",
        "content_length": 0,
        "final_tool": None,
        "retrieval_method": None,
        "attempts": attempts,
        "retrieval_history": retrieval_history,
        "escalation_count": max(0, len(attempts) - 1),
        "browser_fallback_configured": bool(browser_tool),
        "browser_required": not bool(browser_tool),
        "reason_code": last_reason,
        "alternative_discovery": "",
    }
