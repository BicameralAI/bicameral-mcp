"""Functional tests for the bicameral_meta wire-format sentinel (#252 Layer 2).

Each test invokes the unit under test (`_write_wire_format_sentinel` or
`adapter.connect()`) and asserts on returned values, raised exceptions,
captured emit calls, or persisted row contents. No presence-only
descriptions.
"""

from __future__ import annotations

import pytest

import audit_log
from ledger import schema as schema_mod
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import _write_wire_format_sentinel, init_schema


@pytest.fixture
async def fresh_client():
    client = LedgerClient("memory://")
    await client.connect()
    await init_schema(client)
    yield client
    await client.close()


@pytest.fixture(autouse=True)
def _reset_audit_log_state(monkeypatch):
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG", raising=False)
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG_LEVEL", raising=False)
    audit_log._reset_for_tests()
    yield
    audit_log._reset_for_tests()


async def test_write_wire_format_sentinel_creates_row_on_empty_table(fresh_client):
    recorded, running, status = await _write_wire_format_sentinel(fresh_client)
    assert status == "first-write"
    assert recorded is None
    assert running  # non-empty
    rows = await fresh_client.query("SELECT * FROM bicameral_meta")
    assert len(rows) == 1
    row = rows[0]
    assert row["surrealdb_client_version_at_first_write"] == running
    assert row["surrealdb_client_version_at_last_write"] == running
    assert row["last_write_at"] is not None


async def test_write_wire_format_sentinel_returns_running_version_from_importlib_metadata(
    monkeypatch, fresh_client
):
    monkeypatch.setattr(
        "importlib.metadata.version", lambda pkg: "2.0.0-test" if pkg == "surrealdb" else "x"
    )
    recorded, running, status = await _write_wire_format_sentinel(fresh_client)
    assert running == "2.0.0-test"
    rows = await fresh_client.query("SELECT * FROM bicameral_meta")
    assert rows[0]["surrealdb_client_version_at_first_write"] == "2.0.0-test"


async def test_write_wire_format_sentinel_runs_unknown_branch_when_package_missing(
    monkeypatch, fresh_client
):
    import importlib.metadata as ilm

    def _raise(pkg):
        raise ilm.PackageNotFoundError(pkg)

    monkeypatch.setattr("importlib.metadata.version", _raise)
    recorded, running, status = await _write_wire_format_sentinel(fresh_client)
    assert running == "unknown"
    rows = await fresh_client.query("SELECT * FROM bicameral_meta")
    assert rows[0]["surrealdb_client_version_at_first_write"] == "unknown"


async def test_write_wire_format_sentinel_preserves_first_write_on_subsequent_calls(
    monkeypatch, fresh_client
):
    monkeypatch.setattr("importlib.metadata.version", lambda pkg: "2.0.0")
    await _write_wire_format_sentinel(fresh_client)
    monkeypatch.setattr("importlib.metadata.version", lambda pkg: "2.1.0")
    await _write_wire_format_sentinel(fresh_client)
    rows = await fresh_client.query("SELECT * FROM bicameral_meta")
    assert rows[0]["surrealdb_client_version_at_first_write"] == "2.0.0"
    assert rows[0]["surrealdb_client_version_at_last_write"] == "2.1.0"


async def test_write_wire_format_sentinel_returns_match_when_versions_equal(
    monkeypatch, fresh_client
):
    monkeypatch.setattr("importlib.metadata.version", lambda pkg: "2.0.0")
    await _write_wire_format_sentinel(fresh_client)  # first-write
    recorded, running, status = await _write_wire_format_sentinel(fresh_client)
    assert status == "match"
    assert recorded == "2.0.0"
    assert running == "2.0.0"


async def test_write_wire_format_sentinel_returns_drift_when_versions_differ(
    monkeypatch, fresh_client
):
    monkeypatch.setattr("importlib.metadata.version", lambda pkg: "2.0.0")
    await _write_wire_format_sentinel(fresh_client)  # first-write at 2.0.0
    monkeypatch.setattr("importlib.metadata.version", lambda pkg: "2.1.0")
    recorded, running, status = await _write_wire_format_sentinel(fresh_client)
    assert status == "drift"
    assert recorded == "2.0.0"
    assert running == "2.1.0"


async def test_write_wire_format_sentinel_returns_first_write_when_at_first_write_is_none_but_row_exists(
    monkeypatch, fresh_client
):
    # Pre-populate a partial row: at_last_write set but at_first_write None.
    await fresh_client.query(
        "CREATE bicameral_meta SET "
        "surrealdb_client_version_at_first_write = NONE, "
        "surrealdb_client_version_at_last_write = $r, "
        "last_write_at = time::now()",
        {"r": "2.0.0"},
    )
    monkeypatch.setattr("importlib.metadata.version", lambda pkg: "2.0.0")
    recorded, running, status = await _write_wire_format_sentinel(fresh_client)
    assert status == "first-write"
    rows = await fresh_client.query("SELECT * FROM bicameral_meta")
    assert rows[0]["surrealdb_client_version_at_first_write"] == "2.0.0"


async def test_adapter_connect_emits_ledger_schema_verified_on_first_write(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(audit_log, "emit", lambda et, **kw: calls.append((et, kw)))
    adapter = SurrealDBLedgerAdapter("memory://")
    await adapter.connect()
    schema_verified = [c for c in calls if c[0] == audit_log.AuditEventType.LEDGER_SCHEMA_VERIFIED]
    assert len(schema_verified) == 1
    assert schema_verified[0][1]["status"] == "first-write"
    assert schema_verified[0][1]["surrealdb_client_version_running"]
    await adapter._client.close()


async def test_adapter_connect_emits_ledger_version_drift_on_recorded_mismatch(monkeypatch):
    adapter = SurrealDBLedgerAdapter("memory://")
    await adapter.connect()  # first connect populates the row at running version
    # Force a drift by manually rewriting the recorded version, then re-connect via a
    # second adapter pointing at a freshly seeded ledger that already has a different
    # recorded version.
    await adapter._client.query(
        "UPDATE bicameral_meta SET surrealdb_client_version_at_last_write = '0.0.0-old', "
        "surrealdb_client_version_at_first_write = '0.0.0-old'"
    )
    calls: list[tuple] = []
    monkeypatch.setattr(audit_log, "emit", lambda et, **kw: calls.append((et, kw)))
    # Trigger _emit_wire_format_sentinel directly (re-running connect won't fire it
    # because self._connected is already True; in production the next process startup
    # would re-trigger via a fresh adapter against the same SurrealKV file).
    await adapter._emit_wire_format_sentinel()
    drift = [c for c in calls if c[0] == audit_log.AuditEventType.LEDGER_VERSION_DRIFT]
    assert len(drift) == 1
    assert drift[0][1]["surrealdb_client_version_recorded"] == "0.0.0-old"
    assert drift[0][1]["surrealdb_client_version_running"]
    await adapter._client.close()


async def test_adapter_connect_audit_log_emit_failure_does_not_break_connect(monkeypatch):
    def _explode(*a, **kw):
        raise RuntimeError("audit_log surface failure")

    monkeypatch.setattr(audit_log, "emit", _explode)
    adapter = SurrealDBLedgerAdapter("memory://")
    await adapter.connect()  # MUST NOT raise
    assert adapter._connected is True
    rows = await adapter._client.query("SELECT * FROM bicameral_meta")
    assert len(rows) == 1  # sentinel row was still written
    await adapter._client.close()


async def test_adapter_connect_sentinel_helper_failure_does_not_break_connect(monkeypatch):
    async def _explode(client):
        raise RuntimeError("sentinel helper failure")

    monkeypatch.setattr(schema_mod, "_write_wire_format_sentinel", _explode)
    # Re-import path to ensure adapter sees the patched helper:
    import ledger.adapter as adapter_mod

    monkeypatch.setattr(adapter_mod, "_write_wire_format_sentinel", _explode)
    adapter = SurrealDBLedgerAdapter("memory://")
    await adapter.connect()  # MUST NOT raise
    assert adapter._connected is True
    await adapter._client.close()
