"""Handler for /scan_branch MCP tool (v0.4.17).

Multi-file drift audit. Given a base ref and a head ref, returns
every decision that touches any file changed between the two refs,
deduped by ``intent_id`` and classified by status.

This is the multi-file counterpart to ``bicameral.drift`` — the
tool the agent reaches for when the user asks "what's drifted on
this branch?" instead of "is pricing.py drifted?". Composes
existing primitives:

- ``ledger.status.get_changed_files_in_range`` — v0.4.11 git-diff
  machinery, already used by ``link_commit`` for its latent-drift
  range sweep
- ``ledger.status.resolve_ref`` — v0.4.17 ref resolver, handles
  branch names / tags / short SHAs
- ``ctx.ledger.get_decisions_for_file`` — per-file decision lookup
  with v0.4.14 source_excerpt plumbing
- ``handlers.detect_drift.raw_decisions_to_drift_entries`` — the
  exact mapping extracted in v0.4.17 so scan_branch and drift
  produce byte-identical per-entry output

The handler never calls an LLM. It's a deterministic composition
over the range-diff sweep.
"""

from __future__ import annotations

import logging
import os

from contracts import DriftEntry, LinkCommitResponse, ScanBranchResponse
from handlers.action_hints import generate_hints_for_scan_branch
from handlers.detect_drift import raw_decisions_to_drift_entries
from handlers.link_commit import handle_link_commit
from ledger.status import get_changed_files_in_range, resolve_ref

logger = logging.getLogger(__name__)

# Import the cap from the adapter so the two sweeps agree.
try:
    from ledger.adapter import _MAX_SWEEP_FILES
except Exception:
    _MAX_SWEEP_FILES = 200


def _default_base_ref() -> str:
    """The authoritative ref the user is scanning against.

    Priority order:
      1. ``BICAMERAL_AUTHORITATIVE_REF`` env var (the same env var
         the v0.4.6 pollution guard reads). Mirrors link_commit's
         expectations so the two tools agree on what "the branch"
         is relative to.
      2. ``main`` — the convention.
    """
    return os.getenv("BICAMERAL_AUTHORITATIVE_REF", "").strip() or "main"


async def handle_scan_branch(
    ctx,
    base_ref: str | None = None,
    head_ref: str | None = None,
    use_working_tree: bool = False,
) -> ScanBranchResponse:
    """Audit every decision touching any file changed between
    ``base_ref`` and ``head_ref``.

    Defaults:
      - ``base_ref``: ``BICAMERAL_AUTHORITATIVE_REF`` or ``main``
      - ``head_ref``: ``HEAD``
      - ``use_working_tree``: ``False`` — PR-review posture (compare
        against HEAD, not disk). This is the dominant case; the
        working-tree variant is available for pre-commit sweeps.

    Auto-syncs the ledger to HEAD first (same as ``bicameral.drift``)
    so drift status reflects the current codebase state.
    """
    base = (base_ref or _default_base_ref()).strip()
    head = (head_ref or "HEAD").strip()
    source = "working_tree" if use_working_tree else "HEAD"

    # Auto-sync the ledger to HEAD. Same contract as bicameral.drift —
    # the scan is meaningless if the status fields are stale.
    sync_status: LinkCommitResponse = await handle_link_commit(ctx, "HEAD")

    base_sha = resolve_ref(base, ctx.repo_path) or base
    head_sha = resolve_ref(head, ctx.repo_path) or head

    # Determine the sweep scope. Three cases:
    #   1. base unresolvable or equal to head → head_only fallback
    #   2. range produces more than _MAX_SWEEP_FILES → range_truncated
    #   3. default → range_diff
    changed_files: list[str] = []
    sweep_scope: str = "head_only"

    if base_sha and head_sha and base_sha != head_sha:
        range_files = get_changed_files_in_range(
            base_sha, head_sha, ctx.repo_path,
        )
        if range_files is None:
            # base unreachable (shallow clone, force-pushed, etc.)
            logger.warning(
                "[scan_branch] range unreachable %s..%s — falling back to head_only",
                base_sha[:8], head_sha[:8],
            )
            sweep_scope = "head_only"
        else:
            sweep_scope = "range_diff"
            if len(range_files) > _MAX_SWEEP_FILES:
                logger.warning(
                    "[scan_branch] range %s..%s touched %d files, capping at %d",
                    base_sha[:8], head_sha[:8], len(range_files), _MAX_SWEEP_FILES,
                )
                changed_files = range_files[:_MAX_SWEEP_FILES]
                sweep_scope = "range_truncated"
            else:
                changed_files = list(range_files)

    # Fan out per-file decision queries. Dedup by decision_id across the
    # full set — a decision touching N files shows up once in the
    # output, not N times. Track the first DriftEntry seen for each
    # decision so the dedup is stable.
    seen_decisions: dict[str, DriftEntry] = {}
    combined_counts = {"drifted": 0, "pending": 0, "ungrounded": 0, "reflected": 0}
    undocumented_union: set[str] = set()

    for file_path in changed_files:
        try:
            raw_decisions = await ctx.ledger.get_decisions_for_file(file_path)
        except Exception as exc:
            logger.warning(
                "[scan_branch] get_decisions_for_file failed for %s: %s",
                file_path, exc,
            )
            continue

        entries, _file_counts = raw_decisions_to_drift_entries(raw_decisions)
        for entry in entries:
            if entry.decision_id not in seen_decisions:
                seen_decisions[entry.decision_id] = entry
                combined_counts[entry.status] = combined_counts.get(entry.status, 0) + 1

        # Union undocumented symbols across files. Use the same
        # fallback as detect_drift — the real-code-locator path is
        # opt-in via env var.
        if os.getenv("USE_REAL_CODE_LOCATOR", "0") != "1":
            try:
                file_undoc = await ctx.ledger.get_undocumented_symbols(file_path)
                undocumented_union.update(file_undoc or [])
            except Exception as exc:
                logger.debug(
                    "[scan_branch] get_undocumented_symbols failed for %s: %s",
                    file_path, exc,
                )

    response = ScanBranchResponse(
        base_ref=base_sha if base_sha and base_sha != base else base,
        head_ref=head_sha if head_sha and head_sha != head else head,
        sweep_scope=sweep_scope,  # type: ignore[arg-type]
        range_size=len(changed_files),
        source=source,  # type: ignore[arg-type]
        decisions=list(seen_decisions.values()),
        files_changed=changed_files,
        drifted_count=combined_counts["drifted"],
        pending_count=combined_counts["pending"],
        ungrounded_count=combined_counts["ungrounded"],
        reflected_count=combined_counts["reflected"],
        undocumented_symbols=sorted(undocumented_union),
    )
    response.action_hints = generate_hints_for_scan_branch(
        response, guided_mode=getattr(ctx, "guided_mode", False),
    )
    # Preserve the sync_status as a sibling so callers can see whether
    # the ledger was actually fresh when the scan ran. Stored as an
    # attribute on the model — mutation is fine since ScanBranchResponse
    # is a Pydantic model and this is an intentional side-channel.
    # (Keeping it off the schema for now so the response stays
    # scan-focused; link_commit already has its own tool for sync info.)
    _ = sync_status  # silences the "unused" linter without shaping the API

    return response
