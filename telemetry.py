"""Anonymous, privacy-first telemetry for bicameral-mcp.

What IS collected:
  - Tool name         ("bicameral.ingest")
  - Server version    ("0.5.3")
  - Call duration     (integer milliseconds)
  - Error flag        (boolean)
  - Aggregate counts  (integers only — grounded_count, ungrounded_count, etc.)

What is NEVER collected:
  - Decision descriptions, transcript text, or any user-supplied text
  - File paths, repo names, or identifying path information
  - Search queries, code snippets, or any code content
  - Meeting, PRD, or Slack content of any kind

The distinct_id is a random UUID stored at ~/.bicameral/device_id — generated once
per machine, never linked to a real identity. There is no cross-session linkage.

Opt out at any time:
  export BICAMERAL_TELEMETRY=0        # environment variable (persistent in shell profile)
  BICAMERAL_TELEMETRY=0 bicameral-mcp # one-off

Data lives in Bicameral's private PostHog project. To access the team dashboard,
reach out to jin@bicameral-ai.com.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Write-only project API key — safe to ship in source.
# Replace with your PostHog project key before deploying.
# Get it from: https://app.posthog.com → Project Settings → Project API Key
_POSTHOG_KEY = "phc_REPLACE_WITH_YOUR_POSTHOG_PROJECT_KEY"
_POSTHOG_HOST = "https://app.posthog.com"

_TELEMETRY_OFF = frozenset({"0", "false", "no", "off"})


def _is_enabled() -> bool:
    val = os.getenv("BICAMERAL_TELEMETRY", "1").strip().lower()
    return val not in _TELEMETRY_OFF


def _get_device_id() -> str:
    """Return (or generate) the anonymous machine-level device ID."""
    device_file = Path.home() / ".bicameral" / "device_id"
    try:
        device_file.parent.mkdir(parents=True, exist_ok=True)
        if device_file.exists():
            did = device_file.read_text().strip()
            if did:
                return did
        did = str(uuid.uuid4())
        device_file.write_text(did)
        return did
    except Exception:
        # Fallback: ephemeral ID so telemetry never crashes the caller.
        return str(uuid.uuid4())


def record_event(
    tool_name: str,
    duration_ms: int,
    errored: bool,
    version: str,
    diagnostic: dict | None = None,
) -> None:
    """Capture a tool-call event to PostHog. Never raises. Fire-and-forget.

    diagnostic values must be integers or floats — strings are silently dropped
    to ensure no user content leaks through this path.
    """
    if not _is_enabled():
        return
    if _POSTHOG_KEY.startswith("phc_REPLACE"):
        # Key not configured — skip silently rather than erroring.
        return
    try:
        import posthog  # type: ignore[import]

        posthog.api_key = _POSTHOG_KEY
        posthog.host = _POSTHOG_HOST

        props: dict = {
            "tool": tool_name,
            "version": version,
            "duration_ms": duration_ms,
            "errored": errored,
        }
        if diagnostic:
            safe_diag = {
                k: v for k, v in diagnostic.items()
                if isinstance(v, (int, float, bool))
            }
            if safe_diag:
                props["diagnostic"] = safe_diag

        posthog.capture(
            distinct_id=_get_device_id(),
            event="tool_used",
            properties=props,
        )
    except Exception as exc:
        logger.debug("[telemetry] capture failed (non-fatal): %s", exc)
