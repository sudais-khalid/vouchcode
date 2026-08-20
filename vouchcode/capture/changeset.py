"""Reading the set of files a commit touches, before and after the commit exists.

The two hook points see different things. At pre-commit time the commit has no hash yet,
but the index holds exactly what is about to be committed. At post-commit time the hash
exists but the index has been consumed. Capture therefore reads the file list in
pre-commit and resolves the hash in post-commit, and this module provides both halves.
"""

from __future__ import annotations

from git import Repo

# Status letters accepted from git's --diff-filter. Added, Copied, Modified, Renamed,
# and Type-changed paths all carry content in the resulting commit. Deleted paths are
# excluded because there is no post-commit content to attribute or ask questions about.
_CONTENT_BEARING_STATUSES = "ACMRT"


def staged_files(repo: Repo) -> list[str]:
    """Return repository-relative paths staged for the commit currently being made.

    Called from the pre-commit hook, where the index is authoritative.
    """
    if _has_commits(repo):
        raw = repo.git.diff(
            "--cached",
            "--name-only",
            f"--diff-filter={_CONTENT_BEARING_STATUSES}",
        )
    else:
        # On the very first commit of a repository there is no HEAD tree to diff
        # against, and the empty tree object is not guaranteed to exist in the object
        # database yet. The index itself is the complete change set in that case.
        raw = repo.git.ls_files("--cached")
    return _split_paths(raw)


def commit_files(repo: Repo, rev: str = "HEAD") -> list[str]:
    """Return repository-relative paths carrying content in the given commit.

    Called from the post-commit hook as a fallback when no pre-commit record exists,
    for example after a commit produced by a tool that bypassed the pre-commit hook.
    """
    raw = repo.git.show(
        "--name-only",
        "--pretty=format:",
        f"--diff-filter={_CONTENT_BEARING_STATUSES}",
        rev,
    )
    return _split_paths(raw)


def head_commit_sha(repo: Repo) -> str:
    """Return the full hexadecimal hash of the current HEAD commit."""
    return repo.head.commit.hexsha


def current_branch(repo: Repo) -> str | None:
    """Return the checked-out branch name, or None when HEAD is detached."""
    if repo.head.is_detached:
        return None
    return repo.active_branch.name


def _has_commits(repo: Repo) -> bool:
    """Return whether HEAD resolves to a commit.

    A freshly initialized repository has a HEAD reference that points at an unborn
    branch, and accessing it raises rather than returning nothing.
    """
    return repo.head.is_valid()


def _split_paths(raw: str) -> list[str]:
    """Normalize git's newline-delimited path output into a de-duplicated list.

    Order is preserved so that ledger entries stay reproducible for a given commit.
    """
    seen: dict[str, None] = {}
    for line in raw.splitlines():
        path = line.strip()
        if path:
            seen.setdefault(path, None)
    return list(seen)
