"""Functionality tests for the LLM-08 token-bucket rate limit (#216 Phase 2).

Covers:
- ``_TokenBucket`` internals (consume, exhaust, refill, cap)
- ``_check_rate_limit`` (env-disable, exhaust, per-session isolation)
- ``handle_ingest`` integration (drive-to-empty + telemetry + ordering)
- ``server.call_tool`` boundary translation for the rate-limit reason
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import TextContent

import server
from handlers import ingest as ingest_module
from handlers.ingest import (
    _RATE_LIMIT_REGISTRY,
    _check_rate_limit,
    _IngestRefused,
    _TokenBucket,
    handle_ingest,
)


def _ctx_with_rate_limit(
    burst: int = 10,
    refill: float = 1.0,
    session_id: str = "test-session",
    max_bytes: int = 1024 * 1024,
):
    ctx = MagicMock()
    ctx.ingest_max_bytes = max_bytes
    ctx.ingest_rate_limit_burst = burst
    ctx.ingest_rate_limit_refill_per_sec = refill
    ctx.session_id = session_id
    ctx.repo_path = "."
    ctx.ledger = object()
    return ctx


# ── _TokenBucket internals ────────────────────────────────────────────


def test_token_bucket_take_consumes_one_when_full() -> None:
    bucket = _TokenBucket(burst=10, refill_per_sec=1.0)
    assert bucket.take() is True
    # Internal counter should drop from 10.0 to 9.0 (no refill yet at this
    # microsecond granularity).
    assert bucket._tokens < 10.0
    assert bucket._tokens >= 8.99  # tolerate a few microseconds of refill


def test_token_bucket_returns_false_when_empty() -> None:
    bucket = _TokenBucket(burst=10, refill_per_sec=0.0)
    for _ in range(10):
        assert bucket.take() is True
    # Refill 0.0 means no replenishment; 11th call must fail.
    assert bucket.take() is False


def test_token_bucket_refills_over_time() -> None:
    bucket = _TokenBucket(burst=10, refill_per_sec=1.0)
    for _ in range(10):
        bucket.take()
    # Bucket is now empty.
    assert bucket.take() is False

    # Mock time to advance by 5 seconds; expect 5 successful takes.
    with patch.object(ingest_module.time, "monotonic", return_value=bucket._last + 5.0):
        successes = sum(1 for _ in range(10) if bucket.take())
    assert successes == 5


def test_token_bucket_caps_at_burst() -> None:
    bucket = _TokenBucket(burst=10, refill_per_sec=1.0)
    # Drain a few tokens.
    bucket.take()
    bucket.take()
    # Advance 100s — naive accumulation would put _tokens at 8 + 100 = 108;
    # cap must hold at 10.
    with patch.object(ingest_module.time, "monotonic", return_value=bucket._last + 100.0):
        # Force a refill computation by attempting a take.
        bucket.take()
    # After that take, _tokens is at most burst - 1 = 9.0.
    assert bucket._tokens <= 9.0
    assert bucket._tokens > 8.0  # confirm the refill actually happened


# ── _check_rate_limit ────────────────────────────────────────────────


def test_check_rate_limit_passes_when_disabled_via_env(monkeypatch) -> None:
    monkeypatch.setenv("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", "1")
    # Even with burst=1 / refill=0 (would normally exhaust on second call)
    # the env override must let every call through.
    for _ in range(100):
        _check_rate_limit("sid", burst=1, refill_per_sec=0.0)


def test_check_rate_limit_raises_when_session_bucket_empty() -> None:
    # burst=1, refill=0.0: first call passes, second guaranteed empty.
    _check_rate_limit("sid-empty", burst=1, refill_per_sec=0.0)
    with pytest.raises(_IngestRefused) as exc_info:
        _check_rate_limit("sid-empty", burst=1, refill_per_sec=0.0)
    assert exc_info.value.reason == "rate_limit_exceeded"
    # #230 Finding 1: detail must NOT leak the session UUID; emit only the
    # bucket-config shape so operators can tune .bicameral/config.yaml.
    assert "sid-empty" not in exc_info.value.detail
    assert "session " not in exc_info.value.detail
    assert exc_info.value.detail == "bucket empty (burst=1, refill=0.0/s)"


@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "TRUE", "Yes", "ON"])
def test_check_rate_limit_disabled_via_truthy_variants(monkeypatch, truthy: str) -> None:
    """#232 Finding 1: env-var disable accepts the canonical
    ``_GUIDED_MODE_TRUTHY`` vocabulary (1/true/yes/on, case-insensitive)."""
    monkeypatch.setenv("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", truthy)
    # burst=0/refill=0 would exhaust on first call; the env disable must
    # short-circuit before bucket allocation.
    for _ in range(5):
        _check_rate_limit("sid-truthy", burst=0, refill_per_sec=0.0)


def test_check_rate_limit_isolates_sessions() -> None:
    # Exhaust session_a's bucket entirely.
    _check_rate_limit("session_a", burst=1, refill_per_sec=0.0)
    with pytest.raises(_IngestRefused):
        _check_rate_limit("session_a", burst=1, refill_per_sec=0.0)
    # session_b is independent; first call must pass.
    _check_rate_limit("session_b", burst=1, refill_per_sec=0.0)


# ── handle_ingest integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_ingest_raises_ingest_refused_on_rate_limit() -> None:
    ctx = _ctx_with_rate_limit(burst=2, refill=0.0, session_id="sid-drive")
    payload = {"k": "v"}

    # First two ingests pass the rate gate (but fail later when they hit
    # the real ledger). We don't care about that — patch _normalize_payload
    # to short-circuit by raising a sentinel that we catch.
    class _Sentinel(Exception):
        pass

    with patch("handlers.ingest._normalize_payload", side_effect=_Sentinel()):
        for _ in range(2):
            with pytest.raises(_Sentinel):
                await handle_ingest(ctx, payload)
        # Third call: rate gate must fire first.
        with pytest.raises(_IngestRefused) as exc_info:
            await handle_ingest(ctx, payload)
    assert exc_info.value.reason == "rate_limit_exceeded"
    # #230 Finding 1: detail no longer leaks session UUID at the boundary.
    assert "sid-drive" not in exc_info.value.detail
    assert "bucket empty" in exc_info.value.detail


@pytest.mark.asyncio
async def test_handle_ingest_emits_refusal_telemetry_before_reraise_on_rate_limit() -> None:
    ctx = _ctx_with_rate_limit(burst=1, refill=0.0, session_id="sid-tele")
    payload = {"k": "v"}

    class _Sentinel(Exception):
        pass

    # Drain the single token via one passing call.
    with patch("handlers.ingest._normalize_payload", side_effect=_Sentinel()):
        with pytest.raises(_Sentinel):
            await handle_ingest(ctx, payload)

    # Next call: rate-limit gate fires; telemetry must be invoked
    # before the exception leaves handle_ingest.
    with patch("handlers.ingest.preflight_telemetry") as telemetry_mock:
        with pytest.raises(_IngestRefused):
            await handle_ingest(ctx, payload)
        telemetry_mock.write_ingest_refusal_event.assert_called_once_with(
            reason="rate_limit_exceeded", session_id="sid-tele"
        )


@pytest.mark.asyncio
async def test_handle_ingest_size_check_runs_before_rate_check() -> None:
    # Empty bucket AND oversized payload; size check must fire first
    # so the rate-bucket state is not consumed.
    ctx = _ctx_with_rate_limit(burst=1, refill=0.0, session_id="sid-order", max_bytes=10)
    oversized = {"decisions": [{"description": "x" * 500}]}

    with pytest.raises(_IngestRefused) as exc_info:
        await handle_ingest(ctx, oversized)
    assert exc_info.value.reason == "size_limit_exceeded"

    # Bucket for sid-order should still hold its single token: rate check
    # was unreached. We verify by calling _check_rate_limit directly —
    # the registry entry created on the FIRST size-exceeding call would
    # only exist if the rate gate had run.
    assert "sid-order" not in _RATE_LIMIT_REGISTRY


# ── server.call_tool boundary translation ────────────────────────────


@pytest.mark.asyncio
async def test_call_tool_translates_rate_limit_refusal_to_text_content_error() -> None:
    raised = _IngestRefused(
        "rate_limit_exceeded",
        detail="bucket empty (burst=1, refill=1.0/s)",
    )
    sync_stub = AsyncMock(return_value=None)
    handle_stub = AsyncMock(side_effect=raised)
    ctx_stub = MagicMock()
    ctx_stub.repo_path = "."

    with (
        patch.object(server.BicameralContext, "from_env", return_value=ctx_stub),
        patch("handlers.sync_middleware.ensure_ledger_synced", sync_stub),
        patch.object(server, "handle_ingest", handle_stub),
    ):
        result = await server.call_tool("bicameral.ingest", {"payload": {"k": "v"}})

    assert isinstance(result, list) and len(result) == 1
    entry = result[0]
    assert isinstance(entry, TextContent)
    body = json.loads(entry.text)
    assert body["error"] == "rate_limit_exceeded"
    assert "bucket empty" in body["detail"]
    action = body["action"]
    assert "ingest_rate_limit_burst" in action
    assert "ingest_rate_limit_refill_per_sec" in action
    assert "BICAMERAL_INGEST_RATE_LIMIT_DISABLE" in action
