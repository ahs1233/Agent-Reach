"""OAuth 2.1 support for the Ahmed Toolbox MCP endpoint.

The provider is intentionally single-owner and stateless. It implements the
pieces ChatGPT needs for a durable MCP connection: RFC 9728 protected-resource
metadata, RFC 8414 authorization-server metadata, CIMD/DCR client identity,
authorization-code + PKCE (S256), RFC 9207 issuer identification, audience
binding, and renewable refresh tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

_CHATGPT_STABLE_CLIENT_ID = "https://chatgpt.com/oauth/client.json"
_CHATGPT_STABLE_REDIRECT_URI = "https://chatgpt.com/connector_platform_oauth_redirect"
_CHATGPT_CALLBACK_CLIENT_RE = re.compile(
    r"^https://chatgpt\.com/oauth/([A-Za-z0-9_-]{8,200})/client\.json$"
)
_CHATGPT_CALLBACK_REDIRECT_RE = re.compile(
    r"^https://chatgpt\.com/connector/oauth/([A-Za-z0-9_-]{8,200})$"
)
_CODEX_STABLE_CLIENT_ID = "https://chatgpt.com/oauth/codex/client.json"
_CODEX_CALLBACK_CLIENT_RE = re.compile(
    r"^https://chatgpt\.com/oauth/codex/([A-Za-z0-9_-]{8,200})/client\.json$"
)
_CODEX_CALLBACK_PATH_RE = re.compile(
    r"^/callback(?:/([A-Za-z0-9_-]{8,200}))?$"
)
_SCOPE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ALLOWED_SCOPES = ("toolbox", "offline_access")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _compact_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _first(values: Mapping[str, list[str]], key: str) -> str:
    items = values.get(key) or []
    return str(items[0]) if items else ""


def _scope_set(raw: str) -> tuple[str, ...]:
    parts = tuple(dict.fromkeys(part for part in raw.split() if part))
    if not parts:
        return _ALLOWED_SCOPES
    if any(not _SCOPE_RE.fullmatch(part) for part in parts):
        raise OAuthProtocolError("invalid_scope", "scope contains an invalid value")
    unknown = sorted(set(parts) - set(_ALLOWED_SCOPES))
    if unknown:
        raise OAuthProtocolError("invalid_scope", f"unsupported scopes: {', '.join(unknown)}")
    return parts


def _is_loopback_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _codex_loopback_callback(value: str) -> tuple[bool, str]:
    """Validate Codex native-app loopback redirects and return their callback id."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None
        or parsed.port <= 0
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return False, ""
    match = _CODEX_CALLBACK_PATH_RE.fullmatch(parsed.path)
    if not match:
        return False, ""
    return True, match.group(1) or ""


@dataclass(frozen=True)
class OAuthConfig:
    issuer: str
    resource: str
    signing_secret: str
    owner_secret: str
    access_ttl_seconds: int = 900
    refresh_ttl_seconds: int = 31_536_000
    login_cookie_ttl_seconds: int = 2_592_000

    @classmethod
    def from_environment(cls) -> "OAuthConfig":
        explicit = os.environ.get("AHMED_TOOLBOX_PUBLIC_URL", "").strip().rstrip("/")
        railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        issuer = explicit or (f"https://{railway_domain}" if railway_domain else "")
        if not issuer:
            host = os.environ.get("AHMED_TOOLBOX_HOST", "127.0.0.1").strip()
            port = os.environ.get("PORT") or os.environ.get("AHMED_TOOLBOX_PORT", "8765")
            issuer = f"http://{host}:{port}"
        resource = os.environ.get("AHMED_TOOLBOX_RESOURCE", "").strip() or issuer + "/mcp"
        owner_secret = (
            os.environ.get("AHMED_TOOLBOX_OAUTH_OWNER_SECRET", "").strip()
            or os.environ.get("AHMED_TOOLBOX_REFRESH_TOKEN", "").strip()
            or os.environ.get("AHMED_TOOLBOX_TOKEN", "").strip()
        )
        signing_secret = (
            os.environ.get("AHMED_TOOLBOX_AUTH_SECRET", "").strip() or owner_secret
        )
        return cls(
            issuer=issuer,
            resource=resource,
            signing_secret=signing_secret,
            owner_secret=owner_secret,
            access_ttl_seconds=max(
                60,
                int(os.environ.get("AHMED_TOOLBOX_OAUTH_ACCESS_TTL_SECONDS", "900")),
            ),
            refresh_ttl_seconds=max(
                3600,
                int(os.environ.get("AHMED_TOOLBOX_OAUTH_REFRESH_TTL_SECONDS", "31536000")),
            ),
            login_cookie_ttl_seconds=max(
                300,
                int(os.environ.get("AHMED_TOOLBOX_OAUTH_COOKIE_TTL_SECONDS", "2592000")),
            ),
        )

    def validate(self) -> None:
        issuer = urlsplit(self.issuer)
        resource = urlsplit(self.resource)
        if not issuer.scheme or not issuer.netloc:
            raise RuntimeError("AHMED_TOOLBOX_PUBLIC_URL must be an absolute URL")
        if issuer.scheme != "https" and not _is_loopback_url(self.issuer):
            raise RuntimeError("Ahmed Toolbox OAuth issuer must use HTTPS outside loopback")
        if resource.scheme != issuer.scheme or resource.netloc != issuer.netloc:
            raise RuntimeError("Ahmed Toolbox OAuth resource must share the issuer origin")
        if not self.signing_secret:
            raise RuntimeError("AHMED_TOOLBOX_AUTH_SECRET is required for OAuth")
        if not self.owner_secret:
            raise RuntimeError(
                "AHMED_TOOLBOX_OAUTH_OWNER_SECRET, AHMED_TOOLBOX_REFRESH_TOKEN, "
                "or AHMED_TOOLBOX_TOKEN is required for owner authorization"
            )


@dataclass(frozen=True)
class OAuthProtocolError(RuntimeError):
    error: str
    description: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.description


class AhmedOAuthProvider:
    """Small, standards-oriented OAuth provider for a single Ahmed Toolbox owner."""

    def __init__(self, config: OAuthConfig):
        config.validate()
        self.config = config
        self._redeemed_lock = threading.RLock()
        self._redeemed_codes: dict[str, int] = {}

    @property
    def issuer(self) -> str:
        return self.config.issuer.rstrip("/")

    @property
    def resource(self) -> str:
        return self.config.resource

    @property
    def resource_metadata_url(self) -> str:
        return self.issuer + "/.well-known/oauth-protected-resource"

    @property
    def authorization_endpoint(self) -> str:
        return self.issuer + "/oauth/authorize"

    @property
    def token_endpoint(self) -> str:
        return self.issuer + "/oauth/token"

    @property
    def registration_endpoint(self) -> str:
        return self.issuer + "/oauth/register"

    def protected_resource_metadata(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "authorization_servers": [self.issuer],
            "scopes_supported": list(_ALLOWED_SCOPES),
            "bearer_methods_supported": ["header"],
        }

    def authorization_server_metadata(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "registration_endpoint": self.registration_endpoint,
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": list(_ALLOWED_SCOPES),
        }

    def _sign(self, prefix: str, payload: dict[str, Any]) -> str:
        segment = _b64url_encode(_compact_json(payload))
        signing_input = f"{prefix}.{segment}".encode("ascii")
        signature = hmac.new(
            self.config.signing_secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        return f"{prefix}.{segment}.{_b64url_encode(signature)}"

    def _verify(self, token: str, prefix: str) -> dict[str, Any]:
        try:
            supplied_prefix, segment, signature_segment = token.split(".", 2)
        except ValueError as exc:
            raise OAuthProtocolError("invalid_grant", "malformed signed token", 401) from exc
        if supplied_prefix != prefix:
            raise OAuthProtocolError("invalid_grant", "unexpected signed token type", 401)
        signing_input = f"{prefix}.{segment}".encode("ascii")
        expected = hmac.new(
            self.config.signing_secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        try:
            supplied = _b64url_decode(signature_segment)
        except Exception as exc:
            raise OAuthProtocolError("invalid_grant", "malformed token signature", 401) from exc
        if not hmac.compare_digest(expected, supplied):
            raise OAuthProtocolError("invalid_grant", "invalid token signature", 401)
        try:
            payload = json.loads(_b64url_decode(segment))
        except Exception as exc:
            raise OAuthProtocolError("invalid_grant", "invalid token payload", 401) from exc
        if not isinstance(payload, dict):
            raise OAuthProtocolError("invalid_grant", "invalid token payload", 401)
        now = int(time.time())
        try:
            exp = int(payload["exp"])
            iat = int(payload["iat"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OAuthProtocolError("invalid_grant", "token timestamps are invalid", 401) from exc
        if iat > now + 30 or exp <= now:
            raise OAuthProtocolError("invalid_grant", "token is expired or not yet valid", 401)
        return payload

    def _validate_resource(self, resource: str) -> None:
        if resource != self.resource:
            raise OAuthProtocolError("invalid_target", "resource does not match Ahmed Toolbox")

    def _client_redirects(self, client_id: str) -> tuple[str, ...]:
        if client_id == _CHATGPT_STABLE_CLIENT_ID:
            return (_CHATGPT_STABLE_REDIRECT_URI,)
        match = _CHATGPT_CALLBACK_CLIENT_RE.fullmatch(client_id)
        if match:
            callback_id = match.group(1)
            return (f"https://chatgpt.com/connector/oauth/{callback_id}",)
        if client_id.startswith("cl1."):
            try:
                payload = self._verify(client_id, "cl1")
            except OAuthProtocolError as exc:
                raise OAuthProtocolError("invalid_client", exc.description, 401) from exc
            redirects = payload.get("redirect_uris") or []
            if isinstance(redirects, list) and all(isinstance(item, str) for item in redirects):
                return tuple(redirects)
        raise OAuthProtocolError("invalid_client", "unrecognized OAuth client", 401)

    @staticmethod
    def _is_chatgpt_redirect(value: str) -> bool:
        if value == _CHATGPT_STABLE_REDIRECT_URI:
            return True
        if _CHATGPT_CALLBACK_REDIRECT_RE.fullmatch(value):
            return True
        valid, _ = _codex_loopback_callback(value)
        return valid

    def validate_client_redirect(self, client_id: str, redirect_uri: str) -> None:
        # The ChatGPT Windows desktop app uses the Codex MCP client. With RFC 9207
        # issuer identification enabled it identifies as the stable Codex CIMD client
        # and redirects to an ephemeral loopback callback. Older/issuerless clients
        # use a callback-id-specific CIMD identity and callback path.
        valid_loopback, callback_id = _codex_loopback_callback(redirect_uri)
        if client_id == _CODEX_STABLE_CLIENT_ID:
            if valid_loopback and not callback_id:
                return
            raise OAuthProtocolError("invalid_request", "redirect_uri is not registered")
        codex_match = _CODEX_CALLBACK_CLIENT_RE.fullmatch(client_id)
        if codex_match:
            if valid_loopback and callback_id == codex_match.group(1):
                return
            raise OAuthProtocolError("invalid_request", "redirect_uri is not registered")

        redirects = self._client_redirects(client_id)
        if redirect_uri not in redirects or not self._is_chatgpt_redirect(redirect_uri):
            raise OAuthProtocolError("invalid_request", "redirect_uri is not registered")

    def register_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        redirect_uris = payload.get("redirect_uris") or []
        if (
            not isinstance(redirect_uris, list)
            or not redirect_uris
            or not all(isinstance(item, str) for item in redirect_uris)
            or not all(self._is_chatgpt_redirect(item) for item in redirect_uris)
        ):
            raise OAuthProtocolError(
                "invalid_redirect_uri",
                "Ahmed Toolbox only accepts the documented ChatGPT OAuth redirect URIs",
            )
        grant_types = payload.get("grant_types") or ["authorization_code", "refresh_token"]
        if not isinstance(grant_types, list) or not set(grant_types).issubset(
            {"authorization_code", "refresh_token"}
        ):
            raise OAuthProtocolError("invalid_client_metadata", "unsupported grant_types")
        now = int(time.time())
        client_payload = {
            "redirect_uris": list(dict.fromkeys(redirect_uris)),
            "iat": now,
            "exp": now + 315_360_000,
        }
        client_id = self._sign("cl1", client_payload)
        return {
            "client_id": client_id,
            "client_id_issued_at": now,
            "redirect_uris": client_payload["redirect_uris"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "application_type": "web",
        }

    def parse_authorization_params(self, raw_query: str) -> dict[str, Any]:
        values = parse_qs(raw_query, keep_blank_values=True)
        response_type = _first(values, "response_type")
        client_id = _first(values, "client_id")
        redirect_uri = _first(values, "redirect_uri")
        state = _first(values, "state")
        code_challenge = _first(values, "code_challenge")
        code_challenge_method = _first(values, "code_challenge_method")
        resource = _first(values, "resource")
        scopes = _scope_set(_first(values, "scope"))

        if response_type != "code":
            raise OAuthProtocolError("unsupported_response_type", "response_type must be code")
        if not client_id or not redirect_uri:
            raise OAuthProtocolError("invalid_request", "client_id and redirect_uri are required")
        self.validate_client_redirect(client_id, redirect_uri)
        if code_challenge_method != "S256" or not code_challenge:
            raise OAuthProtocolError("invalid_request", "PKCE S256 is required")
        if len(code_challenge) < 43 or len(code_challenge) > 128:
            raise OAuthProtocolError("invalid_request", "code_challenge length is invalid")
        self._validate_resource(resource)
        return {
            "response_type": response_type,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": resource,
            "scope": " ".join(scopes),
        }

    def owner_secret_valid(self, supplied: str) -> bool:
        expected = self.config.owner_secret
        return bool(supplied and expected and hmac.compare_digest(supplied, expected))

    def issue_login_cookie(self) -> str:
        now = int(time.time())
        return self._sign(
            "ls1",
            {
                "sub": "owner",
                "iat": now,
                "exp": now + self.config.login_cookie_ttl_seconds,
                "nonce": secrets.token_urlsafe(12),
            },
        )

    def login_cookie_valid(self, token: str) -> bool:
        try:
            payload = self._verify(token, "ls1")
        except OAuthProtocolError:
            return False
        return payload.get("sub") == "owner"

    def authorization_form(self, params: Mapping[str, str], *, error: str = "") -> str:
        hidden = "\n".join(
            f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(str(value), quote=True)}">'
            for key, value in params.items()
        )
        error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize Ahmed Toolbox</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#f6f7f9;margin:0;padding:32px;color:#18212f}}
.card{{max-width:520px;margin:8vh auto;background:#fff;border:1px solid #dce1e8;border-radius:16px;padding:28px;box-shadow:0 8px 30px rgba(0,0,0,.06)}}
h1{{font-size:24px;margin:0 0 12px}}p{{line-height:1.5}}label{{display:block;font-weight:600;margin:20px 0 8px}}input[type=password]{{width:100%;box-sizing:border-box;padding:12px;border:1px solid #b8c0cc;border-radius:10px;font-size:16px}}
button{{margin-top:18px;width:100%;padding:12px;border:0;border-radius:10px;background:#111827;color:#fff;font-weight:700;font-size:16px}}.error{{color:#a61b1b;font-weight:600}}.small{{font-size:13px;color:#5c6675}}
</style>
</head>
<body><div class="card">
<h1>Authorize Ahmed Toolbox</h1>
<p>ChatGPT is requesting access to your private Ahmed Toolbox MCP server.</p>
{error_html}
<form method="post" action="/oauth/authorize">
{hidden}
<label for="owner_secret">Owner secret</label>
<input id="owner_secret" name="owner_secret" type="password" autocomplete="current-password" required autofocus>
<button type="submit">Authorize ChatGPT</button>
</form>
<p class="small">Use the Ahmed Toolbox OAuth owner secret. It is never placed in the redirect URL.</p>
</div></body></html>"""

    def issue_authorization_code(self, params: Mapping[str, str]) -> str:
        now = int(time.time())
        return self._sign(
            "ac1",
            {
                "sub": "owner",
                "client_id": params["client_id"],
                "redirect_uri": params["redirect_uri"],
                "scope": params["scope"],
                "resource": params["resource"],
                "code_challenge": params["code_challenge"],
                "iat": now,
                "exp": now + 300,
                "nonce": secrets.token_urlsafe(16),
            },
        )

    def authorization_redirect(self, params: Mapping[str, str], code: str) -> str:
        parsed = urlsplit(params["redirect_uri"])
        query = {"code": code, "iss": self.issuer}
        if params.get("state"):
            query["state"] = params["state"]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))

    def _issue_access_and_refresh(
        self,
        *,
        client_id: str,
        scope: str,
        resource: str,
    ) -> dict[str, Any]:
        now = int(time.time())
        common = {
            "iss": self.issuer,
            "sub": "owner",
            "aud": resource,
            "resource": resource,
            "client_id": client_id,
            "scope": scope,
            "iat": now,
        }
        access_token = self._sign(
            "oa1",
            {
                **common,
                "exp": now + self.config.access_ttl_seconds,
                "jti": secrets.token_urlsafe(16),
            },
        )
        refresh_token = self._sign(
            "rt1",
            {
                **common,
                "exp": now + self.config.refresh_ttl_seconds,
                "jti": secrets.token_urlsafe(24),
            },
        )
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": self.config.access_ttl_seconds,
            "refresh_token": refresh_token,
            "scope": scope,
        }

    def exchange_token(self, form: Mapping[str, str]) -> dict[str, Any]:
        grant_type = str(form.get("grant_type") or "")
        client_id = str(form.get("client_id") or "")
        resource = str(form.get("resource") or "")
        if not client_id:
            raise OAuthProtocolError("invalid_client", "client_id is required", 401)

        if grant_type == "authorization_code":
            code = str(form.get("code") or "")
            redirect_uri = str(form.get("redirect_uri") or "")
            code_verifier = str(form.get("code_verifier") or "")
            if not code or not redirect_uri or not code_verifier:
                raise OAuthProtocolError(
                    "invalid_request", "code, redirect_uri, and code_verifier are required"
                )
            self.validate_client_redirect(client_id, redirect_uri)
            self._validate_resource(resource)
            payload = self._verify(code, "ac1")
            if payload.get("client_id") != client_id or payload.get("redirect_uri") != redirect_uri:
                raise OAuthProtocolError("invalid_grant", "authorization code client mismatch")
            if payload.get("resource") != resource:
                raise OAuthProtocolError("invalid_grant", "authorization code resource mismatch")
            try:
                verifier_bytes = code_verifier.encode("ascii")
            except UnicodeEncodeError as exc:
                raise OAuthProtocolError("invalid_grant", "PKCE verifier must be ASCII") from exc
            expected = _b64url_encode(hashlib.sha256(verifier_bytes).digest())
            if not hmac.compare_digest(expected, str(payload.get("code_challenge") or "")):
                raise OAuthProtocolError("invalid_grant", "PKCE verification failed")
            code_digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
            now = int(time.time())
            with self._redeemed_lock:
                self._redeemed_codes = {
                    digest: exp
                    for digest, exp in self._redeemed_codes.items()
                    if exp > now
                }
                if code_digest in self._redeemed_codes:
                    raise OAuthProtocolError(
                        "invalid_grant", "authorization code was already redeemed"
                    )
                self._redeemed_codes[code_digest] = int(payload["exp"])
            return self._issue_access_and_refresh(
                client_id=client_id,
                scope=str(payload.get("scope") or "toolbox offline_access"),
                resource=resource,
            )

        if grant_type == "refresh_token":
            refresh_token = str(form.get("refresh_token") or "")
            if not refresh_token:
                raise OAuthProtocolError("invalid_request", "refresh_token is required")
            payload = self._verify(refresh_token, "rt1")
            if payload.get("client_id") != client_id:
                raise OAuthProtocolError("invalid_grant", "refresh token client mismatch")
            bound_resource = str(payload.get("resource") or payload.get("aud") or "")
            if resource and resource != bound_resource:
                raise OAuthProtocolError("invalid_target", "refresh resource mismatch")
            self._validate_resource(bound_resource)
            original_scope = _scope_set(str(payload.get("scope") or ""))
            requested_scope_raw = str(form.get("scope") or "").strip()
            requested_scope = (
                _scope_set(requested_scope_raw) if requested_scope_raw else original_scope
            )
            if not set(requested_scope).issubset(set(original_scope)):
                raise OAuthProtocolError("invalid_scope", "refresh cannot widen scope")
            return self._issue_access_and_refresh(
                client_id=client_id,
                scope=" ".join(requested_scope),
                resource=bound_resource,
            )

        raise OAuthProtocolError("unsupported_grant_type", "unsupported grant_type")

    def validate_access_token(self, token: str, *, required_scope: str = "toolbox") -> bool:
        if not token.startswith("oa1."):
            return False
        try:
            payload = self._verify(token, "oa1")
        except OAuthProtocolError:
            return False
        if payload.get("iss") != self.issuer:
            return False
        if payload.get("aud") != self.resource or payload.get("resource") != self.resource:
            return False
        scopes = set(str(payload.get("scope") or "").split())
        return required_scope in scopes


def parse_form_body(body: bytes) -> dict[str, str]:
    try:
        raw = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OAuthProtocolError("invalid_request", "form body must be UTF-8") from exc
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}
