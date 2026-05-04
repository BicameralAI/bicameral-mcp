"""Handler for /detect_drift MCP tool.

Code review check: given a file path, surface all decisions that touch symbols
in that file, highlighting any that diverge from current content.
Auto-triggers link_commit(HEAD) first.

v0.4.17: ``raw_decisions_to_drift_entries`` is extracted as a
module-level helper so ``handlers.scan_branch`` can reuse the exact
same per-decision mapping logic without duplicating the loop.

V1 B2: drifted entries get an advisory ``cosmetic_hint`` populated from
``ledger.ast_diff.is_cosmetic_change`` over the region's HEAD bytes vs
working-tree bytes. The hint is enrichment, not a gate — the pure
``raw_decisions_to_drift_entries`` mapping stays IO-free.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from code_locator.indexing.symbol_extractor import EXTENSION_LANGUAGE
from contracts import DetectDriftResponse, DriftEntry, LinkCommitResponse
from handlers.link_commit import handle_link_commit
from ledger.ast_diff import is_cosmetic_change
from ledger.status import get_git_content, resolve_symbol_lines

logger = logging.getLogger(__name__)


def _resolve_subjects_eligible(decision: dict) -> bool:
    """Return True only for decisions that should feed CodeGenome resolve_subjects.

    L2 decisions have the technical specificity needed to map to code symbols.
    L1 (behavioral claims) and L3 (detail) do not — running resolve_subjects
    on them produces noise or no matches. L1 decisions are intentionally
    ungrounded; treating them as grounding gaps is incorrect.

    CodeGenome Phase 1 replaces this stub's body with actual resolve_subjects
    calls. The gate condition stays: only L2 enters the identity graph.
    """
    level = decision.get("decision_level")
    if level is None:
        return True  # pre-v0.9.3 decisions: eligible by default for backward compat
    return level == "L2"


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

        _signoff = d.get("signoff") or {}
        entries.append(
            DriftEntry(
                decision_id=d["decision_id"],
                description=d["description"],
                status=status,
                signoff_state=(_signoff.get("state") if isinstance(_signoff, dict) else None),
                symbol=region.get("symbol", ""),
                lines=tuple(region.get("lines", (0, 0))),
                drift_evidence=drift_evidence,
                source_ref=d.get("source_ref", ""),
                source_excerpt=d.get("source_excerpt", ""),
                meeting_date=d.get("meeting_date", ""),
            )
        )

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
        decision_symbols = {d.get("code_region", {}).get("symbol", "") for d in raw_decisions}
        undocumented = [s["name"] for s in all_symbols if s["name"] not in decision_symbols]
    else:
        undocumented = await ctx.ledger.get_undocumented_symbols(file_path)

    entries, counts = raw_decisions_to_drift_entries(raw_decisions)
    source = "working_tree" if use_working_tree else "HEAD"

    # V1 B2: enrich drifted entries with an AST cosmetic hint. Read-path
    # only — never mutates content_hash, never changes status. Hint is
    # meaningful only when the response advertises ``source="working_tree"``
    # (the cosmetic comparison axis is HEAD vs working tree); skip on
    # HEAD-source so we don't attach hints derived from a diff axis the
    # caller didn't ask about.
    if use_working_tree:
        _enrich_with_cosmetic_hints(entries, file_path, ctx.repo_path)

    return DetectDriftResponse(
        file_path=file_path,
        sync_status=sync_status,
        source=source,
        decisions=entries,
        drifted_count=counts["drifted"],
        pending_count=counts["pending"],
        undocumented_symbols=undocumented,
    )


def _enrich_with_cosmetic_hints(
    entries: list[DriftEntry],
    file_path: str,
    repo_path: str,
) -> None:
    """Set ``cosmetic_hint=True`` on drifted entries whose HEAD→working-tree
    diff is provably whitespace-only per the strict B1 whitelist.

    Per-entry alignment: the stored ``entry.lines`` is the baseline anchor
    (set by ingest, possibly updated by link_commit's symbol-shift heal).
    Lines at HEAD and at the working tree may have shifted independently,
    so we re-resolve the symbol against each ref via tree-sitter and slice
    each ref's content using its own resolved range. If either resolution
    fails, fail safe to ``cosmetic_hint=False`` — the cosmetic-hint
    contract is "False is cheap, True must be earned" (V1 plan §B1).

    Skips non-drifted entries, files we can't read, unsupported extensions,
    and entries whose symbol can't be located at HEAD or working tree.
    """
    drifted = [e for e in entries if e.status == "drifted"]
    if not drifted:
        return

    ext = Path(file_path).suffix.lower()
    lang = EXTENSION_LANGUAGE.get(ext)
    if lang is None:
        return  # unsupported extension — no hint computed for this file

    # NOTE: ledger.status.get_git_content takes start_line / end_line in
    # its signature but ignores them — it always returns the full file
    # body. Two existing legacy callers do the slicing themselves after
    # the call. We pass 0, 0 to make the unused-args reality explicit;
    # we slice locally below per-region. Cleaning up the upstream
    # signature is a separate refactor across all callers (see ledger/
    # status.py:110, ledger/adapter.py:67).
    head_full = get_git_content(file_path, 0, 0, repo_path, ref="HEAD")
    wt_full = get_git_content(file_path, 0, 0, repo_path, ref="working_tree")
    if head_full is None or wt_full is None:
        return  # file missing at one side — can't compare, leave default

    head_lines = head_full.splitlines()
    wt_lines = wt_full.splitlines()

    for entry in drifted:
        # Use entry.symbol to re-resolve aligned line ranges per ref.
        # ``entry.lines`` (the baseline anchor) cannot be trusted for
        # slicing both HEAD and the working tree because shifts on either
        # side can desync the slice from the symbol body. Resolution
        # failure → safe default of cosmetic_hint=False.
        if not entry.symbol:
            continue
        try:
            head_range = resolve_symbol_lines(file_path, entry.symbol, repo_path, ref="HEAD")
            wt_range = resolve_symbol_lines(file_path, entry.symbol, repo_path, ref="working_tree")
        except Exception as exc:
            logger.debug(
                "[detect_drift] resolve_symbol_lines failed for %s/%s: %s",
                file_path,
                entry.symbol,
                exc,
            )
            continue
        if head_range is None or wt_range is None:
            continue  # symbol absent at one side — not a cosmetic case

        head_start, head_end = head_range
        wt_start, wt_end = wt_range
        if head_start <= 0 or head_end < head_start:
            continue
        if wt_start <= 0 or wt_end < wt_start:
            continue

        head_slice = "\n".join(head_lines[head_start - 1 : head_end])
        wt_slice = "\n".join(wt_lines[wt_start - 1 : wt_end])
        if not head_slice or not wt_slice:
            continue
        if head_slice == wt_slice:
            continue  # no byte diff at all — hint is meaningless here
        try:
            entry.cosmetic_hint = is_cosmetic_change(head_slice, wt_slice, lang)
        except Exception as exc:
            logger.debug("[detect_drift] cosmetic hint failed for %s: %s", file_path, exc)
