"""Hash chaining over ledger entries. Phase 4.

Each entry incorporates the cryptographic hash of the preceding entry, so that altering
any entry invalidates every hash after it. This is what makes retroactive tampering
detectable rather than merely discouraged, per Section 4.4 and the first threat listed
in Section 6.2.

Implementation requirement: the hash must be taken over a canonical serialization. The
fixed key order in vouchcode.ledger.entry.LedgerEntry.to_dict exists for this reason, so
that a re-serialized entry hashes identically and an ordering difference is never
mistaken for tampering.

Not implemented in Phase 1.
"""

from __future__ import annotations


def entry_hash(entry: object, previous_hash: str | None) -> None:
    """Compute an entry's hash over its canonical form and its predecessor. Phase 4."""
    raise NotImplementedError("ledger hash chaining is Phase 4 work")


def verify_chain(entries: list[object]) -> None:
    """Verify the chain end to end, reporting the first broken link. Phase 4."""
    raise NotImplementedError("chain verification is Phase 4 work")
