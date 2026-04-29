"""Issue #49 Phase 1 — drift-report renderer contract tests.

Pure-function tests on ``cli.drift_report.render_drift_report``. No
SurrealDB, no LLM, no GitHub API — only the renderer's input → output
shape. All tests use synthetic ``LinkCommitResponse``-shaped dicts
(or ``None`` for the skip path) and assert on the rendered Markdown
string.
"""

from __future__ import annotations

from cli.drift_report import render_drift_report
from contracts import (
    ContinuityResolution,
    LinkCommitResponse,
    PendingComplianceCheck,
    PreClassificationHint,
)

_MARKER = "<!-- bicameral-drift-report -->"


def _check(
    decision_id: str,
    description: str,
    file_path: str,
    start_line: int,
    end_line: int,
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
        symbol=f"f@{start_line}-{end_line}",
        content_hash="0" * 64,
        code_body="def f(): ...",
        pre_classification=pre_classification,
    )


def _response(
    *,
    pending: list[PendingComplianceCheck] | None = None,
    auto_resolved: int = 0,
    continuity: list[ContinuityResolution] | None = None,
    reflected: int = 0,
    drifted: int | None = None,
) -> LinkCommitResponse:
    """Helper: build a LinkCommitResponse with defaults."""
    pending = pending or []
    return LinkCommitResponse(
        commit_hash="abc123def456",
        synced=True,
        reason="new_commit",
        regions_updated=len(pending) + auto_resolved,
        decisions_reflected=reflected,
        decisions_drifted=(
            drifted
            if drifted is not None
            else sum(1 for p in pending if p.pre_classification is None)
        ),
        flow_id="flow_test",
        pending_compliance_checks=pending,
        auto_resolved_count=auto_resolved,
        continuity_resolutions=continuity or [],
    )


def test_renderer_emits_html_marker() -> None:
    """First line of the comment body must carry the marker so the
    sticky-comment poster can find and update an existing one."""
    body = render_drift_report(_response(), pr_number=1, head_sha="abc1234", base_ref="dev")
    assert body.splitlines()[0].strip() == _MARKER


def test_renderer_groups_by_status() -> None:
    """Drifted, uncertain, reflected, auto-resolved each render to a
    distinct table row when count > 0."""
    hint = PreClassificationHint(verdict="uncertain", confidence=0.55)
    pending = [
        _check("dec_drift_a", "decision A", "a.py", 1, 10),
        _check(
            "dec_uncertain_b",
            "decision B",
            "b.py",
            1,
            10,
            pre_classification=hint,
        ),
    ]
    body = render_drift_report(
        _response(pending=pending, auto_resolved=3),
        pr_number=1,
        head_sha="abc1234",
        base_ref="dev",
    )
    assert "Drifted" in body
    assert "Uncertain" in body
    assert "Auto-resolved" in body
    assert "dec_drift_a" in body
    assert "dec_uncertain_b" in body


def test_renderer_omits_zero_count_rows() -> None:
    """Statuses with zero entries must NOT appear in the table."""
    body = render_drift_report(
        _response(auto_resolved=2),
        pr_number=1,
        head_sha="abc1234",
        base_ref="dev",
    )
    # No drifted, no uncertain — only auto-resolved should appear
    assert "| **Drifted** |" not in body
    assert "| **Uncertain** |" not in body
    # Clean state mentions auto-resolution count (case-insensitive — the
    # message phrasing is "auto-resolved 2 cosmetic regions").
    assert "auto-resolved" in body.lower()
    assert "2" in body  # the actual count appears


def test_renderer_clean_state_message() -> None:
    """Zero drifted + zero uncertain → 'All clear' messaging."""
    body = render_drift_report(
        _response(),
        pr_number=42,
        head_sha="abc1234",
        base_ref="dev",
    )
    assert "All clear" in body
    assert _MARKER in body


def test_renderer_skip_state_message() -> None:
    """``response=None`` → skip message naming the missing manifest."""
    body = render_drift_report(
        None,
        pr_number=42,
        head_sha="abc1234",
        base_ref="dev",
    )
    assert "skipped" in body.lower()
    assert "decisions.yaml" in body
    assert _MARKER in body


def test_renderer_truncates_long_decision_lists() -> None:
    """When > 10 decisions per status, render top 10 + 'and N more'."""
    pending = [_check(f"dec_d_{i}", f"decision {i}", f"f{i}.py", 1, 10) for i in range(15)]
    body = render_drift_report(
        _response(pending=pending),
        pr_number=1,
        head_sha="abc1234",
        base_ref="dev",
    )
    assert "and 5 more" in body
    assert "dec_d_0" in body
    assert "dec_d_9" in body
    assert "dec_d_14" not in body  # truncated past index 9


def test_renderer_escapes_pipes_in_rendered_fields() -> None:
    """Pipes in rendered fields (decision_id or file_path) must be
    escaped to keep the Markdown table valid. The renderer renders
    decision_id + file_path; pipes anywhere in either must not corrupt
    the column structure."""
    pending = [
        _check("dec_pipe_id", "irrelevant", "pa|th/file.py", 1, 10),
    ]
    body = render_drift_report(
        _response(pending=pending),
        pr_number=1,
        head_sha="abc1234",
        base_ref="dev",
    )
    table_lines = [line for line in body.splitlines() if "dec_pipe_id" in line]
    assert table_lines, "decision_id must appear in rendered table"
    table_line = table_lines[0]
    # Strip escaped pipes; remaining pipes should be exactly the 4
    # column separators of a table row: | col1 | col2 | col3 |.
    bare_pipes = table_line.replace(r"\|", "").count("|")
    assert bare_pipes == 4, (
        f"expected 4 column-separator pipes, got {bare_pipes} in: {table_line!r}"
    )


def test_renderer_idempotent() -> None:
    """Two calls with identical input produce byte-identical output —
    important so the sticky-comment update is a no-op when nothing
    changed (avoids 'comment edited' notification spam)."""
    response = _response(
        pending=[_check("dec_a", "alpha", "a.py", 1, 10)],
        auto_resolved=2,
    )
    a = render_drift_report(
        response,
        pr_number=1,
        head_sha="abc1234",
        base_ref="dev",
    )
    b = render_drift_report(
        response,
        pr_number=1,
        head_sha="abc1234",
        base_ref="dev",
    )
    assert a == b
