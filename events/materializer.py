"""EventMaterializer — replays event files into the local ledger DB.

On startup in team mode, reads all event files from .bicameral/events/,
filters to events newer than the watermark, and replays them into the
SurrealDBLedgerAdapter in chronological order.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class EventMaterializer:
    """Watermark-based incremental event replay."""

    def __init__(self, events_dir: Path, local_dir: Path) -> None:
        self._events_dir = events_dir
        self._watermark_path = local_dir / "watermark"
        local_dir.mkdir(parents=True, exist_ok=True)

    def _read_watermark(self) -> str:
        """Read the last-materialized event timestamp (or empty string)."""
        if self._watermark_path.exists():
            return self._watermark_path.read_text(encoding="utf-8").strip()
        return ""

    def _write_watermark(self, timestamp: str) -> None:
        """Persist the watermark."""
        self._watermark_path.write_text(timestamp + "\n", encoding="utf-8")

    @staticmethod
    def _extract_timestamp(filename: str) -> str:
        """Extract the ISO timestamp prefix from an event filename.

        Filenames look like: 20260410T180000Z-a1b2c3d4.json
        Returns the timestamp portion: 20260410T180000Z
        """
        stem = filename.rsplit(".", 1)[0]  # strip .json
        return stem.rsplit("-", 1)[0]      # strip -uuid

    async def replay_new_events(self, inner_adapter) -> int:
        """Replay events newer than the watermark into the inner adapter.

        Args:
            inner_adapter: SurrealDBLedgerAdapter (must already be connected).

        Returns:
            Number of events replayed.
        """
        if not self._events_dir.exists():
            return 0

        watermark = self._read_watermark()

        # Glob all event files across all user directories
        event_files = sorted(
            self._events_dir.glob("*/*.json"),
            key=lambda f: f.name,  # lexicographic = chronological
        )

        # Filter to new events
        new_events = [
            f for f in event_files
            if self._extract_timestamp(f.name) > watermark
        ]

        if not new_events:
            return 0

        logger.info(
            "[materializer] replaying %d new events (watermark: %s)",
            len(new_events),
            watermark or "(none)",
        )

        replayed = 0
        for event_file in new_events:
            try:
                event = json.loads(event_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("[materializer] skipping bad event %s: %s", event_file, exc)
                continue

            event_type = event.get("event_type", "")
            payload = event.get("payload", {})

            if event_type == "ingest.completed":
                await inner_adapter.ingest_payload(payload)
                replayed += 1
            elif event_type == "link_commit.completed":
                await inner_adapter.ingest_commit(
                    payload.get("commit_hash", ""),
                    payload.get("repo_path", ""),
                )
                replayed += 1
            else:
                logger.warning("[materializer] unknown event type: %s", event_type)

        # Update watermark to the latest event timestamp
        latest_ts = self._extract_timestamp(new_events[-1].name)
        self._write_watermark(latest_ts)

        logger.info("[materializer] replayed %d events, watermark → %s", replayed, latest_ts)
        return replayed
