"""Command-line interface for Vouchcode.

Output conventions, applied consistently across every command:

    lowercase, declarative, one fact per line
    a leading verb or noun followed by a colon and the subject, as git and cargo do
    diagnostics to stderr, data to stdout, so that output can be piped
    a non-zero exit code whenever the command did not do what was asked

Commands available in Phase 1 are init, status, log, and uninstall. Commands for later
phases (verify, report, scan) are registered as they are implemented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from vouchcode import __version__
from vouchcode.capture.hooks import all_hook_statuses, install_hooks, uninstall_hooks
from vouchcode.config import MANAGED_HOOKS, RepoContext, discover_repo
from vouchcode.errors import VouchcodeError
from vouchcode.ledger.store import initialize_ledger, read_entries

app = typer.Typer(
    name="vouchcode",
    help="Local-first provenance and comprehension verification for git commits.",
    no_args_is_help=True,
    add_completion=False,
)

# stderr console for diagnostics, stdout console for data. Keeping them separate is what
# lets 'vouchcode log --json' be piped into another tool without warnings contaminating
# the stream.
#
# soft_wrap keeps a long path on one line so it stays selectable and copy-pasteable in a
# narrow terminal. markup is off because a file path or an author name may contain
# square brackets, which Rich would otherwise read as style tags. highlight is off
# because automatic coloring of numbers and paths is not how a tool in the git and cargo
# idiom presents output.
_out = Console(soft_wrap=True, markup=False, highlight=False)
_err = Console(stderr=True, soft_wrap=True, markup=False, highlight=False)


def _fail(message: str, code: int = 1) -> typer.Exit:
    """Print an error in the standard form and return an Exit to raise."""
    _err.print(f"error: {message}")
    return typer.Exit(code)


def _context() -> RepoContext:
    """Resolve the repository for a command, converting failures into clean exits."""
    try:
        return discover_repo()
    except VouchcodeError as exc:
        raise _fail(str(exc)) from exc


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option("--version", help="Print the vouchcode version and exit."),
    ] = False,
) -> None:
    """Root callback carrying options that apply to every command."""
    if version:
        _out.print(f"vouchcode {__version__}")
        raise typer.Exit(0)


@app.command()
def init(
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Replace existing hooks that were not created by vouchcode.",
        ),
    ] = False,
) -> None:
    """Install the capture hooks and create the local ledger in this repository."""
    ctx = _context()

    try:
        ledger_path = initialize_ledger(ctx)
        previous = install_hooks(ctx, force=force)
    except VouchcodeError as exc:
        raise _fail(str(exc)) from exc

    for status in previous:
        action = "replaced" if status.exists else "installed"
        _out.print(f"hook {action}: {_display(ctx, status.path)}")

    _out.print(f"ledger ready: {_display(ctx, ledger_path)}")
    _out.print(f"initialized: {ctx.root}")


@app.command()
def status() -> None:
    """Report hook installation state and ledger size for this repository."""
    ctx = _context()

    _out.print(f"repository: {ctx.root}")

    if not ctx.is_initialized:
        _out.print("initialized: no")
        _err.print("error: repository is not initialized. run 'vouchcode init'")
        raise typer.Exit(1)

    _out.print("initialized: yes")

    try:
        statuses = all_hook_statuses(ctx)
        entries = read_entries(ctx.ledger_path)
    except VouchcodeError as exc:
        raise _fail(str(exc)) from exc

    missing = 0
    for hook in statuses:
        if not hook.exists:
            state = "missing"
            missing += 1
        elif hook.owned:
            state = "installed"
        else:
            state = "present, not owned by vouchcode"
            missing += 1
        _out.print(f"hook {hook.name}: {state}")

    _out.print(f"ledger entries: {len(entries)}")

    if missing:
        _err.print(
            f"error: {missing} of {len(MANAGED_HOOKS)} hooks are not active. "
            "run 'vouchcode init' to repair"
        )
        raise typer.Exit(1)


@app.command("log")
def log_command(
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", min=1, help="Show only the newest N entries."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit raw ledger entries as JSON."),
    ] = False,
) -> None:
    """Print ledger entries, newest last."""
    ctx = _context()

    try:
        ctx.require_initialized()
        entries = read_entries(ctx.ledger_path)
    except VouchcodeError as exc:
        raise _fail(str(exc)) from exc

    if limit is not None:
        entries = entries[-limit:]

    if as_json:
        payload = [entry.to_dict() for entry in entries]
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not entries:
        _out.print("ledger is empty")
        return

    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("commit", style="bold")
    table.add_column("timestamp")
    table.add_column("attribution")
    table.add_column("files", justify="right")
    table.add_column("author")

    for entry in entries:
        table.add_row(
            entry.commit[:10],
            entry.timestamp,
            str(entry.attribution.get("status", "unclassified")),
            str(len(entry.files)),
            entry.author_name,
        )

    _out.print(table)


@app.command()
def uninstall() -> None:
    """Remove the capture hooks, leaving the ledger and any foreign hooks in place."""
    ctx = _context()

    try:
        previous = uninstall_hooks(ctx)
    except VouchcodeError as exc:
        raise _fail(str(exc)) from exc

    for hook in previous:
        if hook.exists and hook.owned:
            _out.print(f"hook removed: {_display(ctx, hook.path)}")
        elif hook.blocked:
            _out.print(f"hook left in place, not owned by vouchcode: {hook.path}")

    _out.print(f"ledger retained: {_display(ctx, ctx.ledger_path)}")


def _display(ctx: RepoContext, path: Path) -> str:
    """Render a path relative to the repository root where possible.

    Absolute paths in routine output are noise; a path outside the repository, such as a
    hooks directory relocated by core.hooksPath, is shown in full because the location
    is the informative part.
    """
    try:
        return str(path.relative_to(ctx.root)).replace("\\", "/")
    except ValueError:
        return str(path)


if __name__ == "__main__":
    app()
