"""Notion ingest worker — polls allowlist-via-share databases, runs
canonical extraction, writes a peer-authored team_event per change.

v3 cache contract: classifier_version="legacy-pre-v3" until pipeline
integration (Phase 4) supplies the real heuristic version.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable

import httpx

from ledger.client import LedgerClient

from team_server.auth import notion_client as nc
from team_server.extraction.canonical_cache import upsert_canonical_extraction
from team_server.extraction.llm_extractor import INTERIM_MODEL_VERSION
from team_server.extraction.notion_serializer import serialize_row
from team_server.sync.peer_writer import write_team_event

logger = logging.getLogger(__name__)

Extractor = Callable[[str], Awaitable[dict]]
SOURCE_TYPE = "notion_database_row"
PEER_WORKSPACE_ID = "notion"


async def poll_once(
    db_client: LedgerClient,
    token: str,
    extractor: Extractor,
) -> None:
    databases = await nc.list_databases(token)
    for db_id, _title in databases:
        await _poll_database(db_client, token, db_id, extractor)


async def _poll_database(
    db_client: LedgerClient, token: str, db_id: str, extractor: Extractor,
) -> None:
    watermark = await _load_watermark(db_client, db_id)
    last_advanced = watermark
    try:
        async for row in nc.query_database(token, db_id, watermark):
            await _ingest_row(db_client, token, db_id, row, extractor)
            last_advanced = row.get("last_edited_time", last_advanced)
    except httpx.HTTPError as exc:
        logger.warning("[notion-worker] db=%s aborted mid-iteration: %s", db_id, exc)
    finally:
        if last_advanced != watermark:
            await _store_watermark(db_client, db_id, last_advanced)


async def _ingest_row(
    db_client: LedgerClient,
    token: str,
    db_id: str,
    row: dict,
    extractor: Extractor,
) -> None:
    page_id = row["id"]
    blocks = await nc.fetch_page_blocks(token, page_id)
    text = serialize_row(row, blocks)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    source_ref = f"{db_id}/{page_id}"
    extraction, changed = await upsert_canonical_extraction(
        db_client,
        source_type=SOURCE_TYPE,
        source_ref=source_ref,
        content_hash=content_hash,
        classifier_version="legacy-pre-v3",
        compute_fn=lambda: extractor(text),
        model_version=INTERIM_MODEL_VERSION,
    )
    if not changed:
        return
    await write_team_event(
        db_client,
        workspace_team_id=PEER_WORKSPACE_ID,
        event_type="ingest",
        payload={
            "source_type": SOURCE_TYPE,
            "source_ref": source_ref,
            "content_hash": content_hash,
            "extraction": extraction,
        },
    )


async def _load_watermark(client: LedgerClient, db_id: str) -> str:
    rows = await client.query(
        "SELECT last_seen FROM source_watermark "
        "WHERE source_type = 'notion' AND resource_id = $rid LIMIT 1",
        {"rid": db_id},
    )
    return rows[0]["last_seen"] if rows else ""


async def _store_watermark(client: LedgerClient, db_id: str, value: str) -> None:
    existing = await client.query(
        "SELECT id FROM source_watermark "
        "WHERE source_type = 'notion' AND resource_id = $rid LIMIT 1",
        {"rid": db_id},
    )
    if existing:
        await client.query(
            "UPDATE source_watermark SET last_seen = $v, updated_at = time::now() "
            "WHERE source_type = 'notion' AND resource_id = $rid",
            {"rid": db_id, "v": value},
        )
    else:
        await client.query(
            "CREATE source_watermark CONTENT { source_type: 'notion', "
            "resource_id: $rid, last_seen: $v }",
            {"rid": db_id, "v": value},
        )
