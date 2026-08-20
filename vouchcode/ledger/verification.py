"""Verifying a ledger: hash chain, signatures, and fingerprint comparability.

Reports per entry, never as one verdict for the repository. A single pass or fail would
throw away the two things a reader actually needs: which entry failed first, and whether
what failed was integrity or merely comparability.

Three outcome categories, kept separate because conflating any two of them produces a
misleading report:

    verified              hash recomputes, link matches, signature checks out, and the
                          fingerprints were computed under conditions matching this
                          interpreter.
    tampered              the entry's own content no longer matches its stored hash or
                          its signature. Someone edited it after it was written.
    chain_broken          the entry itself is intact but its link to its predecessor
                          does not match, which is what every entry after a tampered one
                          looks like. Distinguishing this from tampered lets the report
                          name the first point of failure instead of condemning the
                          whole ledger.
    unverifiable_version  cryptographically sound in every respect, but its fingerprints
                          were computed under a different interpreter or a different
                          fingerprinting algorithm, so they cannot be compared with
                          freshly computed ones. This is neither tampering nor a clean
                          verification, and reporting it as either would be wrong.
    unsigned              no signature present. Entries written before signing existed
                          fall here. Not tampering, but not attested either.

The version category is the reason this module exists in the shape it does. Without
it, a ledger written under Python 3.12 and verified under 3.13 would show fingerprints
that do not match and be reported as tampered, and a developer who saw that once would
rightly stop trusting the tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vouchcode.ledger.canonical import canonical_bytes
from vouchcode.ledger.chain import verify_chain
from vouchcode.ledger.signing import (
    SigningError,
    decode_public_key,
    verify_payload,
)
from vouchcode.ledger.store import read_ledger
from vouchcode.segmentation.fingerprint import describe_mismatch, is_comparable

STATUS_VERIFIED = "verified"
STATUS_TAMPERED = "tampered"
STATUS_CHAIN_BROKEN = "chain_broken"
STATUS_UNVERIFIABLE_VERSION = "unverifiable_version"
STATUS_UNSIGNED = "unsigned"

# Statuses that mean the ledger's integrity is in question, as opposed to statuses that
# only limit what can be concluded from it.
INTEGRITY_FAILURES = frozenset({STATUS_TAMPERED, STATUS_CHAIN_BROKEN})


@dataclass(frozen=True)
class EntryVerification:
    """The verification outcome for one entry."""

    index: int
    commit: str
    status: str
    detail: str
    hash_ok: bool
    link_ok: bool
    signature_ok: bool
    version_comparable: bool

    @property
    def is_integrity_failure(self) -> bool:
        return self.status in INTEGRITY_FAILURES


@dataclass(frozen=True)
class LedgerVerification:
    """The verification outcome for a whole ledger."""

    entries: list[EntryVerification] = field(default_factory=list)
    public_key_present: bool = False
    error: str = ""

    @property
    def first_failure(self) -> EntryVerification | None:
        """The earliest entry whose integrity failed, which is where tampering began.

        Later failures are consequences of this one. Reporting the earliest turns a
        verification result into something actionable.
        """
        for entry in self.entries:
            if entry.is_integrity_failure:
                return entry
        return None

    @property
    def intact(self) -> bool:
        """Whether the chain is cryptographically sound end to end.

        Deliberately unaffected by unverifiable_version entries. Those are sound; they
        simply cannot have their fingerprints compared. Folding them into this would
        report a version difference as a compromised ledger.
        """
        return not any(entry.is_integrity_failure for entry in self.entries)

    def counts(self) -> dict[str, int]:
        """Number of entries in each status, for a summary line."""
        tally: dict[str, int] = {}
        for entry in self.entries:
            tally[entry.status] = tally.get(entry.status, 0) + 1
        return tally


def verify_ledger(ledger_path: Path) -> LedgerVerification:
    """Verify every entry in a ledger and report the outcome for each."""
    try:
        document = read_ledger(ledger_path)
    except Exception as exc:
        return LedgerVerification(error=str(exc))

    entries = [item for item in document.get("entries", []) if isinstance(item, dict)]
    encoded_key = document.get("public_key")

    public_key = None
    if isinstance(encoded_key, str) and encoded_key:
        try:
            public_key = decode_public_key(encoded_key)
        except SigningError:
            public_key = None

    chain_findings = verify_chain(entries)
    results: list[EntryVerification] = []

    for entry, finding in zip(entries, chain_findings, strict=True):
        results.append(_verify_entry(entry, finding, public_key))

    return LedgerVerification(
        entries=results,
        public_key_present=public_key is not None,
    )


def _verify_entry(
    entry: dict[str, Any],
    finding: dict[str, Any],
    public_key: Any,
) -> EntryVerification:
    """Classify one entry from its chain finding, signature, and version tag."""
    hash_ok = bool(finding["hash_ok"])
    link_ok = bool(finding["link_ok"])

    raw_signature = entry.get("signature")
    signature = raw_signature if isinstance(raw_signature, str) else ""
    has_signature = bool(signature)

    signature_ok = False
    if has_signature and public_key is not None:
        signature_ok = verify_payload(canonical_bytes(entry), signature, public_key)

    # Fingerprints only exist on entries that carried hunks. An entry without them is
    # trivially comparable, because there is nothing to compare.
    has_fingerprints = bool(entry.get("hunks"))
    recorded_version = entry.get("fingerprint_version")
    version_comparable = (
        True if not has_fingerprints else is_comparable(recorded_version)
    )

    status, detail = _classify(
        hash_ok=hash_ok,
        link_ok=link_ok,
        has_signature=has_signature,
        signature_ok=signature_ok,
        version_comparable=version_comparable,
        recorded_version=recorded_version,
        finding=finding,
    )

    return EntryVerification(
        index=int(finding["index"]),
        commit=str(finding["commit"]),
        status=status,
        detail=detail,
        hash_ok=hash_ok,
        link_ok=link_ok,
        signature_ok=signature_ok,
        version_comparable=version_comparable,
    )


def _classify(
    hash_ok: bool,
    link_ok: bool,
    has_signature: bool,
    signature_ok: bool,
    version_comparable: bool,
    recorded_version: dict[str, Any] | None,
    finding: dict[str, Any],
) -> tuple[str, str]:
    """Reduce the individual checks to one status and an explanation.

    Order is deliberate. Integrity is decided before comparability, because an edited
    entry is an edited entry whatever interpreter wrote it, and reporting it as merely
    non-comparable would hide tampering behind a benign-sounding label.
    """
    if not hash_ok:
        return (
            STATUS_TAMPERED,
            f"content does not match its recorded hash: stored "
            f"{_short(finding['recorded_hash'])}, recomputed "
            f"{_short(finding['recomputed_hash'])}",
        )

    if has_signature and not signature_ok:
        return (
            STATUS_TAMPERED,
            "signature does not verify against the ledger's public key",
        )

    if not link_ok:
        return (
            STATUS_CHAIN_BROKEN,
            f"previous_hash {_short(finding['recorded_previous'])} does not match the "
            f"preceding entry's hash {_short(finding['expected_previous'])}",
        )

    if not has_signature:
        return (
            STATUS_UNSIGNED,
            "entry carries no signature, so its origin is not attested",
        )

    if not version_comparable:
        return (
            STATUS_UNVERIFIABLE_VERSION,
            describe_mismatch(recorded_version),
        )

    return STATUS_VERIFIED, "hash, chain link, and signature all check out"


def _short(digest: str) -> str:
    """Abbreviate a hash for display, keeping enough to be recognizable."""
    return digest[:12] if digest else "(none)"
