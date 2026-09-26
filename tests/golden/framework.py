from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable


@dataclass(frozen=True)
class GoldenResult:
    id: str
    category: str
    status: str
    actual_result: str
    duration_ms: float
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_cases() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("cases.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "ahmed-golden-suite/v1"
    assert payload["count"] == 80
    return list(payload["cases"])


def run_case(
    case: dict[str, Any],
    execute: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]],
) -> GoldenResult:
    started = perf_counter()
    try:
        actual, evidence = execute(case)
        status = "PASS"
    except Exception as exc:  # the report must preserve the real failure
        actual = f"{type(exc).__name__}: {exc}"
        evidence = {"error_type": type(exc).__name__, "error_message": str(exc)}
        status = "FAIL"
    return GoldenResult(
        id=str(case["id"]),
        category=str(case["category"]),
        status=status,
        actual_result=actual,
        duration_ms=round((perf_counter() - started) * 1000.0, 3),
        evidence=evidence,
    )
