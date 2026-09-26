from __future__ import annotations

import sqlite3

from agent_reach.toolbox.research import ResearchStore
from agent_reach.toolbox.retrieval import MAX_RETRIEVAL_ATTEMPTS, retrieve_with_fallback


def _ok(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _err(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def test_fallback_audit_records_start_duration_reason_and_next_backend():
    def caller(name, arguments):
        del arguments
        if name == "reach_read_url":
            return _err("upstream temporarily unavailable")
        if name == "scrapling__fetch":
            return _ok("accepted " + ("x" * 400))
        raise AssertionError(name)

    result = retrieve_with_fallback(
        "https://example.com",
        call_tool=caller,
        min_chars=100,
    )
    assert result["status"] == "SUCCESS"
    assert result["max_attempts"] == MAX_RETRIEVAL_ATTEMPTS == 5
    assert result["retry_policy"] == "fail-fast-per-backend"
    assert len(result["attempts"]) == 2

    first, second = result["attempts"]
    assert first["attempt_index"] == 1
    assert first["started_at"]
    assert first["duration_ms"] >= 0
    assert first["reason_code"] == "TOOL_ERROR"
    assert first["next_backend"] == "scrapling__fetch"

    assert second["attempt_index"] == 2
    assert second["status"] == "SUCCESS"
    assert second["next_backend"] == "scrapling__stealthy_fetch"


def test_rate_limit_is_classified_and_escalated_without_loop():
    calls = []

    def caller(name, arguments):
        del arguments
        calls.append(name)
        if name == "reach_read_url":
            return _err("429 rate limit")
        if name == "scrapling__fetch":
            return _ok("accepted " + ("y" * 400))
        raise AssertionError(name)

    result = retrieve_with_fallback(
        "https://example.com/rate-limited",
        call_tool=caller,
        min_chars=100,
    )
    assert result["attempts"][0]["reason_code"] == "RATE_LIMITED"
    assert calls == ["reach_read_url", "scrapling__fetch"]
    assert len(calls) < MAX_RETRIEVAL_ATTEMPTS


def test_all_backends_fail_returns_explicit_unavailable_with_history():
    def caller(name, arguments):
        del name, arguments
        return _err("dependency unavailable")

    result = retrieve_with_fallback(
        "https://example.com/unavailable",
        call_tool=caller,
        browser_tool="reach_browser_read_url",
        discovery_tool="reach_web_search",
        min_chars=100,
    )
    assert result["status"] == "SOURCE_UNAVAILABLE"
    assert result["content"] == ""
    assert result["final_tool"] is None
    assert len(result["attempts"]) == MAX_RETRIEVAL_ATTEMPTS
    assert all(item["status"] == "FAILED" for item in result["attempts"])
    assert result["attempts"][-1]["next_backend"] is None


def test_sqlite_lock_failure_is_recoverable_without_corruption(tmp_path):
    path = tmp_path / "locked.db"
    store = ResearchStore(str(path))
    blocker = sqlite3.connect(path)
    blocker.execute("PRAGMA journal_mode = WAL")
    blocker.execute("BEGIN IMMEDIATE")
    store._conn.execute("PRAGMA busy_timeout = 1")

    try:
        try:
            store.create_run("must fail while locked")
        except sqlite3.OperationalError as exc:
            assert "locked" in str(exc).lower()
        else:
            raise AssertionError("expected SQLite lock failure")
    finally:
        blocker.rollback()
        blocker.close()

    recovered = store.create_run("after lock release")
    assert recovered["run_id"]
    audit = store.audit_run(recovered["run_id"])
    assert audit["passed"] is True
