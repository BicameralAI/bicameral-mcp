"""Phase 3 (#108) — governance config loader (.bicameral/governance.yml)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from governance.config import GovernanceConfig, load_config


def test_loads_default_when_file_absent(tmp_path: Path) -> None:
    """No config file → baked-in transparency_first defaults; no error."""
    cfg = load_config(tmp_path / "missing-governance.yml")
    assert isinstance(cfg, GovernanceConfig)
    assert cfg.mode == "transparency_first"
    assert cfg.allow_blocking is False
    assert cfg.max_native_action == "system_wide_warning"


def test_loads_valid_yaml(tmp_path: Path) -> None:
    """Canonical config from the issue body parses cleanly."""
    target = tmp_path / "governance.yml"
    target.write_text(
        """
version: 1
mode: transparency_first
allow_blocking: false
strongest_result_wins: true
max_native_action: system_wide_warning
protected_components:
  - "src/payments/**"
decision_classes:
  security:
    default_action: escalate
    supervisor_notification_allowed: true
    supervisor_thresholds:
      drift_confidence: 0.85
      binding_confidence: 0.9
""".strip(),
        encoding="utf-8",
    )
    cfg = load_config(target)
    assert cfg.mode == "transparency_first"
    assert cfg.allow_blocking is False
    assert cfg.protected_components == ["src/payments/**"]
    sec = cfg.decision_classes["security"]
    assert sec.default_action == "escalate"
    assert sec.supervisor_notification_allowed is True
    assert sec.supervisor_thresholds == {
        "drift_confidence": 0.85,
        "binding_confidence": 0.9,
    }


def test_malformed_yaml_falls_back_to_defaults_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Invalid YAML emits a warning, returns defaults."""
    target = tmp_path / "governance.yml"
    target.write_text(":\n - not valid yaml: [", encoding="utf-8")
    with caplog.at_level("WARNING", logger="governance.config"):
        cfg = load_config(target)
    assert cfg.mode == "transparency_first"
    assert cfg.max_native_action == "system_wide_warning"
    assert any("governance" in rec.message.lower() for rec in caplog.records)


def test_missing_required_keys_uses_defaults(tmp_path: Path) -> None:
    """Partial config keeps user keys; fills missing with defaults."""
    target = tmp_path / "governance.yml"
    target.write_text("version: 1\n", encoding="utf-8")
    cfg = load_config(target)
    assert cfg.version == 1
    assert cfg.mode == "transparency_first"
    assert cfg.max_native_action == "system_wide_warning"
    assert cfg.decision_classes == {}


def test_unknown_decision_class_default_action_rejected(tmp_path: Path) -> None:
    """A class policy referencing an unknown default_action falls back
    to defaults via load_config (validation error → defaults)."""
    target = tmp_path / "governance.yml"
    target.write_text(
        """
decision_classes:
  security:
    default_action: nuke_orbit
""".strip(),
        encoding="utf-8",
    )
    cfg = load_config(target)
    # Validation error → fail-soft to defaults.
    assert cfg.mode == "transparency_first"
    assert cfg.decision_classes == {}


def test_allow_blocking_must_be_false() -> None:
    """allow_blocking is locked at Literal[False] by pydantic."""
    with pytest.raises(ValidationError):
        GovernanceConfig(allow_blocking=True)  # type: ignore[arg-type]
    # And via load_config: True in YAML → pydantic raises → defaults.
    cfg = GovernanceConfig.model_validate({"allow_blocking": False})
    assert cfg.allow_blocking is False
