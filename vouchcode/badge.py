"""Locally generated status badge.

Produces a two-segment SVG in the familiar shields.io shape, summarizing the most recent
report. It is embedded in a README with a plain image tag and needs no service.

What this badge is, and what it is not
--------------------------------------

It is a self-reported summary, generated on the developer's own machine from their own
ledger, by a command they ran. Nothing about it is issued, checked, or attested by a
third party. A badge produced this way is exactly as trustworthy as the person who ran
the command, which is to say it is a convenience for readers rather than evidence.

The design follows from that. Two rules govern what it may say:

    It never rounds an unflattering number into a reassuring one. A repository with no
    comprehension coverage says "comprehension not evaluated", not "verified". A low
    pass rate is printed as the low number it is. The badge is allowed to be
    uninteresting.

    It carries its own generation date in the SVG title attribute. A committed badge
    goes stale the moment the ledger moves past it, and a stale badge that cannot be
    dated is silently misleading. Hovering it, or opening the file, shows when it was
    made.

Color carries the same discipline: green only where the underlying data supports it,
grey where nothing was evaluated, amber where coverage exists but is partial. There
is no state in which this badge is green because green looks better.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Shields-style segment colors.
COLOR_LABEL = "#555555"
COLOR_GREEN = "#4C9A2A"
COLOR_AMBER = "#B8860B"
COLOR_GREY = "#8A8A8A"

# Pass rate at or above which the value segment is green. Below it, amber: coverage
# exists but a meaningful share of it did not demonstrate comprehension.
GREEN_PASS_RATE = 90.0

LABEL_TEXT = "vouchcode"

# Approximate advance width per character at 11px in the DejaVu Sans metrics shields
# uses. Estimated rather than measured, because measuring would mean shipping font
# metrics for a decorative image. A few pixels of slack in the segment width is
# invisible; a wrong number in the text would not be.
_CHAR_WIDTH = 6.6
_SEGMENT_PADDING = 12.0


@dataclass(frozen=True)
class BadgeContent:
    """The text and color a report resolves to."""

    label: str
    value: str
    color: str
    generated_at: str


def badge_content(report: dict[str, Any]) -> BadgeContent:
    """Derive the badge text from a report document.

    Reads the same summary block the JSON and PDF reports render, so the badge cannot
    disagree with the artifacts it summarizes.
    """
    summary = report.get("summary") or {}
    percentages = summary.get("percentages") or {}
    comprehension = summary.get("comprehension") or {}

    ai_percent = percentages.get("ai")
    ai_text = "AI share unknown" if ai_percent is None else f"{ai_percent:g}% AI"

    pass_rate = comprehension.get("pass_rate")
    evaluated = comprehension.get("evaluated") or 0

    if pass_rate is None or evaluated == 0:
        # No commit was actually evaluated. Saying "verified" here would be the exact
        # overstatement this module exists to prevent.
        value = f"{ai_text}, comprehension not evaluated"
        color = COLOR_GREY
    elif float(pass_rate) >= GREEN_PASS_RATE:
        value = f"{ai_text}, {pass_rate:g}% comprehension"
        color = COLOR_GREEN
    else:
        value = f"{ai_text}, {pass_rate:g}% comprehension"
        color = COLOR_AMBER

    return BadgeContent(
        label=LABEL_TEXT,
        value=value,
        color=color,
        generated_at=report.get("generated_at") or _now(),
    )


def render_badge(content: BadgeContent) -> str:
    """Render the badge as a standalone SVG string."""
    label_width = _segment_width(content.label)
    value_width = _segment_width(content.value)
    total = label_width + value_width

    label_center = label_width / 2
    value_center = label_width + value_width / 2

    title = (
        f"vouchcode: {content.value}. "
        f"Locally generated from this repository's own ledger on "
        f"{content.generated_at}. Not a third-party attestation."
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total:.0f}" height="20" \
role="img" aria-label="{_escape(content.label)}: {_escape(content.value)}">
  <title>{_escape(title)}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{total:.0f}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width:.0f}" height="20" fill="{COLOR_LABEL}"/>
    <rect x="{label_width:.0f}" width="{value_width:.0f}" height="20" \
fill="{content.color}"/>
    <rect width="{total:.0f}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" \
font-family="Verdana,DejaVu Sans,Geneva,sans-serif" font-size="11">
    <text x="{label_center:.0f}" y="15" fill="#010101" fill-opacity=".3">\
{_escape(content.label)}</text>
    <text x="{label_center:.0f}" y="14">{_escape(content.label)}</text>
    <text x="{value_center:.0f}" y="15" fill="#010101" fill-opacity=".3">\
{_escape(content.value)}</text>
    <text x="{value_center:.0f}" y="14">{_escape(content.value)}</text>
  </g>
</svg>
"""


def write_badge(report: dict[str, Any], destination: Path) -> Path:
    """Generate the badge for a report and write it to disk."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_badge(badge_content(report)), encoding="utf-8")
    return destination


def _segment_width(text: str) -> float:
    """Estimate the pixel width of a badge segment for the given text."""
    return len(text) * _CHAR_WIDTH + _SEGMENT_PADDING


def _escape(text: str) -> str:
    """Escape text for inclusion in SVG markup."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _now() -> str:
    """Current time as an ISO 8601 string in UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
