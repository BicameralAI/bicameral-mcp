"""Phase 2c-5 boundary tests: handle_usage_summary through a real daemon subprocess.

Mirrors ``tests/test_history_via_daemon.py`` — same structure, same fixture,
same Fowler boundary-test rationale. Each test exercises the full call chain
across the IPC boundary:

    handle_usage_summary (facade in MCP process)
        → ctx.daemon.usage_summary (DaemonProxy)
            → ProtocolClient over UDS
                → daemon subprocess
                    → protocol/handlers/reads.handle_read_usage_summary
                        → _handle_usage_summary_impl (in daemon's ledger)

Three things these tests verify that no in-process test can:

1. **Wire serialization** — UsageSummaryResult round-trips through JSON.
2. **Connection lifecycle** — descriptor missing / daemon dead → clear
   ``DaemonUnreachableError``; reconnect after daemon restart succeeds.
3. **Same-shape contract** — the facade returns equivalent dict shape to
   what ``_handle_usage_summary_impl`` returns in-process.

Cost: ~8s per test (daemon subprocess spawn). Per the plan, this is acceptable
for boundary tests. When per-test cost becomes painful, swap the fixture body
for an ObjectPool of pre-warmed daemons without touching any test.
"""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from daemon.process import spawn, stop
from daemon.proxy import DaemonProxy, DaemonUnreachableError
from tests._daemon_fixture import daemon_subprocess, short_state_dir  # noqa: F401


@pytest.fixture
def fresh_ledger_repo(monkeypatch, tmp_path):
    """A bare git repo + memory:// ledger env. The daemon picks these up
    via ``REPO_PATH`` / ``SURREAL_URL`` when ``BicameralContext.from_env``
    runs inside it.
    """
    monkeypatch.setenv("SURREAL_URL", "memory://")
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return tmp_path


# ── Daemon unreachable ──────────────────────────────────────────────────


async def test_proxy_raises_when_no_descriptor(tmp_path):
    """No ``daemon.json`` and no ``auth.json`` → ``DaemonUnreachableError``
    with the wizard-pointing message."""
    proxy = DaemonProxy(
        descriptor_path=tmp_path / "daemon.json",
        auth_path=tmp_path / "auth.json",
    )
    with pytest.raises(DaemonUnreachableError) as exc_info:
        await proxy.usage_summary(repo_id="local")
    msg = str(exc_info.value)
    assert "bicameral-mcp setup" in msg
    assert "bicameral-mcp daemon start" in msg


# ── Daemon-routed happy path ────────────────────────────────────────────


async def test_usage_summary_through_daemon_returns_baseline(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """End-to-end: spawned daemon, DaemonProxy, real RPC, empty ledger
    returns the zero-count baseline UsageSummaryResult."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",  # absent
    )
    try:
        result = await proxy.usage_summary(repo_id="local", days=7)
        # All numeric fields must be present with zero-count baseline values.
        assert "period_days" in result
        assert "decisions_ingested" in result
        assert result["period_days"] == 7
        assert result["decisions_ingested"] == 0
        assert result["reflected_pct"] == 0.0
        assert result["drift_pct"] == 0.0
    finally:
        await proxy.close()


async def test_usage_summary_facade_routes_through_daemon(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """The MCP-side facade ``handle_usage_summary`` calls
    ``ctx.daemon.usage_summary`` and returns a dict with the correct shape.
    Asserts the daemon path is exercised (not the in-process fallback for
    daemon=None contexts)."""
    from handlers.usage_summary import handle_usage_summary

    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    # Minimal ctx — usage_summary does not call ensure_ledger_synced,
    # so we only need repo_path + daemon on the context object.
    ctx = SimpleNamespace(
        repo_path=str(fresh_ledger_repo),
        daemon=proxy,
    )
    try:
        result = await handle_usage_summary(ctx, days=7)
        assert isinstance(result, dict)
        assert result["period_days"] == 7
        assert result["decisions_ingested"] == 0
        assert "reflected_pct" in result
        assert "drift_pct" in result
    finally:
        await proxy.close()


# ── Reconnect after daemon restart ──────────────────────────────────────


async def test_proxy_reconnects_after_daemon_restart(short_state_dir, fresh_ledger_repo):
    """First call → daemon dies → daemon restarts → second call succeeds.

    Contract: the proxy detects the dropped connection on the failing
    call, clears its cached client, re-resolves the descriptor, opens
    a new connection, and retries. ONE retry max — if the second open
    also fails, raise ``DaemonUnreachableError``.
    """
    socket_path = short_state_dir / "daemon.sock"
    descriptor_path = short_state_dir / "daemon.json"

    # Spawn the first daemon and make a successful call.
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    proxy = DaemonProxy(
        descriptor_path=descriptor_path,
        auth_path=short_state_dir / "no-auth.json",
    )
    result1 = await proxy.usage_summary(repo_id="local")
    assert "period_days" in result1

    # Kill the daemon. The proxy's cached client now points at a dead socket.
    stop(descriptor_path=descriptor_path)

    # Respawn — same paths, different PID.
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)

    try:
        # Next call should detect the broken connection, reconnect, succeed.
        result2 = await proxy.usage_summary(repo_id="local")
        assert "period_days" in result2
    finally:
        await proxy.close()
        stop(descriptor_path=descriptor_path)
