"""Tests for the typer CLI in src/trene/cli.py."""

import json
from pathlib import Path

from typer.testing import CliRunner

from trene.backends import FakeBackend
from trene.backends.base import AuthError, BackendError
from trene.cli import app

runner = CliRunner()

PLAN_FILE = "tests/plans/week.json"


def _home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("TRENE_HOME", str(tmp_path))
    return tmp_path


def test_validate_ok(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["validate", PLAN_FILE])
    assert result.exit_code == 0
    assert "OK: base-week-1, 2 workouts, 2099-01-05 to 2099-01-07" in result.output


def test_validate_repeat_in_repeat_exits_1(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "plan": "bad",
        "workouts": [
            {
                "date": "2099-01-05",
                "name": "W",
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
        ],
    }))
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 1
    assert "repeat" in result.output and ".steps." in result.output


def test_validate_missing_file_exits_1(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["validate", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "no such file" in result.output


def test_preview_prints_json(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["preview", PLAN_FILE])
    assert result.exit_code == 0
    payloads = json.loads(result.output)
    assert len(payloads) == 2


def test_push_dry_run_never_calls_backend(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)

    def boom() -> FakeBackend:
        raise AssertionError("get_backend must not be called in a dry run")

    monkeypatch.setattr("trene.cli.get_backend", boom)
    result = runner.invoke(app, ["push", PLAN_FILE, "--dry-run"])
    assert result.exit_code == 0
    create_lines = [
        line for line in result.output.splitlines() if line.startswith("create")
    ]
    assert len(create_lines) == 2
    assert "Dry run: nothing was sent to Garmin." in result.output
    assert "2 create" in result.output


def test_push_then_list(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    fake = FakeBackend()
    monkeypatch.setattr("trene.cli.get_backend", lambda: fake)
    result = runner.invoke(app, ["push", PLAN_FILE])
    assert result.exit_code == 0
    assert sorted(fake.workouts) == [1001, 1002]

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line]
    assert len(lines) == 2
    assert "2099-01-05" in lines[0] and "#1001" in lines[0]
    assert "base-week-1" in lines[0] and "Tempo 3x10" in lines[0]
    assert "2099-01-07" in lines[1] and "#1002" in lines[1]

    result = runner.invoke(app, ["list", "--plan", "base-week-1"])
    assert result.exit_code == 0
    assert "#1001" in result.output and "#1002" in result.output

    result = runner.invoke(app, ["list", "--plan", "other"])
    assert result.exit_code == 0
    assert "Nothing pushed yet." in result.output


def test_list_empty(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "Nothing pushed yet." in result.output


def test_delete(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    fake = FakeBackend()
    monkeypatch.setattr("trene.cli.get_backend", lambda: fake)
    result = runner.invoke(app, ["push", PLAN_FILE])
    assert result.exit_code == 0

    result = runner.invoke(app, ["delete", "base-week-1"])
    assert result.exit_code == 0
    delete_lines = [
        line for line in result.output.splitlines() if line.startswith("delete")
    ]
    assert len(delete_lines) == 2
    assert fake.workouts == {}

    result = runner.invoke(app, ["list"])
    assert "Nothing pushed yet." in result.output


def test_delete_unknown_plan_exits_1(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("trene.cli.get_backend", lambda: FakeBackend())
    result = runner.invoke(app, ["delete", "nope"])
    assert result.exit_code == 1
    assert "no saved plan" in result.output


class _AuthFailing:
    def whoami(self) -> str:
        raise AuthError("Garmin login missing or expired: run `trene login`")


def test_status_auth_error_exits_2(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("trene.cli.get_backend", lambda: _AuthFailing())
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 2
    assert "Garmin error:" in result.output
    assert "trene login" in result.output


def test_status_backend_error_exits_2(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)

    class Failing:
        def whoami(self) -> str:
            raise BackendError("connection refused")

    monkeypatch.setattr("trene.cli.get_backend", lambda: Failing())
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 2
    assert "Garmin error: connection refused" in result.output


def test_status_ok(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("trene.cli.get_backend", lambda: FakeBackend())
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert result.output.startswith("Logged in as ")


def test_login(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    calls: list[tuple] = []

    def fake_login(email: str, password: str, prompt_mfa, path: Path) -> str:
        calls.append((email, password, prompt_mfa(), str(path)))
        return "Martijn Wouters"

    monkeypatch.setattr("trene.cli.login_interactive", fake_login)
    result = runner.invoke(
        app, ["login"], input="a@b.c\nsecret\n123456\n"
    )
    assert result.exit_code == 0
    assert "Logged in as Martijn Wouters" in result.output
    assert f"Tokens saved in {tmp_path}/garmin-tokens" in result.output
    assert calls == [("a@b.c", "secret", "123456", f"{tmp_path}/garmin-tokens")]


def test_push_backend_error_exits_2(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    fake = FakeBackend()
    fake.fail_on.add("create")
    monkeypatch.setattr("trene.cli.get_backend", lambda: fake)
    result = runner.invoke(app, ["push", PLAN_FILE])
    assert result.exit_code == 2
    assert "Garmin error:" in result.output


def test_login_failure_exits_2(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)

    def bad_login(**kwargs: object) -> str:
        raise AuthError("Garmin login missing or expired: run `trene login`")

    monkeypatch.setattr("trene.cli.login_interactive", bad_login)
    result = runner.invoke(app, ["login"], input="a@b.c\nwrong\n123456\n")
    assert result.exit_code == 2
    assert "Garmin error:" in result.output
