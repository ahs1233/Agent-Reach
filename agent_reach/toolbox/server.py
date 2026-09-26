"""Small MCP-compatible HTTP server for Ahmed ToolBox."""

from __future__ import annotations

import json
import os
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlencode, urlsplit

from .auth import AuthConfig, AuthMiddleware
from .gateway import AhmedToolboxGateway, RemoteMCPError
from .market_mcp import auth_token as market_auth_token
from .market_mcp import call_tool as call_market_tool
from .market_mcp import enabled as market_enabled
from .market_mcp import tool_specs as market_tool_specs
from .oauth import AhmedOAuthProvider, OAuthConfig, OAuthProtocolError, parse_form_body

_MAX_REQUEST_BYTES = 1024 * 1024
_SERVER_VERSION = "0.1.2"
_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
_OAUTH_LOGIN_COOKIE = "ahmed_oauth_session"


def _validate_auth_configuration(host: str, token: str) -> None:
    """Compatibility wrapper for the production fail-closed startup gate."""
    try:
        AuthConfig.from_environment(legacy_token=token).validate_for_host(host)
    except RuntimeError as exc:
        raise RuntimeError(
            "AHMED_TOOLBOX_TOKEN is required when AHMED_TOOLBOX_HOST is non-loopback"
        ) from exc


def _initialize_result(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    return {
        "protocolVersion": requested if requested in _PROTOCOL_VERSIONS else _PROTOCOL_VERSIONS[-1],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "Ahmed ToolBox", "version": _SERVER_VERSION},
    }


def _rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _market_initialize_result(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    return {
        "protocolVersion": (
            requested if requested in _PROTOCOL_VERSIONS else _PROTOCOL_VERSIONS[-1]
        ),
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "AHS Market Data MCP", "version": "0.1.0"},
    }


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class ToolboxRequestHandler(BaseHTTPRequestHandler):
    gateway: AhmedToolboxGateway
    auth_middleware: AuthMiddleware | None = None
    oauth_provider: AhmedOAuthProvider | None = None
    auth_token: str = ""

    server_version = f"AhmedToolbox/{_SERVER_VERSION}"

    def log_message(self, fmt: str, *args: object) -> None:
        if os.environ.get("AHMED_TOOLBOX_HTTP_LOG", "").lower() in {"1", "true", "yes"}:
            super().log_message(fmt, *args)

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, body_text: str) -> None:
        body = body_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'",
        )
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, *, set_cookie: str = "") -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _oauth_error(self, exc: OAuthProtocolError) -> None:
        self._send_json(
            exc.status_code,
            {"error": exc.error, "error_description": exc.description},
        )

    def _normalized_path(self) -> str:
        return AuthMiddleware.normalize_path(self.path)

    def _bearer_token(self) -> str:
        header = str(self.headers.get("Authorization") or "")
        if not header.lower().startswith("bearer "):
            return ""
        return header[7:].strip()

    def _oauth_challenge(self) -> dict[str, str]:
        provider = self.oauth_provider
        if provider is None:
            return {}
        return {
            "WWW-Authenticate": (
                f'Bearer resource_metadata="{provider.resource_metadata_url}", '
                'scope="toolbox offline_access"'
            )
        }

    def _authorize_market_request(self) -> bool:
        if not market_enabled():
            self._send_json(404, {"status": "market_mcp_disabled"})
            return False
        configured = market_auth_token()
        if not configured or self._bearer_token() == configured:
            return True
        self._send_json(
            401,
            {
                "error": "invalid_token",
                "error_description": "Authorization required for AHS Market Data MCP",
            },
        )
        return False

    def _authorize_request(self) -> bool:
        path = self._normalized_path()
        if path == "/market-mcp":
            return self._authorize_market_request()
        if path in {
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-authorization-server",
            "/.well-known/openid-configuration",
            "/oauth/authorize",
            "/oauth/token",
            "/oauth/register",
        }:
            return True

        provider = self.oauth_provider
        if (
            path in {"/", "/mcp"}
            and self.command == "POST"
            and provider is not None
            and provider.validate_access_token(self._bearer_token())
        ):
            return True

        middleware = self.auth_middleware
        if middleware is None:
            middleware = AuthMiddleware(
                AuthConfig.from_environment(legacy_token=self.auth_token)
            )
        decision = middleware.authorize(self.command, self.path, self.headers)
        if decision.allowed:
            return True

        if path in {"/", "/mcp"}:
            self._send_json(
                401,
                {
                    "error": "invalid_token",
                    "error_description": "Authorization required for Ahmed Toolbox",
                },
                headers=self._oauth_challenge(),
            )
        else:
            self._send_json(
                decision.status_code,
                _rpc_error(None, -32001, "unauthorized"),
            )
        return False

    def _read_body(self, *, allow_empty: bool = False) -> bytes | None:
        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, _rpc_error(None, -32700, "invalid content length"))
            return None
        if content_length == 0 and allow_empty:
            return b""
        if content_length <= 0 or content_length > _MAX_REQUEST_BYTES:
            self._send_json(413, _rpc_error(None, -32700, "request body too large"))
            return None
        return self.rfile.read(content_length)

    def _read_json_body(self, *, allow_empty: bool = False) -> dict[str, Any] | None:
        body = self._read_body(allow_empty=allow_empty)
        if body is None:
            return None
        if not body and allow_empty:
            return {}
        try:
            payload = json.loads(body)
        except Exception:
            self._send_json(400, _rpc_error(None, -32700, "invalid JSON"))
            return None
        if not isinstance(payload, dict):
            self._send_json(400, _rpc_error(None, -32600, "request must be an object"))
            return None
        return payload

    def _read_form_body(self) -> dict[str, str] | None:
        body = self._read_body()
        if body is None:
            return None
        try:
            return parse_form_body(body)
        except OAuthProtocolError as exc:
            self._oauth_error(exc)
            return None

    def _login_cookie(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
        except Exception:
            return ""
        morsel = cookie.get(_OAUTH_LOGIN_COOKIE)
        return morsel.value if morsel is not None else ""

    def _login_cookie_header(self, token: str) -> str:
        provider = self.oauth_provider
        max_age = provider.config.login_cookie_ttl_seconds if provider else 0
        secure = "; Secure" if provider and provider.issuer.startswith("https://") else ""
        return (
            f"{_OAUTH_LOGIN_COOKIE}={token}; Path=/oauth/authorize; Max-Age={max_age}; "
            f"HttpOnly; SameSite=Lax{secure}"
        )

    def _oauth_params_from_form(self, form: dict[str, str]) -> dict[str, Any]:
        provider = self.oauth_provider
        if provider is None:
            raise OAuthProtocolError("server_error", "OAuth is not configured", 503)
        allowed = {
            key: value
            for key, value in form.items()
            if key
            in {
                "response_type",
                "client_id",
                "redirect_uri",
                "state",
                "code_challenge",
                "code_challenge_method",
                "resource",
                "scope",
            }
        }
        return provider.parse_authorization_params(urlencode(allowed))

    def _serve_oauth_metadata(self, path: str) -> bool:
        provider = self.oauth_provider
        if provider is None:
            return False
        cors = {"Access-Control-Allow-Origin": "*"}
        if path in {
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        }:
            self._send_json(200, provider.protected_resource_metadata(), headers=cors)
            return True
        if path in {
            "/.well-known/oauth-authorization-server",
            "/.well-known/openid-configuration",
        }:
            self._send_json(200, provider.authorization_server_metadata(), headers=cors)
            return True
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802
        path = self._normalized_path()
        self.send_response(204)
        if path.startswith("/.well-known/"):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        else:
            self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorize_request():
            return
        path = self._normalized_path()
        if self._serve_oauth_metadata(path):
            return
        if path == "/health":
            self._send_json(200, {"status": "ok", "service": "ahmed-toolbox"})
            return
        if path == "/oauth/authorize":
            provider = self.oauth_provider
            if provider is None:
                self._send_json(404, {"status": "oauth_not_configured"})
                return
            try:
                params = provider.parse_authorization_params(urlsplit(self.path).query)
            except OAuthProtocolError as exc:
                self._oauth_error(exc)
                return
            if provider.login_cookie_valid(self._login_cookie()):
                code = provider.issue_authorization_code(params)
                self._redirect(provider.authorization_redirect(params, code))
                return
            self._send_html(200, provider.authorization_form(params))
            return
        if path in {"", "/", "/mcp", "/market-mcp"}:
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self._send_json(404, {"status": "not_found"})

    def _handle_market_mcp(self) -> None:
        protocol = self.headers.get("MCP-Protocol-Version")
        if protocol is not None and protocol not in _PROTOCOL_VERSIONS:
            self._send_json(
                400,
                _rpc_error(None, -32600, "unsupported MCP protocol version"),
            )
            return

        payload = self._read_json_body()
        if payload is None:
            return
        method = payload.get("method")
        req_id = payload.get("id")
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            self._send_json(
                400,
                _rpc_error(req_id, -32602, "params must be an object"),
            )
            return

        if method in {"initialize", "tools/list"}:
            print(
                json.dumps({"event": "market_mcp_discovery", "method": method}),
                flush=True,
            )

        if (
            req_id is None
            and isinstance(method, str)
            and method.startswith("notifications/")
        ):
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        if method == "initialize":
            self._send_json(
                200,
                _rpc_result(req_id, _market_initialize_result(params)),
            )
            return
        if method == "ping":
            self._send_json(200, _rpc_result(req_id, {}))
            return
        if method == "tools/list":
            self._send_json(
                200,
                _rpc_result(req_id, {"tools": market_tool_specs()}),
            )
            return
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                self._send_json(
                    200,
                    _rpc_error(req_id, -32602, "arguments must be an object"),
                )
                return
            try:
                result = call_market_tool(name, arguments)
                tool_result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                result,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                    "isError": False,
                }
            except Exception as exc:  # noqa: BLE001
                tool_result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "error": type(exc).__name__,
                                    "message": str(exc),
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                    "isError": True,
                }
            self._send_json(200, _rpc_result(req_id, tool_result))
            return

        self._send_json(
            200,
            _rpc_error(req_id, -32601, f"unsupported method: {method}"),
        )

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorize_request():
            return

        path = self._normalized_path()
        provider = self.oauth_provider

        if path == "/market-mcp":
            self._handle_market_mcp()
            return

        if path == "/oauth/register":
            if provider is None:
                self._send_json(404, {"error": "oauth_not_configured"})
                return
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                registered = provider.register_client(payload)
            except OAuthProtocolError as exc:
                self._oauth_error(exc)
                return
            self._send_json(201, registered)
            return

        if path == "/oauth/token":
            if provider is None:
                self._send_json(404, {"error": "oauth_not_configured"})
                return
            form = self._read_form_body()
            if form is None:
                return
            try:
                token_payload = provider.exchange_token(form)
            except OAuthProtocolError as exc:
                self._oauth_error(exc)
                return
            self._send_json(200, token_payload)
            return

        if path == "/oauth/authorize":
            if provider is None:
                self._send_json(404, {"error": "oauth_not_configured"})
                return
            form = self._read_form_body()
            if form is None:
                return
            try:
                params = self._oauth_params_from_form(form)
            except OAuthProtocolError as exc:
                self._oauth_error(exc)
                return
            authorized = (
                provider.login_cookie_valid(self._login_cookie())
                or provider.owner_secret_valid(form.get("owner_secret", ""))
            )
            if not authorized:
                self._send_html(
                    401,
                    provider.authorization_form(
                        params,
                        error="Owner secret is incorrect.",
                    ),
                )
                return
            code = provider.issue_authorization_code(params)
            cookie = provider.issue_login_cookie()
            self._redirect(
                provider.authorization_redirect(params, code),
                set_cookie=self._login_cookie_header(cookie),
            )
            return

        middleware = self.auth_middleware
        if middleware is None:
            middleware = AuthMiddleware(
                AuthConfig.from_environment(legacy_token=self.auth_token)
            )

        if path == "/auth/token":
            payload = self._read_json_body(allow_empty=True)
            if payload is None:
                return
            requested_ttl = payload.get("ttl_seconds")
            try:
                token_payload = middleware.issue_access_token(
                    requested_ttl_seconds=(
                        None if requested_ttl is None else int(requested_ttl)
                    )
                )
            except (TypeError, ValueError):
                self._send_json(
                    400,
                    _rpc_error(None, -32602, "invalid ttl_seconds"),
                )
                return
            except RuntimeError as exc:
                self._send_json(503, _rpc_error(None, -32003, str(exc)))
                return
            self._send_json(200, token_payload)
            return

        if path not in {"/", "/mcp"}:
            self._send_json(404, _rpc_error(None, -32601, "not found"))
            return

        protocol = self.headers.get("MCP-Protocol-Version")
        if protocol is not None and protocol not in _PROTOCOL_VERSIONS:
            self._send_json(
                400,
                _rpc_error(None, -32600, "unsupported MCP protocol version"),
            )
            return

        payload = self._read_json_body()
        if payload is None:
            return

        method = payload.get("method")
        req_id = payload.get("id")
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            self._send_json(
                400,
                _rpc_error(req_id, -32602, "params must be an object"),
            )
            return

        if method in {"initialize", "tools/list"}:
            print(
                json.dumps({"event": "mcp_discovery", "method": method}),
                flush=True,
            )

        if (
            req_id is None
            and isinstance(method, str)
            and method.startswith("notifications/")
        ):
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        try:
            if method == "initialize":
                self._send_json(
                    200,
                    _rpc_result(req_id, _initialize_result(params)),
                )
                return
            if method == "ping":
                self._send_json(200, _rpc_result(req_id, {}))
                return
            if method == "tools/list":
                tools = []
                list_public = getattr(
                    self.gateway,
                    "list_public_tools",
                    self.gateway.list_tools,
                )
                for spec in list_public():
                    item = dict(spec)
                    if provider is not None:
                        item.setdefault(
                            "securitySchemes",
                            [{"type": "oauth2", "scopes": ["toolbox"]}],
                        )
                    tools.append(item)
                self._send_json(
                    200,
                    _rpc_result(req_id, {"tools": tools}),
                )
                return
            if method == "tools/call":
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    self._send_json(
                        200,
                        _rpc_error(
                            req_id,
                            -32602,
                            "arguments must be an object",
                        ),
                    )
                    return
                call_public = getattr(
                    self.gateway,
                    "call_public_tool",
                    self.gateway.call_tool,
                )
                self._send_json(
                    200,
                    _rpc_result(
                        req_id,
                        call_public(
                            name,
                            arguments,
                            request_id=req_id,
                        ),
                    ),
                )
                return
        except RemoteMCPError as exc:
            self._send_json(
                200,
                _rpc_error(req_id, -32002, str(exc)),
            )
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json(
                500,
                _rpc_error(req_id, -32603, f"internal error: {exc}"),
            )
            return

        self._send_json(
            200,
            _rpc_error(req_id, -32601, f"unsupported method: {method}"),
        )


def main() -> None:
    host = os.environ.get("AHMED_TOOLBOX_HOST", "127.0.0.1")
    port = int(
        os.environ.get("PORT")
        or os.environ.get("AHMED_TOOLBOX_PORT", "8765")
    )
    token = os.environ.get("AHMED_TOOLBOX_TOKEN", "")
    auth_config = AuthConfig.from_environment(legacy_token=token)
    auth_config.validate_for_host(host)

    oauth_config = OAuthConfig.from_environment()
    oauth_provider: AhmedOAuthProvider | None = None
    if oauth_config.signing_secret and oauth_config.owner_secret:
        oauth_provider = AhmedOAuthProvider(oauth_config)
    elif host.strip().lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "Ahmed Toolbox OAuth must be configured on a non-loopback host"
        )

    gateway = AhmedToolboxGateway.from_environment()
    auth_middleware = AuthMiddleware(auth_config)

    handler = type(
        "ConfiguredToolboxHandler",
        (ToolboxRequestHandler,),
        {
            "gateway": gateway,
            "auth_middleware": auth_middleware,
            "oauth_provider": oauth_provider,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Ahmed ToolBox MCP listening on http://{host}:{port}/mcp")
    if oauth_provider is not None:
        print(
            json.dumps(
                {
                    "event": "oauth_ready",
                    "issuer": oauth_provider.issuer,
                    "resource": oauth_provider.resource,
                    "refresh": True,
                    "cimd": True,
                    "pkce": "S256",
                }
            ),
            flush=True,
        )
    server.serve_forever()


if __name__ == "__main__":
    main()
