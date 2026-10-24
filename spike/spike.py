# /// script
# requires-python = ">=3.11"
# dependencies = ["garminconnect==0.3.20"]
# ///
"""Garmin spike: check that the private workout API works and capture fixtures.

Run from the repo root:
    uv run spike/spike.py login
    uv run spike/spike.py fetch
    uv run spike/spike.py push
    uv run spike/spike.py cleanup
"""

import datetime as dt
import getpass
import json
import re
import sys
from pathlib import Path

from garminconnect import Garmin

TOKENS = Path("~/.garminconnect").expanduser()
FIXTURES = Path(__file__).parent / "fixtures"
PREFIX = "SPIKE"

# The handoff's guessed payload: one 15-minute warmup in HR zone 2.
MINIMAL = {
    "workoutName": f"{PREFIX} push test",
    "description": "created by spike.py",
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
    "workoutSegments": [{
        "segmentOrder": 1,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSteps": [{
            "type": "ExecutableStepDTO",
            "stepOrder": 1,
            "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
            "endConditionValue": 900,
            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
            "zoneNumber": 2,
        }],
    }],
}


def client(fresh: bool = False) -> Garmin:
    TOKENS.mkdir(mode=0o700, exist_ok=True)
    if fresh:
        g = Garmin(
            input("Garmin email: ").strip(),
            getpass.getpass("Garmin password: "),
            prompt_mfa=lambda: input("MFA code: ").strip(),
        )
    else:
        g = Garmin()
    g.login(str(TOKENS))
    return g


def save(name: str, data) -> None:
    FIXTURES.mkdir(exist_ok=True)
    path = FIXTURES / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"  saved {path}")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def spike_workouts(g: Garmin) -> list[dict]:
    return [w for w in g.get_workouts(0, 200) if w.get("workoutName", "").startswith(PREFIX)]


def cmd_login() -> None:
    g = client(fresh=True)
    print(f"Login OK. Tokens saved in {TOKENS}")
    workouts = g.get_workouts(0, 5)
    print(f"Workout list OK: got {len(workouts)} workouts. Plain HTTP works.")


def cmd_fetch() -> None:
    g = client()
    found = spike_workouts(g)
    if not found:
        sys.exit(f"No workouts named '{PREFIX} ...' found. Build them in the web UI first.")
    save("list", found)
    for w in found:
        save(slug(w["workoutName"]), g.get_workout_by_id(w["workoutId"]))
    print(f"Fetched {len(found)} workouts.")


def cmd_push() -> None:
    g = client()
    created = g.upload_workout(MINIMAL)
    save("push-create-response", created)
    wid = created["workoutId"]
    print(f"Created workout {wid}")

    full = g.get_workout_by_id(wid)
    full["workoutName"] = f"{PREFIX} push test v2"
    save("push-update-response", g.update_workout(wid, full))
    print("Renamed it to 'SPIKE push test v2' (tests update)")

    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    save("push-schedule-response", g.schedule_workout(wid, tomorrow))
    month = g.get_scheduled_workouts(int(tomorrow[:4]), int(tomorrow[5:7]))
    items = [i for i in month.get("calendarItems", []) if i.get("date") == tomorrow]
    save("push-calendar-items", items)
    print(f"Scheduled for {tomorrow}. Now sync your watch and check it.")


def cmd_cleanup() -> None:
    g = client()
    for w in spike_workouts(g):
        g.delete_workout(w["workoutId"])
        print(f"  deleted {w['workoutName']}")
    left = spike_workouts(g)
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    month = g.get_scheduled_workouts(tomorrow.year, tomorrow.month)
    on_cal = [i for i in month.get("calendarItems", []) if i.get("date") == tomorrow.isoformat()]
    save("cleanup-calendar-after", on_cal)
    print(f"Left in library: {len(left)}. Items on {tomorrow} calendar: {len(on_cal)}.")


COMMANDS = {"login": cmd_login, "fetch": cmd_fetch, "push": cmd_push, "cleanup": cmd_cleanup}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"usage: uv run spike/spike.py {{{'|'.join(COMMANDS)}}}")
    try:
        COMMANDS[sys.argv[1]]()
    except Exception as e:  # noqa: BLE001 - print any failure plainly
        print(f"\nFAILED: {type(e).__name__}: {e}")
        if "403" in str(e):
            print("A 403 likely means Cloudflare blocks this client. Stop here and tell Claude.")
        sys.exit(1)
