"""Handler for bicameral.bind — caller-LLM-driven code region binding."""

from __future__ import annotations

import logging

from contracts import BindResponse, BindResult, PendingComplianceCheck, SyncMetrics
from handlers.sync_middleware import repo_write_barrier

logger = logging.getLogger(__name__)


async def handle_bind(ctx, bindings: list[dict]) -> BindResponse:
    """Create decision→code_region bindings from caller-LLM-supplied locations.

    For each binding:
      1. Verify decision exists (return error if not).
      2. Use start_line/end_line if supplied; else resolve via tree-sitter.
         Error if symbol not found.
      3. Compute content_hash against authoritative_sha.
      4. Upsert code_region + binds_to edge, transition decision ungrounded→pending.
      5. Return PendingComplianceCheck for immediate caller verification.

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
    return response


async def _do_bind(ctx, bindings: list[dict]) -> BindResponse:
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    repo = ctx.repo_path
    authoritative_sha = getattr(ctx, "authoritative_sha", "") or "HEAD"

    results: list[BindResult] = []

    for b in bindings:
        decision_id = str(b.get("decision_id") or "")
        file_path = str(b.get("file_path") or "")
        symbol_name = str(b.get("symbol_name") or "")
        start_line = b.get("start_line")
        end_line = b.get("end_line")
        purpose = str(b.get("purpose") or "")

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

        if start_line is None or end_line is None:
            from ledger.status import resolve_symbol_lines

            resolved = resolve_symbol_lines(file_path, symbol_name, repo, ref=authoritative_sha)
            if resolved is None:
                results.append(
                    BindResult(
                        decision_id=decision_id,
                        region_id="",
                        content_hash="",
                        error=f"symbol '{symbol_name}' not found in {file_path} at {authoritative_sha}",
                    )
                )
                continue
            start_line, end_line = resolved
        else:
            start_line, end_line = int(start_line), int(end_line)
            from ledger.status import get_git_content

            if get_git_content(file_path, 1, 1, repo, ref=authoritative_sha) is None:
                results.append(
                    BindResult(
                        decision_id=decision_id,
                        region_id="",
                        content_hash="",
                        error=f"file '{file_path}' does not exist at {authoritative_sha} — only bind to existing code, never hypothetical files",
                    )
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
                ref=authoritative_sha,
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
                        repo_ref=authoritative_sha,
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

    try:
        from dashboard.server import notify_dashboard

        await notify_dashboard(ctx)
    except Exception:
        pass

    return BindResponse(bindings=results)
