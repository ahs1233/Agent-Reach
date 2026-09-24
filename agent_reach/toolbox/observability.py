"""Structured, secret-safe execution observability for Ahmed Toolbox."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REDACT_RE = re.compile(
    r"(?i)(?:bearer\s+|token[=:]\s*|api[_-]?key[=:]\s*|password[=:]\s*)[^\s,;]+"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _redact(value: str | None, limit: int = 2000) -> str | None:
    if value is None:
        return None
    return _REDACT_RE.sub("[REDACTED]", str(value))[:limit]


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * q
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    weight = position - lo
    return round(ordered[lo] * (1 - weight) + ordered[hi] * weight, 3)


class ExecutionLogStore:
    """Append-only SQLite log. Request arguments and result bodies are never stored."""

    def __init__(self, db_path: str | None = None):
        configured = db_path or os.environ.get("AHMED_OBSERVABILITY_DB_PATH")
        self.db_path = configured or str(Path.home() / ".agent-reach" / "observability.db")
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(Path(self.db_path).expanduser())
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        self._create_schema()

    @classmethod
    def from_environment(cls) -> "ExecutionLogStore":
        return cls(os.environ.get("AHMED_OBSERVABILITY_DB_PATH"))

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_log (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                success INTEGER NOT NULL CHECK(success IN (0, 1)),
                error_type TEXT,
                error_message TEXT,
                retries INTEGER NOT NULL DEFAULT 0,
                fallback_used INTEGER NOT NULL DEFAULT 0 CHECK(fallback_used IN (0, 1)),
                result_size_bytes INTEGER,
                parent_run_id TEXT,
                workflow_id TEXT,
                orchestration_id TEXT,
                session_id TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_execution_log_started_at
                ON execution_log(started_at);
            CREATE INDEX IF NOT EXISTS idx_execution_log_tool_name
                ON execution_log(tool_name);
            CREATE INDEX IF NOT EXISTS idx_execution_log_run_id
                ON execution_log(run_id);
            CREATE INDEX IF NOT EXISTS idx_execution_log_success
                ON execution_log(success);
            """
        )
        self._conn.commit()

    @staticmethod
    def identifiers(
        arguments: dict[str, Any], request_id: str | int | None = None
    ) -> dict[str, str | None]:
        orchestration_id = arguments.get("orchestration_id")
        session_id = arguments.get("session_id")
        workflow_id = arguments.get("workflow_id")
        semantic_run = arguments.get("run_id")
        run_id = (
            str(semantic_run or orchestration_id or workflow_id or session_id)
            if (semantic_run or orchestration_id or workflow_id or session_id)
            else f"exec_{uuid.uuid4().hex}"
        )
        return {
            "run_id": run_id,
            "request_id": str(request_id) if request_id is not None else f"req_{uuid.uuid4().hex}",
            "parent_run_id": str(arguments.get("parent_run_id"))
            if arguments.get("parent_run_id")
            else None,
            "workflow_id": str(workflow_id) if workflow_id else None,
            "orchestration_id": str(orchestration_id) if orchestration_id else None,
            "session_id": str(session_id) if session_id else None,
        }

    @staticmethod
    def result_metadata(result: dict[str, Any]) -> dict[str, Any]:
        try:
            size = len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            size = None
        payload: dict[str, Any] = {}
        for block in result.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = str(block.get("text") or "").strip()
            if not text.startswith("{"):
                continue
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                payload = decoded
                break

        attempts = payload.get("attempts")
        retries = payload.get("retries")
        if retries is None and isinstance(attempts, list):
            retries = max(0, len(attempts) - 1)
        try:
            retries_value = max(0, int(retries or 0))
        except (TypeError, ValueError):
            retries_value = 0
        fallback_used = bool(payload.get("fallback_used"))
        if isinstance(attempts, list) and len(attempts) > 1:
            fallback_used = True
        try:
            fallback_used = fallback_used or int(payload.get("escalation_count") or 0) > 0
        except (TypeError, ValueError):
            pass

        tokens = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
        usage = payload.get("usage")
        if isinstance(usage, dict):
            aliases = {
                "input_tokens": ("input_tokens", "prompt_tokens"),
                "output_tokens": ("output_tokens", "completion_tokens"),
                "total_tokens": ("total_tokens",),
            }
            for target, keys in aliases.items():
                for key in keys:
                    value = usage.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        tokens[target] = value
                        break
        workflow_id = payload.get("workflow_id") or payload.get("workflow_hash")
        return {
            "result_size_bytes": size,
            "retries": retries_value,
            "fallback_used": fallback_used,
            "workflow_id": str(workflow_id) if workflow_id else None,
            **tokens,
        }

    def record(self, **values: Any) -> None:
        row = {
            "run_id": str(values["run_id"]),
            "request_id": str(values["request_id"]),
            "tool_name": str(values["tool_name"]),
            "started_at": str(values["started_at"]),
            "completed_at": str(values["completed_at"]),
            "duration_ms": float(values["duration_ms"]),
            "success": 1 if values.get("success") else 0,
            "error_type": _redact(values.get("error_type"), 200),
            "error_message": _redact(values.get("error_message")),
            "retries": max(0, int(values.get("retries") or 0)),
            "fallback_used": 1 if values.get("fallback_used") else 0,
            "result_size_bytes": values.get("result_size_bytes"),
            "parent_run_id": values.get("parent_run_id"),
            "workflow_id": values.get("workflow_id"),
            "orchestration_id": values.get("orchestration_id"),
            "session_id": values.get("session_id"),
            "input_tokens": values.get("input_tokens"),
            "output_tokens": values.get("output_tokens"),
            "total_tokens": values.get("total_tokens"),
        }
        columns = list(row)
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT INTO execution_log ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(row[column] for column in columns),
            )

    def rows(self, limit: int = 100) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM execution_log ORDER BY seq DESC LIMIT ?", (capped,)
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self, hours: int = 24) -> dict[str, Any]:
        window = max(1, min(int(hours), 720))
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(hours=window))
            .replace(microsecond=0)
            .isoformat()
        )
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool_name,duration_ms,success,error_type,fallback_used "
                "FROM execution_log WHERE started_at >= ? ORDER BY seq",
                (cutoff,),
            ).fetchall()
        calls = len(rows)
        success_count = sum(int(row["success"]) for row in rows)
        durations = [float(row["duration_ms"]) for row in rows]
        fallback_count = sum(int(row["fallback_used"]) for row in rows)
        by_tool: dict[str, list[int]] = {}
        errors: dict[str, int] = {}
        for row in rows:
            name = str(row["tool_name"])
            bucket = by_tool.setdefault(name, [0, 0])
            bucket[0] += 1
            bucket[1] += int(row["success"])
            if not int(row["success"]):
                kind = str(row["error_type"] or "ToolError")
                errors[kind] = errors.get(kind, 0) + 1
        lowest = sorted(
            (
                {
                    "tool_name": name,
                    "calls": counts[0],
                    "success_rate": round(counts[1] / counts[0], 6),
                }
                for name, counts in by_tool.items()
            ),
            key=lambda item: (item["success_rate"], -item["calls"], item["tool_name"]),
        )[:10]
        top_errors = [
            {"error_type": name, "count": count}
            for name, count in sorted(errors.items(), key=lambda item: (-item[1], item[0]))[:10]
        ]
        return {
            "schema": "ahmed-toolbox-execution-stats/v1",
            "window_hours": window,
            "calls": calls,
            "success_rate": round(success_count / calls, 6) if calls else None,
            "p50_ms": _percentile(durations, 0.50),
            "p95_ms": _percentile(durations, 0.95),
            "top_errors": top_errors,
            "lowest_success_tools": lowest,
            "fallback_count": fallback_count,
            "fallback_frequency": round(fallback_count / calls, 6) if calls else None,
        }
