from __future__ import annotations

import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import feedparser
import pytest

from agent_reach.toolbox.ace import ACEStore
from agent_reach.toolbox.ace_mcp import handle_ace_tool
from agent_reach.toolbox.freshness import evaluate_freshness
from agent_reach.toolbox.gateway import AhmedToolboxGateway, RemoteMCPError
from agent_reach.toolbox.orchestration import OrchestrationStore
from agent_reach.toolbox.research import ResearchStore
from agent_reach.toolbox.retrieval import retrieve_with_fallback
from agent_reach.toolbox.runtime import RuntimeStore
from agent_reach.toolbox.server import ToolboxRequestHandler

from .framework import load_cases, run_case

CASES = load_cases()
DETERMINISTIC = [item for item in CASES if item["tier"] == "deterministic_core"]
NOT_PRESENT = [item for item in CASES if item["tier"] == "not_present"]


class FakeReach:
    def doctor(self):
        return {"core": {"status": "ok"}}


class FailingRemote:
    def list_tools(self):
        raise RemoteMCPError("simulated remote unavailable")


def _payload(result):
    return json.loads(result["content"][0]["text"])


def _ok(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _err(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _gateway(tmp_path, *, research=True, orchestration=True, runtime=True, remotes=None):
    return AhmedToolboxGateway(
        remotes=remotes or {},
        agent_reach=FakeReach(),
        research_store=ResearchStore(str(tmp_path / "research.db")),
        research_enabled=research,
        orchestration_store=OrchestrationStore(str(tmp_path / "orch.db")),
        orchestration_enabled=orchestration,
        runtime_store=RuntimeStore(str(tmp_path / "runtime.db")),
        runtime_enabled=runtime,
    )


def _seed(store, obs="ACTUAL", *, slug="seed", publisher="Example", content=None):
    run = store.create_run(f"golden-{slug}")
    body = content or f"{slug} observed value 10"
    source = store.record_source(
        run["run_id"],
        url=f"https://{slug}.example.com/item",
        content=body,
        publisher=publisher,
        retrieval_tool="golden_fixture",
        retrieval_method="deterministic",
        retrieval_status="SUCCESS",
        publication_date="2026-09-23",
        data_cutoff="2026-09-23T10:00:00Z",
        retrieved_at="2026-09-23T10:05:00+00:00",
    )
    evidence = store.add_evidence(
        run["run_id"],
        source_id=source["source_id"],
        supporting_passage=body,
        observation_type=obs,
        value=10,
        unit="units",
    )
    claim = store.add_claim(
        run["run_id"],
        statement=body,
        classification="VERIFIED",
        observation_type=obs,
        supporting_evidence_ids=[evidence["evidence_id"]],
    )
    return run, source, evidence, claim


def _output(store, run, claim, obs="ACTUAL"):
    return store.create_output(
        run["run_id"],
        consumer_type="golden",
        output_type="GOLDEN_RESULT",
        fragments=[{
            "content": claim["statement"],
            "claim_ids": [claim["claim_id"]],
            "asserted_observation_type": obs,
        }],
    )


def _research_case(number):
    store = ResearchStore(":memory:")
    if number == 16:
        run = store.create_run("golden start")
        assert run["run_id"]
        return "ResearchRun created", {"run_id": run["run_id"]}
    if number == 17:
        run = store.create_run("source")
        source = store.record_source(
            run["run_id"], url="https://example.com/source", content="source body",
            retrieval_tool="fixture", retrieval_method="deterministic",
        )
        assert source["source_id"]
        return "Source recorded", {"source_id": source["source_id"]}
    if number == 18:
        run, source, evidence, _ = _seed(store, slug="evidence")
        assert evidence["source_id"] == source["source_id"]
        return "Evidence linked to source", {"run_id": run["run_id"], "evidence_id": evidence["evidence_id"]}
    if number == 19:
        run, _, evidence, claim = _seed(store, slug="claim")
        assert evidence["evidence_id"] in [x["evidence_id"] for x in store.export_run(run["run_id"])["evidence"]]
        return "Claim linked to evidence", {"claim_id": claim["claim_id"]}
    if number in {20, 21}:
        obs = "FORECAST" if number == 20 else "ESTIMATE"
        run, _, _, claim = _seed(store, obs, slug=obs.lower())
        with pytest.raises(ValueError, match="semantic promotion/mismatch"):
            _output(store, run, claim, "ACTUAL")
        return f"{obs} semantic promotion blocked", {"claim_id": claim["claim_id"]}
    if number == 22:
        run = store.create_run("mixed")
        claims = []
        for obs, slug in (("ACTUAL", "actual"), ("FORECAST", "forecast")):
            source = store.record_source(
                run["run_id"], url=f"https://{slug}.example.com", content=slug,
                retrieval_tool="fixture", retrieval_method="deterministic",
            )
            ev = store.add_evidence(
                run["run_id"], source_id=source["source_id"],
                supporting_passage=slug, observation_type=obs,
            )
            claims.append(store.add_claim(
                run["run_id"], statement=slug, classification="INFERENCE",
                observation_type=obs, supporting_evidence_ids=[ev["evidence_id"]],
            ))
        out = store.create_output(
            run["run_id"], consumer_type="golden", output_type="MIXED",
            fragments=[{
                "content": "actual plus forecast",
                "claim_ids": [c["claim_id"] for c in claims],
                "asserted_observation_type": "MIXED",
            }],
        )
        assert out["fragments"][0]["asserted_observation_type"] == "MIXED"
        return "Mixed semantics preserved", {"output_id": out["output_id"]}
    if number in {23, 24}:
        run, _, _, claim = _seed(store, slug=f"out-{number}")
        out = _output(store, run, claim)
        if number == 24:
            audit = store.audit_output(out["output_id"])
            assert audit["passed"] is True
            return "Output audit passed", {"output_id": out["output_id"], "checks": audit["checks"]}
        return "Output created", {"output_id": out["output_id"]}
    if number == 25:
        result = evaluate_freshness(
            data_cutoff="2026-09-23T06:00:00Z",
            publication_date="2026-09-23",
            retrieved_at="2026-09-23T10:00:00Z",
            policy_name="market_price",
            as_of="2026-09-23T10:00:00Z",
        )
        assert result["status"] == "STALE"
        return "Stale market data detected", result
    if number == 26:
        result = evaluate_freshness(
            data_cutoff="2018", publication_date="2019",
            retrieved_at="2026-09-23T10:00:00Z",
            policy_name="academic_evidence", as_of="2026-09-23T10:00:00Z",
        )
        assert result["status"] == "AGE_REPORTED"
        return "Historical academic evidence preserved", result
    if number == 27:
        run = store.create_run("versioning")
        first = store.record_source(
            run["run_id"], url="https://example.com/v", content="v1",
            retrieval_tool="fixture", retrieval_method="deterministic",
        )
        second = store.record_source(
            run["run_id"], url="https://example.com/v", content="v2",
            retrieval_tool="fixture", retrieval_method="deterministic",
        )
        assert second["version_number"] == 2 and second["previous_source_id"] == first["source_id"]
        return "Source version chain preserved", {"v1": first["source_id"], "v2": second["source_id"]}
    if number in {28, 29, 34, 35}:
        run = store.create_run(f"lineage-{number}")
        sources, evidence_ids = [], []
        for idx, pub in enumerate(("Publisher A", "Publisher B")):
            source = store.record_source(
                run["run_id"], url=f"https://lineage-{idx}.example.com/x",
                content=("same representation" if number == 34 else f"representation {idx}"),
                publisher=("Same Publisher" if number == 28 else pub),
                retrieval_tool="fixture", retrieval_method="deterministic",
            )
            ev = store.add_evidence(
                run["run_id"], source_id=source["source_id"],
                supporting_passage=f"support {idx}", observation_type="ACTUAL",
            )
            sources.append(source)
            evidence_ids.append(ev["evidence_id"])
        if number in {29, 35}:
            rel = store.record_source_relationship(
                run["run_id"], source_id=sources[0]["source_id"],
                related_source_id=sources[1]["source_id"],
                relationship_type="INDEPENDENT_OF", basis="independent collection verified",
            )
            assert rel["relationship_type"] == "INDEPENDENT_OF"
        elif number == 34:
            rel = store.record_source_relationship(
                run["run_id"], source_id=sources[0]["source_id"],
                related_source_id=sources[1]["source_id"],
                relationship_type="MIRRORS", basis="same representation",
            )
            assert rel["relationship_type"] == "MIRRORS"
        claim = store.add_claim(
            run["run_id"], statement="lineage claim", classification="VERIFIED",
            observation_type="ACTUAL", supporting_evidence_ids=evidence_ids,
        )
        evaluation = store.evaluate_claim_source_independence(claim["claim_id"])
        if number in {28, 34}:
            assert evaluation["effective_lineage_count"] == 1
        if number in {29, 35}:
            assert evaluation["status"] == "VERIFIED_INDEPENDENT_LINEAGES"
        return "Source lineage evaluated", evaluation
    if number == 30:
        run, _, _, _ = _seed(store, slug="ledger")
        exported = store.export_run(run["run_id"])
        assert exported["ledger"]
        return "Ledger exported", {"ledger_rows": len(exported["ledger"])}
    if number == 31:
        run, _, _, claim = _seed(store, slug="broken")
        with store._lock, store._conn:
            store._conn.execute("DELETE FROM claim_evidence WHERE claim_id = ?", (claim["claim_id"],))
        audit = store.audit_run(run["run_id"])
        assert audit["passed"] is False
        return "Broken provenance detected", audit
    if number == 32:
        run, _, _, _ = _seed(store, slug="complete")
        finished = store.complete_run(run["run_id"])
        assert finished["completed_at"]
        return "ResearchRun completed", {"completed_at": finished["completed_at"]}
    if number == 33:
        result = evaluate_freshness(
            data_cutoff="2026-09-23T09:00:00Z", publication_date=None,
            retrieved_at="2026-09-23T10:00:00Z", policy_name="custom_max_age",
            max_age_seconds=7200, as_of="2026-09-23T10:00:00Z",
        )
        assert result["status"] == "FRESH" and result["max_age_seconds"] == 7200.0
        return "Custom freshness policy enforced", result
    raise AssertionError(f"unhandled research case {number}")


def _runtime_case(number, tmp_path):
    gateway = _gateway(tmp_path)
    if number in {51, 52}:
        steps = [
            {"id": "one", "tool_name": "reach_doctor", "arguments": {}},
            {"id": "two", "tool_name": "reach_doctor", "arguments": {}},
        ]
        if number == 51:
            steps[1]["depends_on"] = ["one"]
        result = _payload(gateway.call_tool("runtime_execute_workflow", {
            "steps": steps, "max_parallel": 2, "auto_learn": False,
        }))
        assert result["ok_count"] == 2
        return ("Sequential DAG completed" if number == 51 else "Parallel DAG completed"), result
    if number == 53:
        parent = _payload(gateway.call_tool("orchestration_start", {
            "objective": "golden delegation", "mode": "general",
            "budget": {"tool_calls": 2, "network_calls": 0},
        }))
        result = _payload(gateway.call_tool("runtime_delegate", {
            "orchestration_id": parent["orchestration_id"],
            "tasks": [
                {"id": "a", "objective": "a", "steps": [{"id": "d", "tool_name": "reach_doctor", "role": "orchestrator"}], "budget": {"tool_calls": 1, "network_calls": 0}},
                {"id": "b", "objective": "b", "steps": [{"id": "d", "tool_name": "reach_doctor", "role": "orchestrator"}], "budget": {"tool_calls": 1, "network_calls": 0}},
            ],
            "max_parallel": 2,
        }))
        assert result["status"] == "ok"
        return "Delegation completed with isolated children", {"tasks": result["tasks"]}
    if number == 56:
        _payload(gateway.call_tool("runtime_memory_put", {"key": "golden-memory", "content": "golden durable value"}))
        found = _payload(gateway.call_tool("runtime_memory_search", {"query": "durable value"}))
        assert found["results"][0]["memory_key"] == "golden-memory"
        return "Durable memory round-trip succeeded", {"count": len(found["results"])}
    if number == 57:
        _payload(gateway.call_tool("runtime_execute_workflow", {
            "session_id": "golden-session",
            "steps": [{"id": "d", "tool_name": "reach_doctor"}],
            "auto_learn": False,
        }))
        found = _payload(gateway.call_tool("runtime_session_search", {
            "query": "workflow_hash", "session_id": "golden-session",
        }))
        assert found["results"]
        return "Session search found workflow", {"count": len(found["results"])}
    if number in {58, 60}:
        _payload(gateway.call_tool("runtime_skill_save", {
            "name": f"golden-skill-{number}",
            "description": "golden successful skill",
            "workflow": [{"id": "d", "tool_name": "reach_doctor"}],
        }))
        result = _payload(gateway.call_tool("runtime_skill_execute", {"name": f"golden-skill-{number}"}))
        assert result["status"] == "ok"
        if number == 60:
            skill = _payload(gateway.call_tool("runtime_skill_get", {"name": f"golden-skill-{number}"}))
            assert skill["successes"] >= 1 and skill["score"] > 0.5
            return "Skill score updated from outcome", {"score": skill["score"], "successes": skill["successes"]}
        return "Skill executed successfully", {"status": result["status"]}
    if number == 59:
        _payload(gateway.call_tool("runtime_skill_save", {
            "name": "golden-failing-skill",
            "workflow": [{"id": "bad", "tool_name": "golden_nonexistent_tool"}],
        }))
        result = _payload(gateway.call_tool("runtime_skill_execute", {"name": "golden-failing-skill"}))
        skill = _payload(gateway.call_tool("runtime_skill_get", {"name": "golden-failing-skill"}))
        assert result["status"] == "error" and skill["failures"] >= 1
        return "Failed skill recorded failure honestly", {"failures": skill["failures"], "score": skill["score"]}
    if number == 61:
        _payload(gateway.call_tool("runtime_skill_save", {
            "name": "golden-rollback", "workflow": [{"id": "a", "tool_name": "reach_doctor"}],
            "change_note": "v1",
        }))
        _payload(gateway.call_tool("runtime_skill_save", {
            "name": "golden-rollback", "workflow": [{"id": "b", "tool_name": "reach_doctor"}],
            "change_note": "v2",
        }))
        rolled = _payload(gateway.call_tool("runtime_skill_rollback", {"name": "golden-rollback", "revision": 1}))
        assert rolled["revision"] >= 3
        return "Skill rollback created new active revision", {"revision": rolled["revision"]}
    if number == 62:
        started = _payload(gateway.call_tool("orchestration_start", {
            "objective": "golden research routing", "mode": "research",
            "budget": {"tool_calls": 3, "network_calls": 0},
        }))
        executed = _payload(gateway.call_tool("orchestration_execute", {
            "orchestration_id": started["orchestration_id"], "role": "evidence_analyst",
            "tool_name": "research_record_source",
            "arguments": {
                "url": "https://example.com/routed", "content": "routed evidence",
                "retrieval_tool": "fixture", "retrieval_method": "deterministic",
            },
        }))
        assert executed["executed"] is True
        return "Research specialist routing authorized correct lane", executed["authorization"]
    if number == 64:
        started = _payload(gateway.call_tool("orchestration_start", {
            "objective": "golden budget", "mode": "general",
            "budget": {"tool_calls": 1, "network_calls": 0},
        }))
        oid = started["orchestration_id"]
        first = _payload(gateway.call_tool("orchestration_execute", {
            "orchestration_id": oid, "role": "orchestrator",
            "tool_name": "reach_doctor", "arguments": {},
        }))
        second = _payload(gateway.call_tool("orchestration_execute", {
            "orchestration_id": oid, "role": "orchestrator",
            "tool_name": "reach_doctor", "arguments": {},
        }))
        assert first["executed"] is True and second["executed"] is False
        assert second["authorization"]["reason"] == "tool_budget_exhausted"
        return "Budget enforced", second["authorization"]
    if number == 65:
        store = OrchestrationStore(str(tmp_path / "journal-only.db"))
        run = store.create_run("golden journal", mode="general")
        store.authorize_tool(run["orchestration_id"], "orchestrator", "reach_doctor", {})
        report = store.verify_journal(run["orchestration_id"])
        assert report["passed"] is True
        return "Append-only journal verified", report
    raise AssertionError(f"unhandled runtime case {number}")


def _retrieval_case(number):
    calls = []
    def caller(name, arguments):
        del arguments
        calls.append(name)
        if number in {4, 66}:
            return _err("Jina unavailable") if name == "reach_read_url" else _ok("usable " + "x" * 400)
        if number == 5:
            if name in {"reach_read_url", "scrapling__fetch"}:
                return _err("backend unavailable")
            return _ok("stealth " + "x" * 400)
        if number == 6:
            return _ok("browser " + "x" * 400) if name == "golden_browser" else _err("backend unavailable")
        if number == 7:
            return _err("backend unavailable")
        raise AssertionError(name)
    result = retrieve_with_fallback(
        "https://example.com/golden", call_tool=caller,
        browser_tool=("golden_browser" if number == 6 else None),
        min_chars=100,
    )
    if number in {4, 66}:
        assert result["final_tool"] == "scrapling__fetch"
    if number == 5:
        assert result["final_tool"] == "scrapling__stealthy_fetch"
    if number == 6:
        assert result["final_tool"] == "golden_browser"
    if number == 7:
        assert result["status"] in {"UNAVAILABLE", "SOURCE_UNAVAILABLE"}
        assert not result.get("content")
    return "Fallback state machine behaved deterministically", {"status": result["status"], "attempts": result["attempts"], "calls": calls}


def _http_server(gateway, token=""):
    handler = type("GoldenHandler", (ToolboxRequestHandler,), {"gateway": gateway, "auth_token": token})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _post(port, body, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    base = {"Content-Type": "application/json"}
    if headers:
        base.update(headers)
    connection.request("POST", "/mcp", body=body, headers=base)
    response = connection.getresponse()
    data = response.read()
    status = response.status
    connection.close()
    return status, data


def _failure_case(number, tmp_path):
    if number == 66:
        return _retrieval_case(number)
    if number in {67, 75}:
        gateway = _gateway(tmp_path, remotes={"exa": FailingRemote()})
        names = {tool["name"] for tool in gateway.list_tools()}
        assert "reach_doctor" in names
        if number == 75:
            doctor = _payload(gateway.call_tool("reach_doctor", {}))
            assert doctor
            return "Remote dependency failure did not kill local core", {"local_tools": len(names), "doctor": doctor}
        return "Exa remote failure isolated from local tool surface", {"local_tools": len(names)}
    if number == 68:
        gateway = _gateway(tmp_path)
        parent = _payload(gateway.call_tool("orchestration_start", {
            "objective": "golden failing child", "mode": "general",
            "budget": {"tool_calls": 2, "network_calls": 0},
        }))
        result = _payload(gateway.call_tool("runtime_delegate", {
            "orchestration_id": parent["orchestration_id"],
            "tasks": [{"id": "bad", "objective": "fail safely", "steps": [{"id": "x", "tool_name": "golden_nonexistent_tool", "role": "orchestrator"}]}],
            "max_parallel": 1,
        }))
        assert result["status"] != "ok"
        assert _payload(gateway.call_tool("reach_doctor", {}))
        return "Subagent/delegated failure remained isolated", {"delegation_status": result["status"]}
    if number == 70:
        path = tmp_path / "interrupted.db"
        first = ResearchStore(str(path))
        run, source, _, _ = _seed(first, slug="interrupt")
        first._conn.close()
        second = ResearchStore(str(path))
        restored = second.get_run(run["run_id"])
        assert restored["completed_at"] is None
        assert second.get_source(source["source_id"], run_id=run["run_id"])["source_id"] == source["source_id"]
        return "Interrupted ResearchRun remained recoverable", {"run_id": run["run_id"]}
    if number in {71,72,73}:
        gateway = _gateway(tmp_path)
        token = "golden-secret" if number == 73 else ""
        server, thread = _http_server(gateway, token)
        try:
            port = server.server_address[1]
            if number == 71:
                status, _ = _post(port, b"{")
                assert status == 400
            elif number == 72:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.putrequest("POST", "/mcp")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", str(1024 * 1024 + 1))
                connection.endheaders()
                response = connection.getresponse()
                status = response.status
                response.read()
                connection.close()
                assert status == 413
            else:
                body = json.dumps({"jsonrpc":"2.0","id":1,"method":"ping"}).encode()
                status, _ = _post(port, body)
                assert status == 401
                status2, _ = _post(port, body, {"Authorization":"Bearer golden-secret"})
                assert status2 == 200
                status = status2
            return "MCP boundary rejected invalid request safely", {"http_status": status}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    if number == 74:
        gateway = AhmedToolboxGateway(
            agent_reach=FakeReach(),
            research_enabled=False,
            orchestration_enabled=False,
            runtime_enabled=False,
            ace_enabled=False,
        )
        names = {tool["name"] for tool in gateway.list_tools()}
        assert "research_start_run" not in names and "reach_doctor" in names
        return "Research-disabled environment preserved local core", {"tool_count": len(names)}
    raise AssertionError(f"unhandled failure case {number}")


def _integration_case(number, tmp_path):
    if number == 78:
        ace = ACEStore(str(tmp_path / "ace.db"))
        research = ResearchStore(str(tmp_path / "ace-research.db"))
        campaign = ace.create_campaign(
            "golden content research", objective="conversion",
            platforms=["instagram"], free_only=True,
        )
        def execute(name, arguments):
            del arguments
            if name == "reach_web_search":
                return _ok("https://example.com/golden-source")
            if name == "reach_retrieve_url":
                return _ok("Observed content pattern from a retrieved source.")
            raise AssertionError(name)
        result = handle_ace_tool(
            ace, "ace_research",
            {"campaign_id": campaign["campaign_id"], "queries": ["golden"], "max_sources": 1},
            research_store=research, execute_tool=execute,
        )
        assert result["research_run_id"] and result["findings"]
        exported = research.export_run(result["research_run_id"])
        assert exported["sources"] and exported["evidence"]
        return "ACE research recorded Research Engine provenance", {"run_id": result["research_run_id"], "evidence": len(exported["evidence"])}
    if number == 79:
        gateway = _gateway(tmp_path)
        server, thread = _http_server(gateway)
        try:
            port = server.server_address[1]
            body = json.dumps({"jsonrpc":"2.0","id":1,"method":"ping"}).encode()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: _post(port, body)[0], range(2)))
            assert results == [200, 200]
            return "Two independent MCP clients served concurrently", {"statuses": results}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    if number == 80:
        store = ResearchStore(str(tmp_path / "full-flow.db"))
        run = store.create_run("golden full pipeline")
        source = store.record_source(
            run["run_id"], url="https://example.com/full", content="Observed fact is 42.",
            retrieval_tool="fixture-search+read", retrieval_method="deterministic",
            retrieval_status="SUCCESS",
            retrieval_history=[
                {"stage":"DISCOVERY","tool":"fixture_search","method":"deterministic","status":"SUCCESS"},
                {"stage":"RETRIEVAL","tool":"fixture_read","method":"deterministic","status":"SUCCESS"},
            ],
        )
        evidence = store.add_evidence(
            run["run_id"], source_id=source["source_id"],
            supporting_passage="Observed fact is 42.", observation_type="ACTUAL", value=42,
        )
        claim = store.add_claim(
            run["run_id"], statement="Observed fact is 42.", classification="VERIFIED",
            observation_type="ACTUAL", supporting_evidence_ids=[evidence["evidence_id"]],
        )
        out = _output(store, run, claim)
        audit = store.audit_output(out["output_id"])
        assert audit["passed"] is True
        run_audit = store.audit_run(run["run_id"])
        assert run_audit["passed"] is True
        return "Full source-evidence-claim-output-audit pipeline passed", {
            "run_id": run["run_id"], "source_id": source["source_id"],
            "evidence_id": evidence["evidence_id"], "claim_id": claim["claim_id"],
            "output_id": out["output_id"],
        }
    raise AssertionError(f"unhandled integration case {number}")


def _execute(case, tmp_path):
    n = int(case["number"])
    if n in {4, 5, 6, 7, 66}:
        return _retrieval_case(n)
    if n == 14:
        parsed = feedparser.parse(b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Golden</title><item><title>One</title><link>https://example.com/1</link></item></channel></rss>""")
        assert parsed.feed.title == "Golden" and parsed.entries[0].title == "One"
        return "RSS parsed", {"entries": len(parsed.entries)}
    if 16 <= n <= 35:
        return _research_case(n)
    if n in {51, 52, 53, 56, 57, 58, 59, 60, 61, 62, 64, 65}:
        return _runtime_case(n, tmp_path)
    if n in {67, 68, 70, 71, 72, 73, 74, 75}:
        return _failure_case(n, tmp_path)
    if n in {78, 79, 80}:
        return _integration_case(n, tmp_path)
    raise AssertionError(f"deterministic case has no executor: {n}")


def test_golden_manifest_is_exact_and_complete():
    assert len(CASES) == 80
    assert [item["number"] for item in CASES] == list(range(1,81))
    for item in CASES:
        assert {"id","category","preconditions","action","expected","evidence","tier"} <= set(item)


@pytest.mark.parametrize("case", DETERMINISTIC, ids=lambda c: c["id"])
def test_golden_deterministic_core(case, tmp_path):
    result = run_case(case, lambda item: _execute(item, tmp_path))
    assert result.status == "PASS", json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


@pytest.mark.parametrize("case", NOT_PRESENT, ids=lambda c: c["id"])
def test_golden_not_present_is_explicit(case):
    pytest.skip(f"NOT_PRESENT: {case['name']}")
