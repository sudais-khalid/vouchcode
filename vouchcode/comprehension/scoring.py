"""Deterministic answer scoring against AST-derived facts. Phase 3.

Scores a typed answer by matching key structural terms against the facts extracted from
the hunk. Deliberately not semantic similarity against a model-generated reference
answer: the same answer must receive the same score on any machine, on any day, with no
network, which is what makes a Vouchcode report reproducible by the party receiving it.

Not implemented in Phase 1.
"""

from __future__ import annotations


def score_answer(answer: str, fact: object) -> None:
    """Score one answer against the fact its question was derived from. Phase 3."""
    raise NotImplementedError("answer scoring is Phase 3 work")
