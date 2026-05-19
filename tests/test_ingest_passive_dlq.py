"""Sociable tests for #418 Phase 0a — passive ingest_mode + DLQ routing.

Per ``CLAUDE.md`` § Sociable Testing for UX Paths: handler tests must use a
real ``SurrealDBLedgerAdapter`` over ``memory://`` and ``SimpleNamespace``
for ``ctx``. The previous solitary pattern (``MagicMock`` ctx + ``AsyncMock``
ledger) is what let production bugs sit invisibly under green coverage; new
handler tests intentionally do not follow that pattern.

Scope:
- soft-gate behavior split by ``ingest_mode``
- hard-gate fail-fast preserved in BOTH modes
- DLQ file permissions (POSIX-only — Windows ACL model doesn't map cleanly)
- audit-event ``disposition`` field flows through both arms
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from handlers.ingest import _IngestRefused, handle_ingest
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate

# ── Sociable substrate: real ledger over memory:// ──────────────────────────

_NS_COUNTER = 0


async def _make_real_adapter() -> SurrealDBLedgerAdapter:
    """Spin up an isolated SurrealDB memory backend for one test.

    Mirrors ``tests/test_sync_middleware.py::_make_real_adapter`` and
    ``tests/test_codegenome_continuity_service.py::_fresh_adapter``.
    """
    global _NS_COUNTER
    _NS_COUNTER += 1
    client = LedgerClient(url="memory://", ns=f"dlq_test_{_NS_COUNTER}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    return adapter


def _make_ctx(adapter, repo_path: str, *, max_bytes: int = 1_000_000) -> SimpleNamespace:
    """Build a SimpleNamespace ctx with only the fields handle_ingest reads
    before normalization. SimpleNamespace (not MagicMock) so missing fields
    raise AttributeError rather than silently inventing themselves."""
    return SimpleNamespace(
        repo_path=repo_path,
        head_sha="0" * 40,
        authoritative_ref="main",
        authoritative_sha="0" * 40,
        ledger=adapter,
        code_graph=SimpleNamespace(resolve_symbols=lambda p: p),
        session_id="test-session-418",
        signer_email_fallback="local-part-only",
        render_source_attribution="redacted",
        ingest_max_bytes=max_bytes,
        ingest_rate_limit_burst=10_000,  # generous, so rate gate doesn't fire
        ingest_rate_limit_refill_per_sec=1_000.0,
        query_timeout_read_seconds=5.0,
        query_timeout_drift_seconds=30.0,
    )


@pytest.fixture
def isolated_dlq_root(tmp_path, monkeypatch) -> Path:
    """Point the DLQ store at a tmp dir for this test."""
    monkeypatch.setenv("BICAMERAL_DATA_PATH", str(tmp_path))
    return tmp_path / "dlq"


# ── 1. active-mode soft-gate fail-fast (unchanged from today) ───────────────


@pytest.mark.asyncio
async def test_active_size_overflow_still_raises(tmp_path, isolated_dlq_root):
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path), max_bytes=100)
    payload = {"query": "x", "title": "t", "decisions": [{"description": "y" * 500}]}

    with pytest.raises(_IngestRefused) as exc:
        await handle_ingest(ctx, payload, source_scope="linear", ingest_mode="active")

    assert exc.value.reason == "size_limit_exceeded"
    # No DLQ row written for active-mode refusal.
    assert not isolated_dlq_root.exists() or not any(isolated_dlq_root.glob("*.jsonl"))


# ── 2. passive-mode soft-gate WARN + DLQ + continue ─────────────────────────


@pytest.mark.asyncio
async def test_passive_size_overflow_writes_dlq_and_returns(tmp_path, isolated_dlq_root):
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path), max_bytes=100)
    payload = {"query": "x", "title": "t", "decisions": [{"description": "y" * 500}]}

    result = await handle_ingest(ctx, payload, source_scope="linear", ingest_mode="passive")

    # Returned normally — no raise.
    assert result.ingested is False
    assert result.stats.dlqd_count == {"size_limit_exceeded": 1}
    assert result.stats.intents_created == 0

    # DLQ JSONL row written for the "linear" source.
    jsonl_path = isolated_dlq_root / "linear.jsonl"
    assert jsonl_path.exists(), "DLQ JSONL was not created"
    rows = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line]
    assert len(rows) == 1
    row = rows[0]
    assert row["reason"] == "size_limit_exceeded"
    assert row["source_id"] == "linear"
    assert row["content_hash"].startswith("sha256:")
    assert row["byte_size"] > 100

    # Sidecar exists and has the right content.
    sidecar = Path(row["raw_content_path"])
    assert sidecar.exists()
    assert sidecar.read_bytes() == json.dumps(payload, default=str).encode("utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only file-mode semantics")
@pytest.mark.asyncio
async def test_passive_dlq_sidecar_is_mode_0600(tmp_path, isolated_dlq_root):
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path), max_bytes=100)
    payload = {"query": "x", "decisions": [{"description": "y" * 500}]}

    result = await handle_ingest(ctx, payload, source_scope="notion", ingest_mode="passive")
    assert result.stats.dlqd_count == {"size_limit_exceeded": 1}

    sidecar = next((isolated_dlq_root / "raw").glob("*.bin"))
    mode = sidecar.stat().st_mode & 0o777
    assert mode == 0o600, f"sidecar mode is 0o{mode:o}, expected 0o600"

    dir_mode = (isolated_dlq_root / "raw").stat().st_mode & 0o777
    assert dir_mode == 0o700, f"raw dir mode is 0o{dir_mode:o}, expected 0o700"


# ── 3. hard-gate fail-fast preserved in BOTH modes ──────────────────────────


@pytest.mark.asyncio
async def test_passive_secret_still_raises(tmp_path, isolated_dlq_root):
    """Hard gate: sensitive_data:secret must fail-fast even in passive mode."""
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path))
    # AWS access key pattern — well-known credential shape detected by
    # the sensitive-data gate. Bypass the canary gate (cheaper) by keeping
    # the rest of the payload benign.
    payload = {
        "query": "deploy plan",
        "decisions": [{"description": "use key AKIAIOSFODNN7EXAMPLE for upload"}],
    }

    with pytest.raises(_IngestRefused) as exc:
        await handle_ingest(ctx, payload, source_scope="linear", ingest_mode="passive")

    assert exc.value.reason.startswith("sensitive_data:")
    # No DLQ row — hard gates never reach DLQ.
    assert not (isolated_dlq_root / "linear.jsonl").exists()


@pytest.mark.asyncio
async def test_passive_malformed_still_raises(tmp_path, isolated_dlq_root):
    """Hard gate: malformed_payload (non-JSON-serializable) must fail-fast
    even in passive mode — there's nothing serializable to write to DLQ.

    Trigger: a ``set()`` is not JSON-serializable and ``default=str`` will
    serialize it as the literal string ``"{...}"`` — which means we need an
    object that ``default=str`` cannot resolve. ``object()`` works because
    ``str(object())`` produces a stable repr but the json encoder still
    fails before ``default`` is consulted for nested non-serializable types.
    The cleanest trigger is a self-referencing dict (RecursionError caught).
    """
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path))

    # Self-referencing structure: json.dumps recurses and raises
    # ValueError("Circular reference detected") — one of the caught types
    # in _check_payload_size's translation block.
    payload: dict = {"query": "x"}
    payload["self"] = payload

    with pytest.raises(_IngestRefused) as exc:
        await handle_ingest(ctx, payload, source_scope="linear", ingest_mode="passive")

    assert exc.value.reason == "malformed_payload"
    assert not (isolated_dlq_root / "linear.jsonl").exists()


# ── 3b. path-traversal defense in source_id ─────────────────────────────────


@pytest.mark.asyncio
async def test_passive_dlq_rejects_traversal_source_scope(tmp_path, isolated_dlq_root):
    """A malicious / buggy caller passing ``source_scope="../../etc/foo"``
    must NOT write the JSONL outside the DLQ root. The store sanitizes
    the value to ``"unknown"`` and records the original under
    ``source_id_raw`` for audit.
    """
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path), max_bytes=100)
    payload = {"query": "x", "decisions": [{"description": "y" * 500}]}

    result = await handle_ingest(ctx, payload, source_scope="../../etc/evil", ingest_mode="passive")

    assert result.stats.dlqd_count == {"size_limit_exceeded": 1}

    # JSONL landed under "unknown.jsonl" inside the DLQ root, not at
    # ../../etc/evil.jsonl outside it.
    assert (isolated_dlq_root / "unknown.jsonl").exists()
    # Confirm nothing slipped outside the dlq root.
    parent_of_root = isolated_dlq_root.parent
    suspect = parent_of_root / "etc"
    assert not suspect.exists(), "traversal escaped the DLQ root"

    row = json.loads((isolated_dlq_root / "unknown.jsonl").read_text().splitlines()[0])
    assert row["source_id"] == "unknown"
    assert row["source_id_raw"] == "../../etc/evil"  # original preserved for audit


@pytest.mark.asyncio
async def test_active_secret_still_raises(tmp_path, isolated_dlq_root):
    """Symmetric check: hard gate behavior is identical across modes."""
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path))
    payload = {
        "query": "deploy plan",
        "decisions": [{"description": "use key AKIAIOSFODNN7EXAMPLE for upload"}],
    }

    with pytest.raises(_IngestRefused) as exc:
        await handle_ingest(ctx, payload, source_scope="linear", ingest_mode="active")

    assert exc.value.reason.startswith("sensitive_data:")


# ── 4. audit event disposition field ────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_event_carries_disposition_warned_and_dlqd(tmp_path, isolated_dlq_root):
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path), max_bytes=100)
    payload = {"query": "x", "decisions": [{"description": "y" * 500}]}

    with patch("audit_log.emit") as emit:
        await handle_ingest(ctx, payload, source_scope="linear", ingest_mode="passive")

    refusal_calls = [c for c in emit.call_args_list if "INGEST_REFUSAL" in str(c)]
    # Find the call carrying disposition. The helper emits one INGEST_REFUSAL
    # per refused payload; assert the disposition kwarg was warned_and_dlqd.
    disposed = [c for c in refusal_calls if c.kwargs.get("disposition") == "warned_and_dlqd"]
    assert len(disposed) == 1, (
        f"expected one warned_and_dlqd refusal emit, got {len(disposed)} from {refusal_calls!r}"
    )


@pytest.mark.asyncio
async def test_audit_event_carries_disposition_rejected_on_active(tmp_path, isolated_dlq_root):
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path), max_bytes=100)
    payload = {"query": "x", "decisions": [{"description": "y" * 500}]}

    with patch("audit_log.emit") as emit:
        with pytest.raises(_IngestRefused):
            await handle_ingest(ctx, payload, source_scope="linear", ingest_mode="active")

    refusal_calls = [c for c in emit.call_args_list if "INGEST_REFUSAL" in str(c)]
    disposed = [c for c in refusal_calls if c.kwargs.get("disposition") == "rejected"]
    assert len(disposed) == 1, (
        f"expected one rejected refusal emit, got {len(disposed)} from {refusal_calls!r}"
    )


# ── 5. hard-gate emit still carries disposition=rejected in passive mode ────


@pytest.mark.asyncio
async def test_hard_gate_disposition_rejected_in_passive(tmp_path, isolated_dlq_root):
    """The disposition field must report 'rejected' for hard gates regardless
    of mode — observability of the security floor must be uniform."""
    adapter = await _make_real_adapter()
    ctx = _make_ctx(adapter, str(tmp_path))
    payload = {
        "query": "x",
        "decisions": [{"description": "use AKIAIOSFODNN7EXAMPLE secret"}],
    }

    with patch("audit_log.emit") as emit:
        with pytest.raises(_IngestRefused):
            await handle_ingest(ctx, payload, source_scope="linear", ingest_mode="passive")

    refusal_calls = [c for c in emit.call_args_list if "INGEST_REFUSAL" in str(c)]
    rejected_calls = [c for c in refusal_calls if c.kwargs.get("disposition") == "rejected"]
    warned_calls = [c for c in refusal_calls if c.kwargs.get("disposition") == "warned_and_dlqd"]
    assert len(rejected_calls) == 1
    assert len(warned_calls) == 0
