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
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "preflight_reminder.py"


def _run_hook(stdin_text: str, env_overrides: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Invoke the hook with stdin_text on stdin; return (rc, stdout, stderr)."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
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


def test_reminder_gates_writes_not_discovery():
    """The reminder must allow Read/Grep/Glob discovery before preflight,
    and gate preflight against WRITE ops only. An earlier shape ("call
    preflight before any file-inspection tool") short-circuited the
    caller-LLM discovery the rest of the contract depends on (the agent
    needs to map "the X feature" → concrete file paths via Read/Grep/Glob
    before calling preflight). Lock the new posture in so future edits
    don't quietly regress it.
    """
    payload = {"prompt": "refactor the reorder feature to a text-editor flow"}
    rc, out, _ = _run_hook(json.dumps(payload))
    assert rc == 0
    ctx = _hook_output(json.loads(out))["additionalContext"]
    # Affirmative: discovery comes first, write op is the gate.
    assert "Read-only discovery FIRST" in ctx
    assert "BEFORE any write op" in ctx
    assert "Edit, Write" in ctx
    # The reminder should explicitly tell the agent to populate file_paths.
    assert "file_paths" in ctx
    # Negative: must NOT forbid file-inspection tools (the old shape).
    assert "before any file-inspection tool" not in ctx
    assert "Before invoking any file-inspection tool" not in ctx


# ── #402: slash-command surface form coverage ─────────────────────────


def test_hook_fires_on_qor_plan_with_issue_url(tmp_path):
    """End-to-end sociable test of the exact #402 reproduction.

    The hook is run as a real subprocess (same shape as Claude Code 2.x
    invokes it) with the literal failing prompt — ``/qor-plan
    <github-issue-url>``. It must emit the preflight ``hookSpecificOutput``
    envelope and write a ``trigger_evaluated`` row to the local JSONL log
    with ``prompt_surface_form == "slash_command_with_url"``.

    The classifier and the hook are NOT mocked — this is the contract
    the agent layer actually sees.
    """
    payload = {"prompt": ("/qor-plan https://github.com/BicameralAI/bicameral-daemon/issues/1")}
    env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    rc, out, _ = _run_hook(json.dumps(payload), env_overrides=env)
    assert rc == 0
    inner = _hook_output(json.loads(out))
    assert "additionalContext" in inner
    assert "bicameral.preflight" in inner["additionalContext"]

    # The trigger_evaluated row carries the surface-form label so we can
    # spot future regressions in the dashboard before users do.
    log_file = tmp_path / ".bicameral" / "preflight_trigger_evaluated.jsonl"
    assert log_file.exists(), "hook must write the trigger_evaluated row"
    lines = [json.loads(line) for line in log_file.read_text().splitlines() if line]
    assert any(
        row.get("fired") is True
        and row.get("prompt_surface_form") == "slash_command_with_url"
        and row.get("slash_command") == "qor-plan"
        for row in lines
    ), f"expected trigger_evaluated row not found in {lines!r}"


def test_hook_does_not_fire_for_qor_status(tmp_path):
    """Read-only slash-commands must not produce ``hookSpecificOutput`` but
    still record a ``fired: false`` telemetry row so we can spot future
    over-fire regressions symmetrically."""
    payload = {"prompt": "/qor-status"}
    env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    rc, out, _ = _run_hook(json.dumps(payload), env_overrides=env)
    assert rc == 0
    parsed = json.loads(out) if out.strip() else {}
    assert "hookSpecificOutput" not in parsed

    log_file = tmp_path / ".bicameral" / "preflight_trigger_evaluated.jsonl"
    assert log_file.exists()
    lines = [json.loads(line) for line in log_file.read_text().splitlines() if line]
    assert any(
        row.get("fired") is False
        and row.get("prompt_surface_form") == "slash_command_bare"
        and row.get("slash_command") == "qor-status"
        for row in lines
    )


def test_hook_telemetry_disabled_env_skips_jsonl(tmp_path):
    """``BICAMERAL_TELEMETRY=0`` must suppress the trigger_evaluated log
    without affecting the gate decision — opt-out parity with the
    existing relay telemetry."""
    payload = {"prompt": "/qor-plan add stripe webhook"}
    env = {
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "BICAMERAL_TELEMETRY": "0",
    }
    rc, out, _ = _run_hook(json.dumps(payload), env_overrides=env)
    assert rc == 0
    # Gate still fires — telemetry opt-out is decoupled from the gate.
    inner = _hook_output(json.loads(out))
    assert "additionalContext" in inner
    # JSONL log is absent because telemetry is disabled.
    log_file = tmp_path / ".bicameral" / "preflight_trigger_evaluated.jsonl"
    assert not log_file.exists(), "BICAMERAL_TELEMETRY=0 must suppress the log file"
