"""The local state file and saved plan copies, under trene's home folder."""

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .schema import Plan, PlanError


def trene_home() -> Path:
    return Path(os.environ.get("TRENE_HOME") or "~/.trene").expanduser()


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
    def load(cls, home: Path) -> "State":
        path = home / "state.json"
        if not path.is_file():
            return cls(entries=[])
        data = json.loads(path.read_text())
        entries = [
            Entry(
                plan=record["plan"],
                date=date.fromisoformat(record["date"]),
                name=record["name"],
                workout_id=record["workout_id"],
                schedule_id=record["schedule_id"],
                payload_hash=record["payload_hash"],
            )
            for record in data["workouts"]
        ]
        return cls(entries=entries)

    def save(self, home: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "workouts": [
                {
                    "plan": entry.plan,
                    "date": entry.date.isoformat(),
                    "name": entry.name,
                    "workout_id": entry.workout_id,
                    "schedule_id": entry.schedule_id,
                    "payload_hash": entry.payload_hash,
                }
                for entry in self.entries
            ],
        }
        path = home / "state.json"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)

    def find(self, plan: str, day: date, name: str) -> Entry | None:
        for entry in self.entries:
            if entry.plan == plan and entry.date == day and entry.name == name:
                return entry
        return None

    def for_plan(self, plan: str) -> list[Entry]:
        return [entry for entry in self.entries if entry.plan == plan]


def save_plan_copy(plan: Plan, home: Path) -> Path:
    path = home / "plans" / f"{plan.plan}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2, exclude_none=True))
    return path


def load_plan_copy(plan_id: str, home: Path) -> Plan:
    path = home / "plans" / f"{plan_id}.json"
    if not path.is_file():
        raise PlanError([f"no saved plan '{plan_id}'"])
    return Plan.model_validate_json(path.read_text())
