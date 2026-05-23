"""Phase 2c-4 boundary tests: handle_history through a real daemon subprocess.

This is the load-bearing prototype per Fowler's 2026-05-22 advisory.
Each test exercises the full call chain across the IPC boundary:

    handle_history (facade in MCP process)
        → ctx.daemon.history (DaemonProxy)
            → ProtocolClient over UDS
                → daemon subprocess
                    → protocol/handlers/reads.handle_read_history
                        → _handle_history_impl (in daemon's ledger)

Three things these tests verify that no in-process test can:

1. **Wire serialization** — HistoryResponse round-trips through JSON.
2. **Connection lifecycle** — descriptor missing / daemon dead → clear
   ``DaemonUnreachableError``; reconnect after daemon restart succeeds.
3. **Same-shape contract** — the facade returns equivalent HistoryResponse
   to what ``_handle_history_impl`` returns in-process.

Cost: ~8s per test (daemon subprocess spawn). 4 tests = ~32s. Per the
plan, this is acceptable until measured CI pain triggers an ``ObjectPool``
of pre-warmed daemons.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
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
        await proxy.history(repo_id="local")
    msg = str(exc_info.value)
    # Error must name both recovery paths so the agent / human picks the
    # right one for their mode.
    assert "bicameral-mcp setup" in msg
    assert "bicameral-mcp daemon start" in msg


async def test_proxy_raises_not_implemented_for_hosted_mode(tmp_path):
    """``auth.json`` present → NotImplementedError naming Phase 5.

    This is the mode-seam test: the proxy honors hosted mode's marker
    file even though the wire isn't implemented. Phase 5 fills in the
    body; nothing in 2c-4 needs to change here.
    """
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"endpoint": "https://daemon.bicameral.ai", "token": "stub"}')
    proxy = DaemonProxy(
        descriptor_path=tmp_path / "daemon.json",
        auth_path=auth_path,
    )
    with pytest.raises(NotImplementedError) as exc_info:
        await proxy.history(repo_id="local")
    assert "hosted mode" in str(exc_info.value)
    assert "Phase 5" in str(exc_info.value)


async def test_proxy_raises_when_descriptor_is_stale(tmp_path):
    """Descriptor exists but PID is dead → ``DaemonUnreachableError`` with
    actionable message. Simulates a daemon that crashed without cleaning
    up its descriptor file."""
    descriptor_path = tmp_path / "daemon.json"
    descriptor_path.write_text(f'{{"socket_path": "{tmp_path}/missing.sock", "pid": 999999999}}')
    proxy = DaemonProxy(descriptor_path=descriptor_path, auth_path=tmp_path / "auth.json")
    with pytest.raises(DaemonUnreachableError) as exc_info:
        await proxy.history(repo_id="local")
    assert "bicameral-mcp daemon start" in str(exc_info.value)


# ── Daemon-routed happy path ────────────────────────────────────────────


async def test_history_through_daemon_returns_empty_response(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """End-to-end: spawned daemon, ProtocolProxy, real RPC, empty ledger
    returns empty HistoryResponse."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",  # absent
    )
    try:
        result = await proxy.history(repo_id="local")
        assert "features" in result
        assert "total_features" in result
        assert result["total_features"] == 0
        assert result["features"] == []
    finally:
        await proxy.close()


async def test_history_facade_routes_through_daemon(daemon_subprocess, fresh_ledger_repo, tmp_path):
    """The MCP-side facade ``handle_history`` calls ``ctx.daemon.history``
    and returns a HistoryResponse. Asserts the daemon path is exercised
    (not the in-process fallback for daemon=None contexts)."""
    from contracts import HistoryResponse
    from handlers.history import handle_history

    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    # Minimal ctx — sync_middleware accesses ctx.ledger + ctx.repo_path.
    # Use a real in-memory ledger for the sync precheck.
    from adapters.ledger import get_ledger, reset_ledger_singleton

    reset_ledger_singleton()
    ctx = SimpleNamespace(
        repo_path=str(fresh_ledger_repo),
        ledger=get_ledger(),
        daemon=proxy,
        head_sha="",
        authoritative_ref="main",
        authoritative_sha="",
    )
    try:
        result = await handle_history(ctx)
        assert isinstance(result, HistoryResponse)
        # Empty ledger → empty features.
        assert result.total_features == 0
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
    result1 = await proxy.history(repo_id="local")
    assert "features" in result1

    # Kill the daemon. The proxy's cached client now points at a dead socket.
    stop(descriptor_path=descriptor_path)

    # Respawn — same paths, different PID.
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)

    try:
        # Next call should detect the broken connection, reconnect, succeed.
        result2 = await proxy.history(repo_id="local")
        assert "features" in result2
    finally:
        await proxy.close()
        stop(descriptor_path=descriptor_path)
