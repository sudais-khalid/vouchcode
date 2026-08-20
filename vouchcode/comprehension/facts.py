"""Extraction of verifiable structural facts from a hunk's AST.

A fact is something the code unambiguously determines, that a reader who understood the
code could state, and that can be checked without running anything. Facts are the ground
truth for both question generation and scoring, which is what keeps the two halves
consistent without a model in the loop: a question is derived from a fact, and an answer
is scored against that same fact.

Each fact carries three separable pieces, and the separation is what makes scoring able
to tell a real answer from a keyword-stuffed one:

    subject    what the question is about, for example the name of the guarded function
               or the collection being iterated.
    condition  the governing expression, rendered back to source.
    outcome    what the code does when that condition holds, for example the value
               returned or the exception raised.

An answer that names the subject and the condition but gets the outcome wrong is not the
same as one that gets all three, and neither is the same as an answer that sprays all
three terms without relating them. Scoring depends on these being distinct fields rather
than one bag of keywords.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from vouchcode.segmentation.astdiff import parse_module

# Fact kinds, matching the question forms named in Section 4.3 of the research
# documentation.
FACT_GUARD_RETURN = "guard_return"
FACT_EMPTY_ITERATION = "empty_iteration"
FACT_EXCEPTION_HANDLER = "exception_handler"
FACT_RAISE = "raise"
FACT_LOOP_TERMINATION = "loop_termination"


@dataclass(frozen=True)
class Fact:
    """One checkable structural claim about a piece of code."""

    kind: str
    subject: str
    condition: str
    outcome: str
    lineno: int
    # Terms that must appear in an answer for it to be about this fact at all. Distinct
    # from the terms that show the answer is correct, which live in outcome_terms.
    subject_terms: tuple[str, ...] = field(default=())
    condition_terms: tuple[str, ...] = field(default=())
    outcome_terms: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the ledger, with a fixed key order for Phase 4 hashing."""
        return {
            "kind": self.kind,
            "subject": self.subject,
            "condition": self.condition,
            "outcome": self.outcome,
            "line": self.lineno,
        }


def extract_facts(source: str) -> list[Fact]:
    """Return the structural facts derivable from a source fragment.

    Facts are returned in source order so that questions follow the reading order of the
    code. A fragment that does not parse yields nothing rather than raising: the caller
    is the comprehension engine, and a hunk it cannot analyze is one it simply does not
    ask about.
    """
    text = _dedent(source)
    if not text.strip():
        return []

    try:
        tree = parse_module(text, "<hunk>")
    except Exception:
        return []

    facts: list[Fact] = []
    for node in ast.walk(tree):
        facts.extend(_facts_for_node(node))

    facts.sort(key=lambda fact: (fact.lineno, fact.kind))
    return facts


def _facts_for_node(node: ast.AST) -> list[Fact]:
    """Extract every fact one node contributes."""
    if isinstance(node, ast.If):
        return _guard_facts(node)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return _iteration_facts(node)
    if isinstance(node, ast.While):
        return _while_facts(node)
    if isinstance(node, ast.ExceptHandler):
        return _handler_facts(node)
    return []


def _guard_facts(node: ast.If) -> list[Fact]:
    """Facts about a branch: what governs it and what happens on each side.

    Only branches whose body terminates the function are turned into facts. A guard
    clause that returns or raises has an outcome that can be stated in one phrase, which
    is what makes it answerable. An arbitrary if whose body merely continues has no
    single outcome worth asking about.
    """
    condition = _render(node.test)
    terminator = _terminating_statement(node.body)
    if terminator is None:
        return []

    outcome, outcome_terms = _describe_terminator(terminator)
    if not outcome:
        return []

    return [
        Fact(
            kind=FACT_GUARD_RETURN,
            subject=condition,
            condition=condition,
            outcome=outcome,
            lineno=node.lineno,
            subject_terms=_identifier_terms(node.test),
            condition_terms=_condition_terms(node.test),
            outcome_terms=outcome_terms,
        )
    ]


def _iteration_facts(node: ast.For | ast.AsyncFor) -> list[Fact]:
    """Facts about a loop: what it iterates and what happens when that is empty.

    The empty-collection case is the one Section 4.3 names, and it is a good question
    precisely because the answer is not visible in the loop body. A reader has to
    understand that the body never runs and reason about what follows.
    """
    iterated = _render(node.iter)
    if not iterated:
        return []

    # What happens after a loop that never executes is whatever follows it. An orelse
    # clause runs when the loop completes without a break, which includes never
    # starting.
    outcome = (
        "the loop body never executes and the else clause runs"
        if node.orelse
        else "the loop body never executes and control continues past the loop"
    )
    outcome_terms = (
        "never",
        "skip",
        "not",
        "no",
        "zero",
        "nothing",
        "continue",
        "past",
    )

    return [
        Fact(
            kind=FACT_EMPTY_ITERATION,
            subject=iterated,
            condition=f"{iterated} is empty",
            outcome=outcome,
            lineno=node.lineno,
            subject_terms=_identifier_terms(node.iter),
            condition_terms=("empty", "nothing", "zero", "no"),
            outcome_terms=outcome_terms,
        )
    ]


def _while_facts(node: ast.While) -> list[Fact]:
    """Facts about what terminates a while loop."""
    condition = _render(node.test)
    if not condition:
        return []

    return [
        Fact(
            kind=FACT_LOOP_TERMINATION,
            subject=condition,
            condition=condition,
            outcome=f"the loop stops when {condition} becomes false",
            lineno=node.lineno,
            subject_terms=_identifier_terms(node.test),
            condition_terms=_condition_terms(node.test),
            outcome_terms=("false", "stop", "end", "exit", "terminate", "until"),
        )
    ]


def _handler_facts(node: ast.ExceptHandler) -> list[Fact]:
    """Facts about an exception path: what it catches and what it does instead."""
    caught = _render(node.type) if node.type else "any exception"
    terminator = _terminating_statement(node.body)

    if terminator is not None:
        outcome, outcome_terms = _describe_terminator(terminator)
    else:
        outcome = "the handler runs and execution continues after the try block"
        outcome_terms = ("continue", "carry", "proceed", "past", "after")

    if not outcome:
        return []

    return [
        Fact(
            kind=FACT_EXCEPTION_HANDLER,
            subject=caught,
            condition=f"{caught} is raised",
            outcome=outcome,
            lineno=node.lineno,
            subject_terms=tuple(_split_identifier(caught)),
            condition_terms=(
                "raise",
                "raised",
                "throw",
                "thrown",
                "error",
                "exception",
            ),
            outcome_terms=outcome_terms,
        )
    ]


def _terminating_statement(body: list[ast.stmt]) -> ast.stmt | None:
    """Return the statement that ends control flow in a block, if one does.

    Only a direct child counts. A return nested inside a further conditional does not
    describe what the block does, it describes what one of its branches does.
    """
    for statement in body:
        if isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            return statement
    return None


def _describe_terminator(node: ast.stmt) -> tuple[str, tuple[str, ...]]:
    """Render what a terminating statement does, and the terms that would say so."""
    if isinstance(node, ast.Return):
        if node.value is None:
            return "returns None", ("none", "null", "nothing", "return")
        rendered = _render(node.value)
        terms = ("return", *_literal_terms(node.value), *_identifier_terms(node.value))
        return f"returns {rendered}", terms

    if isinstance(node, ast.Raise):
        if node.exc is None:
            return "re-raises the active exception", ("raise", "reraise", "propagate")
        rendered = _render(node.exc)
        return (
            f"raises {rendered}",
            ("raise", "raises", "error", "exception", *_identifier_terms(node.exc)),
        )

    if isinstance(node, ast.Continue):
        return "skips to the next iteration", ("continue", "skip", "next", "iteration")

    if isinstance(node, ast.Break):
        return "exits the loop", ("break", "exit", "stop", "leave", "end")

    return "", ()


def _render(node: ast.AST | None) -> str:
    """Render an AST node back to source text.

    ast.unparse is available from Python 3.9 and this project requires 3.10, so it is
    always present. It normalizes formatting, which is what makes a rendered condition
    stable to quote back to the developer in a question.
    """
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _identifier_terms(node: ast.AST | None) -> tuple[str, ...]:
    """Collect the identifiers appearing in an expression, lowercased.

    Attribute names are included because a reader referring to the right method is
    demonstrating they read the code. Literal values are handled separately.
    """
    if node is None:
        return ()

    terms: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            terms.extend(_split_identifier(child.id))
        elif isinstance(child, ast.Attribute):
            terms.extend(_split_identifier(child.attr))
    return tuple(dict.fromkeys(terms))


def _literal_terms(node: ast.AST | None) -> tuple[str, ...]:
    """Collect literal constants in an expression as answerable terms.

    A developer explaining that a function returns None, or zero, or an empty list, is
    stating the outcome. These are the words they would use.
    """
    if node is None:
        return ()

    terms: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant):
            value = child.value
            if value is None:
                terms.extend(("none", "null", "nothing"))
            elif isinstance(value, bool):
                terms.append(str(value).lower())
            elif isinstance(value, (int, float)):
                terms.append(str(value).lower())
            elif isinstance(value, str) and value:
                terms.extend(_split_identifier(value))
        elif isinstance(child, ast.List):
            terms.extend(("list", "empty") if not child.elts else ("list",))
        elif isinstance(child, ast.Dict):
            terms.extend(("dict", "empty") if not child.keys else ("dict",))

    return tuple(dict.fromkeys(terms))


def _condition_terms(node: ast.AST | None) -> tuple[str, ...]:
    """Terms that describe the shape of a condition, beyond its identifiers.

    A guard written as 'not value' is answerable with words like empty, missing, or
    falsy, none of which appear in the source. Mapping operators to the vocabulary a
    person actually uses is what lets a correct plain-English answer score.
    """
    if node is None:
        return ()

    terms: list[str] = list(_identifier_terms(node))
    terms.extend(_literal_terms(node))

    for child in ast.walk(node):
        if isinstance(child, ast.Not) or (
            isinstance(child, ast.UnaryOp) and isinstance(child.op, ast.Not)
        ):
            terms.extend(
                ("not", "no", "empty", "missing", "falsy", "absent", "without")
            )
        elif isinstance(child, ast.Is):
            terms.extend(("is", "none", "null"))
        elif isinstance(child, ast.IsNot):
            terms.extend(("not", "none", "null"))
        elif isinstance(child, ast.Eq):
            terms.extend(("equal", "equals", "same", "matches"))
        elif isinstance(child, ast.NotEq):
            terms.extend(("not", "different", "differs", "unequal"))
        elif isinstance(child, (ast.Lt, ast.LtE)):
            terms.extend(("less", "below", "under", "smaller", "fewer"))
        elif isinstance(child, (ast.Gt, ast.GtE)):
            terms.extend(("greater", "above", "over", "more", "larger", "exceeds"))
        elif isinstance(child, ast.In):
            terms.extend(("in", "contains", "member", "present"))
        elif isinstance(child, ast.NotIn):
            terms.extend(("not", "missing", "absent"))
        elif isinstance(child, ast.And):
            terms.extend(("and", "both"))
        elif isinstance(child, ast.Or):
            terms.extend(("or", "either"))

    return tuple(dict.fromkeys(terms))


def _split_identifier(name: str) -> list[str]:
    """Break an identifier into the words a person would say when describing it.

    snake_case and camelCase both split, so that a developer writing "record key" is
    credited for referring to record_key.
    """
    if not name:
        return []

    words: list[str] = []
    current = ""

    for character in name:
        if character in "_-. ":
            if current:
                words.append(current)
                current = ""
        elif character.isupper() and current and not current[-1].isupper():
            words.append(current)
            current = character
        else:
            current += character

    if current:
        words.append(current)

    lowered = [word.lower() for word in words if word]
    # The whole identifier counts too, since a developer may quote it verbatim.
    if name.lower() not in lowered:
        lowered.append(name.lower())
    return lowered


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
