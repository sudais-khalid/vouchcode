"""Structural comparison of two Python abstract syntax trees. Phase 2.

Parses the pre- and post-commit versions of a file with the standard library ast module
and identifies which top-level and nested definitions actually changed.

Implementation must distinguish these cases, which a naive node-by-node comparison
conflates:

    renamed definition       identifier changed, body identical
    moved definition         position changed, body identical
    modified body            identifier identical, body changed
    added or removed         no counterpart in the other tree

Reporting a rename as a full rewrite would attribute unchanged, previously understood
code to a new AI-generated hunk and send it back through comprehension verification for
no reason, which is both wrong and a bad user experience.

Not implemented in Phase 1.
"""

from __future__ import annotations


def diff_sources(before: str, after: str) -> None:
    """Return the changed definitions between two versions of a source file. Phase 2."""
    raise NotImplementedError("AST structural diffing is Phase 2 work")
