"""Handler for /search_decisions MCP tool.

Pre-flight for implementation planning: given a query, surface past decisions
in the same area with their status. Auto-triggers link_commit(HEAD) first.
"""

from __future__ import annotations

import time

from contracts import (
    CodeRegionSummary,
    DecisionMatch,
    LinkCommitResponse,
    SearchDecisionsResponse,
    SyncMetrics,
)
from handlers.action_hints import generate_hints_for_search
from handlers.link_commit import handle_link_commit


async def handle_search_decisions(
    ctx,
    query: str,
    max_results: int = 10,
    min_confidence: float = 0.5,
) -> SearchDecisionsResponse:
    # V1 A3: time the mandatory catch-up so callers can see how long this
    # handler spent in link_commit. Local timing (not sync_state) so nested
    # calls don't step on each other's metrics. Scope mirrors
    # ``ensure_ledger_synced`` (preflight / history): cover both
    # ``handle_link_commit`` AND ``get_session_start_banner`` so the same
    # ``sync_catchup_ms`` field measures the same surface across handlers.
    t0 = time.perf_counter()
    sync_status: LinkCommitResponse = await handle_link_commit(ctx, "HEAD")
    catchup_ms = round((time.perf_counter() - t0) * 1000, 3)

    raw_matches = await ctx.ledger.search_by_query(
        query, max_results=max_results, min_confidence=min_confidence
    )

    matches: list[DecisionMatch] = []
    suggested_review: list[str] = []

    for m in raw_matches:
        regions = [
            CodeRegionSummary(
                file_path=r["file_path"],
                symbol=r["symbol"],
                lines=tuple(r["lines"]),
                purpose=r.get("purpose", ""),
            )
            for r in m.get("code_regions", [])
        ]

        decision_status = str(m.get("status") or "").strip()
        intent_status = decision_status  # compat alias used below
        if intent_status in ("reflected", "drifted", "pending", "ungrounded"):
            status = intent_status
        elif not regions:
            status = "ungrounded"
        else:
            status = "pending"

        if status in ("drifted", "pending"):
            suggested_review.append(m["decision_id"])

        _signoff = m.get("signoff") or {}
        matches.append(
            DecisionMatch(
                decision_id=m["decision_id"],
                description=m["description"],
                status=status,
                signoff_state=(_signoff.get("state") if isinstance(_signoff, dict) else None),
                confidence=m.get("confidence", 0.5),
                source_ref=m.get("source_ref", ""),
                code_regions=regions,
                drift_evidence=m.get("drift_evidence", ""),
                related_constraints=m.get("related_constraints", []),
                source_excerpt=m.get("source_excerpt", ""),
                meeting_date=m.get("meeting_date", ""),
                signoff=m.get("signoff"),
            )
        )

    ungrounded_count = sum(1 for m in matches if m.status == "ungrounded")

    response = SearchDecisionsResponse(
        query=query,
        sync_status=sync_status,
        matches=matches,
        ungrounded_count=ungrounded_count,
        suggested_review=suggested_review,
    )
    response.action_hints = generate_hints_for_search(
        response,
        guided_mode=getattr(ctx, "guided_mode", False),
    )
    response.sync_metrics = SyncMetrics(sync_catchup_ms=catchup_ms)
    return response
