"""Stylometric attribution fallback. Phase 2.

Used when no direct tool signal exists, for example when a developer pastes code from a
chat interface. Compares statistical characteristics of new code (variable naming
entropy, comment density, structural regularity) against a baseline built from the
developer's own prior commits in the repository, per Section 4.1.

Section 6.2 requires that results from this path always carry an explicit confidence
level and are never reported as a binary classification. That requirement is a property
of the output type, not a formatting choice at the call site.

Not implemented in Phase 1.
"""

from __future__ import annotations

from typing import Any


def build_baseline(repo: Any, author_email: str) -> None:
    """Build a stylometric baseline from an author's prior commits. Phase 2."""
    raise NotImplementedError("stylometric baseline construction is Phase 2 work")


def score_against_baseline(source: str, baseline: Any) -> None:
    """Score a source fragment against a baseline, returning a confidence. Phase 2."""
    raise NotImplementedError("stylometric scoring is Phase 2 work")
