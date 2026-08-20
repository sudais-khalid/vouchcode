"""Entry point executed by the installed git hooks.

Invoked as `python -m vouchcode.capture.runner <event>` from the generated hook scripts.

Failure policy. In Phase 1 the capture path never blocks a commit. A provenance tool
that rejects a developer's commit because of a bug in its own recording logic is worse
than one that records nothing, so an internal error is reported on stderr and the hook
exits zero. This changes in Phase 3, where a failed comprehension check becomes a
deliberate, documented non-zero exit from pre-commit. That distinction (refusing a
commit as a product decision, versus refusing it because of an internal fault) is the
reason the policy is stated here rather than left implicit.

Two-stage capture. pre-commit writes the staged file list to .vouchcode/pending.json,
because the index describing the commit is gone by the time post-commit runs.
post-commit consumes that record, pairs it with the now-known commit hash, appends a
ledger entry, and clears the pending file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from git import Repo

from vouchcode.capture.changeset import (
    commit_files,
    current_branch,
    head_commit_sha,
    staged_files,
)
from vouchcode.config import RepoContext, discover_repo
from vouchcode.errors import VouchcodeError
from vouchcode.ledger.entry import LedgerEntry, utc_timestamp
from vouchcode.ledger.store import append_entry, contains_commit

PRE_COMMIT = "pre-commit"
POST_COMMIT = "post-commit"

_PREFIX = "vouchcode:"


def main(argv: list[str] | None = None) -> int:
    """Dispatch to the handler for the hook event named on the command line."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        _warn("usage: python -m vouchcode.capture.runner <pre-commit|post-commit>")
        return 0

    event = args[0]
    handlers = {PRE_COMMIT: run_pre_commit, POST_COMMIT: run_post_commit}
    handler = handlers.get(event)
    if handler is None:
        _warn(f"unknown hook event '{event}'")
        return 0

    try:
        handler()
    except VouchcodeError as exc:
        # Expected, actionable failure. Report it and let the commit proceed.
        _warn(str(exc))
    except Exception as exc:
        # Unexpected failure. Still non-blocking in Phase 1, but named explicitly so the
        # developer can tell a Vouchcode bug from a Vouchcode policy decision.
        _warn(f"internal error during {event} capture: {exc.__class__.__name__}: {exc}")

    return 0


def run_pre_commit() -> None:
    """Record the change set that is about to be committed."""
    ctx = discover_repo()
    if not ctx.is_initialized:
        # The hook outlived the .vouchcode directory, for example after a manual
        # deletion. Say so rather than recreating state the developer removed.
        _warn(
            "hook is installed but .vouchcode/ is missing. "
            "run 'vouchcode init' to restore it, or 'vouchcode uninstall' to remove "
            "the hooks"
        )
        return

    repo = _open_repo(ctx)
    files = staged_files(repo)
    _write_pending(
        ctx,
        {
            "captured_at": utc_timestamp(),
            "branch": current_branch(repo),
            "files": files,
        },
    )


def run_post_commit() -> None:
    """Pair the recorded change set with the resulting commit and write the ledger."""
    ctx = discover_repo()
    if not ctx.is_initialized:
        return

    repo = _open_repo(ctx)
    commit_sha = head_commit_sha(repo)
    pending = _read_pending(ctx)

    if contains_commit(ctx.ledger_path, commit_sha):
        # The hook fired twice for one commit. Appending again would inflate the
        # history the ledger is supposed to attest to.
        _clear_pending(ctx)
        return

    if pending is not None and pending.get("files"):
        files = list(pending["files"])
        branch = pending.get("branch")
    else:
        # No usable pre-commit record. This is the normal path for a commit made with
        # --no-verify, or by a tool that skips hooks, so fall back to reading the change
        # set out of the finished commit instead of recording nothing.
        files = commit_files(repo, commit_sha)
        branch = current_branch(repo)

    commit = repo.commit(commit_sha)
    entry = LedgerEntry(
        commit=commit_sha,
        timestamp=utc_timestamp(),
        author_name=str(commit.author.name or ""),
        author_email=str(commit.author.email or ""),
        branch=branch,
        files=files,
    )

    append_entry(ctx.ledger_path, entry)
    _clear_pending(ctx)


def _open_repo(ctx: RepoContext) -> Repo:
    """Open the repository at the resolved root."""
    return Repo(ctx.root)


def _write_pending(ctx: RepoContext, payload: dict[str, Any]) -> None:
    """Persist the pre-commit record for the post-commit hook to consume."""
    ctx.vouchcode_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with open(ctx.pending_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _read_pending(ctx: RepoContext) -> dict[str, Any] | None:
    """Load the pre-commit record, returning None when it is absent or unreadable.

    A corrupt pending file is recoverable: post-commit falls back to reading the commit
    itself, so there is nothing to gain by failing here.
    """
    path: Path = ctx.pending_path
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _clear_pending(ctx: RepoContext) -> None:
    """Remove the pre-commit record once it has been consumed."""
    try:
        ctx.pending_path.unlink(missing_ok=True)
    except OSError:
        pass


def _warn(message: str) -> None:
    """Write a diagnostic to stderr, where it will not be mistaken for git's output."""
    print(f"{_PREFIX} {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
