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

# Project identity, defined once so the CLI, the generated PDF, and the README cannot
# drift apart on how the project or its author is named.
PROJECT_NAME = "Vouchcode"
DESCRIPTION = (
    "Local-first cryptographic provenance and comprehension verification for "
    "AI-assisted development"
)
AUTHOR_NAME = "Sudais Khalid"
AUTHOR_URL = "https://sudaiskhalid.com"
REPOSITORY_URL = "https://github.com/sudais-khalid/vouchcode"

__all__ = [
    "AUTHOR_NAME",
    "AUTHOR_URL",
    "DESCRIPTION",
    "PROJECT_NAME",
    "REPOSITORY_URL",
    "__version__",
]
