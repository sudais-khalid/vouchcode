"""Repository discovery and the on-disk layout of Vouchcode state.

All Vouchcode state for a repository lives under a single .vouchcode/ directory at the
repository root. Nothing is written outside that directory except the managed git hooks
themselves, which must live in the git hooks path for git to execute them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from vouchcode.errors import NotAGitRepositoryError, NotInitializedError

VOUCHCODE_DIR_NAME = ".vouchcode"
LEDGER_FILE_NAME = "ledger.json"
PENDING_FILE_NAME = "pending.json"

# Bumped whenever the persisted ledger structure changes in a way that older readers
# cannot interpret. Phase 4 will add hash-chain and signature fields under a new
# version.
LEDGER_SCHEMA_VERSION = 1

# Hooks Vouchcode installs and owns. pre-commit captures the staged change set while it
# is still staged; post-commit resolves the resulting commit hash and writes the ledger
# entry. Both are required, because neither hook alone can observe both facts.
#
# post-merge exists because git does not run the commit hooks for a merge commit it
# creates itself. Without it, 'git merge --no-ff' leaves a commit in history with no
# ledger entry, and a silent gap in a provenance ledger is worse than a coarse record.
MANAGED_HOOKS: tuple[str, ...] = ("pre-commit", "post-commit", "post-merge")

# Marker line embedded in every generated hook. Its presence identifies a hook as
# Vouchcode-owned and therefore safe to overwrite on reinstall. Its absence means the
# hook belongs to the developer or another tool and must not be touched.
HOOK_MARKER = "vouchcode-managed-hook"


@dataclass(frozen=True)
class RepoContext:
    """Resolved locations for one repository under Vouchcode management."""

    root: Path
    git_dir: Path
    hooks_dir: Path

    @property
    def vouchcode_dir(self) -> Path:
        return self.root / VOUCHCODE_DIR_NAME

    @property
    def ledger_path(self) -> Path:
        return self.vouchcode_dir / LEDGER_FILE_NAME

    @property
    def pending_path(self) -> Path:
        return self.vouchcode_dir / PENDING_FILE_NAME

    @property
    def is_initialized(self) -> bool:
        return self.vouchcode_dir.is_dir()

    def require_initialized(self) -> None:
        """Raise if this repository has not been initialized with vouchcode init."""
        if not self.is_initialized:
            raise NotInitializedError(
                f"no {VOUCHCODE_DIR_NAME}/ directory in {self.root}. "
                "run 'vouchcode init' first"
            )


def _resolve_hooks_dir(repo: Repo) -> Path:
    """Return the directory git will look in for hook scripts.

    core.hooksPath overrides the default location, and honoring it matters because a
    hook written to .git/hooks would silently never run when that setting is present.
    """
    configured = repo.config_reader().get_value("core", "hooksPath", "")
    if configured:
        candidate = Path(str(configured)).expanduser()
        if not candidate.is_absolute():
            candidate = Path(repo.working_tree_dir or repo.git_dir) / candidate
        return candidate
    return Path(repo.git_dir) / "hooks"


def discover_repo(start: Path | None = None) -> RepoContext:
    """Locate the git repository containing start, defaulting to the current directory.

    Searches parent directories the same way git itself does.
    """
    start = Path(start or Path.cwd()).resolve()
    try:
        repo = Repo(start, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        raise NotAGitRepositoryError(
            f"not a git repository (or any parent up to mount point): {start}"
        ) from exc

    if repo.bare or repo.working_tree_dir is None:
        raise NotAGitRepositoryError(
            "vouchcode requires a repository with a working tree, "
            "and this repository is bare"
        )

    return RepoContext(
        root=Path(repo.working_tree_dir).resolve(),
        git_dir=Path(repo.git_dir).resolve(),
        hooks_dir=_resolve_hooks_dir(repo).resolve(),
    )
