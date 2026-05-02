"""Phase 4 (#77) — bulk-classify CLI tests."""

from __future__ import annotations

import io

import pytest

from cli.classify import _run, main
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.queries import get_decision_level, update_decision_level, upsert_decision


@pytest.fixture(autouse=True)
def _force_memory_ledger(monkeypatch):
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", "memory://")


@pytest.fixture
async def adapter():
    """Yield a connected memory:// SurrealDBLedgerAdapter.

    memory:// SurrealDB instances are per-connection, so tests share a
    single adapter between seed-time writes and the CLI under test.
    """
    a = SurrealDBLedgerAdapter(url="memory://")
    await a.connect()
    try:
        yield a
    finally:
        await a._client.close()


async def _seed(adapter, description: str, level: str | None = None) -> str:
    did = await upsert_decision(
        adapter._client,
        description=description,
        source_type="manual",
        source_ref="cli-test",
    )
    if level is not None:
        await update_decision_level(adapter._client, did, level)
    return did


@pytest.mark.asyncio
async def test_dry_run_lists_unclassified_decisions(adapter):
    d1 = await _seed(adapter, "Members can pause their subscription.")
    d2 = await _seed(adapter, "Use Redis-backed sessions for scaling.")
    d3 = await _seed(adapter, "Users can export data as CSV.")
    await _seed(adapter, "Already classified L1.", level="L1")
    await _seed(adapter, "Already classified L2.", level="L2")

    out = io.StringIO()
    rc = await _run(apply_changes=False, out=out, adapter=adapter)
    assert rc == 0
    text = out.getvalue()
    assert d1 in text
    assert d2 in text
    assert d3 in text
    assert "Already classified" not in text  # classified rows not surfaced
    assert "Dry run" in text


@pytest.mark.asyncio
async def test_dry_run_does_not_mutate_ledger(adapter):
    d1 = await _seed(adapter, "Members can pause their subscription.")
    d2 = await _seed(adapter, "Use Redis-backed sessions for scaling.")

    out = io.StringIO()
    rc = await _run(apply_changes=False, out=out, adapter=adapter)
    assert rc == 0

    for did in (d1, d2):
        level = await get_decision_level(adapter._client, did)
        assert level is None, f"row {did} mutated by dry-run: level={level!r}"


@pytest.mark.asyncio
async def test_apply_writes_proposed_levels(adapter):
    d1 = await _seed(adapter, "Members can pause their subscription.")
    d2 = await _seed(adapter, "Use Redis-backed sessions for scaling.")
    d3 = await _seed(adapter, "We will ship offline mode by Q3.")

    out = io.StringIO()
    rc = await _run(apply_changes=True, out=out, adapter=adapter)
    assert rc == 0
    assert "Applied 3 classifications" in out.getvalue()

    assert await get_decision_level(adapter._client, d1) == "L1"
    assert await get_decision_level(adapter._client, d2) == "L2"
    assert await get_decision_level(adapter._client, d3) == "L3"


@pytest.mark.asyncio
async def test_apply_skips_already_classified(adapter):
    d_classified = await _seed(adapter, "Already classified L3.", level="L3")
    d_new = await _seed(adapter, "Members can pause their subscription.")

    out = io.StringIO()
    rc = await _run(apply_changes=True, out=out, adapter=adapter)
    assert rc == 0

    # The pre-classified row keeps its original level (not overwritten).
    assert await get_decision_level(adapter._client, d_classified) == "L3"
    # The previously-unclassified row got classified (L1 here).
    assert await get_decision_level(adapter._client, d_new) == "L1"


@pytest.mark.asyncio
async def test_low_confidence_proposals_marked_in_output(adapter):
    await _seed(adapter, "stuff happens here")  # no signal -> low confidence
    await _seed(adapter, "Members can pause their subscription.")  # high

    out = io.StringIO()
    rc = await _run(apply_changes=False, out=out, adapter=adapter)
    assert rc == 0
    text = out.getvalue()
    assert "(low confidence)" in text


@pytest.mark.asyncio
async def test_cli_exit_code_zero_on_success_nonzero_on_ledger_error(adapter, monkeypatch):
    """Success path exits 0; when adapter.connect raises, exits non-zero."""
    await _seed(adapter, "Members can pause their subscription.")

    out = io.StringIO()
    assert await _run(apply_changes=False, out=out, adapter=adapter) == 0

    # Now monkey-patch SurrealDBLedgerAdapter.connect to raise — and pass
    # adapter=None so _run owns adapter creation/connect.
    async def _boom(self):
        raise RuntimeError("ledger unreachable")

    monkeypatch.setattr(SurrealDBLedgerAdapter, "connect", _boom)
    out2 = io.StringIO()
    rc = await _run(apply_changes=False, out=out2, adapter=None)
    assert rc != 0
    assert "ledger unreachable" in out2.getvalue()


@pytest.mark.asyncio
async def test_cli_progress_output_for_large_batch(adapter):
    """When > 100 rows, progress messages print every 100 rows during --apply."""
    for i in range(105):
        await _seed(adapter, f"Members can do thing number {i}.")

    out = io.StringIO()
    rc = await _run(apply_changes=True, out=out, adapter=adapter)
    assert rc == 0
    text = out.getvalue()
    assert "...applied 100/" in text


def test_main_argparse_dry_run_default(monkeypatch):
    """``main([])`` runs dry-run path and exits 0 against an empty ledger."""
    captured = io.StringIO()
    # Patch sys.stdout AT the cli.classify module level — that's what _run
    # captures via ``sys.stdout``-default at call time.
    monkeypatch.setattr("sys.stdout", captured)
    rc = main([])
    assert rc == 0
    assert "Dry run" in captured.getvalue()
