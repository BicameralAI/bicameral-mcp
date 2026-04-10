"""SurrealDB schema for the decision ledger.

Targets SurrealDB 2.x embedded via Python SDK (surrealdb>=1.0.0).
Uses SEARCH ANALYZER syntax (v2 compatible, not FULLTEXT ANALYZER which is v3+).

To initialise: await init_schema(client)
Schema migrations: await migrate(client)
"""

from __future__ import annotations

import logging

from .client import LedgerClient

logger = logging.getLogger(__name__)

# ── Schema version ──────────────────────────────────────────────────────
# Bump this when the schema changes. Each version gets a migration function
# in _MIGRATIONS. Non-breaking additions (new tables/fields) don't need a
# version bump — DEFINE is idempotent. Bump only for:
#   - Field type changes
#   - Constraint changes
#   - Table removals
#   - Anything that would break existing data
SCHEMA_VERSION = 1

# Analyzers
_ANALYZERS = [
    # Business language: transcripts, PRDs, intents
    "DEFINE ANALYZER biz_analyzer TOKENIZERS blank, class, punct FILTERS lowercase, ascii, snowball(english)",
    # Code symbols: CamelCase + snake_case aware
    "DEFINE ANALYZER code_analyzer TOKENIZERS class, camel FILTERS lowercase, ascii",
]

# Core tables
_TABLES = [
    # intent — a decision/requirement extracted from a meeting or PRD
    "DEFINE TABLE intent SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL",
    "DEFINE FIELD description    ON intent TYPE string",
    "DEFINE FIELD rationale      ON intent TYPE string DEFAULT ''",
    "DEFINE FIELD feature_hint   ON intent TYPE string DEFAULT ''",
    "DEFINE FIELD source_type    ON intent TYPE string",
    "DEFINE FIELD source_ref     ON intent TYPE string DEFAULT ''",
    "DEFINE FIELD meeting_date   ON intent TYPE string DEFAULT ''",
    "DEFINE FIELD speakers       ON intent TYPE array DEFAULT []",
    "DEFINE FIELD status         ON intent TYPE string DEFAULT 'ungrounded' "
    "ASSERT $value IN ['reflected', 'drifted', 'pending', 'ungrounded']",
    "DEFINE FIELD created_at     ON intent TYPE datetime DEFAULT time::now()",
    # BM25 full-text index on description (SEARCH ANALYZER = v2 syntax)
    "DEFINE INDEX idx_intent_fts ON intent FIELDS description SEARCH ANALYZER biz_analyzer BM25(1.2, 0.75) HIGHLIGHTS",

    # symbol — a named code entity (function, class, file)
    "DEFINE TABLE symbol SCHEMAFULL",
    "DEFINE FIELD name           ON symbol TYPE string",
    "DEFINE FIELD file_path      ON symbol TYPE string",
    "DEFINE FIELD sym_type       ON symbol TYPE string",
    "DEFINE FIELD last_seen      ON symbol TYPE datetime DEFAULT time::now()",
    "DEFINE FIELD hit_count      ON symbol TYPE int DEFAULT 0",
    "DEFINE INDEX idx_sym_name   ON symbol FIELDS name SEARCH ANALYZER code_analyzer BM25(1.2, 0.75)",
    "DEFINE INDEX idx_sym_file   ON symbol FIELDS file_path",

    # code_region — a specific span within a file (tied to a symbol)
    "DEFINE TABLE code_region SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL",
    "DEFINE FIELD file_path      ON code_region TYPE string",
    "DEFINE FIELD symbol_name    ON code_region TYPE string",
    "DEFINE FIELD start_line     ON code_region TYPE int",
    "DEFINE FIELD end_line       ON code_region TYPE int",
    "DEFINE FIELD purpose        ON code_region TYPE string DEFAULT ''",
    "DEFINE FIELD repo           ON code_region TYPE string DEFAULT ''",
    "DEFINE FIELD pinned_commit  ON code_region TYPE string DEFAULT ''",
    "DEFINE FIELD content_hash   ON code_region TYPE string DEFAULT ''",
    "DEFINE INDEX idx_region_sym  ON code_region FIELDS symbol_name",
    "DEFINE INDEX idx_region_file ON code_region FIELDS repo, file_path",

    # source_span — raw text excerpt from a meeting, PRD, or Slack message
    # Separates "what was said" (source_span) from "what was decided" (intent)
    # so that drift Layer 3 (LLM compliance) can evaluate against original context.
    "DEFINE TABLE source_span SCHEMAFULL",
    "DEFINE FIELD text           ON source_span TYPE string",
    "DEFINE FIELD source_type    ON source_span TYPE string",       # transcript | notion | slack | manual
    "DEFINE FIELD source_ref     ON source_span TYPE string DEFAULT ''",  # meeting ID, page URL, etc.
    "DEFINE FIELD speakers       ON source_span TYPE array DEFAULT []",
    "DEFINE FIELD meeting_date   ON source_span TYPE string DEFAULT ''",
    "DEFINE FIELD created_at     ON source_span TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_span_ref    ON source_span FIELDS source_type, source_ref",
    "DEFINE INDEX idx_span_dedup  ON source_span FIELDS source_type, source_ref, text UNIQUE",

    # vocab_cache — fast repeated query→symbols lookups
    "DEFINE TABLE vocab_cache SCHEMAFULL",
    "DEFINE FIELD query_text     ON vocab_cache TYPE string",
    "DEFINE FIELD repo           ON vocab_cache TYPE string",
    "DEFINE FIELD symbols        ON vocab_cache TYPE array DEFAULT []",
    "DEFINE FIELD hit_count      ON vocab_cache TYPE int DEFAULT 0",
    "DEFINE FIELD last_hit       ON vocab_cache TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_vocab_query ON vocab_cache FIELDS query_text SEARCH ANALYZER biz_analyzer BM25(1.2, 0.75)",
    "DEFINE INDEX idx_vocab_repo  ON vocab_cache FIELDS repo",

    # ledger_sync — idempotency cursor (last synced commit per repo)
    "DEFINE TABLE ledger_sync SCHEMAFULL",
    "DEFINE FIELD repo               ON ledger_sync TYPE string",
    "DEFINE FIELD last_synced_commit ON ledger_sync TYPE string",
    "DEFINE FIELD synced_at          ON ledger_sync TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_sync_repo ON ledger_sync FIELDS repo UNIQUE",

    # source_cursor — upstream ingestion checkpoint per source stream
    "DEFINE TABLE source_cursor SCHEMAFULL",
    "DEFINE FIELD repo            ON source_cursor TYPE string",
    "DEFINE FIELD source_type     ON source_cursor TYPE string",
    "DEFINE FIELD source_scope    ON source_cursor TYPE string DEFAULT 'default'",
    "DEFINE FIELD cursor          ON source_cursor TYPE string DEFAULT ''",
    "DEFINE FIELD last_source_ref ON source_cursor TYPE string DEFAULT ''",
    "DEFINE FIELD synced_at       ON source_cursor TYPE datetime DEFAULT time::now()",
    "DEFINE FIELD status          ON source_cursor TYPE string DEFAULT 'ok'",
    "DEFINE FIELD error           ON source_cursor TYPE string DEFAULT ''",
    "DEFINE INDEX idx_source_cursor ON source_cursor FIELDS repo, source_type, source_scope UNIQUE",
]

# Edge tables
_EDGES = [
    # source_span → intent (extraction provenance)
    "DEFINE TABLE yields SCHEMAFULL TYPE RELATION IN source_span OUT intent",
    "DEFINE FIELD created_at ON yields TYPE datetime DEFAULT time::now()",

    # intent → symbol (vocabulary bridge)
    "DEFINE TABLE maps_to SCHEMAFULL TYPE RELATION IN intent OUT symbol",
    "DEFINE FIELD confidence ON maps_to TYPE float ASSERT $value >= 0 AND $value <= 1",
    "DEFINE FIELD provenance ON maps_to TYPE object DEFAULT {}",
    "DEFINE FIELD created_at ON maps_to TYPE datetime DEFAULT time::now()",

    # symbol → code_region (code locator result)
    "DEFINE TABLE implements SCHEMAFULL TYPE RELATION IN symbol OUT code_region",
    "DEFINE FIELD confidence ON implements TYPE float ASSERT $value >= 0 AND $value <= 1",
    "DEFINE FIELD verified   ON implements TYPE bool DEFAULT false",
    "DEFINE FIELD created_at ON implements TYPE datetime DEFAULT time::now()",

    # code_region → code_region (structural dependency)
    "DEFINE TABLE depends_on SCHEMAFULL TYPE RELATION IN code_region OUT code_region",
    "DEFINE FIELD edge_type  ON depends_on TYPE string",
    "DEFINE FIELD created_at ON depends_on TYPE datetime DEFAULT time::now()",
]


# Schema version tracking
_META = [
    "DEFINE TABLE schema_meta SCHEMAFULL",
    "DEFINE FIELD version     ON schema_meta TYPE int",
    "DEFINE FIELD migrated_at ON schema_meta TYPE datetime DEFAULT time::now()",
]


async def init_schema(client: LedgerClient) -> None:
    """Create all tables, indexes, and analyzers. Idempotent (DEFINE is safe to re-run)."""
    all_statements = _ANALYZERS + _TABLES + _EDGES + _META
    await client.execute_many(all_statements)


# ── Migrations ──────────────────────────────────────────────────────────
# Each migration brings the DB from version N-1 to N.
# Non-breaking migrations (new tables/fields) just stamp the version.
# Breaking migrations clear affected tables and log a warning.
#
# Once Phase 1 (event-sourced collaboration) ships, breaking migrations
# become lossless: drop local DB → re-materialize from git events.


async def _migrate_v0_to_v1(client: LedgerClient) -> None:
    """v0 → v1: Add source_span table + yields edge + port interfaces.

    Non-breaking: init_schema() already created the new tables via DEFINE.
    This migration just stamps the version.
    """
    logger.info("[migration] v0 → v1: source_span table + yields edge (non-breaking)")


# Registry: version → migration function that brings DB from version-1 to version
_MIGRATIONS: dict[int, ...] = {
    1: _migrate_v0_to_v1,
}


async def _get_schema_version(client: LedgerClient) -> int:
    """Read current schema version from DB. Returns 0 if no version tracked."""
    rows = await client.query("SELECT version FROM schema_meta LIMIT 1")
    if rows and rows[0].get("version") is not None:
        return int(rows[0]["version"])
    return 0


async def _set_schema_version(client: LedgerClient, version: int) -> None:
    """Upsert the schema version in schema_meta."""
    await client.execute(
        "DELETE FROM schema_meta",
    )
    await client.execute(
        "CREATE schema_meta SET version = $v, migrated_at = time::now()",
        {"v": version},
    )


async def migrate(client: LedgerClient) -> None:
    """Run any pending migrations to bring the DB up to SCHEMA_VERSION.

    Called after init_schema() in adapter.connect(). Safe to call repeatedly.
    """
    current = await _get_schema_version(client)

    if current == SCHEMA_VERSION:
        return

    if current > SCHEMA_VERSION:
        logger.warning(
            "[migration] DB schema version %d is newer than code version %d. "
            "You may be running an older version of bicameral-mcp.",
            current, SCHEMA_VERSION,
        )
        return

    logger.info(
        "[migration] Schema version %d → %d (%d migration(s) to apply)",
        current, SCHEMA_VERSION, SCHEMA_VERSION - current,
    )

    for target_version in range(current + 1, SCHEMA_VERSION + 1):
        fn = _MIGRATIONS.get(target_version)
        if fn is None:
            logger.warning("[migration] No migration function for version %d", target_version)
            continue
        await fn(client)
        await _set_schema_version(client, target_version)

    logger.info("[migration] Schema migrated to version %d", SCHEMA_VERSION)
