"""Direct AI tool session signals.

Primary attribution source per Section 4.1 of the research documentation. Where an
assistant records which lines it generated, that record states what happened rather than
inferring it, and it takes precedence over the stylometric fallback in every case.

Reading a local file an assistant wrote is not a network call and does not violate the
zero external model dependency constraint. Querying an assistant's API for an opinion
about the code would violate it, and is permanently out of scope.

The signal format. Vouchcode does not hook into any assistant itself, because no single
mechanism works across Claude Code, Copilot, Cursor, and a developer pasting from a chat
window. What it defines instead is a file format that any integration can write, and a
reader for it. Signals live in .vouchcode/signals/ as JSON:

    {
      "tool": "claude-code",
      "recorded_at": "2026-08-20T09:00:00+00:00",
      "ranges": [
        {"path": "vouchcode/cli.py", "start_line": 42, "end_line": 96,
         "generated": true}
      ]
    }

Every range is explicit about whether it was generated, so an integration can record
human-typed regions as positive evidence too rather than leaving them to inference.
Unknown fields are ignored, so the format can grow without breaking older readers.

Honest limitation, stated because it affects how a report should be read. Signals are
written by whatever produced them and Vouchcode cannot verify their truthfulness. They
are trustworthy exactly to the extent that the integration writing them is. A developer
determined to misreport authorship can write whatever ranges they like. What Vouchcode
adds on top is the comprehension layer, which is not satisfiable by editing a JSON file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vouchcode.segmentation.hunks import SOURCE_SIGNAL

# Directory beneath .vouchcode/ where integrations drop signal files.
SIGNALS_DIR_NAME = "signals"

# A hunk is classified from a direct signal only when this much of it is covered by
# ranges of one kind. Between the two bounds it is genuinely mixed, and saying so
# is more useful than forcing a binary answer.
AI_COVERAGE_THRESHOLD = 0.9
HUMAN_COVERAGE_THRESHOLD = 0.1

STATUS_AI = "ai"
STATUS_HUMAN = "human"
STATUS_MIXED = "mixed"


@dataclass(frozen=True)
class SignalRange:
    """One contiguous line range an integration attributed to a tool or to a human."""

    path: str
    start_line: int
    end_line: int
    generated: bool
    tool: str

    def overlap(self, start: int, end: int) -> int:
        """Number of lines shared with the given inclusive range."""
        return max(0, min(self.end_line, end) - max(self.start_line, start) + 1)


@dataclass(frozen=True)
class SignalIndex:
    """All signal ranges available for a repository, grouped by file path."""

    by_path: dict[str, list[SignalRange]]

    @property
    def is_empty(self) -> bool:
        return not self.by_path


def load_signals(vouchcode_dir: Path) -> SignalIndex:
    """Read every signal file for a repository.

    A malformed signal file is skipped rather than raising. Attribution must degrade to
    the stylometric fallback when a signal is unusable, because refusing to record a
    commit over a third party's malformed JSON would make Vouchcode worse than
    useless at exactly the moment it matters.
    """
    directory = vouchcode_dir / SIGNALS_DIR_NAME
    if not directory.is_dir():
        return SignalIndex(by_path={})

    by_path: dict[str, list[SignalRange]] = {}

    for candidate in sorted(directory.glob("*.json")):
        for signal_range in _read_signal_file(candidate):
            by_path.setdefault(signal_range.path, []).append(signal_range)

    return SignalIndex(by_path=by_path)


def classify_range(
    index: SignalIndex,
    path: str,
    start_line: int,
    end_line: int,
) -> dict[str, Any] | None:
    """Classify a line range from direct signals, or return None when none apply.

    Returning None is meaningful and distinct from returning a human classification: it
    says no integration reported on this code at all, which is what sends the hunk
    to the stylometric fallback. A human classification says an integration positively
    asserted
    that a person wrote it.
    """
    ranges = index.by_path.get(_normalize_path(path))
    if not ranges:
        return None

    total_lines = max(1, end_line - start_line + 1)

    generated_lines = 0
    human_lines = 0
    contributing: set[str] = set()

    for signal_range in ranges:
        overlap = signal_range.overlap(start_line, end_line)
        if overlap <= 0:
            continue
        contributing.add(signal_range.tool)
        if signal_range.generated:
            generated_lines += overlap
        else:
            human_lines += overlap

    if not contributing:
        return None

    # Clamped because overlapping ranges from more than one integration can otherwise
    # sum past the size of the hunk.
    coverage = min(1.0, generated_lines / total_lines)

    if coverage >= AI_COVERAGE_THRESHOLD:
        status = STATUS_AI
    elif coverage <= HUMAN_COVERAGE_THRESHOLD:
        status = STATUS_HUMAN
    else:
        status = STATUS_MIXED

    return {
        "status": status,
        "source": SOURCE_SIGNAL,
        # A direct signal is a report of what happened, not an inference from it, so it
        # carries full confidence in the classification. Whether the report itself is
        # truthful is a separate question, addressed in this module's docstring.
        "confidence": 1.0,
        "detail": {
            "ai_line_coverage": round(coverage, 4),
            "generated_lines": generated_lines,
            "human_lines": human_lines,
            "hunk_lines": total_lines,
            "tools": sorted(contributing),
        },
    }


def _read_signal_file(path: Path) -> list[SignalRange]:
    """Parse one signal file, returning an empty list if it is unusable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(data, dict):
        return []

    tool = str(data.get("tool") or "unknown")
    raw_ranges = data.get("ranges")
    if not isinstance(raw_ranges, list):
        return []

    parsed: list[SignalRange] = []
    for item in raw_ranges:
        signal_range = _parse_range(item, tool)
        if signal_range is not None:
            parsed.append(signal_range)
    return parsed


def _parse_range(item: Any, tool: str) -> SignalRange | None:
    """Parse one range entry, returning None when it is malformed."""
    if not isinstance(item, dict):
        return None

    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None

    try:
        start = int(item["start_line"])
        end = int(item["end_line"])
    except (KeyError, TypeError, ValueError):
        return None

    if start < 1 or end < start:
        return None

    return SignalRange(
        path=_normalize_path(raw_path),
        start_line=start,
        end_line=end,
        generated=bool(item.get("generated", True)),
        tool=tool,
    )


def _normalize_path(path: str) -> str:
    """Normalize a repository-relative path for comparison.

    Signal files may be written on a different platform than the one reading them, so
    separators are unified. Git itself always reports forward slashes.
    """
    return path.replace("\\", "/").lstrip("./")
