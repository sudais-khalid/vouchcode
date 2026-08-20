"""Phase 2 exit criterion tests.

The exit criterion is that a commit containing a renamed function with no logical change
is correctly distinguished from a commit that rewrites a function's body, and that the
two produce visibly different attribution output.

The rename tests come in two strengths, because the easy case is not the interesting
one:

    a pure rename, body untouched, which is the minimum bar

    a rename combined with an internal variable rename, which is where a naive
    comparison actually breaks. It either reports a full rewrite, losing the signal that
    this is still the same logic, or reports no change at all, missing a real edit.

Both are asserted separately. A single test covering only the pure rename would let the
harder case pass untested.
"""

from __future__ import annotations

import json
from pathlib import Path

from support import run_git, run_vouchcode, write_file

from vouchcode.segmentation.astdiff import (
    CHANGE_ADDED,
    CHANGE_MODIFIED,
    CHANGE_MOVED,
    CHANGE_REMOVED,
    CHANGE_RENAMED,
    CHANGE_RENAMED_MODIFIED,
    diff_sources,
    extract_definitions,
)
from vouchcode.segmentation.hunks import build_hunks

# A function large enough to be measurable, used wherever a test needs a definition that
# clears the similarity node floor.
PARSER = """def parse_value(raw):
    if not raw:
        return None
    cleaned = raw.strip()
    for character in cleaned:
        if character.isdigit():
            return int(cleaned)
    return cleaned
"""


# ---------------------------------------------------------------------------
# Exit criterion: rename versus rewrite
# ---------------------------------------------------------------------------


def test_pure_rename_is_not_a_rewrite() -> None:
    """A function renamed with no body change is reported as a rename.

    The minimum bar. Reporting this as a removal plus an addition would send code the
    developer already accounted for back through comprehension verification.
    """
    after = PARSER.replace("def parse_value(", "def parse_input(")

    changes = diff_sources(PARSER, after, "m.py")

    assert len(changes) == 1, f"expected one change, got {[c.kind for c in changes]}"
    change = changes[0]

    assert change.kind == CHANGE_RENAMED
    assert change.before is not None and change.before.qualname == "parse_value"
    assert change.after is not None and change.after.qualname == "parse_input"
    assert change.similarity == 1.0

    # The failure mode this rules out explicitly.
    kinds = {c.kind for c in changes}
    assert CHANGE_ADDED not in kinds and CHANGE_REMOVED not in kinds


def test_rename_with_internal_variable_rename_is_linked_but_marked_modified() -> None:
    """A rename plus an internal variable rename is both linked and marked as edited.

    This is the case a naive comparison gets wrong in both directions. Asserting only
    that it is not a rewrite would not catch the opposite error of calling it unchanged,
    so both are asserted here.
    """
    after = PARSER.replace("def parse_value(", "def parse_input(").replace(
        "cleaned", "trimmed"
    )

    changes = diff_sources(PARSER, after, "m.py")

    assert len(changes) == 1, f"expected one change, got {[c.kind for c in changes]}"
    change = changes[0]

    # Not a rewrite: the two definitions are linked to each other.
    assert change.kind == CHANGE_RENAMED_MODIFIED
    assert change.before is not None and change.before.qualname == "parse_value"
    assert change.after is not None and change.after.qualname == "parse_input"

    # Not unchanged: a real edit happened and the classification records it.
    assert change.kind != CHANGE_RENAMED
    assert change.kind != CHANGE_MOVED

    # The logic is recognized as substantially the same.
    assert change.similarity == 1.0


def test_rewritten_body_is_a_modification_not_a_rename() -> None:
    """A replaced body is reported as modified, with low similarity."""
    after = "def parse_value(raw):\n    return str(raw).upper()\n"

    changes = diff_sources(PARSER, after, "m.py")

    assert len(changes) == 1
    assert changes[0].kind == CHANGE_MODIFIED
    assert changes[0].similarity < 0.5, (
        "a rewritten body must not score as substantially the same logic"
    )


def test_rename_and_rewrite_are_visibly_different_outcomes() -> None:
    """The exit criterion, stated as one assertion.

    A rename and a rewrite of the same starting function must not produce the same
    classification, the same similarity, or the same attribution status.
    """
    renamed = diff_sources(PARSER, PARSER.replace("parse_value", "parse_input"), "m.py")
    rewritten = diff_sources(
        PARSER, "def parse_value(raw):\n    return str(raw).upper()\n", "m.py"
    )

    assert renamed[0].kind != rewritten[0].kind
    assert renamed[0].similarity > rewritten[0].similarity

    renamed_hunk = build_hunks(
        "m.py", PARSER, PARSER.replace("parse_value", "parse_input")
    )[0]
    rewritten_hunk = build_hunks(
        "m.py", PARSER, "def parse_value(raw):\n    return str(raw).upper()\n"
    )[0]

    # A pure rename carries no new logic, so it is never attributed or questioned.
    assert renamed_hunk.carries_new_logic is False
    assert renamed_hunk.attribution["status"] == "unchanged"
    assert renamed_hunk.attribution["source"] == "structural"

    # A rewrite does carry new logic and is left for the attribution pass to classify.
    assert rewritten_hunk.carries_new_logic is True
    assert rewritten_hunk.attribution == {}


# ---------------------------------------------------------------------------
# Guards against false rename pairing
# ---------------------------------------------------------------------------


def test_unrelated_small_functions_are_not_paired_as_a_rename() -> None:
    """Two trivial, unrelated functions must not be linked.

    Every single-expression function has nearly the same node sequence, so pairing on
    similarity alone would manufacture renames out of coincidence.
    """
    changes = diff_sources(
        "def alpha(x):\n    return x + 1\n",
        "def beta(y):\n    return y * 2\n",
        "m.py",
    )

    kinds = {change.kind for change in changes}
    assert kinds == {CHANGE_ADDED, CHANGE_REMOVED}


def test_calling_a_different_method_is_not_normalized_away() -> None:
    """Renaming a variable is a naming change; calling a different method is not.

    If attribute names were normalized along with local bindings, these two functions
    would fingerprint identically and a behavioral change would vanish.
    """
    changes = diff_sources(
        "def f(value):\n    return value.strip()\n",
        "def g(other):\n    return other.upper()\n",
        "m.py",
    )

    kinds = {change.kind for change in changes}
    assert CHANGE_RENAMED not in kinds
    assert kinds == {CHANGE_ADDED, CHANGE_REMOVED}


def test_moved_function_is_a_move_not_a_modification() -> None:
    """Reordering definitions changes no logic and must not be reported as an edit."""
    before = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    after = "def b():\n    return 2\n\n\ndef a():\n    return 1\n"

    changes = diff_sources(before, after, "m.py")

    assert {change.kind for change in changes} == {CHANGE_MOVED}
    assert all(change.similarity == 1.0 for change in changes)


# ---------------------------------------------------------------------------
# Segmentation structure
# ---------------------------------------------------------------------------


def test_renaming_a_method_does_not_mark_the_class_changed() -> None:
    """A class header is fingerprinted without its methods.

    Otherwise editing any method would also mark the enclosing class modified, producing
    two hunks for one change and double counting it in attribution.
    """
    before = (
        "class Store:\n    LIMIT = 10\n\n"
        "    def get(self, key):\n        return self._data[key]\n"
    )
    after = before.replace("def get(", "def fetch(")

    changes = diff_sources(before, after, "m.py")

    assert len(changes) == 1
    assert changes[0].kind == CHANGE_RENAMED
    assert changes[0].qualname == "Store.fetch"


def test_class_attribute_change_does_not_mark_methods_changed() -> None:
    """The converse: editing the class body leaves its methods alone."""
    before = (
        "class Store:\n    LIMIT = 10\n\n"
        "    def get(self, key):\n        return self._data[key]\n"
    )
    after = before.replace("LIMIT = 10", "LIMIT = 20")

    changes = diff_sources(before, after, "m.py")

    assert len(changes) == 1
    assert changes[0].kind == CHANGE_MODIFIED
    assert changes[0].qualname == "Store"


def test_module_level_code_produces_a_hunk() -> None:
    """Imports and constants are code a developer is accountable for."""
    changes = diff_sources("VALUE = 1\n", "VALUE = 2\n", "m.py")

    assert len(changes) == 1
    assert changes[0].qualname == "<module>"
    assert changes[0].kind == CHANGE_MODIFIED


def test_reformatting_alone_produces_no_change() -> None:
    """Whitespace and comments are not logic.

    This is the concrete advantage over a line diff, which reports every one of these
    as a modified range.
    """
    before = "def f(a, b):\n    return a + b\n"
    after = (
        "def f(\n    a,\n    b,\n):\n"
        "    # Add the two operands together.\n"
        "    return a + b\n"
    )

    assert diff_sources(before, after, "m.py") == []


def test_unparseable_source_is_reported_not_crashed() -> None:
    """A file that does not parse produces a clean error, not a traceback."""
    from vouchcode.errors import SegmentationError

    try:
        diff_sources("def f():\n    pass\n", "def f(\n", "broken.py")
    except SegmentationError as exc:
        assert "broken.py" in str(exc)
    else:
        raise AssertionError("expected SegmentationError")


def test_definitions_are_extracted_with_qualified_names() -> None:
    """Methods carry their class in the qualified name so two Store.get do not
    collide."""
    source = (
        "TOP = 1\n\n"
        "def free():\n    return TOP\n\n"
        "class A:\n    def get(self):\n        return 1\n\n"
        "class B:\n    def get(self):\n        return 2\n"
    )

    names = [d.qualname for d in extract_definitions(source, "m.py")]

    assert names == ["<module>", "free", "A", "A.get", "B", "B.get"]


# ---------------------------------------------------------------------------
# End to end through a real commit
# ---------------------------------------------------------------------------


def _ledger(root: Path) -> dict:
    return json.loads((root / ".vouchcode" / "ledger.json").read_text(encoding="utf-8"))


def _commit(root: Path, env: dict[str, str], path: str, body: str, message: str) -> str:
    write_file(root, path, body)
    run_git(["add", path], cwd=root, env=env)
    run_git(["commit", "-m", message], cwd=root, env=env)
    return run_git(["rev-parse", "HEAD"], cwd=root, env=env).stdout.strip()


def test_commit_records_hunks_in_the_ledger(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """A real commit populates the hunks field the Phase 1 entry left absent."""
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "parser.py", PARSER, "Add parser")

    entry = _ledger(temp_repo)["entries"][0]

    assert entry["hunks"] is not None
    qualnames = {hunk["qualname"] for hunk in entry["hunks"]}
    assert "parse_value" in qualnames

    hunk = next(h for h in entry["hunks"] if h["qualname"] == "parse_value")
    assert hunk["change"] == "added"
    assert hunk["path"] == "parser.py"
    assert hunk["attribution"]["status"] in {"ai", "human", "mixed", "unclassified"}


def test_rename_commit_is_attributed_as_unchanged_end_to_end(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The exit criterion, exercised through a real git commit.

    Renaming a function and committing it must produce a ledger entry that records the
    rename, links it to the former name, and states that nothing new was authored.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "parser.py", PARSER, "Add parser")
    _commit(
        temp_repo,
        git_env,
        "parser.py",
        PARSER.replace("def parse_value(", "def parse_input("),
        "Rename parse_value to parse_input",
    )

    entry = _ledger(temp_repo)["entries"][1]

    assert len(entry["hunks"]) == 1
    hunk = entry["hunks"][0]

    assert hunk["change"] == "renamed"
    assert hunk["qualname"] == "parse_input"
    assert hunk["previous_qualname"] == "parse_value"
    assert hunk["attribution"] == {
        "status": "unchanged",
        "source": "structural",
        "confidence": 1.0,
    }

    # The commit as a whole authored nothing new.
    assert entry["attribution"]["status"] == "unchanged"


def test_rewrite_commit_is_attributed_differently_from_a_rename(
    temp_repo: Path, git_env: dict[str, str]
) -> None:
    """The other half of the exit criterion, through a real commit.

    A rewrite of the same function must not produce the rename commit's output.
    """
    run_vouchcode(["init"], cwd=temp_repo, env=git_env)
    _commit(temp_repo, git_env, "parser.py", PARSER, "Add parser")
    _commit(
        temp_repo,
        git_env,
        "parser.py",
        "def parse_value(raw):\n    return str(raw).upper()\n",
        "Rewrite parse_value",
    )

    entry = _ledger(temp_repo)["entries"][1]
    hunk = entry["hunks"][0]

    assert hunk["change"] == "modified"
    assert hunk["previous_qualname"] is None
    assert hunk["attribution"]["status"] != "unchanged"
    assert entry["attribution"]["status"] != "unchanged"
