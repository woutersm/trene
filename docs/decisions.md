
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
