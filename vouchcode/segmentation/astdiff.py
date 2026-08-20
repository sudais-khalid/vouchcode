"""Structural comparison of two Python abstract syntax trees.

Parses the pre- and post-commit versions of a file with the standard library ast module
and reports which definitions actually changed, distinguishing cases that a line diff
conflates:

    renamed definition       identifier changed, body identical
    renamed and modified     identifier changed and the body also changed, but the two
                             are still recognizably the same logic
    modified body            identifier identical, body changed
    moved definition         identical in every way except position in the file
    added or removed         no counterpart in the other version

Reporting a rename as a full rewrite would attribute unchanged, previously understood
code to a new AI-generated hunk and send it back through comprehension verification for
no reason. Reporting a rename plus an edit as a pure rename would hide a real change.
Both failure modes matter, so they are separate outcomes here rather than one bucket.

Method. This is deliberately not a general tree edit distance algorithm. Those are
expensive and, more importantly, easy to get subtly wrong in ways that pass a handful of
tests and then misattribute real code. The approach here is narrower and checkable:

    1. Extract every definition (function, method, class header, module-level block)
       with a qualified name.
    2. Compute two fingerprints per definition. The exact fingerprint is an ast.dump
       with the definition's own name blanked, so a rename does not disturb it. The
       normalized fingerprint additionally alpha-renames local bindings, so renaming a
       variable inside the body does not disturb it either.
    3. Match by qualified name first. Whatever is left over is paired across the removed
       and added sets by fingerprint equality, then by structural similarity.

Alpha-renaming is limited to names the definition itself binds: parameters, assignment
targets, loop and comprehension targets, with and except aliases, walrus targets, and
nested definition names. Attribute names, called globals, and constants are left intact,
because calling a different method or returning a different constant is a real change
and must not be normalized away.

Scope approximation, stated plainly. The binding set is computed syntactically over the
definition's subtree, not by real scope analysis, which the ast module does not provide.
A local variable that shadows a module-level name of the same spelling is normalized
along with it. This is a deliberate approximation: it errs toward treating two bodies as
structurally equivalent, and the consequence of that error is a rename being reported
where an add-plus-remove was marginally more accurate, which is the harmless direction.
"""

from __future__ import annotations

import ast
import copy
import difflib
from collections.abc import Iterator
from dataclasses import dataclass, field

from vouchcode.errors import SegmentationError

# Outcome of comparing one definition across two versions of a file.
CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CHANGE_MODIFIED = "modified"
CHANGE_RENAMED = "renamed"
CHANGE_RENAMED_MODIFIED = "renamed_modified"
CHANGE_MOVED = "moved"
CHANGE_UNCHANGED = "unchanged"

# Kinds of definition the segmenter recognizes.
KIND_FUNCTION = "function"
KIND_ASYNC_FUNCTION = "async_function"
KIND_CLASS = "class"
KIND_MODULE = "module"

# Qualified name given to the synthetic definition holding module-level statements that
# sit outside any function or class: imports, constants, and top-level calls.
MODULE_QUALNAME = "<module>"

# Minimum similarity for two unmatched definitions to be paired as a rename rather than
# reported as an unrelated removal and addition.
#
# Chosen conservatively, and the direction of the error is the reason. A missed rename
# degrades to add-plus-remove, which over-reports change: the developer is asked to
# account for code they already understood, which is annoying but safe. A false rename
# links two unrelated definitions and under-reports change, which lets genuinely new
# code inherit an old hunk's provenance. For a tool whose output is meant to be
# evidence, over-reporting is the tolerable failure.
RENAME_SIMILARITY_THRESHOLD = 0.70

# Similarity matching is only trusted for definitions with at least this many nodes.
#
# Short definitions collide regardless of what they do. Both numbers below come from
# measurement rather than intuition: every single-expression function tested produced
# exactly 9 tokens, and unrelated ones scored 0.78 to 0.89 against each other, because a
# nine token sequence differing in one place is arithmetically similar no matter how
# different the behavior is. Real functions in the same test measured 24 to 26 tokens,
# and two genuinely different ones scored 0.64. A floor of 20 sits in the gap.
#
# Below the floor, only exact or normalized fingerprint equality counts as a rename. A
# genuine rename of a one-line function is therefore reported as a removal and an
# addition. That is the conservative direction on purpose, and the cost is small: asking
# a developer to re-read a one-line function is a trivial imposition next to silently
# linking two unrelated definitions.
MIN_NODES_FOR_SIMILARITY = 20

_DEFINITION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class Definition:
    """One logical unit of code extracted from a parsed module."""

    qualname: str
    name: str
    kind: str
    lineno: int
    end_lineno: int
    source: str
    # ast.dump with the definition's own name blanked. Equal fingerprints mean the two
    # bodies are identical down to identifier spelling.
    exact_fingerprint: str
    # As above, and additionally alpha-renamed over locally bound names. Equal
    # fingerprints mean the bodies differ at most in what local variables are named.
    normalized_fingerprint: str
    # Sequence of AST node type names, used for similarity when neither fingerprint
    # matches outright.
    signature: tuple[str, ...] = field(default=())

    @property
    def node_count(self) -> int:
        return len(self.signature)


@dataclass(frozen=True)
class DefinitionChange:
    """How one definition differs between two versions of a file."""

    kind: str
    before: Definition | None
    after: Definition | None
    similarity: float = 0.0

    @property
    def qualname(self) -> str:
        """The name to report this change under, preferring the post-commit name."""
        target = self.after or self.before
        return target.qualname if target else ""

    @property
    def is_change(self) -> bool:
        """Whether this represents an actual difference worth recording."""
        return self.kind != CHANGE_UNCHANGED


def parse_module(source: str, filename: str = "<unknown>") -> ast.Module:
    """Parse source into a module AST, converting a syntax error into a clean failure.

    A file that does not parse cannot be segmented. That is a normal condition, not a
    crash: a commit may legitimately contain a Python file targeting a different
    version, or a template with placeholder syntax. The caller decides what to do about
    it.
    """
    try:
        return ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise SegmentationError(
            f"{filename} could not be parsed as Python at line {exc.lineno}: {exc.msg}"
        ) from exc


def extract_definitions(source: str, filename: str = "<unknown>") -> list[Definition]:
    """Extract every definition from a source file, in source order.

    Recursion rule, stated once because it drives the whole shape of the output:

    Class bodies are descended into, and each method becomes its own definition, because
    a class is a namespace and its methods are independently meaningful units that a
    developer reasons about separately. The class itself contributes a header-only
    definition covering its bases, decorators, and class-level attributes, with nested
    definitions stripped out so that changing a method does not also mark the class as
    changed.

    Function bodies are not descended into. A closure is part of the logic of the
    function that encloses it, not a separate unit, so it stays inline in its parent's
    fingerprint.
    """
    module = parse_module(source, filename)
    definitions: list[Definition] = []

    module_block = _module_block(module, source)
    if module_block is not None:
        definitions.append(module_block)

    for node, qualname in _walk_definitions(module):
        definitions.append(_build_definition(node, qualname, source))

    definitions.sort(key=lambda d: (d.lineno, d.qualname))
    return definitions


def diff_sources(
    before: str,
    after: str,
    filename: str = "<unknown>",
) -> list[DefinitionChange]:
    """Compare two versions of a file and return the changes between them.

    Only actual differences are returned. Definitions that are identical and in the same
    place produce no entry.
    """
    before_defs = extract_definitions(before, filename) if before.strip() else []
    after_defs = extract_definitions(after, filename) if after.strip() else []

    changes: list[DefinitionChange] = []

    before_by_name = {d.qualname: d for d in before_defs}
    after_by_name = {d.qualname: d for d in after_defs}

    # Pass one: definitions that kept their qualified name. Same name is strong evidence
    # of identity, so these are resolved before any fingerprint matching is attempted.
    for qualname in before_by_name.keys() & after_by_name.keys():
        old = before_by_name[qualname]
        new = after_by_name[qualname]
        changes.append(_compare_matched(old, new))

    # Pass two: everything unmatched by name. A rename lives here, as one entry in the
    # removed set and one in the added set that need to be recognized as the same
    # definition wearing a different identifier.
    removed = [d for d in before_defs if d.qualname not in after_by_name]
    added = [d for d in after_defs if d.qualname not in before_by_name]
    changes.extend(_match_renames(removed, added))

    changes = [change for change in changes if change.is_change]
    changes.sort(key=_change_sort_key)
    return changes


def _change_sort_key(change: DefinitionChange) -> tuple[int, str]:
    """Order changes by their position in the post-commit file, then by name.

    A removal has no post-commit side, so it sorts by where it used to be. Deterministic
    ordering matters because these changes become ledger entries, and a ledger whose
    contents reorder between runs is not reproducible.
    """
    anchor = change.after or change.before
    return (anchor.lineno if anchor else 0, change.qualname)


def _compare_matched(old: Definition, new: Definition) -> DefinitionChange:
    """Classify a pair of definitions that share a qualified name."""
    if old.exact_fingerprint == new.exact_fingerprint:
        # Identical logic. The only thing that can still differ is where it sits.
        kind = CHANGE_MOVED if old.lineno != new.lineno else CHANGE_UNCHANGED
        return DefinitionChange(kind=kind, before=old, after=new, similarity=1.0)

    return DefinitionChange(
        kind=CHANGE_MODIFIED,
        before=old,
        after=new,
        similarity=_similarity(old, new),
    )


def _match_renames(
    removed: list[Definition],
    added: list[Definition],
) -> list[DefinitionChange]:
    """Pair leftover removals with leftover additions that are the same definition.

    Candidate pairs are scored, then consumed greedily from the strongest score down,
    with each definition usable once. Greedy is sufficient here and is predictable,
    which matters more than optimality: a developer reading the output should be able to
    reconstruct why two definitions were paired.
    """
    candidates: list[tuple[float, str, Definition, Definition]] = []

    for old in removed:
        for new in added:
            score, kind = _rename_candidate(old, new)
            if score > 0.0:
                candidates.append((score, kind, old, new))

    # Sort by score descending, then by name for a deterministic order among ties. Two
    # runs over the same input must produce the same pairing, or a ledger entry stops
    # being reproducible.
    candidates.sort(key=lambda item: (-item[0], item[3].qualname, item[2].qualname))

    used_old: set[str] = set()
    used_new: set[str] = set()
    changes: list[DefinitionChange] = []

    for score, kind, old, new in candidates:
        if old.qualname in used_old or new.qualname in used_new:
            continue
        used_old.add(old.qualname)
        used_new.add(new.qualname)
        changes.append(
            DefinitionChange(kind=kind, before=old, after=new, similarity=score)
        )

    for old in removed:
        if old.qualname not in used_old:
            changes.append(
                DefinitionChange(kind=CHANGE_REMOVED, before=old, after=None)
            )

    for new in added:
        if new.qualname not in used_new:
            changes.append(DefinitionChange(kind=CHANGE_ADDED, before=None, after=new))

    return changes


def _rename_candidate(old: Definition, new: Definition) -> tuple[float, str]:
    """Score how likely two unmatched definitions are to be the same definition.

    Returns a score and the change kind it would imply. A score of zero means the pair
    should not be linked at all.
    """
    # Different kinds are never the same definition. A function did not become a class.
    if old.kind != new.kind:
        return 0.0, CHANGE_ADDED

    # Identical bodies down to identifier spelling. This is a pure rename.
    if old.exact_fingerprint == new.exact_fingerprint:
        return 1.0, CHANGE_RENAMED

    # Identical once local bindings are alpha-renamed. The definition was renamed and
    # something inside it was renamed too, but the logic is unchanged. This is the case
    # a naive comparison gets wrong in both directions, calling it either a full rewrite
    # or no change at all.
    if old.normalized_fingerprint == new.normalized_fingerprint:
        return 1.0, CHANGE_RENAMED_MODIFIED

    # Neither fingerprint matches, so fall back to structural similarity. Only trusted
    # for definitions large enough that a high ratio means something.
    if (
        old.node_count < MIN_NODES_FOR_SIMILARITY
        or new.node_count < MIN_NODES_FOR_SIMILARITY
    ):
        return 0.0, CHANGE_ADDED

    score = _similarity(old, new)
    if score >= RENAME_SIMILARITY_THRESHOLD:
        return score, CHANGE_RENAMED_MODIFIED

    return 0.0, CHANGE_ADDED


def _similarity(old: Definition, new: Definition) -> float:
    """Structural similarity of two definitions, between 0.0 and 1.0.

    Computed over the sequence of AST node type names rather than over source text, so
    that reformatting, comment changes, and identifier spelling do not affect it.
    """
    if not old.signature and not new.signature:
        return 1.0
    matcher = difflib.SequenceMatcher(
        None, old.signature, new.signature, autojunk=False
    )
    return round(matcher.ratio(), 4)


def _walk_definitions(
    module: ast.Module,
    prefix: str = "",
) -> Iterator[tuple[ast.AST, str]]:
    """Yield each definition node with its qualified name, descending only into classes.

    See extract_definitions for why function bodies are not descended into.
    """
    body = module.body if isinstance(module, ast.Module) else []
    yield from _walk_body(body, prefix)


def _walk_body(body: list[ast.stmt], prefix: str) -> Iterator[tuple[ast.AST, str]]:
    """Yield definitions in a statement list, recursing through class bodies only."""
    for node in body:
        if not isinstance(node, _DEFINITION_NODES):
            continue

        qualname = f"{prefix}{node.name}"
        yield node, qualname

        if isinstance(node, ast.ClassDef):
            yield from _walk_body(node.body, f"{qualname}.")


def _build_definition(node: ast.AST, qualname: str, source: str) -> Definition:
    """Construct the Definition record for one node."""
    kind = _kind_of(node)
    subject = _fingerprint_subject(node)
    normalized = _alpha_rename(subject)

    return Definition(
        qualname=qualname,
        name=getattr(node, "name", qualname),
        kind=kind,
        lineno=_line_span(node)[0],
        end_lineno=_line_span(node)[1],
        source=ast.get_source_segment(source, node) or "",
        exact_fingerprint=_dump(subject),
        normalized_fingerprint=_dump(normalized),
        # Taken from the normalized tree so that local identifier spelling does not
        # affect similarity, while everything else still does.
        signature=_signature(normalized),
    )


def _line_span(node: ast.AST) -> tuple[int, int]:
    """Return a node's inclusive start and end line, coping with missing positions.

    end_lineno is optional on the AST and is absent for some synthesized nodes, in which
    case a node occupies the single line it starts on.
    """
    raw_start = getattr(node, "lineno", 1)
    start = raw_start if isinstance(raw_start, int) else 1

    raw_end = getattr(node, "end_lineno", None)
    end = raw_end if isinstance(raw_end, int) else start

    return start, max(start, end)


def _fingerprint_subject(node: ast.AST) -> ast.AST:
    """Return the node to fingerprint, with its own name blanked.

    Blanking the name is what makes a rename invisible to the exact fingerprint, which
    is the whole basis of rename detection. For a class, nested definitions are also
    stripped, so that editing a method marks the method changed without also marking the
    enclosing class changed.
    """
    subject = copy.deepcopy(node)

    if hasattr(subject, "name"):
        subject.name = ""

    if isinstance(subject, ast.ClassDef):
        subject.body = [
            stmt for stmt in subject.body if not isinstance(stmt, _DEFINITION_NODES)
        ]
        # A class body cannot be empty in the AST, and an emptied one still needs to
        # dump to something stable.
        if not subject.body:
            subject.body = [ast.Pass()]

    return subject


def _module_block(module: ast.Module, source: str) -> Definition | None:
    """Build the synthetic definition covering module-level statements.

    Imports, constants, and top-level calls are code a developer is accountable for, and
    without this they would produce no hunk at all. They are treated as one unit rather
    than as individually tracked statements: matching bare statements across
    versions has no stable identity to match on, and inventing one would be guesswork.
    The cost is
    coarseness, so adding an import marks the module block changed. That is truthful, if
    blunt.
    """
    statements = [
        stmt for stmt in module.body if not isinstance(stmt, _DEFINITION_NODES)
    ]
    if not statements:
        return None

    holder = ast.Module(body=copy.deepcopy(statements), type_ignores=[])

    first = statements[0]
    last = statements[-1]

    return Definition(
        qualname=MODULE_QUALNAME,
        name=MODULE_QUALNAME,
        kind=KIND_MODULE,
        lineno=_line_span(first)[0],
        end_lineno=_line_span(last)[1],
        source="\n".join(
            segment
            for segment in (ast.get_source_segment(source, s) for s in statements)
            if segment
        ),
        exact_fingerprint=_dump(holder),
        normalized_fingerprint=_dump(_alpha_rename(holder)),
        signature=_signature(holder),
    )


def _kind_of(node: ast.AST) -> str:
    """Map an AST node to the definition kind it represents."""
    if isinstance(node, ast.AsyncFunctionDef):
        return KIND_ASYNC_FUNCTION
    if isinstance(node, ast.ClassDef):
        return KIND_CLASS
    return KIND_FUNCTION


def _dump(node: ast.AST) -> str:
    """Serialize an AST to a stable string.

    include_attributes is off, which excludes line and column numbers. Position must not
    contribute to the fingerprint: moving a function without editing it is a move, not a
    modification, and including positions would make every downstream definition in the
    file look changed whenever something above it grew a line.
    """
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _signature(node: ast.AST) -> tuple[str, ...]:
    """Return a discriminating token per node of a subtree, in walk order.

    Node type names alone are far too weak to compare definitions with. Every
    single-expression function has the same shape, so 'return value.strip()' and
    'return other.upper()' produce identical type sequences and would score a perfect
    similarity against each other. Tokens therefore carry the leaf detail that actually
    distinguishes behavior: attribute names, constant values and their types, keyword
    argument names, and surviving identifiers.

    Local identifiers have already been replaced with placeholders by the time this
    runs, so including them costs nothing and including non-local ones is what keeps a
    call to
    a different global from looking identical.
    """
    return tuple(_token(child) for child in _ordered_nodes(node))


def _token(node: ast.AST) -> str:
    """Render one node as a comparison token."""
    type_name = type(node).__name__

    if isinstance(node, ast.Attribute):
        # Calling a different method is a behavioral difference, not a naming one.
        return f"{type_name}:{node.attr}"
    if isinstance(node, ast.Constant):
        return f"{type_name}:{type(node.value).__name__}:{node.value!r}"
    if isinstance(node, ast.Name):
        return f"{type_name}:{node.id}"
    if isinstance(node, ast.arg):
        return f"{type_name}:{node.arg}"
    if isinstance(node, ast.keyword):
        return f"{type_name}:{node.arg}"
    if isinstance(node, ast.ExceptHandler):
        return f"{type_name}:{node.name}"

    # Definition names are blanked in the fingerprint subject, so there is nothing
    # identifying left to add for them. Operator nodes such as Add and Mult are already
    # distinguished by their type name alone.
    return type_name


def _ordered_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Depth-first traversal in field order, which follows source order closely.

    ast.walk is breadth-first, which would interleave nodes from unrelated parts of a
    function and make the similarity ratio less meaningful. Field order also gives
    first-occurrence semantics that the alpha-renamer depends on: parameters come before
    the body, so they receive the first placeholder slots on both sides of a comparison.
    """
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _ordered_nodes(child)


def _alpha_rename(node: ast.AST) -> ast.AST:
    """Return a copy with locally bound names replaced by positional placeholders.

    Two functions that differ only in what their variables are called produce the same
    output here, which is what lets a rename plus an internal variable rename be
    recognized as the same logic rather than as a rewrite.
    """
    subject = copy.deepcopy(node)
    bindings = _collect_bindings(subject)
    if not bindings:
        return subject

    mapping = {name: f"_v{index}" for index, name in enumerate(bindings)}
    renamed: ast.AST = _BindingRenamer(mapping).visit(subject)
    return renamed


def _collect_bindings(node: ast.AST) -> list[str]:
    """Collect names the subtree binds, in first-appearance order.

    Order is first appearance rather than alphabetical, and that matters. Alphabetical
    ordering would assign placeholder slots by spelling, so renaming one variable would
    shift the slots of every other variable and destroy the very equivalence this
    function exists to detect.

    Names declared global or nonlocal are excluded: they refer to an outer scope, so
    normalizing them would erase a real difference between reading one module-level name
    and reading another.
    """
    declared_outer: set[str] = set()
    for child in _ordered_nodes(node):
        if isinstance(child, (ast.Global, ast.Nonlocal)):
            declared_outer.update(child.names)

    ordered: list[str] = []
    seen: set[str] = set()

    def record(name: str | None) -> None:
        if not name or name in seen or name in declared_outer:
            return
        seen.add(name)
        ordered.append(name)

    def record_target(target: ast.AST | None) -> None:
        """Record every name an assignment target binds, including unpacking."""
        if target is None:
            return
        for child in _ordered_nodes(target):
            if isinstance(child, ast.Name):
                record(child.id)

    for child in _ordered_nodes(node):
        if isinstance(child, ast.arg):
            record(child.arg)
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                record_target(target)
        elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
            record_target(child.target)
        elif isinstance(child, (ast.For, ast.AsyncFor)):
            record_target(child.target)
        elif isinstance(child, ast.comprehension):
            record_target(child.target)
        elif isinstance(child, ast.NamedExpr):
            record_target(child.target)
        elif isinstance(child, ast.withitem):
            record_target(child.optional_vars)
        elif isinstance(child, ast.ExceptHandler):
            record(child.name)
        elif isinstance(child, _DEFINITION_NODES):
            # A nested definition binds its own name in the enclosing scope. Skip the
            # subject node itself, whose name was already blanked.
            if child is not node:
                record(child.name)

    return ordered


class _BindingRenamer(ast.NodeTransformer):
    """Rewrites locally bound identifiers to positional placeholders.

    Only identifiers that name a binding are rewritten. Attribute names, called globals,
    keyword argument names, and constants are left alone, because a call to a different
    method or a different constant value is a genuine difference in behavior and must
    survive normalization.
    """

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def visit_Name(self, node: ast.Name) -> ast.AST:
        replacement = self._mapping.get(node.id)
        if replacement is not None:
            node.id = replacement
        return self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        replacement = self._mapping.get(node.arg)
        if replacement is not None:
            node.arg = replacement
        return self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.AST:
        if node.name is not None:
            replacement = self._mapping.get(node.name)
            if replacement is not None:
                node.name = replacement
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._rename_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._rename_definition(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        return self._rename_definition(node)

    def _rename_definition(self, node: ast.AST) -> ast.AST:
        name = getattr(node, "name", None)
        if name:
            replacement = self._mapping.get(name)
            if replacement is not None:
                node.name = replacement  # type: ignore[attr-defined]
        return self.generic_visit(node)
