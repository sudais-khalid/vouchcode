"""Retroactive repository scan.

Applies the heuristic component of the capture layer to a repository's existing commit
history, producing a best-effort record for repositories that adopted Vouchcode after
development began, per Section 4.5.

Two constraints make this honest rather than misleading, and both are enforced here
rather than left to whoever reads the output.

Heuristic only. There is no tool signal to recover after the fact. An assistant that
generated code last March did not leave a Vouchcode signal file, and a scan that
consulted signals would attribute recent commits from evidence and old ones from
inference while presenting both identically. Every scanned entry therefore goes through
the stylometric path, whatever signals happen to exist on disk now.

Never comprehension. A developer cannot be meaningfully quizzed after the fact about
code committed before the tool existed: they would answer with the code in front of
them, months after writing it, which measures nothing. Scanned entries record
comprehension as excluded, with the reason.

Every entry produced here is marked with a capture mode distinguishing it from live
capture, and reports surface that distinction. Presenting reconstructed history with the
same confidence as observed history would be the most consequential lie this tool could
tell, because it is the one a reader has no way to detect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from git import Repo

from vouchcode.capture.attribution import summarize
from vouchcode.capture.changeset import commit_files, commit_parents
from vouchcode.capture.segmentation_pass import (
    MAX_BASELINE_FILES,
    is_python_path,
    read_blob,
)
from vouchcode.capture.stylometry import build_baseline, score_against_baseline
from vouchcode.errors import SegmentationError
from vouchcode.ledger.entry import (
    ENTRY_TYPE_COMMIT,
    ENTRY_TYPE_MERGE,
    LedgerEntry,
    utc_timestamp,
)
from vouchcode.reporting.summary import CAPTURE_RETROACTIVE
from vouchcode.segmentation.fingerprint import current_version
from vouchcode.segmentation.hunks import Hunk, build_hunks

COMPREHENSION_EXCLUDED_RETROACTIVE = "excluded_retroactive"

RETROACTIVE_RATIONALE = (
    "this commit was reconstructed by a retroactive scan rather than captured at "
    "commit time, and comprehension cannot be verified after the fact"
)

SCAN_ATTRIBUTION_NOTE = (
    "attribution for this commit was inferred by a retroactive stylometric scan, "
    "not observed at commit time, and is weaker evidence than a live capture"
)


@dataclass
class ScanResult:
    """The outcome of scanning a repository's history."""

    entries: list[LedgerEntry] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    scanned_commits: int = 0


def scan_history(
    repo: Repo,
    vouchcode_dir: Path,
    limit: int | None = None,
) -> ScanResult:
    """Reconstruct a best-effort ledger from a repository's existing history.

    Walks oldest commit first, so the resulting entries chain in the same order live
    capture would have produced. Reversing that would build a chain whose order
    contradicts the history it describes.
    """
    result = ScanResult()

    commits = list(repo.iter_commits(reverse=True))
    if limit is not None:
        commits = commits[-limit:] if limit < len(commits) else commits

    # Built once from the repository's own history. A per-commit baseline would be
    # cleaner in principle and far too slow to be usable on any real repository.
    baseline = build_baseline(_baseline_sources(repo))

    for commit in commits:
        entry = _scan_commit(repo, commit, baseline, result)
        if entry is not None:
            result.entries.append(entry)
            result.scanned_commits += 1

    return result


def _scan_commit(
    repo: Repo,
    commit: Any,
    baseline: Any,
    result: ScanResult,
) -> LedgerEntry | None:
    """Reconstruct one commit's entry from history."""
    sha = commit.hexsha
    is_merge = len(commit.parents) >= 2

    files = [] if is_merge else commit_files(repo, sha)

    entry = LedgerEntry(
        commit=sha,
        timestamp=utc_timestamp(),
        author_name=str(commit.author.name or ""),
        author_email=str(commit.author.email or ""),
        branch=None,
        files=None if is_merge else files,
        parents=commit_parents(repo, sha),
        entry_type=ENTRY_TYPE_MERGE if is_merge else ENTRY_TYPE_COMMIT,
    )
    entry.capture = CAPTURE_RETROACTIVE
    entry.comprehension = {
        "status": COMPREHENSION_EXCLUDED_RETROACTIVE,
        "rationale": RETROACTIVE_RATIONALE,
    }

    if is_merge or not files:
        return entry

    hunks = _segment(repo, commit, files, result)
    _attribute(hunks, baseline)

    entry.hunks = [hunk.to_dict() for hunk in hunks]
    entry.fingerprint_version = current_version()
    entry.attribution = summarize(hunks).to_dict()
    entry.attribution["note"] = SCAN_ATTRIBUTION_NOTE

    return entry


def _segment(
    repo: Repo,
    commit: Any,
    files: list[str],
    result: ScanResult,
) -> list[Hunk]:
    """Segment one historical commit into hunks."""
    hunks: list[Hunk] = []
    parent = f"{commit.hexsha}~1" if commit.parents else None

    for path in files:
        if not is_python_path(path):
            continue
        try:
            before = read_blob(repo, parent, path) if parent else ""
            after = read_blob(repo, commit.hexsha, path)
            hunks.extend(build_hunks(path, before, after))
        except SegmentationError as exc:
            result.skipped.append(f"{commit.hexsha[:10]} {path}: {exc}")

    return hunks


def _attribute(hunks: list[Hunk], baseline: Any) -> None:
    """Attribute scanned hunks through the stylometric path only.

    Signals are deliberately not consulted. A signal file present today says nothing
    about a commit made before the adapter existed, and reading it would attribute part
    of a scanned history from evidence and the rest from inference while presenting both
    the same way.
    """
    for hunk in hunks:
        if not hunk.carries_new_logic:
            continue
        hunk.attribution = score_against_baseline(hunk.source, baseline)


def _baseline_sources(repo: Repo) -> list[str]:
    """Collect Python sources from the repository head for the stylometric baseline.

    Taken from the current head rather than from each commit's own parent. That is an
    approximation, and it is the honest one available: a scan has no per-commit baseline
    to work from without re-reading the whole tree at every commit, which is
    prohibitively slow. It also means the baseline includes code being scored against
    it, which biases results toward looking like the developer's own style. That bias is
    conservative, since it makes the scan less likely to call something AI-generated,
    and it is another reason scanned attribution is marked as weaker than live capture.
    """
    try:
        listing = repo.git.ls_tree("-r", "--name-only", "HEAD")
    except Exception:
        return []

    sources: list[str] = []
    for path in listing.splitlines():
        path = path.strip()
        if not path or not is_python_path(path):
            continue
        content = read_blob(repo, "HEAD", path)
        if content.strip():
            sources.append(content)
        if len(sources) >= MAX_BASELINE_FILES:
            break

    return sources
