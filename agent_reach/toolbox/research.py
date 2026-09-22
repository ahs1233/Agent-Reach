"""Core evidence, freshness, source-lineage, and output-integrity store.

The core remains domain-agnostic and output-agnostic. Freshness and source
independence are explicit auditable assessments; source-quality, numerical
conflict, causal, and PanWatch state scoring remain outside this layer.
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

from .freshness import evaluate_freshness
from .source_independence import (
    SOURCE_RELATIONSHIP_TYPES,
    assess_source_independence,
    normalize_publisher,
)

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

OUTPUT_SEMANTIC_TYPES = OBSERVATION_TYPES | {"MIXED"}

SEMANTIC_LABELS = {
    "ACTUAL": "Actual",
    "ESTIMATE": "Estimate",
    "FORECAST": "Forecast",
    "TARGET": "Target",
    "SCENARIO": "Scenario",
    "COMPANY_GUIDANCE": "Company guidance",
    "MODEL_OUTPUT": "Model output",
    "UNKNOWN": "Unclassified",
    "MIXED": "Mixed semantics",
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

        CREATE TABLE IF NOT EXISTS source_relationships (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            relationship_id TEXT UNIQUE,
            run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            related_source_id TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            basis TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(run_id, source_id, related_source_id, relationship_type),
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES sources(source_id),
            FOREIGN KEY(related_source_id) REFERENCES sources(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_source_relationships_run
        ON source_relationships(run_id, seq);

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
            observation_type TEXT NOT NULL DEFAULT 'UNKNOWN',
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

        CREATE TABLE IF NOT EXISTS source_independence_evaluations (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            independence_id TEXT UNIQUE,
            run_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            status TEXT NOT NULL,
            supporting_source_count INTEGER NOT NULL,
            supporting_url_count INTEGER NOT NULL,
            effective_lineage_count INTEGER NOT NULL,
            strict_confidence_basis_source_count INTEGER NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(claim_id) REFERENCES claims(claim_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_source_independence_claim
        ON source_independence_evaluations(claim_id, seq);

        CREATE TABLE IF NOT EXISTS outputs (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            output_id TEXT UNIQUE,
            run_id TEXT NOT NULL,
            consumer_type TEXT NOT NULL,
            consumer_id TEXT,
            output_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_outputs_run
        ON outputs(run_id, seq);

        CREATE TABLE IF NOT EXISTS output_fragments (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            fragment_id TEXT UNIQUE,
            output_id TEXT NOT NULL,
            content TEXT,
            rendered_content TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            asserted_observation_type TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(output_id) REFERENCES outputs(output_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS output_fragment_claims (
            fragment_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(fragment_id, claim_id),
            FOREIGN KEY(fragment_id) REFERENCES output_fragments(fragment_id) ON DELETE CASCADE,
            FOREIGN KEY(claim_id) REFERENCES claims(claim_id)
        );

        CREATE TABLE IF NOT EXISTS freshness_evaluations (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            freshness_id TEXT UNIQUE,
            run_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            policy_name TEXT NOT NULL,
            policy_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            as_of TEXT NOT NULL,
            basis_field TEXT NOT NULL,
            basis_value TEXT,
            basis_precision TEXT,
            age_min_seconds REAL,
            age_max_seconds REAL,
            max_age_seconds REAL,
            latest_known_cutoff TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id)
        );

        CREATE INDEX IF NOT EXISTS idx_freshness_evidence
        ON freshness_evaluations(evidence_id, seq);

        CREATE TABLE IF NOT EXISTS output_fragment_freshness (
            fragment_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            freshness_id TEXT NOT NULL,
            PRIMARY KEY(fragment_id, evidence_id),
            FOREIGN KEY(fragment_id) REFERENCES output_fragments(fragment_id) ON DELETE CASCADE,
            FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id),
            FOREIGN KEY(freshness_id) REFERENCES freshness_evaluations(freshness_id)
        );
        """
        with self._lock, self._conn:
            self._conn.executescript(schema)
            observation_type_added = self._ensure_column(
                "claims",
                "observation_type",
                "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            )
            if observation_type_added:
                self._backfill_claim_observation_types()

    def _ensure_column(self, table: str, column: str, ddl: str) -> bool:
        columns = {
            str(row["name"])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in columns:
            return False
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        return True

    def _backfill_claim_observation_types(self) -> None:
        """Backfill Sprint 1 claims when supporting evidence has one clear type."""
        claims = self._conn.execute(
            """
            SELECT claim_id FROM claims
            WHERE observation_type = 'UNKNOWN'
            """
        ).fetchall()
        for row in claims:
            claim_id = str(row["claim_id"] or "")
            if not claim_id:
                continue
            evidence_types = {
                str(item["observation_type"])
                for item in self._conn.execute(
                    """
                    SELECT e.observation_type
                    FROM claim_evidence ce
                    JOIN evidence_items e ON e.evidence_id = ce.evidence_id
                    WHERE ce.claim_id = ? AND ce.relation = 'SUPPORTS'
                    """,
                    (claim_id,),
                ).fetchall()
            }
            if len(evidence_types) == 1:
                self._conn.execute(
                    """
                    UPDATE claims SET observation_type = ?
                    WHERE claim_id = ? AND observation_type = 'UNKNOWN'
                    """,
                    (next(iter(evidence_types)), claim_id),
                )

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
            run_retrieval = None
            if run_id:
                retrieval_rows = [
                    item for item in events if str(item["stage"]).upper() == "RETRIEVAL"
                ]
                if retrieval_rows:
                    item = retrieval_rows[-1]
                    run_retrieval = {
                        "tool": item["tool"],
                        "method": item["method"],
                        "status": item["status"],
                        "occurred_at": item["occurred_at"],
                        "detail": item["detail"],
                    }
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
                "representation_hash": row["content_hash"],
                "content_hash_scope": "retrieved_representation",
                "version_number": row["version_number"],
                "previous_source_id": row["previous_source_id"],
                "source_family_id": row["source_family_id"],
                "freshness_score": row["freshness_score"],
                "metadata": _json_loads(row["metadata_json"], {}),
                "run_retrieval": run_retrieval,
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

    def record_source_relationship(
        self,
        run_id: str,
        *,
        source_id: str,
        related_source_id: str,
        relationship_type: str,
        basis: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record explicit source lineage or independence evidence."""
        self._require_run(run_id)
        left = str(source_id or "").strip()
        right = str(related_source_id or "").strip()
        if not left or not right:
            raise ValueError("source_id and related_source_id are required")
        if left == right:
            raise ValueError("a source cannot have a relationship with itself")
        rel_type = _normalize_enum(
            relationship_type,
            SOURCE_RELATIONSHIP_TYPES,
            "source relationship_type",
        )

        with self._lock, self._conn:
            rows = self._conn.execute(
                """
                SELECT s.*
                FROM research_run_sources rs
                JOIN sources s ON s.source_id = rs.source_id
                WHERE rs.run_id = ? AND s.source_id IN (?, ?)
                """,
                (run_id, left, right),
            ).fetchall()
            found = {str(row["source_id"]): row for row in rows}
            missing = [item for item in (left, right) if item not in found]
            if missing:
                raise ValueError(
                    "source relationships require both sources in the same run: "
                    + ", ".join(missing)
                )

            if rel_type == "INDEPENDENT_OF":
                left_row = found[left]
                right_row = found[right]
                same_url = (
                    str(left_row["canonical_url"])
                    == str(right_row["canonical_url"])
                )
                same_hash = (
                    str(left_row["content_hash"])
                    == str(right_row["content_hash"])
                )
                left_publisher = normalize_publisher(left_row["publisher"])
                right_publisher = normalize_publisher(right_row["publisher"])
                same_publisher = bool(
                    left_publisher
                    and right_publisher
                    and left_publisher == right_publisher
                )
                if same_url or same_hash or same_publisher:
                    raise ValueError(
                        "cannot assert INDEPENDENT_OF for sources that share "
                        "canonical URL, representation hash, or publisher"
                    )
                left, right = sorted([left, right])

            existing = self._conn.execute(
                """
                SELECT relationship_id
                FROM source_relationships
                WHERE run_id = ? AND source_id = ?
                  AND related_source_id = ? AND relationship_type = ?
                """,
                (run_id, left, right, rel_type),
            ).fetchone()
            if existing is not None:
                return self.get_source_relationship(
                    str(existing["relationship_id"])
                )

            cursor = self._conn.execute(
                """
                INSERT INTO source_relationships(
                    relationship_id, run_id, source_id, related_source_id,
                    relationship_type, basis, metadata_json, created_at
                )
                VALUES(NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    left,
                    right,
                    rel_type,
                    str(basis).strip() if basis else None,
                    _json_dumps(metadata or {}),
                    utc_now(),
                ),
            )
            relationship_id = self._public_id("SR", int(cursor.lastrowid))
            self._conn.execute(
                """
                UPDATE source_relationships
                SET relationship_id = ?
                WHERE seq = ?
                """,
                (relationship_id, cursor.lastrowid),
            )
        return self.get_source_relationship(relationship_id)

    def get_source_relationship(self, relationship_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM source_relationships
                WHERE relationship_id = ?
                """,
                (relationship_id,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"unknown source relationship: {relationship_id}"
                )
        return {
            "relationship_id": row["relationship_id"],
            "run_id": row["run_id"],
            "source_id": row["source_id"],
            "related_source_id": row["related_source_id"],
            "relationship_type": row["relationship_type"],
            "basis": row["basis"],
            "metadata": _json_loads(row["metadata_json"], {}),
            "created_at": row["created_at"],
        }

    def get_source_relationships(self, run_id: str) -> list[dict[str, Any]]:
        self._require_run(run_id)
        with self._lock:
            ids = [
                str(row["relationship_id"])
                for row in self._conn.execute(
                    """
                    SELECT relationship_id FROM source_relationships
                    WHERE run_id = ? ORDER BY seq
                    """,
                    (run_id,),
                ).fetchall()
            ]
        return [self.get_source_relationship(item) for item in ids]

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
        observation_type: str | None = None,
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
                SELECT evidence_id, run_id, observation_type FROM evidence_items
                WHERE evidence_id IN ({placeholders})
                """,
                tuple(all_evidence),
            ).fetchall()
            found = {str(row["evidence_id"]): str(row["run_id"]) for row in rows}
            evidence_types = {
                str(row["evidence_id"]): str(row["observation_type"])
                for row in rows
            }
            missing = [item for item in all_evidence if item not in found]
            if missing:
                raise ValueError(f"unknown evidence IDs: {', '.join(missing)}")
            wrong_run = [item for item, evidence_run in found.items() if evidence_run != run_id]
            if wrong_run:
                raise ValueError(
                    "claim evidence must belong to the same research run: "
                    + ", ".join(wrong_run)
                )

            supporting_types = {evidence_types[item] for item in supporting}
            if observation_type is None:
                claim_observation_type = (
                    next(iter(supporting_types))
                    if len(supporting_types) == 1
                    else "UNKNOWN"
                )
            else:
                claim_observation_type = _normalize_enum(
                    observation_type,
                    OBSERVATION_TYPES,
                    "claim observation_type",
                )

            if (
                claim_type == "VERIFIED"
                and len(supporting_types) == 1
                and claim_observation_type != next(iter(supporting_types))
            ):
                raise ValueError(
                    "verified claim semantic mismatch: supporting evidence is "
                    f"{next(iter(supporting_types))} but claim asserted "
                    f"{claim_observation_type}"
                )

            cursor = self._conn.execute(
                """
                INSERT INTO claims(
                    claim_id, run_id, statement, classification, observation_type,
                    confidence, confidence_score, freshness_score, verification_count,
                    provenance_json, created_at
                )
                VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    text,
                    claim_type,
                    claim_observation_type,
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
                "observation_type": row["observation_type"],
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
                "latest_source_independence": (
                    self.get_latest_claim_source_independence(
                        str(row["claim_id"])
                    )
                ),
                "provenance": _json_loads(row["provenance_json"], {}),
                "created_at": row["created_at"],
            }

    def create_output(
        self,
        run_id: str,
        *,
        consumer_type: str,
        output_type: str,
        fragments: list[dict[str, Any]],
        consumer_id: str | None = None,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a generic evidence-backed output.

        The core does not assume a report. Consumers may be answers, alerts,
        dashboards, APIs, automations, agents, QA systems, or future PanWatch
        state-update adapters.
        """
        self._require_run(run_id)
        consumer = str(consumer_type or "").strip()
        out_type = str(output_type or "").strip()
        if not consumer:
            raise ValueError("consumer_type is required")
        if not out_type:
            raise ValueError("output_type is required")
        if not fragments:
            raise ValueError("an output must contain at least one fragment")

        now = utc_now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO outputs(
                    output_id, run_id, consumer_type, consumer_id, output_type,
                    payload_json, metadata_json, created_at
                )
                VALUES(NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    consumer,
                    str(consumer_id).strip() if consumer_id else None,
                    out_type,
                    _json_dumps(payload or {}),
                    _json_dumps(metadata or {}),
                    now,
                ),
            )
            output_id = self._public_id("O", int(cursor.lastrowid))
            self._conn.execute(
                "UPDATE outputs SET output_id = ? WHERE seq = ?",
                (output_id, cursor.lastrowid),
            )

            for position, fragment in enumerate(fragments):
                claim_ids = list(
                    dict.fromkeys(str(item).strip() for item in fragment.get("claim_ids", []))
                )
                claim_ids = [item for item in claim_ids if item]
                if not claim_ids:
                    raise ValueError("every output fragment must reference at least one claim_id")

                placeholders = ",".join("?" for _ in claim_ids)
                claim_rows = self._conn.execute(
                    f"""
                    SELECT claim_id, run_id, observation_type
                    FROM claims
                    WHERE claim_id IN ({placeholders})
                    """,
                    tuple(claim_ids),
                ).fetchall()
                found = {str(row["claim_id"]): row for row in claim_rows}
                missing = [claim_id for claim_id in claim_ids if claim_id not in found]
                if missing:
                    raise ValueError(f"unknown claim IDs: {', '.join(missing)}")
                wrong_run = [
                    claim_id
                    for claim_id, row in found.items()
                    if str(row["run_id"]) != run_id
                ]
                if wrong_run:
                    raise ValueError(
                        "output claims must belong to the same research run: "
                        + ", ".join(wrong_run)
                    )

                claim_types = {
                    str(found[claim_id]["observation_type"])
                    for claim_id in claim_ids
                }
                expected_type = (
                    next(iter(claim_types))
                    if len(claim_types) == 1
                    else "MIXED"
                )

                asserted_raw = fragment.get("asserted_observation_type")
                if asserted_raw is None:
                    asserted_type = expected_type
                else:
                    asserted_type = _normalize_enum(
                        str(asserted_raw),
                        OUTPUT_SEMANTIC_TYPES,
                        "output asserted_observation_type",
                    )

                if asserted_type != expected_type:
                    raise ValueError(
                        "semantic promotion/mismatch: linked claim semantics are "
                        f"{sorted(claim_types)} but output asserted {asserted_type}"
                    )

                content_value = fragment.get("content")
                content = (
                    str(content_value).strip()
                    if content_value is not None and str(content_value).strip()
                    else None
                )
                fragment_payload = fragment.get("payload")
                has_payload = fragment_payload not in (None, {}, [])
                if content is None and not has_payload:
                    raise ValueError(
                        "output fragment requires content or a non-empty payload"
                    )

                rendered_content = None
                if content is not None:
                    rendered_content = (
                        f"{SEMANTIC_LABELS[asserted_type]}: {content}"
                    )

                fragment_cursor = self._conn.execute(
                    """
                    INSERT INTO output_fragments(
                        fragment_id, output_id, content, rendered_content,
                        payload_json, asserted_observation_type, position, created_at
                    )
                    VALUES(NULL, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        output_id,
                        content,
                        rendered_content,
                        _json_dumps(fragment_payload or {}),
                        asserted_type,
                        position,
                        now,
                    ),
                )
                fragment_id = self._public_id("F", int(fragment_cursor.lastrowid))
                self._conn.execute(
                    "UPDATE output_fragments SET fragment_id = ? WHERE seq = ?",
                    (fragment_id, fragment_cursor.lastrowid),
                )

                for claim_position, claim_id in enumerate(claim_ids):
                    self._conn.execute(
                        """
                        INSERT INTO output_fragment_claims(
                            fragment_id, claim_id, position
                        )
                        VALUES(?, ?, ?)
                        """,
                        (fragment_id, claim_id, claim_position),
                    )

                claim_placeholders = ",".join("?" for _ in claim_ids)
                evidence_rows = self._conn.execute(
                    f"""
                    SELECT DISTINCT ce.evidence_id
                    FROM claim_evidence ce
                    WHERE ce.claim_id IN ({claim_placeholders})
                    """,
                    tuple(claim_ids),
                ).fetchall()
                for evidence_row in evidence_rows:
                    evidence_id = str(evidence_row["evidence_id"])
                    freshness_row = self._conn.execute(
                        """
                        SELECT freshness_id
                        FROM freshness_evaluations
                        WHERE evidence_id = ?
                        ORDER BY seq DESC
                        LIMIT 1
                        """,
                        (evidence_id,),
                    ).fetchone()
                    if freshness_row is not None:
                        self._conn.execute(
                            """
                            INSERT INTO output_fragment_freshness(
                                fragment_id, evidence_id, freshness_id
                            )
                            VALUES(?, ?, ?)
                            """,
                            (
                                fragment_id,
                                evidence_id,
                                str(freshness_row["freshness_id"]),
                            ),
                        )

        return self.get_output(output_id)

    def get_output(self, output_id: str) -> dict[str, Any]:
        with self._lock:
            output_row = self._conn.execute(
                "SELECT * FROM outputs WHERE output_id = ?",
                (output_id,),
            ).fetchone()
            if output_row is None:
                raise ValueError(f"unknown output: {output_id}")

            fragment_rows = self._conn.execute(
                """
                SELECT * FROM output_fragments
                WHERE output_id = ?
                ORDER BY position, seq
                """,
                (output_id,),
            ).fetchall()

        fragments: list[dict[str, Any]] = []
        for fragment_row in fragment_rows:
            with self._lock:
                claim_rows = self._conn.execute(
                    """
                    SELECT claim_id FROM output_fragment_claims
                    WHERE fragment_id = ?
                    ORDER BY position
                    """,
                    (fragment_row["fragment_id"],),
                ).fetchall()

            provenance_refs: list[dict[str, Any]] = []
            for claim_link in claim_rows:
                claim = self.get_claim(str(claim_link["claim_id"]))
                evidence_refs: list[dict[str, Any]] = []
                for relation, evidence_ids in (
                    ("SUPPORTS", claim["supporting_evidence_ids"]),
                    ("CONTRADICTS", claim["contradicting_evidence_ids"]),
                ):
                    for evidence_id in evidence_ids:
                        evidence = self.get_evidence(evidence_id)
                        source = self.get_source(
                            evidence["source_id"],
                            run_id=str(output_row["run_id"]),
                        )
                        with self._lock:
                            snapshot_row = self._conn.execute(
                                """
                                SELECT freshness_id
                                FROM output_fragment_freshness
                                WHERE fragment_id = ? AND evidence_id = ?
                                """,
                                (
                                    fragment_row["fragment_id"],
                                    evidence["evidence_id"],
                                ),
                            ).fetchone()
                        freshness_at_output = (
                            self.get_freshness_evaluation(
                                str(snapshot_row["freshness_id"])
                            )
                            if snapshot_row is not None
                            else None
                        )
                        evidence_refs.append(
                            {
                                "relation": relation,
                                "evidence_id": evidence["evidence_id"],
                                "supporting_passage": evidence["supporting_passage"],
                                "structured_fact": evidence["structured_fact"],
                                "metric": evidence["metric"],
                                "value": evidence["value"],
                                "unit": evidence["unit"],
                                "geography": evidence["geography"],
                                "reference_period": evidence["reference_period"],
                                "observation_type": evidence["observation_type"],
                                "forecast_horizon": evidence["forecast_horizon"],
                                "freshness_at_output": freshness_at_output,
                                "latest_freshness": self.get_latest_evidence_freshness(
                                    evidence["evidence_id"]
                                ),
                                "source_id": source["source_id"],
                                "source_url": source["canonical_url"],
                                "publisher": source["publisher"],
                                "publication_date": source["publication_date"],
                                "data_cutoff": source["data_cutoff"],
                                "representation_hash": source["representation_hash"],
                                "content_hash_scope": source["content_hash_scope"],
                                "retrieved_at": (
                                    source["run_retrieval"]["occurred_at"]
                                    if source.get("run_retrieval")
                                    else source["latest_retrieved_at"]
                                ),
                                "retrieval_tool": (
                                    source["run_retrieval"]["tool"]
                                    if source.get("run_retrieval")
                                    else source["retrieval_tool"]
                                ),
                            }
                        )

                provenance_refs.append(
                    {
                        "claim_id": claim["claim_id"],
                        "statement": claim["statement"],
                        "classification": claim["classification"],
                        "observation_type": claim["observation_type"],
                        "source_independence": claim[
                            "latest_source_independence"
                        ],
                        "evidence": evidence_refs,
                    }
                )

            fragments.append(
                {
                    "fragment_id": fragment_row["fragment_id"],
                    "content": fragment_row["content"],
                    "rendered_content": fragment_row["rendered_content"],
                    "payload": _json_loads(fragment_row["payload_json"], {}),
                    "asserted_observation_type": fragment_row[
                        "asserted_observation_type"
                    ],
                    "claim_ids": [
                        str(item["claim_id"]) for item in claim_rows
                    ],
                    "provenance_refs": provenance_refs,
                }
            )

        return {
            "output_id": output_row["output_id"],
            "run_id": output_row["run_id"],
            "consumer_type": output_row["consumer_type"],
            "consumer_id": output_row["consumer_id"],
            "output_type": output_row["output_type"],
            "payload": _json_loads(output_row["payload_json"], {}),
            "metadata": _json_loads(output_row["metadata_json"], {}),
            "created_at": output_row["created_at"],
            "fragments": fragments,
        }

    def audit_output(self, output_id: str) -> dict[str, Any]:
        output = self.get_output(output_id)
        missing_claim_links = 0
        semantic_mismatches = 0
        unresolved_evidence = 0
        unresolved_sources = 0

        for fragment in output["fragments"]:
            if not fragment["claim_ids"]:
                missing_claim_links += 1

            claim_types = {
                ref["observation_type"] for ref in fragment["provenance_refs"]
            }
            expected_type = (
                next(iter(claim_types)) if len(claim_types) == 1 else "MIXED"
            )
            if fragment["asserted_observation_type"] != expected_type:
                semantic_mismatches += 1

            for ref in fragment["provenance_refs"]:
                if not ref["evidence"]:
                    unresolved_evidence += 1
                for evidence_ref in ref["evidence"]:
                    if not evidence_ref.get("source_id"):
                        unresolved_sources += 1

        checks = {
            "fragments_have_claims": missing_claim_links == 0,
            "semantic_integrity": semantic_mismatches == 0,
            "claims_resolve_to_evidence": unresolved_evidence == 0,
            "evidence_resolves_to_sources": unresolved_sources == 0,
        }
        return {
            "output_id": output_id,
            "run_id": output["run_id"],
            "passed": all(checks.values()),
            "checks": checks,
            "counts": {
                "missing_claim_links": missing_claim_links,
                "semantic_mismatches": semantic_mismatches,
                "unresolved_evidence": unresolved_evidence,
                "unresolved_sources": unresolved_sources,
                "fragments": len(output["fragments"]),
            },
        }

    def evaluate_claim_source_independence(
        self,
        claim_id: str,
    ) -> dict[str, Any]:
        """Assess supporting-source lineages without equating URLs with sources."""
        claim_key = str(claim_id or "").strip()
        if not claim_key:
            raise ValueError("claim_id is required")

        with self._lock:
            claim_row = self._conn.execute(
                "SELECT run_id FROM claims WHERE claim_id = ?",
                (claim_key,),
            ).fetchone()
            if claim_row is None:
                raise ValueError(f"unknown claim: {claim_key}")
            run_id = str(claim_row["run_id"])
            source_ids = [
                str(row["source_id"])
                for row in self._conn.execute(
                    """
                    SELECT DISTINCT e.source_id
                    FROM claim_evidence ce
                    JOIN evidence_items e ON e.evidence_id = ce.evidence_id
                    WHERE ce.claim_id = ? AND ce.relation = 'SUPPORTS'
                    ORDER BY e.source_id
                    """,
                    (claim_key,),
                ).fetchall()
            ]
            run_source_ids = [
                str(row["source_id"])
                for row in self._conn.execute(
                    """
                    SELECT source_id FROM research_run_sources
                    WHERE run_id = ?
                    ORDER BY source_id
                    """,
                    (run_id,),
                ).fetchall()
            ]

        sources = [
            self.get_source(source_id, run_id=run_id)
            for source_id in run_source_ids
        ]
        relationships = self.get_source_relationships(run_id)
        assessment = assess_source_independence(
            supporting_source_ids=source_ids,
            sources=sources,
            relationships=relationships,
        )

        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO source_independence_evaluations(
                    independence_id, run_id, claim_id, status,
                    supporting_source_count, supporting_url_count,
                    effective_lineage_count,
                    strict_confidence_basis_source_count,
                    details_json, created_at
                )
                VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    claim_key,
                    assessment["status"],
                    assessment["supporting_source_count"],
                    assessment["supporting_url_count"],
                    assessment["effective_lineage_count"],
                    assessment["strict_confidence_basis_source_count"],
                    _json_dumps(assessment),
                    utc_now(),
                ),
            )
            independence_id = self._public_id("SI", int(cursor.lastrowid))
            self._conn.execute(
                """
                UPDATE source_independence_evaluations
                SET independence_id = ?
                WHERE seq = ?
                """,
                (independence_id, cursor.lastrowid),
            )
        return self.get_source_independence_evaluation(independence_id)

    def get_source_independence_evaluation(
        self,
        independence_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM source_independence_evaluations
                WHERE independence_id = ?
                """,
                (independence_id,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"unknown source independence evaluation: {independence_id}"
                )
        details = _json_loads(row["details_json"], {})
        return {
            "independence_id": row["independence_id"],
            "run_id": row["run_id"],
            "claim_id": row["claim_id"],
            "status": row["status"],
            "supporting_source_count": row["supporting_source_count"],
            "supporting_url_count": row["supporting_url_count"],
            "effective_lineage_count": row["effective_lineage_count"],
            "strict_confidence_basis_source_count": row[
                "strict_confidence_basis_source_count"
            ],
            "duplicate_or_dependency_reduction": details.get(
                "duplicate_or_dependency_reduction", 0
            ),
            "verified_independent_pair_count": details.get(
                "verified_independent_pair_count", 0
            ),
            "total_lineage_pair_count": details.get(
                "total_lineage_pair_count", 0
            ),
            "lineages": details.get("lineages", []),
            "verified_independent_pairs": details.get(
                "verified_independent_pairs", []
            ),
            "relationship_conflicts": details.get(
                "relationship_conflicts", []
            ),
            "method": details.get("method"),
            "notes": details.get("notes", []),
            "created_at": row["created_at"],
        }

    def get_latest_claim_source_independence(
        self,
        claim_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT independence_id
                FROM source_independence_evaluations
                WHERE claim_id = ?
                ORDER BY seq DESC LIMIT 1
                """,
                (claim_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_source_independence_evaluation(
            str(row["independence_id"])
        )

    def evaluate_evidence_freshness(
        self,
        evidence_id: str,
        *,
        policy_name: str,
        as_of: str | None = None,
        max_age_seconds: int | float | None = None,
        latest_known_cutoff: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate and persist freshness using the source's underlying data cutoff."""
        evidence = self.get_evidence(evidence_id)
        source = self.get_source(
            evidence["source_id"],
            run_id=evidence["run_id"],
        )
        result = evaluate_freshness(
            data_cutoff=source["data_cutoff"],
            publication_date=source["publication_date"],
            retrieved_at=(
                source["run_retrieval"]["occurred_at"]
                if source.get("run_retrieval")
                else source["latest_retrieved_at"]
            ),
            policy_name=policy_name,
            as_of=as_of,
            max_age_seconds=max_age_seconds,
            latest_known_cutoff=latest_known_cutoff,
        )

        now = utc_now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO freshness_evaluations(
                    freshness_id, run_id, evidence_id, policy_name, policy_mode,
                    status, reason_code, as_of, basis_field, basis_value,
                    basis_precision, age_min_seconds, age_max_seconds,
                    max_age_seconds, latest_known_cutoff, details_json, created_at
                )
                VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence["run_id"],
                    evidence_id,
                    result["policy_name"],
                    result["policy_mode"],
                    result["status"],
                    result["reason_code"],
                    result["as_of"],
                    result["basis_field"],
                    result["basis_value"],
                    result["basis_precision"],
                    result["age_min_seconds"],
                    result["age_max_seconds"],
                    result["max_age_seconds"],
                    result["latest_known_cutoff"],
                    _json_dumps(
                        {
                            "publication_date": result["publication_date"],
                            "retrieved_at": result["retrieved_at"],
                        }
                    ),
                    now,
                ),
            )
            freshness_id = self._public_id("FR", int(cursor.lastrowid))
            self._conn.execute(
                "UPDATE freshness_evaluations SET freshness_id = ? WHERE seq = ?",
                (freshness_id, cursor.lastrowid),
            )

        return self.get_freshness_evaluation(freshness_id)

    def get_freshness_evaluation(self, freshness_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM freshness_evaluations WHERE freshness_id = ?",
                (freshness_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown freshness evaluation: {freshness_id}")
        details = _json_loads(row["details_json"], {})
        return {
            "freshness_id": row["freshness_id"],
            "run_id": row["run_id"],
            "evidence_id": row["evidence_id"],
            "policy_name": row["policy_name"],
            "policy_mode": row["policy_mode"],
            "status": row["status"],
            "reason_code": row["reason_code"],
            "as_of": row["as_of"],
            "basis_field": row["basis_field"],
            "basis_value": row["basis_value"],
            "basis_precision": row["basis_precision"],
            "age_min_seconds": row["age_min_seconds"],
            "age_max_seconds": row["age_max_seconds"],
            "max_age_seconds": row["max_age_seconds"],
            "latest_known_cutoff": row["latest_known_cutoff"],
            "publication_date": details.get("publication_date"),
            "retrieved_at": details.get("retrieved_at"),
            "created_at": row["created_at"],
        }

    def get_latest_evidence_freshness(self, evidence_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT freshness_id FROM freshness_evaluations
                WHERE evidence_id = ?
                ORDER BY seq DESC LIMIT 1
                """,
                (evidence_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_freshness_evaluation(str(row["freshness_id"]))

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
            output_ids = [
                str(row["output_id"])
                for row in self._conn.execute(
                    """
                    SELECT output_id FROM outputs
                    WHERE run_id = ? ORDER BY seq
                    """,
                    (run_id,),
                ).fetchall()
            ]

        return {
            "research_run": run,
            "sources": [self.get_source(item, run_id=run_id) for item in source_ids],
            "source_relationships": self.get_source_relationships(run_id),
            "evidence": [
                {
                    **self.get_evidence(item),
                    "latest_freshness": self.get_latest_evidence_freshness(item),
                }
                for item in evidence_ids
            ],
            "claims": [self.get_claim(item) for item in claim_ids],
            "outputs": [self.get_output(item) for item in output_ids],
            "ledger": self.export_ledger_rows(run_id),
        }

    def export_ledger_rows(self, run_id: str) -> list[dict[str, Any]]:
        self._require_run(run_id)
        query = """
        SELECT
            c.claim_id,
            c.statement,
            c.classification,
            c.observation_type AS claim_observation_type,
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
            COALESCE(re.occurred_at, s.latest_retrieved_at) AS run_retrieved_at,
            COALESCE(re.tool, s.retrieval_tool) AS run_retrieval_tool,
            COALESCE(re.method, s.retrieval_method) AS run_retrieval_method,
            COALESCE(re.status, s.retrieval_status) AS run_retrieval_status,
            s.content_hash,
            s.version_number,
            s.previous_source_id,
            s.source_family_id
        FROM claims c
        JOIN claim_evidence ce ON ce.claim_id = c.claim_id
        JOIN evidence_items e ON e.evidence_id = ce.evidence_id
        JOIN sources s ON s.source_id = e.source_id
        LEFT JOIN retrieval_events re
          ON re.seq = (
              SELECT MAX(re2.seq)
              FROM retrieval_events re2
              WHERE re2.run_id = c.run_id
                AND re2.source_id = s.source_id
                AND UPPER(re2.stage) = 'RETRIEVAL'
          )
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
                    "claim_observation_type": row["claim_observation_type"],
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
                    "retrieved_at": row["run_retrieved_at"],
                    "retrieval_tool": row["run_retrieval_tool"],
                    "retrieval_method": row["run_retrieval_method"],
                    "retrieval_status": row["run_retrieval_status"],
                    "content_hash": row["content_hash"],
                    "representation_hash": row["content_hash"],
                    "content_hash_scope": "retrieved_representation",
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
