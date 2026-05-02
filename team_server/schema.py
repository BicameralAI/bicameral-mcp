"""Team-server schema — self-managing migrations.

`ensure_schema(client)` is idempotent: safe to call on every startup.
Defines the v0 tables for the team-server's own state. Per audit
Advisory #3 (and the #72 lesson), nested-object fields use
`FLEXIBLE TYPE object` so SurrealDB v2 doesn't strip nested keys.

v2 (Notion v1 plan): cache contract upgraded to upsert-keyed-on
(source_type, source_ref); schema_version table records the post-
migration version as data, not folklore.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from ledger.client import LedgerClient

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

_BASE_STMTS: tuple[str, ...] = (
    # workspace — one row per Slack workspace.
    "DEFINE TABLE workspace SCHEMAFULL",
    "DEFINE FIELD name                  ON workspace TYPE string",
    "DEFINE FIELD slack_team_id         ON workspace TYPE string",
    "DEFINE FIELD oauth_token_encrypted ON workspace TYPE string",
    "DEFINE FIELD created_at            ON workspace TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_workspace_slack_team_id ON workspace FIELDS slack_team_id UNIQUE",

    # channel_allowlist — which Slack channels are ingested per workspace.
    "DEFINE TABLE channel_allowlist SCHEMAFULL",
    "DEFINE FIELD workspace_id ON channel_allowlist TYPE record<workspace>",
    "DEFINE FIELD channel_id   ON channel_allowlist TYPE string",
    "DEFINE FIELD channel_name ON channel_allowlist TYPE string DEFAULT ''",
    "DEFINE FIELD added_at     ON channel_allowlist TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_channel_allowlist_unique ON channel_allowlist FIELDS workspace_id, channel_id UNIQUE",

    # extraction_cache — canonical extraction per (source_type, source_ref).
    # v2: index keyed on (source_type, source_ref) only; content_hash is a
    # tracking column. The v1 (source_type, source_ref, content_hash)
    # index is dropped and redefined by _migrate_v1_to_v2.
    "DEFINE TABLE extraction_cache SCHEMAFULL",
    "DEFINE FIELD source_type            ON extraction_cache TYPE string",
    "DEFINE FIELD source_ref             ON extraction_cache TYPE string",
    "DEFINE FIELD content_hash           ON extraction_cache TYPE string",
    "DEFINE FIELD canonical_extraction   ON extraction_cache FLEXIBLE TYPE object DEFAULT {}",
    "DEFINE FIELD model_version          ON extraction_cache TYPE string",
    "DEFINE FIELD created_at             ON extraction_cache TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_extraction_cache_key ON extraction_cache FIELDS source_type, source_ref UNIQUE",

    # team_event — append-only event log.
    "DEFINE TABLE team_event SCHEMAFULL",
    "DEFINE FIELD author_email ON team_event TYPE string",
    "DEFINE FIELD event_type   ON team_event TYPE string",
    "DEFINE FIELD payload      ON team_event FLEXIBLE TYPE object DEFAULT {}",
    "DEFINE FIELD sequence     ON team_event TYPE int",
    "DEFINE FIELD created_at   ON team_event TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_team_event_sequence ON team_event FIELDS sequence",

    # source_watermark — generic per-source, per-resource watermark.
    # Used by polled sources (Notion v1; future sources reuse).
    "DEFINE TABLE source_watermark SCHEMAFULL",
    "DEFINE FIELD source_type ON source_watermark TYPE string",
    "DEFINE FIELD resource_id ON source_watermark TYPE string",
    "DEFINE FIELD last_seen   ON source_watermark TYPE string DEFAULT ''",
    "DEFINE FIELD updated_at  ON source_watermark TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_source_watermark_key ON source_watermark FIELDS source_type, resource_id UNIQUE",

    # schema_version — single-row table holding the current SCHEMA_VERSION.
    # DELETE-then-CREATE keeps the table at one row regardless of how
    # many times ensure_schema runs. Versioning is data, not folklore.
    "DEFINE TABLE schema_version SCHEMAFULL",
    "DEFINE FIELD version    ON schema_version TYPE int",
    "DEFINE FIELD updated_at ON schema_version TYPE datetime DEFAULT time::now()",
)


async def _migrate_v1_to_v2(client: LedgerClient) -> None:
    """Drop the v1 (source_type, source_ref, content_hash) UNIQUE index,
    dedup duplicates by max(created_at) per (source_type, source_ref),
    then redefine the index on (source_type, source_ref) UNIQUE.
    Idempotent: REMOVE INDEX is a no-op if the index doesn't exist;
    the dedup pass deletes nothing when no duplicates exist."""
    try:
        await client.query("REMOVE INDEX idx_extraction_cache_key ON extraction_cache")
    except Exception as exc:  # noqa: BLE001
        if "does not exist" not in str(exc).lower() and "not found" not in str(exc).lower():
            raise
    rows = await client.query(
        "SELECT id, source_type, source_ref, created_at FROM extraction_cache"
    )
    survivors: dict[tuple[str, str], dict] = {}
    for row in rows or []:
        key = (row["source_type"], row["source_ref"])
        prior = survivors.get(key)
        if prior is None or row["created_at"] > prior["created_at"]:
            survivors[key] = row
    survivor_ids = {r["id"] for r in survivors.values()}
    for row in rows or []:
        if row["id"] not in survivor_ids:
            # row["id"] comes back as "extraction_cache:<rid>"; split for type::thing
            tb, _, rid = str(row["id"]).partition(":")
            await client.query(
                "DELETE type::thing($tb, $rid)",
                {"tb": tb, "rid": rid},
            )
    await client.query(
        "DEFINE INDEX idx_extraction_cache_key ON extraction_cache "
        "FIELDS source_type, source_ref UNIQUE"
    )


_MIGRATIONS: dict[int, Callable[[LedgerClient], Awaitable[None]]] = {
    2: _migrate_v1_to_v2,
}


async def ensure_schema(client: LedgerClient) -> None:
    """Apply base schema (idempotent), run forward migrations, record version."""
    for stmt in _BASE_STMTS:
        try:
            await client.query(stmt)
        except Exception as exc:  # noqa: BLE001
            if "already exists" in str(exc).lower():
                continue
            raise
    for version in sorted(_MIGRATIONS):
        await _MIGRATIONS[version](client)
    # DELETE-then-CREATE keeps the table at one row regardless of how
    # many times ensure_schema runs.
    await client.query("DELETE schema_version")
    await client.query(
        "CREATE schema_version CONTENT { version: $v }",
        {"v": SCHEMA_VERSION},
    )
    logger.info("[team-server] schema ensured at version %s", SCHEMA_VERSION)
