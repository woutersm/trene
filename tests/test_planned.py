"""Tests for src/trene/planned.py and its route, GET /api/planned."""

from datetime import date
from pathlib import Path

from starlette.testclient import TestClient

from trene.backends import FakeBackend
from trene.mcp_server import create_http_app
from trene.planned import planned
from trene.schema import parse_plan
from trene.sync import delete_plan, push

BASE = "https://trene.example"
TOKEN = "a-token-of-at-least-24-chars"

PLAN = {
    "plan": "base-week-1",
    "goal": "Run 10K under 50 minutes in March",
    "workouts": [
        {
            "date": "2099-10-29",
            "name": "Easy 8k",
            "steps": [
                {"kind": "interval", "distance": "8km", "target": {"type": "hr_zone", "zone": 2}}
            ],
        },
        {
            "date": "2099-10-27",
            "name": "Tempo 3x10",
            "notes": "Easy if the legs are heavy",
            "steps": [
                {"kind": "warmup", "time": "10min", "target": {"type": "hr_zone", "zone": 2}},
                {
                    "kind": "repeat",
                    "count": 3,
                    "steps": [
                        {
                            "kind": "interval",
                            "time": "10min",
                            "target": {"type": "pace", "fast": "4:35", "slow": "4:45"},
                        },
                        {"kind": "recovery", "time": "2min"},
                    ],
                },
                {"kind": "cooldown"},
            ],
        },
    ],
}

TEMPO = {
    "plan": "base-week-1",
    "goal": "Run 10K under 50 minutes in March",
    "date": "2099-10-27",
    "name": "Tempo 3x10",
    "notes": "Easy if the legs are heavy",
    "garmin_id": 1002,
    # 600 + 3 * (600 + 120) seconds; 3 * 600 s at 4:40/km is 6429 m.
    "time_s": 2760,
    "distance_m": 6429,
    "steps": [
        {
            "kind": "warmup",
            "time_s": 600,
            "distance_m": None,
            "target": {"type": "hr_zone", "zone": 2},
        },
        {
            "kind": "repeat",
            "count": 3,
            "steps": [
                {
                    "kind": "interval",
                    "time_s": 600,
                    "distance_m": None,
                    "target": {"type": "pace", "fast_s_km": 275, "slow_s_km": 285},
                },
                {"kind": "recovery", "time_s": 120, "distance_m": None, "target": None},
            ],
        },
        {"kind": "cooldown", "time_s": None, "distance_m": None, "target": None},
    ],
}


def _pushed(home: Path) -> FakeBackend:
    backend = FakeBackend()
    push(parse_plan(PLAN), backend, home, date(2099, 1, 1))
    return backend


def test_planned_lists_pushed_workouts_by_date_with_steps_in_numbers(tmp_path: Path) -> None:
    _pushed(tmp_path)
    out = planned(tmp_path)
    assert [w["name"] for w in out["workouts"]] == ["Tempo 3x10", "Easy 8k"]
    assert out["workouts"][0] == TEMPO
    easy = out["workouts"][1]
    assert easy["notes"] is None
    # An HR target gives no pace, so the 8 km step adds no time.
    assert (easy["time_s"], easy["distance_m"]) == (None, 8000)
    assert easy["steps"][0]["distance_m"] == 8000


def test_planned_is_empty_without_state_and_drops_deleted_plans(tmp_path: Path) -> None:
    assert planned(tmp_path) == {"workouts": []}
    backend = _pushed(tmp_path)
    delete_plan("base-week-1", backend, tmp_path, date(2099, 1, 1))
    assert planned(tmp_path) == {"workouts": []}


def test_distance_step_with_pace_counts_in_time(tmp_path: Path) -> None:
    plan = {
        "plan": "p",
        "workouts": [
            {
                "date": "2099-01-05",
                "name": "Reps",
                "steps": [
                    {
                        "kind": "interval",
                        "distance": "1km",
                        "target": {"type": "pace", "fast": "4:00", "slow": "4:10"},
                    }
                ],
            }
        ],
    }
    push(parse_plan(plan), FakeBackend(), tmp_path, date(2099, 1, 1))
    workout = planned(tmp_path)["workouts"][0]
    assert (workout["time_s"], workout["distance_m"]) == (245, 1000)


def _client(home: Path, token: str | None = TOKEN) -> TestClient:
    app = create_http_app(BASE, "correct horse battery", home, api_token=token)
    return TestClient(app, base_url=BASE)


def test_route_needs_the_token(tmp_path: Path) -> None:
    _pushed(tmp_path)
    with _client(tmp_path) as c:
        assert c.get("/api/planned").status_code == 401
        assert c.get("/api/planned", headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = c.get("/api/planned", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert r.json()["workouts"][0] == TEMPO


def test_route_is_absent_without_a_token(tmp_path: Path) -> None:
    with _client(tmp_path, token=None) as c:
        r = c.get("/api/planned", headers={"Authorization": "Bearer "})
        assert r.status_code in (401, 404)
        assert "workouts" not in r.text
