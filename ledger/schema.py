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
SCHEMA_VERSION = 8

# Maps schema version → minimum bicameral-mcp code version that understands it.
# Used to produce actionable "upgrade your binary" messages.
SCHEMA_COMPATIBILITY: dict[int, str] = {
    4: "0.5.0",
    5: "0.6.0",
    6: "0.7.0",
    7: "0.8.0",
    8: "0.9.0",
}

# Migrations that drop or recreate tables/data. These are never auto-applied;
# the user must explicitly confirm via bicameral_reset(confirm=True).
DESTRUCTIVE_MIGRATIONS: frozenset[int] = frozenset()


class DestructiveMigrationRequired(LedgerError):
    """Pending migration step drops data and requires explicit user confirmation.

    Raise when a destructive migration is pending and allow_destructive=False.
    Callers should surface: "run bicameral_reset(confirm=True) to proceed."
    """


class SchemaVersionTooNew(LedgerError):
    """DB schema version is newer than the running code understands.

    Raised when the DB was written by a newer binary. User must upgrade.
    """


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
    "ASSERT $value IN ['reflected', 'drifted', 'pending', 'ungrounded', 'proposal', 'superseded', 'context_pending']",
    "DEFINE FIELD created_at     ON decision TYPE datetime DEFAULT time::now()",
    # v0.4.13-style content-addressable dedup; same derivation, renamed type
    "DEFINE FIELD canonical_id   ON decision TYPE string DEFAULT ''",
    # Double-entry axis — signoff is stored; eng_reflected is derived
    # from compliance_check aggregation at read time via project_decision_status.
    # Shape: {state: 'proposed'|'ratified', session_id, created_at/ratified_at, signer?, note?}
    "DEFINE FIELD signoff ON decision FLEXIBLE TYPE option<object> DEFAULT NONE",
    "DEFINE INDEX idx_decision_canonical ON decision FIELDS canonical_id UNIQUE",
    "DEFINE INDEX idx_decision_fts ON decision FIELDS description "
    "SEARCH ANALYZER biz_analyzer BM25(1.2, 0.75) HIGHLIGHTS",
    # Powers the "awaiting signoff" PM dashboard queue
    "DEFINE INDEX idx_decision_signoff ON decision FIELDS signoff",

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
    "DEFINE FIELD ephemeral    ON compliance_check TYPE bool DEFAULT false",
    "DEFINE INDEX idx_cc_cache_key ON compliance_check FIELDS decision_id, region_id, content_hash UNIQUE",
    "DEFINE INDEX idx_cc_decision  ON compliance_check FIELDS decision_id",
    "DEFINE INDEX idx_cc_region    ON compliance_check FIELDS region_id",
    "DEFINE INDEX idx_cc_commit    ON compliance_check FIELDS commit_hash",
    "DEFINE INDEX idx_cc_ephemeral ON compliance_check FIELDS ephemeral",

    # graph_proposal — AI-generated edge proposals for human review.
    # from_id / to_id are TYPE string (not TYPE record) because this table can
    # link across different node types. Traverse via type::thing($from_id).
    # AI does NOT write here yet (schema-only for v0.8.x infrastructure).
    "DEFINE TABLE graph_proposal SCHEMAFULL",
    "DEFINE FIELD proposal_type ON graph_proposal TYPE string "
    "ASSERT $value IN ['context_for', 'supersedes', 'related_to', 'contradicts']",
    "DEFINE FIELD from_id       ON graph_proposal TYPE string",
    "DEFINE FIELD to_id         ON graph_proposal TYPE string",
    "DEFINE FIELD reason        ON graph_proposal TYPE string DEFAULT ''",
    "DEFINE FIELD confidence    ON graph_proposal TYPE float",
    "DEFINE FIELD state         ON graph_proposal TYPE string "
    "ASSERT $value IN ['pending', 'approved', 'rejected', 'auto_approved'] DEFAULT 'pending'",
    "DEFINE FIELD session_id    ON graph_proposal TYPE string DEFAULT ''",
    "DEFINE FIELD created_at    ON graph_proposal TYPE datetime DEFAULT time::now()",
    "DEFINE FIELD reviewed_at   ON graph_proposal TYPE option<datetime> DEFAULT NONE",
]

# Edge tables — all with UNIQUE(in, out) for team-mode replay idempotency
#
# Decision tier:
#   input_span -yields-> decision    (extraction provenance, 1:N)
#   decision -binds_to-> code_region (decision is about this code, N:N)
#
# HITL edges (v0.8.0):
#   decision -supersedes-> decision  (human-confirmed supersession)
#   input_span -context_for-> decision (human-confirmed context provision)
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

    # decision → decision (human-confirmed supersession — v0.8.0 HITL)
    "DEFINE TABLE supersedes SCHEMAFULL TYPE RELATION IN decision OUT decision",
    "DEFINE FIELD confidence  ON supersedes TYPE float",
    "DEFINE FIELD reason      ON supersedes TYPE string DEFAULT ''",
    "DEFINE FIELD created_at  ON supersedes TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_supersedes_unique ON supersedes FIELDS in, out UNIQUE",

    # input_span → decision (human-confirmed context provision — v0.8.0 HITL)
    "DEFINE TABLE context_for SCHEMAFULL TYPE RELATION IN input_span OUT decision",
    "DEFINE FIELD relevance_score ON context_for TYPE float",
    "DEFINE FIELD reason          ON context_for TYPE string DEFAULT ''",
    "DEFINE FIELD state           ON context_for TYPE string "
    "ASSERT $value IN ['proposed', 'confirmed', 'rejected'] DEFAULT 'proposed'",
    "DEFINE FIELD created_at      ON context_for TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_ctx_unique ON context_for FIELDS in, out UNIQUE",

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


def _with_overwrite(sql: str) -> str:
    """Inject OVERWRITE into a DEFINE statement so it updates existing definitions.

    Transforms e.g. "DEFINE FIELD status ON ..." into
    "DEFINE FIELD status OVERWRITE ON ..." so that init_schema always applies
    the current field constraints (ASSERT clauses, DEFAULT values, TYPE) even
    when the field already exists in the DB.
    """
    for keyword in ("DEFINE TABLE", "DEFINE FIELD", "DEFINE INDEX", "DEFINE ANALYZER", "DEFINE EVENT"):
        if sql.upper().startswith(keyword) and "OVERWRITE" not in sql.upper():
            return keyword + " OVERWRITE" + sql[len(keyword):]
    return sql


async def _execute_define_idempotent(client: LedgerClient, sql: str) -> None:
    """Run a DEFINE statement; treat "already exists" / "already contains" as success.

    "already contains" is SurrealDB's error when a UNIQUE index is attempted on
    a table that already has duplicate rows. This lets the server start so the
    migration that cleans stale data can run; the migration re-issues the index.
    """
    try:
        await client.execute(sql)
    except LedgerError as exc:
        msg = str(exc)
        if "already exists" not in msg and "already contains" not in msg:
            raise
        if "already contains" in msg:
            logger.warning(
                "[schema] DEFINE INDEX skipped — existing data violates UNIQUE "
                "constraint (%s). Migration will clean stale rows and re-apply.",
                sql.split("ON")[0].strip(),
            )


async def init_schema(client: LedgerClient) -> None:
    """Create or update all tables, indexes, and analyzers.

    Uses OVERWRITE on every DEFINE statement so field constraints (ASSERT
    clauses, DEFAULT values, TYPE) are always brought up to the current schema
    definition — even when running against a DB created by an older version.
    """
    for sql in (_ANALYZERS + _TABLES + _EDGES + _META):
        sql = sql.strip()
        if sql:
            await _execute_define_idempotent(client, _with_overwrite(sql))


# ── Migrations ──────────────────────────────────────────────────────────

async def _migrate_v4_to_v5(client: LedgerClient) -> None:
    """v4 → v5: Remove stale v3-era yields edges and deduplicate.

    Some DBs that went through v3→v4 still have residual source_span→intent
    edges in the yields table (the REMOVE TABLE in v3→v4 silently failed).
    Those stale edges prevent DEFINE INDEX idx_yields_unique from being
    applied, which broke startup. This migration:

      1. Deletes any yields edge whose `in` is a source_span record
         or whose `out` is an intent record (v3-era types).
      2. Deduplicates remaining yields edges by (in, out), keeping
         the first-seen record per pair.
      3. Re-applies the unique index now that the table is clean.
    """
    # Step 1: Remove stale v3 edges
    try:
        stale = await client.query(
            "SELECT id FROM yields "
            "WHERE string::starts_with(type::string(in), 'source_span:') "
            "   OR string::starts_with(type::string(out), 'intent:')"
        )
        for row in (stale or []):
            try:
                await client.execute(f"DELETE {row['id']}")
            except Exception:
                pass
        logger.info(
            "[migration] v4 → v5: removed %d stale v3 yields edges",
            len(stale or []),
        )
    except Exception as exc:
        logger.warning("[migration] v4 → v5: stale-edge cleanup failed: %s", exc)

    # Step 2: Deduplicate remaining yields by (in, out)
    try:
        all_yields = await client.query("SELECT id, in, out FROM yields")
        seen: set[tuple[str, str]] = set()
        removed = 0
        for row in (all_yields or []):
            key = (str(row.get("in", "")), str(row.get("out", "")))
            if key in seen:
                try:
                    await client.execute(f"DELETE {row['id']}")
                    removed += 1
                except Exception:
                    pass
            else:
                seen.add(key)
        if removed:
            logger.info("[migration] v4 → v5: removed %d duplicate yields edges", removed)
    except Exception as exc:
        logger.warning("[migration] v4 → v5: dedup failed: %s", exc)

    # Step 3: Re-apply the unique index now that the table is clean
    for sql in [
        "DEFINE INDEX idx_yields_unique ON yields FIELDS in, out UNIQUE",
    ]:
        await _execute_define_idempotent(client, sql)

    logger.info("[migration] v4 → v5: yields table clean, unique index applied")


async def _migrate_v5_to_v6(client: LedgerClient) -> None:
    """v5 → v6: Rename product_signoff → signoff + tag historical records.

    Historical migration policy:
    - product_signoff = {...}  →  signoff = {state:'ratified', signer, ratified_at, session_id:null, note}
    - product_signoff = None with bindings  →  signoff = {state:'ratified', signer:'legacy-migration', ...}
    - product_signoff = None without bindings  →  signoff stays None (ungrounded)

    New ingests after v0.7.0 write signoff = {state:'proposed', ...} by default.
    """
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        all_decisions = await client.query(
            "SELECT id, product_signoff FROM decision"
        )
        migrated = 0
        for row in (all_decisions or []):
            decision_id = str(row.get("id", ""))
            old_signoff = row.get("product_signoff")

            if old_signoff and isinstance(old_signoff, dict):
                # Had explicit product_signoff → tag as ratified
                new_signoff = {
                    "state": "ratified",
                    "signer": old_signoff.get("signer", "unknown"),
                    "ratified_at": old_signoff.get("timestamp", now_iso),
                    "session_id": None,
                    "note": old_signoff.get("note", ""),
                    "source_commit_ref": old_signoff.get("source_commit_ref", ""),
                }
                try:
                    await client.execute(
                        f"UPDATE {decision_id} SET signoff = $s",
                        {"s": new_signoff},
                    )
                    migrated += 1
                except Exception as exc:
                    logger.warning("[migration] v5→v6: failed to migrate %s: %s", decision_id, exc)
            elif old_signoff is None:
                # Check for bindings — implicitly-ratified decisions get legacy-migration tag
                bindings = await client.query(
                    f"SELECT count() AS n FROM binds_to WHERE in = {decision_id} LIMIT 1"
                )
                has_bindings = bindings and int((bindings[0] or {}).get("n", 0)) > 0
                if has_bindings:
                    new_signoff = {
                        "state": "ratified",
                        "signer": "legacy-migration",
                        "ratified_at": now_iso,
                        "session_id": None,
                        "note": "",
                    }
                    try:
                        await client.execute(
                            f"UPDATE {decision_id} SET signoff = $s",
                            {"s": new_signoff},
                        )
                        migrated += 1
                    except Exception as exc:
                        logger.warning("[migration] v5→v6: failed to tag %s: %s", decision_id, exc)

        logger.info("[migration] v5 → v6: migrated %d decision signoff records", migrated)
    except Exception as exc:
        logger.warning("[migration] v5 → v6: signoff migration failed: %s", exc)

    # Re-apply index under new field name
    await _execute_define_idempotent(
        client, "DEFINE INDEX idx_decision_signoff ON decision FIELDS signoff"
    )
    logger.info("[migration] v5 → v6: signoff field indexed")


async def _migrate_v6_to_v7(client: LedgerClient) -> None:
    """v6 → v7: Add HITL graph edges and graph_proposal table.

    Additive only — no data loss. Defines new tables and updates the
    decision.status ASSERT to include 'superseded' and 'context_pending'.
    """
    new_stmts = [
        # New HITL edge tables
        "DEFINE TABLE supersedes SCHEMAFULL TYPE RELATION IN decision OUT decision",
        "DEFINE FIELD confidence  ON supersedes TYPE float",
        "DEFINE FIELD reason      ON supersedes TYPE string DEFAULT ''",
        "DEFINE FIELD created_at  ON supersedes TYPE datetime DEFAULT time::now()",
        "DEFINE INDEX idx_supersedes_unique ON supersedes FIELDS in, out UNIQUE",

        "DEFINE TABLE context_for SCHEMAFULL TYPE RELATION IN input_span OUT decision",
        "DEFINE FIELD relevance_score ON context_for TYPE float",
        "DEFINE FIELD reason          ON context_for TYPE string DEFAULT ''",
        "DEFINE FIELD state           ON context_for TYPE string "
        "ASSERT $value IN ['proposed', 'confirmed', 'rejected'] DEFAULT 'proposed'",
        "DEFINE FIELD created_at      ON context_for TYPE datetime DEFAULT time::now()",
        "DEFINE INDEX idx_ctx_unique ON context_for FIELDS in, out UNIQUE",

        # Proposal infrastructure (AI does not write here yet)
        "DEFINE TABLE graph_proposal SCHEMAFULL",
        "DEFINE FIELD proposal_type ON graph_proposal TYPE string "
        "ASSERT $value IN ['context_for', 'supersedes', 'related_to', 'contradicts']",
        "DEFINE FIELD from_id       ON graph_proposal TYPE string",
        "DEFINE FIELD to_id         ON graph_proposal TYPE string",
        "DEFINE FIELD reason        ON graph_proposal TYPE string DEFAULT ''",
        "DEFINE FIELD confidence    ON graph_proposal TYPE float",
        "DEFINE FIELD state         ON graph_proposal TYPE string "
        "ASSERT $value IN ['pending', 'approved', 'rejected', 'auto_approved'] DEFAULT 'pending'",
        "DEFINE FIELD session_id    ON graph_proposal TYPE string DEFAULT ''",
        "DEFINE FIELD created_at    ON graph_proposal TYPE datetime DEFAULT time::now()",
        "DEFINE FIELD reviewed_at   ON graph_proposal TYPE option<datetime> DEFAULT NONE",

        # Expanded status ASSERT (additive — existing values remain valid)
        "DEFINE FIELD status ON decision TYPE string DEFAULT 'ungrounded' "
        "ASSERT $value IN ['reflected', 'drifted', 'pending', 'ungrounded', "
        "'proposal', 'superseded', 'context_pending']",
    ]
    for sql in new_stmts:
        await _execute_define_idempotent(client, sql.strip())
    logger.info("[migration] v6 → v7: HITL edge tables and graph_proposal defined")


async def _migrate_v7_to_v8(client: LedgerClient) -> None:
    """v7 → v8: Add ephemeral field to compliance_check.

    Additive only — no data loss. Backfills existing records to ephemeral=false
    so queries using WHERE ephemeral = false continue to include all pre-v8 rows.
    """
    new_stmts = [
        "DEFINE FIELD ephemeral ON compliance_check TYPE bool DEFAULT false",
        "DEFINE INDEX idx_cc_ephemeral ON compliance_check FIELDS ephemeral",
    ]
    for sql in new_stmts:
        await _execute_define_idempotent(client, sql.strip())

    try:
        await client.execute("UPDATE compliance_check SET ephemeral = false WHERE ephemeral = NONE")
        logger.info("[migration] v7 → v8: backfilled compliance_check.ephemeral = false on existing rows")
    except Exception as exc:
        logger.warning("[migration] v7 → v8: backfill failed (non-fatal): %s", exc)

    logger.info("[migration] v7 → v8: ephemeral field added to compliance_check")


# Registry: version → migration function that brings DB from version-1 to version.
# Pre-v4 migrations are removed; DBs older than v4 must be reset.
_MIGRATIONS: dict[int, ...] = {
    5: _migrate_v4_to_v5,
    6: _migrate_v5_to_v6,
    7: _migrate_v6_to_v7,
    8: _migrate_v7_to_v8,
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


async def migrate(client: LedgerClient, allow_destructive: bool = False) -> None:
    """Run any pending migrations to bring the DB up to SCHEMA_VERSION.

    Called after init_schema() in adapter.connect(). Safe to call repeatedly.

    Raises:
        SchemaVersionTooNew: DB schema is ahead of the code — upgrade the binary.
        DestructiveMigrationRequired: A pending step drops data and
            allow_destructive=False. Call with allow_destructive=True (via
            bicameral_reset confirm=True) to proceed.
    """
    current = await _get_schema_version(client)

    if current == SCHEMA_VERSION:
        return

    if current > SCHEMA_VERSION:
        required_ver = SCHEMA_COMPATIBILITY.get(current, "unknown")
        raise SchemaVersionTooNew(
            f"DB schema v{current} requires bicameral-mcp>={required_ver}; "
            f"you're running schema v{SCHEMA_VERSION}. "
            f"Upgrade: pipx upgrade bicameral-mcp"
        )

    logger.info(
        "[migration] Schema version %d → %d (%d migration(s) to apply)",
        current, SCHEMA_VERSION, SCHEMA_VERSION - current,
    )

    for target_version in range(current + 1, SCHEMA_VERSION + 1):
        fn = _MIGRATIONS.get(target_version)
        if fn is None:
            logger.warning("[migration] No migration function for version %d", target_version)
            continue
        if target_version in DESTRUCTIVE_MIGRATIONS and not allow_destructive:
            raise DestructiveMigrationRequired(
                f"schema v{target_version - 1}→v{target_version} is a breaking migration "
                f"that drops legacy data. Call bicameral_reset(confirm=True) to proceed."
            )
        await fn(client)
        await _set_schema_version(client, target_version)

    logger.info("[migration] Schema migrated to version %d", SCHEMA_VERSION)
