"""Fingerprint identity: what a stored AST fingerprint means, and when it is comparable.

Vouchcode's segmentation layer identifies definitions by hashing an ast.dump of their
normalized syntax tree. That output is not a stable format across Python versions. The
ast module gains node types and changes field names between releases, and when it does,
the same source file fingerprints differently under a different interpreter.

Left unrecorded, that produces a specific and bad failure. A ledger written under one
interpreter and verified under another would show fingerprints that do not match, and a
verifier has no way to tell an interpreter difference from a tampered entry. It would
report tampering that never happened, or, worse, a future comparison would treat two
genuinely different definitions as matching because they happened to collide.

This module does not fix that. Making fingerprints portable across interpreter
versions would mean writing and maintaining a version-independent serialization of the
Python AST, a much larger commitment than the problem warrants. What it does instead is
make the ledger honest about what a fingerprint means, by recording the conditions under
which it was computed. A verifier can then say "these were computed under different
conditions and cannot be compared" rather than guessing.

Three components are recorded, and each answers a different question:

    algorithm   Vouchcode's own fingerprinting rules. Bumped by hand when the
                normalization changes, for example if alpha-renaming starts covering a
                new binding form. Two fingerprints from different algorithm versions are
                not comparable even on the same interpreter.
    python      The interpreter's major and minor version, which is the coarse signal.
                Patch releases are excluded deliberately: they do not change the AST
                shape, and including them would flag every routine upgrade as
                non-comparable, training a reader to ignore the flag.
    ast_signature
                A hash of ast.dump run over a probe module exercising every construct
                the fingerprinter relies on. This is the precise signal. If a release
                changes how any of those constructs serialize, the signature changes,
                even between two releases sharing a major and minor version. If nothing
                relevant changed, it stays the same, and two interpreters remain
                comparable despite differing elsewhere.

The signature is the load-bearing part. The Python version alone would be a proxy; the
probe measures the thing that actually matters.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from typing import Any

# Vouchcode's own fingerprinting rules. Bump this by hand whenever the normalization in
# vouchcode.segmentation.astdiff changes in a way that alters fingerprint output for
# unchanged source. A change here makes every prior fingerprint non-comparable, which is
# correct: they describe the same code under different rules.
FINGERPRINT_ALGORITHM_VERSION = 1

# Length of a stored fingerprint in hexadecimal characters. Sixteen characters is 64
# bits of a SHA-256 digest, far beyond the collision pressure of the few thousand
# definitions a repository holds, and short enough to read in a ledger.
FINGERPRINT_LENGTH = 16

# Source exercising every construct the fingerprinter depends on. Its serialized form is
# the behavior signature. Extend this whenever the fingerprinter starts relying on a
# construct not represented here, otherwise a change to that construct would slip
# through unflagged.
_PROBE_SOURCE = '''
import os
from typing import Any


CONSTANT = 1
TEXT = "value"
COLLECTION = [1, 2.5, True, None]
MAPPING = {"key": "value"}


class Probe(os.PathLike):
    """Docstring."""

    attribute: int = 0

    def method(self, first, second=1, *args, keyword=None, **kwargs) -> Any:
        total = first + second
        for index, item in enumerate(args):
            if item is None or index > 2:
                continue
            total += item
        while total > 0:
            total -= 1
        else:
            total = 0
        try:
            with open("path") as handle:
                data = handle.read()
        except (OSError, ValueError) as exc:
            raise RuntimeError("failed") from exc
        finally:
            data = ""
        squares = [value**2 for value in range(3) if value]
        lookup = {key: value for key, value in MAPPING.items()}
        if (found := lookup.get("key")) is not None:
            return found
        return lambda argument: argument if squares else data


async def coroutine(items):
    async for item in items:
        yield item
'''


def compute_fingerprint(dump: str) -> str:
    """Hash a normalized ast.dump into the short form stored in the ledger."""
    digest = hashlib.sha256(dump.encode("utf-8")).hexdigest()
    return digest[:FINGERPRINT_LENGTH]


def ast_behavior_signature() -> str:
    """Return a hash of how this interpreter serializes the probe module.

    Computed rather than hardcoded, so that it tracks the running interpreter instead of
    a table someone has to remember to update.
    """
    tree = ast.parse(_PROBE_SOURCE)
    dump = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def python_tag() -> str:
    """Return the interpreter's major and minor version.

    Patch level is excluded on purpose. It does not affect AST shape, and including it
    would mark every routine upgrade non-comparable, which would make the flag noise.
    """
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def current_version() -> dict[str, Any]:
    """Return the fingerprint version tag for the running interpreter."""
    return {
        "algorithm": FINGERPRINT_ALGORITHM_VERSION,
        "python": python_tag(),
        "ast_signature": ast_behavior_signature(),
    }


def is_comparable(recorded: dict[str, Any] | None) -> bool:
    """Whether fingerprints recorded under the given tag can be compared to fresh ones.

    A missing tag is not comparable. An entry written before this field existed carries
    no statement about the conditions of its computation, and assuming they match the
    current ones would be exactly the silent guess this module exists to prevent.
    """
    if not recorded:
        return False

    current = current_version()
    return all(
        recorded.get(key) == current[key]
        for key in ("algorithm", "python", "ast_signature")
    )


def describe_mismatch(recorded: dict[str, Any] | None) -> str:
    """Explain why a recorded tag is not comparable with the current interpreter.

    Phrased for a report reader who is holding a verification result and needs to know
    whether to worry. The answer is that they should not, and why.
    """
    if not recorded:
        return (
            "entry records no fingerprint version, so the conditions under which its "
            "fingerprints were computed are unknown"
        )

    current = current_version()
    differences = [
        f"{key} recorded {recorded.get(key)!r}, current {current[key]!r}"
        for key in ("algorithm", "python", "ast_signature")
        if recorded.get(key) != current[key]
    ]

    if not differences:
        return ""

    return "fingerprints were computed under different conditions: " + "; ".join(
        differences
    )
