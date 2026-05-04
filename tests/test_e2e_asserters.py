"""Unit tests for the e2e flow asserters.

Run the asserter functions in isolation against synthetic tool-call lists.
Lets us pin behaviour like "Flow 1 accepts any commit-history-area file as
a legitimate anchor for the bundled reorder/squash/amend/branch-from
decision" without paying for a full claude-CLI e2e cycle.
"""

from __future__ import annotations

import sys
from pathlib import Path

E2E_DIR = Path(__file__).resolve().parent.parent / "tests" / "e2e"
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

# Importing the orchestrator triggers env-var checks (DESKTOP_REPO_PATH etc.)
# and CLI presence checks that we don't want to fire in unit tests. Stub them
# before import so the module loads without bailing out.
import os  # noqa: E402

os.environ.setdefault("DESKTOP_REPO_PATH", str(Path(__file__).resolve().parent))
os.environ.setdefault("PATH", os.environ.get("PATH", ""))

import shutil  # noqa: E402

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


def _ingest_call(decisions: list[dict]) -> dict:
    return {
        "name": "mcp__bicameral__bicameral_ingest",
        "input": {"payload": {"decisions": decisions}},
    }


def _ratify_call(decision_id: str) -> dict:
    return {
        "name": "mcp__bicameral__bicameral_ratify",
        "input": {"decision_id": decision_id},
    }


def _seed_calls(commit_history_anchor: str) -> list[dict]:
    """Standard Flow 1 sequence: ingest the 3 seed decisions with inline
    bindings, then ratify each. ``commit_history_anchor`` is the file path
    chosen for the bundled commit-history decision — varied across tests
    to confirm the asserter accepts any legitimate area path.
    """
    decisions = [
        {
            "description": "High-signal notifications",
            "code_regions": [{"file_path": "app/src/lib/stores/notifications-store.ts"}],
        },
        {
            "description": "Improved commit history",
            "code_regions": [{"file_path": commit_history_anchor}],
        },
        {
            "description": "Cherry-pick between branches",
            "code_regions": [{"file_path": "app/src/lib/git/cherry-pick.ts"}],
        },
    ]
    return [
        _ingest_call(decisions),
        _ratify_call("decision:1"),
        _ratify_call("decision:2"),
        _ratify_call("decision:3"),
    ]


# ── Flow 1: feature-area binding ────────────────────────────────────────


def test_flow1_passes_with_canonical_git_layer_anchor():
    """The previously-required exact path — must still pass."""
    calls = _seed_calls(commit_history_anchor="app/src/lib/git/reorder.ts")
    ok, detail = run_e2e_flows.assert_flow_1(calls)
    assert ok, f"Flow 1 should pass with canonical reorder.ts anchor; detail: {detail}"


def test_flow1_passes_with_ui_layer_anchor():
    """Previously failing case — agent picks UI-layer commit-list.tsx for the
    bundled commit-history decision. Now accepted as a legitimate anchor."""
    calls = _seed_calls(commit_history_anchor="app/src/ui/history/commit-list.tsx")
    ok, detail = run_e2e_flows.assert_flow_1(calls)
    assert ok, f"Flow 1 should accept commit-list.tsx as commit-history anchor; detail: {detail}"


def test_flow1_passes_with_dispatcher_anchor():
    """Dispatcher also backs the bundled ops (amend, branch-from)."""
    calls = _seed_calls(commit_history_anchor="app/src/ui/dispatcher/dispatcher.ts")
    ok, detail = run_e2e_flows.assert_flow_1(calls)
    assert ok, f"Flow 1 should accept dispatcher.ts as commit-history anchor; detail: {detail}"


def test_flow1_passes_with_squash_anchor():
    """Bundled decision includes drag-to-squash; squash.ts is a legitimate anchor."""
    calls = _seed_calls(commit_history_anchor="app/src/lib/git/squash.ts")
    ok, detail = run_e2e_flows.assert_flow_1(calls)
    assert ok, f"Flow 1 should accept squash.ts as commit-history anchor; detail: {detail}"


def test_flow1_fails_when_commit_history_unbound():
    """Bind something far from the commit-history area — asserter still fails."""
    calls = _seed_calls(commit_history_anchor="app/src/lib/some-unrelated-file.ts")
    ok, detail = run_e2e_flows.assert_flow_1(calls)
    assert not ok, f"Flow 1 must fail when no commit-history-area file is bound; detail: {detail}"
    assert "commit-history area" in detail


def test_flow1_fails_when_cherry_pick_unbound():
    """Replace cherry-pick.ts with something unrelated — asserter fails."""
    decisions = [
        {
            "description": "High-signal notifications",
            "code_regions": [{"file_path": "app/src/lib/stores/notifications-store.ts"}],
        },
        {
            "description": "Improved commit history",
            "code_regions": [{"file_path": "app/src/lib/git/reorder.ts"}],
        },
        {
            "description": "Cherry-pick between branches",
            "code_regions": [{"file_path": "app/src/lib/some-other-thing.ts"}],
        },
    ]
    calls = [_ingest_call(decisions), _ratify_call("d1"), _ratify_call("d2"), _ratify_call("d3")]
    ok, detail = run_e2e_flows.assert_flow_1(calls)
    assert not ok
    assert "cherry-pick area" in detail


def test_flow1_accepts_cherry_pick_tsx():
    """UI-layer cherry-pick.tsx is also a legitimate cherry-pick anchor."""
    decisions = [
        {
            "description": "High-signal notifications",
            "code_regions": [{"file_path": "app/src/lib/stores/notifications-store.ts"}],
        },
        {
            "description": "Improved commit history",
            "code_regions": [{"file_path": "app/src/lib/git/reorder.ts"}],
        },
        {
            "description": "Cherry-pick between branches",
            "code_regions": [{"file_path": "app/src/ui/multi-commit-operation/cherry-pick.tsx"}],
        },
    ]
    calls = [_ingest_call(decisions), _ratify_call("d1"), _ratify_call("d2"), _ratify_call("d3")]
    ok, detail = run_e2e_flows.assert_flow_1(calls)
    assert ok, f"Flow 1 should accept cherry-pick.tsx; detail: {detail}"


def test_flow1_fails_without_ratify():
    """Even if bindings are fine, missing ratify still fails the asserter."""
    calls = _seed_calls(commit_history_anchor="app/src/lib/git/reorder.ts")
    # Drop the three ratify calls.
    calls = [c for c in calls if "ratify" not in c["name"]]
    ok, detail = run_e2e_flows.assert_flow_1(calls)
    assert not ok
    assert "ratify" in detail.lower()
