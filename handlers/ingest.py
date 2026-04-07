"""Handler for /ingest MCP tool.

Productized ingestion entrypoint:
- accepts a normalized payload shaped like the internal CodeLocatorPayload handoff
- writes decisions/code regions into the ledger
- records a source cursor so Slack / Notion / other upstream sources can sync incrementally
"""

from __future__ import annotations

import os
import logging
import re

from adapters.ledger import get_ledger
from contracts import IngestResponse, IngestStats, SourceCursorSummary

logger = logging.getLogger(__name__)


# Threshold for BM25 file-level search. Replaced with eval-calibrated value
# once Silong's RAG eval runs. Fuzzy token path (below) has no threshold —
# rapidfuzz scores are already normalised 0–100.
AUTO_GROUND_THRESHOLD = 0.5

# Common English stop words to filter from intent descriptions before fuzzy
# token matching. Keeps only semantically meaningful terms.
_STOP_WORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "are", "from", "have",
    "will", "when", "then", "been", "also", "into", "about", "should",
    "must", "need", "each", "they", "their", "there", "which", "where",
    "what", "than", "some", "more", "such", "only", "very", "just",
    "like", "make", "made", "use", "used", "using", "after", "before",
})


def _regions_from_symbol_ids(symbol_ids: list[int], db, description: str) -> list[dict]:
    """Resolve a list of symbol IDs to code_region dicts."""
    regions = []
    seen: set[int] = set()
    for sid in symbol_ids:
        if sid in seen:
            continue
        seen.add(sid)
        row = db.lookup_by_id(sid)
        if row:
            regions.append({
                "symbol": row["qualified_name"] or row["name"],
                "file_path": row["file_path"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "type": row["type"],
                "purpose": description,
            })
    return regions


def _auto_ground_via_search(mappings: list[dict], repo: str) -> tuple[list[dict], int]:
    """Auto-ground mappings with no code_regions.

    Two-stage approach:
    1. BM25 search on full description → top file → expand to symbols (fast,
       good for broad queries, threshold-gated).
    2. Fuzzy token matching: tokenise description → rapidfuzz against symbol
       names → direct symbol lookup (no threshold, catches cases where BM25
       finds nothing e.g. short repos or low-frequency terms).

    Stage 2 is the preliminary semantic grounding path that works without
    Silong's RAG eval / threshold calibration.

    Returns:
        (resolved_mappings, grounding_deferred_count) — grounding_deferred_count
        is > 0 when the index was unavailable and grounding was skipped entirely.
        The caller should surface this so the user knows to rebuild the index and
        re-ingest.
    """
    db_path = os.getenv("CODE_LOCATOR_SQLITE_DB", "")
    if not db_path:
        db_path = str(os.path.join(repo, ".bicameral", "code-graph.db"))

    try:
        from adapters.code_locator import get_code_locator
        from code_locator.indexing.sqlite_store import SymbolDB
        locator = get_code_locator()
        db = SymbolDB(db_path)
    except Exception as exc:
        logger.warning("[ingest] auto-ground unavailable: %s", exc)
        # Count how many mappings would have been candidates for grounding
        deferred = sum(1 for m in mappings if not m.get("code_regions"))
        return mappings, deferred

    resolved = []
    for mapping in mappings:
        # Only skip mappings that are already grounded (have code_regions).
        # Mappings with symbols[] but empty code_regions are NOT grounded —
        # _resolve_symbols_to_regions may have failed; let BM25/fuzzy take over.
        if mapping.get("code_regions"):
            resolved.append(mapping)
            continue

        description = mapping.get("intent") or (mapping.get("span") or {}).get("text", "")
        if not description:
            resolved.append(mapping)
            continue

        code_regions = []

        # ── Stage 1: BM25 file search ──────────────────────────────────────
        try:
            hits = locator.search_code(description)
            top = next((h for h in hits if h.get("score", 0) >= AUTO_GROUND_THRESHOLD), None)
            if top:
                file_symbols = db.lookup_by_file(top["file_path"])
                # Rank file symbols by relevance to the description via fuzzy
                # matching, rather than taking the first 5 by DB insertion order.
                tokens = [
                    w for w in re.findall(r"[a-zA-Z]{4,}", description)
                    if w.lower() not in _STOP_WORDS
                ]
                if tokens:
                    validated = locator.validate_symbols(tokens)
                    matched_ids = {v["symbol_id"] for v in validated if v.get("symbol_id")}
                else:
                    matched_ids = set()
                file_symbol_ids = {row["id"] for row in file_symbols}
                relevant_ids = matched_ids & file_symbol_ids
                ranked = sorted(
                    file_symbols,
                    key=lambda r: (r["id"] not in relevant_ids, r["start_line"]),
                )
                code_regions = [
                    {
                        "symbol": row["qualified_name"] or row["name"],
                        "file_path": row["file_path"],
                        "start_line": row["start_line"],
                        "end_line": row["end_line"],
                        "type": row["type"],
                        "purpose": description,
                    }
                    for row in ranked[:3]
                ]
                if code_regions:
                    logger.info(
                        "[ingest] stage1 BM25 grounded '%s' → %s (%d symbols, score=%.2f)",
                        description[:60], top["file_path"], len(code_regions), top["score"],
                    )
        except Exception as exc:
            logger.warning("[ingest] BM25 search failed for '%s': %s", description[:60], exc)

        # ── Stage 2: fuzzy token matching (preliminary semantic grounding) ─
        if not code_regions:
            tokens = [
                w for w in re.findall(r"[a-zA-Z]{4,}", description)
                if w.lower() not in _STOP_WORDS
            ]
            if tokens:
                try:
                    validated = locator.validate_symbols(tokens)  # list[dict]
                    symbol_ids = [v["symbol_id"] for v in validated if v.get("symbol_id")]
                    code_regions = _regions_from_symbol_ids(symbol_ids[:5], db, description)
                    if code_regions:
                        matched = [v["matched_symbol"] for v in validated[:3]]
                        logger.info(
                            "[ingest] stage2 fuzzy grounded '%s' → %s",
                            description[:60], matched,
                        )
                except Exception as exc:
                    logger.warning("[ingest] fuzzy token match failed: %s", exc)

        if code_regions:
            resolved.append({**mapping, "code_regions": code_regions})
        else:
            logger.debug("[ingest] no grounding found for: %s", description[:60])
            resolved.append(mapping)

    return resolved, 0


def _resolve_symbols_to_regions(payload: dict, repo: str) -> dict:
    """For each mapping with symbols[] but no code_regions, look up symbol names
    in the code graph and populate code_regions from the results."""
    mappings = payload.get("mappings")
    if not mappings:
        return payload

    needs_resolution = any(
        m.get("symbols") and not m.get("code_regions")
        for m in mappings
    )
    if not needs_resolution:
        return payload

    db_path = os.getenv("CODE_LOCATOR_SQLITE_DB", "")
    if not db_path:
        import os as _os
        db_path = str(_os.path.join(repo, ".bicameral", "code-graph.db"))

    try:
        from code_locator.indexing.sqlite_store import SymbolDB
        db = SymbolDB(db_path)
    except Exception as exc:
        logger.warning("[ingest] cannot open symbol DB at %s: %s", db_path, exc)
        return payload

    resolved_mappings = []
    for mapping in mappings:
        symbol_names = mapping.get("symbols") or []
        code_regions = mapping.get("code_regions") or []

        if symbol_names and not code_regions:
            for name in symbol_names:
                try:
                    rows = db.lookup_by_name(name)
                except Exception as exc:
                    logger.warning("[ingest] lookup_by_name failed for '%s': %s", name, exc)
                    rows = []
                for row in rows:
                    code_regions.append({
                        "symbol": row["qualified_name"] or row["name"],
                        "file_path": row["file_path"],
                        "start_line": row["start_line"],
                        "end_line": row["end_line"],
                        "type": row["type"],
                        "purpose": mapping.get("intent", ""),
                    })
            if code_regions:
                mapping = {**mapping, "code_regions": code_regions}
            else:
                logger.debug("[ingest] no symbols found in index for: %s", symbol_names)

        resolved_mappings.append(mapping)

    return {**payload, "mappings": resolved_mappings}


def _derive_last_source_ref(payload: dict) -> str:
    mappings = payload.get("mappings") or []
    refs = [str((m.get("span") or {}).get("source_ref", "")).strip() for m in mappings]
    refs = [ref for ref in refs if ref]
    return refs[-1] if refs else str(payload.get("query", "")).strip()


async def handle_ingest(
    payload: dict,
    source_scope: str = "",
    cursor: str = "",
) -> IngestResponse:
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    repo = str(payload.get("repo") or os.getenv("REPO_PATH", "."))
    payload = _resolve_symbols_to_regions(payload, repo)
    mappings, grounding_deferred = _auto_ground_via_search(payload.get("mappings") or [], repo)
    payload = {**payload, "mappings": mappings}
    result = await ledger.ingest_payload(payload)

    cursor_summary = None
    source_type = str(((payload.get("mappings") or [{}])[0].get("span") or {}).get("source_type", "manual"))
    last_source_ref = _derive_last_source_ref(payload)
    if hasattr(ledger, "upsert_source_cursor"):
        cursor_row = await ledger.upsert_source_cursor(
            repo=repo,
            source_type=source_type,
            source_scope=source_scope or "default",
            cursor=cursor or last_source_ref,
            last_source_ref=last_source_ref,
        )
        cursor_summary = SourceCursorSummary(**cursor_row)

    source_refs = []
    for mapping in payload.get("mappings", []):
        span = mapping.get("span") or {}
        ref = str(span.get("source_ref", "")).strip()
        if ref and ref not in source_refs:
            source_refs.append(ref)

    stats = result.get("stats", {})
    return IngestResponse(
        ingested=bool(result.get("ingested", False)),
        repo=str(result.get("repo", repo)),
        query=str(payload.get("query", "")),
        source_refs=source_refs,
        stats=IngestStats(
            intents_created=int(stats.get("intents_created", 0)),
            symbols_mapped=int(stats.get("symbols_mapped", 0)),
            regions_linked=int(stats.get("regions_linked", 0)),
            ungrounded=int(stats.get("ungrounded", 0)),
            grounding_deferred=grounding_deferred,
        ),
        ungrounded_intents=list(result.get("ungrounded_intents", [])),
        source_cursor=cursor_summary,
    )
