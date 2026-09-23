"""Selective ECC-inspired orchestration controls for Ahmed Toolbox."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EFFECT_CLASSES = ("SE0", "SE1", "SE2", "SE3", "SE4")
GENESIS_HASH = "0" * 64

DEFAULT_SPECIALISTS = {
    "orchestrator": ["reach_doctor", "research_export_ledger", "research_audit_run"],
    "discoverer": ["reach_web_search"],
    "retriever": ["reach_read_url", "reach_retrieve_url", "reach_media_ingest", "scrapling__*"],
    "evidence_analyst": ["research_*"],
    "adversarial_reviewer": ["reach_web_search", "reach_read_url", "reach_retrieve_url", "scrapling__*", "research_*"],
    "verifier": ["research_export_ledger", "research_audit_run", "research_get_*", "research_evaluate_*"],
    "synthesizer": ["research_create_output", "research_get_output", "research_audit_output"],
    "growth_experimenter": [
        "ace_*", "reach_web_search", "reach_read_url", "reach_retrieve_url",
        "reach_youtube_browser_inspect", "research_*",
    ],
}

READ_ONLY_REMOTE_TOOLS = {
    "scrapling__bulk_get", "scrapling__fetch", "scrapling__bulk_fetch",
    "scrapling__stealthy_fetch", "scrapling__bulk_stealthy_fetch",
}
NETWORK_TOOLS = {
    "reach_web_search",
    "reach_read_url",
    "reach_retrieve_url",
    "reach_media_ingest",
    "reach_youtube_browser_inspect",
} | READ_ONLY_REMOTE_TOOLS
OUTPUT_TOOLS = {"research_create_output"}
RUN_SCOPED_TOOLS = {
    "research_record_source", "research_record_source_relationship", "research_add_evidence",
    "research_add_claim", "research_create_output", "research_export_ledger", "research_audit_run",
    "research_complete_run",
}
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}", re.I),
    re.compile(r"\b(?:secret|token|password|api[_-]?key)\s*[:=]\s*[^\s,;]{8,}", re.I),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _rank(effect: str) -> int:
    return EFFECT_CLASSES.index(effect)


def classify_tool_effect(name: str) -> str | None:
    if name in {"reach_doctor", "reach_web_search", "reach_read_url", "reach_retrieve_url", "reach_youtube_browser_inspect"}:
        return "SE0"
    if name == "reach_media_ingest" or name.startswith("research_"):
        return "SE1"
    if name in {"ace_status", "ace_report", "ace_next"}:
        return "SE0"
    if name.startswith("ace_"):
        return "SE1"
    if name in READ_ONLY_REMOTE_TOOLS:
        return "SE0"
    return None


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def scan_secret_canaries(value: Any) -> list[str]:
    hits = []
    for text in _strings(value):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(pattern.pattern)
    return sorted(set(hits))


class OrchestrationStore:
    """SQLite-backed control plane with bounded budgets and a hash-linked journal."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    @classmethod
    def from_environment(cls) -> "OrchestrationStore":
        return cls(os.path.expanduser(os.environ.get(
            "AHMED_ORCHESTRATION_DB_PATH", "~/.agent-reach/orchestration.db"
        )))

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS orchestration_runs (
              orchestration_id TEXT PRIMARY KEY, objective TEXT NOT NULL, mode TEXT NOT NULL,
              status TEXT NOT NULL, research_run_id TEXT, budget_json TEXT NOT NULL,
              specialists_json TEXT NOT NULL, max_effect_class TEXT NOT NULL,
              metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS orchestration_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, orchestration_id TEXT NOT NULL,
              occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, role TEXT,
              tool_name TEXT, effect_class TEXT, payload_json TEXT NOT NULL,
              previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_orch_events_run ON orchestration_events(orchestration_id,id);
            """)

    def create_run(self, objective: str, *, mode: str = "research", research_run_id: str | None = None,
                   budget: dict[str, Any] | None = None, specialists: dict[str, Any] | None = None,
                   max_effect_class: str = "SE1", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not objective.strip():
            raise ValueError("objective is required")
        if max_effect_class not in EFFECT_CLASSES:
            raise ValueError("invalid max_effect_class")
        limits = {"tool_calls": 40, "network_calls": 20}
        limits.update({k: int(v) for k, v in (budget or {}).items() if k in limits})
        if any(v < 0 for v in limits.values()):
            raise ValueError("budget values must be non-negative")
        lanes = specialists or DEFAULT_SPECIALISTS
        oid = "orch_" + uuid.uuid4().hex
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO orchestration_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (oid, objective.strip(), mode, "ACTIVE", research_run_id, _dump(limits),
                 _dump(lanes), max_effect_class, _dump(metadata or {}), now, None),
            )
        self.append_event(oid, "RUN_STARTED", payload={"objective": objective, "research_run_id": research_run_id})
        return self.get_run(oid)

    def get_run(self, orchestration_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM orchestration_runs WHERE orchestration_id=?", (orchestration_id,)).fetchone()
        if not row:
            raise ValueError("unknown orchestration_id")
        data = dict(row)
        for key in ("budget_json", "specialists_json", "metadata_json"):
            data[key[:-5]] = json.loads(data.pop(key))
        return data

    def append_event(self, orchestration_id: str, event_type: str, *, role: str | None = None,
                     tool_name: str | None = None, effect_class: str | None = None,
                     payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            if not conn.execute("SELECT 1 FROM orchestration_runs WHERE orchestration_id=?", (orchestration_id,)).fetchone():
                raise ValueError("unknown orchestration_id")
            prev = conn.execute(
                "SELECT event_hash FROM orchestration_events WHERE orchestration_id=? ORDER BY id DESC LIMIT 1",
                (orchestration_id,),
            ).fetchone()
            previous_hash = prev["event_hash"] if prev else GENESIS_HASH
            occurred_at = _now()
            body = {"orchestration_id": orchestration_id, "occurred_at": occurred_at, "event_type": event_type,
                    "role": role, "tool_name": tool_name, "effect_class": effect_class, "payload": payload or {},
                    "previous_hash": previous_hash}
            event_hash = _hash(body)
            cur = conn.execute(
                """INSERT INTO orchestration_events
                (orchestration_id,occurred_at,event_type,role,tool_name,effect_class,payload_json,previous_hash,event_hash)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (orchestration_id, occurred_at, event_type, role, tool_name, effect_class,
                 _dump(payload or {}), previous_hash, event_hash),
            )
            body.update({"id": cur.lastrowid, "event_hash": event_hash})
            return body

    def list_events(self, orchestration_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM orchestration_events WHERE orchestration_id=? ORDER BY id", (orchestration_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            out.append(item)
        return out

    def usage(self, orchestration_id: str) -> dict[str, int]:
        events = self.list_events(orchestration_id)
        allowed = [e for e in events if e["event_type"] == "TOOL_ALLOWED"]
        return {
            "tool_calls": len(allowed),
            "network_calls": sum(
                bool(e["tool_name"]) and (
                    e["tool_name"] in NETWORK_TOOLS or "__" in e["tool_name"]
                )
                for e in allowed
            ),
        }

    def authorize_tool(
        self,
        orchestration_id: str,
        role: str,
        tool_name: str,
        arguments: dict[str, Any],
        effect_class: str | None = None,
    ) -> dict[str, Any]:
        run = self.get_run(orchestration_id)
        reason = None
        effect = effect_class if effect_class is not None else classify_tool_effect(tool_name)
        if effect is not None and effect not in EFFECT_CLASSES:
            effect = None
        patterns = run["specialists"].get(role)
        if run["status"] != "ACTIVE":
            reason = "run_not_active"
        elif not patterns:
            reason = "unknown_role"
        elif not any(fnmatch.fnmatchcase(tool_name, p) for p in patterns):
            reason = "tool_not_allowed_for_role"
        elif effect is None:
            reason = "unknown_effect_default_deny"
        elif _rank(effect) > _rank(run["max_effect_class"]):
            reason = "effect_ceiling_exceeded"
        elif scan_secret_canaries(arguments):
            reason = "secret_canary_detected"
        else:
            usage = self.usage(orchestration_id)
            if usage["tool_calls"] >= run["budget"]["tool_calls"]:
                reason = "tool_budget_exhausted"
            elif (tool_name in NETWORK_TOOLS or "__" in tool_name) and usage["network_calls"] >= run["budget"]["network_calls"]:
                reason = "network_budget_exhausted"
        allowed = reason is None
        event = self.append_event(
            orchestration_id,
            "TOOL_ALLOWED" if allowed else "TOOL_DENIED",
            role=role,
            tool_name=tool_name,
            effect_class=effect,
            payload={"argument_hash": _hash(arguments), "reason": reason},
        )
        return {
            "allowed": allowed,
            "reason": reason,
            "effect_class": effect,
            "authorization_event_id": event["id"],
        }

    def record_result(
        self,
        orchestration_id: str,
        authorization_event_id: int,
        role: str,
        tool_name: str,
        result: dict[str, Any],
        effect_class: str | None = None,
    ) -> None:
        with self._connect() as conn:
            authorization = conn.execute(
                """SELECT id, role, tool_name FROM orchestration_events
                WHERE id=? AND orchestration_id=? AND event_type='TOOL_ALLOWED'""",
                (authorization_event_id, orchestration_id),
            ).fetchone()
            duplicate = conn.execute(
                """SELECT 1 FROM orchestration_events
                WHERE orchestration_id=? AND event_type='TOOL_RESULT'
                AND json_extract(payload_json, '$.authorization_event_id')=? LIMIT 1""",
                (orchestration_id, authorization_event_id),
            ).fetchone()
        if not authorization:
            raise ValueError("result requires a matching TOOL_ALLOWED authorization")
        if authorization["role"] != role or authorization["tool_name"] != tool_name:
            raise ValueError("result does not match authorized role/tool")
        if duplicate:
            raise ValueError("authorization already has a recorded result")
        self.append_event(
            orchestration_id,
            "TOOL_RESULT",
            role=role,
            tool_name=tool_name,
            effect_class=(
                effect_class if effect_class is not None else classify_tool_effect(tool_name)
            ),
            payload={
                "authorization_event_id": authorization_event_id,
                "result_hash": _hash(result),
                "is_error": bool(result.get("isError")),
            },
        )

    def unresolved_authorizations(self, orchestration_id: str) -> list[int]:
        events = self.list_events(orchestration_id)
        allowed = {e["id"] for e in events if e["event_type"] == "TOOL_ALLOWED"}
        resolved = {
            int(e["payload"]["authorization_event_id"])
            for e in events
            if e["event_type"] == "TOOL_RESULT"
            and e["payload"].get("authorization_event_id") is not None
        }
        return sorted(allowed - resolved)

    def verify_journal(self, orchestration_id: str) -> dict[str, Any]:
        previous = GENESIS_HASH
        errors = []
        for event in self.list_events(orchestration_id):
            body = {"orchestration_id": orchestration_id, "occurred_at": event["occurred_at"],
                    "event_type": event["event_type"], "role": event["role"], "tool_name": event["tool_name"],
                    "effect_class": event["effect_class"], "payload": event["payload"], "previous_hash": event["previous_hash"]}
            if event["previous_hash"] != previous or _hash(body) != event["event_hash"]:
                errors.append(event["id"])
            previous = event["event_hash"]
        return {"passed": not errors, "invalid_event_ids": errors, "head_hash": previous}

    def status(self, orchestration_id: str) -> dict[str, Any]:
        run = self.get_run(orchestration_id)
        return {"run": run, "usage": self.usage(orchestration_id), "journal": self.verify_journal(orchestration_id)}

    def complete(self, orchestration_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("UPDATE orchestration_runs SET status='COMPLETE', completed_at=? WHERE orchestration_id=?", (_now(), orchestration_id))
        self.append_event(orchestration_id, "RUN_COMPLETED")
        return self.get_run(orchestration_id)


def build_verification_report(store: OrchestrationStore, orchestration_id: str,
                              research_store: Any | None = None) -> dict[str, Any]:
    status = store.status(orchestration_id)
    run = status["run"]
    checks = {
        "journal_integrity": status["journal"]["passed"],
        "tool_budget": status["usage"]["tool_calls"] <= run["budget"]["tool_calls"],
        "network_budget": status["usage"]["network_calls"] <= run["budget"]["network_calls"],
        "all_authorizations_resolved": not store.unresolved_authorizations(orchestration_id),
    }
    research = None
    if run["mode"] == "research":
        if not run.get("research_run_id") or research_store is None:
            checks["research_linked"] = False
        else:
            checks["research_linked"] = True
            try:
                snapshot = research_store.export_run(run["research_run_id"])
                audit = research_store.audit_run(run["research_run_id"])
                research = {"audit": audit}
                checks["research_has_sources"] = bool(snapshot.get("sources"))
                checks["research_has_evidence"] = bool(snapshot.get("evidence"))
                checks["research_has_claims"] = bool(snapshot.get("claims"))
                checks["research_audit"] = bool(audit.get("passed"))
                checks["claims_have_evidence"] = all(
                    bool(claim.get("supporting_evidence_ids"))
                    for claim in snapshot.get("claims", [])
                )
                checks["verified_claims_checked"] = all(
                    int(claim.get("verification_count") or 0) >= 1
                    for claim in snapshot.get("claims", [])
                    if claim.get("classification") == "VERIFIED"
                )
            except (TypeError, ValueError) as exc:
                research = {"error": str(exc)}
                checks["research_audit"] = False
    return {"passed": all(checks.values()), "checks": checks, "usage": status["usage"],
            "journal": status["journal"], "research": research}


def export_handoff(store: OrchestrationStore, orchestration_id: str,
                   research_store: Any | None = None) -> dict[str, Any]:
    status = store.status(orchestration_id)
    verification = build_verification_report(store, orchestration_id, research_store)
    return {"schema": "ahmed-orchestration-handoff/v1", "orchestration_id": orchestration_id,
            "objective": status["run"]["objective"], "status": status["run"]["status"],
            "research_run_id": status["run"].get("research_run_id"), "budget": status["run"]["budget"],
            "usage": status["usage"], "specialists": status["run"]["specialists"],
            "verification": verification, "journal_head": status["journal"]["head_hash"],
            "blockers": [] if verification["passed"] else [k for k, v in verification["checks"].items() if not v]}
