"""Phase 2b: MCP adapter shells register cleanly and complete the round-trip
through the daemon's protocol surface.

These tests verify that the *bootstrap path* works end-to-end — supervisor
boot, adapter registration, ProtocolClient connect, ingest/egress dispatch.
The shell adapters' bodies are stubs (Phase 2c wires real ledger + notif),
so the assertions target the dispatch path, not the business logic.
"""

from __future__ import annotations

import shutil
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
        assert isinstance(
            supervisor.registry.lookup_ingest("mcp"), MCPIngestAdapter
        )
        assert isinstance(
            supervisor.registry.lookup_egress("mcp"), MCPEgressAdapter
        )
    finally:
        await supervisor.stop()


async def test_client_ingest_through_mcp_adapter_returns_stub_decision_id(
    short_state_dir: Path,
) -> None:
    """End-to-end: client → daemon → MCP adapter → stub response."""
    supervisor = await bootstrap_mcp_daemon(
        socket_path=short_state_dir / "d.sock",
        descriptor_path=short_state_dir / "daemon.json",
    )
    client = ProtocolClient(socket_path=short_state_dir / "d.sock")
    try:
        await client.connect()
        result = await client.ingest(
            IngestRequest(
                adapter_name="mcp",
                payload="meeting note",
                source_id="fathom-123",
                source_ref="2026-05-21 standup",
            )
        )
        assert result.status == "accepted"
        # Stub now includes tenant_id from the connection — default 'local'.
        assert result.decision_ids == ["stub-local-fathom-123"]

        link_result = await client.link_commit(
            LinkCommitRequest(
                repo_id="bicameral-mcp", commit_sha="abc1234", ref="HEAD"
            )
        )
        assert link_result.status == "no_change"
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
