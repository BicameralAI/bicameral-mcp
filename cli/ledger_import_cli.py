"""CLI entrypoint for `bicameral-mcp ledger-import` (#252 Layer 4)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main(from_file: str | None = None) -> int:
    """Read JSONL from stdin or `--from-file <path>` and import.

    Returns 0 on success with summary printed to stdout, 1 on
    validation/import/connect failure with detail printed to stderr.
    """
    from cli._ledger_io_engine import import_jsonl
    from cli.ledger_io import ImportError_
    from ledger.adapter import SurrealDBLedgerAdapter

    if from_file:
        try:
            lines = Path(from_file).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            sys.stderr.write(f"ledger-import: cannot read {from_file}: {exc}\n")
            return 1
    else:
        lines = sys.stdin.read().splitlines()

    async def _run() -> int:
        adapter = SurrealDBLedgerAdapter()
        await adapter.connect()
        try:
            summary = await import_jsonl(adapter, lines)
        except ImportError_ as exc:
            sys.stderr.write(f"ledger-import: {exc}\n")
            return 1
        sys.stdout.write(
            f"ledger-import: wrote {summary.total_records_written} records "
            f"({sum(summary.data_records_written.values())} data + "
            f"{sum(summary.edge_records_written.values())} edges)\n"
        )
        return 0

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ledger-import: adapter connect failed: {exc}\n")
        return 1
