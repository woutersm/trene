from datetime import date
from typing import Protocol


class BackendError(Exception):
    pass


class AuthError(BackendError):
    pass


class NotFoundError(BackendError):
    pass


class WorkoutBackend(Protocol):
    def create(self, payload: dict) -> int: ...

    def update(self, workout_id: int, payload: dict) -> None: ...

    def delete(self, workout_id: int) -> None: ...

    def schedule(self, workout_id: int, day: date) -> int: ...

    def get(self, workout_id: int) -> dict: ...

    def whoami(self) -> str: ...
