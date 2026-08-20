"""Phase 1 exit criterion tests.

The Phase 1 exit criterion is narrow and specific: the capture hooks fire reliably and
the ledger write succeeds on every commit. It is deliberately not an attribution claim.

The central test is test_commit_produces_ledger_entry, which drives a real git commit in
a real repository and asserts on the contents of the resulting ledger file. Asserting
that the command exited zero would not prove the criterion, because a hook that fails
silently also lets git exit zero.
"""

from __future__ import annotations

import json
from pathlib import Path

from support import run_git, run_vouchcode, write_file

from vouchcode.config import HOOK_MARKER, MANAGED_HOOKS


def _ledger(root: Path) -> dict:
    """Read the ledger document from a repository under test."""
    return json.loads((root / ".vouchcode" / "ledger.json").read_text(encoding="utf-8"))


def _commit(
    root: Path, env: dict[str, str], relative: str, body: str, message: str
) -> str:
    """Stage and commit one file, returning the resulting commit hash."""
    write_file(root, relative, body)
    run_git(["add", relative], cwd=root, env=env)
    run_git(["commit", "-m", message], cwd=root, env=env)
    return run_git(["rev-parse", "HEAD"], cwd=root, env=env).stdout.strip()


def test_init_creates_ledger_and_hooks(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """init installs every managed hook and creates an empty ledger."""
    result = run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    assert result.returncode == 0, result.stderr

    for name in MANAGED_HOOKS:
        hook_path = temp_repo / ".git" / "hooks" / name
        assert hook_path.is_file(), f"hook not installed: {name}"
        assert HOOK_MARKER in hook_path.read_text(encoding="utf-8")

    assert _ledger(temp_repo) == {"schema_version": 1, "entries": []}


def test_hook_scripts_use_lf_line_endings(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """Hook scripts must not contain CR characters.

    A CRLF shebang line makes sh report an interpreter-not-found failure at commit time,
    which on Windows is the most likely way for capture to break in the field.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    for name in MANAGED_HOOKS:
        raw = (temp_repo / ".git" / "hooks" / name).read_bytes()
        assert b"\r" not in raw, f"hook {name} contains a carriage return"


def test_commit_produces_ledger_entry(temp_repo: Path, git_env: dict[str, str]) -> None:
    """Phase 1 exit criterion: a git commit produces a matching ledger entry.

    Asserts on the recorded commit hash, the changed file list, and the placeholder
    attribution, so that a hook which runs but records the wrong thing fails the test.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    sha = _commit(temp_repo, git_env, "module.py", "def f():\n    return 1\n", "Add f")

    entries = _ledger(temp_repo)["entries"]
    assert len(entries) == 1, f"expected exactly one ledger entry, got {len(entries)}"

    entry = entries[0]
    assert entry["commit"] == sha
    assert entry["files"] == ["module.py"]
    assert entry["branch"] == "main"
    assert entry["author"]["email"] == "test@example.invalid"
    assert entry["timestamp"].startswith("20")

    # Phase 1 records no attribution. A non-placeholder value here means attribution
    # logic leaked into the foundation phase.
    assert entry["attribution"] == {
        "status": "unclassified",
        "source": None,
        "confidence": None,
    }


def test_ledger_appends_across_commits(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """Every commit appends exactly one entry, in commit order, without rewriting."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    first = _commit(temp_repo, git_env, "a.py", "A = 1\n", "Add a")
    second = _commit(temp_repo, git_env, "b.py", "B = 2\n", "Add b")
    third = _commit(temp_repo, git_env, "a.py", "A = 3\n", "Update a")

    entries = _ledger(temp_repo)["entries"]
    assert [e["commit"] for e in entries] == [first, second, third]
    assert [e["files"] for e in entries] == [["a.py"], ["b.py"], ["a.py"]]


def test_commit_without_hooks_is_still_recorded_on_next_capture(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A commit made with --no-verify still yields an entry from the post-commit path.

    git skips pre-commit under --no-verify but still runs post-commit, so the fallback
    that reads the change set out of the finished commit is what must produce the entry.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    write_file(temp_repo, "skipped.py", "X = 1\n")
    run_git(["add", "skipped.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "--no-verify", "-m", "Add skipped"], cwd=temp_repo, env=git_env)

    entries = _ledger(temp_repo)["entries"]
    assert len(entries) == 1
    assert entries[0]["files"] == ["skipped.py"]


def test_init_refuses_to_overwrite_foreign_hook(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A pre-existing hook Vouchcode does not own is preserved, and init fails."""
    hooks_dir = temp_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    foreign = hooks_dir / "pre-commit"
    foreign.write_text("#!/bin/sh\necho existing project hook\n", encoding="utf-8")

    result = run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
    assert "existing project hook" in foreign.read_text(encoding="utf-8")


def test_init_force_replaces_foreign_hook(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """--force is the documented escape hatch for replacing a foreign hook."""
    hooks_dir = temp_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    result = run_vouchcode(["init", "--force"], cwd=temp_repo, env=git_env)

    assert result.returncode == 0, result.stderr
    assert HOOK_MARKER in (hooks_dir / "pre-commit").read_text(encoding="utf-8")


def test_init_is_idempotent_and_preserves_history(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """Rerunning init must not discard an existing ledger."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "a.py", "A = 1\n", "Add a")

    result = run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    assert result.returncode == 0, result.stderr

    assert len(_ledger(temp_repo)["entries"]) == 1


def test_uninstall_removes_hooks_and_keeps_ledger(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """uninstall removes the managed hooks but leaves recorded provenance intact."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "a.py", "A = 1\n", "Add a")

    result = run_vouchcode(["uninstall"], cwd=temp_repo, env=git_env)
    assert result.returncode == 0, result.stderr

    for name in MANAGED_HOOKS:
        assert not (temp_repo / ".git" / "hooks" / name).exists()

    assert len(_ledger(temp_repo)["entries"]) == 1


def test_status_reports_missing_hooks(temp_repo: Path, git_env: dict[str, str]) -> None:
    """status exits non-zero when a managed hook is absent, so it is usable in CI."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    (temp_repo / ".git" / "hooks" / "post-commit").unlink()

    result = run_vouchcode(["status"], cwd=temp_repo, env=git_env)

    assert result.returncode != 0
    assert "hook post-commit: missing" in result.stdout


def test_log_json_matches_ledger(temp_repo: Path, git_env: dict[str, str]) -> None:
    """log --json emits the recorded entries verbatim, so it can be piped."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    sha = _commit(temp_repo, git_env, "a.py", "A = 1\n", "Add a")

    result = run_vouchcode(["log", "--json"], cwd=temp_repo, env=git_env)
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout)
    assert [item["commit"] for item in payload] == [sha]


def test_commands_outside_a_repository_fail_cleanly(
    tmp_path: Path, git_env: dict[str, str]
) -> None:
    """Running outside a git repository is an actionable error, not a traceback."""
    result = run_vouchcode(["init"], cwd=tmp_path, env=git_env)

    assert result.returncode != 0
    assert "not a git repository" in result.stderr
    assert "Traceback" not in result.stderr
