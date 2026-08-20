"""Human-readable PDF report generation.

Summarizes authorship and comprehension across a range for a reader who will not open
the JSON: an academic supervisor, a hiring manager, a reviewer. Built with ReportLab's
Platypus flowable API.

Library choice, recorded so the reasoning survives: ReportLab over WeasyPrint.
WeasyPrint depends on system GTK, Pango, and Cairo libraries, which on Windows means a
separate installer step. Section 3.1 commits to minimal deployment friction and a
single-command install, and a PDF backend that breaks 'pip install vouchcode' on the
platform the author develops on contradicts that. ReportLab is a self-contained wheel.

Layout priorities, in order, because a report nobody reads carefully is a report that
provides no assurance:

    The key fingerprint comes first, above the figures. It is the one thing on the page
    a verifier is asked to act on, and burying it under statistics would guarantee it
    goes unchecked.

    The verification notice sits directly beneath it, stating what a signature does and
    does not prove. A reader who does not know the limits of the artifact will assume it
    proves more than it does.

    Figures that rest on inference are labeled as such wherever they appear. A number
    from stylometry and a number from a tool signal are not the same kind of claim, and
    a table that presents them identically is misleading regardless of its accuracy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Ink used for structural elements. Restrained on purpose: a provenance report is
# evidence, and heavy color reads as marketing.
_RULE = colors.HexColor("#333333")
_HEADER_BG = colors.HexColor("#e8e8e8")
_ALT_ROW = colors.HexColor("#f6f6f6")
_MUTED = colors.HexColor("#555555")

# Commits listed individually before the table is truncated. A supervisor reviewing a
# final-year project wants the detail; nobody wants four hundred pages of it.
MAX_LISTED_COMMITS = 40


def render_pdf(report: dict[str, Any], destination: Path) -> Path:
    """Render a JSON report document to a PDF file."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    document = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Vouchcode provenance report",
        author=str(report.get("repository") or "vouchcode"),
    )

    styles = _styles()
    story: list[Any] = []

    story.extend(_header(report, styles))
    story.extend(_key_block(report, styles))
    story.extend(_authorship(report, styles))
    story.extend(_attribution_sources(report, styles))
    story.extend(_comprehension(report, styles))
    story.extend(_commit_table(report, styles))

    document.build(story)
    return destination


def _styles() -> dict[str, ParagraphStyle]:
    """Build the paragraph styles used across the report."""
    base = getSampleStyleSheet()

    return {
        "title": ParagraphStyle(
            "VouchTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            spaceAfter=2 * mm,
            alignment=TA_LEFT,
        ),
        "meta": ParagraphStyle(
            "VouchMeta",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            textColor=_MUTED,
            spaceAfter=1 * mm,
        ),
        "heading": ParagraphStyle(
            "VouchHeading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            spaceBefore=6 * mm,
            spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "VouchBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            spaceAfter=2 * mm,
        ),
        # Monospaced and oversized, because this is the string a person compares
        # character by character against another copy.
        "fingerprint": ParagraphStyle(
            "VouchFingerprint",
            parent=base["BodyText"],
            fontName="Courier-Bold",
            fontSize=14,
            leading=18,
            spaceBefore=1 * mm,
            spaceAfter=2 * mm,
        ),
        "notice": ParagraphStyle(
            "VouchNotice",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=_MUTED,
        ),
    }


def _header(report: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    """Title and provenance of the report itself."""
    return [
        Paragraph("Vouchcode provenance report", styles["title"]),
        Paragraph(f"Repository: {_escape(report.get('repository'))}", styles["meta"]),
        Paragraph(f"Range: {_escape(report.get('commit_range'))}", styles["meta"]),
        Paragraph(f"Generated: {_escape(report.get('generated_at'))}", styles["meta"]),
        Spacer(1, 4 * mm),
    ]


def _key_block(report: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    """The signing key fingerprint and what checking it does and does not establish.

    Kept together as one flowable so a page break can never separate the fingerprint
    from the explanation of why it matters."""
    key = report.get("signing_key") or {}

    block = [
        Paragraph("Signing key fingerprint", styles["heading"]),
        Paragraph(_escape(key.get("fingerprint")), styles["fingerprint"]),
        Paragraph(
            f"Algorithm: {_escape(key.get('algorithm'))}. "
            f"Full digest: {_escape(key.get('digest'))}",
            styles["meta"],
        ),
        Spacer(1, 2 * mm),
        Paragraph(_escape(report.get("verification_notice")), styles["notice"]),
    ]
    return [KeepTogether(block), Spacer(1, 3 * mm)]


def _authorship(report: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    """Aggregate authorship figures for the range."""
    summary = report.get("summary") or {}
    percentages = summary.get("percentages") or {}
    lines = summary.get("lines") or {}

    rows = [
        ["Measure", "Value"],
        ["Commits in range", str(summary.get("commits", 0))],
        ["Merge commits (excluded from scoring)", str(summary.get("merges", 0))],
        [
            "Retroactively scanned commits",
            str(summary.get("retroactive_commits", 0)),
        ],
        ["Hunks analyzed", str(summary.get("hunks_total", 0))],
        [
            "Hunks carrying new logic",
            str(summary.get("hunks_carrying_new_logic", 0)),
        ],
        ["Lines of new logic", str(lines.get("attributed", 0))],
        ["AI-attributed", f"{percentages.get('ai', 0.0)} percent"],
        ["Human-attributed", f"{percentages.get('human', 0.0)} percent"],
        ["Unclassified", f"{percentages.get('unclassified', 0.0)} percent"],
    ]

    return [
        Paragraph("Authorship", styles["heading"]),
        Paragraph(
            "Percentages are over lines of changed logic. Renamed and moved code is "
            "excluded from the denominator, because the syntax tree proves its logic "
            "did not change and it was not authored in this range.",
            styles["body"],
        ),
        _table(rows, [110 * mm, 50 * mm]),
    ]


def _attribution_sources(
    report: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """How each attribution was reached, which governs how much it is worth."""
    summary = report.get("summary") or {}
    sources = summary.get("attribution_sources") or {}

    meanings = {
        "structural": "Certainty. The syntax tree proves the logic did not change.",
        "tool_signal": "Evidence. An assistant reported which lines it generated.",
        "stylometry": "Inference. The code diverges from the author's prior style.",
        "none": "No attribution was recorded for these hunks.",
    }

    rows: list[list[str]] = [["Source", "Hunks", "What this means"]]
    for source, count in sorted(sources.items()):
        rows.append(
            [
                source,
                str(count),
                meanings.get(source, "Unrecognized attribution source."),
            ]
        )

    if len(rows) == 1:
        rows.append(["none", "0", "No hunks were attributed in this range."])

    return [
        Paragraph("Attribution sources", styles["heading"]),
        Paragraph(
            "These are not interchangeable. A structural result is proved, a tool "
            "signal is reported, and a stylometric result is inferred and carries an "
            "explicit confidence below certainty. Read any percentage above alongside "
            "how much of it rests on inference.",
            styles["body"],
        ),
        _table(rows, [32 * mm, 18 * mm, 110 * mm], wrap_last=True, styles=styles),
    ]


def _comprehension(
    report: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """Comprehension outcomes, with unevaluated commits reported rather than hidden."""
    summary = report.get("summary") or {}
    comprehension = summary.get("comprehension") or {}
    pass_rate = comprehension.get("pass_rate")

    rows = [
        ["Outcome", "Commits"],
        ["Evaluated", str(comprehension.get("evaluated", 0))],
        ["Passed", str(comprehension.get("passed", 0))],
        ["Failed", str(comprehension.get("failed", 0))],
    ]

    for status, count in sorted((comprehension.get("by_status") or {}).items()):
        if status in {"passed", "failed"}:
            continue
        rows.append([f"Not evaluated: {status}", str(count)])

    rate_text = (
        "No commit in this range was evaluated, so there is no pass rate."
        if pass_rate is None
        else f"Pass rate across evaluated commits: {pass_rate} percent."
    )

    return [
        Paragraph("Comprehension verification", styles["heading"]),
        Paragraph(
            rate_text
            + " The rate is over evaluated commits only. Commits excluded by decision, "
            "such as merges, and commits skipped because no terminal was attached, are "
            "listed separately and are never counted as passes.",
            styles["body"],
        ),
        _table(rows, [110 * mm, 50 * mm]),
    ]


def _commit_table(
    report: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """Per-commit detail, truncated with an explicit note rather than silently."""
    entries = report.get("entries") or []
    if not entries:
        return []

    shown = entries[:MAX_LISTED_COMMITS]

    rows: list[list[str]] = [
        ["Commit", "Type", "Capture", "Attribution", "Comprehension"]
    ]
    for entry in shown:
        attribution = entry.get("attribution") or {}
        comprehension = entry.get("comprehension") or {}
        rows.append(
            [
                str(entry.get("commit", ""))[:10],
                str(entry.get("type", "")),
                str(entry.get("capture", "live")),
                str(attribution.get("status", "")),
                str(comprehension.get("status", "")),
            ]
        )

    story: list[Any] = [
        PageBreak(),
        Paragraph("Commits", styles["heading"]),
        _table(rows, [26 * mm, 18 * mm, 30 * mm, 30 * mm, 56 * mm], repeat_header=True),
    ]

    if len(entries) > MAX_LISTED_COMMITS:
        story.append(
            Paragraph(
                f"Showing the first {MAX_LISTED_COMMITS} of {len(entries)} commits. "
                "The signed JSON report accompanying this document contains every "
                "entry.",
                styles["meta"],
            )
        )

    return story


def _table(
    rows: list[list[str]],
    widths: list[float],
    wrap_last: bool = False,
    repeat_header: bool = False,
    styles: dict[str, ParagraphStyle] | None = None,
) -> Table:
    """Build a styled table from string rows.

    wrap_last converts the final column to Paragraphs so that long explanatory text
    wraps instead of overflowing the cell. ReportLab does not wrap bare strings in table
    cells, which is the single easiest way to produce a PDF that looks broken."""
    data: list[list[Any]] = [list(row) for row in rows]

    if wrap_last and styles is not None:
        for index, row in enumerate(data):
            if index == 0:
                continue
            row[-1] = Paragraph(_escape(row[-1]), styles["body"])

    table = Table(
        data,
        colWidths=widths,
        repeatRows=1 if repeat_header else 0,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ALT_ROW]),
                ("GRID", (0, 0), (-1, -1), 0.4, _RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _escape(value: Any) -> str:
    """Render a value as text safe for ReportLab's inline markup.

    Paragraph interprets a small set of XML-like tags, so an ampersand or angle bracket
    arriving from a repository path or an author name would either vanish or raise. This
    is presentation escaping, not a security boundary.
    """
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
