from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from agent_reach.toolbox.gateway import AhmedToolboxGateway
from agent_reach.toolbox.observability import ExecutionLogStore
from agent_reach.toolbox.server import ToolboxRequestHandler


class FakeReach:
    def doctor(self):
        return {"core": {"status": "ok"}}


def _payload(result):
    return json.loads(result["content"][0]["text"])


def test_execution_log_schema_and_indexes():
    store = ExecutionLogStore(":memory:")
    columns = {
        row["name"] for row in store._conn.execute("PRAGMA table_info(execution_log)").fetchall()
    }
    assert {
        "run_id",
        "request_id",
        "tool_name",
        "started_at",
        "completed_at",
        "duration_ms",
        "success",
        "error_type",
        "error_message",
        "retries",
        "fallback_used",
        "result_size_bytes",
        "parent_run_id",
        "workflow_id",
        "orchestration_id",
        "session_id",
    } <= columns
    indexes = {
        row["name"] for row in store._conn.execute("PRAGMA index_list(execution_log)").fetchall()
    }
    assert {
        "idx_execution_log_started_at",
        "idx_execution_log_tool_name",
        "idx_execution_log_run_id",
        "idx_execution_log_success",
    } <= indexes


def test_gateway_logs_success_failure_and_stats_without_inputs():
    store = ExecutionLogStore(":memory:")
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        observability_store=store,
        observability_enabled=True,
        research_enabled=False,
        orchestration_enabled=False,
        runtime_enabled=False,
        ace_enabled=False,
    )

    ok = gateway.call_tool(
        "reach_doctor",
        {"session_id": "safe-session", "private_field": "SENTINEL_INPUT"},
        request_id="rpc-101",
    )
    bad = gateway.call_tool(
        "unknown_tool",
        {"private_field": "SENTINEL_INPUT"},
        request_id="rpc-102",
    )
    assert ok["isError"] is False
    assert bad["isError"] is True

    rows = store.rows(limit=10)
    assert len(rows) == 2
    by_name = {row["tool_name"]: row for row in rows}
    assert by_name["reach_doctor"]["success"] == 1
    assert by_name["reach_doctor"]["request_id"] == "rpc-101"
    assert by_name["reach_doctor"]["session_id"] == "safe-session"
    assert by_name["unknown_tool"]["success"] == 0
    assert "SENTINEL_INPUT" not in json.dumps(rows, ensure_ascii=False)

    stats = _payload(gateway.call_tool("toolbox_execution_stats", {"hours": 24}))
    assert stats["calls"] == 2
    assert stats["success_rate"] == 0.5
    assert stats["p50_ms"] is not None
    assert stats["p95_ms"] is not None
    assert stats["lowest_success_tools"][0]["tool_name"] == "unknown_tool"


def test_fallback_and_tokens_are_logged_only_when_observed():
    store = ExecutionLogStore(":memory:")
    result = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "attempts": [{"status": "FAILED"}, {"status": "SUCCESS"}],
                        "escalation_count": 1,
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 7,
                            "total_tokens": 19,
                        },
                        "workflow_hash": "wf-observed",
                    }
                ),
            }
        ],
        "isError": False,
    }
    meta = store.result_metadata(result)
    assert meta["retries"] == 1
    assert meta["fallback_used"] is True
    assert meta["input_tokens"] == 12
    assert meta["output_tokens"] == 7
    assert meta["total_tokens"] == 19
    assert meta["workflow_id"] == "wf-observed"

    no_usage = store.result_metadata(
        {"content": [{"type": "text", "text": "ok"}], "isError": False}
    )
    assert no_usage["input_tokens"] is None
    assert no_usage["output_tokens"] is None
    assert no_usage["total_tokens"] is None


def test_server_threads_jsonrpc_request_id_into_execution_log():
    store = ExecutionLogStore(":memory:")
    gateway = AhmedToolboxGateway(
        agent_reach=FakeReach(),
        observability_store=store,
        observability_enabled=True,
        research_enabled=False,
        orchestration_enabled=False,
        runtime_enabled=False,
        ace_enabled=False,
    )
    handler = type(
        "ObservedHandler",
        (ToolboxRequestHandler,),
        {"gateway": gateway, "auth_token": ""},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 4242,
                "method": "tools/call",
                "params": {"name": "reach_doctor", "arguments": {}},
            }
        ).encode()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/mcp",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read())
        conn.close()
        assert response.status == 200
        assert payload["id"] == 4242
        rows = store.rows(limit=5)
        assert rows[0]["request_id"] == "4242"
        assert rows[0]["tool_name"] == "reach_doctor"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
