"""SurrealQL query functions for the decision ledger — v4 (v0.5.0).

v4 graph shape:
  Decision tier:  input_span -yields-> decision -binds_to-> code_region
  Retrieval tier: symbol -locates-> code_region

All functions take a LedgerClient and return plain Python types.
No SDK types (RecordID etc.) leak through — normalization happens in client.py.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

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
        "synced_at": str(datetime.now(UTC).isoformat()),
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
            signoff,
            decision_level,
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
        for region in row.get("code_regions") or []:
            if region and "symbol_name" in region:
                region["symbol"] = region.pop("symbol_name")
    for row in rows:
        spans = row.pop("source_spans", None) or []
        description = row.get("description", "")
        real_spans = [s for s in spans if s and s.get("text") and s.get("text") != description]
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
            signoff,
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
        for region in row.get("code_regions") or []:
            if region and "symbol_name" in region:
                region["symbol"] = region.pop("symbol_name")
        spans = row.pop("source_spans", None) or []
        description = row.get("description", "")
        real_spans = [s for s in spans if s and s.get("text") and s.get("text") != description]
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
                signoff,
                created_at,
                decision_level
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
        for decision in region_row.get("decisions") or []:
            if decision is None:
                continue
            did = str(decision.get("id", ""))
            if did in seen_decision_ids:
                continue
            seen_decision_ids.add(did)
            decision_id_set.add(did)
            results.append(
                {
                    "decision_id": did,
                    "description": decision.get("description", ""),
                    "source_type": decision.get("source_type", ""),
                    "source_ref": decision.get("source_ref", ""),
                    "source_excerpt": "",
                    "meeting_date": "",
                    "speaker": "",
                    "ingested_at": str(decision.get("created_at", "")),
                    "status": decision.get("status", "ungrounded"),
                    "signoff": decision.get("signoff"),
                    "code_region": region,
                }
            )

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
        for r in excerpt_rows or []:
            did = str(r.get("decision_id", ""))
            desc = desc_by_decision.get(did, "")
            spans = r.get("source_spans") or []
            real_spans = [s for s in spans if s and s.get("text") and s.get("text") != desc]
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


async def get_decisions_for_files(
    client: LedgerClient,
    file_paths: list[str],
) -> list[dict]:
    """Bulk reverse traversal: given a list of file paths, return all decisions
    pinned to any code_region in those files.

    Same shape as get_decisions_for_file but batched — avoids N+1 queries
    when the caller has several candidate files from a code locator search.
    """
    if not file_paths:
        return []

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
                signoff,
                created_at,
                decision_level
            } AS decisions
        FROM code_region
        WHERE file_path IN $fps
        """,
        {"fps": file_paths},
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
        for decision in region_row.get("decisions") or []:
            if decision is None:
                continue
            did = str(decision.get("id", ""))
            if did in seen_decision_ids:
                continue
            seen_decision_ids.add(did)
            decision_id_set.add(did)
            results.append(
                {
                    "decision_id": did,
                    "description": decision.get("description", ""),
                    "source_type": decision.get("source_type", ""),
                    "source_ref": decision.get("source_ref", ""),
                    "source_excerpt": "",
                    "meeting_date": "",
                    "ingested_at": str(decision.get("created_at", "")),
                    "status": decision.get("status", "ungrounded"),
                    "signoff": decision.get("signoff"),
                    "code_region": region,
                }
            )

    # Backfill source_excerpt + meeting_date
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
        desc_by_decision = {e["decision_id"]: e.get("description", "") for e in results}
        excerpt_by_decision: dict[str, tuple[str, str]] = {}
        for r in excerpt_rows or []:
            did = str(r.get("decision_id", ""))
            desc = desc_by_decision.get(did, "")
            spans = r.get("source_spans") or []
            real_spans = [s for s in spans if s and s.get("text") and s.get("text") != desc]
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
    signoff: dict | None = None,
    decision_level: str | None = None,
    parent_decision_id: str | None = None,
    governance: dict | None = None,
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
        if signoff is not None:
            set_clause += ", signoff = $signoff"
            update_params["signoff"] = signoff
        if feature_group is not None:
            set_clause += ", feature_group = $feature_group"
            update_params["feature_group"] = feature_group
        if decision_level is not None:
            set_clause += ", decision_level = $decision_level"
            update_params["decision_level"] = decision_level
        if parent_decision_id is not None:
            set_clause += ", parent_decision_id = $parent_decision_id"
            update_params["parent_decision_id"] = parent_decision_id
        if governance is not None:
            set_clause += ", governance = $governance"
            update_params["governance"] = governance
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
    if signoff is not None:
        create_clause += ", signoff=$signoff"
        create_params["signoff"] = signoff
    if feature_group is not None:
        create_clause += ", feature_group=$feature_group"
        create_params["feature_group"] = feature_group
    if decision_level is not None:
        create_clause += ", decision_level=$decision_level"
        create_params["decision_level"] = decision_level
    if parent_decision_id is not None:
        create_clause += ", parent_decision_id=$parent_decision_id"
        create_params["parent_decision_id"] = parent_decision_id
    if governance is not None:
        create_clause += ", governance=$governance"
        create_params["governance"] = governance
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
            "file_path": file_path,
            "symbol_name": symbol_name,
            "start_line": start_line,
            "end_line": end_line,
            "purpose": purpose,
            "repo": repo,
            "content_hash": content_hash,
        },
    )
    if rows:
        return str(rows[0].get("id", ""))
    rows = await client.query(
        "CREATE code_region SET file_path=$fp, symbol_name=$s, start_line=$sl, end_line=$el",
        {"fp": file_path, "s": symbol_name, "sl": start_line, "el": end_line},
    )
    return str(rows[0].get("id", "")) if rows else ""


async def create_code_region(
    client: LedgerClient,
    file_path: str,
    symbol_name: str,
    start_line: int,
    end_line: int,
    purpose: str = "",
    repo: str = "",
    content_hash: str = "",
) -> str:
    """Phase 3 (#60) — create a NEW code_region row, never upsert.

    Unlike ``upsert_code_region`` (which keys on ``(file_path, symbol_name)``
    and silently reuses the same row for same-file relocations or
    line-shifts), this helper always creates a fresh region. Required
    by the continuity-redirect path: when a symbol moves within the
    same file, the old and new regions must have distinct IDs so
    ``update_binds_to_region`` can redirect the binding without
    overwriting the old span. PR #73 review, CodeRabbit MAJOR
    ledger/adapter.py:365.
    """
    rows = await client.query(
        "CREATE code_region SET "
        "file_path=$fp, symbol_name=$s, start_line=$sl, end_line=$el, "
        "purpose=$p, repo=$r, content_hash=$h",
        {
            "fp": file_path,
            "s": symbol_name,
            "sl": start_line,
            "el": end_line,
            "p": purpose,
            "r": repo,
            "h": content_hash,
        },
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
    ephemeral: bool = False,
    semantic_status: str | None = None,
    evidence_refs: list[str] | None = None,
) -> bool:
    """Write a compliance_check row keyed on (decision_id, region_id, content_hash).

    Returns True when written, False when the row already existed (first-write-wins).
    verdict must be one of: 'compliant', 'drifted', 'not_relevant'.
    ephemeral=True marks the verdict as from a WIP/fixup commit; downstream
    queries filter these out when computing decision status and drift scoring.

    Phase 4 (#61) — additive optional fields:
      semantic_status: 'semantically_preserved' | 'semantic_change' | None
                       Records whether the auto-classifier or caller-LLM
                       judged the change cosmetic vs meaningful.
      evidence_refs:   list of free-form audit-trail strings, e.g.
                       ['signature:1.00', 'neighbors:0.97'].
    """
    refs = evidence_refs or []
    try:
        await client.execute(
            "CREATE compliance_check SET decision_id = $d, region_id = $r, "
            "content_hash = $h, verdict = $v, confidence = $cf, "
            "explanation = $e, phase = $p, commit_hash = $cm, pruned = $pr, "
            "ephemeral = $ep, semantic_status = $ss, evidence_refs = $er",
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
                "ep": ephemeral,
                "ss": semantic_status,
                "er": refs,
            },
        )
        return True
    except LedgerError as exc:
        if "already contains" not in str(exc):
            raise
        return False


async def promote_ephemeral_verdict(
    client: LedgerClient,
    decision_id: str,
    region_id: str,
    content_hash: str,
) -> bool:
    """Flip ephemeral=False for any compliance_check row for (d,r,h) with ephemeral=True.

    Called when the same content hash that was first written on a feature branch
    (ephemeral=True) is confirmed to exist on the authoritative branch or in a
    non-ephemeral resolve_compliance call.
    """
    try:
        await client.execute(
            "UPDATE compliance_check SET ephemeral = false "
            "WHERE decision_id = $d AND region_id = $r AND content_hash = $h AND ephemeral = true",
            {"d": decision_id, "r": region_id, "h": content_hash},
        )
        return True
    except Exception:
        return False


async def decision_exists(client: LedgerClient, decision_id: str) -> bool:
    """Return True iff a decision row exists with the given record id."""
    rows = await client.query(f"SELECT id FROM {decision_id} LIMIT 1")
    return bool(rows)


async def get_decision_level(client: LedgerClient, decision_id: str) -> str | None:
    """Return ``decision.decision_level`` (one of ``"L1"``, ``"L2"``, ``"L3"``)
    or ``None`` if the row does not exist or the field is unset.

    Phase 1+2 (#59) callers use this to enforce the L1 exemption: only
    decisions explicitly tagged ``"L2"`` should enter the codegenome
    identity graph. ``None``-valued (unclassified) and ``"L1"`` /
    ``"L3"`` rows are intentionally ungrounded at the identity layer
    and produce no ``code_subject`` / ``subject_identity`` writes.
    """
    rows = await client.query(
        f"SELECT decision_level FROM {decision_id} LIMIT 1",
    )
    if not rows:
        return None
    val = rows[0].get("decision_level")
    return str(val) if val else None


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
    """Return the cached LLM verdict for this exact code shape, or None.

    Includes both ephemeral (feature-branch) and non-ephemeral verdicts — callers
    use the `ephemeral` field on the row to differentiate branch-delta vs main state.
    """
    rows = await client.query(
        "SELECT verdict, pruned, confidence, explanation, phase, checked_at, ephemeral "
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


# ── canonical_id ↔ decision_id resolution (#97 event replay) ──────────
# Decision rows carry both a SurrealDB-generated ``id`` (e.g. ``decision:abc``)
# and a content-addressed ``canonical_id`` (UUIDv5 from description +
# source_type + source_ref). The local id is per-DB; canonical_id is
# stable across authors and machines, so it's the only id safe to ship
# across the JSONL event log.

async def get_canonical_id(
    client: LedgerClient,
    decision_id: str,
) -> str | None:
    """Return the canonical_id for a local decision row, or None."""
    rows = await client.query(
        f"SELECT canonical_id FROM {decision_id} LIMIT 1",
    )
    if rows and isinstance(rows[0], dict):
        cid = rows[0].get("canonical_id")
        return str(cid) if cid else None
    return None


async def find_decision_by_canonical_id(
    client: LedgerClient,
    canonical_id: str,
) -> str | None:
    """Return the local decision id for a canonical_id, or None."""
    if not canonical_id:
        return None
    rows = await client.query(
        "SELECT id FROM decision WHERE canonical_id = $cid LIMIT 1",
        {"cid": canonical_id},
    )
    if rows and isinstance(rows[0], dict):
        did = rows[0].get("id")
        return str(did) if did else None
    return None


# ── decision_level write helper (#77) ─────────────────────────────────
# Single write path used by:
#   - cli/classify.py --apply              (bulk backfill)
#   - handlers/set_decision_level.py       (MCP tool primitive)
#   - dashboard/server.py /api/decision/<id>/level (#76 sibling PR)
# Schema ASSERT at ledger/schema.py enforces L1/L2/L3; this helper adds
# defense-in-depth callers see clear Python errors before SurrealQL runs.

_VALID_LEVELS = frozenset(("L1", "L2", "L3"))
_DECISION_ID_RE = re.compile(r"^decision:[A-Za-z0-9_]+$")


class DecisionNotFound(LedgerError):
    """Raised by update_decision_level when the decision_id has no row."""


async def update_decision_level(
    client: LedgerClient,
    decision_id: str,
    level: str,
) -> None:
    """Set decision_level on a single decision. Idempotent.

    Validates ``level`` against {L1, L2, L3} and ``decision_id`` against
    the canonical ``^decision:[A-Za-z0-9_]+$`` shape before any SurrealQL
    runs (audit S1 defense-in-depth — the existing f-string interpolation
    pattern is consistent with siblings, but a malformed id would still
    surface as a SurrealDB error rather than silent corruption).

    Args:
        client: Connected LedgerClient.
        decision_id: Full record id (e.g. ``"decision:abc123"``).
        level: One of ``"L1"``, ``"L2"``, ``"L3"``.

    Raises:
        ValueError: when ``level`` is not in {L1, L2, L3} or
            ``decision_id`` fails the shape regex.
        DecisionNotFound: when no row exists at ``decision_id``.
    """
    if level not in _VALID_LEVELS:
        raise ValueError(f"invalid level {level!r}; expected one of L1, L2, L3")
    if not _DECISION_ID_RE.match(decision_id):
        raise ValueError(f"invalid decision_id shape: {decision_id!r}")
    rows = await client.query(f"SELECT id FROM {decision_id} LIMIT 1")
    if not rows:
        raise DecisionNotFound(decision_id)
    await client.execute(
        f"UPDATE {decision_id} SET decision_level = $level",
        {"level": level},
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
            <-binds_to<-decision.{id, status, description, signoff, decision_level} AS decisions
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


async def get_regions_with_ephemeral_verdicts(client: LedgerClient) -> list[dict]:
    """Return code_regions that have at least one ephemeral compliance verdict.

    Uses idx_cc_ephemeral (indexed) to find candidate region_ids, then fetches
    full details for only those regions. Far cheaper than scanning all code_regions
    because the typical case is 0-3 ephemeral regions versus the full bound set.

    Used by ingest_commit's already_synced path to detect stale ephemeral hashes
    left by feature-branch binds when returning to an authoritative branch.
    """
    ep_rows = await client.query(
        "SELECT region_id FROM compliance_check WHERE ephemeral = true GROUP BY region_id"
    )
    if not ep_rows:
        return []
    region_ids = list({r.get("region_id", "") for r in ep_rows if r.get("region_id")})
    if not region_ids:
        return []
    rows = await client.query(
        """
        SELECT
            type::string(id) AS region_id,
            file_path, start_line, end_line, content_hash,
            <-binds_to<-decision.{id, signoff} AS decisions
        FROM code_region
        WHERE type::string(id) IN $ids AND content_hash != NONE AND content_hash != ''
        """,
        {"ids": region_ids},
    )
    return [r for r in (rows or []) if r.get("decisions")]


async def get_pending_decisions_with_regions(client: LedgerClient) -> list[dict]:
    """Return flat (decision, region) rows where decision status is 'pending'.

    Used by ingest_commit to surface stale pending checks that were left
    unresolved from an aborted sync run.

    Implementation note: the SurrealDB v2 embedded engine does not allow
    ``AS`` aliases inside graph traversal field selectors (i.e.
    ``->code_region.{type::string(id) AS region_id, ...}`` is rejected as a
    parse error). We query the ``binds_to`` edge table directly and dot-
    access the ``in`` (decision) and ``out`` (region) endpoints; that path
    supports ``AS`` aliases, so we get a flat shape — one row per
    (decision, region) pair — that callers iterate without nested unpack.
    """
    rows = await client.query(
        """
        SELECT
            type::string(in)    AS decision_id,
            in.description      AS description,
            type::string(out)   AS region_id,
            out.file_path       AS file_path,
            out.symbol_name     AS symbol_name,
            out.start_line      AS start_line,
            out.end_line        AS end_line,
            out.content_hash    AS content_hash
        FROM binds_to
        WHERE in.status = 'pending'
        """,
    )
    return rows or []


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


async def has_prior_compliant_verdict(
    client: LedgerClient,
    decision_id: str,
    region_id: str,
) -> bool:
    """True if ANY past content_hash for this (decision, region) was verified compliant.

    Used by project_decision_status to distinguish:
      - never-verified bindings (→ pending, waiting for first verdict)
      - previously-verified bindings where code has since changed (→ drifted)

    Without this check, every code edit silently parks decisions at "pending"
    forever because the new hash has no cache entry.

    Includes ephemeral (feature-branch) verdicts — branch-delta compliance still
    counts as prior signal for drift detection.
    """
    rows = await client.query(
        "SELECT count() AS n FROM compliance_check "
        "WHERE decision_id = $d AND region_id = $r AND verdict = 'compliant' "
        "GROUP ALL",
        {"d": decision_id, "r": region_id},
    )
    if not rows:
        return False
    return int(rows[0].get("n", 0)) > 0


async def project_decision_status(
    client: LedgerClient,
    decision_id: str,
) -> str:
    """Derive decision.status from compliance verdict aggregation (code-compliance axis).

    v0.9.4: signoff and status are orthogonal. signoff tracks human approval state;
    status tracks code-compliance state. Neither gates the other.

    Status values: ungrounded | pending | reflected | drifted

    - No binds_to → 'ungrounded'
    - Any bound region with drifted verdict → 'drifted'
    - Any bound region with no verdict for current hash, prior compliant
      verdict existed → 'drifted' (was verified, code has since changed)
    - Any bound region with no verdict for current hash, no prior verdict
      → 'pending' (awaiting initial verification)
    - All bound regions compliant → 'reflected'

    DRIFTED always wins. Superseded decisions (signoff.state='superseded') are
    retired from tracking — callers must skip them before calling this function.
    """
    dec_rows = await client.query(
        f"SELECT signoff, status FROM {decision_id} LIMIT 1",
    )
    if not dec_rows:
        return "ungrounded"

    signoff = dec_rows[0].get("signoff")

    # Guard: superseded decisions are retired from code tracking.
    # resolve_collision writes signoff.state='superseded' and this function
    # must never overwrite that by re-deriving compliance status.
    if isinstance(signoff, dict) and signoff.get("state") == "superseded":
        return dec_rows[0].get("status") or "ungrounded"

    # Get all non-pruned bound regions + their current content_hash
    binding_rows = await client.query(
        f"""
        SELECT type::string(out) AS region_id, out.content_hash AS content_hash
        FROM binds_to
        WHERE in = {decision_id}
        """,
    )

    if not binding_rows:
        return "ungrounded"

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
            # Cache miss for the current hash. Distinguish first-time bind
            # (pending) from post-verification code change (drifted).
            if await has_prior_compliant_verdict(client, decision_id, region_id):
                any_drifted = True
                all_compliant = False
            else:
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
        return "reflected"
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


# ── HITL graph edges (v0.8.0) ────────────────────────────────────────────────


async def relate_supersedes(
    client: LedgerClient,
    new_id: str,
    old_id: str,
    confidence: float = 1.0,
    reason: str = "",
) -> None:
    """Write decision → supersedes → decision edge. Idempotent via UNIQUE(in, out)."""
    await _execute_idempotent_edge(
        client,
        f"RELATE {new_id}->supersedes->{old_id} "
        "SET confidence=$c, reason=$r, created_at=time::now()",
        {"c": confidence, "r": reason},
    )


async def relate_context_for(
    client: LedgerClient,
    span_id: str,
    decision_id: str,
    state: str = "confirmed",
    relevance_score: float = 0.0,
    reason: str = "",
) -> None:
    """Write input_span → context_for → decision edge.

    state: 'confirmed' | 'rejected' | 'proposed'
    Idempotent: updates state if edge already exists (via UPSERT on unique pair).
    """
    try:
        await client.execute(
            f"RELATE {span_id}->context_for->{decision_id} "
            "SET state=$s, relevance_score=$rs, reason=$r, created_at=time::now()",
            {"s": state, "rs": relevance_score, "r": reason},
        )
    except LedgerError as exc:
        if "already contains" in str(exc):
            # Edge exists — update state and reason in place
            await client.execute(
                f"UPDATE context_for SET state=$s, reason=$r "
                f"WHERE in = {span_id} AND out = {decision_id}",
                {"s": state, "r": reason},
            )
        else:
            raise


async def get_input_span_id(
    client: LedgerClient,
    source_type: str,
    source_ref: str,
    text: str,
) -> str:
    """Look up an input_span record ID by its dedup key. Returns '' if not found."""
    rows = await client.query(
        "SELECT type::string(id) AS id FROM input_span "
        "WHERE source_type = $st AND source_ref = $sr AND text = $text LIMIT 1",
        {"st": source_type, "sr": source_ref, "text": text},
    )
    return str(rows[0].get("id", "")) if rows else ""


async def search_context_pending_by_text(
    client: LedgerClient,
    text: str,
    top_k: int = 5,
) -> list[dict]:
    """BM25 search on decision descriptions, filtered to context_pending signoff state.

    Returns up to top_k decisions that are in context_pending state and whose
    description matches the given text. Score is rank-position (BM25 score is
    always 0.0 in SurrealDB v2 embedded).
    """
    rows = await client.query(
        "SELECT type::string(id) AS decision_id, description, signoff "
        "FROM decision WHERE description @0@ $q LIMIT $n",
        {"q": text, "n": top_k + 10},  # +10 slack for post-filter
    )
    results = []
    total = len(rows)
    for i, row in enumerate(rows):
        signoff = row.get("signoff")
        if not (
            signoff and isinstance(signoff, dict) and signoff.get("state") == "context_pending"
        ):
            continue
        results.append(
            {
                "decision_id": row.get("decision_id", ""),
                "description": row.get("description", ""),
                "overlap_score": round(1.0 - (i / max(total, 1)) * 0.4, 2),
            }
        )
        if len(results) >= top_k:
            break
    return results


async def get_collision_pending_decisions(
    client: LedgerClient,
) -> list[dict]:
    """Return all decisions with signoff.state = 'collision_pending'.

    These are held proposals awaiting HITL supersession resolution. Used by
    preflight to surface unresolved collisions from prior sessions.
    """
    rows = await client.query(
        "SELECT type::string(id) AS decision_id, description, signoff, status "
        "FROM decision WHERE signoff.state = 'collision_pending'",
    )
    return [
        {
            "decision_id": str(r.get("decision_id", "")),
            "description": str(r.get("description", "")),
            "signoff": r.get("signoff"),
            "status": str(r.get("status", "ungrounded")),
        }
        for r in (rows or [])
        if r.get("decision_id")
    ]


async def get_context_for_ready_decisions(
    client: LedgerClient,
) -> list[dict]:
    """Return context_pending decisions that have ≥1 confirmed context_for edge.

    These are ready for ratification — they have context but haven't been
    ratified yet. Used by preflight to surface the ready-for-ratification queue.
    """
    rows = await client.query(
        """
        SELECT
            type::string(id) AS decision_id,
            description,
            signoff,
            status,
            count(<-context_for[WHERE state = 'confirmed']) AS confirmed_ctx_count
        FROM decision
        WHERE signoff.state = 'context_pending'
        """,
    )
    return [
        {
            "decision_id": str(r.get("decision_id", "")),
            "description": str(r.get("description", "")),
            "signoff": r.get("signoff"),
            "status": "context_pending",
        }
        for r in (rows or [])
        if r.get("decision_id") and int(r.get("confirmed_ctx_count") or 0) > 0
    ]


# ── CodeGenome queries (v11, Phase 1+2 / #59) ─────────────────────────────
#
# All writes are gated by ``codegenome.write_identity_records=True`` at the
# handler boundary. These functions never run unless the flag is on, so
# they cannot regress existing behavior when disabled.
#
# Record-ID validation: callers may receive ``decision_id`` and other
# IDs from MCP handler input or from upsert returns. The helpers below
# build SurrealQL via f-string interpolation (the codebase's existing
# pattern), so any ID that reaches a RELATE/SELECT must match the
# canonical ``table:id`` shape. ``_validated_record_id`` enforces that
# shape and raises ``LedgerError`` on mismatch — a single choke point
# per call instead of trusting upstream callers.

import re as _re  # noqa: E402 — module-private import kept adjacent to its usage block

_RECORD_ID_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:[A-Za-z0-9_\-]+$")


def _validated_record_id(value: str, expected_table: str | None = None) -> str:
    """Return ``value`` if it is a well-formed ``table:id`` SurrealDB record id.

    Raises ``LedgerError`` if empty, malformed, or (when
    ``expected_table`` is given) targets a different table.
    """
    v = str(value or "")
    if not _RECORD_ID_RE.fullmatch(v):
        raise LedgerError(f"Invalid record id: {v!r}")
    if expected_table and not v.startswith(f"{expected_table}:"):
        raise LedgerError(f"Expected {expected_table} id, got: {v!r}")
    return v


async def upsert_code_subject(
    client: LedgerClient,
    kind: str,
    canonical_name: str,
    current_confidence: float,
    repo_ref: str | None = None,
) -> str:
    """Create or update a code_subject node, returning the record ID.

    Keyed on (kind, canonical_name) UNIQUE — repeated calls for the same
    logical subject return the same id and refresh ``current_confidence``
    and ``updated_at``.
    """
    rows = await client.query(
        """
        UPSERT code_subject SET
            kind               = $kind,
            canonical_name     = $name,
            repo_ref           = $repo_ref,
            current_confidence = $conf,
            updated_at         = time::now()
        WHERE kind = $kind AND canonical_name = $name
        """,
        {
            "kind": kind,
            "name": canonical_name,
            "repo_ref": repo_ref,
            "conf": current_confidence,
        },
    )
    if rows:
        return str(rows[0].get("id", ""))
    rows = await client.query(
        "CREATE code_subject SET kind=$kind, canonical_name=$name, "
        "repo_ref=$repo_ref, current_confidence=$conf",
        {
            "kind": kind,
            "name": canonical_name,
            "repo_ref": repo_ref,
            "conf": current_confidence,
        },
    )
    return str(rows[0].get("id", "")) if rows else ""


async def upsert_subject_identity(
    client: LedgerClient,
    *,
    address: str,
    identity_type: str,
    structural_signature: str | None,
    behavioral_signature: str | None,
    signature_hash: str | None,
    content_hash: str | None,
    confidence: float,
    model_version: str,
    neighbors_at_bind: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Create-or-fetch a subject_identity row by ``address`` (UNIQUE).

    Address is content-addressable (blake2b of structural signature for
    deterministic_location_v1) so duplicate writes for the same logical
    identity collapse to a single row. Returns the record id.

    Race-safe under concurrent writers: if two callers race past the
    SELECT and both attempt CREATE, the loser hits the UNIQUE(address)
    index and SurrealDB returns "already contains"; we re-SELECT and
    return the winning row's id rather than propagating the conflict.

    ``neighbors_at_bind`` (v12) is persisted as ``array<string>`` when
    provided, ``NONE`` otherwise. Phase 3's continuity matcher reads this
    field to compute Jaccard against post-rebase neighbors.
    """
    rows = await client.query(
        "SELECT id FROM subject_identity WHERE address = $a LIMIT 1",
        {"a": address},
    )
    if rows:
        return str(rows[0].get("id", ""))

    neighbors_value = list(neighbors_at_bind) if neighbors_at_bind is not None else None

    create_args = {
        "address": address,
        "identity_type": identity_type,
        "structural_signature": structural_signature,
        "behavioral_signature": behavioral_signature,
        "signature_hash": signature_hash,
        "content_hash": content_hash,
        "confidence": confidence,
        "model_version": model_version,
        "neighbors_at_bind": neighbors_value,
    }
    try:
        rows = await client.query(
            """
            CREATE subject_identity SET
                address              = $address,
                identity_type        = $identity_type,
                structural_signature = $structural_signature,
                behavioral_signature = $behavioral_signature,
                signature_hash       = $signature_hash,
                content_hash         = $content_hash,
                confidence           = $confidence,
                model_version        = $model_version,
                neighbors_at_bind    = $neighbors_at_bind
            """,
            create_args,
        )
        return str(rows[0].get("id", "")) if rows else ""
    except LedgerError as exc:
        if "already contains" not in str(exc):
            raise
        rows = await client.query(
            "SELECT id FROM subject_identity WHERE address = $a LIMIT 1",
            {"a": address},
        )
        return str(rows[0].get("id", "")) if rows else ""


async def relate_has_identity(
    client: LedgerClient,
    code_subject_id: str,
    subject_identity_id: str,
    confidence: float = 0.9,
) -> None:
    """code_subject → has_identity → subject_identity. Idempotent."""
    csid = _validated_record_id(code_subject_id, "code_subject")
    siid = _validated_record_id(subject_identity_id, "subject_identity")
    await _execute_idempotent_edge(
        client,
        f"RELATE {csid}->has_identity->{siid} SET confidence=$c, created_at=time::now()",
        {"c": confidence},
    )


async def link_decision_to_subject(
    client: LedgerClient,
    decision_id: str,
    code_subject_id: str,
    region_id: str | None = None,
    confidence: float = 0.8,
) -> None:
    """decision → about → code_subject. Idempotent.

    PR #73 review (CodeRabbit MAJOR ledger/queries.py:1567):
    a decision can bind multiple regions; the ``about`` edge carries
    the originating ``region_id`` so the per-region continuity pass
    can disambiguate which stored identity belongs to a given drifted
    region. ``region_id`` is optional for backward-compatibility with
    callers that don't have a specific region in scope.
    """
    did = _validated_record_id(decision_id, "decision")
    csid = _validated_record_id(code_subject_id, "code_subject")
    if region_id:
        rid = _validated_record_id(region_id, "code_region")
        await _execute_idempotent_edge(
            client,
            f"RELATE {did}->about->{csid} SET confidence=$c, region_id=$r, created_at=time::now()",
            {"c": confidence, "r": rid},
        )
    else:
        await _execute_idempotent_edge(
            client,
            f"RELATE {did}->about->{csid} SET confidence=$c, created_at=time::now()",
            {"c": confidence},
        )


async def get_region_metadata(
    client: LedgerClient,
    region_id: str,
) -> dict | None:
    """Phase 3 (#60) — load span + linked-identity kind for a region.

    Returns ``{file_path, symbol_name, start_line, end_line, identity_type}``
    where ``identity_type`` falls back to ``"unknown"`` when no
    ``subject_identity`` is reachable from this region's decision.

    PR #73 review (CodeRabbit MAJOR handlers/link_commit.py:255):
    callers were seeding ``DriftContext`` with ``kind="unknown"`` and
    ``0,0`` line numbers, permanently dropping the kind signal from
    the Phase 3 score and reporting ``ContinuityResolution.old_location``
    as ``:0-0``. This helper closes both gaps with a single query.
    """
    rid = _validated_record_id(region_id, "code_region")
    rows = await client.query(
        f"""
        SELECT
            file_path, symbol_name, start_line, end_line,
            (<-binds_to<-decision->about->code_subject<-has_identity
                <-subject_identity.identity_type)[0] AS identity_type
        FROM {rid}
        LIMIT 1
        """,
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "file_path": row.get("file_path", ""),
        "symbol_name": row.get("symbol_name", ""),
        "start_line": int(row.get("start_line") or 0),
        "end_line": int(row.get("end_line") or 0),
        "identity_type": row.get("identity_type") or "unknown",
    }


async def update_binds_to_region(
    client: LedgerClient,
    decision_id: str,
    old_region_id: str,
    new_region_id: str,
    *,
    confidence: float = 0.85,
) -> None:
    """Phase 3 (#60): redirect a decision's binds_to from old to new region.

    Atomically deletes the old ``decision -binds_to-> old_region`` edge
    and creates a fresh edge to ``new_region`` with
    ``provenance.method = "continuity_resolved"``. Wrapped in a single
    SurrealQL transaction so a failure on the second statement leaves
    the original binding intact rather than orphaning the decision
    (PR #73 review, CodeRabbit MAJOR ledger/queries.py:1602).

    The old binding's audit trail lives in the parallel
    ``identity_supersedes`` edge written by ``write_identity_supersedes``.
    """
    did = _validated_record_id(decision_id, "decision")
    old_id = _validated_record_id(old_region_id, "code_region")
    new_id = _validated_record_id(new_region_id, "code_region")
    # Embed provenance as a SurrealQL object literal — passing it via
    # ``$p`` silently drops nested dicts to ``{}`` under surrealdb-py
    # 2.0.0. The literal value is internal-only (no caller input
    # interpolated). The whole DELETE+RELATE pair is wrapped in a
    # transaction so a partial migration cannot leave a decision
    # ungrounded.
    #
    # Idempotency: a repeat call (same decision_id / old_region_id /
    # new_region_id) finds the old edge already gone and the new edge
    # already present. The RELATE then hits UNIQUE(in, out) and the
    # transaction rolls back with "already contains". We catch that
    # specific error and treat it as success — the desired end state
    # is already in place.
    # Idempotency: when a repeat call finds the old edge already gone
    # and the new edge already present, the transaction's RELATE hits
    # UNIQUE(in, out) and rolls back. Pre-flight by checking whether
    # the desired end state is already in place; if so, no-op.
    existing = await client.query(
        f"SELECT id FROM binds_to WHERE in = {did} AND out = {new_id} LIMIT 1",
    )
    if existing:
        # Desired edge already exists. Ensure the old edge is gone
        # (covers the partial-failure recovery case where a prior
        # transaction succeeded the RELATE but failed the DELETE).
        await client.execute(
            f"DELETE FROM binds_to WHERE in = {did} AND out = {old_id}",
        )
        return
    try:
        await client.execute(
            f"""
            BEGIN TRANSACTION;
            DELETE FROM binds_to WHERE in = {did} AND out = {old_id};
            RELATE {did}->binds_to->{new_id}
                SET confidence = $c,
                    provenance = {{method: 'continuity_resolved'}},
                    created_at = time::now();
            COMMIT TRANSACTION;
            """,
            {"c": confidence},
        )
    except LedgerError as exc:
        msg = str(exc)
        # SurrealDB v2 wraps UNIQUE violations inside transactions as
        # "failed transaction" without exposing the underlying cause.
        # Treat both forms as idempotent if we got past the pre-flight
        # without finding the new edge but the transaction still
        # collided (race condition).
        if "already contains" not in msg and "failed transaction" not in msg:
            raise


async def write_identity_supersedes(
    client: LedgerClient,
    old_identity_id: str,
    new_identity_id: str,
    change_type: str,
    confidence: float,
    evidence_refs: tuple[str, ...] | list[str] = (),
) -> None:
    """Phase 3 (#60): record an identity transition. Idempotent on (in, out).

    ``change_type`` must be one of ``moved``, ``renamed``, ``moved_and_renamed``
    (enforced by the schema's ASSERT).
    """
    old_id = _validated_record_id(old_identity_id, "subject_identity")
    new_id = _validated_record_id(new_identity_id, "subject_identity")
    await _execute_idempotent_edge(
        client,
        f"RELATE {old_id}->identity_supersedes->{new_id} "
        "SET change_type=$ct, confidence=$c, evidence_refs=$er, created_at=time::now()",
        {"ct": change_type, "c": confidence, "er": list(evidence_refs)},
    )


async def write_subject_version(
    client: LedgerClient,
    code_subject_id: str,
    repo_ref: str,
    file_path: str,
    start_line: int,
    end_line: int,
    *,
    symbol_name: str | None = None,
    symbol_kind: str | None = None,
    content_hash: str | None = None,
    signature_hash: str | None = None,
) -> str:
    """Phase 3 (#60): upsert a subject_version row at a concrete location.

    Keyed on ``(repo_ref, file_path, start_line, end_line)`` — repeated calls
    for the same location return the same id. Caller is responsible for the
    ``has_version`` edge (``relate_has_version``).
    """
    _validated_record_id(code_subject_id, "code_subject")  # validate; no interpolation here
    rows = await client.query(
        """
        UPSERT subject_version SET
            repo_ref       = $repo_ref,
            file_path      = $file_path,
            start_line     = $start_line,
            end_line       = $end_line,
            symbol_name    = $symbol_name,
            symbol_kind    = $symbol_kind,
            content_hash   = $content_hash,
            signature_hash = $signature_hash
        WHERE repo_ref = $repo_ref AND file_path = $file_path
              AND start_line = $start_line AND end_line = $end_line
        """,
        {
            "repo_ref": repo_ref,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "symbol_name": symbol_name,
            "symbol_kind": symbol_kind,
            "content_hash": content_hash,
            "signature_hash": signature_hash,
        },
    )
    if rows:
        return str(rows[0].get("id", ""))
    rows = await client.query(
        """
        CREATE subject_version SET
            repo_ref=$repo_ref, file_path=$file_path,
            start_line=$start_line, end_line=$end_line,
            symbol_name=$symbol_name, symbol_kind=$symbol_kind,
            content_hash=$content_hash, signature_hash=$signature_hash
        """,
        {
            "repo_ref": repo_ref,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "symbol_name": symbol_name,
            "symbol_kind": symbol_kind,
            "content_hash": content_hash,
            "signature_hash": signature_hash,
        },
    )
    return str(rows[0].get("id", "")) if rows else ""


async def relate_has_version(
    client: LedgerClient,
    code_subject_id: str,
    subject_version_id: str,
    confidence: float = 0.9,
) -> None:
    """Phase 3 (#60): code_subject → has_version → subject_version. Idempotent.

    Mirrors ``relate_has_identity``. Closes the orphan-edge condition where
    ``has_version`` was defined-but-unused since #59 schema migration.
    """
    csid = _validated_record_id(code_subject_id, "code_subject")
    svid = _validated_record_id(subject_version_id, "subject_version")
    await _execute_idempotent_edge(
        client,
        f"RELATE {csid}->has_version->{svid} SET confidence=$c, created_at=time::now()",
        {"c": confidence},
    )


async def find_subject_identities_for_decision(
    client: LedgerClient,
    decision_id: str,
) -> list[dict]:
    """Two-hop walk: decision → about → code_subject → has_identity → subject_identity.

    Returns a list of dicts with the identity fields needed by Phase 3
    continuity matching. Empty list if the decision has no linked subjects
    (i.e. identity writes were disabled at bind).
    """
    did = _validated_record_id(decision_id, "decision")
    rows = await client.query(
        f"""
        SELECT
            type::string(id)     AS identity_id,
            address,
            identity_type,
            structural_signature,
            behavioral_signature,
            signature_hash,
            content_hash,
            confidence,
            model_version,
            neighbors_at_bind
        FROM {did}->about->code_subject->has_identity->subject_identity
        """,
    )
    return [
        {
            "identity_id": str(r.get("identity_id", "")),
            "address": str(r.get("address", "")),
            "identity_type": str(r.get("identity_type", "")),
            "structural_signature": r.get("structural_signature"),
            "behavioral_signature": r.get("behavioral_signature"),
            "signature_hash": r.get("signature_hash"),
            "content_hash": r.get("content_hash"),
            "confidence": float(r.get("confidence") or 0.0),
            "model_version": str(r.get("model_version", "")),
            "neighbors_at_bind": r.get("neighbors_at_bind"),
        }
        for r in (rows or [])
        if r.get("identity_id")
    ]
