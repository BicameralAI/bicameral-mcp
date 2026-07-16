"""Claude Code pre-work adapter.

Uses the documented Claude Code hooks mechanism: a ``SessionStart`` entry in the
user settings file (``~/.claude/settings.json``). Claude Code delivers a JSON
event on stdin at hook execution time. The ``SessionStart`` event is the genuine
pre-work boundary (verified firing under interactive and ``claude -p`` in
``tests/substrate_parity`` on Claude Code 2.1.x).

Evidence for this host is independent: it does not imply Codex support.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import HostAdapter, HostSessionEvent


class ClaudeCodeAdapter(HostAdapter):
    host_id = "claude"
    display_name = "Claude Code"
    official_mechanism = "Claude Code hooks (SessionStart) in settings.json"

    def default_home(self) -> Path:
        return Path.home() / ".claude"

    def config_path(self) -> Path:
        return self.home() / "settings.json"

    def parse_event(self, payload: dict[str, Any]) -> HostSessionEvent:
        session_id = str(payload.get("session_id") or "")
        source = str(payload.get("source") or "startup")
        cwd_raw = payload.get("cwd")
        cwd = str(cwd_raw) if isinstance(cwd_raw, str) and cwd_raw else None
        return HostSessionEvent(session_id=session_id, source=source, cwd=cwd)
