"""Deciding which commits and hunks the comprehension engine evaluates.

This is the gate that sits in front of question generation. It exists as its own module,
separate from the engine, for one reason: what Vouchcode declines to evaluate is part of
what a report asserts, and a decision that important should be stated in one place and
testable without running a quiz.

The governing principle is that the ledger must be honest about its own coverage. A
field left null reads as an oversight; an explicit status with a rationale reads as a
decision.
Every entry therefore carries a comprehension status, including the entries that were
never evaluated and never will be.

Statuses:

    excluded_merge   A merge commit. Not evaluated, by decision, with the reason
                     recorded on the entry.
    not_required     Evaluated eligible and found to contain no AI-attributed logic.
                     There was nothing to ask about.
    not_evaluated    The engine did not run. Written by any code path that records an
                     entry without reaching the comprehension layer.
    passed / failed  Outcomes of an actual comprehension check.
"""

from __future__ import annotations

from typing import Any

from vouchcode.ledger.entry import ENTRY_TYPE_MERGE
from vouchcode.segmentation.hunks import Hunk

COMPREHENSION_EXCLUDED_MERGE = "excluded_merge"
COMPREHENSION_NOT_REQUIRED = "not_required"
COMPREHENSION_NOT_EVALUATED = "not_evaluated"
COMPREHENSION_PASSED = "passed"
COMPREHENSION_FAILED = "failed"

# Hunk attribution statuses that make a hunk eligible for questioning. Only logic
# attributed to AI generation is questioned, per Section 4.3. A hunk the AST proved
# unchanged, or one attributed to the developer, has nothing to verify.
QUESTIONABLE_STATUSES = frozenset({"ai", "mixed"})

MERGE_RATIONALE = (
    "merge commits are excluded from comprehension scoring: a merge introduces no "
    "independently authored logic, and the commits it joins carry their own entries"
)

NOT_REQUIRED_RATIONALE = (
    "no hunk in this commit was attributed to AI generation, so there was nothing to "
    "verify comprehension of"
)

NOT_EVALUATED_RATIONALE = "the comprehension engine did not run for this commit"


def comprehension_record(status: str, rationale: str) -> dict[str, Any]:
    """Build the comprehension field written onto a ledger entry.

    Rationale is required rather than optional. A status alone tells a reader what
    happened but not why, and the why is the part that distinguishes a considered
    exclusion from a gap in the tool.
    """
    return {"status": status, "rationale": rationale}


def is_merge_excluded(entry_type: str) -> bool:
    """Whether an entry of this type is excluded from comprehension entirely."""
    return entry_type == ENTRY_TYPE_MERGE


def questionable_hunks(hunks: list[Hunk]) -> list[Hunk]:
    """Return the hunks the comprehension engine should generate questions for.

    Two filters, and both matter. The hunk must carry new logic, which excludes renames
    and moves that the AST proved unchanged. And it must be attributed to AI generation,
    because Section 4.3 scopes comprehension verification to AI-attributed hunks.
    """
    return [
        hunk
        for hunk in hunks
        if hunk.carries_new_logic
        and str(hunk.attribution.get("status", "")) in QUESTIONABLE_STATUSES
    ]


def evaluate_gate(
    entry_type: str,
    hunks: list[Hunk],
) -> tuple[list[Hunk], dict[str, Any]]:
    """Decide what to question for one commit, and what to record when nothing is.

    Returns the hunks to question and the comprehension record to write. When the list
    is empty the record explains why, so that a commit which skipped the quiz is
    distinguishable from one that was never looked at.
    """
    if is_merge_excluded(entry_type):
        return [], comprehension_record(COMPREHENSION_EXCLUDED_MERGE, MERGE_RATIONALE)

    eligible = questionable_hunks(hunks)
    if not eligible:
        return [], comprehension_record(
            COMPREHENSION_NOT_REQUIRED, NOT_REQUIRED_RATIONALE
        )

    # Eligible hunks exist. The caller runs the engine and replaces this record with the
    # outcome; until it does, the entry says plainly that nothing ran.
    return eligible, comprehension_record(
        COMPREHENSION_NOT_EVALUATED, NOT_EVALUATED_RATIONALE
    )
