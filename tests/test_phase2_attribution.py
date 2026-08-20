"""Phase 2 attribution tests.

Two properties matter more than raw accuracy here, and both are asserted directly rather
than inferred from behavior:

    A direct tool signal always beats the stylometric fallback. Evidence outranks
    inference, and a report that silently preferred a guess over a record would be
    misleading in exactly the situation the record exists for.

    The stylometric path never produces a binary verdict. Section 6.2 requires an
    explicit confidence on every heuristic result, and requires the path to decline
    rather than guess when it lacks the evidence to say anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from vouchcode.capture import signals, stylometry
from vouchcode.capture.attribution import attribute_hunks, summarize
from vouchcode.segmentation.hunks import Hunk

SAMPLE = """def handler(payload):
    if not payload:
        return None
    cleaned = payload.strip()
    collected = []
    for part in cleaned.split(","):
        if part:
            collected.append(part.lower())
    return collected
"""


def _hunk(path: str = "m.py", start: int = 1, end: int = 9) -> Hunk:
    return Hunk(
        path=path,
        qualname="handler",
        kind="function",
        change="added",
        lineno=start,
        end_lineno=end,
        source=SAMPLE,
    )


def _write_signal(root: Path, payload: dict) -> Path:
    directory = root / "signals"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "session.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Direct tool signals
# ---------------------------------------------------------------------------


def test_signal_classifies_a_fully_covered_hunk_as_ai(tmp_path: Path) -> None:
    """A hunk inside a generated range is AI-attributed with full confidence."""
    _write_signal(
        tmp_path,
        {
            "tool": "claude-code",
            "ranges": [
                {"path": "m.py", "start_line": 1, "end_line": 20, "generated": True}
            ],
        },
    )

    index = signals.load_signals(tmp_path)
    result = signals.classify_range(index, "m.py", 1, 9)

    assert result is not None
    assert result["status"] == "ai"
    assert result["source"] == "tool_signal"
    assert result["confidence"] == 1.0
    assert result["detail"]["tools"] == ["claude-code"]


def test_signal_reports_partial_coverage_as_mixed(tmp_path: Path) -> None:
    """A hunk half covered by a generated range is mixed, not forced to one side."""
    _write_signal(
        tmp_path,
        {
            "tool": "copilot",
            "ranges": [
                {"path": "m.py", "start_line": 1, "end_line": 5, "generated": True}
            ],
        },
    )

    index = signals.load_signals(tmp_path)
    result = signals.classify_range(index, "m.py", 1, 10)

    assert result is not None
    assert result["status"] == "mixed"
    assert 0.4 < result["detail"]["ai_line_coverage"] < 0.6


def test_no_signal_returns_none_rather_than_human(tmp_path: Path) -> None:
    """Absence of a signal is not evidence of human authorship.

    Returning None sends the hunk to the fallback. A human classification would
    assert something no one reported, and would let every unmonitored commit look
    positively verified as hand-written.
    """
    index = signals.load_signals(tmp_path)

    assert index.is_empty
    assert signals.classify_range(index, "m.py", 1, 9) is None


def test_malformed_signal_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A third party's broken JSON must not stop a commit from being recorded."""
    directory = tmp_path / "signals"
    directory.mkdir(parents=True)
    (directory / "broken.json").write_text("{not json", encoding="utf-8")
    (directory / "good.json").write_text(
        json.dumps(
            {
                "tool": "claude-code",
                "ranges": [
                    {"path": "m.py", "start_line": 1, "end_line": 9, "generated": True}
                ],
            }
        ),
        encoding="utf-8",
    )

    index = signals.load_signals(tmp_path)
    result = signals.classify_range(index, "m.py", 1, 9)

    assert result is not None
    assert result["status"] == "ai"


def test_signal_paths_are_platform_normalized(tmp_path: Path) -> None:
    """A signal written on Windows must match the path git reports."""
    _write_signal(
        tmp_path,
        {
            "tool": "cursor",
            "ranges": [
                {
                    "path": "pkg\\mod.py",
                    "start_line": 1,
                    "end_line": 9,
                    "generated": True,
                }
            ],
        },
    )

    index = signals.load_signals(tmp_path)

    assert signals.classify_range(index, "pkg/mod.py", 1, 9) is not None


# ---------------------------------------------------------------------------
# Stylometry: never a bare verdict
# ---------------------------------------------------------------------------


def test_stylometry_declines_without_enough_baseline() -> None:
    """Too few samples means no classification, with the reason stated."""
    baseline = stylometry.build_baseline([SAMPLE, SAMPLE])
    result = stylometry.score_against_baseline(SAMPLE, baseline)

    assert result["status"] == "unclassified"
    assert result["confidence"] is None
    assert "fewer than" in result["detail"]["reason"]


def test_stylometry_declines_on_a_hunk_too_small_to_measure() -> None:
    """A three line function has no measurable style."""
    baseline = stylometry.build_baseline([SAMPLE] * stylometry.MIN_BASELINE_SAMPLES)
    result = stylometry.score_against_baseline("def f():\n    return 1\n", baseline)

    assert result["status"] == "unclassified"
    assert result["confidence"] is None
    assert "required to measure style" in result["detail"]["reason"]


def test_stylometry_confidence_is_always_bounded_below_certainty() -> None:
    """No heuristic result may claim certainty, however extreme the divergence."""
    sources = [SAMPLE.replace("handler", f"handler_{i}") for i in range(20)]
    baseline = stylometry.build_baseline(sources)

    divergent = (
        "def compute_normalized_aggregate_statistics(\n"
        "    input_collection, strategy_name\n"
        "):\n"
        '    """Compute aggregates."""\n'
        "    accumulated_output_mapping = {}\n"
        "    for record_key, record_value in input_collection.items():\n"
        "        accumulated_output_mapping[record_key] = record_value / 100\n"
        "    return accumulated_output_mapping\n"
    )

    for source in (SAMPLE, divergent):
        result = stylometry.score_against_baseline(source, baseline)
        if result["confidence"] is None:
            continue
        assert result["confidence"] <= stylometry.MAX_CONFIDENCE
        assert result["confidence"] < 1.0


def test_stylometry_result_always_states_it_is_an_inference() -> None:
    """Every classification carries the note that it is not an AI detection.

    A report that presented a style measurement as detection would be overclaiming, and
    the disclaimer belongs in the data rather than in whatever renders it.
    """
    sources = [SAMPLE.replace("handler", f"handler_{i}") for i in range(20)]
    baseline = stylometry.build_baseline(sources)
    result = stylometry.score_against_baseline(SAMPLE, baseline)

    assert result["source"] == "stylometry"
    assert "not a detection" in result["detail"]["note"]
    assert result["detail"]["baseline_samples"] >= stylometry.MIN_BASELINE_SAMPLES


# ---------------------------------------------------------------------------
# Precedence and rollup
# ---------------------------------------------------------------------------


def test_direct_signal_takes_precedence_over_stylometry(tmp_path: Path) -> None:
    """Evidence outranks inference, even when a usable baseline exists."""
    _write_signal(
        tmp_path,
        {
            "tool": "claude-code",
            "ranges": [
                {"path": "m.py", "start_line": 1, "end_line": 20, "generated": True}
            ],
        },
    )

    hunk = _hunk()
    baseline_sources = [SAMPLE.replace("handler", f"handler_{i}") for i in range(20)]
    attribute_hunks([hunk], tmp_path, baseline_sources)

    assert hunk.attribution["source"] == "tool_signal"
    assert hunk.attribution["status"] == "ai"


def test_unchanged_hunks_are_never_sent_to_attribution(tmp_path: Path) -> None:
    """A hunk the AST proved unchanged keeps its structural attribution untouched."""
    renamed = Hunk(
        path="m.py",
        qualname="new_name",
        kind="function",
        change="renamed",
        lineno=1,
        end_lineno=9,
        previous_qualname="old_name",
        attribution={"status": "unchanged", "source": "structural", "confidence": 1.0},
        source=SAMPLE,
    )

    attribute_hunks([renamed], tmp_path, [SAMPLE] * 20)

    assert renamed.attribution["source"] == "structural"
    assert renamed.attribution["confidence"] == 1.0


def test_commit_with_both_kinds_of_hunk_rolls_up_as_mixed() -> None:
    """A commit containing AI and human logic is mixed, not collapsed to one label."""
    ai_hunk = _hunk()
    ai_hunk.attribution = {"status": "ai", "source": "tool_signal", "confidence": 1.0}

    human_hunk = _hunk()
    human_hunk.attribution = {
        "status": "human",
        "source": "stylometry",
        "confidence": 0.4,
    }

    summary = summarize([ai_hunk, human_hunk])

    assert summary.status == "mixed"
    assert summary.ai_hunks == 1
    assert summary.human_hunks == 1
    assert 0.0 < summary.ai_line_share < 1.0


def test_commit_summary_reports_no_aggregate_confidence() -> None:
    """Confidence lives on hunks, where it means something specific.

    Averaging a direct signal's certainty with a heuristic's guess would produce a
    number
    describing neither, so the commit level deliberately reports none.
    """
    ai_hunk = _hunk()
    ai_hunk.attribution = {"status": "ai", "source": "tool_signal", "confidence": 1.0}

    assert summarize([ai_hunk]).to_dict()["confidence"] is None


def test_pure_rename_commit_rolls_up_as_unchanged() -> None:
    """A commit that only renames things authored nothing new, and says so."""
    renamed = _hunk()
    renamed.change = "renamed"
    renamed.attribution = {
        "status": "unchanged",
        "source": "structural",
        "confidence": 1.0,
    }

    summary = summarize([renamed])

    assert summary.status == "unchanged"
    assert summary.ai_line_share == 0.0
    assert summary.unchanged_hunks == 1
