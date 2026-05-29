"""Tests for #334 Shape 2 — server-enforced validate/bind snapshot handshake.

When the caller threads ``expected_indexed_at_sha`` (from a prior
``validate_symbols`` response) into a ``bicameral_bind`` binding, the handler
must:

1. Accept the binding when the SHA matches the ref bind will resolve at.
2. Reject with ``snapshot_mismatch`` when the SHA disagrees, before any DB
   round-trip or git resolution.
3. Fall back to pre-Shape-2 behavior (no enforcement) when the field is
   omitted or empty — backward compatibility for existing callers.
4. The rejection happens before decision-exists lookup, so a stale snapshot
   on an unknown decision still reports ``snapshot_mismatch`` not
   ``unknown_decision_id`` — the snapshot mismatch is the actionable signal.

Sociable tests per ``CLAUDE.md`` — real ``SurrealDBLedgerAdapter`` over
``memory://``; only the file/symbol resolution path is patched, since the
Shape 2 reject runs entirely before that path on mismatched bindings.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from handlers.bind import handle_bind
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate


async def _fresh_client() -> LedgerClient:
    c = LedgerClient(url="memory://", ns="snapshot_test", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


async def _seed_decision(client: LedgerClient, description: str = "test decision") -> str:
    rows = await client.query(
        "CREATE decision SET description = $d, source_type = 'manual', status = 'ungrounded'",
        {"d": description},
    )
    return str(rows[0]["id"])


class _StubCtx:
    """Minimal ctx — authoritative_sha pinned so we know exactly what bind resolves at."""

    def __init__(self, ledger, authoritative_sha: str = "abc123") -> None:
        self.ledger = ledger
        self.repo_path = "/tmp/test-repo"
        self.authoritative_sha = authoritative_sha


@pytest.mark.phase2
@pytest.mark.asyncio
@patch("ledger.status.resolve_symbol_lines", return_value=(10, 30))
@patch("ledger.status.get_git_content", return_value="# stub")
async def test_snapshot_handshake_match_accepts(_mock_git, _mock_resolve):
    """Matching expected_indexed_at_sha → binding accepted as normal."""
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "decision under snapshot match")
        ctx = _StubCtx(adapter, authoritative_sha="abc123")

        resp = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": decision_id,
                    "file_path": "server.py",
                    "symbol_name": "handle_search",
                    "start_line": 10,
                    "end_line": 30,
                    "expected_indexed_at_sha": "abc123",
                }
            ],
        )

        assert len(resp.bindings) == 1
        b = resp.bindings[0]
        assert b.error is None, f"unexpected error on matching SHA: {b.error}"
        assert b.region_id != ""
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_snapshot_handshake_mismatch_rejects():
    """Mismatched expected_indexed_at_sha → snapshot_mismatch error, no DB write.

    The reject path runs before decision-exists lookup, so we do not need to
    patch git resolution — the handler bails before touching it.
    """
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "decision under stale snapshot")
        ctx = _StubCtx(adapter, authoritative_sha="abc123")

        resp = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": decision_id,
                    "file_path": "server.py",
                    "symbol_name": "handle_search",
                    "start_line": 10,
                    "end_line": 30,
                    "expected_indexed_at_sha": "def456",  # stale
                }
            ],
        )

        assert len(resp.bindings) == 1
        b = resp.bindings[0]
        assert b.error is not None
        assert "snapshot_mismatch" in b.error
        assert "def456" in b.error and "abc123" in b.error
        assert b.region_id == ""
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
@patch("ledger.status.resolve_symbol_lines", return_value=(10, 30))
@patch("ledger.status.get_git_content", return_value="# stub")
async def test_snapshot_handshake_omitted_preserves_legacy_behavior(_mock_git, _mock_resolve):
    """Field omitted entirely → bind behaves as pre-Shape-2 (no enforcement)."""
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "decision via legacy caller")
        ctx = _StubCtx(adapter, authoritative_sha="abc123")

        resp = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": decision_id,
                    "file_path": "server.py",
                    "symbol_name": "handle_search",
                    "start_line": 10,
                    "end_line": 30,
                    # expected_indexed_at_sha intentionally omitted
                }
            ],
        )

        assert len(resp.bindings) == 1
        b = resp.bindings[0]
        assert b.error is None, f"unexpected error on omitted field: {b.error}"
        assert b.region_id != ""
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
@patch("ledger.status.resolve_symbol_lines", return_value=(10, 30))
@patch("ledger.status.get_git_content", return_value="# stub")
async def test_snapshot_handshake_empty_string_preserves_legacy_behavior(_mock_git, _mock_resolve):
    """Field present but empty string → no enforcement (matches omitted case)."""
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "decision via empty-sha caller")
        ctx = _StubCtx(adapter, authoritative_sha="abc123")

        resp = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": decision_id,
                    "file_path": "server.py",
                    "symbol_name": "handle_search",
                    "start_line": 10,
                    "end_line": 30,
                    "expected_indexed_at_sha": "",  # explicit empty
                }
            ],
        )

        assert len(resp.bindings) == 1
        b = resp.bindings[0]
        assert b.error is None, f"unexpected error on empty SHA: {b.error}"
        assert b.region_id != ""
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_snapshot_mismatch_short_circuits_before_decision_lookup():
    """Stale SHA + unknown decision_id → snapshot_mismatch (not unknown_decision_id).

    The Shape 2 reject runs before the decision-exists check, so the
    snapshot signal is the one the caller acts on. This is the cheap-reject
    contract: avoid DB round-trips when the snapshot is already known stale.
    """
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        ctx = _StubCtx(adapter, authoritative_sha="abc123")

        resp = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": "decision:does_not_exist",
                    "file_path": "server.py",
                    "symbol_name": "handle_search",
                    "start_line": 10,
                    "end_line": 30,
                    "expected_indexed_at_sha": "stale_sha_xyz",
                }
            ],
        )

        assert len(resp.bindings) == 1
        b = resp.bindings[0]
        assert b.error is not None
        assert "snapshot_mismatch" in b.error
        assert "unknown_decision_id" not in b.error
    finally:
        await client.close()
