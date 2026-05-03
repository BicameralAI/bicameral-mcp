"""Functionality tests for scripts/hooks/post_commit_sync_reminder.py.

The hook is invoked as a subprocess by Claude Code on every PostToolUse
matching ``Bash``. Tests run it the same way to exercise stdin/stdout
exactly as production does.

Claude Code 2.x requires PostToolUse hook output shaped as
``{"hookSpecificOutput": {"hookEventName": "PostToolUse",
"additionalContext": "..."}}``. Plain stdout from PostToolUse hooks is
silently dropped to the debug log (per
https://code.claude.com/docs/en/hooks). These tests assert against the
envelope shape — anything else is a broken contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "post_commit_sync_reminder.py"


def _run_hook(stdin_text: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _make_stdin(*, tool_name: str = "Bash", command: str = "") -> str:
    return json.dumps({"tool_name": tool_name, "tool_input": {"command": command}})


def _hook_output(parsed: dict) -> dict:
    """Extract hookSpecificOutput.additionalContext, asserting envelope shape."""
    assert "hookSpecificOutput" in parsed, (
        f"hook must emit hookSpecificOutput envelope (Claude Code 2.x contract); got {parsed!r}"
    )
    inner = parsed["hookSpecificOutput"]
    assert inner.get("hookEventName") == "PostToolUse"
    return inner


def _assert_silent(out: str) -> None:
    """No envelope written. Tolerate fully-empty stdout or `{}`."""
    if not out.strip():
        return
    parsed = json.loads(out)
    assert "hookSpecificOutput" not in parsed


def test_emits_reminder_on_git_commit():
    rc, out, _ = _run_hook(_make_stdin(command="git commit -m 'feat: add foo'"))
    assert rc == 0
    inner = _hook_output(json.loads(out))
    ctx = inner["additionalContext"]
    assert "bicameral: new commit detected" in ctx
    assert "/bicameral:sync" in ctx


def test_emits_reminder_on_git_merge():
    rc, out, _ = _run_hook(_make_stdin(command="git merge feature/foo --no-ff"))
    assert rc == 0
    inner = _hook_output(json.loads(out))
    assert "bicameral: new commit detected" in inner["additionalContext"]


def test_emits_reminder_on_git_pull():
    rc, out, _ = _run_hook(_make_stdin(command="git pull origin main"))
    assert rc == 0
    inner = _hook_output(json.loads(out))
    assert "bicameral: new commit detected" in inner["additionalContext"]


def test_emits_reminder_on_git_rebase_continue():
    rc, out, _ = _run_hook(_make_stdin(command="git rebase --continue"))
    assert rc == 0
    inner = _hook_output(json.loads(out))
    assert "bicameral: new commit detected" in inner["additionalContext"]


def test_silent_on_read_only_git_command():
    """git status, git log, git diff, etc. → silent."""
    for cmd in ["git status", "git log -10", "git diff HEAD", "git branch -a"]:
        rc, out, _ = _run_hook(_make_stdin(command=cmd))
        assert rc == 0
        _assert_silent(out)


def test_silent_on_non_bash_tool():
    """Hook only fires for Bash; other tools → silent."""
    rc, out, _ = _run_hook(_make_stdin(tool_name="Edit", command="git commit"))
    assert rc == 0
    _assert_silent(out)


def test_silent_on_non_git_bash_command():
    rc, out, _ = _run_hook(_make_stdin(command="ls -la"))
    assert rc == 0
    _assert_silent(out)


def test_handles_malformed_stdin():
    rc, out, _ = _run_hook("this is not JSON at all {[}")
    assert rc == 0
    _assert_silent(out)


def test_handles_missing_tool_input():
    payload = json.dumps({"tool_name": "Bash"})
    rc, out, _ = _run_hook(payload)
    assert rc == 0
    _assert_silent(out)


def test_handles_non_dict_tool_input():
    payload = json.dumps({"tool_name": "Bash", "tool_input": "git commit"})
    rc, out, _ = _run_hook(payload)
    assert rc == 0
    _assert_silent(out)


def test_idempotent_on_double_fire():
    stdin = _make_stdin(command="git commit -m 'whatever'")
    rc1, out1, _ = _run_hook(stdin)
    rc2, out2, _ = _run_hook(stdin)
    assert rc1 == rc2 == 0
    assert out1 == out2
