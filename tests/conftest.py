"""Shared fixtures for the Vouchcode test suite.

Tests that exercise the capture layer run against real, throwaway git repositories
rather than mocks. The Phase 1 exit criterion is a claim about what happens when git
actually runs a hook, and a mocked git cannot substantiate it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from support import PROJECT_ROOT, run_git


@pytest.fixture
def git_env() -> dict[str, str]:
    """Environment for git subprocesses with deterministic identity and no user config.

    GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM are pointed at nothing so that the
    developer's own git configuration, including any core.hooksPath or commit.gpgsign
    setting, cannot change the outcome of a test run.
    """
    env = dict(os.environ)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test Author",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            # Ensure a hook subprocess resolves the working tree copy of the package.
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(PROJECT_ROOT), env.get("PYTHONPATH", "")) if p
            ),
        }
    )
    return env


@pytest.fixture
def temp_repo(tmp_path: Path, git_env: dict[str, str]) -> Path:
    """Create an empty git repository with a deterministic initial branch."""
    root = tmp_path / "repo"
    root.mkdir()
    run_git(["init", "--initial-branch=main"], cwd=root, env=git_env)
    return root
