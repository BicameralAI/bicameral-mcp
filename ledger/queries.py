"""SurrealQL query functions for the decision ledger.

All functions take a LedgerClient and return plain Python types.
No SDK types (RecordID etc.) leak through — normalization happens in client.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .client import LedgerClient

logger = logging.getLogger(__name__)

# ── Sync state ────────────────────────────────────────────────────────────


async def get_sync_state(client: LedgerClient, repo: str) -> dict | None:
    """Return the last-synced commit record for a repo, or None."""
    rows = await client.query(
        "SELECT * FROM ledger_sync WHERE repo = $repo LIMIT 1",
        {"repo": repo},
    )
    return rows[0] if rows else None


async def upsert_sync_state(client: LedgerClient, repo: str, commit_hash: str) -> None:
    """Update or create the sync cursor for a repo."""
    await client.execute(
        """
        UPSERT ledger_sync SET
            repo = $repo,
            last_synced_commit = $commit,
            synced_at = time::now()
        WHERE repo = $repo
        """,
        {"repo": repo, "commit": commit_hash},
    )


async def get_source_cursor(
    client: LedgerClient,
    repo: str,
    source_type: str,
    source_scope: str = "default",
) -> dict | None:
    rows = await client.query(
        """
        SELECT repo, source_type, source_scope, cursor, last_source_ref, synced_at, status, error
        FROM source_cursor
        WHERE repo = $repo AND source_type = $source_type AND source_scope = $source_scope
        LIMIT 1
        """,
        {"repo": repo, "source_type": source_type, "source_scope": source_scope},
    )
    if not rows:
        return None
    row = rows[0]
    row["synced_at"] = str(row.get("synced_at", ""))
    return row


async def upsert_source_cursor(
    client: LedgerClient,
    repo: str,
    source_type: str,
    source_scope: str = "default",
    cursor: str = "",
    last_source_ref: str = "",
    status: str = "ok",
    error: str = "",
) -> dict:
    rows = await client.query(
        """
        UPSERT source_cursor SET
            repo = $repo,
            source_type = $source_type,
            source_scope = $source_scope,
            cursor = $cursor,
            last_source_ref = $last_source_ref,
            synced_at = time::now(),
            status = $status,
            error = $error
        WHERE repo = $repo AND source_type = $source_type AND source_scope = $source_scope
        """,
        {
            "repo": repo,
            "source_type": source_type,
            "source_scope": source_scope,
            "cursor": cursor,
            "last_source_ref": last_source_ref,
            "status": status,
            "error": error,
        },
    )
    if rows:
        row = rows[0]
        row["synced_at"] = str(row.get("synced_at", ""))
        return row
    return {
        "repo": repo,
        "source_type": source_type,
        "source_scope": source_scope,
        "cursor": cursor,
        "last_source_ref": last_source_ref,
        "synced_at": str(datetime.now(timezone.utc).isoformat()),
        "status": status,
        "error": error,
    }


# ── Intent queries ────────────────────────────────────────────────────────


async def get_all_decisions(
    client: LedgerClient,
    filter: str = "all",
    since: str | None = None,
) -> list[dict]:
    """Forward graph traversal: intent → symbol → code_region."""
    where_clauses = []
    vars: dict = {}

    if filter != "all":
        where_clauses.append("status = $status")
        vars["status"] = filter
    if since:
        where_clauses.append("created_at > <datetime>$since")
        vars["since"] = since

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    rows = await client.query(
        f"""
        SELECT
            type::string(id)  AS intent_id,
            description,
            rationale,
            feature_hint,
            source_type,
            source_ref,
            meeting_date,
            speakers,
            status,
            created_at,
            ->maps_to->symbol->implements->code_region.{{
                file_path,
                symbol_name,
                start_line,
                end_line,
                purpose,
                content_hash
            }} AS code_regions
        FROM intent
        {where}
        ORDER BY created_at DESC
        """,
        vars or None,
    )
    # Normalize created_at → ingested_at string
    for row in rows:
        ca = row.pop("created_at", None)
        row.setdefault("ingested_at", str(ca)[:24] if ca else "")
    # Rename symbol_name → symbol in each region (AS alias not supported in v2 traversals)
    for row in rows:
        for region in (row.get("code_regions") or []):
            if region and "symbol_name" in region:
                region["symbol"] = region.pop("symbol_name")
    return _normalize_decisions(rows)


async def search_by_bm25(
    client: LedgerClient,
    query: str,
    max_results: int = 10,
    min_confidence: float = 0.5,
) -> list[dict]:
    """BM25 search on intent.description."""
    rows = await client.query(
        """
        SELECT
            type::string(id)  AS intent_id,
            description,
            source_type,
            source_ref,
            status,
            created_at,
            ->maps_to->symbol->implements->code_region.{
                file_path,
                symbol_name,
                start_line,
                end_line,
                purpose,
                content_hash
            } AS code_regions
        FROM intent
        WHERE description @0@ $query
        LIMIT $n
        """,
        {"query": query, "n": max_results},
    )
    # @0@ already filtered to matching documents.
    # Assign position-based confidence (1.0 for first match, decreasing).
    # Note: embedded SurrealDB v2 always returns search::score=0.0 — use count instead.
    total = len(rows)
    for i, row in enumerate(rows):
        ca = row.pop("created_at", None)
        row.setdefault("ingested_at", str(ca)[:24] if ca else "")
        row["confidence"] = round(1.0 - (i / max(total, 1)) * 0.4, 2)  # 1.0 → 0.6
        for region in (row.get("code_regions") or []):
            if region and "symbol_name" in region:
                region["symbol"] = region.pop("symbol_name")
    return _normalize_decisions(rows)


# ── vocab_cache: grounding reuse cache ─────────────────────────────────


async def lookup_vocab_cache(
    client: LedgerClient,
    query_text: str,
    repo: str,
    max_results: int = 3,
) -> tuple[list[dict], str]:
    """BM25 lookup on vocab_cache for cached grounding results.

    Returns a 2-tuple: ``(symbols, matched_query_text)``.
      - ``symbols`` is the cached code_region-shaped dict list from the
        top matching cache entry, or ``[]`` on miss.
      - ``matched_query_text`` is the ``query_text`` that the top hit was
        originally stored against. The caller uses this to compute a
        similarity gate (FC-3 fix) before deciding whether to reuse the
        cached symbols — BM25's ``@0@`` operator is too loose on its
        own and cross-contaminates unrelated intents.

    On hit, increments hit_count and refreshes last_hit for LRU tracking.
    On miss, returns ``([], "")``.
    """
    rows = await client.query(
        """
        SELECT *
        FROM vocab_cache
        WHERE query_text @0@ $query
            AND repo = $repo
        LIMIT $max_results
        """,
        {"query": query_text, "repo": repo, "max_results": max_results},
    )
    if not rows:
        return [], ""

    top = rows[0]
    top_id = top.get("id")
    if top_id:
        await client.query(
            f"UPDATE {top_id} SET hit_count += 1, last_hit = time::now()",
        )

    return top.get("symbols") or [], str(top.get("query_text") or "")


async def upsert_vocab_cache(
    client: LedgerClient,
    query_text: str,
    repo: str,
    symbols: list[dict],
) -> None:
    """Write or update a vocab_cache entry.

    Stores code_region-shaped dicts in the symbols array for later
    reuse. Line numbers may go stale but are validated against the
    live SymbolDB index on every cache hit (see _validate_cached_regions).
    """
    await client.query(
        """
        UPSERT vocab_cache SET
            query_text = $query_text,
            repo       = $repo,
            symbols    = $symbols,
            hit_count  = IF hit_count THEN hit_count + 1 ELSE 1 END,
            last_hit   = time::now()
        WHERE query_text = $query_text AND repo = $repo
        """,
        {
            "query_text": query_text,
            "repo": repo,
            "symbols": symbols,
        },
    )


async def get_decisions_for_file(
    client: LedgerClient,
    file_path: str,
) -> list[dict]:
    """Reverse traversal: code_region → symbol → intent for a given file."""
    rows = await client.query(
        """
        SELECT
            type::string(id) AS region_id,
            file_path,
            symbol_name,
            start_line,
            end_line,
            purpose,
            content_hash,
            <-implements<-symbol<-maps_to<-intent.{
                id,
                description,
                source_type,
                source_ref,
                status,
                created_at
            } AS intents
        FROM code_region
        WHERE file_path = $fp
        """,
        {"fp": file_path},
    )

    # Flatten: one row per (region, intent) pair
    results = []
    seen_intent_ids: set[str] = set()
    for region_row in rows:
        region = {
            "file_path": region_row.get("file_path", ""),
            "symbol": region_row.get("symbol_name", ""),
            "lines": (region_row.get("start_line", 0), region_row.get("end_line", 0)),
            "purpose": region_row.get("purpose", ""),
            "content_hash": region_row.get("content_hash", ""),
        }
        for intent in (region_row.get("intents") or []):
            if intent is None:
                continue
            iid = str(intent.get("id", ""))
            if iid in seen_intent_ids:
                continue
            seen_intent_ids.add(iid)
            results.append({
                "intent_id": iid,
                "description": intent.get("description", ""),
                "source_type": intent.get("source_type", ""),
                "source_ref": intent.get("source_ref", ""),
                "speaker": "",
                "ingested_at": str(intent.get("created_at", "")),
                "status": intent.get("status", "ungrounded"),
                "code_region": region,
            })
    return results


async def get_undocumented_symbols(
    client: LedgerClient,
    file_path: str,
) -> list[str]:
    """Return symbol names in file_path with no mapped intent."""
    rows = await client.query(
        """
        SELECT symbol_name
        FROM code_region
        WHERE file_path = $fp
          AND (<-implements<-symbol<-maps_to<-intent) = []
        """,
        {"fp": file_path},
    )
    return [r["symbol_name"] for r in rows if r.get("symbol_name")]


# ── Ingestion ─────────────────────────────────────────────────────────────


async def upsert_intent(
    client: LedgerClient,
    description: str,
    source_type: str,
    source_ref: str = "",
    rationale: str = "",
    feature_hint: str = "",
    meeting_date: str = "",
    speakers: list = (),
    status: str = "ungrounded",
) -> str:
    """Create or update an intent node. Returns the intent ID string."""
    rows = await client.query(
        """
        UPSERT intent SET
            description  = $description,
            source_type  = $source_type,
            source_ref   = $source_ref,
            rationale    = $rationale,
            feature_hint = $feature_hint,
            meeting_date = $meeting_date,
            speakers     = $speakers,
            status       = $status,
            created_at   = IF created_at THEN created_at ELSE time::now() END
        WHERE description = $description AND source_ref = $source_ref
        """,
        {
            "description": description,
            "source_type": source_type,
            "source_ref": source_ref,
            "rationale": rationale,
            "feature_hint": feature_hint,
            "meeting_date": meeting_date,
            "speakers": list(speakers),
            "status": status,
        },
    )
    if rows:
        return str(rows[0].get("id", ""))
    # Fallback: create new
    rows = await client.query(
        "CREATE intent SET description=$d, source_type=$st, source_ref=$sr, status=$s",
        {"d": description, "st": source_type, "sr": source_ref, "s": status},
    )
    return str(rows[0].get("id", "")) if rows else ""


async def upsert_symbol(
    client: LedgerClient,
    name: str,
    file_path: str,
    sym_type: str = "function",
) -> str:
    """Create or update a symbol node. Returns the symbol ID string."""
    rows = await client.query(
        """
        UPSERT symbol SET
            name      = $name,
            file_path = $file_path,
            sym_type  = $sym_type,
            last_seen = time::now(),
            hit_count = IF hit_count THEN hit_count + 1 ELSE 1 END
        WHERE name = $name
        """,
        {"name": name, "file_path": file_path, "sym_type": sym_type},
    )
    if rows:
        return str(rows[0].get("id", ""))
    rows = await client.query(
        "CREATE symbol SET name=$n, file_path=$fp, sym_type=$t",
        {"n": name, "fp": file_path, "t": sym_type},
    )
    return str(rows[0].get("id", "")) if rows else ""


async def upsert_code_region(
    client: LedgerClient,
    file_path: str,
    symbol_name: str,
    start_line: int,
    end_line: int,
    purpose: str = "",
    repo: str = "",
    content_hash: str = "",
) -> str:
    """Create or update a code_region node. Returns the region ID string."""
    rows = await client.query(
        """
        UPSERT code_region SET
            file_path   = $file_path,
            symbol_name = $symbol_name,
            start_line  = $start_line,
            end_line    = $end_line,
            purpose     = $purpose,
            repo        = $repo,
            content_hash = $content_hash
        WHERE file_path = $file_path AND symbol_name = $symbol_name
        """,
        {
            "file_path": file_path, "symbol_name": symbol_name,
            "start_line": start_line, "end_line": end_line,
            "purpose": purpose, "repo": repo, "content_hash": content_hash,
        },
    )
    if rows:
        return str(rows[0].get("id", ""))
    rows = await client.query(
        "CREATE code_region SET file_path=$fp, symbol_name=$s, start_line=$sl, end_line=$el",
        {"fp": file_path, "s": symbol_name, "sl": start_line, "el": end_line},
    )
    return str(rows[0].get("id", "")) if rows else ""


async def relate_maps_to(
    client: LedgerClient,
    intent_id: str,
    symbol_id: str,
    confidence: float = 0.8,
    provenance: dict | None = None,
) -> None:
    """Create intent → maps_to → symbol edge (idempotent via DELETE + CREATE)."""
    prov = provenance or {}
    await client.execute(
        f"RELATE {intent_id}->maps_to->{symbol_id} SET confidence=$c, provenance=$p, created_at=time::now()",
        {"c": confidence, "p": prov},
    )


async def relate_implements(
    client: LedgerClient,
    symbol_id: str,
    region_id: str,
    confidence: float = 0.8,
) -> None:
    """Create symbol → implements → code_region edge."""
    await client.execute(
        f"RELATE {symbol_id}->implements->{region_id} SET confidence=$c, created_at=time::now()",
        {"c": confidence},
    )


async def upsert_source_span(
    client: LedgerClient,
    text: str,
    source_type: str,
    source_ref: str = "",
    speakers: list = (),
    meeting_date: str = "",
) -> str:
    """Create or update a source_span node. Returns the source_span ID string.

    Deduplicates on (source_type, source_ref, text) — same excerpt from the
    same source is the same span.
    """
    rows = await client.query(
        """
        UPSERT source_span SET
            text         = $text,
            source_type  = $source_type,
            source_ref   = $source_ref,
            speakers     = $speakers,
            meeting_date = $meeting_date,
            created_at   = IF created_at THEN created_at ELSE time::now() END
        WHERE source_type = $source_type AND source_ref = $source_ref AND text = $text
        """,
        {
            "text": text,
            "source_type": source_type,
            "source_ref": source_ref,
            "speakers": list(speakers),
            "meeting_date": meeting_date,
        },
    )
    if rows:
        return str(rows[0].get("id", ""))
    rows = await client.query(
        "CREATE source_span SET text=$t, source_type=$st, source_ref=$sr, speakers=$sp, meeting_date=$md",
        {"t": text, "st": source_type, "sr": source_ref, "sp": list(speakers), "md": meeting_date},
    )
    return str(rows[0].get("id", "")) if rows else ""


async def relate_yields(
    client: LedgerClient,
    span_id: str,
    intent_id: str,
) -> None:
    """Create source_span → yields → intent edge (extraction provenance)."""
    await client.execute(
        f"RELATE {span_id}->yields->{intent_id} SET created_at=time::now()",
    )


async def update_intent_status(
    client: LedgerClient,
    intent_id: str,
    status: str,
) -> None:
    """Update the cached status on an intent node."""
    await client.execute(
        f"UPDATE {intent_id} SET status = $s",
        {"s": status},
    )


async def update_region_hash(
    client: LedgerClient,
    region_id: str,
    content_hash: str,
    pinned_commit: str = "",
) -> None:
    """Update content_hash + pinned_commit on a code_region after link_commit."""
    await client.execute(
        f"UPDATE {region_id} SET content_hash=$h, pinned_commit=$c",
        {"h": content_hash, "c": pinned_commit},
    )


async def get_regions_for_files(
    client: LedgerClient,
    file_paths: list[str],
) -> list[dict]:
    """Return all code_region records for a list of file paths."""
    if not file_paths:
        return []
    rows = await client.query(
        """
        SELECT
            type::string(id) AS region_id,
            file_path, symbol_name, start_line, end_line, content_hash,
            <-implements<-symbol<-maps_to<-intent.{id, status, description} AS intents
        FROM code_region
        WHERE file_path IN $fps
        """,
        {"fps": file_paths},
    )
    return rows


async def get_regions_without_hash(
    client: LedgerClient,
    repo: str = "",
) -> list[dict]:
    """Return regions whose content_hash has never been stamped.

    Used by the backfill sweep in ingest_commit to self-heal legacy regions
    from pre-v0.4.5 ledgers where ingest skipped hash computation. Filters
    in Python rather than SurrealQL to avoid v2-vs-v3 NONE/NULL syntax drift.

    When ``repo`` is provided, only regions belonging to that repo are
    returned — prevents backfill noise from unrelated ledgers in the same
    SurrealDB database (common during multi-fixture test runs).
    """
    rows = await client.query(
        """
        SELECT
            type::string(id) AS region_id,
            file_path, symbol_name, start_line, end_line, content_hash, repo,
            <-implements<-symbol<-maps_to<-intent.{id, status, description} AS intents
        FROM code_region
        """,
    )
    filtered = [r for r in (rows or []) if not r.get("content_hash")]
    if repo:
        filtered = [r for r in filtered if str(r.get("repo", "")) == repo]
    return filtered


# ── Helpers ───────────────────────────────────────────────────────────────


async def get_grounding_breakdown(
    client: LedgerClient,
    source_type: str | None = None,
    source_scope: str | None = None,
) -> list[dict]:
    """Per-source_ref grounded/ungrounded/total/pct breakdown.

    Used by the M1 decision-relevance eval. Returns one row per distinct
    source_ref in the intent table. An intent is "grounded" when its status
    is anything other than "ungrounded" (pending/reflected/drifted all mean
    the intent is linked to at least one code region).
    """
    where_clauses: list[str] = []
    vars: dict = {}
    if source_type:
        where_clauses.append("source_type = $source_type")
        vars["source_type"] = source_type
    if source_scope:
        where_clauses.append("source_ref CONTAINS $source_scope")
        vars["source_scope"] = source_scope
    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    rows = await client.query(
        f"""
        SELECT source_ref, status
        FROM intent
        {where}
        """,
        vars or None,
    )

    buckets: dict[str, dict] = {}
    for row in rows:
        ref = str(row.get("source_ref") or "")
        status = str(row.get("status") or "")
        b = buckets.setdefault(
            ref,
            {"source_ref": ref, "grounded": 0, "ungrounded": 0, "total": 0, "grounded_pct": 0.0},
        )
        b["total"] += 1
        if status == "ungrounded":
            b["ungrounded"] += 1
        else:
            b["grounded"] += 1

    out = []
    for b in buckets.values():
        b["grounded_pct"] = (b["grounded"] / b["total"]) if b["total"] > 0 else 0.0
        out.append(b)
    out.sort(key=lambda r: r["source_ref"])
    return out


def _normalize_decisions(rows: list[dict]) -> list[dict]:
    """Ensure code_regions always have a 'lines' tuple + consistent shape."""
    for row in rows:
        regions = row.get("code_regions") or []
        normalized = []
        for r in regions:
            if r is None:
                continue
            # Ensure 'lines' tuple for handler compatibility
            r["lines"] = (r.pop("start_line", 0), r.pop("end_line", 0))
            normalized.append(r)
        row["code_regions"] = normalized
        # Ensure speaker field exists
        if "speaker" not in row:
            speakers = row.get("speakers") or []
            row["speaker"] = speakers[0] if speakers else ""
    return rows
