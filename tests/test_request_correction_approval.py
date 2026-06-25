"""Tests for the request_correction approval gate (mcp#640).

Coverage:
  - Approval prompt via bicameral.request_correction.approve
  - Single-use enforcement (second submission rejected)
  - Scoped approval (mismatched scope rejected)
  - Unauthorized submission rejection (no prior approval)
"""

from __future__ import annotations

import json

import pytest

import server
from approval_gate import ApprovalGate, ApprovalScope, scope_from_params
from tool_request import MCP_TOOL_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION

# ---------------------------------------------------------------------------
# Unit tests: ApprovalGate and ApprovalScope
# ---------------------------------------------------------------------------


class TestApprovalScope:
    def test_requires_at_least_one_field(self):
        with pytest.raises(ValueError, match="at least one"):
            ApprovalScope()

    def test_key_is_deterministic(self):
        scope_a = ApprovalScope(packet_id="pkt-1")
        scope_b = ApprovalScope(packet_id="pkt-1")
        assert scope_a.key == scope_b.key

    def test_different_fields_produce_different_keys(self):
        scope_a = ApprovalScope(packet_id="pkt-1")
        scope_b = ApprovalScope(packet_id="pkt-2")
        assert scope_a.key != scope_b.key

    def test_description_includes_present_fields(self):
        scope = ApprovalScope(packet_id="pkt-1", excerpt="some text")
        desc = scope.description()
        assert "packet_id=pkt-1" in desc
        assert "excerpt=" in desc

    def test_scope_from_params_builds_correctly(self):
        scope = scope_from_params({"packet_id": "pkt-1", "correction_request": "fix typo"})
        assert scope.packet_id == "pkt-1"
        assert scope.correction_request == "fix typo"
        assert scope.excerpt is None
        assert scope.diff is None

    def test_scope_from_params_rejects_empty(self):
        with pytest.raises(ValueError):
            scope_from_params({})

    def test_scope_from_params_ignores_unrelated_keys(self):
        scope = scope_from_params({"packet_id": "pkt-1", "reason": "whatever", "actor_id": "u"})
        assert scope.packet_id == "pkt-1"


class TestApprovalGate:
    def test_grant_and_consume(self):
        gate = ApprovalGate()
        scope = ApprovalScope(packet_id="pkt-1")
        gate.grant(scope)
        assert gate.has_approval(scope)
        assert gate.consume(scope) is True
        assert gate.has_approval(scope) is False

    def test_consume_without_grant_returns_false(self):
        gate = ApprovalGate()
        scope = ApprovalScope(packet_id="pkt-1")
        assert gate.consume(scope) is False

    def test_single_use_enforcement(self):
        gate = ApprovalGate()
        scope = ApprovalScope(packet_id="pkt-1")
        gate.grant(scope)
        assert gate.consume(scope) is True
        # Second consume fails — approval was single-use.
        assert gate.consume(scope) is False

    def test_scoped_approval_mismatch(self):
        gate = ApprovalGate()
        scope_a = ApprovalScope(packet_id="pkt-1")
        scope_b = ApprovalScope(packet_id="pkt-2")
        gate.grant(scope_a)
        # Trying to consume a different scope fails.
        assert gate.consume(scope_b) is False
        # Original scope is still pending.
        assert gate.has_approval(scope_a) is True

    def test_clear_revokes_all(self):
        gate = ApprovalGate()
        gate.grant(ApprovalScope(packet_id="pkt-1"))
        gate.grant(ApprovalScope(excerpt="x"))
        assert gate.pending_count() == 2
        gate.clear()
        assert gate.pending_count() == 0

    def test_multiple_independent_approvals(self):
        gate = ApprovalGate()
        scope_a = ApprovalScope(packet_id="pkt-1")
        scope_b = ApprovalScope(packet_id="pkt-2")
        gate.grant(scope_a)
        gate.grant(scope_b)
        assert gate.consume(scope_a) is True
        assert gate.consume(scope_b) is True


# ---------------------------------------------------------------------------
# Integration tests: server call_tool with approval gate
# ---------------------------------------------------------------------------


class _FakeClient:
    """Fake daemon client that records dispatched requests."""

    def __init__(self):
        self.requests: list[dict] = []

    async def capabilities(self) -> dict:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"echo_command": tool_request["command"]["name"]},
            "responded_at": "2026-06-25T00:00:00Z",
        }


@pytest.fixture(autouse=True)
def _reset_approval_gate():
    """Ensure the module-level gate is clean between tests."""
    server._approval_gate.clear()
    yield
    server._approval_gate.clear()


@pytest.mark.asyncio
async def test_approve_grants_scoped_approval(monkeypatch):
    """bicameral.request_correction.approve returns approved status."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.request_correction.approve",
        {"packet_id": "pkt-abc", "correction_request": "fix drift"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "approved"
    assert "pkt-abc" in response["scope"]
    assert response["approval_key"]
    # No daemon request should be made for the approve tool.
    assert fake.requests == []


@pytest.mark.asyncio
async def test_approve_rejects_empty_scope(monkeypatch):
    """Approval with no scope fields is rejected."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.request_correction.approve",
        {},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "approval_scope_invalid"


@pytest.mark.asyncio
async def test_submission_rejected_without_approval(monkeypatch):
    """request_correction without prior approve is rejected locally."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.request_correction",
        {"packet_id": "pkt-abc", "correction_request": "fix drift"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "approval_required"
    assert "pkt-abc" in response["requested_scope"]
    # No daemon request should have been dispatched.
    assert fake.requests == []


@pytest.mark.asyncio
async def test_submission_succeeds_with_matching_approval(monkeypatch):
    """request_correction dispatches to daemon when approval matches."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    # Step 1: approve
    await server.call_tool(
        "bicameral.request_correction.approve",
        {"packet_id": "pkt-abc", "correction_request": "fix drift"},
    )
    # Step 2: submit
    content = await server.call_tool(
        "bicameral.request_correction",
        {"packet_id": "pkt-abc", "correction_request": "fix drift", "reason": "verified"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "ok"
    assert fake.requests[0]["command"]["name"] == "correction.request"
    assert fake.requests[0]["command"]["params"]["packet_id"] == "pkt-abc"
    assert fake.requests[0]["command"]["params"]["correction_request"] == "fix drift"
    assert fake.requests[0]["command"]["params"]["reason"] == "verified"


@pytest.mark.asyncio
async def test_single_use_enforcement_second_submission_rejected(monkeypatch):
    """After one successful submission, the same approval cannot be reused."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    # Approve once
    await server.call_tool(
        "bicameral.request_correction.approve",
        {"packet_id": "pkt-abc", "correction_request": "fix drift"},
    )
    # First submission succeeds
    content = await server.call_tool(
        "bicameral.request_correction",
        {"packet_id": "pkt-abc", "correction_request": "fix drift"},
    )
    assert json.loads(content[0].text)["status"] == "ok"

    # Second submission with same scope fails — approval was consumed.
    content = await server.call_tool(
        "bicameral.request_correction",
        {"packet_id": "pkt-abc", "correction_request": "fix drift"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "approval_required"
    # Only one request dispatched to daemon total.
    assert len(fake.requests) == 1


@pytest.mark.asyncio
async def test_scoped_approval_mismatch_rejected(monkeypatch):
    """Approval for scope A does not authorize submission for scope B."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    # Approve for pkt-1
    await server.call_tool(
        "bicameral.request_correction.approve",
        {"packet_id": "pkt-1"},
    )
    # Try to submit for pkt-2 — scope mismatch
    content = await server.call_tool(
        "bicameral.request_correction",
        {"packet_id": "pkt-2"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "approval_required"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_approval_by_excerpt(monkeypatch):
    """Approval can be scoped by excerpt text."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    await server.call_tool(
        "bicameral.request_correction.approve",
        {"excerpt": "the decision states X but code does Y"},
    )
    content = await server.call_tool(
        "bicameral.request_correction",
        {"excerpt": "the decision states X but code does Y"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "ok"
    assert fake.requests[0]["command"]["name"] == "correction.request"


@pytest.mark.asyncio
async def test_approval_by_diff(monkeypatch):
    """Approval can be scoped by diff content."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    diff_text = "--- a/foo.rs\n+++ b/foo.rs\n@@ -1 +1 @@\n-old\n+new"
    await server.call_tool(
        "bicameral.request_correction.approve",
        {"diff": diff_text},
    )
    content = await server.call_tool(
        "bicameral.request_correction",
        {"diff": diff_text},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "ok"


@pytest.mark.asyncio
async def test_submission_empty_scope_rejected(monkeypatch):
    """request_correction with no scope fields is rejected."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.request_correction",
        {"reason": "just a reason with no scope"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "approval_scope_invalid"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_approval_does_not_persist_across_gate_clear(monkeypatch):
    """Gate clear revokes pending approvals."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    await server.call_tool(
        "bicameral.request_correction.approve",
        {"packet_id": "pkt-abc"},
    )
    server._approval_gate.clear()

    content = await server.call_tool(
        "bicameral.request_correction",
        {"packet_id": "pkt-abc"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "approval_required"
