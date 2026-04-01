"""Handler for /ingest MCP tool.

Productized ingestion entrypoint:
- accepts a normalized payload shaped like the internal CodeLocatorPayload handoff
- writes decisions/code regions into the ledger
- records a source cursor so Slack / Notion / other upstream sources can sync incrementally
"""

from __future__ import annotations

import os
import logging

from adapters.ledger import get_ledger
from contracts import IngestResponse, IngestStats, SourceCursorSummary

logger = logging.getLogger(__name__)


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
                rows = db.lookup_by_name(name)
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
        ),
        ungrounded_intents=list(result.get("ungrounded_intents", [])),
        source_cursor=cursor_summary,
    )
