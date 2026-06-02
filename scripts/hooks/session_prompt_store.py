"""Session-scoped prompt handoff for the preflight hook pair (#170).

The PostToolUse capture-reminder gate needs the user's prompt to classify it,
but PostToolUse payloads carry only ``tool_name``/``tool_input``/``tool_response``
— not the prompt. The UserPromptSubmit hook (which DOES receive the prompt)
writes it here keyed by Claude Code ``session_id``; the PostToolUse hook reads it
back; the SessionEnd hook deletes it.

All operations are best-effort and never raise — a broken handoff degrades to
"fire the reminder" (recall-biased), never to a crash that blocks the user.

Storage: ``<tempdir>/bicameral-prompts/<session_id>.txt``. To bound orphans from
sessions that crash without firing SessionEnd, ``write_session_prompt`` also
sweeps files older than ``_STALE_SECONDS`` on each write.
"""

from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path

_DIR_NAME = "bicameral-prompts"
_STALE_SECONDS = 24 * 60 * 60  # 24h — orphan-file staleness bound
_SAFE_SESSION = re.compile(r"[^A-Za-z0-9_.-]")


def _store_dir() -> Path:
    return Path(tempfile.gettempdir()) / _DIR_NAME


def _path_for(session_id: str) -> Path | None:
    sid = _SAFE_SESSION.sub("_", session_id.strip())
    if not sid:
        return None
    return _store_dir() / f"{sid}.txt"


def _sweep_stale(now: float | None = None) -> None:
    """Delete prompt files older than the staleness bound. Best-effort."""
    now = time.time() if now is None else now
    try:
        for f in _store_dir().glob("*.txt"):
            try:
                if now - f.stat().st_mtime > _STALE_SECONDS:
                    f.unlink()
            except OSError:
                continue
    except OSError:
        return


def write_session_prompt(session_id: str, prompt: str) -> None:
    """Persist ``prompt`` for ``session_id`` and sweep stale orphans."""
    if not session_id or not prompt:
        return
    path = _path_for(session_id)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
    except OSError:
        return
    _sweep_stale()


def read_session_prompt(session_id: str) -> str | None:
    """Return the stored prompt for ``session_id``, or None if unavailable."""
    if not session_id:
        return None
    path = _path_for(session_id)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def cleanup_session_prompt(session_id: str) -> None:
    """Delete the stored prompt for ``session_id`` (graceful SessionEnd path)."""
    if not session_id:
        return
    path = _path_for(session_id)
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        return
