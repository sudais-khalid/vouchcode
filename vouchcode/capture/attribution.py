"""Attribution pass: deciding what wrote each hunk.

Precedence, from Section 4.1. A direct tool signal is the primary source and wins
wherever one covers the hunk, because it reports what happened. Stylometry is the
fallback and infers. Structural certainty outranks both: when the AST proves a hunk's
logic did not change, no attribution is attempted at all, because there is nothing new
to attribute.

Every result carries its source, so a report reader can tell evidence from inference
without having to know how Vouchcode works internally.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vouchcode.capture import signals, stylometry
from vouchcode.segmentation.hunks import (
    SOURCE_STRUCTURAL,
    STATUS_UNCHANGED,
    Hunk,
)

STATUS_AI = "ai"
STATUS_HUMAN = "human"
STATUS_MIXED = "mixed"
STATUS_UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class CommitAttribution:
    """Aggregate attribution across every hunk in one commit."""

    status: str
    ai_hunks: int
    human_hunks: int
    mixed_hunks: int
    unclassified_hunks: int
    unchanged_hunks: int
    ai_line_share: float
    sources: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the ledger, with a fixed key order for Phase 4 hashing."""
        return {
            "status": self.status,
            "source": ", ".join(self.sources) if self.sources else None,
            # The commit-level confidence is deliberately absent. Confidence lives on
            # each hunk, where it means something specific. Averaging a direct signal's
            # certainty together with a heuristic's guess would produce a number that
            # describes neither.
            "confidence": None,
            "detail": {
                "ai_hunks": self.ai_hunks,
                "human_hunks": self.human_hunks,
                "mixed_hunks": self.mixed_hunks,
                "unclassified_hunks": self.unclassified_hunks,
                "unchanged_hunks": self.unchanged_hunks,
                "ai_line_share": self.ai_line_share,
            },
        }


def attribute_hunks(
    hunks: list[Hunk],
    vouchcode_dir: Path,
    baseline_sources: list[str],
) -> None:
    """Assign an attribution to every hunk that carries new logic, in place.

    Hunks the AST proves unchanged already carry a structural attribution and are left
    alone. The stylometric baseline is built once and reused, since it is the expensive
    part and does not vary between hunks in a commit.
    """
    signal_index = signals.load_signals(vouchcode_dir)

    pending = [hunk for hunk in hunks if hunk.carries_new_logic]
    if not pending:
        return

    # Built lazily: a commit fully covered by direct signals never needs a baseline, and
    # building one means parsing every prior file in the repository.
    baseline: stylometry.Baseline | None = None

    for hunk in pending:
        from_signal = signals.classify_range(
            signal_index, hunk.path, hunk.lineno, hunk.end_lineno
        )
        if from_signal is not None:
            hunk.attribution = from_signal
            continue

        if baseline is None:
            baseline = stylometry.build_baseline(baseline_sources)

        hunk.attribution = stylometry.score_against_baseline(hunk.source, baseline)


def summarize(hunks: list[Hunk]) -> CommitAttribution:
    """Roll hunk-level attributions up into one commit-level record.

    The rollup counts hunks and weights lines, and deliberately does not average
    confidences. A commit is classified as mixed whenever it contains both AI-attributed
    and human-attributed logic, because that is the truthful description and collapsing
    it to a single label would discard the distinction the ledger exists to preserve.
    """
    counts = {
        STATUS_AI: 0,
        STATUS_HUMAN: 0,
        STATUS_MIXED: 0,
        STATUS_UNCLASSIFIED: 0,
        STATUS_UNCHANGED: 0,
    }
    sources: set[str] = set()

    ai_lines = 0
    attributed_lines = 0

    for hunk in hunks:
        status = str(hunk.attribution.get("status", STATUS_UNCLASSIFIED))
        counts[status] = counts.get(status, 0) + 1

        source = hunk.attribution.get("source")
        if source:
            sources.add(str(source))

        if not hunk.carries_new_logic:
            continue

        attributed_lines += hunk.line_count
        if status == STATUS_AI:
            ai_lines += hunk.line_count
        elif status == STATUS_MIXED:
            # A mixed hunk contributes its measured share where one exists, and half
            # otherwise. Recorded rather than rounded to a whole side, because rounding
            # would overstate whichever side it landed on.
            detail = hunk.attribution.get("detail") or {}
            share = detail.get("ai_line_coverage")
            ai_lines += int(
                hunk.line_count * (share if isinstance(share, (int, float)) else 0.5)
            )

    ai_line_share = (
        round(ai_lines / attributed_lines, 4) if attributed_lines > 0 else 0.0
    )

    return CommitAttribution(
        status=_overall_status(counts),
        ai_hunks=counts[STATUS_AI],
        human_hunks=counts[STATUS_HUMAN],
        mixed_hunks=counts[STATUS_MIXED],
        unclassified_hunks=counts[STATUS_UNCLASSIFIED],
        unchanged_hunks=counts[STATUS_UNCHANGED],
        ai_line_share=ai_line_share,
        sources=sorted(sources - {SOURCE_STRUCTURAL}) or sorted(sources),
    )


def _overall_status(counts: dict[str, int]) -> str:
    """Reduce per-hunk statuses to one commit-level status."""
    has_ai = counts[STATUS_AI] > 0 or counts[STATUS_MIXED] > 0
    has_human = counts[STATUS_HUMAN] > 0

    if has_ai and has_human:
        return STATUS_MIXED
    if has_ai:
        return STATUS_AI
    if has_human:
        return STATUS_HUMAN
    if counts[STATUS_UNCHANGED] > 0 and counts[STATUS_UNCLASSIFIED] == 0:
        # Every hunk in the commit was proven structurally unchanged: a pure rename or
        # move commit. Saying so is more informative than calling it unclassified.
        return STATUS_UNCHANGED
    return STATUS_UNCLASSIFIED
