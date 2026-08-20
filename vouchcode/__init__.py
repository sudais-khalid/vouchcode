"""Vouchcode: local-first provenance and comprehension verification for git commits.

The package is organized as the five cooperating layers described in Section 3.2 of
the research documentation:

    vouchcode.capture        Layer 1, git hook installation and commit interception.
    vouchcode.segmentation   Layer 2, AST-based diff segmentation into logical hunks.
    vouchcode.comprehension  Layer 3, deterministic question generation and scoring.
    vouchcode.ledger         Layer 4, hash-chained and Ed25519-signed local ledger.
    vouchcode.reporting      Layer 5, signed JSON and PDF report generation.

No layer makes a network call and no layer depends on an external language model.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
