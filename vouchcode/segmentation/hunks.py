"""The hunk, Vouchcode's unit of attribution.

A hunk is one coherent unit of logic (a function, a method, a class header, or the
module-level block) together with how it changed and who or what is judged to have
written it. Hunks, not lines, are what the comprehension engine questions and what the
ledger records.

Not every change produces a hunk that carries new logic, and the distinction is the
point of the segmentation layer. A function that was renamed with no other edit contains
exactly the code the developer already accounted for, so it is recorded as a hunk for
visibility but carries no new content, needs no attribution guess, and must never
be sent back through comprehension verification. A function whose body changed does
carry new
content, whatever its name is now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vouchcode.segmentation.astdiff import (
    CHANGE_ADDED,
    CHANGE_MODIFIED,
    CHANGE_MOVED,
    CHANGE_REMOVED,
    CHANGE_RENAMED,
    CHANGE_RENAMED_MODIFIED,
    DefinitionChange,
    diff_sources,
)
from vouchcode.segmentation.fingerprint import compute_fingerprint

# Change kinds that introduce logic the committing developer is accountable for and that
# therefore need attribution and, from Phase 3, comprehension verification.
CARRIES_NEW_LOGIC = frozenset({CHANGE_ADDED, CHANGE_MODIFIED, CHANGE_RENAMED_MODIFIED})

# Change kinds where the AST proves the logic is unchanged. These are recorded so that a
# report can show the work happened, but they are not attributed and not questioned.
STRUCTURALLY_UNCHANGED = frozenset({CHANGE_RENAMED, CHANGE_MOVED, CHANGE_REMOVED})

# Attribution sources, recording which mechanism produced a classification. The value
# matters to a report reader: a direct tool signal is evidence, a heuristic is an
# inference, and a structural proof is a certainty.
SOURCE_SIGNAL = "tool_signal"
SOURCE_STYLOMETRY = "stylometry"
SOURCE_STRUCTURAL = "structural"

# Status used for hunks the AST proves did not change.
STATUS_UNCHANGED = "unchanged"


@dataclass
class Hunk:
    """One changed unit of logic and its attribution."""

    path: str
    qualname: str
    kind: str
    change: str
    lineno: int
    end_lineno: int
    similarity: float = 0.0
    previous_qualname: str | None = None
    # Short hash of the definition's normalized syntax tree. Stored so that a report
    # recipient can re-derive it from the source and check it, and so that Phase 4's
    # hash chain covers the structural identity of the code rather than only its
    # description. Only meaningful alongside the entry's fingerprint_version tag: see
    # vouchcode.segmentation.fingerprint for why.
    fingerprint: str = ""
    attribution: dict[str, Any] = field(default_factory=dict)
    # The post-commit source of this hunk. Held in memory for the attribution and
    # comprehension passes and deliberately never serialized: the ledger records
    # provenance about code, not a second copy of the code itself.
    source: str = ""

    @property
    def carries_new_logic(self) -> bool:
        """Whether this hunk introduces logic the developer must account for."""
        return self.change in CARRIES_NEW_LOGIC

    @property
    def line_count(self) -> int:
        """Number of source lines the hunk spans in the post-commit file."""
        return max(0, self.end_lineno - self.lineno + 1)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the ledger, with a fixed key order for Phase 4 hashing."""
        return {
            "path": self.path,
            "qualname": self.qualname,
            "previous_qualname": self.previous_qualname,
            "kind": self.kind,
            "change": self.change,
            "lines": [self.lineno, self.end_lineno],
            "similarity": self.similarity,
            "fingerprint": self.fingerprint,
            "attribution": dict(self.attribution),
        }


def build_hunks(
    path: str,
    before: str,
    after: str,
) -> list[Hunk]:
    """Segment one file's change into hunks.

    Raises SegmentationError if either version fails to parse. The caller decides
    whether an unparseable file is fatal or simply skipped.
    """
    return [_from_change(path, change) for change in diff_sources(before, after, path)]


def _from_change(path: str, change: DefinitionChange) -> Hunk:
    """Convert one definition change into a hunk."""
    # A removal has no post-commit definition, so its position is reported from the
    # pre-commit side. Everything else reports its position after the change.
    anchor = change.after or change.before
    assert anchor is not None  # diff_sources never emits a change with neither side

    # Recorded only for a rename, where knowing the former name is what lets a reader
    # connect this hunk to the history it inherited.
    previous = None
    if change.kind in (CHANGE_RENAMED, CHANGE_RENAMED_MODIFIED) and change.before:
        previous = change.before.qualname

    hunk = Hunk(
        path=path,
        qualname=anchor.qualname,
        kind=anchor.kind,
        change=change.kind,
        lineno=anchor.lineno,
        end_lineno=anchor.end_lineno,
        similarity=change.similarity,
        previous_qualname=previous,
        fingerprint=compute_fingerprint(anchor.normalized_fingerprint),
        source=anchor.source,
    )

    if not hunk.carries_new_logic:
        # The abstract syntax tree proves the logic did not change. That is a certainty,
        # not an estimate, so it is recorded with full confidence and a structural
        # source rather than handed to the attribution pass to guess about.
        hunk.attribution = {
            "status": STATUS_UNCHANGED,
            "source": SOURCE_STRUCTURAL,
            "confidence": 1.0,
        }

    return hunk
