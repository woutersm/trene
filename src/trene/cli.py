"""The `trene` command-line interface, over the trene library."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import NoReturn

import typer

from trene.backends import BackendError, FakeBackend, WorkoutBackend
from trene.backends.garmin import from_tokens, login_interactive
from trene.compiler import compile_plan
from trene.schema import PlanError, load_plan
from trene.state import trene_home
from trene.sync import Report, delete_plan, list_pushed
from trene.sync import push as push_sync

app = typer.Typer(no_args_is_help=True, help="Push JSON running plans to Garmin Connect.")


def get_backend() -> WorkoutBackend:
    return from_tokens(trene_home() / "garmin-tokens")


@app.command()
def login() -> None:
    """Log in to Garmin and save tokens under trene's home folder."""
    home = trene_home()
    name = login_interactive(
        email=typer.prompt("Garmin email"),
        password=typer.prompt("Garmin password", hide_input=True),
        prompt_mfa=lambda: typer.prompt("MFA code"),
        path=home / "garmin-tokens",
    )
    typer.echo(f"Logged in as {name}. Tokens saved in {home / 'garmin-tokens'}.")


@app.command()
def status() -> None:
    """Show the name of the Garmin account currently logged in."""
    try:
        typer.echo(f"Logged in as {get_backend().whoami()}.")
    except BackendError as e:
        _backend_error(e)


@app.command()
def validate(plan: Path) -> None:
    """Check a plan file without touching Garmin."""
    try:
        parsed = load_plan(plan)
    except PlanError as e:
        _plan_error(e)
    first = min(w.date for w in parsed.workouts)
    last = max(w.date for w in parsed.workouts)
    typer.echo(
        f"OK: {parsed.plan}, {len(parsed.workouts)} workouts, "
        f"{first.isoformat()} to {last.isoformat()}"
    )


@app.command()
def preview(plan: Path) -> None:
    """Print the Garmin payloads a plan compiles to, as JSON."""
    try:
        payloads = compile_plan(load_plan(plan))
    except PlanError as e:
        _plan_error(e)
    typer.echo(json.dumps(payloads, indent=2))


@app.command()
def push(plan: Path, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Push a plan to the Garmin calendar, updating state under the home folder."""
    try:
        parsed = load_plan(plan)
        backend = FakeBackend() if dry_run else get_backend()
        report = push_sync(
            parsed, backend, trene_home(), date.today(), dry_run=dry_run
        )
    except PlanError as e:
        _plan_error(e)
    except BackendError as e:
        _backend_error(e)
    _print_report(report)


@app.command()
def list(plan: str | None = typer.Option(None, "--plan")) -> None:
    """List the workouts trene has pushed, optionally for one plan."""
    entries = list_pushed(trene_home(), plan)
    if not entries:
        typer.echo("Nothing pushed yet.")
        return
    for entry in entries:
        typer.echo(f"{entry.date.isoformat()}  {entry.plan}  {entry.name}  #{entry.workout_id}")


@app.command()
def delete(plan_id: str, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Delete all of a saved plan's workouts from the Garmin calendar."""
    try:
        backend = FakeBackend() if dry_run else get_backend()
        report = delete_plan(plan_id, backend, trene_home(), date.today(), dry_run=dry_run)
    except PlanError as e:
        _plan_error(e)
    except BackendError as e:
        _backend_error(e)
    _print_report(report)


def _print_report(report: Report) -> None:
    for action in report.actions:
        typer.echo(f"{action.op:<9} {action.date.isoformat()}  {action.name}")
    counts = report.counts()
    typer.echo(
        f"{counts['create']} create, {counts['update']} update, "
        f"{counts['delete']} delete, {counts['unchanged']} unchanged, "
        f"{counts['past']} past"
    )
    if report.dry_run:
        typer.echo("Dry run: nothing was sent to Garmin.")


def _plan_error(error: PlanError) -> NoReturn:
    for line in error.errors:
        typer.echo(line)
    raise typer.Exit(code=1)


def _backend_error(error: BackendError) -> NoReturn:
    typer.echo(f"Garmin error: {error}")
    raise typer.Exit(code=2)
