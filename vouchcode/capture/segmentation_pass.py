"""Running segmentation and attribution over a commit.

Sits between the capture hooks and the segmentation layer: reads the pre- and
post-commit content of each changed Python file out of git, segments the difference into
hunks, attributes them, and returns both the hunks and the commit-level rollup for the
ledger.

Only Python files are segmented. Section 1.4 scopes the initial system to Python, and a
non-Python file is recorded in the entry's file list but produces no hunks rather than
being silently dropped or guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from git import Repo

from vouchcode.capture.attribution import CommitAttribution, attribute_hunks, summarize
from vouchcode.errors import SegmentationError
from vouchcode.segmentation.hunks import Hunk, build_hunks

PYTHON_SUFFIXES = (".py", ".pyi")

# Upper bound on prior files read to build the stylometric baseline. Reading the entire
# history of a large repository inside a commit hook would make committing feel broken,
# and a baseline stops improving long before that point.
MAX_BASELINE_FILES = 60


@dataclass
class SegmentationResult:
    """Outcome of segmenting one commit."""

    hunks: list[Hunk]
    attribution: CommitAttribution
    skipped: list[str]


def segment_commit(
    repo: Repo,
    commit_sha: str,
    vouchcode_dir: Path,
    files: list[str],
) -> SegmentationResult:
    """Segment and attribute every Python file changed by a commit."""
    hunks: list[Hunk] = []
    skipped: list[str] = []

    for path in files:
        if not _is_python(path):
            continue
        try:
            before = _read_blob(repo, f"{commit_sha}~1", path)
            after = _read_blob(repo, commit_sha, path)
            hunks.extend(build_hunks(path, before, after))
        except SegmentationError as exc:
            # An unparseable file is recorded as skipped rather than failing the commit.
            # A report that says which files could not be analyzed is honest; one that
            # silently omits them is not.
            skipped.append(f"{path}: {exc}")

    baseline_sources = _baseline_sources(repo, commit_sha, exclude=set(files))
    attribute_hunks(hunks, vouchcode_dir, baseline_sources)

    return SegmentationResult(
        hunks=hunks,
        attribution=summarize(hunks),
        skipped=skipped,
    )


def _is_python(path: str) -> bool:
    return path.lower().endswith(PYTHON_SUFFIXES)


def _read_blob(repo: Repo, rev: str, path: str) -> str:
    """Read a file's content at a revision, returning empty string when absent.

    Absent is the normal case twice over: a file added by this commit has no content in
    the parent, and the first commit of a repository has no parent at all.
    """
    try:
        blob = repo.git.show(f"{rev}:{path}")
    except Exception:
        return ""
    return blob if isinstance(blob, str) else ""


def _baseline_sources(
    repo: Repo,
    commit_sha: str,
    exclude: set[str],
) -> list[str]:
    """Collect prior Python sources for the stylometric baseline.

    Read from the commit's parent rather than from the commit itself, so that the code
    being attributed is never part of the baseline it is measured against. Including it
    would drag the baseline toward the hunk and suppress exactly the divergence the
    measurement is looking for.
    """
    parent = f"{commit_sha}~1"

    try:
        listing = repo.git.ls_tree("-r", "--name-only", parent)
    except Exception:
        return []

    sources: list[str] = []
    for path in listing.splitlines():
        path = path.strip()
        if not path or not _is_python(path) or path in exclude:
            continue
        content = _read_blob(repo, parent, path)
        if content.strip():
            sources.append(content)
        if len(sources) >= MAX_BASELINE_FILES:
            break

    return sources
