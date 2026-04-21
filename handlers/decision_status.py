"""Handler for /decision_status MCP tool.

Surfaces implementation status of all tracked decisions.
Auto-syncs the ledger to HEAD before returning status.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from contracts import CodeRegionSummary, DecisionStatusEntry, DecisionStatusResponse

logger = logging.getLogger(__name__)


async def handle_decision_status(
    ctx,
    filter: str = "all",
    since: str | None = None,
    ref: str = "HEAD",
) -> DecisionStatusResponse:
    # Auto-sync to HEAD so status reflects current code state
    try:
        from handlers.link_commit import handle_link_commit
        await handle_link_commit(ctx, ref)
    except Exception as exc:
        logger.warning("[status] auto-sync failed: %s", exc)

    decisions_raw = await ctx.ledger.get_all_decisions(filter=filter)

    entries: list[DecisionStatusEntry] = []
    summary: dict[str, int] = {"reflected": 0, "drifted": 0, "pending": 0, "ungrounded": 0}

    for d in decisions_raw:
        if since and d.get("ingested_at", "") < since:
            continue

        status = d.get("status", "ungrounded")
        summary[status] = summary.get(status, 0) + 1

        regions = [
            CodeRegionSummary(
                file_path=r["file_path"],
                symbol=r["symbol"],
                lines=tuple(r["lines"]),
                purpose=r.get("purpose", ""),
            )
            for r in d.get("code_regions", [])
        ]

        entries.append(DecisionStatusEntry(
            decision_id=d["decision_id"],
            description=d["description"],
            status=status,
            source_type=d.get("source_type", ""),
            source_ref=d.get("source_ref", ""),
            ingested_at=d.get("ingested_at", ""),
            code_regions=regions,
            drift_evidence=d.get("drift_evidence", ""),
            blast_radius=d.get("blast_radius", []),
            source_excerpt=d.get("source_excerpt", ""),
            meeting_date=d.get("meeting_date", ""),
            speakers=d.get("speakers", []),
            product_signoff=d.get("product_signoff"),
        ))

    return DecisionStatusResponse(
        ref=ref,
        as_of=datetime.now(timezone.utc).isoformat(),
        summary=summary,
        decisions=entries,
    )
