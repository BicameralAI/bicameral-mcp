"""Functionality tests for scripts/hooks/post_preflight_capture_reminder.py.

The hook is invoked as a subprocess by Claude Code on every PostToolUse
matching ``mcp__bicameral__bicameral_preflight``. Tests run it the same
way to exercise stdin/stdout exactly as production does.

The hook emits plain stdout text (no envelope) — the same shape the
existing PostToolUse/Bash hook uses. Claude Code appends the hook's
stdout to the tool result the agent sees on the next turn.
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


def test_emits_reminder_when_decisions_surfaced():
    """fired=True with ≥1 decision → reminder containing each decision_id + Step 5.6 template."""
    stdin = _make_stdin(
        fired=True,
        decisions=[
            {"decision_id": "decision:abc123", "description": "Drag-and-drop to reorder commits"},
            {"decision_id": "decision:def456", "description": "Cherry-pick across branches"},
        ],
    )
    rc, out, _ = _run_hook(stdin)
    assert rc == 0
    assert "<system-reminder>" in out
    assert "decision:abc123" in out
    assert "decision:def456" in out
    assert "Drag-and-drop to reorder commits" in out
    assert "bicameral.ingest" in out
    assert "agent_session" in out
    assert "bicameral.resolve_collision" in out
    assert "supersede" in out and "keep_both" in out and "link_parent" in out


def test_silent_when_fired_false():
    """fired=False → no output."""
    stdin = _make_stdin(fired=False, decisions=[])
    rc, out, _ = _run_hook(stdin)
    assert rc == 0
    assert out.strip() == ""


def test_silent_when_decisions_empty():
    """fired=True but decisions=[] → no output (nothing to contradict)."""
    stdin = _make_stdin(fired=True, decisions=[])
    rc, out, _ = _run_hook(stdin)
    assert rc == 0
    assert out.strip() == ""


def test_handles_response_as_json_string():
    """tool_response can arrive as a JSON-encoded string; reminder still fires."""
    stdin = _make_stdin(
        fired=True,
        decisions=[{"decision_id": "decision:xyz", "description": "Some constraint"}],
        response_as_string=True,
    )
    rc, out, _ = _run_hook(stdin)
    assert rc == 0
    assert "decision:xyz" in out
    assert "<system-reminder>" in out


def test_silent_when_tool_name_does_not_match():
    """Hook only fires for bicameral_preflight; other tools → silent."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git commit"},
        "tool_response": {"fired": True, "decisions": [{"decision_id": "x", "description": "y"}]},
    }
    rc, out, _ = _run_hook(json.dumps(payload))
    assert rc == 0
    assert out.strip() == ""


def test_handles_malformed_stdin():
    """Non-JSON stdin returns rc 0 with no output — never blocks user."""
    rc, out, _ = _run_hook("this is not JSON at all {[}")
    assert rc == 0
    assert out.strip() == ""


def test_handles_missing_tool_response():
    """Payload without tool_response → silent (no contradiction signal)."""
    payload = {"tool_name": PREFLIGHT_TOOL_NAME, "tool_input": {}}
    rc, out, _ = _run_hook(json.dumps(payload))
    assert rc == 0
    assert out.strip() == ""


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
