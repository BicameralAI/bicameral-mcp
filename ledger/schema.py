"""SurrealDB schema for the decision ledger.

Targets SurrealDB 2.x embedded via Python SDK (surrealdb>=1.0.0).
Uses SEARCH ANALYZER syntax (v2 compatible, not FULLTEXT ANALYZER which is v3+).

To initialise: await init_schema(client)
Schema migrations: await migrate(client)
"""

from __future__ import annotations

import logging

from .client import LedgerClient, LedgerError

logger = logging.getLogger(__name__)

# ── Schema version ──────────────────────────────────────────────────────
# Bump this when the schema changes. Each version gets a migration function
# in _MIGRATIONS. Non-breaking additions (new tables/fields) don't need a
# version bump — DEFINE is idempotent. Bump only for:
#   - Field type changes
#   - Constraint changes
#   - Table removals
#   - Anything that would break existing data
SCHEMA_VERSION = 3

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
    "DEFINE FIELD speakers       ON intent TYPE array<string> DEFAULT []",
    "DEFINE FIELD status         ON intent TYPE string DEFAULT 'ungrounded' "
    "ASSERT $value IN ['reflected', 'drifted', 'pending', 'ungrounded']",
    "DEFINE FIELD created_at     ON intent TYPE datetime DEFAULT time::now()",
    # v0.4.13: content-addressable ID derived from canonicalized
    # description + source_ref. Two team members ingesting the same
    # source independently produce the same canonical_id and dedupe at
    # the DB level via the UNIQUE index. See ledger/canonical.py.
    "DEFINE FIELD canonical_id   ON intent TYPE string DEFAULT ''",
    "DEFINE INDEX idx_intent_canonical ON intent FIELDS canonical_id UNIQUE",
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
    "DEFINE FIELD speakers       ON source_span TYPE array<string> DEFAULT []",
    "DEFINE FIELD meeting_date   ON source_span TYPE string DEFAULT ''",
    "DEFINE FIELD created_at     ON source_span TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_span_ref    ON source_span FIELDS source_type, source_ref",
    "DEFINE INDEX idx_span_dedup  ON source_span FIELDS source_type, source_ref, text UNIQUE",

    # vocab_cache — grounding reuse cache for query→code_region lookups.
    # Wired in handlers/ingest.py: read before ground_mappings(), written after.
    # Stores code_region-shaped dicts in the symbols array. Line numbers may
    # go stale but are validated against the live SymbolDB index on every hit.
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

    # compliance_check — LLM verification cache (plan: 2026-04-20-ingest-time-verification).
    # Every caller-session LLM judgment lands here: ingest-time grounding,
    # drift detection, re-grounding, supersession, divergence. Cache key is
    # (intent_id, region_id, content_hash) — the hash of the code the LLM
    # actually evaluated. Query path: look up this exact tuple; hit → reuse
    # verdict, miss → next drift sweep emits a pending_compliance_check.
    "DEFINE TABLE compliance_check SCHEMAFULL",
    "DEFINE FIELD intent_id    ON compliance_check TYPE string",
    "DEFINE FIELD region_id    ON compliance_check TYPE string",
    "DEFINE FIELD content_hash ON compliance_check TYPE string",
    "DEFINE FIELD commit_hash  ON compliance_check TYPE string DEFAULT ''",
    "DEFINE FIELD compliant    ON compliance_check TYPE bool",
    "DEFINE FIELD confidence   ON compliance_check TYPE string "
    "ASSERT $value IN ['high', 'medium', 'low']",
    "DEFINE FIELD explanation  ON compliance_check TYPE string DEFAULT ''",
    "DEFINE FIELD phase        ON compliance_check TYPE string "
    "ASSERT $value IN ['ingest', 'drift', 'regrounding', 'supersession', 'divergence'] "
    "DEFAULT 'drift'",
    "DEFINE FIELD checked_at   ON compliance_check TYPE datetime DEFAULT time::now()",
    # Cache key: exactly one verdict per (intent, region, content_hash).
    # Replaying the same batch is a no-op — foundational for idempotent resolve_compliance.
    "DEFINE INDEX idx_cc_cache_key ON compliance_check FIELDS intent_id, region_id, content_hash UNIQUE",
    "DEFINE INDEX idx_cc_intent    ON compliance_check FIELDS intent_id",
    "DEFINE INDEX idx_cc_region    ON compliance_check FIELDS region_id",
    "DEFINE INDEX idx_cc_commit    ON compliance_check FIELDS commit_hash",
]

# Edge tables
#
# v0.4.13: every edge table gets a UNIQUE index on (in, out) so the same
# logical relationship can never be created twice. Without this, team-mode
# replay creates duplicate edges every time peer event files are
# materialized — even when the endpoint nodes dedupe correctly. The
# UNIQUE index pushes idempotency into the DB layer so application code
# doesn't have to remember to check.
_EDGES = [
    # source_span → intent (extraction provenance)
    "DEFINE TABLE yields SCHEMAFULL TYPE RELATION IN source_span OUT intent",
    "DEFINE FIELD created_at ON yields TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_yields_unique ON yields FIELDS in, out UNIQUE",

    # intent → symbol (vocabulary bridge)
    "DEFINE TABLE maps_to SCHEMAFULL TYPE RELATION IN intent OUT symbol",
    "DEFINE FIELD confidence ON maps_to TYPE float ASSERT $value >= 0 AND $value <= 1",
    "DEFINE FIELD provenance ON maps_to TYPE object DEFAULT {}",
    "DEFINE FIELD created_at ON maps_to TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_maps_to_unique ON maps_to FIELDS in, out UNIQUE",

    # symbol → code_region (code locator result)
    "DEFINE TABLE implements SCHEMAFULL TYPE RELATION IN symbol OUT code_region",
    "DEFINE FIELD confidence ON implements TYPE float ASSERT $value >= 0 AND $value <= 1",
    "DEFINE FIELD verified   ON implements TYPE bool DEFAULT false",
    "DEFINE FIELD created_at ON implements TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_implements_unique ON implements FIELDS in, out UNIQUE",

    # code_region → code_region (structural dependency)
    # depends_on uses (in, out, edge_type) as the dedup key — different
    # edge types between the same regions ARE legitimate distinct edges
    # (e.g., A "calls" B AND A "imports from" B's module).
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
    """Run a DEFINE statement; treat "already exists" as success.

    SurrealDB v2 rejects ``DEFINE ANALYZER``/``TABLE``/``FIELD``/``INDEX``
    when the target already exists. Pre-v0.4.20 the client silently
    discarded that rejection, which masked the fact that the DDL was
    never actually idempotent. v0.4.20 made the client raise (Phase 1b);
    this helper re-establishes the idempotency the ``init_schema``
    docstring promises — surgically, so other ``execute`` paths keep
    surfacing real errors.

    Any SurrealDB rejection that is NOT "already exists" re-raises as
    ``LedgerError`` — schema bugs still surface loudly.
    """
    try:
        await client.execute(sql)
    except LedgerError as exc:
        if "already exists" not in str(exc):
            raise


async def init_schema(client: LedgerClient) -> None:
    """Create all tables, indexes, and analyzers.

    Idempotent: running against an already-initialized database is a
    no-op. Hardened in v0.4.22 after v0.4.20's Phase 1b exposed that the
    prior "idempotency" relied on the client silently discarding
    "already exists" rejections. Each DEFINE statement now runs through
    ``_execute_define_idempotent`` so we keep DDL idempotent while
    still raising on real DDL bugs.
    """
    for sql in (_ANALYZERS + _TABLES + _EDGES + _META):
        sql = sql.strip()
        if sql:
            await _execute_define_idempotent(client, sql)


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


async def _migrate_v1_to_v2(client: LedgerClient) -> None:
    """v1 → v2: vocab_cache.symbols needs FLEXIBLE TYPE array for nested objects.

    Re-define the field so existing DBs can store code_region-shaped dicts.
    init_schema() already includes the new DEFINE, so on fresh DBs this
    redefinition is redundant and SurrealDB v2 rejects it with "already
    exists." That rejection used to be silently discarded; now the client
    raises, so we swallow the specific "already exists" case here and
    re-raise anything else.
    """
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
    """v2 → v3: compliance_check table — LLM verification cache.

    Non-breaking: init_schema() already created the new table via DEFINE
    before migrate() runs. This migration just stamps the version so the
    schema_meta row reflects current code.

    Part of the unified compliance verification rollout
    (thoughts/shared/plans/2026-04-20-ingest-time-verification.md).
    """
    logger.info("[migration] v2 → v3: compliance_check table (non-breaking)")


# Registry: version → migration function that brings DB from version-1 to version
_MIGRATIONS: dict[int, ...] = {
    1: _migrate_v0_to_v1,
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
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
