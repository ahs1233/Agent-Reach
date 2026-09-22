from __future__ import annotations

import json

from agent_reach.toolbox.retrieval import retrieve_with_fallback


def _ok(text: str) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    }


def _error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def test_jina_success_stops_without_escalation() -> None:
    calls: list[str] = []

    def caller(name: str, arguments: dict) -> dict:
        calls.append(name)
        assert arguments["url"] == "https://example.com/page"
        return _ok("A" * 400)

    result = retrieve_with_fallback(
        "https://example.com/page",
        call_tool=caller,
        min_chars=200,
    )

    assert result["status"] == "SUCCESS"
    assert result["final_tool"] == "reach_read_url"
    assert result["escalation_count"] == 0
    assert calls == ["reach_read_url"]


def test_tool_error_escalates_from_jina_to_scrapling() -> None:
    calls: list[str] = []

    def caller(name: str, arguments: dict) -> dict:
        del arguments
        calls.append(name)
        if name == "reach_read_url":
            return _error("Jina anti-bot failure")
        if name == "scrapling__fetch":
            return _ok("B" * 400)
        raise AssertionError(f"unexpected tool: {name}")

    result = retrieve_with_fallback(
        "https://example.com/page",
        call_tool=caller,
        min_chars=200,
    )

    assert result["status"] == "SUCCESS"
    assert result["final_tool"] == "scrapling__fetch"
    assert [item["status"] for item in result["attempts"]] == [
        "FAILED",
        "SUCCESS",
    ]
    assert calls == ["reach_read_url", "scrapling__fetch"]


def test_missing_expected_evidence_escalates_to_stealthy() -> None:
    calls: list[str] = []

    def caller(name: str, arguments: dict) -> dict:
        del arguments
        calls.append(name)
        if name == "reach_read_url":
            return _ok("A" * 400)
        if name == "scrapling__fetch":
            return _ok("B" * 400 + " one-token")
        if name == "scrapling__stealthy_fetch":
            return _ok("C" * 400 + " alpha beta")
        raise AssertionError(f"unexpected tool: {name}")

    result = retrieve_with_fallback(
        "https://example.com/page",
        call_tool=caller,
        required_terms=["alpha", "beta"],
        min_chars=200,
    )

    assert result["status"] == "SUCCESS"
    assert result["final_tool"] == "scrapling__stealthy_fetch"
    assert result["attempts"][0]["reason_code"] == "MISSING_REQUIRED_TERMS"
    assert result["attempts"][1]["reason_code"] == "MISSING_REQUIRED_TERMS"
    assert result["attempts"][2]["reason_code"] == "CONTENT_ACCEPTED"
    assert calls == [
        "reach_read_url",
        "scrapling__fetch",
        "scrapling__stealthy_fetch",
    ]


def test_antibot_representation_is_blocked_and_escalated() -> None:
    def caller(name: str, arguments: dict) -> dict:
        del arguments
        if name == "reach_read_url":
            return _ok("Verify you are human " + "x" * 400)
        return _ok("usable " + "y" * 400)

    result = retrieve_with_fallback(
        "https://example.com/page",
        call_tool=caller,
        min_chars=200,
    )

    assert result["status"] == "SUCCESS"
    assert result["attempts"][0]["status"] == "BLOCKED"
    assert result["attempts"][0]["reason_code"] == "ANTI_BOT_CHALLENGE"
    assert result["final_tool"] == "scrapling__fetch"


def test_all_read_only_paths_fail_without_browser_adapter() -> None:
    def caller(name: str, arguments: dict) -> dict:
        del name, arguments
        return _error("backend unavailable")

    result = retrieve_with_fallback(
        "https://example.com/page",
        call_tool=caller,
    )

    assert result["status"] == "SOURCE_UNAVAILABLE"
    assert result["browser_fallback_configured"] is False
    assert result["browser_required"] is True
    assert len(result["attempts"]) == 3
    assert all(item["status"] == "FAILED" for item in result["attempts"])


def test_configured_browser_is_last_resort_only() -> None:
    calls: list[str] = []

    def caller(name: str, arguments: dict) -> dict:
        del arguments
        calls.append(name)
        if name == "patchright__fetch":
            return _ok("browser result " + "z" * 400)
        return _error("failed")

    result = retrieve_with_fallback(
        "https://example.com/page",
        call_tool=caller,
        browser_tool="patchright__fetch",
    )

    assert result["status"] == "SUCCESS"
    assert result["final_tool"] == "patchright__fetch"
    assert result["browser_fallback_configured"] is True
    assert result["browser_required"] is False
    assert calls == [
        "reach_read_url",
        "scrapling__fetch",
        "scrapling__stealthy_fetch",
        "patchright__fetch",
    ]


def test_remote_json_content_is_unwrapped_before_validation() -> None:
    payload = json.dumps({"content": "needle " + "x" * 400})

    def caller(name: str, arguments: dict) -> dict:
        del name, arguments
        return _ok(payload)

    result = retrieve_with_fallback(
        "https://example.com/page",
        call_tool=caller,
        required_terms=["needle"],
    )

    assert result["status"] == "SUCCESS"
    assert result["content"].startswith("needle")



def test_antibot_error_is_recorded_as_blocked() -> None:
    calls: list[str] = []

    def caller(name: str, arguments: dict) -> dict:
        del arguments
        calls.append(name)
        if name == "reach_read_url":
            return _error("Jina Reader 返回了反爬验证页")
        return _ok("usable " + "q" * 400)

    result = retrieve_with_fallback(
        "https://example.com/page",
        call_tool=caller,
    )

    assert result["status"] == "SUCCESS"
    assert result["attempts"][0]["status"] == "BLOCKED"
    assert result["attempts"][0]["reason_code"] == "ANTI_BOT_CHALLENGE"
    assert result["retrieval_history"][0]["status"] == "BLOCKED"
