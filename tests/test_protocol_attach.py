"""Phase 2b: system.attach RPC binds tenant_id to the connection.

After attach, every subsequent RPC sees the tenant via ConnectionContext.
Before attach, only system.version and system.attach are allowed.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from protocol.client import ProtocolClient
from protocol.contracts import ConnectionContext, IngestRequest
from protocol.server import ProtocolServer


@pytest.fixture
def short_socket_dir():
    base = Path(tempfile.mkdtemp(prefix="bm-", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


async def test_pre_attach_rpc_other_than_version_is_refused(
    short_socket_dir: Path,
) -> None:
    """A custom RPC before system.attach gets a protocol error response."""
    server = ProtocolServer(short_socket_dir / "d.sock")

    async def handle_anything(params: dict, ctx: ConnectionContext) -> dict:
        return {"ok": True, "tenant": ctx.tenant_id}

    server.register("custom.method", handle_anything)
    await server.start()

    # Build a raw client that does NOT auto-attach so we can issue a
    # pre-attach call and verify rejection.
    import asyncio
    import json

    reader, writer = await asyncio.open_unix_connection(str(short_socket_dir / "d.sock"))
    try:
        # custom.method with no prior system.attach: expect error response.
        req = {"jsonrpc": "2.0", "id": 1, "method": "custom.method", "params": {}}
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        resp_line = await reader.readline()
        resp = json.loads(resp_line)
        assert "error" in resp
        assert "system.attach" in resp["error"]["message"]
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_attach_then_call_sees_tenant_in_context(
    short_socket_dir: Path,
) -> None:
    """After system.attach, ConnectionContext.tenant_id flows to the handler."""
    server = ProtocolServer(short_socket_dir / "d.sock")

    received: list[ConnectionContext] = []

    async def handle_probe(_params: dict, ctx: ConnectionContext) -> dict:
        received.append(ctx)
        return {"tenant": ctx.tenant_id, "user": ctx.user_id}

    server.register("probe.context", handle_probe)
    await server.start()

    client = ProtocolClient(
        socket_path=short_socket_dir / "d.sock",
        tenant_id="acme-team",
        user_id="alice@acme.example",
    )
    try:
        await client.connect()  # connect now also runs system.attach
        result = await client._call("probe.context", {})
        assert result == {"tenant": "acme-team", "user": "alice@acme.example"}
        assert received[0].tenant_id == "acme-team"
        assert received[0].user_id == "alice@acme.example"
    finally:
        await client.close()
        await server.stop()


async def test_each_connection_has_independent_tenant_binding(
    short_socket_dir: Path,
) -> None:
    """Two concurrent clients with different tenant_ids do not share context."""
    server = ProtocolServer(short_socket_dir / "d.sock")

    async def handle_whoami(_params: dict, ctx: ConnectionContext) -> dict:
        return {"tenant": ctx.tenant_id}

    server.register("probe.whoami", handle_whoami)
    await server.start()

    client_a = ProtocolClient(socket_path=short_socket_dir / "d.sock", tenant_id="tenant-a")
    client_b = ProtocolClient(socket_path=short_socket_dir / "d.sock", tenant_id="tenant-b")
    try:
        await client_a.connect()
        await client_b.connect()
        r_a = await client_a._call("probe.whoami", {})
        r_b = await client_b._call("probe.whoami", {})
        assert r_a == {"tenant": "tenant-a"}
        assert r_b == {"tenant": "tenant-b"}
    finally:
        await client_a.close()
        await client_b.close()
        await server.stop()
