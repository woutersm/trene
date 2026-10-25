"""Tests for the MCP server in src/trene/mcp_server.py."""

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from trene import mcp_server
from trene.backends import FakeBackend
from trene.backends.base import AuthError
from trene.mcp_server import server
from trene.schema import Plan

PLAN_FILE = "tests/plans/week.json"
TOOL_NAMES = [
    "validate_plan",
    "preview_plan",
    "push_plan",
    "list_pushed",
    "get_plan",
    "delete_pushed",
    "get_garmin_workout",
    "auth_status",
]


def _plan() -> Plan:
    return Plan.model_validate_json(Path(PLAN_FILE).read_text())


def _home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("TRENE_HOME", str(tmp_path))
    return tmp_path


class _AuthFailing:
    def whoami(self) -> str:
        raise AuthError("Garmin login missing or expired: run `trene login`")


def _fake() -> FakeBackend:
    return FakeBackend()


def test_lists_exactly_the_eight_tools() -> None:
    tools = asyncio.run(server.list_tools())
    assert [tool.name for tool in tools] == TOOL_NAMES
    push = next(tool for tool in tools if tool.name == "push_plan")
    assert {"plan", "dry_run", "confirm"} <= set(push.input_schema["properties"])


def test_push_default_is_dry_run_and_never_calls_backend(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)

    def boom():
        raise AssertionError("get_backend must not be called in a dry run")

    monkeypatch.setattr("trene.mcp_server.get_backend", boom)
    report = mcp_server.push_plan(_plan())
    assert report["dry_run"] is True
    assert [action["op"] for action in report["actions"]] == ["create", "create"]
    assert not (tmp_path / "state.json").exists()


def test_push_without_confirm_raises_tool_error(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("trene.mcp_server.get_backend", _fake)
    with pytest.raises(ToolError, match="confirm=true"):
        mcp_server.push_plan(_plan(), dry_run=False)
    assert not (tmp_path / "state.json").exists()


def test_push_confirmed_then_get_and_list(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    fake = FakeBackend()
    monkeypatch.setattr("trene.mcp_server.get_backend", lambda: fake)

    report = mcp_server.push_plan(_plan(), dry_run=False, confirm=True)
    assert report["dry_run"] is False
    assert sorted(fake.workouts) == [1001, 1002]

    plan_id = report["plan"]
    stored = mcp_server.get_plan(plan_id)
    assert stored == _plan().model_dump(mode="json", exclude_none=True)

    entries = mcp_server.list_pushed()
    assert len(entries) == 2
    assert entries[0] == {
        "plan": plan_id,
        "date": "2099-01-05",
        "name": "Tempo 3x10",
        "workout_id": 1001,
    }
    assert entries[1]["workout_id"] == 1002
    assert mcp_server.list_pushed("other") == []


def test_get_plan_missing_raises_tool_error(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    with pytest.raises(ToolError):
        mcp_server.get_plan("missing")


def test_delete_pushed(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    fake = FakeBackend()
    monkeypatch.setattr("trene.mcp_server.get_backend", lambda: fake)
    mcp_server.push_plan(_plan(), dry_run=False, confirm=True)

    with pytest.raises(ToolError, match="confirm=true"):
        mcp_server.delete_pushed("base-week-1", dry_run=False)
    assert sorted(fake.workouts) == [1001, 1002]

    report = mcp_server.delete_pushed("base-week-1", dry_run=False, confirm=True)
    assert [action["op"] for action in report["actions"]] == ["delete", "delete"]
    assert fake.workouts == {}
    assert mcp_server.list_pushed() == []


def test_get_plan_not_saved_raises_tool_error(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("trene.mcp_server.get_backend", _fake)
    with pytest.raises(ToolError):
        mcp_server.delete_pushed("nope")


def test_validate_plan_week_json(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    result = mcp_server.validate_plan(_plan())
    assert result["ok"] is True
    assert result["plan"] == "base-week-1"
    assert result["workouts"] == 2
    assert result["first"] == "2099-01-05"
    assert result["last"] == "2099-01-07"
    assert result["past"] == []


def test_preview_plan(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    payloads = mcp_server.preview_plan(_plan())
    assert len(payloads) == 2


def test_get_garmin_workout(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    fake = FakeBackend()
    monkeypatch.setattr("trene.mcp_server.get_backend", lambda: fake)
    mcp_server.push_plan(_plan(), dry_run=False, confirm=True)
    payload = mcp_server.get_garmin_workout(1001)
    assert payload is fake.workouts[1001]
    with pytest.raises(ToolError):
        mcp_server.get_garmin_workout(9999)


def test_auth_status_ok(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("trene.mcp_server.get_backend", _fake)
    assert mcp_server.auth_status() == {"ok": True, "name": "Fake Runner"}


def test_auth_status_auth_error(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("trene.mcp_server.get_backend", lambda: _AuthFailing())
    result = mcp_server.auth_status()
    assert result["ok"] is False
    assert "trene login" in result["error"]


def test_push_backend_error_becomes_tool_error(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    fake = FakeBackend()
    fake.fail_on.add("create")
    monkeypatch.setattr("trene.mcp_server.get_backend", lambda: fake)
    with pytest.raises(ToolError, match="fake failure"):
        mcp_server.push_plan(_plan(), dry_run=False, confirm=True)


def test_tool_schemas_have_no_refs() -> None:
    # n8n drops $defs; every schema must stand on its own.
    import asyncio
    import json

    from trene.mcp_server import build_server

    tools = asyncio.run(build_server().list_tools())
    for t in tools:
        text = json.dumps(t.input_schema)
        assert "$ref" not in text and "$defs" not in text, t.name
    validate = next(t for t in tools if t.name == "validate_plan")
    plan = validate.input_schema["properties"]["plan"]
    assert "workouts" in plan["properties"]
    step_kinds = json.dumps(plan["properties"]["workouts"])
    assert "repeat" in step_kinds and "pace" in step_kinds


def test_plan_tools_describe_the_format() -> None:
    import asyncio
    import json

    from trene.mcp_server import PLAN_FORMAT, build_server
    from trene.schema import parse_plan

    tools = {t.name: t for t in asyncio.run(build_server().list_tools())}
    for name in ("validate_plan", "preview_plan", "push_plan"):
        assert PLAN_FORMAT in tools[name].description
    assert PLAN_FORMAT not in tools["list_pushed"].description
    # The example in the description is itself a valid plan.
    example = PLAN_FORMAT.split("\n", 1)[1].split("\n- ", 1)[0]
    parse_plan(json.loads(example))
