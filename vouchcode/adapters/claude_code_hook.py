"""Claude Code PostToolUse adapter.

Registered as a PostToolUse hook in a target repository's own .claude/settings.json,
this script records which files the assistant wrote or edited into Vouchcode's signal
file, so that the attribution pass can classify those hunks from direct evidence rather
than falling back to stylometric inference.

Registration, in the target repository (not in Vouchcode's own repository):

    {
      "hooks": {
        "PostToolUse": [
          {
            "matcher": "Edit|Write",
            "hooks": [
              {"type": "command", "command": "vouchcode-claude-hook", "timeout": 10}
            ]
          }
        ]
      }
    }

Input contract, verified against the Claude Code hook documentation rather than assumed.
The hook receives one JSON object on stdin carrying, among other fields, "tool_name" and
"tool_input", and for the file-editing tools "tool_input" carries "file_path". The
documented file-editing tools are Edit, Write, and NotebookEdit. CLAUDE_PROJECT_DIR is
available in the environment, and "cwd" is present in the payload as a fallback.

Line ranges. The payload does not state which lines were generated, but it carries
enough to derive them, which is why this adapter needs no change to the signal format:

    Write          the tool created or replaced the whole file, so the range is the
                   whole file.
    Edit           tool_input carries "new_string", the text just inserted. Locating it
                   in the post-edit file gives the exact range.
    fallback       when the inserted text cannot be located, for example because the
                   editor normalized whitespace, the whole file is recorded and the
                   range is marked as imprecise. Over-attributing to the assistant sends
                   more code to comprehension verification, which is the safe direction;
                   under-attributing would let generated code pass unexamined.

NotebookEdit is not handled. Its payload identifies a cell rather than a line range, and
notebooks are outside the Python source scope of the segmentation layer.

Failure policy. This hook never fails a Claude Code session. Every error path exits zero
with a diagnostic on stderr. A provenance tool that interrupted the developer's editor
because it could not write a JSON file would be swiftly and correctly uninstalled.

Privacy. Only the repository-relative path, the line range, a timestamp, and the tool
name are written. Prompts, transcripts, and file contents are never recorded.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vouchcode.capture.signals import SIGNALS_DIR_NAME
from vouchcode.config import VOUCHCODE_DIR_NAME

# Identity written into the signal file, and the name a report will show as the source
# of the attribution.
TOOL_NAME = "claude-code"

# Signal file this adapter owns. One file per tool keeps adapters from overwriting each
# other, since the reader merges every JSON file in the directory.
SIGNAL_FILE_NAME = "claude-code.json"

# Tools whose payload carries a file_path this adapter can act on.
HANDLED_TOOLS = ("Write", "Edit")

_PREFIX = "vouchcode-claude-hook:"


def main(argv: list[str] | None = None) -> int:
    """Read one hook payload from stdin and record a signal. Always returns zero."""
    del argv  # The hook takes no arguments; its input arrives on stdin.

    try:
        raw = sys.stdin.read()
    except OSError as exc:
        return _abort(f"could not read hook input: {exc}")

    if not raw.strip():
        return _abort("empty hook input")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _abort(f"hook input is not valid JSON: {exc.msg}")

    if not isinstance(payload, dict):
        return _abort("hook input is not a JSON object")

    try:
        record_event(payload)
    except Exception as exc:
        # Deliberately broad. Nothing this adapter can fail at is worth interrupting a
        # developer's editing session for.
        return _abort(f"{exc.__class__.__name__}: {exc}")

    return 0


def record_event(
    payload: dict[str, Any],
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Record one PostToolUse event as a signal range.

    Returns whether a signal was written. Separated from main so that tests can drive it
    with a payload directly, without a subprocess and a pipe.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ

    tool_name = str(payload.get("tool_name") or "")
    if tool_name not in HANDLED_TOOLS:
        return False

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False

    raw_path = tool_input.get("file_path")
    if not isinstance(raw_path, str) or not raw_path:
        return False

    repo_root = _resolve_repo_root(payload, env)
    if repo_root is None:
        raise RuntimeError(
            "could not locate a vouchcode-initialized repository for this edit"
        )

    edited = Path(raw_path)
    if not edited.is_absolute():
        edited = (repo_root / edited).resolve()

    try:
        relative = edited.resolve().relative_to(repo_root)
    except ValueError:
        # The assistant edited something outside the repository. Not this repository's
        # provenance to record.
        return False

    start, end, detection = _line_range(edited, tool_name, tool_input)
    if start is None or end is None:
        return False

    _append_range(
        repo_root,
        {
            "path": str(relative).replace("\\", "/"),
            "start_line": start,
            "end_line": end,
            "generated": True,
            # Ignored by the reader, which only consumes path, line bounds, and the
            # generated flag. Recorded so a person inspecting the file can tell an
            # exact range from a whole-file fallback.
            "detection": detection,
            "recorded_at": _timestamp(),
        },
    )
    return True


def _line_range(
    path: Path,
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[int | None, int | None, str]:
    """Derive the line range the tool just wrote.

    Returns the inclusive bounds and how they were determined.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, "unreadable"

    total_lines = max(1, len(content.splitlines()))

    if tool_name == "Write":
        # Write replaces the whole file, so the whole file is what it generated.
        return 1, total_lines, "whole_file"

    inserted = tool_input.get("new_string")
    if not isinstance(inserted, str) or not inserted:
        return 1, total_lines, "whole_file_no_insert_text"

    offset = content.find(inserted)
    if offset < 0:
        # The inserted text is not present verbatim, most likely because a formatter ran
        # after the edit. Fall back to the whole file and mark it, rather than silently
        # recording a range that is probably wrong.
        return 1, total_lines, "whole_file_insert_not_found"

    start = content.count("\n", 0, offset) + 1
    end = start + max(0, len(inserted.splitlines()) - 1)
    return start, min(end, total_lines), "exact"


def _resolve_repo_root(
    payload: dict[str, Any],
    environ: Mapping[str, str],
) -> Path | None:
    """Find the Vouchcode-initialized repository this edit belongs to.

    Tries the documented project directory variable first, then the cwd the hook payload
    reports, then the file's own location. Each candidate is walked upward looking for a
    .vouchcode directory, because the edited file may sit deep inside the tree.
    """
    candidates: list[Path] = []

    project_dir = environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        candidates.append(Path(project_dir))

    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        candidates.append(Path(cwd))

    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        raw_path = tool_input.get("file_path")
        if isinstance(raw_path, str) and raw_path:
            candidates.append(Path(raw_path).parent)

    for candidate in candidates:
        root = _walk_up_for_vouchcode(candidate)
        if root is not None:
            return root

    return None


def _walk_up_for_vouchcode(start: Path) -> Path | None:
    """Return the nearest ancestor of start containing a .vouchcode directory."""
    try:
        current = start.resolve()
    except OSError:
        return None

    for directory in [current, *current.parents]:
        if (directory / VOUCHCODE_DIR_NAME).is_dir():
            return directory
    return None


def _append_range(repo_root: Path, new_range: dict[str, Any]) -> None:
    """Add one range to this adapter's signal file, merging overlapping entries.

    Read, modify, write, in that order, onto a file this adapter alone owns. Merging
    keeps the file bounded across a long session: an assistant editing one function
    repeatedly would otherwise accumulate hundreds of near-identical ranges.
    """
    directory = repo_root / VOUCHCODE_DIR_NAME / SIGNALS_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / SIGNAL_FILE_NAME

    document = _read_document(target)
    ranges = [r for r in document.get("ranges", []) if isinstance(r, dict)]
    ranges.append(new_range)

    document["tool"] = TOOL_NAME
    document["recorded_at"] = _timestamp()
    document["ranges"] = _merge_ranges(ranges)

    _write_atomic(target, document)


def _read_document(target: Path) -> dict[str, Any]:
    """Load the existing signal document, starting fresh if it is absent or unusable.

    A corrupt signal file is replaced rather than raising. It is a cache of
    observations, not a ledger, and losing it degrades attribution to stylometry rather
    than losing provenance outright.
    """
    if not target.is_file():
        return {"tool": TOOL_NAME, "ranges": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tool": TOOL_NAME, "ranges": []}
    if not isinstance(data, dict):
        return {"tool": TOOL_NAME, "ranges": []}
    return data


def _merge_ranges(ranges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse overlapping and adjacent ranges for the same path and generated flag."""
    buckets: dict[tuple[str, bool], list[dict[str, Any]]] = {}

    for item in ranges:
        path = item.get("path")
        if not isinstance(path, str):
            continue
        try:
            int(item["start_line"])
            int(item["end_line"])
        except (KeyError, TypeError, ValueError):
            continue
        buckets.setdefault((path, bool(item.get("generated", True))), []).append(item)

    merged: list[dict[str, Any]] = []

    for _key, items in sorted(buckets.items()):
        items.sort(key=lambda r: (int(r["start_line"]), int(r["end_line"])))
        current = dict(items[0])

        for item in items[1:]:
            start = int(item["start_line"])
            end = int(item["end_line"])
            # Adjacent counts as overlapping: two edits on consecutive lines describe
            # one region, and splitting them would only inflate the file.
            if start <= int(current["end_line"]) + 1:
                current["end_line"] = max(int(current["end_line"]), end)
                if current.get("detection") != item.get("detection"):
                    current["detection"] = "merged"
                current["recorded_at"] = item.get(
                    "recorded_at", current.get("recorded_at")
                )
            else:
                merged.append(current)
                current = dict(item)

        merged.append(current)

    return merged


def _write_atomic(target: Path, document: dict[str, Any]) -> None:
    """Write the signal file through a temporary file and a rename.

    A half-written signal file read by a concurrent commit hook would be discarded as
    malformed, silently downgrading attribution for that commit.
    """
    serialized = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    temp_path = target.with_name(target.name + ".tmp")

    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temp_path, target)


def _timestamp() -> str:
    """Current time as an ISO 8601 string in UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _abort(message: str) -> int:
    """Report a diagnostic and exit successfully.

    PostToolUse runs after the tool has already executed and cannot undo it, so a
    non-zero exit would surface an error about work that already succeeded. The
    diagnostic goes to stderr, where the assistant records it.
    """
    print(f"{_PREFIX} {message}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
