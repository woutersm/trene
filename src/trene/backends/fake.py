from datetime import date

from .base import BackendError, NotFoundError


class FakeBackend:
    workouts: dict[int, dict]
    schedules: dict[int, tuple[int, date]]
    calls: list[tuple]
    fail_on: set[str]

    def __init__(self) -> None:
        self.workouts = {}
        self.schedules = {}
        self.calls = []
        self.fail_on = set()
        self._next_workout_id = 1001
        self._next_schedule_id = 5001

    def _maybe_fail(self, method: str) -> None:
        if method in self.fail_on:
            raise BackendError("fake failure")

    def _check_workout(self, workout_id: int) -> None:
        if workout_id not in self.workouts:
            raise NotFoundError(f"workout {workout_id} not found")

    def create(self, payload: dict) -> int:
        self._maybe_fail("create")
        workout_id = self._next_workout_id
        self._next_workout_id += 1
        self.workouts[workout_id] = payload
        self.calls.append(("create", workout_id))
        return workout_id

    def update(self, workout_id: int, payload: dict) -> None:
        self._maybe_fail("update")
        self._check_workout(workout_id)
        self.workouts[workout_id] = payload
        self.calls.append(("update", workout_id))

    def delete(self, workout_id: int) -> None:
        self._maybe_fail("delete")
        self._check_workout(workout_id)
        del self.workouts[workout_id]
        self.schedules = {
            sid: entry
            for sid, entry in self.schedules.items()
            if entry[0] != workout_id
        }
        self.calls.append(("delete", workout_id))

    def schedule(self, workout_id: int, day: date) -> int:
        self._maybe_fail("schedule")
        self._check_workout(workout_id)
        schedule_id = self._next_schedule_id
        self._next_schedule_id += 1
        self.schedules[schedule_id] = (workout_id, day)
        self.calls.append(("schedule", workout_id, day))
        return schedule_id

    def get(self, workout_id: int) -> dict:
        self._maybe_fail("get")
        self._check_workout(workout_id)
        return self.workouts[workout_id]

    def whoami(self) -> str:
        self._maybe_fail("whoami")
        return "Fake Runner"
