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
        # v0.4.16: accept ``text`` as a synonym for ``description`` / ``title``.
        # The natural-format example in skills/bicameral-ingest/SKILL.md
        # documents ``{text: "..."}`` — without this fallback, Pydantic
        # silently drops the unknown field and the decision evaporates.
        text = d.description or d.title or d.text
        if not text:
            continue
        # Prefer the explicit raw passage when the caller provides one.
        # Fall back to the decision text only as a placeholder — downstream
        # yields-JOIN filters drop text==description spans as synthetic.
        span_text = d.source_excerpt or text
        mappings.append({
            "intent": text,
            "span": {
                **source_meta,
                "text": span_text,
                "source_ref": d.id or source_meta["source_ref"],
                "speakers": d.participants or source_meta["speakers"],
            },
            "symbols": [],
            "code_regions": [],
        })

    for a in validated.action_items:
        # v0.4.16: accept ``text`` as a synonym for ``action``. Without this
        # fallback, an action_item following the SKILL.md example literally
        # (`{text: "...", owner: "..."}`) produces a phantom
        # ``[Action: <owner>] `` prefix with no body, which then BM25-grounds
        # against any code symbol containing "Action" in its name. Witnessed
        # live during the v0.4.16 dogfood ingest of the demo gallery.
        body = a.action or a.text
        if not body:
            continue
        intent = f"[Action: {a.owner}] {body}"
        mappings.append({
            "intent": intent,
            "span": {**source_meta, "text": intent},
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


# ── FC-3: vocab cache similarity gate ──────────────────────────────
#
# The vocab cache uses SurrealDB's ``@0@`` BM25 full-text operator to match
# incoming descriptions against stored ``query_text``. Without a similarity
# threshold, two unrelated intents sharing incidental tokens cross-match —
# witnessed live on Accountable 2026-04-14 where a "Stripe payment-link
# fallback" decision inherited 8 bogus regions from an earlier "weekly
# bulletin page" ingest.
#
# The gate below computes Jaccard similarity over non-stopword tokens ≥4
# chars. Cache hits below the threshold are discarded, forcing the caller
# to fall through to fresh grounding (which is already correct, per FC-2).
# Jaccard was chosen over embeddings because:
#   1. Deterministic, no model dependency (git-for-specs.md invariant:
#      "no LLM in critical indexing path")
#   2. The downstream ground_mappings pipeline already handles semantic
#      variation via BM25+graph fusion — an embedding gate here would
#      double-count
#   3. 20 LOC vs 200+ LOC with a new dependency

_VOCAB_SIMILARITY_THRESHOLD = 0.5

_VOCAB_STOPWORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "are", "from", "have",
    "will", "when", "then", "been", "also", "into", "about", "should",
    "must", "need", "each", "they", "their", "there", "which", "where",
    "what", "than", "some", "more", "such", "only", "very", "just",
    "like", "make", "made", "use", "used", "using", "after", "before",
    "over", "under", "between", "through", "against",
})


def _content_tokens(text: str) -> set[str]:
    """Lowercase, non-stopword, ≥4-char tokens for similarity comparison."""
    import re
    raw = re.findall(r"[A-Za-z]{4,}", text or "")
    return {t.lower() for t in raw if t.lower() not in _VOCAB_STOPWORDS}


def _jaccard_similarity(a: str, b: str) -> float:
    """Jaccard coefficient over ``_content_tokens`` sets.

    Returns 0.0 when either set is empty. Returns 1.0 when both strings
    produce identical token sets.
    """
    ta = _content_tokens(a)
    tb = _content_tokens(b)
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    union = ta | tb
    return len(intersection) / len(union)


def _validate_cached_regions(
    regions: list[dict],
    code_graph,
    current_description: str = "",
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

    v0.4.7 (FC-3): when ``current_description`` is non-empty, the returned
    region's ``purpose`` field is rewritten to it. Previously this function
    preserved the cached region's stale ``purpose`` (= the ORIGINAL
    intent's description), cross-wiring intents so one decision's regions
    carried another decision's label. Witnessed live on Accountable
    2026-04-14.
    """
    try:
        code_graph._ensure_initialized()
        db = code_graph._db
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
        entry = {
            **region,
            "file_path": row["file_path"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "type": row["type"],
        }
        if current_description:
            entry["purpose"] = current_description  # FC-3: rewrite stale purpose
        valid.append(entry)
    return valid


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

    # Vocab cache reuse: check if similar queries were already grounded.
    # Runs before ground_mappings — a hit skips the full BM25 pipeline.
    mappings_to_ground = payload.get("mappings") or []
    cache_hits = 0
    cache_similarity_rejections = 0
    pre_grounded: set[str] = set()
    for mapping in mappings_to_ground:
        if mapping.get("code_regions"):
            # Track caller-supplied regions so write-back doesn't cache them
            desc = mapping.get("intent") or (mapping.get("span") or {}).get("text", "")
            if desc:
                pre_grounded.add(desc)
            continue
        description = mapping.get("intent") or (mapping.get("span") or {}).get("text", "")
        if not description:
            continue
        try:
            cached_symbols, matched_query_text = await ledger.lookup_vocab_cache(description, repo)
            if cached_symbols:
                # FC-3 similarity gate: the vocab cache lookup uses SurrealDB's
                # BM25 @0@ operator, which is too loose on its own. Two unrelated
                # intents sharing incidental tokens can cross-match. Compute
                # Jaccard similarity between the incoming description and the
                # matched query_text, and reject the cache hit if it's below
                # threshold. Falls through to fresh grounding via ground_mappings.
                similarity = _jaccard_similarity(description, matched_query_text)
                if similarity < _VOCAB_SIMILARITY_THRESHOLD:
                    cache_similarity_rejections += 1
                    logger.info(
                        "[ingest] vocab cache rejected (similarity %.2f < %.2f): "
                        "current=%r matched=%r",
                        similarity, _VOCAB_SIMILARITY_THRESHOLD,
                        description[:60], matched_query_text[:60],
                    )
                    continue
                valid_regions = _validate_cached_regions(
                    cached_symbols, ctx.code_graph,
                    current_description=description,  # FC-3: rewrite purpose
                )
                if valid_regions:
                    mapping["code_regions"] = valid_regions
                    cache_hits += 1
                    pre_grounded.add(description)
                    logger.info(
                        "[ingest] vocab cache hit for '%s' (%d/%d regions valid, sim=%.2f)",
                        description[:60],
                        len(valid_regions), len(cached_symbols), similarity,
                    )
                else:
                    logger.debug(
                        "[ingest] vocab cache discarded (all regions stale): '%s'",
                        description[:60],
                    )
        except Exception as exc:
            logger.debug("[ingest] vocab cache lookup failed: %s", exc)

    mappings, grounding_deferred = ctx.code_graph.ground_mappings(mappings_to_ground)

    # Write-back: cache newly-grounded results in vocab_cache for future reuse.
    for mapping in mappings:
        code_regions = mapping.get("code_regions")
        if not code_regions:
            continue
        desc = mapping.get("intent") or (mapping.get("span") or {}).get("text", "")
        if not desc or desc in pre_grounded:
            continue
        try:
            await ledger.upsert_vocab_cache(desc, repo, code_regions)
        except Exception as exc:
            logger.debug("[ingest] vocab cache write failed: %s", exc)

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

    # Sync ledger to HEAD and re-ground any previously ungrounded intents
    try:
        await handle_link_commit(ctx, "HEAD")
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
        "[ingest] complete: %d/%d grounded (%.0f%%) | deferred=%d | source_refs=%s",
        grounded_count,
        intents_created,
        grounded_pct * 100.0,
        grounding_deferred,
        source_refs,
    )

    return IngestResponse(
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
            grounding_deferred=grounding_deferred,
            cache_hits=cache_hits,
        ),
        ungrounded_intents=list(result.get("ungrounded_intents", [])),
        source_cursor=cursor_summary,
        brief=brief_response,
        judgment_payload=judgment_payload,
    )
