"""Retroactive repository scan. Phase 5.

Applies the heuristic component of the capture layer to a repository's existing commit
history, producing a best-effort report for repositories that adopted Vouchcode after
development began, per Section 4.5.

Results from this path are heuristic by construction: there is no tool signal to recover
after the fact. Section 6.2 requires that they carry an explicit confidence level, and a
retroactive report must state plainly that it was reconstructed rather than captured, so
that a recipient does not read it as equivalent to a ledger built from live capture.

Not implemented in Phase 1.
"""

from __future__ import annotations


def scan_history(repo: object, since: str | None = None) -> None:
    """Reconstruct a best-effort ledger from existing commit history. Phase 5."""
    raise NotImplementedError("retroactive history scanning is Phase 5 work")
