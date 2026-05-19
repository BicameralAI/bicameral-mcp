"""setup_wizard._build_config must NOT write SURREAL_URL or CODE_LOCATOR_SQLITE_DB.

#368 R4 — the ledger and code-graph paths come from `ledger_locator` at runtime
(resolved to `~/.bicameral/projects/<id>/`), not from `.mcp.json` env entries
the wizard writes. The wizard's job is to wire the MCP runner; the locator
owns the state-path question.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import setup_wizard  # noqa: E402


def test_build_config_no_surreal_url(tmp_path):
    config = setup_wizard._build_config(tmp_path, mode="solo")
    assert "SURREAL_URL" not in config["env"]


def test_build_config_no_code_locator_sqlite_db(tmp_path):
    config = setup_wizard._build_config(tmp_path, mode="solo")
    assert "CODE_LOCATOR_SQLITE_DB" not in config["env"]


def test_build_config_team_mode_no_state_env_vars(tmp_path):
    config = setup_wizard._build_config(tmp_path, mode="team")
    assert "SURREAL_URL" not in config["env"]
    assert "CODE_LOCATOR_SQLITE_DB" not in config["env"]
