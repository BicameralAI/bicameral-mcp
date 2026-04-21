"""SurrealDB schema for the decision ledger — v4 (v0.5.0 clean break).

Targets SurrealDB 2.x embedded via Python SDK (surrealdb>=1.0.0).
Uses SEARCH ANALYZER syntax (v2 compatible, not FULLTEXT ANALYZER which is v3+).

Graph shape (v4):
  Decision tier:  input_span -yields-> decision -binds_to-> code_region
  Retrieval tier: symbol -locates-> code_region

To initialise: await init_schema(client)
Schema migrations: await migrate(client)
"""

from __future__ import annotations

import logging

from .client import LedgerClient, LedgerError

logger = logging.getLogger(__name__)

# ── Schema version ──────────────────────────────────────────────────────
# v4 (v0.5.0): atomic clean break.
#   - source_span → input_span (verbatim text required, no DEFAULT)
#   - intent → decision (+ product_signoff double-entry axis)
#   - compliance_check.intent_id → decision_id (+ pruned flag)
#   - edges: yields(input_span→decision), binds_to(decision→code_region),
#             locates(symbol→code_region)
#   - removed: maps_to, implements
SCHEMA_VERSION = 4

# Analyzers
_ANALYZERS = [
    # Business language: transcripts, PRDs, decisions
    "DEFINE ANALYZER biz_analyzer TOKENIZERS blank, class, punct FILTERS lowercase, ascii, snowball(english)",
    # Code symbols: CamelCase + snake_case aware
    "DEFINE ANALYZER code_analyzer TOKENIZERS class, camel FILTERS lowercase, ascii",
]

# Core tables
_TABLES = [
    # ── Decision tier ────────────────────────────────────────────────────

    # input_span — raw verbatim text excerpt from a meeting, PRD, Slack, or
    # implementation-time rationale. "What was said / written."
    # text is required — no DEFAULT. A span without verbatim text is rejected
    # at the ingest contract boundary (IngestDecision.source_excerpt must be
    # non-empty). See v0.5.0 plan §Core Principle.
    "DEFINE TABLE input_span SCHEMAFULL",
    "DEFINE FIELD text           ON input_span TYPE string "
    "ASSERT string::len($value) > 0",
    "DEFINE FIELD source_type    ON input_span TYPE string",       # transcript | notion | slack | document | manual | implementation_choice
    "DEFINE FIELD source_ref     ON input_span TYPE string DEFAULT ''",  # meeting ID, page URL, etc.
    "DEFINE FIELD speakers       ON input_span TYPE array<string> DEFAULT []",
    "DEFINE FIELD meeting_date   ON input_span TYPE string DEFAULT ''",
    "DEFINE FIELD created_at     ON input_span TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_input_span_ref   ON input_span FIELDS source_type, source_ref",
    # Dedup: same excerpt from same source is the same span
    "DEFINE INDEX idx_input_span_dedup ON input_span FIELDS source_type, source_ref, text UNIQUE",

    # decision — extracted decision / requirement. "What was decided."
    # Denormalized source fields (source_type, source_ref, speakers, meeting_date)
    # are kept for query speed; they mirror the linked input_span but are never
    # derived from the span's text when the excerpt is missing.
    "DEFINE TABLE decision SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL",
    "DEFINE FIELD description    ON decision TYPE string",
    "DEFINE FIELD rationale      ON decision TYPE string DEFAULT ''",
    "DEFINE FIELD feature_hint   ON decision TYPE string DEFAULT ''",
    "DEFINE FIELD feature_group  ON decision TYPE option<string> DEFAULT NONE",
    "DEFINE FIELD source_type    ON decision TYPE string DEFAULT ''",
    "DEFINE FIELD source_ref     ON decision TYPE string DEFAULT ''",
    "DEFINE FIELD meeting_date   ON decision TYPE string DEFAULT ''",
    "DEFINE FIELD speakers       ON decision TYPE array<string> DEFAULT []",
    "DEFINE FIELD status         ON decision TYPE string DEFAULT 'ungrounded' "
    "ASSERT $value IN ['reflected', 'drifted', 'pending', 'ungrounded']",
    "DEFINE FIELD created_at     ON decision TYPE datetime DEFAULT time::now()",
    # v0.4.13-style content-addressable dedup; same derivation, renamed type
    "DEFINE FIELD canonical_id   ON decision TYPE string DEFAULT ''",
    # Double-entry axis — product_signoff is stored; eng_reflected is derived
    # from compliance_check aggregation at read time via project_decision_status.
    # Shape when set: {signer: str, timestamp: str, source_commit_ref: str, note: str}
    "DEFINE FIELD product_signoff ON decision FLEXIBLE TYPE option<object> DEFAULT NONE",
    "DEFINE INDEX idx_decision_canonical ON decision FIELDS canonical_id UNIQUE",
    "DEFINE INDEX idx_decision_fts ON decision FIELDS description "
    "SEARCH ANALYZER biz_analyzer BM25(1.2, 0.75) HIGHLIGHTS",
    # Powers the "awaiting signoff" PM dashboard queue
    "DEFINE INDEX idx_decision_signoff ON decision FIELDS product_signoff",

    # ── Shared / unchanged ──────────────────────────────────────────────

    # symbol — a named code entity (function, class, file). Retrieval-tier only.
    "DEFINE TABLE symbol SCHEMAFULL",
    "DEFINE FIELD name           ON symbol TYPE string",
    "DEFINE FIELD file_path      ON symbol TYPE string",
    "DEFINE FIELD sym_type       ON symbol TYPE string",
    "DEFINE FIELD last_seen      ON symbol TYPE datetime DEFAULT time::now()",
    "DEFINE FIELD hit_count      ON symbol TYPE int DEFAULT 0",
    "DEFINE INDEX idx_sym_name   ON symbol FIELDS name SEARCH ANALYZER code_analyzer BM25(1.2, 0.75)",
    "DEFINE INDEX idx_sym_file   ON symbol FIELDS file_path",

    # code_region — a specific span within a file. Shared between the two tiers:
    # decision tier addresses it via binds_to; retrieval tier via locates.
    "DEFINE TABLE code_region SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL",
    "DEFINE FIELD file_path      ON code_region TYPE string",
    "DEFINE FIELD symbol_name    ON code_region TYPE string",   # display-only metadata, not a graph edge target
    "DEFINE FIELD start_line     ON code_region TYPE int",
    "DEFINE FIELD end_line       ON code_region TYPE int",
    "DEFINE FIELD purpose        ON code_region TYPE string DEFAULT ''",
    "DEFINE FIELD repo           ON code_region TYPE string DEFAULT ''",
    "DEFINE FIELD pinned_commit  ON code_region TYPE string DEFAULT ''",
    "DEFINE FIELD content_hash   ON code_region TYPE string DEFAULT ''",
    "DEFINE INDEX idx_region_sym  ON code_region FIELDS symbol_name",
    "DEFINE INDEX idx_region_file ON code_region FIELDS repo, file_path",

    # vocab_cache — grounding reuse cache for query→code_region lookups
    "DEFINE TABLE vocab_cache SCHEMAFULL",
    "DEFINE FIELD query_text     ON vocab_cache TYPE string",
    "DEFINE FIELD repo           ON vocab_cache TYPE string",
    "DEFINE FIELD symbols        ON vocab_cache FLEXIBLE TYPE array DEFAULT []",
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

    # compliance_check — LLM verification cache.
    # Cache key: (decision_id, region_id, content_hash) — one verdict per code shape.
    # pruned=true means the caller said "not_relevant" — retrieval mistake, binds_to
    # edge has been deleted. Row kept for audit trail.
    "DEFINE TABLE compliance_check SCHEMAFULL",
    "DEFINE FIELD decision_id  ON compliance_check TYPE string",   # renamed from intent_id
    "DEFINE FIELD region_id    ON compliance_check TYPE string",
    "DEFINE FIELD content_hash ON compliance_check TYPE string",
    "DEFINE FIELD commit_hash  ON compliance_check TYPE string DEFAULT ''",
    "DEFINE FIELD verdict      ON compliance_check TYPE string "
    "ASSERT $value IN ['compliant', 'drifted', 'not_relevant']",
    "DEFINE FIELD pruned       ON compliance_check TYPE bool DEFAULT false",
    "DEFINE FIELD confidence   ON compliance_check TYPE string "
    "ASSERT $value IN ['high', 'medium', 'low']",
    "DEFINE FIELD explanation  ON compliance_check TYPE string DEFAULT ''",
    "DEFINE FIELD phase        ON compliance_check TYPE string "
    "ASSERT $value IN ['ingest', 'drift', 'regrounding', 'supersession', 'divergence'] "
    "DEFAULT 'drift'",
    "DEFINE FIELD checked_at   ON compliance_check TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_cc_cache_key ON compliance_check FIELDS decision_id, region_id, content_hash UNIQUE",
    "DEFINE INDEX idx_cc_decision  ON compliance_check FIELDS decision_id",
    "DEFINE INDEX idx_cc_region    ON compliance_check FIELDS region_id",
    "DEFINE INDEX idx_cc_commit    ON compliance_check FIELDS commit_hash",
]

# Edge tables — all with UNIQUE(in, out) for team-mode replay idempotency
#
# Decision tier:
#   input_span -yields-> decision    (extraction provenance, 1:N)
#   decision -binds_to-> code_region (decision is about this code, N:N)
#
# Retrieval tier:
#   symbol -locates-> code_region   (symbol body occupies this region at this commit)
_EDGES = [
    # input_span → decision (extraction provenance)
    "DEFINE TABLE yields SCHEMAFULL TYPE RELATION IN input_span OUT decision",
    "DEFINE FIELD created_at ON yields TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_yields_unique ON yields FIELDS in, out UNIQUE",

    # decision → code_region (direct binding — decision tier only)
    "DEFINE TABLE binds_to SCHEMAFULL TYPE RELATION IN decision OUT code_region",
    "DEFINE FIELD confidence ON binds_to TYPE float ASSERT $value >= 0 AND $value <= 1",
    "DEFINE FIELD provenance ON binds_to TYPE object DEFAULT {}",
    "DEFINE FIELD created_at ON binds_to TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_binds_to_unique ON binds_to FIELDS in, out UNIQUE",

    # symbol → code_region (retrieval tier — BM25 / graph / future embeddings)
    "DEFINE TABLE locates SCHEMAFULL TYPE RELATION IN symbol OUT code_region",
    "DEFINE FIELD confidence ON locates TYPE float ASSERT $value >= 0 AND $value <= 1",
    "DEFINE FIELD created_at ON locates TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_locates_unique ON locates FIELDS in, out UNIQUE",

    # code_region → code_region (structural dependency — unchanged)
    "DEFINE TABLE depends_on SCHEMAFULL TYPE RELATION IN code_region OUT code_region",
    "DEFINE FIELD edge_type  ON depends_on TYPE string",
    "DEFINE FIELD created_at ON depends_on TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_depends_on_unique ON depends_on FIELDS in, out, edge_type UNIQUE",
]

# Schema version tracking
_META = [
    "DEFINE TABLE schema_meta SCHEMAFULL",
    "DEFINE FIELD version     ON schema_meta TYPE int",
    "DEFINE FIELD migrated_at ON schema_meta TYPE datetime DEFAULT time::now()",
]


async def _execute_define_idempotent(client: LedgerClient, sql: str) -> None:
    """Run a DEFINE statement; treat "already exists" as success."""
    try:
        await client.execute(sql)
    except LedgerError as exc:
        if "already exists" not in str(exc):
            raise


async def init_schema(client: LedgerClient) -> None:
    """Create all tables, indexes, and analyzers.

    Idempotent: running against an already-initialized database is a
    no-op. Each DEFINE statement runs through _execute_define_idempotent
    so DDL bugs still surface loudly while "already exists" is a no-op.
    """
    for sql in (_ANALYZERS + _TABLES + _EDGES + _META):
        sql = sql.strip()
        if sql:
            await _execute_define_idempotent(client, sql)


# ── Migrations ──────────────────────────────────────────────────────────

async def _migrate_v0_to_v1(client: LedgerClient) -> None:
    """v0 → v1: Add source_span table + yields edge (non-breaking)."""
    logger.info("[migration] v0 → v1: source_span table + yields edge (non-breaking)")


async def _migrate_v1_to_v2(client: LedgerClient) -> None:
    """v1 → v2: vocab_cache.symbols needs FLEXIBLE TYPE array for nested objects."""
    try:
        await client.execute(
            "DEFINE FIELD symbols ON vocab_cache FLEXIBLE TYPE array DEFAULT []",
        )
    except LedgerError as exc:
        if "already exists" not in str(exc):
            raise
        logger.debug("[migration] v1 → v2: vocab_cache.symbols already FLEXIBLE — skipping")
    logger.info("[migration] v1 → v2: vocab_cache.symbols → FLEXIBLE TYPE array")


async def _migrate_v2_to_v3(client: LedgerClient) -> None:
    """v2 → v3: compliance_check table — LLM verification cache (non-breaking)."""
    logger.info("[migration] v2 → v3: compliance_check table (non-breaking)")


async def _migrate_v3_to_v4(client: LedgerClient) -> None:
    """v3 → v4: v0.5.0 decision-tier refactor — BREAKING / clean break.

    Drops legacy tables (intent, source_span, maps_to, implements, old yields).
    Recreates compliance_check with decision_id (renamed from intent_id).
    New tables (input_span, decision, binds_to, locates) already created
    by init_schema which ran before this migration.

    Source cursor rows survive — they guide replay of original ingests
    against the clean v4 schema.
    """
    logger.warning(
        "[migration] v3 → v4: Schema v4 is a clean break — legacy v3 data is "
        "dropped. Re-ingest from sources via bicameral.ingest. "
        "Run bicameral.reset to see the source_cursor replay plan."
    )

    # Drop legacy tables. REMOVE TABLE cascades indexes + data.
    for table in ("intent", "source_span", "maps_to", "implements"):
        try:
            await client.execute(f"REMOVE TABLE {table}")
            logger.debug("[migration] dropped table %s", table)
        except Exception as exc:
            logger.debug("[migration] drop %s skipped: %s", table, exc)

    # Drop old yields (source_span → intent) so init_schema's new
    # yields (input_span → decision) can take its place uncontested.
    try:
        await client.execute("REMOVE TABLE yields")
        logger.debug("[migration] dropped old yields edge")
    except Exception as exc:
        logger.debug("[migration] drop yields skipped: %s", exc)

    # Recreate yields with v4 endpoint types (init_schema already tried but
    # was blocked by "already exists" on the old table).
    for sql in [
        "DEFINE TABLE yields SCHEMAFULL TYPE RELATION IN input_span OUT decision",
        "DEFINE FIELD created_at ON yields TYPE datetime DEFAULT time::now()",
        "DEFINE INDEX idx_yields_unique ON yields FIELDS in, out UNIQUE",
    ]:
        await _execute_define_idempotent(client, sql)

    # Drop compliance_check (had intent_id) so init_schema's new definition
    # (decision_id + pruned field) can land cleanly.
    try:
        await client.execute("REMOVE TABLE compliance_check")
        logger.debug("[migration] dropped compliance_check for field rename")
    except Exception as exc:
        logger.debug("[migration] drop compliance_check skipped: %s", exc)

    # Recreate compliance_check with v4 fields.
    for sql in [
        "DEFINE TABLE compliance_check SCHEMAFULL",
        "DEFINE FIELD decision_id  ON compliance_check TYPE string",
        "DEFINE FIELD region_id    ON compliance_check TYPE string",
        "DEFINE FIELD content_hash ON compliance_check TYPE string",
        "DEFINE FIELD commit_hash  ON compliance_check TYPE string DEFAULT ''",
        "DEFINE FIELD verdict      ON compliance_check TYPE string "
        "ASSERT $value IN ['compliant', 'drifted', 'not_relevant']",
        "DEFINE FIELD pruned       ON compliance_check TYPE bool DEFAULT false",
        "DEFINE FIELD confidence   ON compliance_check TYPE string "
        "ASSERT $value IN ['high', 'medium', 'low']",
        "DEFINE FIELD explanation  ON compliance_check TYPE string DEFAULT ''",
        "DEFINE FIELD phase        ON compliance_check TYPE string "
        "ASSERT $value IN ['ingest', 'drift', 'regrounding', 'supersession', 'divergence'] "
        "DEFAULT 'drift'",
        "DEFINE FIELD checked_at   ON compliance_check TYPE datetime DEFAULT time::now()",
        "DEFINE INDEX idx_cc_cache_key ON compliance_check FIELDS decision_id, region_id, content_hash UNIQUE",
        "DEFINE INDEX idx_cc_decision  ON compliance_check FIELDS decision_id",
        "DEFINE INDEX idx_cc_region    ON compliance_check FIELDS region_id",
        "DEFINE INDEX idx_cc_commit    ON compliance_check FIELDS commit_hash",
    ]:
        await _execute_define_idempotent(client, sql)

    logger.info("[migration] v3 → v4: complete")


# Registry: version → migration function that brings DB from version-1 to version
_MIGRATIONS: dict[int, ...] = {
    1: _migrate_v0_to_v1,
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
    4: _migrate_v3_to_v4,
}


async def _get_schema_version(client: LedgerClient) -> int:
    """Read current schema version from DB. Returns 0 if no version tracked."""
    rows = await client.query("SELECT version FROM schema_meta LIMIT 1")
    if rows and rows[0].get("version") is not None:
        return int(rows[0]["version"])
    return 0


async def _set_schema_version(client: LedgerClient, version: int) -> None:
    """Upsert the schema version in schema_meta."""
    await client.execute("DELETE FROM schema_meta")
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
