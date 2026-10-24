# trene v1

trene turns a JSON running plan into Garmin Connect structured workouts and puts them on the
Garmin calendar, so they sync to the watch. People use it through a CLI (`trene`) and Claude
uses it through an MCP server (`trene-mcp`). Both call the same library functions.

## Facts every phase needs

**Stack, already installed.** Python 3.12, uv, pydantic 2, typer 0.27, `garminconnect==0.3.20`,
and `mcp` 2.x (in the dev group and the `mcp` extra). pytest and ruff for checks. Add no
dependencies and do not edit `pyproject.toml`: the entry points `trene = "trene.cli:app"` and
`trene-mcp = "trene.mcp_server:main"` are already declared there.

**Layout.** Source in `src/trene/`, tests in `tests/` (a package: `tests/__init__.py` exists),
Garmin responses captured from a real account in `tests/fixtures/*.json`. Import as
`from trene.schema import Plan`.

**Rules for all code.**
- No test touches the network or the real home folder. Tests that need a backend use
  `FakeBackend` (Phase 3). Tests that need trene's home folder set
  `monkeypatch.setenv("TRENE_HOME", str(tmp_path))`.
- `garminconnect` is imported in exactly one module: `src/trene/backends/garmin.py`.
- `schema.py` and `compiler.py` are pure: no file writes, no network, no `date.today()`.
  Functions that need today's date take it as a `today: date` argument.
- Dates in tests: use 2099 for the future and 2020 for the past, so tests never go stale.
- Type hints on every function. Run `uv run ruff check --fix src tests` before the gate.

**The plan format.** A plan is JSON. Each step has a `kind`. A step ends on `time`, on
`distance`, or, with neither, when the runner presses the lap button. A step has at most one
`target`. Repeats hold plain steps only; a repeat inside a repeat is invalid.

```json
{
  "plan": "base-week-1",
  "goal": "Half marathon on 2027-02-14 in 1:35",
  "workouts": [
    {
      "date": "2099-01-05",
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
    },
    {
      "date": "2099-01-07",
      "name": "Easy 8k",
      "steps": [
        {"kind": "interval", "distance": "8km", "target": {"type": "hr", "low": 130, "high": 145}}
      ]
    }
  ]
}
```

Step kinds: `warmup`, `interval`, `recovery`, `rest`, `cooldown`, `repeat`. Target types:
`hr_zone` (`zone` 1-5), `hr` (`low`/`high` bpm), `pace` (`fast`/`slow` as `m:ss` per km),
`cadence` (`low`/`high` steps per minute). Times use `s`, `min`, `h` (`90s`, `10min`, `1h30min`).
Distances use `m` and `km` (`400m`, `1.5km`). `15m` is a distance, never a time.

**The gate** for every phase is the same two commands. The second one fails with "no tests
ran" until Phase 1 adds tests; that is expected.

## Phase 1: The plan schema

Build `src/trene/schema.py`: the pydantic models for the plan format above, the three
parsers, and loading. No compiler, no files besides the ones listed under Where.

```python
class PlanError(ValueError):
    """A plan that fails validation. `errors` holds one line per problem."""
    def __init__(self, errors: list[str]) -> None: ...
    errors: list[str]          # e.g. "workouts.0.steps.1.time: Value error, '15m' is not a time ..."
    # str(err) is the lines joined with "\n"

def parse_time(text: str) -> int        # seconds
def parse_distance(text: str) -> float  # meters
def parse_pace(text: str) -> int        # seconds per km

class HrZoneTarget(BaseModel):  type: Literal["hr_zone"]; zone: int        # 1..5
class HrTarget(BaseModel):      type: Literal["hr"]; low: int; high: int   # bpm, 30..250
class PaceTarget(BaseModel):    type: Literal["pace"]; fast: str; slow: str
class CadenceTarget(BaseModel): type: Literal["cadence"]; low: int; high: int  # 60..250
Target = Annotated[HrZoneTarget | HrTarget | PaceTarget | CadenceTarget, Field(discriminator="type")]

class Step(BaseModel):
    kind: Literal["warmup", "interval", "recovery", "rest", "cooldown"]
    time: str | None = None
    distance: str | None = None
    target: Target | None = None

class Repeat(BaseModel):
    kind: Literal["repeat"]
    count: int                 # 2..99
    steps: list[Step]          # at least 1; Step only, so repeats cannot nest

WorkoutStep = Annotated[Step | Repeat, Field(discriminator="kind")]

class Workout(BaseModel):
    date: date
    name: str                  # 1..80 characters after stripping whitespace
    notes: str | None = None
    steps: list[WorkoutStep]   # at least 1

class Plan(BaseModel):
    plan: str                  # pattern ^[a-z0-9][a-z0-9-]{0,63}$
    goal: str | None = None
    workouts: list[Workout]    # at least 1

def parse_plan(data: str | dict) -> Plan   # JSON text or a dict; raises PlanError
def load_plan(path: Path) -> Plan          # reads the file; missing file or bad JSON -> PlanError
```

Rules:
- Every model uses `model_config = ConfigDict(extra="forbid")`, so a misspelled field is an
  error. Every field has `Field(description=...)` in plain words, because Claude reads these
  descriptions from the MCP tool schema. Example: `time: str | None = Field(None,
  description="How long the step lasts, like 90s, 10min or 1h30min. Leave out time and distance for a lap-button step.")`.
- Keep `time`, `distance`, `fast` and `slow` as the strings given. A `field_validator` checks
  each one by calling its parser, so the plan dumps back exactly as it came in.
- `parse_time`: regex `^(?:(\d+)h)?(?:(\d+)min)?(?:(\d+)s)?$`, at least one part, total > 0.
  For text ending in `m` such as `15m`, the error says: `'15m' is not a time: use 15min for minutes (m means meters)`.
- `parse_distance`: regex `^(\d+(?:\.\d+)?)(m|km)$`, total > 0. `1.5km` returns `1500.0`.
- `parse_pace`: regex `^(\d{1,2}):([0-5]\d)(?:/km)?$`. `4:35` and `4:35/km` both return `275`.
- A `Step` with both `time` and `distance` is an error. `low > high` is an error for `hr` and
  `cadence`. A `pace` whose `fast` is slower than `slow` (more seconds per km) is an error.
- Two workouts in one plan with the same `date` and `name` are an error: that pair is the key
  later phases use to recognise a workout.
- `PlanError` lines come from `ValidationError.errors()`: `".".join(str(p) for p in e["loc"])`,
  then `": "`, then `e["msg"]`. For a missing file: `"<path>: no such file"`. For bad JSON:
  `"<path>: not valid JSON: <reason>"`.
- Replace the body of `src/trene/__init__.py` (the `hello()` function) with `__version__ = "0.1.0"`.

Also create `tests/plans/week.json` with exactly the JSON from the preamble. Later phases'
tests load it.

Where:
- `src/trene/schema.py`: new
- `src/trene/__init__.py` 1-2: `hello()`, to be replaced
- `tests/test_schema.py`: new
- `tests/plans/week.json`: new

### Exit criteria
- `load_plan(Path("tests/plans/week.json"))` returns a `Plan` with 2 workouts, and the first
  workout's second step is a `Repeat` with `count == 3`.
- `parse_time` returns 90 for `90s`, 600 for `10min`, 5400 for `1h30min`; it raises
  `ValueError` for `15m`, `0min`, `""` and `10 min`, and the `15m` message contains `use 15min`.
- `parse_distance("1.5km") == 1500.0`, `parse_distance("400m") == 400.0`; `10min` raises.
- `parse_pace("4:35") == 275 == parse_pace("4:35/km")`; `4:75` raises.
- Tests show a `PlanError` (with a line naming the field path) for each of: a repeat inside a
  repeat, a misspelled field (`tme`), time and distance together, `hr` low above high, a pace
  with fast slower than slow, a repeat with `count` 1, and a duplicate date+name pair.
- `Plan.model_validate_json(plan.model_dump_json(exclude_none=True))` equals the original plan.
- Nothing outside the four files under Where is edited.

### Gate
```
uv run ruff check src tests
uv run pytest -q
```

## Phase 2: The compiler

Build `src/trene/compiler.py`: it turns a `Workout` into the JSON body that Garmin's create and
update calls take. Pure functions only.

```python
RUNNING = {"sportTypeId": 1, "sportTypeKey": "running"}

def compile_workout(workout: Workout, plan_id: str) -> dict
def compile_plan(plan: Plan) -> list[dict]      # one payload per workout, same order
def payload_hash(payload: dict) -> str          # sha256 hex of json.dumps(payload, sort_keys=True)
```

The payload:

```python
{
    "workoutName": workout.name,
    "description": "\n\n".join(x for x in [workout.notes, f"trene plan {plan_id}"] if x),
    "sportType": RUNNING,
    "workoutSegments": [{"segmentOrder": 1, "sportType": RUNNING, "workoutSteps": [...]}],
}
```

A plain step. Every key is always present, with `None` where it does not apply:

```python
{
    "type": "ExecutableStepDTO",
    "stepOrder": n,
    "childStepId": None,           # or the repeat group's number when inside a repeat
    "stepType": {"stepTypeId": ..., "stepTypeKey": kind},
    "endCondition": {"conditionTypeId": ..., "conditionTypeKey": ...},
    "endConditionValue": ...,      # float seconds, float meters, or None for lap button
    "targetType": {"workoutTargetTypeId": ..., "workoutTargetTypeKey": ...},
    "targetValueOne": ..., "targetValueTwo": ..., "zoneNumber": ...,
}
```

A repeat:

```python
{
    "type": "RepeatGroupDTO",
    "stepOrder": n,
    "childStepId": g,              # 1 for the workout's first repeat, 2 for the second, ...
    "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
    "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
    "endConditionValue": float(count),
    "numberOfIterations": count,
    "skipLastRestStep": True,
    "smartRepeat": False,
    "workoutSteps": [...],         # its steps, each with childStepId = g
}
```

The ids, checked against the fixtures:

| what | value |
| --- | --- |
| stepTypeId | warmup 1, cooldown 2, interval 3, recovery 4, rest 5, repeat 6 |
| endCondition | lap.button 1 (value `None`), time 2 (value seconds as float), distance 3 (value meters as float), iterations 7 |
| no target | `{"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}`, values and zone `None` |
| `hr_zone` | type 4 `heart.rate.zone`, `zoneNumber` = zone, values `None` |
| `hr` | type 4 `heart.rate.zone`, `targetValueOne` = float(low), `targetValueTwo` = float(high), `zoneNumber` `None` |
| `cadence` | type 3 `cadence`, `targetValueOne` = float(low), `targetValueTwo` = float(high) |
| `pace` | type 6 `pace.zone`, `targetValueOne` = 1000 / parse_pace(fast), `targetValueTwo` = 1000 / parse_pace(slow) (meters per second; One is the faster end) |

`stepOrder` counts 1, 2, 3, ... depth first through the whole workout: a repeat takes the
next number, then its children take the numbers after it, then the step after the repeat
continues from there. Example: warmup 1, repeat 2, its interval 3, its recovery 4, cooldown 5.

Test against the fixtures with this helper (copy it into the test file as is; you do not need
to open the fixture files):

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
STEP_KEYS = ["type", "stepOrder", "childStepId", "stepType", "endCondition", "endConditionValue",
             "targetType", "targetValueOne", "targetValueTwo", "zoneNumber"]
REPEAT_KEYS = ["type", "stepOrder", "childStepId", "stepType", "endCondition", "endConditionValue",
               "numberOfIterations", "skipLastRestStep", "smartRepeat"]
ID_KEYS = {"stepType": ("stepTypeId", "stepTypeKey"),
           "endCondition": ("conditionTypeId", "conditionTypeKey"),
           "targetType": ("workoutTargetTypeId", "workoutTargetTypeKey")}

def trim(step: dict) -> dict:
    repeat = step["type"] == "RepeatGroupDTO"
    out = {}
    for k in REPEAT_KEYS if repeat else STEP_KEYS:
        v = step.get(k)
        if k in ID_KEYS:
            v = {kk: v[kk] for kk in ID_KEYS[k]}
        if isinstance(v, float):
            v = round(v, 4)
        out[k] = v
    if repeat:
        out["workoutSteps"] = [trim(s) for s in step["workoutSteps"]]
    return out

def fixture_steps(name: str) -> list[dict]:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return [trim(s) for s in data["workoutSegments"][0]["workoutSteps"]]
```

Each fixture was built from these steps (as JSON step lists; wrap each in a `Workout` dated
2099-01-05). The test asserts `[trim(s) for s in payload["workoutSegments"][0]["workoutSteps"]] == fixture_steps(name)`:

- `steps`: `[{"kind":"warmup","time":"10min","target":{"type":"hr_zone","zone":2}}, {"kind":"interval","distance":"1km","target":{"type":"pace","fast":"4:35","slow":"4:45"}}, {"kind":"rest","time":"1min"}, {"kind":"cooldown","time":"5min"}]`
- `targets`: `[{"kind":"interval","time":"5min","target":{"type":"hr","low":130,"high":150}}, {"kind":"interval","time":"5min","target":{"type":"cadence","low":170,"high":180}}, {"kind":"interval","distance":"2km"}]`
- `repeats`: `[{"kind":"warmup","time":"10min"}, {"kind":"repeat","count":3,"steps":[{"kind":"interval","distance":"400m"},{"kind":"recovery","time":"90s"}]}, {"kind":"cooldown","time":"10min"}]`
- `lap`: `[{"kind":"interval"}, {"kind":"repeat","count":2,"steps":[{"kind":"interval","time":"60s"},{"kind":"recovery"}]}, {"kind":"repeat","count":2,"steps":[{"kind":"interval","time":"30s"},{"kind":"recovery","time":"30s"}]}]`.
  This fixture was created with `skipLastRestStep: False`, so for `lap` only, pop
  `skipLastRestStep` from every repeat on both sides before comparing.

Where:
- `src/trene/compiler.py`: new
- `src/trene/schema.py`: `Workout`, `Step`, `Repeat`, the targets, `parse_time`, `parse_distance`,
  `parse_pace` from Phase 1. Import them; do not edit the file.
- `tests/test_compiler.py`: new
- `tests/fixtures/steps.json`, `targets.json`, `repeats.json`, `lap.json`: read by the helper above

### Exit criteria
- The four fixture tests pass with the `trim` helper exactly as given.
- A test checks the payload's top level: `workoutName`, `sportType == RUNNING`, one segment with
  `segmentOrder` 1, and `description == "Even effort.\n\ntrene plan base-week-1"` for the first
  workout of `tests/plans/week.json` (and `"trene plan base-week-1"` alone when notes is missing).
- `compile_plan` on `tests/plans/week.json` returns 2 payloads in plan order.
- `payload_hash` is the same for two equal payloads and differs when one step's time changes.
- Nothing outside `src/trene/compiler.py` and `tests/test_compiler.py` is edited.

### Gate
```
uv run ruff check src tests
uv run pytest -q
```

## Phase 3: Backends

Build the backend layer: a protocol, an in-memory fake for tests, and the Garmin backend over
`garminconnect`. This is the only phase that imports `garminconnect`.

`src/trene/backends/base.py`:

```python
class BackendError(Exception): ...
class AuthError(BackendError): ...
class NotFoundError(BackendError): ...

class WorkoutBackend(Protocol):
    def create(self, payload: dict) -> int: ...                 # returns the workout id
    def update(self, workout_id: int, payload: dict) -> None: ...
    def delete(self, workout_id: int) -> None: ...               # also removes its calendar entries
    def schedule(self, workout_id: int, day: date) -> int: ...   # returns the schedule id
    def get(self, workout_id: int) -> dict: ...
    def whoami(self) -> str: ...                                 # account name; proves the login works
```

`src/trene/backends/fake.py`, `class FakeBackend`:
- `workouts: dict[int, dict]`, `schedules: dict[int, tuple[int, date]]` (schedule id ->
  (workout id, day)), `calls: list[tuple]` (one entry per call, like `("create", 1001)`,
  `("update", 1001)`, `("delete", 1001)`, `("schedule", 1001, date(2099, 1, 5))`).
- Workout ids count from 1001, schedule ids from 5001.
- `update`, `delete`, `schedule` and `get` on an unknown id raise `NotFoundError`. `delete`
  also drops that workout's schedules, as Garmin does.
- `fail_on: set[str]`, empty by default: calling a method named in it raises
  `BackendError("fake failure")`. Phase 4 uses this to test a push that fails halfway.
- `whoami()` returns `"Fake Runner"`.

`src/trene/backends/garmin.py`:

```python
from pathlib import Path
from garminconnect import (Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError,
                           GarminConnectNotFoundError, GarminConnectTooManyRequestsError)

DEFAULT_TOKENS = Path("~/.garminconnect").expanduser()

class GarminBackend:
    def __init__(self, client: Garmin) -> None: ...
    # create:   int(client.upload_workout(payload)["workoutId"])
    # update:   client.update_workout(workout_id, payload)        returns {} on success
    # delete:   client.delete_workout(workout_id)
    # schedule: int(client.schedule_workout(workout_id, day.isoformat())["workoutScheduleId"])
    # get:      client.get_workout_by_id(workout_id)
    # whoami:   client.get_full_name() or "unknown"

def from_tokens(path: Path = DEFAULT_TOKENS) -> GarminBackend:
    # client = Garmin(); client.login(str(path)); return GarminBackend(client)

def login_interactive(email: str, password: str, prompt_mfa: Callable[[], str],
                      path: Path = DEFAULT_TOKENS) -> str:
    # path.mkdir(mode=0o700, parents=True, exist_ok=True)
    # client = Garmin(email, password, prompt_mfa=prompt_mfa); client.login(str(path))
    # return client.get_full_name() or "unknown"
```

These calls and return shapes were checked against a real account. `client.login(path)` with no
saved tokens raises `GarminConnectAuthenticationError("Username and password are required")`.
Every method and both functions translate errors the same way:
`GarminConnectAuthenticationError` -> `AuthError("Garmin login missing or expired: run `trene login`")`,
`GarminConnectNotFoundError` -> `NotFoundError(str(e))`,
`GarminConnectConnectionError` and `GarminConnectTooManyRequestsError` -> `BackendError(str(e))`.
Put that mapping in one helper (a context manager or a wrapper) rather than repeating it.

`src/trene/backends/__init__.py` re-exports `WorkoutBackend`, `BackendError`, `AuthError`,
`NotFoundError` and `FakeBackend`. It does not import `garmin.py`, so importing the package never
loads `garminconnect`.

Test `GarminBackend` with a stub client, never the real one. The stub's `upload_workout` returns
`json.loads((FIXTURES / "create-response.json").read_text())` (its `workoutId` is `1739502191`) and
`schedule_workout` returns the parsed `schedule-response.json` (its `workoutScheduleId` is
`1818472172`). Test `from_tokens` and `login_interactive` with
`monkeypatch.setattr("trene.backends.garmin.Garmin", StubGarmin)`, where `StubGarmin`'s
constructor accepts the same arguments and records them.

Where:
- `src/trene/backends/__init__.py`, `base.py`, `fake.py`, `garmin.py`: new
- `tests/test_backends.py`: new
- `tests/fixtures/create-response.json`, `schedule-response.json`: read by the stub

### Exit criteria
- `GarminBackend(stub).create({...}) == 1739502191` and `.schedule(1739502191, date(2099, 1, 5)) == 1818472172`,
  and the stub saw `"2099-01-05"` as the date string.
- A stub that raises `GarminConnectAuthenticationError` makes `create` raise `AuthError` whose
  message contains `trene login`; `GarminConnectNotFoundError` makes `get` raise `NotFoundError`.
- `from_tokens(tmp_path)` with a stub whose `login` raises the auth error raises `AuthError`.
- `login_interactive` passes email, password and `prompt_mfa` to the constructor, calls `login`
  with `str(path)`, creates the folder, and returns the stub's full name.
- `FakeBackend` tests: ids start at 1001 and 5001, `delete` drops schedules, unknown ids raise
  `NotFoundError`, `fail_on={"update"}` makes `update` raise `BackendError`.
- `python -c "import trene.backends, sys; assert 'garminconnect' not in sys.modules"` passes
  (write it as a test using `subprocess` and `sys.executable`).
- Nothing outside the five new files under Where is edited.

### Gate
```
uv run ruff check src tests
uv run pytest -q
```

## Phase 4: State and sync

Build the logic that makes the Garmin calendar match a plan: the local state file in
`src/trene/state.py`, and push, delete, list and get-plan in `src/trene/sync.py`. Test it only
with `FakeBackend`.

`src/trene/state.py`:

```python
def trene_home() -> Path   # Path(os.environ.get("TRENE_HOME") or "~/.trene").expanduser()

@dataclass
class Entry:
    plan: str
    date: date
    name: str
    workout_id: int
    schedule_id: int
    payload_hash: str

@dataclass
class State:
    entries: list[Entry]
    @classmethod
    def load(cls, home: Path) -> "State"     # home/"state.json"; a missing file is an empty State
    def save(self, home: Path) -> None       # write home/"state.json.tmp", then os.replace; mkdir home first
    def find(self, plan: str, day: date, name: str) -> Entry | None
    def for_plan(self, plan: str) -> list[Entry]

def save_plan_copy(plan: Plan, home: Path) -> Path   # home/"plans"/f"{plan.plan}.json", plan.model_dump_json(indent=2, exclude_none=True)
def load_plan_copy(plan_id: str, home: Path) -> Plan # missing -> PlanError([f"no saved plan '{plan_id}'"])
```

`state.json` looks like this (dates as ISO strings):

```json
{"version": 1, "workouts": [{"plan": "base-week-1", "date": "2099-01-05", "name": "Tempo 3x10",
  "workout_id": 1001, "schedule_id": 5001, "payload_hash": "3f2a..."}]}
```

`src/trene/sync.py`:

```python
Op = Literal["create", "update", "unchanged", "delete", "past"]

@dataclass(frozen=True)
class Action:
    op: Op
    date: date
    name: str
    workout_id: int | None    # None for a create in a dry run

@dataclass
class Report:
    plan: str
    dry_run: bool
    actions: list[Action]
    def counts(self) -> dict[str, int]   # every Op as a key, 0 when absent
    def to_dict(self) -> dict            # {"plan", "dry_run", "actions": [{"op", "date" (ISO), "name", "workout_id"}], "counts"}

def push(plan: Plan, backend: WorkoutBackend, home: Path, today: date, dry_run: bool = False) -> Report
def delete_plan(plan_id: str, backend: WorkoutBackend, home: Path, today: date, dry_run: bool = False) -> Report
def list_pushed(home: Path, plan_id: str | None = None) -> list[Entry]   # sorted by date, then name
def get_plan(plan_id: str, home: Path) -> Plan                           # load_plan_copy
```

`push` decides one action per workout in the file, in file order, then one per leftover state
entry of this plan, sorted by date:

1. Workout dated before `today`: `past`. No backend call, state untouched.
2. Entry for (plan, date, name) exists and `payload_hash(compile_workout(...))` equals its hash:
   `unchanged`.
3. Entry exists with a different hash: `update`. `backend.update(entry.workout_id, payload)`,
   then store the new hash. The calendar entry stays, because Garmin keeps schedules on update.
4. No entry: `create`. `workout_id = backend.create(payload)`,
   `schedule_id = backend.schedule(workout_id, workout.date)`, then add the entry.
5. A state entry of this plan whose (date, name) is not in the file: `past` if its date is
   before `today` (entry kept), otherwise `delete`: `backend.delete(entry.workout_id)` (treat
   `NotFoundError` as already gone), then remove the entry. Deleting a workout also removes it
   from the Garmin calendar.

In a real run, save the state after each create, update or delete, so a failure halfway leaves
the state matching Garmin; the error then propagates. After every action succeeds, call
`save_plan_copy`. A dry run makes no backend calls and writes nothing, and returns the same
actions a real run would.

`delete_plan` applies rule 5 to every entry of `plan_id`: future ones are deleted, past ones
reported as `past` and kept. The saved plan copy stays.

Where:
- `src/trene/state.py`, `src/trene/sync.py`: new
- `tests/test_sync.py`: new
- `src/trene/compiler.py`: `compile_workout`, `payload_hash` (Phase 2). Import; do not edit.
- `src/trene/backends/fake.py`: `FakeBackend` (Phase 3). Import; do not edit.
- `src/trene/schema.py`: `Plan`, `PlanError`, `load_plan`. Use `load_plan(Path("tests/plans/week.json"))` in tests.

### Exit criteria
- First push of `week.json` with `today=date(2099, 1, 1)`: 2 `create` actions, 2 workouts and 2
  schedules in the fake, 2 entries in `state.json`, and `home/plans/base-week-1.json` exists.
- A second identical push: 2 `unchanged` actions and no new entries in `backend.calls`.
- Changing one step's time, then pushing: 1 `update` with the same workout id, 1 `unchanged`.
- Removing a workout from the plan, then pushing: 1 `delete`; the fake no longer has that workout
  or its schedule, and the state entry is gone.
- With `today=date(2099, 1, 6)`, the 2099-01-05 workout is `past` whether it changed, stayed, or
  was removed from the file, and the fake sees no call for it.
- A dry run returns the same ops as the real run that follows it, makes no backend calls and
  creates no `state.json`.
- Failing halfway: push a plan holding only the 2099-01-05 workout, then change its time, add the
  2099-01-07 workout, set `backend.fail_on = {"create"}` and push again. `BackendError`
  propagates, and `state.json` holds one entry whose hash is the new (updated) one.
- Nothing outside the three new files under Where is edited.

### Gate
```
uv run ruff check src tests
uv run pytest -q
```

## Phase 5: The CLI

Build `src/trene/cli.py`, a typer app named `app`, over the functions from Phases 1-4. Each
command is a thin wrapper: parse arguments, call the library, print.

```python
app = typer.Typer(no_args_is_help=True, help="Push JSON running plans to Garmin Connect.")

def get_backend() -> WorkoutBackend:   # from_tokens(); tests replace it with monkeypatch
    ...
```

Import `from_tokens` and `login_interactive` into `trene.cli`'s namespace
(`from trene.backends.garmin import from_tokens, login_interactive`), so tests can patch
`trene.cli.get_backend` and `trene.cli.login_interactive`.

| command | does | prints |
| --- | --- | --- |
| `trene login` | prompts `Garmin email`, `Garmin password` (`hide_input=True`), and `MFA code` through `prompt_mfa`; calls `login_interactive` | `Logged in as <name>. Tokens saved in <path>.` |
| `trene status` | `get_backend().whoami()` | `Logged in as <name>.` |
| `trene validate PLAN` | `load_plan` | `OK: <plan id>, <n> workouts, <first date> to <last date>` |
| `trene preview PLAN` | `compile_plan` | the payloads as one JSON list, `indent=2` |
| `trene push PLAN [--dry-run]` | `sync.push(plan, get_backend(), trene_home(), date.today(), dry_run)` | the report (below) |
| `trene list [--plan ID]` | `list_pushed` | one line per entry: `<date>  <plan>  <name>  #<workout_id>`, or `Nothing pushed yet.` |
| `trene delete PLAN_ID [--dry-run]` | `sync.delete_plan(...)` | the report |

The report: one line per action, `f"{a.op:<9} {a.date.isoformat()}  {a.name}"`, then a summary line
`"1 create, 1 update, 0 delete, 2 unchanged, 0 past"` (all five ops, in that order), then
`Dry run: nothing was sent to Garmin.` when `dry_run` is set. In a dry run, do not call
`get_backend()` at all: pass a `FakeBackend()`, since no backend call is made anyway.

Errors: a `PlanError` prints each of its lines and exits with code 1. A `BackendError` (including
`AuthError`) prints `Garmin error: <message>` and exits with code 2. Print everything with
`typer.echo` to stdout.

Test with `typer.testing.CliRunner`. `runner.invoke(app, ["login"], input="a@b.c\nsecret\n123456\n")`
feeds the three prompts; `result.output` holds the printed text and `result.exit_code` the code.
Use `monkeypatch.setenv("TRENE_HOME", str(tmp_path))` and replace the backend with
`monkeypatch.setattr("trene.cli.get_backend", lambda: fake)`.

Where:
- `src/trene/cli.py`: new
- `tests/test_cli.py`: new
- `src/trene/sync.py` (Phase 4): `push`, `delete_plan`, `list_pushed`, `Report`. Import; do not edit.
- `src/trene/state.py` (Phase 4): `trene_home`.
- `tests/plans/week.json`: the plan file for CLI tests (dated 2099, so `date.today()` is safe).

### Exit criteria
- `trene validate tests/plans/week.json` prints `OK: base-week-1, 2 workouts, 2099-01-05 to 2099-01-07` and exits 0.
- `trene validate` on a file with a repeat inside a repeat exits 1 and prints a line with the field path.
- `trene push tests/plans/week.json --dry-run` exits 0, prints two `create` lines and the dry-run
  line, and `get_backend` was never called (patch it with a function that raises).
- `trene push` (real, fake backend) then `trene list` shows both workouts with ids `#1001` and `#1002`.
- `trene delete base-week-1` after that push prints two `delete` lines and leaves the fake empty.
- A backend raising `AuthError` makes `trene status` exit 2 with output containing `trene login`.
- `trene login` with fed input prints `Logged in as` and passed the three answers to the patched
  `login_interactive`.
- Nothing outside `src/trene/cli.py` and `tests/test_cli.py` is edited.

### Gate
```
uv run ruff check src tests
uv run pytest -q
```

## Phase 6: The MCP server

Build `src/trene/mcp_server.py`: an MCP server over stdio exposing eight tools that call the same
library functions as the CLI. `mcp` is version 2.x, where the server class is `MCPServer`
(the 1.x name `FastMCP` no longer exists). This shape was checked on the installed version:

```python
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

server = MCPServer("trene", instructions=INSTRUCTIONS)

@server.tool()
def validate_plan(plan: Plan) -> dict:
    """Check a plan and summarise it."""
    ...
# The decorator returns the plain function, so tests call validate_plan(plan) directly.
# A pydantic model as a parameter becomes the tool's JSON input schema, and an invalid plan is
# rejected before the function runs, with the field path in the error.
# Raise ToolError("message") for errors: Claude sees the message. Any other exception reaches
# Claude only as "Error executing tool <name>", without the message.
# asyncio.run(server.list_tools()) returns tools with .name and .input_schema (a dict).

def main() -> None:
    server.run()   # stdio
```

The tools:

| tool | arguments | returns |
| --- | --- | --- |
| `validate_plan` | `plan: Plan` | `{"ok": True, "plan", "workouts": n, "first": date, "last": date, "past": [names dated before today]}` |
| `preview_plan` | `plan: Plan` | `compile_plan(plan)` |
| `push_plan` | `plan: Plan, dry_run: bool = True, confirm: bool = False` | `Report.to_dict()` |
| `list_pushed` | `plan_id: str \| None = None` | list of entries: `{"plan", "date", "name", "workout_id"}` |
| `get_plan` | `plan_id: str` | the saved plan, `plan.model_dump(mode="json", exclude_none=True)` |
| `delete_pushed` | `plan_id: str, dry_run: bool = True, confirm: bool = False` | `Report.to_dict()` |
| `get_garmin_workout` | `workout_id: int` | `backend.get(workout_id)` |
| `auth_status` | none | `{"ok": True, "name": whoami()}`, or `{"ok": False, "error": message}` on `BackendError` |

Rules:
- `push_plan` and `delete_pushed` with `dry_run=False` and `confirm=False` raise
  `ToolError("Writing to Garmin needs confirm=true. Show the user the dry run first.")` and touch nothing.
- A dry run uses `FakeBackend()` and never calls `get_backend()`, like the CLI.
- `PlanError` and `BackendError` become `ToolError(str(e))`.
- Dates in return values are ISO strings. `today` is `date.today()`; home is `trene_home()`.
- `get_backend()` is a module-level function returning `from_tokens()`; tests patch
  `trene.mcp_server.get_backend`.
- Each tool has a one-line docstring saying when to use it; Claude reads it.
- `INSTRUCTIONS`:
  `"trene puts running workouts on the user's Garmin calendar. Build the plan as JSON, call validate_plan, then push_plan with dry_run=true and show the user the actions. Call push_plan with dry_run=false and confirm=true only after the user agrees. To change a plan, call get_plan, edit it, and push it again under the same plan id: changed workouts are updated, removed ones deleted, and workouts dated before today are never touched."`

Where:
- `src/trene/mcp_server.py`: new
- `tests/test_mcp_server.py`: new
- `src/trene/sync.py`, `src/trene/state.py` (Phase 4), `src/trene/compiler.py` (Phase 2),
  `src/trene/backends/` (Phase 3): import; do not edit.
- `src/trene/cli.py` (Phase 5): the same dry-run and error handling, for reference only.

### Exit criteria
- `asyncio.run(server.list_tools())` returns exactly the eight tool names above, and
  `push_plan`'s `input_schema["properties"]` contains `plan`, `dry_run` and `confirm`.
- `push_plan(plan)` with defaults returns `dry_run: True`, two `create` actions, and never calls
  `get_backend` (patched to raise).
- `push_plan(plan, dry_run=False)` raises `ToolError` containing `confirm=true`, and no
  `state.json` is created.
- `push_plan(plan, dry_run=False, confirm=True)` with a patched `FakeBackend` creates two workouts;
  then `get_plan("base-week-1")` returns a dict equal to the plan's
  `model_dump(mode="json", exclude_none=True)`, and `list_pushed()` returns two entries.
- `get_plan("missing")` raises `ToolError`; `auth_status()` with a backend raising `AuthError`
  returns `ok: False` and an error containing `trene login`.
- `validate_plan` on `week.json` returns `workouts: 2`, `first: "2099-01-05"`, `last: "2099-01-07"`.
- Nothing outside `src/trene/mcp_server.py` and `tests/test_mcp_server.py` is edited.

### Gate
```
uv run ruff check src tests
uv run pytest -q
```
