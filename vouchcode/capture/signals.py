"""Direct AI tool session signals. Phase 2.

Primary attribution source per Section 4.1 of the research documentation. Where a
supported assistant records session or acceptance metadata on disk, that record is
authoritative and carries full confidence, because it states what was generated rather
than inferring it.

Reading a local session record written by an assistant is not a network call and does
not violate the zero external model dependency constraint. Querying an assistant's API
for an opinion about the code would violate it, and is out of scope permanently.

Not implemented in Phase 1. Phase 1's exit criterion is that the hook fires and the
ledger write succeeds, and partial attribution logic here would blur it.
"""

from __future__ import annotations

from typing import Any


def detect_session_signal(repo_root: Any, files: list[str]) -> None:
    """Return a direct-signal attribution record, or None when no signal is available.

    Phase 2. Raises until implemented so that a caller wired up early fails loudly
    rather than silently recording every commit as human-authored.
    """
    raise NotImplementedError("direct AI tool signal detection is Phase 2 work")
