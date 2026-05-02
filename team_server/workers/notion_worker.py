"""Notion ingest worker — polls allowlist-via-share databases, runs
the extraction pipeline, writes peer-authored team_event per change.

Idempotent: same (db_id, page_id) with unchanged content + classifier
version yields no new event. Per-database watermark advances
monotonically; partial failures preserve watermark at the last
successfully-ingested row.

When `config` is None, falls back to the legacy `extractor(text)` path.
When `config` is provided, the pipeline runs with rules resolved per
database.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable, Optional

import httpx

from ledger.client import LedgerClient

from team_server.auth import notion_client as nc
from team_server.config import (
    RulesDisabled, TeamServerConfig, resolve_rules_for_notion,
)
from team_server.extraction.canonical_cache import upsert_canonical_extraction
from team_server.extraction.heuristic_classifier import derive_classifier_version
from team_server.extraction.llm_extractor import INTERIM_MODEL_VERSION
from team_server.extraction.notion_serializer import serialize_row
from team_server.extraction.pipeline import extract_decision_pipeline
from team_server.sync.peer_writer import write_team_event

logger = logging.getLogger(__name__)

Extractor = Callable[[str], Awaitable[dict]]
LLMExtractFn = Callable[[str, list], Awaitable[dict]]
SOURCE_TYPE = "notion_database_row"
PEER_WORKSPACE_ID = "notion"


async def poll_once(
    db_client: LedgerClient,
    token: str,
    extractor: Extractor,
    *,
    config: Optional[TeamServerConfig] = None,
    llm_extract_fn: Optional[LLMExtractFn] = None,
) -> None:
    databases = await nc.list_databases(token)
    for db_id, _title in databases:
        await _poll_database(
            db_client, token, db_id, extractor,
            config=config, llm_extract_fn=llm_extract_fn,
        )


async def _poll_database(
    db_client: LedgerClient,
    token: str,
    db_id: str,
    extractor: Extractor,
    *,
    config: Optional[TeamServerConfig],
    llm_extract_fn: Optional[LLMExtractFn],
) -> None:
    watermark = await _load_watermark(db_client, db_id)
    last_advanced = watermark
    try:
        async for row in nc.query_database(token, db_id, watermark):
            await _ingest_row(
                db_client, token, db_id, row, extractor,
                config=config, llm_extract_fn=llm_extract_fn,
            )
            last_advanced = row.get("last_edited_time", last_advanced)
    except httpx.HTTPError as exc:
        logger.warning("[notion-worker] db=%s aborted mid-iteration: %s", db_id, exc)
    finally:
        if last_advanced != watermark:
            await _store_watermark(db_client, db_id, last_advanced)


def _resolve_classifier_version(
    config: Optional[TeamServerConfig], db_id: str,
) -> tuple[str, object]:
    if config is None:
        return "legacy-pre-v3", None
    rules_or_disabled = resolve_rules_for_notion(config, db_id)
    if isinstance(rules_or_disabled, RulesDisabled):
        return "rules-disabled", rules_or_disabled
    return derive_classifier_version(rules_or_disabled), rules_or_disabled


def _notion_context(row: dict) -> dict:
    return {
        "last_edited_by": (row.get("last_edited_by") or {}).get("id"),
        "edit_count": row.get("edit_count"),
        "reactions": [],
        "thread_position": 0,
    }


async def _ingest_row(
    db_client: LedgerClient,
    token: str,
    db_id: str,
    row: dict,
    extractor: Extractor,
    *,
    config: Optional[TeamServerConfig],
    llm_extract_fn: Optional[LLMExtractFn],
) -> None:
    page_id = row["id"]
    blocks = await nc.fetch_page_blocks(token, page_id)
    text = serialize_row(row, blocks)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    source_ref = f"{db_id}/{page_id}"
    classifier_version, rules_or_disabled = _resolve_classifier_version(config, db_id)

    async def compute():
        if rules_or_disabled is None:
            return await extractor(text)
        return await extract_decision_pipeline(
            text=text, message=row, context=_notion_context(row),
            rules_or_disabled=rules_or_disabled,
            llm_extract_fn=llm_extract_fn,
        )

    extraction, changed = await upsert_canonical_extraction(
        db_client,
        source_type=SOURCE_TYPE,
        source_ref=source_ref,
        content_hash=content_hash,
        classifier_version=classifier_version,
        compute_fn=compute,
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
