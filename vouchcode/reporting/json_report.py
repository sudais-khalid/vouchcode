"""Signed JSON report generation. Phase 5.

Emits the ledger data together with the public key required to verify it. The document
must be verifiable by a third party using only a standard Ed25519 implementation, with
no Vouchcode install, which constrains it to a self-describing format with an explicitly
documented canonicalization.

Not implemented in Phase 1.
"""

from __future__ import annotations


def build_report(entries: list[object], public_key: object) -> None:
    """Build the signed, self-verifying JSON report document. Phase 5."""
    raise NotImplementedError("JSON report generation is Phase 5 work")
