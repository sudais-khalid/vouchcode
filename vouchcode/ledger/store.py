"""Reading and appending the local JSON ledger at .vouchcode/ledger.json.

The store is append-only by contract. Nothing in the Vouchcode CLI rewrites or deletes
an existing entry, because the ledger's value rests on the claim that entries are only
ever added. Phase 4 enforces that claim cryptographically with a hash chain; Phase 1
establishes it as an invariant of this module.

Writes are atomic. A ledger truncated by a crash midway through a write would be
indistinguishable from a tampered ledger, so every write goes to a temporary file in the
same directory and is then renamed over the target.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from vouchcode.config import LEDGER_SCHEMA_VERSION, RepoContext
from vouchcode.errors import LedgerError
from vouchcode.ledger.entry import LedgerEntry


def empty_ledger() -> dict[str, Any]:
    """Return the document a freshly initialized ledger contains."""
    return {"schema_version": LEDGER_SCHEMA_VERSION, "entries": []}


def read_ledger(path: Path) -> dict[str, Any]:
    """Load the ledger document, returning an empty ledger when the file is absent.

    A missing file is a normal state (an initialized repository with no commits yet).
    A malformed file is not, and raises rather than being silently replaced, because
    overwriting an unparseable ledger would discard exactly the evidence a tamper
    investigation needs.
    """
    if not path.is_file():
        return empty_ledger()

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerError(f"cannot read ledger {path}: {exc}") from exc

    if not raw.strip():
        return empty_ledger()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerError(
            f"ledger {path} is not valid JSON at line {exc.lineno} column {exc.colno}: "
            f"{exc.msg}"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise LedgerError(
            f"ledger {path} does not match the expected structure "
            "(an object with an 'entries' array)"
        )

    return data


def read_entries(path: Path) -> list[LedgerEntry]:
    """Load the ledger and return its entries as typed records."""
    document = read_ledger(path)
    try:
        return [LedgerEntry.from_dict(item) for item in document["entries"]]
    except (KeyError, TypeError, AttributeError) as exc:
        raise LedgerError(f"ledger {path} contains a malformed entry: {exc}") from exc


def append_entry(path: Path, entry: LedgerEntry) -> dict[str, Any]:
    """Append one entry to the ledger and persist it, returning the updated document."""
    document = read_ledger(path)
    document.setdefault("schema_version", LEDGER_SCHEMA_VERSION)
    document["entries"].append(entry.to_dict())
    write_ledger(path, document)
    return document


def write_ledger(path: Path, document: dict[str, Any]) -> None:
    """Serialize the ledger document to disk atomically."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LedgerError(
            f"cannot create ledger directory {path.parent}: {exc}"
        ) from exc

    serialized = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    temp_path = path.with_name(path.name + ".tmp")

    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        # Leaving a stale temporary file behind would make the next write look like it
        # collided with a concurrent one, so clean it up before surfacing the failure.
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise LedgerError(f"cannot write ledger {path}: {exc}") from exc


def initialize_ledger(ctx: RepoContext) -> Path:
    """Create .vouchcode/ and an empty ledger if one is not already present.

    Idempotent: rerunning vouchcode init on a repository with history must not discard
    that history.
    """
    try:
        ctx.vouchcode_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LedgerError(f"cannot create {ctx.vouchcode_dir}: {exc}") from exc

    if not ctx.ledger_path.exists():
        write_ledger(ctx.ledger_path, empty_ledger())

    return ctx.ledger_path


def contains_commit(path: Path, commit: str) -> bool:
    """Return whether an entry for the given commit hash already exists.

    Guards against duplicate entries when a hook fires more than once for one commit,
    which happens with some git frontends and with commit --amend retries.
    """
    return any(entry.commit == commit for entry in read_entries(path))
