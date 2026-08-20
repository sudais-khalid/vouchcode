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

Three hooks, not two. Git does not run the commit hooks for a merge commit it creates
itself, so 'git merge --no-ff' would otherwise leave a commit in history with no ledger
entry. post-merge closes that gap. It fires for fast-forward merges and pulls as well,
which create no commit, so the handler records only when HEAD is an unrecorded merge
commit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from git import Repo

from vouchcode.capture.changeset import (
    commit_files,
    commit_parents,
    current_branch,
    head_commit_sha,
    is_merge_commit,
    staged_files,
)
from vouchcode.capture.segmentation_pass import segment_commit, segment_staged
from vouchcode.comprehension import engine
from vouchcode.comprehension.eligibility import (
    COMPREHENSION_EXCLUDED_MERGE,
    MERGE_RATIONALE,
    comprehension_record,
    evaluate_gate,
)
from vouchcode.config import RepoContext, discover_repo
from vouchcode.errors import VouchcodeError
from vouchcode.ledger.entry import (
    ENTRY_TYPE_COMMIT,
    ENTRY_TYPE_MERGE,
    LedgerEntry,
    utc_timestamp,
)
from vouchcode.ledger.store import append_entry, contains_commit
from vouchcode.segmentation.fingerprint import current_version

PRE_COMMIT = "pre-commit"
POST_COMMIT = "post-commit"
POST_MERGE = "post-merge"

_PREFIX = "vouchcode:"


def main(argv: list[str] | None = None) -> int:
    """Dispatch to the handler for the hook event named on the command line."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        _warn("usage: python -m vouchcode.capture.runner <pre-commit|post-commit>")
        return 0

    event = args[0]
    handlers = {
        PRE_COMMIT: run_pre_commit,
        POST_COMMIT: run_post_commit,
        POST_MERGE: run_post_merge,
    }
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

    pending: dict[str, Any] = {
        "captured_at": utc_timestamp(),
        "branch": current_branch(repo),
        "files": files,
    }

    outcome = _verify_comprehension(ctx, repo, files)
    if outcome is not None:
        # Carried to post-commit rather than recomputed there. The questions were asked
        # against the staged content, and re-deriving the result afterward could produce
        # a different one, which would mean the ledger recorded something the developer
        # was never actually asked.
        pending["comprehension"] = outcome.to_dict()

        if outcome.blocks_commit:
            _write_pending(ctx, pending)
            _refuse(outcome.rationale)

    _write_pending(ctx, pending)


def _verify_comprehension(
    ctx: RepoContext,
    repo: Repo,
    files: list[str],
) -> engine.CommitComprehension | None:
    """Run the comprehension check over the staged change, or return None on failure.

    Returning None means the check could not run at all, which is a capture failure and
    must not block the commit. That is the distinction the module docstring draws: a
    refusal is a product decision, and a fault is not.
    """
    try:
        result = segment_staged(repo, ctx.vouchcode_dir, files)
        eligible, _record = evaluate_gate(ENTRY_TYPE_COMMIT, result.hunks)
        return engine.verify(eligible)
    except Exception as exc:
        _warn(f"comprehension check skipped: {exc.__class__.__name__}: {exc}")
        return None


def _refuse(reason: str) -> None:
    """Stop the commit because comprehension was not demonstrated.

    Exits non-zero from pre-commit, which is the one place Vouchcode deliberately
    prevents a commit. The message says what to do next, because a gate that only says
    no is a gate developers route around.
    """
    _warn(reason)
    _warn(
        "commit refused. read the code above, or run 'git commit --no-verify' to "
        "record the commit with comprehension unverified"
    )
    raise SystemExit(1)


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
        files: list[str] | None = list(pending["files"])
        branch = pending.get("branch")
    else:
        # No usable pre-commit record. This is the normal path for a commit made with
        # --no-verify, or by a tool that skips hooks, so fall back to reading the change
        # set out of the finished commit instead of recording nothing.
        files = commit_files(repo, commit_sha)
        branch = current_branch(repo)

    append_entry(ctx.ledger_path, _build_entry(repo, ctx, commit_sha, files, branch))
    _clear_pending(ctx)


def run_post_merge() -> None:
    """Record a merge commit that git created without running the commit hooks.

    post-merge also fires for fast-forward merges and for pulls that create no commit at
    all, so the handler records only when HEAD is a merge commit that is not already in
    the ledger. Both guards are load bearing: without the parent-count check a
    fast-forward would append a duplicate entry for someone else's commit, and without
    the ledger check a hand-resolved merge already recorded by post-commit would be
    appended twice.
    """
    ctx = discover_repo()
    if not ctx.is_initialized:
        return

    repo = _open_repo(ctx)
    commit_sha = head_commit_sha(repo)

    if not is_merge_commit(repo, commit_sha):
        return

    if contains_commit(ctx.ledger_path, commit_sha):
        return

    append_entry(
        ctx.ledger_path,
        _build_entry(repo, ctx, commit_sha, files=None, branch=current_branch(repo)),
        ctx.vouchcode_dir,
    )
    _clear_pending(ctx)


def _build_entry(
    repo: Repo,
    ctx: RepoContext,
    commit_sha: str,
    files: list[str] | None,
    branch: str | None,
    recorded_comprehension: dict[str, Any] | None = None,
) -> LedgerEntry:
    """Assemble a ledger entry, classifying merges by parent count.

    Classification lives here rather than in either hook handler so that a merge is
    recorded identically whether git produced it automatically (post-merge) or the
    developer resolved conflicts and finished it by hand (post-commit). The files
    argument is discarded for a merge: see the merge note in vouchcode.ledger.entry for
    why a merge carries no file list in Phase 1.
    """
    commit = repo.commit(commit_sha)
    merge = len(commit.parents) >= 2

    entry = LedgerEntry(
        commit=commit_sha,
        timestamp=utc_timestamp(),
        author_name=str(commit.author.name or ""),
        author_email=str(commit.author.email or ""),
        branch=branch,
        files=None if merge else files,
        parents=commit_parents(repo, commit_sha),
        entry_type=ENTRY_TYPE_MERGE if merge else ENTRY_TYPE_COMMIT,
    )

    if merge:
        # Stated on the entry rather than left empty. A merge is excluded from
        # comprehension by decision, and the ledger must say so explicitly so that a
        # report reader can tell a considered exclusion from an unevaluated gap.
        entry.comprehension = comprehension_record(
            COMPREHENSION_EXCLUDED_MERGE, MERGE_RATIONALE
        )
        return entry

    if files:
        _apply_segmentation(entry, repo, ctx, commit_sha, files, recorded_comprehension)

    return entry


def _apply_segmentation(
    entry: LedgerEntry,
    repo: Repo,
    ctx: RepoContext,
    commit_sha: str,
    files: list[str],
    recorded_comprehension: dict[str, Any] | None = None,
) -> None:
    """Run the Phase 2 segmentation and attribution pass over a commit's files.

    Failure here degrades the entry rather than losing it. A commit whose segmentation
    raised is still worth recording with its identity and file list intact, and an entry
    that says it could not be analyzed is more useful than no entry at all. The
    reason is recorded in the entry's skipped list so the gap shows up in a report.
    """
    try:
        result = segment_commit(repo, commit_sha, ctx.vouchcode_dir, files)
    except Exception as exc:
        entry.skipped = [f"segmentation failed: {exc.__class__.__name__}: {exc}"]
        return

    entry.hunks = [hunk.to_dict() for hunk in result.hunks]
    # Stamped alongside the hunks, never separately. A fingerprint without the
    # conditions
    # of its computation is not verifiable, so the two are written together or not at
    # all.
    entry.fingerprint_version = current_version()
    entry.attribution = result.attribution.to_dict()
    entry.skipped = result.skipped

    # The gate decides what the comprehension engine would question and records why when
    # the answer is nothing. When pre-commit actually ran the check, its result is
    # authoritative and replaces this, because that is the exchange the developer had.
    _eligible, entry.comprehension = evaluate_gate(entry.entry_type, result.hunks)

    if recorded_comprehension:
        entry.comprehension = dict(recorded_comprehension)


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
