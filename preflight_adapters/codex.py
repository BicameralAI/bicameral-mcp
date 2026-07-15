"""Codex CLI pre-work adapter.

Uses the documented Codex lifecycle hooks mechanism: a ``SessionStart`` entry in
the Codex hooks file (``$CODEX_HOME/hooks.json``, default ``~/.codex/hooks.json``).
Codex delivers a JSON event on stdin whose ``session-start`` input schema carries
``session_id``, ``cwd``, and a ``source`` of ``startup``/``resume``/``clear``/
``compact``. Only ``startup`` is treated as a genuine pre-work boundary.

Evidence for this host is independent: it does not imply Claude Code support.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import HostAdapter, HostSessionEvent


class CodexAdapter(HostAdapter):
    host_id = "codex"
    display_name = "Codex CLI"
    official_mechanism = "Codex lifecycle hooks (SessionStart) in hooks.json"

    def default_home(self) -> Path:
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            return Path(codex_home)
        return Path.home() / ".codex"

    def config_path(self) -> Path:
        return self.home() / "hooks.json"

    def parse_event(self, payload: dict[str, Any]) -> HostSessionEvent:
        session_id = str(payload.get("session_id") or "")
        source = str(payload.get("source") or "startup")
        cwd_raw = payload.get("cwd")
        cwd = str(cwd_raw) if isinstance(cwd_raw, str) and cwd_raw else None
        return HostSessionEvent(session_id=session_id, source=source, cwd=cwd)
