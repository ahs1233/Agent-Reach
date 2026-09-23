"""Hermes-inspired selective agent runtime for Ahmed Toolbox.

This module intentionally keeps the execution surface narrower than arbitrary
Python execution. It provides the same high-value primitive -- many internal
tool calls per model turn -- as a bounded workflow DAG with dependency
resolution, parallel waves, result references, durable memory, and reusable
procedural skills.

The design is original Ahmed Toolbox code informed by public concepts in
NousResearch/hermes-agent (MIT). See docs/HERMES_SELECTIVE_INTEGRATION_2026-09-23.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_MAX_STEPS = 50
_MAX_PARALLEL = 8
_MAX_RESULT_CHARS = 24_000
_STEP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_BLOCKED_RECURSIVE_TOOLS = {
    "runtime_execute_workflow",
    "runtime_delegate",
    "runtime_skill_execute",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _compact(value: Any, max_chars: int = _MAX_RESULT_CHARS) -> Any:
    """Bound returned text without destroying the structured envelope."""
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return value[:max_chars] + "\n[TRUNCATED BY AHMED RUNTIME]"
    if isinstance(value, list):
        return [_compact(item, max_chars) for item in value]
    if isinstance(value, dict):
        return {str(k): _compact(v, max_chars) for k, v in value.items()}
    return value


def _lookup_path(value: Any, path: str) -> Any:
    current = value
    if not path:
        return current
    for token in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"invalid list result reference token: {token}") from exc
        elif isinstance(current, dict):
            if token not in current:
                raise ValueError(f"result reference path not found: {path}")
            current = current[token]
        else:
            raise ValueError(f"cannot descend into result reference path: {path}")
    return current


def resolve_step_references(value: Any, completed: dict[str, dict[str, Any]]) -> Any:
    """Resolve {"$step": "id", "path": "result.content.0.text"} recursively."""
    if isinstance(value, dict):
        if set(value).issubset({"$step", "path"}) and "$step" in value:
            step_id = str(value["$step"])
            if step_id not in completed:
                raise ValueError(f"step reference is not complete: {step_id}")
            return _lookup_path(completed[step_id], str(value.get("path") or ""))
        return {k: resolve_step_references(v, completed) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_step_references(item, completed) for item in value]
    return value


def validate_workflow(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise ValueError("workflow requires at least one step")
    if len(steps) > _MAX_STEPS:
        raise ValueError(f"workflow exceeds {_MAX_STEPS} steps")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise ValueError(f"step {index} must be an object")
        step_id = str(raw.get("id") or f"step_{index + 1}")
        if not _STEP_ID_RE.fullmatch(step_id):
            raise ValueError(f"invalid step id: {step_id}")
        if step_id in ids:
            raise ValueError(f"duplicate step id: {step_id}")
        ids.add(step_id)
        tool_name = str(raw.get("tool_name") or "").strip()
        if not tool_name:
            raise ValueError(f"step {step_id} requires tool_name")
        if tool_name in _BLOCKED_RECURSIVE_TOOLS or tool_name.startswith("orchestration_"):
            raise ValueError(f"recursive control-plane tool denied in workflow: {tool_name}")
        arguments = raw.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError(f"step {step_id} arguments must be an object")
        depends_on = raw.get("depends_on") or []
        if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
            raise ValueError(f"step {step_id} depends_on must be an array of step ids")
        normalized.append({
            "id": step_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "role": str(raw.get("role") or "orchestrator"),
            "depends_on": list(dict.fromkeys(depends_on)),
        })

    by_id = {step["id"]: step for step in normalized}
    for step in normalized:
        unknown = [dep for dep in step["depends_on"] if dep not in by_id]
        if unknown:
            raise ValueError(f"step {step['id']} has unknown dependencies: {unknown}")
        if step["id"] in step["depends_on"]:
            raise ValueError(f"step {step['id']} cannot depend on itself")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        if step_id in visiting:
            raise ValueError("workflow dependency cycle detected")
        visiting.add(step_id)
        for dep in by_id[step_id]["depends_on"]:
            visit(dep)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in by_id:
        visit(step_id)
    return normalized


class RuntimeStore:
    """Durable runtime memory, session index, and versioned procedural skills."""

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).expanduser())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._fts_enabled = False
        self._init_db()

    @classmethod
    def from_environment(cls) -> "RuntimeStore":
        return cls(os.environ.get("AHMED_RUNTIME_DB_PATH", "~/.agent-reach/runtime.db"))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_memory (
                  memory_key TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  content TEXT NOT NULL,
                  tags_json TEXT NOT NULL,
                  metadata_json TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_sessions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id TEXT NOT NULL,
                  role TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  text TEXT NOT NULL,
                  metadata_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_sessions_session
                  ON runtime_sessions(session_id, id);
                CREATE TABLE IF NOT EXISTS runtime_skills (
                  name TEXT PRIMARY KEY,
                  description TEXT NOT NULL,
                  workflow_json TEXT NOT NULL,
                  revision INTEGER NOT NULL,
                  successes INTEGER NOT NULL DEFAULT 0,
                  failures INTEGER NOT NULL DEFAULT 0,
                  score REAL NOT NULL DEFAULT 0.5,
                  source TEXT NOT NULL,
                  metadata_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_skill_versions (
                  name TEXT NOT NULL,
                  revision INTEGER NOT NULL,
                  description TEXT NOT NULL,
                  workflow_json TEXT NOT NULL,
                  change_note TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(name, revision)
                );
                """
            )
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS runtime_session_fts "
                    "USING fts5(session_id UNINDEXED, role UNINDEXED, kind UNINDEXED, text)"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS runtime_memory_fts "
                    "USING fts5(memory_key UNINDEXED, kind UNINDEXED, content, tags)"
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False

    def put_memory(
        self,
        key: str,
        content: str,
        *,
        kind: str = "fact",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = key.strip()
        content = content.strip()
        if not key or not content:
            raise ValueError("memory key and content are required")
        if len(key) > 200 or len(content) > 50_000:
            raise ValueError("memory exceeds size limits")
        tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()][:30]
        now = _now()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM runtime_memory WHERE memory_key=?", (key,)
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """INSERT INTO runtime_memory
                (memory_key,kind,content,tags_json,metadata_json,content_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(memory_key) DO UPDATE SET
                  kind=excluded.kind, content=excluded.content, tags_json=excluded.tags_json,
                  metadata_json=excluded.metadata_json, content_hash=excluded.content_hash,
                  updated_at=excluded.updated_at""",
                (key, kind, content, _dump(tags), _dump(metadata or {}), digest, created_at, now),
            )
            if self._fts_enabled:
                conn.execute("DELETE FROM runtime_memory_fts WHERE memory_key=?", (key,))
                conn.execute(
                    "INSERT INTO runtime_memory_fts(memory_key,kind,content,tags) VALUES (?,?,?,?)",
                    (key, kind, content, " ".join(tags)),
                )
        return self.get_memory(key)

    def get_memory(self, key: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runtime_memory WHERE memory_key=?", (key,)).fetchone()
        if not row:
            raise ValueError("unknown memory key")
        item = dict(row)
        item["tags"] = _json(item.pop("tags_json"), [])
        item["metadata"] = _json(item.pop("metadata_json"), {})
        return item

    def search_memory(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 50))
        with self._connect() as conn:
            rows: list[sqlite3.Row]
            if self._fts_enabled:
                try:
                    hits = conn.execute(
                        "SELECT memory_key FROM runtime_memory_fts WHERE runtime_memory_fts MATCH ? LIMIT ?",
                        (query, limit),
                    ).fetchall()
                    keys = [row["memory_key"] for row in hits]
                    rows = []
                    for key in keys:
                        row = conn.execute(
                            "SELECT * FROM runtime_memory WHERE memory_key=?", (key,)
                        ).fetchone()
                        if row:
                            rows.append(row)
                except sqlite3.OperationalError:
                    rows = []
            else:
                rows = []
            if not rows:
                pattern = f"%{query}%"
                rows = conn.execute(
                    "SELECT * FROM runtime_memory WHERE content LIKE ? OR memory_key LIKE ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (pattern, pattern, limit),
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["tags"] = _json(item.pop("tags_json"), [])
            item["metadata"] = _json(item.pop("metadata_json"), {})
            out.append(item)
        return out

    def record_session(
        self,
        session_id: str,
        text: str,
        *,
        role: str = "runtime",
        kind: str = "event",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        text = text.strip()
        if not text:
            raise ValueError("session text is required")
        text = text[:100_000]
        session_id = session_id.strip() or "default"
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO runtime_sessions(session_id,role,kind,text,metadata_json,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, role, kind, text, _dump(metadata or {}), _now()),
            )
            row_id = int(cur.lastrowid)
            if self._fts_enabled:
                conn.execute(
                    "INSERT INTO runtime_session_fts(session_id,role,kind,text) VALUES (?,?,?,?)",
                    (session_id, role, kind, text),
                )
        return row_id

    def search_sessions(
        self, query: str, *, session_id: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 50))
        with self._connect() as conn:
            if self._fts_enabled:
                try:
                    if session_id:
                        hits = conn.execute(
                            "SELECT rowid FROM runtime_session_fts WHERE runtime_session_fts MATCH ? "
                            "AND session_id=? LIMIT ?",
                            (query, session_id, limit),
                        ).fetchall()
                    else:
                        hits = conn.execute(
                            "SELECT rowid FROM runtime_session_fts WHERE runtime_session_fts MATCH ? LIMIT ?",
                            (query, limit),
                        ).fetchall()
                    ids = [int(row["rowid"]) for row in hits]
                    rows = []
                    for row_id in ids:
                        row = conn.execute(
                            "SELECT * FROM runtime_sessions WHERE id=?", (row_id,)
                        ).fetchone()
                        if row:
                            rows.append(row)
                except sqlite3.OperationalError:
                    rows = []
            else:
                rows = []
            if not rows:
                pattern = f"%{query}%"
                if session_id:
                    rows = conn.execute(
                        "SELECT * FROM runtime_sessions WHERE session_id=? AND text LIKE ? "
                        "ORDER BY id DESC LIMIT ?",
                        (session_id, pattern, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM runtime_sessions WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                        (pattern, limit),
                    ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _json(item.pop("metadata_json"), {})
            out.append(item)
        return out

    def save_skill(
        self,
        name: str,
        description: str,
        workflow: list[dict[str, Any]],
        *,
        source: str = "learned",
        metadata: dict[str, Any] | None = None,
        change_note: str = "",
    ) -> dict[str, Any]:
        name = name.strip()
        if not _STEP_ID_RE.fullmatch(name):
            raise ValueError("skill name must be 1-80 safe characters")
        normalized = validate_workflow(workflow)
        workflow_json = _dump(normalized)
        now = _now()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM runtime_skills WHERE name=?", (name,)).fetchone()
            if row and row["workflow_json"] == workflow_json and row["description"] == description:
                return self.get_skill(name)
            revision = int(row["revision"] + 1) if row else 1
            created_at = row["created_at"] if row else now
            successes = int(row["successes"]) if row else 0
            failures = int(row["failures"]) if row else 0
            score = float(row["score"]) if row else 0.5
            conn.execute(
                """INSERT INTO runtime_skills
                (name,description,workflow_json,revision,successes,failures,score,source,metadata_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                  description=excluded.description, workflow_json=excluded.workflow_json,
                  revision=excluded.revision, source=excluded.source,
                  metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
                (name, description, workflow_json, revision, successes, failures, score,
                 source, _dump(metadata or {}), created_at, now),
            )
            conn.execute(
                "INSERT INTO runtime_skill_versions(name,revision,description,workflow_json,change_note,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (name, revision, description, workflow_json, change_note, now),
            )
        return self.get_skill(name)

    def get_skill(self, name: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runtime_skills WHERE name=?", (name,)).fetchone()
        if not row:
            raise ValueError("unknown skill")
        item = dict(row)
        item["workflow"] = _json(item.pop("workflow_json"), [])
        item["metadata"] = _json(item.pop("metadata_json"), {})
        return item

    def list_skills(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name,description,revision,successes,failures,score,source,updated_at "
                "FROM runtime_skills ORDER BY score DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_skill_outcome(self, name: str, success: bool) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT successes,failures FROM runtime_skills WHERE name=?", (name,)
            ).fetchone()
            if not row:
                raise ValueError("unknown skill")
            successes = int(row["successes"]) + (1 if success else 0)
            failures = int(row["failures"]) + (0 if success else 1)
            score = (successes + 1.0) / (successes + failures + 2.0)
            conn.execute(
                "UPDATE runtime_skills SET successes=?,failures=?,score=?,updated_at=? WHERE name=?",
                (successes, failures, score, _now(), name),
            )
        return self.get_skill(name)


def execute_workflow(
    steps: list[dict[str, Any]],
    execute_tool: Callable[[str, dict[str, Any], str], dict[str, Any]],
    *,
    max_parallel: int = 4,
    timeout_seconds: float = 120.0,
    fail_fast: bool = True,
) -> dict[str, Any]:
    """Execute a validated DAG while keeping each tool call out of model context."""
    normalized = validate_workflow(steps)
    max_parallel = max(1, min(int(max_parallel), _MAX_PARALLEL))
    timeout_seconds = max(1.0, min(float(timeout_seconds), 900.0))
    started = time.monotonic()
    deadline = started + timeout_seconds
    pending = {step["id"]: step for step in normalized}
    completed: dict[str, dict[str, Any]] = {}
    failed: set[str] = set()
    running: dict[Any, tuple[str, float]] = {}
    executor = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="ahmed-runtime")

    def invoke(step: dict[str, Any]) -> dict[str, Any]:
        call_started = time.monotonic()
        arguments = resolve_step_references(step["arguments"], completed)
        try:
            result = execute_tool(step["tool_name"], arguments, step["role"])
        except Exception as exc:  # noqa: BLE001 - execution boundary
            result = {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            }
        return {
            "id": step["id"],
            "tool_name": step["tool_name"],
            "role": step["role"],
            "status": "error" if bool(result.get("isError")) else "ok",
            "duration_ms": round((time.monotonic() - call_started) * 1000, 2),
            "result_hash": _hash(result),
            "result": _compact(result),
        }

    try:
        while pending or running:
            if time.monotonic() >= deadline:
                for future in running:
                    future.cancel()
                for step_id in list(pending):
                    completed[step_id] = {
                        "id": step_id,
                        "status": "skipped",
                        "reason": "workflow_timeout",
                    }
                    failed.add(step_id)
                    pending.pop(step_id, None)
                break

            launched = False
            for step_id, step in list(pending.items()):
                deps = step["depends_on"]
                if any(dep in failed for dep in deps):
                    completed[step_id] = {
                        "id": step_id,
                        "tool_name": step["tool_name"],
                        "role": step["role"],
                        "status": "skipped",
                        "reason": "dependency_failed",
                    }
                    failed.add(step_id)
                    pending.pop(step_id)
                    continue
                if all(dep in completed for dep in deps) and len(running) < max_parallel:
                    future = executor.submit(invoke, step)
                    running[future] = (step_id, time.monotonic())
                    pending.pop(step_id)
                    launched = True

            if fail_fast and failed and not running:
                for step_id, step in list(pending.items()):
                    completed[step_id] = {
                        "id": step_id,
                        "tool_name": step["tool_name"],
                        "role": step["role"],
                        "status": "skipped",
                        "reason": "fail_fast",
                    }
                    failed.add(step_id)
                    pending.pop(step_id)
                break

            if not running:
                if pending and not launched:
                    raise RuntimeError("workflow stalled despite acyclic validation")
                continue

            remaining = max(0.01, deadline - time.monotonic())
            done, _ = wait(tuple(running), timeout=min(0.25, remaining), return_when=FIRST_COMPLETED)
            for future in done:
                step_id, _ = running.pop(future)
                try:
                    item = future.result()
                except Exception as exc:  # pragma: no cover - invoke converts ordinary errors
                    item = {
                        "id": step_id,
                        "status": "error",
                        "reason": f"worker_failure:{type(exc).__name__}",
                    }
                completed[step_id] = item
                if item.get("status") != "ok":
                    failed.add(step_id)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    ordered = [completed[step["id"]] for step in normalized if step["id"] in completed]
    ok_count = sum(item.get("status") == "ok" for item in ordered)
    return {
        "schema": "ahmed-runtime-workflow/v1",
        "status": "ok" if ok_count == len(normalized) else "partial" if ok_count else "error",
        "step_count": len(normalized),
        "ok_count": ok_count,
        "error_count": sum(item.get("status") == "error" for item in ordered),
        "skipped_count": sum(item.get("status") == "skipped" for item in ordered),
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "steps": ordered,
        "workflow_hash": _hash(normalized),
    }


def execute_delegation(
    tasks: list[dict[str, Any]],
    execute_task: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    max_parallel: int = 3,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    """Run isolated specialist workstreams concurrently and return summaries only."""
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("delegation requires at least one task")
    if len(tasks) > 12:
        raise ValueError("delegation supports at most 12 tasks")
    max_parallel = max(1, min(int(max_parallel), 6))
    timeout_seconds = max(1.0, min(float(timeout_seconds), 1800.0))
    started = time.monotonic()
    executor = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="ahmed-delegate")
    futures: dict[Any, str] = {}
    results: dict[str, dict[str, Any]] = {}
    try:
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                raise ValueError(f"delegation task {index} must be an object")
            task_id = str(task.get("id") or f"task_{index + 1}")
            if not _STEP_ID_RE.fullmatch(task_id) or task_id in results or task_id in futures.values():
                raise ValueError(f"invalid or duplicate delegation task id: {task_id}")
            futures[executor.submit(execute_task, dict(task))] = task_id
        done, not_done = wait(tuple(futures), timeout=timeout_seconds)
        for future in done:
            task_id = futures[future]
            try:
                outcome = future.result()
                results[task_id] = {
                    "id": task_id,
                    "status": "ok" if outcome.get("status") == "ok" else "partial",
                    "result": _compact(outcome),
                }
            except Exception as exc:  # noqa: BLE001
                results[task_id] = {
                    "id": task_id,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        for future in not_done:
            task_id = futures[future]
            future.cancel()
            results[task_id] = {"id": task_id, "status": "timeout"}
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    ordered_ids = [str(task.get("id") or f"task_{i + 1}") for i, task in enumerate(tasks)]
    ordered = [results[task_id] for task_id in ordered_ids]
    return {
        "schema": "ahmed-runtime-delegation/v1",
        "status": "ok" if all(item["status"] == "ok" for item in ordered) else "partial",
        "task_count": len(tasks),
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "tasks": ordered,
    }


def new_session_id(prefix: str = "rt") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
