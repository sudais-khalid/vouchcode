"""CI policy gate over the ledger.

A report is something a person reads. A gate is something a pipeline runs, and it is
what makes Vouchcode worth installing on a repository that receives contributions,
rather than merely interesting to look at. A maintainer drowning in AI-generated pull
requests does not want another document to review. They want the build to tell them
which submitted code nobody has accounted for.

The policy, stated once so it is not inferred from the code:

    A hunk is gated when it is attributed to AI generation at or above a confidence
    threshold. Gated hunks must belong to a commit carrying a passing comprehension
    record. Anything else fails the build.

Why the confidence threshold defaults where it does
---------------------------------------------------

MIN_CONFIDENCE defaults to 0.9, which in practice means the gate acts on direct tool
signals and structural proof, both of which carry confidence 1.0, and never on
stylometry, which is capped below 0.75 by construction.

That is deliberate and it is the conservative direction. Stylometric attribution is an
inference about whether code resembles a developer's prior style. Failing a build on
that inference would mean blocking a merge because a heuristic had a hunch, which is
exactly the overreach this project argues against everywhere else. A maintainer who
wants stricter behavior can lower the threshold explicitly, which makes it a decision
they took rather than a default they inherited.

The consequence, stated plainly: on a repository with no assistant integration writing
signal files, this gate will pass almost everything, because almost nothing clears 0.9.
That is honest rather than useless. The gate enforces accountability for code that is
known to be AI-generated; it does not pretend to detect AI-generated code that nobody
reported.

Output is plain text on purpose. This runs in a CI log with no terminal, where Rich
formatting becomes escape-sequence noise, so the interactive presentation used by the
comprehension prompt is deliberately not reused here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from git import Repo

from vouchcode.ledger.store import read_entries

# Confidence at or above which an AI attribution is strong enough to gate a build on.
# See the module docstring for why this excludes stylometry by default.
MIN_CONFIDENCE = 0.9

# Attribution statuses that put a hunk in scope. Mixed counts: a hunk that is partly
# generated still contains generated logic somebody must account for.
GATED_STATUSES = frozenset({"ai", "mixed"})

# The only comprehension status that satisfies the gate. Every other value, including
# the honest skips and exclusions, means the developer did not demonstrate understanding
# of this code, whatever the reason.
PASSING_COMPREHENSION = "passed"


@dataclass(frozen=True)
class GatedHunk:
    """One AI-attributed hunk the gate examined."""

    commit: str
    path: str
    qualname: str
    source: str
    confidence: float
    comprehension: str

    @property
    def ok(self) -> bool:
        return self.comprehension == PASSING_COMPREHENSION

    def line(self) -> str:
        """Render one plain-text result line for a CI log."""
        verdict = "PASS" if self.ok else "FAIL"
        return (
            f"{verdict}  {self.commit[:10]}  {self.path}:{self.qualname}  "
            f"ai via {self.source} conf {self.confidence:.2f}  "
            f"comprehension: {self.comprehension}"
        )


@dataclass
class GateResult:
    """The outcome of running the gate over a commit range."""

    base_ref: str = ""
    commits_in_range: int = 0
    entries_matched: int = 0
    hunks: list[GatedHunk] = field(default_factory=list)
    skipped_unknown_commits: list[str] = field(default_factory=list)
    min_confidence: float = MIN_CONFIDENCE
    require_comprehension: bool = True

    @property
    def failures(self) -> list[GatedHunk]:
        return [hunk for hunk in self.hunks if not hunk.ok]

    @property
    def passed(self) -> bool:
        """Whether the build should be allowed through.

        A range containing no gated hunks passes. That is not a loophole: it means
        nothing in the range was reported as AI-generated at a confidence worth acting
        on, and inventing a failure from an absence of evidence would be the same
        overreach the threshold exists to avoid.
        """
        if not self.require_comprehension:
            return True
        return not self.failures

    def report_lines(self) -> list[str]:
        """Render the whole result as plain text, one fact per line."""
        lines = [
            "vouchcode gate",
            f"base ref: {self.base_ref or '(none)'}",
            f"commits in range: {self.commits_in_range}",
            f"commits found in ledger: {self.entries_matched}",
            f"minimum confidence: {self.min_confidence:.2f}",
            f"comprehension required: {'yes' if self.require_comprehension else 'no'}",
            f"gated hunks: {len(self.hunks)}",
            "",
        ]

        for hunk in self.hunks:
            lines.append(hunk.line())

        if self.hunks:
            lines.append("")

        for commit in self.skipped_unknown_commits:
            # Surfaced rather than ignored. A commit git knows about and the ledger does
            # not is a gap in coverage, and a gate that stayed silent about it would let
            # an unrecorded commit pass as though it had been checked.
            lines.append(f"WARN  {commit[:10]}  not present in the ledger, not checked")

        if self.skipped_unknown_commits:
            lines.append("")

        if not self.require_comprehension:
            lines.append("result: pass, comprehension not required")
        elif not self.hunks:
            lines.append(
                "result: pass, no AI-attributed hunks at or above the confidence "
                "threshold in this range"
            )
        elif self.failures:
            lines.append(
                f"result: fail, {len(self.failures)} of {len(self.hunks)} gated hunks "
                "lack a passing comprehension record"
            )
        else:
            lines.append(
                f"result: pass, all {len(self.hunks)} gated hunks carry a passing "
                "comprehension record"
            )

        return lines


def commits_in_range(repo: Repo, base_ref: str | None) -> list[str]:
    """Return the commit hashes introduced by HEAD relative to a base ref.

    With no base ref the whole reachable history is the range, which is what a developer
    running the gate locally on a fresh clone expects. In CI the base ref is the branch
    the pull request targets.
    """
    if not base_ref:
        return [commit.hexsha for commit in repo.iter_commits()]

    try:
        raw = repo.git.rev_list(f"{base_ref}..HEAD")
    except Exception as exc:
        raise GateError(
            f"cannot resolve commit range {base_ref}..HEAD: {exc}. "
            "check that the base ref exists and that the CI checkout has enough history"
        ) from exc

    return [line.strip() for line in raw.splitlines() if line.strip()]


def run_gate(
    ledger_path: Path,
    repo: Repo,
    base_ref: str | None = None,
    min_confidence: float = MIN_CONFIDENCE,
    require_comprehension: bool = True,
) -> GateResult:
    """Evaluate the gate policy over a commit range.

    Ledger reading goes through the existing store rather than parsing the file again,
    so the gate and every other command share one definition of what an entry is.
    """
    wanted = commits_in_range(repo, base_ref)
    wanted_set = set(wanted)

    entries = read_entries(ledger_path)
    by_commit = {entry.commit: entry for entry in entries}

    result = GateResult(
        base_ref=base_ref or "",
        commits_in_range=len(wanted),
        min_confidence=min_confidence,
        require_comprehension=require_comprehension,
    )

    for commit in wanted:
        entry = by_commit.get(commit)
        if entry is None:
            result.skipped_unknown_commits.append(commit)
            continue

        result.entries_matched += 1
        comprehension = str(
            (entry.comprehension or {}).get("status") or "not_evaluated"
        )

        for hunk in entry.hunks or []:
            gated = _gated_hunk(hunk, commit, comprehension, min_confidence)
            if gated is not None:
                result.hunks.append(gated)

    del wanted_set
    return result


def _gated_hunk(
    hunk: dict[str, Any],
    commit: str,
    comprehension: str,
    min_confidence: float,
) -> GatedHunk | None:
    """Return the hunk as a gated record, or None when the policy does not cover it."""
    attribution = hunk.get("attribution") or {}
    status = str(attribution.get("status") or "")

    if status not in GATED_STATUSES:
        return None

    raw_confidence = attribution.get("confidence")
    if not isinstance(raw_confidence, (int, float)):
        # An attribution with no stated confidence makes no claim strong enough to fail
        # a build on. Consistent with how verification treats a missing version tag.
        return None

    confidence = float(raw_confidence)
    if confidence < min_confidence:
        return None

    lines = hunk.get("lines") or [0, 0]
    del lines

    return GatedHunk(
        commit=commit,
        path=str(hunk.get("path") or ""),
        qualname=str(hunk.get("qualname") or ""),
        source=str(attribution.get("source") or "unknown"),
        confidence=confidence,
        comprehension=comprehension,
    )


class GateError(Exception):
    """Raised when the gate cannot determine what to check."""
