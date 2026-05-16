"""Unit tests for the M6 preflight retrieval recall eval (#58 Phase A).

Pure-function tests on the classifier + aggregator + renderer. No surrealdb,
no API, no ledger. Sociable in the relevant sense (per the CLAUDE.md rule):
where the SUT touches a "collaborator," we pass a real lightweight stand-in
(``types.SimpleNamespace`` for the response, real ``M6Case`` dataclasses)
rather than ``MagicMock`` — keeps the tests honest about response shape.

The end-to-end test that actually drives ``handle_preflight`` against a
seeded ledger lives in the eval runner itself; it's tested via the CI
step (which is warn-only initially per the plan).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "preflight_m6"))

from dataset import M6Case  # type: ignore[import-not-found]  # noqa: E402, I001
from eval_preflight_m6_recall import (  # type: ignore[import-not-found]  # noqa: E402, I001
    _aggregate,
    _per_case_row,
    classify_outcome,
)


def _make_case(
    case_id: str = "T_test",
    miss_mode: str = "vocabulary_mismatch",
    topic: str = "test topic",
) -> M6Case:
    return M6Case(
        case_id=case_id,
        miss_mode=miss_mode,
        topic=topic,
        intended_description="some intended description for the test fixture",
    )


def _make_response(decision_ids: list[str], *, fired: bool = True) -> SimpleNamespace:
    """Lightweight stand-in for PreflightResponse — uses real SimpleNamespace
    so any access to a field we don't set fails honestly (per CLAUDE.md
    sociable-testing rule: SimpleNamespace > MagicMock for this exact reason).
    """
    decisions = [SimpleNamespace(decision_id=did) for did in decision_ids]
    return SimpleNamespace(
        decisions=decisions,
        fired=fired,
        sources_chained=["region"] if decision_ids else [],
    )


# ── classify_outcome ────────────────────────────────────────────────────


def test_classify_outcome_surfaced() -> None:
    case = _make_case()
    response = _make_response(["decision:wanted", "decision:other"])
    assert classify_outcome(case, response, "decision:wanted") == "surfaced"


def test_classify_outcome_missed() -> None:
    case = _make_case()
    response = _make_response(["decision:other_only"])
    assert classify_outcome(case, response, "decision:wanted") == "missed"


def test_classify_outcome_missed_when_empty_response() -> None:
    case = _make_case()
    response = _make_response([])
    assert classify_outcome(case, response, "decision:wanted") == "missed"


def test_classify_outcome_error_when_response_is_none() -> None:
    """Seeder failure path: runner passes response=None and outcome must be
    'error' so it doesn't get counted in the recall denominator."""
    case = _make_case()
    assert classify_outcome(case, None, "decision:wanted") == "error"


def test_classify_outcome_missed_when_no_intended_id() -> None:
    """If the seeder couldn't determine the intended_decision_id (empty
    string), every case classifies as missed — guards against the seeder
    silently failing to ingest."""
    case = _make_case()
    response = _make_response(["decision:something"])
    assert classify_outcome(case, response, "") == "missed"


# ── _per_case_row ───────────────────────────────────────────────────────


def test_per_case_row_captures_response_fields() -> None:
    case = _make_case(case_id="T_capture", miss_mode="transitive_relevance")
    response = SimpleNamespace(
        decisions=[SimpleNamespace(decision_id="decision:a")],
        fired=True,
        sources_chained=["region", "graph"],
    )
    row = _per_case_row(case, response, "decision:a", "surfaced")
    assert row["case_id"] == "T_capture"
    assert row["miss_mode"] == "transitive_relevance"
    assert row["outcome"] == "surfaced"
    assert row["fired"] is True
    assert row["sources_chained"] == ["region", "graph"]
    assert row["n_decisions_surfaced"] == 1
    assert row["surfaced_decision_ids"] == ["decision:a"]
    assert row["error_msg"] == ""


def test_per_case_row_error_path_captures_msg() -> None:
    case = _make_case(case_id="T_err")
    row = _per_case_row(case, None, "", "error", error_msg="seed: RuntimeError: nope")
    assert row["outcome"] == "error"
    assert row["fired"] is False
    assert row["sources_chained"] == []
    assert "RuntimeError" in row["error_msg"]


# ── _aggregate ──────────────────────────────────────────────────────────


def _row(case_id: str, miss_mode: str, outcome: str, *, fired: bool = True) -> dict:
    return {
        "case_id": case_id,
        "miss_mode": miss_mode,
        "topic": "t",
        "intended_description": "d",
        "intended_decision_id": "d:1",
        "intended_file_path": "",
        "file_paths": [],
        "decision_status": "ratified",
        "outcome": outcome,
        "fired": fired,
        "sources_chained": [],
        "n_decisions_surfaced": 1 if outcome == "surfaced" else 0,
        "surfaced_decision_ids": ["d:1"] if outcome == "surfaced" else [],
        "error_msg": "",
    }


def test_aggregate_basic_recall_math() -> None:
    rows = [
        _row("a1", "vocabulary_mismatch", "surfaced"),
        _row("a2", "vocabulary_mismatch", "missed"),
        _row("b1", "unbound_decision", "surfaced"),
        _row("b2", "unbound_decision", "surfaced"),
    ]
    agg = _aggregate(rows)
    # 3 surfaced / 4 total → 0.75
    assert agg["recall"] == 0.75
    # 4/4 fired
    assert agg["fire_rate"] == 1.0
    assert agg["total_cases"] == 4
    assert agg["outcomes"] == {"surfaced": 3, "missed": 1}


def test_aggregate_errors_excluded_from_recall_denominator() -> None:
    """Per the plan: errors are infra (not agent misses) and should NOT
    drag recall. This is a load-bearing semantic — if the seeder failed
    for 3 of 5 cases, the recall metric reflects what the 2 evaluable
    cases produced, not 2/5."""
    rows = [
        _row("a1", "vocabulary_mismatch", "surfaced"),
        _row("a2", "vocabulary_mismatch", "missed"),
        _row("a3", "vocabulary_mismatch", "error"),
        _row("a4", "vocabulary_mismatch", "error"),
        _row("a5", "vocabulary_mismatch", "error"),
    ]
    agg = _aggregate(rows)
    # evaluable=2 (surfaced+missed); surfaced=1 → recall=0.5
    assert agg["recall"] == 0.5
    assert agg["error_count"] == 3
    # per-mode also excludes errors from its denominator
    assert agg["per_miss_mode"]["vocabulary_mismatch"]["recall"] == 0.5
    assert agg["per_miss_mode"]["vocabulary_mismatch"]["errors"] == 3


def test_aggregate_per_miss_mode_breakdown() -> None:
    """The per-mode axis is the load-bearing diagnostic — Phase B's choice
    of optimization direction is picked from this breakdown."""
    rows = [
        _row("v1", "vocabulary_mismatch", "surfaced"),
        _row("v2", "vocabulary_mismatch", "surfaced"),
        _row("v3", "vocabulary_mismatch", "missed"),
        _row("u1", "unbound_decision", "missed"),
        _row("u2", "unbound_decision", "missed"),
        _row("t1", "transitive_relevance", "surfaced"),
    ]
    agg = _aggregate(rows)
    per_mode = agg["per_miss_mode"]
    assert per_mode["vocabulary_mismatch"]["recall"] == round(2 / 3, 4)
    assert per_mode["unbound_decision"]["recall"] == 0.0
    assert per_mode["transitive_relevance"]["recall"] == 1.0


def test_aggregate_fire_rate_counts_all_rows() -> None:
    """Fire rate denominator is total (NOT evaluable) — even errored rows
    that ran preflight successfully but couldn't be classified contribute
    to whether the agent saw a surfaced block."""
    rows = [
        _row("a1", "vocabulary_mismatch", "surfaced", fired=True),
        _row("a2", "vocabulary_mismatch", "surfaced", fired=False),
        _row("a3", "vocabulary_mismatch", "missed", fired=False),
        _row("a4", "vocabulary_mismatch", "missed", fired=False),
    ]
    agg = _aggregate(rows)
    assert agg["fire_rate"] == 0.25


def test_aggregate_handles_empty_rows() -> None:
    """Defensive — runner can produce zero rows if all cases match a
    --case-id filter that finds nothing. Aggregator must not divide by zero."""
    agg = _aggregate([])
    assert agg["recall"] == 0.0
    assert agg["fire_rate"] == 0.0
    assert agg["total_cases"] == 0


# ── Smoke test: dataset import is clean ─────────────────────────────────


def test_dataset_imports_and_validates() -> None:
    """The dataset module runs _validate_dataset() at import time — this
    test just confirms a fresh import doesn't raise (catches schema
    regressions where someone adds an invalid case_type / miss_mode)."""
    import importlib

    import dataset as ds  # type: ignore[import-not-found]

    importlib.reload(ds)
    assert len(ds.ALL_CASES) >= 25, "fixture must have at least 25 cases per Phase A spec"
    for miss_mode in ("vocabulary_mismatch", "unbound_decision", "transitive_relevance"):
        cases = ds.cases_by_miss_mode(miss_mode)
        assert len(cases) >= 7, (
            f"each miss_mode must have at least 7 cases for statistical signal; "
            f"got {len(cases)} for {miss_mode}"
        )


@pytest.mark.parametrize(
    "miss_mode",
    ["vocabulary_mismatch", "unbound_decision", "transitive_relevance"],
)
def test_dataset_cases_per_miss_mode_have_required_fields(miss_mode: str) -> None:
    """Per-case-type field invariants — catches drift if someone adds a
    transitive case without intended_file_path, or an unbound case with
    decision_status accidentally set to ratified."""
    import dataset as ds  # type: ignore[import-not-found]

    for c in ds.cases_by_miss_mode(miss_mode):
        assert c.topic.strip(), f"{c.case_id}: empty topic"
        assert c.intended_description.strip(), f"{c.case_id}: empty intended_description"
        if miss_mode == "transitive_relevance":
            assert c.file_paths, f"{c.case_id}: transitive case needs file_paths"
            assert c.intended_file_path, f"{c.case_id}: transitive case needs intended_file_path"
        if miss_mode == "unbound_decision":
            assert c.decision_status == "ungrounded", (
                f"{c.case_id}: unbound case must have decision_status='ungrounded'"
            )
