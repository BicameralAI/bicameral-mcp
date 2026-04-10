"""EventFileWriter — atomic JSON event file writer.

Writes immutable event files to .bicameral/events/{author}/ with
timestamp-UUID filenames. Uses tmp+rename for atomic writes.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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
        """Write an event file atomically. Returns the path to the written file."""
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        short_uuid = uuid4().hex[:8]

        envelope = EventEnvelope(
            event_id=f"{ts}-{short_uuid}",
            event_type=event_type,
            author=self._author,
            timestamp=now,
            payload=payload,
        )

        filename = f"{ts}-{short_uuid}.json"
        path = self._user_dir / filename

        # Atomic write: tmp file then rename
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(envelope.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )
        tmp.rename(path)

        logger.info("[events] wrote %s/%s", self._author, filename)
        return path
