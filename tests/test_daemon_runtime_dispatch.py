"""Phase 2a: Runtime correctly dispatches protocol calls to registered adapters."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from daemon.registry import AdapterRegistry, AdapterRegistryError
from daemon.runtime import Runtime
from protocol.client import ProtocolClient
from protocol.contracts import (
    ConnectionContext,
    DeliveryResult,
    IngestRequest,
    IngestResult,
    LinkCommitRequest,
    LinkCommitResult,
    NotificationEvent,
)


@pytest.fixture
def short_socket_dir():
    base = Path(tempfile.mkdtemp(prefix="bm-", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


class _RecordingIngest:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[IngestRequest, ConnectionContext]] = []

    async def ingest(self, req: IngestRequest, ctx: ConnectionContext) -> IngestResult:
        self.calls.append((req, ctx))
        return IngestResult(status="accepted", decision_ids=["d1"])

    async def link_commit(
        self, _req: LinkCommitRequest, _ctx: ConnectionContext
    ) -> LinkCommitResult:
        return LinkCommitResult(status="linked", regions_updated=3)


class _RecordingEgress:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[NotificationEvent, ConnectionContext]] = []

    async def deliver(
        self, event: NotificationEvent, ctx: ConnectionContext
    ) -> DeliveryResult:
        self.calls.append((event, ctx))
        return DeliveryResult(status="delivered")


async def test_runtime_routes_ingest_to_registered_adapter(short_socket_dir: Path) -> None:
    """Client.ingest with adapter_name='mcp' reaches the mcp adapter and returns its result."""
    registry = AdapterRegistry()
    adapter = _RecordingIngest("mcp")
    registry.register_ingest(adapter)
    runtime = Runtime(short_socket_dir / "d.sock", registry)
    await runtime.start()
    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        result = await client.ingest(
            IngestRequest(
                adapter_name="mcp",
                payload="we decided X",
                source_id="src-1",
                source_ref="ref-1",
            )
        )
    finally:
        await client.close()
        await runtime.stop()
    assert result.status == "accepted"
    assert result.decision_ids == ["d1"]
    assert len(adapter.calls) == 1
    captured_req, captured_ctx = adapter.calls[0]
    assert captured_req.payload == "we decided X"
    assert captured_ctx.tenant_id == "local"  # default from ProtocolClient


async def test_runtime_routes_egress_to_channel_named_in_params(
    short_socket_dir: Path,
) -> None:
    """egress.deliver carries a 'channel' param naming the registered egress adapter."""
    registry = AdapterRegistry()
    slack = _RecordingEgress("slack")
    stderr = _RecordingEgress("stderr")
    registry.register_egress(slack)
    registry.register_egress(stderr)
    runtime = Runtime(short_socket_dir / "d.sock", registry)
    await runtime.start()
    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        # Build the wire payload directly so we can pass 'channel'.
        params = NotificationEvent(
            event_type="drift_detected",
            summary="velocity threshold drifted",
            severity="warn",
        ).model_dump()
        params["channel"] = "slack"
        result = await client._call("egress.deliver", params)
    finally:
        await client.close()
        await runtime.stop()
    assert result["status"] == "delivered"
    assert len(slack.calls) == 1
    assert len(stderr.calls) == 0


async def test_runtime_rejects_unknown_adapter_name(short_socket_dir: Path) -> None:
    """An ingest call with an unregistered adapter_name surfaces as an RPC error."""
    registry = AdapterRegistry()
    # No adapters registered.
    runtime = Runtime(short_socket_dir / "d.sock", registry)
    await runtime.start()
    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        with pytest.raises(Exception, match="unknown ingest adapter"):
            await client.ingest(
                IngestRequest(
                    adapter_name="linear",
                    payload="x",
                    source_id="s",
                    source_ref="r",
                )
            )
    finally:
        await client.close()
        await runtime.stop()
