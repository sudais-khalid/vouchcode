"""Phase 5 exit criterion tests.

Three reports over the same underlying ledger must produce three outcomes:

    a report from a legitimate ledger verifies, and its displayed fingerprint matches
    the actual signing key
    a report whose JSON is altered after generation fails its own signature check
    a report regenerated from a ledger re-signed under a substitute key still verifies
    as internally self-consistent

The third case is the important one, and it is important because it passes. That is the
point. A forged report carrying fabricated figures verifies perfectly against its own
embedded key, because a signature binds a document to a key and not to a person. Phase 4
identified this and left it open; the fingerprint is what a verifier can act on, and
the test below demonstrates both halves: internal verification alone does not catch
the forgery, and comparing the fingerprint against an independently obtained copy does.

Nothing here should be read as the substitution problem being solved. It is not solvable
inside the artifact. What changed is that a verifier is now given something concrete to
check, and is told plainly, inside the report itself, that they need to check it.

ReportLab contract. The library had a usable reference available, unlike the Ed25519
and AST-diffing work, and its behavior is still asserted rather than assumed: that a
document builds, that its text is reachable, and that the fingerprint and the limits
notice actually appear on the page a reader sees.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from support import extract_pdf_text, run_git, run_vouchcode, write_file

from vouchcode.ledger.canonical import canonical_bytes
from vouchcode.ledger.chain import link
from vouchcode.ledger.signing import (
    ensure_keypair,
    key_fingerprint,
    load_private_key,
    load_public_key,
    public_key_b64,
    sign_payload,
)
from vouchcode.reporting.json_report import (
    build_report,
    verify_report,
    write_report,
)
from vouchcode.reporting.pdf_report import render_pdf
from vouchcode.reporting.summary import CAPTURE_RETROACTIVE, summarize_entries

SOURCE = """def handler_{index}(value, strict):
    if not value:
        raise ValueError("value required")
    collected = []
    for item in value:
        if item is None and strict:
            return None
        collected.append(item)
    return collected
"""


def _vouchcode_dir(root: Path) -> Path:
    return root / ".vouchcode"


def _ledger_path(root: Path) -> Path:
    return _vouchcode_dir(root) / "ledger.json"


def _build_repo(root: Path, env: dict[str, str], count: int = 3) -> None:
    run_vouchcode(["init"], cwd=root, env=env)
    for index in range(count):
        write_file(root, f"mod{index}.py", SOURCE.format(index=index))
        run_git(["add", f"mod{index}.py"], cwd=root, env=env)
        run_git(["commit", "-m", f"Add handler {index}"], cwd=root, env=env)


def _entries(root: Path) -> list[dict]:
    return json.loads(_ledger_path(root).read_text(encoding="utf-8"))["entries"]


def _report(root: Path) -> dict:
    return build_report(_entries(root), _vouchcode_dir(root), repository=str(root))


def _forge_under_substitute_key(source: Path, destination: Path) -> str:
    """Copy a repository, replace its key, rewrite its ledger, and re-sign everything.

    This is the key-substitution attack in full: the forger does not tamper with a
    signed ledger, which would be detected. They discard the key, write whatever
    ledger they like, and sign it with their own. Returns the substitute fingerprint.
    """
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("out"))

    keys = destination / ".vouchcode" / "keys"
    for name in ("signing_key.pem", "signing_key.pub"):
        (keys / name).unlink(missing_ok=True)

    vouchcode_dir = destination / ".vouchcode"
    ensure_keypair(vouchcode_dir)
    key = load_private_key(vouchcode_dir)

    ledger = vouchcode_dir / "ledger.json"
    document = json.loads(ledger.read_text(encoding="utf-8"))

    for entry in document["entries"]:
        entry["comprehension"] = {
            "status": "passed",
            "rationale": "comprehension demonstrated",
        }
    document["public_key"] = public_key_b64(vouchcode_dir)

    previous = None
    for entry in document["entries"]:
        entry.pop("entry_hash", None)
        entry.pop("signature", None)
        link(entry, previous)
        entry["signature"] = sign_payload(canonical_bytes(entry), key)
        previous = entry

    ledger.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    return key_fingerprint(key.public_key())


# ---------------------------------------------------------------------------
# Exit criterion
# ---------------------------------------------------------------------------


def test_case_one_legitimate_report_verifies_and_fingerprint_matches(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A report from an untouched ledger verifies against the real signing key."""
    _build_repo(temp_repo, git_env)

    document = _report(temp_repo)
    trusted = key_fingerprint(load_public_key(_vouchcode_dir(temp_repo)))

    result = verify_report(document, trusted)

    assert result.signature_ok is True
    assert result.fingerprint == trusted
    assert result.fingerprint_ok is True
    assert result.trustworthy is True

    # The fingerprint the report displays is the one a verifier compares, so it must be
    # the actual key's, not a value carried alongside it.
    assert document["signing_key"]["fingerprint"] == trusted


def test_case_two_altered_report_fails_its_own_verification(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """Editing a generated report breaks its signature, whatever the ledger says.

    The edit chosen is a flattering one, changing the comprehension record, because that
    is what someone would actually alter.
    """
    _build_repo(temp_repo, git_env)

    document = _report(temp_repo)
    trusted = key_fingerprint(load_public_key(_vouchcode_dir(temp_repo)))

    assert document["summary"]["comprehension"]["passed"] == 0
    document["summary"]["comprehension"]["passed"] = 3
    document["summary"]["comprehension"]["pass_rate"] = 100.0

    result = verify_report(document, trusted)

    assert result.signature_ok is False
    assert result.trustworthy is False
    assert "does not verify" in result.error

    # The key is untouched, so the fingerprint still matches. Only the content changed,
    # and the signature is what catches that.
    assert result.fingerprint_ok is True


def test_case_three_substitute_key_report_is_self_consistent_but_not_trustworthy(
    temp_repo: Path, git_env: dict[str, str], tmp_path: Path
) -> None:
    """The key-substitution case, and why the fingerprint requirement exists.

    A forged report verifies. That is not a defect in the verification, it is the limit
    of what a signature can establish, and the assertion below states it directly rather
    than leaving it implied.
    """
    _build_repo(temp_repo, git_env)
    trusted = key_fingerprint(load_public_key(_vouchcode_dir(temp_repo)))

    forged_repo = tmp_path / "forged"
    substitute = _forge_under_substitute_key(temp_repo, forged_repo)

    forged = build_report(
        _entries(forged_repo), _vouchcode_dir(forged_repo), repository=str(forged_repo)
    )

    # The forged report claims a perfect record.
    assert forged["summary"]["comprehension"]["pass_rate"] == 100.0

    # Internal verification alone passes. This is the honest limitation, asserted so it
    # cannot be quietly lost.
    internal = verify_report(forged)
    assert internal.signature_ok is True, (
        "a report signed with a substitute key verifies internally, which is exactly "
        "why the signature alone is not proof of origin"
    )

    # And a verifier who checks only that has learned nothing about who signed it.
    assert internal.fingerprint_checked is False
    assert internal.trustworthy is False

    # The fingerprint comparison is what catches it.
    checked = verify_report(forged, trusted)
    assert checked.signature_ok is True
    assert checked.fingerprint_ok is False
    assert checked.trustworthy is False
    assert checked.fingerprint == substitute
    assert checked.fingerprint != trusted


def test_the_three_cases_produce_three_distinct_outcomes(
    temp_repo: Path, git_env: dict[str, str], tmp_path: Path
) -> None:
    """The exit criterion, stated as one assertion over the same starting ledger."""
    _build_repo(temp_repo, git_env)
    trusted = key_fingerprint(load_public_key(_vouchcode_dir(temp_repo)))

    legitimate = verify_report(_report(temp_repo), trusted)

    altered_doc = _report(temp_repo)
    altered_doc["summary"]["commits"] = 99
    altered = verify_report(altered_doc, trusted)

    forged_repo = tmp_path / "forged"
    _forge_under_substitute_key(temp_repo, forged_repo)
    forged = verify_report(
        build_report(_entries(forged_repo), _vouchcode_dir(forged_repo)), trusted
    )

    outcomes = {
        "legitimate": (legitimate.signature_ok, legitimate.fingerprint_ok),
        "altered": (altered.signature_ok, altered.fingerprint_ok),
        "forged": (forged.signature_ok, forged.fingerprint_ok),
    }

    assert outcomes["legitimate"] == (True, True)
    assert outcomes["altered"] == (False, True)
    assert outcomes["forged"] == (True, False)
    assert len(set(outcomes.values())) == 3


# ---------------------------------------------------------------------------
# Fingerprint handling
# ---------------------------------------------------------------------------


def test_fingerprint_is_grouped_for_human_comparison(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The displayed form is grouped and uppercase, so it can be compared by eye."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    fingerprint = key_fingerprint(load_public_key(_vouchcode_dir(temp_repo)))
    groups = fingerprint.split()

    assert len(groups) == 8
    assert all(len(group) == 4 for group in groups)
    assert fingerprint == fingerprint.upper()


def test_fingerprint_comparison_ignores_spacing_and_case(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A verifier retyping a fingerprint must not fail over whitespace.

    Failing them over presentation would teach them the check is unreliable rather than
    that the key is wrong, which is the opposite of the intended lesson.
    """
    _build_repo(temp_repo, git_env, count=1)

    document = _report(temp_repo)
    trusted = document["signing_key"]["fingerprint"]

    for variant in (trusted.lower(), trusted.replace(" ", ""), f"  {trusted}  "):
        assert verify_report(document, variant).fingerprint_ok is True


def test_key_command_prints_the_fingerprint(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A developer needs a way to publish the fingerprint outside any report."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    result = run_vouchcode(["key"], cwd=temp_repo, env=git_env)

    assert result.returncode == 0, result.stderr
    expected = key_fingerprint(load_public_key(_vouchcode_dir(temp_repo)))
    assert expected in result.stdout


# ---------------------------------------------------------------------------
# Report contents
# ---------------------------------------------------------------------------


def test_report_states_the_limits_of_its_own_signature(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The notice travels with the artifact, not just with the documentation.

    A verifier reads the report, not this repository. A limitation recorded only in a
    README is a limitation the person holding the PDF will never see.
    """
    _build_repo(temp_repo, git_env, count=1)

    notice = _report(temp_repo)["verification_notice"]

    assert "does not prove who" in notice
    assert "independently" in notice


def test_pdf_shows_the_fingerprint_and_the_notice(
    temp_repo: Path, git_env: dict[str, str], tmp_path: Path
) -> None:
    """The PDF a reader opens carries both, in text they can actually read.

    Asserted against decoded page content rather than raw bytes. ReportLab compresses
    page streams, so a search of the raw file finds nothing and would make an empty PDF
    indistinguishable from a correct one.
    """
    _build_repo(temp_repo, git_env, count=1)

    document = _report(temp_repo)
    pdf_path = render_pdf(document, tmp_path / "report.pdf")

    assert pdf_path.is_file()
    assert pdf_path.read_bytes().startswith(b"%PDF")

    text = extract_pdf_text(pdf_path)

    assert "Vouchcode provenance report" in text
    assert "Signing key fingerprint" in text
    assert document["signing_key"]["fingerprint"].split()[0] in text
    assert "does not prove who" in text
    assert "Authorship" in text
    assert "Attribution sources" in text
    assert "Comprehension verification" in text


def test_pdf_and_json_report_the_same_figures(
    temp_repo: Path, git_env: dict[str, str], tmp_path: Path
) -> None:
    """Both formats render one summary, so they cannot disagree.

    Asserted by rendering from the same document and confirming the summary the PDF drew
    from is the one the JSON carries, which is what the shared summary module exists to
    guarantee.
    """
    _build_repo(temp_repo, git_env)

    document = _report(temp_repo)
    render_pdf(document, tmp_path / "report.pdf")

    recomputed = summarize_entries(_entries(temp_repo)).to_dict()

    assert document["summary"] == recomputed


def test_report_command_writes_both_artifacts(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The CLI produces the JSON and the PDF, and names the fingerprint."""
    _build_repo(temp_repo, git_env, count=1)

    result = run_vouchcode(
        ["report", "-o", "out", "--name", "r"], cwd=temp_repo, env=git_env
    )

    assert result.returncode == 0, result.stderr
    assert (temp_repo / "out" / "r.json").is_file()
    assert (temp_repo / "out" / "r.pdf").is_file()
    assert "signing key fingerprint" in result.stdout


def test_verify_report_command_reports_all_three_cases(
    temp_repo: Path, git_env: dict[str, str], tmp_path: Path
) -> None:
    """The CLI distinguishes verified, altered, and substitute-key reports."""
    _build_repo(temp_repo, git_env, count=1)
    trusted = key_fingerprint(load_public_key(_vouchcode_dir(temp_repo)))

    good = temp_repo / "good.json"
    write_report(_report(temp_repo), good)

    clean = run_vouchcode(
        ["verify-report", str(good), "--expect-fingerprint", trusted],
        cwd=temp_repo,
        env=git_env,
    )
    assert clean.returncode == 0
    assert "fingerprint: matches" in clean.stdout

    altered_doc = _report(temp_repo)
    altered_doc["summary"]["commits"] = 99
    altered = temp_repo / "altered.json"
    write_report(altered_doc, altered)

    broken = run_vouchcode(
        ["verify-report", str(altered), "--expect-fingerprint", trusted],
        cwd=temp_repo,
        env=git_env,
    )
    assert broken.returncode == 1
    assert "INVALID" in broken.stdout

    forged_repo = tmp_path / "forged"
    _forge_under_substitute_key(temp_repo, forged_repo)
    forged_path = temp_repo / "forged.json"
    write_report(
        build_report(_entries(forged_repo), _vouchcode_dir(forged_repo)), forged_path
    )

    substituted = run_vouchcode(
        ["verify-report", str(forged_path), "--expect-fingerprint", trusted],
        cwd=temp_repo,
        env=git_env,
    )
    assert substituted.returncode == 1
    assert "signature: valid" in substituted.stdout
    assert "signed by a different key than expected" in substituted.stderr


# ---------------------------------------------------------------------------
# Retroactive scan
# ---------------------------------------------------------------------------


def test_scan_marks_entries_as_retroactive(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """Reconstructed history must never look like observed history."""
    run_git(["init", "--initial-branch=main"], cwd=temp_repo, env=git_env)
    for index in range(3):
        write_file(temp_repo, f"old{index}.py", SOURCE.format(index=index))
        run_git(["add", f"old{index}.py"], cwd=temp_repo, env=git_env)
        run_git(["commit", "-m", f"Historic {index}"], cwd=temp_repo, env=git_env)

    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    result = run_vouchcode(["scan"], cwd=temp_repo, env=git_env)

    assert result.returncode == 0, result.stderr

    entries = _entries(temp_repo)
    assert entries, "scan produced no entries"
    assert all(entry["capture"] == CAPTURE_RETROACTIVE for entry in entries)


def test_scan_never_records_comprehension_as_evaluated(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A developer cannot be quizzed after the fact, so nothing may claim they were."""
    run_git(["init", "--initial-branch=main"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "old.py", SOURCE.format(index=0))
    run_git(["add", "old.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "Historic"], cwd=temp_repo, env=git_env)

    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    run_vouchcode(["scan"], cwd=temp_repo, env=git_env)

    for entry in _entries(temp_repo):
        comprehension = entry["comprehension"]
        assert comprehension["status"] == "excluded_retroactive"
        assert comprehension["status"] not in {"passed", "failed"}
        assert "cannot be verified after the fact" in comprehension["rationale"]


def test_scan_does_not_overwrite_live_captures(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A commit observed when it was made is better evidence than a later guess."""
    _build_repo(temp_repo, git_env, count=2)

    before = _entries(temp_repo)
    assert all(entry["capture"] == "live" for entry in before)

    run_vouchcode(["scan"], cwd=temp_repo, env=git_env)

    after = _entries(temp_repo)
    assert len(after) == len(before)
    assert all(entry["capture"] == "live" for entry in after)


def test_report_counts_retroactive_commits_separately(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A reader must be able to see how much of a report rests on reconstruction."""
    run_git(["init", "--initial-branch=main"], cwd=temp_repo, env=git_env)
    write_file(temp_repo, "old.py", SOURCE.format(index=0))
    run_git(["add", "old.py"], cwd=temp_repo, env=git_env)
    run_git(["commit", "-m", "Historic"], cwd=temp_repo, env=git_env)

    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    run_vouchcode(["scan"], cwd=temp_repo, env=git_env)

    summary = _report(temp_repo)["summary"]

    assert summary["retroactive_commits"] >= 1
    assert summary["retroactive_commits"] == summary["commits"]


# ---------------------------------------------------------------------------
# Aggregate figures
# ---------------------------------------------------------------------------


def test_unchanged_hunks_are_excluded_from_percentages() -> None:
    """A rename must not dilute the AI share of a commit that was mostly generated."""
    entries = [
        {
            "type": "commit",
            "comprehension": {"status": "not_required"},
            "hunks": [
                {
                    "lines": [1, 10],
                    "attribution": {"status": "ai", "source": "tool_signal"},
                },
                {
                    "lines": [20, 200],
                    "attribution": {"status": "unchanged", "source": "structural"},
                },
            ],
        }
    ]

    summary = summarize_entries(entries)

    assert summary.hunks_total == 2
    assert summary.hunks_new_logic == 1
    assert summary.ai_percentage == 100.0
    assert summary.lines_attributed == 10


def test_pass_rate_counts_only_evaluated_commits() -> None:
    """Skipped and excluded commits are never counted as passes."""
    entries = [
        {"comprehension": {"status": "passed"}},
        {"comprehension": {"status": "failed"}},
        {"comprehension": {"status": "excluded_merge"}},
        {"comprehension": {"status": "skipped_non_interactive"}},
    ]

    summary = summarize_entries(entries)

    assert summary.comprehension_evaluated == 2
    assert summary.comprehension_pass_rate == 50.0
    assert summary.comprehension_by_status["excluded_merge"] == 1
    assert summary.comprehension_by_status["skipped_non_interactive"] == 1


def test_pass_rate_is_none_when_nothing_was_evaluated() -> None:
    """No evaluation means no rate, not a rate of zero.

    Zero percent would read as universal failure, which is a different and much worse
    claim than having asked nothing.
    """
    summary = summarize_entries([{"comprehension": {"status": "excluded_merge"}}])

    assert summary.comprehension_pass_rate is None


def test_attribution_sources_are_counted_separately() -> None:
    """Evidence, inference, and proof are not interchangeable and are not merged."""
    entries = [
        {
            "hunks": [
                {
                    "lines": [1, 2],
                    "attribution": {"status": "ai", "source": "tool_signal"},
                },
                {
                    "lines": [3, 4],
                    "attribution": {"status": "ai", "source": "stylometry"},
                },
                {
                    "lines": [5, 6],
                    "attribution": {"status": "unchanged", "source": "structural"},
                },
            ]
        }
    ]

    summary = summarize_entries(entries)

    assert summary.by_source["tool_signal"] == 1
    assert summary.by_source["stylometry"] == 1
    assert summary.by_source["structural"] == 1
