
## Phase 1: The plan schema

- Cross-field errors (time+distance, low>high, fast/slow, duplicate workouts) are raised
  with `pydantic_core.ValidationError.from_exception_data`, using the `ctx` key in line
  errors (not `context` — that key is silently ignored and pydantic 2.14 raises
  `TypeError: 'error' required in context`). This keeps the error's `loc` pointing at the
  offending field(s) when it bubbles into `Plan`; a plain `ValueError` from a model
  validator would collapse the path to the model itself.
- Discriminator union errors carry the resolved tag in their loc: a step that fails its
  `time`/`distance`/target checks reports as `workouts.0.steps.0.interval.<field>` (and a
  repeat-inside-repeat as `...repeat.steps.0.kind`), because `Step|Repeat`/`Target` are
  `Field(discriminator=...)` unions. Test expectations match these locs.
- `from __future__ import annotations` is on in `schema.py`, so pydantic cannot resolve
  module-level names for `Field(pattern=...)`: the plan-name pattern is inlined, and
  `date` is imported as `datetime as dt` because the `Workout.date` field would otherwise
  shadow the type.

## Phase 2: The compiler

`compiler.py` maps the plan's `kind`/target strings to Garmin's integer ids with two small
lookup helpers; the repeat group gets a per-workout `childStepId` (1, 2, ...) that its
children inherit, and `stepOrder` is a single depth-first counter as specified. A step with
neither `time` nor `distance` ends on `lap.button` (condition 1) with `endConditionValue`
`None`, matching the `lap` fixture. The `lap` fixture was captured with
`skipLastRestStep: False`, so its test pops that key from both sides before comparing, as the
design prescribed. All four fixture files from Phase 1 are the source of truth; the compiler
emits only the documented keys, and the test's `trim` helper (copied verbatim from the
design) bridges to the fixture files.

## Phase 3: Backends

- `garmin.py` centralizes all error translation in one `_translate(fn)` wrapper (garminconnect is imported only here); every `GarminBackend` method and both module functions run their client call through it, so the auth/not-found/connection mappings are written exactly once.
- `__init__.py` re-exports `FakeBackend` and the protocol but never imports `garmin.py`, keeping `garminconnect` out of `sys.modules` for library-only use; this is pinned by a subprocess test.
- `FakeBackend.fail_on` is a plain set (not a method) so Phase 4 can toggle failures mid-push; schedule removal on `delete` filters the `schedules` dict since Garmin drops a workout's calendar entries with the workout.

## Phase 4: State and sync

- `state.json` has a trailing newline and is written via `state.json.tmp` + `os.replace`; `plans/*.json` copies are exactly `plan.model_dump_json(indent=2, exclude_none=True)` with no trailing newline, per the spec.
- `push` rule 1 (past) deliberately applies to workouts in the file, so a changed step on a past date is reported `past`, never `update`; leftover state entries of the plan that are past are kept, not deleted.
- `delete_plan` calls `load_plan_copy` up front (so a missing plan raises `PlanError`) even in a dry run; it does not re-save the plan copy afterwards.
- `Action.workout_id` is `None` only for `create` in a dry run (the backend was never called); all other ops carry the id.

## Phase 5: The CLI

- **Auth token path** (undecided before this phase): `get_backend()` calls
  `from_tokens(trene_home() / "garmin-tokens")`, so tokens live in trene's home folder
  (`$TRENE_HOME` or `~/.trene`) rather than garminconnect's default `~/.garminconnect`.
  `trene login` saves to the same path, so a login and a subsequent run always agree.
- The CLI imports `from_tokens` and `login_interactive` into its own namespace
  (`from trene.backends.garmin import ...`) only so tests can monkeypatch
  `trene.cli.login_interactive`; `garminconnect` is still imported in exactly one module.
- The `push` sync function is imported as `push_sync` because the typer command is named
  `push`; they are distinct objects in `trene.cli`.
- Errors map to exit codes: `PlanError` -> one line per problem, exit 1;
  `BackendError` (incl. `AuthError`) -> `Garmin error: <message>`, exit 2. A dry-run
  `push`/`delete` passes a `FakeBackend()` and never calls `get_backend()`, so a dry run
  works with no Garmin login.

## Phase 6: The MCP server

- The MCP tool `list_pushed` collides in name with the library function `trene.sync.list_pushed`; the library import is aliased `list_pushed_entries` (one alias per `from trene.sync import` line to satisfy ruff I001).
- `get_plan` reuses `trene.sync.get_plan` aliased `load_saved_plan`; its `PlanError` (missing plan copy) is wrapped into `ToolError`, as is any `PlanError`/`BackendError` from `push_plan`/`delete_pushed`, so Claude sees the message rather than a generic "Error executing tool".
- The `confirm` guard runs before any backend/state work, so `push_plan(dry_run=False)` without `confirm` never creates `state.json`.
