
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
