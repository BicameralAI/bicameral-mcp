"""Governance config — parse ``.bicameral/governance.yml``.

Fail-soft posture: a missing or malformed config falls back to the
baked-in ``transparency_first`` defaults with a stderr warning. The
non-blocking absolute is enforced at the type level via
``allow_blocking: Literal[False]`` — pydantic raises if a config tries
to set it to ``True``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


_NativeAction = Literal[
    "context",
    "warn",
    "escalate",
    "notify_supervisor",
    "system_wide_warning",
]


class DecisionClassPolicy(BaseModel):
    """Per-class policy: default action plus per-class thresholds."""

    default_action: _NativeAction = "warn"
    supervisor_notification_allowed: bool = False
    system_wide_warning_allowed: bool = False
    escalation_thresholds: dict[str, float] = {}
    supervisor_thresholds: dict[str, float] = {}


class GovernanceConfig(BaseModel):
    """Parsed and validated ``.bicameral/governance.yml``.

    ``allow_blocking`` is locked at ``Literal[False]`` to enforce the
    non-blocking absolute at the type level. Pydantic refuses any other
    value at parse time, so the engine never has to special-case
    "what if a user tries to enable blocking" — they can't.
    """

    version: int = 1
    mode: Literal["transparency_first"] = "transparency_first"
    allow_blocking: Literal[False] = False
    strongest_result_wins: bool = True
    max_native_action: _NativeAction = "system_wide_warning"
    protected_components: list[str] = []
    decision_classes: dict[str, DecisionClassPolicy] = {}
    required_conditions_for_supervisor_notification: list[str] = [
        "decision_status_is_ratified",
        "decision_is_active",
        "protected_decision_class",
        "no_superseding_decision",
        "drift_confidence_above_threshold",
        "binding_confidence_above_threshold",
    ]


def load_config(path: Path | None = None) -> GovernanceConfig:
    """Read ``.bicameral/governance.yml`` and return a ``GovernanceConfig``.

    Fail-soft: returns baked-in defaults on missing file, malformed
    YAML, or pydantic validation errors. Logs a warning to stderr in
    the latter two cases so users notice silently broken config files.

    All YAML parsing uses ``yaml.safe_load`` — never ``yaml.load`` —
    to prevent arbitrary tag-driven object construction.
    """
    target = path if path is not None else (Path.cwd() / ".bicameral" / "governance.yml")
    if not target.exists():
        return GovernanceConfig()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        return GovernanceConfig.model_validate(raw or {})
    except (yaml.YAMLError, ValidationError) as exc:
        logger.warning("[governance] malformed %s: %s -- using defaults", target, exc)
        return GovernanceConfig()
