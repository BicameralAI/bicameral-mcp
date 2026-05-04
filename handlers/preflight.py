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
from pathlib import Path

from contracts import (
    BriefDecision,
    CodeRegionSummary,
    DecisionMatch,
    PreflightResponse,
)
from governance import config as governance_config
from governance import engine as governance_engine
from governance.contracts import (
    GovernanceFinding,
    HITLPrompt,
    HITLPromptOption,
    derive_governance_metadata,
)
from governance.finding_factories import consolidate, from_preflight_drift_candidate
from handlers.action_hints import generate_hints_from_findings
from handlers.analysis import _to_brief_decision
from preflight_telemetry import (
    new_preflight_id,
    recent_bypass_seconds,
    telemetry_enabled,
    write_preflight_event,
)

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


async def _region_anchored_preflight(
    ctx,
    file_paths: list[str],
) -> tuple[list[DecisionMatch], bool]:
    """file_paths (caller-supplied) → decisions pinned to those regions.

    The caller LLM is responsible for resolving which files a proposed change
    will touch — preflight then looks up decisions pinned to those files in
    the ledger. Before the lookup, run a 1-hop code-graph expansion via the
    code-locator adapter (#173): caller-LLM discovery is imprecise, and a
    decision bound to ``app/src/lib/git/reorder.ts`` should still surface
    when the caller passes the structurally-near ``app/src/ui/multi-commit-
    operation/reorder.tsx``. Expansion is deterministic, no LLM in the path,
    bounded by ``code_locator/config.py::max_neighbors_per_result``.

    Returns ``(matches, expanded)`` where ``expanded`` is True iff the graph
    expansion produced extra paths beyond the caller-supplied set, so the
    caller can record ``"graph"`` in ``sources_chained``. Direct-pin matches
    carry ``confidence=0.9``; matches surfaced only via expanded paths carry
    ``confidence=0.7``.
    """
    if not file_paths:
        return [], False

    # Dedup + normalize while preserving caller-supplied order.
    seen_paths: set[str] = set()
    ordered: list[str] = []
    for fp in file_paths:
        fp = (fp or "").strip()
        if fp and fp not in seen_paths:
            seen_paths.add(fp)
            ordered.append(fp)
    if not ordered:
        return [], False

    # Graph expansion. Defensive: code_graph may be absent (mock contexts) or
    # the adapter may not implement the method (older deployments). Either
    # case falls back to direct file_paths only.
    direct_paths: set[str] = set(ordered)
    expanded_paths = list(ordered)
    expanded_only_paths: set[str] = set()
    code_graph = getattr(ctx, "code_graph", None)
    expander = getattr(code_graph, "expand_file_paths_via_graph", None) if code_graph else None
    if expander is not None:
        try:
            expanded_paths, added_paths = expander(ordered, hops=1)
            expanded_only_paths = set(added_paths)
        except Exception as exc:
            logger.debug("[preflight:region] graph expansion failed: %s", exc)
            expanded_paths = list(ordered)
            expanded_only_paths = set()

    try:
        raw = await ctx.ledger.get_decisions_for_files(expanded_paths)
    except Exception as exc:
        logger.debug("[preflight:region] ledger region lookup failed: %s", exc)
        return [], False

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

    return matches, surfaced_via_expansion


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

    # Per-session dedup — same topic within 5 min is silenced.
    if _check_dedup(ctx, topic):
        logger.debug("[preflight] dedup hit for topic: %r", topic[:60])
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
            region_matches, used_graph_expansion = await _region_anchored_preflight(ctx, file_paths)
            if region_matches:
                sources_chained.append("region")
                if used_graph_expansion:
                    sources_chained.append("graph")
        except Exception as exc:
            logger.debug("[preflight] region lookup failed: %s", exc)

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

    # #108-#110 — governance finding (Phase 3). Build a finding per
    # drifted region candidate, run the engine, consolidate per
    # (decision_id, region_id), and attach the highest-severity
    # consolidated finding to the response. #112 (Phase 4) plumbs
    # bypass-recency from preflight_telemetry.recent_bypass_seconds
    # so recently-bypassed decisions render one tier softer.
    governance_finding: GovernanceFinding | None = None
    try:
        governance_finding = await _build_governance_finding(ctx, drift_candidates)
    except Exception as exc:
        logger.debug("[preflight] governance finding build failed: %s", exc)

    # #112 — HITL clarification prompts. Iterate every decision that
    # surfaced (region matches + collision/context-pending HITL rows)
    # and emit a prompt for any unresolved signoff state. Bypass option
    # is mandatory and last in every prompt's option list.
    hitl_prompts = _build_hitl_prompts(
        region_matches,
        unresolved_collisions,
        context_pending_ready,
    )

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
        governance_finding=governance_finding,
        hitl_prompts=hitl_prompts,
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


async def _build_governance_finding(
    ctx,
    drift_candidates: list[BriefDecision],
) -> GovernanceFinding | None:
    """Build a consolidated governance finding for preflight drift
    candidates. Returns the highest-severity consolidated finding
    (with policy_result attached) or None if there are no candidates.

    #112 (Phase 4): bypass-recency is read at the call site via
    ``preflight_telemetry.recent_bypass_seconds`` and passed as a
    scalar into the engine. Engine purity is preserved -- IO happens
    here, not in ``evaluate()``. When telemetry is disabled the
    recency lookup is skipped entirely.
    """
    if not drift_candidates:
        return None

    inner = getattr(ctx.ledger, "_inner", ctx.ledger)
    client = getattr(inner, "_client", None)
    if client is None:
        return None

    cfg = governance_config.load_config()

    findings: list[GovernanceFinding] = []
    for candidate in drift_candidates:
        decision_level: str | None = None
        signoff_state: str | None = None
        decision_status_pipeline: str | None = None
        governance_raw: dict | None = None
        try:
            rows = await client.query(
                f"SELECT decision_level, signoff, status, governance "
                f"FROM {candidate.decision_id} LIMIT 1"
            )
            if rows:
                row = rows[0]
                decision_level = row.get("decision_level") or None
                sf = row.get("signoff") or {}
                if isinstance(sf, dict):
                    signoff_state = sf.get("state")
                decision_status_pipeline = row.get("status")
                gov = row.get("governance")
                if isinstance(gov, dict) and gov:
                    governance_raw = gov
        except Exception as exc:
            logger.debug(
                "[preflight] decision lookup for governance failed (%s): %s",
                candidate.decision_id,
                exc,
            )

        explicit = None
        if governance_raw:
            try:
                from governance.contracts import GovernanceMetadata

                explicit = GovernanceMetadata.model_validate(governance_raw)
            except Exception:
                explicit = None
        metadata = derive_governance_metadata(decision_level, explicit)

        # Determine the engine's DecisionStatus from the raw row signals.
        if signoff_state in (
            "ratified",
            "proposed",
            "rejected",
            "superseded",
            "collision_pending",
            "context_pending",
        ):
            decision_status = signoff_state
        elif decision_status_pipeline == "ungrounded":
            decision_status = "ungrounded"
        else:
            decision_status = "active"

        finding = from_preflight_drift_candidate(candidate, metadata)
        # #112 — Phase 4 wiring. Read bypass recency from the JSONL
        # log; engine drops one escalation tier when within window.
        recency: int | None = None
        if telemetry_enabled():
            try:
                recency = recent_bypass_seconds(candidate.decision_id)
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug(
                    "[preflight] recent_bypass_seconds(%s) failed: %s",
                    candidate.decision_id,
                    exc,
                )
                recency = None
        policy = governance_engine.evaluate(
            finding=finding,
            metadata=metadata,
            config=cfg,
            decision_status=decision_status,  # type: ignore[arg-type]
            bypass_recency_seconds=recency,
        )
        findings.append(finding.model_copy(update={"policy_result": policy}))

    if not findings:
        return None

    consolidated = consolidate(findings)
    if not consolidated:
        return None
    # Sort by action ladder severity so the response surfaces the
    # strongest signal. Stable on ties.
    ladder = governance_engine._ACTION_LADDER

    def _severity_key(f: GovernanceFinding) -> int:
        if f.policy_result is None:
            return -1
        try:
            return ladder.index(f.policy_result.action)
        except ValueError:
            return -1

    consolidated.sort(key=_severity_key, reverse=True)
    return consolidated[0]


# ── #112 — HITL clarification prompts ────────────────────────────────

# Bypass option is mandatory and last in every prompt. The skill side
# asserts ``options[-1].kind == "bypass"`` -- breaking this contract
# breaks the surface.
_BYPASS_OPTION = HITLPromptOption(
    kind="bypass",
    label="Bypass — proceed without resolving (recorded)",
)

# Trigger states that yield a HITL prompt. Mirrors the
# ``HITLPrompt.trigger`` literal in ``governance/contracts.py``. Any
# decision whose ``signoff_state`` is in this set surfaces a prompt.
_HITL_TRIGGER_STATES: frozenset[str] = frozenset(
    {
        "proposed",
        "ai_surfaced",
        "needs_context",
        "collision_pending",
        "context_pending",
    }
)


def _hitl_options_for(trigger: str) -> list[HITLPromptOption]:
    """Return the option set for a given trigger.

    Three shapes per the plan:
      - generic: ratify / reject / needs_context / defer / bypass
      - collision_pending: supersedes_a_b / supersedes_b_a /
        keep_parallel / defer / bypass
      - ai_surfaced: confirm_proposed / ratify_now / reject /
        needs_context / bypass

    Bypass is ALWAYS last.
    """
    if trigger == "collision_pending":
        return [
            HITLPromptOption(kind="supersedes_a_b", label="A supersedes B"),
            HITLPromptOption(kind="supersedes_b_a", label="B supersedes A"),
            HITLPromptOption(kind="keep_parallel", label="Keep both in parallel"),
            HITLPromptOption(kind="defer", label="Defer — decide later"),
            _BYPASS_OPTION,
        ]
    if trigger == "ai_surfaced":
        return [
            HITLPromptOption(kind="confirm_proposed", label="Confirm as proposed"),
            HITLPromptOption(kind="ratify_now", label="Ratify now"),
            HITLPromptOption(kind="reject", label="Reject — not a real decision"),
            HITLPromptOption(kind="needs_context", label="Needs more context"),
            _BYPASS_OPTION,
        ]
    # Generic: proposed, needs_context, context_pending.
    return [
        HITLPromptOption(kind="ratify", label="Ratify"),
        HITLPromptOption(kind="reject", label="Reject"),
        HITLPromptOption(kind="needs_context", label="Needs more context"),
        HITLPromptOption(kind="defer", label="Defer — decide later"),
        _BYPASS_OPTION,
    ]


def _hitl_question_for(trigger: str, description: str) -> str:
    """Compose a one-line clarification question for the prompt."""
    snippet = (description or "").strip()
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    if trigger == "collision_pending":
        return f"Two decisions appear to conflict — which path applies? ({snippet})"
    if trigger == "ai_surfaced":
        return f"AI surfaced this as a possible decision — confirm? ({snippet})"
    if trigger == "needs_context":
        return f"This decision needs more context — what's missing? ({snippet})"
    if trigger == "context_pending":
        return f"Awaiting context to ground this decision — provide one? ({snippet})"
    return f"This decision is unresolved — confirm or revise? ({snippet})"


def _prompt_from(decision_id: str, description: str, trigger: str) -> HITLPrompt:
    """Build a HITLPrompt for a single (decision_id, signoff_state)."""
    return HITLPrompt(
        decision_id=decision_id,
        trigger=trigger,  # type: ignore[arg-type]
        question=_hitl_question_for(trigger, description),
        options=_hitl_options_for(trigger),
    )


def _build_hitl_prompts(
    region_matches: list[DecisionMatch],
    unresolved_collisions: list[BriefDecision],
    context_pending_ready: list[BriefDecision],
) -> list[HITLPrompt]:
    """Scan all surfaced decisions and emit one HITLPrompt per
    unresolved signoff_state. De-duped by decision_id.

    Triggers come from ``signoff_state`` directly when it is one of
    the configured trigger states; ``unresolved_collisions`` rows
    always emit a ``collision_pending`` prompt and
    ``context_pending_ready`` rows always emit a ``context_pending``
    prompt -- those queries explicitly target those states.
    """
    prompts: list[HITLPrompt] = []
    seen: set[str] = set()

    def _add(decision_id: str, description: str, trigger: str) -> None:
        if not decision_id or decision_id in seen:
            return
        if trigger not in _HITL_TRIGGER_STATES:
            return
        prompts.append(_prompt_from(decision_id, description, trigger))
        seen.add(decision_id)

    for m in region_matches:
        state = (m.signoff_state or "").strip()
        if state in _HITL_TRIGGER_STATES:
            _add(m.decision_id, m.description, state)

    for d in unresolved_collisions:
        _add(d.decision_id, d.description, "collision_pending")

    for d in context_pending_ready:
        _add(d.decision_id, d.description, "context_pending")

    return prompts
