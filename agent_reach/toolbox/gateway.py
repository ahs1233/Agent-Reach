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

import fnmatch
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

from agent_reach import AgentReach
from agent_reach.channels.web import WebChannel

from .ace import ACEStore
from .ace_mcp import ace_tool_specs, handle_ace_tool
from .browser_use import inspect_youtube_page, probe_browser_use, read_public_page
from .observability import ExecutionLogStore
from .observability import utc_now as observability_utc_now
from .orchestration import OrchestrationStore, classify_tool_effect
from .orchestration_mcp import handle_orchestration_tool, orchestration_tool_specs
from .research import ResearchStore
from .research_mcp import handle_research_tool, research_tool_specs
from .retrieval import retrieve_with_fallback
from .runtime import RuntimeStore
from .runtime_mcp import handle_runtime_tool, runtime_tool_specs
from .subagent import SubagentModelClient, SubagentModelError
from .temporal_mcp import handle_temporal_tool, temporal_tool_specs
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
_DEFAULT_REMOTE_EFFECTS: dict[str, dict[str, str]] = {
    "scrapling": {name: "SE0" for name in _DEFAULT_REMOTE_ALLOWLISTS["scrapling"]},
}
_REMOTE_TRUST_LEVELS = {"untrusted", "read_only", "full"}


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
    trust: str = "untrusted"
    effect_classes: dict[str, str] | None = None

    @property
    def prefix(self) -> str:
        value = _PREFIX_RE.sub("_", self.name.strip()).strip("_").lower()
        if not value:
            raise ValueError("remote MCP name must contain at least one safe character")
        return value

    def allows(self, tool_name: str) -> bool:
        return tool_name in set(self.allow_tools)

    def effect_class(self, tool_name: str) -> str | None:
        if self.effect_classes and tool_name in self.effect_classes:
            value = self.effect_classes[tool_name]
            return value if value in {"SE0", "SE1", "SE2", "SE3", "SE4"} else None
        return _DEFAULT_REMOTE_EFFECTS.get(self.prefix, {}).get(tool_name)

    def can_execute(self, tool_name: str) -> bool:
        if not self.allows(tool_name):
            return False
        effect = self.effect_class(tool_name)
        if self.trust == "full":
            return True
        return effect == "SE0"


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

    @staticmethod
    def _public_meta_tool_specs() -> list[dict[str, Any]]:
        return [
            {
                "name": "toolbox_catalog",
                "description": (
                    "Search the internal Ahmed Toolbox capability catalog without loading every "
                    "tool schema into the model context. Use this before toolbox_invoke when the "
                    "exact internal tool name or arguments are unknown. Request schemas only for "
                    "the small set of tools you intend to call."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "Optional name/description search text.",
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "all",
                                "runtime",
                                "research",
                                "orchestration",
                                "ace",
                                "reach",
                                "observability",
                                "remote",
                            ],
                            "default": "all",
                        },
                        "include_schema": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Include inputSchema only for returned matches. Prefer false for "
                                "discovery, then true with an exact/specific query before invoking."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 12,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "toolbox_invoke",
                "description": (
                    "Invoke one internal Ahmed Toolbox tool by exact name. Discover the tool with "
                    "toolbox_catalog first when needed, and pass arguments matching that tool's "
                    "inputSchema. This preserves the full internal capability set behind a compact "
                    "public MCP surface."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["tool_name"],
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                        "arguments": {
                            "type": "object",
                            "default": {},
                            "additionalProperties": True,
                        },
                    },
                    "additionalProperties": False,
                },
            },
        ]

    @staticmethod
    def _catalog_category(tool_name: str) -> str:
        if tool_name.startswith("runtime_"):
            return "runtime"
        if tool_name.startswith("orchestration_"):
            return "orchestration"
        if tool_name.startswith("ace_"):
            return "ace"
        if tool_name.startswith("research_"):
            return "research"
        if tool_name.startswith("reach_"):
            return "reach"
        if tool_name == "toolbox_execution_stats":
            return "observability"
        if "__" in tool_name:
            return "remote"
        return "other"

    def list_public_tools(self) -> list[dict[str, Any]]:
        """Return the externally advertised MCP surface.

        The full internal registry remains available to runtime/subagents through
        list_tools. Compact mode changes only what remote clients receive from
        tools/list; no internal capability is removed.
        """
        if not self.compact_surface_enabled:
            return self.list_tools()

        full = self.list_tools()
        health_names = {"runtime_status", "reach_doctor"}
        public = [tool for tool in full if tool.get("name") in health_names]
        public.extend(self._public_meta_tool_specs())
        return public

    def _catalog_result(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip().lower()
        category = str(arguments.get("category") or "all").strip().lower()
        include_schema = bool(arguments.get("include_schema", False))
        try:
            limit = int(arguments.get("limit") or 12)
        except (TypeError, ValueError):
            limit = 12
        limit = max(1, min(limit, 20))

        matches: list[dict[str, Any]] = []
        for spec in self.list_tools():
            name = str(spec.get("name") or "")
            if not name:
                continue
            tool_category = self._catalog_category(name)
            if category != "all" and tool_category != category:
                continue
            description = str(spec.get("description") or "").strip()
            haystack = f"{name} {description}".lower()
            if query and all(term not in haystack for term in query.split()):
                continue
            item: dict[str, Any] = {
                "name": name,
                "category": tool_category,
                "description": description[:280],
            }
            if include_schema:
                item["inputSchema"] = spec.get("inputSchema") or {
                    "type": "object",
                    "properties": {},
                }
            matches.append(item)
            if len(matches) >= limit:
                break

        return self._text_result(
            json.dumps(
                {
                    "schema": "ahmed-toolbox-catalog/v1",
                    "query": query,
                    "category": category,
                    "include_schema": include_schema,
                    "matches": matches,
                },
                ensure_ascii=False,
                default=str,
            )
        )

    def call_public_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        request_id: str | int | None = None,
    ) -> dict[str, Any]:
        """Dispatch public tools while keeping cached legacy calls valid."""
        arguments = arguments or {}
        if self.compact_surface_enabled and name == "toolbox_catalog":
            return self._catalog_result(arguments)
        if self.compact_surface_enabled and name == "toolbox_invoke":
            tool_name = str(arguments.get("tool_name") or "").strip()
            if not tool_name:
                return self._text_result("tool_name is required", is_error=True)
            if tool_name in {"toolbox_catalog", "toolbox_invoke"}:
                return self._text_result(
                    f"meta-tool recursion denied: {tool_name}", is_error=True
                )
            nested_arguments = arguments.get("arguments") or {}
            if not isinstance(nested_arguments, dict):
                return self._text_result("arguments must be an object", is_error=True)
            return self.call_tool(
                tool_name,
                nested_arguments,
                request_id=request_id,
            )

        # Backward compatibility for clients that cached the previous tool list.
        return self.call_tool(name, arguments, request_id=request_id)
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
        runtime_store: RuntimeStore | None = None,
        runtime_enabled: bool | None = None,
        ace_store: ACEStore | None = None,
        ace_enabled: bool | None = None,
        observability_store: ExecutionLogStore | None = None,
        observability_enabled: bool | None = None,
        compact_surface_enabled: bool | None = None,
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
        requested_runtime = (
            _env_flag("AHMED_RUNTIME_ENABLED", False)
            if runtime_enabled is None
            else bool(runtime_enabled)
        )
        self.runtime_enabled = requested_runtime or self.orchestration_enabled
        self.runtime_store = (
            runtime_store or RuntimeStore.from_environment()
            if self.runtime_enabled
            else None
        )
        self.subagent_client = (
            SubagentModelClient.from_environment() if self.runtime_enabled else None
        )
        requested_ace = (
            _env_flag("AHMED_ACE_ENABLED", self.runtime_enabled)
            if ace_enabled is None
            else bool(ace_enabled)
        )
        self.ace_enabled = requested_ace
        if self.ace_enabled and self.research_store is None:
            self.research_enabled = True
            self.research_store = research_store or ResearchStore.from_environment()
        self.ace_store = (
            ace_store or ACEStore.from_environment()
            if self.ace_enabled
            else None
        )
        self.observability_enabled = (
            bool(observability_store)
            if observability_enabled is None
            else bool(observability_enabled)
        )
        self.observability_store = (
            observability_store if self.observability_enabled else None
        )
        self.compact_surface_enabled = (
            _env_flag("AHMED_TOOLBOX_COMPACT_MCP_SURFACE", False)
            if compact_surface_enabled is None
            else bool(compact_surface_enabled)
        )

    @classmethod
    def from_environment(cls) -> "AhmedToolboxGateway":
        raw = os.environ.get("AHMED_TOOLBOX_REMOTE_MCPS", "").strip()
        observability_enabled = _env_flag("AHMED_OBSERVABILITY_ENABLED", True)
        observability_store = (
            ExecutionLogStore.from_environment() if observability_enabled else None
        )
        if not raw:
            return cls(
                observability_store=observability_store,
                observability_enabled=observability_enabled,
            )
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

            trust = str(item.get("trust") or ("read_only" if prefix in _DEFAULT_REMOTE_ALLOWLISTS else "untrusted")).strip().lower()
            if trust not in _REMOTE_TRUST_LEVELS:
                raise ValueError(
                    f"remote MCP {name!r} trust must be one of: untrusted, read_only, full"
                )
            raw_effects = item.get("effect_classes") or {}
            if not isinstance(raw_effects, dict):
                raise ValueError(f"remote MCP {name!r} effect_classes must be an object")
            effect_classes: dict[str, str] = {}
            for tool_name, effect in raw_effects.items():
                effect_value = str(effect)
                if effect_value not in {"SE0", "SE1", "SE2", "SE3", "SE4"}:
                    raise ValueError(
                        f"remote MCP {name!r} has invalid effect class for {tool_name!r}"
                    )
                effect_classes[str(tool_name)] = effect_value

            remote_config = RemoteMCPConfig(
                name=str(name),
                url=str(item["url"]),
                token=token,
                timeout_seconds=float(item.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS),
                protocol_version=str(item.get("protocol_version") or "2024-11-05"),
                allow_tools=allow_tools,
                trust=trust,
                effect_classes=effect_classes,
            )
            prefix = remote_config.prefix
            if prefix in remotes:
                raise ValueError(f"duplicate remote MCP prefix: {prefix}")
            remotes[prefix] = RemoteMCPClient(remote_config)
        return cls(
            remotes,
            observability_store=observability_store,
            observability_enabled=observability_enabled,
        )

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
                "name": "reach_browser_read_url",
                "description": (
                    "Read rendered text from a public HTTP(S) page through Browser Use. "
                    "Read-only and bounded; local/private network targets are rejected."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 100000,
                            "default": 50000,
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 5,
                            "maximum": 120,
                            "default": 45,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "reach_youtube_browser_inspect",
                "description": (
                    "Read rendered YouTube UI through Browser Use when yt-dlp is "
                    "insufficient. Read-only: title, description, visible text, "
                    "and an optional bounded comment sample."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "include_comments": {"type": "boolean", "default": False},
                        "max_comments": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 50,
                            "default": 20,
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 5,
                            "maximum": 120,
                            "default": 45,
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
                    "browser backend -> targeted web discovery. Alternative discovery is "
                    "kept distinct from the blocked original source to preserve provenance."
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
                        "discovery_query": {
                            "type": "string",
                            "maxLength": 1000,
                            "description": (
                                "Optional targeted-search query used only after the "
                                "original URL retrieval path is exhausted."
                            ),
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
        if self.observability_enabled and self.observability_store is not None:
            tools.append(
                {
                    "name": "toolbox_execution_stats",
                    "description": (
                        "Return secret-safe Ahmed Toolbox execution reliability "
                        "metrics for a bounded recent time window."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "hours": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 720,
                                "default": 24,
                            }
                        },
                        "additionalProperties": False,
                    },
                }
            )
        if self.research_enabled:
            tools.extend(research_tool_specs())
            tools.extend(temporal_tool_specs())
        if self.orchestration_enabled:
            tools.extend(orchestration_tool_specs())
        if self.runtime_enabled:
            tools.extend(runtime_tool_specs())
        if self.ace_enabled:
            tools.extend(ace_tool_specs())
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

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        request_id: str | int | None = None,
    ) -> dict[str, Any]:
        arguments = arguments or {}
        store = self.observability_store if self.observability_enabled else None
        identifiers = (
            store.identifiers(arguments, request_id=request_id)
            if store is not None
            else None
        )
        started_at = observability_utc_now()
        started = time.perf_counter()
        try:
            result = self._dispatch_tool(name, arguments)
        except Exception as exc:
            if store is not None and identifiers is not None:
                store.record(
                    **identifiers,
                    tool_name=name,
                    started_at=started_at,
                    completed_at=observability_utc_now(),
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=False,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            raise

        if store is not None and identifiers is not None:
            meta = store.result_metadata(result)
            success = not bool(result.get("isError"))
            error_message = None
            if not success:
                error_message = "\n".join(
                    str(block.get("text") or "")
                    for block in (result.get("content") or [])
                    if isinstance(block, dict) and block.get("type") == "text"
                ).strip()
            workflow_id = identifiers.get("workflow_id") or meta.pop("workflow_id")
            store.record(
                **{**identifiers, "workflow_id": workflow_id},
                tool_name=name,
                started_at=started_at,
                completed_at=observability_utc_now(),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                success=success,
                error_type=None if success else "ToolError",
                error_message=error_message,
                **meta,
            )
        return result

    def _dispatch_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if name == "toolbox_execution_stats":
            if not self.observability_enabled or self.observability_store is None:
                return self._text_result("observability is disabled", is_error=True)
            try:
                hours = int(arguments.get("hours") or 24)
            except (TypeError, ValueError):
                return self._text_result("hours must be an integer", is_error=True)
            return self._text_result(
                json.dumps(
                    self.observability_store.stats(hours=hours),
                    ensure_ascii=False,
                    default=str,
                )
            )

        if self.orchestration_enabled and self.orchestration_store is not None:
            try:
                orchestration_result = handle_orchestration_tool(
                    self.orchestration_store,
                    name,
                    arguments,
                    research_store=self.research_store,
                    execute_tool=self._call_tool_unorchestrated,
                    resolve_effect=self.resolve_tool_effect,
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(f"Orchestration error: {exc}", is_error=True)
            if orchestration_result is not None:
                return self._text_result(
                    json.dumps(orchestration_result, ensure_ascii=False, default=str)
                )

        if self.runtime_enabled and self.runtime_store is not None:
            try:
                runtime_result = handle_runtime_tool(
                    self.runtime_store,
                    name,
                    arguments,
                    execute_step=self._runtime_execute_step,
                    execute_agent=self._runtime_execute_agent,
                    agent_status=(
                        self.subagent_client.status()
                        if self.subagent_client is not None
                        else {"available": False}
                    ),
                    orchestration_store=self.orchestration_store,
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(f"Runtime error: {exc}", is_error=True)
            if runtime_result is not None:
                return self._text_result(
                    json.dumps(runtime_result, ensure_ascii=False, default=str)
                )
        return self._call_tool_unorchestrated(name, arguments)

    def resolve_tool_effect(self, name: str) -> str | None:
        """Return the effect class used by orchestration/runtime trust gates."""
        local = classify_tool_effect(name)
        if local is not None:
            return local
        if "__" not in name:
            return None
        prefix, remote_name = name.split("__", 1)
        remote = self.remotes.get(prefix)
        if remote is None or not remote_name:
            return None
        return remote.config.effect_class(remote_name)

    def _runtime_execute_agent(
        self,
        task: dict[str, Any],
        orchestration_id: str,
    ) -> dict[str, Any]:
        if self.subagent_client is None:
            return {
                "status": "error",
                "summary": "model-backed subagents are disabled",
                "reason": "runtime_disabled",
            }
        if not self.subagent_client.config.available:
            return {
                "status": "error",
                "summary": "model-backed subagents are not configured",
                "reason": "model_not_configured",
            }
        if self.orchestration_store is None:
            return {
                "status": "error",
                "summary": "orchestration store is required for model-backed subagents",
                "reason": "orchestration_disabled",
            }

        run = self.orchestration_store.get_run(orchestration_id)
        role = str(task.get("role") or "adversarial_reviewer")
        role_patterns = run["specialists"].get(role) or []
        if not role_patterns:
            return {
                "status": "error",
                "summary": f"unknown subagent role: {role}",
                "reason": "unknown_role",
            }
        requested_patterns = [
            str(item)
            for item in (task.get("tool_allowlist") or ["*"])
            if str(item).strip()
        ]
        if not requested_patterns:
            requested_patterns = ["*"]

        available_specs: list[dict[str, Any]] = []
        for spec in self.list_tools():
            tool_name = str(spec.get("name") or "")
            if not tool_name or tool_name.startswith(("runtime_", "orchestration_")):
                continue
            if not any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in role_patterns):
                continue
            if not any(
                fnmatch.fnmatchcase(tool_name, pattern)
                for pattern in requested_patterns
            ):
                continue
            available_specs.append(spec)

        if not available_specs:
            return {
                "status": "error",
                "summary": "subagent has no tools after role/allowlist filtering",
                "reason": "empty_toolset",
            }

        try:
            return self.subagent_client.run(
                goal=str(task.get("goal") or task.get("objective") or ""),
                role=role,
                tools=available_specs,
                execute_tool=lambda name, arguments, tool_role: self._runtime_execute_step(
                    name, arguments, tool_role, orchestration_id
                ),
                context=str(task.get("context") or ""),
                max_turns=(
                    int(task["max_turns"]) if task.get("max_turns") is not None else None
                ),
                timeout_seconds=(
                    float(task["timeout_seconds"])
                    if task.get("timeout_seconds") is not None
                    else min(float(self.subagent_client.config.timeout_seconds), 120.0)
                ),
            )
        except (SubagentModelError, TypeError, ValueError) as exc:
            return {
                "schema": "ahmed-runtime-model-subagent/v1",
                "status": "error",
                "summary": f"{type(exc).__name__}: {exc}",
                "reason": "subagent_failure",
            }

    def _runtime_execute_step(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        role: str,
        orchestration_id: str | None,
    ) -> dict[str, Any]:
        if tool_name.startswith("runtime_") or tool_name.startswith("orchestration_"):
            return self._text_result(
                f"runtime recursion denied: {tool_name}", is_error=True
            )
        if orchestration_id:
            if not self.orchestration_enabled or self.orchestration_store is None:
                return self._text_result(
                    "orchestration_id supplied but orchestration is disabled",
                    is_error=True,
                )
            try:
                executed = handle_orchestration_tool(
                    self.orchestration_store,
                    "orchestration_execute",
                    {
                        "orchestration_id": orchestration_id,
                        "role": role,
                        "tool_name": tool_name,
                        "arguments": arguments,
                    },
                    research_store=self.research_store,
                    execute_tool=self._call_tool_unorchestrated,
                    resolve_effect=self.resolve_tool_effect,
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(
                    f"orchestrated runtime step failed: {exc}", is_error=True
                )
            if not executed or not executed.get("executed"):
                return self._text_result(
                    json.dumps(executed or {"executed": False}, ensure_ascii=False, default=str),
                    is_error=True,
                )
            return executed["result"]

        effect = self.resolve_tool_effect(tool_name)
        if effect != "SE0":
            return self._text_result(
                f"runtime step {tool_name!r} requires orchestration_id "
                f"(effect={effect or 'unknown'})",
                is_error=True,
            )
        return self._call_tool_unorchestrated(tool_name, arguments)

    def _call_tool_unorchestrated(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        arguments = arguments or {}

        if self.ace_enabled and self.ace_store is not None:
            try:
                ace_result = handle_ace_tool(
                    self.ace_store,
                    name,
                    arguments,
                    research_store=self.research_store,
                    execute_tool=lambda tool_name, tool_arguments: self._call_tool_unorchestrated(
                        tool_name, tool_arguments
                    ),
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(
                    f"ACE error: {exc}",
                    is_error=True,
                )
            if ace_result is not None:
                return self._text_result(
                    json.dumps(ace_result, ensure_ascii=False, default=str)
                )

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
            try:
                temporal_result = handle_temporal_tool(
                    self.research_store,
                    name,
                    arguments,
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(
                    f"Research temporal error: {exc}",
                    is_error=True,
                )
            if temporal_result is not None:
                return self._text_result(
                    json.dumps(temporal_result, ensure_ascii=False, default=str)
                )

        if name == "reach_doctor":
            data = self.agent_reach.doctor()
            browser = probe_browser_use()
            data["browser_use"] = {
                "status": "ok" if browser.available else "off",
                "name": "Interactive Browser",
                "message": browser.detail,
                "tier": 1,
                "backends": ["browser-use"],
                "active_backend": "browser-use" if browser.available else None,
            }
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

        if name == "reach_browser_read_url":
            url = str(arguments.get("url") or "").strip()
            if not url:
                return self._text_result("url is required", is_error=True)
            try:
                evidence = read_public_page(
                    url,
                    max_chars=int(arguments.get("max_chars") or 50000),
                    timeout_seconds=int(arguments.get("timeout_seconds") or 45),
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(
                    f"Browser read input error: {exc}",
                    is_error=True,
                )
            except Exception as exc:  # noqa: BLE001 - optional browser boundary
                return self._text_result(
                    f"Browser read failed: {exc}",
                    is_error=True,
                )
            return self._text_result(
                json.dumps(evidence, ensure_ascii=False, default=str)
            )

        if name == "reach_youtube_browser_inspect":
            url = str(arguments.get("url") or "").strip()
            if not url:
                return self._text_result("url is required", is_error=True)
            try:
                evidence = inspect_youtube_page(
                    url,
                    include_comments=bool(arguments.get("include_comments", False)),
                    max_comments=int(arguments.get("max_comments") or 20),
                    timeout_seconds=int(arguments.get("timeout_seconds") or 45),
                )
            except (TypeError, ValueError) as exc:
                return self._text_result(
                    f"YouTube browser inspection input error: {exc}",
                    is_error=True,
                )
            except Exception as exc:  # noqa: BLE001 - map optional browser failure into MCP result
                return self._text_result(
                    f"YouTube browser inspection failed: {exc}",
                    is_error=True,
                )
            return self._text_result(
                json.dumps(evidence, ensure_ascii=False, default=str)
            )

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
                "reach_browser_read_url",
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
                    discovery_tool="reach_web_search",
                    discovery_query=(
                        str(arguments.get("discovery_query") or "").strip() or None
                    ),
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
        remote_config = getattr(remote, "config", None)
        if remote_config is not None and not remote_config.can_execute(remote_name):
            effect = remote_config.effect_class(remote_name)
            return self._text_result(
                f"remote trust gate denied {name}: trust={remote_config.trust}, "
                f"effect={effect or 'unknown'}; explicitly classify the tool and use trust=full "
                "only for operator-approved write-capable servers",
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
