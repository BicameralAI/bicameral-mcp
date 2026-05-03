"""SessionEnd hook bridge for bicameral-capture-corrections.

Reads Claude Code's SessionEnd hook stdin contract, extracts the parent
session's transcript_path, and spawns capture-corrections via `claude -p`
with the transcript path propagated through BICAMERAL_PARENT_TRANSCRIPT_PATH.

Closes the transcript-passing half of #156. Without this bridge, the prior
inline shell command spawned `claude -p` with no transcript context, leaving
--auto-ingest mode silently no-op.

Optional argv flags ``--mcp-config <path>`` + ``--strict-mcp-config`` are
forwarded to the spawned ``claude -p`` so test harnesses can point the
subprocess at a non-default ledger.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

GUARD_ENV = "BICAMERAL_SESSION_END_RUNNING"
TRANSCRIPT_ENV = "BICAMERAL_PARENT_TRANSCRIPT_PATH"
CHILD_CLAUDE_CMD = ["claude", "-p", "/bicameral:capture-corrections --auto-ingest"]


def read_hook_stdin(stdin_text: str) -> dict:
    """Parse the SessionEnd hook contract JSON. Returns {} on parse failure
    so the hook never crashes the parent session."""
    try:
        return json.loads(stdin_text)
    except (json.JSONDecodeError, ValueError):
        return {}


def should_run(cwd: str, env: dict) -> bool:
    """True iff cwd has .bicameral/ AND the recursion guard is unset."""
    if not Path(cwd, ".bicameral").is_dir():
        return False
    if env.get(GUARD_ENV):
        return False
    return True


def _compute_subprocess_env(stdin_text: str, current_env: dict) -> dict:
    """Build the env for the spawned subprocess: copy + recursion guard +
    parent transcript path from the hook payload."""
    payload = read_hook_stdin(stdin_text)
    new_env = dict(current_env)
    new_env[GUARD_ENV] = "1"
    new_env[TRANSCRIPT_ENV] = payload.get("transcript_path", "")
    return new_env


def _build_child_argv(extra_argv: list[str]) -> list[str]:
    """Build the spawned claude argv. ``--mcp-config <path>`` and
    ``--strict-mcp-config`` are forwarded if present in extra_argv."""
    argv = list(CHILD_CLAUDE_CMD)
    if "--mcp-config" in extra_argv:
        i = extra_argv.index("--mcp-config")
        argv.extend(["--mcp-config", extra_argv[i + 1]])
    if "--strict-mcp-config" in extra_argv:
        argv.append("--strict-mcp-config")
    return argv


def main(argv: list[str] | None = None) -> int:
    extra = argv if argv is not None else sys.argv[1:]
    stdin_text = sys.stdin.read() if not sys.stdin.isatty() else ""
    payload = read_hook_stdin(stdin_text)
    cwd = payload.get("cwd") or os.getcwd()
    if not should_run(cwd, dict(os.environ)):
        return 0
    env = _compute_subprocess_env(stdin_text, dict(os.environ))
    child_argv = _build_child_argv(extra)
    try:
        subprocess.run(child_argv, env=env, check=False)
    except (FileNotFoundError, OSError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
