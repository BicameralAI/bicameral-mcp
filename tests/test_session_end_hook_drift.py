"""Functionality tests for SessionEnd hook drift fix per
plan-147-flow4-ledger-validation.md Phase 2.

Verifies the canonical hook command shape lands in:
  - .claude/settings.json (the deployed hook)
  - setup_wizard._BICAMERAL_SESSION_END_COMMAND (the source of truth for
    fresh installs)

The canonical command per skills/bicameral-capture-corrections/SKILL.md:207:

  [ -d .bicameral ] && [ -z "$BICAMERAL_SESSION_END_RUNNING" ] && \
    BICAMERAL_SESSION_END_RUNNING=1 \
    claude -p '/bicameral:capture-corrections --auto-ingest' || true
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
    "claude -p '/bicameral:capture-corrections --auto-ingest' || true"
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


def test_settings_json_session_end_passes_auto_ingest_flag():
    """Behavior: deployed SessionEnd hook invokes capture-corrections in batch (auto-ingest) mode."""
    cmd = _extract_session_end_command()
    assert "--auto-ingest" in cmd


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


def test_build_session_end_command_with_mcp_config_inserts_flags():
    """Behavior: passing ``mcp_config_path`` inserts ``--mcp-config <path>``
    + ``--strict-mcp-config`` after the prompt, before the ``|| true``
    fallback. This is the test-harness path: spawned subprocess writes
    to the harness's test ledger instead of the user's default
    (~/.bicameral/ledger.db)."""
    import setup_wizard

    cmd = setup_wizard._build_session_end_command(mcp_config_path="/tmp/x/mcp.json")
    assert "--mcp-config /tmp/x/mcp.json" in cmd
    assert "--strict-mcp-config" in cmd
    # Re-entrancy guard and --auto-ingest preserved.
    assert '[ -z "$BICAMERAL_SESSION_END_RUNNING" ]' in cmd
    assert "--auto-ingest" in cmd
    # Path with shell metachar still safe (shlex.quote applied).
    cmd2 = setup_wizard._build_session_end_command(mcp_config_path="/tmp/with space/mcp.json")
    assert "'/tmp/with space/mcp.json'" in cmd2
