from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from agent_reach.toolbox.auth import AuthConfig, AuthMiddleware
from agent_reach.toolbox.oauth import AhmedOAuthProvider, OAuthConfig
from agent_reach.toolbox.server import ToolboxRequestHandler


class _Gateway:
    def list_tools(self):
        return [
            {
                "name": "runtime_status",
                "description": "runtime status",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]

    def call_tool(self, name, arguments, request_id=None):
        assert name == "runtime_status"
        return {
            "content": [{"type": "text", "text": json.dumps({"status": "ok"})}],
            "isError": False,
        }


def _pkce() -> tuple[str, str]:
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _start_server():
    auth = AuthMiddleware(
        AuthConfig(
            legacy_token="legacy",
            refresh_token="legacy-refresh",
            signing_secret="signing-secret",
            access_ttl_seconds=30,
        )
    )
    handler = type(
        "OAuthHandler",
        (ToolboxRequestHandler,),
        {"gateway": _Gateway(), "auth_middleware": auth, "oauth_provider": None},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{server.server_port}"
    handler.oauth_provider = AhmedOAuthProvider(
        OAuthConfig(
            issuer=base,
            resource=base + "/mcp",
            signing_secret="oauth-signing-secret",
            owner_secret="owner-secret",
            access_ttl_seconds=60,
            refresh_ttl_seconds=3600,
            login_cookie_ttl_seconds=600,
        )
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, base


def _auth_params(base: str, challenge: str) -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": "https://chatgpt.com/oauth/client.json",
        "redirect_uri": "https://chatgpt.com/connector_platform_oauth_redirect",
        "state": "state-123",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": base + "/mcp",
        "scope": "toolbox offline_access",
    }


def test_chatgpt_oauth_discovery_pkce_refresh_and_mcp_access():
    server, thread, base = _start_server()
    try:
        metadata = requests.get(
            base + "/.well-known/oauth-protected-resource", timeout=5
        )
        assert metadata.status_code == 200
        resource = metadata.json()
        assert resource["resource"] == base + "/mcp"
        assert resource["authorization_servers"] == [base]
        assert "offline_access" in resource["scopes_supported"]

        oauth_metadata = requests.get(
            base + "/.well-known/oauth-authorization-server", timeout=5
        )
        assert oauth_metadata.status_code == 200
        oauth = oauth_metadata.json()
        assert oauth["client_id_metadata_document_supported"] is True
        assert oauth["authorization_response_iss_parameter_supported"] is True
        assert oauth["code_challenge_methods_supported"] == ["S256"]
        assert "refresh_token" in oauth["grant_types_supported"]
        assert "offline_access" in oauth["scopes_supported"]

        unauth = requests.post(
            base + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
            timeout=5,
        )
        assert unauth.status_code == 401
        assert "oauth-protected-resource" in unauth.headers["WWW-Authenticate"]

        verifier, challenge = _pkce()
        params = _auth_params(base, challenge)
        authorize = requests.get(
            base + "/oauth/authorize?" + urlencode(params), timeout=5
        )
        assert authorize.status_code == 200
        assert "Authorize Ahmed Toolbox" in authorize.text

        session = requests.Session()
        approved = session.post(
            base + "/oauth/authorize",
            data={**params, "owner_secret": "owner-secret"},
            timeout=5,
            allow_redirects=False,
        )
        assert approved.status_code == 302
        assert "ahmed_oauth_session=" in approved.headers["Set-Cookie"]
        redirect = urlsplit(approved.headers["Location"])
        query = parse_qs(redirect.query)
        assert query["iss"] == [base]
        assert query["state"] == ["state-123"]
        code = query["code"][0]

        token = requests.post(
            base + "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": params["client_id"],
                "redirect_uri": params["redirect_uri"],
                "code": code,
                "code_verifier": verifier,
                "resource": base + "/mcp",
            },
            timeout=5,
        )
        assert token.status_code == 200
        first = token.json()
        assert first["token_type"] == "Bearer"
        assert first["refresh_token"]
        assert "offline_access" in first["scope"]

        replay = requests.post(
            base + "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": params["client_id"],
                "redirect_uri": params["redirect_uri"],
                "code": code,
                "code_verifier": verifier,
                "resource": base + "/mcp",
            },
            timeout=5,
        )
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"

        tools = requests.post(
            base + "/mcp",
            headers={"Authorization": f"Bearer {first['access_token']}"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout=5,
        )
        assert tools.status_code == 200
        spec = tools.json()["result"]["tools"][0]
        assert spec["securitySchemes"] == [{"type": "oauth2", "scopes": ["toolbox"]}]

        refreshed = requests.post(
            base + "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": params["client_id"],
                "refresh_token": first["refresh_token"],
                "resource": base + "/mcp",
            },
            timeout=5,
        )
        assert refreshed.status_code == 200
        second = refreshed.json()
        assert second["access_token"] != first["access_token"]
        assert second["refresh_token"] != first["refresh_token"]

        status = requests.post(
            base + "/mcp",
            headers={"Authorization": f"Bearer {second['access_token']}"},
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "runtime_status", "arguments": {}},
            },
            timeout=5,
        )
        assert status.status_code == 200
        result = status.json()["result"]
        assert json.loads(result["content"][0]["text"])["status"] == "ok"

        second_authorize = session.get(
            base + "/oauth/authorize?" + urlencode(params),
            timeout=5,
            allow_redirects=False,
        )
        assert second_authorize.status_code == 302
        assert second_authorize.headers["Location"].startswith(
            "https://chatgpt.com/connector_platform_oauth_redirect?"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dynamic_registration_is_chatgpt_only_and_stateless():
    provider = AhmedOAuthProvider(
        OAuthConfig(
            issuer="https://toolbox.example",
            resource="https://toolbox.example/mcp",
            signing_secret="signing-secret",
            owner_secret="owner-secret",
        )
    )
    registered = provider.register_client(
        {
            "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
            "grant_types": ["authorization_code", "refresh_token"],
        }
    )
    assert registered["client_id"].startswith("cl1.")
    provider.validate_client_redirect(
        registered["client_id"],
        "https://chatgpt.com/connector_platform_oauth_redirect",
    )


def test_codex_cimd_loopback_clients_are_supported_and_strict():
    provider = AhmedOAuthProvider(
        OAuthConfig(
            issuer="https://toolbox.example",
            resource="https://toolbox.example/mcp",
            signing_secret="signing-secret",
            owner_secret="owner-secret",
        )
    )

    provider.validate_client_redirect(
        "https://chatgpt.com/oauth/codex/client.json",
        "http://127.0.0.1:43123/callback",
    )
    provider.validate_client_redirect(
        "https://chatgpt.com/oauth/codex/client.json",
        "http://localhost:43123/callback",
    )

    callback_id = "abc123ABC_-x"
    provider.validate_client_redirect(
        f"https://chatgpt.com/oauth/codex/{callback_id}/client.json",
        f"http://127.0.0.1:43123/callback/{callback_id}",
    )

    bad_pairs = [
        (
            "https://chatgpt.com/oauth/codex/client.json",
            "https://127.0.0.1:43123/callback",
        ),
        (
            "https://chatgpt.com/oauth/codex/client.json",
            "http://127.0.0.1:43123/callback/wrong",
        ),
        (
            f"https://chatgpt.com/oauth/codex/{callback_id}/client.json",
            "http://127.0.0.1:43123/callback",
        ),
        (
            f"https://chatgpt.com/oauth/codex/{callback_id}/client.json",
            "http://127.0.0.1:43123/callback/other",
        ),
    ]
    for client_id, redirect_uri in bad_pairs:
        try:
            provider.validate_client_redirect(client_id, redirect_uri)
        except Exception:
            pass
        else:
            raise AssertionError(f"unexpectedly accepted {client_id=} {redirect_uri=}")
