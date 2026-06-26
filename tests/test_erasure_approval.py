"""Tests for the PII erasure approval gate (GDPR Art.17, mcp#545).

Coverage:
  - ErasureScope creation and key determinism
  - ErasureGate grant/consume/single-use enforcement
  - Server call_tool routing for erase_subject.approve and erase_subject
  - Fail-closed behavior (rejection without prior approval)
  - Scoped approval (mismatched subject_id rejected)
"""

from __future__ import annotations

import json

import pytest

import server
from erasure_gate import ErasureGate, ErasureScope, scope_from_params
from tool_request import MCP_TOOL_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION

# ---------------------------------------------------------------------------
# Unit tests: ErasureScope
# ---------------------------------------------------------------------------


class TestErasureScope:
    def test_requires_subject_id(self):
        with pytest.raises(ValueError, match="non-empty subject_id"):
            ErasureScope(subject_id="")

    def test_key_is_deterministic(self):
        scope_a = ErasureScope(subject_id="user-42")
        scope_b = ErasureScope(subject_id="user-42")
        assert scope_a.key == scope_b.key

    def test_different_subject_ids_produce_different_keys(self):
        scope_a = ErasureScope(subject_id="user-1")
        scope_b = ErasureScope(subject_id="user-2")
        assert scope_a.key != scope_b.key

    def test_predicate_affects_key(self):
        scope_a = ErasureScope(subject_id="user-1")
        scope_b = ErasureScope(subject_id="user-1", predicate="before:2025-01-01")
        assert scope_a.key != scope_b.key

    def test_reason_does_not_affect_key(self):
        scope_a = ErasureScope(subject_id="user-1", reason="GDPR request")
        scope_b = ErasureScope(subject_id="user-1", reason="different reason")
        assert scope_a.key == scope_b.key

    def test_description_includes_subject_id(self):
        scope = ErasureScope(subject_id="user-42")
        assert "subject_id=user-42" in scope.description()

    def test_description_includes_predicate_and_reason(self):
        scope = ErasureScope(subject_id="user-42", predicate="all", reason="Art.17")
        desc = scope.description()
        assert "subject_id=user-42" in desc
        assert "predicate=" in desc
        assert "reason=" in desc


class TestScopeFromParams:
    def test_builds_from_subject_id(self):
        scope = scope_from_params({"subject_id": "user-42"})
        assert scope.subject_id == "user-42"
        assert scope.predicate is None
        assert scope.reason is None

    def test_builds_with_all_fields(self):
        scope = scope_from_params(
            {"subject_id": "user-42", "predicate": "before:2025", "reason": "GDPR"}
        )
        assert scope.subject_id == "user-42"
        assert scope.predicate == "before:2025"
        assert scope.reason == "GDPR"

    def test_rejects_missing_subject_id(self):
        with pytest.raises(ValueError, match="subject_id"):
            scope_from_params({})

    def test_rejects_empty_subject_id(self):
        with pytest.raises(ValueError, match="subject_id"):
            scope_from_params({"subject_id": ""})

    def test_ignores_unrelated_keys(self):
        scope = scope_from_params({"subject_id": "user-42", "unrelated": "value"})
        assert scope.subject_id == "user-42"


# ---------------------------------------------------------------------------
# Unit tests: ErasureGate
# ---------------------------------------------------------------------------


class TestErasureGate:
    def test_grant_and_consume(self):
        gate = ErasureGate()
        scope = ErasureScope(subject_id="user-42")
        gate.grant(scope)
        assert gate.has_approval(scope)
        assert gate.consume(scope) is True
        assert gate.has_approval(scope) is False

    def test_consume_without_grant_returns_false(self):
        gate = ErasureGate()
        scope = ErasureScope(subject_id="user-42")
        assert gate.consume(scope) is False

    def test_single_use_enforcement(self):
        gate = ErasureGate()
        scope = ErasureScope(subject_id="user-42")
        gate.grant(scope)
        assert gate.consume(scope) is True
        assert gate.consume(scope) is False

    def test_scoped_approval_mismatch(self):
        gate = ErasureGate()
        scope_a = ErasureScope(subject_id="user-1")
        scope_b = ErasureScope(subject_id="user-2")
        gate.grant(scope_a)
        assert gate.consume(scope_b) is False
        assert gate.has_approval(scope_a) is True

    def test_clear_revokes_all(self):
        gate = ErasureGate()
        gate.grant(ErasureScope(subject_id="user-1"))
        gate.grant(ErasureScope(subject_id="user-2"))
        assert gate.pending_count() == 2
        gate.clear()
        assert gate.pending_count() == 0

    def test_multiple_independent_approvals(self):
        gate = ErasureGate()
        scope_a = ErasureScope(subject_id="user-1")
        scope_b = ErasureScope(subject_id="user-2")
        gate.grant(scope_a)
        gate.grant(scope_b)
        assert gate.consume(scope_a) is True
        assert gate.consume(scope_b) is True


# ---------------------------------------------------------------------------
# Integration tests: server call_tool with erasure gate
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
def _reset_erasure_gate():
    """Ensure the module-level erasure gate is clean between tests."""
    server._erasure_gate.clear()
    yield
    server._erasure_gate.clear()


@pytest.mark.asyncio
async def test_erasure_approve_grants_scoped_approval(monkeypatch):
    """erase_subject.approve returns approved status with scope."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.privacy.erase_subject.approve",
        {"subject_id": "user-42", "reason": "GDPR Art.17 request"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "approved"
    assert "user-42" in response["scope"]
    assert response["approval_key"]
    assert fake.requests == []


@pytest.mark.asyncio
async def test_erasure_approve_rejects_missing_subject_id(monkeypatch):
    """Approval without subject_id is rejected."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.privacy.erase_subject.approve",
        {},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "erasure_scope_invalid"


@pytest.mark.asyncio
async def test_erasure_rejected_without_approval(monkeypatch):
    """erase_subject without prior approve is rejected locally (fail-closed)."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.privacy.erase_subject",
        {"subject_id": "user-42"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "erasure_approval_required"
    assert "user-42" in response["requested_scope"]
    assert fake.requests == []


@pytest.mark.asyncio
async def test_erasure_succeeds_with_matching_approval(monkeypatch):
    """erase_subject dispatches to daemon when approval matches."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    # Step 1: approve
    await server.call_tool(
        "bicameral.privacy.erase_subject.approve",
        {"subject_id": "user-42"},
    )
    # Step 2: erase
    content = await server.call_tool(
        "bicameral.privacy.erase_subject",
        {"subject_id": "user-42"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "ok"
    assert fake.requests[0]["command"]["name"] == "privacy.erase_subject"
    assert fake.requests[0]["command"]["params"]["subject_id"] == "user-42"


@pytest.mark.asyncio
async def test_erasure_single_use_second_call_rejected(monkeypatch):
    """After one successful erasure, the same approval cannot be reused."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    await server.call_tool(
        "bicameral.privacy.erase_subject.approve",
        {"subject_id": "user-42"},
    )
    # First erasure succeeds
    content = await server.call_tool(
        "bicameral.privacy.erase_subject",
        {"subject_id": "user-42"},
    )
    assert json.loads(content[0].text)["status"] == "ok"

    # Second erasure with same scope fails
    content = await server.call_tool(
        "bicameral.privacy.erase_subject",
        {"subject_id": "user-42"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "erasure_approval_required"
    assert len(fake.requests) == 1


@pytest.mark.asyncio
async def test_erasure_scoped_mismatch_rejected(monkeypatch):
    """Approval for subject A does not authorize erasure of subject B."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    await server.call_tool(
        "bicameral.privacy.erase_subject.approve",
        {"subject_id": "user-1"},
    )
    content = await server.call_tool(
        "bicameral.privacy.erase_subject",
        {"subject_id": "user-2"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "erasure_approval_required"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_erasure_with_predicate(monkeypatch):
    """Approval scoped with predicate matches erasure with same predicate."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    await server.call_tool(
        "bicameral.privacy.erase_subject.approve",
        {"subject_id": "user-42", "predicate": "before:2025-01-01"},
    )
    content = await server.call_tool(
        "bicameral.privacy.erase_subject",
        {"subject_id": "user-42", "predicate": "before:2025-01-01"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "ok"
    assert fake.requests[0]["command"]["params"]["predicate"] == "before:2025-01-01"


@pytest.mark.asyncio
async def test_erasure_predicate_mismatch_rejected(monkeypatch):
    """Approval with predicate A does not authorize erasure with predicate B."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    await server.call_tool(
        "bicameral.privacy.erase_subject.approve",
        {"subject_id": "user-42", "predicate": "before:2025-01-01"},
    )
    content = await server.call_tool(
        "bicameral.privacy.erase_subject",
        {"subject_id": "user-42", "predicate": "after:2025-01-01"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "erasure_approval_required"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_erasure_approval_does_not_persist_across_clear(monkeypatch):
    """Gate clear revokes pending erasure approvals."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    await server.call_tool(
        "bicameral.privacy.erase_subject.approve",
        {"subject_id": "user-42"},
    )
    server._erasure_gate.clear()

    content = await server.call_tool(
        "bicameral.privacy.erase_subject",
        {"subject_id": "user-42"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "erasure_approval_required"


@pytest.mark.asyncio
async def test_erasure_submission_empty_subject_id_rejected(monkeypatch):
    """erase_subject with no subject_id is rejected."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.privacy.erase_subject",
        {"reason": "just a reason with no subject_id"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "erasure_scope_invalid"
    assert fake.requests == []
