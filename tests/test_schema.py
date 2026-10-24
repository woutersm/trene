"""Tests for the plan schema: parsers, validation errors, and loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trene.schema import (
    Plan,
    PlanError,
    Repeat,
    load_plan,
    parse_distance,
    parse_pace,
    parse_plan,
    parse_time,
)

PLANS = Path(__file__).parent / "plans"


# --- parsers -----------------------------------------------------------------


class TestParseTime:
    def test_seconds(self):
        assert parse_time("90s") == 90

    def test_minutes(self):
        assert parse_time("10min") == 600

    def test_hours_and_minutes(self):
        assert parse_time("1h30min") == 5400

    @pytest.mark.parametrize("text", ["15m", "0min", "", "10 min", "min", "h"])
    def test_invalid(self, text: str):
        with pytest.raises(ValueError):
            parse_time(text)

    def test_m_suffix_suggests_minutes(self):
        with pytest.raises(ValueError, match="use 15min"):
            parse_time("15m")


class TestParseDistance:
    def test_km(self):
        assert parse_distance("1.5km") == 1500.0

    def test_meters(self):
        assert parse_distance("400m") == 400.0

    @pytest.mark.parametrize("text", ["10min", "15", "1.5", "km", "0m"])
    def test_invalid(self, text: str):
        with pytest.raises(ValueError):
            parse_distance(text)


class TestParsePace:
    def test_plain(self):
        assert parse_pace("4:35") == 275

    def test_per_km(self):
        assert parse_pace("4:35/km") == 275

    @pytest.mark.parametrize("text", ["4:75", "4:5", ":35", "4.35", ""])
    def test_invalid(self, text: str):
        with pytest.raises(ValueError):
            parse_pace(text)


# --- loading the week plan ----------------------------------------------------


class TestLoadPlan:
    def test_week_plan(self):
        plan = load_plan(PLANS / "week.json")
        assert isinstance(plan, Plan)
        assert len(plan.workouts) == 2
        second = plan.workouts[0].steps[1]
        assert isinstance(second, Repeat)
        assert second.count == 3

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(PlanError, match=r"no such file"):
            load_plan(tmp_path / "nope.json")

    def test_bad_json(self, tmp_path: Path):
        path = tmp_path / "broken.json"
        path.write_text("{ not json")
        with pytest.raises(PlanError) as exc:
            load_plan(path)
        line = str(exc.value)
        assert line.startswith(f"{path}: not valid JSON: ")
        assert "Expecting property name" in line

    def test_parse_plan_accepts_dict(self):
        plan = parse_plan(json.loads((PLANS / "week.json").read_text()))
        assert plan.plan == "base-week-1"

    def test_parse_plan_bad_json_string(self):
        with pytest.raises(PlanError, match="not valid JSON"):
            parse_plan("{ no")


# --- validation errors ---------------------------------------------------------


def _one_workout() -> dict:
    return {"date": "2099-01-05", "name": "Easy", "steps": [{"kind": "interval", "time": "5min"}]}


def _plan(workouts: list[dict]) -> dict:
    return {"plan": "p1", "workouts": workouts}


def expect_plan_error(data: dict, fragment: str) -> None:
    with pytest.raises(PlanError) as exc:
        parse_plan(data)
    assert any(fragment in line for line in exc.value.errors)


class TestPlanErrors:
    def test_repeat_inside_repeat(self):
        data = _plan(
            [
                {
                    "date": "2099-01-05",
                    "name": "Nested",
                    "steps": [
                        {
                            "kind": "repeat",
                            "count": 3,
                            "steps": [
                                {
                                    "kind": "repeat",
                                    "count": 2,
                                    "steps": [{"kind": "interval", "time": "5min"}],
                                }
                            ],
                        }
                    ],
                }
            ]
        )
        # The discriminator loc carries the resolved tag, so the inner step is
        # workouts.0.steps.0.repeat.steps.0.
        expect_plan_error(data, "workouts.0.steps.0.repeat.steps.0.kind")

    def test_misspelled_field(self):
        data = _plan([_one_workout()])
        data["workouts"][0]["steps"][0]["tme"] = "5min"
        expect_plan_error(data, "workouts.0.steps.0.interval.tme")

    def test_time_and_distance_together(self):
        data = _plan(
            [
                {
                    "date": "2099-01-05",
                    "name": "Both",
                    "steps": [{"kind": "interval", "time": "5min", "distance": "8km"}],
                }
            ]
        )
        expect_plan_error(data, "workouts.0.steps.0.interval")

    def test_hr_low_above_high(self):
        data = _plan(
            [
                {
                    "date": "2099-01-05",
                    "name": "Hr",
                    "steps": [
                        {
                            "kind": "interval",
                            "time": "5min",
                            "target": {"type": "hr", "low": 160, "high": 140},
                        }
                    ],
                }
            ]
        )
        expect_plan_error(data, "workouts.0.steps.0.interval.target.hr")

    def test_pace_fast_slower_than_slow(self):
        data = _plan(
            [
                {
                    "date": "2099-01-05",
                    "name": "Pace",
                    "steps": [
                        {
                            "kind": "interval",
                            "time": "5min",
                            "target": {"type": "pace", "fast": "5:00", "slow": "4:30"},
                        }
                    ],
                }
            ]
        )
        expect_plan_error(data, "workouts.0.steps.0.interval.target.pace")

    def test_cadence_low_above_high(self):
        data = _plan(
            [
                {
                    "date": "2099-01-05",
                    "name": "Cad",
                    "steps": [
                        {
                            "kind": "interval",
                            "time": "5min",
                            "target": {"type": "cadence", "low": 180, "high": 160},
                        }
                    ],
                }
            ]
        )
        expect_plan_error(data, "workouts.0.steps.0.interval.target.cadence")

    def test_repeat_count_one(self):
        data = _plan(
            [
                {
                    "date": "2099-01-05",
                    "name": "Count",
                    "steps": [
                        {
                            "kind": "repeat",
                            "count": 1,
                            "steps": [{"kind": "interval", "time": "5min"}],
                        }
                    ],
                }
            ]
        )
        expect_plan_error(data, "workouts.0.steps.0.repeat.count")

    def test_duplicate_date_and_name(self):
        data = _plan([_one_workout(), _one_workout()])
        expect_plan_error(data, "workouts")

    def test_empty_repeat_steps(self):
        data = _plan(
            [
                {
                    "date": "2099-01-05",
                    "name": "Empty repeat",
                    "steps": [{"kind": "repeat", "count": 3, "steps": []}],
                }
            ]
        )
        expect_plan_error(data, "workouts.0.steps.0.repeat.steps")

    def test_bad_step_kind(self):
        data = _plan(
            [
                {
                    "date": "2099-01-05",
                    "name": "Kind",
                    "steps": [{"kind": "sprint", "time": "5min"}],
                }
            ]
        )
        expect_plan_error(data, "workouts.0.steps.0")

    def test_bad_plan_name(self):
        data = _plan([_one_workout()])
        data["plan"] = "Not a Plan!"
        expect_plan_error(data, "plan")

    def test_workout_without_steps(self):
        data = _plan([{"date": "2099-01-05", "name": "No steps"}])
        expect_plan_error(data, "workouts.0.steps")


# --- round trip ---------------------------------------------------------------


class TestRoundTrip:
    def test_dump_reload(self):
        plan = load_plan(PLANS / "week.json")
        assert Plan.model_validate_json(plan.model_dump_json(exclude_none=True)) == plan

    def test_parse_plan_round_trip(self):
        original = json.loads((PLANS / "week.json").read_text())
        plan = parse_plan(original)
        dumped = plan.model_dump_json(exclude_none=True)
        assert dumped == json.dumps(original, separators=(",", ":"))
