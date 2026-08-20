"""Canonical serialization of a ledger entry for hashing and signing.

A hash chain is only as trustworthy as the byte sequence it hashes. If the same logical
entry can serialize two different ways, verification produces false tamper reports on
untouched ledgers, and a developer who sees one learns to ignore all of them.

Four rules, each closing a specific way the bytes could drift:

    sorted keys        the hash does not depend on dictionary ordering surviving a JSON
                       round trip. Entries are written in a readable fixed order for
                       humans, and that order is now purely cosmetic: reformatting a
                       ledger with a different serializer cannot break verification.
    no whitespace      separators are tight, so indentation choices carry no weight.
    ASCII escaping     non-ASCII characters serialize identically regardless of the
                       reader's encoding settings.
    explicit exclusion the entry's own hash and signature are removed before hashing,
                       because an entry cannot contain its own digest.

The excluded fields are named rather than inferred. Inferring them would mean a future
field starting with "hash" silently leaves the covered payload, which is exactly the
kind of quiet coverage gap that makes an attestation worthless.
"""

from __future__ import annotations

import json
from typing import Any

# Fields that are products of hashing and signing rather than content, and so cannot be
# inside the payload they attest to.
EXCLUDED_FIELDS = frozenset({"entry_hash", "signature"})


def canonical_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the part of an entry that the hash and signature cover.

    previous_hash is deliberately included. It is what binds this entry to its
    predecessor, and an attestation that did not cover it would let entries be reordered
    or re-parented without detection.
    """
    return {key: value for key, value in entry.items() if key not in EXCLUDED_FIELDS}


def canonical_bytes(entry: dict[str, Any]) -> bytes:
    """Serialize an entry's covered payload to the exact bytes that get hashed.

    This function is the specification. A third party verifying a Vouchcode report
    without installing Vouchcode reimplements this and nothing else, so it is kept small
    and free of any dependency beyond the standard library.
    """
    return json.dumps(
        canonical_payload(entry),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
