"""Running the comprehension check for one commit and recording its outcome.

Sits between the pre-commit hook and the prompt: takes the hunks the gate selected, runs
the questions, and reduces the per-hunk results into the single record written onto the
ledger entry.

Where this runs, and why it matters. Comprehension is checked in pre-commit, not
post-commit, because pre-commit is the only hook that can still refuse the commit. That
refusal is a product decision and is reported as one. It stays distinguishable from the
internal-error path in vouchcode.capture.runner, which never blocks: a developer must
always be able to tell "you have not accounted for this code" from "the tool broke".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vouchcode.comprehension.eligibility import (
    COMPREHENSION_FAILED,
    COMPREHENSION_NOT_REQUIRED,
    COMPREHENSION_PASSED,
    comprehension_record,
)
from vouchcode.comprehension.prompt import (
    AnswersUnavailable,
    HunkResult,
    is_interactive,
)
from vouchcode.comprehension.prompt import run_comprehension_check as _run_prompt
from vouchcode.segmentation.hunks import Hunk

# Recorded when a commit could not be verified because nothing was there to answer.
COMPREHENSION_SKIPPED_NON_INTERACTIVE = "skipped_non_interactive"
COMPREHENSION_NO_QUESTIONS = "no_questions_derivable"

NON_INTERACTIVE_RATIONALE = (
    "no terminal was attached to answer comprehension questions, so verification was "
    "skipped rather than recorded as passed"
)

NO_QUESTIONS_RATIONALE = (
    "the AI-attributed hunks in this commit contain no branch, loop, or exception "
    "structure to derive a verifiable question from"
)


@dataclass
class CommitComprehension:
    """The comprehension outcome for one commit."""

    status: str
    rationale: str
    results: list[HunkResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == COMPREHENSION_PASSED

    @property
    def blocks_commit(self) -> bool:
        """Whether this outcome should stop the commit.

        Only an actual failure blocks. A skip is not a failure: refusing every commit
        made without a terminal would make Vouchcode unusable in any script, and would
        push developers toward disabling it entirely, which protects nothing.
        """
        return self.status == COMPREHENSION_FAILED

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the ledger, with a fixed key order for Phase 4 hashing."""
        record = comprehension_record(self.status, self.rationale)
        record["hunks"] = [result.to_dict() for result in self.results]
        record["mean_score"] = (
            round(
                sum(result.mean_score for result in self.results) / len(self.results), 4
            )
            if self.results
            else 0.0
        )
        return record


def verify(
    hunks: list[Hunk],
    interactive: bool | None = None,
    **prompt_kwargs: Any,
) -> CommitComprehension:
    """Run the comprehension check over the eligible hunks of one commit.

    interactive is injectable so that tests can exercise the prompt path without a
    pseudo-terminal. Left unset, it is detected.
    """
    if not hunks:
        return CommitComprehension(
            status=COMPREHENSION_NOT_REQUIRED,
            rationale=(
                "no hunk in this commit was attributed to AI generation, so there was "
                "nothing to verify comprehension of"
            ),
        )

    if interactive is None:
        interactive = is_interactive()

    if not interactive:
        return CommitComprehension(
            status=COMPREHENSION_SKIPPED_NON_INTERACTIVE,
            rationale=NON_INTERACTIVE_RATIONALE,
        )

    try:
        results = _run_prompt(hunks, **prompt_kwargs)
    except AnswersUnavailable as exc:
        # The terminal looked usable and was not. Recorded as a skip with the reason,
        # never as a failure: refusing a commit because Vouchcode could not ask the
        # question would punish the developer for the tool's blind spot.
        return CommitComprehension(
            status=COMPREHENSION_SKIPPED_NON_INTERACTIVE,
            rationale=f"{NON_INTERACTIVE_RATIONALE} ({exc})",
        )

    if not results:
        return CommitComprehension(
            status=COMPREHENSION_NO_QUESTIONS,
            rationale=NO_QUESTIONS_RATIONALE,
        )

    failed = [result for result in results if not result.passed]

    if failed:
        names = ", ".join(f"{r.path}:{r.qualname}" for r in failed)
        return CommitComprehension(
            status=COMPREHENSION_FAILED,
            rationale=f"comprehension not demonstrated for {names}",
            results=results,
        )

    return CommitComprehension(
        status=COMPREHENSION_PASSED,
        rationale=(
            f"comprehension demonstrated for {len(results)} AI-attributed "
            f"{'hunk' if len(results) == 1 else 'hunks'}"
        ),
        results=results,
    )
