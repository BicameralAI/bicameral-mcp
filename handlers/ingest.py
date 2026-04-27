"""Handler for /ingest MCP tool.

Thin orchestration: validate payload, resolve symbols, ingest into ledger, then sync.
Auto-grounding removed in caller-LLM binding flow (v0.5.1+).
"""

from __future__ import annotations

import logging

from contracts import (
    ContextForCandidate,
    CreatedDecision,
    IngestPayload,
    IngestResponse,
    IngestStats,
    SourceCursorSummary,
)

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
        text = d.description or d.title or d.text
        if not text:
            continue
        span_text = d.source_excerpt or text
        mapping: dict = {
            "intent": text,
            "span": {
                **source_meta,
                "text": span_text,
                "source_ref": d.id or source_meta["source_ref"],
                "speakers": d.participants or source_meta["speakers"],
            },
            "symbols": [],
            "code_regions": [],
        }
        if d.signoff is not None:
            mapping["signoff"] = d.signoff
        if d.feature_group is not None:
            mapping["feature_group"] = d.feature_group
        mappings.append(mapping)

    # Action items are task assignments, not product decisions — they belong in a
    # ticket tracker, not the decision ledger.  We accept them in the payload for
    # backwards compat but do not write them to the ledger.

    for q in validated.open_questions:
        # Open questions are requirement gaps: a known unknown that is neither
        # claimed (no source commitment) nor fulfilled (no code). They are stored
        # with the "[Open Question]" prefix so the history handler can surface
        # them as "gap" status entries rather than ordinary decisions.
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


_TOPIC_MAX = 200


def _word_truncate(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars on a word boundary."""
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    if " " in clipped:
        return clipped.rsplit(" ", 1)[0]
    return clipped


def _derive_topic(payload: dict) -> str:
    """Pick a topic string for the judge_gaps auto-chain.

    Priority: payload.query → longest decision description → payload.title.
    Returns empty string when nothing useful is found (skips chain).
    """
    query = str(payload.get("query") or "").strip()
    if query:
        return _word_truncate(query, _TOPIC_MAX)

    decisions = payload.get("decisions") or []
    decision_texts = [
        str(d.get("description") or d.get("title") or "").strip()
        for d in decisions
        if isinstance(d, dict)
    ]
    decision_texts = [t for t in decision_texts if t]
    if decision_texts:
        return _word_truncate(max(decision_texts, key=len), _TOPIC_MAX)

    title = str(payload.get("title") or "").strip()
    if title:
        return _word_truncate(title, _TOPIC_MAX)

    return ""


async def _find_context_for_candidates(
    mappings: list[dict],
    ledger,
    top_k: int = 5,
) -> list[ContextForCandidate]:
    """After ingest writes spans, find context_pending decisions that may be answered.

    Runs BM25 search per span text and filters to decisions with
    signoff.state='context_pending'. Returns up to top_k candidates total
    (deduped by (span_id, decision_id) pair). Never raises — returns [] on error.
    """
    from ledger.queries import get_input_span_id, search_context_pending_by_text

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    seen_pairs: set[tuple[str, str]] = set()
    candidates: list[ContextForCandidate] = []

    for mapping in mappings:
        span = mapping.get("span") or {}
        span_text = span.get("text", "")
        source_type = span.get("source_type", "manual")
        source_ref = span.get("source_ref", "")
        if not span_text:
            continue
        try:
            span_id = await get_input_span_id(client, source_type, source_ref, span_text)
            if not span_id:
                continue
            matches = await search_context_pending_by_text(client, span_text, top_k=top_k)
            for m in matches:
                decision_id = m.get("decision_id", "")
                if not decision_id:
                    continue
                pair = (span_id, decision_id)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                candidates.append(ContextForCandidate(
                    span_id=span_id,
                    decision_id=decision_id,
                    decision_description=m.get("description", ""),
                    overlap_score=float(m.get("overlap_score", 0.0)),
                ))
                if len(candidates) >= top_k:
                    return candidates
        except Exception as exc:
            logger.debug("[ingest] context_for scan failed: %s", exc)

    return candidates


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

    # For agent_session / manual ingests (gap answers, inline resolutions),
    # backfill the git user email as the speaker when speakers is empty.
    # Transcript/slack/document spans carry their own speaker lists; only
    # session-originated spans lack an author and need this backfill.
    _SESSION_SOURCE_TYPES = {"agent_session", "manual"}
    _git_email_cache: str | None = None
    for mapping in payload.get("mappings") or []:
        span = mapping.get("span") or {}
        if span.get("source_type") in _SESSION_SOURCE_TYPES and not span.get("speakers"):
            if _git_email_cache is None:
                from events.writer import _get_git_email
                _git_email_cache = _get_git_email(ctx.repo_path)
            if _git_email_cache and _git_email_cache != "unknown":
                span["speakers"] = [_git_email_cache]

    payload = ctx.code_graph.resolve_symbols(payload)

    from datetime import datetime, timezone
    _now_iso = datetime.now(timezone.utc).isoformat()
    _session_id = getattr(ctx, "session_id", None) or ""

    # v0.7.0: every new ingest enters as 'proposed' by default.
    # v0.9.3: supersession detection removed from server — caller-LLM checks
    # bicameral.history after ingest and calls bicameral_resolve_collision for conflicts.
    mappings = payload.get("mappings") or []
    _proposed_signoff = {"state": "proposed", "session_id": _session_id, "created_at": _now_iso}
    for m in mappings:
        if m.get("signoff") is None:
            m["signoff"] = _proposed_signoff
    payload = {**payload, "mappings": mappings}

    # Pollution guard (v0.4.6, Bug 3): warn the user if they're ingesting
    # from a non-authoritative ref. The ingest still proceeds — baselines
    # will be stamped against the authoritative ref via ingest_payload(ctx=ctx)
    # below, so no data is corrupted. The warning is informational only.
    authoritative_ref = getattr(ctx, "authoritative_ref", "")
    authoritative_sha = getattr(ctx, "authoritative_sha", "")
    head_sha = getattr(ctx, "head_sha", "")
    if authoritative_sha and head_sha and authoritative_sha != head_sha:
        logger.warning(
            "[ingest] checked out on a ref that differs from authoritative %s "
            "(HEAD=%s); baseline hashes will be stamped against %s so the "
            "ledger stays branch-independent. Switch to %s if you want "
            "baselines pinned to the current working tree.",
            authoritative_ref, head_sha[:8], authoritative_ref, authoritative_ref,
        )

    # v0.4.8: writes always invalidate the within-call sync cache. In the
    # top-level ingest path this is a no-op (no cache exists yet this call),
    # but the invariant "mutations clear cache" must hold symmetrically —
    # otherwise a future chain that runs a read handler *before* ingest and
    # then writes would leave a stale cache covering post-write reads.
    try:
        from handlers.link_commit import handle_link_commit, invalidate_sync_cache
        invalidate_sync_cache(ctx)
    except Exception:
        pass

    result = await ledger.ingest_payload(payload, ctx=ctx)

    # v0.8.0: context_for candidate detection.
    # After spans are written, BM25-search for context_pending decisions that
    # the new spans may answer. Returns up to 5 candidates across all mappings.
    context_for_candidates: list = []
    try:
        context_for_candidates = await _find_context_for_candidates(
            payload.get("mappings") or [], ledger, top_k=5
        )
    except Exception as exc:
        logger.debug("[ingest] context_for detection failed: %s", exc)

    # Sync ledger to HEAD and re-ground any previously ungrounded intents.
    # The LinkCommitResponse carries ``pending_compliance_checks`` from the
    # drift sweep — the caller LLM resolves them via bicameral.resolve_compliance.
    sync_status = None
    try:
        sync_status = await handle_link_commit(ctx, "HEAD")
    except Exception as exc:
        logger.warning("[ingest] post-ingest link_commit failed: %s", exc)

    # Auto-chain: fire judge_gaps on a derived topic so the caller gets a
    # structured gap-judgment payload in the same response as ingest stats.
    # Failures are swallowed — must not break the ingest itself.
    judgment_payload = None
    try:
        topic = _derive_topic(payload)
        if topic:
            from handlers.gap_judge import handle_judge_gaps
            judgment_payload = await handle_judge_gaps(ctx, topic=topic)
    except Exception as exc:
        logger.warning("[ingest] post-ingest gap-judge chain failed: %s", exc)

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
    intents_created = int(stats.get("intents_created", 0))
    ungrounded_count = int(stats.get("ungrounded", 0))
    grounded_count = max(intents_created - ungrounded_count, 0)
    grounded_pct = (grounded_count / intents_created) if intents_created > 0 else 0.0

    logger.info(
        "[ingest] complete: %d/%d grounded (%.0f%%) | source_refs=%s",
        grounded_count,
        intents_created,
        grounded_pct * 100.0,
        source_refs,
    )

    ingest_response = IngestResponse(
        ingested=bool(result.get("ingested", False)),
        repo=str(result.get("repo", repo)),
        query=str(payload.get("query", "")),
        source_refs=source_refs,
        stats=IngestStats(
            intents_created=intents_created,
            symbols_mapped=int(stats.get("symbols_mapped", 0)),
            regions_linked=int(stats.get("regions_linked", 0)),
            ungrounded=ungrounded_count,
            grounded=grounded_count,
            grounded_pct=grounded_pct,
            grounding_deferred=0,
        ),
        created_decisions=[
            CreatedDecision(
                decision_id=d["decision_id"],
                description=d["description"],
                decision_level=d.get("decision_level"),
            )
            for d in result.get("created_decisions", [])
        ],
        pending_grounding_decisions=[
            d for d in result.get("ungrounded_decisions", [])
            if d.get("decision_level") != "L1"
        ],
        context_for_candidates=context_for_candidates,
        source_cursor=cursor_summary,
        judgment_payload=judgment_payload,
        sync_status=sync_status,
    )

    try:
        from dashboard.server import notify_dashboard
        await notify_dashboard(ctx)
    except Exception:
        pass

    return ingest_response
