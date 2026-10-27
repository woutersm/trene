"""The `trene-mcp` server: an MCP server for Claude, exposing the same library
functions as the CLI as tools. Runs over stdio by default, or over HTTP with a
single-owner OAuth login for claude.ai (`trene-mcp --http`)."""

from __future__ import annotations

import asyncio
import hmac
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, urlparse

import uvicorn
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp

from trene.auth import OwnerAuth
from trene.backends import BackendError, FakeBackend, WorkoutBackend
from trene.backends.garmin import from_tokens
from trene.compiler import compile_plan
from trene.planned import planned
from trene.schema import Plan, PlanError
from trene.state import trene_home
from trene.sync import delete_plan
from trene.sync import get_plan as load_saved_plan
from trene.sync import list_pushed as list_pushed_entries
from trene.sync import push as push_sync

INSTRUCTIONS = (
    "trene puts running workouts on the user's Garmin calendar. Build the plan as "
    "JSON, call validate_plan, then dry_run_plan and show the user the actions. "
    "Call push_plan with dry_run=false and confirm=true only after "
    "the user agrees. To change a plan, call get_plan, edit it, and push it again "
    "under the same plan id: changed workouts are updated, removed ones deleted, "
    "and workouts dated before today are never touched."
)

# Some MCP clients (n8n) pass the model only the top level of a tool's schema, so the
# plan format also goes into the description of every tool that takes a plan.
PLAN_FORMAT = """Plan format (JSON):
{"plan": "base-week-1", "goal": "optional text", "workouts": [
  {"date": "2026-11-03", "name": "Tempo 3x10", "notes": "optional", "steps": [
    {"kind": "warmup", "time": "15min", "target": {"type": "hr_zone", "zone": 2}},
    {"kind": "repeat", "count": 3, "steps": [
      {"kind": "interval", "time": "10min",
       "target": {"type": "pace", "fast": "4:35", "slow": "4:45"}},
      {"kind": "recovery", "time": "3min"}]},
    {"kind": "cooldown"}]}]}
- kind: warmup, interval, recovery, rest, cooldown, or repeat (count 2-99, plain steps, no nesting).
- time: "90s", "10min", "1h30min" ("15m" is 15 meters). distance: "400m", "1.5km".
  Neither: the step ends on the lap button.
- target (optional): {"type": "pace", "fast": "4:35", "slow": "4:45"} per km,
  {"type": "hr_zone", "zone": 2}, {"type": "hr", "low": 130, "high": 145},
  {"type": "cadence", "low": 170, "high": 180}.
- No other fields."""


def get_backend() -> WorkoutBackend:
    return from_tokens(trene_home() / "garmin-tokens")


def _write_guarded(dry_run: bool, confirm: bool) -> None:
    if not dry_run and not confirm:
        raise ToolError(
            "Writing to Garmin needs confirm=true. Show the user the dry run first."
        )


def validate_plan(plan: Plan) -> dict:
    """Check a plan and summarise it before doing anything else."""
    today = date.today()
    return {
        "ok": True,
        "plan": plan.plan,
        "workouts": len(plan.workouts),
        "first": min(w.date for w in plan.workouts).isoformat(),
        "last": max(w.date for w in plan.workouts).isoformat(),
        "past": sorted(
            w.name for w in plan.workouts if w.date < today
        ),
    }


def preview_plan(plan: Plan) -> list[dict]:
    """Show the Garmin payloads a plan compiles to, without pushing."""
    return compile_plan(plan)


def push_plan(
    plan: Plan, dry_run: bool = True, confirm: bool = False
) -> dict:
    """Push a plan to the Garmin calendar; dry_run previews the actions."""
    _write_guarded(dry_run, confirm)
    backend = FakeBackend() if dry_run else get_backend()
    try:
        return push_sync(
            plan, backend, trene_home(), date.today(), dry_run=dry_run
        ).to_dict()
    except (PlanError, BackendError) as e:
        raise ToolError(str(e)) from e


def dry_run_plan(plan: Plan) -> dict:
    """Show what pushing a plan would create, update or delete, without contacting Garmin."""
    return push_plan(plan, dry_run=True)


def list_pushed(plan_id: str | None = None) -> list[dict]:
    """List the workouts trene has pushed, optionally for one plan."""
    return [
        {
            "plan": entry.plan,
            "date": entry.date.isoformat(),
            "name": entry.name,
            "workout_id": entry.workout_id,
        }
        for entry in list_pushed_entries(trene_home(), plan_id)
    ]


def get_plan(plan_id: str) -> dict:
    """Fetch a saved plan so it can be edited and re-pushed."""
    try:
        return load_saved_plan(plan_id, trene_home()).model_dump(
            mode="json", exclude_none=True
        )
    except PlanError as e:
        raise ToolError(str(e)) from e


def delete_pushed(
    plan_id: str, dry_run: bool = True, confirm: bool = False
) -> dict:
    """Delete all of a saved plan's workouts from the Garmin calendar."""
    _write_guarded(dry_run, confirm)
    backend = FakeBackend() if dry_run else get_backend()
    try:
        return delete_plan(
            plan_id, backend, trene_home(), date.today(), dry_run=dry_run
        ).to_dict()
    except (PlanError, BackendError) as e:
        raise ToolError(str(e)) from e


def get_garmin_workout(workout_id: int) -> dict:
    """Fetch one workout as it is stored on Garmin."""
    try:
        return get_backend().get(workout_id)
    except BackendError as e:
        raise ToolError(str(e)) from e


def auth_status() -> dict:
    """Show the name of the Garmin account currently logged in."""
    try:
        return {"ok": True, "name": get_backend().whoami()}
    except BackendError as e:
        return {"ok": False, "error": str(e)}


TOOLS = [
    validate_plan,
    preview_plan,
    dry_run_plan,
    push_plan,
    list_pushed,
    get_plan,
    delete_pushed,
    get_garmin_workout,
    auth_status,
]


def inline_refs(schema: dict) -> dict:
    """Replace every `$ref` with its definition and drop `$defs`.

    Some MCP clients (n8n) drop `$defs`, so the model saw `plan` as a bare object and
    guessed its fields. Pydantic's `discriminator` mapping points into `$defs` too; it is
    an OpenAPI hint, and the `const` on each variant's tag already says the same.
    """
    defs = schema.get("$defs", {})

    def resolve(node: object) -> object:
        if isinstance(node, dict):
            if "$ref" in node:
                target = resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
                rest = {k: resolve(v) for k, v in node.items() if k != "$ref"}
                return {**target, **rest}
            return {
                k: resolve(v) for k, v in node.items() if k not in ("$defs", "discriminator")
            }
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def _tools() -> list[Tool]:
    tools = []
    for fn in TOOLS:
        tool = Tool.from_function(fn)
        tool.parameters = inline_refs(tool.parameters)
        if "plan" in tool.parameters.get("properties", {}):
            tool.description = f"{tool.description}\n\n{PLAN_FORMAT}"
        tools.append(tool)
    return tools


def build_server(
    auth: OwnerAuth | None = None, base_url: str | None = None
) -> MCPServer:
    """Create the trene MCPServer, with OAuth when `auth` is given."""
    if auth is None:
        srv = MCPServer("trene", instructions=INSTRUCTIONS, tools=_tools())
    else:
        srv = MCPServer(
            "trene",
            instructions=INSTRUCTIONS,
            tools=_tools(),
            auth_server_provider=auth,
            auth=AuthSettings(
                issuer_url=base_url,
                resource_server_url=f"{base_url}/mcp",
                validate_token_resource=False,
                client_registration_options=ClientRegistrationOptions(enabled=True),
                revocation_options=RevocationOptions(enabled=True),
            ),
        )
    return srv


server = build_server()


def _login_form(request_id: str, error: str | None) -> str:
    error_html = f'<p style="color:#c0392b">{error}</p>' if error else ""
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect trene</title></head>
<body style="font-family:system-ui,sans-serif;display:flex;justify-content:center;
max-width:360px;margin:4rem auto;padding:0 16px">
<form method="post" action="/login?{urlencode({"request": request_id})}">
<h1>Connect trene</h1>
<p>Claude wants to use trene on your Garmin calendar.</p>
{error_html}
<label>
<input type="password" name="password" style="width:100%;padding:.5rem;
box-sizing:border-box" autofocus>
</label>
<button type="submit" style="padding:.5rem 1rem;margin-top:1rem">Connect</button>
</form>
</body>
</html>"""


EXPIRED_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<title>Connect trene</title></head><body style='font-family:system-ui,"
    "sans-serif;margin:4rem auto'>"
    "<h1>This login link has expired. Start the connection again from Claude.</h1>"
    "</body></html>"
)


def create_http_app(
    base_url: str,
    password: str,
    home: Path,
    extra_redirects: tuple[str, ...] = (),
    api_token: str | None = None,
) -> ASGIApp:
    """The Starlette app for `trene-mcp --http`. With `api_token`, `/api/planned` serves
    the pushed workouts to a reader holding that token (heim)."""
    base_url = base_url.rstrip("/")
    auth = OwnerAuth(base_url, password, home / "auth.json", extra_redirects=extra_redirects)
    srv = build_server(auth, base_url)

    @srv.custom_route("/login", methods=["GET", "POST"])
    async def login(request: Request) -> Response:
        request_id = request.query_params.get("request", "")
        if not auth.has_request(request_id):
            return HTMLResponse(EXPIRED_HTML, status_code=400)
        if request.method == "GET":
            return HTMLResponse(_login_form(request_id, None), status_code=200)
        form = await request.form()
        password_value = str(form.get("password", ""))
        try:
            url = auth.approve(request_id, password_value)
        except KeyError:
            return HTMLResponse(EXPIRED_HTML, status_code=400)
        if url is None:
            await asyncio.sleep(1)
            return HTMLResponse(
                _login_form(request_id, "Wrong password."), status_code=401
            )
        return RedirectResponse(url, status_code=302)

    @srv.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True})

    if api_token:
        expected = f"Bearer {api_token}".encode()

        @srv.custom_route("/api/planned", methods=["GET"])
        async def api_planned(request: Request) -> Response:
            given = request.headers.get("authorization", "").encode()
            if not hmac.compare_digest(given, expected):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return JSONResponse(planned(home))

    return srv.streamable_http_app(
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(
            allowed_hosts=[urlparse(base_url).netloc]
        ),
    )


def main() -> None:
    if "--http" not in sys.argv[1:]:
        server.run()
        return
    url = os.environ.get("TRENE_URL")
    password = os.environ.get("TRENE_PASSWORD")
    port_raw = os.environ.get("TRENE_PORT")
    if url is None:
        print("TRENE_URL is required (https://...)", file=sys.stderr)
        sys.exit(2)
    url = url.rstrip("/")
    if not (
        url.startswith("https://")
        or url.startswith("http://127.0.0.1")
        or url.startswith("http://localhost")
    ):
        print(
            "TRENE_URL must start with https:// (or http://127.0.0.1 / "
            "http://localhost for local tests)",
            file=sys.stderr,
        )
        sys.exit(2)
    if password is None or len(password) < 12:
        print("TRENE_PASSWORD must be set and at least 12 characters", file=sys.stderr)
        sys.exit(2)
    try:
        port = int(port_raw) if port_raw else 8000
    except ValueError:
        print(f"Invalid TRENE_PORT: {port_raw!r}", file=sys.stderr)
        sys.exit(2)
    # More OAuth callbacks the login may send codes to, e.g. an n8n instance's.
    raw = os.environ.get("TRENE_REDIRECT_URIS", "")
    extra = tuple(u.strip() for u in raw.split(",") if u.strip())
    bad = [u for u in extra if not u.startswith("https://")]
    if bad:
        print(f"TRENE_REDIRECT_URIS entries must start with https://: {bad[0]}", file=sys.stderr)
        sys.exit(2)
    # The read-only token for /api/planned (heim); unset, the route does not exist.
    api_token = os.environ.get("TRENE_API_TOKEN") or None
    if api_token is not None and len(api_token) < 24:
        print("TRENE_API_TOKEN must be at least 24 characters", file=sys.stderr)
        sys.exit(2)
    uvicorn.run(
        create_http_app(url, password, trene_home(), extra, api_token),
        host="0.0.0.0",
        port=port,
    )


if __name__ == "__main__":
    main()
