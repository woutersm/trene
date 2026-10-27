# trene

Pushes JSON running plans to Garmin Connect as structured workouts, through a CLI (`trene`)
and an MCP server (`trene-mcp`). The README covers usage; this file covers working on it.

## Commands

```sh
uv run pytest -q              # the whole suite, offline, under a second
uv run ruff check src tests   # lint; --fix to apply
uv run trene <command>        # the CLI against the real account (see "Live checks")
```

## Layout

| Module | Job |
| --- | --- |
| `src/trene/schema.py` | pydantic models for the plan JSON, parsers for time, distance and pace, `PlanError` |
| `src/trene/compiler.py` | plan → Garmin workout JSON; `payload_hash` |
| `src/trene/backends/` | `WorkoutBackend` protocol, `FakeBackend` for tests, `garmin.py` over `garminconnect` |
| `src/trene/state.py` | `~/.trene/state.json` (what trene created) and saved plan copies |
| `src/trene/sync.py` | push, delete, list, get_plan: makes the Garmin calendar match a plan |
| `src/trene/cli.py` | typer app; thin wrappers |
| `src/trene/mcp_server.py` | `MCPServer` (mcp 2.x) tools; thin wrappers |
| `src/trene/planned.py` | pushed workouts as plain JSON for `/api/planned` (heim) |

## Rules

- Tests never touch the network or the real home folder: use `FakeBackend` and
  `monkeypatch.setenv("TRENE_HOME", str(tmp_path))`. Use 2099 dates for the future, 2020 for the past.
- `garminconnect` is imported only in `backends/garmin.py`.
- `schema.py` and `compiler.py` stay pure: no I/O, no `date.today()`; pass `today` in.
- The CLI and the MCP server call the same `sync` functions. A dry run uses `FakeBackend()`
  and never logs in.
- The MCP server raises `ToolError` for anything Claude should read; other exceptions reach
  Claude without their message. In mcp 2.x the class is `MCPServer`, not `FastMCP`.
- Plan field descriptions (`Field(description=...)`) are the MCP tool schema Claude reads;
  keep them plain and accurate.
- Commits read as the owner's: no Claude trailers or attribution lines.

## Garmin facts, verified on a real account

Captured workouts are in `tests/fixtures/` (personal data scrubbed). The raw spike files in
`spike/fixtures/` are gitignored.

- Login: `garminconnect==0.3.20` (needs Python 3.12) over plain HTTP; no Cloudflare block as of
  2026-10-24. Tokens in `~/.trene/garmin-tokens`.
- Step types: warmup 1, cooldown 2, interval 3, recovery 4, rest 5, repeat 6.
  End conditions: lap.button 1 (value null), time 2 (s), distance 3 (m), iterations 7.
- Targets: no.target 1, cadence 3, heart.rate.zone 4 (`zoneNumber` for a zone, or
  `targetValueOne/Two` for bpm), pace.zone 6 (m/s; `targetValueOne` is the faster end).
- Repeats are `RepeatGroupDTO`; `childStepId` numbers the groups 1, 2, ...; `stepOrder` counts
  depth first through the whole workout. Garmin's editor cannot nest repeats.
- Update (PUT) takes a freshly compiled payload, returns `{}`, and keeps the calendar entry.
  Deleting a workout removes its calendar entries.

## Live checks

Anything that writes to Garmin needs the owner's go-ahead first. A safe check: a one-workout
plan with id `trene-live-test` dated tomorrow, `trene push`, change a step, push again
(expect `update`), read it back with `get_backend().get(id)`, then `trene delete trene-live-test`.

## Server (Claude in the browser)

- `trene-mcp --http` serves MCP at `/mcp` with a single-owner OAuth login (`src/trene/auth.py`,
  `/login` page in `mcp_server.py`). Registration accepts only claude.ai / claude.com callbacks,
  loopback addresses, and the exact https callbacks listed in `TRENE_REDIRECT_URIS` (n8n Cloud's
  shared `https://oauth.n8n.cloud/oauth2/callback`). Scopes mean nothing: any requested scope is
  accepted (`AnyScopeClient`), because n8n asks for `openid` without registering it.
- `GET /api/planned` (`src/trene/planned.py`), only when `TRENE_API_TOKEN` is set: every pushed
  workout as JSON, read from state and the saved plans, never from Garmin. heim (`~/Dev/heim`,
  `docs/trene-design.md` there) reads it with the token as a bearer.
- Runs at `https://trene.wouters.dev` on the owner's VPS (`root@169.58.170.60`), as service
  `trene` in the `woutersm/wouters.dev` compose stack (local clone `~/Dev/wouters.dev/vps`), behind
  Traefik. `TRENE_PASSWORD` is in `/root/wouters.dev/.env` on the VPS; the `trene-data` volume at
  `/data` holds Garmin tokens, state, saved plans and `auth.json`.
- Pushing to `main` runs CI, which publishes `ghcr.io/woutersm/trene:latest`. Deploy:
  `ssh root@169.58.170.60 'cd /root/wouters.dev && docker compose pull trene && docker compose up -d trene'`.
- Use trene from one place: the server and a local `~/.trene` sharing Garmin tokens can log each
  other out.

## History

- `handoff.md`: the original research and decisions (its YAML schema and unverified mapping are
  superseded by the code).
- `docs/trene-design.md`: the styre design v1 was built from; `docs/decisions.md`: choices the
  builder made. `AGENTS.md` and `.styre/` belong to styre.
- Next design: automatic re-planning after a missed workout.
