"""Helpers for driving real git repositories and the Vouchcode CLI from tests.

Kept out of conftest.py and imported by module name so that the suite does not depend on
'tests' being importable as a package. An unrelated distribution can and does install a
top-level 'tests' package into site-packages, which would otherwise shadow this one.
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import zlib
from pathlib import Path

# Repository under test, so that a hook running inside a temporary repository imports
# the working tree copy of vouchcode rather than requiring an installed one.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_git(
    args: list[str], cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run a git command in the given repository, raising on failure.

    Failures include stdout and stderr in the message, because a hook that fails during
    a commit reports through git's own output and would otherwise be invisible.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def run_vouchcode(
    args: list[str], cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the Vouchcode CLI as a subprocess in the given repository.

    Invoked through the module path rather than an installed console script so that the
    suite passes without an editable install being present.
    """
    return subprocess.run(
        [sys.executable, "-m", "vouchcode.cli", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def write_file(root: Path, relative: str, content: str) -> Path:
    """Write a file inside the repository, creating parent directories as needed."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def extract_pdf_text(pdf_path: Path) -> str:
    """Return the visible text of a PDF, for asserting on what a reader actually sees.

    ReportLab writes page content through an ASCII85 then Flate filter chain by default,
    so a naive search of the raw bytes finds nothing and would make an empty PDF and a
    correct one look identical. The decode order here matters and is applied with
    fallbacks, because a stream may be filtered either way or not at all.

    This parses only enough PDF to read text-showing operators. It is a test helper, not
    a PDF library, and it is deliberately tolerant: anything it cannot decode is skipped
    rather than raising, so one unusual stream cannot fail an assertion about another.
    """
    raw = pdf_path.read_bytes()
    chunks: list[bytes] = []
    position = 0

    while True:
        start = raw.find(b"stream", position)
        if start < 0:
            break
        if raw[max(0, start - 3) : start] == b"end":
            position = start + 6
            continue

        body_start = start + 6
        while raw[body_start : body_start + 1] in (b"\r", b"\n"):
            body_start += 1

        end = raw.find(b"endstream", body_start)
        if end < 0:
            break

        body = raw[body_start:end].strip()
        if body.endswith(b"~>"):
            body = body[:-2]

        for decode in (
            lambda data: zlib.decompress(base64.a85decode(data)),
            zlib.decompress,
            lambda data: data,
        ):
            try:
                chunks.append(decode(body))
                break
            except Exception:
                continue

        position = end + 9

    content = b"\n".join(chunks).decode("latin-1", errors="replace")
    return " ".join(re.findall(r"\(([^)]*)\)", content))
