"""Deterministic question generation from extracted facts.

Questions are produced by templating over the facts of the specific hunk, so the
question set differs with the code rather than being drawn from a fixed bank. Section
6.2 identifies rote pattern matching against a fixed bank as a threat this design
partially mitigates: a developer who has seen one Vouchcode quiz has not seen the next
one, because the next one quotes their own control flow back at them.

Question forms follow Section 4.3:

    branching    what the function does when a guarding condition holds
    iteration    what happens when the collection being iterated is empty
    exceptions   which error triggers a path and what the fallback is

Generation is a pure function of the AST. No external language model, per Section 3.1
and CLAUDE.md Rule 4.

Question selection. A hunk with twenty branches does not produce twenty questions. Facts
are ranked so that the ones a reader must actually understand come first, and the set is
capped, because a quiz long enough to be resented is a quiz that gets clicked through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vouchcode.comprehension.facts import (
    FACT_EMPTY_ITERATION,
    FACT_EXCEPTION_HANDLER,
    FACT_GUARD_RETURN,
    FACT_LOOP_TERMINATION,
    Fact,
    extract_facts,
)

# Most questions asked about any single hunk.
MAX_QUESTIONS_PER_HUNK = 3

# Ranking of fact kinds, most worth asking first. Exception paths lead because they are
# the least likely to have been read: an assistant's error handling is the part a
# developer skims. Empty iteration follows because its answer is not visible in the loop
# body and has to be reasoned about.
_KIND_PRIORITY = {
    FACT_EXCEPTION_HANDLER: 0,
    FACT_EMPTY_ITERATION: 1,
    FACT_GUARD_RETURN: 2,
    FACT_LOOP_TERMINATION: 3,
}

_TEMPLATES = {
    FACT_GUARD_RETURN: "When {condition}, what does this code do and why?",
    FACT_EMPTY_ITERATION: "What happens if {subject} is empty when this code runs?",
    FACT_EXCEPTION_HANDLER: (
        "Which condition triggers the {subject} path here, and what happens instead?"
    ),
    FACT_LOOP_TERMINATION: "What makes this loop stop, and what is true when it does?",
}


@dataclass(frozen=True)
class Question:
    """One question, bound to the fact it was derived from and scored against."""

    text: str
    fact: Fact
    # Facts from the same hunk that this question is not about. Scoring uses them to
    # detect an answer that describes a different part of the code, which is a specific
    # and common way of being wrong.
    distractors: tuple[Fact, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the ledger, with a fixed key order for Phase 4 hashing."""
        return {"question": self.text, "fact": self.fact.to_dict()}


def generate_questions(
    source: str,
    limit: int = MAX_QUESTIONS_PER_HUNK,
) -> list[Question]:
    """Derive questions from a hunk's source.

    Returns an empty list when the hunk has no answerable structure, which is a normal
    outcome: a three-line function with no branches, no loop, and no error handling has
    nothing to verify comprehension of, and inventing a question about it would be
    theatre.
    """
    facts = extract_facts(source)
    if not facts:
        return []

    selected = _select(facts, limit)

    return [
        Question(
            text=_render(fact),
            fact=fact,
            distractors=tuple(other for other in facts if other is not fact),
        )
        for fact in selected
    ]


def _select(facts: list[Fact], limit: int) -> list[Fact]:
    """Rank facts by how much understanding they demand, then cap the set.

    Ties break on source order so that a given hunk always produces the same
    questions in the same order. Reproducibility is not cosmetic here: a report that
    cannot be regenerated identically is not evidence.
    """
    ranked = sorted(
        facts,
        key=lambda fact: (_KIND_PRIORITY.get(fact.kind, 99), fact.lineno),
    )
    chosen = ranked[: max(0, limit)]
    return sorted(chosen, key=lambda fact: fact.lineno)


def _render(fact: Fact) -> str:
    """Fill the template for a fact's kind."""
    template = _TEMPLATES.get(fact.kind)
    if template is None:
        return f"Explain what happens when {fact.condition}."
    return template.format(subject=fact.subject, condition=fact.condition)
