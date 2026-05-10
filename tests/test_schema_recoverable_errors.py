"""SurrealDB error-format contract test (#296 Layer A).

``ledger.schema._execute_define_idempotent`` swallows the substrings in
``RECOVERABLE_DEFINE_PATTERNS``: those are the load-bearing safety
contract that lets ``init_schema`` continue past row-state that the
next migration is responsible for cleaning up.

If a future ``surrealdb-py`` bump changes the error-string format, the
catch silently stops working and the user sees the same crash that
motivated #296. This test fabricates the bad-row state, provokes the
error, and asserts at least one of the recoverable substrings matches.
When it fails, the message tells the maintainer exactly what the new
format looks like so the constants list can be updated explicitly.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient, LedgerError
from ledger.schema import RECOVERABLE_DEFINE_PATTERNS


async def _fresh_client(suffix: str) -> LedgerClient:
    c = LedgerClient(url="memory://", ns="bicameral_test", db=f"recov_{suffix}")
    await c.connect()
    return c


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_define_index_unique_violation_matches_recoverable_pattern() -> None:
    """A DEFINE INDEX UNIQUE on a table with duplicate rows must produce
    one of the substrings in ``RECOVERABLE_DEFINE_PATTERNS`` (this is
    what the v4→v5 cleanup relies on)."""
    c = await _fresh_client("uniq")
    try:
        await c.execute("DEFINE TABLE thing SCHEMAFULL")
        await c.execute("DEFINE FIELD k ON thing TYPE string")
        # Two rows with the same key — UNIQUE will reject the index.
        await c.execute("CREATE thing SET k = 'dup'")
        await c.execute("CREATE thing SET k = 'dup'")
        with pytest.raises(LedgerError) as exc:
            await c.execute("DEFINE INDEX idx_thing_k ON thing FIELDS k UNIQUE")
        msg = str(exc.value).lower()
        matched = [p for p in RECOVERABLE_DEFINE_PATTERNS if p in msg]
        assert matched, (
            "SurrealDB UNIQUE-violation error string changed — the "
            "_execute_define_idempotent catch will no longer cover it. "
            "Update RECOVERABLE_DEFINE_PATTERNS in ledger/schema.py to "
            f"include a substring of: {exc.value!r}"
        )
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_define_index_overwrite_type_violation_matches_recoverable_pattern() -> None:
    """A DEFINE INDEX OVERWRITE on a table whose existing rows violate the
    column types referenced by the index must produce one of
    ``RECOVERABLE_DEFINE_PATTERNS``. This is the exact failure mode that
    motivated #296 (yields.in = source_span:* against the new
    RELATION IN input_span constraint)."""
    c = await _fresh_client("type")
    try:
        # Set up a typed RELATION table with a row that violates the type.
        await c.execute("DEFINE TABLE source_span SCHEMAFULL")
        await c.execute("DEFINE TABLE input_span SCHEMAFULL")
        await c.execute("DEFINE TABLE decision SCHEMAFULL")
        await c.execute("CREATE source_span:legacy_1 SET text = 'old'")
        await c.execute("CREATE input_span:fresh_1 SET text = 'new'")
        await c.execute("CREATE decision:d_1 SET description = 'd'")
        # Permissive yields → insert the bad row → tighten the type and
        # try to apply the unique index. The OVERWRITE re-validates.
        await c.execute("DEFINE TABLE yields SCHEMAFULL TYPE RELATION")
        await c.execute("RELATE source_span:legacy_1 -> yields -> decision:d_1")
        await c.execute("RELATE input_span:fresh_1 -> yields -> decision:d_1")
        # Tighten — this re-validates the source_span row against the new IN type.
        await c.execute(
            "DEFINE TABLE OVERWRITE yields SCHEMAFULL "
            "TYPE RELATION IN input_span OUT decision"
        )
        with pytest.raises(LedgerError) as exc:
            await c.execute(
                "DEFINE INDEX OVERWRITE idx_yields_unique ON yields FIELDS in, out UNIQUE"
            )
        msg = str(exc.value).lower()
        matched = [p for p in RECOVERABLE_DEFINE_PATTERNS if p in msg]
        assert matched, (
            "SurrealDB type-mismatch error string changed — the "
            "_execute_define_idempotent catch will no longer cover the #296 "
            "scenario. Update RECOVERABLE_DEFINE_PATTERNS in ledger/schema.py "
            f"to include a substring of: {exc.value!r}"
        )
    finally:
        await c.close()


@pytest.mark.phase2
def test_recoverable_patterns_constant_is_lowercase() -> None:
    """The catch lower-cases the SurrealDB message before substring
    matching. Patterns must be lowercase too, or they'd silently never
    match."""
    for pattern in RECOVERABLE_DEFINE_PATTERNS:
        assert pattern == pattern.lower(), (
            f"RECOVERABLE_DEFINE_PATTERNS entry {pattern!r} contains "
            "non-lowercase characters; the substring catch lower-cases "
            "the SurrealDB message before matching."
        )
