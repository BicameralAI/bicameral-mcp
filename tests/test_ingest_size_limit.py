"""Functionality tests for the LLM-02 payload-size guardrail (#216 Phase 1).

Covers ``_check_payload_size`` (pure helper) and ``handle_ingest``
integration (gate + telemetry emission + propagation). The MCP-boundary
translation lives in ``test_server_ingest_refusal.py``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.ingest import _check_payload_size, _IngestRefused, handle_ingest


def _ctx_with_max_bytes(max_bytes: int, session_id: str = "test-session"):
    """Stub BicameralContext that only exposes the fields handle_ingest
    reads before any ledger or normalization work runs."""
    ctx = MagicMock()
    ctx.ingest_max_bytes = max_bytes
    ctx.session_id = session_id
    ctx.repo_path = "."
    # ledger has no `connect`; we want handle_ingest to skip the connect
    # branch entirely. spec=[] would block AsyncMock attr lookup; instead
    # use a plain object whose hasattr("connect") is False.
    ctx.ledger = object()
    return ctx


def test_check_payload_size_passes_when_under_cap() -> None:
    # Small payload, generous cap — must not raise.
    _check_payload_size({"k": "v"}, 1024)


def test_check_payload_size_raises_at_exact_excess() -> None:
    # Build a payload that serializes to exactly cap + 1 bytes.
    cap = 50
    # `{"k": "<padding>"}` — figure out the padding length so the
    # serialized form is cap + 1 bytes.
    skeleton = json.dumps({"k": ""}).encode("utf-8")
    padding_len = (cap + 1) - len(skeleton)
    payload = {"k": "x" * padding_len}
    serialized_size = len(json.dumps(payload).encode("utf-8"))
    assert serialized_size == cap + 1, (
        f"test setup wrong: got {serialized_size}, expected {cap + 1}"
    )

    with pytest.raises(_IngestRefused) as exc_info:
        _check_payload_size(payload, cap)
    assert exc_info.value.reason == "size_limit_exceeded"
    assert "bytes" in exc_info.value.detail


def test_check_payload_size_uses_serialized_byte_count() -> None:
    # The gate measures the serialized JSON form, not the raw string
    # content of any single field. With unicode chars the serialized form
    # is multiple bytes per char (either UTF-8 multi-byte or escaped
    # ``\uXXXX``); both routes produce byte counts strictly greater than
    # the inner-string char count. Pick a cap between the inner char
    # count and the serialized byte count: a naive char-based check would
    # pass, the byte-based check must refuse.
    raw = "\U0001f600" * 30  # 30 grinning-face chars, 30 chars long
    payload = {"k": raw}
    inner_char_count = len(raw)
    serialized_byte_count = len(json.dumps(payload, default=str).encode("utf-8"))
    assert serialized_byte_count > inner_char_count, "test setup needs multi-byte chars"

    cap = (inner_char_count + serialized_byte_count) // 2
    assert inner_char_count <= cap < serialized_byte_count

    with pytest.raises(_IngestRefused) as exc_info:
        _check_payload_size(payload, cap)
    assert exc_info.value.reason == "size_limit_exceeded"


def test_check_payload_size_includes_schema_overhead() -> None:
    # Tiny inner text, but nested-object overhead pushes past cap.
    payload = {"decisions": [{"id": f"d-{i}", "title": "x", "description": "y"} for i in range(50)]}
    serialized_size = len(json.dumps(payload).encode("utf-8"))
    # Pick a cap that the inner text alone (~50 chars total) is well under
    # but the serialized form (with all the JSON braces / quotes / keys)
    # exceeds.
    cap = 200
    assert serialized_size > cap

    with pytest.raises(_IngestRefused) as exc_info:
        _check_payload_size(payload, cap)
    assert exc_info.value.reason == "size_limit_exceeded"


@pytest.mark.asyncio
async def test_handle_ingest_raises_ingest_refused_on_size_excess() -> None:
    cap = 100
    oversized = {"decisions": [{"description": "x" * 500}]}
    ctx = _ctx_with_max_bytes(cap)
    # Replace the ledger with a recording mock so we can assert no write happened.
    ledger_mock = MagicMock()
    ledger_mock.connect = AsyncMock()
    ledger_mock.ingest_payload = AsyncMock()
    ctx.ledger = ledger_mock

    with pytest.raises(_IngestRefused) as exc_info:
        await handle_ingest(ctx, oversized)

    assert exc_info.value.reason == "size_limit_exceeded"
    assert "bytes" in exc_info.value.detail
    assert str(cap) in exc_info.value.detail
    # No ledger write should have happened — the gate ran before connect.
    ledger_mock.ingest_payload.assert_not_called()
    # Per-216 ordering invariant (devil's-advocate finding): a refused payload
    # must NOT pay the ledger-connect handshake either. Locks the gate-before-
    # connect ordering against drift.
    ledger_mock.connect.assert_not_called()


@pytest.mark.asyncio
async def test_handle_ingest_emits_refusal_telemetry_before_reraise_on_size_excess() -> None:
    cap = 100
    oversized = {"decisions": [{"description": "y" * 500}]}
    ctx = _ctx_with_max_bytes(cap, session_id="sid-abc")

    with patch("handlers.ingest.preflight_telemetry") as telemetry_mock:
        with pytest.raises(_IngestRefused):
            await handle_ingest(ctx, oversized)
        telemetry_mock.write_ingest_refusal_event.assert_called_once_with(
            reason="size_limit_exceeded", session_id="sid-abc"
        )


# ── #232 Finding 2: malformed_payload (circular-ref / non-serializable) ──


def test_check_payload_size_handles_circular_ref_payload() -> None:
    """#232 Finding 2: a circular-reference dict raises ValueError from
    json.dumps; the gate must translate to _IngestRefused('malformed_payload')
    at the same MCP boundary as the other refusals — no fail-open path."""
    circular: dict = {"k": "v"}
    circular["self"] = circular  # circular reference

    with pytest.raises(_IngestRefused) as exc_info:
        _check_payload_size(circular, max_bytes=1024)
    assert exc_info.value.reason == "malformed_payload"
    assert "JSON-serializable" in exc_info.value.detail
    # Surface the underlying exception type so operators can diagnose.
    assert "ValueError" in exc_info.value.detail


def test_check_payload_size_handles_recursion_error_payload() -> None:
    """A payload whose serialization raises RecursionError (e.g., deeply
    nested object) is also translated to malformed_payload, not leaked as
    an unhandled exception past the gate. Patches ``json.dumps`` to raise
    RecursionError unconditionally — tests the gate's exception-handling
    contract, not a specific input shape."""
    payload = {"k": "v"}

    def raise_recursion_error(*_args, **_kwargs):
        raise RecursionError("simulated deep nesting")

    with patch("handlers.ingest.json.dumps", side_effect=raise_recursion_error):
        with pytest.raises(_IngestRefused) as exc_info:
            _check_payload_size(payload, max_bytes=1024)
    assert exc_info.value.reason == "malformed_payload"
    assert "RecursionError" in exc_info.value.detail


def test_check_payload_size_handles_typeerror_payload() -> None:
    """Non-JSON-serializable object that ALSO can't be coerced via default=str
    raises TypeError; same translation."""

    class NonSerializableObject:
        def __str__(self):
            raise TypeError("intentional: cannot stringify")

    payload = {"opaque": NonSerializableObject()}
    with pytest.raises(_IngestRefused) as exc_info:
        _check_payload_size(payload, max_bytes=1024)
    assert exc_info.value.reason == "malformed_payload"
    assert "TypeError" in exc_info.value.detail
