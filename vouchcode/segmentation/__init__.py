"""Layer 2, Segmentation. Phase 2.

Parses the pre-commit and post-commit versions of each modified Python file into
abstract syntax trees and isolates changed function and block definitions, so that
attribution aligns with logical units of code rather than raw line ranges. Section 4.2
of the research documentation gives the rationale: line diffs misattribute formatting
changes and fragment a single logical edit across several ranges.

Known hazard to address during implementation: a renamed function is the case where
naive AST diffing fails, reporting a whole-body rewrite where only the identifier
changed. The phase's test suite must cover it explicitly.
"""
