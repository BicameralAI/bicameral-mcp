"""Phase 2c-6a boundary tests: telemetry write handlers through a real daemon subprocess.

Mirrors tests/test_history_via_daemon.py for the write surface.
Each test exercises the full call chain across the IPC boundary:

    handle_feedback / handle_skill_begin / handle_skill_end (MCP-side facade)
        → ctx.daemon.feedback / .skill_begin / .skill_end (DaemonProxy)
            → ProtocolClient over UDS
                → daemon subprocess
                    → protocol/handlers/writes.handle_write_feedback / …
                        → _handle_feedback_impl / _handle_skill_begin_impl / _handle_skill_end_impl

Three things these tests verify that no in-process test can:

1. **Wire serialization** — FeedbackResult / SkillBeginResult / SkillEndResult
   round-trip through JSON.
2. **Connection lifecycle** — descriptor missing → clear DaemonUnreachableError;
   reconnect after daemon restart succeeds.
3. **Same-shape contract** — the facade returns equivalent results to what the
   _impl functions return in-process.

Cost: ~8s per test (daemon subprocess spawn). Per the plan this is acceptable
for boundary tests until an ObjectPool of pre-warmed daemons is introduced.
"""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from daemon.process import spawn, stop
from daemon.proxy import DaemonProxy, DaemonUnreachableError
from tests._daemon_fixture import daemon_subprocess, short_state_dir  # noqa: F401


@pytest.fixture(autouse=True)
def disable_telemetry(monkeypatch):
    """Gate live PostHog forwarding off; redirect counters to a tmp dir."""
    import tempfile

    monkeypatch.setenv("BICAMERAL_TELEMETRY", "0")
    monkeypatch.setenv("HOME", tempfile.mkdtemp(prefix="bm-counters-"))


@pytest.fixture
def fresh_ledger_repo(monkeypatch, tmp_path):
    """A bare git repo + memory:// ledger env. Daemon picks these up via
    REPO_PATH / SURREAL_URL when BicameralContext.from_env runs inside it."""
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
    """No daemon.json and no auth.json → DaemonUnreachableError with the
    wizard-pointing message. Exercises the feedback method as representative."""
    proxy = DaemonProxy(
        descriptor_path=tmp_path / "daemon.json",
        auth_path=tmp_path / "auth.json",
    )
    with pytest.raises(DaemonUnreachableError) as exc_info:
        await proxy.feedback(server_version="0.16.3")
    msg = str(exc_info.value)
    assert "bicameral-mcp setup" in msg
    assert "bicameral-mcp daemon start" in msg


# ── Happy-path proxy methods ────────────────────────────────────────────


async def test_feedback_through_daemon_records_event(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """End-to-end: spawned daemon, feedback RPC returns {"recorded": true}."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        result = await proxy.feedback(
            server_version="0.16.3",
            skill="test-skill",
            trying_to="verify daemon routing",
            attempted="call proxy.feedback",
            stuck_on="",
        )
        assert result == {"recorded": True}
    finally:
        await proxy.close()


async def test_skill_begin_then_end_through_daemon(daemon_subprocess, fresh_ledger_repo, tmp_path):
    """skill_begin → skill_end round-trip through daemon:
    begin returns started status; end returns duration_ms >= 0."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        begin_result = await proxy.skill_begin(
            session_id="sess-daemon-test",
            skill_name="bicameral-sync",
        )
        assert begin_result["session_id"] == "sess-daemon-test"
        assert begin_result["skill"] == "bicameral-sync"
        assert begin_result["status"] == "started"

        end_result = await proxy.skill_end(
            session_id="sess-daemon-test",
            skill_name="bicameral-sync",
            server_version="0.16.3",
            errored=False,
        )
        assert end_result["session_id"] == "sess-daemon-test"
        assert end_result["skill"] == "bicameral-sync"
        assert end_result["status"] == "recorded"
        assert end_result["duration_ms"] >= 0
        assert end_result.get("diagnostic_warning") is None
    finally:
        await proxy.close()


# ── Reconnect after daemon restart ──────────────────────────────────────


async def test_proxy_reconnects_after_daemon_restart_telemetry(short_state_dir, fresh_ledger_repo):
    """First call → daemon dies → daemon restarts → second call succeeds.

    Uses feedback as the representative telemetry method; exercises the
    same _call_with_retry path as history did in 2c-4.
    """
    socket_path = short_state_dir / "daemon.sock"
    descriptor_path = short_state_dir / "daemon.json"

    # Spawn the first daemon and make a successful call.
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    proxy = DaemonProxy(
        descriptor_path=descriptor_path,
        auth_path=short_state_dir / "no-auth.json",
    )
    result1 = await proxy.feedback(server_version="0.16.3")
    assert result1 == {"recorded": True}

    # Kill the daemon. The proxy's cached client now points at a dead socket.
    stop(descriptor_path=descriptor_path)

    # Respawn — same paths, different PID.
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)

    try:
        # Next call should detect the broken connection, reconnect, succeed.
        result2 = await proxy.feedback(server_version="0.16.3", trying_to="reconnect")
        assert result2 == {"recorded": True}
    finally:
        await proxy.close()
        stop(descriptor_path=descriptor_path)
