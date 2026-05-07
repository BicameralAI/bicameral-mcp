"""Functionality tests for `release.evidence_collect` (#218 SOC2-03).

Locks the per-release evidence-collection contract:
- Subprocess discipline: list-form argv, shell=False (OWASP A03)
- Markdown rendering: PR table, CI table, reviewer attribution
- Failure propagation: subprocess error raises, no silent empty-evidence
- Empty-window discipline: explicit "No PRs in window" notes (not silent omission)
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from release import evidence_collect


def _stub_run_returns(stdout_per_call: list[bytes]):
    """Build a subprocess.run side_effect that returns canned stdout per call.

    Captures the cmd + kwargs of each invocation for OWASP A03 assertions.
    """
    captured: list[tuple[list[str], dict]] = []
    iterator = iter(stdout_per_call)

    def runner(cmd, **kwargs):
        captured.append((cmd, kwargs))
        stdout = next(iterator)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=b"")

    return runner, captured


def test_collect_evidence_renders_markdown_with_pr_table() -> None:
    """`collect_evidence` queries gh for merged PRs in the tag window and
    renders a markdown table with one row per PR."""
    pr_json = json.dumps(
        [
            {
                "number": 237,
                "title": "feat(supply-chain): cosign hooks-manifest + SBOM",
                "mergedAt": "2026-05-06T22:30:00Z",
                "url": "https://github.com/BicameralAI/bicameral-mcp/pull/237",
            },
            {
                "number": 238,
                "title": "fix(ingest): bundle devil's-advocate followups",
                "mergedAt": "2026-05-06T22:45:00Z",
                "url": "https://github.com/BicameralAI/bicameral-mcp/pull/238",
            },
        ]
    ).encode()
    empty_reviews = b'{"reviews": []}'
    runner, _ = _stub_run_returns([pr_json, b"[]", empty_reviews, empty_reviews])
    with patch.object(subprocess, "run", runner):
        md = evidence_collect.collect_evidence(from_tag="v0.13.7", to_tag="v0.13.8")
    assert "| 237 |" in md or "| #237 |" in md or "237" in md
    assert "cosign hooks-manifest" in md
    assert "238" in md


def test_collect_evidence_renders_markdown_with_ci_runs() -> None:
    runs_json = json.dumps(
        [
            {
                "name": "MCP Regression Tests",
                "conclusion": "success",
                "url": "https://github.com/BicameralAI/bicameral-mcp/actions/runs/12345",
                "createdAt": "2026-05-06T20:00:00Z",
            }
        ]
    ).encode()
    runner, _ = _stub_run_returns([b"[]", runs_json, b"[]"])
    with patch.object(subprocess, "run", runner):
        md = evidence_collect.collect_evidence(from_tag="v0.13.7", to_tag="v0.13.8")
    assert "MCP Regression Tests" in md
    assert "success" in md.lower()


def test_collect_evidence_renders_markdown_with_reviewer_attribution() -> None:
    """Reviewer-attribution section lists PRs with at least one approving review."""
    pr_json = json.dumps(
        [{"number": 237, "title": "test", "mergedAt": "2026-05-06T00:00:00Z", "url": "u1"}]
    ).encode()
    reviews_json = json.dumps(
        {"reviews": [{"author": {"login": "alice"}, "state": "APPROVED"}]}
    ).encode()
    runner, _ = _stub_run_returns([pr_json, b"[]", reviews_json])
    with patch.object(subprocess, "run", runner):
        md = evidence_collect.collect_evidence(from_tag="v0.13.7", to_tag="v0.13.8")
    assert "alice" in md
    assert "APPROVED" in md or "approved" in md.lower()


def test_collect_evidence_uses_list_form_argv_with_no_shell_true() -> None:
    """OWASP A03 commitment: every subprocess invocation is list-form argv,
    no shell=True. Captured via stub runner."""
    runner, captured = _stub_run_returns([b"[]", b"[]"])
    with patch.object(subprocess, "run", runner):
        evidence_collect.collect_evidence(from_tag="v0.13.7", to_tag="v0.13.8")
    assert len(captured) >= 1
    for cmd, kwargs in captured:
        assert isinstance(cmd, list), f"must be list-form argv, got {type(cmd)}"
        assert kwargs.get("shell") in (None, False), "shell=True forbidden"


def test_collect_evidence_raises_on_subprocess_failure() -> None:
    """Subprocess error propagates — no silent empty-evidence fallback."""

    def fail_runner(cmd, **_kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"gh CLI not authenticated")

    with patch.object(subprocess, "run", fail_runner):
        with pytest.raises(subprocess.CalledProcessError):
            evidence_collect.collect_evidence(from_tag="v0.13.7", to_tag="v0.13.8")


def test_render_markdown_omits_empty_sections_with_explicit_note() -> None:
    """Empty PR list / empty CI list MUST emit explicit notes in the markdown,
    not silent omission (which would be misleading evidence)."""
    md = evidence_collect.render_markdown(
        prs=[],
        ci_runs=[],
        reviews_by_pr={},
        from_tag="v0.13.7",
        to_tag="v0.13.8",
    )
    assert "No PRs" in md or "no PRs" in md
    assert "No CI runs" in md or "no CI runs" in md
