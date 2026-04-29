"""Issue #114 Phase 0 — plan-grounding lint contract tests.

Pure-function tests on ``scripts.lint_plan_grounding``. Each test
constructs a synthetic plan-*.md content string and asserts on the
diagnostic list the linter produces. No real plan files are read,
no git, no network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the script as a module so tests can call its public functions
# without requiring it to be a proper Python package (it's a standalone
# dev/CI utility under scripts/).
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "lint_plan_grounding.py"
_SPEC = importlib.util.spec_from_file_location("lint_plan_grounding", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_plan_grounding"] = _MODULE
_SPEC.loader.exec_module(_MODULE)

lint_plan_text = _MODULE.lint_plan_text
main = _MODULE.main


def _existing_repo_path() -> Path:
    """Return a path that's known to exist on the repo working tree
    (this very test directory). Used in synthetic plan inputs that
    must not trigger diagnostics."""
    return Path("tests/test_lint_plan_grounding.py")


def test_clean_plan_emits_no_diagnostics(tmp_path) -> None:
    """A plan referencing only existing paths produces zero
    diagnostics — the lint must not false-positive on cleanly-grounded
    plans."""
    plan = (
        "# Plan: clean grounding\n\n"
        "Modifies `scripts/lint_plan_grounding.py` and "
        "`tests/test_lint_plan_grounding.py` (both real).\n"
    )
    diagnostics = lint_plan_text(plan, repo_root=Path("."))
    assert diagnostics == []


def test_nonexistent_path_emits_diagnostic(tmp_path) -> None:
    """A plan referencing a path that does not exist must emit one
    diagnostic per unresolved token. Carries the line number for
    actionable feedback."""
    plan = "# Plan: bad grounding\n\nAdds `bicameral/foo_module.py` to the repo.\n"
    diagnostics = lint_plan_text(plan, repo_root=Path("."))
    assert len(diagnostics) == 1
    diag = diagnostics[0]
    assert diag.token == "bicameral/foo_module.py"
    assert diag.line == 3  # 1-indexed; the third line of the synthetic plan


def test_new_marker_exempts_path() -> None:
    """A path explicitly marked **new** on its bullet line is exempt
    from grounding — plans deliberately propose new files."""
    plan = "# Plan: with new marker\n\n- `bicameral/brand_new_module.py` — **new**, ~50 LOC.\n"
    diagnostics = lint_plan_text(plan, repo_root=Path("."))
    assert diagnostics == []


def test_planned_suffix_exempts_path() -> None:
    """A path followed by a `(planned)` / `(future)` / `(v2)` suffix
    is exempt — author signals the path is aspirational, not extant."""
    plan = (
        "# Plan: with planned suffix\n\n"
        "Future module `bicameral/v2_optimizer.py` (planned) — see Phase 5.\n"
    )
    diagnostics = lint_plan_text(plan, repo_root=Path("."))
    assert diagnostics == []


def test_html_comment_skipped() -> None:
    """Tokens inside `<!-- ... -->` HTML comments are skipped — those
    are author notes / examples that shouldn't be linted."""
    plan = (
        "# Plan: with HTML comment\n\n"
        "<!-- Earlier draft mentioned `bicameral/old.py` but we removed it. -->\n"
        "Real change: `scripts/lint_plan_grounding.py`.\n"
    )
    diagnostics = lint_plan_text(plan, repo_root=Path("."))
    assert diagnostics == []


def test_quote_block_skipped() -> None:
    """Tokens inside Markdown blockquotes (`>` prefix) are skipped —
    those are typically illustrative quotations, not file claims."""
    plan = (
        "# Plan: with blockquote\n\n"
        "> The audit said: `bicameral/foo.py` does not exist. Fixed in v2.\n"
        "Real change: `scripts/lint_plan_grounding.py`.\n"
    )
    diagnostics = lint_plan_text(plan, repo_root=Path("."))
    assert diagnostics == []


def test_main_exits_zero_when_all_clean(tmp_path) -> None:
    """``main([str(plan_path)])`` returns 0 when the plan grounds
    cleanly. Used by CI as the gate signal."""
    plan = tmp_path / "plan-clean.md"
    plan.write_text("Touches `scripts/lint_plan_grounding.py`.\n")
    assert main([str(plan)]) == 0


def test_main_exits_one_when_diagnostics(tmp_path) -> None:
    """``main([str(plan_path)])`` returns 1 when any diagnostic fires.
    CI must block the merge in that state."""
    plan = tmp_path / "plan-bad.md"
    plan.write_text("Touches `bicameral/nonexistent.py`.\n")
    assert main([str(plan)]) == 1
