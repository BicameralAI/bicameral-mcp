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
import re
import time
from pathlib import Path

from contracts import (
    BriefDecision,
    CodeRegionSummary,
    DecisionMatch,
    PreflightResponse,
)
from handlers.action_hints import generate_hints_from_findings
from handlers.analysis import _to_brief_decision
from preflight_telemetry import (
    new_preflight_id,
    telemetry_enabled,
    write_dedup_event,
    write_preflight_event,
)
from protocol.categorization import grounding_analyze

logger = logging.getLogger(__name__)


# v0.4.12: dedup TTL — same topic preflight-checked within this many
# seconds in the current MCP server session is silently skipped. Avoids
# the "developer asks 4 follow-up questions about Stripe webhook,
# preflight fires 4 times" annoyance. 5 minutes is long enough to cover
# a back-and-forth conversation, short enough that the next implementation
# session gets fresh context.
_DEDUP_TTL_SECONDS = 300

_PRODUCT_STAGE_MSG = (
    "Note: some operations (ingest, compliance checks, index sweeps) may take "
    "a few minutes — this is expected at the current scale. "
    "Always keep bicameral-mcp up to date (`bicameral.update`) for the fastest experience."
)
_ONBOARDED_MARKER = Path.home() / ".bicameral" / "onboarded"


# #200 Phase 3 / #209 refinement: render_source_attribution policy patterns.
# The redacted mode preserves structural shape (so the operator can see
# "this is from a meeting on a date" without seeing who or when) while
# leaving capitalized context tokens (Sprint, Linear, GitHub, etc.) intact.
#
# v1 used a broad `\b[A-Z][a-z]+\b` that redacted every capitalized lowercase
# token — including platform/tool names — and broke the agent's structural
# parsing of source_refs (#209). v2 uses four POSITIONAL-cue patterns: a name
# only matches when it follows an explicit cue (`· `, `, ` adjacent to a date,
# `^Speaker:\s`, `^From:\s`). Context tokens never follow these cues by
# construction, so no allowlist is needed.
_DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NAME_AFTER_BULLET = re.compile(r"(?<=· )[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)*")
_NAME_AFTER_COMMA = re.compile(
    r"(?<=, )[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)*(?=,?\s+\d{4}-\d{2}-\d{2})"
)
_SPEAKER_NAME = re.compile(r"(?<=^Speaker:\s)[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)*", re.MULTILINE)
_FROM_NAME = re.compile(r"(?<=^From:\s)[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)*", re.MULTILINE)


def _apply_attribution_policy(matches: list, mode: str) -> list:
    """Apply `render_source_attribution` policy to DecisionMatch.source_ref.

    Modes (from `.bicameral/config.yaml: render_source_attribution`):
      - `full`: pass through verbatim (legacy)
      - `redacted` (default since #209): replace name + date patterns with
        placeholders. Names match only after explicit positional cues (`· `,
        `, ` adjacent to a date, `Speaker:`, `From:`); context tokens like
        Sprint/Linear/GitHub survive because they never follow these cues.
      - `hidden`: blank source_ref entirely

    Returns a new list of DecisionMatch instances (Pydantic copies via
    model_copy) with source_ref transformed; never mutates the inputs.
    The function is pure: same inputs → same outputs, no I/O, no state.
    """
    if mode == "full":
        return matches
    transformed = []
    for m in matches:
        if mode == "hidden":
            new_source_ref = ""
        else:  # redacted
            redacted = _DATE_PATTERN.sub("<DATE_REDACTED>", m.source_ref)
            redacted = _NAME_AFTER_BULLET.sub("<NAME_REDACTED>", redacted)
            redacted = _NAME_AFTER_COMMA.sub("<NAME_REDACTED>", redacted)
            redacted = _SPEAKER_NAME.sub("<NAME_REDACTED>", redacted)
            redacted = _FROM_NAME.sub("<NAME_REDACTED>", redacted)
            new_source_ref = redacted
        transformed.append(m.model_copy(update={"source_ref": new_source_ref}))
    return transformed


def _should_show_product_stage() -> bool:
    """True on first preflight call per device. Creates the marker on first call."""
    try:
        if _ONBOARDED_MARKER.exists():
            return False
        _ONBOARDED_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _ONBOARDED_MARKER.touch()
        return True
    except Exception:
        return False


_GENERIC_TOPICS = frozenset(
    {
        "code",
        "project",
        "everything",
        "anything",
        "stuff",
        "thing",
        "things",
        "feature",
        "features",
        "system",
        "module",
        "function",
        "method",
    }
)

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "are",
        "from",
        "have",
        "will",
        "when",
        "then",
        "been",
        "also",
        "into",
        "about",
        "should",
        "must",
        "need",
        "each",
        "they",
        "their",
        "there",
        "which",
        "where",
        "what",
        "than",
        "some",
        "more",
        "such",
        "only",
        "very",
        "just",
        "like",
        "make",
        "made",
        "use",
        "used",
        "using",
        "after",
        "before",
        "over",
        "under",
        "between",
        "through",
        "against",
        "implement",
        "build",
        "create",
        "modify",
        "refactor",
        "update",
        "change",
        "fix",
        "edit",
        "remove",
        "delete",
    }
)


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


def _normalize_file_paths_for_key(file_paths: list[str] | None) -> str:
    """Canonicalize file_paths into a stable string component for the dedup
    cache key. Sorted + lowercased + deduplicated — order-insensitive so
    callers passing ``["a.py", "b.py"]`` and ``["b.py", "a.py"]`` collide.
    Empty / None collapse to an empty string (the absent-path sentinel).
    """
    if not file_paths:
        return ""
    seen: set[str] = set()
    out: list[str] = []
    for fp in file_paths:
        if not fp:
            continue
        norm = fp.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return "|".join(sorted(out))


def _dedup_key_for(
    topic: str,
    file_paths: list[str] | None = None,
    ledger_revision: str | None = None,
) -> str:
    """Compose the per-session preflight dedup cache key (#87 Phase 4).

    The key is the 3-tuple ``(normalized_topic, normalized_file_paths,
    ledger_revision)`` joined by ``||`` (an unambiguous separator that
    can't appear in any normalized component). All three components must
    match for a cache hit:

    - **normalized_topic** — case-insensitive, content-tokens, sorted.
      Catches phrasings like 'Stripe webhook' / 'webhook stripe' as the
      same topic (legacy v0.4.12 behavior, preserved).
    - **normalized_file_paths** — sorted + lowercased + deduplicated. A
      same-topic call against a different region misses the cache.
    - **ledger_revision** — MAX(updated_at) over the decision table at
      call time. Any ledger mutation (new decision, status change, HITL
      signoff write) bumps this and invalidates the cache for prior
      same-topic calls.

    ``ledger_revision=None`` is reserved for the bypass path: callers MUST
    NOT pass None and expect dedup to function. The handler checks for
    None separately and skips dedup entirely (Kevin's amendment).
    """
    topic_norm = " ".join(sorted(_content_tokens(topic)))
    paths_norm = _normalize_file_paths_for_key(file_paths)
    rev_norm = ledger_revision or ""
    return f"{topic_norm}||{paths_norm}||{rev_norm}"


def _check_dedup(
    ctx,
    topic: str,
    file_paths: list[str] | None = None,
    ledger_revision: str | None = None,
) -> bool:
    """Return True when the (topic, file_paths, ledger_revision) tuple was
    already preflight-checked within ``_DEDUP_TTL_SECONDS``. Marks the tuple
    as checked at current time when not deduped (so repeat fires within the
    window are silenced).

    The cache is keyed in ``ctx._sync_state["preflight_topics"]`` (the dict
    name is a legacy label kept for backwards-compat — it now holds the
    3-tuple key, not bare topics).
    """
    sync_state = getattr(ctx, "_sync_state", None)
    if not isinstance(sync_state, dict):
        return False
    topics: dict[str, float] = sync_state.setdefault("preflight_topics", {})
    key = _dedup_key_for(topic, file_paths, ledger_revision)
    if not key.split("||")[0]:
        # Empty topic component → topic too short to dedup on; legacy contract.
        return False
    now = time.time()
    last = topics.get(key, 0.0)
    if now - last < _DEDUP_TTL_SECONDS:
        return True
    topics[key] = now
    return False


def _dedup_miss_was_revision_bump(
    ctx,
    topic: str,
    file_paths: list[str] | None,
    ledger_revision: str | None,
) -> bool:
    """Classify a dedup miss: did it miss because ``ledger_revision``
    advanced since the prior same-(topic, file_paths) call?

    Returns True when:
    - the current (topic, file_paths) prefix has been seen before within
      ``_DEDUP_TTL_SECONDS``,
    - but the prior entry's revision component differs from the current
      ``ledger_revision``.

    This is the M7a/M7c signal — a decision landed (M7a) or HITL state
    cleared (M7c) between two same-topic/same-paths calls, and the new
    `ledger_revision` invalidated the cache as intended. Phase 5
    telemetry (#87) emits a ``preflight_dedup_decision`` event with
    ``reason=invalidated_by_revision_bump`` on True.

    False for: first-call misses (no prior prefix entry), expired
    entries (older than TTL), and file_paths-shift misses (the prefix
    itself differs, not the revision suffix).
    """
    sync_state = getattr(ctx, "_sync_state", None)
    if not isinstance(sync_state, dict):
        return False
    topics: dict[str, float] = sync_state.get("preflight_topics") or {}
    if not topics:
        return False
    current_key = _dedup_key_for(topic, file_paths, ledger_revision)
    parts = current_key.split("||")
    if len(parts) != 3:
        return False
    topic_norm, paths_norm, current_rev = parts
    if not topic_norm:
        return False
    prefix = f"{topic_norm}||{paths_norm}||"
    now = time.time()
    for stored_key, ts in topics.items():
        if not stored_key.startswith(prefix):
            continue
        if stored_key == current_key:
            # Identical key — would have been a cache hit, not a miss.
            continue
        if now - ts >= _DEDUP_TTL_SECONDS:
            continue
        # Same prefix, different rev, within TTL → revision bump.
        stored_rev = stored_key[len(prefix) :]
        if stored_rev != current_rev:
            return True
    return False


async def _region_anchored_preflight(
    ctx,
    file_paths: list[str],
) -> tuple[list[DecisionMatch], bool, str | None]:
    """file_paths (caller-supplied) → decisions pinned to those regions.

    The caller LLM is responsible for resolving which files a proposed change
    will touch — preflight then looks up decisions pinned to those files in
    the ledger. Before the lookup, run a 1-hop code-graph expansion via the
    code-locator adapter (#173): caller-LLM discovery is imprecise, and a
    decision bound to ``app/src/lib/git/reorder.ts`` should still surface
    when the caller passes the structurally-near ``app/src/ui/multi-commit-
    operation/reorder.tsx``. Expansion is deterministic, no LLM in the path,
    bounded by ``code_locator/config.py::max_neighbors_per_result``.

    Returns ``(matches, expanded, fallback_reason)``:
      - ``expanded`` is True iff the graph expansion produced extra paths
        beyond the caller-supplied set, so the caller can record ``"graph"``
        in ``sources_chained``. Direct-pin matches carry ``confidence=0.9``;
        matches surfaced only via expanded paths carry ``confidence=0.7``.
      - ``fallback_reason`` is non-None iff expansion was attempted but
        couldn't run cleanly (#243). Possible values: ``"absent"`` (no
        ``code_graph`` on ctx), ``"missing_method"`` (``code_graph`` lacks
        ``expand_file_paths_via_graph``), ``"exception:<type>"`` (expander
        raised). Caller adds ``"graph_unavailable"`` to ``sources_chained``
        when non-None; the granular reason flows to the telemetry counter.
    """
    if not file_paths:
        return [], False, None

    # Dedup + normalize while preserving caller-supplied order.
    seen_paths: set[str] = set()
    ordered: list[str] = []
    for fp in file_paths:
        fp = (fp or "").strip()
        if fp and fp not in seen_paths:
            seen_paths.add(fp)
            ordered.append(fp)
    if not ordered:
        return [], False, None

    # Graph expansion. #243: surface the silent fallback as a loud signal —
    # response carries `"graph_unavailable"` (added by caller), exception
    # case logs at WARN, telemetry counter increments. Three fallback
    # reasons distinguished for the telemetry signal:
    #   - absent          : no `code_graph` on ctx (mock contexts, older
    #                       deployments without the adapter wired)
    #   - missing_method  : `code_graph` set but no
    #                       `expand_file_paths_via_graph` attribute
    #   - exception:<typ> : expander raised at runtime (uninitialized
    #                       index, sqlite locked, missing repo, etc.)
    direct_paths: set[str] = set(ordered)
    expanded_paths = list(ordered)
    expanded_only_paths: set[str] = set()
    fallback_reason: str | None = None
    code_graph = getattr(ctx, "code_graph", None)
    if code_graph is None:
        fallback_reason = "absent"
    else:
        expander = getattr(code_graph, "expand_file_paths_via_graph", None)
        if expander is None:
            fallback_reason = "missing_method"
        else:
            try:
                expanded_paths, added_paths = expander(ordered, hops=1)
                expanded_only_paths = set(added_paths)
            except Exception as exc:
                fallback_reason = f"exception:{type(exc).__name__}"
                logger.warning(
                    "[preflight:fallback] graph expansion raised %s: %s — "
                    "recall degraded for this call (#243)",
                    type(exc).__name__,
                    exc,
                )
                expanded_paths = list(ordered)
                expanded_only_paths = set()

    if fallback_reason is not None:
        try:
            from preflight_telemetry import write_fallback_event

            write_fallback_event(
                reason=fallback_reason,
                session_id=str(getattr(ctx, "session_id", "unknown") or "unknown"),
            )
        except Exception as exc:
            # Telemetry must never break the hot path. Silent on failure
            # (counter just won't increment for this call).
            logger.debug("[preflight:fallback] telemetry emit failed: %s", exc)

    try:
        raw = await ctx.ledger.get_decisions_for_files(expanded_paths)
    except Exception as exc:
        logger.debug("[preflight:region] ledger region lookup failed: %s", exc)
        return [], False, fallback_reason

    matches: list[DecisionMatch] = []
    seen_ids: set[str] = set()
    surfaced_via_expansion = False
    for d in raw:
        did = d.get("decision_id", "")
        if did in seen_ids:
            continue
        seen_ids.add(did)
        region_dict = d.get("code_region")
        regions = []
        if region_dict:
            regions = [
                CodeRegionSummary(
                    file_path=region_dict.get("file_path", ""),
                    symbol=region_dict.get("symbol", ""),
                    lines=tuple(region_dict.get("lines", (0, 0))),
                    purpose=region_dict.get("purpose", ""),
                )
            ]

        status = str(d.get("status") or "ungrounded")
        if status not in ("reflected", "drifted", "pending", "ungrounded"):
            status = "ungrounded" if not regions else "pending"

        # Provenance: a decision is "directly pinned" if any of its bound
        # code_regions live in a caller-supplied path; otherwise it was only
        # reached via 1-hop graph expansion. Caller can de-prioritize the
        # latter (lower confidence) without losing recall.
        bound_paths = {
            (r.get("file_path") or "").strip()
            for r in (d.get("code_regions") or [])
            if r and (r.get("file_path") or "").strip()
        }
        # Single-region decisions also have a top-level ``code_region`` (used
        # above); include it in the provenance check.
        if region_dict and (region_dict.get("file_path") or "").strip():
            bound_paths.add(region_dict["file_path"].strip())
        is_direct = bool(bound_paths & direct_paths) if bound_paths else not expanded_only_paths
        if not is_direct:
            surfaced_via_expansion = True

        _sf = d.get("signoff") or {}
        # #157 — pruned decisions are excluded from preflight surfaces.
        if isinstance(_sf, dict) and _sf.get("state") == "pruned":
            continue
        matches.append(
            DecisionMatch(
                decision_id=d.get("decision_id", ""),
                description=d.get("description", ""),
                status=status,
                signoff_state=(_sf.get("state") if isinstance(_sf, dict) else None),
                confidence=0.9 if is_direct else 0.7,
                source_ref=d.get("source_ref", ""),
                code_regions=regions,
                drift_evidence="",
                related_constraints=[],
                source_excerpt=d.get("source_excerpt", ""),
                meeting_date=d.get("meeting_date", ""),
                signoff=d.get("signoff"),
            )
        )

    return matches, surfaced_via_expansion, fallback_reason


@grounding_analyze("grounding.analyze.preflight")
async def handle_preflight(
    ctx,
    topic: str,
    file_paths: list[str] | None = None,
    participants: list[str] | None = None,
) -> PreflightResponse:
    """Pre-flight context check. Gates output by ``ctx.guided_mode``."""
    guided_mode = bool(getattr(ctx, "guided_mode", False))

    # #65 — generate the per-call preflight_id once, when telemetry is enabled.
    # Stable across the preflight → downstream-tool engagement chain.
    pid: str | None = new_preflight_id() if telemetry_enabled() else None
    session_id = str(getattr(ctx, "session_id", "unknown") or "unknown")

    # Explicit mute via env var — one-line off-switch for the session.
    if os.getenv("BICAMERAL_PREFLIGHT_MUTE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        if pid is not None:
            write_preflight_event(
                session_id=session_id,
                preflight_id=pid,
                topic=topic,
                file_paths=file_paths or [],
                fired=False,
                surfaced_ids=[],
                reason="preflight_disabled",
            )
        return PreflightResponse(
            topic=topic,
            fired=False,
            reason="preflight_disabled",
            guided_mode=guided_mode,
            preflight_id=pid,
        )

    # Per-session dedup (#87 Phase 4) — same (topic, file_paths,
    # ledger_revision) tuple within 5 min is silenced. Revision lookup
    # failures BYPASS dedup entirely (Kevin's amendment on issue #87, B2
    # signoff thread) rather than degrade to a partial key that could
    # silently suppress a valid call. Correctness over saving a preflight
    # call.
    ledger_revision: str | None = None
    try:
        from ledger.queries import get_ledger_revision

        inner = getattr(ctx.ledger, "_inner", ctx.ledger)
        _client = getattr(inner, "_client", None)
        if _client is not None:
            ledger_revision = await get_ledger_revision(_client)
    except Exception as exc:  # noqa: BLE001
        # Defensive — get_ledger_revision already swallows its own
        # exceptions and returns None, but if accessing ctx.ledger._inner
        # raises (test stubs without that shape) we still want to bypass
        # dedup rather than crash the handler.
        logger.warning(
            "[preflight] ledger revision lookup raised — bypassing dedup: %s",
            exc,
        )
        ledger_revision = None

    if ledger_revision is None:
        # BYPASS: revision is unknown → cannot safely dedup. Loud warning
        # for ops visibility; #87 Phase 5 telemetry counter quantifies
        # how often this happens in production. A sustained spike is the
        # signal to look at ledger health (transient SurrealDB faults,
        # schema mismatch, etc.).
        logger.warning(
            "[preflight] dedup bypassed — ledger_revision lookup failed for "
            "topic %r; the next same-topic call will re-evaluate fully",
            topic[:60],
        )
        write_dedup_event(
            reason="bypassed_revision_unknown",
            session_id=session_id,
            preflight_id=pid,
        )
    elif _check_dedup(ctx, topic, file_paths, ledger_revision):
        logger.debug(
            "[preflight] dedup hit for topic=%r file_paths=%s rev=%s",
            topic[:60],
            file_paths,
            ledger_revision[:32] if ledger_revision else "",
        )
        if pid is not None:
            write_preflight_event(
                session_id=session_id,
                preflight_id=pid,
                topic=topic,
                file_paths=file_paths or [],
                fired=False,
                surfaced_ids=[],
                reason="recently_checked",
            )
        return PreflightResponse(
            topic=topic,
            fired=False,
            reason="recently_checked",
            guided_mode=guided_mode,
            preflight_id=pid,
        )

    # Cache-miss classification (#87 Phase 5): if the miss was caused by
    # a ledger_revision bump (same topic + file_paths seen before but
    # with a different revision still within TTL), emit the M7a/c
    # signal. This is the counter Kevin asked for — "so we can tell the
    # new key is doing useful work in production". File-paths-shift
    # misses (M7b) are intentionally NOT counted here; the file_paths
    # component of the key is observable from preflight_events.jsonl
    # via the existing ``file_paths_hash`` field if a follow-up wants
    # to backfill that metric.
    if ledger_revision is not None and _dedup_miss_was_revision_bump(
        ctx, topic, file_paths, ledger_revision
    ):
        write_dedup_event(
            reason="invalidated_by_revision_bump",
            session_id=session_id,
            preflight_id=pid,
        )

    # #343 — ledger-awareness fast-path. When the caller supplied file_paths
    # and guided_mode is off, check whether ANY decisions are bound to those
    # files BEFORE the expensive sync + full query chain. If zero decisions
    # exist, the preflight has no value to surface — return immediately.
    # This eliminates noise on un-ingested code paths.
    if file_paths and not guided_mode:
        try:
            inner = getattr(ctx.ledger, "_inner", ctx.ledger)
            _client = getattr(inner, "_client", None)
            if _client is not None:
                from ledger.queries import has_decisions_for_files

                has_any = await has_decisions_for_files(_client, file_paths)
                if not has_any:
                    if pid is not None:
                        write_preflight_event(
                            session_id=session_id,
                            preflight_id=pid,
                            topic=topic,
                            file_paths=file_paths,
                            fired=False,
                            surfaced_ids=[],
                            reason="no_relevant_decisions",
                        )
                    return PreflightResponse(
                        topic=topic,
                        fired=False,
                        reason="no_relevant_decisions",
                        guided_mode=guided_mode,
                        preflight_id=pid,
                    )
        except Exception as exc:
            logger.debug("[preflight] ledger-awareness fast-path failed: %s", exc)

    # V1 A3: time the call locally so the metric reflects THIS handler's catch-up.
    import time as _time

    from contracts import SyncMetrics
    from handlers.sync_middleware import ensure_ledger_synced

    _t0 = _time.perf_counter()
    await ensure_ledger_synced(ctx)
    sync_metrics = SyncMetrics(sync_catchup_ms=round((_time.perf_counter() - _t0) * 1000, 3))

    sources_chained: list[str] = []

    # Region-anchored lookup: caller-supplied file_paths → decisions pinned to those files.
    # High-precision direct pin — the caller LLM has scoped which files the task will touch.
    # Topic-based keyword search is intentionally removed; the skill reads bicameral.history()
    # directly and uses LLM reasoning to identify relevant feature groups.
    region_matches: list[DecisionMatch] = []
    if file_paths:
        try:
            (
                region_matches,
                used_graph_expansion,
                fallback_reason,
            ) = await _region_anchored_preflight(ctx, file_paths)
            if region_matches:
                sources_chained.append("region")
                if used_graph_expansion:
                    sources_chained.append("graph")
            # #243 — surface graph-expansion fallback as a loud signal,
            # additive to existing tags. Caller can render a recall-degraded
            # warning to the agent. Bare tag — granular reason flows through
            # the telemetry counter, not the response shape.
            if fallback_reason is not None:
                sources_chained.append("graph_unavailable")
        except Exception as exc:
            logger.debug("[preflight] region lookup failed: %s", exc)

    # #200 Phase 3: apply render_source_attribution policy server-side.
    # Default `redacted` strips name + date patterns from source_ref so
    # attribution detail doesn't leak into shared screens / pair sessions.
    # Mode read from `.bicameral/config.yaml: render_source_attribution`
    # at config load via context.py.
    region_matches = _apply_attribution_policy(
        region_matches, getattr(ctx, "render_source_attribution", "redacted")
    )

    decisions = [_to_brief_decision(m) for m in region_matches]
    drift_candidates = [_to_brief_decision(m) for m in region_matches if m.status == "drifted"]

    # HITL annotations — topic-independent ledger health checks that fire regardless of topic.
    unresolved_collisions: list[BriefDecision] = []
    context_pending_ready: list[BriefDecision] = []
    try:
        from ledger.queries import get_collision_pending_decisions, get_context_for_ready_decisions

        inner = getattr(ctx.ledger, "_inner", ctx.ledger)
        client = inner._client
        coll_rows = await get_collision_pending_decisions(client)
        for r in coll_rows:
            _sf = r.get("signoff") or {}
            unresolved_collisions.append(
                BriefDecision(
                    decision_id=r["decision_id"],
                    description=r["description"],
                    status=r.get("status") or "ungrounded",
                    signoff_state=(_sf.get("state") if isinstance(_sf, dict) else None),
                    signoff=r.get("signoff"),
                )
            )
        ctx_rows = await get_context_for_ready_decisions(client)
        for r in ctx_rows:
            _sf = r.get("signoff") or {}
            context_pending_ready.append(
                BriefDecision(
                    decision_id=r["decision_id"],
                    description=r["description"],
                    status=r.get("status") or "ungrounded",
                    signoff_state=(_sf.get("state") if isinstance(_sf, dict) else None),
                    signoff=r.get("signoff"),
                )
            )
    except Exception as exc:
        logger.debug("[preflight] HITL annotation queries failed: %s", exc)

    fired = bool(region_matches or unresolved_collisions or context_pending_ready or guided_mode)
    action_hints = generate_hints_from_findings([], drift_candidates, [], guided_mode)

    # #224 Phase C-pre: surface recent timeout counts so a Claude
    # PreToolUse / SessionStart hook can read current gate posture
    # without a separate MCP roundtrip. Defaults to {"read": 0, "drift": 0}
    # if the telemetry buffer is unavailable.
    try:
        from ledger.timeout_telemetry import recent_timeout_counts

        recent_timeouts = recent_timeout_counts()
    except Exception:
        recent_timeouts = {"read": 0, "drift": 0}

    response = PreflightResponse(
        topic=topic,
        fired=fired,
        reason="fired" if fired else "no_matches",  # type: ignore[arg-type]
        guided_mode=guided_mode,
        decisions=decisions,
        drift_candidates=drift_candidates,
        divergences=[],
        open_questions=[],
        action_hints=action_hints,
        sources_chained=sources_chained,
        unresolved_collisions=unresolved_collisions,
        context_pending_ready=context_pending_ready,
        sync_metrics=sync_metrics,
        product_stage=_PRODUCT_STAGE_MSG if _should_show_product_stage() else None,
        preflight_id=pid,
        recent_timeout_count=recent_timeouts,
    )

    # #65 — capture-loop event. surfaced_ids is the union of decision_ids the
    # response is steering the agent toward, used for triage joins.
    if pid is not None:
        surfaced_ids: list[str] = []
        for d in decisions:
            if d.decision_id:
                surfaced_ids.append(d.decision_id)
        for d in unresolved_collisions:
            if d.decision_id and d.decision_id not in surfaced_ids:
                surfaced_ids.append(d.decision_id)
        for d in context_pending_ready:
            if d.decision_id and d.decision_id not in surfaced_ids:
                surfaced_ids.append(d.decision_id)
        write_preflight_event(
            session_id=session_id,
            preflight_id=pid,
            topic=topic,
            file_paths=file_paths or [],
            fired=fired,
            surfaced_ids=surfaced_ids,
            reason=response.reason,
        )

    return response
