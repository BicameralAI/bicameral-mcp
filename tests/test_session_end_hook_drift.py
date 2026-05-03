"""Functionality tests for SessionEnd hook drift fix per
plan-147-flow4-ledger-validation.md Phase 2 + Priority B v0 final-blockers
plan (transcript bridge).

Verifies the canonical hook command shape lands in:
  - .claude/settings.json (the deployed hook)
  - setup_wizard._BICAMERAL_SESSION_END_COMMAND (the source of truth for
    fresh installs)

The canonical command is now ``python3 -m events.session_end_bridge``
(post-Priority-B v0 final-blockers). The bridge module handles the
.bicameral/ guard, BICAMERAL_SESSION_END_RUNNING recursion guard,
--auto-ingest flag, and BICAMERAL_PARENT_TRANSCRIPT_PATH env-var
propagation that closes the transcript-passing half of #156.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


CANONICAL_COMMAND = "python3 -m events.session_end_bridge"


def _extract_session_end_command() -> str:
    """Parse .claude/settings.json and return the SessionEnd hook command string."""
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    session_end = settings["hooks"]["SessionEnd"]
    return session_end[0]["hooks"][0]["command"]


def test_settings_json_session_end_invokes_bridge_module():
    """Behavior: deployed SessionEnd hook dispatches to the canonical
    bridge module (which encapsulates the .bicameral/ guard, recursion
    guard, --auto-ingest, and transcript-path propagation)."""
    cmd = _extract_session_end_command()
    assert "events.session_end_bridge" in cmd


def test_setup_wizard_renders_canonical_session_end_hook():
    """Behavior: setup_wizard's source-of-truth constant matches the
    canonical bridge form. Drift between this constant and the bridge
    module's contract is the failure mode this test exists to catch."""
    import setup_wizard

    assert setup_wizard._BICAMERAL_SESSION_END_COMMAND == CANONICAL_COMMAND


def test_build_session_end_command_no_args_matches_canonical():
    """Behavior: the parameterized helper, when called with no args,
    produces the same string as the no-args constant — i.e. end-user
    installs are unchanged by the helper's existence."""
    import setup_wizard

    assert setup_wizard._build_session_end_command() == CANONICAL_COMMAND


def test_build_session_end_command_with_mcp_config_inserts_flags():
    """Behavior: passing ``mcp_config_path`` appends ``--mcp-config <path>``
    + ``--strict-mcp-config`` to the bridge invocation. This is the
    test-harness path: the bridge forwards these flags to the spawned
    ``claude -p`` so its capture-corrections writes to the harness's
    test ledger instead of the user's default (~/.bicameral/ledger.db)."""
    import setup_wizard

    cmd = setup_wizard._build_session_end_command(mcp_config_path="/tmp/x/mcp.json")
    assert "events.session_end_bridge" in cmd
    assert "--mcp-config /tmp/x/mcp.json" in cmd
    assert "--strict-mcp-config" in cmd
    # Path with shell metachar still safe (shlex.quote applied).
    cmd2 = setup_wizard._build_session_end_command(mcp_config_path="/tmp/with space/mcp.json")
    assert "'/tmp/with space/mcp.json'" in cmd2
