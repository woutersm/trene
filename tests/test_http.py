"""Tests for the HTTP mode of src/trene/mcp_server.py (trene-mcp --http)."""

import base64
import hashlib
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from trene import mcp_server
from trene.mcp_server import create_http_app, main

BASE = "https://trene.example"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PASSWORD = "correct horse battery"

TOOL_NAMES = [
    "validate_plan",
    "preview_plan",
    "push_plan",
    "list_pushed",
    "get_plan",
    "delete_pushed",
    "get_garmin_workout",
    "auth_status",
]


def _client(tmp_path: Path) -> TestClient:
    app = create_http_app(BASE, PASSWORD, tmp_path)
    return TestClient(app, base_url=BASE, follow_redirects=False)


def _register(c: TestClient) -> dict:
    return c.post(
        "/register",
        json={"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"},
    ).json()


def _verifier_and_challenge() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _login_and_get_token(
    c: TestClient, reg: dict, password: str = PASSWORD
) -> tuple[str, str]:
    """Run the owner login and the code exchange; returns (code, access_token)."""
    verifier, challenge = _verifier_and_challenge()
    a = c.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": reg["client_id"],
            "redirect_uri": CALLBACK,
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    login = urlparse(a.headers["location"])
    assert login.scheme == "https"
    assert login.netloc == "trene.example"
    assert login.path == "/login"
    request_id = parse_qs(login.query)["request"][0]
    assert request_id
    ok = c.post(
        f"{login.path}?{login.query}", data={"password": password}
    )
    location = ok.headers.get("location")
    if location is None:
        pytest.fail(f"expected a redirect, got {ok.status_code}: {ok.text[:200]}")
    code = parse_qs(urlparse(location).query)["code"][0]
    tok = c.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": reg["client_id"],
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
        },
    ).json()
    return code, tok["access_token"]


def _mcp_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json, text/event-stream",
    }


def test_full_flow(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        # Unauthenticated /mcp is 401 with the protected-resource header.
        r = c.post("/mcp", json={})
        assert r.status_code == 401
        assert "/.well-known/oauth-protected-resource/mcp" in r.headers[
            "www-authenticate"
        ]

        reg = _register(c)
        assert "client_id" in reg

        _, access_token = _login_and_get_token(c, reg)

        h = _mcp_headers(access_token)
        r = c.post(
            "/mcp",
            headers=h,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        assert r.status_code == 200
        h["mcp-session-id"] = r.headers["mcp-session-id"]
        c.post(
            "/mcp",
            headers=h,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )
        r = c.post(
            "/mcp",
            headers=h,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert r.status_code == 200
        for name in TOOL_NAMES:
            assert name in r.text


def test_wrong_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("trene.mcp_server.asyncio.sleep", fake_sleep)
    with _client(tmp_path) as c:
        reg = _register(c)
        verifier, challenge = _verifier_and_challenge()
        a = c.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": reg["client_id"],
                "redirect_uri": CALLBACK,
                "state": "s",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        login = urlparse(a.headers["location"])
        response = c.post(
            f"{login.path}?{login.query}", data={"password": "nope nope nope"}
        )
    assert response.status_code == 401
    assert "Wrong password." in response.text
    assert "location" not in response.headers
    assert sleeps == [1]


def test_unknown_request_id(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        response = c.post(
            "/login", params={"request": "not-a-real-request"}, data={"password": PASSWORD}
        )
        assert response.status_code == 400
        assert response.text
        expired = c.get("/login", params={"request": "not-a-real-request"})
        assert expired.status_code == 400
        assert "expired" in expired.text.lower()


def test_bad_host_refused(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        reg = _register(c)
        _, access_token = _login_and_get_token(c, reg)
        h = _mcp_headers(access_token)
        h["Host"] = "evil.example"
        r = c.post("/mcp", headers=h, json={})
        assert r.status_code in (400, 421)


def test_health_without_token(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True}


def test_token_survives_restart(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        reg = _register(c)
        _, access_token = _login_and_get_token(c, reg)
    with _client(tmp_path) as c2:
        r = c2.post(
            "/mcp",
            headers=_mcp_headers(access_token),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        assert r.status_code == 200


@pytest.fixture
def http_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> pytest.MonkeyPatch:
    monkeypatch.setenv("TRENE_URL", BASE)
    monkeypatch.setenv("TRENE_PASSWORD", PASSWORD)
    monkeypatch.setenv("TRENE_HOME", str(tmp_path / "home"))
    return monkeypatch


def test_main_http_default_port(
    http_env: pytest.MonkeyPatch,
) -> None:
    http_env.delenv("TRENE_PORT", raising=False)
    calls: list[dict] = []
    http_env.setattr(mcp_server.uvicorn, "run", lambda app, **kw: calls.append(kw))
    http_env.setattr(sys, "argv", ["trene-mcp", "--http"])
    main()
    assert calls == [{"host": "0.0.0.0", "port": 8000}]


def test_main_http_custom_port(http_env: pytest.MonkeyPatch) -> None:
    http_env.setenv("TRENE_PORT", "8443")
    calls: list[dict] = []
    http_env.setattr(mcp_server.uvicorn, "run", lambda app, **kw: calls.append(kw))
    http_env.setattr(sys, "argv", ["trene-mcp", "--http"])
    main()
    assert calls[0]["port"] == 8443


@pytest.mark.parametrize(
    ("env", "expected_error"),
    [
        ({"TRENE_PASSWORD": PASSWORD}, "TRENE_URL"),
        ({"TRENE_URL": BASE}, "TRENE_PASSWORD"),
        ({"TRENE_URL": BASE, "TRENE_PASSWORD": "too-short"}, "TRENE_PASSWORD"),
        (
            {"TRENE_URL": "http://public.example", "TRENE_PASSWORD": PASSWORD},
            "TRENE_URL",
        ),
    ],
)
def test_main_http_invalid_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    env: dict[str, str],
    expected_error: str,
) -> None:
    for name in ("TRENE_URL", "TRENE_PASSWORD", "TRENE_PORT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TRENE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "argv", ["trene-mcp", "--http"])
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 2
    assert expected_error in capsys.readouterr().err
