"""Tests for the single-owner OAuth provider (src/trene/auth.py)."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.auth.provider import AccessToken, AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from trene.auth import (
    ACCESS_TTL,
    CODE_TTL,
    REFRESH_TTL,
    REQUEST_TTL,
    OwnerAuth,
)


def client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="c1", redirect_uris=["https://claude.ai/api/mcp/auth_callback"]
    )


def params() -> AuthorizationParams:
    return AuthorizationParams(
        state="s",
        scopes=None,
        code_challenge="x" * 43,
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_provided_explicitly=True,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def exchange(auth: OwnerAuth, c: OAuthClientInformationFull, redirect: str) -> OAuthToken:
    from urllib.parse import parse_qs

    code = parse_qs(redirect.split("?", 1)[1])["code"][0]
    stored = run(auth.load_authorization_code(c, code))
    assert stored is not None
    return run(auth.exchange_authorization_code(c, stored))


def redirect_from(auth: OwnerAuth) -> str:
    url = run(auth.authorize(client(), params()))
    result = auth.approve(url.rsplit("request=", 1)[1], "hunter2")
    assert result is not None
    return result


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "store.json"


@pytest.fixture
def now() -> list[float]:
    return [1000.0]


@pytest.fixture
def auth(store: Path, now: list[float]) -> OwnerAuth:
    return OwnerAuth(
        base_url="https://trene.example", password="hunter2", store=store, clock=lambda: now[0]
    )


def test_authorize_returns_login_url_and_request_lives_request_ttl(
    auth: OwnerAuth, now: list[float]
) -> None:
    url = run(auth.authorize(client(), params()))
    assert url.startswith("https://trene.example/login?request=")
    request_id = url.rsplit("request=", 1)[1]
    assert auth.has_request(request_id)
    now[0] += REQUEST_TTL + 1
    assert not auth.has_request(request_id)


def test_authorize_request_id_is_unique_per_call(auth: OwnerAuth) -> None:
    a = run(auth.authorize(client(), params()))
    b = run(auth.authorize(client(), params()))
    assert a != b


def test_approve_wrong_password_keeps_request(auth: OwnerAuth) -> None:
    url = run(auth.authorize(client(), params()))
    request_id = url.rsplit("request=", 1)[1]
    assert auth.approve(request_id, "wrong") is None
    assert auth.has_request(request_id)


def test_approve_unknown_or_expired_request_raises(auth: OwnerAuth, now: list[float]) -> None:
    with pytest.raises(KeyError):
        auth.approve("no-such-request", "hunter2")
    url = run(auth.authorize(client(), params()))
    request_id = url.rsplit("request=", 1)[1]
    now[0] += REQUEST_TTL + 1
    with pytest.raises(KeyError):
        auth.approve(request_id, "hunter2")


def test_approve_redirects_with_code_and_state(auth: OwnerAuth) -> None:
    url = run(auth.authorize(client(), params()))
    request_id = url.rsplit("request=", 1)[1]
    result = auth.approve(request_id, "hunter2")
    assert result is not None
    assert result.startswith("https://claude.ai/api/mcp/auth_callback?")
    from urllib.parse import parse_qs

    query = parse_qs(result.split("?", 1)[1])
    assert "code" in query
    assert query["state"] == ["s"]
    # The request was consumed, so a second approve fails.
    with pytest.raises(KeyError):
        auth.approve(request_id, "hunter2")


def test_code_exchanges_once_then_load_returns_none(auth: OwnerAuth, now: list[float]) -> None:
    redirect = redirect_from(auth)
    from urllib.parse import parse_qs

    code = parse_qs(redirect.split("?", 1)[1])["code"][0]
    c = client()
    stored = run(auth.load_authorization_code(c, code))
    assert stored is not None
    assert stored.scopes == []

    token = run(auth.exchange_authorization_code(c, stored))
    assert token.token_type == "Bearer"
    assert token.access_token
    assert token.refresh_token
    assert token.expires_in == ACCESS_TTL
    assert token.scope is None

    assert run(auth.load_authorization_code(c, code)) is None

    # A code also stops loading once expired, or when shown to another client.
    other = OAuthClientInformationFull(
        client_id="c2", redirect_uris=["https://claude.ai/api/mcp/auth_callback"]
    )
    url2 = run(auth.authorize(client(), params()))
    rid2 = url2.rsplit("request=", 1)[1]
    redirect2 = auth.approve(rid2, "hunter2")
    assert redirect2 is not None
    code2 = parse_qs(redirect2.split("?", 1)[1])["code"][0]
    assert run(auth.load_authorization_code(other, code2)) is None
    now[0] += CODE_TTL + 1
    assert run(auth.load_authorization_code(c, code2)) is None


def test_access_token_loads_then_expires(auth: OwnerAuth, now: list[float]) -> None:
    token = exchange(auth, client(), redirect_from(auth))
    loaded = run(auth.load_access_token(token.access_token))
    assert loaded is not None
    assert loaded.client_id == "c1"
    assert loaded.subject == "owner"
    now[0] += ACCESS_TTL + 1
    assert run(auth.load_access_token(token.access_token)) is None
    assert run(auth.load_access_token("bogus")) is None


def test_refresh_token_rotates(auth: OwnerAuth) -> None:
    token = exchange(auth, client(), redirect_from(auth))
    assert token.refresh_token is not None
    old = token.refresh_token

    loaded = run(auth.load_refresh_token(client(), old))
    assert loaded is not None
    assert loaded.subject == "owner"
    assert loaded.expires_at is not None and loaded.expires_at == 1000 + REFRESH_TTL

    again = run(auth.exchange_refresh_token(client(), loaded, None))
    assert again.refresh_token is not None
    assert again.refresh_token != old
    assert again.access_token != token.access_token
    assert run(auth.load_refresh_token(client(), old)) is None
    assert run(auth.load_refresh_token(client(), "bogus")) is None

    # A refresh token for another client is invisible to this one.
    other = OAuthClientInformationFull(
        client_id="c2", redirect_uris=["https://claude.ai/api/mcp/auth_callback"]
    )
    assert run(auth.load_refresh_token(other, again.refresh_token)) is None


def test_exchange_refresh_token_uses_new_scopes(auth: OwnerAuth) -> None:
    token = exchange(auth, client(), redirect_from(auth))
    loaded = run(auth.load_refresh_token(client(), token.refresh_token))
    assert loaded is not None
    rotated = run(auth.exchange_refresh_token(client(), loaded, ["read"]))
    assert rotated.scope == "read"


def test_revoke_token(auth: OwnerAuth) -> None:
    token = exchange(auth, client(), redirect_from(auth))
    assert run(auth.load_access_token(token.access_token)) is not None
    run(auth.revoke_token(run(auth.load_access_token(token.access_token))))
    assert run(auth.load_access_token(token.access_token)) is None
    # Unknown tokens are ignored.
    run(auth.revoke_token(run(auth.load_refresh_token(client(), token.refresh_token))))
    assert run(auth.load_refresh_token(client(), token.refresh_token)) is None
    # Revoking an unknown token is ignored.
    bogus = AccessToken(token="bogus", client_id="c1", scopes=[], expires_at=None)
    run(auth.revoke_token(bogus))


def test_persistence_across_restart(store: Path, now: list[float]) -> None:
    first = OwnerAuth(
        base_url="https://trene.example", password="hunter2", store=store, clock=lambda: now[0]
    )
    run(first.register_client(client()))
    redirect = redirect_from(first)
    token = exchange(first, client(), redirect)

    assert store.exists()
    data = json.loads(store.read_text())
    assert data["version"] == 1

    second = OwnerAuth(
        base_url="https://trene.example", password="hunter2", store=store, clock=lambda: now[0]
    )
    assert run(second.get_client("c1")) is not None
    assert run(second.load_access_token(token.access_token)) is not None
    assert run(second.load_refresh_token(client(), token.refresh_token)) is not None


def test_register_client_replaces_existing(store: Path, now: list[float]) -> None:
    a = OwnerAuth(
        base_url="https://trene.example", password="hunter2", store=store, clock=lambda: now[0]
    )
    run(a.register_client(client()))
    run(a.register_client(client()))
    data = json.loads(store.read_text())
    assert len(data["clients"]) == 1


def test_load_drops_expired_tokens(tmp_path: Path) -> None:
    store = tmp_path / "store.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        json.dumps(
            {
                "version": 1,
                "clients": [],
                "access": [
                    {
                        "token": "old-access",
                        "client_id": "c1",
                        "scopes": [],
                        "expires_at": 500,
                        "resource": None,
                        "subject": "owner",
                    },
                    {
                        "token": "fresh-access",
                        "client_id": "c1",
                        "scopes": [],
                        "expires_at": 5000,
                        "resource": None,
                        "subject": "owner",
                    },
                ],
                "refresh": [
                    {
                        "token": "old-refresh",
                        "client_id": "c1",
                        "scopes": [],
                        "expires_at": 500,
                        "resource": None,
                        "subject": "owner",
                    },
                    {
                        "token": "fresh-refresh",
                        "client_id": "c1",
                        "scopes": [],
                        "expires_at": 5000,
                        "resource": None,
                        "subject": "owner",
                    },
                ],
            }
        )
    )
    now = [1000.0]
    a = OwnerAuth(
        base_url="https://trene.example", password="hunter2", store=store, clock=lambda: now[0]
    )
    assert run(a.load_access_token("old-access")) is None
    assert run(a.load_access_token("fresh-access")) is not None
    assert run(a.load_refresh_token(client(), "old-refresh")) is None
    assert run(a.load_refresh_token(client(), "fresh-refresh")) is not None
