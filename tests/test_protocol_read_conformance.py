"""Phase 2c-2 conformance tests for the ``read.*`` protocol surface.

Every registered method must dispatch on a well-formed request and return a
response that round-trips through its declared result model. Two extra
constraint tests encode the bicameral-preflight findings:

- Deterministic read path (no LLM hop in read handlers).
- Lazy ledger connect preserved (``ProtocolServer.__init__`` does not open
  a SurrealDB connection).

These tests run the server **in-process** — same Python interpreter, UDS
loopback. Phase 2c-4 adds the per-test daemon-subprocess fixture that
verifies wire-level + connection-lifecycle behavior across a real process
boundary; the in-process tests stay focused on contract shape.
"""

from __future__ import annotations

import inspect
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from protocol.client import ProtocolClient
from protocol.handlers.reads import register_read_handlers
from protocol.server import ProtocolServer


@pytest.fixture
def short_socket_dir():
    """macOS AF_UNIX paths cap around 104 chars; pytest's tmp_path is too deep."""
    base = Path(tempfile.mkdtemp(prefix="bm-", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def memory_ledger_env(monkeypatch, tmp_path):
    """Point ``BicameralContext.from_env()`` at a fresh in-memory ledger.

    Required because the protocol read handlers delegate to the real MCP
    handlers (``handlers.history``, ``handlers.usage_summary``), which build
    a ``BicameralContext`` that opens a SurrealDB connection. ``memory://``
    keeps each test isolated without a SurrealKV file on disk.
    """
    monkeypatch.setenv("SURREAL_URL", "memory://")
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    # Initialize a minimal git repo so resolve_head doesn't choke.
    import subprocess

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
    yield tmp_path


async def test_read_history_dispatches(short_socket_dir, memory_ledger_env):
    """``read.history`` returns a HistoryResponse-shaped payload for an empty ledger."""
    server = ProtocolServer(short_socket_dir / "d.sock")
    register_read_handlers(server)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        result = await client._call("read.history", {"repo_id": "test", "ref": "HEAD"})
        assert isinstance(result, dict)
        # Wire shape: HistoryResponse — features, truncated, total_features, as_of.
        assert "features" in result
        assert "truncated" in result
        assert "total_features" in result
        assert result["total_features"] == 0  # empty ledger
        assert result["features"] == []
    finally:
        await client.close()
        await server.stop()


async def test_read_usage_summary_dispatches(short_socket_dir, memory_ledger_env):
    """``read.usage_summary`` returns the flat UsageSummaryResult envelope."""
    server = ProtocolServer(short_socket_dir / "d.sock")
    register_read_handlers(server)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        result = await client._call("read.usage_summary", {"repo_id": "test", "days": 7})
        assert isinstance(result, dict)
        # Flat envelope per the "no nested wrappers" preflight constraint.
        for key in (
            "period_days",
            "ingest_calls",
            "bind_calls_total",
            "decisions_ingested",
            "decisions_ungrounded",
            "decisions_pending",
            "decisions_reflected",
            "decisions_drifted",
            "reflected_pct",
            "drift_pct",
            "cosmetic_drift_pct",
            "error_rate",
        ):
            assert key in result, f"missing field {key}"
        assert result["period_days"] == 7
    finally:
        await client.close()
        await server.stop()


async def test_read_usage_summary_rejects_out_of_range_days(short_socket_dir, memory_ledger_env):
    """Pydantic validation: ``days`` is bounded [0, 365]; out-of-range raises."""
    from protocol.contracts import ProtocolError

    server = ProtocolServer(short_socket_dir / "d.sock")
    register_read_handlers(server)
    await server.start()

    client = ProtocolClient(socket_path=short_socket_dir / "d.sock")
    try:
        await client.connect()
        with pytest.raises(ProtocolError):
            await client._call("read.usage_summary", {"repo_id": "test", "days": 999})
    finally:
        await client.close()
        await server.stop()


# ── Constraint tests (bicameral preflight, 2026-05-22) ──────────────────


def test_read_handlers_have_no_llm_imports():
    """Constraint: ``read.*`` methods stay deterministic — no LLM hop.

    Bound decision: ``decision:wndwxgam2m8yjor0igya`` — "MCP server:
    deterministic tools, no nested LLM". A future contributor adding an LLM
    fallback to a read handler must come back and explain why.
    """
    import protocol.handlers.reads as reads_module

    source = inspect.getsource(reads_module)
    forbidden = [
        "import litellm",
        "from litellm",
        "import openai",
        "from openai",
        "import anthropic",
        "from anthropic",
        "ChatCompletion",
        "AsyncAnthropic",
    ]
    for keyword in forbidden:
        assert keyword not in source, (
            f"read handler imports '{keyword}' — read.* must remain deterministic "
            f"(bicameral decision:wndwxgam2m8yjor0igya)"
        )


def test_protocol_server_init_does_not_open_db_connection(tmp_path):
    """Constraint: lazy ledger connect preserved.

    Bound decision: ``decision:k44cko8xtkcswk55kytz`` — "Lazy connection
    in SurrealDB ledger adapter". ``ProtocolServer(socket_path)`` must not
    open a SurrealDB connection or read the ledger; that happens on first
    method dispatch.
    """
    sock = tmp_path / "d.sock"
    # Construct + register without starting — must not touch the DB.
    server = ProtocolServer(sock)
    register_read_handlers(server)

    # Smoke: no DB-related attributes spuriously populated on the server itself.
    assert not hasattr(server, "_db")
    assert not hasattr(server, "_ledger")
    assert not hasattr(server, "_client")


def test_register_read_handlers_is_idempotent(tmp_path):
    """Re-registering the same method overwrites the prior handler.

    Per-test daemon fixtures will call ``register_read_handlers(server)`` on
    a fresh server each time; this assertion locks the no-conflict contract.
    """
    sock = tmp_path / "d.sock"
    server = ProtocolServer(sock)
    register_read_handlers(server)
    # Second call must not raise.
    register_read_handlers(server)
    assert "read.history" in server._methods
    assert "read.usage_summary" in server._methods
