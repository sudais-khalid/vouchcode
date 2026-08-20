"""Stylometric attribution fallback.

Used when no direct tool signal covers a hunk, which is the normal case when a developer
pastes code from a chat interface. Compares statistical characteristics of the code
against a baseline built from the developer's own prior work in the repository, per
Section 4.1 of the research documentation.

What this is, and what it is not. This measures whether a hunk looks like the rest
of the repository's code. It does not detect AI. No local statistic can, and claiming
otherwise
would be the kind of overreach that makes a provenance report worthless the first time
someone checks it. What it produces is a divergence measurement and an explicit
confidence, and Section 6.2 requires that this path never present a binary verdict.

Three consequences of that honesty are built into the interface rather than left to the
caller's discretion:

    Below MIN_BASELINE_SAMPLES prior definitions, it refuses to classify at all and
    returns an unclassified result with a stated reason. A baseline of three functions
    describes nothing.

    Below MIN_HUNK_NODES, it refuses to classify a hunk as too small to measure. A three
    line function has no measurable style.

    Confidence is always returned, is never 1.0, and is capped at MAX_CONFIDENCE. A
    heuristic that reports certainty is lying.

Metrics. Three families, chosen because Section 4.1 names them and because each is
computable from the AST and raw source with no external dependency:

    naming        mean identifier length, and the Shannon entropy of identifier
                  character distribution. Generated code tends toward longer, more
                  uniform, more descriptive identifiers than hand-typed code.
    commenting    comment lines and docstrings as a fraction of the whole.
    regularity    how uniform the structure is: variation in line length and in
                  statement nesting depth.

Every metric is a ratio or a bounded average so that a large hunk and a small one are
comparable.
"""

from __future__ import annotations

import ast
import math
import tokenize
from collections import Counter
from dataclasses import dataclass
from io import StringIO
from typing import Any

from vouchcode.segmentation.astdiff import parse_module
from vouchcode.segmentation.hunks import SOURCE_STYLOMETRY

# A baseline built from fewer definitions than this describes nothing, and scoring
# against it would manufacture confidence out of noise.
MIN_BASELINE_SAMPLES = 12

# A hunk smaller than this has no measurable style. Reporting a divergence for a three
# line function would be numerology.
MIN_HUNK_NODES = 20

# Ceiling on reported confidence. This path is an inference from statistics, and no
# amount of divergence makes it a certainty. The cap is what stops a report from
# presenting a heuristic as evidence.
MAX_CONFIDENCE = 0.75

# Divergence above which a hunk is called stylistically anomalous. Expressed in standard
# deviations from the baseline mean, averaged across metrics.
DIVERGENCE_THRESHOLD = 1.5

STATUS_AI = "ai"
STATUS_HUMAN = "human"
STATUS_UNCLASSIFIED = "unclassified"

_METRIC_NAMES = (
    "mean_identifier_length",
    "identifier_entropy",
    "comment_density",
    "docstring_ratio",
    "line_length_variation",
    "nesting_variation",
)


@dataclass(frozen=True)
class Metrics:
    """Stylometric measurements of one source fragment."""

    values: dict[str, float]
    node_count: int

    def vector(self) -> list[float]:
        return [self.values.get(name, 0.0) for name in _METRIC_NAMES]


@dataclass(frozen=True)
class Baseline:
    """Distribution of each metric across a developer's prior work."""

    means: dict[str, float]
    deviations: dict[str, float]
    sample_count: int

    @property
    def is_usable(self) -> bool:
        return self.sample_count >= MIN_BASELINE_SAMPLES


def measure(source: str) -> Metrics | None:
    """Measure one source fragment, returning None when it cannot be parsed.

    The fragment is dedented before parsing so that a method body extracted from a class
    is measurable on its own.
    """
    text = _dedent(source)
    if not text.strip():
        return None

    try:
        tree = parse_module(text, "<hunk>")
    except Exception:
        return None

    identifiers = _identifiers(tree)
    lines = [line for line in text.splitlines() if line.strip()]
    node_count = sum(1 for _ in ast.walk(tree))

    return Metrics(
        values={
            "mean_identifier_length": _mean([len(name) for name in identifiers]),
            "identifier_entropy": _entropy("".join(identifiers)),
            "comment_density": _comment_density(text, len(lines)),
            "docstring_ratio": _docstring_ratio(tree),
            "line_length_variation": _coefficient_of_variation(
                [len(line) for line in lines]
            ),
            "nesting_variation": _coefficient_of_variation(_nesting_depths(tree)),
        },
        node_count=node_count,
    )


def build_baseline(sources: list[str]) -> Baseline:
    """Build a baseline from a developer's prior source fragments.

    Each fragment contributes one sample. Fragments that fail to parse or are too small
    to measure are skipped rather than counted, so sample_count reflects usable evidence
    rather than files looked at.
    """
    samples: list[Metrics] = []
    for source in sources:
        metrics = measure(source)
        if metrics is not None and metrics.node_count >= MIN_HUNK_NODES:
            samples.append(metrics)

    if not samples:
        return Baseline(means={}, deviations={}, sample_count=0)

    means: dict[str, float] = {}
    deviations: dict[str, float] = {}

    for index, name in enumerate(_METRIC_NAMES):
        column = [sample.vector()[index] for sample in samples]
        mean = _mean(column)
        means[name] = mean
        deviations[name] = _stdev(column, mean)

    return Baseline(means=means, deviations=deviations, sample_count=len(samples))


def score_against_baseline(source: str, baseline: Baseline) -> dict[str, Any]:
    """Score a hunk against a baseline, always returning an explicit confidence.

    Never returns a bare verdict. Every result carries the confidence, the measured
    divergence, and, when it declines to classify, the reason it declined.
    """
    if not baseline.is_usable:
        return _unclassified(
            f"baseline has {baseline.sample_count} samples, "
            f"fewer than the {MIN_BASELINE_SAMPLES} required to score against"
        )

    metrics = measure(source)
    if metrics is None:
        return _unclassified("hunk source could not be parsed for measurement")

    if metrics.node_count < MIN_HUNK_NODES:
        return _unclassified(
            f"hunk has {metrics.node_count} nodes, "
            f"fewer than the {MIN_HUNK_NODES} required to measure style"
        )

    per_metric: dict[str, float] = {}
    for name in _METRIC_NAMES:
        deviation = baseline.deviations.get(name, 0.0)
        difference = abs(metrics.values.get(name, 0.0) - baseline.means.get(name, 0.0))
        # A metric with no spread across the baseline carries no information, so it
        # contributes zero rather than an infinite z-score.
        per_metric[name] = 0.0 if deviation <= 1e-9 else difference / deviation

    divergence = _mean(list(per_metric.values()))
    status = STATUS_AI if divergence >= DIVERGENCE_THRESHOLD else STATUS_HUMAN

    return {
        "status": status,
        "source": SOURCE_STYLOMETRY,
        "confidence": _confidence(divergence, baseline.sample_count),
        "detail": {
            "divergence": round(divergence, 4),
            "threshold": DIVERGENCE_THRESHOLD,
            "baseline_samples": baseline.sample_count,
            "per_metric_divergence": {
                name: round(value, 4) for name, value in per_metric.items()
            },
            # Repeated in every heuristic result so that a report cannot present this
            # classification without also presenting its nature.
            "note": (
                "heuristic inference from code style, not a detection of AI authorship"
            ),
        },
    }


def _confidence(divergence: float, sample_count: int) -> float:
    """Map divergence and baseline size to a bounded confidence.

    Two things move confidence. Distance from the threshold in either direction
    makes the classification more decisive, and a larger baseline makes the comparison
    more
    trustworthy. Both are capped, and their product can never reach certainty.
    """
    # Saturating in the distance from the threshold: a divergence of 0 or of 3 standard
    # deviations is a clear call, one sitting exactly on the threshold is a coin flip.
    decisiveness = min(
        1.0, abs(divergence - DIVERGENCE_THRESHOLD) / DIVERGENCE_THRESHOLD
    )

    # Saturating in baseline size, reaching its ceiling at four times the minimum.
    evidence = min(1.0, sample_count / (MIN_BASELINE_SAMPLES * 4))

    # Floored so that a result never claims to be worthless while still asserting a
    # status, and capped so that it never claims certainty.
    return round(max(0.1, MAX_CONFIDENCE * decisiveness * evidence), 4)


def _unclassified(reason: str) -> dict[str, Any]:
    """Return a result that declines to classify, with the reason stated."""
    return {
        "status": STATUS_UNCLASSIFIED,
        "source": SOURCE_STYLOMETRY,
        "confidence": None,
        "detail": {"reason": reason},
    }


def _identifiers(tree: ast.AST) -> list[str]:
    """Collect identifier spellings that reflect naming style.

    Attribute names and keyword argument names are included because they are chosen by
    whoever wrote the code. Imported module names are not, because they are chosen by
    whoever wrote the library.
    """
    names: list[str] = []
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported.add((alias.asname or alias.name).split(".")[0])

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            names.append(node.arg)

    return [name for name in names if name and name not in imported]


def _comment_density(source: str, line_count: int) -> float:
    """Fraction of non-blank lines carrying a comment.

    Uses tokenize rather than scanning for a hash character, because a hash inside a
    string literal is not a comment and counting it would measure the wrong thing.
    """
    if line_count <= 0:
        return 0.0

    comment_lines: set[int] = set()
    try:
        for token in tokenize.generate_tokens(StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                comment_lines.add(token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A fragment that tokenizes badly still has measurable structure elsewhere, so
        # this metric degrades to zero rather than failing the whole measurement.
        return 0.0

    return round(len(comment_lines) / line_count, 6)


def _docstring_ratio(tree: ast.AST) -> float:
    """Fraction of definitions carrying a docstring."""
    definitions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    if not definitions:
        return 0.0

    documented = sum(1 for node in definitions if ast.get_docstring(node))
    return round(documented / len(definitions), 6)


def _nesting_depths(tree: ast.AST) -> list[int]:
    """Depth of each statement, measuring how uniformly nested the code is."""
    depths: list[int] = []

    def walk(node: ast.AST, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                depths.append(depth)
                walk(child, depth + 1)
            else:
                walk(child, depth)

    walk(tree, 0)
    return depths


def _entropy(text: str) -> float:
    """Shannon entropy of a character distribution, in bits."""
    if not text:
        return 0.0
    counts = Counter(text)
    total = len(text)
    return round(
        -sum((n / total) * math.log2(n / total) for n in counts.values()),
        6,
    )


def _coefficient_of_variation(values: list[int]) -> float:
    """Standard deviation divided by the mean, a scale-free measure of variability.

    Scale-free matters: comparing raw line-length deviation across a short hunk and a
    long baseline would measure size rather than style.
    """
    if not values:
        return 0.0
    mean = _mean(values)
    if mean <= 1e-9:
        return 0.0
    return round(_stdev(values, mean) / mean, 6)


def _mean(values: list[float] | list[int]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stdev(values: list[float] | list[int], mean: float) -> float:
    """Population standard deviation about a known mean."""
    if len(values) < 2:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _dedent(source: str) -> str:
    """Remove common leading indentation so an extracted method parses standalone."""
    lines = source.splitlines()
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    if not indents:
        return source
    shift = min(indents)
    if shift == 0:
        return source
    return "\n".join(line[shift:] if line.strip() else line for line in lines)
