"""Handler for /bicameral_preflight MCP tool.

Proactive context surfacing: agents call this BEFORE implementing
code to get a gated context block. The handler:

  1. Validates the topic deterministically (≥4 chars, ≥2 content tokens,
     not a generic catch-all). Failed validation → fired=False.
  2. Checks per-session dedup — if the same topic was preflight-checked
     within the last 5 minutes, fired=False.
  3. Region-anchored lookup: if the caller passed ``file_paths``, looks
     up decisions pinned to those files in the ledger.
  4. Ledger keyword search: ``handle_search_decisions(topic)``.
  5. Merges region-anchored (higher precision) with keyword matches.
  6. Empty matches → fired=False with reason=no_matches.
  7. Runs divergence detection and gap extraction directly on search
     results (pure functions from handlers.analysis — no extra IO).
  8. **Gating**:
     - guided_mode=False (normal): fired=True only when matches contain
       drift, ungrounded, divergences, or open questions.
     - guided_mode=True (standard): fired=True on any matches.
  9. Returns a ``PreflightResponse`` with everything composed.

The gate logic lives in Python, not in the skill markdown. The skill is
a thin wrapper that renders the response when fired=True.

Trust contract: ``fired=False`` means the agent produces ZERO OUTPUT.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from contracts import (
    ActionHint,
    BriefDecision,
    BriefDivergence,
    BriefGap,
    CodeRegionSummary,
    DecisionMatch,
    PreflightResponse,
)
from handlers.analysis import _detect_divergences, _extract_gaps, _to_brief_decision
from handlers.action_hints import generate_hints_from_findings
from handlers.search_decisions import handle_search_decisions

logger = logging.getLogger(__name__)


# v0.4.12: dedup TTL — same topic preflight-checked within this many
# seconds in the current MCP server session is silently skipped. Avoids
# the "developer asks 4 follow-up questions about Stripe webhook,
# preflight fires 4 times" annoyance. 5 minutes is long enough to cover
# a back-and-forth conversation, short enough that the next implementation
# session gets fresh context.
_DEDUP_TTL_SECONDS = 300

_GENERIC_TOPICS = frozenset({
    "code", "project", "everything", "anything", "stuff",
    "thing", "things", "feature", "features", "system",
    "module", "function", "method",
})

_STOPWORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "are", "from", "have",
    "will", "when", "then", "been", "also", "into", "about", "should",
    "must", "need", "each", "they", "their", "there", "which", "where",
    "what", "than", "some", "more", "such", "only", "very", "just",
    "like", "make", "made", "use", "used", "using", "after", "before",
    "over", "under", "between", "through", "against", "implement",
    "build", "create", "modify", "refactor", "update", "change", "fix",
    "edit", "remove", "delete",
})


def _content_tokens(text: str) -> set[str]:
    """Lowercase non-stopword 4+ char tokens. Reuses the FC-3 tokenizer
    shape but with implementation verbs added to the stopword set so
    'implement Stripe webhook' yields ['stripe', 'webhook']."""
    import re
    raw = re.findall(r"[A-Za-z]{4,}", text or "")
    return {t.lower() for t in raw if t.lower() not in _STOPWORDS}


def _validate_topic(topic: str) -> bool:
    """Deterministic guard: topic must be non-trivial enough that ledger
    keyword search has a chance of finding meaningful matches.

    Returns False when:
    - Topic is empty or shorter than 4 chars
    - Topic has fewer than 2 content tokens after stopword/length filtering
    - Topic is a generic catch-all single word
    """
    if not topic or len(topic.strip()) < 4:
        return False
    normalized = topic.strip().lower()
    if normalized in _GENERIC_TOPICS:
        return False
    tokens = _content_tokens(topic)
    if len(tokens) < 2:
        return False
    return True


def _dedup_key_for(topic: str) -> str:
    """Normalize topic for dedup key — case-insensitive, content-tokens
    only, sorted. Catches phrasings like 'Stripe webhook' and
    'webhook stripe' as the same topic."""
    return " ".join(sorted(_content_tokens(topic)))


def _check_dedup(ctx, topic: str) -> bool:
    """Return True when this topic was already preflight-checked within
    ``_DEDUP_TTL_SECONDS``. Marks the topic as checked at current time
    when not deduped (so repeat fires within the window are silenced).
    """
    sync_state = getattr(ctx, "_sync_state", None)
    if not isinstance(sync_state, dict):
        return False
    topics: dict[str, float] = sync_state.setdefault("preflight_topics", {})
    key = _dedup_key_for(topic)
    if not key:
        return False
    now = time.time()
    last = topics.get(key, 0.0)
    if now - last < _DEDUP_TTL_SECONDS:
        return True
    topics[key] = now
    return False


def _has_actionable_signal_in_search(matches: list) -> bool:
    """Normal mode trust gate: surface only when there's something the
    developer actually needs to know. Drifted decisions count, ungrounded
    decisions count. Plain reflected matches don't.
    """
    return any(
        m.status in ("drifted", "ungrounded")
        for m in matches
    )



async def _region_anchored_preflight(
    ctx,
    file_paths: list[str],
) -> list[DecisionMatch]:
    """file_paths (caller-supplied) → decisions pinned to those regions.

    The caller LLM is responsible for resolving which files a proposed change
    will touch — preflight then looks up decisions pinned to those files in
    the ledger. Returns DecisionMatch objects with confidence=0.9 (direct
    pin, not keyword match).
    """
    if not file_paths:
        return []

    # Dedup + normalize while preserving caller-supplied order.
    seen_paths: set[str] = set()
    ordered: list[str] = []
    for fp in file_paths:
        fp = (fp or "").strip()
        if fp and fp not in seen_paths:
            seen_paths.add(fp)
            ordered.append(fp)
    if not ordered:
        return []

    try:
        raw = await ctx.ledger.get_decisions_for_files(ordered)
    except Exception as exc:
        logger.debug("[preflight:region] ledger region lookup failed: %s", exc)
        return []

    matches: list[DecisionMatch] = []
    seen_ids: set[str] = set()
    for d in raw:
        did = d.get("decision_id", "")
        if did in seen_ids:
            continue
        seen_ids.add(did)
        region_dict = d.get("code_region")
        regions = []
        if region_dict:
            regions = [CodeRegionSummary(
                file_path=region_dict.get("file_path", ""),
                symbol=region_dict.get("symbol", ""),
                lines=tuple(region_dict.get("lines", (0, 0))),
                purpose=region_dict.get("purpose", ""),
            )]

        status = str(d.get("status") or "ungrounded")
        if status not in ("reflected", "drifted", "pending", "ungrounded"):
            status = "ungrounded" if not regions else "pending"

        matches.append(DecisionMatch(
            decision_id=d.get("decision_id", ""),
            description=d.get("description", ""),
            status=status,
            confidence=0.9,
            source_ref=d.get("source_ref", ""),
            code_regions=regions,
            drift_evidence="",
            related_constraints=[],
            source_excerpt=d.get("source_excerpt", ""),
            meeting_date=d.get("meeting_date", ""),
            signoff=d.get("signoff"),
        ))

    return matches


def _merge_decision_matches(
    region: list[DecisionMatch],
    keyword: list[DecisionMatch],
) -> list[DecisionMatch]:
    """Union of region-anchored and ledger-keyword matches, deduplicated by decision_id.

    Region-anchored results come first (direct pin = higher precision). Keyword
    results fill in decisions that exist in the ledger but aren't yet pinned to
    code regions.
    """
    seen: set[str] = set()
    merged: list[DecisionMatch] = []
    for m in region:
        if m.decision_id not in seen:
            seen.add(m.decision_id)
            merged.append(m)
    for m in keyword:
        if m.decision_id not in seen:
            seen.add(m.decision_id)
            merged.append(m)
    return merged


async def handle_preflight(
    ctx,
    topic: str,
    file_paths: list[str] | None = None,
    participants: list[str] | None = None,
) -> PreflightResponse:
    """Pre-flight context check. Gates output by ``ctx.guided_mode``."""
    guided_mode = bool(getattr(ctx, "guided_mode", False))

    # Explicit mute via env var — one-line off-switch for the session.
    if os.getenv("BICAMERAL_PREFLIGHT_MUTE", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return PreflightResponse(
            topic=topic,
            fired=False,
            reason="preflight_disabled",
            guided_mode=guided_mode,
        )

    # Topic validation — deterministic guard. Failed validation =
    # silent skip, no preflight call.
    if not _validate_topic(topic):
        logger.debug("[preflight] topic failed validation: %r", topic[:60])
        return PreflightResponse(
            topic=topic,
            fired=False,
            reason="topic_too_generic",
            guided_mode=guided_mode,
        )

    # Per-session dedup — same topic within 5 min is silenced.
    if _check_dedup(ctx, topic):
        logger.debug("[preflight] dedup hit for topic: %r", topic[:60])
        return PreflightResponse(
            topic=topic,
            fired=False,
            reason="recently_checked",
            guided_mode=guided_mode,
        )

    from handlers.sync_middleware import ensure_ledger_synced
    await ensure_ledger_synced(ctx)

    sources_chained: list[str] = []

    # Step 1a — region-anchored lookup: caller-supplied file_paths → pinned decisions.
    # The caller LLM (Claude Code etc.) has already scoped which files the proposed
    # change will touch; preflight uses that directly. High-precision pin, not a
    # keyword-overlap guess. Empty file_paths skips this arm silently.
    region_matches: list[DecisionMatch] = []
    if file_paths:
        try:
            region_matches = await _region_anchored_preflight(ctx, file_paths)
            if region_matches:
                sources_chained.append("region")
        except Exception as exc:
            logger.debug("[preflight] region lookup failed: %s", exc)

    # Step 1b — ledger keyword search on decision descriptions.
    # Catches decisions not yet pinned to code regions (ungrounded), and decisions
    # whose text happens to overlap with the topic vocabulary.
    try:
        search_resp = await handle_search_decisions(
            ctx,
            query=topic,
            max_results=10,
            min_confidence=0.4,
        )
        sources_chained.append("search")
    except Exception as exc:
        # Fail open — preflight never blocks on bicameral being unavailable.
        logger.warning("[preflight] search failed, fail-open: %s", exc)
        return PreflightResponse(
            topic=topic,
            fired=False,
            reason="no_matches",
            guided_mode=guided_mode,
        )

    # Merge: region-anchored results first (direct pin = high precision),
    # BM25 fills in decisions that aren't yet pinned to any code region.
    if region_matches:
        merged = _merge_decision_matches(region_matches, search_resp.matches)
        search_resp = search_resp.model_copy(update={"matches": merged})

    if not search_resp.matches:
        return PreflightResponse(
            topic=topic,
            fired=False,
            reason="no_matches",
            guided_mode=guided_mode,
            sources_chained=sources_chained,
        )

    # Search-level gate: in normal mode, require actionable signal.
    search_actionable = _has_actionable_signal_in_search(search_resp.matches)

    # Run divergence detection and gap extraction directly on search results.
    # Pure functions — no extra IO, no duplicate link_commit or search call.
    divergences = _detect_divergences(search_resp.matches)
    gaps = _extract_gaps(search_resp.matches)
    open_questions = [
        g for g in gaps
        if "open-question" in g.hint or "open question" in g.hint
    ]
    analysis_actionable = bool(divergences) or bool(open_questions)

    # Final gate: normal mode fires only on actionable signal; guided fires on any match.
    if guided_mode:
        fired = True
        reason = "fired"
    elif search_actionable or analysis_actionable:
        fired = True
        reason = "fired"
    else:
        fired = False
        reason = "no_actionable_signal"

    if not fired:
        return PreflightResponse(
            topic=topic,
            fired=False,
            reason=reason,  # type: ignore[arg-type]
            guided_mode=guided_mode,
            sources_chained=sources_chained,
        )

    decisions = [_to_brief_decision(m) for m in search_resp.matches]
    drift_candidates = [
        _to_brief_decision(m)
        for m in search_resp.matches
        if m.status == "drifted"
    ]
    action_hints = generate_hints_from_findings(
        divergences, drift_candidates, gaps, guided_mode,
    ) or search_resp.action_hints

    # v0.8.0: HITL annotations — topic-independent ledger health checks.
    # These fire on every preflight (when fired=True) regardless of topic.
    unresolved_collisions: list[BriefDecision] = []
    context_pending_ready: list[BriefDecision] = []
    try:
        from ledger.queries import get_collision_pending_decisions, get_context_for_ready_decisions
        inner = getattr(ctx.ledger, "_inner", ctx.ledger)
        client = inner._client
        coll_rows = await get_collision_pending_decisions(client)
        for r in coll_rows:
            unresolved_collisions.append(BriefDecision(
                decision_id=r["decision_id"],
                description=r["description"],
                status="proposal",
                signoff=r.get("signoff"),
            ))
        ctx_rows = await get_context_for_ready_decisions(client)
        for r in ctx_rows:
            context_pending_ready.append(BriefDecision(
                decision_id=r["decision_id"],
                description=r["description"],
                status="context_pending",
                signoff=r.get("signoff"),
            ))
    except Exception as exc:
        logger.debug("[preflight] HITL annotation queries failed: %s", exc)

    return PreflightResponse(
        topic=topic,
        fired=True,
        reason="fired",  # type: ignore[arg-type]
        guided_mode=guided_mode,
        decisions=decisions,
        drift_candidates=drift_candidates,
        divergences=divergences,
        open_questions=open_questions,
        action_hints=action_hints,
        sources_chained=sources_chained,
        unresolved_collisions=unresolved_collisions,
        context_pending_ready=context_pending_ready,
    )
