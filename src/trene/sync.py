"""Make the Garmin calendar match a plan: push, delete, list, get."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from .backends import NotFoundError, WorkoutBackend
from .compiler import compile_workout, payload_hash
from .schema import Plan
from .state import Entry, State, load_plan_copy, save_plan_copy

Op = Literal["create", "update", "unchanged", "delete", "past"]

OPS = ("create", "update", "unchanged", "delete", "past")


@dataclass(frozen=True)
class Action:
    op: Op
    date: date
    name: str
    workout_id: int | None = None  # None for a create in a dry run


@dataclass
class Report:
    plan: str
    dry_run: bool
    actions: list[Action] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        result = {op: 0 for op in OPS}
        for action in self.actions:
            result[action.op] += 1
        return result

    def to_dict(self) -> dict:
        return {
            "plan": self.plan,
            "dry_run": self.dry_run,
            "actions": [
                {
                    "op": action.op,
                    "date": action.date.isoformat(),
                    "name": action.name,
                    "workout_id": action.workout_id,
                }
                for action in self.actions
            ],
            "counts": self.counts(),
        }


def push(
    plan: Plan,
    backend: WorkoutBackend,
    home: Path,
    today: date,
    dry_run: bool = False,
) -> Report:
    state = State.load(home)
    report = Report(plan=plan.plan, dry_run=dry_run)

    def save() -> None:
        if not dry_run:
            state.save(home)

    for workout in plan.workouts:
        entry = state.find(plan.plan, workout.date, workout.name)
        if workout.date < today:
            report.actions.append(Action("past", workout.date, workout.name))
            continue
        payload = compile_workout(workout, plan.plan)
        digest = payload_hash(payload)
        if entry is not None and entry.payload_hash == digest:
            report.actions.append(
                Action("unchanged", workout.date, workout.name, entry.workout_id)
            )
            continue
        if entry is not None:
            if not dry_run:
                backend.update(entry.workout_id, payload)
            report.actions.append(
                Action("update", workout.date, workout.name, entry.workout_id)
            )
            entry.payload_hash = digest
            save()
            continue
        workout_id = None
        if not dry_run:
            workout_id = backend.create(payload)
            entry = Entry(
                plan=plan.plan,
                date=workout.date,
                name=workout.name,
                workout_id=workout_id,
                schedule_id=backend.schedule(workout_id, workout.date),
                payload_hash=digest,
            )
            state.entries.append(entry)
            save()
        report.actions.append(
            Action("create", workout.date, workout.name, workout_id)
        )

    file_keys = {(w.date, w.name) for w in plan.workouts}
    leftovers = sorted(
        (
            entry
            for entry in state.for_plan(plan.plan)
            if (entry.date, entry.name) not in file_keys
        ),
        key=lambda entry: entry.date,
    )
    for entry in leftovers:
        if entry.date < today:
            report.actions.append(Action("past", entry.date, entry.name, entry.workout_id))
            continue
        if not dry_run:
            try:
                backend.delete(entry.workout_id)
            except NotFoundError:
                pass
            state.entries.remove(entry)
            save()
        report.actions.append(
            Action("delete", entry.date, entry.name, entry.workout_id)
        )

    if not dry_run:
        save_plan_copy(plan, home)
    return report


def delete_plan(
    plan_id: str,
    backend: WorkoutBackend,
    home: Path,
    today: date,
    dry_run: bool = False,
) -> Report:
    # The saved copy must exist; it stays after the entries go.
    load_plan_copy(plan_id, home)
    state = State.load(home)
    report = Report(plan=plan_id, dry_run=dry_run)
    entries = sorted(state.for_plan(plan_id), key=lambda entry: entry.date)
    for entry in entries:
        if entry.date < today:
            report.actions.append(
                Action("past", entry.date, entry.name, entry.workout_id)
            )
            continue
        if not dry_run:
            try:
                backend.delete(entry.workout_id)
            except NotFoundError:
                pass
            state.entries.remove(entry)
            state.save(home)
        report.actions.append(
            Action("delete", entry.date, entry.name, entry.workout_id)
        )
    return report


def list_pushed(home: Path, plan_id: str | None = None) -> list[Entry]:
    entries = State.load(home).entries
    if plan_id is not None:
        entries = [entry for entry in entries if entry.plan == plan_id]
    return sorted(entries, key=lambda entry: (entry.date, entry.name))


def get_plan(plan_id: str, home: Path) -> Plan:
    return load_plan_copy(plan_id, home)
