"""Deterministic question generation from extracted facts. Phase 3.

Questions are produced by templating over the facts of the specific hunk, so that the
question set differs with the code rather than being drawn from a fixed bank. Section
6.2 identifies rote pattern matching against a fixed bank as a threat this design
partially mitigates.

Question forms named in Section 4.3:

    branching    what does this function return when the guarding condition is false
    iteration    what happens if the collection being iterated over is empty
    exceptions   which error condition triggers this path, and what is the fallback

Generation is a pure function of the AST. No external language model, per Section 3.1
and CLAUDE.md Rule 4.

Not implemented in Phase 1.
"""

from __future__ import annotations


def generate_questions(facts: object) -> None:
    """Derive questions from a hunk's extracted structural facts. Phase 3."""
    raise NotImplementedError("question generation is Phase 3 work")
