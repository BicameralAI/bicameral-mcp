"""Sociable unit tests for NotificationsConfig (#330 + #335 Phase 2a)."""

from __future__ import annotations

from pathlib import Path

import pytest

from notifications.config import ChannelConfig, NotificationsConfig


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "notifications.yml"
    p.write_text(content, encoding="utf-8")
    return p


# ── fail-closed paths ────────────────────────────────────────────────


def test_load_returns_empty_on_missing_file(tmp_path: Path) -> None:
    cfg = NotificationsConfig.load(tmp_path / "nope.yml")
    assert cfg.channels == ()


def test_load_returns_empty_on_malformed_yaml(tmp_path: Path, capsys) -> None:
    p = _write_config(tmp_path, "{ this is not valid yaml")
    cfg = NotificationsConfig.load(p)
    assert cfg.channels == ()
    err = capsys.readouterr().err
    assert "unreadable" in err


def test_load_returns_empty_when_notification_policy_key_absent(tmp_path: Path) -> None:
    p = _write_config(tmp_path, "other_root_key: value\n")
    cfg = NotificationsConfig.load(p)
    assert cfg.channels == ()


def test_load_returns_empty_when_channels_key_is_not_a_list(tmp_path: Path) -> None:
    p = _write_config(
        tmp_path,
        "notification_policy:\n  channels: not-a-list\n",
    )
    cfg = NotificationsConfig.load(p)
    assert cfg.channels == ()


# ── happy-path parsing ──────────────────────────────────────────────


def test_load_parses_single_slack_channel(tmp_path: Path) -> None:
    p = _write_config(
        tmp_path,
        """
notification_policy:
  channels:
    - type: slack
      webhook_url_env: MY_SLACK_HOOK
""".lstrip(),
    )
    cfg = NotificationsConfig.load(p)
    assert len(cfg.channels) == 1
    ch = cfg.channels[0]
    assert isinstance(ch, ChannelConfig)
    assert ch.type == "slack"
    assert ch.events == ()
    assert ch.extra == {"webhook_url_env": "MY_SLACK_HOOK"}


def test_load_parses_channel_with_events_filter(tmp_path: Path) -> None:
    p = _write_config(
        tmp_path,
        """
notification_policy:
  channels:
    - type: slack
      webhook_url_env: MY_SLACK_HOOK
      events: [decision_ratified, drift_detected]
""".lstrip(),
    )
    cfg = NotificationsConfig.load(p)
    assert cfg.channels[0].events == ("decision_ratified", "drift_detected")


def test_load_ignores_channel_with_unknown_type(tmp_path: Path, capsys) -> None:
    p = _write_config(
        tmp_path,
        """
notification_policy:
  channels:
    - type: pigeon
      address: imaginary
""".lstrip(),
    )
    cfg = NotificationsConfig.load(p)
    assert cfg.channels == ()
    err = capsys.readouterr().err
    assert "unknown channel type" in err
    assert "'pigeon'" in err


def test_load_preserves_extra_fields_for_adapter_consumption(tmp_path: Path) -> None:
    p = _write_config(
        tmp_path,
        """
notification_policy:
  channels:
    - type: slack
      webhook_url_env: HOOK
      custom_field: value
""".lstrip(),
    )
    cfg = NotificationsConfig.load(p)
    assert cfg.channels[0].extra == {
        "webhook_url_env": "HOOK",
        "custom_field": "value",
    }


def test_load_with_explicit_path_overrides_default(tmp_path: Path) -> None:
    custom_path = _write_config(
        tmp_path,
        """
notification_policy:
  channels:
    - type: stderr
""".lstrip(),
    )
    cfg = NotificationsConfig.load(custom_path)
    assert len(cfg.channels) == 1
    assert cfg.channels[0].type == "stderr"


def test_load_filters_channel_entries_without_type(tmp_path: Path) -> None:
    p = _write_config(
        tmp_path,
        """
notification_policy:
  channels:
    - webhook_url_env: foo
    - type: slack
      webhook_url_env: bar
""".lstrip(),
    )
    cfg = NotificationsConfig.load(p)
    # First entry skipped (no type); second parses.
    assert len(cfg.channels) == 1
    assert cfg.channels[0].type == "slack"
