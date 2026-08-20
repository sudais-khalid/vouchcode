"""Phase 4 exit criterion tests.

Three chains over the same underlying content must produce three different verification
outcomes:

    a legitimate chain verifies clean end to end
    a chain with one entry's stored data mutated is detected as broken from that entry
    forward, naming the first point of failure rather than condemning the whole ledger
    a chain whose fingerprints were computed under a different interpreter is
    reported as non-comparable, neither tampered nor silently verified

The third case matters most, because it is the direct test of whether the fingerprint
versioning resolution actually closed the concern. It is constructed by
rebuilding the chain with a different version tag and re-signing it properly, which is
what a genuinely different interpreter would have produced. Editing the field in place
would be tampering, and would correctly be reported as such: the point is that a
legitimately written ledger from another interpreter is not tampering.

Library contract. The cryptography package's Ed25519 API had no verified reference
available, so its behavior is asserted here directly rather than assumed: signature
length, determinism, rejection of a tampered payload, rejection of a foreign key, and
PEM round-tripping.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

from support import run_git, run_vouchcode, write_file

from vouchcode.ledger.canonical import canonical_bytes, canonical_payload
from vouchcode.ledger.chain import GENESIS_HASH, entry_hash, link, verify_chain
from vouchcode.ledger.signing import (
    decode_public_key,
    encode_public_key,
    ensure_keypair,
    key_paths,
    load_private_key,
    sign_payload,
    verify_payload,
)
from vouchcode.ledger.verification import (
    STATUS_CHAIN_BROKEN,
    STATUS_TAMPERED,
    STATUS_UNVERIFIABLE_VERSION,
    STATUS_VERIFIED,
    verify_ledger,
)

SOURCE = """def handler_{index}(value):
    if not value:
        return None
    return value.strip()
"""


def _ledger_path(root: Path) -> Path:
    return root / ".vouchcode" / "ledger.json"


def _read(root: Path) -> dict:
    return json.loads(_ledger_path(root).read_text(encoding="utf-8"))


def _write(root: Path, document: dict) -> None:
    _ledger_path(root).write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )


def _build_chain(root: Path, env: dict[str, str], count: int = 3) -> None:
    """Create a repository with a signed, chained ledger of the given length."""
    run_vouchcode(["init"], cwd=root, env=env)
    for index in range(count):
        write_file(root, f"mod{index}.py", SOURCE.format(index=index))
        run_git(["add", f"mod{index}.py"], cwd=root, env=env)
        run_git(["commit", "-m", f"Add handler {index}"], cwd=root, env=env)


def _resign_chain(root: Path, document: dict) -> None:
    """Rebuild every hash and signature in a chain, as a legitimate writer would.

    Used to construct a ledger that another interpreter could genuinely have produced.
    Without this the version case would be indistinguishable from tampering, and would
    prove nothing about the version category.
    """
    key = load_private_key(root / ".vouchcode")
    previous = None
    for entry in document["entries"]:
        entry.pop("entry_hash", None)
        entry.pop("signature", None)
        link(entry, previous)
        entry["signature"] = sign_payload(canonical_bytes(entry), key)
        previous = entry


# ---------------------------------------------------------------------------
# Exit criterion: three chains, three outcomes
# ---------------------------------------------------------------------------


def test_case_one_legitimate_chain_verifies_clean(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """An untouched chain verifies end to end, every entry."""
    _build_chain(temp_repo, git_env)

    result = verify_ledger(_ledger_path(temp_repo))

    assert result.intact is True
    assert result.first_failure is None
    assert result.public_key_present is True
    assert [entry.status for entry in result.entries] == [STATUS_VERIFIED] * 3

    for entry in result.entries:
        assert entry.hash_ok and entry.link_ok and entry.signature_ok


def test_case_two_mutation_is_detected_from_that_entry_forward(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """One mutated field breaks the entry and every link after it.

    Asserts on the shape of the damage, not merely that verification failed. The mutated
    entry must be tampered, the entries after it must be chain_broken, and the entries
    before it must still verify, because that is what identifies where the edit was.
    """
    _build_chain(temp_repo, git_env)

    document = _read(temp_repo)
    # A single field, changed in place, with nothing else touched.
    document["entries"][1]["attribution"]["confidence"] = 0.99
    _write(temp_repo, document)

    result = verify_ledger(_ledger_path(temp_repo))

    assert result.intact is False
    assert [entry.status for entry in result.entries] == [
        STATUS_VERIFIED,
        STATUS_TAMPERED,
        STATUS_CHAIN_BROKEN,
    ]

    failure = result.first_failure
    assert failure is not None
    assert failure.index == 1, "the first point of failure must be the mutated entry"
    assert failure.hash_ok is False

    # The entry after it is intact in itself, and only its link is broken. That
    # distinction is what stops the report from blaming the wrong entry.
    downstream = result.entries[2]
    assert downstream.hash_ok is True
    assert downstream.link_ok is False


def test_case_three_different_interpreter_is_non_comparable_not_tampered(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A legitimately written chain from another interpreter is neither of the others.

    The chain is rebuilt and re-signed with a different fingerprint version tag, as a
    different interpreter would have produced. Every cryptographic check passes;
    only the fingerprints are non-comparable.
    """
    _build_chain(temp_repo, git_env)

    document = _read(temp_repo)
    document["entries"][1]["fingerprint_version"] = {
        "algorithm": 1,
        "python": "3.12",
        "ast_signature": "aaaaaaaaaaaaaaaa",
    }
    _resign_chain(temp_repo, document)
    _write(temp_repo, document)

    result = verify_ledger(_ledger_path(temp_repo))

    assert [entry.status for entry in result.entries] == [
        STATUS_VERIFIED,
        STATUS_UNVERIFIABLE_VERSION,
        STATUS_VERIFIED,
    ]

    flagged = result.entries[1]

    # Not tampered: every cryptographic check passes.
    assert flagged.hash_ok is True
    assert flagged.link_ok is True
    assert flagged.signature_ok is True
    assert flagged.status != STATUS_TAMPERED

    # Not silently verified either.
    assert flagged.status != STATUS_VERIFIED
    assert flagged.version_comparable is False
    assert "different conditions" in flagged.detail

    # The ledger's integrity is not in question, so the chain is still intact.
    assert result.intact is True
    assert result.first_failure is None


def test_the_three_cases_produce_three_distinct_outcomes(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The exit criterion, stated as one assertion over the same starting content."""
    _build_chain(temp_repo, git_env)
    clean = _read(temp_repo)

    legitimate = verify_ledger(_ledger_path(temp_repo)).entries[1].status

    mutated_doc = json.loads(json.dumps(clean))
    mutated_doc["entries"][1]["attribution"]["confidence"] = 0.99
    _write(temp_repo, mutated_doc)
    mutated = verify_ledger(_ledger_path(temp_repo)).entries[1].status

    version_doc = json.loads(json.dumps(clean))
    version_doc["entries"][1]["fingerprint_version"] = {
        "algorithm": 1,
        "python": "3.12",
        "ast_signature": "aaaaaaaaaaaaaaaa",
    }
    _resign_chain(temp_repo, version_doc)
    _write(temp_repo, version_doc)
    versioned = verify_ledger(_ledger_path(temp_repo)).entries[1].status

    assert len({legitimate, mutated, versioned}) == 3, (
        f"expected three distinct outcomes, got {legitimate}, {mutated}, {versioned}"
    )
    assert legitimate == STATUS_VERIFIED
    assert mutated == STATUS_TAMPERED
    assert versioned == STATUS_UNVERIFIABLE_VERSION


# ---------------------------------------------------------------------------
# Fingerprint versioning
# ---------------------------------------------------------------------------


def test_entries_with_hunks_record_a_fingerprint_version(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A fingerprint is never stored without the conditions under which it was made."""
    _build_chain(temp_repo, git_env, count=1)

    entry = _read(temp_repo)["entries"][0]

    assert entry["hunks"], "expected the commit to produce hunks"
    assert all(hunk["fingerprint"] for hunk in entry["hunks"])

    version = entry["fingerprint_version"]
    assert version["algorithm"] == 1
    assert version["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert version["ast_signature"]


def test_changing_only_the_version_field_yields_non_comparable(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The resolution, tested directly: version differs, nothing else does.

    This is the test the fingerprint versioning work was specified to produce. The
    outcome must be neither tampered nor confirmed identical.
    """
    _build_chain(temp_repo, git_env, count=1)

    document = _read(temp_repo)
    document["entries"][0]["fingerprint_version"]["python"] = "3.9"
    _resign_chain(temp_repo, document)
    _write(temp_repo, document)

    entry = verify_ledger(_ledger_path(temp_repo)).entries[0]

    assert entry.status == STATUS_UNVERIFIABLE_VERSION
    assert entry.status not in {STATUS_TAMPERED, STATUS_VERIFIED}
    assert entry.signature_ok is True


def test_missing_version_tag_is_not_assumed_comparable(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """An entry with no version tag makes no claim, so nothing may be concluded."""
    _build_chain(temp_repo, git_env, count=1)

    document = _read(temp_repo)
    document["entries"][0]["fingerprint_version"] = None
    _resign_chain(temp_repo, document)
    _write(temp_repo, document)

    entry = verify_ledger(_ledger_path(temp_repo)).entries[0]

    assert entry.status == STATUS_UNVERIFIABLE_VERSION
    assert "records no fingerprint version" in entry.detail


# ---------------------------------------------------------------------------
# Chain mechanics
# ---------------------------------------------------------------------------


def test_genesis_entry_uses_the_defined_constant(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The first entry has a defined predecessor value, not a null."""
    _build_chain(temp_repo, git_env, count=2)

    entries = _read(temp_repo)["entries"]

    assert entries[0]["previous_hash"] == GENESIS_HASH
    assert entries[1]["previous_hash"] == entries[0]["entry_hash"]


def test_hash_covers_previous_hash_so_entries_cannot_be_reordered() -> None:
    """Swapping two entries breaks the chain, because the link is part of the hash."""
    first = {"commit": "a", "files": ["a.py"]}
    second = {"commit": "b", "files": ["b.py"]}

    link(first, None)
    link(second, first)

    findings = verify_chain([second, first])

    assert not all(finding["link_ok"] for finding in findings)


def test_entry_hash_excludes_its_own_hash_and_signature() -> None:
    """An entry cannot contain its own digest, so both fields sit outside it."""
    entry = {"commit": "a", "entry_hash": "x", "signature": "y", "previous_hash": "z"}

    payload = canonical_payload(entry)

    assert "entry_hash" not in payload
    assert "signature" not in payload
    assert "previous_hash" in payload, "the link must be covered by the hash"


def test_canonical_bytes_ignore_key_order() -> None:
    """Reformatting a ledger must not break verification."""
    one = {"a": 1, "b": 2, "previous_hash": "x"}
    two = {"previous_hash": "x", "b": 2, "a": 1}

    assert canonical_bytes(one) == canonical_bytes(two)
    assert entry_hash(one) == entry_hash(two)


# ---------------------------------------------------------------------------
# Ed25519 library contract, asserted rather than assumed
# ---------------------------------------------------------------------------


def test_keypair_is_generated_at_init_and_is_idempotent(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """init creates a keypair, and rerunning it never replaces one.

    Replacing a key would orphan every signature already recorded, turning a rerun of
    init into silent destruction of the provenance record.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    paths = key_paths(temp_repo / ".vouchcode")
    assert paths.private.is_file()
    assert paths.public.is_file()

    original = paths.private.read_bytes()
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)

    assert paths.private.read_bytes() == original


def test_private_key_permissions_are_restricted_where_supported(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """Owner read and write only, on platforms with POSIX permission bits.

    Skipped on Windows rather than asserted loosely, because chmod is a documented no-op
    there and a passing assertion would imply a protection the file does not have.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    paths = key_paths(temp_repo / ".vouchcode")

    if sys.platform.startswith("win"):
        return

    mode = stat.S_IMODE(paths.private.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR, f"unexpected key mode {oct(mode)}"


def test_signature_rejects_a_modified_payload(tmp_path: Path) -> None:
    """A signature must not verify against altered bytes."""
    ensure_keypair(tmp_path)
    key = load_private_key(tmp_path)
    public = key.public_key()

    payload = b'{"commit":"abc"}'
    signature = sign_payload(payload, key)

    assert verify_payload(payload, signature, public) is True
    assert verify_payload(b'{"commit":"abd"}', signature, public) is False


def test_signature_rejects_a_foreign_key(tmp_path: Path) -> None:
    """A signature from one key must not verify under another.

    This is what makes key substitution detectable by a verifier who knows which key to
    expect, per Section 6.2.
    """
    ensure_keypair(tmp_path / "one")
    ensure_keypair(tmp_path / "two")

    payload = b"payload"
    signature = sign_payload(payload, load_private_key(tmp_path / "one"))
    foreign = load_private_key(tmp_path / "two").public_key()

    assert verify_payload(payload, signature, foreign) is False


def test_signing_is_deterministic(tmp_path: Path) -> None:
    """Ed25519 signatures are deterministic, so a ledger is byte-reproducible.

    Asserted rather than assumed. A randomized scheme would make two runs over identical
    content produce different ledgers, and reproducibility is what lets a recipient
    check a report independently.
    """
    ensure_keypair(tmp_path)
    key = load_private_key(tmp_path)

    payload = b"same payload"
    assert sign_payload(payload, key) == sign_payload(payload, key)


def test_public_key_survives_encoding_round_trip(tmp_path: Path) -> None:
    """The base64 form embedded in a ledger rebuilds into a working key."""
    ensure_keypair(tmp_path)
    key = load_private_key(tmp_path)

    encoded = encode_public_key(key.public_key())
    rebuilt = decode_public_key(encoded)

    payload = b"payload"
    assert verify_payload(payload, sign_payload(payload, key), rebuilt) is True


def test_ledger_embeds_the_public_key(temp_repo: Path, git_env: dict[str, str]) -> None:
    """Verification needs nothing beyond the ledger file itself."""
    _build_chain(temp_repo, git_env, count=1)

    document = _read(temp_repo)

    assert document["public_key"]
    assert decode_public_key(document["public_key"]) is not None


# ---------------------------------------------------------------------------
# The verify command
# ---------------------------------------------------------------------------


def test_verify_command_reports_per_entry_and_names_the_first_failure(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The CLI names where the break started, and exits non-zero."""
    _build_chain(temp_repo, git_env)

    document = _read(temp_repo)
    document["entries"][1]["attribution"]["confidence"] = 0.99
    _write(temp_repo, document)

    result = run_vouchcode(["verify"], cwd=temp_repo, env=git_env)

    assert result.returncode == 1
    assert "tampered" in result.stdout
    assert "chain_broken" in result.stdout
    assert "first failure at entry 1" in result.stderr


def test_verify_command_exits_zero_for_a_version_difference(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A non-comparable entry is not a failure and must not exit non-zero.

    Exiting non-zero here would train a reader to ignore the signal, which would defeat
    the point of having a separate category for it.
    """
    _build_chain(temp_repo, git_env, count=1)

    document = _read(temp_repo)
    document["entries"][0]["fingerprint_version"]["python"] = "3.9"
    _resign_chain(temp_repo, document)
    _write(temp_repo, document)

    result = run_vouchcode(["verify"], cwd=temp_repo, env=git_env)

    assert result.returncode == 0
    assert "unverifiable_version" in result.stdout
    assert "cannot be compared" in result.stdout


def test_verify_command_reports_a_clean_chain(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A healthy ledger reports briefly and exits zero."""
    _build_chain(temp_repo, git_env)

    result = run_vouchcode(["verify"], cwd=temp_repo, env=git_env)

    assert result.returncode == 0
    assert "chain intact" in result.stdout
    assert "tampered" not in result.stdout
