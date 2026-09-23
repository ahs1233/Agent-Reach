"""Read-only Browser Use integration for interactive YouTube evidence.

The adapter intentionally exposes a narrow deterministic surface instead of an
open-ended browser agent. yt-dlp remains the primary YouTube ingestion path;
Browser Use is reserved for UI-only evidence such as rendered descriptions and
comments.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

_ALLOWED_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
)
_RESULT_MARKER = "__AHMED_BROWSER_USE_JSON__"
_DEFAULT_TIMEOUT_SECONDS = 45
_MAX_TIMEOUT_SECONDS = 120
_MAX_COMMENTS = 50


class BrowserUseUnavailable(RuntimeError):
    """Raised when the Browser Use CLI is not installed or runnable."""


class BrowserUseError(RuntimeError):
    """Raised when an interactive browser inspection fails."""


@dataclass(frozen=True)
class BrowserUseStatus:
    available: bool
    executable: str | None
    detail: str


@dataclass(frozen=True)
class YouTubeBrowserEvidence:
    source_url: str
    resolved_url: str
    title: str
    description: str
    visible_text: str
    comments: tuple[str, ...]
    retrieval_tool: str = "browser-use"
    mode: str = "read_only"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["comments"] = list(self.comments)
        payload["provenance"] = {
            "retrieval_tool": self.retrieval_tool,
            "interaction_mode": self.mode,
            "source_kind": "rendered_youtube_page",
        }
        payload["limitations"] = [
            "rendered page text may differ by locale, account session, or A/B test",
            "comments are a bounded visible sample, not the full comment corpus",
            "page statements are source evidence, not independently verified facts",
        ]
        return payload


def probe_browser_use() -> BrowserUseStatus:
    """Return non-secret Browser Use CLI availability information."""
    executable = shutil.which("browser-use")
    if not executable:
        return BrowserUseStatus(
            False,
            None,
            "browser-use CLI not found; install the optional interactive-browser extra on Python 3.11+",
        )
    return BrowserUseStatus(True, executable, "browser-use CLI available")


def _validate_youtube_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _ALLOWED_YOUTUBE_HOSTS:
        raise ValueError("browser inspection is restricted to public YouTube URLs")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")
    return value


def _bounded_int(value: int, *, minimum: int, maximum: int, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return max(minimum, min(parsed, maximum))


def _build_script(url: str, *, include_comments: bool, max_comments: int) -> str:
    safe_url = json.dumps(url, ensure_ascii=False)
    comment_block = "comments = []"
    if include_comments:
        comment_block = f"""
scroll(0, 7000)
import time
time.sleep(2)
comments = js("Array.from(document.querySelectorAll('ytd-comment-thread-renderer #content-text')).slice(0, {max_comments}).map(e => e.innerText || '')") or []
""".strip()

    return f"""
import json
new_tab({safe_url})
wait_for_load()
title = js("document.title || ''") or ''
resolved_url = js("location.href || ''") or {safe_url}
description = js("(document.querySelector('#description-inline-expander') || document.querySelector('#description'))?.innerText || ''") or ''
visible_text = js("document.body ? document.body.innerText.slice(0, 30000) : ''") or ''
{comment_block}
payload = {{
    'resolved_url': resolved_url,
    'title': title,
    'description': description,
    'visible_text': visible_text,
    'comments': comments,
}}
print({_RESULT_MARKER!r} + json.dumps(payload, ensure_ascii=False))
""".strip() + "\n"


def inspect_youtube_page(
    url: str,
    *,
    include_comments: bool = False,
    max_comments: int = 20,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Inspect rendered YouTube UI through Browser Use without mutating account state."""
    source_url = _validate_youtube_url(url)
    max_comments = _bounded_int(
        max_comments,
        minimum=0,
        maximum=_MAX_COMMENTS,
        name="max_comments",
    )
    timeout_seconds = _bounded_int(
        timeout_seconds,
        minimum=5,
        maximum=_MAX_TIMEOUT_SECONDS,
        name="timeout_seconds",
    )

    status = probe_browser_use()
    if not status.available or not status.executable:
        raise BrowserUseUnavailable(status.detail)

    script = _build_script(
        source_url,
        include_comments=bool(include_comments and max_comments),
        max_comments=max_comments,
    )
    try:
        completed = subprocess.run(
            [status.executable],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrowserUseError(
            f"browser-use execution failed: {type(exc).__name__}: {exc}"
        ) from exc

    stdout = completed.stdout or ""
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        detail = stderr or stdout.strip() or f"exit code {completed.returncode}"
        raise BrowserUseError(f"browser-use returned an error: {detail[:2000]}")

    marker_line = next(
        (line for line in reversed(stdout.splitlines()) if line.startswith(_RESULT_MARKER)),
        None,
    )
    if marker_line is None:
        raise BrowserUseError(
            "browser-use completed without structured Ahmed Toolbox output"
        )

    try:
        payload = json.loads(marker_line[len(_RESULT_MARKER) :])
    except json.JSONDecodeError as exc:
        raise BrowserUseError(
            "browser-use returned malformed structured output"
        ) from exc
    if not isinstance(payload, dict):
        raise BrowserUseError("browser-use structured output must be an object")

    raw_comments = payload.get("comments") or []
    comments = tuple(
        str(item).strip() for item in raw_comments if str(item).strip()
    )[:max_comments]
    evidence = YouTubeBrowserEvidence(
        source_url=source_url,
        resolved_url=str(payload.get("resolved_url") or source_url).strip(),
        title=str(payload.get("title") or "").strip(),
        description=str(payload.get("description") or "").strip(),
        visible_text=str(payload.get("visible_text") or "")[:30000],
        comments=comments,
    )
    return evidence.to_dict()
