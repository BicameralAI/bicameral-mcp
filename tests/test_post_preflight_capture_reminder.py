"""#170 — implementation-intent gate on the post-preflight capture reminder.

The PostToolUse capture hook must suppress its disambiguation reminder when the
originating user prompt had no implementation intent (read-only), and must keep
firing for implementation-intent prompts — including compatible ones like
"add tests for X" (the #175 invariant: never lexically pre-judge contradiction).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.hooks._prompt_intent_state import (  # noqa: E402
    read_intent,
    state_path,
    write_intent,
)
from scripts.hooks.preflight_intent import (  # noqa: E402
    has_implementation_signal,
    should_fire_preflight,
)

POST_HOOK = REPO_ROOT / "scripts" / "hooks" / "post_preflight_capture_reminder.py"
PRE_HOOK = REPO_ROOT / "scripts" / "hooks" / "preflight_reminder.py"

_DECISION = {"decision_id": "decision:abc123", "description": "Drag-to-reorder commits"}


def _sid() -> str:
    return f"test-170-{uuid.uuid4().hex}"


def _run(script: Path, payload: dict) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.returncode, proc.stdout


def _fired(stdout: str) -> bool:
    """True iff the hook emitted a capture-reminder envelope."""
    if not stdout.strip():
        return False
    parsed = json.loads(stdout)
    return "hookSpecificOutput" in parsed


def _post_payload(sid: str) -> dict:
    return {
        "tool_name": "mcp__bicameral__bicameral_preflight",
        "tool_response": {"fired": True, "decisions": [_DECISION]},
        "session_id": sid,
    }


# ── State module unit tests ────────────────────────────────────────────


def test_state_round_trip():
    sid = _sid()
    write_intent(sid, True)
    assert read_intent(sid) is True
    write_intent(sid, False)
    assert read_intent(sid) is False


def test_read_absent_is_none():
    assert read_intent(_sid()) is None  # never written
    assert read_intent("") is None  # empty session id


def test_ttl_sweep_removes_stale():
    stale_sid, fresh_sid = _sid(), _sid()
    write_intent(stale_sid, True)
    # backdate the stale file well past the TTL
    os.utime(state_path(stale_sid), (time.time() - 90_000, time.time() - 90_000))
    write_intent(fresh_sid, True)  # any write triggers the sweep
    assert not state_path(stale_sid).exists(), "stale state file should be swept"
    assert read_intent(fresh_sid) is True, "fresh state file must survive the sweep"


def test_read_stale_is_none():
    sid = _sid()
    write_intent(sid, False)
    os.utime(state_path(sid), (time.time() - 90_000, time.time() - 90_000))
    assert read_intent(sid) is None


# ── PostToolUse capture-hook behavioral tests (subprocess) ──────────────


def test_suppressed_when_read_only():
    """Read-only prompt (fire=False persisted) → reminder suppressed."""
    sid = _sid()
    write_intent(sid, False)
    rc, out = _run(POST_HOOK, _post_payload(sid))
    assert rc == 0
    assert not _fired(out), "capture reminder must be suppressed for a non-impl prompt"


def test_fires_when_impl_intent():
    """Implementation-intent prompt (fire=True persisted) → reminder fires."""
    sid = _sid()
    write_intent(sid, True)
    rc, out = _run(POST_HOOK, _post_payload(sid))
    assert rc == 0
    assert _fired(out), "capture reminder must fire for an impl-intent prompt"
    assert "AskUserQuestion" in out  # the #175 disambiguation path intact


def test_default_fires_when_state_absent():
    """No state file for the session → fire (safe default; missed capture is irreversible)."""
    rc, out = _run(POST_HOOK, _post_payload(_sid()))
    assert rc == 0
    assert _fired(out), "absent state must default to firing"


def test_fires_for_compatible_add_tests():
    """#175 guard (end-to-end): 'add tests for X' carries implementation signal
    (verb 'add'), so it is NOT lexically suppressed and reaches the
    user-disambiguation — even though should_fire_preflight skip-lists it."""
    prompt = "add tests for the existing drag-to-reorder behavior"
    assert has_implementation_signal(prompt) is True
    assert (
        should_fire_preflight(prompt) is False
    )  # skip-listed: documents the deliberate divergence
    sid = _sid()
    rc, _ = _run(PRE_HOOK, {"prompt": prompt, "session_id": sid})
    assert rc == 0
    assert read_intent(sid) is True  # write side persists has_implementation_signal
    rc, out = _run(POST_HOOK, _post_payload(sid))
    assert rc == 0 and _fired(out), "add-tests must reach user-disambiguation (#175)"


# ── Write-side: UserPromptSubmit hook persists the classification ───────


def test_write_side_persists_signal():
    sid = _sid()
    prompt = "refactor the rate limiter to a sliding window"
    rc, _ = _run(PRE_HOOK, {"prompt": prompt, "session_id": sid})
    assert rc == 0
    expected = has_implementation_signal(prompt)
    assert expected is True
    assert read_intent(sid) is True


def test_write_side_persists_false_for_read_only():
    sid = _sid()
    prompt = "what does the rate limiter currently do?"
    rc, _ = _run(PRE_HOOK, {"prompt": prompt, "session_id": sid})
    assert rc == 0
    expected = has_implementation_signal(prompt)
    assert expected is False
    assert read_intent(sid) is False
