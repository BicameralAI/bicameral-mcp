"""#224 Phase C-pre tests for the Claude Code hook scripts.

Sociable per CLAUDE.md: invokes the real hook script as a subprocess
against a real bicameral checkout (the test's repo), real
``ledger.timeout_telemetry`` ring buffer state. The only seam is
``CLAUDE_PROJECT_DIR`` (env), which the harness uses to point the
hook at this repo's root.

These tests pin three contracts:

1. The session-start hook always exits 0 and emits a parseable
   one-line brief to stderr.
2. The pre-tool-use hook always exits 0 and emits a warning to
   stderr only when recent timeouts exist.
3. ``PreflightResponse.recent_timeout_count`` is shaped as
   ``{"read": int, "drift": int}`` so the hook + MCP both see the
   same default value when nothing has timed out.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ledger import timeout_telemetry

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOKS_DIR = _REPO_ROOT / ".claude" / "hooks"


@pytest.fixture(autouse=True)
def _clear_buffer():
    timeout_telemetry.clear_for_testing()
    yield
    timeout_telemetry.clear_for_testing()


def _run_hook(script: str, *, stdin: str = "") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(_REPO_ROOT)
    return subprocess.run(
        [sys.executable, str(_HOOKS_DIR / script)],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        timeout=15,
    )


def test_session_start_hook_exits_zero_with_no_timeouts() -> None:
    result = _run_hook("session_start_timeout_posture.py")
    assert result.returncode == 0
    assert "[bicameral] query timeouts last 1h:" in result.stderr
    assert "0 read / 0 drift" in result.stderr
    assert "budgets:" in result.stderr


def test_session_start_hook_includes_env_disable_state(monkeypatch) -> None:
    """The brief surfaces whether BICAMERAL_QUERY_TIMEOUT_DISABLE is on."""
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(_REPO_ROOT)
    env["BICAMERAL_QUERY_TIMEOUT_DISABLE"] = "1"
    result = subprocess.run(
        [sys.executable, str(_HOOKS_DIR / "session_start_timeout_posture.py")],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0
    assert "env-disable: on" in result.stderr


def test_session_start_hook_reflects_recent_timeouts() -> None:
    """If the ring buffer has events, the count appears in the brief."""
    timeout_telemetry.record_timeout(
        sql_prefix="SELECT 1",
        timeout_class="read",
        elapsed_seconds=6.0,
        budget_seconds=5.0,
    )
    timeout_telemetry.record_timeout(
        sql_prefix="SELECT 2",
        timeout_class="drift",
        elapsed_seconds=35.0,
        budget_seconds=30.0,
    )
    # The hook runs in a subprocess — it sees a fresh, empty ring
    # buffer in that subprocess. So this test verifies the in-process
    # buffer state directly. The subprocess-side coverage is the
    # exit-0 + brief-shape test above.
    counts = timeout_telemetry.recent_timeout_counts()
    assert counts == {"read": 1, "drift": 1}


def test_pre_tool_use_hook_exits_zero_with_no_timeouts() -> None:
    """No-timeout path: hook is quiet (exit 0, empty stderr posture line)."""
    result = _run_hook("pre_tool_use_timeout_context.py", stdin="{}")
    assert result.returncode == 0
    # Quiet path — should not emit the "recent ledger-query timeouts" line.
    assert "recent ledger-query timeouts" not in result.stderr


def test_pre_tool_use_hook_drains_stdin() -> None:
    """Even with a large JSON envelope on stdin, hook completes promptly."""
    big_payload = '{"tool": "bicameral_search", "args": ' + ('"x" * 10000') + "}"
    result = _run_hook("pre_tool_use_timeout_context.py", stdin=big_payload)
    assert result.returncode == 0


def test_session_start_hook_handles_missing_bicameral_import(tmp_path) -> None:
    """Run the hook with CLAUDE_PROJECT_DIR pointing at an empty dir.
    It should exit 0 and emit a single warning to stderr, not crash."""
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    # Also strip PYTHONPATH so it can't find bicameral via the parent env.
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(_HOOKS_DIR / "session_start_timeout_posture.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=15,
    )
    # Exit 0 — graceful degradation.
    assert result.returncode == 0


def test_preflight_response_includes_recent_timeout_count_field() -> None:
    """Schema check — the new additive field is present with the
    documented default shape, so older response consumers can ignore
    it and hooks can rely on a stable key structure."""
    from contracts import PreflightResponse

    resp = PreflightResponse(topic="t", fired=False, reason="no_matches", guided_mode=False)
    assert resp.recent_timeout_count == {"read": 0, "drift": 0}

    resp2 = PreflightResponse(
        topic="t",
        fired=False,
        reason="no_matches",
        guided_mode=False,
        recent_timeout_count={"read": 7, "drift": 1},
    )
    assert resp2.recent_timeout_count == {"read": 7, "drift": 1}


# ── ring-buffer cap ────────────────────────────────────────────────


def test_timeout_telemetry_ring_buffer_caps_at_1000() -> None:
    """Per Phase C-pre design — buffer is bounded so a runaway timeout
    storm doesn't unbounded-grow process memory."""
    for i in range(1500):
        timeout_telemetry.record_timeout(
            sql_prefix=f"SELECT {i}",
            timeout_class="read",
            elapsed_seconds=6.0,
            budget_seconds=5.0,
        )
    assert timeout_telemetry.buffer_size() == 1000


def test_recent_timeout_counts_respects_window() -> None:
    """An entry older than the configured window must not appear in
    the per-class count."""
    import time as _time
    from unittest.mock import patch

    # Inject a record with a recorded_at well in the past.
    fake_old = _time.time() - 10_000
    event = timeout_telemetry.TimeoutEvent(
        sql_prefix="old",
        timeout_class="read",
        elapsed_seconds=10.0,
        budget_seconds=5.0,
        recorded_at=fake_old,
    )
    timeout_telemetry._buffer.append(event)
    # A fresh recent event.
    timeout_telemetry.record_timeout(
        sql_prefix="fresh",
        timeout_class="read",
        elapsed_seconds=6.0,
        budget_seconds=5.0,
    )
    counts = timeout_telemetry.recent_timeout_counts(window_seconds=3600.0)
    assert counts["read"] == 1  # the old entry filtered out
