"""Functional tests for audit_log lifecycle hooks (#227 Phase 2).

server_start / server_shutdown / config_load events. Each test invokes
the relevant entry point and asserts on captured emit calls.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr

import pytest

import audit_log
import context as context_mod
from audit_log import AuditEventType


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG", raising=False)
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG_LEVEL", raising=False)
    audit_log._reset_for_tests()
    context_mod._config_load_emitted = False
    yield
    audit_log._reset_for_tests()
    context_mod._config_load_emitted = False


def _captured_emit_calls(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []

    def _stub(event_type, **kwargs):
        calls.append((event_type, kwargs))

    monkeypatch.setattr(audit_log, "emit", _stub)
    return calls


def test_serve_stdio_emits_server_start_at_entry(monkeypatch):
    import server as server_mod

    calls = _captured_emit_calls(monkeypatch)

    async def _stub_dashboard_start(ctx_factory):
        raise RuntimeError("intentional short-circuit")

    class _StubDashboard:
        async def start(self, ctx_factory):
            await _stub_dashboard_start(ctx_factory)

    monkeypatch.setattr(server_mod, "get_dashboard_server", lambda: _StubDashboard())

    import asyncio

    with pytest.raises(RuntimeError):
        asyncio.run(server_mod.serve_stdio())

    event_types = [c[0] for c in calls]
    assert AuditEventType.SERVER_START in event_types
    assert event_types[0] == AuditEventType.SERVER_START


def test_serve_stdio_emits_server_shutdown_in_finally(monkeypatch):
    import server as server_mod

    calls = _captured_emit_calls(monkeypatch)

    class _StubDashboard:
        async def start(self, ctx_factory):
            raise RuntimeError("intentional fail")

    monkeypatch.setattr(server_mod, "get_dashboard_server", lambda: _StubDashboard())

    import asyncio

    with pytest.raises(RuntimeError):
        asyncio.run(server_mod.serve_stdio())

    event_types = [c[0] for c in calls]
    assert AuditEventType.SERVER_SHUTDOWN in event_types


def test_config_load_emits_exactly_once_across_multiple_from_env_calls(monkeypatch):
    calls = _captured_emit_calls(monkeypatch)

    # Invoke the helper directly with a stub instance so we don't touch
    # the full from_env() chain (which requires a repo).
    class _StubInstance:
        ingest_max_bytes = 1024
        ingest_rate_limit_burst = 10
        ingest_rate_limit_refill_per_sec = 1.0
        guided_mode = False

    context_mod._emit_config_load_once(_StubInstance())
    context_mod._emit_config_load_once(_StubInstance())
    context_mod._emit_config_load_once(_StubInstance())

    config_load_calls = [c for c in calls if c[0] == AuditEventType.CONFIG_LOAD]
    assert len(config_load_calls) == 1


def test_config_load_payload_includes_int_config_values_not_paths(monkeypatch):
    """Capture the actual emitted record, not the call kwargs, to verify the
    rendered JSON has only safe-to-log fields and no path-bearing keys."""
    buf = io.StringIO()

    class _StubInstance:
        ingest_max_bytes = 2048
        ingest_rate_limit_burst = 20
        ingest_rate_limit_refill_per_sec = 2.0
        guided_mode = False

    with redirect_stderr(buf):
        context_mod._emit_config_load_once(_StubInstance())
    line = buf.getvalue().strip()
    record = json.loads(line)
    assert record["event_type"] == "config_load"
    assert record["ingest_max_bytes"] == 2048
    assert record["ingest_rate_limit_burst"] == 20
    assert record["ingest_rate_limit_refill_per_sec"] == 2.0
    assert record["guided_mode"] is False
    # Forbid-list / explicit-omission discipline:
    for forbidden_key in ("repo_path", "surreal_url", "file_paths"):
        assert forbidden_key not in record
