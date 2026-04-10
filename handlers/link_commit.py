"""Handler for /link_commit MCP tool.

Heartbeat of the ledger — syncs a commit's changes into the graph.
Idempotent: calling twice for the same commit is a no-op.

Phase 0: backed by MockLedgerAdapter (no SurrealDB, no git ops)
Phase 2: backed by SurrealDBLedgerAdapter with real git diff parsing
"""

from __future__ import annotations

import logging
import os

from adapters.ledger import get_drift_analyzer, get_ledger
from contracts import LinkCommitResponse

logger = logging.getLogger(__name__)


async def _reground_ungrounded(ledger, repo_path: str) -> int:
    """Attempt to ground any ungrounded intents now that the index may be ready.

    Queries the ledger for all ungrounded intents, runs them through the
    two-stage auto-grounding pipeline, and re-ingests those that get grounded.
    Returns the count of newly grounded intents.

    This is the fix for GAP-04/GAP-09: intents ingested before the index was
    built are permanently stuck as ungrounded without this pass.
    """
    from handlers.ingest import _auto_ground_via_search, handle_ingest

    try:
        ungrounded = await ledger.get_all_decisions(filter="ungrounded")
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

    resolved, deferred = _auto_ground_via_search(mappings, repo_path)
    if deferred:
        # Index still not ready — nothing to do yet
        return 0

    newly_grounded = [m for m in resolved if m.get("code_regions")]
    if not newly_grounded:
        return 0

    # Re-ingest the grounded mappings. upsert_intent is idempotent on
    # (description, source_ref) so this updates existing intents, not duplicates.
    payload = {
        "repo": repo_path,
        "commit_hash": "HEAD",
        "mappings": newly_grounded,
    }
    try:
        await ledger.ingest_payload(payload)
        logger.info(
            "[link_commit] lazy re-grounding: %d/%d ungrounded intents now grounded",
            len(newly_grounded), len(ungrounded),
        )
    except Exception as exc:
        logger.warning("[link_commit] lazy re-grounding ingest failed: %s", exc)
        return 0

    return len(newly_grounded)


async def handle_link_commit(commit_hash: str = "HEAD") -> LinkCommitResponse:
    ledger = get_ledger()
    repo_path = os.getenv("REPO_PATH", ".")

    drift_analyzer = get_drift_analyzer()
    result = await ledger.ingest_commit(commit_hash, repo_path, drift_analyzer=drift_analyzer)

    # Lazy re-grounding pass: attempt to ground any intents that were ingested
    # before the index was built (GAP-04 / GAP-09).
    await _reground_ungrounded(ledger, repo_path)

    return LinkCommitResponse(
        commit_hash=result["commit_hash"],
        synced=result["synced"],
        reason=result["reason"],
        regions_updated=result.get("regions_updated", 0),
        decisions_reflected=result.get("decisions_reflected", 0),
        decisions_drifted=result.get("decisions_drifted", 0),
        undocumented_symbols=result.get("undocumented_symbols", []),
    )
