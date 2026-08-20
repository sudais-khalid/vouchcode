"""Command-line interface for Vouchcode.

Output conventions, applied consistently across every command:

    lowercase, declarative, one fact per line
    a leading verb or noun followed by a colon and the subject, as git and cargo do
    diagnostics to stderr, data to stdout, so that output can be piped
    a non-zero exit code whenever the command did not do what was asked

Commands available are init, status, log, verify, and uninstall. Reporting commands
arrive with Phase 5.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from vouchcode import __version__
from vouchcode.capture.hooks import all_hook_statuses, install_hooks, uninstall_hooks
from vouchcode.config import MANAGED_HOOKS, RepoContext, discover_repo
from vouchcode.errors import VouchcodeError
from vouchcode.ledger.entry import LedgerEntry
from vouchcode.ledger.store import initialize_ledger, read_entries
from vouchcode.ledger.verification import (
    STATUS_UNVERIFIABLE_VERSION,
    verify_ledger,
)

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

    for line in format_log_table(entries):
        _out.print(line)


@app.command()
def verify(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Print a line for every entry."),
    ] = False,
) -> None:
    """Check the ledger's hash chain and signatures, reporting per entry.

    Exit codes are distinct on purpose, because the two failure modes call for different
    responses. An integrity failure means the record was altered and exits 1. An entry
    that is sound but whose fingerprints cannot be compared against this interpreter
    exits 0: nothing is wrong with the ledger, and treating it as a failure would train
    a reader to ignore the one signal that says a report needs a second look.
    """
    ctx = _context()

    try:
        ctx.require_initialized()
    except VouchcodeError as exc:
        raise _fail(str(exc)) from exc

    result = verify_ledger(ctx.ledger_path)

    if result.error:
        raise _fail(result.error)

    if not result.entries:
        _out.print("ledger is empty, nothing to verify")
        return

    if not result.public_key_present:
        _err.print(
            "warning: ledger carries no public key, so signatures cannot be checked"
        )

    for entry in result.entries:
        # Every failing entry is shown. Clean entries are shown only on request, so that
        # a healthy ledger of a thousand commits reports in one line rather than a
        # thousand.
        interesting = entry.status != "verified"
        if verbose or interesting:
            _out.print(f"{entry.index:>5}  {entry.commit[:10]}  {entry.status}")
            if interesting:
                _out.print(f"{'':>5}  {'':>10}  {entry.detail}")

    _out.print("")
    for status, count in sorted(result.counts().items()):
        _out.print(f"{status}: {count}")

    failure = result.first_failure
    if failure is not None:
        _err.print(
            f"error: ledger integrity failed. first failure at entry {failure.index}, "
            f"commit {failure.commit[:10]}: {failure.detail}"
        )
        raise typer.Exit(1)

    non_comparable = result.counts().get(STATUS_UNVERIFIABLE_VERSION, 0)
    if non_comparable:
        # Reported on stdout, not stderr, and without a non-zero exit. This is a
        # statement about what the ledger can prove, not a defect in it.
        _out.print(
            f"note: {non_comparable} entries were fingerprinted under different "
            "conditions and cannot be compared against this interpreter"
        )

    _out.print("chain intact")


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


def format_log_table(entries: list[LedgerEntry]) -> list[str]:
    """Render ledger entries as aligned plain-text rows, one string per line.

    Deliberately not a Rich table. A table sized to the terminal truncates the timestamp
    column in a narrow window, and Rich marks the truncation with a Unicode ellipsis
    that does not survive a console using a legacy code page, so a commit timestamp
    could display corrupted. Columns are sized to their contents instead: a long line
    wraps in the terminal rather than losing characters, and output stays ASCII.

    Returned as a list rather than printed so that the layout is testable without
    capturing a console.
    """
    headers = ("commit", "type", "timestamp", "attribution", "files", "author")
    rows: list[tuple[str, ...]] = [headers]

    for entry in entries:
        rows.append(
            (
                entry.commit[:10],
                entry.entry_type,
                entry.timestamp,
                str(entry.attribution.get("status", "unclassified")),
                # A merge carries no file list at all, which is a different statement
                # from carrying an empty one, so it renders as a dash, not as zero.
                "-" if entry.files is None else str(len(entry.files)),
                entry.author_name,
            )
        )

    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
    # The files column is a count and reads correctly only when right aligned.
    right_aligned = {headers.index("files")}

    lines = []
    for row in rows:
        cells = [
            cell.rjust(widths[i]) if i in right_aligned else cell.ljust(widths[i])
            for i, cell in enumerate(row)
        ]
        lines.append("  ".join(cells).rstrip())
    return lines


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
