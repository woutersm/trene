"""The `trene-mcp` server: an MCP server over stdio for Claude, exposing
the same library functions as the CLI as tools."""

from __future__ import annotations

from datetime import date

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from trene.backends import BackendError, FakeBackend, WorkoutBackend
from trene.backends.garmin import from_tokens
from trene.compiler import compile_plan
from trene.schema import Plan, PlanError
from trene.state import trene_home
from trene.sync import delete_plan
from trene.sync import get_plan as load_saved_plan
from trene.sync import list_pushed as list_pushed_entries
from trene.sync import push as push_sync

INSTRUCTIONS = (
    "trene puts running workouts on the user's Garmin calendar. Build the plan as "
    "JSON, call validate_plan, then push_plan with dry_run=true and show the user "
    "the actions. Call push_plan with dry_run=false and confirm=true only after "
    "the user agrees. To change a plan, call get_plan, edit it, and push it again "
    "under the same plan id: changed workouts are updated, removed ones deleted, "
    "and workouts dated before today are never touched."
)

server = MCPServer("trene", instructions=INSTRUCTIONS)


def get_backend() -> WorkoutBackend:
    return from_tokens(trene_home() / "garmin-tokens")


def _write_guarded(dry_run: bool, confirm: bool) -> None:
    if not dry_run and not confirm:
        raise ToolError(
            "Writing to Garmin needs confirm=true. Show the user the dry run first."
        )


@server.tool()
def validate_plan(plan: Plan) -> dict:
    """Check a plan and summarise it before doing anything else."""
    today = date.today()
    return {
        "ok": True,
        "plan": plan.plan,
        "workouts": len(plan.workouts),
        "first": min(w.date for w in plan.workouts).isoformat(),
        "last": max(w.date for w in plan.workouts).isoformat(),
        "past": sorted(
            w.name for w in plan.workouts if w.date < today
        ),
    }


@server.tool()
def preview_plan(plan: Plan) -> list[dict]:
    """Show the Garmin payloads a plan compiles to, without pushing."""
    return compile_plan(plan)


@server.tool()
def push_plan(
    plan: Plan, dry_run: bool = True, confirm: bool = False
) -> dict:
    """Push a plan to the Garmin calendar; dry_run previews the actions."""
    _write_guarded(dry_run, confirm)
    backend = FakeBackend() if dry_run else get_backend()
    try:
        return push_sync(
            plan, backend, trene_home(), date.today(), dry_run=dry_run
        ).to_dict()
    except (PlanError, BackendError) as e:
        raise ToolError(str(e)) from e


@server.tool()
def list_pushed(plan_id: str | None = None) -> list[dict]:
    """List the workouts trene has pushed, optionally for one plan."""
    return [
        {
            "plan": entry.plan,
            "date": entry.date.isoformat(),
            "name": entry.name,
            "workout_id": entry.workout_id,
        }
        for entry in list_pushed_entries(trene_home(), plan_id)
    ]


@server.tool()
def get_plan(plan_id: str) -> dict:
    """Fetch a saved plan so it can be edited and re-pushed."""
    try:
        return load_saved_plan(plan_id, trene_home()).model_dump(
            mode="json", exclude_none=True
        )
    except PlanError as e:
        raise ToolError(str(e)) from e


@server.tool()
def delete_pushed(
    plan_id: str, dry_run: bool = True, confirm: bool = False
) -> dict:
    """Delete all of a saved plan's workouts from the Garmin calendar."""
    _write_guarded(dry_run, confirm)
    backend = FakeBackend() if dry_run else get_backend()
    try:
        return delete_plan(
            plan_id, backend, trene_home(), date.today(), dry_run=dry_run
        ).to_dict()
    except (PlanError, BackendError) as e:
        raise ToolError(str(e)) from e


@server.tool()
def get_garmin_workout(workout_id: int) -> dict:
    """Fetch one workout as it is stored on Garmin."""
    try:
        return get_backend().get(workout_id)
    except BackendError as e:
        raise ToolError(str(e)) from e


@server.tool()
def auth_status() -> dict:
    """Show the name of the Garmin account currently logged in."""
    try:
        return {"ok": True, "name": get_backend().whoami()}
    except BackendError as e:
        return {"ok": False, "error": str(e)}


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
