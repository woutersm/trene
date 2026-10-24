"""Compile a plan into the JSON bodies Garmin's create/update calls take.

Pure functions only: no file writes, no network, no clocks.
"""

import hashlib
import json

from .schema import PaceTarget, Plan, Step, Workout, parse_distance, parse_pace, parse_time

RUNNING = {"sportTypeId": 1, "sportTypeKey": "running"}

_STEP_TYPES = {
    "warmup": (1, "warmup"),
    "cooldown": (2, "cooldown"),
    "interval": (3, "interval"),
    "recovery": (4, "recovery"),
    "rest": (5, "rest"),
}

_NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}


def _end_condition(step: Step) -> tuple[dict, float | None]:
    if step.time is not None:
        return (
            {"conditionTypeId": 2, "conditionTypeKey": "time"},
            float(parse_time(step.time)),
        )
    if step.distance is not None:
        return (
            {"conditionTypeId": 3, "conditionTypeKey": "distance"},
            parse_distance(step.distance),
        )
    return {"conditionTypeId": 1, "conditionTypeKey": "lap.button"}, None


def _target_dict(step: Step) -> tuple[dict, float | None, float | None, int | None]:
    target = step.target
    if target is None:
        return _NO_TARGET, None, None, None
    if target.type == "hr_zone":
        return (
            {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
            None,
            None,
            target.zone,
        )
    if target.type == "hr":
        return (
            {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
            float(target.low),
            float(target.high),
            None,
        )
    if target.type == "cadence":
        return (
            {"workoutTargetTypeId": 3, "workoutTargetTypeKey": "cadence"},
            float(target.low),
            float(target.high),
            None,
        )
    pace: PaceTarget = target
    return (
        {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
        1000 / parse_pace(pace.fast),
        1000 / parse_pace(pace.slow),
        None,
    )


def _compile_step(
    step: Step, order: int, child_step_id: int | None
) -> dict:
    condition, condition_value = _end_condition(step)
    target_type, value_one, value_two, zone = _target_dict(step)
    step_type_id, step_type_key = _STEP_TYPES[step.kind]
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "childStepId": child_step_id,
        "stepType": {"stepTypeId": step_type_id, "stepTypeKey": step_type_key},
        "endCondition": condition,
        "endConditionValue": condition_value,
        "targetType": target_type,
        "targetValueOne": value_one,
        "targetValueTwo": value_two,
        "zoneNumber": zone,
    }


def compile_workout(workout: Workout, plan_id: str) -> dict:
    """Turn a Workout into the payload for Garmin's create/update calls."""
    description = "\n\n".join(
        part for part in (workout.notes, f"trene plan {plan_id}") if part
    )
    counter = {"order": 0}
    repeat_groups = {"count": 0}
    steps: list[dict] = []

    def walk(steps_in: list) -> None:
        for s in steps_in:
            counter["order"] += 1
            if s.kind == "repeat":
                repeat_groups["count"] += 1
                group_id = repeat_groups["count"]
                steps.append(
                    {
                        "type": "RepeatGroupDTO",
                        "stepOrder": counter["order"],
                        "childStepId": group_id,
                        "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
                        "endCondition": {
                            "conditionTypeId": 7,
                            "conditionTypeKey": "iterations",
                        },
                        "endConditionValue": float(s.count),
                        "numberOfIterations": s.count,
                        "skipLastRestStep": True,
                        "smartRepeat": False,
                        "workoutSteps": [],
                    }
                )
                for child in s.steps:
                    counter["order"] += 1
                    steps[-1]["workoutSteps"].append(
                        _compile_step(child, counter["order"], group_id)
                    )
            else:
                steps.append(_compile_step(s, counter["order"], None))

    walk(workout.steps)
    return {
        "workoutName": workout.name,
        "description": description,
        "sportType": RUNNING,
        "workoutSegments": [
            {"segmentOrder": 1, "sportType": RUNNING, "workoutSteps": steps}
        ],
    }


def compile_plan(plan: Plan) -> list[dict]:
    """One payload per workout, in plan order."""
    return [compile_workout(workout, plan.plan) for workout in plan.workouts]


def payload_hash(payload: dict) -> str:
    """The sha256 hex digest of the payload with sorted keys."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
