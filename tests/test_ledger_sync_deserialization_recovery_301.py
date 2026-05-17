"""Tests for #301 — ledger_sync row-format deserialization recovery routing.

Three behaviours under test (sociable where it counts, narrow seam where the
failure mode is a SurrealKV record-revision mismatch that can't be triggered
naturally):

1. ``ledger.client.query`` / ``ledger.client.execute`` raise
   ``LedgerDeserializationError`` (subclass of ``LedgerError``) — not the
   generic ``LedgerError`` — when SurrealDB returns a record-format
   mismatch. The new exception's message embeds the recovery hint.
2. ``handlers.diagnose._classify_recovery`` returns
   ``reset_rebuild`` / ``reset_destructive`` (not ``clean``) when the
   ``Diagnosis.row_probe_warnings`` field is non-empty, even if
   ``schema_version_recorded == schema_version_expected``. The next_action
   quotes ``bicameral_reset(...)``.
3. ``handlers.sync_middleware.ensure_ledger_synced`` re-raises
   ``LedgerDeserializationError`` instead of swallowing it at DEBUG, so the
   agent sees the recovery hint via the MCP error envelope.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

try:
    from surrealdb import SurrealError
except ImportError:  # pragma: no cover — pinned SDK exposes it, fallback mirrors client.py
    SurrealError = Exception  # type: ignore[assignment,misc]

from cli.diagnose import Diagnosis
from handlers.diagnose import _classify_recovery
from handlers.sync_middleware import _reset_repo_locks_for_tests, ensure_ledger_synced
from ledger.client import (
    LedgerClient,
    LedgerDeserializationError,
    LedgerError,
    _is_deserialization_error,
)
from ledger.schema import SCHEMA_VERSION, init_schema, migrate

# ── 1. ledger.client classification ────────────────────────────────────────


def test_is_deserialization_error_matches_invalid_revision():
    assert _is_deserialization_error("Invalid revision `3` for type `Value`")


def test_is_deserialization_error_matches_wrapper_text():
    assert _is_deserialization_error("Versioned error: A deserialization error occured: ...")


def test_is_deserialization_error_does_not_match_unrelated_errors():
    assert not _is_deserialization_error("UNIQUE constraint violation on idx_x")
    assert not _is_deserialization_error("ASSERT failed on field text")


def test_ledger_deserialization_error_is_subclass_of_ledger_error():
    # Existing `except LedgerError` handlers must still catch the new class.
    err = LedgerDeserializationError(raw="Invalid revision `3`", sql_prefix="SELECT 1")
    assert isinstance(err, LedgerError)


def test_ledger_deserialization_error_message_embeds_recovery_hint():
    err = LedgerDeserializationError(raw="Invalid revision `3`", sql_prefix="SELECT 1")
    msg = str(err)
    assert "bicameral_reset" in msg
    assert "replay_from_events=True" in msg
    assert "Invalid revision `3`" in msg


@pytest.mark.asyncio
async def test_query_raises_deserialization_error_when_surrealdb_complains():
    """The classifier triggers on a real LedgerClient.query() path.

    Narrow seam: we patch the surrealdb-py async call so it raises
    ``SurrealError("Invalid revision ...")`` — this is the documented failure
    mode for SurrealKV record-format drift and cannot be triggered naturally
    against ``memory://``.
    """
    client = LedgerClient(url="memory://", ns="t301_q", db="ledger_test")
    await client.connect()
    try:
        boom = SurrealError("Invalid revision `3` for type `Value`")
        with patch.object(client._db, "query", AsyncMock(side_effect=boom)):
            with pytest.raises(LedgerDeserializationError) as ei:
                await client.query("SELECT * FROM ledger_sync WHERE repo = 'x' LIMIT 1")
        assert "bicameral_reset" in str(ei.value)
        assert ei.value.sql_prefix.startswith("SELECT * FROM ledger_sync")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_query_with_non_deserialization_error_still_raises_plain_ledger_error():
    """Unrelated SurrealErrors must not be reclassified as deserialization."""
    client = LedgerClient(url="memory://", ns="t301_q2", db="ledger_test")
    await client.connect()
    try:
        boom = SurrealError("Some other constraint failure")
        with patch.object(client._db, "query", AsyncMock(side_effect=boom)):
            with pytest.raises(LedgerError) as ei:
                await client.query("SELECT * FROM ledger_sync")
        assert not isinstance(ei.value, LedgerDeserializationError)
    finally:
        await client.close()


# ── 2. _classify_recovery routing on row_probe_warnings ────────────────────


def _stub_diagnosis(
    *,
    row_warnings: list[str],
    schema_rec: int | None = None,
    schema_exp: int | None = None,
    ledger_url: str = "memory://",
) -> Diagnosis:
    """Minimal Diagnosis fixture — only the fields _classify_recovery reads.

    Diagnosis is a frozen dataclass; the other fields are populated with
    type-correct defaults so the constructor accepts the row.
    """
    return Diagnosis(
        bicameral_version="0.15.1",
        python_version="3.11.0",
        platform_str="test",
        surrealdb_running="2.0.0",
        ledger_url=ledger_url,
        ledger_size_bytes=None,
        ledger_mtime_iso=None,
        schema_version_recorded=schema_rec if schema_rec is not None else SCHEMA_VERSION,
        schema_version_expected=schema_exp if schema_exp is not None else SCHEMA_VERSION,
        surrealdb_first_write=None,
        surrealdb_last_write=None,
        last_write_at=None,
        drift_status="match",
        audit_log_channel="stderr",
        table_counts={"decision": 0},
        row_probe_warnings=row_warnings,
        recent_events=[],
        suggestions=[],
    )


def test_classify_recovery_returns_reset_destructive_when_row_warnings_present_no_events():
    d = _stub_diagnosis(
        row_warnings=["ledger_sync: SurrealError: Invalid revision `3` for type `Value`"],
    )
    path, next_action = _classify_recovery(d)
    assert path == "reset_destructive"
    assert "bicameral_reset" in next_action
    assert "replay_from_events=False" in next_action
    assert "ledger_sync" in next_action


def test_classify_recovery_returns_reset_rebuild_when_events_present(tmp_path, monkeypatch):
    """Same row_warnings, but events/*.jsonl exists next to the ledger db file.

    ``_events_present`` resolves the events dir as ``<db_path>.parent / events``
    (handlers/diagnose.py:174-184), so the fixture lays the file under
    ``<bicameral_dir>/events/`` and points the ledger URL at
    ``<bicameral_dir>/ledger.db``.
    """
    bicameral_dir = tmp_path / ".bicameral"
    events_dir = bicameral_dir / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "0001.jsonl").write_text("{}\n")
    ledger_url = f"surrealkv://{bicameral_dir}/ledger.db"
    d = _stub_diagnosis(
        row_warnings=["ledger_sync: SurrealError: Invalid revision `3`"],
        ledger_url=ledger_url,
    )
    path, next_action = _classify_recovery(d)
    assert path == "reset_rebuild"
    assert "replay_from_events=True" in next_action


def test_classify_recovery_row_warnings_outrank_clean_schema():
    """Schema matches exactly but rows are broken → must NOT return 'clean'."""
    d = _stub_diagnosis(
        row_warnings=["ledger_sync: SurrealError: Invalid revision `3`"],
        schema_rec=SCHEMA_VERSION,
        schema_exp=SCHEMA_VERSION,
    )
    path, _ = _classify_recovery(d)
    assert path != "clean"


def test_classify_recovery_no_row_warnings_stays_clean():
    """Regression guard — happy-path schema_rec == schema_exp still returns 'clean'."""
    d = _stub_diagnosis(row_warnings=[])
    path, next_action = _classify_recovery(d)
    assert path == "clean"
    assert "No remediation needed" in next_action


# ── 3. ensure_ledger_synced re-raises instead of swallowing ────────────────


@pytest.mark.asyncio
async def test_ensure_ledger_synced_reraises_deserialization_error():
    """Previously: broad ``except Exception`` swallowed this to DEBUG, masking
    the failure. After #301: LedgerDeserializationError surfaces to the
    caller so the MCP transport renders the recovery hint to the agent.
    """
    _reset_repo_locks_for_tests()
    ctx = SimpleNamespace(repo_path=".")
    boom = LedgerDeserializationError(
        raw="Invalid revision `3` for type `Value`",
        sql_prefix="SELECT * FROM ledger_sync",
    )
    with (
        patch(
            "handlers.link_commit._read_current_head_sha",
            return_value="deadbeef" * 5,
        ),
        patch(
            "handlers.link_commit.handle_link_commit",
            AsyncMock(side_effect=boom),
        ),
    ):
        with pytest.raises(LedgerDeserializationError) as ei:
            await ensure_ledger_synced(ctx)
    assert "bicameral_reset" in str(ei.value)


@pytest.mark.asyncio
async def test_ensure_ledger_synced_still_swallows_unrelated_errors():
    """Non-deserialization failures stay isolated — the prior broad-catch
    contract still applies to keep the catch-up best-effort."""
    _reset_repo_locks_for_tests()
    ctx = SimpleNamespace(repo_path=".")
    with (
        patch(
            "handlers.link_commit._read_current_head_sha",
            return_value="cafebabe" * 5,
        ),
        patch(
            "handlers.link_commit.handle_link_commit",
            AsyncMock(side_effect=RuntimeError("transient io")),
        ),
    ):
        # Should NOT raise — broad exception is still swallowed.
        result = await ensure_ledger_synced(ctx)
    assert result is None
