"""The hunk, Vouchcode's unit of attribution. Phase 2.

A hunk is one coherent unit of logic (a function, a method, a class body, or a
module-level block) together with the attribution assigned to it. Hunks, not lines, are
what the comprehension engine questions and what the ledger records.

Not implemented in Phase 1.
"""

from __future__ import annotations


def build_hunk(*args: object, **kwargs: object) -> None:
    """Construct a hunk record from a changed AST node. Phase 2."""
    raise NotImplementedError("hunk construction is Phase 2 work")
