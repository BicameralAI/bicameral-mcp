"""Phase 2c-2b conformance tests for the ``write.*`` telemetry surface.

Mirrors ``tests/test_protocol_read_conformance.py`` — dispatch shape,
Pydantic input validation, idempotent registration. The handlers are
telemetry-only (no ledger mutation), so the underlying ``send_event`` /
``record_skill_event`` calls are gated off via ``BICAMERAL_TELEMETRY=0`` to
avoid hitting the live PostHog relay during tests.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from protocol.client import ProtocolClient
from protocol.handlers.writes import register_write_handlers
from protocol.server import ProtocolServer


@pytest.fixture
def short_socket_dir():
    base = Path(tempfile.mkdtemp(prefix="bm-", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(autouse=True)
def disable_telemetry(monkeypatch):
    """Gate live PostHog forwarding off. Local counters still fire (file-only)."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "0")
    # Counters write under ~/.bicameral/counters — redirect to a temp dir so the
    # tests don't pollute the developer's local store.
    monkeypatch.setenv("HOME", tempfile.mkdtemp(prefix="bm-counters-"))


async def test_write_feedback_dispatches(short_socket_dir):
    server = ProtocolServer(short_socket_dir / "d.sock")
    register_write_handlers(server)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        result = await client._call(
            "write.feedback",
            {
                "server_version": "0.16.3",
                "skill": "test-skill",
                "trying_to": "ship 2c-2b",
                "attempted": "wire write.feedback",
                "stuck_on": "",
            },
        )
        assert result == {"recorded": True}
    finally:
        await client.close()
        await server.stop()


async def test_write_skill_begin_dispatches(short_socket_dir):
    server = ProtocolServer(short_socket_dir / "d.sock")
    register_write_handlers(server)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        result = await client._call(
            "write.skill_begin",
            {"session_id": "sess-1", "skill_name": "bicameral-sync"},
        )
        assert result["session_id"] == "sess-1"
        assert result["skill"] == "bicameral-sync"
        assert result["status"] == "started"
    finally:
        await client.close()
        await server.stop()


async def test_write_skill_end_dispatches_paired_with_begin(short_socket_dir):
    """skill_end consumes the t0 set by skill_begin and reports duration_ms."""
    server = ProtocolServer(short_socket_dir / "d.sock")
    register_write_handlers(server)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        await client._call(
            "write.skill_begin",
            {"session_id": "sess-2", "skill_name": "test-skill"},
        )
        result = await client._call(
            "write.skill_end",
            {
                "session_id": "sess-2",
                "skill_name": "test-skill",
                "server_version": "0.16.3",
                "errored": False,
                "error_class": None,
                "diagnostic": None,
            },
        )
        assert result["session_id"] == "sess-2"
        assert result["skill"] == "test-skill"
        assert result["status"] == "recorded"
        assert result["duration_ms"] >= 0
        # No diagnostic_warning on a clean call.
        assert result.get("diagnostic_warning") is None
    finally:
        await client.close()
        await server.stop()


async def test_write_skill_end_without_begin_returns_zero_duration(short_socket_dir):
    """Unknown session_id → duration_ms = 0; status still 'recorded'."""
    server = ProtocolServer(short_socket_dir / "d.sock")
    register_write_handlers(server)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        result = await client._call(
            "write.skill_end",
            {
                "session_id": "never-began",
                "skill_name": "test-skill",
                "server_version": "0.16.3",
            },
        )
        assert result["duration_ms"] == 0
        assert result["status"] == "recorded"
    finally:
        await client.close()
        await server.stop()


async def test_write_feedback_rejects_extra_fields(short_socket_dir):
    """Pydantic ``extra='forbid'`` rejects unknown keys at the protocol boundary."""
    from protocol.contracts import ProtocolError

    server = ProtocolServer(short_socket_dir / "d.sock")
    register_write_handlers(server)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        with pytest.raises(ProtocolError):
            await client._call(
                "write.feedback",
                {
                    "server_version": "0.16.3",
                    "skill": "x",
                    "unexpected_field": "boom",
                },
            )
    finally:
        await client.close()
        await server.stop()


async def test_write_skill_begin_requires_session_id(short_socket_dir):
    """Missing required field → Pydantic raises, dispatcher returns error."""
    from protocol.contracts import ProtocolError

    server = ProtocolServer(short_socket_dir / "d.sock")
    register_write_handlers(server)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        with pytest.raises(ProtocolError):
            await client._call("write.skill_begin", {"skill_name": "x"})
    finally:
        await client.close()
        await server.stop()


def test_register_write_handlers_is_idempotent(tmp_path):
    """Locks the no-conflict re-registration contract — same as reads."""
    sock = tmp_path / "d.sock"
    server = ProtocolServer(sock)
    register_write_handlers(server)
    register_write_handlers(server)  # must not raise
    assert "write.feedback" in server._methods
    assert "write.skill_begin" in server._methods
    assert "write.skill_end" in server._methods
