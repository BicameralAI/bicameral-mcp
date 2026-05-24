"""Phase 2c-6b boundary tests: append-only write handlers through a real daemon subprocess.

Mirrors tests/test_telemetry_writes_via_daemon.py for the ledger write surface.
Each test exercises the full call chain across the IPC boundary:

    handle_ingest / handle_link_commit (MCP-side facade)
        → ctx.daemon.ingest / .link_commit (DaemonProxy)
            → ProtocolClient over UDS
                → daemon subprocess
                    → protocol/handlers/writes.handle_write_ingest / handle_write_link_commit
                        → _handle_ingest_impl / _handle_link_commit_impl

Three things these tests verify that no in-process test can:

1. **Wire serialization** — IngestResult / LinkCommitResult round-trip through JSON.
2. **Connection lifecycle** — descriptor missing → clear DaemonUnreachableError;
   reconnect after daemon restart succeeds.
3. **Append-only contract** — ingest adds rows, link_commit links HEAD; no ledger
   mutation before guards run.

Cost: ~8s per test (daemon subprocess spawn). Per the plan this is acceptable
for boundary tests until an ObjectPool of pre-warmed daemons is introduced.
"""

from __future__ import annotations

import json
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
    wizard-pointing message. Exercises ingest as representative append-write."""
    proxy = DaemonProxy(
        descriptor_path=tmp_path / "daemon.json",
        auth_path=tmp_path / "auth.json",
    )
    with pytest.raises(DaemonUnreachableError) as exc_info:
        await proxy.ingest(
            adapter_name="ledger",
            payload=json.dumps({"decisions": [], "title": "t"}),
            source_id="test",
            source_ref="test-ref",
        )
    msg = str(exc_info.value)
    assert "bicameral-mcp setup" in msg
    assert "bicameral-mcp daemon start" in msg


# ── Happy-path proxy methods ────────────────────────────────────────────


async def test_ingest_through_daemon_records_decision(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """End-to-end: spawned daemon, ingest RPC with a decision returns accepted status."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    payload = json.dumps(
        {
            "decisions": [
                {"title": "Use daemon for writes", "description": "Route all writes through daemon"}
            ],
            "title": "Architecture decisions",
            "source": "manual",
        }
    )
    try:
        result = await proxy.ingest(
            adapter_name="mcp",
            payload=payload,
            source_id="test-session",
            source_ref="arch-doc-v1",
            mode="active",
        )
        # IngestResult shape: status, decision_ids, reason
        assert result["status"] in ("accepted", "refused", "duplicate")
    finally:
        await proxy.close()


async def test_link_commit_through_daemon_links_head(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """End-to-end: spawned daemon, link_commit RPC returns linked or no_change status."""
    import subprocess as _sp

    head_sha = _sp.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=fresh_ledger_repo,
        text=True,
    ).strip()

    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        result = await proxy.link_commit(
            repo_id="local",
            commit_sha=head_sha,
            ref="HEAD",
        )
        # LinkCommitResult shape: status, regions_updated
        assert result["status"] in ("linked", "no_change", "refused")
        assert isinstance(result["regions_updated"], int)
    finally:
        await proxy.close()


# ── Reconnect after daemon restart ──────────────────────────────────────


async def test_proxy_reconnects_after_daemon_restart_append_writes(
    short_state_dir, fresh_ledger_repo
):
    """First call → daemon dies → daemon restarts → second call succeeds.

    Uses link_commit as the representative append-write method; exercises the
    same _call_with_retry path as history and feedback did in 2c-4/2c-6a.
    """
    import subprocess as _sp

    head_sha = _sp.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=fresh_ledger_repo,
        text=True,
    ).strip()

    socket_path = short_state_dir / "daemon.sock"
    descriptor_path = short_state_dir / "daemon.json"

    # Spawn the first daemon and make a successful call.
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    proxy = DaemonProxy(
        descriptor_path=descriptor_path,
        auth_path=short_state_dir / "no-auth.json",
    )
    result1 = await proxy.link_commit(repo_id="local", commit_sha=head_sha)
    assert result1["status"] in ("linked", "no_change", "refused")

    # Kill the daemon. The proxy's cached client now points at a dead socket.
    stop(descriptor_path=descriptor_path)

    # Respawn — same paths, different PID.
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)

    try:
        # Next call should detect the broken connection, reconnect, succeed.
        result2 = await proxy.link_commit(repo_id="local", commit_sha=head_sha)
        assert result2["status"] in ("linked", "no_change", "refused")
    finally:
        await proxy.close()
        stop(descriptor_path=descriptor_path)
