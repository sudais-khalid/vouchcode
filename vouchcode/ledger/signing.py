"""Ed25519 signing of ledger entries. Phase 4.

A keypair is generated and held locally by the developer, and every entry is signed with
it. Section 6.1 places a compromised local machine and a compromised private key outside
the threat model, consistent with GPG commit signing and Sigstore keyless signing.
Section 6.2 mitigates key substitution by embedding the public key in the distributed
report and recommending out-of-band verification for high-stakes use.

Per CLAUDE.md Rule 6, run /find-skill for the cryptography package's Ed25519 API before
implementing this module. A subtly wrong signing pattern is expensive to discover after
a ledger has accumulated history under it.

Open decisions to settle at implementation time, recorded now so they are not made by
accident:

    private key storage format and file permissions
    whether to support an encrypted private key and therefore a passphrase prompt
    key rotation, and how a report represents entries signed under a superseded key

Not implemented in Phase 1.
"""

from __future__ import annotations


def generate_keypair(destination: object) -> None:
    """Generate an Ed25519 keypair and persist it locally. Phase 4."""
    raise NotImplementedError("keypair generation is Phase 4 work")


def sign_entry(entry_digest: bytes, private_key: object) -> None:
    """Sign an entry digest with the local Ed25519 private key. Phase 4."""
    raise NotImplementedError("entry signing is Phase 4 work")


def verify_signature(entry_digest: bytes, signature: bytes, public_key: object) -> None:
    """Verify one entry signature against a public key. Phase 4."""
    raise NotImplementedError("signature verification is Phase 4 work")
