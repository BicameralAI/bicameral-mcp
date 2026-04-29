"""Bulk-classify CLI for unclassified decisions (#77).

Reads every decision row whose ``decision_level`` is NONE, runs the
heuristic classifier, prints a table of (decision_id, proposed_level,
rationale) for human review, and — when ``--apply`` is passed — persists
the proposed level via the ``ledger.queries.update_decision_level``
helper. Same write path as the MCP ``bicameral.set_decision_level`` tool
and the dashboard inline-edit POST endpoint.

Default is dry-run (no writes). ``--apply`` is required to mutate the
ledger.

Usage:

    bicameral-mcp-classify           # dry-run, table only
    bicameral-mcp-classify --apply   # apply heuristic levels to all rows
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TextIO

from classify.heuristic import classify
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.queries import update_decision_level

_PROGRESS_INTERVAL = 100


def _format_proposal_table(
    proposals: list[tuple[str, str, str, str]],
    out: TextIO,
) -> None:
    """Render the dry-run proposal table to ``out``.

    proposals: list of (decision_id, description, proposed_level, rationale)
    """
    if not proposals:
        out.write("No unclassified decisions found.\n")
        return

    out.write(f"{'decision_id':<48}  {'level':<5}  {'rationale'}\n")
    out.write(f"{'-' * 48}  {'-' * 5}  {'-' * 60}\n")
    for did, _desc, level, rationale in proposals:
        flag = " (low confidence)" if rationale.lower().startswith("low confidence") else ""
        out.write(f"{did:<48}  {level:<5}  {rationale}{flag}\n")


async def _gather_proposals(
    client,
) -> list[tuple[str, str, str, str]]:
    """Read every unclassified decision and run the heuristic.

    Returns a list of (decision_id, description, level, rationale) tuples.
    """
    rows = await client.query(
        "SELECT type::string(id) AS decision_id, description "
        "FROM decision WHERE decision_level = NONE"
    )
    proposals: list[tuple[str, str, str, str]] = []
    for row in rows or []:
        did = row.get("decision_id") or ""
        desc = row.get("description") or ""
        level, rationale = classify(desc)
        proposals.append((did, desc, level, rationale))
    return proposals


async def _run(
    apply_changes: bool,
    *,
    out: TextIO | None = None,
    adapter: SurrealDBLedgerAdapter | None = None,
) -> int:
    """Core async entry point. Returns process exit code.

    Args:
        apply_changes: when True, persist proposed levels via
            ``update_decision_level``; when False, dry-run only.
        out: where to render the table and status lines. Defaults to
            ``sys.stdout`` (resolved at call time so test monkeypatches on
            ``sys.stdout`` work).
        adapter: optional pre-connected adapter. When supplied, ``_run``
            does not connect or close it (caller owns the lifecycle). The
            CLI ``main`` always creates and tears down its own adapter.
    """
    if out is None:
        out = sys.stdout

    owns_adapter = adapter is None
    if adapter is None:
        adapter = SurrealDBLedgerAdapter()
        try:
            await adapter.connect()
        except Exception as exc:
            out.write(f"error: failed to connect to ledger: {exc}\n")
            return 2

    client = adapter._client
    try:
        proposals = await _gather_proposals(client)
        _format_proposal_table(proposals, out)

        if not apply_changes:
            out.write(f"\nDry run -- {len(proposals)} proposals shown. Use --apply to write.\n")
            return 0

        # Apply mode: per-row writes via the same primitive as the MCP tool.
        applied = 0
        for i, (did, _desc, level, _rationale) in enumerate(proposals, 1):
            try:
                await update_decision_level(client, did, level)
                applied += 1
            except Exception as exc:
                out.write(f"error: failed to update {did}: {exc}\n")
                return 3
            if i % _PROGRESS_INTERVAL == 0:
                out.write(f"  ...applied {i}/{len(proposals)}\n")
        out.write(f"Applied {applied} classifications.\n")
        return 0
    finally:
        if owns_adapter:
            await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bicameral-mcp-classify",
        description=(
            "Bulk-classify unclassified decisions in the local ledger. "
            "Default is dry-run (prints proposals); pass --apply to write."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write proposed levels to the ledger (default: dry-run only).",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.apply))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
