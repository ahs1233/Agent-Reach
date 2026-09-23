"""Ahmed Content Experiment Engine (ACE).

ACE is a domain layer over Ahmed Toolbox retrieval/research/runtime capabilities.
It owns campaign, experiment, measurement, cost, and learning state while
research provenance remains in the existing ResearchStore.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

FINDING_KINDS = {"FACT", "OBSERVATION", "INFERENCE", "HYPOTHESIS"}
EVALUATION_STATES = {"WINNER", "LOSER", "INCONCLUSIVE", "NEEDS_MORE_DATA"}
EXPERIMENT_STATES = {"DRAFT", "READY", "RUNNING", "COMPLETED", "PAUSED"}
PERSONA_TYPES = {
    "BRAND_ACCOUNT",
    "FOUNDER_ACCOUNT",
    "FACELESS_ACCOUNT",
    "AI_CHARACTER",
    "EDUCATIONAL_PERSONA",
    "ENTERTAINMENT_PERSONA",
    "PRODUCT_FOCUSED_ACCOUNT",
}
PLATFORMS = {"instagram", "tiktok", "youtube_shorts"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _ratio(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if 1.0 < number <= 100.0:
        number /= 100.0
    return max(0.0, min(number, 1.0))


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _slug_words(text: str, limit: int = 5) -> list[str]:
    words = re.findall(r"[A-Za-z0-9\u0600-\u06ff]+", text.lower())
    out: list[str] = []
    for word in words:
        if len(word) < 3 or word in out:
            continue
        out.append(word)
        if len(out) >= limit:
            break
    return out


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    capabilities: tuple[str, ...]
    estimated_cost: float
    quality: float
    latency_seconds: float
    quota: str = "local"
    paid: bool = False
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CreativeProvider(Protocol):
    spec: ProviderSpec

    def generate(
        self,
        kind: str,
        prompt: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class LocalManifestProvider:
    """Zero-cost provider for scripts/manifests, never fake media bytes."""

    spec = ProviderSpec(
        name="local_manifest",
        capabilities=(
            "script",
            "caption",
            "storyboard",
            "thumbnail_spec",
            "publishing_package",
        ),
        estimated_cost=0.0,
        quality=0.55,
        latency_seconds=0.01,
        quota="local",
        paid=False,
        available=True,
    )

    def generate(
        self,
        kind: str,
        prompt: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if kind not in self.spec.capabilities:
            raise ValueError(f"local_manifest does not support {kind}")
        return {
            "provider": self.spec.name,
            "kind": kind,
            "prompt": prompt,
            "context": context,
            "artifact_type": "MANIFEST",
            "media_generated": False,
            "estimated_cost": 0.0,
        }


class CostAwareRouter:
    """Prefer local/free providers and never cross into paid when FREE_ONLY."""

    def __init__(self, providers: list[CreativeProvider] | None = None):
        self.providers = providers or [LocalManifestProvider()]

    def route(self, kind: str, *, free_only: bool = True) -> CreativeProvider:
        candidates = [
            provider
            for provider in self.providers
            if provider.spec.available
            and kind in provider.spec.capabilities
            and (not free_only or not provider.spec.paid)
        ]
        if not candidates:
            mode = "FREE_ONLY" if free_only else "configured"
            raise ValueError(f"no {mode} creative provider available for {kind}")
        candidates.sort(
            key=lambda provider: (
                provider.spec.paid,
                provider.spec.estimated_cost,
                -provider.spec.quality,
                provider.spec.latency_seconds,
            )
        )
        return candidates[0]


_GUARDRAILS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "fake_testimonial",
        re.compile(
            r"\b(?:thousands|hundreds) of (?:customers|patients) say\b",
            re.I,
        ),
    ),
    (
        "fake_credentials",
        re.compile(
            r"\b(?:certified doctor|licensed physician|officially certified expert)\b",
            re.I,
        ),
    ),
    (
        "fabricated_authority",
        re.compile(
            r"\b(?:as a doctor|as your lawyer|as a licensed financial adviser)\b",
            re.I,
        ),
    ),
    (
        "guaranteed_outcome",
        re.compile(
            r"\b(?:guaranteed profit|guaranteed cure|100% guaranteed return)\b",
            re.I,
        ),
    ),
    (
        "impersonation",
        re.compile(
            r"\b(?:I am|we are) (?:Meta|TikTok|YouTube|a government ministry)\b",
            re.I,
        ),
    ),
)


def validate_content_claims(
    text: str,
    *,
    persona_type: str | None = None,
    transparent_ai: bool = False,
) -> dict[str, Any]:
    violations = [
        name
        for name, pattern in _GUARDRAILS
        if pattern.search(text or "")
    ]
    if str(persona_type or "").upper() == "AI_CHARACTER" and not transparent_ai:
        violations.append("ai_character_must_be_transparent")
    return {
        "allowed": not violations,
        "violations": sorted(set(violations)),
    }


def infer_content_signals(text: str) -> list[str]:
    lowered = (text or "").lower()
    signals: list[str] = []
    checks = {
        "question_hook": ("?", "why ", "how ", "شنو", "ليش", "كيف"),
        "demonstration": (
            "demo",
            "demonstration",
            "before and after",
            "watch this",
            "شوف",
            "تجربة",
        ),
        "price_comparison": (
            "price",
            "cheaper",
            " vs ",
            "compare",
            "سعر",
            "أرخص",
            "مقارنة",
        ),
        "problem_solution": ("problem", "solution", "pain", "مشكلة", "حل"),
        "social_proof": (
            "review",
            "rating",
            "customer",
            "testimonial",
            "تقييم",
            "زبون",
        ),
        "clear_cta": (
            "download",
            "try",
            "shop",
            "order",
            "جرّب",
            "حمّل",
            "اطلب",
        ),
        "local_language": (
            "iraq",
            "iraqi",
            "baghdad",
            "najaf",
            "العراق",
            "عراقي",
            "النجف",
            "بغداد",
        ),
    }
    for label, needles in checks.items():
        if any(needle in lowered for needle in needles):
            signals.append(label)
    return signals


class ACEStore:
    """SQLite-backed ACE state. Research evidence remains in ResearchStore."""

    def __init__(self, db_path: str | None = None):
        configured = db_path or os.environ.get("AHMED_ACE_DB_PATH")
        self.db_path = configured or str(Path.home() / ".agent-reach" / "ace.db")
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
    def from_environment(cls) -> "ACEStore":
        return cls(os.environ.get("AHMED_ACE_DB_PATH"))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _create_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS campaigns(
          campaign_id TEXT PRIMARY KEY,
          goal TEXT NOT NULL,
          objective TEXT NOT NULL,
          platforms_json TEXT NOT NULL,
          free_only INTEGER NOT NULL,
          status TEXT NOT NULL,
          research_run_id TEXT,
          metadata_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS research_findings(
          finding_id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          summary TEXT NOT NULL,
          source_ref TEXT,
          source_url TEXT,
          retrieved_at TEXT NOT NULL,
          retrieval_method TEXT,
          confidence REAL NOT NULL,
          evidence_ids_json TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          FOREIGN KEY(campaign_id) REFERENCES campaigns(campaign_id)
        );
        CREATE TABLE IF NOT EXISTS personas(
          persona_id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL,
          persona_type TEXT NOT NULL,
          identity_json TEXT NOT NULL,
          audience_json TEXT NOT NULL,
          voice_json TEXT NOT NULL,
          visual_language_json TEXT NOT NULL,
          content_pillars_json TEXT NOT NULL,
          allowed_claims_json TEXT NOT NULL,
          prohibited_claims_json TEXT NOT NULL,
          cta_style TEXT,
          consistency_rules_json TEXT NOT NULL,
          transparent_ai INTEGER NOT NULL,
          FOREIGN KEY(campaign_id) REFERENCES campaigns(campaign_id)
        );
        CREATE TABLE IF NOT EXISTS hypotheses(
          hypothesis_id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL,
          target_audience TEXT NOT NULL,
          variable TEXT NOT NULL,
          control_json TEXT NOT NULL,
          variant_json TEXT NOT NULL,
          expected_outcome TEXT NOT NULL,
          primary_metric TEXT NOT NULL,
          secondary_metrics_json TEXT NOT NULL,
          minimum_evidence INTEGER NOT NULL,
          confidence REAL NOT NULL,
          status TEXT NOT NULL,
          evidence_refs_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(campaign_id) REFERENCES campaigns(campaign_id)
        );
        CREATE TABLE IF NOT EXISTS experiments(
          experiment_id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL,
          hypothesis_id TEXT NOT NULL,
          name TEXT NOT NULL,
          objective TEXT NOT NULL,
          variable TEXT NOT NULL,
          primary_metric TEXT NOT NULL,
          secondary_metrics_json TEXT NOT NULL,
          minimum_sample INTEGER NOT NULL,
          status TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(campaign_id) REFERENCES campaigns(campaign_id)
        );
        CREATE TABLE IF NOT EXISTS variants(
          variant_id TEXT PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          label TEXT NOT NULL,
          is_control INTEGER NOT NULL,
          persona_id TEXT,
          script_json TEXT NOT NULL,
          variable_value_json TEXT NOT NULL,
          rationale TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
        );
        CREATE TABLE IF NOT EXISTS assets(
          asset_id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL,
          variant_id TEXT,
          kind TEXT NOT NULL,
          provider TEXT NOT NULL,
          uri TEXT,
          payload_json TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          estimated_cost REAL NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(campaign_id, content_hash),
          FOREIGN KEY(campaign_id) REFERENCES campaigns(campaign_id)
        );
        CREATE TABLE IF NOT EXISTS publications(
          publication_id TEXT PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          variant_id TEXT NOT NULL,
          platform TEXT NOT NULL,
          status TEXT NOT NULL,
          package_json TEXT NOT NULL,
          external_id TEXT,
          published_at TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
        );
        CREATE TABLE IF NOT EXISTS metric_snapshots(
          metric_snapshot_id TEXT PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          variant_id TEXT NOT NULL,
          platform TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          age_hours REAL NOT NULL,
          account_size INTEGER,
          metrics_json TEXT NOT NULL,
          FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
        );
        CREATE TABLE IF NOT EXISTS evaluations(
          evaluation_id TEXT PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          state TEXT NOT NULL,
          winner_variant_id TEXT,
          confidence REAL NOT NULL,
          scores_json TEXT NOT NULL,
          uncertainty_json TEXT NOT NULL,
          reasons_json TEXT NOT NULL,
          recommendation TEXT NOT NULL,
          evaluated_at TEXT NOT NULL,
          FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
        );
        CREATE TABLE IF NOT EXISTS learnings(
          learning_id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL,
          experiment_id TEXT NOT NULL,
          worked_json TEXT NOT NULL,
          failed_json TEXT NOT NULL,
          changed_json TEXT NOT NULL,
          uncertain_json TEXT NOT NULL,
          next_tests_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(campaign_id) REFERENCES campaigns(campaign_id)
        );
        CREATE TABLE IF NOT EXISTS cost_records(
          cost_record_id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL,
          experiment_id TEXT,
          provider TEXT NOT NULL,
          operation TEXT NOT NULL,
          estimated_cost REAL NOT NULL,
          actual_cost REAL,
          currency TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(campaign_id) REFERENCES campaigns(campaign_id)
        );
        """
        with self._lock, self._conn:
            self._conn.executescript(schema)

    def _one(self, query: str, params: tuple[Any, ...]) -> sqlite3.Row:
        row = self._conn.execute(query, params).fetchone()
        if row is None:
            raise ValueError("record not found")
        return row

    def create_campaign(
        self,
        goal: str,
        *,
        objective: str = "balanced",
        platforms: list[str] | None = None,
        free_only: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        goal = str(goal or "").strip()
        if not goal:
            raise ValueError("goal is required")
        selected = [
            str(platform).lower()
            for platform in (platforms or ["instagram", "tiktok"])
        ]
        invalid = [platform for platform in selected if platform not in PLATFORMS]
        if invalid:
            raise ValueError(f"unsupported platforms: {', '.join(invalid)}")
        campaign_id = _id("cmp")
        now = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    campaign_id,
                    goal,
                    str(objective or "balanced"),
                    _dump(selected),
                    int(free_only),
                    "ACTIVE",
                    None,
                    _dump(metadata or {}),
                    now,
                    now,
                ),
            )
        return self.get_campaign(campaign_id)

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._one(
                "SELECT * FROM campaigns WHERE campaign_id=?",
                (campaign_id,),
            )
        data = dict(row)
        data["platforms"] = _load(data.pop("platforms_json"), [])
        data["metadata"] = _load(data.pop("metadata_json"), {})
        data["free_only"] = bool(data["free_only"])
        return data

    def set_research_run(self, campaign_id: str, run_id: str) -> None:
        with self._lock, self._conn:
            self._one(
                "SELECT campaign_id FROM campaigns WHERE campaign_id=?",
                (campaign_id,),
            )
            self._conn.execute(
                "UPDATE campaigns SET research_run_id=?, updated_at=? WHERE campaign_id=?",
                (run_id, utc_now(), campaign_id),
            )

    def add_finding(
        self,
        campaign_id: str,
        *,
        kind: str,
        summary: str,
        source_ref: str | None = None,
        source_url: str | None = None,
        retrieval_method: str | None = None,
        confidence: float = 0.5,
        evidence_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = str(kind).upper()
        if normalized not in FINDING_KINDS:
            raise ValueError("invalid finding kind")
        finding_id = _id("find")
        with self._lock, self._conn:
            self._one(
                "SELECT campaign_id FROM campaigns WHERE campaign_id=?",
                (campaign_id,),
            )
            self._conn.execute(
                "INSERT INTO research_findings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    finding_id,
                    campaign_id,
                    normalized,
                    str(summary).strip(),
                    source_ref,
                    source_url,
                    utc_now(),
                    retrieval_method,
                    max(0.0, min(float(confidence), 1.0)),
                    _dump(evidence_ids or []),
                    _dump(metadata or {}),
                ),
            )
        return self.get_finding(finding_id)

    def get_finding(self, finding_id: str) -> dict[str, Any]:
        row = self._one(
            "SELECT * FROM research_findings WHERE finding_id=?",
            (finding_id,),
        )
        data = dict(row)
        data["evidence_ids"] = _load(data.pop("evidence_ids_json"), [])
        data["metadata"] = _load(data.pop("metadata_json"), {})
        return data

    def list_findings(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT finding_id FROM research_findings
            WHERE campaign_id=? ORDER BY retrieved_at
            """,
            (campaign_id,),
        ).fetchall()
        return [
            self.get_finding(str(row["finding_id"]))
            for row in rows
        ]

    def derive_content_patterns(self, campaign_id: str) -> list[dict[str, Any]]:
        findings = self.list_findings(campaign_id)
        counts: dict[str, int] = {}
        for finding in findings:
            for signal in finding["metadata"].get("signals", []):
                key = str(signal)
                counts[key] = counts.get(key, 0) + 1
        sample_size = len(findings)
        denominator = max(1, sample_size)
        patterns: list[dict[str, Any]] = []
        for name, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            frequency = count / denominator
            confidence = min(
                0.95,
                frequency * (1 - math.exp(-sample_size / 4)),
            )
            patterns.append(
                {
                    "pattern": name,
                    "support": count,
                    "sample_size": sample_size,
                    "frequency": round(frequency, 3),
                    "confidence": round(confidence, 3),
                    "causation_claimed": False,
                }
            )
        return patterns

    def create_persona(
        self,
        campaign_id: str,
        *,
        persona_type: str,
        identity: dict[str, Any],
        audience: dict[str, Any],
        voice: dict[str, Any],
        visual_language: dict[str, Any],
        content_pillars: list[str],
        allowed_claims: list[str],
        prohibited_claims: list[str],
        cta_style: str,
        consistency_rules: list[str],
        transparent_ai: bool = False,
    ) -> dict[str, Any]:
        normalized = str(persona_type).upper()
        if normalized not in PERSONA_TYPES:
            raise ValueError("invalid persona_type")
        if normalized == "AI_CHARACTER" and not transparent_ai:
            raise ValueError("AI_CHARACTER must be transparent")
        persona_id = _id("per")
        with self._lock, self._conn:
            self._one(
                "SELECT campaign_id FROM campaigns WHERE campaign_id=?",
                (campaign_id,),
            )
            self._conn.execute(
                "INSERT INTO personas VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    persona_id,
                    campaign_id,
                    normalized,
                    _dump(identity),
                    _dump(audience),
                    _dump(voice),
                    _dump(visual_language),
                    _dump(content_pillars),
                    _dump(allowed_claims),
                    _dump(prohibited_claims),
                    cta_style,
                    _dump(consistency_rules),
                    int(transparent_ai),
                ),
            )
        return {
            "persona_id": persona_id,
            "campaign_id": campaign_id,
            "persona_type": normalized,
            "identity": identity,
            "audience": audience,
            "voice": voice,
            "visual_language": visual_language,
            "content_pillars": content_pillars,
            "allowed_claims": allowed_claims,
            "prohibited_claims": prohibited_claims,
            "cta_style": cta_style,
            "consistency_rules": consistency_rules,
            "transparent_ai": transparent_ai,
        }

    def create_hypothesis(
        self,
        campaign_id: str,
        *,
        target_audience: str,
        variable: str,
        control: dict[str, Any],
        variant: dict[str, Any],
        expected_outcome: str,
        primary_metric: str,
        secondary_metrics: list[str],
        minimum_evidence: int = 200,
        confidence: float = 0.5,
        status: str = "PROPOSED",
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        hypothesis_id = _id("hyp")
        minimum = max(20, int(minimum_evidence))
        bounded_confidence = max(0.0, min(float(confidence), 1.0))
        created_at = utc_now()
        with self._lock, self._conn:
            self._one(
                "SELECT campaign_id FROM campaigns WHERE campaign_id=?",
                (campaign_id,),
            )
            self._conn.execute(
                "INSERT INTO hypotheses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    hypothesis_id,
                    campaign_id,
                    target_audience,
                    variable,
                    _dump(control),
                    _dump(variant),
                    expected_outcome,
                    primary_metric,
                    _dump(secondary_metrics),
                    minimum,
                    bounded_confidence,
                    status,
                    _dump(evidence_refs or []),
                    created_at,
                ),
            )
        return {
            "hypothesis_id": hypothesis_id,
            "campaign_id": campaign_id,
            "target_audience": target_audience,
            "variable": variable,
            "control": control,
            "variant": variant,
            "expected_outcome": expected_outcome,
            "primary_metric": primary_metric,
            "secondary_metrics": secondary_metrics,
            "minimum_evidence": minimum,
            "confidence": bounded_confidence,
            "status": status,
            "evidence_refs": evidence_refs or [],
            "created_at": created_at,
        }

    def create_experiment(
        self,
        campaign_id: str,
        hypothesis: dict[str, Any],
        *,
        name: str | None = None,
    ) -> dict[str, Any]:
        experiment_id = _id("exp")
        with self._lock, self._conn:
            campaign = self.get_campaign(campaign_id)
            self._conn.execute(
                "INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    experiment_id,
                    campaign_id,
                    hypothesis["hypothesis_id"],
                    name or hypothesis["variable"],
                    campaign["objective"],
                    hypothesis["variable"],
                    hypothesis["primary_metric"],
                    _dump(hypothesis["secondary_metrics"]),
                    hypothesis["minimum_evidence"],
                    "READY",
                    _dump({}),
                    utc_now(),
                ),
            )
        return self.get_experiment(experiment_id)

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        row = self._one(
            "SELECT * FROM experiments WHERE experiment_id=?",
            (experiment_id,),
        )
        data = dict(row)
        data["secondary_metrics"] = _load(
            data.pop("secondary_metrics_json"),
            [],
        )
        data["metadata"] = _load(data.pop("metadata_json"), {})
        return data

    def list_experiments(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT experiment_id FROM experiments
            WHERE campaign_id=? ORDER BY created_at
            """,
            (campaign_id,),
        ).fetchall()
        return [
            self.get_experiment(str(row["experiment_id"]))
            for row in rows
        ]

    def add_variant(
        self,
        experiment_id: str,
        *,
        label: str,
        is_control: bool,
        persona_id: str | None,
        script: dict[str, Any],
        variable_value: dict[str, Any],
        rationale: str,
    ) -> dict[str, Any]:
        self._one(
            "SELECT experiment_id FROM experiments WHERE experiment_id=?",
            (experiment_id,),
        )
        check = validate_content_claims(_dump(script))
        if not check["allowed"]:
            raise ValueError(
                "unsafe script: " + ",".join(check["violations"])
            )
        variant_id = _id("var")
        digest = hashlib.sha256(_dump(script).encode()).hexdigest()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO variants VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    variant_id,
                    experiment_id,
                    label,
                    int(is_control),
                    persona_id,
                    _dump(script),
                    _dump(variable_value),
                    rationale,
                    digest,
                ),
            )
        return {
            "variant_id": variant_id,
            "experiment_id": experiment_id,
            "label": label,
            "is_control": bool(is_control),
            "persona_id": persona_id,
            "script": script,
            "variable_value": variable_value,
            "rationale": rationale,
            "content_hash": digest,
        }

    def list_variants(self, experiment_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM variants
            WHERE experiment_id=? ORDER BY rowid
            """,
            (experiment_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["is_control"] = bool(item["is_control"])
            item["script"] = _load(item.pop("script_json"), {})
            item["variable_value"] = _load(
                item.pop("variable_value_json"),
                {},
            )
            result.append(item)
        return result

    def add_asset(
        self,
        campaign_id: str,
        *,
        variant_id: str | None,
        kind: str,
        provider: str,
        payload: dict[str, Any],
        estimated_cost: float = 0.0,
        uri: str | None = None,
    ) -> dict[str, Any]:
        self._one(
            "SELECT campaign_id FROM campaigns WHERE campaign_id=?",
            (campaign_id,),
        )
        digest = hashlib.sha256(
            _dump(
                {
                    "kind": kind,
                    "variant_id": variant_id,
                    "payload": payload,
                }
            ).encode()
        ).hexdigest()
        asset_id = _id("asset")
        now = utc_now()
        with self._lock, self._conn:
            duplicate = self._conn.execute(
                """
                SELECT asset_id FROM assets
                WHERE campaign_id=? AND content_hash=?
                """,
                (campaign_id, digest),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    f"duplicate content: {duplicate['asset_id']}"
                )
            self._conn.execute(
                "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    asset_id,
                    campaign_id,
                    variant_id,
                    kind,
                    provider,
                    uri,
                    _dump(payload),
                    digest,
                    float(estimated_cost),
                    now,
                ),
            )
            self._conn.execute(
                "INSERT INTO cost_records VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    _id("cost"),
                    campaign_id,
                    None,
                    provider,
                    f"generate:{kind}",
                    float(estimated_cost),
                    None,
                    "USD",
                    _dump({"asset_id": asset_id}),
                    now,
                ),
            )
        return {
            "asset_id": asset_id,
            "campaign_id": campaign_id,
            "variant_id": variant_id,
            "kind": kind,
            "provider": provider,
            "uri": uri,
            "payload": payload,
            "content_hash": digest,
            "estimated_cost": float(estimated_cost),
        }

    def create_publication_package(
        self,
        experiment_id: str,
        variant: dict[str, Any],
        *,
        platform: str,
        package: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = platform.lower()
        if normalized not in PLATFORMS:
            raise ValueError("unsupported platform")
        publication_id = _id("pub")
        now = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO publications VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    publication_id,
                    experiment_id,
                    variant["variant_id"],
                    normalized,
                    "PACKAGE_READY",
                    _dump(package),
                    None,
                    None,
                    now,
                ),
            )
        return {
            "publication_id": publication_id,
            "experiment_id": experiment_id,
            "variant_id": variant["variant_id"],
            "platform": normalized,
            "status": "PACKAGE_READY",
            "package": package,
            "created_at": now,
        }

    def record_metrics(
        self,
        experiment_id: str,
        variant_id: str,
        *,
        platform: str,
        metrics: dict[str, Any],
        age_hours: float = 24.0,
        account_size: int | None = None,
    ) -> dict[str, Any]:
        self._one(
            """
            SELECT variant_id FROM variants
            WHERE experiment_id=? AND variant_id=?
            """,
            (experiment_id, variant_id),
        )
        metric_id = _id("met")
        normalized_platform = str(platform).lower()
        captured_at = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO metric_snapshots VALUES (?,?,?,?,?,?,?,?)",
                (
                    metric_id,
                    experiment_id,
                    variant_id,
                    normalized_platform,
                    captured_at,
                    max(0.0, float(age_hours)),
                    account_size,
                    _dump(metrics),
                ),
            )
        return {
            "metric_snapshot_id": metric_id,
            "experiment_id": experiment_id,
            "variant_id": variant_id,
            "platform": normalized_platform,
            "metrics": metrics,
            "age_hours": max(0.0, float(age_hours)),
            "account_size": account_size,
            "captured_at": captured_at,
        }

    def _latest_metrics(
        self,
        experiment_id: str,
    ) -> dict[str, dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT m.*
            FROM metric_snapshots m
            JOIN (
              SELECT variant_id, MAX(captured_at) AS latest
              FROM metric_snapshots
              WHERE experiment_id=?
              GROUP BY variant_id
            ) x
              ON x.variant_id=m.variant_id
             AND x.latest=m.captured_at
            WHERE m.experiment_id=?
            """,
            (experiment_id, experiment_id),
        ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            item["metrics"] = _load(
                item.pop("metrics_json"),
                {},
            )
            result[str(row["variant_id"])] = item
        return result

    @staticmethod
    def _performance(
        metrics: dict[str, Any],
        objective: str,
    ) -> tuple[float, dict[str, float], int]:
        views = max(0, int(_safe_float(metrics.get("views"))))
        reach = max(0, int(_safe_float(metrics.get("reach"))))
        exposure = max(views, reach, 1)

        completion = _ratio(metrics.get("completion_rate"))
        average_duration = _safe_float(
            metrics.get("average_view_duration")
        )
        if completion <= 0 and average_duration > 0:
            length = max(
                1.0,
                _safe_float(
                    metrics.get("content_length_seconds") or 30
                ),
            )
            completion = min(1.0, average_duration / length)

        engagement = (
            _safe_float(metrics.get("likes"))
            + 2 * _safe_float(metrics.get("comments"))
            + 3 * _safe_float(metrics.get("shares"))
            + 3 * _safe_float(metrics.get("saves"))
        ) / exposure
        intent = (
            _safe_float(metrics.get("profile_visits"))
            + 2 * _safe_float(metrics.get("link_clicks"))
            + _safe_float(metrics.get("followers_gained"))
        ) / exposure
        conversion = _safe_float(
            metrics.get("conversions")
        ) / exposure

        components = {
            "retention": min(completion, 1.0),
            "engagement": min(engagement, 1.0),
            "intent": min(intent, 1.0),
            "conversion": min(conversion, 1.0),
        }

        objective_lower = objective.lower()
        if (
            "conversion" in objective_lower
            or "revenue" in objective_lower
        ):
            weights = {
                "retention": 0.15,
                "engagement": 0.15,
                "intent": 0.25,
                "conversion": 0.45,
            }
        elif (
            "retention" in objective_lower
            or "watch" in objective_lower
        ):
            weights = {
                "retention": 0.50,
                "engagement": 0.25,
                "intent": 0.15,
                "conversion": 0.10,
            }
        else:
            weights = {
                "retention": 0.35,
                "engagement": 0.25,
                "intent": 0.20,
                "conversion": 0.20,
            }
        score = sum(
            components[key] * weights[key]
            for key in components
        )
        return score, components, exposure

    def _historical_baseline(
        self,
        campaign_id: str,
        exclude_experiment_id: str,
    ) -> float | None:
        rows = self._conn.execute(
            """
            SELECT ev.scores_json
            FROM evaluations ev
            JOIN experiments ex
              ON ex.experiment_id=ev.experiment_id
            WHERE ex.campaign_id=?
              AND ex.experiment_id<>?
              AND ev.state='WINNER'
            """,
            (campaign_id, exclude_experiment_id),
        ).fetchall()
        winners: list[float] = []
        for row in rows:
            scores = _load(row["scores_json"], {})
            if not scores:
                continue
            winners.append(
                max(
                    _safe_float(item.get("score"))
                    for item in scores.values()
                    if isinstance(item, dict)
                )
            )
        if not winners:
            return None
        winners.sort()
        return winners[len(winners) // 2]

    def evaluate(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.get_experiment(experiment_id)
        variants = self.list_variants(experiment_id)
        snapshots = self._latest_metrics(experiment_id)
        reasons: list[str] = []

        if len(variants) < 2:
            raise ValueError(
                "experiment requires at least two variants"
            )

        if any(
            variant["variant_id"] not in snapshots
            for variant in variants
        ):
            reasons.append(
                "missing analytics for one or more variants"
            )
            return self._save_evaluation(
                experiment_id,
                "NEEDS_MORE_DATA",
                None,
                0.0,
                {},
                {},
                reasons,
                "ITERATE",
            )

        scores: dict[str, Any] = {}
        uncertainties: dict[str, float] = {}
        minimum_sample = int(experiment["minimum_sample"])

        for variant in variants:
            variant_id = variant["variant_id"]
            score, components, exposure = self._performance(
                snapshots[variant_id]["metrics"],
                experiment["objective"],
            )
            account_size = snapshots[variant_id].get(
                "account_size"
            )
            account_scale = max(
                1.0,
                math.log10(max(int(account_size or 1), 10)),
            )
            uncertainty = min(
                0.5,
                (0.5 / math.sqrt(max(exposure, 1) / 100.0))
                * min(1.25, account_scale / 2),
            )
            scores[variant_id] = {
                "label": variant["label"],
                "score": round(score, 6),
                "components": {
                    key: round(value, 6)
                    for key, value in components.items()
                },
                "sample_size": exposure,
                "age_hours": snapshots[variant_id]["age_hours"],
                "account_size": account_size,
                "variant_state": "INCONCLUSIVE",
            }
            uncertainties[variant_id] = round(
                uncertainty,
                6,
            )

        smallest_sample = min(
            item["sample_size"] for item in scores.values()
        )
        completeness = sum(
            1
            for item in scores.values()
            if any(
                value > 0
                for value in item["components"].values()
            )
        ) / len(scores)
        confidence = min(
            0.99,
            (
                1
                - math.exp(
                    -smallest_sample
                    / max(minimum_sample, 200)
                )
            )
            * completeness,
        )
        ordered = sorted(
            scores,
            key=lambda variant_id: scores[variant_id]["score"],
            reverse=True,
        )
        top, second = ordered[0], ordered[1]
        gap = (
            scores[top]["score"]
            - scores[second]["score"]
        )
        combined_uncertainty = (
            uncertainties[top]
            + uncertainties[second]
        )

        if smallest_sample < minimum_sample:
            state = "NEEDS_MORE_DATA"
            winner = None
            recommendation = "ITERATE"
            reasons.append(
                "minimum sample not reached: "
                f"{smallest_sample} < {minimum_sample}"
            )
        elif gap <= combined_uncertainty:
            state = "INCONCLUSIVE"
            winner = None
            recommendation = "ITERATE"
            reasons.append(
                "score gap is inside conservative uncertainty"
            )
        else:
            state = "WINNER"
            winner = top
            scores[top]["variant_state"] = "WINNER"
            for variant_id in ordered[1:]:
                scores[variant_id]["variant_state"] = "LOSER"
            reasons.append(
                "top normalized score exceeds runner-up "
                "beyond conservative uncertainty"
            )
            baseline = self._historical_baseline(
                experiment["campaign_id"],
                experiment_id,
            )
            top_score = scores[top]["score"]
            if baseline is not None:
                if (
                    confidence >= 0.70
                    and top_score < baseline * 0.65
                ):
                    recommendation = "KILL"
                    reasons.append(
                        "winner is materially below the campaign "
                        "historical baseline despite adequate confidence"
                    )
                elif (
                    confidence >= 0.70
                    and top_score > baseline * 1.10
                ):
                    recommendation = "SCALE"
                    reasons.append(
                        "winner materially exceeds the campaign "
                        "historical baseline with adequate confidence"
                    )
                else:
                    recommendation = "ITERATE"
            elif confidence >= 0.75:
                recommendation = "SCALE"
                reasons.append(
                    "no historical baseline exists; scale is based "
                    "on strong sample confidence, not a fixed threshold alone"
                )
            else:
                recommendation = "ITERATE"

        return self._save_evaluation(
            experiment_id,
            state,
            winner,
            confidence,
            scores,
            uncertainties,
            reasons,
            recommendation,
        )

    def _save_evaluation(
        self,
        experiment_id: str,
        state: str,
        winner: str | None,
        confidence: float,
        scores: dict[str, Any],
        uncertainty: dict[str, Any],
        reasons: list[str],
        recommendation: str,
    ) -> dict[str, Any]:
        if state not in EVALUATION_STATES:
            raise ValueError("invalid evaluation state")
        evaluation_id = _id("eval")
        evaluated_at = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    evaluation_id,
                    experiment_id,
                    state,
                    winner,
                    float(confidence),
                    _dump(scores),
                    _dump(uncertainty),
                    _dump(reasons),
                    recommendation,
                    evaluated_at,
                ),
            )
        return {
            "evaluation_id": evaluation_id,
            "experiment_id": experiment_id,
            "state": state,
            "winner_variant_id": winner,
            "confidence": round(float(confidence), 4),
            "scores": scores,
            "uncertainty": uncertainty,
            "reasons": reasons,
            "recommendation": recommendation,
            "evaluated_at": evaluated_at,
        }

    def learn(
        self,
        experiment_id: str,
        evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        experiment = self.get_experiment(experiment_id)
        state = str(evaluation.get("state") or "")
        if state in {"INCONCLUSIVE", "NEEDS_MORE_DATA"}:
            worked: list[str] = []
            failed: list[str] = []
            uncertain = [
                "The tested variable remains unresolved."
            ]
            next_tests = [
                "Collect more balanced exposure before changing "
                "another major variable."
            ]
        else:
            winner = evaluation.get("winner_variant_id")
            worked = (
                [
                    f"Variant {winner} led on the normalized "
                    "campaign objective score."
                ]
                if winner
                else []
            )
            failed = [
                "Non-winning variants underperformed the winner "
                "on this experiment objective."
            ]
            uncertain = [
                "The result supports this isolated comparison but "
                "does not prove universal causation."
            ]
            next_tests = [
                f"Refine {experiment['variable']} while holding "
                "other major variables constant."
            ]

        learning_id = _id("learn")
        changed = [
            f"Evidence updated for variable: {experiment['variable']}"
        ]
        created_at = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO learnings VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    learning_id,
                    experiment["campaign_id"],
                    experiment_id,
                    _dump(worked),
                    _dump(failed),
                    _dump(changed),
                    _dump(uncertain),
                    _dump(next_tests),
                    created_at,
                ),
            )
        return {
            "learning_id": learning_id,
            "campaign_id": experiment["campaign_id"],
            "experiment_id": experiment_id,
            "what_worked": worked,
            "what_failed": failed,
            "what_changed": changed,
            "what_remains_uncertain": uncertain,
            "what_to_test_next": next_tests,
            "created_at": created_at,
        }

    def latest_learning(
        self,
        campaign_id: str,
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM learnings
            WHERE campaign_id=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (campaign_id,),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["what_worked"] = _load(
            data.pop("worked_json"),
            [],
        )
        data["what_failed"] = _load(
            data.pop("failed_json"),
            [],
        )
        data["what_changed"] = _load(
            data.pop("changed_json"),
            [],
        )
        data["what_remains_uncertain"] = _load(
            data.pop("uncertain_json"),
            [],
        )
        data["what_to_test_next"] = _load(
            data.pop("next_tests_json"),
            [],
        )
        return data

    def report(self, campaign_id: str) -> dict[str, Any]:
        campaign = self.get_campaign(campaign_id)
        findings = self.list_findings(campaign_id)
        experiments = self.list_experiments(campaign_id)

        evaluation_rows = self._conn.execute(
            """
            SELECT ev.*
            FROM evaluations ev
            JOIN experiments ex
              ON ex.experiment_id=ev.experiment_id
            WHERE ex.campaign_id=?
            ORDER BY ev.evaluated_at
            """,
            (campaign_id,),
        ).fetchall()
        evaluations: list[dict[str, Any]] = []
        for row in evaluation_rows:
            item = dict(row)
            item["scores"] = _load(
                item.pop("scores_json"),
                {},
            )
            item["uncertainty"] = _load(
                item.pop("uncertainty_json"),
                {},
            )
            item["reasons"] = _load(
                item.pop("reasons_json"),
                [],
            )
            evaluations.append(item)

        learning_rows = self._conn.execute(
            """
            SELECT * FROM learnings
            WHERE campaign_id=?
            ORDER BY created_at
            """,
            (campaign_id,),
        ).fetchall()
        learnings: list[dict[str, Any]] = []
        for row in learning_rows:
            item = dict(row)
            item["what_worked"] = _load(
                item.pop("worked_json"),
                [],
            )
            item["what_failed"] = _load(
                item.pop("failed_json"),
                [],
            )
            item["what_changed"] = _load(
                item.pop("changed_json"),
                [],
            )
            item["what_remains_uncertain"] = _load(
                item.pop("uncertain_json"),
                [],
            )
            item["what_to_test_next"] = _load(
                item.pop("next_tests_json"),
                [],
            )
            learnings.append(item)

        cost_row = self._conn.execute(
            """
            SELECT COALESCE(
              SUM(COALESCE(actual_cost, estimated_cost)),
              0
            ) AS total
            FROM cost_records
            WHERE campaign_id=?
            """,
            (campaign_id,),
        ).fetchone()

        publication_count = int(
            self._conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM publications p
                JOIN experiments ex
                  ON ex.experiment_id=p.experiment_id
                WHERE ex.campaign_id=?
                """,
                (campaign_id,),
            ).fetchone()["n"]
        )

        return {
            "schema": "ace-report/v1",
            "campaign": campaign,
            "research_findings": findings,
            "content_patterns": self.derive_content_patterns(
                campaign_id
            ),
            "experiments": experiments,
            "evaluations": evaluations,
            "learnings": learnings,
            "publishing_packages": publication_count,
            "cost_usd": round(
                float(cost_row["total"] or 0),
                4,
            ),
        }


def structured_script(
    hook: str,
    setup: str,
    value: str,
    proof: str,
    cta: str,
    pattern_interrupt: str = "Visual reset at 2-3 seconds",
) -> dict[str, str]:
    return {
        "hook": hook,
        "setup": setup,
        "value": value,
        "pattern_interrupt": pattern_interrupt,
        "proof_or_demonstration": proof,
        "cta": cta,
    }


def hashtag_terms(goal: str) -> list[str]:
    return [f"#{word}" for word in _slug_words(goal, 5)]
