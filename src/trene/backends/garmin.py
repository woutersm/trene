from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from .base import AuthError, BackendError, NotFoundError

DEFAULT_TOKENS = Path("~/.garminconnect").expanduser()

def _translate[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except GarminConnectAuthenticationError as e:
        raise AuthError("Garmin login missing or expired: run `trene login`") from e
    except GarminConnectNotFoundError as e:
        raise NotFoundError(str(e)) from e
    except (GarminConnectConnectionError, GarminConnectTooManyRequestsError) as e:
        raise BackendError(str(e)) from e


class GarminBackend:
    def __init__(self, client: Garmin) -> None:
        self.client = client

    def create(self, payload: dict) -> int:
        return _translate(
            lambda: int(self.client.upload_workout(payload)["workoutId"])
        )

    def update(self, workout_id: int, payload: dict) -> None:
        _translate(lambda: self.client.update_workout(workout_id, payload))

    def delete(self, workout_id: int) -> None:
        _translate(lambda: self.client.delete_workout(workout_id))

    def schedule(self, workout_id: int, day: date) -> int:
        return _translate(
            lambda: int(
                self.client.schedule_workout(workout_id, day.isoformat())[
                    "workoutScheduleId"
                ]
            )
        )

    def get(self, workout_id: int) -> dict:
        return _translate(lambda: self.client.get_workout_by_id(workout_id))

    def whoami(self) -> str:
        return _translate(lambda: self.client.get_full_name()) or "unknown"


def from_tokens(path: Path = DEFAULT_TOKENS) -> GarminBackend:
    client = Garmin()
    _translate(lambda: client.login(str(path)))
    return GarminBackend(client)


def login_interactive(
    email: str,
    password: str,
    prompt_mfa: Callable[[], str],
    path: Path = DEFAULT_TOKENS,
) -> str:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    client = Garmin(email, password, prompt_mfa=prompt_mfa)
    _translate(lambda: client.login(str(path)))
    return client.get_full_name() or "unknown"
