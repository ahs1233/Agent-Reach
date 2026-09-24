"""Synchronous HTTP client for the Ahmed ToolBox MCP gateway.

Schema discovery is intentionally synchronous because PanWatch constructs its
ToolRegistry in a synchronous service factory. Actual tool execution is wrapped
with asyncio.to_thread by the registry adapter so it never blocks the runtime
event loop.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import httpx

_MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class AhmedToolboxError(RuntimeError):
    pass


class AhmedToolboxClient:
    def __init__(
        self,
        url: str,
        *,
        token: str = "",
        refresh_token: str = "",
        access_ttl_seconds: int = 300,
        timeout_seconds: float = 5.0,
        protocol_version: str = "2024-11-05",
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.refresh_token = refresh_token
        self.access_ttl_seconds = max(1, int(access_ttl_seconds))
        self.timeout_seconds = timeout_seconds
        self.protocol_version = protocol_version
        self._lock = threading.RLock()
        self._initialized = False
        self._next_id = 1
        self._access_token = ""
        self._access_expires_at = 0.0
        self._client = httpx.Client(timeout=timeout_seconds)

    def _id(self) -> int:
        with self._lock:
            value = self._next_id
            self._next_id += 1
            return value

    @classmethod
    def from_environment(cls, url: str | None = None) -> "AhmedToolboxClient":
        resolved_url = (
            url
            or os.environ.get("AHMED_TOOLBOX_URL", "").strip()
            or os.environ.get("AHMED_TOOLBOX_MCP_URL", "").strip()
        )
        if not resolved_url:
            raise AhmedToolboxError("AHMED_TOOLBOX_URL is required")
        return cls(
            resolved_url,
            token=os.environ.get("AHMED_TOOLBOX_TOKEN", "").strip(),
            refresh_token=os.environ.get("AHMED_TOOLBOX_REFRESH_TOKEN", "").strip(),
            access_ttl_seconds=int(
                os.environ.get("AHMED_TOOLBOX_ACCESS_TTL_SECONDS", "300")
            ),
        )

    def _base_url(self) -> str:
        return self.url[:-4] if self.url.endswith("/mcp") else self.url

    def _refresh_access_token_locked(self) -> None:
        if not self.refresh_token:
            raise AhmedToolboxError("Ahmed ToolBox refresh token is not configured")
        response = self._client.post(
            self._base_url() + "/auth/token",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.refresh_token}",
            },
            json={"ttl_seconds": self.access_ttl_seconds},
        )
        if response.status_code != 200:
            raise AhmedToolboxError(
                f"Ahmed ToolBox token refresh failed: HTTP {response.status_code}"
            )
        try:
            payload = response.json()
            self._access_token = str(payload["access_token"])
            expires_in = max(1, int(payload["expires_in"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AhmedToolboxError("Ahmed ToolBox token refresh payload is invalid") from exc
        self._access_expires_at = time.time() + expires_in

    def _authorization_token(self) -> str:
        if not self.refresh_token:
            return self.token
        with self._lock:
            if (
                not self._access_token
                or self._access_expires_at - time.time() <= 5.0
            ):
                self._refresh_access_token_locked()
            return self._access_token

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        token = self._authorization_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        try:
            response = self._client.post(
                self.url,
                headers=self._headers(),
                json=payload,
            )
            if response.status_code == 401 and self.refresh_token:
                with self._lock:
                    self._access_token = ""
                    self._access_expires_at = 0.0
                response = self._client.post(
                    self.url,
                    headers=self._headers(),
                    json=payload,
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AhmedToolboxError(f"Ahmed ToolBox request failed: {exc}") from exc

        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise AhmedToolboxError("Ahmed ToolBox response exceeded size limit")
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise AhmedToolboxError("Ahmed ToolBox returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise AhmedToolboxError("Ahmed ToolBox returned a non-object response")
        if body.get("error"):
            raise AhmedToolboxError(f"Ahmed ToolBox MCP error: {body['error']}")
        return body

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._rpc(
                "initialize",
                {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "PanWatch", "version": "external-tools-0.1"},
                },
            )
            self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self.ensure_initialized()
        result = (self._rpc("tools/list").get("result") or {})
        tools = result.get("tools") or []
        if not isinstance(tools, list):
            raise AhmedToolboxError("Ahmed ToolBox tools/list payload is malformed")
        return [item for item in tools if isinstance(item, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.ensure_initialized()
        result = (
            self._rpc(
                "tools/call",
                {"name": name, "arguments": arguments},
            ).get("result")
            or {}
        )
        if not isinstance(result, dict):
            raise AhmedToolboxError("Ahmed ToolBox tools/call payload is malformed")
        return result
