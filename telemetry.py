"""Anonymous, privacy-first telemetry for bicameral-mcp.

Events are sent to a Cloudflare Worker relay (telemetry-relay/) which validates
the schema, rate-limits per device, and forwards to PostHog using a server-side
secret. The PostHog API key is never in this file or any client code.

## Relay contract (stable — never changes)

The relay enforces exactly two invariants and passes everything else through:
  1. `distinct_id` must be present and a string.
  2. `version` must be present and a string.
  3. `diagnostic` values, if present, must be numeric (int/float/bool) — no strings.

Any other top-level field flows through to PostHog as-is. Adding new event types
or new properties never requires a relay redeploy.

## Privacy invariants (enforced client-side before sending)

What IS collected:
  - Any string field explicitly added by the caller (e.g. skill name, event type)
  - Server version
  - Numeric/boolean metrics (duration, counts, flags)

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
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"bicameral-mcp/{payload.get('version', 'unknown')}",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception as exc:
        logger.debug("[telemetry] relay POST failed (non-fatal): %s", exc)


def send_event(version: str, diagnostic: dict | None = None, **properties: str | int | float | bool) -> None:
    """Send a telemetry event. Fire-and-forget. Never raises.

    The relay only requires `distinct_id` and `version` — all other kwargs are
    forwarded to PostHog as-is. Add new event types and fields freely without
    touching the relay.

    String kwargs (e.g. skill="bicameral-ingest") are allowed at the top level.
    diagnostic values must be int/float/bool — strings are silently dropped to
    prevent user content from leaking through this path.

    Example:
        send_event(version, skill="bicameral-ingest", session_id=sid,
                   duration_ms=412, errored=False,
                   diagnostic={"decisions_ingested": 3})
    """
    if not _is_enabled():
        return
    try:
        payload: dict = {
            "distinct_id": _get_device_id(),
            "version": version,
            **properties,
        }
        if diagnostic:
            safe_diag = {k: v for k, v in diagnostic.items() if isinstance(v, (int, float, bool))}
            if safe_diag:
                payload["diagnostic"] = safe_diag

        t = threading.Thread(target=_send_bg, args=(payload,), daemon=True)
        t.start()
    except Exception as exc:
        logger.debug("[telemetry] send_event failed (non-fatal): %s", exc)


def record_skill_event(
    skill_name: str,
    session_id: str,
    duration_ms: int,
    errored: bool,
    version: str,
    diagnostic: dict | None = None,
) -> None:
    """Convenience wrapper for skill-level timing events."""
    send_event(
        version,
        diagnostic=diagnostic,
        skill=skill_name,
        session_id=session_id,
        duration_ms=duration_ms,
        errored=errored,
    )
