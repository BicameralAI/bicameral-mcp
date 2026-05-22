"""Phase 1 versioning: minor-additive tolerance + major-bump rejection."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from protocol.client import ProtocolClient
from protocol.contracts import IngestRequest, IngestResult, ProtocolVersionError
from protocol.server import ProtocolServer


@pytest.fixture
def short_socket_dir():
    """macOS AF_UNIX path is ~104-char capped; pytest tmp_path is too deep."""
    base = Path(tempfile.mkdtemp(prefix="bm-", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


async def test_v0_1_client_ignores_unknown_field_from_v0_2_server(
    short_socket_dir: Path,
) -> None:
    """Server returns a payload with an extra field; v0.1 client tolerates it.

    Minor-version bumps add optional fields. The wire layer must not choke
    on unknown keys in a response; the typed client extracts only declared
    fields and discards the rest.
    """
    server = ProtocolServer(short_socket_dir / "d.sock")

    async def handle_ingest_with_extra_field(_params: dict) -> dict:
        return {
            "status": "accepted",
            "decision_ids": ["d1"],
            "reason": None,
            "future_telemetry": {"latency_ms": 42},  # v0.2 addition
        }

    server.register("ingest.ingest", handle_ingest_with_extra_field)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        result = await client.ingest(
            IngestRequest(
                adapter_name="mcp",
                payload="x",
                source_id="s",
                source_ref="r",
            )
        )
        assert isinstance(result, IngestResult)
        assert result.status == "accepted"
        assert result.decision_ids == ["d1"]
    finally:
        await client.close()
        await server.stop()


async def test_v0_1_client_rejects_major_bump_server(short_socket_dir: Path) -> None:
    """Server advertises a future major version; client.connect() raises."""
    server = ProtocolServer(short_socket_dir / "d.sock")

    async def handle_version(_params: dict) -> str:
        return "1.0.0"

    server.register("system.version", handle_version)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        with pytest.raises(ProtocolVersionError):
            await client.connect()
    finally:
        await client.close()
        await server.stop()
