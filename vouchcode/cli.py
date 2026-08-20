"""Command-line interface for Vouchcode.

Output conventions, applied consistently across every command:

    lowercase, declarative, one fact per line
    a leading verb or noun followed by a colon and the subject, as git and cargo do
    diagnostics to stderr, data to stdout, so that output can be piped
    a non-zero exit code whenever the command did not do what was asked

Commands: init, status, log, key, verify, report, verify-report, scan, uninstall.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from git import Repo
from rich.console import Console

from vouchcode import __version__
from vouchcode.capture.hooks import all_hook_statuses, install_hooks, uninstall_hooks
from vouchcode.config import MANAGED_HOOKS, RepoContext, discover_repo
from vouchcode.errors import VouchcodeError
from vouchcode.ledger.entry import LedgerEntry
from vouchcode.ledger.signing import (
    SigningError,
    key_digest,
    key_fingerprint,
    load_public_key,
    public_key_b64,
)
from vouchcode.ledger.store import append_entry, initialize_ledger, read_entries
from vouchcode.ledger.verification import (
    STATUS_UNVERIFIABLE_VERSION,
    verify_ledger,
)
from vouchcode.reporting.json_report import (
    build_report,
    read_report,
    verify_report,
    write_report,
)
from vouchcode.reporting.pdf_report import render_pdf
from vouchcode.reporting.scan import scan_history

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


@app.command("key")
def key_command(
    full: Annotated[
        bool,
        typer.Option("--full", help="Print the complete digest and the encoded key."),
    ] = False,
) -> None:
    """Print this repository's signing key fingerprint.

    Publish this fingerprint somewhere a verifier can reach independently of any report
    you send them: a repository README, a profile page, a message sent at a different
    time. That is what makes it useful. A fingerprint a verifier only ever sees inside
    the report it is meant to authenticate proves nothing, because a forged report
    carries a forged key and a fingerprint matching it perfectly.
    """
    ctx = _context()

    try:
        ctx.require_initialized()
        public_key = load_public_key(ctx.vouchcode_dir)
    except (VouchcodeError, SigningError) as exc:
        raise _fail(str(exc)) from exc

    _out.print(f"fingerprint: {key_fingerprint(public_key)}")

    if full:
        _out.print(f"digest: {key_digest(public_key)}")
        _out.print(f"public key: {public_key_b64(ctx.vouchcode_dir)}")


@app.command()
def report(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Directory to write the report into."),
    ] = Path("."),
    name: Annotated[
        str,
        typer.Option("--name", help="Base filename for the generated artifacts."),
    ] = "vouchcode-report",
    since: Annotated[
        str | None,
        typer.Option("--since", help="Include commits after this commit hash."),
    ] = None,
    pdf: Annotated[
        bool,
        typer.Option("--pdf/--no-pdf", help="Also render the human-readable PDF."),
    ] = True,
) -> None:
    """Compile the ledger into a signed JSON report and a PDF summary."""
    ctx = _context()

    try:
        ctx.require_initialized()
        entries = [entry.to_dict() for entry in read_entries(ctx.ledger_path)]
    except VouchcodeError as exc:
        raise _fail(str(exc)) from exc

    commit_range = "full history"
    if since:
        selected, commit_range = _slice_since(entries, since)
        if selected is None:
            raise _fail(f"commit {since} is not in the ledger")
        entries = selected

    if not entries:
        raise _fail("no ledger entries in the selected range")

    try:
        document = build_report(
            entries,
            ctx.vouchcode_dir,
            repository=str(ctx.root),
            commit_range=commit_range,
        )
    except (VouchcodeError, SigningError) as exc:
        raise _fail(str(exc)) from exc

    json_path = write_report(document, output / f"{name}.json")
    _out.print(f"json report: {json_path}")

    if pdf:
        pdf_path = render_pdf(document, output / f"{name}.pdf")
        _out.print(f"pdf report: {pdf_path}")

    fingerprint = document["signing_key"]["fingerprint"]
    _out.print(f"signing key fingerprint: {fingerprint}")
    _out.print(
        "publish this fingerprint where a recipient can obtain it independently of "
        "this report, otherwise they have nothing to compare it against"
    )


@app.command("verify-report")
def verify_report_command(
    path: Annotated[Path, typer.Argument(help="Path to the JSON report to check.")],
    expect_fingerprint: Annotated[
        str | None,
        typer.Option(
            "--expect-fingerprint",
            help="Fingerprint obtained independently of this report.",
        ),
    ] = None,
) -> None:
    """Check a report's signature, and its key against a fingerprint you already trust.

    Without --expect-fingerprint this checks only that the report has not been altered
    since signing. That is worth knowing and it is not proof of origin: a forged report
    signed with a substitute key passes this check exactly as a genuine one does. Supply
    a fingerprint you obtained elsewhere to check the report came from the key you
    expected.
    """
    if not path.is_file():
        raise _fail(f"no report at {path}")

    try:
        document = read_report(path)
    except (OSError, ValueError) as exc:
        raise _fail(f"cannot read report {path}: {exc}") from exc

    result = verify_report(document, expect_fingerprint or "")

    _out.print(f"report: {path}")
    _out.print(f"signature: {'valid' if result.signature_ok else 'INVALID'}")
    _out.print(f"key fingerprint: {result.fingerprint}")

    if not result.signature_ok:
        _err.print(f"error: {result.error or 'report signature does not verify'}")
        raise typer.Exit(1)

    if not result.fingerprint_checked:
        _out.print("expected fingerprint: not supplied")
        _out.print(
            "note: the signature proves this report is unaltered, not who signed it. "
            "rerun with --expect-fingerprint to check it against a key you trust"
        )
        return

    _out.print(f"expected fingerprint: {result.expected_fingerprint}")

    if not result.fingerprint_ok:
        _err.print(
            "error: key fingerprint does not match the one supplied. this report is "
            "internally consistent but was signed by a different key than expected"
        )
        raise typer.Exit(1)

    _out.print("fingerprint: matches")
    _out.print("report verified and signed by the expected key")


@app.command()
def scan(
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Scan only the newest N commits."),
    ] = None,
) -> None:
    """Reconstruct a best-effort ledger from history for a repository that adopted
    Vouchcode late.

    Every entry produced is marked as retroactively scanned. Attribution comes from the
    stylometric heuristic only, because no tool signal survives for a commit made before
    the tool was installed, and comprehension is never scored, because a developer
    cannot be meaningfully quizzed months later on code sitting in front of them.
    """
    ctx = _context()

    try:
        ctx.require_initialized()
    except VouchcodeError as exc:
        raise _fail(str(exc)) from exc

    repo = Repo(ctx.root)
    existing = {entry.commit for entry in read_entries(ctx.ledger_path)}

    result = scan_history(repo, ctx.vouchcode_dir, limit=limit)

    appended = 0
    for entry in result.entries:
        # Never overwrite a live capture with a reconstruction. A commit observed at the
        # time it was made carries better evidence than anything a scan can infer.
        if entry.commit in existing:
            continue
        append_entry(ctx.ledger_path, entry, ctx.vouchcode_dir)
        appended += 1

    _out.print(f"commits scanned: {result.scanned_commits}")
    _out.print(f"entries added: {appended}")
    _out.print(
        f"entries skipped, already captured: {result.scanned_commits - appended}"
    )

    for skipped in result.skipped:
        _err.print(f"warning: {skipped}")

    if appended:
        _out.print(
            "these entries are marked as retroactively scanned and carry weaker "
            "evidence than live capture"
        )


def _slice_since(
    entries: list[dict[str, Any]],
    since: str,
) -> tuple[list[dict[str, Any]] | None, str]:
    """Return the entries after a given commit, and a label describing the range."""
    for index, entry in enumerate(entries):
        if str(entry.get("commit", "")).startswith(since):
            return entries[index + 1 :], f"after {since[:10]}"
    return None, ""


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
