"""Stateless authentication lifecycle for Ahmed Toolbox HTTP access."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Mapping

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ACCESS_TOKEN_PREFIX = "at1"
_AUDIENCE = "ahmed-toolbox"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _extract_bearer(headers: Mapping[str, str]) -> str:
    header = str(headers.get("Authorization") or "")
    if not header.lower().startswith("bearer "):
        return ""
    return header[7:].strip()


@dataclass(frozen=True)
class AuthConfig:
    """Durable auth inputs.

    The long-lived refresh/bootstrap credentials and signing key come from the
    process environment (Railway Variables in production). Access tokens are
    stateless HMAC-signed values and never depend on an in-memory server session.
    """

    legacy_token: str = ""
    refresh_token: str = ""
    signing_secret: str = ""
    access_ttl_seconds: int = 300
    allow_legacy_token: bool = True

    @classmethod
    def from_environment(cls, *, legacy_token: str = "") -> "AuthConfig":
        resolved_legacy = legacy_token.strip() or os.environ.get(
            "AHMED_TOOLBOX_TOKEN", ""
        ).strip()
        resolved_refresh = (
            os.environ.get("AHMED_TOOLBOX_REFRESH_TOKEN", "").strip()
            or resolved_legacy
        )
        # Compatibility fallback keeps existing deployments bootable while the
        # explicit Railway signing secret is introduced. Production config uses
        # AHMED_TOOLBOX_AUTH_SECRET.
        resolved_signing = (
            os.environ.get("AHMED_TOOLBOX_AUTH_SECRET", "").strip()
            or resolved_refresh
        )
        allow_legacy = (
            os.environ.get("AHMED_TOOLBOX_ALLOW_LEGACY_TOKEN", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        )
        return cls(
            legacy_token=resolved_legacy,
            refresh_token=resolved_refresh,
            signing_secret=resolved_signing,
            access_ttl_seconds=_env_int("AHMED_TOOLBOX_ACCESS_TTL_SECONDS", 300),
            allow_legacy_token=allow_legacy,
        )

    def validate_for_host(self, host: str) -> None:
        if host.strip().lower() in _LOOPBACK_HOSTS:
            return
        if not (self.legacy_token or self.refresh_token):
            raise RuntimeError(
                "Ahmed Toolbox requires a persistent auth credential on a non-loopback host"
            )
        if self.refresh_token and not self.signing_secret:
            raise RuntimeError(
                "AHMED_TOOLBOX_AUTH_SECRET is required when refresh auth is enabled"
            )


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    principal: str
    status_code: int = 200
    reason: str = ""


class AuthMiddleware:
    """Single HTTP auth policy boundary.

    Every HTTP request is evaluated here. Health remains intentionally public.
    MCP requests accept a short-lived signed access token; the legacy static
    token can remain temporarily enabled during client migration. /auth/token
    accepts only the durable refresh credential and returns a stateless access
    token that survives server restarts because validation depends only on the
    persistent signing secret.
    """

    def __init__(self, config: AuthConfig):
        self.config = config

    @staticmethod
    def normalize_path(path: str) -> str:
        normalized = (path.split("?", 1)[0] or "/").rstrip("/")
        return normalized or "/"

    def authorize(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
    ) -> AuthDecision:
        normalized = self.normalize_path(path)
        method = method.upper()

        if normalized == "/health":
            return AuthDecision(True, "public-health")

        bearer = _extract_bearer(headers)

        if normalized == "/auth/token":
            if method != "POST":
                return AuthDecision(False, "refresh", 401, "refresh credential required")
            if self.validate_refresh_token(bearer):
                return AuthDecision(True, "refresh")
            return AuthDecision(False, "refresh", 401, "invalid refresh credential")

        if normalized in {"/", "/mcp"}:
            if self.validate_access_token(bearer):
                return AuthDecision(True, "mcp")
            return AuthDecision(False, "mcp", 401, "invalid or expired access credential")

        # Unknown routes still pass through the middleware before route handling.
        return AuthDecision(True, "unrouted")

    def validate_refresh_token(self, token: str) -> bool:
        expected = self.config.refresh_token
        return bool(expected and token and hmac.compare_digest(token, expected))

    def issue_access_token(
        self,
        *,
        requested_ttl_seconds: int | None = None,
        now: int | None = None,
    ) -> dict[str, object]:
        if not self.config.refresh_token or not self.config.signing_secret:
            raise RuntimeError("refresh authentication is not configured")
        current = int(time.time() if now is None else now)
        requested = (
            self.config.access_ttl_seconds
            if requested_ttl_seconds is None
            else max(1, int(requested_ttl_seconds))
        )
        ttl = min(requested, self.config.access_ttl_seconds)
        payload = {
            "aud": _AUDIENCE,
            "exp": current + ttl,
            "iat": current,
            "v": 1,
        }
        payload_segment = _b64url_encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signing_input = f"{_ACCESS_TOKEN_PREFIX}.{payload_segment}".encode("ascii")
        signature = hmac.new(
            self.config.signing_secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        token = f"{_ACCESS_TOKEN_PREFIX}.{payload_segment}.{_b64url_encode(signature)}"
        return {
            "schema": "ahmed-toolbox-auth/v1",
            "token_type": "Bearer",
            "access_token": token,
            "expires_in": ttl,
            "expires_at": current + ttl,
        }

    def validate_access_token(self, token: str, *, now: int | None = None) -> bool:
        if not token:
            return False

        if (
            self.config.allow_legacy_token
            and self.config.legacy_token
            and hmac.compare_digest(token, self.config.legacy_token)
        ):
            return True

        try:
            prefix, payload_segment, signature_segment = token.split(".", 2)
        except ValueError:
            return False
        if prefix != _ACCESS_TOKEN_PREFIX or not self.config.signing_secret:
            return False

        signing_input = f"{prefix}.{payload_segment}".encode("ascii")
        expected_signature = hmac.new(
            self.config.signing_secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        try:
            supplied_signature = _b64url_decode(signature_segment)
        except Exception:
            return False
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False

        try:
            payload = json.loads(_b64url_decode(payload_segment))
            audience = payload["aud"]
            issued_at = int(payload["iat"])
            expires_at = int(payload["exp"])
            version = int(payload["v"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

        current = int(time.time() if now is None else now)
        if audience != _AUDIENCE or version != 1:
            return False
        if issued_at > current + 30:
            return False
        return expires_at > current