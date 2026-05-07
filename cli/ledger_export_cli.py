"""CLI entrypoint for `bicameral-mcp ledger-export` (#252 Layer 4)."""

from __future__ import annotations

import asyncio
import sys


def main() -> int:
    """Stream JSON-Lines export to stdout. Returns 0 on success, 1 on
    adapter-connect or query failure."""
    from cli._ledger_io_engine import export_jsonl
    from ledger.adapter import SurrealDBLedgerAdapter

    async def _run() -> int:
        adapter = SurrealDBLedgerAdapter()
        await adapter.connect()
        async for line in export_jsonl(adapter):
            sys.stdout.write(line + "\n")
        return 0

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — operator needs failure context
        sys.stderr.write(f"ledger-export: adapter connect or query failed: {exc}\n")
        return 1
