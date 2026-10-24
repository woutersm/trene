from datetime import date
from pathlib import Path

import pytest

from trene.backends import FakeBackend
from trene.schema import Plan, PlanError, load_plan
from trene.state import Entry, State, load_plan_copy, save_plan_copy, trene_home
from trene.sync import delete_plan, get_plan, list_pushed, push

PLAN_PATH = Path("tests/plans/week.json")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("TRENE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def plan() -> "Plan":
    return load_plan(PLAN_PATH)


def test_trene_home_uses_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRENE_HOME", str(tmp_path))
    assert trene_home() == tmp_path


def test_trene_home_defaults_to_dot(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TRENE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert trene_home() == tmp_path / ".trene"


def test_state_missing_file_is_empty(home: Path) -> None:
    state = State.load(home)
    assert state.entries == []


def test_state_save_and_load_roundtrip(home: Path) -> None:
    entry = Entry(
        plan="base-week-1",
        date=date(2099, 1, 5),
        name="Tempo 3x10",
        workout_id=1001,
        schedule_id=5001,
        payload_hash="abc",
    )
    state = State(entries=[entry])
    state.save(home)
    path = home / "state.json"
    assert path.is_file()
    data = path.read_text()
    assert '"date":"2099-01-05"' in data.replace(" ", "")
    assert '"version": 1' in data
    loaded = State.load(home)
    assert loaded.entries == [entry]


def test_state_save_mkdirs_home(tmp_path: Path) -> None:
    home = tmp_path / "nested" / "home"
    state = State(
        entries=[
            Entry("p", date(2099, 1, 5), "w", 1, 2, "h"),
        ]
    )
    state.save(home)
    assert (home / "state.json").is_file()


def test_state_find_and_for_plan(home: Path) -> None:
    entries = [
        Entry("a", date(2099, 1, 5), "w1", 1, 11, "h1"),
        Entry("a", date(2099, 1, 7), "w2", 2, 12, "h2"),
        Entry("b", date(2099, 1, 5), "w3", 3, 13, "h3"),
    ]
    state = State(entries=entries)
    assert state.find("a", date(2099, 1, 7), "w2").workout_id == 2
    assert state.find("a", date(2099, 1, 7), "nope") is None
    assert state.for_plan("a") == entries[:2]


def test_save_and_load_plan_copy(home: Path, plan: "Plan") -> None:
    path = save_plan_copy(plan, home)
    assert path == home / "plans" / "base-week-1.json"
    assert path.is_file()
    assert load_plan_copy("base-week-1", home) == plan


def test_load_plan_copy_missing(home: Path) -> None:
    with pytest.raises(PlanError) as excinfo:
        load_plan_copy("nope", home)
    assert str(excinfo.value) == "no saved plan 'nope'"


def test_first_push_creates(
    home: Path, backend: FakeBackend, plan: "Plan"
) -> None:
    report = push(plan, backend, home, date(2099, 1, 1))
    assert [a.op for a in report.actions] == ["create", "create"]
    assert report.counts() == {
        "create": 2,
        "update": 0,
        "unchanged": 0,
        "delete": 0,
        "past": 0,
    }
    assert len(backend.workouts) == 2
    assert len(backend.schedules) == 2
    state = State.load(home)
    assert len(state.entries) == 2
    assert all(e.plan == "base-week-1" for e in state.entries)
    assert (home / "plans" / "base-week-1.json").is_file()
    assert (home / "plans" / "base-week-1.json").read_text() == plan.model_dump_json(
        indent=2, exclude_none=True
    )


def test_second_push_unchanged(
    home: Path, backend: FakeBackend, plan: "Plan"
) -> None:
    push(plan, backend, home, date(2099, 1, 1))
    calls = len(backend.calls)
    report = push(plan, backend, home, date(2099, 1, 1))
    assert [a.op for a in report.actions] == ["unchanged", "unchanged"]
    assert len(backend.calls) == calls
    assert len(State.load(home).entries) == 2


def test_changed_step_updates(
    home: Path, backend: FakeBackend, plan: "Plan"
) -> None:
    push(plan, backend, home, date(2099, 1, 1))
    entry = State.load(home).find("base-week-1", date(2099, 1, 5), "Tempo 3x10")
    first_id = entry.workout_id

    changed = plan.model_copy(deep=True)
    changed.workouts[0].steps[0].time = "20min"
    report = push(changed, backend, home, date(2099, 1, 1))

    assert [a.op for a in report.actions] == ["update", "unchanged"]
    update = report.actions[0]
    assert update.workout_id == first_id
    assert entry.workout_id == first_id
    assert backend.workouts[first_id]["workoutName"] == "Tempo 3x10"
    assert len(State.load(home).entries) == 2


def test_removed_workout_deletes(
    home: Path, backend: FakeBackend, plan: "Plan"
) -> None:
    push(plan, backend, home, date(2099, 1, 1))
    gone = State.load(home).find("base-week-1", date(2099, 1, 7), "Easy 8k")
    workout_id = gone.workout_id
    schedule_id = gone.schedule_id

    reduced = plan.model_copy(deep=True)
    reduced.workouts = reduced.workouts[:1]
    report = push(reduced, backend, home, date(2099, 1, 1))

    assert [a.op for a in report.actions] == ["unchanged", "delete"]
    assert workout_id not in backend.workouts
    assert schedule_id not in backend.schedules
    assert not any(e.workout_id == workout_id for e in State.load(home).entries)


def test_past_workouts_are_not_touched(
    home: Path, backend: FakeBackend, plan: "Plan"
) -> None:
    push(plan, backend, home, date(2099, 1, 1))
    state = State.load(home)
    entry = state.find("base-week-1", date(2099, 1, 5), "Tempo 3x10")
    workout_id = entry.workout_id
    calls = len(backend.calls)

    # changed, then today past it
    changed = plan.model_copy(deep=True)
    changed.workouts[0].steps[0].time = "20min"
    report = push(changed, backend, home, date(2099, 1, 6))
    assert [a.op for a in report.actions] == ["past", "unchanged"]
    assert len(backend.calls) == calls  # no call for the past workout
    assert entry.workout_id == workout_id  # state entry untouched
    assert backend.workouts[workout_id] is not None

    # unchanged, then past
    calls = len(backend.calls)
    report = push(plan, backend, home, date(2099, 1, 6))
    assert [a.op for a in report.actions] == ["past", "unchanged"]
    assert len(backend.calls) == calls

    # removed, then past (keep only the 01-07 workout)
    reduced = plan.model_copy(deep=True)
    reduced.workouts = reduced.workouts[1:]
    calls = len(backend.calls)
    report = push(reduced, backend, home, date(2099, 1, 6))
    assert [a.op for a in report.actions] == ["unchanged", "past"]
    assert len(backend.calls) == calls
    assert any(
        e.workout_id == workout_id for e in State.load(home).entries
    )  # kept


def test_dry_run(
    home: Path, backend: FakeBackend, plan: "Plan"
) -> None:
    dry = push(plan, backend, home, date(2099, 1, 1), dry_run=True)
    assert [a.op for a in dry.actions] == ["create", "create"]
    assert all(a.workout_id is None for a in dry.actions)
    assert backend.calls == []
    assert not (home / "state.json").exists()

    real = push(plan, backend, home, date(2099, 1, 1))
    assert [a.op for a in real.actions] == [a.op for a in dry.actions]
    assert len(State.load(home).entries) == 2


def test_dry_run_delete(home: Path, backend: FakeBackend, plan: "Plan") -> None:
    push(plan, backend, home, date(2099, 1, 1))
    calls = len(backend.calls)
    reduced = plan.model_copy(deep=True)
    reduced.workouts = reduced.workouts[:1]
    report = push(reduced, backend, home, date(2099, 1, 1), dry_run=True)
    assert [a.op for a in report.actions] == ["unchanged", "delete"]
    assert len(backend.calls) == calls
    assert len(State.load(home).entries) == 2  # entry kept


def test_failing_halfway(
    home: Path, backend: FakeBackend, plan: "Plan"
) -> None:
    first = plan.model_copy(deep=True)
    first.workouts = first.workouts[:1]
    push(first, backend, home, date(2099, 1, 1))

    changed = plan.model_copy(deep=True)  # 01-05 changed + 01-07 added
    changed.workouts[0].steps[0].time = "20min"
    backend.fail_on = {"create"}

    from trene.backends import BackendError

    with pytest.raises(BackendError):
        push(changed, backend, home, date(2099, 1, 1))

    state = State.load(home)
    assert len(state.entries) == 1
    entry = state.entries[0]
    assert entry.date == date(2099, 1, 5)
    payload = backend.get(entry.workout_id)
    from trene.compiler import compile_workout, payload_hash

    assert entry.payload_hash == payload_hash(
        compile_workout(changed.workouts[0], "base-week-1")
    )
    assert payload == backend.workouts[entry.workout_id]


def test_delete_plan(home: Path, backend: FakeBackend, plan: "Plan") -> None:
    push(plan, backend, home, date(2099, 1, 1))
    report = delete_plan("base-week-1", backend, home, date(2099, 1, 1))
    assert [a.op for a in report.actions] == ["delete", "delete"]
    assert backend.workouts == {}
    assert backend.schedules == {}
    assert State.load(home).entries == []
    assert (home / "plans" / "base-week-1.json").is_file()  # copy kept


def test_delete_plan_past_entries_kept(
    home: Path, backend: FakeBackend, plan: "Plan"
) -> None:
    push(plan, backend, home, date(2099, 1, 1))
    report = delete_plan("base-week-1", backend, home, date(2099, 1, 6))
    assert [a.op for a in report.actions] == ["past", "delete"]
    assert len(backend.workouts) == 1
    assert len(State.load(home).entries) == 1


def test_delete_plan_missing_copy(home: Path, backend: FakeBackend) -> None:
    with pytest.raises(PlanError) as excinfo:
        delete_plan("nope", backend, home, date(2099, 1, 1))
    assert str(excinfo.value) == "no saved plan 'nope'"


def test_list_pushed(home: Path, backend: FakeBackend, plan: "Plan") -> None:
    assert list_pushed(home) == []
    push(plan, backend, home, date(2099, 1, 1))
    entries = list_pushed(home)
    assert [e.name for e in entries] == ["Tempo 3x10", "Easy 8k"]
    entries = list_pushed(home, "base-week-1")
    assert len(entries) == 2
    assert list_pushed(home, "other") == []


def test_get_plan(home: Path, plan: "Plan") -> None:
    save_plan_copy(plan, home)
    assert get_plan("base-week-1", home) == plan


def test_schedule_failure_removes_new_workout(
    home: Path, backend: FakeBackend, plan: "Plan"
) -> None:
    from trene.backends import BackendError

    backend.fail_on = {"schedule"}
    with pytest.raises(BackendError):
        push(plan, backend, home, date(2099, 1, 1))
    assert backend.workouts == {}  # no orphan left on Garmin
    backend.fail_on = set()
    report = push(plan, backend, home, date(2099, 1, 1))
    assert [a.op for a in report.actions] == ["create", "create"]
    assert len(backend.workouts) == 2  # no duplicates


def test_delete_plan_after_failed_push(home: Path, backend: FakeBackend, plan: "Plan") -> None:
    from trene.backends import BackendError

    push_one = plan.model_copy(deep=True)
    push_one.workouts.append(push_one.workouts[1].model_copy(update={"name": "Extra"}))
    real_create = backend.create
    created: list[int] = []

    def create_once(payload: dict) -> int:
        if created:
            raise BackendError("fake failure")
        created.append(real_create(payload))
        return created[0]

    backend.create = create_once  # type: ignore[method-assign]
    with pytest.raises(BackendError):
        push(push_one, backend, home, date(2099, 1, 1))
    assert not (home / "plans" / "base-week-1.json").exists()  # copy is saved only at the end
    report = delete_plan("base-week-1", backend, home, date(2099, 1, 1))
    assert [a.op for a in report.actions] == ["delete"]
    assert backend.workouts == {}
