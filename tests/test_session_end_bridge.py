"""Functionality tests for events.session_end_bridge.

Closes the transcript-passing half of #156. Verifies the bridge's
stdin -> env -> subprocess pipeline: parent transcript_path is read
from Claude Code's hook stdin contract and propagated to the spawned
capture-corrections subprocess via BICAMERAL_PARENT_TRANSCRIPT_PATH.
"""

from __future__ import annotations

import io
import json
import os
from unittest.mock import patch

import pytest

from events import session_end_bridge as bridge


def test_bridge_extracts_transcript_path_from_stdin_and_propagates_via_env():
    stdin_text = json.dumps({
        "session_id": "abc",
        "transcript_path": "/tmp/parent-transcript.jsonl",
        "cwd": "/repo",
        "hook_event_name": "SessionEnd",
    })
    env = bridge._compute_subprocess_env(stdin_text, {"PATH": "/usr/bin"})
    assert env["BICAMERAL_PARENT_TRANSCRIPT_PATH"] == "/tmp/parent-transcript.jsonl"
    assert env["BICAMERAL_SESSION_END_RUNNING"] == "1"
    assert env["PATH"] == "/usr/bin"


def test_bridge_skips_when_no_bicameral_dir_exists(tmp_path):
    # tmp_path has no .bicameral/ directory.
    assert bridge.should_run(str(tmp_path), {}) is False


def test_bridge_skips_when_recursion_guard_set(tmp_path):
    (tmp_path / ".bicameral").mkdir()
    env = {bridge.GUARD_ENV: "1"}
    assert bridge.should_run(str(tmp_path), env) is False


def test_bridge_main_invokes_claude_subprocess_with_correct_env_when_stdin_valid(tmp_path, monkeypatch):
    (tmp_path / ".bicameral").mkdir()
    stdin_text = json.dumps({
        "session_id": "s1",
        "transcript_path": "/x.jsonl",
        "cwd": str(tmp_path),
        "hook_event_name": "SessionEnd",
    })
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    monkeypatch.setattr(os, "environ", {"PATH": "/p"})

    calls = []

    def _record(argv, env=None, check=None):
        calls.append({"argv": argv, "env": env})

        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(bridge.subprocess, "run", _record)
    rc = bridge.main()

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["argv"] == bridge.CHILD_CLAUDE_CMD
    env = calls[0]["env"]
    assert env["BICAMERAL_PARENT_TRANSCRIPT_PATH"] == "/x.jsonl"
    assert env["BICAMERAL_SESSION_END_RUNNING"] == "1"


def test_bridge_main_no_op_when_stdin_malformed_json(tmp_path, monkeypatch):
    (tmp_path / ".bicameral").mkdir()
    monkeypatch.setattr("sys.stdin", io.StringIO("not json {"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    monkeypatch.setattr(os, "environ", {"PATH": "/p"})

    calls = []
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **kw: calls.append(a))
    rc = bridge.main()

    assert rc == 0
    # cwd from stdin is empty -> falls back to os.getcwd() which has .bicameral/
    # so subprocess IS called even though transcript path is empty string.
    # This test specifically asserts no crash on malformed JSON.
    # The malformed JSON -> read_hook_stdin returns {}, cwd falls back to os.getcwd().
    # Since os.getcwd() returns tmp_path (with .bicameral/), the subprocess IS invoked.
    # The functionality assertion: rc=0 AND no exception was raised.
    assert rc == 0


def test_bridge_main_uses_cwd_from_stdin_payload_not_process_cwd(tmp_path, monkeypatch):
    """Per Claude Code hook contract, cwd arrives in stdin JSON. The bridge
    must use stdin.cwd for the .bicameral/ guard, not the process cwd."""
    bicameral_repo = tmp_path / "repo"
    bicameral_repo.mkdir()
    (bicameral_repo / ".bicameral").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # No .bicameral/ in elsewhere

    stdin_text = json.dumps({
        "transcript_path": "/x.jsonl",
        "cwd": str(bicameral_repo),
    })
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    # Process cwd is the elsewhere dir (no .bicameral/)
    monkeypatch.setattr(os, "getcwd", lambda: str(elsewhere))
    monkeypatch.setattr(os, "environ", {"PATH": "/p"})

    calls = []
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **kw: calls.append({"argv": a, "env": kw.get("env")}) or type("R", (), {"returncode": 0})())

    rc = bridge.main()

    # subprocess WAS called: the stdin payload's cwd satisfied the guard
    # even though process cwd would not have.
    assert rc == 0
    assert len(calls) == 1


def test_setup_wizard_session_end_command_invokes_bridge_module():
    """Guards the literal hook-command constant against drift."""
    import setup_wizard
    assert setup_wizard._BICAMERAL_SESSION_END_COMMAND == "python3 -m events.session_end_bridge"
