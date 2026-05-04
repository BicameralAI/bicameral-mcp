"""Functionality tests for team_server Phase 2 — channel allow-list config."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_config_yaml_loads_channel_allowlist(tmp_path):
    """Behavior: load_channel_allowlist parses a valid YAML and returns a
    structured object whose Slack workspaces + channel lists match input."""
    from team_server.config import load_channel_allowlist

    cfg_path = tmp_path / "team-server-config.yml"
    cfg_path.write_text(textwrap.dedent("""\
        slack:
          workspaces:
            - team_id: T123
              channels:
                - C001
                - C002
            - team_id: T999
              channels:
                - CABC
    """))
    config = load_channel_allowlist(cfg_path)
    workspaces = {w.team_id: w.channels for w in config.slack.workspaces}
    assert workspaces == {"T123": ["C001", "C002"], "T999": ["CABC"]}


def test_config_yaml_rejects_missing_workspace_id(tmp_path):
    """Behavior: load_channel_allowlist raises ValueError when a workspace
    entry omits team_id (required field)."""
    from team_server.config import load_channel_allowlist

    cfg_path = tmp_path / "team-server-config.yml"
    cfg_path.write_text(textwrap.dedent("""\
        slack:
          workspaces:
            - channels:
                - C001
    """))
    with pytest.raises(ValueError) as excinfo:
        load_channel_allowlist(cfg_path)
    assert "team_id" in str(excinfo.value).lower()
