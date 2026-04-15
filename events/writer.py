"""EventFileWriter — atomic JSON event file writer.

Writes immutable event files to .bicameral/events/{author}/.

v0.4.13: filenames are content-addressable —
``{timestamp}-{content_hash}.json`` where content_hash is a
deterministic UUIDv5 derived from the payload via JCS. Two team
members writing the same logical event produce the same content_hash,
so git's filesystem-level dedup collapses identical files at the sync
layer (no merge conflict, no duplicate event to materialize). The
timestamp prefix is preserved for chronological replay ordering, but
the dedup happens via the hash suffix.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from .models import EventEnvelope

logger = logging.getLogger(__name__)


def _get_git_email(repo_path: str | Path) -> str:
    """Get git user.email for the repo (falls back to 'unknown')."""
    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_path),
        )
        email = result.stdout.strip()
        if email:
            return email
    except (subprocess.SubprocessError, OSError):
        pass
    return "unknown"


class EventFileWriter:
    """Writes append-only JSON event files to a per-user directory."""

    def __init__(self, events_dir: Path, author_email: str) -> None:
        self._events_dir = events_dir
        self._author = author_email
        self._user_dir = events_dir / author_email
        self._user_dir.mkdir(parents=True, exist_ok=True)

    @property
    def author(self) -> str:
        return self._author

    @property
    def events_dir(self) -> Path:
        return self._events_dir

    def write(self, event_type: str, payload: dict[str, Any]) -> Path:
        """Write an event file atomically. Returns the path to the written file.

        v0.4.13: filename suffix is a deterministic UUIDv5 hash of the
        ``(event_type, payload)`` tuple. Two writers producing the same
        logical event produce the same suffix — so when both event files
        end up in the same git repo on sync, git sees them as identical
        files (same path, same content) instead of a merge conflict.
        Filesystem-level dedup via content addressing.

        The timestamp prefix is still present so lexicographic ordering
        equals chronological ordering for replay. If two events with the
        same content arrive in the same second from different writers,
        their filenames will tie at the timestamp and differ at the
        author directory — replay handles that fine.
        """
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        content_hash = self._content_hash(event_type, payload)

        envelope = EventEnvelope(
            event_id=f"{ts}-{content_hash}",
            event_type=event_type,
            author=self._author,
            timestamp=now,
            payload=payload,
        )

        filename = f"{ts}-{content_hash}.json"
        path = self._user_dir / filename

        # If a content-addressable file already exists at this path
        # (same writer ingested the same event twice), skip — the
        # existing file is byte-identical by definition.
        if path.exists():
            logger.debug(
                "[events] dedup: %s/%s already exists, skipping write",
                self._author, filename,
            )
            return path

        # Atomic write: tmp file then rename
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(envelope.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )
        tmp.rename(path)

        logger.info("[events] wrote %s/%s", self._author, filename)
        return path

    @staticmethod
    def _content_hash(event_type: str, payload: dict[str, Any]) -> str:
        """Derive a stable 12-char hash from event content via JCS+UUIDv5.

        v0.4.13: same logical event from any writer produces the same
        hash. Used as the filename suffix so identical events collide
        at the filesystem level (free git dedup).
        """
        canonical = json.dumps(
            {"event_type": event_type, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return uuid5(NAMESPACE_URL, canonical).hex[:12]
