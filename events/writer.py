"""EventJsonlWriter — append-only JSONL event log writer (v0.4.20).

Each contributor owns a single file: ``.bicameral/events/{email}.jsonl``.
Events are appended one per line. Git merges are additive (both sides
only append), and a single ``write()`` under O_APPEND is atomic for
lines up to PIPE_BUF (~4 KB on Linux / macOS) — we take an advisory
flock for anything larger.

Replaces the v0.4.13 one-file-per-event layout (content-addressable
JSON files under ``{email}/`` subdirectories). Dedup now relies on the
DB-level ``canonical_id`` UNIQUE index instead of filesystem collisions.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel, Field

if sys.platform != "win32":
    import fcntl
else:
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _lock_exclusive(fp: IO[bytes]) -> None:
    """Acquire an exclusive advisory lock on POSIX; no-op on Windows.

    JSONL lines for these events are well under PIPE_BUF (~4KB), so the
    OS-level append on a file opened "ab" is atomic on Windows even
    without explicit locking.
    """
    if fcntl is not None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)


def _unlock(fp: IO[bytes]) -> None:
    """Release the lock acquired by _lock_exclusive; no-op on Windows."""
    if fcntl is not None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


class EventEnvelope(BaseModel):
    """One event line in ``{email}.jsonl``."""
    schema_version: int = 2
    event_type: str
    author: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


def _get_git_email(repo_path: str | Path) -> str:
    """Get git user.email for the repo (falls back to 'unknown')."""
    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True, text=True, timeout=5, cwd=str(repo_path),
        )
        email = result.stdout.strip()
        if email:
            return email
    except (subprocess.SubprocessError, OSError):
        pass
    return "unknown"


class EventFileWriter:
    """Appends events to ``.bicameral/events/{author}.jsonl``."""

    def __init__(self, events_dir: Path, author_email: str) -> None:
        self._events_dir = events_dir
        self._author = author_email
        self._path = events_dir / f"{author_email}.jsonl"
        # In-process serialization: flock guards across processes; this Lock
        # guards across threads that share the same writer instance.
        self._mu = threading.Lock()
        events_dir.mkdir(parents=True, exist_ok=True)

    @property
    def author(self) -> str:
        return self._author

    @property
    def events_dir(self) -> Path:
        return self._events_dir

    @property
    def path(self) -> Path:
        return self._path

    def write(self, event_type: str, payload: dict[str, Any]) -> Path:
        """Append one event line. Returns the JSONL file path."""
        envelope = EventEnvelope(
            event_type=event_type, author=self._author, payload=payload,
        )
        line = json.dumps(envelope.model_dump(), separators=(",", ":"), default=str) + "\n"
        with self._mu:
            with open(self._path, "ab") as f:
                _lock_exclusive(f)
                try:
                    f.write(line.encode("utf-8"))
                finally:
                    _unlock(f)
        logger.debug("[events] appended %s to %s.jsonl", event_type, self._author)
        return self._path
