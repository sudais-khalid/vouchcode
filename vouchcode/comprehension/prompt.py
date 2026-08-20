"""Terminal prompt for the comprehension check.

Presents each AI-attributed hunk and its questions through Rich and collects typed
answers, per Section 4.3. Runs from the pre-commit hook, which is the point at which a
commit can still be refused.

Tone follows CLAUDE.md Rule 3. This prompt interrupts a developer mid-commit to ask them
to account for code they are about to sign their name to, which is a serious thing to do
and reads better stated plainly than dressed up. It shows the code, asks the question,
takes the answer, and reports the result.

Non-interactive environments. A commit made from a script, a CI job, or an editor that
does not attach a terminal cannot answer questions. Rather than hanging on a read that
will never return, the prompt detects the absence of a usable terminal and reports that
verification was skipped, with the reason recorded on the ledger entry. A skipped check
is never recorded as a passed one.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from vouchcode.comprehension.questions import Question, generate_questions
from vouchcode.comprehension.scoring import AnswerScore, score_answer
from vouchcode.segmentation.hunks import Hunk

# Reader used when none is supplied. Indirected so that tests can drive the prompt with
# scripted answers without a pseudo-terminal.
AnswerReader = Callable[[str], str]


class AnswersUnavailable(Exception):
    """Raised when answers cannot be collected at all.

    Distinct from an empty answer, and the distinction is the whole point. An empty
    answer is a developer declining to explain, which is a failure. An unavailable
    answer is Vouchcode being unable to ask, which is not the developer's fault and must
    never be recorded as a failed check.

    isatty is not sufficient to tell these apart. A hook can inherit a stdin that
    reports as a terminal and then returns end of file on the first read, which is what
    happens when a commit is driven by a test harness or a wrapper process. Reaching
    this exception is the reliable signal, because it is the read itself failing.
    """


@dataclass
class HunkResult:
    """The comprehension outcome for one hunk."""

    path: str
    qualname: str
    scores: list[AnswerScore] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)

    @property
    def mean_score(self) -> float:
        if not self.scores:
            return 0.0
        return round(sum(score.value for score in self.scores) / len(self.scores), 4)

    @property
    def passed(self) -> bool:
        """Every question must be answered adequately, not merely most of them.

        Averaging would let a developer skate through a hunk by answering one question
        well and guessing at the rest, which is exactly the behavior this layer
        exists to detect.
        """
        return bool(self.scores) and all(score.passed for score in self.scores)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the ledger, with a fixed key order for Phase 4 hashing."""
        return {
            "path": self.path,
            "qualname": self.qualname,
            "passed": self.passed,
            "mean_score": self.mean_score,
            "answers": [
                {**question.to_dict(), **score.to_dict()}
                for question, score in zip(self.questions, self.scores, strict=True)
            ],
        }


def run_comprehension_check(
    hunks: list[Hunk],
    console: Console | None = None,
    reader: AnswerReader | None = None,
) -> list[HunkResult]:
    """Prompt for and score answers covering the AI-attributed hunks.

    Returns one result per hunk that produced questions. A hunk with no answerable
    structure yields no result rather than an empty pass, so that a commit full of
    trivial generated code is not recorded as having been verified.
    """
    out = console or Console(soft_wrap=True, markup=False, highlight=False)
    ask = reader or _default_reader

    results: list[HunkResult] = []

    for hunk in hunks:
        questions = generate_questions(hunk.source)
        if not questions:
            continue

        _show_hunk(out, hunk)

        result = HunkResult(path=hunk.path, qualname=hunk.qualname)

        for index, question in enumerate(questions, start=1):
            out.print(f"\nquestion {index} of {len(questions)}: {question.text}")
            answer = ask("answer: ")
            score = score_answer(answer, question.fact, question.distractors)

            result.questions.append(question)
            result.scores.append(score)

            out.print(f"  {score.verdict}, score {score.value:.2f}: {score.reason}")

        results.append(result)

    return results


def is_interactive() -> bool:
    """Whether a terminal is attached that a developer could answer through.

    A commit run from a hook with stdout redirected still has a usable stdin, but one
    with no stdin at all cannot be answered.
    """
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _show_hunk(console: Console, hunk: Hunk) -> None:
    """Display the code a developer is about to be asked about.

    The code is shown before the questions, deliberately. The point is not to test
    recall of something they cannot see; it is to require that they read and can
    account for what they are committing.
    """
    attribution = hunk.attribution or {}
    source = attribution.get("source", "unknown")
    confidence = attribution.get("confidence")
    confidence_text = "unknown" if confidence is None else f"{float(confidence):.2f}"

    console.print("")
    console.print(
        Panel(
            Syntax(hunk.source or "", "python", theme="ansi_dark", line_numbers=False),
            title=f"{hunk.path}: {hunk.qualname}",
            subtitle=f"attributed ai via {source}, confidence {confidence_text}",
            border_style="none",
        )
    )


def _default_reader(prompt_text: str) -> str:
    """Read one answer from the terminal, treating interruption as an empty answer.

    An interrupted prompt must not be recorded as a passed check, and an empty answer
    scores as unanswered, so surfacing it that way is both simpler and more honest than
    raising through the hook.
    """
    try:
        return input(prompt_text)
    except EOFError as exc:
        # Nothing is attached to answer. Not a wrong answer, an unaskable question.
        raise AnswersUnavailable(
            "the input stream reported end of file before an answer could be read"
        ) from exc
    except KeyboardInterrupt as exc:
        raise AnswersUnavailable(
            "the comprehension check was interrupted before it finished"
        ) from exc
