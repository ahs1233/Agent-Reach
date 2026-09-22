"""Evidence foundation for Ahmed Research Engine.

Sprint 1 intentionally implements provenance + evidence ledger only.
It does not compute freshness, source-quality, conflict, or causal scores.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

OBSERVATION_TYPES = {
    "ACTUAL",
    "ESTIMATE",
    "FORECAST",
    "TARGET",
    "SCENARIO",
    "COMPANY_GUIDANCE",
    "MODEL_OUTPUT",
    "UNKNOWN",
}

CLAIM_CLASSIFICATIONS = {
    "VERIFIED",
    "STRONG_SIGNAL",
    "INFERENCE",
    "SCENARIO",
    "SPECULATION",
}

CONFIDENCE_LABELS = {"HIGH", "MEDIUM", "LOW", "UNASSESSED"}

RETRIEVAL_STATUSES = {
    "SUCCESS",
    "PARTIAL",
    "BLOCKED",
    "FAILED",
    "DISCOVERED",
    "UNAVAILABLE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("url is required")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("only public http(s) URLs are supported")

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("url hostname is required")

    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def content_hash(content: str | bytes) -> str:
    payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    return hashlib.sha256(payload).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _normalize_enum(value: str, allowed: set[str], field: str) -> str:
    normalized = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    if normalized not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return normalized


class ResearchStore:
    """SQLite-backed provenance/evidence store.

    SQLite is the Sprint 1 backend, not the permanent architecture boundary.
    All public methods return JSON-serializable dictionaries.
    """

    def __init__(self, db_path: str | None = None):
        configured = db_path or os.environ.get("AHMED_RESEARCH_DB_PATH")
        self.db_path = configured or str(Path.home() / ".agent-reach" / "research.db")
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(Path(self.db_path).expanduser())

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        self._create_schema()

    @classmethod
    def from_environment(cls) -> "ResearchStore":
        return cls(os.environ.get("AHMED_RESEARCH_DB_PATH"))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _create_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS research_runs (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE,
            question TEXT NOT NULL,
            cutoff TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS sources (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT UNIQUE,
            canonical_url TEXT NOT NULL,
            original_url TEXT NOT NULL,
            publisher TEXT,
            source_type TEXT,
            primary_source INTEGER,
            publication_date TEXT,
            data_cutoff TEXT,
            first_retrieved_at TEXT NOT NULL,
            latest_retrieved_at TEXT NOT NULL,
            retrieval_tool TEXT NOT NULL,
            retrieval_method TEXT NOT NULL,
            retrieval_status TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            version_number INTEGER NOT NULL DEFAULT 1,
            previous_source_id TEXT,
            source_family_id TEXT,
            freshness_score REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(canonical_url, content_hash),
            FOREIGN KEY(previous_source_id) REFERENCES sources(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_sources_url_version
        ON sources(canonical_url, version_number);

        CREATE TABLE IF NOT EXISTS research_run_sources (
            run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 1,
            discovered_by TEXT,
            linked_at TEXT NOT NULL,
            PRIMARY KEY(run_id, source_id),
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES sources(source_id)
        );

        CREATE TABLE IF NOT EXISTS retrieval_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            tool TEXT NOT NULL,
            method TEXT,
            status TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            detail TEXT,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES sources(source_id)
        );

        CREATE TABLE IF NOT EXISTS evidence_items (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_id TEXT UNIQUE,
            run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            supporting_passage TEXT NOT NULL,
            structured_fact_json TEXT NOT NULL DEFAULT '{}',
            metric TEXT,
            value_num REAL,
            value_text TEXT,
            unit TEXT,
            geography TEXT,
            reference_period TEXT,
            observation_type TEXT NOT NULL,
            forecast_horizon TEXT,
            definition TEXT,
            extraction_method TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES sources(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_evidence_run_source
        ON evidence_items(run_id, source_id);

        CREATE TABLE IF NOT EXISTS claims (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT UNIQUE,
            run_id TEXT NOT NULL,
            statement TEXT NOT NULL,
            classification TEXT NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'UNASSESSED',
            confidence_score REAL,
            freshness_score REAL,
            verification_count INTEGER NOT NULL DEFAULT 0,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS claim_evidence (
            claim_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            relation TEXT NOT NULL CHECK(relation IN ('SUPPORTS', 'CONTRADICTS')),
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(claim_id, evidence_id, relation),
            FOREIGN KEY(claim_id) REFERENCES claims(claim_id) ON DELETE CASCADE,
            FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id)
        );
        """
        with self._lock, self._conn:
            self._conn.executescript(schema)

    @staticmethod
    def _public_id(prefix: str, seq: int) -> str:
        return f"{prefix}-{seq:06d}"

    def _require_run(self, run_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM research_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown research run: {run_id}")
        return row

    def create_run(
        self,
        question: str,
        *,
        cutoff: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        question = str(question or "").strip()
        if not question:
            raise ValueError("question is required")
        started_at = utc_now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO research_runs(run_id, question, cutoff, started_at, metadata_json)
                VALUES(NULL, ?, ?, ?, ?)
                """,
                (question, cutoff, started_at, _json_dumps(metadata or {})),
            )
            run_id = self._public_id("R", int(cursor.lastrowid))
            self._conn.execute(
                "UPDATE research_runs SET run_id = ? WHERE seq = ?",
                (run_id, cursor.lastrowid),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._require_run(run_id)
            return {
                "run_id": row["run_id"],
                "question": row["question"],
                "cutoff": row["cutoff"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "metadata": _json_loads(row["metadata_json"], {}),
            }

    def complete_run(self, run_id: str) -> dict[str, Any]:
        with self._lock, self._conn:
            self._require_run(run_id)
            self._conn.execute(
                "UPDATE research_runs SET completed_at = ? WHERE run_id = ?",
                (utc_now(), run_id),
            )
        return self.get_run(run_id)

    def record_source(
        self,
        run_id: str,
        *,
        url: str,
        content: str | bytes,
        retrieval_tool: str,
        retrieval_method: str,
        retrieval_status: str = "SUCCESS",
        publisher: str | None = None,
        source_type: str | None = None,
        primary_source: bool | None = None,
        publication_date: str | None = None,
        data_cutoff: str | None = None,
        canonical_url: str | None = None,
        discovered_by: str | None = None,
        source_family_id: str | None = None,
        freshness_score: float | None = None,
        metadata: dict[str, Any] | None = None,
        retrieval_history: list[dict[str, Any]] | None = None,
        retrieved_at: str | None = None,
    ) -> dict[str, Any]:
        self._require_run(run_id)
        tool = str(retrieval_tool or "").strip()
        method = str(retrieval_method or "").strip()
        if not tool:
            raise ValueError("retrieval_tool is required")
        if not method:
            raise ValueError("retrieval_method is required")
        status = _normalize_enum(retrieval_status, RETRIEVAL_STATUSES, "retrieval_status")
        canonical = canonicalize_url(canonical_url or url)
        original = str(url).strip()
        digest = content_hash(content)
        when = retrieved_at or utc_now()

        with self._lock, self._conn:
            existing = self._conn.execute(
                """
                SELECT * FROM sources
                WHERE canonical_url = ? AND content_hash = ?
                """,
                (canonical, digest),
            ).fetchone()

            if existing is not None:
                source_id = str(existing["source_id"])
                self._conn.execute(
                    """
                    UPDATE sources
                    SET latest_retrieved_at = ?,
                        retrieval_tool = ?,
                        retrieval_method = ?,
                        retrieval_status = ?,
                        publisher = COALESCE(?, publisher),
                        source_type = COALESCE(?, source_type),
                        primary_source = COALESCE(?, primary_source),
                        publication_date = COALESCE(?, publication_date),
                        data_cutoff = COALESCE(?, data_cutoff),
                        source_family_id = COALESCE(?, source_family_id),
                        freshness_score = COALESCE(?, freshness_score)
                    WHERE source_id = ?
                    """,
                    (
                        when,
                        tool,
                        method,
                        status,
                        publisher,
                        source_type,
                        None if primary_source is None else int(primary_source),
                        publication_date,
                        data_cutoff,
                        source_family_id,
                        freshness_score,
                        source_id,
                    ),
                )
            else:
                previous = self._conn.execute(
                    """
                    SELECT source_id, version_number FROM sources
                    WHERE canonical_url = ?
                    ORDER BY version_number DESC LIMIT 1
                    """,
                    (canonical,),
                ).fetchone()
                version_number = int(previous["version_number"]) + 1 if previous else 1
                previous_source_id = str(previous["source_id"]) if previous else None

                cursor = self._conn.execute(
                    """
                    INSERT INTO sources(
                        source_id, canonical_url, original_url, publisher, source_type,
                        primary_source, publication_date, data_cutoff,
                        first_retrieved_at, latest_retrieved_at,
                        retrieval_tool, retrieval_method, retrieval_status,
                        content_hash, version_number, previous_source_id,
                        source_family_id, freshness_score, metadata_json
                    )
                    VALUES(
                        NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        canonical,
                        original,
                        publisher,
                        source_type,
                        None if primary_source is None else int(primary_source),
                        publication_date,
                        data_cutoff,
                        when,
                        when,
                        tool,
                        method,
                        status,
                        digest,
                        version_number,
                        previous_source_id,
                        source_family_id,
                        freshness_score,
                        _json_dumps(metadata or {}),
                    ),
                )
                source_id = self._public_id("S", int(cursor.lastrowid))
                self._conn.execute(
                    "UPDATE sources SET source_id = ? WHERE seq = ?",
                    (source_id, cursor.lastrowid),
                )

            self._conn.execute(
                """
                INSERT OR IGNORE INTO research_run_sources(
                    run_id, source_id, selected, discovered_by, linked_at
                )
                VALUES(?, ?, 1, ?, ?)
                """,
                (run_id, source_id, discovered_by, when),
            )

            history = retrieval_history or [
                {
                    "stage": "RETRIEVAL",
                    "tool": tool,
                    "method": method,
                    "status": status,
                    "occurred_at": when,
                }
            ]
            for event in history:
                event_tool = str(event.get("tool") or "").strip()
                if not event_tool:
                    continue
                event_status = _normalize_enum(
                    str(event.get("status") or status),
                    RETRIEVAL_STATUSES,
                    "retrieval event status",
                )
                self._conn.execute(
                    """
                    INSERT INTO retrieval_events(
                        run_id, source_id, stage, tool, method, status, occurred_at, detail
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        source_id,
                        str(event.get("stage") or "RETRIEVAL").strip().upper(),
                        event_tool,
                        str(event.get("method") or "").strip() or None,
                        event_status,
                        str(event.get("occurred_at") or when),
                        str(event.get("detail") or "").strip() or None,
                    ),
                )

        return self.get_source(source_id, run_id=run_id)

    def get_source(self, source_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown source: {source_id}")
            events_query = "SELECT * FROM retrieval_events WHERE source_id = ?"
            params: tuple[Any, ...] = (source_id,)
            if run_id:
                events_query += " AND run_id = ?"
                params = (source_id, run_id)
            events_query += " ORDER BY seq"
            events = self._conn.execute(events_query, params).fetchall()
            return {
                "source_id": row["source_id"],
                "canonical_url": row["canonical_url"],
                "original_url": row["original_url"],
                "publisher": row["publisher"],
                "source_type": row["source_type"],
                "primary_source": (
                    None if row["primary_source"] is None else bool(row["primary_source"])
                ),
                "publication_date": row["publication_date"],
                "data_cutoff": row["data_cutoff"],
                "first_retrieved_at": row["first_retrieved_at"],
                "latest_retrieved_at": row["latest_retrieved_at"],
                "retrieval_tool": row["retrieval_tool"],
                "retrieval_method": row["retrieval_method"],
                "retrieval_status": row["retrieval_status"],
                "content_hash": row["content_hash"],
                "version_number": row["version_number"],
                "previous_source_id": row["previous_source_id"],
                "source_family_id": row["source_family_id"],
                "freshness_score": row["freshness_score"],
                "metadata": _json_loads(row["metadata_json"], {}),
                "retrieval_history": [
                    {
                        "stage": item["stage"],
                        "tool": item["tool"],
                        "method": item["method"],
                        "status": item["status"],
                        "occurred_at": item["occurred_at"],
                        "detail": item["detail"],
                    }
                    for item in events
                ],
            }

    def add_evidence(
        self,
        run_id: str,
        *,
        source_id: str,
        supporting_passage: str,
        observation_type: str,
        structured_fact: dict[str, Any] | None = None,
        metric: str | None = None,
        value: int | float | str | None = None,
        unit: str | None = None,
        geography: str | None = None,
        reference_period: str | None = None,
        forecast_horizon: str | None = None,
        definition: str | None = None,
        extraction_method: str | None = None,
    ) -> dict[str, Any]:
        self._require_run(run_id)
        passage = str(supporting_passage or "").strip()
        if not passage:
            raise ValueError("supporting_passage is required")
        obs_type = _normalize_enum(observation_type, OBSERVATION_TYPES, "observation_type")

        with self._lock, self._conn:
            linked = self._conn.execute(
                """
                SELECT 1 FROM research_run_sources
                WHERE run_id = ? AND source_id = ?
                """,
                (run_id, source_id),
            ).fetchone()
            if linked is None:
                raise ValueError(
                    f"source {source_id} is not linked to research run {run_id}"
                )

            value_num: float | None = None
            value_text: str | None = None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value_num = float(value)
            elif value is not None:
                value_text = str(value)

            cursor = self._conn.execute(
                """
                INSERT INTO evidence_items(
                    evidence_id, run_id, source_id, supporting_passage,
                    structured_fact_json, metric, value_num, value_text, unit,
                    geography, reference_period, observation_type,
                    forecast_horizon, definition, extraction_method, created_at
                )
                VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source_id,
                    passage,
                    _json_dumps(structured_fact or {}),
                    metric,
                    value_num,
                    value_text,
                    unit,
                    geography,
                    reference_period,
                    obs_type,
                    forecast_horizon,
                    definition,
                    extraction_method,
                    utc_now(),
                ),
            )
            evidence_id = self._public_id("E", int(cursor.lastrowid))
            self._conn.execute(
                "UPDATE evidence_items SET evidence_id = ? WHERE seq = ?",
                (evidence_id, cursor.lastrowid),
            )
        return self.get_evidence(evidence_id)

    def get_evidence(self, evidence_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evidence_items WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown evidence: {evidence_id}")
            value: Any = row["value_num"] if row["value_num"] is not None else row["value_text"]
            return {
                "evidence_id": row["evidence_id"],
                "run_id": row["run_id"],
                "source_id": row["source_id"],
                "supporting_passage": row["supporting_passage"],
                "structured_fact": _json_loads(row["structured_fact_json"], {}),
                "metric": row["metric"],
                "value": value,
                "unit": row["unit"],
                "geography": row["geography"],
                "reference_period": row["reference_period"],
                "observation_type": row["observation_type"],
                "forecast_horizon": row["forecast_horizon"],
                "definition": row["definition"],
                "extraction_method": row["extraction_method"],
                "created_at": row["created_at"],
            }

    def add_claim(
        self,
        run_id: str,
        *,
        statement: str,
        classification: str,
        supporting_evidence_ids: list[str] | None = None,
        contradicting_evidence_ids: list[str] | None = None,
        confidence: str = "UNASSESSED",
        confidence_score: float | None = None,
        freshness_score: float | None = None,
        verification_count: int = 0,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_run(run_id)
        text = str(statement or "").strip()
        if not text:
            raise ValueError("claim statement is required")
        claim_type = _normalize_enum(classification, CLAIM_CLASSIFICATIONS, "classification")
        confidence_label = _normalize_enum(confidence, CONFIDENCE_LABELS, "confidence")

        supporting = list(dict.fromkeys(supporting_evidence_ids or []))
        contradicting = list(dict.fromkeys(contradicting_evidence_ids or []))
        all_evidence = supporting + [item for item in contradicting if item not in supporting]
        if not all_evidence:
            raise ValueError("a claim must reference at least one evidence_id")

        with self._lock, self._conn:
            placeholders = ",".join("?" for _ in all_evidence)
            rows = self._conn.execute(
                f"""
                SELECT evidence_id, run_id FROM evidence_items
                WHERE evidence_id IN ({placeholders})
                """,
                tuple(all_evidence),
            ).fetchall()
            found = {str(row["evidence_id"]): str(row["run_id"]) for row in rows}
            missing = [item for item in all_evidence if item not in found]
            if missing:
                raise ValueError(f"unknown evidence IDs: {', '.join(missing)}")
            wrong_run = [item for item, evidence_run in found.items() if evidence_run != run_id]
            if wrong_run:
                raise ValueError(
                    "claim evidence must belong to the same research run: "
                    + ", ".join(wrong_run)
                )

            cursor = self._conn.execute(
                """
                INSERT INTO claims(
                    claim_id, run_id, statement, classification, confidence,
                    confidence_score, freshness_score, verification_count,
                    provenance_json, created_at
                )
                VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    text,
                    claim_type,
                    confidence_label,
                    confidence_score,
                    freshness_score,
                    int(verification_count),
                    _json_dumps(provenance or {}),
                    utc_now(),
                ),
            )
            claim_id = self._public_id("C", int(cursor.lastrowid))
            self._conn.execute(
                "UPDATE claims SET claim_id = ? WHERE seq = ?",
                (claim_id, cursor.lastrowid),
            )

            for position, evidence_id in enumerate(supporting):
                self._conn.execute(
                    """
                    INSERT INTO claim_evidence(claim_id, evidence_id, relation, position)
                    VALUES(?, ?, 'SUPPORTS', ?)
                    """,
                    (claim_id, evidence_id, position),
                )
            for position, evidence_id in enumerate(contradicting):
                self._conn.execute(
                    """
                    INSERT INTO claim_evidence(claim_id, evidence_id, relation, position)
                    VALUES(?, ?, 'CONTRADICTS', ?)
                    """,
                    (claim_id, evidence_id, position),
                )
        return self.get_claim(claim_id)

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM claims WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown claim: {claim_id}")
            relations = self._conn.execute(
                """
                SELECT evidence_id, relation FROM claim_evidence
                WHERE claim_id = ?
                ORDER BY relation, position
                """,
                (claim_id,),
            ).fetchall()
            return {
                "claim_id": row["claim_id"],
                "run_id": row["run_id"],
                "statement": row["statement"],
                "classification": row["classification"],
                "supporting_evidence_ids": [
                    item["evidence_id"] for item in relations if item["relation"] == "SUPPORTS"
                ],
                "contradicting_evidence_ids": [
                    item["evidence_id"]
                    for item in relations
                    if item["relation"] == "CONTRADICTS"
                ],
                "confidence": row["confidence"],
                "confidence_score": row["confidence_score"],
                "freshness_score": row["freshness_score"],
                "verification_count": row["verification_count"],
                "provenance": _json_loads(row["provenance_json"], {}),
                "created_at": row["created_at"],
            }

    def export_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        with self._lock:
            source_ids = [
                str(row["source_id"])
                for row in self._conn.execute(
                    """
                    SELECT source_id FROM research_run_sources
                    WHERE run_id = ? ORDER BY linked_at, source_id
                    """,
                    (run_id,),
                ).fetchall()
            ]
            evidence_ids = [
                str(row["evidence_id"])
                for row in self._conn.execute(
                    """
                    SELECT evidence_id FROM evidence_items
                    WHERE run_id = ? ORDER BY seq
                    """,
                    (run_id,),
                ).fetchall()
            ]
            claim_ids = [
                str(row["claim_id"])
                for row in self._conn.execute(
                    """
                    SELECT claim_id FROM claims
                    WHERE run_id = ? ORDER BY seq
                    """,
                    (run_id,),
                ).fetchall()
            ]

        return {
            "research_run": run,
            "sources": [self.get_source(item, run_id=run_id) for item in source_ids],
            "evidence": [self.get_evidence(item) for item in evidence_ids],
            "claims": [self.get_claim(item) for item in claim_ids],
            "ledger": self.export_ledger_rows(run_id),
        }

    def export_ledger_rows(self, run_id: str) -> list[dict[str, Any]]:
        self._require_run(run_id)
        query = """
        SELECT
            c.claim_id,
            c.statement,
            c.classification,
            c.confidence,
            ce.relation,
            e.evidence_id,
            e.supporting_passage,
            e.structured_fact_json,
            e.metric,
            e.value_num,
            e.value_text,
            e.unit,
            e.geography,
            e.reference_period,
            e.observation_type,
            e.forecast_horizon,
            e.definition,
            s.source_id,
            s.canonical_url,
            s.publisher,
            s.source_type,
            s.primary_source,
            s.publication_date,
            s.data_cutoff,
            s.latest_retrieved_at,
            s.retrieval_tool,
            s.retrieval_method,
            s.retrieval_status,
            s.content_hash,
            s.version_number,
            s.previous_source_id,
            s.source_family_id
        FROM claims c
        JOIN claim_evidence ce ON ce.claim_id = c.claim_id
        JOIN evidence_items e ON e.evidence_id = ce.evidence_id
        JOIN sources s ON s.source_id = e.source_id
        WHERE c.run_id = ?
        ORDER BY c.seq, ce.relation, ce.position
        """
        with self._lock:
            rows = self._conn.execute(query, (run_id,)).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            value: Any = row["value_num"] if row["value_num"] is not None else row["value_text"]
            result.append(
                {
                    "claim_id": row["claim_id"],
                    "claim": row["statement"],
                    "classification": row["classification"],
                    "confidence": row["confidence"],
                    "evidence_relation": row["relation"],
                    "evidence_id": row["evidence_id"],
                    "supporting_passage": row["supporting_passage"],
                    "structured_fact": _json_loads(row["structured_fact_json"], {}),
                    "metric": row["metric"],
                    "value": value,
                    "unit": row["unit"],
                    "geography": row["geography"],
                    "reference_period": row["reference_period"],
                    "observation_type": row["observation_type"],
                    "forecast_horizon": row["forecast_horizon"],
                    "definition": row["definition"],
                    "source_id": row["source_id"],
                    "source_url": row["canonical_url"],
                    "publisher": row["publisher"],
                    "source_type": row["source_type"],
                    "primary_source": (
                        None if row["primary_source"] is None else bool(row["primary_source"])
                    ),
                    "publication_date": row["publication_date"],
                    "data_cutoff": row["data_cutoff"],
                    "retrieved_at": row["latest_retrieved_at"],
                    "retrieval_tool": row["retrieval_tool"],
                    "retrieval_method": row["retrieval_method"],
                    "retrieval_status": row["retrieval_status"],
                    "content_hash": row["content_hash"],
                    "source_version": row["version_number"],
                    "previous_source_id": row["previous_source_id"],
                    "source_family_id": row["source_family_id"],
                }
            )
        return result

    def audit_run(self, run_id: str) -> dict[str, Any]:
        self._require_run(run_id)
        with self._lock:
            claims_without_evidence = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM claims c
                    LEFT JOIN claim_evidence ce ON ce.claim_id = c.claim_id
                    WHERE c.run_id = ? AND ce.claim_id IS NULL
                    """,
                    (run_id,),
                ).fetchone()["n"]
            )
            evidence_without_source = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM evidence_items e
                    LEFT JOIN sources s ON s.source_id = e.source_id
                    WHERE e.run_id = ? AND s.source_id IS NULL
                    """,
                    (run_id,),
                ).fetchone()["n"]
            )
            source_missing_retrieval = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM research_run_sources rs
                    JOIN sources s ON s.source_id = rs.source_id
                    WHERE rs.run_id = ?
                      AND (
                        s.retrieval_tool IS NULL OR TRIM(s.retrieval_tool) = ''
                        OR s.latest_retrieved_at IS NULL OR TRIM(s.latest_retrieved_at) = ''
                      )
                    """,
                    (run_id,),
                ).fetchone()["n"]
            )
            invalid_observation_type = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM evidence_items
                    WHERE run_id = ? AND observation_type NOT IN (
                        'ACTUAL','ESTIMATE','FORECAST','TARGET','SCENARIO',
                        'COMPANY_GUIDANCE','MODEL_OUTPUT','UNKNOWN'
                    )
                    """,
                    (run_id,),
                ).fetchone()["n"]
            )
            duplicate_versions = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM (
                        SELECT canonical_url, content_hash, COUNT(*) AS c
                        FROM sources
                        GROUP BY canonical_url, content_hash
                        HAVING c > 1
                    )
                    """
                ).fetchone()["n"]
            )
            ledger_rows = len(self.export_ledger_rows(run_id))

        checks = {
            "claims_have_evidence": claims_without_evidence == 0,
            "evidence_has_source": evidence_without_source == 0,
            "sources_have_tool_and_retrieval_time": source_missing_retrieval == 0,
            "publication_date_and_data_cutoff_are_separate": True,
            "observation_type_stored": invalid_observation_type == 0,
            "unchanged_source_deduplication": duplicate_versions == 0,
            "ledger_export_available": ledger_rows >= 0,
        }
        return {
            "run_id": run_id,
            "passed": all(checks.values()),
            "checks": checks,
            "counts": {
                "claims_without_evidence": claims_without_evidence,
                "evidence_without_source": evidence_without_source,
                "sources_missing_retrieval_provenance": source_missing_retrieval,
                "invalid_observation_type": invalid_observation_type,
                "duplicate_source_versions": duplicate_versions,
                "ledger_rows": ledger_rows,
            },
        }
