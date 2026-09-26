"""Auto-refreshing HTTP authentication client for Ahmed Toolbox."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests


class AhmedToolboxAuthError(RuntimeError):
    pass


class AutoRefreshingBearer:
    """Acquire and renew short-lived access tokens from a durable refresh secret.

    The access token cache is disposable. A process restart simply reloads the
    refresh secret from the deployment secret store and obtains a new access
    token, so no server/client RAM session is required for continuity.
    """

    def __init__(
        self,
        base_url: str,
        refresh_token: str,
        *,
        requested_ttl_seconds: int = 300,
        refresh_skew_seconds: float = 5.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.refresh_token = refresh_token.strip()
        self.requested_ttl_seconds = max(1, int(requested_ttl_seconds))
        self.refresh_skew_seconds = max(0.0, float(refresh_skew_seconds))
        self._session = session or requests.Session()
        self._lock = threading.RLock()
        self._access_token = ""
        self._expires_at = 0.0
        self.refresh_count = 0

    def invalidate(self) -> None:
        with self._lock:
            self._access_token = ""
            self._expires_at = 0.0

    def _refresh_locked(self, *, timeout: float) -> None:
        if not self.refresh_token:
            raise AhmedToolboxAuthError("refresh token is not configured")
        response = self._session.post(
            self.base_url + "/auth/token",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.refresh_token}",
            },
            json={"ttl_seconds": self.requested_ttl_seconds},
            timeout=timeout,
        )
        if response.status_code != 200:
            raise AhmedToolboxAuthError(
                f"access token refresh failed with HTTP {response.status_code}"
            )
        try:
            payload = response.json()
            access_token = str(payload["access_token"])
            expires_in = max(1, int(payload["expires_in"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise AhmedToolboxAuthError("invalid token refresh response") from exc
        self._access_token = access_token
        self._expires_at = time.time() + expires_in
        self.refresh_count += 1

    def access_token(self, *, timeout: float = 15.0) -> str:
        with self._lock:
            remaining = self._expires_at - time.time()
            effective_skew = min(
                self.refresh_skew_seconds,
                max(0.0, remaining / 4.0),
            )
            if not self._access_token or remaining <= effective_skew:
                self._refresh_locked(timeout=timeout)
            return self._access_token

    def _replace_session_after_connection_failure(self) -> None:
        with self._lock:
            self._session.close()
            self._session = requests.Session()

    def request(
        self,
        method: str,
        path: str,
        *,
        timeout: float = 30.0,
        retry_auth_once: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self.access_token(timeout=timeout)}"
        try:
            response = self._session.request(
                method,
                self.base_url + path,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
        except requests.ConnectionError:
            # A service restart can invalidate a pooled TCP connection. Replace
            # the transport session and retry once; auth state is independently
            # recoverable from the persistent refresh credential.
            self._replace_session_after_connection_failure()
            headers["Authorization"] = f"Bearer {self.access_token(timeout=timeout)}"
            response = self._session.request(
                method,
                self.base_url + path,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )

        if response.status_code == 401 and retry_auth_once:
            self.invalidate()
            headers["Authorization"] = f"Bearer {self.access_token(timeout=timeout)}"
            response = self._session.request(
                method,
                self.base_url + path,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
        return response

    def close(self) -> None:
        self._session.close()
