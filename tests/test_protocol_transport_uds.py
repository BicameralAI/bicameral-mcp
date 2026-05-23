"""Phase 1 transport: UDS+JSON-RPC client/server end-to-end + per-client isolation."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from protocol.client import ProtocolClient
from protocol.contracts import ValidateSymbolsRequest
from protocol.server import ProtocolServer


@pytest.fixture
def short_socket_dir():
    """macOS limits AF_UNIX paths to ~104 chars; pytest's tmp_path is too deep."""
    base = Path(tempfile.mkdtemp(prefix="bm-", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
async def running_server(short_socket_dir: Path):
    """Boot a ProtocolServer with a fixture symbol handler bound."""
    server = ProtocolServer(short_socket_dir / "d.sock")

    async def handle_validate(params: dict) -> list[dict]:
        names = params.get("candidates", [])
        return [
            {"name": name, "file": f"{name}.py", "start_line": 1, "end_line": 5} for name in names
        ]

    server.register("grounding.lookup.validate_symbols", handle_validate)
    await server.start()
    try:
        yield server, short_socket_dir / "d.sock"
    finally:
        await server.stop()


async def test_client_rpc_returns_server_payload(running_server) -> None:
    """Assertion is on the *list returned*, not on call presence."""
    _server, socket_path = running_server
    client = ProtocolClient(socket_path=socket_path)
    await client.connect()
    try:
        result = await client.validate_symbols(
            ValidateSymbolsRequest(repo_id="r", ref="HEAD", candidates=["foo", "bar"])
        )
    finally:
        await client.close()
    assert [s.name for s in result] == ["foo", "bar"]
    assert all(s.start_line == 1 and s.end_line == 5 for s in result)


async def test_two_clients_no_response_interleave(running_server) -> None:
    """Each client's responses come back in its own request order."""
    _server, socket_path = running_server

    async def run_client(label: str) -> list[list[str]]:
        client = ProtocolClient(socket_path=socket_path)
        await client.connect()
        try:
            results: list[list[str]] = []
            for i in range(50):
                req = ValidateSymbolsRequest(
                    repo_id="r",
                    ref="HEAD",
                    candidates=[f"{label}_{i}"],
                )
                resp = await client.validate_symbols(req)
                results.append([s.name for s in resp])
            return results
        finally:
            await client.close()

    a_results, b_results = await asyncio.gather(run_client("a"), run_client("b"))

    assert [r[0] for r in a_results] == [f"a_{i}" for i in range(50)]
    assert [r[0] for r in b_results] == [f"b_{i}" for i in range(50)]
