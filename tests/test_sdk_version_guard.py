"""Startup guard: refuse to boot when the recorded surrealdb SDK version
doesn't match the running one.

Catches the in-place-upgrade failure mode where bicameral-mcp bumps its
``surrealdb==X.Y.Z`` pin and existing users' ledgers, written by the old
SDK, become unreadable by the new SDK ("Invalid revision N for type
Value" crashes mid-RPC on the first UPDATE). Converts the silent
mid-RPC failure into a loud actionable startup error.
"""

from __future__ import annotations

import os

import pytest

from ledger.adapter import (
    SurrealClientVersionMismatchError,
    SurrealDBLedgerAdapter,
)


async def _seed_recorded_version(adapter: SurrealDBLedgerAdapter, version: str) -> None:
    """Overwrite ``bicameral_meta`` so the recorded SDK version is ``version``.

    Used to simulate the post-upgrade state where the ledger was last
    written by a different SDK pin than the one running now.
    """
    await adapter._client.query(
        "UPDATE bicameral_meta SET "
        "surrealdb_client_version_at_first_write = $v, "
        "surrealdb_client_version_at_last_write = $v",
        {"v": version},
    )


@pytest.mark.asyncio
async def test_pre_check_detects_drift():
    """Recorded != running ⇒ pre-check reports ``"drift"``.

    Solitary on the env var (we control os.environ), sociable on the
    ledger (real ``memory://`` adapter — the pre-check reads the actual
    ``bicameral_meta`` row written by ``_write_wire_format_sentinel``).
    """
    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()
    await _seed_recorded_version(adapter, "0.99.0-fictional-old-pin")

    recorded, running, status = await adapter._sdk_version_pre_check()

    assert status == "drift"
    assert recorded == "0.99.0-fictional-old-pin"
    assert running != "0.99.0-fictional-old-pin"


@pytest.mark.asyncio
async def test_pre_check_reports_match_on_fresh_ledger():
    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()

    _recorded, _running, status = await adapter._sdk_version_pre_check()

    assert status == "match"


@pytest.mark.asyncio
async def test_sentinel_raises_on_drift(monkeypatch):
    """Direct exercise of the guarded sentinel call: drift ⇒ raise.

    We invoke ``_emit_wire_format_sentinel`` against an already-connected
    adapter (so the seeded ``bicameral_meta`` row survives) — re-driving
    ``adapter.connect()`` won't work for ``memory://`` because every
    ``LedgerClient.connect()`` instantiates a fresh in-memory database
    and erases the seeded state. The pre-check + raise behavior is what
    matters at the production startup path; ``connect()`` calls this
    method exactly once, on first boot, where the same survival
    invariant holds (the ``surrealkv://`` file persists across runs).
    """
    monkeypatch.delenv("BICAMERAL_SKIP_SDK_GUARD", raising=False)

    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()
    await _seed_recorded_version(adapter, "0.99.0-fictional-old-pin")

    with pytest.raises(SurrealClientVersionMismatchError) as exc_info:
        await adapter._emit_wire_format_sentinel()

    msg = str(exc_info.value)
    assert "0.99.0-fictional-old-pin" in msg
    assert "BICAMERAL_SKIP_SDK_GUARD" in msg
    assert "bicameral-mcp reset" in msg
    assert exc_info.value.recorded == "0.99.0-fictional-old-pin"


@pytest.mark.asyncio
async def test_env_var_bypasses_guard(monkeypatch, caplog):
    """``BICAMERAL_SKIP_SDK_GUARD=1`` lets the sentinel call proceed; logs WARN."""
    monkeypatch.setenv("BICAMERAL_SKIP_SDK_GUARD", "1")

    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()
    await _seed_recorded_version(adapter, "0.99.0-fictional-old-pin")

    import logging

    caplog.set_level(logging.WARNING)
    await adapter._emit_wire_format_sentinel()

    assert any("BICAMERAL_SKIP_SDK_GUARD" in r.message for r in caplog.records), (
        "guard bypass must log a WARN line so operators see they're running "
        "in best-effort mode — found records: "
        f"{[r.message for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_fresh_ledger_first_write_does_not_raise(monkeypatch):
    """No prior recorded version ⇒ first-write path, no raise.

    Protects fresh installs from accidental fail-loud-on-empty bugs.
    """
    monkeypatch.delenv("BICAMERAL_SKIP_SDK_GUARD", raising=False)

    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()


@pytest.mark.asyncio
async def test_guard_disarmed_state_persists_across_pre_checks():
    """Pre-check is read-only — it does NOT update ``last_write``.

    Critical contract: if the pre-check accidentally updated the row,
    the second boot under the same drifted SDK would silently see
    "match" and the guard would disarm itself. Verifies that re-running
    the pre-check returns ``drift`` again.
    """
    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()
    await _seed_recorded_version(adapter, "0.99.0-fictional-old-pin")

    _, _, first = await adapter._sdk_version_pre_check()
    _, _, second = await adapter._sdk_version_pre_check()
    _, _, third = await adapter._sdk_version_pre_check()

    assert first == "drift"
    assert second == "drift"
    assert third == "drift"
