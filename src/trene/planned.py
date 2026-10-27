"""The pushed workouts as plain JSON, for readers other than Claude (heim's /fit).

Steps come in numbers (seconds, meters, seconds per km), and each workout carries an
estimate of its time and distance: at least what its steps add up to. A step with a pace
target counts in both, at the middle of the range; a step without one counts only in what
it states.
"""

from __future__ import annotations

from pathlib import Path

from trene.schema import (
    PaceTarget,
    Plan,
    PlanError,
    Repeat,
    Step,
    parse_distance,
    parse_pace,
    parse_time,
)
from trene.state import State, load_plan_copy


def _target(step: Step) -> dict | None:
    target = step.target
    if target is None:
        return None
    if isinstance(target, PaceTarget):
        return {
            "type": "pace",
            "fast_s_km": parse_pace(target.fast),
            "slow_s_km": parse_pace(target.slow),
        }
    return target.model_dump()


def _leaf(step: Step) -> dict:
    return {
        "kind": step.kind,
        "time_s": parse_time(step.time) if step.time else None,
        "distance_m": round(parse_distance(step.distance)) if step.distance else None,
        "target": _target(step),
    }


def _estimate(step: Step) -> tuple[float | None, float | None]:
    """(seconds, meters) one step adds; None where it says nothing."""
    time_s = parse_time(step.time) if step.time else None
    distance_m = parse_distance(step.distance) if step.distance else None
    if isinstance(step.target, PaceTarget):
        mid = (parse_pace(step.target.fast) + parse_pace(step.target.slow)) / 2
        if time_s is not None:
            distance_m = time_s / mid * 1000
        elif distance_m is not None:
            time_s = distance_m / 1000 * mid
    return time_s, distance_m


def workout_json(plan: Plan, index: int, garmin_id: int) -> dict:
    workout = plan.workouts[index]
    steps: list[dict] = []
    time_s: float | None = None
    distance_m: float | None = None

    def add(step: Step, times: int) -> None:
        nonlocal time_s, distance_m
        t, d = _estimate(step)
        if t is not None:
            time_s = (time_s or 0) + t * times
        if d is not None:
            distance_m = (distance_m or 0) + d * times

    for step in workout.steps:
        if isinstance(step, Repeat):
            steps.append(
                {"kind": "repeat", "count": step.count, "steps": [_leaf(s) for s in step.steps]}
            )
            for inner in step.steps:
                add(inner, step.count)
        else:
            steps.append(_leaf(step))
            add(step, 1)
    return {
        "plan": plan.plan,
        "goal": plan.goal,
        "date": workout.date.isoformat(),
        "name": workout.name,
        "notes": workout.notes,
        "garmin_id": garmin_id,
        "time_s": round(time_s) if time_s is not None else None,
        "distance_m": round(distance_m) if distance_m is not None else None,
        "steps": steps,
    }


def planned(home: Path) -> dict:
    """Every workout trene pushed, by date then name, with its steps."""
    plans: dict[str, Plan | None] = {}
    out: list[dict] = []
    for entry in sorted(State.load(home).entries, key=lambda e: (e.date, e.name)):
        if entry.plan not in plans:
            try:
                plans[entry.plan] = load_plan_copy(entry.plan, home)
            except PlanError:
                plans[entry.plan] = None
        plan = plans[entry.plan]
        if plan is None:
            continue
        for i, workout in enumerate(plan.workouts):
            if workout.date == entry.date and workout.name == entry.name:
                out.append(workout_json(plan, i, entry.workout_id))
                break
    return {"workouts": out}
