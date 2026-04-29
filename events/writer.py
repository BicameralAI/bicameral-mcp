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
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Cross-platform advisory file lock for the event JSONL writer.
#
# Background: this module appends one line per event to a per-author
# ``.bicameral/events/{email}.jsonl`` file. A single ``write()`` under
# ``O_APPEND`` is atomic for lines up to PIPE_BUF (~4 KB on Linux/macOS),
# but events can exceed that, so we take an advisory exclusive lock for
# the duration of the write.
#
# POSIX (Linux, macOS): ``fcntl.flock(LOCK_EX)`` / ``LOCK_UN``.
# Windows: ``msvcrt.locking(LK_LOCK)`` / ``LK_UNLCK`` — needs a byte-range,
# so we lock 1 byte at the file's current position. Contention semantics
# are equivalent for the single-writer-per-author pattern this module uses.
#
# Both branches are ``# pragma: no cover`` for the inactive platform.
if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
    import msvcrt

    # On Windows, ``msvcrt.locking`` operates on a byte-range starting at
    # the current file position. We always lock byte 0 (the same byte for
    # every writer) so concurrent writers serialize on a shared mutex
    # byte. The actual append happens via ``open(..., "ab")``, which on
    # Windows seeks to EOF for each write — the byte-0 lock is the
    # serialization primitive, not a region lock.
    def _lock_exclusive(f: IO[bytes]) -> None:
        """Acquire an exclusive advisory lock on byte 0 (Windows)."""
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(f: IO[bytes]) -> None:
        """Release the advisory lock on byte 0 (Windows)."""
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_exclusive(f: IO[bytes]) -> None:
        """Acquire an exclusive advisory lock (POSIX)."""
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock(f: IO[bytes]) -> None:
        """Release the advisory lock (POSIX)."""
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class EventEnvelope(BaseModel):
    """One event line in ``{email}.jsonl``."""

    schema_version: int = 2
    event_type: str
    author: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


def _get_git_email(repo_path: str | Path) -> str:
    """Get git user.email for the repo (falls back to 'unknown')."""
    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_path),
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
            event_type=event_type,
            author=self._author,
            payload=payload,
        )
        line = json.dumps(envelope.model_dump(), separators=(",", ":"), default=str) + "\n"
        with open(self._path, "ab") as f:
            _lock_exclusive(f)
            try:
                f.write(line.encode("utf-8"))
            finally:
                _unlock(f)
        logger.debug("[events] appended %s to %s.jsonl", event_type, self._author)
        return self._path
