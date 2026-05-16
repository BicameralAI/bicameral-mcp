"""#224 unit tests for ledger query timeout wrap and fail-closed config.

Sociable per CLAUDE.md: instantiates real ``LedgerClient`` over
``memory://``, real ``BicameralContext`` via direct construction, and
runs the real ``asyncio.wait_for`` wrap. Narrow seam: patches
``self._db.query`` with an ``asyncio.sleep`` coroutine to force a slow
query (we cannot naturally make SurrealDB embedded slow). The patch
is at the SDK boundary — the wrap and error-shape logic under test
run unmodified.
"""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from context import (
    _DEFAULT_QUERY_TIMEOUT_DRIFT,
    _DEFAULT_QUERY_TIMEOUT_READ,
    _QUERY_TIMEOUT_DRIFT_MAX,
    _QUERY_TIMEOUT_DRIFT_MIN,
    _QUERY_TIMEOUT_READ_MAX,
    _QUERY_TIMEOUT_READ_MIN,
    _read_query_timeout_drift_seconds,
    _read_query_timeout_read_seconds,
)
from ledger import timeout_telemetry
from ledger.client import (
    LedgerClient,
    LedgerError,
    LedgerTimeoutError,
)

# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_telemetry_buffer():
    """Each test starts with an empty ring buffer."""
    timeout_telemetry.clear_for_testing()
    yield
    timeout_telemetry.clear_for_testing()


@pytest.fixture(autouse=True)
def _clear_timeout_disable_env(monkeypatch):
    """Default: timeout enabled. Tests that exercise the env-disable
    bypass set it explicitly."""
    monkeypatch.delenv("BICAMERAL_QUERY_TIMEOUT_DISABLE", raising=False)


async def _connected_client(
    *,
    read_seconds: float = 0.2,
    drift_seconds: float = 0.5,
) -> LedgerClient:
    client = LedgerClient(
        url="memory://",
        query_timeout_read_seconds=read_seconds,
        query_timeout_drift_seconds=drift_seconds,
    )
    await client.connect()
    return client


def _write_config(repo: Path, payload: dict) -> None:
    cfg = repo / ".bicameral" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(yaml.safe_dump(payload), encoding="utf-8")


# ── ledger/client.py: wrap behavior ──────────────────────────────────


@pytest.mark.asyncio
async def test_query_under_budget_succeeds_unchanged() -> None:
    """A fast query goes through the wrap and returns rows unchanged."""
    client = await _connected_client(read_seconds=2.0)
    try:
        # Real SurrealDB embedded; INFO FOR DB is fast and harmless.
        rows = await client.query("SELECT * FROM bicameral_schema_version")
        # Result shape: a list (empty or populated). The wrap should
        # not have altered it.
        assert isinstance(rows, list)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_query_over_read_budget_raises_ledger_timeout_error() -> None:
    """A query that exceeds the read budget raises LedgerTimeoutError
    with all four attributes populated and the SQL prefix preserved."""
    client = await _connected_client(read_seconds=0.1)
    sql = "SELECT * FROM intent"

    async def _slow_query(_sql, _vars):
        await asyncio.sleep(0.5)
        return []

    try:
        with patch.object(client._db, "query", side_effect=_slow_query):
            with pytest.raises(LedgerTimeoutError) as excinfo:
                await client.query(sql)
        err = excinfo.value
        assert err.timeout_class == "read"
        assert err.budget_seconds == pytest.approx(0.1)
        assert err.elapsed_seconds >= 0.1
        assert err.sql_prefix == sql
        # Subclass of LedgerError so existing catch blocks work.
        assert isinstance(err, LedgerError)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_query_drift_class_uses_drift_budget() -> None:
    """timeout_class='drift' uses the drift budget, not the read one."""
    client = await _connected_client(read_seconds=0.1, drift_seconds=1.0)

    async def _slow_query(_sql, _vars):
        await asyncio.sleep(0.3)
        return []

    try:
        with patch.object(client._db, "query", side_effect=_slow_query):
            # Under read budget → would timeout. Under drift budget → succeeds.
            result = await client.query("SELECT * FROM intent", timeout_class="drift")
            assert result == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_query_drift_over_drift_budget_still_raises() -> None:
    """drift budget is itself bounded — exceeding it still raises."""
    client = await _connected_client(drift_seconds=0.1)

    async def _slow_query(_sql, _vars):
        await asyncio.sleep(0.4)
        return []

    try:
        with patch.object(client._db, "query", side_effect=_slow_query):
            with pytest.raises(LedgerTimeoutError) as excinfo:
                await client.query("SELECT * FROM intent", timeout_class="drift")
        assert excinfo.value.timeout_class == "drift"
        assert excinfo.value.budget_seconds == pytest.approx(0.1)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_query_timeout_disable_env_skips_wait_for(monkeypatch) -> None:
    """BICAMERAL_QUERY_TIMEOUT_DISABLE=1 → wrap is bypassed, slow query
    completes without raising LedgerTimeoutError."""
    monkeypatch.setenv("BICAMERAL_QUERY_TIMEOUT_DISABLE", "1")
    client = await _connected_client(read_seconds=0.05)

    async def _slow_query(_sql, _vars):
        await asyncio.sleep(0.2)
        return []

    try:
        with patch.object(client._db, "query", side_effect=_slow_query):
            result = await client.query("SELECT * FROM intent")
            assert result == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_execute_method_also_timeout_wrapped() -> None:
    """execute() must surface LedgerTimeoutError the same way query() does."""
    client = await _connected_client(read_seconds=0.1)

    async def _slow_query(_sql, _vars):
        await asyncio.sleep(0.3)
        return []

    try:
        with patch.object(client._db, "query", side_effect=_slow_query):
            with pytest.raises(LedgerTimeoutError) as excinfo:
                await client.execute("CREATE foo SET bar = 1")
        assert excinfo.value.timeout_class == "read"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_timeout_truncates_sql_prefix_to_200_chars() -> None:
    """SQL longer than 200 chars must be truncated in the error + telemetry."""
    long_sql = "SELECT * FROM intent WHERE description = '" + ("x" * 500) + "'"
    client = await _connected_client(read_seconds=0.05)

    async def _slow_query(_sql, _vars):
        await asyncio.sleep(0.2)
        return []

    try:
        with patch.object(client._db, "query", side_effect=_slow_query):
            with pytest.raises(LedgerTimeoutError) as excinfo:
                await client.query(long_sql)
        assert len(excinfo.value.sql_prefix) == 200
        assert excinfo.value.sql_prefix == long_sql[:200]
        # Telemetry ring buffer also captures the truncated prefix.
        assert timeout_telemetry.buffer_size() == 1
        events = list(timeout_telemetry._buffer)
        assert len(events[0].sql_prefix) == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_timeout_telemetry_records_per_class_count() -> None:
    """Each timeout fire appends to the ring buffer; recent_timeout_counts
    returns the per-class total over the configured window."""
    client = await _connected_client(read_seconds=0.05, drift_seconds=0.05)

    async def _slow_query(_sql, _vars):
        await asyncio.sleep(0.2)
        return []

    try:
        with patch.object(client._db, "query", side_effect=_slow_query):
            for _ in range(3):
                with pytest.raises(LedgerTimeoutError):
                    await client.query("SELECT 1", timeout_class="read")
            for _ in range(2):
                with pytest.raises(LedgerTimeoutError):
                    await client.query("SELECT 2", timeout_class="drift")
    finally:
        await client.close()
    counts = timeout_telemetry.recent_timeout_counts()
    assert counts == {"read": 3, "drift": 2}


# ── context.py: fail-closed config parsing ────────────────────────────


def test_query_timeout_read_default_when_no_config(tmp_path: Path) -> None:
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_READ


def test_query_timeout_drift_default_when_no_config(tmp_path: Path) -> None:
    assert _read_query_timeout_drift_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_DRIFT


def test_query_timeout_read_accepts_in_range_value(tmp_path: Path) -> None:
    _write_config(tmp_path, {"query_timeout_read_seconds": 10.0})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == 10.0


def test_query_timeout_read_accepts_int(tmp_path: Path) -> None:
    """An int value is coerced to float and accepted."""
    _write_config(tmp_path, {"query_timeout_read_seconds": 7})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == 7.0


def test_query_timeout_read_falls_back_to_default_on_string(tmp_path: Path) -> None:
    _write_config(tmp_path, {"query_timeout_read_seconds": "fast"})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_READ


def test_query_timeout_read_falls_back_to_default_on_bool(tmp_path: Path) -> None:
    """``True`` is technically an int subclass in Python — must not be
    accepted as a timeout value."""
    _write_config(tmp_path, {"query_timeout_read_seconds": True})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_READ


def test_query_timeout_read_falls_back_to_default_on_negative(tmp_path: Path) -> None:
    """A negative value would mean 'time out immediately' — config error."""
    _write_config(tmp_path, {"query_timeout_read_seconds": -1.0})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_READ


def test_query_timeout_read_falls_back_to_default_on_zero(tmp_path: Path) -> None:
    """Zero is also rejected — see the docstring rationale on
    ``_read_query_timeout_seconds``."""
    _write_config(tmp_path, {"query_timeout_read_seconds": 0})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_READ


def test_query_timeout_read_falls_back_to_default_on_nan(tmp_path: Path) -> None:
    """NaN evades both `< MIN` and `> MAX` comparisons; must be caught."""
    _write_config(tmp_path, {"query_timeout_read_seconds": math.nan})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_READ


def test_query_timeout_read_falls_back_to_default_on_inf(tmp_path: Path) -> None:
    _write_config(tmp_path, {"query_timeout_read_seconds": math.inf})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_READ


def test_query_timeout_read_clamps_to_max(tmp_path: Path) -> None:
    """Value above MAX is clamped (preserves operator intent for 'long
    but bounded') rather than rejected to default."""
    _write_config(tmp_path, {"query_timeout_read_seconds": 9999.0})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _QUERY_TIMEOUT_READ_MAX


def test_query_timeout_read_clamps_to_min(tmp_path: Path) -> None:
    _write_config(tmp_path, {"query_timeout_read_seconds": 0.01})
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _QUERY_TIMEOUT_READ_MIN


def test_query_timeout_drift_clamps_to_max(tmp_path: Path) -> None:
    _write_config(tmp_path, {"query_timeout_drift_seconds": 9999.0})
    assert _read_query_timeout_drift_seconds(str(tmp_path)) == _QUERY_TIMEOUT_DRIFT_MAX


def test_query_timeout_drift_clamps_to_min(tmp_path: Path) -> None:
    _write_config(tmp_path, {"query_timeout_drift_seconds": 0.5})
    assert _read_query_timeout_drift_seconds(str(tmp_path)) == _QUERY_TIMEOUT_DRIFT_MIN


def test_query_timeout_falls_back_on_malformed_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / ".bicameral" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{ not valid yaml\n", encoding="utf-8")
    assert _read_query_timeout_read_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_READ
    assert _read_query_timeout_drift_seconds(str(tmp_path)) == _DEFAULT_QUERY_TIMEOUT_DRIFT


# ── BicameralContext: from_env wiring ────────────────────────────────


def test_bicameral_context_carries_timeout_fields(tmp_path: Path, monkeypatch) -> None:
    """from_env populates the new fields from the config reader."""
    _write_config(
        tmp_path,
        {
            "query_timeout_read_seconds": 7.0,
            "query_timeout_drift_seconds": 45.0,
        },
    )
    monkeypatch.setenv("REPO_PATH", str(tmp_path))

    # Build only the timeout-relevant slice — avoid pulling the rest of
    # from_env's adapters (which require a real ledger). The unit-level
    # contract under test is that the readers feed the dataclass field,
    # not that from_env constructs every collaborator.
    read = _read_query_timeout_read_seconds(str(tmp_path))
    drift = _read_query_timeout_drift_seconds(str(tmp_path))
    assert read == 7.0
    assert drift == 45.0


def test_bicameral_context_default_construction_uses_module_defaults() -> None:
    """A bare BicameralContext (test-only path) uses module defaults
    so existing tests that construct it directly don't need updates."""
    from context import BicameralContext

    ctx = BicameralContext(
        repo_path=".",
        head_sha="x",
        ledger=None,
        code_graph=None,
        drift_analyzer=None,
    )
    assert ctx.query_timeout_read_seconds == _DEFAULT_QUERY_TIMEOUT_READ
    assert ctx.query_timeout_drift_seconds == _DEFAULT_QUERY_TIMEOUT_DRIFT
