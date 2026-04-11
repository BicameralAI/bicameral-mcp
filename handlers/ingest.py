"""Handler for /ingest MCP tool.

Thin orchestration: validate payload, resolve symbols, auto-ground,
ingest into ledger, then sync.
"""

from __future__ import annotations

import logging

from contracts import IngestPayload, IngestResponse, IngestStats, SourceCursorSummary

logger = logging.getLogger(__name__)


def _normalize_payload(payload: dict) -> dict:
    """Validate and normalize ingest payload using Pydantic contracts.

    1. Validates the raw dict against IngestPayload (fails fast on bad types)
    2. If ``mappings`` is already present, returns as-is (internal format)
    3. If ``decisions``/``action_items``/``open_questions`` present, converts to mappings
    """
    validated = IngestPayload.model_validate(payload)

    # Already has mappings — convert back to dict and return
    if validated.mappings:
        return validated.model_dump()

    mappings: list[dict] = []
    source_meta = {
        "source_type": validated.source,
        "source_ref": validated.title,
        "speakers": validated.participants,
        "meeting_date": validated.date,
    }

    for d in validated.decisions:
        text = d.description or d.title
        if not text:
            continue
        mappings.append({
            "intent": text,
            "span": {
                **source_meta,
                "text": text,
                "source_ref": d.id or source_meta["source_ref"],
                "speakers": d.participants or source_meta["speakers"],
            },
            "symbols": [],
            "code_regions": [],
        })

    for a in validated.action_items:
        text = f"[Action: {a.owner}] {a.action}"
        mappings.append({
            "intent": text,
            "span": {**source_meta, "text": text},
            "symbols": [],
            "code_regions": [],
        })

    for q in validated.open_questions:
        text = f"[Open Question] {q}"
        mappings.append({
            "intent": text,
            "span": {**source_meta, "text": text},
            "symbols": [],
            "code_regions": [],
        })

    if not mappings:
        logger.warning(
            "[ingest] payload validated but produced 0 mappings: %s",
            list(payload.keys()),
        )
        return validated.model_dump()

    result = validated.model_dump()
    result["mappings"] = mappings
    return result


def _validate_cached_regions(
    regions: list[dict], code_graph,
) -> list[dict]:
    """Check cached code_regions against the live symbol index.

    Returns only regions whose symbol still exists in the index.
    Initializes the code graph lazily (only triggered when there's
    a cache hit to validate).

    Handles qualified names (e.g., "PaymentService.processPayment")
    by falling back to the last segment after "." since
    SymbolDB.lookup_by_name() matches on the short ``name`` column.

    When lookup_by_name returns multiple rows, prefers the row matching
    the cached region's file_path to avoid picking an unrelated symbol.
    """
    try:
        code_graph._ensure_initialized()
        db = code_graph._validate_tool._db
    except Exception:
        return []

    valid = []
    for region in regions:
        symbol = region.get("symbol", "")
        if not symbol:
            continue
        cached_file = region.get("file_path", "")

        rows = db.lookup_by_name(symbol)
        if not rows and "." in symbol:
            rows = db.lookup_by_name(symbol.rsplit(".", 1)[-1])
        if not rows:
            continue

        # Prefer the row matching the cached file_path; fall back to rows[0]
        row = next(
            (r for r in rows if r["file_path"] == cached_file),
            rows[0],
        )
        valid.append({
            **region,
            "file_path": row["file_path"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
        })
    return valid


def _derive_last_source_ref(payload: dict) -> str:
    mappings = payload.get("mappings") or []
    refs = [str((m.get("span") or {}).get("source_ref", "")).strip() for m in mappings]
    refs = [ref for ref in refs if ref]
    return refs[-1] if refs else str(payload.get("query", "")).strip()


async def handle_ingest(
    ctx,
    payload: dict,
    source_scope: str = "",
    cursor: str = "",
) -> IngestResponse:
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload = _normalize_payload(payload)
    repo = str(payload.get("repo") or ctx.repo_path)
    payload = ctx.code_graph.resolve_symbols(payload)

    # Decision grounding reuse: check if similar intents were already grounded.
    # Runs before ground_mappings — a hit skips the full BM25 pipeline.
    mappings_to_ground = payload.get("mappings") or []
    cache_hits = 0
    for mapping in mappings_to_ground:
        if mapping.get("code_regions"):
            continue
        description = mapping.get("intent") or (mapping.get("span") or {}).get("text", "")
        if not description:
            continue
        try:
            cached = await ledger.lookup_cached_groundings(description, repo)
            if cached:
                top = cached[0]
                valid_regions = _validate_cached_regions(
                    top.get("code_regions", []), ctx.code_graph,
                )
                if valid_regions:
                    mapping["code_regions"] = valid_regions
                    cache_hits += 1
                    logger.info(
                        "[ingest] grounding reuse hit for '%s' (confidence=%.2f, %d/%d regions valid)",
                        description[:60], top.get("confidence", 0),
                        len(valid_regions), len(top.get("code_regions", [])),
                    )
                else:
                    logger.debug(
                        "[ingest] grounding reuse discarded (all regions stale): '%s'",
                        description[:60],
                    )
        except Exception as exc:
            logger.debug("[ingest] grounding reuse lookup failed: %s", exc)

    mappings, grounding_deferred = ctx.code_graph.ground_mappings(mappings_to_ground)
    payload = {**payload, "mappings": mappings}
    result = await ledger.ingest_payload(payload)

    # Sync ledger to HEAD and re-ground any previously ungrounded intents
    try:
        from handlers.link_commit import handle_link_commit
        await handle_link_commit(ctx, "HEAD")
    except Exception as exc:
        logger.warning("[ingest] post-ingest link_commit failed: %s", exc)

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
            cache_hits=cache_hits,
        ),
        ungrounded_intents=list(result.get("ungrounded_intents", [])),
        source_cursor=cursor_summary,
    )
