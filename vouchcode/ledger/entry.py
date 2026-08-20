"""The schema of a single ledger entry.

One entry records one commit. Phase 1 populates the identity and change-set fields and
leaves attribution unclassified; later phases fill the remaining fields in place rather
than changing the entry's shape, so that a Phase 1 ledger stays readable by a Phase 5
reader.

Field lifecycle:

    commit, type, timestamp, author, branch,   Phase 1, written by the capture layer.
    parents, files
    attribution.status / source / confidence   Phase 2, written by the segmentation and
                                               attribution pass.
    hunks                                      Phase 2.
    comprehension                              Phase 3.
    previous_hash, entry_hash, signature       Phase 4.

Merge commits. An entry whose type is "merge" carries files as null rather than as a
list. This is deliberate and is not a missing value. A merge commit's diff against its
first parent reproduces every file brought in from the side branch, all of which the
side branch's own entries already record, so computing a file list here would double
count content. The only genuinely new content in a merge is conflict resolution, and
deciding whether that constitutes authored work is an attribution question that belongs
to Phase 2. Phase 1 records that the merge happened and defers the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Entry kinds. A commit with two or more parents is a merge regardless of which hook
# observed it, so that a merge resolved by hand through 'git commit' and one produced
# directly by 'git merge' are recorded identically.
ENTRY_TYPE_COMMIT = "commit"
ENTRY_TYPE_MERGE = "merge"

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
    files: list[str] | None
    parents: list[str] = field(default_factory=list)
    entry_type: str = ENTRY_TYPE_COMMIT
    attribution: dict[str, Any] = field(default_factory=unclassified_attribution)
    # Hunk-level segmentation results, written by Phase 2. Absent for merge commits and
    # for commits touching no Python file, which is a different statement from present
    # and empty: empty means the files were segmented and yielded nothing.
    hunks: list[dict[str, Any]] | None = None
    # Files that could not be parsed into an abstract syntax tree, recorded so that a
    # report states what it could not analyze rather than quietly omitting it.
    skipped: list[str] = field(default_factory=list)

    @property
    def is_merge(self) -> bool:
        """Whether this entry records a merge commit."""
        return self.entry_type == ENTRY_TYPE_MERGE

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the exact key order used on disk.

        Key order is fixed rather than incidental because Phase 4 will hash the
        serialized form, and a hash over an unstable ordering would produce spurious
        tamper reports.
        """
        return {
            "commit": self.commit,
            "type": self.entry_type,
            "timestamp": self.timestamp,
            "author": {"name": self.author_name, "email": self.author_email},
            "branch": self.branch,
            "parents": list(self.parents),
            "files": None if self.files is None else list(self.files),
            "attribution": dict(self.attribution),
            "hunks": None if self.hunks is None else [dict(h) for h in self.hunks],
            "skipped": list(self.skipped),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerEntry:
        """Reconstruct an entry from its serialized form."""
        author = data.get("author") or {}
        raw_files = data.get("files")
        return cls(
            commit=data["commit"],
            timestamp=data["timestamp"],
            author_name=author.get("name", ""),
            author_email=author.get("email", ""),
            branch=data.get("branch"),
            files=None if raw_files is None else list(raw_files),
            parents=list(data.get("parents") or []),
            entry_type=data.get("type", ENTRY_TYPE_COMMIT),
            attribution=dict(data.get("attribution") or unclassified_attribution()),
            hunks=None if data.get("hunks") is None else list(data["hunks"]),
            skipped=list(data.get("skipped") or []),
        )

    def file_count(self) -> int:
        """Return the number of recorded files, treating a merge's null as zero.

        Reporting code needs a number for every entry. Merges contribute nothing to a
        file count precisely because their content is already counted elsewhere.
        """
        return 0 if self.files is None else len(self.files)


def utc_timestamp() -> str:
    """Return the current time as an ISO 8601 string in UTC.

    UTC with an explicit offset rather than local time, so that entries from developers
    in different time zones order correctly in a shared report.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
