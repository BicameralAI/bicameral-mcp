"""Functionality tests for Flow 4 path-X-(b) ledger validation.

Tests the pure helper `count_agent_session_decisions` from
`tests/e2e/_ledger_helpers.py` and the merge logic that
`_validate_flow4_via_ledger` applies to a `FlowResult`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "e2e"))

from _ledger_helpers import count_agent_session_decisions  # noqa: E402


@dataclass
class FlowResultStub:
    flow_id: str
    passed: bool
    verdict_reason: str
    body: str


def test_counts_zero_when_no_agent_session_decisions():
    snapshot = {
        "decisions": [
            {"decision_id": "d1", "source_type": "manual"},
            {"decision_id": "d2", "source_type": "transcript"},
        ]
    }
    assert count_agent_session_decisions(snapshot) == 0


def test_counts_only_agent_session_decisions():
    snapshot = {
        "decisions": [
            {"decision_id": "d1", "source_type": "agent_session"},
            {"decision_id": "d2", "source_type": "manual"},
            {"decision_id": "d3", "source_type": "agent_session"},
            {"decision_id": "d4", "source_type": "transcript"},
            {"decision_id": "d5", "source_type": "manual"},
            {"decision_id": "d6", "source_type": "manual"},
            {"decision_id": "d7", "source_type": "manual"},
            {"decision_id": "d8", "source_type": "agent_session"},
        ]
    }
    assert count_agent_session_decisions(snapshot) == 3


def test_handles_missing_source_type_field():
    snapshot = {
        "decisions": [
            {"decision_id": "d1"},  # legacy row, no source_type
            {"decision_id": "d2", "source_type": "agent_session"},
            {"decision_id": "d3", "source_type": None},
        ]
    }
    assert count_agent_session_decisions(snapshot) == 1


def test_handles_error_snapshot():
    snapshot = {"error": "connection failed"}
    assert count_agent_session_decisions(snapshot) is None


def _merge(flow: FlowResultStub, snapshot: dict) -> None:
    """Mirror of `_validate_flow4_via_ledger`'s merge logic on a stub
    FlowResult, so unit tests exercise the merge invariants without
    importing the full harness module."""
    count = count_agent_session_decisions(snapshot)
    if count is None:
        flow.body += (
            f"\n— Ledger validation —\nINCONCLUSIVE: ledger query failed: {snapshot.get('error')}\n"
        )
        return
    if count > 0:
        if not flow.passed:
            flow.passed = True
            flow.verdict_reason = (
                f"in-stream asserter FAIL but SessionEnd subprocess effect "
                f"observed in ledger ({count} agent_session decisions, path-X-b)"
            )
        flow.body += (
            f"\n— Ledger validation —\n"
            f"PASS: {count} decision(s) with source_type='agent_session' "
            f"present in ledger after harness completion (path-X-b: SessionEnd "
            f"subprocess and/or in-session capture-corrections wrote them).\n"
        )
    else:
        flow.body += (
            "\n— Ledger validation —\n"
            "path-X-b absent: zero decisions with source_type='agent_session' "
            "after harness completion. SessionEnd subprocess either did not "
            "fire, did not detect uningested corrections, or failed silently.\n"
        )


def test_validate_merges_pass_into_flow4_result():
    """Asserter FAIL + ledger has agent_session → upgrade to PASS."""
    flow = FlowResultStub(
        flow_id="Flow 4",
        passed=False,
        verdict_reason="initial",
        body="initial body",
    )
    snapshot = {
        "decisions": [
            {"decision_id": "d1", "source_type": "agent_session"},
            {"decision_id": "d2", "source_type": "agent_session"},
        ]
    }
    _merge(flow, snapshot)
    assert flow.passed is True
    assert "SessionEnd subprocess effect observed" in flow.verdict_reason
    assert "agent_session" in flow.body


def test_validate_preserves_existing_pass():
    """Asserter PASS + ledger has agent_session → keep PASS, append note only."""
    flow = FlowResultStub(
        flow_id="Flow 4",
        passed=True,
        verdict_reason="initial",
        body="initial body",
    )
    snapshot = {"decisions": [{"decision_id": "d1", "source_type": "agent_session"}]}
    _merge(flow, snapshot)
    assert flow.passed is True
    assert flow.verdict_reason == "initial"
    assert "Ledger validation" in flow.body


def test_validate_handles_inconclusive_ledger():
    """Ledger query error → INCONCLUSIVE annotation, verdict unchanged."""
    flow = FlowResultStub(
        flow_id="Flow 4",
        passed=False,
        verdict_reason="initial",
        body="initial body",
    )
    snapshot = {"error": "connection failed"}
    _merge(flow, snapshot)
    assert flow.passed is False
    assert flow.verdict_reason == "initial"
    assert "INCONCLUSIVE" in flow.body
