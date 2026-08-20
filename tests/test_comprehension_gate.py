"""Tests for the comprehension gate: what Vouchcode declines to evaluate, and why.

The ledger must be honest about its own coverage. A merge commit is excluded from
comprehension scoring by decision, and that decision has to be visible in the entry
as an explicit status with a rationale. A null field would read as an oversight,
which is exactly the impression a provenance report must not create about its gaps.

The central test here is that a merge never reaches question generation. It asserts on
the code path, not only on the recorded status, because an entry could carry the right
status while the engine still ran and threw its work away.
"""

from __future__ import annotations

import json
from pathlib import Path

from support import run_git, run_vouchcode, write_file

from vouchcode.comprehension.eligibility import (
    COMPREHENSION_EXCLUDED_MERGE,
    COMPREHENSION_NOT_EVALUATED,
    COMPREHENSION_NOT_REQUIRED,
    evaluate_gate,
    is_merge_excluded,
    questionable_hunks,
)
from vouchcode.ledger.entry import ENTRY_TYPE_COMMIT, ENTRY_TYPE_MERGE
from vouchcode.segmentation.hunks import Hunk

SAMPLE = "def handler(payload):\n    return payload.strip()\n"


def _hunk(status: str, change: str = "added") -> Hunk:
    return Hunk(
        path="m.py",
        qualname="handler",
        kind="function",
        change=change,
        lineno=1,
        end_lineno=2,
        attribution={"status": status, "source": "tool_signal", "confidence": 1.0},
        source=SAMPLE,
    )


# ---------------------------------------------------------------------------
# Merge exclusion
# ---------------------------------------------------------------------------


def test_merge_is_excluded_from_comprehension() -> None:
    """A merge entry type is excluded outright, regardless of what it contains."""
    assert is_merge_excluded(ENTRY_TYPE_MERGE) is True
    assert is_merge_excluded(ENTRY_TYPE_COMMIT) is False


def test_merge_never_reaches_question_generation() -> None:
    """The gate returns no hunks for a merge, even when eligible hunks are present.

    Asserting on the returned hunk list rather than only on the status is the point. An
    entry could carry the correct exclusion status while the engine still generated
    questions and discarded them, which would be wasted work and, worse, would mean the
    exclusion was cosmetic rather than real.
    """
    ai_hunks = [_hunk("ai"), _hunk("mixed")]

    eligible, record = evaluate_gate(ENTRY_TYPE_MERGE, ai_hunks)

    assert eligible == [], "a merge must yield no hunks to question"
    assert record["status"] == COMPREHENSION_EXCLUDED_MERGE

    # The same hunks are eligible on a non-merge, proving the exclusion is what stopped
    # them rather than the hunks being ineligible on their own.
    eligible_on_commit, _ = evaluate_gate(ENTRY_TYPE_COMMIT, ai_hunks)
    assert len(eligible_on_commit) == 2


def test_merge_exclusion_carries_a_rationale_not_a_null() -> None:
    """The exclusion is recorded as a decision, with a stated reason."""
    _eligible, record = evaluate_gate(ENTRY_TYPE_MERGE, [])

    assert record["status"] == COMPREHENSION_EXCLUDED_MERGE
    assert isinstance(record["rationale"], str)
    assert record["rationale"].strip(), "rationale must not be empty"
    assert "merge" in record["rationale"].lower()

    # Explicitly not a null or absent field.
    assert record.get("status") is not None
    assert set(record) == {"status", "rationale"}


# ---------------------------------------------------------------------------
# Eligibility of individual hunks
# ---------------------------------------------------------------------------


def test_only_ai_attributed_hunks_are_questioned() -> None:
    """Human-attributed and structurally unchanged hunks are never questioned."""
    hunks = [
        _hunk("ai"),
        _hunk("mixed"),
        _hunk("human"),
        _hunk("unclassified"),
        _hunk("unchanged", change="renamed"),
    ]

    eligible = questionable_hunks(hunks)

    assert {h.attribution["status"] for h in eligible} == {"ai", "mixed"}


def test_renamed_hunk_is_never_questioned_even_if_attributed_ai() -> None:
    """A rename carries no new logic, so it is not questioned whatever its attribution.

    This is the Phase 2 guarantee holding at the Phase 3 boundary: code the AST proved
    unchanged must not be sent back to the developer to re-explain.
    """
    renamed = _hunk("ai", change="renamed")

    assert renamed.carries_new_logic is False
    assert questionable_hunks([renamed]) == []


def test_commit_with_no_ai_hunks_records_not_required() -> None:
    """A commit with nothing to verify says so, rather than looking unevaluated."""
    _eligible, record = evaluate_gate(ENTRY_TYPE_COMMIT, [_hunk("human")])

    assert record["status"] == COMPREHENSION_NOT_REQUIRED
    assert "attributed to AI" in record["rationale"]


def test_commit_with_ai_hunks_defaults_to_not_evaluated() -> None:
    """Until the engine runs, an eligible commit says plainly that nothing ran."""
    eligible, record = evaluate_gate(ENTRY_TYPE_COMMIT, [_hunk("ai")])

    assert len(eligible) == 1
    assert record["status"] == COMPREHENSION_NOT_EVALUATED


# ---------------------------------------------------------------------------
# End to end through a real merge commit
# ---------------------------------------------------------------------------


def _ledger(root: Path) -> dict:
    return json.loads((root / ".vouchcode" / "ledger.json").read_text(encoding="utf-8"))


def _commit(root: Path, env: dict[str, str], path: str, body: str, message: str) -> str:
    write_file(root, path, body)
    run_git(["add", path], cwd=root, env=env)
    run_git(["commit", "-m", message], cwd=root, env=env)
    return run_git(["rev-parse", "HEAD"], cwd=root, env=env).stdout.strip()


def test_real_merge_commit_records_explicit_exclusion(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A merge produced by git carries the exclusion status in its ledger entry."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "base.py", "BASE = 1\n", "Add base")

    run_git(["checkout", "-b", "side"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "side.py", "SIDE = 1\n", "Add side")

    run_git(["checkout", "main"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "main.py", "MAIN = 1\n", "Add main")
    run_git(
        ["merge", "--no-ff", "side", "-m", "Merge side"], cwd=temp_repo, env=git_env
    )
    merge_sha = run_git(
        ["rev-parse", "HEAD"], cwd=temp_repo, env=git_env
    ).stdout.strip()

    entries = {e["commit"]: e for e in _ledger(temp_repo)["entries"]}
    merge_entry = entries[merge_sha]

    assert merge_entry["type"] == "merge"
    assert merge_entry["comprehension"]["status"] == COMPREHENSION_EXCLUDED_MERGE
    assert merge_entry["comprehension"]["rationale"]


def test_every_entry_carries_a_comprehension_status(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """No entry may leave comprehension empty.

    An empty field is indistinguishable from a bug. Every entry states its
    comprehension status, including the ones that were never evaluated.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "a.py", "A = 1\n", "Add a")
    _commit(temp_repo, git_env, "b.py", "def f(x):\n    return x\n", "Add f")

    run_git(["checkout", "-b", "side"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "c.py", "C = 1\n", "Add c")
    run_git(["checkout", "main"], cwd=temp_repo, env=git_env)
    run_git(
        ["merge", "--no-ff", "side", "-m", "Merge side"], cwd=temp_repo, env=git_env
    )

    for entry in _ledger(temp_repo)["entries"]:
        comprehension = entry["comprehension"]
        assert comprehension, f"entry {entry['commit'][:8]} has an empty comprehension"
        assert comprehension["status"]
        assert comprehension["rationale"]
