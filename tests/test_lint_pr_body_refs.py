"""Issue #114 Phase 1 — PR-body refs lint contract tests.

Pure-function tests on ``.github/scripts/lint_pr_body_refs``. Each
test passes a synthetic PR body string to the linter and asserts on
the warning list. Includes the SECURITY-CRITICAL ``--from-env`` test
that verifies the no-shell-interpolation invocation matches file-mode
output.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "scripts" / "lint_pr_body_refs.py"
)
_SPEC = importlib.util.spec_from_file_location("lint_pr_body_refs", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_pr_body_refs"] = _MODULE
_SPEC.loader.exec_module(_MODULE)

lint_pr_body = _MODULE.lint_pr_body
main = _MODULE.main


def test_closes_keyword_recognised() -> None:
    """Body with `Closes #42` produces zero warnings — that's the
    canonical full-closure pattern."""
    body = "## Summary\n\nFixes the bug.\n\nCloses #42\n"
    assert lint_pr_body(body) == []


def test_refs_keyword_recognised() -> None:
    """Body with `Refs #42` produces zero warnings — partial /
    architectural reference, not a closure."""
    body = "## Summary\n\nRelated work.\n\nRefs #42\n"
    assert lint_pr_body(body) == []


def test_bare_mention_in_prose_warns() -> None:
    """Body with a bare `#42` in prose (no Closes/Refs keyword,
    not under a Linked-issues section) triggers a warning."""
    body = "## Summary\n\nPhase 1 (#42) — adds the contracts.\n"
    warnings = lint_pr_body(body)
    assert len(warnings) == 1
    assert warnings[0].number == 42


def test_linked_issues_section_exempts_bare_mentions() -> None:
    """Bare `#NUMBER` tokens under a `## Linked issues` heading are
    exempt — the section header itself is the link wrapper."""
    body = "## Linked issues\n\n- #42\n- #43\n"
    assert lint_pr_body(body) == []


def test_main_always_returns_zero(tmp_path) -> None:
    """``main()`` is advisory — always returns 0 even when warnings
    are emitted. CI uses the warnings as informational signal, not
    a merge gate."""
    body_file = tmp_path / "body.md"
    body_file.write_text("Bare mention (#42) in prose.\n")
    assert main(["--body", str(body_file)]) == 0


def test_main_reads_from_env_var(monkeypatch, capsys) -> None:
    """SECURITY-CRITICAL path: the CI workflow uses ``--from-env
    PR_BODY`` to avoid shell-string interpolation of user-controlled
    PR-body text (OWASP A03 mitigation per #114 audit v1).

    Verify that ``--from-env`` produces the SAME warnings as
    ``--body file`` for identical input."""
    body = "## Summary\n\nPhase 1 (#108) prose mention.\n"
    monkeypatch.setenv("BICAMERAL_TEST_PR_BODY", body)
    rc = main(["--from-env", "BICAMERAL_TEST_PR_BODY"])
    assert rc == 0  # advisory
    captured = capsys.readouterr()
    # The bare #108 mention should produce a warning to stderr
    assert "108" in captured.err
    assert "warning" in captured.err.lower()
