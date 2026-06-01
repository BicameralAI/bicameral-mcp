"""Regression coverage for #504 auto-sync pending-check response injection.

The production bug lives at the MCP server boundary: ``ensure_ledger_synced``
returns a full ``LinkCommitResponse`` and ``server.py`` decides what to attach
to the outer tool response. These tests keep that seam narrow while using real
response contracts and, for the no-overlap path, a real memory:// ledger plus
the real preflight handler.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import server
from contracts import LinkCommitResponse, PendingComplianceCheck, PreflightResponse
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate

_NS_COUNTER = 0


def _pending(file_path: str, *, decision_id: str = "decision:scope") -> PendingComplianceCheck:
    return PendingComplianceCheck(
        phase="drift",
        decision_id=decision_id,
        region_id=f"code_region:{file_path.replace('/', '_').replace('.', '_')}",
        decision_description=f"Decision for {file_path}",
        file_path=file_path,
        symbol="target",
        content_hash=f"hash-{file_path}",
        code_body="def target():\n    return True\n",
    )


def _sync_response(checks: list[PendingComplianceCheck]) -> LinkCommitResponse:
    return LinkCommitResponse(
        commit_hash="1234567890abcdef",
        synced=True,
        reason="new_commit",
        pending_compliance_checks=checks,
        flow_id="flow-504",
    )


def _ctx(*, ledger=None) -> SimpleNamespace:
    return SimpleNamespace(
        repo_path=str(Path(__file__).resolve().parents[1]),
        ledger=ledger,
        guided_mode=False,
        _sync_state={},
        code_graph=None,
        render_source_attribution="redacted",
        session_id="test-504",
    )


async def _real_adapter() -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    global _NS_COUNTER
    _NS_COUNTER += 1
    client = LedgerClient(url="memory://", ns=f"pending_scope_{_NS_COUNTER}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    return adapter, client


@pytest.mark.asyncio
async def test_auto_sync_pending_checks_are_filtered_to_preflight_file_paths(monkeypatch):
    """A preflight for app/foo.py must not inherit unrelated sync checks."""

    checks = [
        _pending("app/foo.py", decision_id="decision:in-scope"),
        _pending("pilot/mcp/ledger/adapter.py", decision_id="decision:unrelated"),
    ]
    ctx = _ctx()

    monkeypatch.setattr(server.BicameralContext, "from_env", lambda: ctx)
    monkeypatch.setattr(server, "get_update_notice", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "handlers.sync_middleware.ensure_ledger_synced",
        AsyncMock(return_value=_sync_response(checks)),
    )
    monkeypatch.setattr("handlers.sync_middleware.ensure_team_synced", AsyncMock(return_value=None))
    monkeypatch.setattr(
        server,
        "handle_preflight",
        AsyncMock(
            return_value=PreflightResponse(
                topic="fix scoped pending checks",
                fired=False,
                reason="no_matches",
                guided_mode=False,
            )
        ),
    )

    result = await server._call_tool_impl(
        "bicameral.preflight",
        {"topic": "fix scoped pending checks", "file_paths": ["app/foo.py"]},
    )

    payload = json.loads(result[0].text)
    attached = payload["_pending_compliance_checks"]
    assert [c["file_path"] for c in attached] == ["app/foo.py"]
    omitted = payload["_pending_compliance_omitted"]
    assert omitted["scoped_out"] is True
    assert omitted["omitted"] == 1
    assert omitted["file_paths"] == ["pilot/mcp/ledger/adapter.py"]
    assert payload["_pending_flow_id"] == "flow-504"
    assert "1 decision(s) need compliance verification" in payload["_sync_guidance"]
    assert "outside the current file scope" in payload["_sync_guidance"]


@pytest.mark.asyncio
async def test_auto_sync_pending_checks_preserve_signal_when_preflight_paths_have_no_overlap(
    monkeypatch,
):
    """If caller paths have zero overlap, attach only a compact follow-up signal."""

    adapter, client = await _real_adapter()
    ctx = _ctx(ledger=adapter)
    try:
        monkeypatch.setattr(server.BicameralContext, "from_env", lambda: ctx)
        monkeypatch.setattr(server, "get_update_notice", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "handlers.sync_middleware.ensure_ledger_synced",
            AsyncMock(return_value=_sync_response([_pending("pilot/mcp/ledger/adapter.py")])),
        )
        monkeypatch.setattr(
            "handlers.sync_middleware.ensure_team_synced",
            AsyncMock(return_value=None),
        )

        result = await server._call_tool_impl(
            "bicameral.preflight",
            {"topic": "edit unrelated landing page", "file_paths": ["site/index.html"]},
        )

        payload = json.loads(result[0].text)
        assert payload["fired"] is False
        digest = payload["_pending_compliance_checks"]
        assert digest["scoped_out"] is True
        assert digest["total"] == 1
        assert digest["kept"] == 0
        assert digest["omitted"] == 1
        assert digest["checks"] == []
        assert digest["file_paths"] == ["pilot/mcp/ledger/adapter.py"]
        assert "bicameral.link_commit" in digest["hint"]
        assert payload["_pending_flow_id"] == "flow-504"
        assert "outside this tool call's file scope" in payload["_sync_guidance"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_auto_sync_pending_checks_use_truncation_digest_when_over_budget(monkeypatch):
    """Large in-scope sync payloads should stay under the server budget."""

    checks = []
    for i in range(30):
        check = _pending("app/foo.py", decision_id=f"decision:{i}")
        check.code_body = "x = 1\n" * 500
        checks.append(check)
    ctx = _ctx()

    monkeypatch.setattr(server.BicameralContext, "from_env", lambda: ctx)
    monkeypatch.setattr(server, "get_update_notice", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "handlers.sync_middleware.ensure_ledger_synced",
        AsyncMock(return_value=_sync_response(checks)),
    )
    monkeypatch.setattr("handlers.sync_middleware.ensure_team_synced", AsyncMock(return_value=None))
    monkeypatch.setattr(
        server,
        "handle_preflight",
        AsyncMock(
            return_value=PreflightResponse(
                topic="fix scoped pending checks",
                fired=False,
                reason="no_matches",
                guided_mode=False,
            )
        ),
    )

    result = await server._call_tool_impl(
        "bicameral.preflight",
        {"topic": "fix scoped pending checks", "file_paths": ["app/foo.py"]},
    )

    payload = json.loads(result[0].text)
    digest = payload["_pending_compliance_checks"]
    assert digest["truncated"] is True
    assert digest["total"] == 30
    assert 0 < digest["kept"] < 30
    assert digest["omitted"] == 30 - digest["kept"]
    assert "bicameral.history" in digest["hint"]
    assert len(result[0].text) <= 20_000
