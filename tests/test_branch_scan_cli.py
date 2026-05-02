"""Issue #48 Phase 0 — branch-scan CLI contract tests.

Pure-function tests on ``cli.branch_scan.render_terminal_summary`` plus
exit-code behavior tests on ``cli.branch_scan.main``. Mocks
``handle_link_commit`` to avoid SurrealDB. No real git, no real
ledger, no real subprocess.
"""

from __future__ import annotations

from unittest.mock import patch

from cli.branch_scan import main, render_terminal_summary
from contracts import (
    LinkCommitResponse,
    PendingComplianceCheck,
    PreClassificationHint,
)


def _check(
    decision_id: str,
    description: str,
    file_path: str,
    symbol: str,
    *,
    pre_classification: PreClassificationHint | None = None,
) -> PendingComplianceCheck:
    """Helper: construct a PendingComplianceCheck for fixtures."""
    return PendingComplianceCheck(
        phase="drift",
        decision_id=decision_id,
        region_id=f"rgn_{decision_id}",
        decision_description=description,
        file_path=file_path,
        symbol=symbol,
        content_hash="0" * 64,
        code_body="def f(): ...",
        pre_classification=pre_classification,
    )


def _response(
    *,
    pending: list[PendingComplianceCheck] | None = None,
    auto_resolved: int = 0,
) -> LinkCommitResponse:
    """Helper: build a LinkCommitResponse with defaults."""
    pending = pending or []
    return LinkCommitResponse(
        commit_hash="abc123def456",
        synced=True,
        reason="new_commit",
        regions_updated=len(pending) + auto_resolved,
        decisions_drifted=len(pending),
        flow_id="flow_test",
        pending_compliance_checks=pending,
        auto_resolved_count=auto_resolved,
    )


def test_renderer_empty_when_no_drift() -> None:
    """No drift, no auto-resolved → empty string. The hook caller treats
    empty output as 'all clean, no warning needed'."""
    body = render_terminal_summary(_response())
    assert body == ""


def test_renderer_skip_message_when_response_none() -> None:
    """``response=None`` ⇒ skip advisory. Used when no ledger is
    configured in the repo."""
    body = render_terminal_summary(None)
    assert "no ledger" in body.lower()
    assert "skipped" in body.lower()


def test_renderer_drift_summary_groups_by_decision() -> None:
    """Two drifted decisions → header + bullet list with each decision's
    name and file:symbol locator. Format matches the issue body's
    illustrative output."""
    pending = [
        _check("dec_auth", "Auth token expiry", "src/auth.py", "checkExpiry@40-55"),
        _check("dec_rate", "Rate limit window", "src/rate.py", "applyLimit@12-28"),
    ]
    body = render_terminal_summary(_response(pending=pending))
    assert "bicameral" in body
    assert "2 decisions" in body
    assert "dec_auth" in body
    assert "dec_rate" in body
    # Bullets must show file:symbol so the user can navigate
    assert "src/auth.py" in body
    assert "checkExpiry@40-55" in body


def test_renderer_uncertain_treated_as_drifted() -> None:
    """Pending check with ``pre_classification.verdict == 'uncertain'``
    is included in the drift count — the hook surfaces ambiguity, it
    doesn't filter for the user (let the human decide)."""
    hint = PreClassificationHint(verdict="uncertain", confidence=0.55)
    pending = [
        _check(
            "dec_unc",
            "uncertain decision",
            "src/u.py",
            "f@1-10",
            pre_classification=hint,
        ),
    ]
    body = render_terminal_summary(_response(pending=pending))
    assert "1 decision" in body
    assert "dec_unc" in body


@patch("cli.branch_scan._compute_drift")
def test_main_exit_zero_when_no_drift(mock_compute) -> None:
    """``main([])`` with no drift → returncode 0. The hook proceeds."""
    mock_compute.return_value = _response()
    assert main([]) == 0


@patch("cli.branch_scan._compute_drift")
def test_main_exit_two_when_block_env_set(mock_compute, monkeypatch) -> None:
    """``BICAMERAL_PUSH_HOOK_BLOCK=1`` + drift detected → exit code 2.
    Caller hook treats 2 as 'hard-block; do not even prompt'."""
    monkeypatch.setenv("BICAMERAL_PUSH_HOOK_BLOCK", "1")
    pending = [_check("dec_a", "alpha", "a.py", "f@1-10")]
    mock_compute.return_value = _response(pending=pending)
    assert main([]) == 2


@patch("cli.branch_scan._compute_drift")
@patch("cli.branch_scan._stdin_is_tty", return_value=False)
def test_main_exit_zero_when_non_tty_and_drift(
    mock_tty,
    mock_compute,
    monkeypatch,
) -> None:
    """Non-TTY (CI, scripts) + drift detected → exit code 0 (warn-only,
    do not block). Block-env-var override only applies in TTY contexts;
    non-TTY is the safe default that never blocks automation."""
    monkeypatch.delenv("BICAMERAL_PUSH_HOOK_BLOCK", raising=False)
    pending = [_check("dec_a", "alpha", "a.py", "f@1-10")]
    mock_compute.return_value = _response(pending=pending)
    assert main([]) == 0
