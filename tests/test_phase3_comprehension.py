"""Phase 3 exit criterion tests.

The exit criterion is that a shallow answer stuffed with the right keywords, but
demonstrating no reasoning about the actual control flow, is distinguishable from both a
genuine correct answer and a confidently wrong one. All three are asserted against the
same hunk and the same question, because separating them on different questions would
prove nothing.

The ordering assertion matters as much as the individual verdicts. Correct must outrank
shallow, and shallow must outrank incorrect: engaging with the right region of code
without understanding it is worth more than nothing, and asserting something false about
the code is worth less than admitting nothing.

Scoring is deterministic and local, per Section 3.1 and CLAUDE.md Rule 4. Nothing in
this module or the code it exercises makes a network call or consults a language model.
"""

from __future__ import annotations

import json
from pathlib import Path

from support import run_git, run_vouchcode, write_file

from vouchcode.comprehension import engine
from vouchcode.comprehension.facts import (
    FACT_EMPTY_ITERATION,
    FACT_EXCEPTION_HANDLER,
    FACT_GUARD_RETURN,
    extract_facts,
)
from vouchcode.comprehension.questions import generate_questions
from vouchcode.comprehension.scoring import (
    PASS_THRESHOLD,
    VERDICT_CORRECT,
    VERDICT_INCORRECT,
    VERDICT_PARTIAL,
    VERDICT_SHALLOW,
    VERDICT_UNANSWERED,
    score_answer,
)
from vouchcode.segmentation.hunks import Hunk

HUNK_SOURCE = """def normalize_records(records, strict):
    if records is None:
        raise ValueError("records required")
    result = {}
    for key, value in records.items():
        if value is None and strict:
            return None
        result[key] = value.strip()
    return result
"""


def _guard_question():
    """The question about the records-is-None guard, used by the criterion tests."""
    return next(
        question
        for question in generate_questions(HUNK_SOURCE)
        if question.fact.kind == FACT_GUARD_RETURN
        and "records" in question.fact.condition
    )


def _score(answer: str):
    question = _guard_question()
    return score_answer(answer, question.fact, question.distractors)


# ---------------------------------------------------------------------------
# Exit criterion: three answers to one question must land in three places
# ---------------------------------------------------------------------------

CORRECT_ANSWER = (
    "If records is None the function raises a ValueError, because it needs records "
    "to work with."
)

KEYWORD_STUFFED_ANSWER = (
    "records none valueerror raise error exception required strict value"
)

CONFIDENTLY_WRONG_ANSWER = (
    "When records is None, the function simply returns None so the caller gets an "
    "empty result back."
)


def test_correct_answer_is_recognized() -> None:
    """An answer relating the guard to the outcome passes."""
    score = _score(CORRECT_ANSWER)

    assert score.verdict == VERDICT_CORRECT
    assert score.passed is True
    assert score.value >= PASS_THRESHOLD


def test_keyword_stuffed_answer_is_recognized_as_shallow() -> None:
    """Right terms, no claim. Must not pass, and must not be called incorrect either.

    This answer contains more of the code's terms than the correct one does, which is
    exactly why term overlap alone cannot grade it.
    """
    score = _score(KEYWORD_STUFFED_ANSWER)

    assert score.verdict == VERDICT_SHALLOW
    assert score.passed is False
    assert "does not connect them" in score.reason


def test_confidently_wrong_answer_is_recognized_as_incorrect() -> None:
    """Fluent, connected, and asserting the wrong outcome."""
    score = _score(CONFIDENTLY_WRONG_ANSWER)

    assert score.verdict == VERDICT_INCORRECT
    assert score.passed is False
    assert "but the code produces" in score.reason


def test_the_three_cases_are_ordered_correctly() -> None:
    """The exit criterion, stated as one assertion.

    All three scored against the same question, ranked, and required to be distinct.
    """
    correct = _score(CORRECT_ANSWER)
    shallow = _score(KEYWORD_STUFFED_ANSWER)
    wrong = _score(CONFIDENTLY_WRONG_ANSWER)

    assert correct.value > shallow.value > wrong.value, (
        f"expected correct > shallow > incorrect, got "
        f"{correct.value} / {shallow.value} / {wrong.value}"
    )

    assert len({correct.verdict, shallow.verdict, wrong.verdict}) == 3
    assert correct.passed and not shallow.passed and not wrong.passed


def test_keyword_stuffing_matches_more_terms_than_the_correct_answer() -> None:
    """The premise the scorer has to defeat, asserted directly.

    If this ever stops being true the exit criterion tests get easier for the wrong
    reason, and the guard they provide quietly weakens.
    """
    correct = _score(CORRECT_ANSWER)
    shallow = _score(KEYWORD_STUFFED_ANSWER)

    assert shallow.components["matched_terms"] > correct.components["matched_terms"]


# ---------------------------------------------------------------------------
# Regression tests for the scoring bugs found during Phase 3
# ---------------------------------------------------------------------------


def test_stuffing_padded_with_articles_is_still_shallow() -> None:
    """Padding a keyword list with articles must not defeat the stuffing gate.

    The first implementation measured function word density alone, and scored this
    answer 1.0 correct. Density was replaced by a composite that also requires a linking
    word and some vocabulary of the answer's own.
    """
    score = _score(
        "the records is the none and the valueerror the raise the error the "
        "exception the strict"
    )

    assert score.verdict == VERDICT_SHALLOW
    assert score.passed is False


def test_terse_correct_answer_is_not_penalized_for_brevity() -> None:
    """A precise two-word answer passes.

    The question already quotes the condition back to the developer, so restating it
    demonstrates nothing. An earlier weighting required the restatement and marked this
    answer down to partial.
    """
    score = _score("raises ValueError")

    assert score.verdict == VERDICT_CORRECT
    assert score.passed is True


def test_restating_the_condition_is_not_read_as_a_rival_outcome_claim() -> None:
    """Saying None while quoting the guard must not register as hedging.

    An answer to a question about "records is None" will almost always contain the word
    None. An earlier hedging check read that as a claim that the function returns None,
    and marked every correct answer as hedging between two outcomes.
    """
    score = _score(CORRECT_ANSWER)

    assert score.verdict == VERDICT_CORRECT
    assert "hedges" not in score.reason


def test_hedging_between_two_outcomes_does_not_pass() -> None:
    """Covering both possibilities is not an explanation."""
    score = _score("it either returns None or raises a ValueError depending on things")

    assert score.verdict == VERDICT_PARTIAL
    assert score.passed is False
    assert "hedges" in score.reason


def test_naming_the_wrong_exception_does_not_pass() -> None:
    """Right shape, wrong specific. The exception type is the answer, not decoration."""
    score = _score("it raises a TypeError because the records argument has bad type")

    assert score.passed is False
    assert "typeerror" in score.reason.lower()


def test_empty_and_irrelevant_answers_are_unanswered() -> None:
    """Nothing and noise are both distinguishable from a wrong answer."""
    assert _score("").verdict == VERDICT_UNANSWERED
    assert _score("asdf qwer zxcv").verdict == VERDICT_UNANSWERED


# ---------------------------------------------------------------------------
# Fact extraction and question generation
# ---------------------------------------------------------------------------


def test_facts_cover_the_three_forms_named_in_the_research_document() -> None:
    """Branching, iteration, and exception handling all produce facts."""
    source = """def handler(items):
    if not items:
        return []
    for item in items:
        pass
    try:
        return sum(items)
    except TypeError:
        return None
"""
    kinds = {fact.kind for fact in extract_facts(source)}

    assert FACT_GUARD_RETURN in kinds
    assert FACT_EMPTY_ITERATION in kinds
    assert FACT_EXCEPTION_HANDLER in kinds


def test_questions_are_derived_from_this_hunk_not_a_fixed_bank() -> None:
    """Two different hunks produce different questions.

    A fixed question bank would be memorizable, which Section 6.2 names as a threat this
    design partially mitigates by deriving questions from the specific code.
    """
    first = {q.text for q in generate_questions(HUNK_SOURCE)}
    second = {
        q.text
        for q in generate_questions(
            "def other(limit):\n"
            "    while limit > 0:\n"
            "        limit -= 1\n"
            "    return limit\n"
        )
    }

    assert first and second
    assert not (first & second)


def test_question_generation_is_deterministic() -> None:
    """The same hunk yields the same questions in the same order, every time.

    A report that cannot be regenerated identically is not evidence.
    """
    runs = [[q.text for q in generate_questions(HUNK_SOURCE)] for _ in range(5)]

    assert all(run == runs[0] for run in runs)


def test_a_hunk_with_no_structure_yields_no_questions() -> None:
    """Inventing a question about a trivial function would be theatre."""
    assert generate_questions("def add(a, b):\n    return a + b\n") == []


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def _ai_hunk() -> Hunk:
    return Hunk(
        path="m.py",
        qualname="normalize_records",
        kind="function",
        change="added",
        lineno=1,
        end_lineno=9,
        attribution={"status": "ai", "source": "tool_signal", "confidence": 1.0},
        source=HUNK_SOURCE,
    )


def _answer_with(answers: list[str]):
    """Return a reader that replays scripted answers in order."""
    remaining = list(answers)

    def reader(_prompt: str) -> str:
        return remaining.pop(0) if remaining else ""

    return reader


def test_engine_passes_when_every_question_is_answered_correctly() -> None:
    """A commit whose questions are all answered adequately passes."""
    outcome = engine.verify(
        [_ai_hunk()],
        interactive=True,
        reader=_answer_with(
            [
                "raises ValueError",
                "the loop body never runs so it returns the empty result dict",
                "it returns None because the value is missing and strict is set",
            ]
        ),
    )

    assert outcome.status == "passed"
    assert outcome.passed is True
    assert outcome.blocks_commit is False


def test_engine_fails_and_blocks_when_answers_are_stuffed() -> None:
    """Keyword stuffing every question fails the commit, not passes it."""
    outcome = engine.verify(
        [_ai_hunk()],
        interactive=True,
        reader=_answer_with([KEYWORD_STUFFED_ANSWER] * 5),
    )

    assert outcome.status == "failed"
    assert outcome.blocks_commit is True
    assert "normalize_records" in outcome.rationale


def test_engine_requires_every_question_not_a_passing_average() -> None:
    """One good answer must not carry a hunk whose other answers are empty.

    Averaging would let a developer answer the easiest question and guess the rest,
    which is the behavior this layer exists to detect.
    """
    outcome = engine.verify(
        [_ai_hunk()],
        interactive=True,
        reader=_answer_with(["raises ValueError", "", ""]),
    )

    assert outcome.status == "failed"


def test_non_interactive_commit_is_skipped_never_recorded_as_passed() -> None:
    """A commit with no terminal cannot answer, and must not be credited as verified."""
    outcome = engine.verify([_ai_hunk()], interactive=False)

    assert outcome.status == "skipped_non_interactive"
    assert outcome.passed is False
    assert outcome.blocks_commit is False
    assert "rather than recorded as passed" in outcome.rationale


def test_commit_with_no_ai_hunks_needs_no_verification() -> None:
    """Nothing attributed to AI means nothing to verify."""
    outcome = engine.verify([], interactive=True)

    assert outcome.status == "not_required"
    assert outcome.blocks_commit is False


# ---------------------------------------------------------------------------
# End to end through a real commit
# ---------------------------------------------------------------------------


def _ledger(root: Path) -> dict:
    return json.loads((root / ".vouchcode" / "ledger.json").read_text(encoding="utf-8"))


def test_real_commit_records_comprehension_status(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A commit made without a terminal records the skip, with its reason.

    The test runner attaches no tty, which is exactly the non-interactive path, so this
    asserts the honest-skip behavior rather than a simulated pass.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "m.py", HUNK_SOURCE)
    run_git(["add", "m.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "Add normalize_records"], cwd=temp_repo, env=git_env)

    comprehension = _ledger(temp_repo)["entries"][0]["comprehension"]

    assert comprehension["status"] in {
        "skipped_non_interactive",
        "not_required",
        "no_questions_derivable",
    }
    assert comprehension["rationale"]
    assert comprehension["status"] != "passed", (
        "a commit made with no terminal must never be recorded as verified"
    )


def test_unavailable_answers_are_skipped_not_failed() -> None:
    """A terminal that reports as usable and then returns EOF must not fail the commit.

    isatty is not a reliable interactivity check: a hook can inherit a stdin that claims
    to be a terminal and then ends immediately, which is what happens under a test
    harness or a wrapper process. Scoring the resulting empty answers as wrong would
    refuse the developer's commit for the tool's blind spot.
    """
    from vouchcode.comprehension.prompt import AnswersUnavailable

    def eof_reader(_prompt: str) -> str:
        raise AnswersUnavailable("stream ended")

    outcome = engine.verify([_ai_hunk()], interactive=True, reader=eof_reader)

    assert outcome.status == "skipped_non_interactive"
    assert outcome.blocks_commit is False
    assert outcome.passed is False
