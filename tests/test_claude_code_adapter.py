"""Tests for the Claude Code PostToolUse adapter.

Two levels, and the distinction between them is stated plainly rather than blurred.

The unit and integration tests below drive the adapter with hook payloads constructed
to match the documented PostToolUse contract, then confirm the capture layer reads the
resulting signal and records tool_signal attribution on a real commit. These are
faithful to the documented input shape, and they exercise the real adapter, the real
signal file, the real git hooks, and the real ledger.

They are not a live Claude Code invocation, and that limit is real rather than
rhetorical. A live invocation was attempted during development: a payload-logging hook
was registered in a project .claude/settings.json and file edits were then performed
through the assistant's own Write tool. The hook did not fire, because hook
configuration is read when a session starts and the session predated the file, and a
test runner cannot restart an interactive assistant session to pick it up.

What that leaves verified and unverified, stated precisely:

    verified      the adapter's behavior given a payload of the documented shape, the
                  signal file it writes, the capture layer reading that signal, and the
                  tool_signal attribution appearing on a real commit. The subprocess
                  tests invoke the adapter exactly as a hook would, over stdin, and
                  assert on its exit code.
    verified      the field vocabulary, checked against the Claude Code hook
                  documentation: tool_name, tool_input, tool_input.file_path,
                  tool_input.new_string, cwd, and CLAUDE_PROJECT_DIR.
    not verified  that a live session populates those fields as documented. The payloads
                  below are constructed to the documented contract, not captured from a
                  running assistant.

Closing that last gap needs a manual check: register the hook, restart the session, edit
a file, and confirm .vouchcode/signals/claude-code.json gains a range.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from support import run_git, run_vouchcode, write_file

from vouchcode.adapters.claude_code_hook import (
    SIGNAL_FILE_NAME,
    TOOL_NAME,
    record_event,
)

GENERATED = """def normalize_records(records, strict):
    if records is None:
        raise ValueError("records required")
    result = {}
    for key, value in records.items():
        if value is None and strict:
            raise ValueError(key)
        result[key] = value
    return result
"""


def _signal_document(root: Path) -> dict:
    path = root / ".vouchcode" / "signals" / SIGNAL_FILE_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def _write_payload(root: Path, relative: str) -> dict:
    """Build a PostToolUse payload for the Write tool, per the documented shape."""
    return {
        "session_id": "test-session",
        "transcript_path": str(root / "transcript.jsonl"),
        "cwd": str(root),
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(root / relative), "content": GENERATED},
    }


def _edit_payload(root: Path, relative: str, old: str, new: str) -> dict:
    """Build a PostToolUse payload for the Edit tool, per the documented shape."""
    return {
        "session_id": "test-session",
        "cwd": str(root),
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(root / relative),
            "old_string": old,
            "new_string": new,
        },
    }


# ---------------------------------------------------------------------------
# The adapter writes the existing signal format
# ---------------------------------------------------------------------------


def test_write_tool_records_the_whole_file(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A Write replaced the whole file, so the whole file is recorded as generated."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "generated.py", GENERATED)

    recorded = record_event(_write_payload(temp_repo, "generated.py"), environ={})

    assert recorded is True
    document = _signal_document(temp_repo)
    assert document["tool"] == TOOL_NAME

    entry = document["ranges"][0]
    assert entry["path"] == "generated.py"
    assert entry["start_line"] == 1
    assert entry["end_line"] == len(GENERATED.splitlines())
    assert entry["generated"] is True
    assert entry["detection"] == "whole_file"


def test_edit_tool_records_the_exact_inserted_range(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """An Edit records only the lines it inserted, located by the inserted text.

    Recording the whole file for a targeted edit would attribute the developer's own
    surrounding code to the assistant.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    original = "def kept():\n    return 1\n\n\ndef also_kept():\n    return 2\n"
    inserted = "def added():\n    return 3\n"
    write_file(temp_repo, "mixed.py", original + "\n\n" + inserted)

    recorded = record_event(
        _edit_payload(temp_repo, "mixed.py", "", inserted), environ={}
    )

    assert recorded is True
    entry = _signal_document(temp_repo)["ranges"][0]

    # The original occupies lines 1 to 6, then two blank separator lines, so the
    # inserted two-line function occupies lines 9 and 10.
    assert entry["detection"] == "exact"
    assert entry["start_line"] == 9
    assert entry["end_line"] == 10

    # The developer's own functions are outside the recorded range.
    assert entry["start_line"] > 6


def test_edit_falls_back_to_whole_file_when_text_is_not_found(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """An unlocatable insertion is recorded as the whole file, and marked as such.

    Over-attributing sends more code to comprehension verification, which is the safe
    direction. The marker is what stops a reader from mistaking it for a precise range.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "reformatted.py", GENERATED)

    recorded = record_event(
        _edit_payload(temp_repo, "reformatted.py", "", "text that is not in the file"),
        environ={},
    )

    assert recorded is True
    entry = _signal_document(temp_repo)["ranges"][0]
    assert entry["detection"] == "whole_file_insert_not_found"
    assert entry["start_line"] == 1


def test_unhandled_tools_are_ignored(temp_repo: Path, git_env: dict[str, str]) -> None:
    """A Bash or Read event is not a file authorship signal."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    for tool in ("Bash", "Read", "Grep", "NotebookEdit"):
        payload = {"tool_name": tool, "cwd": str(temp_repo), "tool_input": {}}
        assert record_event(payload, environ={}) is False

    assert not (temp_repo / ".vouchcode" / "signals").exists()


def test_edits_outside_the_repository_are_not_recorded(
    temp_repo: Path, git_env: dict[str, str], tmp_path: Path
) -> None:
    """A file edited elsewhere is not this repository's provenance to claim."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    outside = tmp_path / "outside.py"
    outside.write_text(GENERATED, encoding="utf-8")

    payload = _write_payload(temp_repo, "unused.py")
    payload["tool_input"]["file_path"] = str(outside)

    assert record_event(payload, environ={}) is False


def test_repeated_edits_merge_rather_than_accumulate(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A long session editing one file must not grow the signal file without bound."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "generated.py", GENERATED)

    for _ in range(5):
        record_event(_write_payload(temp_repo, "generated.py"), environ={})

    ranges = _signal_document(temp_repo)["ranges"]
    assert len(ranges) == 1, f"expected merged ranges, got {len(ranges)}"


def test_project_dir_environment_variable_locates_the_repository(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """CLAUDE_PROJECT_DIR is used when the payload's cwd is unhelpful."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "generated.py", GENERATED)

    payload = _write_payload(temp_repo, "generated.py")
    payload.pop("cwd")

    assert record_event(payload, environ={"CLAUDE_PROJECT_DIR": str(temp_repo)}) is True


# ---------------------------------------------------------------------------
# The hook never breaks the editing session
# ---------------------------------------------------------------------------


def test_hook_exits_zero_on_malformed_input() -> None:
    """PostToolUse runs after the tool succeeded, so the hook must never report failure.

    Driven as a real subprocess through the installed entry point, because the exit code
    is the contract with Claude Code and calling main directly would not exercise it.
    """
    for payload in ("", "{not json", "[]", '{"tool_name": "Write"}'):
        result = subprocess.run(
            [sys.executable, "-m", "vouchcode.adapters.claude_code_hook"],
            input=payload,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode} on input {payload!r}: {result.stderr}"
        )


def test_hook_reports_diagnostics_on_stderr_not_stdout() -> None:
    """Diagnostics must not contaminate stdout, which Claude Code parses as JSON."""
    result = subprocess.run(
        [sys.executable, "-m", "vouchcode.adapters.claude_code_hook"],
        input="{not json",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert "vouchcode-claude-hook:" in result.stderr


# ---------------------------------------------------------------------------
# End to end: hook writes a signal, capture layer reads it, ledger records it
# ---------------------------------------------------------------------------


def test_hook_signal_produces_tool_signal_attribution_in_the_ledger(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The full path, from a hook invocation to the recorded ledger entry.

    The adapter is driven as a real subprocess reading a payload from stdin, exactly as
    Claude Code would invoke it, then a real commit is made and the resulting ledger
    entry is asserted on. Nothing is stubbed between the hook and the ledger.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "generated.py", GENERATED)

    payload = _write_payload(temp_repo, "generated.py")
    hook = subprocess.run(
        [sys.executable, "-m", "vouchcode.adapters.claude_code_hook"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(temp_repo),
        env=git_env,
    )
    assert hook.returncode == 0, hook.stderr

    run_git(["add", "generated.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "Add normalize_records"], cwd=temp_repo, env=git_env)

    ledger = json.loads(
        (temp_repo / ".vouchcode" / "ledger.json").read_text(encoding="utf-8")
    )
    entry = ledger["entries"][0]

    hunk = next(h for h in entry["hunks"] if h["qualname"] == "normalize_records")

    assert hunk["attribution"]["source"] == "tool_signal"
    assert hunk["attribution"]["confidence"] == 1.0
    assert hunk["attribution"]["status"] == "ai"
    assert hunk["attribution"]["detail"]["tools"] == [TOOL_NAME]

    assert entry["attribution"]["source"] == "tool_signal"
    assert entry["attribution"]["status"] == "ai"


def test_without_the_hook_the_same_commit_falls_back_to_stylometry(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The control case, proving the signal is what changed the attribution.

    Committing identical code with no signal present must not produce tool_signal
    attribution. Without this, the test above would pass even if the capture layer
    attributed everything to a tool signal regardless of evidence.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "generated.py", GENERATED)

    run_git(["add", "generated.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "Add normalize_records"], cwd=temp_repo, env=git_env)

    ledger = json.loads(
        (temp_repo / ".vouchcode" / "ledger.json").read_text(encoding="utf-8")
    )
    hunk = next(
        h for h in ledger["entries"][0]["hunks"] if h["qualname"] == "normalize_records"
    )

    assert hunk["attribution"]["source"] != "tool_signal"
    assert hunk["attribution"]["source"] == "stylometry"
