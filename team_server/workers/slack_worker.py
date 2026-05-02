"""Slack ingest worker — polls allowlisted channels, runs canonical
extraction (upsert-keyed by source_ref), writes a peer-authored
team_event per change.

Idempotent: same Slack message ts with unchanged content yields no new
team_event row (the upsert returns changed=False on cache hit).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable, Iterable

from ledger.client import LedgerClient

from team_server.extraction.canonical_cache import upsert_canonical_extraction
from team_server.extraction.llm_extractor import INTERIM_MODEL_VERSION
from team_server.sync.peer_writer import write_team_event

logger = logging.getLogger(__name__)

Extractor = Callable[[str], Awaitable[dict]]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_ref_for_message(channel: str, ts: str) -> str:
    return f"{channel}/{ts}"


async def poll_once(
    db_client: LedgerClient,
    slack_client,
    workspace_team_id: str,
    channels: Iterable[str],
    extractor: Extractor,
) -> None:
    """One polling pass over allowlisted channels."""
    for channel in channels:
        history = slack_client.conversations_history(channel=channel)
        if not history.get("ok", False):
            logger.warning("[slack-worker] history failed for %s", channel)
            continue
        for message in history.get("messages", []):
            await _ingest_message(
                db_client, workspace_team_id, channel, message, extractor
            )


async def _ingest_message(
    db_client: LedgerClient,
    workspace_team_id: str,
    channel: str,
    message: dict,
    extractor: Extractor,
) -> None:
    text = message.get("text", "")
    ts = message.get("ts", "")
    source_ref = _source_ref_for_message(channel, ts)
    content_hash = _content_hash(text)
    extraction, changed = await upsert_canonical_extraction(
        db_client,
        source_type="slack",
        source_ref=source_ref,
        content_hash=content_hash,
        compute_fn=lambda: extractor(text),
        model_version=INTERIM_MODEL_VERSION,
    )
    if not changed:
        return
    await write_team_event(
        db_client,
        workspace_team_id=workspace_team_id,
        event_type="ingest",
        payload={
            "source_type": "slack",
            "source_ref": source_ref,
            "content_hash": content_hash,
            "extraction": extraction,
        },
    )
