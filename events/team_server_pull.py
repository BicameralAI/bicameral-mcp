"""Per-dev pull from team-server's /events endpoint.

This module is OUTSIDE the deterministic core (per CONCEPT.md literal-
keyword parsing — `docs/SHADOW_GENOME.md` Failure Entry #6 addendum).
Network calls are permitted here; failures must NOT cascade into the
deterministic retrieval/status path.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def _read_watermark(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return 0


def _write_watermark(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value), encoding="utf-8")


async def pull_team_server_events(
    team_server_url: str,
    watermark_path: Path,
    *,
    timeout: float = 10.0,
) -> list[dict]:
    """Pull new events from `<team_server_url>/events?since=<watermark>`.
    On any HTTP failure or transport error, return [] and leave watermark
    unchanged. Failure-isolation contract: this function never raises."""
    since = _read_watermark(watermark_path)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{team_server_url}/events",
                params={"since": since, "limit": 1000},
                timeout=timeout,
            )
        events: list[dict] = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("team-server pull failed: %s", exc)
        return []
    if events:
        last_seq = max(int(e.get("sequence", since)) for e in events)
        _write_watermark(watermark_path, last_seq)
    return events
