"""Handler for /bicameral_preflight MCP tool (v0.4.12).

Proactive context surfacing: agents call this BEFORE implementing
code to get a gated context block. The handler:

  1. Validates the topic deterministically (≥4 chars, ≥2 content tokens,
     not a generic catch-all). Failed validation → fired=False.
  2. Checks per-session dedup — if the same topic was preflight-checked
     within the last 5 minutes, fired=False.
  3. Calls ``handle_search_decisions(topic)`` internally.
  4. Empty matches → fired=False with reason=no_matches.
  5. **Gating**:
     - guided_mode=False (normal): fired=True only when matches contain
       drift, ungrounded decisions, or (after brief chain) divergences /
       open questions. "Less intense" — silent on plain matches.
     - guided_mode=True (standard): fired=True on any matches.
  6. Conditionally chains to ``handle_brief(topic)`` when matches contain
     drift or ungrounded status — that's where divergences and gaps live.
  7. Returns a ``PreflightResponse`` with everything composed.

The gate logic lives in Python (here), not in the skill markdown, so
it's enforced regardless of agent compliance. The skill is a thin
wrapper that calls this tool and renders the response when fired=True.

Trust contract: ``fired=False`` means the agent produces ZERO OUTPUT to
the user. No "I checked and found nothing" noise. The empty path is
silent.
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
    PreflightResponse,
)
from handlers.brief import handle_brief, _to_brief_decision
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
    """Deterministic guard: topic must be non-trivial enough that BM25
    has a chance of finding meaningful matches.

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


def _has_actionable_signal_in_brief(brief_response) -> bool:
    """Brief-level signal: divergences or open-question gaps. Drift is
    already covered by the search-level gate.
    """
    if brief_response.divergences:
        return True
    if any(
        ("open-question" in g.hint or "open question" in g.hint)
        for g in brief_response.gaps
    ):
        return True
    return False


def _extract_open_questions(brief_response) -> list[BriefGap]:
    return [
        g for g in brief_response.gaps
        if "open-question" in g.hint or "open question" in g.hint
    ]


async def handle_preflight(
    ctx,
    topic: str,
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

    # Lazy HEAD catch-up: if HEAD has advanced since the last link_commit sync
    # (e.g. a commit was made outside of a bicameral.ingest call), run it now
    # so the search below reflects the latest committed code.
    # Swallow all exceptions — preflight must never block on ledger issues.
    try:
        from handlers.link_commit import handle_link_commit, _read_current_head_sha
        live_head = _read_current_head_sha(ctx.repo_path)
        sync_state = getattr(ctx, "_sync_state", None) or {}
        if live_head and live_head != sync_state.get("last_sync_sha"):
            await handle_link_commit(ctx, "HEAD")
            logger.debug("[preflight] lazy link_commit ran for %s", live_head[:8])
    except Exception as exc:
        logger.debug("[preflight] lazy link_commit failed: %s", exc)

    sources_chained: list[str] = []

    # Step 1 — call search. Cheap, single ledger query.
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

    if not search_resp.matches:
        return PreflightResponse(
            topic=topic,
            fired=False,
            reason="no_matches",
            guided_mode=guided_mode,
            sources_chained=sources_chained,
        )

    # Search-level gate: in normal mode, require actionable signal in
    # the search response itself. Plain reflected matches are silenced.
    search_actionable = _has_actionable_signal_in_search(search_resp.matches)

    # Step 2 — conditionally chain to brief. Brief is the source of truth
    # for divergences and open-question gaps. We chain when the search
    # already shows signal (drift/ungrounded), or when guided mode wants
    # the full picture even on plain matches.
    chain_brief = search_actionable or guided_mode
    brief_resp = None
    if chain_brief:
        try:
            brief_resp = await handle_brief(
                ctx, topic=topic, participants=participants,
            )
            sources_chained.append("brief")
        except Exception as exc:
            logger.warning("[preflight] brief chain failed, continuing: %s", exc)
            brief_resp = None

    brief_actionable = bool(brief_resp) and _has_actionable_signal_in_brief(brief_resp)

    # Final gate decision: in normal mode, fired=True only when at least
    # one of search or brief turned up actionable signal. In guided mode,
    # fired=True on any matches at all.
    if guided_mode:
        fired = True
        reason = "fired"
    else:
        if search_actionable or brief_actionable:
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

    # Compose the populated response. Brief is the richer view (it has
    # severity_tier, drift_evidence, divergences, gaps), but it can
    # return empty in pathological cases. Fall back to search-derived
    # values when brief didn't surface anything useful.
    brief_has_decisions = (
        brief_resp is not None and len(brief_resp.decisions) > 0
    )
    if brief_has_decisions:
        decisions = brief_resp.decisions
        drift_candidates = brief_resp.drift_candidates
    else:
        decisions = [_to_brief_decision(m) for m in search_resp.matches]
        drift_candidates = [
            _to_brief_decision(m)
            for m in search_resp.matches
            if m.status == "drifted"
        ]

    # Divergences and open questions can only come from brief (search
    # doesn't compute them). When brief was skipped or empty, they're
    # naturally empty here.
    divergences = brief_resp.divergences if brief_resp is not None else []
    open_questions = _extract_open_questions(brief_resp) if brief_resp is not None else []

    # Action hints come from whichever response carries more signal.
    # Brief's hint set is a superset of search's when brief was chained,
    # so prefer brief when it has anything; otherwise use search's hints.
    if brief_resp is not None and brief_resp.action_hints:
        action_hints = brief_resp.action_hints
    else:
        action_hints = search_resp.action_hints

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
    )
