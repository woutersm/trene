# trene

Put structured running workouts on your Garmin calendar from a JSON plan, by hand or through
Claude. Workouts sync to the watch like ones built in Garmin Connect.

> **Unofficial.** trene is not affiliated with Garmin. It uses the private endpoints of the
> Garmin Connect web app, which Garmin can change at any time.
> Last checked against a real account: **2026-10-25**.

## Install

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer (uv downloads it if needed).

```sh
git clone <this repo> trene && cd trene
uv sync --extra mcp
uv run trene login        # email, password and MFA code; only the CLI ever sees them
uv run trene status       # "Logged in as <your name>."
```

The login saves tokens in `~/.trene/garmin-tokens` and refreshes them on its own. Run
`trene login` again when a command says the login is missing or expired.

## A plan

```json
{
  "plan": "base-week-1",
  "goal": "Half marathon on 2027-02-14 in 1:35",
  "workouts": [
    {
      "date": "2026-11-03",
      "name": "Tempo 3x10",
      "notes": "Even effort.",
      "steps": [
        {"kind": "warmup", "time": "15min", "target": {"type": "hr_zone", "zone": 2}},
        {"kind": "repeat", "count": 3, "steps": [
          {"kind": "interval", "time": "10min", "target": {"type": "pace", "fast": "4:35", "slow": "4:45"}},
          {"kind": "recovery", "time": "3min"}
        ]},
        {"kind": "cooldown"}
      ]
    }
  ]
}
```

| Field | Values |
| --- | --- |
| `kind` | `warmup`, `interval`, `recovery`, `rest`, `cooldown`, `repeat` |
| `time` | `90s`, `10min`, `1h30min` (`15m` is rejected: `m` means meters) |
| `distance` | `400m`, `1.5km` |
| neither | the step ends when you press lap |
| `target` | `{"type": "hr_zone", "zone": 2}`, `{"type": "hr", "low": 130, "high": 145}`, `{"type": "pace", "fast": "4:35", "slow": "4:45"}` (per km), `{"type": "cadence", "low": 170, "high": 180}` |
| `repeat` | `count` 2-99 and a list of plain `steps`; repeats cannot nest (Garmin's editor can't either) |

Running only. Targets are absolute numbers, not named zones.

## Commands

```sh
trene validate plan.json          # check it; errors name the exact field
trene preview plan.json           # the Garmin JSON it sends
trene push plan.json --dry-run    # what would change, without contacting Garmin
trene push plan.json              # create, update and schedule
trene list [--plan ID]            # what trene has pushed
trene delete PLAN_ID [--dry-run]  # remove a plan's workouts from Garmin
```

How a push decides, per workout (a workout is known by plan id + date + name):

- new: created and put on the calendar
- changed: updated in place, calendar entry kept
- unchanged: nothing sent
- removed from the file: deleted from Garmin, calendar entry too
- dated before today: never touched, so rewriting a plan cannot change your history

trene only ever touches workouts it created. It keeps track of them in `~/.trene/state.json`
and saves a copy of every pushed plan in `~/.trene/plans/<plan-id>.json`. Set `TRENE_HOME` to
use another folder.

## Use it from Claude

`trene-mcp` is an MCP server over stdio with these tools: `validate_plan`, `preview_plan`,
`dry_run_plan`, `push_plan`, `list_pushed`, `get_plan`, `delete_pushed`, `get_garmin_workout`, `auth_status`.
Pushing and deleting are dry runs unless Claude passes `confirm=true`, which it is told to do
only after you approve the dry run. `dry_run_plan` can only preview, so a client can gate
`push_plan` behind its own approval step without gating dry runs.

Run `trene login` in a terminal first: the server cannot ask for an MFA code.

**Claude Code**

```sh
claude mcp add trene -- uv --directory /path/to/trene run --extra mcp trene-mcp
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "trene": {
      "command": "uv",
      "args": ["--directory", "/path/to/trene", "run", "--extra", "mcp", "trene-mcp"]
    }
  }
}
```

With the Strava connector also enabled, Claude can read your recent runs and zones, write a
plan, show the dry run, and push it after you say yes. To change a plan later, ask Claude: it
calls `get_plan`, edits it, and pushes again under the same plan id.

## Run it on a server (Claude in the browser)

Claude in the browser connects to a public HTTPS address, so trene runs on a server as a
dockerized HTTP service. Set in your compose environment:

- `TRENE_URL` — the public address, e.g. `https://trene.wouters.dev`.
- `TRENE_PASSWORD` — the password for the login page (12+ characters).
- `TRENE_PORT` — the port the server listens on, default `8000`.
- `TRENE_REDIRECT_URIS` — optional, comma separated: more OAuth callback addresses the login
  accepts besides claude.ai and loopback. For n8n Cloud that is
  `https://oauth.n8n.cloud/oauth2/callback` (shared by all n8n Cloud accounts, like claude.ai's;
  the password is still required).

`deploy/compose.yaml` is the service block for the VPS stack (behind Traefik). Your state
lives in the `trene-data` volume mounted at `/data`: fill it either with

```sh
docker compose run --rm -it trene trene login
```

or by copying a working `~/.trene` folder into the volume.

To connect: claude.ai → Settings → Connectors → Add custom connector → URL
`https://trene.wouters.dev/mcp`, then type the password on the page that opens.

## Develop

```sh
uv run pytest -q              # offline; never contacts Garmin
uv run ruff check src tests
```

Garmin responses captured from a real account are in `tests/fixtures/`; the compiler is tested
against them field by field.

## License

MIT
