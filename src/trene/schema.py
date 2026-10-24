"""The plan schema: pydantic models, parsers, and loading."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Annotated, Literal

import pydantic_core
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TIME_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)min)?(?:(\d+)s)?$")
_DISTANCE_RE = re.compile(r"^(\d+(?:\.\d+)?)(m|km)$")
_PACE_RE = re.compile(r"^(\d{1,2}):([0-5]\d)(?:/km)?$")


class PlanError(ValueError):
    """A plan that fails validation. `errors` holds one line per problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def parse_time(text: str) -> int:
    """Parse a step duration like `90s`, `10min` or `1h30min` into seconds."""
    m = _TIME_RE.match(text)
    if m:
        hours, minutes, seconds = (int(p) if p else 0 for p in m.groups())
        total = hours * 3600 + minutes * 60 + seconds
        if total > 0:
            return total
    if text.endswith("m"):
        digits = text[:-1]
        if digits.isdigit():
            raise ValueError(
                f"'{text}' is not a time: use {digits}min for minutes (m means meters)"
            )
    raise ValueError(f"'{text}' is not a time")


def parse_distance(text: str) -> float:
    """Parse a step distance like `400m` or `1.5km` into meters."""
    m = _DISTANCE_RE.match(text)
    if m and float(m.group(1)) > 0:
        value = float(m.group(1))
        return value * 1000.0 if m.group(2) == "km" else value
    raise ValueError(f"'{text}' is not a distance")


def parse_pace(text: str) -> int:
    """Parse a pace like `4:35` or `4:35/km` into seconds per km."""
    m = _PACE_RE.match(text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    raise ValueError(f"'{text}' is not a pace")


def _field_error(model: str, *fields: str, message: str) -> pydantic_core.ValidationError:
    """A value error that keeps its field path when it bubbles up into the plan."""
    return pydantic_core.ValidationError.from_exception_data(
        title=model,
        line_errors=[
            {
                "type": "value_error",
                "loc": fields,
                "msg": f"Value error, {message}",
                "input": None,
                "ctx": {"error": ValueError(message)},
            }
        ],
    )


class HrZoneTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["hr_zone"] = Field(description="Target type: heart-rate zone.")
    zone: int = Field(ge=1, le=5, description="The heart-rate zone, 1 (easiest) to 5 (hardest).")


class HrTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["hr"] = Field(description="Target type: heart rate in beats per minute.")
    low: int = Field(ge=30, le=250, description="The lowest heart rate in beats per minute.")
    high: int = Field(ge=30, le=250, description="The highest heart rate in beats per minute.")

    @model_validator(mode="after")
    def _range(self) -> HrTarget:
        if self.low > self.high:
            raise _field_error(
                "HrTarget",
                "low",
                "high",
                message=f"low ({self.low}) must be at most high ({self.high})",
            )
        return self


class PaceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pace"] = Field(description="Target type: pace in minutes:seconds per km.")
    fast: str = Field(
        description="The fastest pace, like 4:35 or 4:35/km (minutes:seconds per km)."
    )
    slow: str = Field(
        description="The slowest pace, like 4:45 or 4:45/km (minutes:seconds per km)."
    )

    @field_validator("fast", "slow")
    @classmethod
    def _pace(cls, v: str) -> str:
        parse_pace(v)
        return v

    @model_validator(mode="after")
    def _range(self) -> PaceTarget:
        if parse_pace(self.fast) > parse_pace(self.slow):
            raise _field_error(
                "PaceTarget",
                "fast",
                "slow",
                message=f"fast ({self.fast}) must be at least as quick as slow ({self.slow})",
            )
        return self


class CadenceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["cadence"] = Field(description="Target type: cadence in steps per minute.")
    low: int = Field(ge=60, le=250, description="The lowest cadence in steps per minute.")
    high: int = Field(ge=60, le=250, description="The highest cadence in steps per minute.")

    @model_validator(mode="after")
    def _range(self) -> CadenceTarget:
        if self.low > self.high:
            raise _field_error(
                "CadenceTarget",
                "low",
                "high",
                message=f"low ({self.low}) must be at most high ({self.high})",
            )
        return self


Target = Annotated[
    HrZoneTarget | HrTarget | PaceTarget | CadenceTarget,
    Field(discriminator="type"),
]


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["warmup", "interval", "recovery", "rest", "cooldown"] = Field(
        description="What this step is: warmup, interval, recovery, rest, or cooldown."
    )
    time: str | None = Field(
        None,
        description=(
            "How long the step lasts, like 90s, 10min or 1h30min. "
            "Leave out time and distance for a lap-button step."
        ),
    )
    distance: str | None = Field(
        None,
        description=(
            "How far the step goes, like 400m or 1.5km. "
            "Leave out time and distance for a lap-button step."
        ),
    )
    target: Target | None = Field(
        None, description="The effort target for this step, or omit for untargeted effort."
    )

    @field_validator("time")
    @classmethod
    def _time(cls, v: str | None) -> str | None:
        if v is not None:
            parse_time(v)
        return v

    @field_validator("distance")
    @classmethod
    def _distance(cls, v: str | None) -> str | None:
        if v is not None:
            parse_distance(v)
        return v

    @model_validator(mode="after")
    def _exactly_one_endpoint(self) -> Step:
        if self.time is not None and self.distance is not None:
            raise _field_error(
                "Step", "time", "distance", message="a step ends on time or distance, not both"
            )
        return self


class Repeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["repeat"] = Field(
        description="Repeat the steps below, one block per lap of the count."
    )
    count: int = Field(ge=2, le=99, description="How many times to repeat the steps.")
    steps: list[Step] = Field(
        min_length=1, description="The steps to repeat. Plain steps only; no nested repeats."
    )


WorkoutStep = Annotated[Step | Repeat, Field(discriminator="kind")]


class Workout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date = Field(description="The day the workout happens, like 2099-01-05.")
    name: str = Field(
        min_length=1, max_length=80, description="A short name for the workout."
    )
    notes: str | None = Field(None, description="Optional free-text notes for the runner.")
    steps: list[WorkoutStep] = Field(
        min_length=1, description="The steps of the workout, in order."
    )


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]{0,63}$",
        description="A lowercase kebab-case name for the plan.",
    )
    goal: str | None = Field(None, description="The goal this plan serves, in a sentence.")
    workouts: list[Workout] = Field(
        min_length=1, description="The workouts, in calendar order."
    )

    @model_validator(mode="after")
    def _unique_workouts(self) -> Plan:
        seen: dict[tuple[dt.date, str], None] = {}
        for workout in self.workouts:
            key = (workout.date, workout.name)
            if key in seen:
                raise _field_error(
                    "Plan",
                    "workouts",
                    message=f"workouts share date {workout.date} and name '{workout.name}'",
                )
            seen[key] = None
        return self


def parse_plan(data: str | dict) -> Plan:
    """Parse plan JSON text (or a dict) into a Plan. Raises PlanError."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise PlanError([f"not valid JSON: {exc.msg}"]) from exc
    return _validate(data)


def load_plan(path: Path) -> Plan:
    """Read a plan file and parse it. Missing file or bad JSON raises PlanError."""
    if not path.is_file():
        raise PlanError([f"{path}: no such file"])
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise PlanError([f"{path}: not valid JSON: {exc.msg}"]) from exc
    return _validate(data)


def _validate(data: dict) -> Plan:
    try:
        return Plan.model_validate(data)
    except Exception as exc:
        raise PlanError([_error_line(error) for error in exc.errors()]) from exc


def _error_line(error: dict) -> str:
    loc = ".".join(str(part) for part in error["loc"])
    return f"{loc}: {error['msg']}"
