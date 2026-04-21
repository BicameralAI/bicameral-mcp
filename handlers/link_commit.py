"""Handler for /link_commit MCP tool.

Heartbeat of the ledger — syncs a commit's changes into the graph.
Idempotent: calling twice for the same commit is a no-op.
"""

from __future__ import annotations

import logging

from contracts import LinkCommitResponse, PendingComplianceCheck


_VERIFICATION_INSTRUCTION = (
    "Evaluate each pending_compliance_check — decide whether the code_body "
    "semantically implements the intent_description. Call "
    "bicameral.resolve_compliance with phase=<group phase> and a batch of "
    "verdicts: [{intent_id, region_id, content_hash, compliant, confidence, "
    "explanation}]. Group by phase if the batch mixes phases. One tool call "
    "resolves the whole batch."
)

logger = logging.getLogger(__name__)


async def _reground_ungrounded(ctx) -> int:
    """Attempt to ground any ungrounded intents now that the index may be ready.

    Returns the count of newly grounded intents.
    """
    try:
        ungrounded = await ctx.ledger.get_all_decisions(filter="ungrounded")
    except Exception as exc:
        logger.warning("[link_commit] could not query ungrounded intents: %s", exc)
        return 0

    if not ungrounded:
        return 0

    # Build synthetic mappings (no code_regions — that's what grounding will fill)
    mappings = [
        {
            "span": {
                "text": d["description"],
                "source_type": d.get("source_type", "manual"),
                "source_ref": d.get("source_ref", ""),
            },
            "intent": d["description"],
            "symbols": [],
            "code_regions": [],
        }
        for d in ungrounded
    ]

    resolved, deferred = ctx.code_graph.ground_mappings(mappings)
    if deferred:
        return 0

    newly_grounded = [m for m in resolved if m.get("code_regions")]
    if not newly_grounded:
        return 0

    payload = {
        "repo": ctx.repo_path,
        "commit_hash": "HEAD",
        "mappings": newly_grounded,
    }
    try:
        # Thread ctx through so the pollution guard in ingest_payload uses
        # the authoritative_sha instead of HEAD for baseline stamping.
        await ctx.ledger.ingest_payload(payload, ctx=ctx)
        logger.info(
            "[link_commit] lazy re-grounding: %d/%d ungrounded intents now grounded",
            len(newly_grounded), len(ungrounded),
        )
    except Exception as exc:
        logger.warning("[link_commit] lazy re-grounding ingest failed: %s", exc)
        return 0

    return len(newly_grounded)


def _read_current_head_sha(repo_path: str) -> str:
    """Re-read HEAD via ``git rev-parse HEAD`` — single subprocess,
    authoritative.

    ``ctx.head_sha`` is captured at ``BicameralContext.from_env()`` time
    and frozen, so it goes stale the instant any handler commits. The
    dedup guard can't trust it — a fresh ``git rev-parse`` is the only
    way to know whether a chained ``link_commit("HEAD")`` is asking to
    sync the same commit the cache was populated against, or a newer
    commit that must run a fresh sweep.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _sync_cache_lookup(ctx, commit_hash: str) -> LinkCommitResponse | None:
    """Return a cached ``LinkCommitResponse`` if this SHA was already
    synced within the current MCP call, else ``None``.

    v0.4.8: auto-chained tool calls (e.g. ``ingest → brief``) both fire
    ``handle_link_commit("HEAD")``. On the same unchanged HEAD that's
    wasted work — second sweep re-reads the same refs and updates
    nothing. This guard collapses the duplicate.

    The **full real response** from the first sync is cached, so dedup
    hits forward the same ``regions_updated`` / ``decisions_drifted`` /
    ``decisions_reflected`` numbers that downstream handlers like
    ``handle_search_decisions`` and ``handle_detect_drift`` surface in
    their ``sync_status`` field. The caller-facing ``reason`` is
    **normalized to ``"already_synced"``** on dedup hits so callers can
    distinguish "ran fresh" from "skipped as idempotent" — matching the
    ledger-level ``ingest_commit`` idempotency wording.

    Eligibility:

    - ``commit_hash == "HEAD"`` → re-reads HEAD via ``git rev-parse``
      to detect a moved HEAD. Skips dedup when the live SHA differs
      from the cached SHA (the repo advanced between calls, we must
      re-sync fresh).
    - Explicit SHA equal to ``last_sync_sha`` → always hits cache.
    - Any other explicit SHA → always runs (may be paging history).

    Cleared on any mutation via ``invalidate_sync_cache(ctx)``.
    """
    sync_state = getattr(ctx, "_sync_state", None)
    if not isinstance(sync_state, dict):
        return None
    cached_sha = sync_state.get("last_sync_sha")
    cached_response = sync_state.get("last_sync_response")
    if not cached_sha or not isinstance(cached_response, LinkCommitResponse):
        return None

    hit = False
    if commit_hash in ("HEAD", ""):
        live_head = _read_current_head_sha(getattr(ctx, "repo_path", "") or ".")
        if live_head and live_head == cached_sha:
            hit = True
    elif commit_hash == cached_sha:
        hit = True

    if not hit:
        return None

    # Normalize reason to match the ledger's own idempotency wording.
    # Every other field (regions_updated, decisions_drifted, etc.) stays
    # verbatim from the first-call response so downstream ``sync_status``
    # consumers see real numbers (B23).
    return cached_response.model_copy(update={"reason": "already_synced"})


def _store_sync_cache(ctx, commit_hash: str, response: LinkCommitResponse) -> None:
    """Record the real response from a successful sync so subsequent
    ``handle_link_commit`` calls in the same MCP call can short-circuit.

    For ``commit_hash == "HEAD"`` we persist the **live** ``git rev-parse
    HEAD`` result, not ``ctx.head_sha`` (which is captured at ctx
    creation time and can go stale mid-call).
    """
    sync_state = getattr(ctx, "_sync_state", None)
    if not isinstance(sync_state, dict):
        return
    if commit_hash in ("HEAD", ""):
        live_head = _read_current_head_sha(getattr(ctx, "repo_path", "") or ".")
        if not live_head:
            return
        sync_state["last_sync_sha"] = live_head
    else:
        sync_state["last_sync_sha"] = commit_hash
    sync_state["last_sync_response"] = response


def invalidate_sync_cache(ctx) -> None:
    """Clear the within-call sync cache. Call before any write that would
    invalidate a prior sync's view of repo state (ingest_payload, update,
    reset, or any flow that mutates the ledger). Callers must hold the
    invariant: writes clear cache → next read runs a fresh sync.
    """
    sync_state = getattr(ctx, "_sync_state", None)
    if isinstance(sync_state, dict):
        sync_state.pop("last_sync_sha", None)
        sync_state.pop("last_sync_response", None)


async def handle_link_commit(ctx, commit_hash: str = "HEAD") -> LinkCommitResponse:
    # v0.4.8: short-circuit if we've already synced this SHA within this
    # MCP call. Returns the FULL cached response from the first sync so
    # downstream consumers (search/drift's ``sync_status``) see real
    # region counts, not synthetic zeros.
    cached = _sync_cache_lookup(ctx, commit_hash)
    if cached is not None:
        logger.debug(
            "[link_commit] sync dedup: %s already synced in this call",
            commit_hash,
        )
        return cached

    # Self-heal legacy regions with empty content_hash from pre-v0.4.5
    # ingests. Scoped to ctx.repo_path so multi-repo SurrealDB instances
    # stay isolated; no-op once every region in this repo has a baseline.
    try:
        if hasattr(ctx.ledger, "backfill_empty_hashes"):
            await ctx.ledger.backfill_empty_hashes(
                ctx.repo_path, drift_analyzer=ctx.drift_analyzer,
            )
    except Exception as exc:
        logger.warning("[link_commit] backfill failed: %s", exc)

    # Pollution guard (v0.4.6, Bug 1): pass the authoritative branch name
    # through so ingest_commit can refuse baseline writes when the current
    # branch doesn't match. Branch-name comparison survives normal commits
    # that advance main (still "main") but catches feature-branch work.
    authoritative_ref = getattr(ctx, "authoritative_ref", "") or ""

    result = await ctx.ledger.ingest_commit(
        commit_hash,
        ctx.repo_path,
        drift_analyzer=ctx.drift_analyzer,
        authoritative_ref=authoritative_ref,
    )

    await _reground_ungrounded(ctx)

    pending_raw = result.get("pending_compliance_checks", []) or []
    pending = [PendingComplianceCheck(**p) for p in pending_raw]

    response = LinkCommitResponse(
        commit_hash=result["commit_hash"],
        synced=result["synced"],
        reason=result["reason"],
        regions_updated=result.get("regions_updated", 0),
        decisions_reflected=result.get("decisions_reflected", 0),
        decisions_drifted=result.get("decisions_drifted", 0),
        undocumented_symbols=result.get("undocumented_symbols", []),
        sweep_scope=result.get("sweep_scope", "head_only"),
        range_size=result.get("range_size", 0),
        pending_compliance_checks=pending,
        verification_instruction=_VERIFICATION_INSTRUCTION if pending else "",
    )
    _store_sync_cache(ctx, commit_hash, response)

    try:
        from dashboard.server import notify_dashboard
        await notify_dashboard(ctx)
    except Exception:
        pass

    return response
