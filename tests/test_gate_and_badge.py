"""Tests for the CI gate and the locally generated badge.

The gate's exit criterion follows the same before-and-after pattern as every phase: the
same underlying data, changed in one respect, must flip the outcome. A ledger whose
AI-attributed hunk carries a passing comprehension record exits zero; the identical
ledger without that record exits non-zero. Asserting only that a broken ledger fails
would not prove the gate is reading what it claims to read.

The badge tests are mostly about what it refuses to say. A badge that rounded an
unevaluated repository up to "verified" would be the most misleading artifact this
project could emit, precisely because it is the one people paste into a README without
reading the report behind it.
"""

from __future__ import annotations

import json
from pathlib import Path

from git import Repo
from support import run_git, run_vouchcode, write_file

from vouchcode.badge import (
    COLOR_AMBER,
    COLOR_GREEN,
    COLOR_GREY,
    badge_content,
    render_badge,
)
from vouchcode.gate import MIN_CONFIDENCE, run_gate
from vouchcode.ledger.canonical import canonical_bytes
from vouchcode.ledger.chain import link
from vouchcode.ledger.signing import load_private_key, sign_payload

SIGNALLED = """def normalize(records, strict):
    if records is None:
        raise ValueError("records required")
    out = {}
    for key, value in records.items():
        if value is None and strict:
            return None
        out[key] = value
    return out
"""


def _vouchcode_dir(root: Path) -> Path:
    return root / ".vouchcode"


def _ledger_path(root: Path) -> Path:
    return _vouchcode_dir(root) / "ledger.json"


def _build_ai_commit(root: Path, env: dict[str, str]) -> None:
    """Create a branch whose commit contains a hunk attributed via a tool signal.

    A signal file is written first, so the hunk lands at confidence 1.0 rather than
    falling to stylometry, which sits below the gate's threshold by design.
    """
    run_vouchcode(["init"], cwd=root, env=env)
    write_file(root, "base.py", "BASE = 1\n")
    run_git(["add", "base.py"], cwd=root, env=env)
    run_git(["commit", "-m", "base"], cwd=root, env=env)

    run_git(["checkout", "-b", "feature"], cwd=root, env=env)

    signals = _vouchcode_dir(root) / "signals"
    signals.mkdir(parents=True, exist_ok=True)
    (signals / "claude-code.json").write_text(
        json.dumps(
            {
                "tool": "claude-code",
                "ranges": [
                    {
                        "path": "generated.py",
                        "start_line": 1,
                        "end_line": 20,
                        "generated": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    write_file(root, "generated.py", SIGNALLED)
    run_git(["add", "generated.py"], cwd=root, env=env)
    run_git(["commit", "-m", "Add normalize"], cwd=root, env=env)


def _set_comprehension(root: Path, status: str) -> None:
    """Set the newest entry's comprehension status and re-sign the chain.

    Re-signing matters. Editing the field in place would leave a tampered ledger, and
    the gate would then read data that verification rejects, which is not the situation
    under test.
    """
    path = _ledger_path(root)
    document = json.loads(path.read_text(encoding="utf-8"))
    key = load_private_key(_vouchcode_dir(root))

    document["entries"][-1]["comprehension"] = {
        "status": status,
        "rationale": f"set to {status} for test",
    }

    previous = None
    for entry in document["entries"]:
        entry.pop("entry_hash", None)
        entry.pop("signature", None)
        link(entry, previous)
        entry["signature"] = sign_payload(canonical_bytes(entry), key)
        previous = entry

    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Gate exit criterion
# ---------------------------------------------------------------------------


def test_missing_comprehension_on_an_ai_hunk_fails_the_gate(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """An AI-attributed hunk nobody accounted for stops the build."""
    _build_ai_commit(temp_repo, git_env)

    result = run_gate(_ledger_path(temp_repo), Repo(temp_repo), base_ref="main")

    assert result.hunks, "expected the signalled hunk to be gated"
    assert result.passed is False
    assert len(result.failures) == 1
    assert result.failures[0].qualname == "normalize"
    assert result.failures[0].source == "tool_signal"
    assert result.failures[0].confidence == 1.0


def test_passing_comprehension_on_the_same_hunk_passes_the_gate(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The same ledger, with the record present, allows the build through.

    This is the other half of the exit criterion. One field changes and the outcome
    flips, which is what proves the gate reads the comprehension record rather than
    merely counting hunks.
    """
    _build_ai_commit(temp_repo, git_env)
    _set_comprehension(temp_repo, "passed")

    result = run_gate(_ledger_path(temp_repo), Repo(temp_repo), base_ref="main")

    assert len(result.hunks) == 1
    assert result.passed is True
    assert result.failures == []


def test_skipped_comprehension_is_not_treated_as_passing(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """An honest skip is not a pass.

    A commit made with no terminal attached records skipped_non_interactive. That is
    the truthful status, and it is still not evidence anyone read the code, so the gate
    must not accept it.
    """
    _build_ai_commit(temp_repo, git_env)
    _set_comprehension(temp_repo, "skipped_non_interactive")

    result = run_gate(_ledger_path(temp_repo), Repo(temp_repo), base_ref="main")

    assert result.passed is False


def test_stylometry_attribution_does_not_fail_the_build_by_default(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """Inference must not block a merge at the default threshold.

    Stylometric confidence is capped below 0.75 by construction, and the gate's default
    threshold of 0.9 therefore excludes it. Blocking on a heuristic's hunch is the
    overreach this project argues against everywhere else.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "base.py", "BASE = 1\n")
    run_git(["add", "base.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "base"], cwd=temp_repo, env=git_env)

    run_git(["checkout", "-b", "feature"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "plain.py", SIGNALLED)
    run_git(["add", "plain.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "Add plain"], cwd=temp_repo, env=git_env)

    result = run_gate(_ledger_path(temp_repo), Repo(temp_repo), base_ref="main")

    assert all(hunk.confidence >= MIN_CONFIDENCE for hunk in result.hunks)
    assert result.passed is True


def test_lowering_the_threshold_is_an_explicit_choice(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A maintainer can opt into enforcing on inference, and it is opt-in."""
    _build_ai_commit(temp_repo, git_env)

    strict = run_gate(
        _ledger_path(temp_repo), Repo(temp_repo), base_ref="main", min_confidence=0.0
    )

    assert strict.min_confidence == 0.0
    assert len(strict.hunks) >= 1


def test_no_ai_hunks_in_range_passes(temp_repo: Path, git_env: dict[str, str]) -> None:
    """An absence of gated hunks is a pass, not an invented failure."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "base.py", "BASE = 1\n")
    run_git(["add", "base.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "base"], cwd=temp_repo, env=git_env)
    run_git(["checkout", "-b", "feature"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "notes.txt", "not python\n")
    run_git(["add", "notes.txt"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "notes"], cwd=temp_repo, env=git_env)

    result = run_gate(_ledger_path(temp_repo), Repo(temp_repo), base_ref="main")

    assert result.hunks == []
    assert result.passed is True
    assert "no AI-attributed hunks" in "\n".join(result.report_lines())


def test_commits_missing_from_the_ledger_are_warned_about(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A commit git knows about and the ledger does not is a coverage gap.

    Reported rather than ignored, because a silent gate would let an unrecorded commit
    pass as though it had been checked.
    """
    run_git(["checkout", "-b", "feature"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "early.py", "EARLY = 1\n")
    run_git(["add", "early.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "before vouchcode existed"], cwd=temp_repo, env=git_env)

    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    result = run_gate(_ledger_path(temp_repo), Repo(temp_repo), base_ref=None)

    assert result.skipped_unknown_commits
    assert "not present in the ledger" in "\n".join(result.report_lines())


def test_gate_output_is_plain_text_for_a_ci_log(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """No terminal escape sequences, because a CI log is not a terminal."""
    _build_ai_commit(temp_repo, git_env)

    result = run_vouchcode(["gate", "--base-ref", "main"], cwd=temp_repo, env=git_env)

    assert result.returncode == 1
    assert "\x1b[" not in result.stdout, "gate output contains terminal escapes"
    assert "vouchcode gate" in result.stdout
    assert "FAIL" in result.stdout


def test_gate_command_exit_codes(temp_repo: Path, git_env: dict[str, str]) -> None:
    """The command exits non-zero on failure and zero once the record is present."""
    _build_ai_commit(temp_repo, git_env)

    failing = run_vouchcode(["gate", "--base-ref", "main"], cwd=temp_repo, env=git_env)
    assert failing.returncode == 1

    _set_comprehension(temp_repo, "passed")

    passing = run_vouchcode(["gate", "--base-ref", "main"], cwd=temp_repo, env=git_env)
    assert passing.returncode == 0, passing.stdout + passing.stderr
    assert "PASS" in passing.stdout


# ---------------------------------------------------------------------------
# Badge
# ---------------------------------------------------------------------------


def _report_with(ai: float, pass_rate: float | None, evaluated: int) -> dict:
    return {
        "generated_at": "2026-08-20T12:00:00+00:00",
        "summary": {
            "percentages": {"ai": ai},
            "comprehension": {"pass_rate": pass_rate, "evaluated": evaluated},
        },
    }


def test_badge_says_not_evaluated_rather_than_verified() -> None:
    """A repository with no comprehension coverage must not read as verified.

    This is the badge's most important behavior. It is the artifact most likely to be
    pasted into a README and read without the report behind it.
    """
    content = badge_content(_report_with(43.4, None, 0))

    assert "comprehension not evaluated" in content.value
    assert "verified" not in content.value.lower()
    assert content.color == COLOR_GREY


def test_badge_reports_a_low_pass_rate_as_it_is() -> None:
    """A poor result is printed, not rounded into something reassuring."""
    content = badge_content(_report_with(60.0, 25.0, 8))

    assert "25% comprehension" in content.value
    assert content.color == COLOR_AMBER


def test_badge_is_green_only_when_the_data_supports_it() -> None:
    """Green requires a high pass rate over commits actually evaluated."""
    content = badge_content(_report_with(30.0, 100.0, 12))

    assert content.color == COLOR_GREEN
    assert "100% comprehension" in content.value


def test_badge_carries_its_generation_date() -> None:
    """A stale committed badge must at least be datable."""
    svg = render_badge(badge_content(_report_with(43.4, None, 0)))

    assert "<title>" in svg
    assert "2026-08-20T12:00:00+00:00" in svg
    assert "Not a third-party attestation" in svg


def test_badge_svg_is_well_formed_and_ascii() -> None:
    """The badge is parseable XML and contains no forbidden characters."""
    import xml.etree.ElementTree as ElementTree

    svg = render_badge(badge_content(_report_with(43.4, None, 0)))

    root = ElementTree.fromstring(svg)
    assert root.tag.endswith("svg")
    assert all(ord(character) < 128 for character in svg)


def test_badge_command_writes_a_file(temp_repo: Path, git_env: dict[str, str]) -> None:
    """The command produces the SVG and says plainly what it is not."""
    _build_ai_commit(temp_repo, git_env)

    result = run_vouchcode(["badge", "-o", "badge.svg"], cwd=temp_repo, env=git_env)

    assert result.returncode == 0, result.stderr
    assert (temp_repo / "badge.svg").is_file()
    assert "not a third-party attestation" in result.stdout


# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------


def test_about_names_the_author_and_repository(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """Identity is printed by the tool, not only claimed in the README."""
    result = run_vouchcode(["about"], cwd=temp_repo, env=git_env)

    assert result.returncode == 0, result.stderr
    assert "Sudais Khalid" in result.stdout
    assert "https://sudaiskhalid.com" in result.stdout
    assert "github.com/sudais-khalid/VOUCHCODE" in result.stdout
