# Garmin workout push: handoff

Oct 24, 2026 · @Martijn

## Goal and status

The goal is a local Python library that reads a YAML training plan, converts it to Garmin Connect structured workouts, and schedules them on the Garmin calendar so they sync to the watch. An MCP server on top of the library lets Claude push plans directly. Later, the library and the server get published.

No code exists yet. The chat covered research and design only. Nothing was run against Garmin, because the sandbox used in the chat cannot reach Garmin's servers. Every Garmin API detail in this doc comes from memory or from third-party projects, and needs checking against a real account (see Import steps).

V1 scope is assumed to be running and cycling. Strength and HYROX sessions are left out because Garmin's step model handles them poorly. This was never confirmed, so change it if you disagree.

## Research findings

The hard part of this project is the Garmin connection, not the YAML or the MCP. Garmin has no public API for individuals, its login flow broke in March 2026, and a second blocker (Cloudflare) was reported after that. Everything in this section comes from search results read on 2026-10-24, except the Strava page, which was fetched in full.

**Garmin access**

- Garmin's official Training API is for approved business partners. Individuals have no supported way to create workouts, so every open-source tool calls the private endpoints that the Garmin Connect web app uses. Garmin can change them at any time. This point is from general knowledge and was not re-checked this session.
- In March 2026 Garmin changed its authentication. [garth](https://garth.readthedocs.io/) is now deprecated, and its docs say new logins no longer work. A session saved before the change may keep working until its OAuth1 token expires, about a year after issue.
- The current upstream [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) was reworked. A fork's README says it now logs in through the mobile SSO flow of the Android app and uses bearer tokens from diauth.garmin.com. It caches them in \~/.garminconnect/garmin\_tokens.json and refreshes them automatically. Read the upstream README to confirm, because the text I saw came from a fork.
- One project reports that Garmin added Cloudflare TLS fingerprinting that returns 403 to Python requests, Node fetch and curl. Its workaround is a headless Playwright browser with cookies from a manual login, and those cookies expire after a few hours. I could not tell whether this applies to the mobile-token endpoints that python-garminconnect uses. Test it first (Import steps, phase 1), because the answer decides the Garmin backend design.
- Garmin Connect only imports FIT files for completed activities, not for planned workouts. A workout has to be created through the web UI or the same private endpoint. Copying a workout FIT file to the watch over USB also works, but it skips the calendar. The USB route is from memory.

**Existing tools that push workouts to Garmin**

| Project | Approach | What to take from it |
| --- | --- | --- |
| [charlesfrisbee/garmin-workouts-mcp](https://glama.ai/mcp/servers/@charlesfrisbee/garmin-workouts-mcp) | TypeScript, installs with npx, creates workouts from natural language in Garmin Connect | Its workout JSON builder, as a reference for the payload |
| [brunosantos/garmin-workouts-mcp](https://www.remoteopenclaw.com/mcp/brunosantos/garmin-workouts-mcp) | 11 read and write tools, including upload\_workout, upload\_workouts and download\_workout (FIT) | Tool design, and a fuller payload reference |
| [slaat/garmin-connect-workouts-mcp](https://enterprisedna.co/directories/mcp/slaat-garmin-connect-workouts-mcp) | Pace, heart rate and cadence targets and repeat blocks, added September 2026 | A recent project that may handle the new auth |
| [kbaudour/garmin-claude-mcp](https://glama.ai/mcp/servers/f3bbqyezlc) and [jonas-theobald/garmin-mcp-bridge](https://glama.ai/mcp/servers/jonas-theobald/garmin-mcp-bridge) | Go through Intervals.icu, which syncs with Garmin Connect in both directions | Proof that the Intervals.icu route works without Garmin login |
| [etweisberg/garmin-connect-mcp](https://glama.ai/mcp/servers/mvarnoxhm5) | Playwright browser with captured cookies | The fallback if plain HTTP clients are blocked |

Several older Garmin MCP servers use garth, so new logins through them probably fail. Check each repo's last commit before reading its code.

**Alternatives that avoid the Garmin login**

- Intervals.icu has a documented API with a plain API key, and it uploads planned workouts to Garmin Connect. Community MCP servers for it exist ([mvilanova](https://github.com/mvilanova/intervals-mcp-server), [hhopke](https://claudemarketplaces.com/mcp/hhopke/intervals-icu-mcp)). This is the stable backend if the direct Garmin route stays fragile.
- TrainingPeaks syncs structured workouts to Garmin Connect up to 15 days ahead, per [its help page](https://help.trainingpeaks.com/hc/en-us/articles/115000325647). It needs a paid plan and has no Claude connection.
- The [Strava MCP connector](https://support.strava.com/en-us/articles/15401531-what-is-the-strava-mcp-connector) launched on June 1, 2026 for subscribers. It is read-only, so it can supply history and zones for plan design but cannot push anything.

## Decisions

Eight decisions are settled, one is proposed (packaging), one is assumed (sports), and three are open. The Garmin transport and the second backend wait for the phase 1 spike to show what Garmin allows.

| Topic | Decision | Reason | Status |
| --- | --- | --- | --- |
| Language | Python 3.11 or newer, managed with uv | pydantic, PyYAML and the official MCP SDK are all mature in Python | Decided |
| Source of truth | YAML plan files kept in git | A plan can be diffed and reviewed before it reaches the watch | Decided |
| Core design | Schema models and the compiler are pure functions with no network access | They can be unit-tested with snapshot tests, and nothing fragile is in them | Decided |
| Backend interface | A WorkoutBackend protocol with create, update, delete, schedule and list | The Garmin-facing code is the part most likely to break, so it stays replaceable | Decided |
| Credentials | Login happens only through the CLI, including MFA. The MCP server loads cached tokens and never sees a password | Keeps secrets out of the Claude config and out of the chat | Decided |
| MCP write safety | push\_plan defaults to dry\_run and needs an explicit confirm flag. Delete only touches workouts the tool created | A wrong plan from a model should not reach the calendar without a human check | Decided |
| Idempotency | A local state file maps plan id, date and workout name to the Garmin workout id | A second push updates instead of creating duplicates | Decided |
| Duration syntax | Time uses s, min and h. Distance uses m and km | 15m is ambiguous between minutes and meters | Decided |
| Packaging | One distribution with an optional mcp extra and two entry points, garminpush and garminpush-mcp | The core has no MCP dependency, and you wanted the option of bundling them | Proposed |
| Sports in v1 | Running and cycling | Garmin's step model covers them well. Strength and HYROX do not fit | Assumed, confirm |
| Garmin transport | Start with python-garminconnect behind the backend protocol. Alternatives are a small own client or Playwright cookies | Depends on whether plain HTTP clients are blocked | Open, decide after the spike |
| Second backend | Intervals.icu through its API key | Works without a Garmin login and is the fallback if the direct route fails | Open, build after the Garmin backend or sooner if the spike fails |
| Package name | Working name garminpush | Check PyPI availability before publishing | Open |

## Architecture

&#91;embedded content: architecture · CLI and MCP server over one core and swappable backends\]

The CLI and the MCP server call the same core functions. Only the backend layer makes network calls, so a Garmin change breaks one file and the compiler tests keep running offline. The Garmin and Intervals.icu backends differ only in where they send the compiled workout.

The MCP server exposes these tools.

| Tool | What it does | Writes to Garmin |
| --- | --- | --- |
| validate\_plan | Parses the YAML and returns errors with the field path | No |
| preview\_plan | Returns the compiled Garmin JSON for each workout | No |
| push\_plan | Creates or updates workouts and schedules them. Defaults to dry\_run and needs confirm to write | Yes |
| list\_pushed | Lists workouts the tool created, read from the state file | No |
| delete\_pushed | Deletes the workouts the tool created for one plan id | Yes |
| get\_garmin\_workout | Fetches one workout by id, for checking the compiler against real data | No |
| auth\_status | Reports whether the cached tokens are still valid | No |

## YAML schema draft

A plan file has an id, a list of workouts, and for each workout a date, a sport and a list of steps. The plan id feeds the idempotency key, so changing it makes the next push create new workouts.

```yaml
plan: base-block-1
workouts:
  - date: 2026-10-27
    name: Tempo 3x10
    sport: running
    steps:
      - warmup: {time: 15min, target: {hr_zone: 2}}
      - repeat: 3
        do:
          - interval: {time: 10min, target: {pace: "4:35-4:45/km"}}
          - recovery: {time: 3min}
      - cooldown: {time: 10min}

  - date: 2026-10-29
    name: Sweet spot 2x20
    sport: cycling
    steps:
      - warmup: {time: 15min, target: {power: 150-200w}}
      - repeat: 2
        do:
          - interval: {time: 20min, target: {power: 230-245w}}
          - recovery: {time: 5min, target: {power: 120-150w}}
      - cooldown: {time: 10min}
```

Rules for the first version:

- Step kinds are warmup, interval, recovery, rest, cooldown and repeat. A repeat holds a do list and may nest once.
- A step has either time (s, min, h), distance (m, km) or neither. Neither means a lap-button step that ends when the athlete presses lap.
- Target types are hr\_zone (1 to 5), hr (bpm range), pace (min:sec per km range), power (watts range), power\_zone (1 to 7) and cadence (rpm range). A step has at most one target. Pace is valid for running only. Power is valid for cycling only.
- Validation rejects a range where the low value exceeds the high value, a repeat count below 2, a date in the past, and a duration of zero.
- An optional notes field on a workout becomes the Garmin workout description.

Claude can fill HR zones, pace zones and FTP from the Strava connector's athlete zones tool, so zone targets can reference real numbers. Decide later whether the YAML should accept named zones that resolve at compile time or only absolute values. Absolute values are simpler and keep the compiler pure.

## Garmin workout JSON mapping

Nothing in this section is verified. The IDs and field names are from memory of the Garmin Connect web payloads and from third-party projects. Treat them as a starting hypothesis and replace them with fixtures captured from your own account (Import steps, phase 1).

| Garmin field | Values to expect |
| --- | --- |
| sportType.sportTypeId | running 1, cycling 2 |
| stepType.stepTypeId | warmup 1, cooldown 2, interval 3, recovery 4, rest 5, repeat 6 |
| endCondition.conditionTypeId | lap.button 1, time 2, distance 3, iterations 7 |
| targetType.workoutTargetTypeId | no.target 1, power.zone 2, cadence 3, heart.rate.zone 4, speed.zone 5, pace.zone 6 |
| endConditionValue | seconds for time, meters for distance |
| targetValueOne, targetValueTwo | bpm, watts or rpm directly. Pace and speed targets are meters per second |
| zoneNumber | the zone, used when the target is a zone rather than a range |

A minimal running workout with one warmup step looks like this.

```json
{
  "workoutName": "[plan:base-block-1] Tempo 3x10",
  "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
  "workoutSegments": [{
    "segmentOrder": 1,
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
    "workoutSteps": [{
      "type": "ExecutableStepDTO",
      "stepOrder": 1,
      "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
      "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
      "endConditionValue": 900,
      "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
      "zoneNumber": 2
    }]
  }]
}
```

A repeat is a step of type RepeatGroupDTO with stepTypeKey repeat, a numberOfIterations value, an iterations end condition, and its own nested workoutSteps list.

Workout endpoints, also unverified. The path prefix is /workout-service on the Garmin Connect API host.

| Action | Request |
| --- | --- |
| Create | POST /workout-service/workout |
| Update | PUT /workout-service/workout/{id} |
| Delete | DELETE /workout-service/workout/{id} |
| Read one | GET /workout-service/workout/{id} |
| List | GET /workout-service/workouts with start and limit |
| Schedule | POST /workout-service/schedule/{id} with body {"date": "YYYY-MM-DD"} |

Two details most likely to be wrong are which of targetValueOne and targetValueTwo holds the low end of a pace range, and whether a nested repeat needs extra fields. Settle both by building a workout in the Garmin web UI, fetching it with the read call, and copying its shape.

## Import steps

Work in five phases. Phase 1 is a go or no-go check on the Garmin connection and should take an hour or two. Do not build the compiler before it passes.

**Phase 0. Set up the project**

1. Install uv, then run `uv init --lib garminpush` and `cd garminpush`.
2. Add runtime dependencies with `uv add pydantic pyyaml typer garminconnect`.
3. Add development tools with `uv add --dev pytest ruff mypy`.
4. Add the MCP SDK later as an optional extra (phase 4). The core should import without it.
5. `Run git init and add` a `.gitignore` that excludes token files and the state file, and a `tests/fixtures/` folder.

**Phase 1. Spike the Garmin connection**

1. Log in with python-garminconnect. Run its example script once. It asks for the MFA code and stores tokens under \~/.garminconnect.
2. Call the list-workouts endpoint through the library. A JSON list means plain HTTP works. A 403 from Cloudflare means switching transport (decision table, Garmin transport).
3. In the Garmin Connect web UI, build one workout per step kind and per target type. Include one repeat and one nested repeat. Give each a distinct name.
4. Fetch each one with the read call and save the response to `tests/fixtures/`. These files are the ground truth for the compiler.
5. POST a minimal hand-written workout, then schedule it for tomorrow. Open the Garmin Connect app, sync the watch, and confirm the workout shows under the calendar and on the device.
6. Delete the test workouts through the API and confirm they disappear.

If steps 1 and 2 fail, stop and choose between the Playwright cookie route and the Intervals.icu backend before writing more code.

**Phase 2. Build the core with tests**

1. Write the pydantic models for the schema above, with the validation rules.
2. Write the compiler. Test it by comparing its output to the phase 1 fixtures, field by field.
3. Add property tests for duration and pace parsing, including the minutes versus meters case.

**Phase 3. Backend, state file and CLI**

1. Implement the WorkoutBackend protocol with a fake in-memory backend for tests and the Garmin backend for real use.
2. Add the state file at \~/.garminpush/state.json and the idempotent push logic.
3. Add the CLI commands `login`, `validate`, `preview`, `push --dry-run`, `push`, `list` and `delete`.
4. Run `garminpush push plan.yaml --dry-run`, then one workout for real, then a full plan.

**Phase 4. Add the MCP server**

1. Add the extra with `uv add "mcp[cli]" --optional mcp`, and create `garminpush/mcp_server.py` using FastMCP over stdio.
2. Expose the tools listed under Architecture. Each tool calls the same library functions as the CLI.
3. Register it in Claude Code with `claude mcp add garminpush -- uv --directory /path/to/garminpush run garminpush-mcp`.
4. For Claude Desktop, add an entry to claude\_desktop\_config.json with command uv and args `--directory`, the project path, `run`, `garminpush-mcp`.
5. Run `garminpush login` in a terminal before first use, because the MCP server cannot prompt for MFA.
6. In Claude, ask for a one-day plan with the preview tool first, then push with dry\_run on, then push for real.

**Phase 5. Plan with the Strava connector**

1. Connect the Strava connector in Claude, which is read-only.
2. Ask Claude to read recent activities and athlete zones, write a YAML plan with those zones, and call the MCP preview tool.
3. Review the YAML in git before pushing.

## Packaging and publishing plan

Publish one package with an optional MCP extra. The core library has no MCP dependency, so people who only want the CLI do not install the SDK, and people who want Claude integration run `pip install garminpush[mcp]`. A separate MCP package adds a second release process for little gain.

**Package layout**

```
src/garminpush/
  schema.py          pydantic models and parsing
  compiler.py        models to Garmin JSON, no network
  backends/
    base.py          WorkoutBackend protocol
    garmin.py        Garmin Connect backend
    intervals.py     Intervals.icu backend (later)
    fake.py          in-memory backend for tests
  state.py           local state file and idempotency
  cli.py             typer app, entry point garminpush
  mcp_server.py      FastMCP server, entry point garminpush-mcp
tests/
  fixtures/          Garmin responses captured in phase 1
```

**Before the first release**

1. Confirm the name is free with `pip index versions garminpush`, or pick another.
2. Choose a license. MIT is the usual choice and matches most projects in the research table.
3. Write the README with a clear notice that the project is unofficial, is not affiliated with Garmin, and uses private endpoints that may change. Read Garmin's Connect terms of use first and decide whether you are comfortable publishing a tool that automates them.
4. State the last date the live checks passed. This will go stale, so put it in the README and in the changelog.
5. Mark live tests with a pytest marker such as `live` and skip them by default. CI should run only the offline tests.
6. Keep the version at 0.x until the payloads have survived a few weeks of real use.

**Publishing to PyPI**

1. Build with `uv build` and check the files with `twine check`.
2. Upload to TestPyPI first and install from it in a clean environment.
3. Set up trusted publishing from GitHub Actions to PyPI, so no API token is stored.
4. Tag a release on GitHub. The workflow builds and publishes.

**Publishing the MCP server**

- The official MCP Registry takes a server.json file, and for a PyPI package it checks ownership through a line in the package README of the form `mcp-name: io.github.woutersm/garminpush`. The namespace comes from your GitHub account. This comes from memory of the registry docs, so read them at publish time. Publishing uses the `mcp-publisher` command line tool.
- Directories such as Glama and MCP Market index public GitHub projects on their own, and the research above shows several that already list similar servers. A good README with the tool list and a Claude Code install line is enough for them.
- Claude Code users install it with `claude mcp add garminpush -- uvx --from garminpush[mcp] garminpush-mcp`, which needs no manual clone.

## Next steps and open questions

The first action is the phase 1 spike. Its result decides which Garmin transport to use and whether the Intervals.icu backend comes first.

1. Run phase 1 on the local machine and note whether plain HTTP reaches the workout endpoints.
2. Save the captured workouts as fixtures and update the mapping table in this doc with the verified IDs.
3. Build the schema, compiler and tests (phase 2).
4. Add the backend, state file and CLI (phase 3).
5. Add the MCP server (phase 4) and test it with Claude Code.
6. Publish to TestPyPI, then PyPI, then the MCP registry (packaging section).

Open questions

- Are running and cycling enough for v1, or do you need strength or HYROX sessions? Strength needs exercise steps, which Garmin handles differently.
- Should the YAML accept named zones that resolve from your Strava zones, or only absolute values?
- If the direct Garmin route is blocked, do you prefer the Playwright cookie approach or the Intervals.icu backend?
- Is the package name garminpush acceptable, and which license do you want?
- Do you want to read Garmin's terms of use before deciding to publish?

## Sources

Checked on 2026-10-24. Only the Strava page was opened in full. The rest are search-result excerpts, so open the originals before relying on a detail.

- [Strava MCP connector help page](https://support.strava.com/en-us/articles/15401531-what-is-the-strava-mcp-connector), read in full.
- [garth documentation](https://garth.readthedocs.io/), deprecation notice and the note on saved sessions.
- [python-garminconnect issue 332](https://github.com/cyberjunky/python-garminconnect/issues/332), login failures after the March 2026 change.
- [python-garminconnect fork README](https://github.com/AlexandrChulisov/my-python-garminconnect), description of the new mobile SSO and bearer token flow.
- [etweisberg/garmin-connect-mcp](https://glama.ai/mcp/servers/mvarnoxhm5), report of Cloudflare TLS fingerprinting and the Playwright workaround.
- [charlesfrisbee/garmin-workouts-mcp](https://glama.ai/mcp/servers/@charlesfrisbee/garmin-workouts-mcp), natural language to Garmin workouts.
- [brunosantos/garmin-workouts-mcp](https://www.remoteopenclaw.com/mcp/brunosantos/garmin-workouts-mcp), tool list.
- [slaat/garmin-connect-workouts-mcp](https://enterprisedna.co/directories/mcp/slaat-garmin-connect-workouts-mcp), recent workout MCP with pace, HR and cadence targets.
- [Garmin Connect MCP server for strength training](https://glama.ai/mcp/servers/spdklsj8bh), note that Garmin Connect imports FIT files only for completed activities.
- [kbaudour/garmin-claude-mcp](https://glama.ai/mcp/servers/f3bbqyezlc) and [jonas-theobald/garmin-mcp-bridge](https://glama.ai/mcp/servers/jonas-theobald/garmin-mcp-bridge), Garmin workflows through Intervals.icu.
- [mvilanova/intervals-mcp-server](https://github.com/mvilanova/intervals-mcp-server) and [hhopke/intervals-icu-mcp](https://claudemarketplaces.com/mcp/hhopke/intervals-icu-mcp), community Intervals.icu servers.
- [TrainingPeaks help, structured workout sync](https://help.trainingpeaks.com/hc/en-us/articles/115000325647), 15-day Garmin sync window.
