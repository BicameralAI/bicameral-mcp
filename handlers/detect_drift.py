"""Handler for /detect_drift MCP tool.

Code review check: given a file path, surface all decisions that touch symbols
in that file, highlighting any that diverge from current content.
Auto-triggers link_commit(HEAD) first.

v0.4.17: ``raw_decisions_to_drift_entries`` is extracted as a
module-level helper so ``handlers.scan_branch`` can reuse the exact
same per-decision mapping logic without duplicating the loop.
"""

from __future__ import annotations

import os
from pathlib import Path

from contracts import DetectDriftResponse, DriftEntry, LinkCommitResponse
from handlers.link_commit import handle_link_commit


def raw_decisions_to_drift_entries(
    raw_decisions: list[dict],
) -> tuple[list[DriftEntry], dict[str, int]]:
    """Map raw ledger decision dicts to ``DriftEntry`` models.

    Returns the entry list plus a status-count dict with keys
    ``drifted``, ``pending``, ``ungrounded``, ``reflected``. The
    caller decides which counts to surface on its response envelope.

    Pure function — no IO, no ctx.
    """
    entries: list[DriftEntry] = []
    counts = {"drifted": 0, "pending": 0, "ungrounded": 0, "reflected": 0}

    for d in raw_decisions:
        region = d.get("code_region", {})
        status = d.get("status", "ungrounded")

        drift_evidence = ""
        if status == "drifted":
            drift_evidence = "Content hash mismatch detected (mock)"
        if status in counts:
            counts[status] += 1
        else:
            counts["ungrounded"] += 1

        entries.append(DriftEntry(
            decision_id=d["decision_id"],
            description=d["description"],
            status=status,
            symbol=region.get("symbol", ""),
            lines=tuple(region.get("lines", (0, 0))),
            drift_evidence=drift_evidence,
            source_ref=d.get("source_ref", ""),
            source_excerpt=d.get("source_excerpt", ""),
            meeting_date=d.get("meeting_date", ""),
        ))

    return entries, counts


async def handle_detect_drift(
    ctx,
    file_path: str,
    use_working_tree: bool = True,
) -> DetectDriftResponse:
    sync_status: LinkCommitResponse = await handle_link_commit(ctx, "HEAD")

    raw_decisions = await ctx.ledger.get_decisions_for_file(file_path)

    if os.getenv("USE_REAL_CODE_LOCATOR", "0") == "1":
        abs_path = str((Path(ctx.repo_path) / file_path).resolve())
        all_symbols = await ctx.code_graph.extract_symbols(abs_path)
        decision_symbols = {
            d.get("code_region", {}).get("symbol", "") for d in raw_decisions
        }
        undocumented = [
            s["name"] for s in all_symbols if s["name"] not in decision_symbols
        ]
    else:
        undocumented = await ctx.ledger.get_undocumented_symbols(file_path)

    entries, counts = raw_decisions_to_drift_entries(raw_decisions)
    source = "working_tree" if use_working_tree else "HEAD"

    return DetectDriftResponse(
        file_path=file_path,
        sync_status=sync_status,
        source=source,
        decisions=entries,
        drifted_count=counts["drifted"],
        pending_count=counts["pending"],
        undocumented_symbols=undocumented,
    )
