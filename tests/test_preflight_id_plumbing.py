"""Tests that preflight_id flows from the caller's MCP arguments through
each handler back into the response (#65, Phase 2).

The handler-level tests construct minimal stub ctx objects and minimal stub
ledger adapters — we're verifying the **plumb-through**, not the handler's
own business logic. Full end-to-end coverage lives in the existing phase2/3
suites.
"""

from __future__ import annotations

import importlib
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _isolate_home(monkeypatch, tmp_path: Path) -> None:
    """Reroute HOME so preflight_telemetry / ratify / bind don't write into
    the developer's real ~/.bicameral during plumb-through tests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    import preflight_telemetry as pt

    importlib.reload(pt)


# ── PreflightResponse: id is generated when telemetry is on ─────────


@pytest.mark.asyncio
async def test_preflight_response_has_uuid_id_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_MUTE", "1")  # short-circuit
    _isolate_home(monkeypatch, tmp_path)
    # Reload preflight handler to pick up the new pt module path.
    import handlers.preflight as preflight_handler

    importlib.reload(preflight_handler)

    ctx = SimpleNamespace(guided_mode=False, session_id="s1")
    resp = await preflight_handler.handle_preflight(
        ctx,
        topic="Stripe webhook payment intent",
        file_paths=["routes/webhook.py"],
    )
    assert resp.preflight_id is not None
    assert _UUID4_RE.match(resp.preflight_id), resp.preflight_id


@pytest.mark.asyncio
async def test_preflight_response_id_none_when_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_MUTE", "1")
    _isolate_home(monkeypatch, tmp_path)
    import handlers.preflight as preflight_handler

    importlib.reload(preflight_handler)

    ctx = SimpleNamespace(guided_mode=False, session_id="s1")
    resp = await preflight_handler.handle_preflight(
        ctx,
        topic="Stripe webhook payment intent",
        file_paths=["routes/webhook.py"],
    )
    assert resp.preflight_id is None


@pytest.mark.asyncio
async def test_preflight_id_unique_per_call(monkeypatch, tmp_path):
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_MUTE", "1")
    _isolate_home(monkeypatch, tmp_path)
    import handlers.preflight as preflight_handler

    importlib.reload(preflight_handler)

    ctx = SimpleNamespace(guided_mode=False, session_id="s1")
    a = await preflight_handler.handle_preflight(ctx, topic="topic one alpha")
    b = await preflight_handler.handle_preflight(ctx, topic="topic two beta")
    assert a.preflight_id != b.preflight_id
    assert a.preflight_id and b.preflight_id


# ── link_commit echoes caller-supplied preflight_id ────────────────


@pytest.mark.asyncio
async def test_link_commit_passes_through_preflight_id(monkeypatch, tmp_path):
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)  # off
    _isolate_home(monkeypatch, tmp_path)
    import handlers.link_commit as lc

    importlib.reload(lc)

    ledger = MagicMock()
    ledger.ingest_commit = AsyncMock(
        return_value={
            "commit_hash": "abc123",
            "synced": True,
            "reason": "new_commit",
            "regions_updated": 0,
            "decisions_reflected": 0,
            "decisions_drifted": 0,
            "undocumented_symbols": [],
            "sweep_scope": "head_only",
            "range_size": 0,
            "pending_compliance_checks": [],
            "pending_grounding_checks": [],
        }
    )
    ledger.backfill_empty_hashes = AsyncMock()

    ctx = SimpleNamespace(
        ledger=ledger,
        repo_path=str(tmp_path),
        drift_analyzer=None,
        authoritative_ref="",
        _sync_state={},
        session_id="s1",
    )
    resp = await lc.handle_link_commit(ctx, "abc123", preflight_id="caller-pid-123")
    assert resp.preflight_id == "caller-pid-123"


# ── bind echoes caller-supplied preflight_id ────────────────────────


@pytest.mark.asyncio
async def test_bind_passes_through_preflight_id(monkeypatch, tmp_path):
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)
    _isolate_home(monkeypatch, tmp_path)
    import handlers.bind as bind_handler

    importlib.reload(bind_handler)

    # _do_bind requires a deeply mocked ledger; patch it out and check the
    # outer handle_bind threads preflight_id correctly.
    fake_response = bind_handler.BindResponse(bindings=[])

    async def _fake_do_bind(ctx, bindings):
        return fake_response

    monkeypatch.setattr(bind_handler, "_do_bind", _fake_do_bind)

    # repo_write_barrier is an async ctx manager; replace with a no-op.
    class _FakeBarrier:
        async def __aenter__(self):
            return SimpleNamespace(held_ms=0.0)

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(bind_handler, "repo_write_barrier", lambda ctx: _FakeBarrier())

    ctx = SimpleNamespace(session_id="s1")
    resp = await bind_handler.handle_bind(
        ctx,
        [{"decision_id": "d1", "file_path": "a.py", "symbol_name": "f"}],
        preflight_id="caller-pid-bind",
    )
    assert resp.preflight_id == "caller-pid-bind"


# ── ratify echoes caller-supplied preflight_id ──────────────────────


@pytest.mark.asyncio
async def test_ratify_passes_through_preflight_id(monkeypatch, tmp_path):
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)
    _isolate_home(monkeypatch, tmp_path)
    import handlers.ratify as ratify_handler

    importlib.reload(ratify_handler)

    # Mock out ledger queries.
    monkeypatch.setattr(ratify_handler, "decision_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(
        ratify_handler, "project_decision_status", AsyncMock(return_value="reflected")
    )
    monkeypatch.setattr(ratify_handler, "update_decision_status", AsyncMock())

    fake_client = MagicMock()
    fake_client.query = AsyncMock(
        side_effect=[
            [{"signoff": None}],  # initial select
            None,  # update
        ]
    )
    fake_inner = SimpleNamespace(_client=fake_client)
    fake_ledger = SimpleNamespace(_inner=fake_inner)

    ctx = SimpleNamespace(
        ledger=fake_ledger,
        authoritative_sha="abc",
        session_id="s1",
    )
    resp = await ratify_handler.handle_ratify(
        ctx,
        "decision:abc",
        "alice",
        note="ok",
        action="ratify",
        preflight_id="caller-pid-ratify",
    )
    assert resp.preflight_id == "caller-pid-ratify"


# ── update returns a dict carrying preflight_id ─────────────────────


@pytest.mark.asyncio
async def test_update_returns_preflight_id_in_dict(monkeypatch, tmp_path):
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)
    _isolate_home(monkeypatch, tmp_path)
    import handlers.update as update_handler

    importlib.reload(update_handler)

    # Force the version fetcher to a known value.
    monkeypatch.setattr(update_handler, "_fetch_recommended_version", lambda: "0.99.0")

    out = await update_handler.handle_update(
        action="check",
        current_version="0.0.1",
        repo_path="",
        preflight_id="caller-pid-update",
    )
    assert out.get("preflight_id") == "caller-pid-update"
    assert out["status"] == "update_available"


@pytest.mark.asyncio
async def test_update_unknown_action_still_carries_preflight_id(monkeypatch, tmp_path):
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)
    _isolate_home(monkeypatch, tmp_path)
    import handlers.update as update_handler

    importlib.reload(update_handler)

    out = await update_handler.handle_update(
        action="bogus",
        current_version="0.0.1",
        repo_path="",
        preflight_id="caller-pid-update-bogus",
    )
    assert out.get("preflight_id") == "caller-pid-update-bogus"
    assert out["status"] == "error"


# ── Engagement row is written when telemetry is on ──────────────────


@pytest.mark.asyncio
async def test_bind_emits_engagement_when_telemetry_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")
    _isolate_home(monkeypatch, tmp_path)
    import handlers.bind as bind_handler

    importlib.reload(bind_handler)

    fake_response = bind_handler.BindResponse(bindings=[])

    async def _fake_do_bind(ctx, bindings):
        return fake_response

    monkeypatch.setattr(bind_handler, "_do_bind", _fake_do_bind)

    class _FakeBarrier:
        async def __aenter__(self):
            return SimpleNamespace(held_ms=0.0)

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(bind_handler, "repo_write_barrier", lambda ctx: _FakeBarrier())

    ctx = SimpleNamespace(session_id="s99")
    await bind_handler.handle_bind(
        ctx,
        [{"decision_id": "d1", "file_path": "a.py", "symbol_name": "f"}],
        preflight_id="explicit-pid",
    )
    eng_file = tmp_path / ".bicameral" / "engagements.jsonl"
    assert eng_file.exists()
    import json

    rows = [json.loads(line) for line in eng_file.read_text().splitlines()]
    assert rows[-1]["preflight_id"] == "explicit-pid"
    assert rows[-1]["tool"] == "bicameral.bind"
    assert rows[-1]["attribution"] == "explicit"
