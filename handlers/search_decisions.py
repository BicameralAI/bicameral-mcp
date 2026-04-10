"""Handler for /search_decisions MCP tool.

Pre-flight for implementation planning: given a query, surface past decisions
in the same area with their status. Auto-triggers link_commit(HEAD) first.
"""

from __future__ import annotations

from contracts import CodeRegionSummary, DecisionMatch, LinkCommitResponse, SearchDecisionsResponse
from handlers.link_commit import handle_link_commit


async def handle_search_decisions(
    ctx,
    query: str,
    max_results: int = 10,
    min_confidence: float = 0.5,
) -> SearchDecisionsResponse:
    sync_status: LinkCommitResponse = await handle_link_commit(ctx, "HEAD")

    raw_matches = await ctx.ledger.search_by_query(query, max_results=max_results, min_confidence=min_confidence)

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

        if not regions:
            status = "ungrounded"
        else:
            raw_regions = m.get("code_regions", [])
            status = raw_regions[0].get("status", "pending") if raw_regions else "pending"

        if status in ("drifted", "pending"):
            suggested_review.append(m["intent_id"])

        matches.append(DecisionMatch(
            intent_id=m["intent_id"],
            description=m["description"],
            status=status,
            confidence=m.get("confidence", 0.5),
            source_ref=m.get("source_ref", ""),
            code_regions=regions,
            drift_evidence=m.get("drift_evidence", ""),
            related_constraints=m.get("related_constraints", []),
        ))

    ungrounded_count = sum(1 for m in matches if m.status == "ungrounded")

    return SearchDecisionsResponse(
        query=query,
        sync_status=sync_status,
        matches=matches,
        ungrounded_count=ungrounded_count,
        suggested_review=suggested_review,
    )
