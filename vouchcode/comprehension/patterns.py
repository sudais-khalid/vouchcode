"""Lexical patterns used by answer scoring.

Kept in their own module so the regular expressions are written once, as ordinary
source, rather than assembled by string manipulation, where an escape sequence can be
mangled into a control character leaving no visible trace in the file.
"""

from __future__ import annotations

import re

# Matches a named exception such as ValueError or KeyError, in code or in an answer.
EXCEPTION_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*(?:error|exception))\b")

# Word-like token, used to split an answer into comparable terms.
WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")

# Verbs that turn a following value into a claim about what the code returns. "None" on
# its own is usually the developer restating the guard condition, as in "if records is
# None"; only a return verb nearby makes it an assertion about the outcome.
RETURN_VERBS = frozenset(
    {"return", "returns", "returning", "returned", "gives", "give", "yields", "yield"}
)

# How far back to look for a return verb before treating a null-ish word as an outcome
# claim. Three tokens covers "returns an empty None" and stops well short of reaching
# into a separate clause.
RETURN_VERB_WINDOW = 3
