"""Integration tests for the #170 capture-reminder suppression gate.

Drives the real PostToolUse hook ``main()`` with JSON payloads on stdin and a
real (tempdir-redirected) session prompt file written by the real
``session_prompt_store`` — no mocks of the classifier or the store. Covers the
three #170 acceptance fixtures, the recall-bias fallback (missing prompt → fire),
and the cleanup/staleness story.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts.hooks import post_preflight_capture_reminder as hook  # noqa: E402
from scripts.hooks import session_end_queue_writer as session_end  # noqa: E402
from scripts.hooks import session_prompt_store as store  # noqa: E402

_SID = "sess-170-test"
_DECISIONS = [{"decision_id": "decision:abc", "description": "Drag-to-reorder commits"}]


def _run_hook(payload: dict) -> str:
    """Invoke the hook main() with payload on stdin; return captured stdout."""
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps(payload))
    sys.stdout = io.StringIO()
    try:
        hook.main()
        return sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_in, old_out


def _payload(session_id: str = _SID) -> dict:
    return {
        "tool_name": "mcp__bicameral__bicameral_preflight",
        "tool_response": {"fired": True, "decisions": _DECISIONS},
        "session_id": session_id,
    }


def _fired(stdout: str) -> bool:
    return "system-reminder" in stdout and "AskUserQuestion" in stdout


def _redirect_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))


def test_contradiction_prompt_fires(monkeypatch, tmp_path):
    """Acceptance (a): a contradiction / write-intent prompt → reminder fires."""
    _redirect_store(monkeypatch, tmp_path)
    store.write_session_prompt(
        _SID, "actually we're switching reorder to buttons — update reorder.ts"
    )
    assert _fired(_run_hook(_payload()))


def test_compatible_add_tests_still_fires(monkeypatch, tmp_path):
    """Acceptance (b) trade-off: 'add tests for X' carries 'add' → NOT suppressed.
    Documented #170 decision (preserve #175 over suppressing compatible writes)."""
    _redirect_store(monkeypatch, tmp_path)
    store.write_session_prompt(_SID, "add tests for drag-to-reorder")
    assert _fired(_run_hook(_payload()))


def test_read_only_prompt_suppressed(monkeypatch, tmp_path):
    """Acceptance (c): a read-only / tangential prompt → reminder suppressed."""
    _redirect_store(monkeypatch, tmp_path)
    store.write_session_prompt(_SID, "explain how the reorder flow works")
    assert _run_hook(_payload()) == ""


def test_missing_prompt_file_fires(monkeypatch, tmp_path):
    """Recall-bias fallback: no stored prompt → fire (never silently suppress)."""
    _redirect_store(monkeypatch, tmp_path)
    # no write_session_prompt for this session
    assert _fired(_run_hook(_payload(session_id="sess-never-written")))


def test_no_decisions_no_reminder(monkeypatch, tmp_path):
    """Precondition preserved: fired but zero decisions → no reminder."""
    _redirect_store(monkeypatch, tmp_path)
    store.write_session_prompt(_SID, "add the feature")
    payload = _payload()
    payload["tool_response"] = {"fired": True, "decisions": []}
    assert _run_hook(payload) == ""


# ── store round-trip + cleanup + staleness sweep ─────────────────────────


def test_store_round_trip_and_cleanup(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    store.write_session_prompt(_SID, "hello prompt")
    assert store.read_session_prompt(_SID) == "hello prompt"
    store.cleanup_session_prompt(_SID)
    assert store.read_session_prompt(_SID) is None


def test_staleness_sweep_on_write(monkeypatch, tmp_path):
    """A backdated orphan is swept on the next write; a fresh file survives."""
    _redirect_store(monkeypatch, tmp_path)
    store.write_session_prompt("old-session", "stale")
    old_file = tmp_path / store._DIR_NAME / "old-session.txt"
    backdated = time.time() - (store._STALE_SECONDS + 3600)
    import os

    os.utime(old_file, (backdated, backdated))
    store.write_session_prompt("new-session", "fresh")  # triggers sweep
    assert not old_file.exists()
    assert (tmp_path / store._DIR_NAME / "new-session.txt").exists()


def test_session_end_hook_cleans_prompt(monkeypatch, tmp_path):
    """The SessionEnd hook deletes the session prompt file (graceful path)."""
    _redirect_store(monkeypatch, tmp_path)
    store.write_session_prompt(_SID, "to be cleaned")
    old_in = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"session_id": _SID}))
    try:
        session_end.main()
    finally:
        sys.stdin = old_in
    assert store.read_session_prompt(_SID) is None
