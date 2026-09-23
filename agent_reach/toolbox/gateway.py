"""MCP gateway that composes Agent Reach with remote MCP servers.

The gateway keeps credentials and transport configuration in the host
environment. Remote tools are namespaced (for example ``scrapling__fetch``)
so that two MCP servers may expose the same tool name without collisions.

Remote MCP tools are deny-by-default unless an allowlist is configured. A small
read-only default allowlist is provided for Scrapling one-shot GET/browser
fetchers. Stateful sessions and arbitrary HTTP methods are intentionally not
exposed by default.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

import requests

from agent_reach import AgentReach
from agent_reach.channels.web import WebChannel

from .orchestration import OrchestrationStore
from .orchestration_mcp import handle_orchestration_tool, orchestration_tool_specs
from .research import ResearchStore
from .research_mcp import handle_research_tool, research_tool_specs
from .retrieval import retrieve_with_fallback
from .video import ingest_media

_MAX_REMOTE_RESPONSE_BYTES = 5 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PREFIX_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}

_DEFAULT_REMOTE_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    # Read-only one-shot tools. `make_request` is excluded because its schema
    # also permits POST/PUT/DELETE. Session tools are excluded because they
    # mutate remote browser/request-session state.
    "scrapling": (
        "bulk_get",
        "fetch",
        "bulk_fetch",
        "stealthy_fetch",
        "bulk_stealthy_fetch",
    ),
}


class RemoteMCPError(RuntimeError):
    """Raised when a configured remote MCP server cannot satisfy a request."""


@dataclass(frozen=True)
class RemoteMCPConfig:
    name: str
    url: str
    token: str = ""
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    protocol_version: str = "2024-11-05"
    allow_tools: tuple[str, ...] = ()

    @property
    def prefix(self) -> str:
        value = _PREFIX_RE.sub("_", self.name.strip()).strip("_").lower()
        if not value:
            raise ValueError("remote MCP name must contain at least one safe character")
        return value

    def allows(self, tool_name: str) -> bool:
        return tool_name in set(self.allow_tools)


class RemoteMCPClient:
    """Minimal HTTP MCP client with lazy initialize/session handling."""

    def __init__(self, config: RemoteMCPConfig, session: requests.Session | None = None):
        self.config = config
        self._session = session or requests.Session()
        self._session_id: str | None = None
        self._negotiated_protocol: str | None = None
        self._initialized = False
        self._lock = threading.RLock()
        self._next_id = 1

    def is_tool_allowed(self, tool_name: str) -> bool:
        return self.config.allows(tool_name)

    def _request_id(self) -> int:
        with self._lock:
            value = self._next_id
            self._next_id += 1
            return value

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._negotiated_protocol:
            headers["MCP-Protocol-Version"] = self._negotiated_protocol
        return headers

    @staticmethod
    def _decode_payload(content_type: str, body: bytes, request_id: int) -> dict[str, Any]:
        text = body.decode("utf-8", errors="replace")
        if "text/event-stream" not in content_type.lower():
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise RemoteMCPError("remote MCP returned a non-object JSON payload")
            return payload

        fallback: dict[str, Any] | None = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if fallback is None:
                fallback = payload
            if payload.get("id") == request_id:
                return payload
        if fallback is not None:
            return fallback
        raise RemoteMCPError("remote MCP returned SSE without a JSON-RPC payload")

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._request_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        try:
            response = self._session.post(
                self.config.url,
                headers=self._headers(),
                json=payload,
                timeout=self.config.timeout_seconds,
                stream=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RemoteMCPError(
                f"{self.config.name} MCP request failed: {exc}"
            ) from exc

        session_id = response.headers.get("Mcp-Session-Id") or response.headers.get(
            "mcp-session-id"
        )
        if session_id:
            self._session_id = session_id

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_REMOTE_RESPONSE_BYTES:
                raise RemoteMCPError(
                    f"{self.config.name} MCP response exceeded "
                    f"{_MAX_REMOTE_RESPONSE_BYTES} bytes"
                )
            chunks.append(chunk)

        try:
            decoded = self._decode_payload(
                response.headers.get("Content-Type", ""),
                b"".join(chunks),
                request_id,
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RemoteMCPError(
                f"{self.config.name} MCP returned invalid JSON"
            ) from exc

        if "error" in decoded:
            raise RemoteMCPError(
                f"{self.config.name} MCP error: {decoded.get('error')}"
            )
        return decoded

    def _notify_initialized(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        try:
            response = self._session.post(
                self.config.url,
                headers=self._headers(),
                json=payload,
                timeout=self.config.timeout_seconds,
                stream=False,
            )
            if response.status_code >= 400:
                return
            session_id = response.headers.get("Mcp-Session-Id") or response.headers.get(
                "mcp-session-id"
            )
            if session_id:
                self._session_id = session_id
        except requests.RequestException:
            return

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            initialized = False
            try:
                init_response = self.rpc(
                    "initialize",
                    {
                        "protocolVersion": self.config.protocol_version,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "ahmed-toolbox",
                            "version": "0.1.0",
                        },
                    },
                )
                negotiated = (
                    (init_response.get("result") or {}).get("protocolVersion")
                    if isinstance(init_response, dict)
                    else None
                )
                self._negotiated_protocol = str(
                    negotiated or self.config.protocol_version
                )
                initialized = True
            except RemoteMCPError:
                # Some small MCP servers allow tools/list directly and do not
                # implement initialize. The actual tools/list call remains
                # authoritative, so this compatibility fallback is acceptable.
                pass
            if initialized:
                self._notify_initialized()
            self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self.ensure_initialized()
        payload = self.rpc("tools/list")
        result = payload.get("result") or {}
        tools = result.get("tools") or []
        if not isinstance(tools, list):
            raise RemoteMCPError(f"{self.config.name} MCP tools/list is malformed")
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.is_tool_allowed(name):
            raise RemoteMCPError(
                f"{self.config.name} MCP tool is not allowlisted: {name}"
            )
        self.ensure_initialized()
        payload = self.rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            raise RemoteMCPError(f"{self.config.name} MCP tools/call is malformed")
        return result


class AhmedToolboxGateway:
    """Compose local Agent Reach tools and configured remote MCP tools."""

    def __init__(
        self,
        remotes: dict[str, RemoteMCPClient] | None = None,
        *,
        agent_reach: AgentReach | None = None,
        research_store: ResearchStore | None = None,
        research_enabled: bool | None = None,
        orchestration_store: OrchestrationStore | None = None,
        orchestration_enabled: bool | None = None,
    ):
        self.agent_reach = agent_reach or AgentReach()
        self.remotes = remotes or {}
        self.orchestration_enabled = (
            _env_flag("AHMED_ORCHESTRATION_ENABLED", False)
            if orchestration_enabled is None
            else bool(orchestration_enabled)
        )
        self.research_enabled = (
            _env_flag("AHMED_RESEARCH_ENABLED", False)
            if research_enabled is None
            else bool(research_enabled)
        ) or self.orchestration_enabled
        self.research_store = (
            research_store or ResearchStore.from_environment()
            if self.research_enabled
            else None
        )
        self.orchestration_store = (
            orchestration_store or OrchestrationStore.from_environment()
            if self.orchestration_enabled
            else None
        )

    @classmethod
    def from_environment(cls) -> "AhmedToolboxGateway":
        raw = os.environ.get("AHMED_TOOLBOX_REMOTE_MCPS", "").strip()
        if not raw:
            return cls()
        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("AHMED_TOOLBOX_REMOTE_MCPS must be valid JSON") from exc
        if not isinstance(config, dict):
            raise ValueError("AHMED_TOOLBOX_REMOTE_MCPS must be a JSON object")

        remotes: dict[str, RemoteMCPClient] = {}
        for name, item in config.items():
            if not isinstance(item, dict) or not item.get("url"):
                raise ValueError(f"remote MCP {name!r} requires a url")
            token = str(item.get("token") or "")
            token_env = str(item.get("token_env") or "")
            if token_env:
                token = os.environ.get(token_env, token)

            prefix = _PREFIX_RE.sub("_", str(name).strip()).strip("_").lower()
            raw_allow_tools = item.get("allow_tools")
            if raw_allow_tools is None:
                allow_tools = _DEFAULT_REMOTE_ALLOWLISTS.get(prefix, ())
            elif isinstance(raw_allow_tools, list) and all(
                isinstance(tool, str) for tool in raw_allow_tools
            ):
                allow_tools = tuple(raw_allow_tools)
            else:
                raise ValueError(
                    f"remote MCP {name!r} allow_tools must be a list of tool names"
                )

            remote_config = RemoteMCPConfig(
                name=str(name),
                url=str(item["url"]),
                token=token,
                timeout_seconds=float(item.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS),
                protocol_version=str(item.get("protocol_version") or "2024-11-05"),
                allow_tools=allow_tools,
            )
            prefix = remote_config.prefix
            if prefix in remotes:
                raise ValueError(f"duplicate remote MCP prefix: {prefix}")
            remotes[prefix] = RemoteMCPClient(remote_config)
        return cls(remotes)

    @staticmethod
    def _local_tool_specs() -> list[dict[str, Any]]:
        return [
            {
                "name": "reach_doctor",
                "description": (
                    "Inspect Agent Reach channel health and configured backends. "
                    "Use this before relying on optional social/search channels."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "reach_web_search",
                "description": (
                    "Search the public web through Agent Reach's documented Exa "
                    "route (mcporter -> exa.web_search_exa). Read-only discovery tool."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "num_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "reach_media_ingest",
                "description": "Ingest public video/audio into timestamped transcript evidence with provenance.",
                "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}, "provider": {"type": "string", "enum": ["auto", "groq", "openai"], "default": "auto"}, "language": {"type": "string"}}, "additionalProperties": False},
            },
            {
                "name": "reach_retrieve_url",
                "description": (
                    "Retrieve a public page through the controlled fallback state machine: "
                    "Jina -> Scrapling fetch -> Scrapling stealthy -> optional configured "
                    "browser backend. Returns content plus every attempt/reason."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 200000,
                            "default": 100000,
                        },
                        "min_chars": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200000,
                            "default": 200,
                        },
                        "required_terms": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": "string", "maxLength": 500},
                        },
                        "require_all_terms": {
                            "type": "boolean",
                            "default": True,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "reach_read_url",
                "description": (
                    "Read a public HTTP(S) page through Agent Reach's Jina Reader "
                    "path and return cleaned Markdown. This is a read-only tool."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string"},
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 100000,
                            "default": 20000,
                        },
                    },
                    "additionalProperties": False,
                },
            },
        ]

    def list_tools(self) -> list[dict[str, Any]]:
        tools = list(self._local_tool_specs())
        if self.research_enabled:
            tools.extend(research_tool_specs())
        if self.orchestration_enabled:
            tools.extend(orchestration_tool_specs())
        used_names = {tool["name"] for tool in tools}

        for prefix, remote in sorted(self.remotes.items()):
            try:
                remote_tools = remote.list_tools()
            except RemoteMCPError:
                # One unavailable research backend must not remove Agent Reach
                # or other healthy MCP servers from the gateway's tool surface.
                continue
            for tool in remote_tools:
                original = str(tool.get("name") or "").strip()
                if not original or not remote.is_tool_allowed(original):
                    continue
                public_name = f"{prefix}__{original}"
                if public_name in used_names:
                    raise RemoteMCPError(f"duplicate gateway tool name: {public_name}")
                used_names.add(public_name)
                description = str(tool.get("description") or "").strip()
                tools.append(
                    {
                        "name": public_name,
                        "description": (
                            f"[{prefix} MCP] {description}" if description else f"[{prefix} MCP]"
                        ),
                        "inputSchema": tool.get("inputSchema")
                        or {"type": "object", "properties": {}},
                    }
                )
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        if self.orchestration_enabled and self.orchestration_store is not None:
            try:
                orchestration_result = handle_orchestration_tool(
                    self.orchestration_store,
                    name,
                    arguments,
                    research_store=self.research_store,
                    execute_tool=self._call_tool_unorchestrated,
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(f"Orchestration error: {exc}", is_error=True)
            if orchestration_result is not None:
                return self._text_result(
                    json.dumps(orchestration_result, ensure_ascii=False, default=str)
                )
        return self._call_tool_unorchestrated(name, arguments)

    def _call_tool_unorchestrated(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        arguments = arguments or {}

        if self.research_enabled and self.research_store is not None:
            try:
                research_result = handle_research_tool(
                    self.research_store,
                    name,
                    arguments,
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(
                    f"Research evidence error: {exc}",
                    is_error=True,
                )
            if research_result is not None:
                return self._text_result(
                    json.dumps(research_result, ensure_ascii=False, default=str)
                )

        if name == "reach_doctor":
            data = self.agent_reach.doctor()
            return self._text_result(json.dumps(data, ensure_ascii=False, default=str))

        if name == "reach_web_search":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return self._text_result("query is required", is_error=True)
            if len(query) > 1000:
                return self._text_result("query exceeds 1000 characters", is_error=True)
            try:
                num_results = int(arguments.get("num_results") or 5)
            except (TypeError, ValueError):
                return self._text_result("num_results must be an integer", is_error=True)
            num_results = max(1, min(num_results, 10))

            # Primary route: the Agent Reach documented mcporter -> Exa path.
            # Pin the config explicitly so Railway process cwd/config discovery
            # cannot make a healthy deployment behave differently.
            mcporter = shutil.which("mcporter")
            mcporter_error = ""
            if mcporter:
                env = dict(os.environ)
                env.setdefault(
                    "MCPORTER_CONFIG",
                    os.path.join(os.getcwd(), "config", "mcporter.json"),
                )
                try:
                    completed = subprocess.run(
                        [
                            mcporter,
                            "call",
                            "exa.web_search_exa",
                            f"query={query}",
                            f"numResults={num_results}",
                            "--output",
                            "text",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=45,
                        check=False,
                        env=env,
                    )
                    output = (completed.stdout or "").strip()
                    if completed.returncode == 0 and output:
                        return self._text_result(output[:100000])
                    mcporter_error = (
                        completed.stderr or output or "mcporter search failed"
                    ).strip()[:4000]
                except (OSError, subprocess.TimeoutExpired) as exc:
                    mcporter_error = f"{type(exc).__name__}: {exc}"
            else:
                mcporter_error = "mcporter executable not found"

            # Fail-soft fallback: call Exa's hosted MCP directly through the
            # same minimal read-only MCP client used for other remote servers.
            # This keeps public web search available when the CLI/config layer
            # fails, without widening the exposed tool surface.
            exa = RemoteMCPClient(
                RemoteMCPConfig(
                    name="exa",
                    url="https://mcp.exa.ai/mcp",
                    allow_tools=("web_search_exa",),
                    timeout_seconds=30.0,
                )
            )
            try:
                result = exa.call_tool(
                    "web_search_exa",
                    {"query": query, "numResults": num_results},
                )
                if isinstance(result, dict) and not bool(result.get("isError")):
                    return result
                remote_text = "\n".join(
                    str(item.get("text") or "")
                    for item in (result.get("content") or [])
                    if isinstance(item, dict)
                ).strip()
                fallback_error = remote_text or "direct Exa MCP returned an error"
            except Exception as exc:  # noqa: BLE001 - controlled research fallback
                fallback_error = f"{type(exc).__name__}: {exc}"

            diagnostic = (
                "Agent Reach web search unavailable after both routes; "
                f"mcporter={mcporter_error[:1200]!r}; "
                f"direct_exa={fallback_error[:1200]!r}"
            )
            return self._text_result(diagnostic, is_error=True)

        if name == "reach_media_ingest":
            url = str(arguments.get("url") or "").strip()
            if not url:
                return self._text_result("url is required", is_error=True)
            try:
                manifest = ingest_media(url, provider=str(arguments.get("provider") or "auto"), language=(str(arguments.get("language")).strip() if arguments.get("language") else None))
            except Exception as exc:  # noqa: BLE001
                return self._text_result(f"Media ingestion failed: {exc}", is_error=True)
            return self._text_result(json.dumps(manifest, ensure_ascii=False))

        if name == "reach_retrieve_url":
            url = str(arguments.get("url") or "").strip()
            if not url:
                return self._text_result("url is required", is_error=True)
            try:
                max_chars = int(arguments.get("max_chars") or 100000)
                min_chars = int(arguments.get("min_chars") or 200)
            except (TypeError, ValueError):
                return self._text_result(
                    "max_chars and min_chars must be integers",
                    is_error=True,
                )
            required_terms = arguments.get("required_terms") or []
            if not isinstance(required_terms, list):
                return self._text_result(
                    "required_terms must be an array",
                    is_error=True,
                )
            browser_tool = os.environ.get(
                "AHMED_TOOLBOX_BROWSER_FALLBACK_TOOL",
                "",
            ).strip()
            if browser_tool == "reach_retrieve_url":
                browser_tool = ""
            try:
                outcome = retrieve_with_fallback(
                    url,
                    call_tool=lambda tool_name, tool_args: self._call_tool_unorchestrated(
                        tool_name,
                        tool_args,
                    ),
                    max_chars=max_chars,
                    min_chars=min_chars,
                    required_terms=[str(item) for item in required_terms],
                    require_all_terms=bool(
                        arguments.get("require_all_terms", True)
                    ),
                    browser_tool=browser_tool or None,
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(
                    f"retrieval fallback error: {exc}",
                    is_error=True,
                )
            return self._text_result(
                json.dumps(outcome, ensure_ascii=False, default=str)
            )

        if name == "reach_read_url":
            url = str(arguments.get("url") or "").strip()
            if not url:
                return self._text_result("url is required", is_error=True)
            try:
                max_chars = int(arguments.get("max_chars") or 20000)
            except (TypeError, ValueError):
                return self._text_result("max_chars must be an integer", is_error=True)
            max_chars = max(1000, min(max_chars, 100000))
            try:
                text = WebChannel().read(url)
            except Exception as exc:  # noqa: BLE001 - map upstream failure into MCP result
                return self._text_result(f"Agent Reach read failed: {exc}", is_error=True)
            truncated = len(text) > max_chars
            text = text[:max_chars]
            if truncated:
                text += "\n\n[TRUNCATED BY AHMED TOOLBOX]"
            return self._text_result(text)

        if "__" not in name:
            return self._text_result(f"unknown tool: {name}", is_error=True)

        prefix, remote_name = name.split("__", 1)
        remote = self.remotes.get(prefix)
        if remote is None or not remote_name:
            return self._text_result(f"unknown tool: {name}", is_error=True)
        if not remote.is_tool_allowed(remote_name):
            return self._text_result(
                f"tool is not allowlisted: {name}",
                is_error=True,
            )
        try:
            return remote.call_tool(remote_name, arguments)
        except RemoteMCPError as exc:
            return self._text_result(str(exc), is_error=True)

    @staticmethod
    def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }
