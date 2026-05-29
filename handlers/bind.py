"""Handler for bicameral.bind — caller-LLM-driven code region binding."""

from __future__ import annotations

import logging

from contracts import BindResponse, BindResult, PendingComplianceCheck, SyncMetrics
from handlers.link_commit import _is_ephemeral_commit
from handlers.sync_middleware import repo_write_barrier
from preflight_telemetry import telemetry_enabled, write_engagement
from protocol.categorization import grounding_lookup

logger = logging.getLogger(__name__)


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """True when line spans [a_start, a_end] and [b_start, b_end] share any line.

    Inclusive on both ends. Used by #280 Branch B to confirm a caller-supplied
    line range overlaps the tree-sitter-resolved span for the named symbol —
    rejecting hallucinated line ranges without forcing exact-equality, which
    would block legitimate sub-region binds.
    """
    return a_start <= b_end and b_start <= a_end


def _emit_m2_attempt(
    *,
    decision_id: str,
    decision_source: str | None,
    success: bool,
    handler_rejected: bool,
) -> None:
    """Fire-and-forget M2 grounding-attempt event (#280 PR-3).

    Wraps ``m2_grounding_log.record_attempt`` in try/except so a telemetry
    failure never breaks bind. Skip the call entirely when ``decision_id``
    is empty (API misuse) or unknown (handled elsewhere — those aren't
    representative grounding attempts and would skew the precision metric).
    """
    if not decision_id:
        return
    try:
        from m2_grounding_log import record_attempt

        record_attempt(
            decision_id=decision_id,
            decision_source=decision_source,
            success=success,
            handler_rejected=handler_rejected,
        )
    except Exception as exc:
        logger.debug("[bind] m2 telemetry emit failed (non-fatal): %s", exc)


@grounding_lookup("grounding.lookup.bind")
async def handle_bind(
    ctx,
    bindings: list[dict],
    *,
    preflight_id: str | None = None,
) -> BindResponse:
    """Create decision→code_region bindings from caller-LLM-supplied locations.

    Each binding is a dict with these fields:

      - ``decision_id`` (required) — target decision row.
      - ``file_path`` (required) — repo-relative path.
      - ``symbol_name`` (required) — symbol to bind to.
      - ``start_line`` / ``end_line`` (optional) — span hint; tree-sitter
        resolves on miss.
      - ``purpose`` (optional) — human-readable label.
      - ``expected_indexed_at_sha`` (optional, #334 Shape 2) — the SHA the
        caller's ``validate_symbols`` was indexed at (returned as
        ``indexed_at_sha`` on each ``ValidatedSymbol``). When supplied, the
        handler rejects the binding with ``snapshot_mismatch`` if it differs
        from the ref bind is about to resolve at. Threading this field
        upgrades the validate→bind handshake from a doc-only convention to a
        server-enforced contract. Omit to preserve pre-Shape-2 behavior.

    For each binding:
      1. Verify decision exists (return error if not).
      2. Snapshot handshake — if ``expected_indexed_at_sha`` is supplied and
         disagrees with bind's effective ref, reject with
         ``snapshot_mismatch`` (#334 Shape 2).
      3. Use start_line/end_line if supplied; else resolve via tree-sitter.
         Error if symbol not found.
      4. Compute content_hash against authoritative_sha.
      5. Upsert code_region + binds_to edge, transition decision ungrounded→pending.
      6. Return PendingComplianceCheck for immediate caller verification.

    V1 A2-light: the whole handler body runs under ``repo_write_barrier``
    so two concurrent bind calls against the same repo are serialized.
    Does NOT protect against concurrent resolve_compliance / cross-process
    writers — those are V2 scope.

    V1 A3: the barrier's hold duration is attached to the response as
    ``sync_metrics.barrier_held_ms``.
    """
    async with repo_write_barrier(ctx) as timing:
        response = await _do_bind(ctx, bindings)
    response.sync_metrics = SyncMetrics(barrier_held_ms=timing.held_ms)
    response.preflight_id = preflight_id

    if telemetry_enabled():
        # One row per bind call (not per binding) — the call is the unit of
        # engagement. decision_id is the first binding's id when present;
        # file_paths is the union of file paths across the call.
        first_decision = (str(bindings[0].get("decision_id") or "") if bindings else None) or None
        file_paths = [str(b.get("file_path") or "") for b in (bindings or []) if b.get("file_path")]
        write_engagement(
            session_id=str(getattr(ctx, "session_id", "unknown") or "unknown"),
            tool="bicameral.bind",
            decision_id=first_decision,
            preflight_id=preflight_id,
            file_paths=file_paths or None,
        )

    return response


async def _do_bind(ctx, bindings: list[dict]) -> BindResponse:
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    repo = ctx.repo_path
    authoritative_sha = getattr(ctx, "authoritative_sha", "") or "HEAD"

    # #332 ephemeral-aware ref: when the current HEAD has not yet landed on
    # the authoritative branch, resolve symbols and compute content_hash
    # against head_sha (the branch tip) instead of authoritative_sha (main
    # tip). This prevents bind from rejecting branch-local files/symbols
    # and ensures content_hash matches what link_commit's drift sweep sees.
    head_sha = getattr(ctx, "head_sha", "") or ""
    authoritative_ref = getattr(ctx, "authoritative_ref", "") or ""
    effective_ref = authoritative_sha
    if head_sha and _is_ephemeral_commit(head_sha, repo, authoritative_ref=authoritative_ref):
        effective_ref = head_sha

    results: list[BindResult] = []

    for b in bindings:
        decision_id = str(b.get("decision_id") or "")
        file_path = str(b.get("file_path") or "")
        symbol_name = str(b.get("symbol_name") or "")
        start_line = b.get("start_line")
        end_line = b.get("end_line")
        purpose = str(b.get("purpose") or "")
        expected_indexed_at_sha = str(b.get("expected_indexed_at_sha") or "")

        if not decision_id or not file_path or not symbol_name:
            results.append(
                BindResult(
                    decision_id=decision_id,
                    region_id="",
                    content_hash="",
                    error="decision_id, file_path, and symbol_name are required",
                )
            )
            continue

        # #334 Shape 2 — snapshot handshake. When the caller threads through
        # the ``indexed_at_sha`` they got from ``validate_symbols``, refuse to
        # write if it disagrees with the ref bind is about to resolve at.
        # Empty / missing field falls back to the pre-Shape-2 behavior (no
        # enforcement) so existing callers don't regress. Cheap rejection —
        # placed before decision lookup so a stale snapshot doesn't burn DB
        # round-trips.
        if expected_indexed_at_sha and expected_indexed_at_sha != effective_ref:
            results.append(
                BindResult(
                    decision_id=decision_id,
                    region_id="",
                    content_hash="",
                    error=(
                        f"snapshot_mismatch: validate_symbols indexed at "
                        f"{expected_indexed_at_sha} but bind resolves at "
                        f"{effective_ref} — re-run validate_symbols against "
                        f"the current snapshot and retry"
                    ),
                )
            )
            continue

        try:
            exists = await ledger.decision_exists(decision_id)
        except Exception as exc:
            results.append(
                BindResult(
                    decision_id=decision_id,
                    region_id="",
                    content_hash="",
                    error=f"decision lookup failed: {exc}",
                )
            )
            continue

        if not exists:
            results.append(
                BindResult(
                    decision_id=decision_id,
                    region_id="",
                    content_hash="",
                    error=f"unknown_decision_id: {decision_id}",
                )
            )
            continue

        # #280 PR-3 — resolve decision_source once for telemetry. Cheap query
        # (single field SELECT). Best-effort; on lookup failure we still bind
        # but log "unknown" as the source.
        try:
            decision_source = await ledger.get_decision_source(decision_id)
        except Exception:
            decision_source = None

        if start_line is None or end_line is None:
            from ledger.status import resolve_symbol_lines

            resolved = resolve_symbol_lines(file_path, symbol_name, repo, ref=effective_ref)
            if resolved is None:
                results.append(
                    BindResult(
                        decision_id=decision_id,
                        region_id="",
                        content_hash="",
                        error=f"symbol '{symbol_name}' not found in {file_path} at {effective_ref}",
                    )
                )
                _emit_m2_attempt(
                    decision_id=decision_id,
                    decision_source=decision_source,
                    success=False,
                    handler_rejected=True,
                )
                continue
            start_line, end_line = resolved
        else:
            start_line, end_line = int(start_line), int(end_line)
            from ledger.status import get_git_content, resolve_symbol_lines

            if get_git_content(file_path, 1, 1, repo, ref=effective_ref) is None:
                results.append(
                    BindResult(
                        decision_id=decision_id,
                        region_id="",
                        content_hash="",
                        error=f"file '{file_path}' does not exist at {effective_ref} — only bind to existing code, never hypothetical files",
                    )
                )
                _emit_m2_attempt(
                    decision_id=decision_id,
                    decision_source=decision_source,
                    success=False,
                    handler_rejected=True,
                )
                continue

            # #280 — caller-supplied line range cannot bypass symbol
            # verification. Branch A (no lines) already runs tree-sitter via
            # resolve_symbol_lines and rejects on miss; Branch B (with lines)
            # used to skip that check, accepting any symbol_name as long as
            # the file existed. That was the silent-acceptance surface for
            # M2 grounding precision regressions.
            resolved = resolve_symbol_lines(file_path, symbol_name, repo, ref=effective_ref)
            if resolved is None:
                results.append(
                    BindResult(
                        decision_id=decision_id,
                        region_id="",
                        content_hash="",
                        error=f"symbol '{symbol_name}' not found in {file_path} at {effective_ref} — caller-supplied line range cannot bypass symbol verification (#280)",
                    )
                )
                _emit_m2_attempt(
                    decision_id=decision_id,
                    decision_source=decision_source,
                    success=False,
                    handler_rejected=True,
                )
                continue
            resolved_start, resolved_end = resolved
            if not _spans_overlap(start_line, end_line, resolved_start, resolved_end):
                results.append(
                    BindResult(
                        decision_id=decision_id,
                        region_id="",
                        content_hash="",
                        error=f"symbol '{symbol_name}' resolves at lines {resolved_start}-{resolved_end} but caller supplied {start_line}-{end_line} — span mismatch (#280)",
                    )
                )
                _emit_m2_attempt(
                    decision_id=decision_id,
                    decision_source=decision_source,
                    success=False,
                    handler_rejected=True,
                )
                continue

        try:
            bind_result = await ledger.bind_decision(
                decision_id=decision_id,
                file_path=file_path,
                symbol_name=symbol_name,
                start_line=start_line,
                end_line=end_line,
                repo=repo,
                ref=effective_ref,
                purpose=purpose,
            )
        except Exception as exc:
            logger.warning("[bind] bind_decision failed: %s", exc)
            results.append(
                BindResult(
                    decision_id=decision_id,
                    region_id="",
                    content_hash="",
                    error=str(exc),
                )
            )
            _emit_m2_attempt(
                decision_id=decision_id,
                decision_source=decision_source,
                success=False,
                handler_rejected=False,  # ledger error, not a #280 reject
            )
            continue

        region_id = bind_result["region_id"]
        content_hash = bind_result["content_hash"]

        # CodeGenome identity write (#59) — side-effect only, off by
        # default. Failure here must not change the bind response
        # contract; caller behavior is identical whether the flag is on
        # or off.
        #
        # L1 exemption (Jin's spec-governance proposal §4.2): only
        # decisions explicitly tagged ``"L2"`` enter the codegenome
        # identity graph. ``"L1"`` decisions are intentionally
        # ungrounded at the identity layer (PMs evaluate them via
        # claims/evidence, not code regions). ``"L3"`` is never
        # tracked. ``None`` (unclassified) is treated as L3 by the
        # tolerant policy — the row is safe by default; classification
        # can be added later without re-binding.
        cg_config = getattr(ctx, "codegenome_config", None)
        cg_adapter = getattr(ctx, "codegenome", None)
        if (
            cg_config is not None
            and cg_adapter is not None
            and getattr(cg_config, "identity_writes_active", lambda: False)()
        ):
            try:
                level = await ledger.get_decision_level(decision_id)
            except Exception as exc:
                logger.warning(
                    "[bind] decision_level lookup failed for %s: %s — skipping codegenome write",
                    decision_id,
                    exc,
                )
                level = None  # treat lookup failure as "skip" — safer than over-writing
            if level == "L2":
                from codegenome.bind_service import write_codegenome_identity

                try:
                    await write_codegenome_identity(
                        ledger=ledger,
                        codegenome=cg_adapter,
                        decision_id=decision_id,
                        file_path=file_path,
                        symbol_name=symbol_name,
                        symbol_kind="unknown",
                        start_line=int(start_line),
                        end_line=int(end_line),
                        repo_ref=effective_ref,
                        code_region_content_hash=content_hash,
                        code_locator=getattr(ctx, "code_graph", None),
                        region_id=region_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[bind] codegenome identity write failed for %s: %s",
                        decision_id,
                        exc,
                    )
            else:
                logger.debug(
                    "[bind] L1 exemption — skipping codegenome write for %s (decision_level=%r)",
                    decision_id,
                    level,
                )

        pending_check = None
        if content_hash:
            try:
                desc = await ledger.get_decision_description(decision_id)
            except Exception:
                desc = ""
            pending_check = PendingComplianceCheck(
                phase="ingest",
                decision_id=decision_id,
                region_id=region_id,
                decision_description=desc,
                file_path=file_path,
                symbol=symbol_name,
                content_hash=content_hash,
            )

        results.append(
            BindResult(
                decision_id=decision_id,
                region_id=region_id,
                content_hash=content_hash,
                pending_compliance_check=pending_check,
            )
        )
        _emit_m2_attempt(
            decision_id=decision_id,
            decision_source=decision_source,
            success=True,
            handler_rejected=False,
        )

    try:
        from dashboard.server import notify_dashboard

        await notify_dashboard(ctx)
    except Exception:
        pass

    return BindResponse(bindings=results, bind_effective_ref=effective_ref)
