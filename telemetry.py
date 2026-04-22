"""Anonymous, privacy-first telemetry for bicameral-mcp.

Events are sent to a Cloudflare Worker relay (telemetry-relay/) which validates
the schema, rate-limits per device, and forwards to PostHog using a server-side
secret. The PostHog API key is never in this file or any client code.

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
  export BICAMERAL_TELEMETRY=0        # environment variable
  BICAMERAL_TELEMETRY=0 bicameral-mcp # one-off per invocation

Data lives in Bicameral's private PostHog project. To access the team dashboard,
reach out to jin@bicameral-ai.com.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_RELAY_URL = "https://bicameral-telemetry-relay.bicameral-ai.workers.dev/event"
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
        return str(uuid.uuid4())


def _send_bg(payload: dict) -> None:
    """POST to the relay in a daemon thread. Never raises."""
    try:
        import urllib.request
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            _RELAY_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception as exc:
        logger.debug("[telemetry] relay POST failed (non-fatal): %s", exc)


def record_event(
    tool_name: str,
    duration_ms: int,
    errored: bool,
    version: str,
    diagnostic: dict | None = None,
) -> None:
    """Queue a tool-call event to the relay. Fire-and-forget. Never raises.

    diagnostic values must be integers or floats — strings are silently dropped
    to ensure no user content leaks through this path.
    """
    if not _is_enabled():
        return
    try:
        payload: dict = {
            "distinct_id": _get_device_id(),
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
                payload["diagnostic"] = safe_diag

        t = threading.Thread(target=_send_bg, args=(payload,), daemon=True)
        t.start()
    except Exception as exc:
        logger.debug("[telemetry] record_event failed (non-fatal): %s", exc)
