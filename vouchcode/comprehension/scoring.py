"""Deterministic answer scoring against AST-derived facts.

Scores a typed answer by matching it against the structural facts extracted from the
hunk. Deliberately not semantic similarity against a model-generated reference answer:
the same answer must receive the same score on any machine, on any day, with no network,
which is what makes a Vouchcode report reproducible by the party receiving it.

The problem this module actually has to solve
---------------------------------------------

Term matching alone is trivially defeated. An answer that sprays every word from the
code back at the grader hits every term a correct answer would hit, and a scorer that
counts term overlap cannot tell the two apart. That failure would be worse than having
no
comprehension layer, because it would produce a signed attestation that a developer
understood code they never read.

Three answers to the same question must therefore land in three different places:

    a correct answer      relates the condition to the outcome, and names the right
                          outcome.
    a shallow answer      contains the right terms without connecting them into a claim.
    an incorrect answer   is fluent and connected but asserts the wrong outcome.

Separating them without a language model rests on three measurements, none of which
requires understanding the answer:

    relevance     does the answer refer to this fact at all, by naming the subject or
                  the condition.
    outcome       does it assert the outcome the code actually produces, judged by
                  outcome family rather than by loose word overlap, so that "returns
                  None" and "raises an error" are recognized as different claims.
    coherence     is the answer written as prose rather than assembled as a word list.
                  Measured from function word density and from whether a connective
                  links the condition to the outcome. Natural English is roughly forty
                  percent function words; a keyword list is near zero, because the words
                  that carry grammar are exactly the ones a stuffer has no reason to
                  include.

Coherence is only applied where stuffing is possible. A terse correct answer of three
words has no function words either, and penalizing it would punish precision. The gate
therefore triggers only when an answer hits several distinct terms while lacking the
grammar that would connect them, which is the actual signature of stuffing.

What this does not claim
------------------------

This measures whether an answer is structurally consistent with the code, not whether
the
developer understands it. Someone who reads the code carefully enough to answer
correctly
has demonstrated something real; someone who reasons their way to the right answer by
other means has defeated it. Section 6.2 records this as partially mitigated rather than
solved, and that assessment is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vouchcode.comprehension.facts import Fact
from vouchcode.comprehension.patterns import (
    EXCEPTION_RE,
    RETURN_VERB_WINDOW,
    RETURN_VERBS,
    WORD_RE,
)

VERDICT_CORRECT = "correct"
VERDICT_PARTIAL = "partial"
VERDICT_SHALLOW = "shallow"
VERDICT_INCORRECT = "incorrect"
VERDICT_UNANSWERED = "unanswered"

# An answer shorter than this is not an explanation of anything.
MIN_ANSWER_WORDS = 2

# Stuffing is only possible once an answer names several distinct things. Below this
# many
# matched terms, a low function-word ratio means the answer is terse, not assembled.
STUFFING_MIN_MATCHED_TERMS = 4

# Composite coherence below which an answer that names several of the code's terms is
# treated as a word list rather than an explanation. Set against the composite rather
# than against function word density alone: density by itself is defeated by padding a
# keyword list with articles, which was the first implementation's actual failure.
STUFFING_COHERENCE = 0.5

# Score ceiling applied to an answer identified as shallow. Kept above zero because the
# developer did engage with the right region of code, and below the passing threshold
# because engagement is not comprehension.
SHALLOW_SCORE_CEILING = 0.35

# Score at or above which an answer counts as demonstrating comprehension.
PASS_THRESHOLD = 0.6

# Outcome families. Two answers asserting different families are making different claims
# about what the code does, which is what lets a confident wrong answer be told apart
# from a correct one instead of both scoring on shared vocabulary.
FAMILY_RETURNS_NONE = "returns_none"
FAMILY_RETURNS_VALUE = "returns_value"
FAMILY_RAISES = "raises"
FAMILY_CONTINUES = "continues"
FAMILY_BREAKS = "breaks"
FAMILY_SKIPS = "skips"

_FAMILY_MARKERS: dict[str, tuple[str, ...]] = {
    FAMILY_RAISES: (
        "raise",
        "raises",
        "raised",
        "throw",
        "throws",
        "thrown",
        "error",
        "exception",
        "fails",
        "fail",
        "crash",
        "abort",
    ),
    FAMILY_RETURNS_NONE: ("none", "null", "nothing"),
    FAMILY_RETURNS_VALUE: (
        "return",
        "returns",
        "returned",
        "gives",
        "yields",
        "result",
    ),
    FAMILY_CONTINUES: (
        "continue",
        "continues",
        "proceed",
        "proceeds",
        "past",
        "after",
        "carries",
        "onward",
    ),
    FAMILY_BREAKS: ("break", "breaks", "exits", "exit", "leave", "leaves"),
    FAMILY_SKIPS: (
        "skip",
        "skips",
        "skipped",
        "never",
        "not run",
        "no iterations",
        "zero",
    ),
}

# Words that carry grammar rather than content. Their presence is what distinguishes a
# sentence from a pile of nouns. Deliberately a small, fixed, English list: it is a
# structural signal, not a vocabulary the developer has to match.
_FUNCTION_WORDS = frozenset(
    """
    a an the this that these those it its is are was were be been being am
    do does did doing done have has had having
    will would shall should can could may might must
    if when whenever while unless until because since so then thus therefore hence
    and or but nor yet however though although whereas
    in on at to from by for with without of off out up down over under into onto
    as than about after before during through between
    i we you he she they them us him her their our your my me
    what which who whom whose where why how
    not no never any some all both each either neither
    there here again also just only even still
    """.split()
)

# Words that join a condition to a consequence. Deliberately narrower than the function
# word list: an explanation says "if X then Y" or "X so Y", while padding a word list
# with
# articles produces "the X the Y", which satisfies density without asserting a relation.
# Copulas are excluded for exactly that reason.
_LINKING_WORDS = frozenset(
    """
    if when whenever while unless until because since so then therefore thus hence
    that which who where why how instead rather otherwise but however though although
    it this these those they meaning means causing causes leading resulting
    """.split()
)


# Outcome families that are mutually exclusive terminal behaviors. An answer asserting
# more than one of these is hedging rather than explaining.
_TERMINAL_FAMILIES = frozenset(
    {FAMILY_RETURNS_NONE, FAMILY_RETURNS_VALUE, FAMILY_RAISES}
)


@dataclass(frozen=True)
class AnswerScore:
    """The outcome of scoring one answer against one fact."""

    verdict: str
    value: float
    reason: str
    components: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.value >= PASS_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the ledger, with a fixed key order for Phase 4 hashing."""
        return {
            "verdict": self.verdict,
            "score": round(self.value, 4),
            "reason": self.reason,
            "components": {k: round(v, 4) for k, v in sorted(self.components.items())},
        }


def score_answer(
    answer: str,
    fact: Fact,
    distractors: tuple[Fact, ...] = (),
) -> AnswerScore:
    """Score one answer against the fact its question was derived from."""
    del distractors  # Reserved for cross-fact checks; the outcome family covers this.

    words = _words(answer)

    if len(words) < MIN_ANSWER_WORDS:
        return AnswerScore(
            verdict=VERDICT_UNANSWERED,
            value=0.0,
            reason="answer is too short to explain anything",
        )

    word_set = set(words)

    relevance = _coverage(word_set, (*fact.subject_terms, *fact.condition_terms))
    outcome = _coverage(word_set, fact.outcome_terms)

    expected_family = _family_of(fact.outcome_terms, fact.outcome)
    asserted = _asserted_families(answer, word_set)

    coherence, function_ratio = _coherence(answer, words, fact)
    matched_terms = _matched_count(
        word_set, (*fact.subject_terms, *fact.condition_terms, *fact.outcome_terms)
    )

    components = {
        "relevance": relevance,
        "outcome": outcome,
        "coherence": coherence,
        "function_word_ratio": function_ratio,
        "matched_terms": float(matched_terms),
    }

    # Not about this fact at all. Checked first, because an answer describing a
    # different
    # branch is not a shallow answer to this question, it is an answer to another one.
    if relevance <= 0.0 and outcome <= 0.0:
        return AnswerScore(
            verdict=VERDICT_UNANSWERED,
            value=0.0,
            reason="answer does not refer to this part of the code",
            components=components,
        )

    # Stuffing gate. An answer naming several of the code's terms without the structure
    # that would relate them is a word list, whatever its term coverage. Gated on the
    # composite coherence rather than on function word density alone, because density by
    # itself is defeated by padding the list with articles.
    if matched_terms >= STUFFING_MIN_MATCHED_TERMS and coherence < STUFFING_COHERENCE:
        return AnswerScore(
            verdict=VERDICT_SHALLOW,
            value=min(SHALLOW_SCORE_CEILING, round((relevance + outcome) / 2, 4)),
            reason=(
                "answer names the right terms but does not connect them into a claim "
                "about what the code does"
            ),
            components=components,
        )

    # Wrong claim. The answer asserts an outcome family the code does not produce and
    # does not assert the one it does. This is the fluent, confident, incorrect case,
    # and
    # it scores below a shallow answer: asserting something false is worse than
    # asserting nothing.
    if expected_family and asserted and expected_family not in asserted:
        return AnswerScore(
            verdict=VERDICT_INCORRECT,
            value=round(0.15 * relevance, 4),
            reason=(
                f"answer describes a {'/'.join(sorted(asserted))} outcome, "
                f"but the code produces {fact.outcome}"
            ),
            components=components,
        )

    # Hedging. Asserting two mutually exclusive terminal outcomes is not an explanation,
    # it is covering both options. Scored as partial rather than correct, because one of
    # the two claims is necessarily false.
    terminal_claims = asserted & _TERMINAL_FAMILIES
    if len(terminal_claims) > 1:
        return AnswerScore(
            verdict=VERDICT_PARTIAL,
            value=min(SHALLOW_SCORE_CEILING, round(0.3 * relevance + 0.3 * outcome, 4)),
            reason=(
                "answer hedges between "
                f"{' and '.join(sorted(terminal_claims))} rather than stating which "
                "the code does"
            ),
            components=components,
        )

    # Named the wrong exception. Right shape of answer, wrong specific. Caught
    # separately
    # from the family check, which only sees that both are raises.
    wrong_exception = _names_a_different_exception(word_set, fact.outcome)
    if wrong_exception:
        return AnswerScore(
            verdict=VERDICT_PARTIAL,
            value=min(SHALLOW_SCORE_CEILING, round(0.3 * relevance, 4)),
            reason=(
                f"answer names {wrong_exception}, but the code produces {fact.outcome}"
            ),
            components=components,
        )

    # Weighted toward the outcome. The question already quotes the condition back to the
    # developer, so repeating it demonstrates nothing; naming what the code does about
    # it
    # is the part that requires having read the code. An answer of "raises ValueError"
    # is complete, and must not be marked down for declining to restate the question.
    value = round(0.2 * relevance + 0.65 * outcome + 0.15 * coherence, 4)

    if outcome <= 0.0:
        return AnswerScore(
            verdict=VERDICT_PARTIAL,
            value=min(value, SHALLOW_SCORE_CEILING),
            reason="answer names the condition but not what the code does about it",
            components=components,
        )

    if value >= PASS_THRESHOLD:
        verdict = VERDICT_CORRECT
        reason = "answer relates the condition to the outcome the code produces"
    else:
        verdict = VERDICT_PARTIAL
        reason = "answer is on the right track but incomplete"

    return AnswerScore(
        verdict=verdict, value=value, reason=reason, components=components
    )


def _names_a_different_exception(word_set: set[str], outcome: str) -> str:
    """Return the exception an answer named when the code raises a different one.

    Only fires when both sides name something specific. An answer that says "raises an
    error" without naming a type is not wrong, it is unspecific, and is scored on its
    merits by the ordinary path.
    """
    expected = {
        match.group(1).lower() for match in EXCEPTION_RE.finditer(outcome.lower())
    }
    if not expected:
        return ""

    generic = {"error", "exception"}
    named = {
        word
        for word in word_set
        if word.endswith(("error", "exception")) and word not in generic
    }
    if not named:
        return ""

    if named & expected:
        return ""

    return sorted(named)[0]


def _words(text: str) -> list[str]:
    """Tokenize an answer into lowercased word-like tokens."""
    return [match.group(0).lower() for match in WORD_RE.finditer(text or "")]


# Credit for naming a term group. A term group is a list of ways a person might phrase
# one idea, so hitting any of them means they expressed it. One match earns most of the
# credit and a second earns the rest, which keeps a gradient without demanding that a
# concise answer use two synonyms for the same thing.
_SINGLE_MATCH_CREDIT = 0.75


def _coverage(word_set: set[str], terms: tuple[str, ...]) -> float:
    """How fully the answer names a group of interchangeable terms.

    Requiring several matches punishes precision. A correct answer to "what happens if
    the collection is empty" may carry exactly one of the outcome words, because one is
    all the sentence needs, and grading it at half credit was wrong.
    """
    if not terms:
        return 0.0

    matched = _matched_count(word_set, terms)
    if matched == 0:
        return 0.0
    if matched == 1:
        return _SINGLE_MATCH_CREDIT
    return 1.0


def _matched_count(word_set: set[str], terms: tuple[str, ...]) -> int:
    """Number of distinct terms from a group that appear in the answer."""
    return len({term for term in terms if term and term in word_set})


def _family_of(outcome_terms: tuple[str, ...], outcome: str) -> str:
    """Classify what a fact's outcome actually is.

    Read from the rendered outcome text first, because it states the behavior directly,
    and the term list is a superset built for matching rather than for classification.
    """
    lowered = outcome.lower()

    if lowered.startswith("raises") or lowered.startswith("re-raises"):
        return FAMILY_RAISES
    if lowered.startswith("returns none"):
        return FAMILY_RETURNS_NONE
    if lowered.startswith("returns"):
        return FAMILY_RETURNS_VALUE
    if "never executes" in lowered:
        return FAMILY_SKIPS
    if lowered.startswith("skips"):
        return FAMILY_CONTINUES
    if lowered.startswith("exits"):
        return FAMILY_BREAKS
    if "continues" in lowered:
        return FAMILY_CONTINUES

    del outcome_terms  # Only the rendered outcome is authoritative for classification.
    return ""


def _asserted_families(answer: str, word_set: set[str]) -> set[str]:
    """Which outcome families the answer claims.

    Multi-word markers are checked against the raw text, single words against the token
    set, so that "does not run" is recognized without tokenization losing the phrase.
    """
    lowered = (answer or "").lower()
    words = _words(answer)
    asserted: set[str] = set()

    for family, markers in _FAMILY_MARKERS.items():
        if family == FAMILY_RETURNS_NONE:
            # Handled separately below. A bare "None" is far more often the developer
            # restating the guard they were asked about, as in "if records is None",
            # than a claim that the function returns None.
            continue
        for marker in markers:
            if " " in marker:
                if marker in lowered:
                    asserted.add(family)
                    break
            elif marker in word_set:
                asserted.add(family)
                break

    if _claims_returns_none(words):
        asserted.add(FAMILY_RETURNS_NONE)

    # "returns None" is a returns_value phrasing too, but the None is the specific
    # claim.
    # Collapsing lets an answer saying "returns None" not read as asserting two rival
    # families at once.
    if FAMILY_RETURNS_NONE in asserted:
        asserted.discard(FAMILY_RETURNS_VALUE)

    return asserted


def _claims_returns_none(words: list[str]) -> bool:
    """Whether the answer asserts that the code returns None, rather than mentioning it.

    The distinction matters because the question quotes the guard back at the developer.
    An answer to "when records is None, what does this code do" will almost always
    contain the word None while making no claim about a None return at all, and treating
    that as a rival outcome claim would mark every correct answer as hedging. It did,
    before this existed.
    """
    null_words = {"none", "null", "nothing"}

    for index, word in enumerate(words):
        if word not in null_words:
            continue
        window = words[max(0, index - RETURN_VERB_WINDOW) : index]
        if any(candidate in RETURN_VERBS for candidate in window):
            return True

    return False


def _coherence(answer: str, words: list[str], fact: Fact) -> tuple[float, float]:
    """Measure whether the answer reads as an explanation, and return the ratio used.

    Three contributions, because any one alone is defeatable:

        density    function words present at all, so the answer has grammar.
        linking    a word that joins a condition to a consequence is present. Narrower
                   than density, because padding a list with articles raises density
                   without asserting any relation between the terms.
        novelty    some content words are the answer's own rather than echoes of the
                   code. An explanation introduces vocabulary; a regurgitation does not.

    Density alone was the first implementation and it was trivially defeated by padding
    a keyword list with articles, which is why the other two exist.
    """
    if not words:
        return 0.0, 0.0

    function_hits = sum(1 for word in words if word in _FUNCTION_WORDS)
    ratio = function_hits / len(words)

    # Saturating at the low end of natural English rather than the average, so that a
    # terse but grammatical answer is not penalized for brevity.
    density_score = min(1.0, ratio / 0.30)

    link_score = 1.0 if _links_condition_to_outcome(words, fact) else 0.0
    novelty_score = _novelty(words, fact)

    coherence = 0.35 * density_score + 0.35 * link_score + 0.30 * novelty_score
    return round(coherence, 4), round(ratio, 4)


def _novelty(words: list[str], fact: Fact) -> float:
    """Fraction of content words that are the answer's own rather than echoes.

    A regurgitated word list is made entirely of terms lifted from the code. A real
    explanation contains words the code never used, because describing behavior requires
    vocabulary the code does not contain.
    """
    echoed = (
        set(fact.subject_terms) | set(fact.condition_terms) | set(fact.outcome_terms)
    )

    content = [word for word in words if word not in _FUNCTION_WORDS]
    if not content:
        return 0.0

    novel = sum(1 for word in content if word not in echoed)
    # Saturating at half, since a correct terse answer is legitimately mostly echo.
    return min(1.0, (novel / len(content)) / 0.5)


def _links_condition_to_outcome(words: list[str], fact: Fact) -> bool:
    """Whether a condition term and an outcome term appear in one connected span.

    Connectedness is approximated by a function word occurring between them. That is a
    weak test on its own and a useful one in combination: an explanation says "if X then
    Y", while a word list says "X Y".
    """
    condition_terms = set(fact.subject_terms) | set(fact.condition_terms)
    outcome_terms = set(fact.outcome_terms)

    condition_positions = [i for i, w in enumerate(words) if w in condition_terms]
    outcome_positions = [i for i, w in enumerate(words) if w in outcome_terms]

    if not condition_positions or not outcome_positions:
        return False

    for start in condition_positions:
        for end in outcome_positions:
            low, high = (start, end) if start < end else (end, start)
            if high - low <= 1:
                continue
            if any(word in _LINKING_WORDS for word in words[low + 1 : high]):
                return True

    # A linking word anywhere in a short answer still signals an explanation, since the
    # linker often precedes both terms, as in "if X then Y".
    if len(words) <= 25 and any(word in _LINKING_WORDS for word in words):
        return True

    return False
