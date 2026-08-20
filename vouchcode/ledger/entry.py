"""The schema of a single ledger entry.

One entry records one commit. Phase 1 populates the identity and change-set fields and
leaves attribution unclassified; later phases fill the remaining fields in place rather
than changing the entry's shape, so that a Phase 1 ledger stays readable by a Phase 5
reader.

Field lifecycle:

    commit, timestamp, author, branch, files   Phase 1, written by the capture layer.
    attribution.status / source / confidence   Phase 2, written by the segmentation and
                                               attribution pass.
    hunks                                      Phase 2.
    comprehension                              Phase 3.
    previous_hash, entry_hash, signature       Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Attribution status values. Phase 1 only ever emits UNCLASSIFIED; the remaining values
# are declared here so that the vocabulary is fixed before Phase 2 populates it.
ATTRIBUTION_UNCLASSIFIED = "unclassified"
ATTRIBUTION_HUMAN = "human"
ATTRIBUTION_AI = "ai"
ATTRIBUTION_MIXED = "mixed"


def unclassified_attribution() -> dict[str, Any]:
    """Return the Phase 1 placeholder attribution record.

    status is the classification, source records which mechanism produced it (a direct
    AI tool signal or the stylometric fallback), and confidence carries the explicit
    confidence level that Section 6.2 requires for any heuristic result. All three are
    unset in Phase 1 because no attribution has been attempted.
    """
    return {"status": ATTRIBUTION_UNCLASSIFIED, "source": None, "confidence": None}


@dataclass
class LedgerEntry:
    """One commit's provenance record."""

    commit: str
    timestamp: str
    author_name: str
    author_email: str
    branch: str | None
    files: list[str]
    attribution: dict[str, Any] = field(default_factory=unclassified_attribution)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the exact key order used on disk.

        Key order is fixed rather than incidental because Phase 4 will hash the
        serialized form, and a hash over an unstable ordering would produce spurious
        tamper reports.
        """
        return {
            "commit": self.commit,
            "timestamp": self.timestamp,
            "author": {"name": self.author_name, "email": self.author_email},
            "branch": self.branch,
            "files": list(self.files),
            "attribution": dict(self.attribution),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerEntry:
        """Reconstruct an entry from its serialized form."""
        author = data.get("author") or {}
        return cls(
            commit=data["commit"],
            timestamp=data["timestamp"],
            author_name=author.get("name", ""),
            author_email=author.get("email", ""),
            branch=data.get("branch"),
            files=list(data.get("files") or []),
            attribution=dict(data.get("attribution") or unclassified_attribution()),
        )


def utc_timestamp() -> str:
    """Return the current time as an ISO 8601 string in UTC.

    UTC with an explicit offset rather than local time, so that entries from developers
    in different time zones order correctly in a shared report.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
