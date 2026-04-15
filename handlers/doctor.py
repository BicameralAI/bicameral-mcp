"""Handler for /doctor MCP tool (v0.4.18).

The composition layer. Replaces ``bicameral.drift`` as the user-facing
"check for drift" entry point and picks the right scope automatically
based on what the caller hands in:

- ``file_path`` given → per-file scope. Delegates to
  ``handle_detect_drift``. Same output shape the old
  ``bicameral.drift`` tool produced, just nested inside a
  ``DoctorResponse.file_scan`` field.
- ``file_path`` absent → branch + ledger scope. Runs
  ``handle_scan_branch`` against the current branch (using the
  same auto-detected base ref as scan_branch) and composes a
  compact ``DoctorLedgerSummary`` of repo-wide status counts
  alongside.
- Nothing to scan (empty ledger, clean repo) → ``scope="empty"``.

Server-side orchestration only. No LLM, no heuristics beyond
"did the caller name a file or not." The agent sees a structured
envelope and renders whichever sub-field was populated.
"""

from __future__ import annotations

import logging

from contracts import (
    ActionHint,
    DoctorLedgerSummary,
    DoctorResponse,
)
from handlers.detect_drift import handle_detect_drift
from handlers.scan_branch import handle_scan_branch

logger = logging.getLogger(__name__)


async def _build_ledger_summary(ctx) -> DoctorLedgerSummary:
    """Compact repo-wide status summary. One ledger roundtrip."""
    try:
        decisions = await ctx.ledger.get_all_decisions(filter="all")
    except Exception as exc:
        logger.warning("[doctor] ledger_summary fetch failed: %s", exc)
        return DoctorLedgerSummary()

    summary = DoctorLedgerSummary(total=len(decisions))
    for d in decisions:
        status = d.get("status", "ungrounded")
        if status == "drifted":
            summary.drifted += 1
        elif status == "pending":
            summary.pending += 1
        elif status == "reflected":
            summary.reflected += 1
        else:
            summary.ungrounded += 1
    return summary


def _compose_action_hints(
    file_scan_hints: list[ActionHint],
    branch_scan_hints: list[ActionHint],
) -> list[ActionHint]:
    """Dedupe + merge hints from the sub-scans by ``kind``.

    The sub-handlers each generate their own ``review_drift`` /
    ``ground_decision`` hints. Doctor surfaces both sets but collapses
    duplicate kinds to a single hint (the first one wins — usually
    the branch-scan hint, which is broader). The refs list is
    unioned so the agent still has the full set of intent_ids and
    file paths.
    """
    merged: dict[str, ActionHint] = {}
    for hint in (*file_scan_hints, *branch_scan_hints):
        existing = merged.get(hint.kind)
        if existing is None:
            merged[hint.kind] = hint
            continue
        combined_refs = list(dict.fromkeys([*existing.refs, *hint.refs]))
        merged[hint.kind] = ActionHint(
            kind=existing.kind,
            message=existing.message,
            blocking=existing.blocking or hint.blocking,
            refs=combined_refs,
        )
    return list(merged.values())


async def handle_doctor(
    ctx,
    file_path: str | None = None,
    base_ref: str | None = None,
    head_ref: str | None = None,
    use_working_tree: bool = False,
) -> DoctorResponse:
    """Auto-detect scope and run the appropriate drift check.

    - ``file_path`` given → file scope. Runs detect_drift on that
      one file and returns ``scope="file"`` with ``file_scan``
      populated.
    - ``file_path`` absent → branch scope. Runs scan_branch +
      ledger_summary and returns ``scope="branch"``. If the scan
      produced no files changed AND the ledger is empty,
      ``scope="empty"``.
    """
    if file_path:
        file_scan = await handle_detect_drift(
            ctx,
            file_path=file_path,
            use_working_tree=use_working_tree,
        )
        return DoctorResponse(
            scope="file",
            file_scan=file_scan,
            action_hints=[],  # detect_drift has no hint generator today
        )

    # Branch scope: fan out scan_branch + ledger summary in one call.
    branch_scan = await handle_scan_branch(
        ctx,
        base_ref=base_ref,
        head_ref=head_ref,
        use_working_tree=use_working_tree,
    )
    ledger_summary = await _build_ledger_summary(ctx)

    # Honest empty path: nothing to scan AND nothing in the ledger.
    if (
        not branch_scan.decisions
        and not branch_scan.files_changed
        and ledger_summary.total == 0
    ):
        return DoctorResponse(scope="empty")

    merged_hints = _compose_action_hints([], list(branch_scan.action_hints))

    return DoctorResponse(
        scope="branch",
        branch_scan=branch_scan,
        ledger_summary=ledger_summary,
        action_hints=merged_hints,
    )
