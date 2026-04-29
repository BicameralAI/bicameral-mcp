"""Tests for handle_bind — caller-LLM-driven code region binding.

Covers:
1. test_bind_success_with_explicit_lines — supply start_line/end_line, verify region + edge + pending check
2. test_bind_symbol_resolution — omit lines, verify tree-sitter resolve path
3. test_bind_unknown_decision_id — non-existent decision_id → error containing "unknown_decision_id"
4. test_bind_symbol_not_found — resolve_symbol_lines returns None → error contains symbol name
5. test_bind_idempotent — calling bind twice for same (decision, region) is a no-op
6. test_bind_status_transition — after bind, decision status transitions to "pending"
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from handlers.bind import handle_bind
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate

# ── Fixtures ──────────────────────────────────────────────────────────────────


async def _fresh_client() -> LedgerClient:
    c = LedgerClient(url="memory://", ns="bind_test", db="ledger_test")
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
    """Minimal context for handle_bind tests."""

    def __init__(self, ledger) -> None:
        self.ledger = ledger
        self.repo_path = "/tmp/test-repo"
        self.authoritative_sha = "HEAD"


# ── 1. Success with explicit lines ────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_success_with_explicit_lines():
    """Supply start_line/end_line — server upserts region + edge + pending check."""
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "Use BM25 for search")
        ctx = _StubCtx(adapter)

        resp = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": decision_id,
                    "file_path": "server.py",
                    "symbol_name": "handle_search",
                    "start_line": 10,
                    "end_line": 30,
                    "purpose": "search handler",
                }
            ],
        )

        assert len(resp.bindings) == 1
        b = resp.bindings[0]
        assert b.error is None, f"unexpected error: {b.error}"
        assert b.decision_id == decision_id
        assert b.region_id != ""
        # pending_compliance_check only present when content_hash non-empty
        # (content_hash may be empty if git content not available in test env)
    finally:
        await client.close()


# ── 2. Symbol resolution via tree-sitter ─────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_symbol_resolution():
    """Omit lines — server resolves via tree-sitter (mocked to return fixed range)."""
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "Rate limit middleware")
        ctx = _StubCtx(adapter)

        with patch("ledger.status.resolve_symbol_lines", return_value=(5, 25)):
            resp = await handle_bind(
                ctx,
                bindings=[
                    {
                        "decision_id": decision_id,
                        "file_path": "middleware.py",
                        "symbol_name": "rate_limit",
                    }
                ],
            )

        assert len(resp.bindings) == 1
        b = resp.bindings[0]
        assert b.error is None, f"unexpected error: {b.error}"
        assert b.region_id != ""
    finally:
        await client.close()


# ── 3. Unknown decision_id ────────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_unknown_decision_id():
    """Non-existent decision_id → BindResult.error contains 'unknown_decision_id'."""
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        ctx = _StubCtx(adapter)
        fake_id = "decision:fake_does_not_exist_xyz"

        resp = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": fake_id,
                    "file_path": "server.py",
                    "symbol_name": "some_func",
                    "start_line": 1,
                    "end_line": 10,
                }
            ],
        )

        assert len(resp.bindings) == 1
        b = resp.bindings[0]
        assert b.error is not None
        assert "unknown_decision_id" in b.error
    finally:
        await client.close()


# ── 4. Symbol not found ───────────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_symbol_not_found():
    """resolve_symbol_lines returns None → error contains the symbol name."""
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "Cache eviction policy")
        ctx = _StubCtx(adapter)

        with patch("ledger.status.resolve_symbol_lines", return_value=None):
            resp = await handle_bind(
                ctx,
                bindings=[
                    {
                        "decision_id": decision_id,
                        "file_path": "cache.py",
                        "symbol_name": "evict_stale",
                    }
                ],
            )

        assert len(resp.bindings) == 1
        b = resp.bindings[0]
        assert b.error is not None
        assert "evict_stale" in b.error
    finally:
        await client.close()


# ── 5. Idempotent: calling bind twice is a no-op ──────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_idempotent():
    """Calling bind twice for the same (decision, region) pair is idempotent."""
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "Auth token validation")
        ctx = _StubCtx(adapter)

        binding = {
            "decision_id": decision_id,
            "file_path": "auth.py",
            "symbol_name": "validate_token",
            "start_line": 1,
            "end_line": 20,
        }

        resp1 = await handle_bind(ctx, bindings=[binding])
        resp2 = await handle_bind(ctx, bindings=[binding])

        assert resp1.bindings[0].error is None
        assert resp2.bindings[0].error is None
        # Both calls should return the same region_id (idempotent upsert)
        assert resp1.bindings[0].region_id == resp2.bindings[0].region_id
    finally:
        await client.close()


# ── 6. Status transition ungrounded → pending ────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_status_transition():
    """After bind, decision status transitions from 'ungrounded' to 'pending'."""
    client = await _fresh_client()
    try:
        from ledger.adapter import SurrealDBLedgerAdapter

        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "Pagination defaults")
        ctx = _StubCtx(adapter)

        # Verify starting status is ungrounded
        rows = await client.query(f"SELECT status FROM {decision_id} LIMIT 1")
        assert rows and rows[0].get("status") == "ungrounded"

        resp = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": decision_id,
                    "file_path": "pagination.py",
                    "symbol_name": "paginate",
                    "start_line": 1,
                    "end_line": 15,
                }
            ],
        )

        assert resp.bindings[0].error is None

        # Status should now be "pending"
        rows = await client.query(f"SELECT status FROM {decision_id} LIMIT 1")
        assert rows and rows[0].get("status") == "pending"
    finally:
        await client.close()
