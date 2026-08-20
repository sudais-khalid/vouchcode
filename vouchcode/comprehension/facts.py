"""Extraction of verifiable structural facts from a hunk's AST. Phase 3.

A fact is something the code unambiguously determines and a reader who understood the
code could state: the condition guarding a branch, what a function returns when that
condition is false, the termination condition of a loop, the behavior when an iterated
collection is empty, the exception type a handler catches and its fallback.

Facts are the ground truth for both question generation and scoring, which is what keeps
the two halves consistent without a model in the loop.

Not implemented in Phase 1.
"""

from __future__ import annotations


def extract_facts(source: str) -> None:
    """Return the structural facts derivable from a source fragment. Phase 3."""
    raise NotImplementedError("AST fact extraction is Phase 3 work")
