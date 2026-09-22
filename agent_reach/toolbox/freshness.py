"""Metric-aware freshness evaluation for Ahmed Research Engine.

Freshness is evaluated from the underlying data cutoff whenever possible.
Publication date is metadata, not a substitute for data recency.

The engine deliberately avoids false-precision scores. It returns categorical
status plus the measured age interval and policy inputs.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timezone
from typing import Any, NamedTuple

FRESHNESS_STATUSES = {
    "FRESH",
    "STALE",
    "UNCERTAIN",
    "AGE_REPORTED",
    "UNKNOWN",
}

POLICY_MODES = {
    "MAX_AGE",
    "LATEST_RELEASE",
    "AGE_ONLY",
}

DEFAULT_FRESHNESS_POLICIES: dict[str, dict[str, Any]] = {
    "market_price": {
        "mode": "MAX_AGE",
        "max_age_seconds": 60 * 60,
        "require_data_cutoff": True,
    },
    "breaking_news": {
        "mode": "MAX_AGE",
        "max_age_seconds": 6 * 60 * 60,
        "require_data_cutoff": True,
    },
    "technology_state": {
        "mode": "MAX_AGE",
        "max_age_seconds": 90 * 24 * 60 * 60,
        "require_data_cutoff": True,
    },
    "macro_indicator": {
        "mode": "LATEST_RELEASE",
        "require_data_cutoff": True,
    },
    "company_guidance": {
        "mode": "LATEST_RELEASE",
        "require_data_cutoff": True,
    },
    "structural_data": {
        "mode": "LATEST_RELEASE",
        "require_data_cutoff": True,
    },
    "academic_evidence": {
        "mode": "AGE_ONLY",
        "require_data_cutoff": False,
    },
}


class TemporalInterval(NamedTuple):
    raw: str
    precision: str
    start: datetime
    end: datetime


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_temporal_interval(value: str | None) -> TemporalInterval | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    if len(raw) == 4 and raw.isdigit():
        year = int(raw)
        return TemporalInterval(
            raw=raw,
            precision="YEAR",
            start=datetime(year, 1, 1, tzinfo=timezone.utc),
            end=datetime(year, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc),
        )

    if len(raw) == 7 and raw[4] == "-":
        try:
            year = int(raw[:4])
            month = int(raw[5:7])
            last_day = calendar.monthrange(year, month)[1]
        except (ValueError, calendar.IllegalMonthError):
            return None
        return TemporalInterval(
            raw=raw,
            precision="MONTH",
            start=datetime(year, month, 1, tzinfo=timezone.utc),
            end=datetime(
                year,
                month,
                last_day,
                23,
                59,
                59,
                999999,
                tzinfo=timezone.utc,
            ),
        )

    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        try:
            parsed_date = date.fromisoformat(raw)
        except ValueError:
            return None
        return TemporalInterval(
            raw=raw,
            precision="DAY",
            start=datetime.combine(parsed_date, time.min, tzinfo=timezone.utc),
            end=datetime.combine(parsed_date, time.max, tzinfo=timezone.utc),
        )

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = _as_utc(datetime.fromisoformat(normalized))
    except ValueError:
        return None
    return TemporalInterval(
        raw=raw,
        precision="DATETIME",
        start=parsed,
        end=parsed,
    )


def _resolve_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    interval = parse_temporal_interval(value)
    if interval is None:
        raise ValueError("as_of must be an ISO date or datetime")
    # For a date/month/year cutoff, use its interval end so the caller does
    # not accidentally pretend midnight precision.
    return interval.end


def resolve_policy(
    policy_name: str,
    *,
    max_age_seconds: int | float | None = None,
) -> dict[str, Any]:
    name = str(policy_name or "").strip().lower()
    if not name:
        raise ValueError("policy_name is required")

    if name == "custom_max_age":
        if max_age_seconds is None or float(max_age_seconds) <= 0:
            raise ValueError("custom_max_age requires max_age_seconds > 0")
        return {
            "name": name,
            "mode": "MAX_AGE",
            "max_age_seconds": float(max_age_seconds),
            "require_data_cutoff": True,
        }

    policy = DEFAULT_FRESHNESS_POLICIES.get(name)
    if policy is None:
        raise ValueError(
            "unknown freshness policy; use one of: "
            + ", ".join(sorted(DEFAULT_FRESHNESS_POLICIES | {"custom_max_age": {}}))
        )
    resolved = {"name": name, **policy}
    if max_age_seconds is not None:
        if resolved["mode"] != "MAX_AGE":
            raise ValueError("max_age_seconds override is valid only for MAX_AGE policies")
        if float(max_age_seconds) <= 0:
            raise ValueError("max_age_seconds must be > 0")
        resolved["max_age_seconds"] = float(max_age_seconds)
    return resolved


def evaluate_freshness(
    *,
    data_cutoff: str | None,
    publication_date: str | None,
    retrieved_at: str | None,
    policy_name: str,
    as_of: str | None = None,
    max_age_seconds: int | float | None = None,
    latest_known_cutoff: str | None = None,
) -> dict[str, Any]:
    policy = resolve_policy(policy_name, max_age_seconds=max_age_seconds)
    as_of_dt = _resolve_as_of(as_of)

    data_interval = parse_temporal_interval(data_cutoff)
    publication_interval = parse_temporal_interval(publication_date)
    retrieval_interval = parse_temporal_interval(retrieved_at)

    if data_interval is None:
        return {
            "policy_name": policy["name"],
            "policy_mode": policy["mode"],
            "status": "UNKNOWN",
            "reason_code": (
                "MISSING_DATA_CUTOFF"
                if not data_cutoff
                else "UNPARSEABLE_DATA_CUTOFF"
            ),
            "as_of": as_of_dt.isoformat(),
            "basis_field": "data_cutoff",
            "basis_value": data_cutoff,
            "basis_precision": None,
            "age_min_seconds": None,
            "age_max_seconds": None,
            "max_age_seconds": policy.get("max_age_seconds"),
            "latest_known_cutoff": latest_known_cutoff,
            "publication_date": publication_date,
            "retrieved_at": retrieved_at,
        }

    if data_interval.start > as_of_dt:
        return {
            "policy_name": policy["name"],
            "policy_mode": policy["mode"],
            "status": "UNKNOWN",
            "reason_code": "FUTURE_DATA_CUTOFF",
            "as_of": as_of_dt.isoformat(),
            "basis_field": "data_cutoff",
            "basis_value": data_cutoff,
            "basis_precision": data_interval.precision,
            "age_min_seconds": None,
            "age_max_seconds": None,
            "max_age_seconds": policy.get("max_age_seconds"),
            "latest_known_cutoff": latest_known_cutoff,
            "publication_date": publication_date,
            "retrieved_at": retrieved_at,
        }

    age_min = max(0.0, (as_of_dt - data_interval.end).total_seconds())
    age_max = max(0.0, (as_of_dt - data_interval.start).total_seconds())

    if policy["mode"] == "AGE_ONLY":
        status = "AGE_REPORTED"
        reason = "RECENCY_REPORTED_NOT_DOMINANT"
    elif policy["mode"] == "MAX_AGE":
        threshold = float(policy["max_age_seconds"])
        if age_max <= threshold:
            status = "FRESH"
            reason = "WITHIN_MAX_AGE"
        elif age_min > threshold:
            status = "STALE"
            reason = "EXCEEDS_MAX_AGE"
        else:
            status = "UNCERTAIN"
            reason = "DATE_PRECISION_CROSSES_FRESHNESS_BOUNDARY"
    elif policy["mode"] == "LATEST_RELEASE":
        latest = parse_temporal_interval(latest_known_cutoff)
        if latest is None:
            status = "UNKNOWN"
            reason = (
                "LATEST_KNOWN_CUTOFF_REQUIRED"
                if not latest_known_cutoff
                else "UNPARSEABLE_LATEST_KNOWN_CUTOFF"
            )
        elif latest.start > as_of_dt:
            status = "UNKNOWN"
            reason = "FUTURE_LATEST_KNOWN_CUTOFF"
        elif data_interval.end < latest.start:
            status = "STALE"
            reason = "OLDER_THAN_LATEST_KNOWN_RELEASE"
        elif data_interval.start > latest.end:
            status = "FRESH"
            reason = "NEWER_THAN_DECLARED_LATEST_RELEASE"
        elif (
            data_interval.raw == latest.raw
            and data_interval.precision == latest.precision
        ):
            status = "FRESH"
            reason = "MATCHES_LATEST_KNOWN_RELEASE"
        else:
            status = "UNCERTAIN"
            reason = "DATE_PRECISION_OVERLAPS_LATEST_RELEASE"
    else:  # pragma: no cover - guarded by policy resolver
        raise ValueError(f"unsupported policy mode: {policy['mode']}")

    return {
        "policy_name": policy["name"],
        "policy_mode": policy["mode"],
        "status": status,
        "reason_code": reason,
        "as_of": as_of_dt.isoformat(),
        "basis_field": "data_cutoff",
        "basis_value": data_cutoff,
        "basis_precision": data_interval.precision,
        "age_min_seconds": round(age_min, 6),
        "age_max_seconds": round(age_max, 6),
        "max_age_seconds": policy.get("max_age_seconds"),
        "latest_known_cutoff": latest_known_cutoff,
        "publication_date": (
            publication_interval.raw if publication_interval else publication_date
        ),
        "retrieved_at": (
            retrieval_interval.raw if retrieval_interval else retrieved_at
        ),
    }
