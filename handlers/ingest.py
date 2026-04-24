"""Handler for /ingest MCP tool.

Thin orchestration: validate payload, resolve symbols, ingest into ledger, then sync.
Auto-grounding removed in caller-LLM binding flow (v0.5.1+).
"""

from __future__ import annotations

import logging

from contracts import (
    IngestPayload,
    IngestResponse,
    IngestStats,
    SourceCursorSummary,
    SupersessionCandidate,
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
        if d.product_signoff is not None:
            mapping["product_signoff"] = d.product_signoff
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


# ── v0.4.8: post-ingest brief auto-chain ────────────────────────────


_BRIEF_TOPIC_MAX = 200


def _word_truncate(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars on a word boundary."""
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    # Drop the trailing (possibly half-word) token if there's still a
    # space somewhere earlier in the slice. Falls through to the raw
    # clip when the slice is one giant token with no spaces.
    if " " in clipped:
        return clipped.rsplit(" ", 1)[0]
    return clipped


def _derive_brief_topic(payload: dict) -> str:
    """Pick a topic string for the auto-fired ``handle_brief`` call.

    Priority order:
      1. ``payload.query`` if the caller supplied one — most accurate
         signal of what they were asking about.
      2. The single **longest** raw decision description from
         ``payload.decisions``. We pick one (not a concatenation) because
         multi-topic ingests otherwise blend unrelated content into one
         BM25 query and brief returns a mess. Most ingests cluster around
         one topic anyway; the longest decision is the richest anchor.
         Raw ``decisions[]`` avoids the ``[Action:]`` / ``[Open Question]``
         prefixes that ``_normalize_payload`` injects into ``mappings[].intent``.
      3. ``payload.title`` as a last resort.
      4. Empty string → caller skips the brief chain entirely.
    """
    query = str(payload.get("query") or "").strip()
    if query:
        return _word_truncate(query, _BRIEF_TOPIC_MAX)

    decisions = payload.get("decisions") or []
    decision_texts: list[str] = []
    for d in decisions:
        if not isinstance(d, dict):
            continue
        text = str(d.get("description") or d.get("title") or "").strip()
        if text:
            decision_texts.append(text)
    if decision_texts:
        longest = max(decision_texts, key=len)
        return _word_truncate(longest, _BRIEF_TOPIC_MAX)

    title = str(payload.get("title") or "").strip()
    if title:
        return _word_truncate(title, _BRIEF_TOPIC_MAX)

    return ""


async def _find_overlap_candidates(
    description: str,
    ledger,
    top_k: int = 3,
) -> list[SupersessionCandidate]:
    """Query existing decisions via BM25 to surface supersession candidates.

    Returns up to ``top_k`` decisions whose description overlaps with
    ``description``, excluding exact matches. Pure retrieval — no LLM.
    The caller-LLM skill classifies whether each candidate is a true
    supersession (ask) or a parallel decision (auto-record silently).

    Returns [] on any failure so a BM25 error never breaks ingest.
    """
    try:
        rows = await ledger.search_by_query(
            query=description,
            max_results=top_k + 1,  # +1 to account for potential self-match
            min_confidence=0.4,
        )
    except Exception as exc:
        logger.debug("[ingest] supersession BM25 query failed: %s", exc)
        return []

    candidates: list[SupersessionCandidate] = []
    desc_lower = description.lower().strip()
    for row in rows:
        # Skip self-match (exact description equality, case-insensitive)
        if (row.get("description") or "").lower().strip() == desc_lower:
            continue
        candidates.append(SupersessionCandidate(
            decision_id=row.get("decision_id") or row.get("id") or "",
            description=row.get("description") or "",
            overlap_score=float(row.get("score") or row.get("confidence") or 0.0),
            product_signoff=row.get("product_signoff"),
            projected_status=row.get("status") or "ungrounded",
        ))
        if len(candidates) >= top_k:
            break

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

    # Stop-and-ask v1: supersession candidate detection.
    # For each incoming decision, BM25-query existing decisions and surface
    # top-3 overlap candidates. Pure retrieval — the caller-LLM skill
    # classifies whether each candidate is a true supersession (ask) or a
    # parallel decision (auto-record silently). Failures are swallowed so
    # this never blocks the ingest itself.
    supersession_candidates_all: list[SupersessionCandidate] = []
    for mapping in payload.get("mappings") or []:
        description = mapping.get("intent") or (mapping.get("span") or {}).get("text", "")
        if not description:
            continue
        try:
            candidates = await _find_overlap_candidates(description, ledger, top_k=3)
            supersession_candidates_all.extend(candidates)
        except Exception as exc:
            logger.debug("[ingest] supersession scan failed for '%s': %s", description[:60], exc)

    # No auto-grounding: mappings are passed through as-is.
    # Caller-LLM binding flow: the caller uses bicameral.bind after ingest
    # to supply code regions for ungrounded decisions.
    mappings = payload.get("mappings") or []
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

    # Sync ledger to HEAD and re-ground any previously ungrounded intents.
    # The LinkCommitResponse carries ``pending_compliance_checks`` from the
    # drift sweep — the caller LLM resolves them via bicameral.resolve_compliance.
    sync_status = None
    try:
        sync_status = await handle_link_commit(ctx, "HEAD")
    except Exception as exc:
        logger.warning("[ingest] post-ingest link_commit failed: %s", exc)

    # v0.4.8: auto-fire bicameral_brief on a derived topic so the caller
    # gets divergence / drift / gap / suggested_question signal in the
    # same response as the ingest stats. Brief failures are logged and
    # swallowed — they must not break the ingest itself.
    brief_response = None
    brief_topic: str | None = None
    try:
        brief_topic = _derive_brief_topic(payload)
        if brief_topic:
            from handlers.brief import handle_brief
            brief_participants = payload.get("participants") or None
            brief_response = await handle_brief(
                ctx,
                topic=brief_topic,
                participants=brief_participants,
            )
    except Exception as exc:
        logger.warning("[ingest] post-ingest brief chain failed: %s", exc)

    # v0.4.16: when the brief produced decisions, chain into the gap
    # judge to attach a caller-session judgment_payload. The payload
    # carries the rubric + context pack; the caller's Claude session
    # applies it in its own LLM context. Server never calls an LLM.
    # Standalone bicameral.brief calls never go through this path —
    # only the ingest auto-chain attaches the payload.
    judgment_payload = None
    if (
        brief_response is not None
        and brief_response.decisions
        and brief_topic
    ):
        try:
            from handlers.gap_judge import handle_judge_gaps
            judgment_payload = await handle_judge_gaps(
                ctx, topic=brief_topic,
            )
        except Exception as exc:
            logger.warning("[ingest] post-brief gap-judge chain failed: %s", exc)

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
        pending_grounding_decisions=list(
            result.get("ungrounded_decisions", [])
        ),
        supersession_candidates=supersession_candidates_all,
        source_cursor=cursor_summary,
        brief=brief_response,
        judgment_payload=judgment_payload,
        sync_status=sync_status,
    )

    try:
        from dashboard.server import notify_dashboard
        await notify_dashboard(ctx)
    except Exception:
        pass

    return ingest_response
