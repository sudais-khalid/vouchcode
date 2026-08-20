"""Installation, inspection, and removal of the git hooks Vouchcode owns.

Design notes worth defending:

Two hooks, not one. pre-commit records the staged change set while the index still
describes it, and post-commit resolves the commit hash that only exists once the commit
is written. A single hook cannot observe both. The pre-commit half is also the point at
which Phase 3 will gate a commit on comprehension, so establishing it now avoids
restructuring the capture path later.

Absolute interpreter path. The hook records the interpreter that ran vouchcode init
rather than resolving python from PATH at commit time. Git executes hooks with a reduced
environment, and GUI git clients frequently run with a PATH that omits the virtual
environment the developer installed Vouchcode into. Recording the path makes the hook
work from any git frontend.

Ownership marker. Every generated hook carries HOOK_MARKER. A hook without that marker
belongs to the developer or another tool, and installation refuses to overwrite it
rather than destroying work that Vouchcode did not create.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from vouchcode.config import HOOK_MARKER, MANAGED_HOOKS, RepoContext
from vouchcode.errors import HookInstallationError

_HOOK_TEMPLATE = """#!/bin/sh
# {marker}
# Installed by vouchcode init. Edits to this file are lost on reinstall.
# Remove with: vouchcode uninstall
"{interpreter}" -m vouchcode.capture.runner {event}
"""


@dataclass(frozen=True)
class HookStatus:
    """The state of one managed hook path on disk."""

    name: str
    path: Path
    exists: bool
    owned: bool

    @property
    def blocked(self) -> bool:
        """Whether a foreign hook occupies the path Vouchcode needs."""
        return self.exists and not self.owned


def hook_status(ctx: RepoContext, name: str) -> HookStatus:
    """Report whether the named hook exists and whether Vouchcode owns it."""
    path = ctx.hooks_dir / name
    if not path.is_file():
        return HookStatus(name=name, path=path, exists=False, owned=False)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HookInstallationError(f"cannot read existing hook {path}: {exc}") from exc
    return HookStatus(
        name=name,
        path=path,
        exists=True,
        owned=HOOK_MARKER in content,
    )


def all_hook_statuses(ctx: RepoContext) -> list[HookStatus]:
    """Report the state of every hook Vouchcode manages."""
    return [hook_status(ctx, name) for name in MANAGED_HOOKS]


def render_hook(event: str, interpreter: str | None = None) -> str:
    """Render the shell source of the hook script for the given event.

    Kept separate from the write so that tests can assert on hook content without
    touching a real repository.
    """
    return _HOOK_TEMPLATE.format(
        marker=HOOK_MARKER,
        interpreter=_posix_interpreter_path(interpreter or sys.executable),
        event=event,
    )


def install_hooks(ctx: RepoContext, force: bool = False) -> list[HookStatus]:
    """Write every managed hook into the repository's hooks directory.

    Refuses to replace a hook Vouchcode does not own unless force is set. Returns the
    status of each hook as it stood before the write, so the caller can report which
    paths were newly created and which were replaced.
    """
    try:
        ctx.hooks_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HookInstallationError(
            f"cannot create hooks directory {ctx.hooks_dir}: {exc}"
        ) from exc

    previous = all_hook_statuses(ctx)

    blocked = [s for s in previous if s.blocked]
    if blocked and not force:
        names = ", ".join(str(s.path) for s in blocked)
        raise HookInstallationError(
            "refusing to overwrite existing hook(s) not created by vouchcode: "
            f"{names}. "
            "back them up and rerun with --force, or merge the vouchcode invocation "
            "into them by hand"
        )

    for status in previous:
        _write_hook(status.path, render_hook(status.name))

    return previous


def uninstall_hooks(ctx: RepoContext) -> list[HookStatus]:
    """Remove every managed hook Vouchcode owns, leaving foreign hooks in place.

    Returns the status of each hook as it stood before removal.
    """
    previous = all_hook_statuses(ctx)
    for status in previous:
        if status.exists and status.owned:
            try:
                status.path.unlink()
            except OSError as exc:
                raise HookInstallationError(
                    f"cannot remove hook {status.path}: {exc}"
                ) from exc
    return previous


def _write_hook(path: Path, content: str) -> None:
    """Write a hook script with LF line endings and the executable bit set.

    Line endings matter: git executes hooks through sh even on Windows, and a CRLF
    shebang line produces an interpreter-not-found failure at commit time.
    """
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except OSError as exc:
        raise HookInstallationError(f"cannot write hook {path}: {exc}") from exc

    _make_executable(path)


def _make_executable(path: Path) -> None:
    """Set the executable bit where the platform has one.

    Windows has no executable permission bit, and git for Windows runs hooks through sh
    regardless, so the call is a no-op there rather than an error.
    """
    if os.name == "nt":
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        raise HookInstallationError(
            f"cannot mark hook {path} as executable: {exc}"
        ) from exc


def _posix_interpreter_path(interpreter: str) -> str:
    """Convert an interpreter path into a form the hook's shell can execute.

    Git for Windows runs hooks under its bundled sh, which accepts a drive-letter path
    only with forward slashes. Backslashes there would be read as escape sequences.
    """
    return str(Path(interpreter)).replace("\\", "/")
