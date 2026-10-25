# trene remote

Make trene's MCP server reachable from Claude in the browser. claude.ai connects from
Anthropic's servers to a public HTTPS address (a "custom connector"), so `trene-mcp` gets an
HTTP mode, a single-user OAuth login that claude.ai can complete, and a Docker image that runs
on the owner's VPS behind Traefik at `https://trene.wouters.dev`. The stdio mode for Claude Code
and Claude Desktop keeps working unchanged.

## Facts every phase needs

**Stack, already installed.** Python 3.12, uv, `mcp` 2.3.0 (brings `starlette` 1.10 and
`uvicorn` 0.54), pydantic 2, pytest, ruff, and `httpx` in the dev group (Starlette's
`TestClient` needs it). Add no dependencies and do not edit `pyproject.toml`.

**What exists.** `src/trene/mcp_server.py` defines eight tools on a module-level
`server = MCPServer("trene", instructions=INSTRUCTIONS)` with `@server.tool()` decorators, and
`main()` runs `server.run()` over stdio. The tools call `trene.sync`, `trene.compiler` and the
backend from `get_backend()`. `trene.state.trene_home()` returns `$TRENE_HOME` or `~/.trene`.
`tests/test_mcp_server.py` imports `server` and the tool functions from `trene.mcp_server`, calls
the functions directly, and patches `trene.mcp_server.get_backend`. Those tests must keep passing
unchanged.

**How claude.ai logs in** (OAuth 2.1 with PKCE and dynamic client registration). The `mcp`
library implements every endpoint below; trene supplies the provider object and the password page:

1. `POST /mcp` without a token → `401` with a `WWW-Authenticate` header pointing at
   `/.well-known/oauth-protected-resource/mcp`.
2. claude.ai reads that and `/.well-known/oauth-authorization-server`, then `POST /register`s
   itself (redirect URI `https://claude.ai/api/mcp/auth_callback`).
3. It sends the owner's browser to `GET /authorize?...`. The library calls
   `provider.authorize(client, params)`, which returns trene's own `/login?request=<id>` URL.
4. The owner types the password on `/login`. On the right password, trene creates an
   authorization code and redirects to claude.ai's callback with `code` and `state`.
5. claude.ai `POST /token`s the code with its PKCE verifier and gets an access token and a
   refresh token. Every later `/mcp` call carries `Authorization: Bearer <access token>`.

**Rules.** No test opens a network port or touches the real home folder: HTTP tests use
`starlette.testclient.TestClient`, and anything that writes files uses `tmp_path`. Async provider
methods are called in tests with `asyncio.run(...)` (there is no pytest-asyncio). Type hints on
every function. Run `uv run ruff check --fix src tests` before the gate.

## Phase 1: The owner login provider

Build `src/trene/auth.py`: an OAuth authorization-server provider for exactly one person, the
owner, who proves who they are with a password. No HTTP code in this phase; Phase 2 wires it in.

```python
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams,
                                      RefreshToken, construct_redirect_uri)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

ACCESS_TTL = 3600              # seconds
REFRESH_TTL = 90 * 24 * 3600
CODE_TTL = 300
REQUEST_TTL = 600

class OwnerAuth:
    def __init__(self, base_url: str, password: str, store: Path, clock: Callable[[], float] = time.time) -> None
    # The provider protocol the mcp library calls (all async):
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None
    async def register_client(self, client_info: OAuthClientInformationFull) -> None
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str
    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None
    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken
    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None
    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken
    async def load_access_token(self, token: str) -> AccessToken | None
    async def revoke_token(self, token: AccessToken | RefreshToken) -> None
    # For the /login page (sync):
    def has_request(self, request_id: str) -> bool
    def approve(self, request_id: str, password: str) -> str | None
```

How each one behaves:

- `authorize` stores `(client.client_id, params, clock() + REQUEST_TTL)` under
  `request_id = secrets.token_urlsafe(16)` and returns `f"{base_url}/login?request={request_id}"`.
- `has_request` is true for a stored request that has not expired.
- `approve` returns `None` for a wrong password (compare with
  `hmac.compare_digest(password.encode(), self._password.encode())`) and leaves the request in
  place so the owner can retry. It raises `KeyError` for an unknown or expired request. On the
  right password it removes the request, stores a new `AuthorizationCode` (code
  `secrets.token_urlsafe(32)`, `expires_at=clock() + CODE_TTL`, the request's `scopes or []`,
  `code_challenge`, `redirect_uri`, `redirect_uri_provided_explicitly`, `resource`, and
  `subject="owner"`), and returns
  `construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)`.
- `load_authorization_code` returns the stored code, or `None` when unknown, expired, or issued to
  another client. `exchange_authorization_code` deletes the code and issues tokens.
- Issuing tokens: a new access token (`secrets.token_urlsafe(32)`, `expires_at=int(clock()) + ACCESS_TTL`)
  and refresh token (`expires_at=int(clock()) + REFRESH_TTL`), both with the client id, scopes,
  `resource` and `subject="owner"`. Return
  `OAuthToken(access_token=..., token_type="Bearer", expires_in=ACCESS_TTL, refresh_token=..., scope=" ".join(scopes) or None)`.
- `load_refresh_token` returns `None` when unknown, expired, or for another client.
  `exchange_refresh_token` deletes the old refresh token and issues a new pair (rotation),
  using `scopes or refresh_token.scopes`.
- `load_access_token` returns `None` when unknown or expired.
- `revoke_token` removes that token from whichever table holds it; unknown tokens are ignored.

**Persistence.** Registered clients, access tokens and refresh tokens survive a restart, so
claude.ai stays connected when the container is replaced. After every change to them, write
`store` (a JSON file; create its folder) through a temporary file and `os.replace`:

```json
{"version": 1,
 "clients": [<client.model_dump(mode="json", exclude_none=True)>],
 "access": [<AccessToken.model_dump(mode="json")>],
 "refresh": [<RefreshToken.model_dump(mode="json")>]}
```

`__init__` loads the file when it exists (`OAuthClientInformationFull.model_validate(...)`,
`AccessToken.model_validate(...)`, `RefreshToken.model_validate(...)`) and drops expired tokens.
Pending login requests and authorization codes live in memory only; they last minutes.

In tests, build a client with
`OAuthClientInformationFull(client_id="c1", redirect_uris=["https://claude.ai/api/mcp/auth_callback"])`
and params with
`AuthorizationParams(state="s", scopes=None, code_challenge="x" * 43, redirect_uri="https://claude.ai/api/mcp/auth_callback", redirect_uri_provided_explicitly=True)`.
Drive expiry with a fake clock: `now = [1000.0]` and `clock=lambda: now[0]`, then `now[0] += 301`.

Where:
- `src/trene/auth.py`: new
- `tests/test_auth.py`: new

### Exit criteria
- `authorize` returns `https://trene.example/login?request=<id>` for `base_url="https://trene.example"`,
  and `has_request(<id>)` is true until `REQUEST_TTL` passes.
- `approve` with a wrong password returns `None` and the request still exists; with the right one
  it returns a URL starting with `https://claude.ai/api/mcp/auth_callback?` whose query has `code`
  and `state=s`; a second `approve` of the same request raises `KeyError`.
- The code exchanges once for an `OAuthToken` with `token_type == "Bearer"`; afterwards
  `load_authorization_code` returns `None`, and it also returns `None` after `CODE_TTL`.
- `load_access_token` returns the token, then `None` after `ACCESS_TTL`.
- Exchanging a refresh token returns a new pair, and the old refresh token then loads as `None`.
- A second `OwnerAuth` on the same `store` file knows the registered client and the access and
  refresh tokens issued by the first.
- `revoke_token` makes `load_access_token` return `None`.
- Nothing outside `src/trene/auth.py` and `tests/test_auth.py` is edited.

### Gate
```
uv run ruff check src tests
uv run pytest -q
```

## Phase 2: The HTTP server

Give `trene-mcp` an HTTP mode with the owner login, in `src/trene/mcp_server.py`, without
changing how the stdio mode or the existing tests behave.

Restructure the module:
- Keep the eight tool functions as plain module-level functions with the same names, signatures,
  docstrings and behaviour, but without the `@server.tool()` decorator.
- `TOOLS = [validate_plan, preview_plan, push_plan, list_pushed, get_plan, delete_pushed, get_garmin_workout, auth_status]`.
- `build_server(auth: OwnerAuth | None = None, base_url: str | None = None) -> MCPServer` creates
  the server and calls `srv.add_tool(fn)` for each of `TOOLS`. With `auth`, it passes
  `auth_server_provider=auth` and these settings; without, it creates the server exactly as now:

```python
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

MCPServer("trene", instructions=INSTRUCTIONS, auth_server_provider=auth, auth=AuthSettings(
    issuer_url=base_url,
    resource_server_url=f"{base_url}/mcp",
    validate_token_resource=False,
    client_registration_options=ClientRegistrationOptions(enabled=True),
    revocation_options=RevocationOptions(enabled=True),
))
```

- `server = build_server()` stays at module level, so `from trene.mcp_server import server` and the
  stdio mode work as before.
- `create_http_app(base_url: str, password: str, home: Path) -> Starlette`:
  `auth = OwnerAuth(base_url, password, home / "auth.json")`, `srv = build_server(auth, base_url)`,
  then two routes on `srv`, then the app:

```python
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

@srv.custom_route("/login", methods=["GET", "POST"])
async def login(request: Request) -> Response: ...

@srv.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return JSONResponse({"ok": True})

return srv.streamable_http_app(
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(allowed_hosts=[urlparse(base_url).netloc]),
)
```

- `/login`: read `request` from the query string. Unknown or expired (`not auth.has_request(...)`)
  → HTML "This login link has expired. Start the connection again from Claude." with status 400.
  `GET` → the form. `POST` → `await request.form()`, then `auth.approve(request_id, password)`:
  a URL → `RedirectResponse(url, status_code=302)`; `None` → `await asyncio.sleep(1)` (slows
  guessing) and the form again with "Wrong password." and status 401. The form is a small
  self-contained HTML page: title "Connect trene", one sentence ("Claude wants to use trene on
  your Garmin calendar."), a `password` input named `password`, a "Connect" button, inline CSS,
  no external files. The form posts back to the same URL including `?request=...`.
- `main()`: with no arguments, `server.run()` (stdio) as now. With `--http`, read the environment,
  print a one-line error and `sys.exit(2)` when a value is missing or invalid, otherwise run
  `uvicorn.run(create_http_app(url, password, trene_home()), host="0.0.0.0", port=port)`:
  - `TRENE_URL`: required; must start with `https://` (or `http://127.0.0.1` / `http://localhost`
    for local tests); a trailing `/` is stripped.
  - `TRENE_PASSWORD`: required; at least 12 characters.
  - `TRENE_PORT`: optional, default `8000`.

Test the whole flow claude.ai runs, in-process. This was checked against the installed `mcp`;
the `with` block matters, because it starts the app's session manager:

```python
import base64, hashlib, secrets
from urllib.parse import parse_qs, urlparse
from starlette.testclient import TestClient

BASE = "https://trene.example"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"

app = create_http_app(BASE, "correct horse battery", tmp_path)
with TestClient(app, base_url=BASE, follow_redirects=False) as c:
    assert c.post("/mcp", json={}).status_code == 401
    reg = c.post("/register", json={"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}).json()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    a = c.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": CALLBACK,
                                    "state": "s", "code_challenge": challenge, "code_challenge_method": "S256"})
    login = urlparse(a.headers["location"])              # https://trene.example/login?request=...
    ok = c.post(f"{login.path}?{login.query}", data={"password": "correct horse battery"})
    code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
    tok = c.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                 "redirect_uri": CALLBACK, "code_verifier": verifier}).json()
    h = {"Authorization": f"Bearer {tok['access_token']}", "Accept": "application/json, text/event-stream"}
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
    h["mcp-session-id"] = r.headers["mcp-session-id"]
    c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    # r.text is an SSE body: "event: message\ndata: {...json...}"; the tool names are in it
```

Patch `asyncio.sleep` in `trene.mcp_server` (`monkeypatch.setattr("trene.mcp_server.asyncio.sleep", fake)`)
in the wrong-password test so it does not wait. Test `main()` by setting the environment with
`monkeypatch`, patching `trene.mcp_server.uvicorn.run` to record its arguments, and setting
`sys.argv` to `["trene-mcp", "--http"]`.

Where:
- `src/trene/mcp_server.py` 1-144: the module to restructure; tools at 44-136, `server` at 30,
  `main` at 139-140
- `src/trene/auth.py` (Phase 1): `OwnerAuth`. Import; do not edit.
- `tests/test_http.py`: new
- `tests/test_mcp_server.py`: must pass unchanged; do not edit

### Exit criteria
- The flow above passes: unauthenticated `/mcp` is 401, the login redirect goes to
  `https://trene.example/login?request=`, and `tools/list` returns all eight tool names.
- A wrong password on `/login` returns 401 with "Wrong password." and no redirect; `/login` with an
  unknown request id returns 400.
- `/mcp` with a valid token but `Host: evil.example` is refused (status 400 or 421).
- `GET /health` returns `{"ok": true}` without a token.
- After the flow, a second `create_http_app` on the same `tmp_path` accepts the first app's access
  token on `/mcp` (initialize returns 200).
- `main()` with `--http` calls `uvicorn.run` with port 8000 by default and `TRENE_PORT` when set; it
  exits with code 2 when `TRENE_URL` or `TRENE_PASSWORD` is missing, when the password is shorter
  than 12 characters, or when `TRENE_URL` is `http://` on a public host.
- `tests/test_mcp_server.py` is unchanged and passes.
- Nothing outside `src/trene/mcp_server.py` and `tests/test_http.py` is edited.

### Gate
```
uv run ruff check src tests
uv run pytest -q
```

## Phase 3: Docker image and CI

Package trene as a Docker image and publish it from GitHub Actions, like the owner's other
service on the same VPS. Then document running it.

Create `Dockerfile` with exactly this content (it was built and run once; `TZ` matters, because
"today" decides which workouts count as past):

```dockerfile
FROM ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --extra mcp --no-install-project
COPY src ./src
RUN uv sync --locked --extra mcp
ENV PATH="/app/.venv/bin:$PATH" TRENE_HOME=/data TZ=Europe/Amsterdam
VOLUME /data
EXPOSE 8000
CMD ["trene-mcp", "--http"]
```

Create `.dockerignore` with one entry per line: `.venv`, `.git`, `.styre`, `.ruff_cache`,
`.pytest_cache`, `__pycache__`, `spike`, `tests`, `docs`.

Create `.github/workflows/ci.yml`: on push to `main` and on pull requests; `permissions:
contents: read`. Job `test` on `ubuntu-latest`: `actions/checkout@v5`, `astral-sh/setup-uv@v6`,
`uv sync --locked`, `uv run ruff check src tests`, `uv run pytest -q`. Job `publish`, only for
pushes to `main` (`if: github.event_name == 'push' && github.ref == 'refs/heads/main'`),
`needs: [test]`, with `permissions: contents: read, packages: write`:

```yaml
      - uses: actions/checkout@v5
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64
          push: true
          tags: |
            ghcr.io/woutersm/trene:latest
            ghcr.io/woutersm/trene:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Create `deploy/compose.yaml`: the service block for the VPS stack, which already runs Traefik with
a certificate resolver named `myresolver` and an entrypoint `websecure`:

```yaml
services:
  trene:
    image: ghcr.io/woutersm/trene:latest
    container_name: trene
    restart: unless-stopped
    environment:
      TRENE_URL: https://trene.wouters.dev
      TRENE_PASSWORD: ${TRENE_PASSWORD:?set TRENE_PASSWORD in .env}
    volumes:
      - trene-data:/data
    labels:
      - traefik.enable=true
      - traefik.http.routers.trene.rule=Host(`trene.wouters.dev`)
      - traefik.http.routers.trene.entrypoints=websecure
      - traefik.http.routers.trene.tls.certresolver=myresolver
      - traefik.http.services.trene.loadbalancer.server.port=8000

volumes:
  trene-data:
```

Add a section "## Run it on a server (Claude in the browser)" to `README.md`, directly before
"## Develop", with: what it is for (claude.ai connects to a public HTTPS address, so trene runs on
a server); the environment variables `TRENE_URL`, `TRENE_PASSWORD` (12+ characters), `TRENE_PORT`;
that `deploy/compose.yaml` is the service block; that Garmin tokens and state live in the `/data`
volume, filled either by `docker compose run --rm -it trene trene login` or by copying a working
`~/.trene` folder into the volume; and how to connect: claude.ai → Settings → Connectors → Add
custom connector → URL `https://trene.wouters.dev/mcp`, then type the password on the page that
opens. Keep it under 40 lines.

Where:
- `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`, `deploy/compose.yaml`: new
- `README.md`: the line `## Develop` (search for it); add the new section above it

### Exit criteria
- `docker build -t trene:test .` succeeds, and `docker run --rm trene:test trene --help` lists the
  `push` command.
- `docker run --rm trene:test python -c "import datetime; print(datetime.datetime.now().astimezone().tzname())"`
  prints `CET` or `CEST`.
- `docker run --rm trene:test trene-mcp --http` exits with code 2 and an error naming `TRENE_URL`.
- `.github/workflows/ci.yml` and `deploy/compose.yaml` parse as YAML: the gate's `mikefarah/yq`
  command exits 0 on them (PyYAML is not installed, so do not check with Python).
- The README section names all three environment variables and the URL `https://trene.wouters.dev/mcp`.
- Nothing outside the five files under Where is edited.

### Gate
```
uv run ruff check src tests
uv run pytest -q
docker build -q -t trene:test .
docker run --rm trene:test trene --help
sh -c 'docker run --rm trene:test trene-mcp --http; test $? -eq 2'
docker run --rm -v "$PWD:/w" -w /w mikefarah/yq:4 '.' .github/workflows/ci.yml deploy/compose.yaml
```
