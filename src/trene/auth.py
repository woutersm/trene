"""A single-owner OAuth authorization-server provider.

One person, the owner, proves who they are with a password. Registered clients,
access tokens and refresh tokens persist in a JSON file so the owner's connection
to claude.ai survives a restart. Pending login requests and authorization codes
live in memory only; they last minutes.
"""

import hmac
import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

ACCESS_TTL = 3600  # seconds
REFRESH_TTL = 90 * 24 * 3600
CODE_TTL = 300
REQUEST_TTL = 600

# Where a login may send its code: claude.ai's callbacks, or the person's own machine
# (Claude Code and Claude Desktop use a loopback callback). Anything else would let a
# stranger register their own site and collect a code from a real login link.
CLAUDE_CALLBACKS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)


def allowed_redirect(uri: str) -> bool:
    parsed = urlparse(uri)
    loopback = parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")
    return uri in CLAUDE_CALLBACKS or loopback


@dataclass
class LoginRequest:
    client_id: str
    params: AuthorizationParams
    expires_at: float


class OwnerAuth:
    def __init__(
        self,
        base_url: str,
        password: str,
        store: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._password = password
        self._store = store
        self._clock = clock
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._access: dict[str, AccessToken] = {}
        self._refresh: dict[str, RefreshToken] = {}
        self._requests: dict[str, LoginRequest] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._load()

    # -- provider protocol (all async) -------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        bad = [str(u) for u in client_info.redirect_uris or [] if not allowed_redirect(str(u))]
        if bad:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description=f"redirect URI not allowed: {bad[0]}",
            )
        self._clients[client_info.client_id] = client_info
        self._save()

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        now = self._clock()
        self._requests = {k: r for k, r in self._requests.items() if r.expires_at >= now}
        self._codes = {k: c for k, c in self._codes.items() if c.expires_at >= now}
        request_id = secrets.token_urlsafe(16)
        self._requests[request_id] = LoginRequest(
            client_id=client.client_id, params=params, expires_at=self._clock() + REQUEST_TTL
        )
        return f"{self._base_url}/login?request={quote(request_id, safe='')}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if code is None or code.expires_at < self._clock() or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)
        return self._issue_tokens(
            client.client_id, authorization_code.scopes, authorization_code.resource
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token = self._refresh.get(refresh_token)
        if (
            token is None
            or token.client_id != client.client_id
            or token.expires_at is not None
            and token.expires_at < self._clock()
        ):
            return None
        return token

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        self._refresh.pop(refresh_token.token, None)
        return self._issue_tokens(
            client.client_id, scopes or refresh_token.scopes, refresh_token.resource
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = self._access.get(token)
        if access is None or access.expires_at is not None and access.expires_at < self._clock():
            return None
        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._access.pop(token.token, None)
        self._refresh.pop(token.token, None)
        self._save()

    # -- /login page (sync) ------------------------------------------------

    def has_request(self, request_id: str) -> bool:
        request = self._requests.get(request_id)
        return request is not None and request.expires_at >= self._clock()

    def approve(self, request_id: str, password: str) -> str | None:
        request = self._requests.get(request_id)
        if request is None or request.expires_at < self._clock():
            raise KeyError(request_id)
        if not hmac.compare_digest(password.encode(), self._password.encode()):
            return None
        self._requests.pop(request_id, None)
        params = request.params
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            expires_at=self._clock() + CODE_TTL,
            scopes=params.scopes or [],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject="owner",
            client_id=request.client_id,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    # -- internals -----------------------------------------------------------

    def _issue_tokens(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        now = int(self._clock())
        access = AccessToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TTL,
            resource=resource,
            subject="owner",
        )
        refresh = RefreshToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TTL,
            resource=resource,
            subject="owner",
        )
        self._access[access.token] = access
        self._refresh[refresh.token] = refresh
        self._save()
        return OAuthToken(
            access_token=access.token,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            refresh_token=refresh.token,
            scope=" ".join(scopes) or None,
        )

    def _save(self) -> None:
        now = self._clock()
        self._access = {
            k: t for k, t in self._access.items() if t.expires_at is None or t.expires_at >= now
        }
        self._refresh = {
            k: t for k, t in self._refresh.items() if t.expires_at is None or t.expires_at >= now
        }
        self._store.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "clients": [
                c.model_dump(mode="json", exclude_none=True) for c in self._clients.values()
            ],
            "access": [t.model_dump(mode="json") for t in self._access.values()],
            "refresh": [t.model_dump(mode="json") for t in self._refresh.values()],
        }
        tmp = self._store.with_suffix(self._store.suffix + ".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self._store)

    def _load(self) -> None:
        if not self._store.exists():
            return
        data = json.loads(self._store.read_text())
        now = self._clock()
        clients = [
            OAuthClientInformationFull.model_validate(c) for c in data.get("clients", [])
        ]
        self._clients = {c.client_id: c for c in clients}
        self._access = {
            t.token: t
            for t in (AccessToken.model_validate(x) for x in data.get("access", []))
            if t.expires_at is None or t.expires_at >= now
        }
        self._refresh = {
            t.token: t
            for t in (RefreshToken.model_validate(x) for x in data.get("refresh", []))
            if t.expires_at is None or t.expires_at >= now
        }
