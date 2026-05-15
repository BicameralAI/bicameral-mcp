"""Shared fixtures for `tests/perf/` — file-backed SurrealKV perf tests (#357 sub-task 2).

These tests run against a real on-disk SurrealKV instance, not `memory://`.
Devin's #357 critique flagged that every perf claim shipped to dev came
from `memory://` (a CPU-cache benchmark, not a storage benchmark); this
fixture closes that gap.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate

_NS_COUNTER = 0


@pytest.fixture
async def surrealkv_client(tmp_path):
    """Build a fresh on-disk SurrealKV ledger with schema migrated.

    Yields a connected `LedgerClient`. Backing file lives under pytest's
    `tmp_path` so the OS cleans up automatically when the test finishes.
    Each test gets a unique namespace to prevent any cross-test bleeding
    if the same process re-enters the fixture.
    """
    global _NS_COUNTER
    _NS_COUNTER += 1

    db_path = tmp_path / "perf.db"
    url = f"surrealkv://{db_path}"
    client = LedgerClient(url=url, ns=f"perf_{_NS_COUNTER}", db="ledger_perf")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    try:
        yield client
    finally:
        await client.close()
