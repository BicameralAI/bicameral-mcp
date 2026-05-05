"""SessionEnd hook — copy the parent session's transcript to the queue.

Receives a JSON envelope on stdin from Claude Code's SessionEnd hook
contract: ``{"session_id": "...", "transcript_path": "...", "cwd": "...", ...}``.

Replaces the broken ``claude -p '/bicameral-capture-corrections --auto-ingest'``
canonical command (#156). Pure shell-style behavior: no claude subprocess,
no MCP config, no auth. Errors swallowed (exit 0) so a broken hook never
blocks a user from ending their session.

Invocation: ``python3 scripts/hooks/session_end_queue_writer.py`` from the
repo root (path-style, consistent with the other three hooks in
``.claude/settings.json``). The ``sys.path`` bootstrap below mirrors the
shape used by ``scripts/hooks/preflight_reminder.py:41-42`` so the
``from events.transcript_queue import write_pending`` import resolves
when invoked path-style.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from events.transcript_queue import write_pending  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    session_id = str(payload.get("session_id") or uuid.uuid4())
    transcript_path = str(payload.get("transcript_path", "")).strip()
    cwd = str(payload.get("cwd", "")).strip()
    if not transcript_path or not cwd:
        return 0
    try:
        write_pending(cwd, session_id, transcript_path)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
