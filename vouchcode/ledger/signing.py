"""Ed25519 signing of ledger entries.

A keypair is generated and held locally by the developer at vouchcode init, and every
entry is signed with it. Section 6.1 places a compromised local machine and a
compromised private key outside the threat model, consistent with GPG commit signing
and Sigstore keyless signing.

Scope decisions for this version, recorded here and in CLAUDE.md so they are read as
decisions rather than as oversights:

    No passphrase protection. The private key is written unencrypted. A passphrase would
    mean prompting on every commit, or caching the decrypted key somewhere, and the
    second defeats the point while the first makes committing intolerable. Section 6.1
    already places a compromised local machine out of scope, so a passphrase would
    defend against a threat the model does not cover while imposing a cost on every
    commit. Revisit only alongside a real agent or keychain integration.

    Restrictive file permissions. The private key is written owner read and write only.
    On Windows there is no POSIX permission bit to set, and the call is a documented
    no-op rather than a silent one: the key inherits directory permissions, which is
    weaker, and a report reader should know that.

    Key rotation is out of scope. There is one key per repository, for its lifetime.
    Rotation raises questions this version does not answer: whether entries signed under
    a superseded key stay verifiable, how a verifier learns a rotation was legitimate
    rather than a substitution, and what a report shows for a history spanning two keys.
    Answering those badly is worse than not offering the feature, so the feature is not
    offered.

Key substitution, per Section 6.2. Nothing here stops someone from discarding the
keypair, generating a new one, and re-signing a rewritten ledger. The chain would verify
cleanly under the new key. What defeats that is the public key being known to the
verifier in advance, which is why the reporting layer embeds it and why the
documentation recommends out-of-band verification for high-stakes use. Signing proves
the ledger was produced by the holder of a specific key; it is the verifier's job to
know which key that should be.

The library contract used here was verified by execution rather than taken from memory,
because no reference for this API was available. See the module tests for what is
verified.
"""

from __future__ import annotations

import base64
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from vouchcode.errors import VouchcodeError

# Directory and file names beneath .vouchcode/ where the keypair lives.
KEYS_DIR_NAME = "keys"
PRIVATE_KEY_NAME = "signing_key.pem"
PUBLIC_KEY_NAME = "signing_key.pub"

# Owner read and write, nothing for group or other.
PRIVATE_KEY_MODE = stat.S_IRUSR | stat.S_IWUSR

# Named so that a report states which algorithm produced a signature rather than leaving
# a verifier to infer it from the signature length.
SIGNATURE_ALGORITHM = "ed25519"


class SigningError(VouchcodeError):
    """Raised when a key cannot be generated, loaded, or used."""


@dataclass(frozen=True)
class KeyPaths:
    """Where a repository's signing material lives."""

    private: Path
    public: Path

    @property
    def exists(self) -> bool:
        return self.private.is_file()


def key_paths(vouchcode_dir: Path) -> KeyPaths:
    """Return the key file locations for a repository."""
    directory = vouchcode_dir / KEYS_DIR_NAME
    return KeyPaths(
        private=directory / PRIVATE_KEY_NAME,
        public=directory / PUBLIC_KEY_NAME,
    )


def ensure_keypair(vouchcode_dir: Path) -> KeyPaths:
    """Generate a keypair if the repository does not already have one.

    Idempotent, and deliberately so. Regenerating a key on an initialized repository
    would orphan every signature already in the ledger, turning a rerun of init into
    silent, irreversible destruction of the provenance record.
    """
    paths = key_paths(vouchcode_dir)

    if paths.exists:
        return paths

    try:
        paths.private.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SigningError(
            f"cannot create key directory {paths.private.parent}: {exc}"
        ) from exc

    private_key = Ed25519PrivateKey.generate()
    _write_private_key(paths.private, private_key)
    _write_public_key(paths.public, private_key.public_key())

    return paths


def load_private_key(vouchcode_dir: Path) -> Ed25519PrivateKey:
    """Load the repository's signing key."""
    paths = key_paths(vouchcode_dir)
    if not paths.exists:
        raise SigningError(
            f"no signing key at {paths.private}. run 'vouchcode init' to create one"
        )

    try:
        data = paths.private.read_bytes()
    except OSError as exc:
        raise SigningError(f"cannot read signing key {paths.private}: {exc}") from exc

    try:
        key = serialization.load_pem_private_key(data, password=None)
    except Exception as exc:
        raise SigningError(
            f"signing key {paths.private} is not a readable unencrypted PEM key: {exc}"
        ) from exc

    if not isinstance(key, Ed25519PrivateKey):
        raise SigningError(
            f"signing key {paths.private} is not an Ed25519 key, and Vouchcode "
            "signatures are Ed25519 only"
        )

    return key


def load_public_key(vouchcode_dir: Path) -> Ed25519PublicKey:
    """Load the repository's public key from disk."""
    return load_private_key(vouchcode_dir).public_key()


def public_key_b64(vouchcode_dir: Path) -> str:
    """Return the repository's public key as base64 for embedding in a ledger.

    Raw 32-byte encoding rather than PEM, because it goes into a JSON field a third
    party reads. Any Ed25519 implementation can consume raw public key bytes; PEM would
    make a verifier parse a container format to reach the same 32 bytes.
    """
    return encode_public_key(load_public_key(vouchcode_dir))


def encode_public_key(public_key: Ed25519PublicKey) -> str:
    """Encode a public key as base64 of its raw 32 bytes."""
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def decode_public_key(encoded: str) -> Ed25519PublicKey:
    """Rebuild a public key from the base64 form stored in a ledger or report."""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise SigningError(f"public key is not valid base64: {exc}") from exc

    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise SigningError(f"public key is not a valid Ed25519 key: {exc}") from exc


def sign_payload(payload: bytes, private_key: Ed25519PrivateKey) -> str:
    """Sign the canonical bytes of an entry, returning base64.

    The signature covers the entry's canonical payload directly rather than a digest of
    it. Ed25519 hashes internally, so signing the payload is not slower, and it means a
    verifier checks the signature against the same bytes it hashes, with no opportunity
    for the two to be computed over different things.
    """
    return base64.b64encode(private_key.sign(payload)).decode("ascii")


def verify_payload(
    payload: bytes,
    signature: str,
    public_key: Ed25519PublicKey,
) -> bool:
    """Verify a base64 signature over canonical bytes.

    Returns a boolean rather than raising, because a failed signature is an expected
    verification outcome to report per entry, not an error in the verifier.
    """
    try:
        raw = base64.b64decode(signature, validate=True)
    except Exception:
        return False

    try:
        public_key.verify(raw, payload)
    except InvalidSignature:
        return False
    except Exception:
        return False

    return True


def _write_private_key(path: Path, private_key: Ed25519PrivateKey) -> None:
    """Write the private key with restrictive permissions.

    Permissions are set before the key is written where the platform allows it, so that
    the file is never briefly readable by others while it contains key material.
    """
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_KEY_MODE
        )
        try:
            os.write(descriptor, pem)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SigningError(f"cannot write signing key {path}: {exc}") from exc

    _restrict(path)


def _write_public_key(path: Path, public_key: Ed25519PublicKey) -> None:
    """Write the public key, which is not secret and needs no special permissions."""
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    try:
        path.write_bytes(pem)
    except OSError as exc:
        raise SigningError(f"cannot write public key {path}: {exc}") from exc


def _restrict(path: Path) -> None:
    """Reduce a file to owner read and write where the platform supports it.

    On Windows this is a no-op, stated rather than hidden. NTFS access control is not
    reachable through chmod, and pretending otherwise would let a report imply a
    protection the file does not have. A Windows developer relying on this should
    restrict the .vouchcode directory through the filesystem's own access controls.
    """
    if os.name == "nt":
        return

    try:
        os.chmod(path, PRIVATE_KEY_MODE)
    except OSError as exc:
        raise SigningError(f"cannot restrict permissions on {path}: {exc}") from exc
