"""SurrealQL query functions for the decision ledger — v4 (v0.5.0).

v4 graph shape:
  Decision tier:  input_span -yields-> decision -binds_to-> code_region
  Retrieval tier: symbol -locates-> code_region

All functions take a LedgerClient and return plain Python types.
No SDK types (RecordID etc.) leak through — normalization happens in client.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .client import LedgerClient, LedgerError

logger = logging.getLogger(__name__)


# ── Idempotent edge creation ──────────────────────────────────────────────
#
# Edge tables (yields, binds_to, locates, depends_on) each have a
# UNIQUE(in, out) index so the same logical relationship is never created twice.
# Team-mode event replay re-issues every RELATE; duplicates are rejected by the
# DB and treated as a no-op success here.

async def _execute_idempotent_edge(
    client: LedgerClient, sql: str, vars: dict | None = None
) -> None:
    """Run a RELATE statement that may hit a UNIQUE(in, out) violation.

    A "Database index ... already contains" error is treated as success.
    Any other LedgerError re-raises.
    """
    try:
        await client.execute(sql, vars)
    except LedgerError as exc:
        if "already contains" not in str(exc):
            raise
        # Duplicate edge — already at desired end state, no-op.


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


# ── Decision queries ──────────────────────────────────────────────────────


async def get_all_decisions(
    client: LedgerClient,
    filter: str = "all",
    since: str | None = None,
) -> list[dict]:
    """Forward graph traversal: decision → binds_to → code_region."""
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
            type::string(id)  AS decision_id,
            description,
            rationale,
            feature_hint,
            source_type,
            source_ref,
            meeting_date,
            speakers,
            status,
            product_signoff,
            created_at,
            ->binds_to->code_region.{{
                file_path,
                symbol_name,
                start_line,
                end_line,
                purpose,
                content_hash
            }} AS code_regions,
            <-yields<-input_span.{{text, meeting_date, speakers}} AS source_spans
        FROM decision
        {where}
        ORDER BY created_at DESC
        """,
        vars or None,
    )
    for row in rows:
        ca = row.pop("created_at", None)
        row.setdefault("ingested_at", str(ca)[:24] if ca else "")
    for row in rows:
        for region in (row.get("code_regions") or []):
            if region and "symbol_name" in region:
                region["symbol"] = region.pop("symbol_name")
    for row in rows:
        spans = row.pop("source_spans", None) or []
        description = row.get("description", "")
        real_spans = [
            s for s in spans
            if s and s.get("text") and s.get("text") != description
        ]
        first_span = real_spans[0] if real_spans else None
        row["source_excerpt"] = (first_span.get("text") if first_span else "") or ""
        if not row.get("meeting_date"):
            row["meeting_date"] = (first_span.get("meeting_date") if first_span else "") or ""
        if not row.get("speakers"):
            row["speakers"] = (first_span.get("speakers") if first_span else []) or []
    return _normalize_decisions(rows)


async def search_by_bm25(
    client: LedgerClient,
    query: str,
    max_results: int = 10,
    min_confidence: float = 0.5,
) -> list[dict]:
    """BM25 search on decision.description.

    Also pulls input_span.text (raw passage) + meeting_date via the
    yields reverse edge so callers can render the meeting excerpt.
    """
    rows = await client.query(
        """
        SELECT
            type::string(id)  AS decision_id,
            description,
            source_type,
            source_ref,
            status,
            product_signoff,
            created_at,
            ->binds_to->code_region.{
                file_path,
                symbol_name,
                start_line,
                end_line,
                purpose,
                content_hash
            } AS code_regions,
            <-yields<-input_span.{text, meeting_date} AS source_spans
        FROM decision
        WHERE description @0@ $query
        LIMIT $n
        """,
        {"query": query, "n": max_results},
    )
    total = len(rows)
    for i, row in enumerate(rows):
        ca = row.pop("created_at", None)
        row.setdefault("ingested_at", str(ca)[:24] if ca else "")
        row["confidence"] = round(1.0 - (i / max(total, 1)) * 0.4, 2)
        for region in (row.get("code_regions") or []):
            if region and "symbol_name" in region:
                region["symbol"] = region.pop("symbol_name")
        spans = row.pop("source_spans", None) or []
        description = row.get("description", "")
        real_spans = [
            s for s in spans
            if s and s.get("text") and s.get("text") != description
        ]
        first_span = real_spans[0] if real_spans else None
        row["source_excerpt"] = (first_span.get("text") if first_span else "") or ""
        row["meeting_date"] = (first_span.get("meeting_date") if first_span else "") or ""
    return _normalize_decisions(rows)


# ── vocab_cache: grounding reuse cache ─────────────────────────────────


async def lookup_vocab_cache(
    client: LedgerClient,
    query_text: str,
    repo: str,
    max_results: int = 3,
) -> tuple[list[dict], str]:
    """BM25 lookup on vocab_cache for cached grounding results.

    Returns a 2-tuple: (symbols, matched_query_text).
    On hit, increments hit_count and refreshes last_hit for LRU tracking.
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
    """Write or update a vocab_cache entry."""
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
    """Reverse traversal: code_region → binds_to (reverse) → decision for a given file.

    Also pulls source_excerpt + meeting_date per decision via the
    yields reverse edge so the drift handler can render the meeting passage.
    """
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
            <-binds_to<-decision.{
                id,
                description,
                source_type,
                source_ref,
                status,
                product_signoff,
                created_at
            } AS decisions
        FROM code_region
        WHERE file_path = $fp
        """,
        {"fp": file_path},
    )

    results = []
    seen_decision_ids: set[str] = set()
    decision_id_set: set[str] = set()
    for region_row in rows:
        region = {
            "file_path": region_row.get("file_path", ""),
            "symbol": region_row.get("symbol_name", ""),
            "lines": (region_row.get("start_line", 0), region_row.get("end_line", 0)),
            "purpose": region_row.get("purpose", ""),
            "content_hash": region_row.get("content_hash", ""),
        }
        for decision in (region_row.get("decisions") or []):
            if decision is None:
                continue
            did = str(decision.get("id", ""))
            if did in seen_decision_ids:
                continue
            seen_decision_ids.add(did)
            decision_id_set.add(did)
            results.append({
                "decision_id": did,
                "description": decision.get("description", ""),
                "source_type": decision.get("source_type", ""),
                "source_ref": decision.get("source_ref", ""),
                "source_excerpt": "",
                "meeting_date": "",
                "speaker": "",
                "ingested_at": str(decision.get("created_at", "")),
                "status": decision.get("status", "ungrounded"),
                "product_signoff": decision.get("product_signoff"),
                "code_region": region,
            })

    # Backfill source_excerpt + meeting_date via yields reverse edge
    if decision_id_set:
        excerpt_rows = await client.query(
            """
            SELECT
                type::string(id) AS decision_id,
                <-yields<-input_span.{text, meeting_date} AS source_spans
            FROM decision
            WHERE type::string(id) IN $ids
            """,
            {"ids": list(decision_id_set)},
        )
        excerpt_by_decision: dict[str, tuple[str, str]] = {}
        desc_by_decision = {e["decision_id"]: e.get("description", "") for e in results}
        for r in (excerpt_rows or []):
            did = str(r.get("decision_id", ""))
            desc = desc_by_decision.get(did, "")
            spans = r.get("source_spans") or []
            real_spans = [
                s for s in spans
                if s and s.get("text") and s.get("text") != desc
            ]
            first = real_spans[0] if real_spans else None
            if first:
                excerpt_by_decision[did] = (
                    str(first.get("text") or ""),
                    str(first.get("meeting_date") or ""),
                )
        for entry in results:
            did = entry["decision_id"]
            if did in excerpt_by_decision:
                excerpt, mdate = excerpt_by_decision[did]
                entry["source_excerpt"] = excerpt
                entry["meeting_date"] = mdate

    return results


async def get_undocumented_symbols(
    client: LedgerClient,
    file_path: str,
) -> list[str]:
    """Return symbol names in file_path with no bound decision."""
    rows = await client.query(
        """
        SELECT symbol_name
        FROM code_region
        WHERE file_path = $fp
          AND (<-binds_to<-decision) = []
        """,
        {"fp": file_path},
    )
    return [r["symbol_name"] for r in rows if r.get("symbol_name")]


# ── Ingestion ─────────────────────────────────────────────────────────────


async def upsert_decision(
    client: LedgerClient,
    description: str,
    source_type: str,
    source_ref: str = "",
    rationale: str = "",
    feature_hint: str = "",
    feature_group: str | None = None,
    meeting_date: str = "",
    speakers: list = (),
    status: str = "ungrounded",
    product_signoff: dict | None = None,
) -> str:
    """Create or update a decision node. Returns the decision ID string.

    Dedup key is canonical_id (UUIDv5 derived from canonicalized description +
    canonicalized source_ref). Falls back to description-based query for
    legacy rows pre-v0.4.13 (now kept for safety across re-ingests).
    """
    from .canonical import canonical_decision_id

    cid = canonical_decision_id(description, source_type, source_ref)

    existing = await client.query(
        "SELECT id FROM decision WHERE canonical_id = $cid LIMIT 1",
        {"cid": cid},
    )
    if existing:
        update_params: dict = {
            "rationale": rationale,
            "feature_hint": feature_hint,
            "meeting_date": meeting_date,
            "speakers": list(speakers),
            "status": status,
        }
        set_clause = (
            "rationale = $rationale, feature_hint = $feature_hint, "
            "meeting_date = $meeting_date, speakers = $speakers, status = $status"
        )
        if product_signoff is not None:
            set_clause += ", product_signoff = $product_signoff"
            update_params["product_signoff"] = product_signoff
        if feature_group is not None:
            set_clause += ", feature_group = $feature_group"
            update_params["feature_group"] = feature_group
        await client.query(
            f"UPDATE {existing[0]['id']} SET {set_clause}",
            update_params,
        )
        return str(existing[0]["id"])

    # Truly new — CREATE with canonical_id stamped
    create_params: dict = {
        "d": description,
        "st": source_type,
        "sr": source_ref,
        "s": status,
        "cid": cid,
        "rationale": rationale,
        "feature_hint": feature_hint,
        "meeting_date": meeting_date,
        "speakers": list(speakers),
    }
    create_clause = (
        "CREATE decision SET description=$d, source_type=$st, source_ref=$sr, "
        "status=$s, canonical_id=$cid, rationale=$rationale, "
        "feature_hint=$feature_hint, meeting_date=$meeting_date, "
        "speakers=$speakers"
    )
    if product_signoff is not None:
        create_clause += ", product_signoff=$product_signoff"
        create_params["product_signoff"] = product_signoff
    if feature_group is not None:
        create_clause += ", feature_group=$feature_group"
        create_params["feature_group"] = feature_group
    rows = await client.query(create_clause, create_params)
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


async def upsert_compliance_check(
    client: LedgerClient,
    decision_id: str,
    region_id: str,
    content_hash: str,
    verdict: str,
    confidence: str,
    explanation: str,
    phase: str,
    commit_hash: str = "",
    pruned: bool = False,
) -> bool:
    """Write a compliance_check row keyed on (decision_id, region_id, content_hash).

    Returns True when written, False when the row already existed (first-write-wins).
    verdict must be one of: 'compliant', 'drifted', 'not_relevant'.
    """
    try:
        await client.execute(
            "CREATE compliance_check SET decision_id = $d, region_id = $r, "
            "content_hash = $h, verdict = $v, confidence = $cf, "
            "explanation = $e, phase = $p, commit_hash = $cm, pruned = $pr",
            {
                "d": decision_id,
                "r": region_id,
                "h": content_hash,
                "v": verdict,
                "cf": confidence,
                "e": explanation,
                "p": phase,
                "cm": commit_hash,
                "pr": pruned,
            },
        )
        return True
    except LedgerError as exc:
        if "already contains" not in str(exc):
            raise
        return False


async def decision_exists(client: LedgerClient, decision_id: str) -> bool:
    """Return True iff a decision row exists with the given record id."""
    rows = await client.query(f"SELECT id FROM {decision_id} LIMIT 1")
    return bool(rows)


async def region_exists(client: LedgerClient, region_id: str) -> bool:
    """Return True iff a code_region row exists with the given record id."""
    rows = await client.query(f"SELECT id FROM {region_id} LIMIT 1")
    return bool(rows)


async def get_compliance_verdict(
    client: LedgerClient,
    decision_id: str,
    region_id: str,
    content_hash: str,
) -> dict | None:
    """Return the cached LLM verdict for this exact code shape, or None."""
    rows = await client.query(
        "SELECT verdict, pruned, confidence, explanation, phase, checked_at "
        "FROM compliance_check "
        "WHERE decision_id = $d AND region_id = $r AND content_hash = $h "
        "LIMIT 1",
        {"d": decision_id, "r": region_id, "h": content_hash},
    )
    return rows[0] if rows else None


async def relate_yields(
    client: LedgerClient,
    span_id: str,
    decision_id: str,
) -> None:
    """Create input_span → yields → decision edge. Idempotent via UNIQUE(in, out)."""
    await _execute_idempotent_edge(
        client,
        f"RELATE {span_id}->yields->{decision_id} SET created_at=time::now()",
    )


async def relate_binds_to(
    client: LedgerClient,
    decision_id: str,
    region_id: str,
    confidence: float = 0.8,
    provenance: dict | None = None,
) -> None:
    """Create decision → binds_to → code_region edge. Idempotent via UNIQUE(in, out)."""
    prov = provenance or {}
    await _execute_idempotent_edge(
        client,
        f"RELATE {decision_id}->binds_to->{region_id} SET confidence=$c, provenance=$p, created_at=time::now()",
        {"c": confidence, "p": prov},
    )


async def relate_locates(
    client: LedgerClient,
    symbol_id: str,
    region_id: str,
    confidence: float = 0.8,
) -> None:
    """Create symbol → locates → code_region edge. Idempotent via UNIQUE(in, out)."""
    await _execute_idempotent_edge(
        client,
        f"RELATE {symbol_id}->locates->{region_id} SET confidence=$c, created_at=time::now()",
        {"c": confidence},
    )


async def upsert_input_span(
    client: LedgerClient,
    text: str,
    source_type: str,
    source_ref: str = "",
    speakers: list = (),
    meeting_date: str = "",
) -> str:
    """Create or update an input_span node. Returns the input_span ID string.

    Deduplicates on (source_type, source_ref, text). text must be non-empty
    (enforced by the schema ASSERT constraint).
    """
    if not text:
        return ""
    rows = await client.query(
        """
        UPSERT input_span SET
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
        "CREATE input_span SET text=$t, source_type=$st, source_ref=$sr, speakers=$sp, meeting_date=$md",
        {"t": text, "st": source_type, "sr": source_ref, "sp": list(speakers), "md": meeting_date},
    )
    return str(rows[0].get("id", "")) if rows else ""


async def update_decision_status(
    client: LedgerClient,
    decision_id: str,
    status: str,
) -> None:
    """Update the cached status on a decision node."""
    await client.execute(
        f"UPDATE {decision_id} SET status = $s",
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
    """Return all code_region records for a list of file paths.

    Uses binds_to reverse traversal to find linked decisions.
    """
    if not file_paths:
        return []
    rows = await client.query(
        """
        SELECT
            type::string(id) AS region_id,
            file_path, symbol_name, start_line, end_line, content_hash,
            <-binds_to<-decision.{id, status, description} AS decisions
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
    """Return regions whose content_hash has never been stamped."""
    rows = await client.query(
        """
        SELECT
            type::string(id) AS region_id,
            file_path, symbol_name, start_line, end_line, content_hash, repo,
            <-binds_to<-decision.{id, status, description} AS decisions
        FROM code_region
        """,
    )
    filtered = [r for r in (rows or []) if not r.get("content_hash")]
    if repo:
        filtered = [r for r in filtered if str(r.get("repo", "")) == repo]
    return filtered


async def delete_binds_to_edge(
    client: LedgerClient,
    decision_id: str,
    region_id: str,
) -> None:
    """Delete a binds_to edge between a decision and a code_region.

    Used when a caller returns a not_relevant verdict — retrieval made a
    mistake and the binding should not be kept.
    """
    try:
        await client.execute(
            f"DELETE FROM binds_to WHERE in = {decision_id} AND out = {region_id}",
        )
    except Exception as exc:
        logger.warning("[delete_binds_to] %s → %s failed: %s", decision_id, region_id, exc)


async def project_decision_status(
    client: LedgerClient,
    decision_id: str,
) -> str:
    """Derive decision.status from product_signoff + compliance verdict aggregation.

    Double-entry 2×2 + strict aggregation:
    - No binds_to AND product_signoff None → 'ungrounded'
    - No binds_to AND product_signoff set → 'pending'
    - Any bound region with drifted verdict → 'drifted'
    - Any bound region with no verdict for current hash → 'pending'
    - All bound regions compliant + product_signoff set → 'reflected'
    - All bound regions compliant + product_signoff None → 'pending' (hero case)

    DRIFTED always wins over signoff state — broken code is broken code.
    """
    dec_rows = await client.query(
        f"SELECT product_signoff FROM {decision_id} LIMIT 1",
    )
    if not dec_rows:
        return "ungrounded"
    product_signoff = dec_rows[0].get("product_signoff")

    # Get all non-pruned bound regions + their current content_hash
    binding_rows = await client.query(
        f"""
        SELECT type::string(out) AS region_id, out.content_hash AS content_hash
        FROM binds_to
        WHERE in = {decision_id}
        """,
    )

    if not binding_rows:
        return "pending" if product_signoff else "ungrounded"

    all_compliant = True
    any_drifted = False
    any_pending = False

    for binding in binding_rows:
        region_id = binding.get("region_id", "")
        content_hash = binding.get("content_hash", "")

        if not region_id or not content_hash:
            any_pending = True
            all_compliant = False
            continue

        verdict = await get_compliance_verdict(client, decision_id, region_id, content_hash)
        if verdict is None:
            any_pending = True
            all_compliant = False
        elif verdict.get("pruned", False):
            # Pruned regions are not_relevant — invisible to aggregation
            continue
        elif verdict.get("verdict") != "compliant":
            any_drifted = True
            all_compliant = False

    if any_drifted:
        return "drifted"
    if any_pending:
        return "pending"
    if all_compliant:
        return "reflected" if product_signoff else "pending"
    return "pending"


# ── Helpers ───────────────────────────────────────────────────────────────


async def get_grounding_breakdown(
    client: LedgerClient,
    source_type: str | None = None,
    source_scope: str | None = None,
) -> list[dict]:
    """Per-source_ref grounded/ungrounded/total/pct breakdown."""
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
        FROM decision
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
            r["lines"] = (r.pop("start_line", 0), r.pop("end_line", 0))
            normalized.append(r)
        row["code_regions"] = normalized
        if "speaker" not in row:
            speakers = row.get("speakers") or []
            row["speaker"] = speakers[0] if speakers else ""
    return rows
