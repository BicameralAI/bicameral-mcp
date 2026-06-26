"""Regression coverage for #332: bind on ephemeral/feature branches.

After link_commit(HEAD) on a feature branch, binding.create must resolve
symbols against the synced head (effective ref), not a stale
authoritative_sha.  The daemon owns the effective-ref selection logic;
the MCP thin client's job is to populate ``commit_sha`` and ``ref_name``
from the workspace so the daemon has the context it needs.

These tests exercise:
1. Auto-population of ``commit_sha`` / ``ref_name`` from the workspace
   when the caller omits them (the common ephemeral-branch path).
2. Explicit values are never overridden (authoritative-branch path).
3. Non-git workspaces degrade gracefully (no crash, params absent).
4. Daemon ``bind_effective_ref`` response field is surfaced verbatim.
"""

from __future__ import annotations

import json
import subprocess
import tempfile

import pytest

import server
from tool_request import MCP_TOOL_COMMANDS, _resolve_workspace_ref
from version import TOOLREQUEST_PROTOCOL_VERSION


class _EphemeralDaemon:
    """Fake daemon that echoes back binding.create params and includes
    the ``bind_effective_ref`` field introduced by the daemon fix for #332."""

    protocol_version = TOOLREQUEST_PROTOCOL_VERSION

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def capabilities(self) -> dict:
        return {
            "toolrequest_protocol_version": self.protocol_version,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        command = tool_request["command"]["name"]
        params = tool_request["command"]["params"]

        if command == "binding.create":
            effective_ref = params.get("commit_sha", "authoritative-fallback")
            return {
                "request_id": tool_request["request_id"],
                "status": "ok",
                "result": {
                    "decision_or_candidate_id": params["decision_or_candidate_id"],
                    "evidence_state": "verified",
                    "verified": True,
                    "bind_effective_ref": effective_ref,
                    "ledger_revision": 1,
                },
                "responded_at": "2026-06-26T00:00:00Z",
            }

        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"echo_command": command},
            "responded_at": "2026-06-26T00:00:00Z",
        }


@pytest.fixture(autouse=True)
def _reset_approval_gate():
    server._approval_gate.clear()
    yield
    server._approval_gate.clear()


def _patch_daemon(monkeypatch, daemon, workspace="/repo"):
    monkeypatch.setattr(server, "_client", lambda: daemon)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "ephemeral-test-actor")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", workspace)
    monkeypatch.delenv("BICAMERAL_DAEMON_URL", raising=False)
    monkeypatch.delenv("BICAMERAL_BOT_DAEMON_URL", raising=False)


# ------------------------------------------------------------------
# _resolve_workspace_ref unit tests
# ------------------------------------------------------------------


def _git_init(path: str) -> None:
    """Initialise a throwaway git repo with identity config (needed on CI)."""
    subprocess.check_call(["git", "init", path], stdout=subprocess.DEVNULL)
    subprocess.check_call(
        ["git", "-C", path, "config", "user.email", "test@test"],
        stdout=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "-C", path, "config", "user.name", "test"],
        stdout=subprocess.DEVNULL,
    )


def test_resolve_workspace_ref_returns_head_and_branch(tmp_path):
    _git_init(str(tmp_path))
    subprocess.check_call(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    head_sha, branch = _resolve_workspace_ref(str(tmp_path))
    assert len(head_sha) == 40
    assert branch in ("main", "master")


def test_resolve_workspace_ref_feature_branch(tmp_path):
    _git_init(str(tmp_path))
    subprocess.check_call(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "-C", str(tmp_path), "checkout", "-b", "feature/issue-332"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "feature work"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    head_sha, branch = _resolve_workspace_ref(str(tmp_path))
    assert len(head_sha) == 40
    assert branch == "feature/issue-332"


def test_resolve_workspace_ref_non_git_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        head_sha, branch = _resolve_workspace_ref(d)
    assert head_sha == ""
    assert branch == ""


def test_resolve_workspace_ref_memory_workspace_returns_empty():
    head_sha, branch = _resolve_workspace_ref("memory://fake")
    assert head_sha == ""
    assert branch == ""


# ------------------------------------------------------------------
# Integration: binding.create auto-populates ref context from workspace
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_binding_create_auto_populates_ref_from_workspace(monkeypatch, tmp_path):
    """On a feature branch with no explicit commit_sha/ref_name, MCP must
    auto-populate them from the workspace git state so the daemon can
    perform effective-ref selection (#332)."""
    _git_init(str(tmp_path))
    subprocess.check_call(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "-C", str(tmp_path), "checkout", "-b", "feature/new-symbol"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "add symbol"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    expected_sha = (
        subprocess.check_output(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        .decode()
        .strip()
    )

    daemon = _EphemeralDaemon()
    _patch_daemon(monkeypatch, daemon, workspace=str(tmp_path))

    content = await server.call_tool(
        "bicameral.bind",
        {
            "decision_or_candidate_id": "DEC-332",
            "bindings": [{"symbol": "NewSymbol", "file": "new_module.py"}],
            "workspace": str(tmp_path),
        },
    )

    assert len(daemon.requests) == 1
    params = daemon.requests[0]["command"]["params"]
    assert params["commit_sha"] == expected_sha
    assert params["ref_name"] == "feature/new-symbol"

    parsed = json.loads(content[0].text)
    assert parsed["status"] == "ok"
    assert parsed["result"]["bind_effective_ref"] == expected_sha


@pytest.mark.asyncio
async def test_binding_create_preserves_explicit_ref(monkeypatch, tmp_path):
    """When the caller provides explicit commit_sha and ref_name, MCP must
    not override them — preserving authoritative-branch behavior."""
    _git_init(str(tmp_path))
    subprocess.check_call(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    daemon = _EphemeralDaemon()
    _patch_daemon(monkeypatch, daemon, workspace=str(tmp_path))

    content = await server.call_tool(
        "bicameral.bind",
        {
            "decision_or_candidate_id": "DEC-332",
            "bindings": [{"symbol": "OldSymbol"}],
            "commit_sha": "explicit-sha-from-caller",
            "ref_name": "main",
            "workspace": str(tmp_path),
        },
    )

    assert len(daemon.requests) == 1
    params = daemon.requests[0]["command"]["params"]
    assert params["commit_sha"] == "explicit-sha-from-caller"
    assert params["ref_name"] == "main"

    parsed = json.loads(content[0].text)
    assert parsed["result"]["bind_effective_ref"] == "explicit-sha-from-caller"


@pytest.mark.asyncio
async def test_binding_create_non_git_workspace_omits_ref(monkeypatch):
    """When the workspace is not a git repo (e.g. memory://), commit_sha and
    ref_name must be absent from the ToolRequest (graceful degradation)."""
    daemon = _EphemeralDaemon()
    _patch_daemon(monkeypatch, daemon, workspace="memory://no-git")

    content = await server.call_tool(
        "bicameral.bind",
        {
            "decision_or_candidate_id": "DEC-332",
            "bindings": [{"symbol": "SomeSymbol"}],
            "workspace": "memory://no-git",
        },
    )

    assert len(daemon.requests) == 1
    params = daemon.requests[0]["command"]["params"]
    assert "commit_sha" not in params
    assert "ref_name" not in params

    parsed = json.loads(content[0].text)
    assert parsed["status"] == "ok"
    assert parsed["result"]["bind_effective_ref"] == "authoritative-fallback"


@pytest.mark.asyncio
async def test_bind_effective_ref_surfaced_verbatim(monkeypatch):
    """The daemon's bind_effective_ref field must survive the thin client
    unmodified (typed-state passthrough, same as other binding states)."""

    def respond(command, req):
        if command == "binding.create":
            return {
                "status": "ok",
                "result": {
                    "decision_or_candidate_id": "DEC-332",
                    "evidence_state": "verified",
                    "verified": True,
                    "bind_effective_ref": "abc123featurehead",
                },
                "responded_at": "2026-06-26T00:00:00Z",
            }
        return None

    daemon = _EphemeralDaemon()
    daemon.send_tool_request = _make_responder(respond, daemon)  # type: ignore[assignment]
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.bind",
        {
            "decision_or_candidate_id": "DEC-332",
            "bindings": [{"symbol": "x"}],
            "commit_sha": "abc123featurehead",
        },
    )
    parsed = json.loads(content[0].text)
    assert parsed["result"]["bind_effective_ref"] == "abc123featurehead"


def _make_responder(respond_fn, daemon):
    """Wrap a response function around the daemon's send_tool_request."""

    async def _send(tool_request):
        daemon.requests.append(tool_request)
        command = tool_request["command"]["name"]
        override = respond_fn(command, tool_request)
        if override is not None:
            return {"request_id": tool_request["request_id"], **override}
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"echo_command": command},
            "responded_at": "2026-06-26T00:00:00Z",
        }

    return _send
