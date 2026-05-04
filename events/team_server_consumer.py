"""Periodic team-server event consumer.

Closes the pull→dispatch gap: pulls events from a team-server URL on
a fixed interval, bridges each event's payload to IngestPayload shape,
and invokes inner_adapter.ingest_payload directly. Bypasses JSONL —
team-server events have their own canonical home in the team-server's
SurrealDB; re-rendering as per-author JSONL files would be redundant.

Failure isolation: pull failures return [] (per pull_team_server_events
contract); per-event ingest failures are caught and logged so a single
malformed event doesn't kill the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from events.team_server_bridge import (
    bridge_team_server_payload,
    is_team_server_payload,
)
from events.team_server_pull import pull_team_server_events

logger = logging.getLogger(__name__)


async def consume_team_server_events_once(
    team_server_url: str,
    watermark_path: Path,
    inner_adapter,
    llm_extract_fn=None,
) -> int:
    """Pull + dispatch one batch. Returns the count of events ingested."""
    events = await pull_team_server_events(
        team_server_url=team_server_url,
        watermark_path=watermark_path,
    )
    ingested = 0
    for event in events:
        payload = event.get("payload") or {}
        if not is_team_server_payload(payload):
            continue
        bridged = bridge_team_server_payload(payload)
        if not bridged.get("decisions"):
            continue
        try:
            await inner_adapter.ingest_payload(bridged)
            ingested += 1
        except Exception:  # noqa: BLE001 — per-event isolation
            logger.exception(
                "[team-server-consumer] ingest failed for %s",
                payload.get("source_ref", "<unknown>"),
            )
    return ingested


def start_team_server_consumer_if_configured(
    adapter,
    *,
    watermark_path: Path | None = None,
) -> asyncio.Task | None:
    """Spawn the consumer loop if BICAMERAL_TEAM_SERVER_URL is set.
    Returns the task (caller cancels on shutdown) or None when off.

    Defensive unwrap: TeamWriteAdapter (returned by get_ledger() in
    team mode) wraps SurrealDBLedgerAdapter and emits 'ingest.completed'
    via self._writer.write(...) BEFORE delegating ingest_payload.
    Consumer-driven ingest must use the inner adapter to bypass the
    writer; if we used the wrapper, every team-server event would echo
    into per-dev JSONL → git push → other devs replay → O(N²) cross-dev
    replay amplification per team-server event. Audit-round-2 Finding A.
    """
    url = os.environ.get("BICAMERAL_TEAM_SERVER_URL", "").strip()
    if not url:
        return None
    inner_adapter = getattr(adapter, "_inner", adapter)
    interval = int(os.environ.get("BICAMERAL_TEAM_SERVER_PULL_INTERVAL_SECONDS", "60"))
    if watermark_path is None:
        data_path = os.environ.get(
            "BICAMERAL_DATA_PATH",
            os.environ.get("REPO_PATH", "."),
        )
        watermark_path = Path(data_path) / ".bicameral" / "local" / "team_server_watermark"
        watermark_path.parent.mkdir(parents=True, exist_ok=True)

    async def _loop():
        while True:
            try:
                ingested = await consume_team_server_events_once(
                    url,
                    watermark_path,
                    inner_adapter,
                )
                if ingested:
                    logger.info(
                        "[team-server-consumer] ingested %d events",
                        ingested,
                    )
            except Exception:  # noqa: BLE001
                logger.exception("[team-server-consumer] iteration failed")
            await asyncio.sleep(interval)

    return asyncio.create_task(_loop(), name="bicameral-team-server-consumer")
