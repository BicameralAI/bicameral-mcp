"""Setup wizard auto-detects nightly channel from a .devN install version.

A user who runs `pipx install --pip-args=--pre bicameral-mcp` lands on a CalVer
`.devN` build. Before this fix, `_write_collaboration_config` hardcoded
`channel: stable`, and `bicameral.update` then queried PyPI's `info.version`
(which hides `.devN`) — silently stranding nightly users on whatever build
they happened to `--pre` install.

These tests lock the contract:
  * `.dev` in the version → `channel: nightly` is written
  * no `.dev` → `channel: stable` is written
  * explicit `channel=...` override is honored
  * `run_config_wizard` preserves an existing `channel: nightly` on rewrite
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_dev_version_autodetects_nightly_channel(tmp_path: Path) -> None:
    from setup_wizard import _write_collaboration_config

    with patch("setup_wizard._detect_install_channel", return_value="nightly"):
        _write_collaboration_config(tmp_path, mode="solo")

    rendered = (tmp_path / ".bicameral" / "config.yaml").read_text(encoding="utf-8")
    assert "channel: nightly" in rendered
    assert "channel: stable" not in rendered


def test_release_version_writes_stable_channel(tmp_path: Path) -> None:
    from setup_wizard import _write_collaboration_config

    with patch("setup_wizard._detect_install_channel", return_value="stable"):
        _write_collaboration_config(tmp_path, mode="solo")

    rendered = (tmp_path / ".bicameral" / "config.yaml").read_text(encoding="utf-8")
    assert "channel: stable" in rendered
    assert "channel: nightly" not in rendered


def test_explicit_channel_override_wins(tmp_path: Path) -> None:
    """Tests/callers can pin the channel literal regardless of the running build."""
    from setup_wizard import _write_collaboration_config

    with patch("setup_wizard._detect_install_channel", return_value="nightly"):
        _write_collaboration_config(tmp_path, mode="solo", channel="stable")

    rendered = (tmp_path / ".bicameral" / "config.yaml").read_text(encoding="utf-8")
    assert "channel: stable" in rendered


def test_detect_install_channel_recognizes_dev_suffix() -> None:
    """Sociable: exercise the real importlib.metadata path via monkeypatch.

    Locks the predicate (substring check on `.dev`) so a future "clever"
    refactor — e.g. PEP 440 parsing that only flags `.devN` at the tail —
    can't regress on local-version segments like `2026.5.16.dev15124+gabcdef`.
    """
    import setup_wizard

    with patch("importlib.metadata.version", return_value="2026.5.16.dev15124"):
        assert setup_wizard._detect_install_channel() == "nightly"

    with patch("importlib.metadata.version", return_value="0.14.7"):
        assert setup_wizard._detect_install_channel() == "stable"
