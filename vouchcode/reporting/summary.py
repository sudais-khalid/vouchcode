"""Aggregating a ledger range into the figures a report presents.

Kept separate from both output formats, so the JSON and the PDF cannot drift into
reporting different numbers for the same range. Both render this.

Counting rules, stated because a percentage is only meaningful alongside what it
divides:

    Authorship percentages are over lines of changed logic in hunks that carry new
    logic. Hunks the AST proved unchanged, renames and moves, are excluded from the
    denominator. Counting them would let a large rename dilute the AI share of a commit
    that was mostly generated, which would be flattering and false.

    The comprehension pass rate is over commits that were actually evaluated. Commits
    excluded by decision, merges, and commits skipped for want of a terminal are counted
    and reported separately rather than folded in as passes or failures. A pass rate
    that quietly counted unevaluated commits as passes would be the single most
    misleading number this tool could produce.

    Attribution source counts are reported per hunk, never collapsed. A reader needs to
    see how much of a claim rests on a tool signal, which is evidence, versus
    stylometry, which is inference, versus structural proof, which is certainty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Capture modes, distinguishing what was observed from what was reconstructed.
CAPTURE_LIVE = "live"
CAPTURE_RETROACTIVE = "retroactive_scan"


@dataclass
class ReportSummary:
    """Aggregate figures across a range of ledger entries."""

    commits: int = 0
    merges: int = 0
    retroactive_commits: int = 0

    hunks_total: int = 0
    hunks_new_logic: int = 0

    lines_ai: int = 0
    lines_human: int = 0
    lines_mixed: int = 0
    lines_unclassified: int = 0

    by_source: dict[str, int] = field(default_factory=dict)
    by_hunk_status: dict[str, int] = field(default_factory=dict)

    comprehension_evaluated: int = 0
    comprehension_passed: int = 0
    comprehension_failed: int = 0
    comprehension_by_status: dict[str, int] = field(default_factory=dict)

    @property
    def lines_attributed(self) -> int:
        """Lines in hunks carrying new logic, the denominator for every percentage."""
        return (
            self.lines_ai
            + self.lines_human
            + self.lines_mixed
            + self.lines_unclassified
        )

    @property
    def ai_percentage(self) -> float:
        """Share of changed logic attributed to AI generation, mixed hunks included."""
        if self.lines_attributed <= 0:
            return 0.0
        return round(
            100.0 * (self.lines_ai + self.lines_mixed) / self.lines_attributed, 1
        )

    @property
    def human_percentage(self) -> float:
        if self.lines_attributed <= 0:
            return 0.0
        return round(100.0 * self.lines_human / self.lines_attributed, 1)

    @property
    def unclassified_percentage(self) -> float:
        if self.lines_attributed <= 0:
            return 0.0
        return round(100.0 * self.lines_unclassified / self.lines_attributed, 1)

    @property
    def comprehension_pass_rate(self) -> float | None:
        """Pass rate over evaluated commits, or None when none were evaluated.

        None rather than zero. A repository where comprehension never ran has no pass
        rate, and printing zero percent would read as universal failure.
        """
        if self.comprehension_evaluated <= 0:
            return None
        return round(
            100.0 * self.comprehension_passed / self.comprehension_evaluated, 1
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the JSON report, with a fixed key order for hashing."""
        return {
            "commits": self.commits,
            "merges": self.merges,
            "retroactive_commits": self.retroactive_commits,
            "hunks_total": self.hunks_total,
            "hunks_carrying_new_logic": self.hunks_new_logic,
            "lines": {
                "attributed": self.lines_attributed,
                "ai": self.lines_ai,
                "human": self.lines_human,
                "mixed": self.lines_mixed,
                "unclassified": self.lines_unclassified,
            },
            "percentages": {
                "ai": self.ai_percentage,
                "human": self.human_percentage,
                "unclassified": self.unclassified_percentage,
            },
            "attribution_sources": dict(sorted(self.by_source.items())),
            "hunk_statuses": dict(sorted(self.by_hunk_status.items())),
            "comprehension": {
                "evaluated": self.comprehension_evaluated,
                "passed": self.comprehension_passed,
                "failed": self.comprehension_failed,
                "pass_rate": self.comprehension_pass_rate,
                "by_status": dict(sorted(self.comprehension_by_status.items())),
            },
        }


def summarize_entries(entries: list[dict[str, Any]]) -> ReportSummary:
    """Aggregate a list of ledger entries into report figures."""
    summary = ReportSummary()

    for entry in entries:
        summary.commits += 1

        if entry.get("type") == "merge":
            summary.merges += 1

        if entry.get("capture") == CAPTURE_RETROACTIVE:
            summary.retroactive_commits += 1

        _count_comprehension(summary, entry)

        for hunk in entry.get("hunks") or []:
            _count_hunk(summary, hunk)

    return summary


def _count_hunk(summary: ReportSummary, hunk: dict[str, Any]) -> None:
    """Fold one hunk into the running totals."""
    summary.hunks_total += 1

    attribution = hunk.get("attribution") or {}
    status = str(attribution.get("status") or "unclassified")
    source = str(attribution.get("source") or "none")

    summary.by_hunk_status[status] = summary.by_hunk_status.get(status, 0) + 1
    summary.by_source[source] = summary.by_source.get(source, 0) + 1

    if status == "unchanged":
        # Proven unchanged by the AST. Counted for visibility, excluded from every
        # percentage, because it is not part of what was authored in this range.
        return

    summary.hunks_new_logic += 1

    lines = hunk.get("lines") or [0, 0]
    try:
        span = max(0, int(lines[1]) - int(lines[0]) + 1)
    except (TypeError, ValueError, IndexError):
        span = 0

    if status == "ai":
        summary.lines_ai += span
    elif status == "human":
        summary.lines_human += span
    elif status == "mixed":
        summary.lines_mixed += span
    else:
        summary.lines_unclassified += span


def _count_comprehension(summary: ReportSummary, entry: dict[str, Any]) -> None:
    """Fold one entry's comprehension outcome into the running totals."""
    comprehension = entry.get("comprehension") or {}
    status = str(comprehension.get("status") or "not_evaluated")

    summary.comprehension_by_status[status] = (
        summary.comprehension_by_status.get(status, 0) + 1
    )

    # Only genuine outcomes move the pass rate. Everything else is reported by status
    # and kept out of the ratio.
    if status == "passed":
        summary.comprehension_evaluated += 1
        summary.comprehension_passed += 1
    elif status == "failed":
        summary.comprehension_evaluated += 1
        summary.comprehension_failed += 1
