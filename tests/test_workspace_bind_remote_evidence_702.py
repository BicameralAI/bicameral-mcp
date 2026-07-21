"""Git-remote-as-evidence for MCP-assisted workspace bind (mcp#702).

Owner decision (issue #702): the candidate local workspace's git remote may be
used as *evidence* for a workspace-bind proposal — normalized to a stable
``org/repo`` ref. A match against the registered project's source ref raises
confidence; a clear mismatch fails closed before dispatch; a missing/ambiguous
remote yields a lower-confidence proposal that still requires explicit
confirmation. The git remote is never project identity: ``project_id`` remains
the authority key, and the daemon still owns validation and materialization.

The daemon is seamed off; these are deterministic, offline, no-LLM tests.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import server
from tool_request import (
    MCP_TOOL_COMMANDS,
    build_tool_request,
    detect_candidate_repo_ref,
    evaluate_remote_evidence,
    normalize_repo_ref,
)

BIND_TOOL = "bicameral.workspace.bind"


def _git_repo(path, origin_url: str | None) -> str:
    """Init a bare-config git repo at *path* with an optional ``origin`` remote."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    if origin_url is not None:
        subprocess.run(["git", "remote", "add", "origin", origin_url], cwd=path, check=True)
    return str(path)


# ---------------------------------------------------------------------------
# normalize_repo_ref: SSH / scp / HTTPS / plain forms -> stable org/repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("git@github.com:BicameralAI/ada-service.git", "BicameralAI/ada-service"),
        ("https://github.com/BicameralAI/ada-service.git", "BicameralAI/ada-service"),
        ("https://github.com/BicameralAI/ada-service", "BicameralAI/ada-service"),
        ("ssh://git@github.com/BicameralAI/ada-service.git", "BicameralAI/ada-service"),
        (
            "https://x-access-token:TOKEN@github.com/BicameralAI/ada-service.git",
            "BicameralAI/ada-service",
        ),
        ("git@github.com:BicameralAI/ada-service/", "BicameralAI/ada-service"),
        ("BicameralAI/ada-service", "BicameralAI/ada-service"),
    ],
)
def test_normalize_repo_ref_forms(raw, expected):
    assert normalize_repo_ref(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not-a-repo", None, 123, ["x"]])
def test_normalize_repo_ref_rejects_non_repo(raw):
    assert normalize_repo_ref(raw) is None


# ---------------------------------------------------------------------------
# detect_candidate_repo_ref: reads origin only; missing origin -> ambiguous
# ---------------------------------------------------------------------------


def test_detect_candidate_repo_ref_reads_origin(tmp_path):
    repo = _git_repo(tmp_path / "ada", "git@github.com:BicameralAI/ada-service.git")
    assert detect_candidate_repo_ref(repo) == "BicameralAI/ada-service"


def test_detect_candidate_repo_ref_none_without_origin(tmp_path):
    repo = _git_repo(tmp_path / "no-origin", None)
    assert detect_candidate_repo_ref(repo) is None


def test_detect_candidate_repo_ref_none_for_non_git(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert detect_candidate_repo_ref(str(plain)) is None


# ---------------------------------------------------------------------------
# evaluate_remote_evidence: match / contradiction / unverified / ambiguous
# ---------------------------------------------------------------------------


def test_evidence_match_is_high_confidence(tmp_path):
    repo = _git_repo(tmp_path / "ada", "git@github.com:BicameralAI/ada-service.git")
    evidence = evaluate_remote_evidence(
        candidate_path=repo,
        project_source_refs=["https://github.com/BicameralAI/ada-service.git"],
    )
    assert evidence.verdict == "match"
    assert evidence.confidence == pytest.approx(0.95)
    assert evidence.candidate_repo_ref == "BicameralAI/ada-service"
    assert "matches" in evidence.reason


def test_evidence_match_is_case_insensitive(tmp_path):
    repo = _git_repo(tmp_path / "ada", "git@github.com:BicameralAI/Ada-Service.git")
    evidence = evaluate_remote_evidence(
        candidate_path=repo,
        project_source_refs=["bicameralai/ada-service"],
    )
    assert evidence.verdict == "match"


def test_evidence_contradiction_fails_closed(tmp_path):
    repo = _git_repo(tmp_path / "other", "git@github.com:BicameralAI/other-service.git")
    evidence = evaluate_remote_evidence(
        candidate_path=repo,
        project_source_refs=["BicameralAI/ada-service"],
    )
    assert evidence.verdict == "contradiction"
    assert evidence.confidence == 0.0
    assert evidence.candidate_repo_ref == "BicameralAI/other-service"


def test_evidence_unverified_when_no_source_ref(tmp_path):
    repo = _git_repo(tmp_path / "ada", "git@github.com:BicameralAI/ada-service.git")
    evidence = evaluate_remote_evidence(candidate_path=repo, project_source_refs=[])
    assert evidence.verdict == "unverified"
    assert evidence.confidence == pytest.approx(0.6)


def test_evidence_ambiguous_without_remote(tmp_path):
    repo = _git_repo(tmp_path / "no-origin", None)
    evidence = evaluate_remote_evidence(
        candidate_path=repo,
        project_source_refs=["BicameralAI/ada-service"],
    )
    assert evidence.verdict == "ambiguous"
    assert evidence.confidence == pytest.approx(0.4)
    assert evidence.candidate_repo_ref is None


# ---------------------------------------------------------------------------
# _workspace_bind_params: evidence shapes proposal confidence + reason
# ---------------------------------------------------------------------------


def _bind_params(**args) -> dict:
    request = build_tool_request(
        command_name=MCP_TOOL_COMMANDS[BIND_TOOL],
        params=args,
        authority={},
    )
    return request["command"]["params"]


def test_matching_remote_raises_proposal_confidence(tmp_path):
    repo = _git_repo(tmp_path / "ada", "git@github.com:BicameralAI/ada-service.git")
    params = _bind_params(
        project_id="proj_ada",
        candidate_path=repo,
        project_source_refs=["BicameralAI/ada-service"],
        confirmed=True,
    )
    assert params["proposal"]["confidence"] == pytest.approx(0.95)
    assert "matches" in params["proposal"]["reason"]
    # The remote is evidence only: it is never sent as project identity, and
    # the proposal keeps its contract shape (no repo-ref field leaks in).
    assert params["proposal"]["project_id"] == "proj_ada"
    assert "candidate_repo_ref" not in params["proposal"]
    assert "project_source_refs" not in params


def test_explicit_confidence_overrides_evidence(tmp_path):
    repo = _git_repo(tmp_path / "ada", "git@github.com:BicameralAI/ada-service.git")
    params = _bind_params(
        project_id="proj_ada",
        candidate_path=repo,
        project_source_refs=["BicameralAI/ada-service"],
        confidence=0.2,
        confirmed=True,
    )
    assert params["proposal"]["confidence"] == pytest.approx(0.2)


def test_ambiguous_remote_lowers_confidence(tmp_path):
    repo = _git_repo(tmp_path / "no-origin", None)
    params = _bind_params(project_id="proj_ada", candidate_path=repo, confirmed=True)
    assert params["proposal"]["confidence"] == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# server pre-dispatch guard: contradiction fails closed, dispatches nothing
# ---------------------------------------------------------------------------


class _BindDaemon:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def capabilities(self) -> dict:
        return {
            "toolrequest_protocol_version": server.TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
            "workspace_binding_available": True,
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        return {
            "status": "ok",
            "result": {"status": "bound", "outcome": {"project_id": "proj_ada"}},
            "request_id": tool_request["request_id"],
        }


def _patch(monkeypatch, daemon: _BindDaemon) -> None:
    monkeypatch.setattr(server, "_client", lambda: daemon)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "operator-1")
    monkeypatch.delenv("BICAMERAL_DAEMON_URL", raising=False)
    monkeypatch.delenv("BICAMERAL_BOT_DAEMON_URL", raising=False)


@pytest.mark.asyncio
async def test_contradicting_remote_fails_closed_without_dispatch(monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "other", "git@github.com:BicameralAI/other-service.git")
    daemon = _BindDaemon()
    _patch(monkeypatch, daemon)

    content = await server.call_tool(
        BIND_TOOL,
        {
            "project_id": "proj_ada",
            "candidate_path": repo,
            "project_source_refs": ["BicameralAI/ada-service"],
            "confirmed": True,
        },
    )
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "rejected"
    assert parsed["bound"] is False
    assert parsed["error_kind"] == "workspace_remote_mismatch"
    assert parsed["candidate_repo_ref"] == "BicameralAI/other-service"
    assert "authority" in parsed["authority_note"].lower()
    # Fail-closed at the MCP surface: nothing was dispatched to the daemon.
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_matching_remote_dispatches_with_high_confidence(monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "ada", "git@github.com:BicameralAI/ada-service.git")
    daemon = _BindDaemon()
    _patch(monkeypatch, daemon)

    await server.call_tool(
        BIND_TOOL,
        {
            "project_id": "proj_ada",
            "candidate_path": repo,
            "project_source_refs": ["BicameralAI/ada-service"],
            "confirmed": True,
        },
    )

    assert len(daemon.requests) == 1
    proposal = daemon.requests[0]["command"]["params"]["proposal"]
    assert proposal["confidence"] == pytest.approx(0.95)
    assert proposal["project_id"] == "proj_ada"


@pytest.mark.asyncio
async def test_no_source_ref_dispatches_normally(monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "ada", "git@github.com:BicameralAI/ada-service.git")
    daemon = _BindDaemon()
    _patch(monkeypatch, daemon)

    await server.call_tool(
        BIND_TOOL,
        {"project_id": "proj_ada", "candidate_path": repo, "confirmed": True},
    )

    assert len(daemon.requests) == 1
