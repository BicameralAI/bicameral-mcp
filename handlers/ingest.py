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
    mappings, grounding_deferred = ctx.code_graph.ground_mappings(payload.get("mappings") or [])
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
        ),
        ungrounded_intents=list(result.get("ungrounded_intents", [])),
        source_cursor=cursor_summary,
    )
