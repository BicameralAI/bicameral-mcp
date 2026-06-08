"""#170 — session-scoped implementation-intent state shared between hooks.

The PostToolUse capture reminder (``post_preflight_capture_reminder.py``)
should stay silent when the originating user prompt had no implementation
intent — a read-only prompt cannot be refining a surfaced decision, so the
disambiguation question is pure noise. The UserPromptSubmit hook
(``preflight_reminder.py``) already computes ``should_fire_preflight(prompt)``;
it persists that boolean here keyed by ``session_id``, and the capture hook
reads it.

Single source of truth for the handoff file. Pure filesystem; no ledger, no
network. Files are swept on a TTL so they garbage-collect across session
boundaries without a SessionEnd hook. Absence/unreadability reads as ``None``
(caller defaults to firing — missed capture is irreversible).
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path

_TTL_SECONDS = 86400  # 24h — longer than any plausible session; GC backstop
_DIR_NAME = "bicameral_prompt_intent"
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _state_dir() -> Path:
    return Path(tempfile.gettempdir()) / _DIR_NAME


def state_path(session_id: str) -> Path:
    """Per-session state file path. ``session_id`` is sanitized to a safe
    filename component (defends against path traversal via the harness-supplied
    id)."""
    safe = _SANITIZE_RE.sub("_", session_id)[:128] or "_"
    return _state_dir() / f"{safe}.json"


def _sweep_stale(now: float) -> None:
    directory = _state_dir()
    if not directory.is_dir():
        return
    for stale in directory.glob("*.json"):
        try:
            if now - stale.stat().st_mtime > _TTL_SECONDS:
                stale.unlink()
        except OSError:
            pass  # best-effort GC; never raise from a hook path


def write_intent(session_id: str, fire: bool) -> None:
    """Persist ``fire`` for ``session_id`` and sweep stale files. Best-effort:
    any filesystem error is swallowed (absence defaults to firing downstream)."""
    if not session_id:
        return
    now = time.time()
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        _sweep_stale(now)
        state_path(session_id).write_text(
            json.dumps({"fire": bool(fire), "ts": now}), encoding="utf-8"
        )
    except OSError:
        pass


def read_intent(session_id: str) -> bool | None:
    """Return the persisted ``fire`` boolean for ``session_id``, or ``None``
    when absent, unreadable, or malformed."""
    if not session_id:
        return None
    try:
        data = json.loads(state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    fire = data.get("fire") if isinstance(data, dict) else None
    return fire if isinstance(fire, bool) else None
