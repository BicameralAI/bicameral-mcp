"""Phase-1 e2e-gating test for Priority B v0.

Asserts Flow 2 is registered in the e2e flow runner's FLOW_PLAN with
the correct asserter wired up. If Flow 2 is removed, renamed, or
detached from `assert_flow_2`, this test fires immediately — guarding
the contradiction-capture validation surface (the runtime functionality
test for the preflight Step 5.6 contract).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

_RUNNER_PATH = Path(__file__).resolve().parent / "e2e" / "run_e2e_flows.py"


def _load_runner_module():
    """Load run_e2e_flows.py with env preconditions stubbed so its import
    succeeds in unit-test contexts (the runner module exits on import if
    DESKTOP_REPO_PATH or 'claude'/'bicameral-mcp' on PATH are missing —
    those are e2e harness preconditions, not relevant for FLOW_PLAN
    inspection)."""
    env = dict(os.environ)
    env.setdefault("DESKTOP_REPO_PATH", "/tmp/desktop-clone-stub")
    with patch.dict(os.environ, env), patch.object(shutil, "which", lambda _: "/usr/bin/stub"):
        spec = importlib.util.spec_from_file_location("run_e2e_flows", _RUNNER_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["run_e2e_flows"] = mod
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            sys.modules.pop("run_e2e_flows", None)
            raise
        return mod


def test_flow_2a_runs_in_e2e_default_set():
    runner = _load_runner_module()
    flows_by_id = {f.flow_id: f for f in runner.FLOW_PLAN}
    assert "Flow 2" in flows_by_id, (
        f"Flow 2 missing from e2e default set; got: {sorted(flows_by_id.keys())}"
    )
    flow_2 = flows_by_id["Flow 2"]
    assert flow_2.asserter is runner.assert_flow_2, (
        "Flow 2's asserter is not wired to assert_flow_2 — "
        "the contradiction-capture validation surface is detached."
    )
