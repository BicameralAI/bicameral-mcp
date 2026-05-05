"""Functionality tests for the SessionEnd hook canonical command shape.

History:
  - Originally (plan-147-flow4-ledger-validation.md Phase 2) verified the
    `claude -p '/bicameral-capture-corrections --auto-ingest'` invocation
    landed identically in .claude/settings.json and
    setup_wizard._BICAMERAL_SESSION_END_COMMAND.
  - Reshaped per plan-156-sessionend-queue-pivot.md Phase 3: the prior
    canonical command spawned an empty subprocess that couldn't access the
    parent transcript. The new shape pipes the SessionEnd JSON envelope
    into a Python script that writes the transcript to a per-session
    pending queue; capture-corrections drains the queue at next-session
    preflight Step 3.5 / Step 0.

Verifies the canonical hook command shape lands in:
  - .claude/settings.json (the deployed hook)
  - setup_wizard._BICAMERAL_SESSION_END_COMMAND (the source of truth for
    fresh installs)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


CANONICAL_COMMAND = (
    '[ -d .bicameral ] && [ -z "$BICAMERAL_SESSION_END_RUNNING" ] && '
    "BICAMERAL_SESSION_END_RUNNING=1 "
    "python3 scripts/hooks/session_end_queue_writer.py || true"
)


def _extract_session_end_command() -> str:
    """Parse .claude/settings.json and return the SessionEnd hook command string."""
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    session_end = settings["hooks"]["SessionEnd"]
    return session_end[0]["hooks"][0]["command"]


def test_settings_json_session_end_has_reentrancy_guard():
    """Behavior: deployed SessionEnd hook short-circuits when env var is set."""
    cmd = _extract_session_end_command()
    assert '[ -z "$BICAMERAL_SESSION_END_RUNNING" ]' in cmd
    assert "BICAMERAL_SESSION_END_RUNNING=1" in cmd


def test_settings_json_session_end_invokes_queue_writer():
    """Behavior: deployed SessionEnd hook invokes the queue writer (#156).

    Replaces the prior `--auto-ingest` flag assertion. The queue writer
    copies the parent transcript into `.bicameral/pending-transcripts/`
    so the next session's capture-corrections drain can surface
    corrections with full ledger context."""
    cmd = _extract_session_end_command()
    assert "scripts/hooks/session_end_queue_writer.py" in cmd
    assert "--auto-ingest" not in cmd
    assert "claude -p" not in cmd


def test_setup_wizard_renders_canonical_session_end_hook():
    """Behavior: setup_wizard's source-of-truth constant matches the
    canonical command verbatim. Drift between this constant and the
    SKILL.md prescription is the failure mode this test exists to catch."""
    import setup_wizard

    assert setup_wizard._BICAMERAL_SESSION_END_COMMAND == CANONICAL_COMMAND


def test_build_session_end_command_no_args_matches_canonical():
    """Behavior: the parameterized helper, when called with no args,
    produces the same string as the no-args constant — i.e. end-user
    installs are unchanged by the helper's existence."""
    import setup_wizard

    assert setup_wizard._build_session_end_command() == CANONICAL_COMMAND


def test_build_session_end_command_ignores_mcp_config_path():
    """Behavior: post #156, the new SessionEnd hook is a path-style Python
    script that writes a transcript-queue file; it does not spawn a
    `claude -p` subprocess and therefore does not need MCP config
    inheritance. The `mcp_config_path` parameter is retained for caller
    signature stability (e.g. the e2e harness's prior call site) but is
    documented as ignored. This test locks the documented contract: the
    function returns the canonical command regardless of the argument."""
    import setup_wizard

    assert (
        setup_wizard._build_session_end_command(mcp_config_path="/tmp/x/mcp.json")
        == CANONICAL_COMMAND
    )
    assert setup_wizard._build_session_end_command(mcp_config_path=None) == CANONICAL_COMMAND
