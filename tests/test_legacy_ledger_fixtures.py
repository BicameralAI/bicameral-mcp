"""Fixture-replay regression suite (#296 Layer A + B).

For every frozen DB shape under ``tests/fixtures/legacy_ledgers/``:

  1. Build the bad state in a fresh ``memory://`` ledger.
  2. Run ``init_schema`` + ``migrate`` (the production code path).
  3. Assert the schema reaches ``SCHEMA_VERSION``, the fixture's own
     ``assert_clean`` invariants hold, and a second ``init_schema`` +
     ``migrate`` is a no-op (idempotent).

Adding a new fixture requires no test code — register it in
``FIXTURES`` and the parametrized test exercises it on every PR.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from ledger.client import LedgerClient
from ledger.schema import SCHEMA_VERSION, init_schema, migrate

# Fixtures live under tests/fixtures/legacy_ledgers/. ``tests/`` is not
# a package (no top-level __init__.py) so we extend sys.path to import
# the fixture modules by name.
_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "legacy_ledgers"
if str(_FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(_FIXTURES_DIR))

import v3_yields_source_span  # noqa: E402 — see sys.path comment

# Each entry: (slug, module). Module must export `build(client)` and
# may export `assert_clean(client)` for fixture-specific invariants.
FIXTURES = [
    ("v3_yields_source_span", v3_yields_source_span),
]


async def _fresh_client(slug: str) -> LedgerClient:
    c = LedgerClient(url="memory://", ns="bicameral_test", db=f"fixture_{slug}")
    await c.connect()
    return c


@pytest.mark.phase2
@pytest.mark.asyncio
@pytest.mark.parametrize("slug,module", FIXTURES, ids=[s for s, _ in FIXTURES])
async def test_legacy_ledger_fixture_reaches_clean_state(slug: str, module) -> None:
    """init_schema + migrate must terminate cleanly on every fixture."""
    c = await _fresh_client(slug)
    try:
        # Build the broken DB state.
        await module.build(c)

        # Run the production init/migrate path. It must not raise — that's
        # the entire safety contract this suite enforces.
        await init_schema(c)
        await migrate(c, allow_destructive=True)

        # Schema reached current version.
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows, f"{slug}: schema_meta empty after migrate"
        assert rows[0]["version"] == SCHEMA_VERSION, (
            f"{slug}: schema_meta.version = {rows[0]['version']!r}, expected {SCHEMA_VERSION}"
        )

        # Fixture-specific invariants.
        if hasattr(module, "assert_clean"):
            await module.assert_clean(c)

        # Idempotency: a second pass changes nothing.
        await init_schema(c)
        await migrate(c, allow_destructive=True)
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows[0]["version"] == SCHEMA_VERSION, (
            f"{slug}: schema_meta.version regressed on second migrate ({rows[0]['version']!r})"
        )
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_fixture_registry_imports() -> None:
    """Every fixture in ``FIXTURES`` must export an async ``build``.

    Catches typos in the registry — a missing ``build`` would silently
    skip the migration assertion on that row.
    """
    for slug, module in FIXTURES:
        assert hasattr(module, "build"), f"{slug} missing build()"
        # Re-import via importlib to confirm the module is on sys.path
        # under its registered slug (catches typos in FIXTURES).
        importlib.import_module(slug)
