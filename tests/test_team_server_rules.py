"""Phase 2 — trigger rules schema + per-source/per-channel merge."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from team_server.config import (
    RulesDisabled, TeamServerConfig,
    load_rules_from_config,
    resolve_rules_for_notion, resolve_rules_for_slack,
)


def test_load_rules_from_yaml_returns_typed_rules(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "slack:\n"
        "  heuristics:\n"
        "    global:\n"
        "      keywords: [decided, agreed]\n"
    )
    config = load_rules_from_config(str(cfg))
    assert config.slack.heuristics.global_rules.keywords == ["decided", "agreed"]


def test_resolve_rules_for_slack_channel_merges_global_with_channel_override(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "slack:\n"
        "  heuristics:\n"
        "    global:\n"
        "      keywords: [a, b]\n"
        "    channels:\n"
        "      C123:\n"
        "        keywords: [c]\n"
    )
    config = load_rules_from_config(str(cfg))
    result = resolve_rules_for_slack(config, "C123")
    assert not isinstance(result, RulesDisabled)
    assert result.keywords == ("a", "b", "c")


def test_resolve_rules_for_slack_channel_with_disabled_returns_disabled_marker(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "slack:\n"
        "  heuristics:\n"
        "    global:\n"
        "      keywords: [a]\n"
        "    channels:\n"
        "      C-RANDOM:\n"
        "        enabled: false\n"
    )
    config = load_rules_from_config(str(cfg))
    result = resolve_rules_for_slack(config, "C-RANDOM")
    assert isinstance(result, RulesDisabled)


def test_resolve_rules_for_notion_database_merges_global_with_database_override(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "notion:\n"
        "  heuristics:\n"
        "    global:\n"
        "      keywords: [x, y]\n"
        "    databases:\n"
        "      db1:\n"
        "        keywords: [z]\n"
    )
    config = load_rules_from_config(str(cfg))
    result = resolve_rules_for_notion(config, "db1")
    assert not isinstance(result, RulesDisabled)
    assert result.keywords == ("x", "y", "z")


def test_invalid_yaml_keyword_negatives_pattern_raises_value_error(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "slack:\n"
        "  heuristics:\n"
        "    global:\n"
        "      keyword_negatives: [123]\n"  # ints, not strings
    )
    with pytest.raises(ValueError):
        load_rules_from_config(str(cfg))
