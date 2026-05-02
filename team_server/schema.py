"""Team-server schema — self-managing migrations.

`ensure_schema(client)` is idempotent: safe to call on every startup.
Defines the v0 tables for the team-server's own state. Per audit
Advisory #3 (and the #72 lesson), nested-object fields use
`FLEXIBLE TYPE object` so SurrealDB v2 doesn't strip nested keys.
"""

from __future__ import annotations

import logging

from ledger.client import LedgerClient

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# v0 schema. Append-only across versions; future migrations are added as
# `_migrate_v1_to_v2`, etc., dispatched through `_MIGRATIONS`.
_BASE_STMTS: tuple[str, ...] = (
    # workspace — one row per Slack workspace (single-workspace v0 still
    # uses the table for forward-compat with multi-workspace v1).
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

    # extraction_cache — canonical extraction per (source_type, source_ref, content_hash).
    # FLEXIBLE on canonical_extraction so nested decision dicts are preserved (#72 lesson).
    "DEFINE TABLE extraction_cache SCHEMAFULL",
    "DEFINE FIELD source_type            ON extraction_cache TYPE string",
    "DEFINE FIELD source_ref             ON extraction_cache TYPE string",
    "DEFINE FIELD content_hash           ON extraction_cache TYPE string",
    "DEFINE FIELD canonical_extraction   ON extraction_cache FLEXIBLE TYPE object DEFAULT {}",
    "DEFINE FIELD model_version          ON extraction_cache TYPE string",
    "DEFINE FIELD created_at             ON extraction_cache TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_extraction_cache_key ON extraction_cache FIELDS source_type, source_ref, content_hash UNIQUE",

    # team_event — append-only event log. FLEXIBLE on payload for the same reason.
    "DEFINE TABLE team_event SCHEMAFULL",
    "DEFINE FIELD author_email ON team_event TYPE string",
    "DEFINE FIELD event_type   ON team_event TYPE string",
    "DEFINE FIELD payload      ON team_event FLEXIBLE TYPE object DEFAULT {}",
    "DEFINE FIELD sequence     ON team_event TYPE int",
    "DEFINE FIELD created_at   ON team_event TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_team_event_sequence ON team_event FIELDS sequence",
)

_MIGRATIONS: dict[int, tuple[str, ...]] = {
    # 2: ("DEFINE FIELD ... new in v2",),
}


async def ensure_schema(client: LedgerClient) -> None:
    """Apply base schema (idempotent) and run any forward migrations."""
    for stmt in _BASE_STMTS:
        try:
            await client.query(stmt)
        except Exception as exc:
            # SurrealDB raises on duplicate DEFINE only when content differs;
            # idempotent re-define on identical statements succeeds. Log and
            # continue if the underlying error is a benign re-define.
            if "already exists" in str(exc).lower():
                continue
            raise
    for version in sorted(_MIGRATIONS):
        for stmt in _MIGRATIONS[version]:
            await client.query(stmt)
    logger.info("[team-server] schema ensured at version %s", SCHEMA_VERSION)
