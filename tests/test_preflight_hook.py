"""Functionality tests for scripts/hooks/preflight_reminder.py.

The hook is invoked as a subprocess by Claude Code. Tests run it the
same way to exercise stdin/stdout exactly as production does.

Claude Code 2.x requires UserPromptSubmit hook output shaped as
``{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
"additionalContext": "..."}}``. The legacy top-level
``{"additionalContext": ...}`` shape is silently dropped by the CLI,
so these tests assert against the nested shape — anything else is a
broken contract regardless of whether the hook process exits cleanly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "preflight_reminder.py"


def _run_hook(stdin_text: str) -> tuple[int, str, str]:
    """Invoke the hook with stdin_text on stdin; return (rc, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _hook_output(parsed: dict) -> dict:
    """Extract the hookSpecificOutput payload, asserting the envelope shape."""
    assert "hookSpecificOutput" in parsed, (
        f"hook must emit hookSpecificOutput envelope (Claude Code 2.x contract); got {parsed!r}"
    )
    inner = parsed["hookSpecificOutput"]
    assert inner.get("hookEventName") == "UserPromptSubmit"
    return inner


def test_emits_additional_context_on_match():
    """Fire-worthy prompt produces additionalContext containing the directive."""
    payload = {"prompt": "Please refactor the rate limiter to sliding window."}
    rc, out, _ = _run_hook(json.dumps(payload))
    assert rc == 0
    inner = _hook_output(json.loads(out))
    assert "additionalContext" in inner
    assert "<system-reminder>" in inner["additionalContext"]
    assert "bicameral.preflight" in inner["additionalContext"]


def test_emits_empty_on_no_match():
    """Skip-worthy prompt produces empty response (no hookSpecificOutput)."""
    payload = {"prompt": "fix the typo in README"}
    rc, out, _ = _run_hook(json.dumps(payload))
    assert rc == 0
    parsed = json.loads(out) if out.strip() else {}
    assert "hookSpecificOutput" not in parsed


def test_handles_malformed_stdin():
    """Non-JSON stdin returns rc 0 with empty/no response — never blocks user."""
    rc, out, _ = _run_hook("this is not JSON at all {[}")
    assert rc == 0
    if out.strip():
        parsed = json.loads(out)
        assert "hookSpecificOutput" not in parsed


def test_idempotent_on_double_fire():
    """Same prompt twice produces identical output (no state leak)."""
    payload = {"prompt": "implement the OAuth callback for Google Calendar"}
    rc1, out1, _ = _run_hook(json.dumps(payload))
    rc2, out2, _ = _run_hook(json.dumps(payload))
    assert rc1 == rc2 == 0
    assert out1 == out2


def test_handles_natural_contradiction_prompt():
    """The literal Flow 2 prompt fires the hook (issue #146 acceptance)."""
    payload = {
        "prompt": (
            "I know the roadmap said drag-and-drop to reorder commits, "
            "but actually we're switching to a text-editor approach. "
            "Please update cherry-pick.ts and reorder.ts."
        )
    }
    rc, out, _ = _run_hook(json.dumps(payload))
    assert rc == 0
    inner = _hook_output(json.loads(out))
    assert "additionalContext" in inner
    assert "bicameral.preflight" in inner["additionalContext"]
