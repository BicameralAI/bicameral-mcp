"""Handler for /link_commit MCP tool.

Heartbeat of the ledger — syncs a commit's changes into the graph.
Idempotent: calling twice for the same commit is a no-op.
"""

from __future__ import annotations

import logging

from contracts import LinkCommitResponse

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
        await ctx.ledger.ingest_payload(payload)
        logger.info(
            "[link_commit] lazy re-grounding: %d/%d ungrounded intents now grounded",
            len(newly_grounded), len(ungrounded),
        )
    except Exception as exc:
        logger.warning("[link_commit] lazy re-grounding ingest failed: %s", exc)
        return 0

    return len(newly_grounded)


async def handle_link_commit(ctx, commit_hash: str = "HEAD") -> LinkCommitResponse:
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

    result = await ctx.ledger.ingest_commit(
        commit_hash, ctx.repo_path, drift_analyzer=ctx.drift_analyzer,
    )

    await _reground_ungrounded(ctx)

    return LinkCommitResponse(
        commit_hash=result["commit_hash"],
        synced=result["synced"],
        reason=result["reason"],
        regions_updated=result.get("regions_updated", 0),
        decisions_reflected=result.get("decisions_reflected", 0),
        decisions_drifted=result.get("decisions_drifted", 0),
        undocumented_symbols=result.get("undocumented_symbols", []),
    )
