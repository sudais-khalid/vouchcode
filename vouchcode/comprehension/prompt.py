"""Terminal prompt for the comprehension check. Phase 3.

Presents the hunk and its questions through Rich and collects typed answers, per
Section 4.3. Runs from the pre-commit hook, which is the point at which a commit can
still be refused, and is therefore where the gating exit code described in
vouchcode.capture.runner becomes a deliberate product behavior rather than a fault.

Not implemented in Phase 1.
"""

from __future__ import annotations


def run_comprehension_check(hunks: object) -> None:
    """Prompt for and score answers covering the AI-attributed hunks. Phase 3."""
    raise NotImplementedError("the comprehension prompt is Phase 3 work")
