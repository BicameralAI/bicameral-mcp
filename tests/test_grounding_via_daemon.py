"""Phase 2c-7a boundary tests: grounding operations through a real daemon subprocess.

Mirrors ``tests/test_history_via_daemon.py`` — same fixture, same Fowler
boundary-test rationale. Each test exercises the full call chain across the
IPC boundary:

    DaemonProxy.<grounding_method>
        → _call_with_retry("grounding.<ns>.<method>", params)
            → ProtocolClient over UDS
                → daemon subprocess
                    → protocol/handlers/grounding.<dispatcher>

Three things these tests verify that no in-process test can:

1. **Wire serialization** — grounding results round-trip through JSON.
2. **Connection lifecycle** — descriptor missing → clear DaemonUnreachableError;
   reconnect after daemon restart succeeds.
3. **Same-shape contract** — the facade returns the correct DriftResult /
   Symbol shape even from an empty environment.

Cost: ~8s per test (daemon subprocess spawn). Acceptable for boundary tests.
"""

from __future__ import annotations

import os
import subprocess
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


async def test_proxy_raises_when_no_descriptor_validate_symbols(tmp_path):
    """No daemon.json → DaemonUnreachableError with actionable hint."""
    proxy = DaemonProxy(
        descriptor_path=tmp_path / "daemon.json",
        auth_path=tmp_path / "auth.json",
    )
    with pytest.raises(DaemonUnreachableError) as exc_info:
        await proxy.validate_symbols(repo_id="local", candidates=["foo"])
    msg = str(exc_info.value)
    assert "bicameral-mcp setup" in msg
    assert "bicameral-mcp daemon start" in msg


# ── Daemon-routed happy path: validate_symbols ──────────────────────────


async def test_validate_symbols_through_daemon(daemon_subprocess, fresh_ledger_repo, tmp_path):
    """validate_symbols over real daemon returns list (empty for unknown symbol
    in empty repo) — but the RPC round-trip itself succeeds."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        result = await proxy.validate_symbols(
            repo_id="local",
            candidates=["nonexistent_symbol_xyz"],
        )
        # Empty code graph → empty list; RPC itself must succeed.
        assert isinstance(result, list)
    finally:
        await proxy.close()


# ── Daemon-routed happy path: extract_symbols ───────────────────────────


async def test_extract_symbols_through_daemon(daemon_subprocess, fresh_ledger_repo, tmp_path):
    """extract_symbols over real daemon for a nonexistent file → empty list."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        result = await proxy.extract_symbols(
            repo_id="local",
            file_path="no_such_file.py",
        )
        assert isinstance(result, list)
    finally:
        await proxy.close()


# ── Daemon-routed happy path: analyze_region ───────────────────────────


async def test_analyze_region_through_daemon(daemon_subprocess, fresh_ledger_repo, tmp_path):
    """analyze_region over real daemon returns DriftResult shape.

    The region targets a nonexistent file → ungrounded. The key assertion
    is that the wire format is correct (status, content_hash, confidence).
    """
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    region = {
        "file": "no_such_file.py",
        "symbol": "some_func",
        "start_line": 1,
        "end_line": 10,
        "stored_hash": "abc123",
    }
    try:
        result = await proxy.analyze_region(repo_id="local", region=region)
        # Must have DriftResult shape.
        assert "status" in result
        assert "content_hash" in result
        assert "confidence" in result
        assert result["status"] in ("reflected", "drifted", "pending", "ungrounded")
    finally:
        await proxy.close()


# ── Daemon-routed happy path: batch_analyze_regions ────────────────────


async def test_batch_analyze_returns_list(daemon_subprocess, fresh_ledger_repo, tmp_path):
    """batch_analyze_regions over real daemon returns a list with one entry
    per input region."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    regions = [
        {
            "file": "no_such_file.py",
            "symbol": "func_a",
            "start_line": 1,
            "end_line": 5,
            "stored_hash": "",
        },
        {
            "file": "no_such_file.py",
            "symbol": "func_b",
            "start_line": 6,
            "end_line": 10,
            "stored_hash": "",
        },
    ]
    try:
        results = await proxy.batch_analyze_regions(repo_id="local", regions=regions)
        assert isinstance(results, list)
        assert len(results) == 2
        for r in results:
            assert "status" in r
            assert "content_hash" in r
    finally:
        await proxy.close()


# ── detect_drift facade routes through daemon ───────────────────────────


async def test_detect_drift_facade_routes_through_daemon(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """handle_detect_drift with ctx.daemon set → uses daemon path.

    Since the ledger is empty, returns a DetectDriftResponse with no decisions.
    This test verifies the facade's daemon branch runs without error and
    returns the correct response shape.
    """
    from contracts import DetectDriftResponse
    from handlers.detect_drift import handle_detect_drift

    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    from adapters.ledger import get_drift_analyzer, get_ledger, reset_ledger_singleton

    reset_ledger_singleton()
    ledger = get_ledger()

    ctx = SimpleNamespace(
        repo_path=str(fresh_ledger_repo),
        ledger=ledger,
        daemon=proxy,
        head_sha="",
        authoritative_ref="main",
        authoritative_sha="",
        drift_analyzer=get_drift_analyzer(),
    )
    try:
        result = await handle_detect_drift(ctx, file_path="no_such_file.py")
        assert isinstance(result, DetectDriftResponse)
        assert result.file_path == "no_such_file.py"
        assert result.decisions == []
        assert result.drifted_count == 0
    finally:
        await proxy.close()


# ── detect_drift falls back when daemon=None ────────────────────────────


async def test_detect_drift_impl_without_daemon(fresh_ledger_repo, tmp_path):
    """When ctx.daemon is None, _handle_detect_drift_impl is used directly.

    This is the non-daemon / test-context path. Verifies the facade's
    fallback path produces the correct shape.
    """
    from adapters.ledger import get_drift_analyzer, get_ledger, reset_ledger_singleton
    from contracts import DetectDriftResponse
    from handlers.detect_drift import _handle_detect_drift_impl

    reset_ledger_singleton()
    ledger = get_ledger()

    ctx = SimpleNamespace(
        repo_path=str(fresh_ledger_repo),
        ledger=ledger,
        head_sha="",
        authoritative_ref="main",
        authoritative_sha="",
        drift_analyzer=get_drift_analyzer(),
    )
    result = await _handle_detect_drift_impl(ctx, file_path="no_such_file.py")
    assert isinstance(result, DetectDriftResponse)
    assert result.decisions == []


# ── Reconnect after daemon restart ──────────────────────────────────────


async def test_proxy_reconnects_after_daemon_restart_grounding(short_state_dir, fresh_ledger_repo):
    """First grounding call → daemon dies → daemon restarts → second call succeeds.

    Same reconnect contract as test_history_via_daemon — verify the retry
    logic works for the grounding.analyze.region method specifically.
    """
    socket_path = short_state_dir / "daemon.sock"
    descriptor_path = short_state_dir / "daemon.json"

    # Spawn the first daemon and make a successful grounding call.
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    proxy = DaemonProxy(
        descriptor_path=descriptor_path,
        auth_path=short_state_dir / "no-auth.json",
    )
    region = {
        "file": "no_such_file.py",
        "symbol": "f",
        "start_line": 1,
        "end_line": 5,
        "stored_hash": "",
    }
    result1 = await proxy.analyze_region(repo_id="local", region=region)
    assert "status" in result1

    # Kill the daemon.
    stop(descriptor_path=descriptor_path)

    # Respawn — same paths, different PID.
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)

    try:
        result2 = await proxy.analyze_region(repo_id="local", region=region)
        assert "status" in result2
    finally:
        await proxy.close()
        stop(descriptor_path=descriptor_path)
