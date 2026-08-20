"""Exception hierarchy shared across every Vouchcode layer.

Errors are separated into two kinds. A VouchcodeError is an expected, actionable
condition that the CLI renders as a concise message and a non-zero exit code. Anything
else propagates as a normal traceback, because an unexpected failure in a provenance
tool should be loud rather than swallowed.
"""

from __future__ import annotations


class VouchcodeError(Exception):
    """Base class for expected, actionable Vouchcode failures."""


class NotAGitRepositoryError(VouchcodeError):
    """Raised when a command requires a git repository and none was found."""


class NotInitializedError(VouchcodeError):
    """Raised when a command requires .vouchcode/ and the directory is absent."""


class HookInstallationError(VouchcodeError):
    """Raised when a git hook cannot be written or would overwrite foreign content."""


class LedgerError(VouchcodeError):
    """Raised when the ledger file is unreadable, malformed, or cannot be written."""


class SegmentationError(VouchcodeError):
    """Raised when a source file cannot be parsed into an abstract syntax tree.

    Not every Python file in a commit is parseable by the interpreter running Vouchcode.
    A file may target a newer syntax, or carry template placeholders. That is a normal
    condition to report and skip, not a crash.
    """
