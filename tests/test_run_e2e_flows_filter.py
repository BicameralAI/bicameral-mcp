"""Behavioral tests for the `--flow` substring filter helper in
``tests/e2e/run_e2e_flows.py`` (#156 PR B Phase 2).

The helper ``_filter_flow_plan(plan, pattern)`` is a pure function: given
the canonical FLOW_PLAN list and an optional substring pattern, returns
the subset of FlowSpecs whose ``flow_id`` contains the pattern. Used by
``main()``'s ``--flow PATTERN`` argparse arg so CI can validate one flow
(or one cross-flow pair like Flow 4 + Flow 4b) without running the full
e2e suite.

Mirrors the import pattern from ``tests/test_e2e_asserters.py:30-42``:
``run_e2e_flows`` performs ``shutil.which`` lookups for ``claude`` and
``bicameral-mcp`` at import time; this stub-and-import dance lets the
helper be tested without those binaries on PATH.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
E2E_DIR = REPO_ROOT / "tests" / "e2e"
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

_orig_which = shutil.which


def _which_stub(name: str, *args, **kwargs):
    if name in ("claude", "bicameral-mcp"):
        return f"/stub/{name}"
    return _orig_which(name, *args, **kwargs)


shutil.which = _which_stub  # type: ignore[assignment]
try:
    import run_e2e_flows  # noqa: E402
finally:
    shutil.which = _orig_which  # type: ignore[assignment]


def test_filter_flow_plan_returns_all_when_pattern_is_none() -> None:
    result = run_e2e_flows._filter_flow_plan(run_e2e_flows.FLOW_PLAN, None)
    assert result == run_e2e_flows.FLOW_PLAN
    assert result is run_e2e_flows.FLOW_PLAN or len(result) == len(run_e2e_flows.FLOW_PLAN)


def test_filter_flow_plan_substring_matches_multiple() -> None:
    result = run_e2e_flows._filter_flow_plan(run_e2e_flows.FLOW_PLAN, "Flow 4")
    flow_ids = [s.flow_id for s in result]
    assert "Flow 4" in flow_ids
    assert "Flow 4b" in flow_ids
    expected_order = [s.flow_id for s in run_e2e_flows.FLOW_PLAN if "Flow 4" in s.flow_id]
    assert flow_ids == expected_order


def test_filter_flow_plan_exact_match_returns_single() -> None:
    result = run_e2e_flows._filter_flow_plan(run_e2e_flows.FLOW_PLAN, "Flow 4b")
    assert len(result) == 1
    assert result[0].flow_id == "Flow 4b"
