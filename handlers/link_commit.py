"""Handler for /link_commit MCP tool.

Heartbeat of the ledger — syncs a commit's changes into the graph.
Idempotent: calling twice for the same commit is a no-op.

SAMPLING MIGRATION NOTE
-----------------------
The two-tool split (link_commit → caller reads code → resolve_compliance)
is a manual implementation of the MCP sampling primitive, which does not
yet have broad client support (Claude Code as of 2026-04 does not expose
sampling for third-party MCP servers).

Once client sampling support lands, the intended migration is:
  1. link_commit fires sampling/createMessage with pending_compliance_checks
     as the prompt, scoped read_file tools, and a structured verdict schema.
  2. The LLM sub-loop runs inside this tool call; verdicts come back inline.
  3. link_commit writes compliance rows itself and returns a fully resolved
     response — resolve_compliance becomes an internal helper, not a tool.

Until then, flow_id ties the two calls together for auditability:
  - link_commit writes flow_id into the response and sync cache.
  - resolve_compliance validates the flow_id matches the last sync.
  - resolve_compliance called without a matching flow_id logs a warning
    (stale or orphaned call).
"""

from __future__ import annotations

import logging
import subprocess
import uuid

from contracts import LinkCommitResponse, PendingComplianceCheck


def _is_ephemeral_commit(commit_hash: str, repo_path: str, authoritative_ref: str = "") -> bool:
    """Return True when the commit has not yet landed in the authoritative branch.

    Uses `git merge-base --is-ancestor` to check reachability. A commit on a
    feature branch (or any WIP commit not yet merged to main) is ephemeral;
    its compliance verdicts are excluded from drift scoring and status projection
    until it lands in the authoritative ref.

    Returns False (non-ephemeral) when authoritative_ref is unset or the check fails.
    """
    if not authoritative_ref or not commit_hash:
        return False
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit_hash, authoritative_ref],
            cwd=repo_path,
            capture_output=True,
            timeout=5,
        )
        return result.returncode != 0  # 0 = reachable from main = not ephemeral
    except Exception:
        return False


_VERIFICATION_INSTRUCTION_BASE = (
    "Evaluate each pending_compliance_check — decide whether the code_body "
    "semantically implements the intent_description. Call "
    "bicameral.resolve_compliance with phase=<group phase> and a batch of "
    "verdicts: [{intent_id, region_id, content_hash, compliant, confidence, "
    "explanation}]. Group by phase if the batch mixes phases. One tool call "
    "resolves the whole batch."
)

_GROUNDING_INSTRUCTION_UNGROUNDED = (
    " For pending_grounding_checks with reason='ungrounded': use your own "
    "code search (Grep/Read), then validate_symbols / extract_symbols to "
    "confirm the target, then call bicameral.bind with decision_id, "
    "file_path, symbol_name, and optionally start_line/end_line."
)

# V1 D1 / Codex pass-12 finding #2: relocation cases (symbol_disappeared)
# must NOT route to bicameral.bind. Bind on the new location would leave
# the old binding live and produce duplicate-binding state under the N:N
# binds_to relation. Atomic rebind (which retires the stale edge in the
# same write) ships in V2 (design doc §8 D2 — bicameral_rebind with
# old-binding CAS + fresh L3 verdict on the new target).
_GROUNDING_INSTRUCTION_RELOCATION = (
    " For pending_grounding_checks with reason='symbol_disappeared': "
    "INFORMATIONAL ONLY. The original_lines / file_path / symbol fields "
    "tell you where this decision USED to live; safe atomic rebind "
    "(which retires the stale edge in the same write) ships in V2. "
    "Do NOT call bicameral.bind on the new location — that would leave "
    "the old edge live and produce duplicate-binding state. Use git "
    "history (`git show <prev_ref>:<file_path>` over original_lines) "
    "to inform a future rebind, but do not bind directly."
)


def _build_verification_instruction(
    pending_compliance: list,
    pending_grounding: list[dict],
) -> str:
    """Compose the verification instruction conditional on which payloads
    actually fired. Splits ungrounded vs symbol_disappeared guidance so
    relocation cases never get an unsafe ``bicameral.bind`` CTA.
    """
    parts: list[str] = []
    if pending_compliance:
        parts.append(_VERIFICATION_INSTRUCTION_BASE)
    reasons = {c.get("reason") for c in pending_grounding}
    if "ungrounded" in reasons:
        parts.append(_GROUNDING_INSTRUCTION_UNGROUNDED)
    if "symbol_disappeared" in reasons:
        parts.append(_GROUNDING_INSTRUCTION_RELOCATION)
    return "".join(parts)

logger = logging.getLogger(__name__)


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
        sync_state.pop("pending_flow_id", None)


async def _run_continuity_pass(ctx, pending: list[PendingComplianceCheck]) -> list:
    """Phase 3 (#60): per-region continuity resolution. Returns the list
    of ``ContinuityResolution`` objects (empty when the flag is off, no
    drifted regions, or evaluation raises). Suppression of the
    PendingComplianceCheck list happens in the caller.
    """
    cg_config = getattr(ctx, "codegenome_config", None)
    cg_adapter = getattr(ctx, "codegenome", None)
    if cg_config is None or cg_adapter is None:
        return []
    if not (getattr(cg_config, "enabled", False) and getattr(cg_config, "enhance_drift", False)):
        return []
    if not pending:
        return []

    from codegenome.continuity_service import DriftContext, evaluate_continuity_for_drift

    resolutions: list = []
    for p in pending:
        # PR #73 review (CodeRabbit MAJOR handlers/link_commit.py:255):
        # the prior code seeded DriftContext with old_symbol_kind="unknown"
        # and 0,0 line numbers — permanently dropping the kind signal
        # from continuity scoring (20% of the weighted score) and
        # reporting ContinuityResolution.old_location as ":0-0". Load
        # the bound region's actual span + identity_type via the new
        # ledger.queries.get_region_metadata helper. Lookup failure
        # falls back to the previous "unknown"/0,0 behaviour so the
        # response shape is preserved when the region row is missing
        # (which would itself indicate a deeper inconsistency).
        meta = None
        try:
            if hasattr(ctx.ledger, "get_region_metadata"):
                meta = await ctx.ledger.get_region_metadata(p.region_id)
        except Exception as exc:
            logger.debug(
                "[link_commit] region metadata lookup failed for %s: %s",
                p.region_id, exc,
            )
        if meta:
            old_kind = str(meta.get("identity_type") or "unknown")
            old_start = int(meta.get("start_line") or 0)
            old_end = int(meta.get("end_line") or 0)
        else:
            old_kind, old_start, old_end = "unknown", 0, 0
        drift = DriftContext(
            decision_id=p.decision_id, region_id=p.region_id,
            old_file_path=p.file_path, old_symbol_name=p.symbol,
            old_symbol_kind=old_kind,
            old_start_line=old_start, old_end_line=old_end,
            repo_ref=getattr(ctx, "authoritative_sha", "") or "HEAD",
            repo_path=ctx.repo_path,
        )
        try:
            r = await evaluate_continuity_for_drift(
                ledger=ctx.ledger, codegenome=cg_adapter, code_locator=ctx.code_graph,
                drift=drift,
            )
        except Exception as exc:  # noqa: BLE001 — failure-isolated by design
            logger.warning("[link_commit] continuity eval failed for region %s: %s", p.region_id, exc)
            continue
        if r is not None:
            resolutions.append(r)
    return resolutions


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

    # CodeGenome hook (L2-only enrichment): when CodeGenome Phase 1 ships,
    # call resolve_subjects here for each newly-bound L2 decision to map it
    # to code symbols via get_neighbors. Gate with _resolve_subjects_eligible
    # from handlers.detect_drift — L1 decisions must never enter the identity graph.
    result = await ctx.ledger.ingest_commit(
        commit_hash,
        ctx.repo_path,
        drift_analyzer=ctx.drift_analyzer,
        authoritative_ref=authoritative_ref,
    )

    pending_raw = result.get("pending_compliance_checks", []) or []
    pending = [PendingComplianceCheck(**p) for p in pending_raw]

    # Phase 3 (#60): when codegenome.enhance_drift is enabled, attempt
    # continuity resolution for each drifted region BEFORE the caller
    # sees the PendingComplianceCheck. Auto-resolved regions are removed
    # from `pending`. Failure-isolated: any exception falls through to
    # the existing PendingComplianceCheck flow with the response shape
    # intact.
    continuity_resolutions = await _run_continuity_pass(ctx, pending)
    if continuity_resolutions:
        resolved_region_ids = {
            r.old_code_region_id for r in continuity_resolutions
            if r.semantic_status in ("identity_moved", "identity_renamed")
        }
        if resolved_region_ids:
            pending = [p for p in pending if p.region_id not in resolved_region_ids]

    pending_grounding_raw = result.get("pending_grounding_checks", []) or []

    has_action_items = bool(pending) or bool(pending_grounding_raw)
    verification_text = (
        _build_verification_instruction(pending, pending_grounding_raw)
        if has_action_items
        else ""
    )

    is_ephemeral = _is_ephemeral_commit(
        result["commit_hash"],
        ctx.repo_path,
        authoritative_ref=authoritative_ref,
    )

    flow_id = str(uuid.uuid4())
    sync_state = getattr(ctx, "_sync_state", None)
    if isinstance(sync_state, dict):
        sync_state["pending_flow_id"] = flow_id
        sync_state["pending_ephemeral"] = is_ephemeral

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
        pending_grounding_checks=pending_grounding_raw,
        verification_instruction=verification_text,
        flow_id=flow_id,
        ephemeral=is_ephemeral,
        continuity_resolutions=continuity_resolutions,
    )
    _store_sync_cache(ctx, commit_hash, response)

    try:
        from dashboard.server import notify_dashboard
        await notify_dashboard(ctx)
    except Exception:
        pass

    return response
