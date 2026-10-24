"""Tests for the compiler against the captured Garmin fixtures."""

import json
from pathlib import Path

from trene.compiler import RUNNING, compile_plan, compile_workout, payload_hash
from trene.schema import Workout, load_plan

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


def steps_payload(steps: list[dict]) -> dict:
    workout = Workout.model_validate(
        {"date": "2099-01-05", "name": "Fixture", "steps": steps}
    )
    return compile_workout(workout, "fixture")


def _assert_steps(payload: dict, name: str) -> None:
    assert [trim(s) for s in payload["workoutSegments"][0]["workoutSteps"]] == fixture_steps(name)


def test_steps_fixture() -> None:
    steps = [
        {"kind": "warmup", "time": "10min", "target": {"type": "hr_zone", "zone": 2}},
        {"kind": "interval", "distance": "1km",
         "target": {"type": "pace", "fast": "4:35", "slow": "4:45"}},
        {"kind": "rest", "time": "1min"},
        {"kind": "cooldown", "time": "5min"},
    ]
    _assert_steps(steps_payload(steps), "steps")


def test_targets_fixture() -> None:
    steps = [
        {"kind": "interval", "time": "5min", "target": {"type": "hr", "low": 130, "high": 150}},
        {"kind": "interval", "time": "5min", "target": {"type": "cadence",
                                                         "low": 170, "high": 180}},
        {"kind": "interval", "distance": "2km"},
    ]
    _assert_steps(steps_payload(steps), "targets")


def test_repeats_fixture() -> None:
    steps = [
        {"kind": "warmup", "time": "10min"},
        {"kind": "repeat", "count": 3, "steps": [
            {"kind": "interval", "distance": "400m"},
            {"kind": "recovery", "time": "90s"},
        ]},
        {"kind": "cooldown", "time": "10min"},
    ]
    _assert_steps(steps_payload(steps), "repeats")


def test_lap_fixture() -> None:
    steps = [
        {"kind": "interval"},
        {"kind": "repeat", "count": 2, "steps": [
            {"kind": "interval", "time": "60s"},
            {"kind": "recovery"},
        ]},
        {"kind": "repeat", "count": 2, "steps": [
            {"kind": "interval", "time": "30s"},
            {"kind": "recovery", "time": "30s"},
        ]},
    ]
    payload = steps_payload(steps)
    left = [trim(s) for s in payload["workoutSegments"][0]["workoutSteps"]]
    right = fixture_steps("lap")
    for s in left + right:
        if s["type"] == "RepeatGroupDTO":
            s.pop("skipLastRestStep")
            for child in s["workoutSteps"]:
                child.pop("skipLastRestStep", None)
    assert left == right


def test_payload_top_level() -> None:
    plan = load_plan(Path(__file__).parent / "plans" / "week.json")
    payload = compile_plan(plan)[0]
    assert payload["workoutName"] == "Tempo 3x10"
    assert payload["sportType"] == RUNNING
    assert len(payload["workoutSegments"]) == 1
    assert payload["workoutSegments"][0]["segmentOrder"] == 1
    assert payload["description"] == "Even effort.\n\ntrene plan base-week-1"


def test_payload_description_without_notes() -> None:
    workout = Workout.model_validate(
        {"date": "2099-01-05", "name": "Easy", "steps": [
            {"kind": "interval", "distance": "8km"}]}
    )
    payload = compile_workout(workout, "base-week-1")
    assert payload["description"] == "trene plan base-week-1"


def test_compile_plan_order() -> None:
    plan = load_plan(Path(__file__).parent / "plans" / "week.json")
    payloads = compile_plan(plan)
    assert [p["workoutName"] for p in payloads] == ["Tempo 3x10", "Easy 8k"]


def test_payload_hash_equal_and_different() -> None:
    a = steps_payload([{"kind": "warmup", "time": "10min"}])
    b = steps_payload([{"kind": "warmup", "time": "10min"}])
    c = steps_payload([{"kind": "warmup", "time": "11min"}])
    assert payload_hash(a) == payload_hash(b)
    assert payload_hash(a) != payload_hash(c)
