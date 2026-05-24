"""Phase 2b / 2c-6b: MCP adapter shells register cleanly and complete the round-trip
through the daemon's protocol surface.

These tests verify that the *bootstrap path* works end-to-end — supervisor
boot, adapter registration, ProtocolClient connect, ingest/egress dispatch.

Phase 2c-6b: MCPIngestAdapter now wires real ledger writes via
``_handle_ingest_impl`` / ``_handle_link_commit_impl`` (replacing the Phase 2b
stubs). The test payload uses valid JSON and a memory:// ledger so the real
impl can run in-process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from integrations.mcp_adapter import (
    MCPEgressAdapter,
    MCPIngestAdapter,
    bootstrap_mcp_daemon,
)
from protocol.client import ProtocolClient
from protocol.contracts import IngestRequest, LinkCommitRequest, NotificationEvent


@pytest.fixture
def short_state_dir():
    base = Path(tempfile.mkdtemp(prefix="bm-", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def fresh_ledger_repo(monkeypatch, tmp_path):
    """A bare git repo + memory:// ledger env so the real adapter can run."""
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


async def test_bootstrap_registers_mcp_adapter_under_name_mcp(
    short_state_dir: Path,
) -> None:
    """After bootstrap, the registry exposes ingest+egress named 'mcp'."""
    supervisor = await bootstrap_mcp_daemon(
        socket_path=short_state_dir / "d.sock",
        descriptor_path=short_state_dir / "daemon.json",
    )
    try:
        assert "mcp" in supervisor.registry.ingest_names()
        assert "mcp" in supervisor.registry.egress_names()
        assert isinstance(supervisor.registry.lookup_ingest("mcp"), MCPIngestAdapter)
        assert isinstance(supervisor.registry.lookup_egress("mcp"), MCPEgressAdapter)
    finally:
        await supervisor.stop()


async def test_client_ingest_through_mcp_adapter_dispatches_to_impl(
    short_state_dir: Path,
    fresh_ledger_repo: Path,
) -> None:
    """End-to-end: client → daemon → MCP adapter → real _handle_ingest_impl.

    Phase 2c-6b: MCPIngestAdapter now calls _handle_ingest_impl (real ledger
    write) instead of returning a stub. The test asserts on the protocol-level
    result shape — status is ``accepted`` or ``refused``; no stub IDs.
    """
    supervisor = await bootstrap_mcp_daemon(
        socket_path=short_state_dir / "d.sock",
        descriptor_path=short_state_dir / "daemon.json",
    )
    client = ProtocolClient(socket_path=short_state_dir / "d.sock")
    try:
        await client.connect()
        payload = json.dumps(
            {
                "decisions": [
                    {"title": "Bootstrap dispatch test", "description": "Verify adapter round-trip"}
                ],
                "title": "Bootstrap test",
                "source": "manual",
            }
        )
        result = await client.ingest(
            IngestRequest(
                adapter_name="mcp",
                payload=payload,
                source_id="bootstrap-test",
                source_ref="2026-05-24 test",
            )
        )
        # Phase 2c-6b: real impl — status is accepted or refused (not stub).
        assert result.status in ("accepted", "refused", "duplicate")
        assert isinstance(result.decision_ids, list)

        link_result = await client.link_commit(
            LinkCommitRequest(repo_id="local", commit_sha="HEAD", ref="HEAD")
        )
        assert link_result.status in ("linked", "no_change", "refused")
    finally:
        await client.close()
        await supervisor.stop()


async def test_client_egress_through_mcp_adapter_returns_delivered(
    short_state_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    """Egress dispatch reaches MCP adapter; stderr marker is emitted."""
    supervisor = await bootstrap_mcp_daemon(
        socket_path=short_state_dir / "d.sock",
        descriptor_path=short_state_dir / "daemon.json",
    )
    client = ProtocolClient(socket_path=short_state_dir / "d.sock")
    try:
        await client.connect()
        params = NotificationEvent(
            event_type="drift_detected",
            summary="velocity threshold drifted",
            severity="warn",
        ).model_dump()
        params["channel"] = "mcp"
        result = await client._call("egress.deliver", params)
        assert result["status"] == "delivered"
    finally:
        await client.close()
        await supervisor.stop()
    captured = capsys.readouterr()
    assert "[bicameral.egress tenant=local] drift_detected" in captured.err
