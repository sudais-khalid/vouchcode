"""Helpers for driving real git repositories and the Vouchcode CLI from tests.

Kept out of conftest.py and imported by module name so that the suite does not depend on
'tests' being importable as a package. An unrelated distribution can and does install a
top-level 'tests' package into site-packages, which would otherwise shadow this one.
"""

from __future__ import annotations

import subprocess
import sys
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
