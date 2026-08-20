"""Human-readable PDF report generation. Phase 5.

Summarizes aggregate authorship percentages and comprehension scores across the
repository's history for a reader who will not inspect the JSON, such as an academic
supervisor or a hiring manager.

Library choice, recorded here so the reasoning survives: ReportLab over WeasyPrint.
WeasyPrint depends on system GTK, Pango, and Cairo libraries, which on Windows means a
separate installer step. Section 3.1 commits to minimal deployment friction and a
single-command install, and a PDF backend that breaks 'pip install vouchcode' on the
platform the author develops on contradicts that. ReportLab is a self-contained wheel.

Per CLAUDE.md Rule 6, run /find-skill for ReportLab before implementing. A candidate was
identified during the Phase 1 skill sweep at ricable/claude-scientific-skills
(scientific-skills/reportlab) and should be evaluated then rather than installed early.

Not implemented in Phase 1.
"""

from __future__ import annotations


def render_pdf(report: object, destination: object) -> None:
    """Render the report document to a PDF file. Phase 5."""
    raise NotImplementedError("PDF report generation is Phase 5 work")
