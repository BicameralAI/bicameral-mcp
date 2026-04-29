"""Issue #49 Phase 3 — drift-report renderer integration smoke.

End-to-end exercise: load a saved ``LinkCommitResponse`` JSON
fixture, deserialize via the Pydantic contract, run the renderer,
assert on the rendered output. Pure-data; no SurrealDB, no LLM, no
GitHub API.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli.drift_report import render_drift_report
from contracts import LinkCommitResponse

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "drift_report"


def _load(name: str) -> LinkCommitResponse:
    """Load a fixture JSON and deserialize via the Pydantic model."""
    path = _FIXTURES / name
    with open(path, encoding="utf-8") as fh:
        return LinkCommitResponse.model_validate_json(fh.read())


def test_integration_clean_state() -> None:
    """clean.json: zero pending, four auto-resolved → 'All clear'."""
    response = _load("clean.json")
    body = render_drift_report(response, pr_number=42, head_sha="5e96e47", base_ref="dev")
    assert "All clear" in body
    assert "auto-resolved" in body.lower()
    assert "4" in body  # the auto-resolved count


def test_integration_drifted_state() -> None:
    """drifted.json: 2 drifted + 1 uncertain → table with all three
    decision IDs and the right column headers."""
    response = _load("drifted.json")
    body = render_drift_report(response, pr_number=42, head_sha="abcdef0", base_ref="main")
    assert "**Drifted**" in body
    assert "**Uncertain**" in body
    assert "dec_threshold" in body
    assert "dec_retry_policy" in body
    assert "dec_async_boundary" in body
    # Reflected: 5 should appear in the totals line
    assert "Reflected:** 5" in body


def test_integration_truncate_state() -> None:
    """truncate.json: 15 drifted decisions → top 10 rendered, then
    'and 5 more'. Verifies the renderer caps long lists."""
    response = _load("truncate.json")
    body = render_drift_report(response, pr_number=99, head_sha="fffffff", base_ref="dev")
    assert "and 5 more" in body
    assert "dec_t_00" in body
    assert "dec_t_09" in body
    assert "dec_t_14" not in body  # truncated past index 9


def test_integration_skip_state() -> None:
    """response=None → skip message naming the manifest path."""
    body = render_drift_report(None, pr_number=42, head_sha="abcdef0", base_ref="dev")
    assert "skipped" in body.lower()
    assert "decisions.yaml" in body
