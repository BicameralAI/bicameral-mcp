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

from .base import HostAdapter, HostConfigError, HostSessionEvent


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

    def _write_hook(self, runner_invocation: str) -> None:
        # Validate the operator-owned hooks file before creating the supporting
        # user config layer, preserving fail-closed behavior on malformed JSON.
        self._read_config()
        self._ensure_user_config_layer()
        super()._write_hook(runner_invocation)

    def _ensure_user_config_layer(self) -> None:
        """Make user hooks discoverable without bypassing Codex hook trust."""
        config = self.home() / "config.toml"
        if config.exists():
            return
        try:
            config.parent.mkdir(parents=True, exist_ok=True)
            config.open("xb").close()
        except FileExistsError:
            return
        except OSError as exc:
            raise HostConfigError(
                f"Cannot create Codex user config layer {config}; refusing to install the hook."
            ) from exc

    def parse_event(self, payload: dict[str, Any]) -> HostSessionEvent:
        session_id = str(payload.get("session_id") or "")
        source = str(payload.get("source") or "startup")
        cwd_raw = payload.get("cwd")
        cwd = str(cwd_raw) if isinstance(cwd_raw, str) and cwd_raw else None
        return HostSessionEvent(session_id=session_id, source=source, cwd=cwd)
