"""Signed JSON report generation and verification.

Emits the ledger data for a range, the aggregate figures, and the public key required to
verify it, then signs the whole document. A recipient checks it with a standard Ed25519
implementation and the canonicalization in vouchcode.ledger.canonical, without
installing Vouchcode and without an account.

What a passing verification means, and what it does not
-------------------------------------------------------

A report that verifies is internally consistent: its contents have not been altered
since it was signed, by whoever held the key it names. That is the whole of the claim.

It is not proof of identity. Anyone can generate a keypair, write a ledger, and sign a
report with it, and that report verifies perfectly. The signature binds the document to
a key, not to a person.

Closing that gap is not something a report can do by itself, and this module does not
pretend otherwise. What it does is give a verifier something concrete to check: the
key's fingerprint is displayed prominently, in a form a person can compare or read
aloud. A verifier who obtains the same fingerprint from somewhere the report author does
not control, a repository README written months earlier, a profile page, a message sent
at another time, can compare the two. If they match, the report was signed by the key
that also published that fingerprint. If they differ, something is wrong.

That is a real check and a limited one. It is stated in the report itself rather than
buried in documentation, because a verifier who does not know the check exists will not
perform it, and a report that implies more assurance than it provides is worse than one
that provides none.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vouchcode.ledger.canonical import canonical_bytes
from vouchcode.ledger.entry import utc_timestamp
from vouchcode.ledger.signing import (
    SIGNATURE_ALGORITHM,
    SigningError,
    decode_public_key,
    fingerprint_from_encoded,
    key_digest,
    load_private_key,
    public_key_b64,
    sign_payload,
    verify_payload,
)
from vouchcode.reporting.summary import ReportSummary, summarize_entries

REPORT_VERSION = 1

# Signature field name, excluded from the payload it covers. Named to match the ledger's
# own field so a reader who has seen one recognizes the other.
SIGNATURE_FIELD = "report_signature"

# Text carried inside every report. A verifier reads the artifact, not this codebase, so
# the limits of what a signature proves have to travel with the document.
VERIFICATION_NOTICE = (
    "Verifying this report's signature proves that its contents have not been altered "
    "since it was signed by the holder of the key below. It does not prove who that "
    "holder is. Anyone can generate a key and sign a report with it. To establish "
    "identity, compare the key fingerprint below against a copy of the same "
    "fingerprint obtained independently of this report, for example one published in "
    "the developer's repository or profile at an earlier date. Matching fingerprints "
    "mean the same key "
    "produced both. Differing fingerprints mean this report was not signed by the key "
    "you expected."
)


@dataclass(frozen=True)
class ReportVerification:
    """The outcome of checking a report against itself and a trusted fingerprint."""

    signature_ok: bool
    fingerprint: str
    expected_fingerprint: str = ""
    error: str = ""

    @property
    def fingerprint_checked(self) -> bool:
        return bool(self.expected_fingerprint)

    @property
    def fingerprint_ok(self) -> bool:
        """Whether the report's key matches the fingerprint the verifier expected.

        True when no expectation was supplied, because nothing was claimed. Read
        alongside fingerprint_checked, never on its own: an unchecked fingerprint is not
        a matching one.
        """
        if not self.expected_fingerprint:
            return True
        return _normalize(self.fingerprint) == _normalize(self.expected_fingerprint)

    @property
    def trustworthy(self) -> bool:
        """Whether this report both verifies and comes from the expected key.

        Deliberately requires an expected fingerprint. A report that merely verifies is
        self-consistent, which this property does not treat as sufficient, because that
        is precisely the assurance gap the fingerprint exists to close.
        """
        return self.signature_ok and self.fingerprint_checked and self.fingerprint_ok


def build_report(
    entries: list[dict[str, Any]],
    vouchcode_dir: Path,
    repository: str = "",
    commit_range: str = "",
) -> dict[str, Any]:
    """Build and sign the JSON report document for a range of entries."""
    encoded_key = public_key_b64(vouchcode_dir)
    public_key = decode_public_key(encoded_key)

    summary: ReportSummary = summarize_entries(entries)

    document: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "generated_at": utc_timestamp(),
        "repository": repository,
        "commit_range": commit_range or "full history",
        "signing_key": {
            "algorithm": SIGNATURE_ALGORITHM,
            "public_key": encoded_key,
            "digest": key_digest(public_key),
            "fingerprint": fingerprint_from_encoded(encoded_key),
        },
        "verification_notice": VERIFICATION_NOTICE,
        "summary": summary.to_dict(),
        "entries": entries,
    }

    private_key = load_private_key(vouchcode_dir)
    document[SIGNATURE_FIELD] = sign_payload(_payload_bytes(document), private_key)

    return document


def write_report(document: dict[str, Any], destination: Path) -> Path:
    """Write a report document to disk."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return destination


def verify_report(
    document: dict[str, Any],
    expected_fingerprint: str = "",
) -> ReportVerification:
    """Check a report's signature, and optionally its key against a trusted fingerprint.

    The two checks answer different questions and are reported separately. The signature
    answers whether the document changed. The fingerprint answers whether the right key
    signed it. A report can pass the first and fail the second, which is exactly the
    key-substitution case, and collapsing them into one verdict would hide it.
    """
    encoded_key = document.get("signing_key", {}).get("public_key")
    if not isinstance(encoded_key, str) or not encoded_key:
        return ReportVerification(
            signature_ok=False,
            fingerprint="",
            expected_fingerprint=expected_fingerprint,
            error="report carries no public key",
        )

    try:
        public_key = decode_public_key(encoded_key)
        fingerprint = fingerprint_from_encoded(encoded_key)
    except SigningError as exc:
        return ReportVerification(
            signature_ok=False,
            fingerprint="",
            expected_fingerprint=expected_fingerprint,
            error=str(exc),
        )

    signature = document.get(SIGNATURE_FIELD)
    if not isinstance(signature, str) or not signature:
        return ReportVerification(
            signature_ok=False,
            fingerprint=fingerprint,
            expected_fingerprint=expected_fingerprint,
            error="report carries no signature",
        )

    signature_ok = verify_payload(_payload_bytes(document), signature, public_key)

    return ReportVerification(
        signature_ok=signature_ok,
        fingerprint=fingerprint,
        expected_fingerprint=expected_fingerprint,
        error="" if signature_ok else "report signature does not verify",
    )


def read_report(path: Path) -> dict[str, Any]:
    """Load a report document from disk."""
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def _payload_bytes(document: dict[str, Any]) -> bytes:
    """Return the bytes a report's signature covers.

    Reuses the ledger's canonicalization rather than defining a second one. A verifier
    who has implemented the ledger check already has everything needed for this, and one
    canonical form cannot drift out of step with another that does not exist.
    """
    return canonical_bytes(
        {key: value for key, value in document.items() if key != SIGNATURE_FIELD}
    )


def _normalize(fingerprint: str) -> str:
    """Reduce a fingerprint to a comparable form.

    Spacing and case are presentation. A verifier retyping a fingerprint from a README,
    or pasting one with the grouping stripped, is making the same comparison, and
    failing them over whitespace would teach them the check is unreliable rather than
    that the key is wrong."""
    return "".join(fingerprint.split()).upper()
