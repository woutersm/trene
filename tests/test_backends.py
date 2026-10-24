from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectNotFoundError,
)

from trene.backends import (
    AuthError,
    BackendError,
    FakeBackend,
    NotFoundError,
)
from trene.backends.garmin import GarminBackend, from_tokens, login_interactive

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class StubGarmin:
    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        prompt_mfa=None,
    ) -> None:
        self.email = email
        self.password = password
        self.prompt_mfa = prompt_mfa
        self.login_args: list[str] = []
        self.full_name = "Stub Runner"
        self.raise_on: set[str] = set()
        self.calls: list[tuple] = []

    def _maybe_raise(self, method: str) -> None:
        if method in self.raise_on:
            if method == "auth":
                raise GarminConnectAuthenticationError("Username and password are required")
            raise GarminConnectNotFoundError(f"{method} not found")

    def login(self, path: str) -> None:
        self._maybe_raise("auth")
        self.login_args.append(path)

    def upload_workout(self, payload: dict) -> dict:
        self._maybe_raise("auth")
        self.calls.append(("upload_workout", payload))
        return _fixture("create-response.json")

    def update_workout(self, workout_id: int, payload: dict) -> dict:
        self._maybe_raise("auth")
        self.calls.append(("update_workout", workout_id, payload))
        return {}

    def delete_workout(self, workout_id: int) -> None:
        self._maybe_raise("auth")
        self.calls.append(("delete_workout", workout_id))

    def schedule_workout(self, workout_id: int, day: str) -> dict:
        self._maybe_raise("auth")
        self.calls.append(("schedule_workout", workout_id, day))
        return _fixture("schedule-response.json")

    def get_workout_by_id(self, workout_id: int) -> dict:
        self._maybe_raise("auth")
        self.calls.append(("get_workout_by_id", workout_id))
        return {"workoutId": workout_id}

    def get_full_name(self) -> str:
        return self.full_name


class NotFoundStub(StubGarmin):
    def get_workout_by_id(self, workout_id: int) -> dict:
        raise GarminConnectNotFoundError(f"workout {workout_id} not found")


def test_create_returns_workout_id() -> None:
    stub = StubGarmin()
    backend = GarminBackend(stub)
    assert backend.create({"workoutName": "x"}) == 1739502191
    assert stub.calls == [("upload_workout", {"workoutName": "x"})]


def test_schedule_returns_schedule_id_and_iso_date() -> None:
    stub = StubGarmin()
    backend = GarminBackend(stub)
    assert backend.schedule(1739502191, date(2099, 1, 5)) == 1818472172
    assert stub.calls == [("schedule_workout", 1739502191, "2099-01-05")]


def test_get_update_delete_passthrough() -> None:
    stub = StubGarmin()
    backend = GarminBackend(stub)
    assert backend.get(42) == {"workoutId": 42}
    backend.update(42, {"workoutName": "y"})
    backend.delete(42)
    assert stub.calls == [
        ("get_workout_by_id", 42),
        ("update_workout", 42, {"workoutName": "y"}),
        ("delete_workout", 42),
    ]


def test_whoami() -> None:
    assert GarminBackend(StubGarmin()).whoami() == "Stub Runner"

    class NoName(StubGarmin):
        def get_full_name(self) -> str:
            return ""

    assert GarminBackend(NoName()).whoami() == "unknown"


def test_auth_error_translated() -> None:
    stub = StubGarmin()
    stub.raise_on.add("auth")
    with pytest.raises(AuthError) as exc:
        GarminBackend(stub).create({})
    assert "trene login" in str(exc.value)


def test_not_found_translated() -> None:
    with pytest.raises(NotFoundError):
        GarminBackend(NotFoundStub()).get(42)


def test_from_tokens_raises_auth_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("trene.backends.garmin.Garmin", StubGarmin)
    stub_holder: dict[str, StubGarmin] = {}

    class RecordingGarmin(StubGarmin):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.raise_on.add("auth")
            stub_holder["stub"] = self

    monkeypatch.setattr("trene.backends.garmin.Garmin", RecordingGarmin)
    with pytest.raises(AuthError):
        from_tokens(tmp_path)


def test_login_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    class Recording(StubGarmin):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            seen["stub"] = self
            seen["args"] = args
            seen["kwargs"] = kwargs

    monkeypatch.setattr("trene.backends.garmin.Garmin", Recording)
    tokens = tmp_path / "garminconnect"
    def prompt() -> str:
        return "123456"

    name = login_interactive("me@example.com", "pw", prompt, path=tokens)

    assert name == "Stub Runner"
    assert seen["args"] == ("me@example.com", "pw")
    assert seen["kwargs"]["prompt_mfa"] is prompt
    assert seen["stub"].login_args == [str(tokens)]
    assert tokens.is_dir()


def test_fake_ids_start_at_1001_and_5001() -> None:
    backend = FakeBackend()
    assert backend.create({"a": 1}) == 1001
    assert backend.create({"b": 2}) == 1002
    assert backend.schedule(1001, date(2099, 1, 5)) == 5001
    assert backend.schedule(1002, date(2099, 1, 7)) == 5002
    assert backend.schedules[5001] == (1001, date(2099, 1, 5))
    assert backend.calls == [
        ("create", 1001),
        ("create", 1002),
        ("schedule", 1001, date(2099, 1, 5)),
        ("schedule", 1002, date(2099, 1, 7)),
    ]


def test_fake_delete_drops_schedules() -> None:
    backend = FakeBackend()
    wid = backend.create({"a": 1})
    sid = backend.schedule(wid, date(2099, 1, 5))
    other = backend.create({"b": 2})
    other_sid = backend.schedule(other, date(2099, 1, 6))

    backend.delete(wid)

    assert wid not in backend.workouts
    assert sid not in backend.schedules
    assert other_sid in backend.schedules
    assert backend.calls[-1] == ("delete", wid)


def test_fake_unknown_ids_raise_not_found() -> None:
    backend = FakeBackend()
    with pytest.raises(NotFoundError):
        backend.update(9999, {})
    with pytest.raises(NotFoundError):
        backend.delete(9999)
    with pytest.raises(NotFoundError):
        backend.schedule(9999, date(2099, 1, 5))
    with pytest.raises(NotFoundError):
        backend.get(9999)


def test_fake_fail_on() -> None:
    backend = FakeBackend()
    wid = backend.create({})
    backend.fail_on = {"update"}
    with pytest.raises(BackendError, match="fake failure"):
        backend.update(wid, {})


def test_fake_whoami() -> None:
    assert FakeBackend().whoami() == "Fake Runner"


def test_importing_backends_does_not_load_garminconnect() -> None:
    code = (
        "import trene.backends, sys; "
        "assert 'garminconnect' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
