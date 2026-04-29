"""User consent for outbound telemetry (issue #39).

Three responsibilities, kept independent of ``telemetry.py``:

  1. **Consent marker** — persisted at ``~/.bicameral/consent.json`` with
     ``{telemetry: "enabled"|"disabled", policy_version, acknowledged_at,
     acknowledged_via}``. File mode 0o600 on POSIX.

  2. **First-boot notice** — non-blocking. On the first boot of an
     upgraded binary that hasn't acknowledged the current policy version,
     emits the notice via MCP ``notifications/message`` (when an active
     session is available) and stderr (always). Server keeps running.

  3. **``telemetry_allowed()``** — single source of truth for the
     network relay. Returns True when env var ``BICAMERAL_TELEMETRY != "0"``
     AND (marker missing OR marker.telemetry == "enabled"). Missing
     marker preserves current default-on behavior so users don't lose
     telemetry between upgrade and first-boot acknowledgment.

Test escape hatch: ``BICAMERAL_SKIP_CONSENT_NOTICE=1`` short-circuits
``notify_if_first_run`` (used by tests/conftest.py and CI).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

POLICY_VERSION = 1
"""Bump when telemetry policy changes (new fields, new endpoints).
Re-fires the first-boot notice once for everyone on the next boot."""

_CONSENT_FILE = Path.home() / ".bicameral" / "consent.json"
_OFF_VALUES = frozenset({"0", "false", "no", "off"})


_NOTICE_TEXT = (
    "Bicameral collects anonymous usage statistics (skill name, duration, "
    "version, error flag — no code, no decision text, no file paths). "
    "To opt out: run `bicameral-mcp setup`, or set BICAMERAL_TELEMETRY=0 "
    "in your `.mcp.json` env block. This notice will not appear again "
    "unless the telemetry policy changes."
)


def read_consent() -> dict | None:
    """Return the marker contents, or None if missing/malformed."""
    if not _CONSENT_FILE.exists():
        return None
    try:
        return json.loads(_CONSENT_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("[consent] read failed: %s", exc)
        return None


def write_consent(telemetry: bool, *, via: str) -> None:
    """Atomic write of the consent marker. Mode 0o600 on POSIX.

    Raises OSError on disk failure — wizard treats this as fatal;
    notify_if_first_run swallows it.
    """
    record: dict[str, Any] = {
        "telemetry": "enabled" if telemetry else "disabled",
        "policy_version": POLICY_VERSION,
        "acknowledged_at": datetime.now(UTC).isoformat(),
        "acknowledged_via": via,
    }
    _CONSENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CONSENT_FILE.with_suffix(".json.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(tmp), flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f, separators=(",", ":"))
    os.replace(tmp, _CONSENT_FILE)


def telemetry_allowed() -> bool:
    """Single source of truth for whether the relay path may run.

    True when:
      - env var BICAMERAL_TELEMETRY != "0" (allows runtime opt-out), AND
      - marker is missing (default-on for upgraders) OR
        marker.telemetry == "enabled"
    """
    env_val = os.getenv("BICAMERAL_TELEMETRY", "1").strip().lower()
    if env_val in _OFF_VALUES:
        return False
    marker = read_consent()
    if marker is None:
        return True  # default-on for users who haven't seen the notice yet
    return marker.get("telemetry") == "enabled"


def _should_notify() -> bool:
    """True iff the notice has not been emitted for the current policy version."""
    if os.getenv("BICAMERAL_SKIP_CONSENT_NOTICE", "").strip() == "1":
        return False
    marker = read_consent()
    if marker is None:
        return True
    return int(marker.get("policy_version", 0)) < POLICY_VERSION


def notify_if_first_run(send_mcp_notification: Callable[[str, str], Any] | None = None) -> None:
    """Emit the first-boot notice once and stamp the marker. Never raises.

    ``send_mcp_notification`` is a callable taking (severity, message).
    When provided and a session is active, the notice surfaces in the
    user's MCP client (Claude Code, etc.). stderr mirror covers headless
    contexts and provides a record either way.
    """
    try:
        if not _should_notify():
            return
        # Surface to MCP client if available.
        if send_mcp_notification is not None:
            try:
                send_mcp_notification("info", _NOTICE_TEXT)
            except Exception as exc:
                logger.debug("[consent] MCP notification failed: %s", exc)
        # Stderr mirror — always.
        print(_NOTICE_TEXT, file=sys.stderr, flush=True)
        # Stamp marker so we don't repeat. Default = enabled (matches
        # current opt-out posture); user changes via wizard or env var.
        try:
            write_consent(telemetry=True, via="first_boot_notice")
        except OSError as exc:
            logger.debug("[consent] marker write failed: %s", exc)
    except Exception as exc:
        logger.debug("[consent] notify_if_first_run failed: %s", exc)
