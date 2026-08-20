"""Hash chaining over ledger entries.

Each entry incorporates the cryptographic hash of the preceding entry, so that altering
any entry invalidates every hash after it. This is what makes retroactive tampering
detectable rather than merely discouraged, per Section 4.4 and the first threat listed
in Section 6.2.

What the chain does and does not prove. It proves that the sequence of entries has not
been edited, reordered, or had entries removed from the middle, because any of those
changes break a link. It does not prove the entries were truthful when written: a
developer who lies to the capture layer produces a perfectly valid chain of false
statements. The chain protects the record after the fact, and the comprehension layer is
what makes the record costly to falsify in the first place.

The genesis entry. The first entry in a ledger has no predecessor, so its previous_hash
is GENESIS_HASH, a defined constant of sixty-four zeros. This is a real value rather
than null on purpose: null would make the field's absence and a deliberate genesis
marker indistinguishable, and a verifier walking the chain would have to special-case a
missing key rather than compare a value. Sixty-four zeros is also not a reachable
SHA-256 output in practice, so it cannot collide with a genuine predecessor hash."""

from __future__ import annotations

import hashlib
from typing import Any

from vouchcode.ledger.canonical import canonical_bytes

# previous_hash of the first entry in a ledger. Sixty-four zeros matches the width of a
# SHA-256 hexadecimal digest so that every previous_hash field has the same shape.
GENESIS_HASH = "0" * 64


def entry_hash(entry: dict[str, Any]) -> str:
    """Compute an entry's hash over its canonical form.

    The entry must already carry its previous_hash, since that field is part of what the
    hash covers.
    """
    return hashlib.sha256(canonical_bytes(entry)).hexdigest()


def link(entry: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Attach previous_hash and entry_hash to an entry, returning it.

    Called once when an entry is finalized. Order matters: previous_hash is set first,
    because entry_hash covers it.
    """
    entry["previous_hash"] = (
        GENESIS_HASH if previous is None else previous["entry_hash"]
    )
    entry["entry_hash"] = entry_hash(entry)
    return entry


def expected_previous_hash(previous: dict[str, Any] | None) -> str:
    """Return the previous_hash an entry should carry, given its predecessor.

    Computed from the predecessor's content, not read from its stored entry_hash field.
    That distinction is what makes tampering propagate. An edited entry keeps its old
    stored hash, so comparing against that value would leave every following entry
    looking intact and confine the damage report to one entry. Comparing against what
    the predecessor's content actually hashes to means an edit anywhere breaks every
    link after it, which is the property a hash chain exists to provide.
    """
    if previous is None:
        return GENESIS_HASH
    return entry_hash(previous)


def verify_chain(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check every link and hash in a chain, returning one finding per entry.

    Each finding reports two things independently, because they fail for different
    reasons and a reader needs to tell them apart:

        hash_ok   the entry's stored hash matches a recomputation of its own content.
                  False means this entry's content was altered.
        link_ok   the entry's previous_hash matches its predecessor's stored hash.
                  False means either the predecessor was altered or an entry was
                  inserted, removed, or reordered.

    An entry whose own content was edited fails hash_ok, and every entry after it fails
    link_ok while still passing its own hash_ok. That difference is what lets a verifier
    name the first point of failure rather than declaring the whole ledger invalid: the
    first entry failing hash_ok is where the edit happened, and the link failures after
    it are consequences.
    """
    findings: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None

    for index, entry in enumerate(entries):
        recorded_hash = str(entry.get("entry_hash") or "")
        recomputed = entry_hash(entry)
        expected_previous = expected_previous_hash(previous)
        recorded_previous = str(entry.get("previous_hash") or "")

        findings.append(
            {
                "index": index,
                "commit": entry.get("commit", ""),
                "hash_ok": bool(recorded_hash) and recorded_hash == recomputed,
                "link_ok": recorded_previous == expected_previous,
                "recorded_hash": recorded_hash,
                "recomputed_hash": recomputed,
                "recorded_previous": recorded_previous,
                "expected_previous": expected_previous,
            }
        )

        previous = entry

    return findings
