"""Handler for bicameral.bind — caller-LLM-driven code region binding."""

from __future__ import annotations
import logging
from contracts import BindResponse, BindResult, PendingComplianceCheck

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
    """
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
            results.append(BindResult(
                decision_id=decision_id, region_id="", content_hash="",
                error="decision_id, file_path, and symbol_name are required",
            ))
            continue

        try:
            exists = await ledger.decision_exists(decision_id)
        except Exception as exc:
            results.append(BindResult(
                decision_id=decision_id, region_id="", content_hash="",
                error=f"decision lookup failed: {exc}",
            ))
            continue

        if not exists:
            results.append(BindResult(
                decision_id=decision_id, region_id="", content_hash="",
                error=f"unknown_decision_id: {decision_id}",
            ))
            continue

        if start_line is None or end_line is None:
            from ledger.status import resolve_symbol_lines
            resolved = resolve_symbol_lines(file_path, symbol_name, repo, ref=authoritative_sha)
            if resolved is None:
                results.append(BindResult(
                    decision_id=decision_id, region_id="", content_hash="",
                    error=f"symbol '{symbol_name}' not found in {file_path} at {authoritative_sha}",
                ))
                continue
            start_line, end_line = resolved
        else:
            start_line, end_line = int(start_line), int(end_line)

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
            results.append(BindResult(
                decision_id=decision_id, region_id="", content_hash="",
                error=str(exc),
            ))
            continue

        region_id = bind_result["region_id"]
        content_hash = bind_result["content_hash"]

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

        results.append(BindResult(
            decision_id=decision_id,
            region_id=region_id,
            content_hash=content_hash,
            pending_compliance_check=pending_check,
        ))

    try:
        from dashboard.server import notify_dashboard
        await notify_dashboard(ctx)
    except Exception:
        pass

    return BindResponse(bindings=results)
