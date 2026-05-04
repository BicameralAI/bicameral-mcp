"""Functionality tests for scripts/hooks/post_preflight_capture_reminder.py.

The hook is invoked as a subprocess by Claude Code on every PostToolUse
matching ``mcp__bicameral__bicameral_preflight``. Tests run it the same
way to exercise stdin/stdout exactly as production does.

Claude Code 2.x requires PostToolUse hook output shaped as
``{"hookSpecificOutput": {"hookEventName": "PostToolUse",
"additionalContext": "..."}}``. Plain stdout from PostToolUse hooks is
silently dropped to the debug log (per
https://code.claude.com/docs/en/hooks — only UserPromptSubmit /
UserPromptExpansion / SessionStart treat raw stdout as agent-visible
context). These tests assert against the envelope shape — anything else
is a broken contract regardless of whether the hook process exits
cleanly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "post_preflight_capture_reminder.py"

PREFLIGHT_TOOL_NAME = "mcp__bicameral__bicameral_preflight"


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


def _make_stdin(*, fired: bool, decisions: list[dict], response_as_string: bool = False) -> str:
    response = {"fired": fired, "decisions": decisions}
    payload = {
        "tool_name": PREFLIGHT_TOOL_NAME,
        "tool_input": {"topic": "reorder commits", "file_paths": ["app/src/lib/git/reorder.ts"]},
        "tool_response": json.dumps(response) if response_as_string else response,
    }
    return json.dumps(payload)


def _hook_output(parsed: dict) -> dict:
    """Extract hookSpecificOutput.additionalContext, asserting envelope shape."""
    assert "hookSpecificOutput" in parsed, (
        f"hook must emit hookSpecificOutput envelope (Claude Code 2.x contract); got {parsed!r}"
    )
    inner = parsed["hookSpecificOutput"]
    assert inner.get("hookEventName") == "PostToolUse"
    return inner


def test_emits_reminder_when_decisions_surfaced():
    """fired=True with ≥1 decision → envelope with reminder containing each
    decision_id + the Step 5.6.1 AskUserQuestion disambiguation template
    (per #175). The reminder no longer templates the bare ingest+
    resolve_collision sequence — it templates the user-disambiguation
    question whose answer drives Step 5.6.2's mechanical capture.
    """
    stdin = _make_stdin(
        fired=True,
        decisions=[
            {"decision_id": "decision:abc123", "description": "Drag-and-drop to reorder commits"},
            {"decision_id": "decision:def456", "description": "Cherry-pick across branches"},
        ],
    )
    rc, out, _ = _run_hook(stdin)
    assert rc == 0
    inner = _hook_output(json.loads(out))
    ctx = inner["additionalContext"]
    assert "<system-reminder>" in ctx
    # Surfaced decisions are listed verbatim so the agent can scope the
    # disambiguation question.
    assert "decision:abc123" in ctx
    assert "decision:def456" in ctx
    assert "Drag-and-drop to reorder commits" in ctx
    # The Step 5.6.1 AskUserQuestion shape is templated.
    assert "AskUserQuestion" in ctx
    assert "supersede" in ctx and "keep_both" in ctx
    assert "unrelated" in ctx
    # Branch instructions for Step 5.6.2 are still present so the agent
    # knows what to do with each answer.
    assert "agent_session" in ctx
    assert "resolve_collision" in ctx


def test_reminder_routes_judgment_to_user_not_agent():
    """Per #175, the agent must NOT judge contradiction itself — it asks
    the user via ``AskUserQuestion`` and acts on the answer mechanically.
    Lock the user-disambiguation posture in so future edits don't quietly
    regress to ``"you MUST capture"`` (which the agent demonstrably
    ignored on borderline prompts) or to the original ``"IF you
    contradict ..."`` conditional gate.
    """
    stdin = _make_stdin(
        fired=True,
        decisions=[{"decision_id": "decision:abc", "description": "Some prior decision"}],
    )
    _, out, _ = _run_hook(stdin)
    ctx = _hook_output(json.loads(out))["additionalContext"]
    # Affirmative: judgment moves to the user.
    assert "do NOT judge contradiction yourself" in ctx
    assert "ask the user" in ctx
    assert "AskUserQuestion" in ctx
    # Negative: must NOT contain the prior unconditional capture wording
    # (which short-circuited the user-in-the-loop design) NOR the original
    # conditional escape hatch (which over-deferred to agent judgment).
    assert "BEFORE any code edits, you MUST capture" not in ctx
    assert "If your current prompt CONTRADICTS" not in ctx
    assert "If your prompt is COMPATIBLE" not in ctx
    assert "ignore this and proceed normally" not in ctx


def _assert_silent(out: str) -> None:
    """No envelope written. Tolerate fully-empty stdout or `{}`."""
    if not out.strip():
        return
    parsed = json.loads(out)
    assert "hookSpecificOutput" not in parsed


def test_silent_when_fired_false():
    """fired=False → no envelope."""
    stdin = _make_stdin(fired=False, decisions=[])
    rc, out, _ = _run_hook(stdin)
    assert rc == 0
    _assert_silent(out)


def test_silent_when_decisions_empty():
    """fired=True but decisions=[] → no envelope (nothing to contradict)."""
    stdin = _make_stdin(fired=True, decisions=[])
    rc, out, _ = _run_hook(stdin)
    assert rc == 0
    _assert_silent(out)


def test_handles_response_as_json_string():
    """tool_response can arrive as a JSON-encoded string; reminder still fires."""
    stdin = _make_stdin(
        fired=True,
        decisions=[{"decision_id": "decision:xyz", "description": "Some constraint"}],
        response_as_string=True,
    )
    rc, out, _ = _run_hook(stdin)
    assert rc == 0
    inner = _hook_output(json.loads(out))
    assert "decision:xyz" in inner["additionalContext"]


def test_silent_when_tool_name_does_not_match():
    """Hook only fires for bicameral_preflight; other tools → silent."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git commit"},
        "tool_response": {"fired": True, "decisions": [{"decision_id": "x", "description": "y"}]},
    }
    rc, out, _ = _run_hook(json.dumps(payload))
    assert rc == 0
    _assert_silent(out)


def test_handles_malformed_stdin():
    """Non-JSON stdin returns rc 0 with no envelope — never blocks user."""
    rc, out, _ = _run_hook("this is not JSON at all {[}")
    assert rc == 0
    _assert_silent(out)


def test_handles_missing_tool_response():
    """Payload without tool_response → silent (no contradiction signal)."""
    payload = {"tool_name": PREFLIGHT_TOOL_NAME, "tool_input": {}}
    rc, out, _ = _run_hook(json.dumps(payload))
    assert rc == 0
    _assert_silent(out)


def test_idempotent_on_double_fire():
    """Same input twice produces identical output (no state leak)."""
    stdin = _make_stdin(
        fired=True,
        decisions=[{"decision_id": "decision:abc", "description": "Some decision"}],
    )
    rc1, out1, _ = _run_hook(stdin)
    rc2, out2, _ = _run_hook(stdin)
    assert rc1 == rc2 == 0
    assert out1 == out2
